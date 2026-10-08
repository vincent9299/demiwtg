"""Explicit migration from table-backed image references to independent objects.

Reads one fixed source snapshot, writes a NEW table, and preserves source tables
and historical versions. Object files are durable, content-addressed and shared
across migrations. No production migration runs merely by importing this module.
"""
import argparse
from collections import OrderedDict
import json
import re
from pathlib import Path

import lance
import pyarrow as pa

from demiflow.lance.storage import resolve_local_uri
from demiflow.objects import LocalObjectStore, ObjectRef


LEGACY_FIELDS = {'relative_uri', 'version', 'sha256', 'column'}


def contains_legacy_image_ref(value):
    """Recognize bare, nested and JSON-encoded asset refs, excluding DatasetRefs."""
    if isinstance(value, str) and value[:1] in {'{', '['}:
        # Wide historical payloads can repeat gigabytes of source metadata.
        # A DatasetRef has lance_version, not the image ref's version key.
        # Check necessary key tokens before decoding it; escaped JSON strings
        # are included because another JSON layer may wrap the image reference.
        if ('blob_ref' not in value and 'blob_uri' not in value
                and not ('relative_uri' in value and 'sha256' in value and '"version' in value
                         and re.search(r'\\*"version\\*"\s*:', value))):
            return False
        if 'blob_ref' not in value and 'blob_uri' not in value:
            # A BlobRef is a flat object with only scalar values. Decode the
            # small objects at its URI keys, instead of expanding the enclosing
            # historical payload (which can contain huge repeated source lists).
            decoder, offset = json.JSONDecoder(), 0
            while (position := value.find('"relative_uri', offset)) >= 0:
                offset = position + len('"relative_uri')
                start = value.rfind('{', 0, position)
                if start < 0:
                    continue
                try:
                    candidate, end = decoder.raw_decode(value, start)
                except ValueError:
                    # DatasetRefs are often themselves JSON strings. Unwrap
                    # their small leaf object without expanding the outer row.
                    end = value.find('}', position) + 1
                    fragment = value[start:end] if end else ''
                    try:
                        for _ in range(8):
                            try:
                                candidate = json.loads(fragment)
                                break
                            except ValueError:
                                fragment = json.loads('"' + fragment + '"')
                        else:
                            break
                    except ValueError:
                        # Unusual quoted braces or deeper encodings retain the
                        # full parser path, so they cannot hide a real image ref.
                        break
                if (end > position and isinstance(candidate, dict)
                        and {'relative_uri', 'version', 'sha256'} <= set(candidate) <= LEGACY_FIELDS):
                    return True
            else:
                return False
        try:
            return contains_legacy_image_ref(json.loads(value))
        except ValueError:
            return False
    if isinstance(value, list):
        return any(contains_legacy_image_ref(item) for item in value)
    if isinstance(value, dict):
        keys = set(value)
        return (({'relative_uri', 'version', 'sha256'} <= keys <= LEGACY_FIELDS)
                or {'blob_uri', 'blob_version', 'sha256'} <= keys
                or any('blob_ref' in key for key in keys)
                or any(contains_legacy_image_ref(item) for item in value.values()))
    return False


class ExportObjects:
    """Bounded reference cache; payloads are streamed one object at a time."""
    def __init__(self, root, directory):
        self.root = Path(root)
        self.store = LocalObjectStore(directory)
        self.cache = OrderedDict()
        self.datasets = OrderedDict()

    def from_table(self, uri, version, sha256, column='data'):
        key = (uri, version, sha256, column)
        if key in self.cache:
            self.cache.move_to_end(key)
            return self.cache[key]
        ObjectRef(self.store.reference(sha256).uri, sha256)  # validate before SQL
        source = (uri, version)
        if source not in self.datasets:
            self.datasets[source] = lance.dataset(str(resolve_local_uri(self.root / uri)), version=version)
            if len(self.datasets) > 8:
                self.datasets.popitem(last=False)
        self.datasets.move_to_end(source)
        dataset = self.datasets[source]
        matches = dataset.to_table(columns=['sha256'], filter=f"sha256 = '{sha256}'", with_row_id=True)
        if matches.num_rows != 1:
            raise ValueError('Image reference must resolve exactly one source row')
        blob = dataset.take_blobs(column, ids=matches['_rowid'].to_pylist())[0]
        if blob is None:
            raise ValueError('Referenced image has no stored bytes: ' + sha256)
        try:
            ref = self.store.put_stream(blob, sha256=sha256)
        finally:
            blob.close()
        self.cache[key] = ref
        if len(self.cache) > 256:
            self.cache.popitem(last=False)
        return ref

    def value(self, value):
        """Convert known asset references, keeping ordinary table refs unchanged."""
        if isinstance(value, list):
            return [self.value(item) for item in value]
        if isinstance(value, dict):
            keys = set(value)
            if {'relative_uri', 'version', 'sha256'} <= keys and keys <= LEGACY_FIELDS:
                return self.from_table(value['relative_uri'], value['version'], value['sha256'],
                                       value.get('column') or 'data').to_dict()
            result = {}
            for name, item in value.items():
                key = name.replace('blob_ref', 'object_ref')
                result[key] = self.value(item)
            if {'blob_uri', 'blob_version', 'sha256'} <= keys:
                ref = self.from_table(value['blob_uri'], value['blob_version'], value['sha256'],
                                      value.get('blob_column') or 'data')
                for name in ('blob_uri', 'blob_version', 'blob_column'):
                    result.pop(name, None)
                result['object_uri'] = ref.uri
            return result
        if isinstance(value, str) and value[:1] in {'{', '['}:
            try:
                parsed = json.loads(value)
            except ValueError:
                return value
            converted = self.value(parsed)
            return value if converted == parsed else json.dumps(converted, ensure_ascii=False)
        return value


