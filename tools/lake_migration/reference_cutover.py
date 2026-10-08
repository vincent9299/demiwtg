"""Stage ordinary URI heads from already verified independent image objects."""
from collections import OrderedDict
import hashlib
import json
from pathlib import Path
import sqlite3

import lance
import pyarrow as pa

from demiflow.objects import ObjectRef
from demiflow.lance.storage import schema_hash
from .image_objects import ExportObjects, LEGACY_FIELDS, _field
from .blob_snapshot_rebuild import atomic_json, equal_streams


class VerifiedObjects(ExportObjects):
    """Resolve expected content identities through the completed export ledger.

    This is a one-time migration operation. Runtime readers use the stored URI
    directly, without this ledger or the previous source table.
    """
    def __init__(self, root, audit):
        super().__init__(root, Path(root) / 'objects')
        self.database = sqlite3.connect(f'file:{Path(audit) / "objects.sqlite"}?mode=ro',
                                       uri=True, check_same_thread=False)
        self.resolved = 0
        self.absent = OrderedDict()

    def prepare(self, rows):
        """Batch the ledger reads so remote SQLite pages are not read per row."""
        keys = set()
        def collect(value):
            if isinstance(value, dict):
                if isinstance(value.get('sha256'), str):
                    keys.add(value['sha256'])
                if isinstance(value.get('image_sha256'), str):
                    keys.add(value['image_sha256'])
                for item in value.values():
                    if isinstance(item, (dict, list)):
                        collect(item)
            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, (dict, list)):
                        collect(item)
        collect(rows)
        if not keys:
            return
        keys = list(keys)
        slots = ','.join('?' * len(keys))
        present = self.database.execute(f'SELECT sha256,uri FROM objects WHERE sha256 IN ({slots})', keys)
        for key, uri in present:
            self.cache[key] = ObjectRef(uri, key)
        absent = {r[0] for r in self.database.execute(f'SELECT sha256 FROM absent_objects WHERE sha256 IN ({slots})', keys)}
        for key in keys:
            self.absent[key] = key in absent
        for cache in (self.cache, self.absent):
            while len(cache) > 16384:
                cache.popitem(last=False)

    def reference(self, sha256):
        if sha256 in self.cache:
            self.cache.move_to_end(sha256)
            return self.cache[sha256]
        row = self.database.execute('SELECT uri FROM objects WHERE sha256=?', (sha256,)).fetchone()
        if row is None:
            raise ValueError('No verified exported object: ' + str(sha256))
        ref = ObjectRef(row[0], sha256)
        self.cache[sha256] = ref
        if len(self.cache) > 8192:
            self.cache.popitem(last=False)
        return ref

    def from_table(self, uri, version, sha256, column='data'):
        if column != 'data' or not uri or type(version) is not int or version < 1:
            raise ValueError('Invalid historical image binding')
        self.resolved += 1
        return self.reference(sha256)

    def is_absent(self, sha256):
        if sha256 in self.absent:
            return self.absent[sha256]
        return self.database.execute('SELECT 1 FROM absent_objects WHERE sha256=?', (sha256,)).fetchone() is not None

    def value(self, value):
        if isinstance(value, dict) and {'relative_uri', 'version', 'sha256'} <= set(value) <= LEGACY_FIELDS:
            if self.is_absent(value['sha256']):
                return None
        return super().value(value)


