"""Relocate idle curation datasets without rewriting rows, versions or journals.

Use a same-filesystem directory rename. The receipt records every file's inode,
size and mtime before and after, plus Lance head/version metadata. Existing
relocation aliases are flattened because the platform resolves one exact hop.
"""
import argparse
from contextlib import ExitStack
import fcntl
import json
import os
from pathlib import Path

import lance
from demiflow.execution.artifacts import run_lock, resolve_local_artifact
from demiflow.lance.control import table_lock_path
from demiflow.lance.storage import resolve_local_uri, schema_hash
from preparation.qid_images.operators.contracts import atomic_json


DIRECTORIES = {
    'demiwtg/benchmark/edit/source_images/datasets': 'demiwtg/curation/edit_scene_images/datasets',
    'demiwtg/curation/t2i/datasets': 'demiwtg/curation/t2i_training_samples/datasets',
    'demiwtg/curation/edit/datasets': 'demiwtg/curation/edit_training_pairs/datasets',
}


def file_inventory(directory):
    rows = []
    for parent, dirs, files in os.walk(directory):
        for name in [*dirs, *files]:
            path = Path(parent) / name
            if path.is_symlink():
                raise ValueError('Symlink in migration source: ' + str(path))
        for name in files:
            path = Path(parent) / name
            stat = path.stat()
            rows.append([path.relative_to(directory).as_posix(), stat.st_dev,
                         stat.st_ino, stat.st_size, stat.st_mtime_ns])
    return sorted(rows)


def table_inventory(directory):
    result = {}
    for path in sorted(directory.glob('*.lance')):
        ds = lance.dataset(str(path))
        result[path.name] = {'version': ds.version, 'rows': ds.count_rows(),
                             'schema_hash': schema_hash(ds.schema)}
    return result


def relocate(root, directories=None):
    root = Path(root).resolve()
    directories = dict(directories or DIRECTORIES)
    control = root / '_demiflow/curation_ownership_20261002'
    manifest = root / '_demiflow/lance_locations.json'
    artifacts = root / '_demiflow/artifact_locations.json'
    with ExitStack() as stack:
        stack.enter_context(run_lock(control))
        stack.enter_context(run_lock(root / '_demiflow/lance_location_migration'))
        # Both versions of the Edit scene pipeline must be idle during relocation.
        old_controls = root / '_demiflow/benchmark_edit_source_images'
        for run in sorted(old_controls.iterdir() if old_controls.exists() else []):
            if run.is_dir():
                stack.enter_context(run_lock(run))
                stack.enter_context(run_lock(root / '_demiflow/curation_edit_scene_images' / run.name))
        mapping = json.loads(manifest.read_text()) if manifest.exists() else {'version': 1, 'tables': {}}
        if mapping.get('version') != 1:
            raise ValueError('Unsupported location mapping')
        files = json.loads(artifacts.read_text()) if artifacts.exists() else {'version': 1, 'files': {}}
        if files.get('version') != 1:
            raise ValueError('Unsupported artifact location mapping')
        results = []
        for index, (old, new) in enumerate(directories.items()):
            source, target = root / old, root / new
            proof = control / f'directory_{index}.json'
            if proof.exists():
                receipt = json.loads(proof.read_text())
                if (receipt['old'], receipt['new']) != (old, new):
                    raise ValueError('Migration receipt belongs to another mapping')
            else:
                if not source.is_dir() or target.exists():
                    raise ValueError('Require one original directory and no destination: ' + old)
                target.parent.mkdir(parents=True, exist_ok=True)
                if source.stat().st_dev != target.parent.stat().st_dev:
                    raise ValueError('This migration requires same-filesystem rename')
                for table in sorted(source.glob('*.lance')):
                    lock = stack.enter_context(table_lock_path(table).open('a'))
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                receipt = {'old': old, 'new': new, 'files': file_inventory(source),
                           'tables': table_inventory(source), 'complete': False}
                atomic_json(proof, receipt)
            if source.exists():
                if target.exists() or file_inventory(source) != receipt['files']:
                    raise ValueError('Migration source changed or destination exists')
                source.rename(target)
            if file_inventory(target) != receipt['files'] or table_inventory(target) != receipt['tables']:
                raise ValueError('Relocated file identity or table head differs')
            for alias, physical in list(mapping['tables'].items()):
                if physical.startswith(old + '/'):
                    mapping['tables'][alias] = new + physical[len(old):]
            for name in receipt['tables']:
                alias, physical = old + '/' + name, new + '/' + name
                if alias in mapping['tables'] and mapping['tables'][alias] != physical:
                    raise ValueError('Conflicting existing table alias: ' + alias)
                mapping['tables'][alias] = physical
            atomic_json(manifest, mapping)
            for alias, physical in list(files['files'].items()):
                if physical.startswith(old + '/'):
                    files['files'][alias] = new + physical[len(old):]
            for name, *_ in receipt['files']:
                # Internal Lance files are resolved by the table alias. Explicit
                # artifacts cover saved SQLite refs and offline input objects.
                if any(part.endswith('.lance') for part in Path(name).parts):
                    continue
                alias, physical = old + '/' + name, new + '/' + name
                if alias in files['files'] and files['files'][alias] != physical:
                    raise ValueError('Conflicting artifact alias: ' + alias)
                files['files'][alias] = physical
            atomic_json(artifacts, files)
            for name, *_ in receipt['files']:
                if not any(part.endswith('.lance') for part in Path(name).parts):
                    if resolve_local_artifact(source / name) != target / name:
                        raise ValueError('Historical artifact reference failed to resolve')
            for name, head in receipt['tables'].items():
                resolved = resolve_local_uri(source / name)
                if resolved != target / name:
                    raise ValueError('Historical table reference failed to resolve')
                if lance.dataset(str(resolved), version=head['version']).count_rows() != head['rows']:
                    raise ValueError('Historical fixed-version count changed')
            receipt['complete'] = True
            atomic_json(proof, receipt)
            results.append({'old': old, 'new': new, 'files': len(receipt['files']),
                            'tables': len(receipt['tables'])})
            print(json.dumps(results[-1]), flush=True)
        result = {'complete': True, 'directories': results, 'rows_rewritten': False,
                  'file_identity_preserved': True, 'history_manifests_preserved': True}
        atomic_json(control / 'result.json', result)
        return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True, type=Path)
    print(json.dumps(relocate(parser.parse_args().root), indent=2))
