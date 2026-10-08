"""Commit validated image migration stages, with durable recovery receipts.

Callers must quiesce readers of the physical Blob tables before swapping them.
Retirement is deliberately a separate operation after end-to-end validation.
"""
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil

import lance

from demiflow.lance.control import table_lock_path
from demiflow.lance.refs import DatasetRef
from demiflow.lance.registry import Catalog
from demiflow.lance.storage import schema_hash
from .blob_snapshot_rebuild import atomic_json, equal_streams


def sync_directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


@contextmanager
def exclusive(path):
    with table_lock_path(path).open('a') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def register_head(root, path):
    catalog = Catalog(root)
    old = [ref for ref in catalog.registered() if Path(ref.resolve(root)).resolve() == path.resolve()]
    if not old:
        return None  # Unregistered stage tables keep their existing ownership.
    prior = max(old, key=lambda ref: ref.lance_version)
    dataset = lance.dataset(str(path))
    ref = DatasetRef(**{**prior.to_dict(), 'lance_version':dataset.version,
        'schema_hash':schema_hash(dataset.schema), 'row_count':dataset.count_rows()})
    catalog.register(ref)
    return ref.to_dict()


def commit_blob_table(root, staged_receipt, *, audit):
    root, audit = Path(root).resolve(), Path(audit)
    receipt = dict(staged_receipt)
    source, stage = Path(receipt['source']), Path(receipt['target'])
    identity = hashlib.sha256(str(source).encode()).hexdigest()[:20]
    record = audit / 'commits' / (identity + '.json')
    backup = stage.with_name(stage.name + '.original')
    if record.exists():
        saved = json.loads(record.read_text())
        if saved.get('state') in {'committed', 'retired'}:
            return saved
    else:
        saved = {'source':str(source), 'stage':str(stage), 'backup':str(backup),
                 'source_version':receipt['source_version'], 'state':'prepared'}
    if receipt.get('verified_versions') != list(range(1, receipt['source_version'] + 1)) or not receipt.get('head'):
        raise ValueError('Every historical version and the URI head must be validated')
    with exclusive(source):
        # Resume a crash between the two directory renames before opening Lance.
        if not source.exists() and backup.exists() and stage.exists():
            os.rename(stage, source)
            sync_directory(source.parent)
        original = lance.dataset(str(source))
        if original.version == receipt['source_version'] and not backup.exists():
            replacement = lance.dataset(str(stage))
            if replacement.version != receipt['head']['version'] or 'data' in replacement.schema.names:
                raise ValueError('Staged head differs from verified receipt')
            if backup.exists():
                raise ValueError('Original backup already exists; inspect migration state')
            atomic_json(record, saved)
            os.rename(source, backup)
            try:
                os.rename(stage, source)
            except BaseException:
                os.rename(backup, source)
                raise
            sync_directory(source.parent)
            sync_directory(backup.parent)
        elif not backup.exists() or original.version != receipt['head']['version']:
            raise ValueError('Source changed since staging; do not replace concurrent writes')
        current = lance.dataset(str(source))
        if schema_hash(current.schema) != receipt['head']['schema_hash'] or current.count_rows() != receipt['head']['rows']:
            raise ValueError('Swapped table differs from staged head')
        saved.update(state='committed', head_version=current.version,
                     schema_hash=schema_hash(current.schema), rows=current.count_rows(),
                     registered=register_head(root, source))
        atomic_json(record, saved)
    return saved


def commit_reference_table(root, staged_receipt, *, audit):
    root, audit = Path(root).resolve(), Path(audit)
    receipt = dict(staged_receipt)
    if not receipt.get('verified'):
        raise ValueError('Reference stage not verified')
    source, stage = Path(receipt['source']), Path(receipt['target'])
    record = audit / 'reference_commits' / (hashlib.sha256(str(source).encode()).hexdigest()[:20] + '.json')
    if record.exists():
        return json.loads(record.read_text())
    with exclusive(source):
        current = lance.dataset(str(source))
        prepared = lance.dataset(str(stage), version=receipt['staged_version'])
        if not receipt['changed_rows'] and prepared.schema.equals(current.schema, check_metadata=True):
            return {'source':str(source), 'head_version':current.version, 'state':'unchanged'}
        if current.version == receipt['source_version']:
            saved = lance.write_dataset(prepared.scanner(batch_size=128, batch_readahead=1,
                fragment_readahead=1).to_reader(), str(source), mode='overwrite')
        elif current.version > receipt['source_version'] and current.schema.equals(prepared.schema, check_metadata=True):
            # A process can exit after Lance committed and before the receipt.
            # Resume only if its full logical result equals our verified stage.
            saved = current
        else:
            raise ValueError('Reference source changed since staging')
        checked = equal_streams(prepared.scanner(batch_size=128).to_batches(),
                                saved.scanner(batch_size=128).to_batches())
        if checked != receipt['rows'] or schema_hash(saved.schema) != receipt['schema_hash']:
            raise ValueError('Committed reference head differs')
        kinds = {'BTree':'BTREE', 'LabelList':'LABEL_LIST'}
        original = lance.dataset(str(source), version=receipt['source_version'])
        for index in original.list_indices():
            if index['type'] not in kinds or len(index['fields']) != 1:
                raise ValueError('Index requires explicit migration: ' + index['name'])
            saved.create_scalar_index(index['fields'][0], kinds[index['type']],
                                      name=index['name'], replace=True)
            saved = lance.dataset(str(source))
        result = {'source':str(source), 'old_version':receipt['source_version'],
                  'head_version':saved.version, 'rows':checked, 'state':'committed',
                  'registered':register_head(root, source)}
        atomic_json(record, result)
        return result


def retire_blob_backup(commit_record, *, audit):
    """Remove only the exact original directory named by a committed receipt."""
    source, backup = Path(commit_record['source']), Path(commit_record['backup'])
    if commit_record.get('state') != 'committed' or not backup.name.endswith('.lance.original'):
        raise ValueError('Retirement requires an explicit committed migration receipt')
    if source == backup or not backup.is_dir():
        raise ValueError('Unexpected original backup path')
    current = lance.dataset(str(source))
    if 'data' in current.schema.names or current.count_rows() != commit_record['rows']:
        raise ValueError('Live object metadata must validate before retirement')
    if any(source.rglob('*.blob')):
        raise ValueError('Live snapshots still contain internal Blob files')
    identity = hashlib.sha256(str(source).encode()).hexdigest()[:20]
    # Keep original version manifests for forensic comparison, never pixel files.
    manifest_archive = Path(audit) / 'original_manifests' / identity
    manifest_archive.mkdir(parents=True, exist_ok=True)
    for name in ('_versions', '_transactions'):
        if (backup / name).exists():
            shutil.copytree(backup / name, manifest_archive / name, dirs_exist_ok=True)
    # Column-only stages share untouched files through hardlinks. Removing a
    # backup directory does not reclaim those shared file contents.
    files = [p.stat() for p in backup.rglob('*') if p.is_file()]
    removed_bytes = sum(stat.st_size for stat in files)
    unlinked_exclusive_bytes = sum(stat.st_size for stat in files if stat.st_nlink == 1)
    shutil.rmtree(backup)
    sync_directory(backup.parent)
    result = {**commit_record, 'state':'retired', 'removed_lance_bytes':removed_bytes,
              'unlinked_exclusive_bytes':unlinked_exclusive_bytes}
    atomic_json(Path(audit) / 'commits' / (identity + '.json'), result)
    return result
