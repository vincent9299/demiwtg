"""Rebuild Blob table snapshots using external objects, preserving logical history.

Historical schemas/versions keep read-only external Blob descriptors so existing
frozen DatasetRefs still validate. A new head drops Blob fields and exposes plain
image_uri. No source path is modified by this staging operation.
"""
import hashlib
import json
import os
from pathlib import Path
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import lance
import pyarrow as pa
from lance.fragment import FragmentMetadata, write_fragments
from demiflow.lance.storage import schema_hash


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    with temporary.open('w') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def equal_streams(left, right):
    """Compare complete logical values regardless of scanner batch boundaries."""
    left, right = iter(left), iter(right)
    a = next(left, None)
    b = next(right, None)
    checked = 0
    while a is not None and b is not None:
        count = min(a.num_rows, b.num_rows)
        if not a.slice(0, count).equals(b.slice(0, count), check_metadata=True):
            raise ValueError(f'Metadata differs at row {checked}')
        checked += count
        a = a.slice(count) if count < a.num_rows else next(left, None)
        b = b.slice(count) if count < b.num_rows else next(right, None)
    if a is not None or b is not None:
        raise ValueError('Snapshot row counts differ')
    return checked


def rebuild(root, source_uri, target_uri, *, audit, column='data', make_head=True):
    root = Path(root).resolve()
    source, target = (root / source_uri).resolve(), (root / target_uri).resolve()
    if source == target:
        raise ValueError('Staging target must differ from source')
    audit = Path(audit)
    identity = hashlib.sha256(str(source).encode()).hexdigest()[:20]
    receipt_path = audit / 'tables' / (identity + '.json')
    # Arrow consumes the batch generator on its own reader thread.
    database = sqlite3.connect(f'file:{audit / "objects.sqlite"}?mode=ro', uri=True,
                              check_same_thread=False)
    latest = lance.dataset(str(source))
    versions = latest.versions()
    expected_versions = [v['version'] for v in versions]
    if expected_versions != list(range(1, latest.version + 1)):
        raise ValueError('Non-contiguous history requires explicit version mapping')
    receipt = (json.loads(receipt_path.read_text()) if receipt_path.exists() else
        {'source':str(source),'source_version':latest.version,'target':str(target),
         'original_versions':[{'version':v['version'],'timestamp':v['timestamp'].isoformat(),
                               'metadata':v['metadata']} for v in versions], 'fragments':{},'verified_versions':[]})
    if receipt['source_version'] != latest.version or receipt['target'] != str(target):
        raise ValueError('Staging source changed')
    target.parent.mkdir(parents=True, exist_ok=True)
    stage = lance.dataset(str(target)) if (target / '_versions').exists() else None
    verification_pool = ThreadPoolExecutor(max_workers=48)

    def external_batches(dataset, fragment, schema):
        for batch in dataset.scanner(fragments=[fragment], batch_size=256, batch_readahead=1,
                                     fragment_readahead=1).to_batches():
            keys = batch['sha256'].to_pylist()
            descriptors = batch[column].to_pylist()
            values = {r[0]:r for r in database.execute(
                'SELECT sha256,uri,size,mtime_ns FROM objects WHERE sha256 IN (' + ','.join('?'*len(keys)) + ')', keys)}
            def checked_uri(pair):
                key, blob = pair
                if blob is None:
                    return None
                if key not in values:
                    raise ValueError('Object not exported and verified: ' + key)
                saved = values[key]
                path = root / 'objects' / key[:2] / key
                stat = path.stat()
                if stat.st_size != saved[2] or stat.st_mtime_ns != saved[3]:
                    # mtime is only a fast path. Shared filesystem metadata can
                    # refresh after publication; the content hash is decisive.
                    from demiflow.objects import ObjectRef
                    if ObjectRef(saved[1], key).verify() != saved[2]:
                        raise ValueError('Exported object changed before staging: ' + key)
                if blob.get('size') is not None and blob['size'] != saved[2]:
                    raise ValueError('Snapshot Blob size differs from exported object: ' + key)
                return saved[1]
            uris = list(verification_pool.map(checked_uri, zip(keys, descriptors)))
            yield pa.RecordBatch.from_arrays([
                lance.blob_array(uris) if field.name == column else batch[field.name]
                for field in schema], schema=schema)

    for version in expected_versions:
        dataset = lance.dataset(str(source), version=version)
        if not stage or stage.version < version:
            fragments = []
            for original in dataset.get_fragments():
                key = hashlib.sha256(json.dumps({'schema':schema_hash(dataset.schema),
                    'fragment': original.metadata.to_json()},sort_keys=True).encode()).hexdigest()
                if key in receipt['fragments']:
                    fragments.extend(FragmentMetadata.from_json(json.dumps(v)) for v in receipt['fragments'][key])
                    continue
                batches = pa.RecordBatchReader.from_batches(dataset.schema, external_batches(dataset,original,dataset.schema))
                written = write_fragments(batches, str(target), schema=dataset.schema,
                    mode='create' if stage is None else 'append', data_storage_version='2.2',
                    allow_external_blob_outside_bases=True, external_blob_mode='reference')
                receipt['fragments'][key] = [fragment.to_json() for fragment in written]
                atomic_json(receipt_path, receipt)
                fragments.extend(written)
            operation = lance.LanceOperation.Overwrite(dataset.schema, fragments)
            stage = lance.LanceDataset.commit(str(target), operation, read_version=stage.version if stage else None,
                commit_message=f'External object migration of original snapshot {version}')
            if stage.version != version:
                raise ValueError('Staged historical version number differs')
        if version not in receipt['verified_versions']:
            migrated = lance.dataset(str(target),version=version)
            if schema_hash(migrated.schema) != schema_hash(dataset.schema):
                raise ValueError('Historical schema differs')
            columns = [f.name for f in dataset.schema if f.name != column]
            checked = equal_streams(
                dataset.scanner(columns=columns,batch_size=512,batch_readahead=1,fragment_readahead=1).to_batches(),
                migrated.scanner(columns=columns,batch_size=512,batch_readahead=1,fragment_readahead=1).to_batches())
            for batch in migrated.scanner(columns=['sha256',column],batch_size=2048).to_batches():
                for row in batch.to_pylist():
                    descriptor = row[column]
                    if descriptor is not None and (descriptor['kind'] != 3 or
                        descriptor['blob_uri'] != (root/'objects'/row['sha256'][:2]/row['sha256']).as_uri()):
                        raise ValueError('Historical Blob must be an ordinary complete external object')
            if checked != dataset.count_rows():
                raise ValueError('Historical metadata verification did not cover every row')
            receipt['verified_versions'].append(version)
            atomic_json(receipt_path,receipt)
            print(json.dumps({'staged':source_uri,'verified_version':version,'rows':checked}),flush=True)
    if make_head and not receipt.get('head'):
        schema = pa.schema([f for f in latest.schema if f.name != column] +
                           ([] if 'image_uri' in latest.schema.names else [pa.field('image_uri',pa.string())]),
                           metadata=latest.schema.metadata)
        current = lance.dataset(str(target),version=latest.version)
        def metadata_batches():
            for batch in current.scanner(batch_size=256,batch_readahead=1,fragment_readahead=1).to_batches():
                records = batch.to_pylist()
                for row in records:
                    descriptor = row.pop(column)
                    row['image_uri'] = descriptor['blob_uri'] if descriptor else None
                    if 'storage_mode' in row and descriptor:
                        row['storage_mode'] = 'object_uri'
                yield pa.RecordBatch.from_pylist(records,schema=schema)
        stage=lance.write_dataset(pa.RecordBatchReader.from_batches(schema,metadata_batches()),str(target),mode='overwrite')
        receipt['head']={'version':stage.version,'schema_hash':schema_hash(stage.schema),'rows':stage.count_rows()}
        if receipt['head']['rows'] != latest.count_rows() or column in stage.schema.names:
            raise ValueError('Plain-URI head verification failed')
        if equal_streams(metadata_batches(), stage.scanner(batch_size=256,
                batch_readahead=1, fragment_readahead=1).to_batches()) != latest.count_rows():
            raise ValueError('Plain-URI head metadata or image mapping differs')
        atomic_json(receipt_path,receipt)
    if any(target.rglob('*.blob')):
        raise ValueError('Staged table unexpectedly contains internal Blob sidecars')
    # Recreate scalar lookup indices on the ordinary-URI head only. Historical
    # snapshots retain their logical rows without depending on old row addresses.
    if make_head:
        stage = lance.dataset(str(target))
        existing = {index['name'] for index in stage.list_indices()}
        kinds = {'BTree':'BTREE', 'LabelList':'LABEL_LIST'}
        for index in latest.list_indices():
            if index['name'] in existing:
                continue
            if index['type'] not in kinds or len(index['fields']) != 1:
                raise ValueError('Index requires explicit migration: ' + index['name'])
            stage.create_scalar_index(index['fields'][0], kinds[index['type']], name=index['name'])
            stage = lance.dataset(str(target))
        receipt['head']['version'] = stage.version
        atomic_json(receipt_path, receipt)
    database.close()
    verification_pool.shutdown()
    return receipt
