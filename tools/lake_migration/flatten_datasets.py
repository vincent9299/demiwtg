"""Approved, one-time relocation of whole Lance tables into flat owner folders.

Renames on one filesystem preserve all versions, indexes and blob bytes. Frozen
references retain their identity and resolve through an exact relocation map.
No models, row rewrites, merges, symlinks, or data-file deletion are involved.
"""
from contextlib import ExitStack
from pathlib import Path
import argparse
import fcntl
import json
import os

import lance
import pyarrow as pa
from demiflow.lance.control import control_directory, table_lock_path
from demiflow.lance.storage import schema_hash

PLAN = Path(__file__).with_name('flat_datasets_plan.json')


def inventory(path):
    """Inode+size+mtime prove same files across a same-filesystem rename."""
    result = {}
    for folder, dirs, files in os.walk(path):
        for name in sorted(files):
            file = Path(folder) / name
            if file.is_symlink():
                raise ValueError('Unexpected symbolic link: ' + str(file))
            stat = file.stat()
            result[str(file.relative_to(path))] = [stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns]
    return result


def relocate_remaining_controls(workspace, plan):
    """Keep legacy reservation/run locks with the new owner; remove empty old folders."""
    old_root = workspace / 'datasets'
    branch = old_root / 'runs'
    if not branch.exists():
        return
    mapping = {item['old']: item['new'] for item in plan}
    for path in [p for p in branch.rglob('*') if p.is_file()]:
        relative = path.relative_to(old_root)
        parts = relative.parts
        if path.name == 'calls.reservation.lock':
            table = workspace / mapping[str(relative.with_name('calls.lance'))]
            target = control_directory(table) / 'reservation.lock'
        elif path.name == '.lock' and parts[:2] == ('runs', 'pipeline'):
            owner = {'t2i': 'curation/t2i', 'edit': 'curation/edit'}[parts[2]]
            target = workspace / 'demiwtg' / owner / 'datasets/_demiflow' / parts[3] / '.lock'
        elif path.name == 'write.lock' and '_demiflow' in parts:
            owner = {'t2i': 'curation/t2i', 'edit': 'curation/edit'}[parts[2]]
            target = (workspace / 'demiwtg' / owner / 'datasets/_demiflow' /
                      ('uncommitted_materials__' + parts[3] + '__' + path.parent.name) / path.name)
        else:
            raise ValueError('Unexpected file outside a migrated table: ' + str(path))
        with path.open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if target.exists():raise ValueError('Control file collision: ' + str(target))
            target.parent.mkdir(parents=True, exist_ok=True)
            path.rename(target)
    for folder, dirs, files in os.walk(branch, topdown=False):
        path = Path(folder)
        if not any(path.iterdir()):path.rmdir()


