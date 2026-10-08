"""Move public assets to their owners, preserving frozen references and bytes.

Run parts separately when the shared document library has an active writer.
This is a one-time storage migration, not a producer or a publication pipeline.
"""
import argparse
from contextlib import ExitStack
import fcntl
import json
import os
from pathlib import Path

import lance
from demiflow.execution.artifacts import run_lock, resolve_local_artifact
from demiflow.lance.control import control_directory, table_lock_path
from demiflow.lance.storage import resolve_local_uri, schema_hash
from preparation.qid_images.operators.contracts import atomic_json


TABLES = {
    'datasets/articles.lance': 'demiwtg/preparation/articles/datasets/articles.lance',
    'datasets/images.lance': 'demiwtg/preparation/images/catalog/datasets/images.lance',
    'datasets/qid_images.lance': 'demiwtg/preparation/qid_images/datasets/qid_images.lance',
    'datasets/qid_concepts.lance': 'demiwtg/preparation/qid_concepts/datasets/qid_concepts.lance',
    'datasets/registry_datasets.lance': '_demiflow/registry/datasets.lance',
    'datasets/registry_releases.lance': '_demiflow/registry/releases.lance',
}
MASTER = {'datasets/master_concepts.lance': 'demiwtg/collect/datasets/concepts.lance',
          'datasets/master_concepts.json': 'demiwtg/collect/datasets/concepts.json'}
DOCUMENTS = {f'datasets/{name}': f'demiwtg/preparation/wiki_documents/datasets/{name}'
             for name in ('document_objects', 'document_library')}
FINISH = {'datasets/README.md': '_demiflow/dataset_ownership_20261002/previous_README.md',
          'datasets/_demiflow': '_demiflow/dataset_ownership_20261002/retired_table_controls'}


def inventory(path):
    """Same-filesystem inode/size/mtime proof, including empty directories."""
    rows = []
    def visit(current, relative):
        stat = current.stat(follow_symlinks=False)
        if current.is_symlink():
            raise ValueError('Symlinks are not migrated: ' + str(current))
        directory = current.is_dir()
        # Directory allocation size may change on rename (e.g. overlayfs).
        # Child membership is proved by the inventory, bytes by file stats.
        rows.append([relative, 'directory' if directory else 'file', stat.st_dev,
                     stat.st_ino, None if directory else stat.st_size,
                     None if directory else stat.st_mtime_ns])
        if directory:
            with os.scandir(current) as entries:
                for entry in entries:
                    visit(Path(entry.path), relative + '/' + entry.name if relative else entry.name)
    visit(Path(path), '')
    return sorted(rows)


def table_history(path):
    ds = lance.dataset(str(path))
    return {'head': ds.version, 'rows': ds.count_rows(), 'schema_hash': schema_hash(ds.schema),
            'versions': sorted(v['version'] for v in ds.versions())}


def move_asset(root, old, new, *, control, mappings):
    source, target = root / old, root / new
    proof = control / (old.replace('/', '__') + '.json')
    if proof.exists():
        saved = json.loads(proof.read_text())
        if (saved['old'], saved['new']) != (old, new):
            raise ValueError('Receipt belongs to another move')
    else:
        if not source.exists() or target.exists():
            raise ValueError('Require exactly the original asset: ' + old)
        target.parent.mkdir(parents=True, exist_ok=True)
        if source.stat().st_dev != target.parent.stat().st_dev:
            raise ValueError('Only same-filesystem moves are supported')
        saved = {'old': old, 'new': new, 'files': inventory(source), 'complete': False}
        if old.endswith('.lance') and '/_demiflow/' not in old:
            saved['table'] = table_history(source)
        atomic_json(proof, saved)
    if source.exists():
        if target.exists() or inventory(source) != saved['files']:
            raise ValueError('Source changed since inventory: ' + old)
        source.rename(target)
    if inventory(target) != saved['files']:
        raise ValueError('File identities changed: ' + new)
    if 'table' in saved and table_history(target) != saved['table']:
        raise ValueError('Table history changed: ' + new)
    for kind, (filename, mapping) in mappings.items():
        aliases = mapping[kind]
        for alias, physical in list(aliases.items()):
            if physical == old or physical.startswith(old + '/'):
                aliases[alias] = new + physical[len(old):]
        entries = [('',)] if kind == 'tables' and 'table' in saved else []
        if kind == 'files' and 'table' not in saved:
            entries = saved['files']
        for name, *_ in entries:
            alias = old + ('/' + name if name else '')
            physical = new + ('/' + name if name else '')
            if alias in aliases and aliases[alias] != physical:
                raise ValueError('Conflicting existing alias: ' + alias)
            aliases[alias] = physical
        atomic_json(filename, mapping)
    resolver = resolve_local_uri if 'table' in saved else resolve_local_artifact
    if resolver(source) != target:
        raise ValueError('Frozen reference did not resolve: ' + old)
    saved['complete'] = True
    atomic_json(proof, saved)
    print(json.dumps({'old': old, 'new': new, 'entries': len(saved['files'])}), flush=True)
    return {k: v for k, v in saved.items() if k != 'files'}


def relocate(root, part, pairs=None):
    root = Path(root).resolve()
    control = root / '_demiflow/dataset_ownership_20261002'
    groups = {'core': TABLES, 'master': MASTER, 'documents': DOCUMENTS, 'finish': FINISH}
    pairs = dict(groups[part] if pairs is None else pairs)
    with ExitStack() as stack:
        stack.enter_context(run_lock(control))
        stack.enter_context(run_lock(root / '_demiflow/lance_location_migration'))
        if part in {'master', 'documents', 'finish'}:
            for owner in ('concepts', 'wiki_documents'):
                runs = root / '_demiflow' / owner
                for run in sorted(runs.iterdir() if runs.exists() else []):
                    if run.is_dir():
                        stack.enter_context(run_lock(run))
        # Move each table's coordination files together with its data, holding
        # the original lock inode across the move. Never lock a recreated alias.
        for old, new in list(pairs.items()):
            if old.endswith('.lance'):
                actual = root / (old if control_directory(root / old).exists() else new)
                if not (root / old).exists() and not (root / new).exists():
                    raise ValueError('Missing table: ' + old)
                if (root / old).exists():
                    actual = root / old
                lock = stack.enter_context(table_lock_path(actual).open('a'))
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                old_control = control_directory(root / old).relative_to(root).as_posix()
                new_control = control_directory(root / new).relative_to(root).as_posix()
                pairs[old_control] = new_control
        mappings = {}
        for kind, name in [('tables', 'lance_locations.json'), ('files', 'artifact_locations.json')]:
            filename = root / '_demiflow' / name
            mapping = json.loads(filename.read_text()) if filename.exists() else {'version': 1, kind: {}}
            if mapping.get('version') != 1 or not isinstance(mapping.get(kind), dict):
                raise ValueError('Unsupported location mapping')
            mappings[kind] = (filename, mapping)
        results = [move_asset(root, old, new, control=control, mappings=mappings)
                   for old, new in pairs.items()]
        if part == 'finish' and (root / 'datasets').exists():
            (root / 'datasets').rmdir()  # Refuse to discard any unaccounted asset.
        result = {'complete': True, 'part': part, 'assets': results,
                  'rows_rewritten': False, 'versions_preserved': True}
        atomic_json(control / (part + '.json'), result)
        return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True, type=Path)
    parser.add_argument('--part', required=True, choices=['core', 'master', 'documents', 'finish'])
    args = parser.parse_args()
    relocate(args.root, args.part)
