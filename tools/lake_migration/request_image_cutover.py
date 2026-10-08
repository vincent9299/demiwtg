"""Externalize images in single-snapshot preparation request stages.

Model call journals are separate immutable evidence. These preparation tables
are consumed through from_stage_row, which restores the exact original inputs.
"""
import hashlib
import json
from pathlib import Path
import time

import lance
import pyarrow as pa

from demiflow.objects import LocalObjectStore, externalize_data_uris, restore_data_uris
from demiflow.lance.storage import schema_hash
from .object_cutover import journal
from .blob_snapshot_rebuild import atomic_json, equal_streams


class RememberObjects(LocalObjectStore):
    def __init__(self, path):
        super().__init__(path)
        self.saved = {}

    def put(self, value, *, sha256=None):
        key = hashlib.sha256(value).hexdigest()
        if key not in self.saved:
            self.saved[key] = super().put(value, sha256=sha256)
        return self.saved[key]


def stage_requests(root, uri, *, audit):
    root, audit = Path(root).resolve(), Path(audit)
    source = root / uri
    target = root / '_staging/request_image_cutover_20260929' / uri
    identity = hashlib.sha256(str(source).encode()).hexdigest()[:20]
    receipt_path = audit / 'requests' / (identity + '.json')
    if receipt_path.exists():
        return json.loads(receipt_path.read_text())
    original = lance.dataset(str(source))
    if original.version != 1 or not {'stage', 'row_id', 'payload'} <= set(original.schema.names):
        raise ValueError('Only explicit single-snapshot preparation stages are supported')
    store = RememberObjects(root / 'objects')
    changed = 0

    def batches():
        nonlocal changed
        for batch in original.scanner(batch_size=1, batch_readahead=1, fragment_readahead=1).to_batches():
            rows = batch.to_pylist()
            for row in rows:
                for name in ('payload', 'review_json'):
                    if not row.get(name):
                        continue
                    value = json.loads(row[name])
                    converted = externalize_data_uris(value, store)
                    if converted != value:
                        if restore_data_uris(converted) != value:
                            raise ValueError('Externalized request does not restore exact input')
                        row[name] = json.dumps(converted, ensure_ascii=False, sort_keys=True)
                        changed += 1
            yield pa.RecordBatch.from_pylist(rows, schema=original.schema)

    target.parent.mkdir(parents=True, exist_ok=True)
    saved = lance.write_dataset(pa.RecordBatchReader.from_batches(original.schema, batches()),
                                str(target), mode='overwrite' if target.exists() else 'create')
    checked = equal_streams(batches(), saved.scanner(batch_size=1, batch_readahead=1, fragment_readahead=1).to_batches())
    if checked != original.count_rows() or saved.version != 1:
        raise ValueError('Staged request version or row count differs')
    with journal(audit / 'objects.sqlite') as database:
        for key, ref in store.saved.items():
            size = ref.verify()
            path = root / 'objects' / key[:2] / key
            database.execute('INSERT OR REPLACE INTO objects VALUES(?,?,?,?,?)',
                              (key, size, ref.uri, path.stat().st_mtime_ns, time.time()))
    receipt = {'source':str(source),'source_version':1,'target':str(target),
        'verified_versions':[1], 'head':{'version':1,'rows':checked,'schema_hash':schema_hash(saved.schema)},
        'objects':len(store.saved), 'restores_exact_original_requests':True}
    atomic_json(receipt_path, receipt)
    print(json.dumps(receipt, ensure_ascii=False), flush=True)
    return receipt