def run(workspace):
    workspace = Path(workspace).resolve()
    old_root = workspace / 'datasets'
    plan = json.loads(PLAN.read_text())
    manifest = workspace / '_demiflow/lance_locations.json'
    receipt = workspace / '_demiflow/flat_datasets_receipt.json'
    audit = old_root / 'relocation__pipeline_datasets_20260923.lance'
    if receipt.exists():
        saved = json.loads(receipt.read_text())
        if saved['plan'] != plan:
            raise ValueError('Relocation plan changed')
        for item in saved['tables']:
            if item['committed']:
                ds = lance.dataset(workspace / item['new'], version=item['version'])
                assert ds.count_rows() == item['row_count']
        relocate_remaining_controls(workspace, plan)
        return {'tables': len(plan), 'already_complete': True}
    if manifest.exists():
        raise ValueError('An existing location manifest needs explicit reconciliation')
    if len({item['new'] for item in plan}) != len(plan):
        raise ValueError('Destination collision')
    for item in plan:
        source, target = old_root / item['old'], workspace / item['new']
        if not source.is_dir() or target.exists():
            raise ValueError('Missing source or existing target: ' + str(source))
        target.parent.mkdir(parents=True, exist_ok=True)
        if source.stat().st_dev != target.parent.stat().st_dev:
            raise ValueError('Relocation must be a same-filesystem rename')
    moved, snapshots = [], []
    with ExitStack() as locks:
        for item in sorted(plan, key=lambda row: row['old']):
            source = old_root / item['old']
            lock = locks.enter_context(table_lock_path(source).open('a'))
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        for item in plan:
            source = old_root / item['old']
            committed = (source / '_versions').is_dir() and any((source / '_versions').glob('*.manifest'))
            ds = lance.dataset(source) if committed else None
            snapshots.append({**item, 'committed': committed,
                              'version': ds.version if ds is not None else None, 'row_count': ds.count_rows() if ds is not None else None,
                              'schema_hash': schema_hash(ds.schema) if ds is not None else None,
                              'versions': sorted(v['version'] for v in ds.versions()) if ds is not None else [],
                              'files': inventory(source)})
        print(f'Preflight: {len(snapshots)} tables, all write locks acquired', flush=True)
        try:
            for item in snapshots:
                source, target = old_root / item['old'], workspace / item['new']
                source.rename(target)
                moved.append((target, source))
                control = control_directory(source)
                if control.exists():
                    new_control = control_directory(target)
                    new_control.parent.mkdir(parents=True, exist_ok=True)
                    if new_control.exists():raise ValueError('Control directory collision')
                    control.rename(new_control)
                    moved.append((new_control, control))
                if inventory(target) != item['files']:
                    raise ValueError('Files changed during rename: ' + str(target))
                if not item['committed']:
                    continue
                ds = lance.dataset(target)
                if (ds.version != item['version'] or ds.count_rows() != item['row_count']
                        or schema_hash(ds.schema) != item['schema_hash']
                        or sorted(v['version'] for v in ds.versions()) != item['versions']):
                    raise ValueError('Dataset identity changed: ' + str(target))
            mapping = {}
            for item in plan:
                # Both the old data root and the current workspace root are supported.
                mapping[item['old']] = item['new']
                mapping['datasets/' + item['old']] = item['new']
            manifest.parent.mkdir(exist_ok=True)
            pending = manifest.with_suffix('.pending')
            pending.write_text(json.dumps({'version': 1, 'tables': mapping}, ensure_ascii=False, indent=2) + '\n')
            pending.replace(manifest)
        except BaseException:
            manifest.unlink(missing_ok=True)
            for target, source in reversed(moved):
                source.parent.mkdir(parents=True, exist_ok=True)
                target.rename(source)
            raise
    # Save compact, queryable verification evidence; original tables remain untouched.
    rows = [{k: v for k, v in item.items() if k not in {'files', 'versions'}} |
            {'version_count': len(item['versions']), 'file_count': len(item['files']),
             'byte_size': sum(v[2] for v in item['files'].values()),
             'same_files_verified': True} for item in snapshots]
    lance.write_dataset(pa.Table.from_pylist(rows), audit, mode='create')
    receipt.write_text(json.dumps({'plan': plan, 'tables': rows}, ensure_ascii=False, indent=2) + '\n')
    relocate_remaining_controls(workspace, plan)
    # Only remove empty superseded folders, never recurse into Lance/control folders.
    for name in ['raw', 'curated', 'master', 'registry', 'runs']:
        branch = old_root / name
        if not branch.exists():continue
        for folder, dirs, files in os.walk(branch, topdown=False):
            p = Path(folder)
            if not any(p.iterdir()):p.rmdir()
    return {'tables': len(plan), 'versions': sum(len(s['versions']) for s in snapshots),
            'bytes_unchanged': sum(r['byte_size'] for r in rows), 'audit': str(audit)}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', type=Path, required=True)
    print(json.dumps(run(parser.parse_args().workspace), ensure_ascii=False, indent=2))