def stage_references(root, source_uri, version, target_uri, *, audit, public_images=False,
                     batch_size=128):
    root = Path(root).resolve()
    source, target = root / source_uri, root / target_uri
    if source.resolve() == target.resolve():
        raise ValueError('Staging must not modify the source')
    audit = Path(audit)
    identity = hashlib.sha256((str(source) + '@' + str(version)).encode()).hexdigest()[:20]
    receipt_path = audit / 'references' / (identity + '.json')
    previous_receipt = audit / 'references' / (hashlib.sha256(str(source).encode()).hexdigest()[:20] + '.json')
    if not receipt_path.exists() and previous_receipt.exists():
        previous = json.loads(previous_receipt.read_text())
        if previous['source_version'] == version and previous['target'] == str(target):
            return previous
    if receipt_path.exists():
        receipt = json.loads(receipt_path.read_text())
        if receipt['source_version'] != version or receipt['target'] != str(target):
            raise ValueError('Reference migration scope changed')
        return receipt
    dataset = lance.dataset(str(source), version=version)
    exporter = VerifiedObjects(root, audit)
    names = set(dataset.schema.names)
    artifacts = {'blob_uri', 'blob_version', 'sha256'} <= names
    omitted = {'blob_uri', 'blob_version', 'blob_column'} if artifacts else set()
    fields = [(f if public_images else _field(f)) for f in dataset.schema if f.name not in omitted]
    if public_images and 'image_uri' not in names:
        fields.append(pa.field('image_uri', pa.string()))
    if artifacts:
        fields.append(pa.field('object_uri', pa.string(), nullable=False))
    schema = pa.schema(fields, metadata=dataset.schema.metadata)
    changed = 0
    top_reference = ('blob_ref' in names and all(_field(f).equals(f) for f in dataset.schema
                                               if f.name != 'blob_ref'))

    def converted_batches():
        nonlocal changed
        for batch in dataset.scanner(batch_size=batch_size, batch_readahead=1,
                                     fragment_readahead=1).to_batches():
            if public_images:
                # Copy annotation/provenance arrays byte-for-byte. Only image_uri
                # belongs to this migration; decoding all nested model results
                # would needlessly inflate memory and touch historical evidence.
                columns = [n for n in ('sha256', 'availability', 'byte_size') if n in names]
                originals = batch.select(columns).to_pylist()
                exporter.prepare(originals)
                uris = [None if (row.get('availability') in {'metadata_only','missing','unavailable'}
                                or row.get('byte_size') == 0 or exporter.is_absent(row['sha256']))
                        else exporter.reference(row['sha256']).uri for row in originals]
                changed += len(originals)
                yield pa.RecordBatch.from_arrays([pa.array(uris, type=pa.string()) if f.name == 'image_uri'
                    else batch[f.name] for f in schema], schema=schema)
                continue
            if top_reference:
                originals = batch.select(['blob_ref']).to_pylist()
                exporter.prepare(originals)
                references = [exporter.value(row['blob_ref']) for row in originals]
                changed += len(originals)
                yield pa.RecordBatch.from_arrays([pa.array(references, type=f.type) if f.name == 'object_ref'
                    else batch[f.name] for f in schema], schema=schema)
                continue
            rows = []
            originals = batch.to_pylist()
            exporter.prepare(originals)
            for original in originals:
                row = exporter.value(original)
                if public_images:
                    if (row.get('availability') in {'metadata_only', 'missing', 'unavailable'}
                            or row.get('byte_size') == 0 or exporter.is_absent(row['sha256'])):
                        row['image_uri'] = None
                    else:
                        row['image_uri'] = exporter.reference(row['sha256']).uri
                changed += row != original
                rows.append(row)
            yield pa.RecordBatch.from_pylist(rows, schema=schema)

    target.parent.mkdir(parents=True, exist_ok=True)
    saved = lance.write_dataset(pa.RecordBatchReader.from_batches(schema, converted_batches()),
                                str(target), mode='overwrite' if target.exists() else 'create')
    changed_rows = changed
    checked = equal_streams(converted_batches(), saved.scanner(batch_size=batch_size,
        batch_readahead=1, fragment_readahead=1).to_batches())
    if checked != dataset.count_rows():
        raise ValueError('Reference migration did not verify every row')
    receipt = {'source':str(source), 'source_version':version, 'target':str(target),
        'staged_version':saved.version, 'rows':checked, 'changed_rows':changed_rows,
        'schema_hash':schema_hash(saved.schema), 'verified':True}
    atomic_json(receipt_path, receipt)
    exporter.database.close()
    print(json.dumps(receipt, ensure_ascii=False), flush=True)
    return receipt