def _type(datatype):
    if pa.types.is_struct(datatype):
        keys = {f.name for f in datatype}
        if {'relative_uri', 'version', 'sha256'} <= keys and keys <= LEGACY_FIELDS:
            return pa.struct([('uri', pa.string()), ('sha256', pa.string())])
        return pa.struct([_field(f) for f in datatype])
    if pa.types.is_list(datatype):
        return pa.list_(_field(datatype.value_field))
    if pa.types.is_large_list(datatype):
        return pa.large_list(_field(datatype.value_field))
    return datatype


def _field(field):
    return pa.field(field.name.replace('blob_ref', 'object_ref'), _type(field.type),
                    nullable=field.nullable, metadata=field.metadata)


def migrate_table(root, source_uri, version, target_uri, *, object_directory=None, batch_size=32):
    """Export images and references into a new table; never replace an old table.

    Raw images with a `data` column are explicitly exported to `image_uri`.
    Public images use their existing source_refs only during this migration.
    Nested legacy BlobRefs and JSON-encoded asset refs are converted as well.
    Missing/corrupt referenced bytes abort the migration, leaving the old table.
    """
    root = Path(root).resolve()
    source = Path(resolve_local_uri(root / source_uri)).resolve()
    target = (root / target_uri).resolve()
    if target == source or target.exists():
        raise ValueError('Migration requires a new target table; source/history must remain unchanged')
    if type(version) is not int or version < 1 or type(batch_size) is not int or batch_size < 1:
        raise ValueError('version and batch_size must be positive integers')
    dataset = lance.dataset(str(source), version=version)
    exporter = ExportObjects(root, object_directory or root / 'objects')
    names = set(dataset.schema.names)
    raw_images = {'sha256', 'data'} <= names
    public_images = {'sha256', 'source_refs'} <= names
    artifacts = {'blob_uri', 'blob_version', 'sha256'} <= names
    omitted = {'data'} if raw_images else set()
    if artifacts:
        omitted |= {'blob_uri', 'blob_version', 'blob_column'}
    fields = [_field(f) for f in dataset.schema if f.name not in omitted]
    if (raw_images or public_images) and 'image_uri' not in names:
        fields.append(pa.field('image_uri', pa.string()))
    if artifacts:
        fields.append(pa.field('object_uri', pa.string(), nullable=False))
    schema = pa.schema(fields, metadata=dataset.schema.metadata)

    def batches():
        scanner = dataset.scanner(columns=[n for n in dataset.schema.names if not (raw_images and n == 'data')],
                                  batch_size=batch_size, batch_readahead=1, fragment_readahead=1)
        count = 0
        for batch in scanner.to_batches():
            rows = []
            for original in batch.to_pylist():
                row = exporter.value(original)
                if raw_images or public_images:
                    uri = row.get('image_uri')
                    if uri:
                        ObjectRef(uri, row['sha256']).verify()
                    elif row.get('availability') in {'metadata_only', 'missing', 'unavailable'} or row.get('byte_size') == 0:
                        row['image_uri'] = None
                    elif raw_images:
                        row['image_uri'] = exporter.from_table(str(source), version, row['sha256']).uri
                    else:
                        errors = []
                        for binding in original.get('source_refs') or []:
                            binding = json.loads(binding) if isinstance(binding, str) else binding
                            try:
                                row['image_uri'] = exporter.from_table(binding['relative_uri'],
                                    binding['lance_version'], row['sha256']).uri
                                break
                            except (ValueError, OSError) as exc:
                                errors.append(str(exc))
                        else:
                            raise ValueError('Cannot migrate image ' + row['sha256'] + ': ' + '; '.join(errors))
                    if raw_images:
                        row['storage_mode'] = 'object_uri' if row.get('image_uri') else 'metadata_only'
                rows.append(row)
            count += len(rows)
            yield pa.RecordBatch.from_pylist(rows, schema=schema)
            if count % 1024 < len(rows):
                print(f'[image objects] migrated {count:,} rows', flush=True)

    target.parent.mkdir(parents=True, exist_ok=True)
    result = lance.write_dataset(pa.RecordBatchReader.from_batches(schema, batches()), str(target), mode='create')
    if result.count_rows() != dataset.count_rows():
        raise ValueError('Migration row count differs from source')
    return {'source': {'uri': str(source), 'version': version},
            'target': {'uri': str(target), 'version': result.version}, 'rows': result.count_rows()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--source', required=True)
    parser.add_argument('--version', type=int, required=True)
    parser.add_argument('--target', required=True)
    parser.add_argument('--objects', type=Path)
    parser.add_argument('--batch-size', type=int, default=32)
    args = parser.parse_args()
    print(json.dumps(migrate_table(args.root, args.source, args.version, args.target,
        object_directory=args.objects, batch_size=args.batch_size), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
