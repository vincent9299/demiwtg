"""Move idle T2I case evaluation and retained probe evidence to evaluation.

One-time ownership migration: rename files on the same filesystem, preserve all
Lance versions and journal bytes, and register exact historical URI mappings.
Run before updating source imports; this is not a pipeline execution entry.
"""
import argparse
from contextlib import ExitStack
import fcntl
import hashlib
import json
from pathlib import Path
import shutil

from demiflow.execution.artifacts import run_lock
from demiflow.lance.control import table_lock_path
from demiflow.lance.storage import resolve_local_uri
from preparation.qid_images.operators.contracts import atomic_json
from tools.lake_migration.dataset_ownership import move_asset, table_history

OLD = 'demiwtg/benchmark/t2i/case_annotation'
PROBE = 'demiwtg/benchmark/t2i/zimage_probe'
NEW = 'demiwtg/evaluation/t2i/case_annotation'


def relocate(root):
    root = Path(root).resolve()
    control = root / '_demiflow/t2i_case_ownership_20261003'
    with ExitStack() as stack:
        stack.enter_context(run_lock(control))
        stack.enter_context(run_lock(root / '_demiflow/lance_location_migration'))
        # Reserve each known run while preserving the lock inode across rename.
        owner_pairs = {}
        for owner in ('benchmark_t2i_case_annotation', 'benchmark_t2i_zimage_probe'):
            parent = root / '_demiflow' / owner
            for run in sorted(parent.iterdir() if parent.exists() else []):
                if run.is_dir():
                    stack.enter_context(run_lock(run))
                    owner_pairs[run.relative_to(root).as_posix()] = (
                        '_demiflow/evaluation_t2i_case_annotation/' + run.name)

        pairs = {OLD: NEW}
        # Flat, unique run names let the retired probe's tables share the owner.
        probe_data = root / PROBE / 'datasets'
        for entry in sorted(probe_data.iterdir()):
            if entry.name == '_demiflow':
                for item in sorted(entry.iterdir()):
                    pairs[item.relative_to(root).as_posix()] = NEW + '/datasets/_demiflow/' + item.name
            else:
                pairs[entry.relative_to(root).as_posix()] = NEW + '/datasets/' + entry.name
        pairs[PROBE + '/README.md'] = NEW + '/archive/zimage_probe_20260929/retirement_20260929.md'
        pairs.update(owner_pairs)
        # Fail before changing anything if a retired artifact would overwrite a
        # current one; the existing case module itself must have no destination.
        for old, new in pairs.items():
            target = root / new
            if not (root / old).exists() or target.exists():
                raise ValueError('Missing source or occupied destination: ' + old)
            if old.startswith(PROBE + '/datasets/'):
                overlap = root / OLD / new.removeprefix(NEW + '/')
                if overlap.exists():
                    raise ValueError('Probe artifact collides with case artifact: ' + old)

        tables, hashes = {}, {}
        for owner in (OLD, PROBE):
            directory = root / owner / 'datasets'
            for table in sorted(directory.glob('*.lance')):
                lock = stack.enter_context(table_lock_path(table).open('a'))
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                old = table.relative_to(root).as_posix()
                tables[old] = {'new': NEW + '/datasets/' + table.name,
                               'history': table_history(table)}
            for file in sorted(directory.rglob('*')):
                if file.is_file():
                    with file.open('rb') as stream:
                        hashes[file.relative_to(root).as_posix()] = hashlib.file_digest(stream, 'sha256').hexdigest()
        # table_lock_path may have created control directories; include them too.
        for item in sorted((probe_data / '_demiflow').iterdir()):
            pairs[item.relative_to(root).as_posix()] = NEW + '/datasets/_demiflow/' + item.name

        mappings = {}
        for kind, name in [('tables', 'lance_locations.json'), ('files', 'artifact_locations.json')]:
            filename = root / '_demiflow' / name
            value = json.loads(filename.read_text()) if filename.exists() else {'version': 1, kind: {}}
            if value.get('version') != 1 or not isinstance(value.get(kind), dict):
                raise ValueError('Invalid relocation manifest')
            mappings[kind] = (filename, value)
        atomic_json(control / 'before.json', {'tables': tables, 'sha256': hashes})
        results = [move_asset(root, old, new, control=control, mappings=mappings)
                   for old, new in pairs.items()]
        # Moving a module registers file artifacts; add exact table aliases for
        # tables nested within that directory (the resolver never prefix-guesses).
        filename, mapping = mappings['tables']
        for old, info in tables.items():
            mapping['tables'][old] = info['new']
        atomic_json(filename, mapping)
        for old, info in tables.items():
            target = root / info['new']
            if resolve_local_uri(root / old) != target or table_history(target) != info['history']:
                raise ValueError('Historical table changed: ' + old)
        for old, expected in hashes.items():
            name = old.split('/datasets/', 1)[1]
            with (root / NEW / 'datasets' / name).open('rb') as stream:
                if hashlib.file_digest(stream, 'sha256').hexdigest() != expected:
                    raise ValueError('Data/journal bytes changed: ' + old)

        # Only disposable bytecode is removed. Retired source and all evidence
        # remain under the owner's archive/datasets; no forwarding package/link.
        for owner in (root / PROBE, root / NEW):
            for cache in list(owner.rglob('__pycache__')):
                shutil.rmtree(cache)
        (probe_data / '_demiflow').rmdir()
        probe_data.rmdir()
        (root / PROBE).rmdir()
        for owner in ('benchmark_t2i_case_annotation', 'benchmark_t2i_zimage_probe'):
            parent = root / '_demiflow' / owner
            if parent.exists():
                parent.rmdir()
        result = {'complete': True, 'tables': len(tables), 'files_hashed': len(hashes),
                  'rows_rewritten': False, 'all_versions_preserved': True,
                  'data_sha256_preserved': True, 'moves': results}
        atomic_json(control / 'result.json', result)
        return {key: value for key, value in result.items() if key != 'moves'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True, type=Path)
    print(json.dumps(relocate(parser.parse_args().root), indent=2))
