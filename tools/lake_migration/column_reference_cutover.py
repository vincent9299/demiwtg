"""Migrate image columns without rewriting unrelated nested business results."""
import hashlib
import json
from pathlib import Path
import threading

import lance
import pyarrow as pa
import pyarrow.compute as pc

from demiflow.lance.snapshot import clone_local_snapshot
from demiflow.lance.storage import schema_hash
from .reference_cutover import VerifiedObjects
from .image_objects import _field, contains_legacy_image_ref
from .blob_snapshot_rebuild import atomic_json, equal_streams


def reorder_columns(dataset, names):
    """Project existing field IDs in a different order without rewriting values.

    Lance exposes Project but no Python schema reorder method. Its standard
    pickle reducer preserves field IDs in flattened protobuf records. Reorder
    whole root-field groups, without editing any protobuf or field identity.
    Validate the reducer layout and resulting Arrow schema before committing.
    """
    if dataset.schema.names == names:
        return dataset
    schema = dataset.lance_schema
    rebuild, parts = schema.__reduce_ex__(4)
    def count(field):
        return 1 + sum(count(child) for child in field.children())
    groups, offset = {}, 1
    for field in schema.fields():
        size = count(field)
        groups[field.name()] = parts[offset:offset + size]
        offset += size
    if offset != len(parts) or set(names) != set(groups):
        raise ValueError('Unsupported Lance schema serialization layout')
    projected = rebuild(parts[0], *(record for name in names for record in groups[name]))
    expected = pa.schema([dataset.schema.field(name) for name in names], metadata=dataset.schema.metadata)
    if not projected.to_pyarrow().equals(expected, check_metadata=True):
        raise ValueError('Lance metadata projection did not preserve fields')
    return lance.LanceDataset.commit(dataset.uri,
        lance.LanceOperation.Project(projected, preserves_nullability=True), read_version=dataset.version)


def stage_columns(root, uri, version, target_uri, *, audit, public_images=False,
                  article_illustrations=False):
    root, audit = Path(root).resolve(), Path(audit)
    source, target = root / uri, root / target_uri
    identity = hashlib.sha256((str(source) + '@' + str(version)).encode()).hexdigest()[:20]
    record = audit / 'column_references' / (identity + '.json')
    if record.exists():
        receipt = json.loads(record.read_text())
        if receipt['target'] != str(target):
            raise ValueError('Column migration target changed')
        return receipt
    if target.exists():
        raise ValueError('Unfinished column stage must be inspected before resuming: ' + str(target))
    original = lance.dataset(str(source), version=version)
    names = set(original.schema.names)
    objects = VerifiedObjects(root, audit)
    artifacts = {'blob_uri','blob_version','sha256'} <= names
    omitted = {'blob_uri','blob_version','blob_column'} if artifacts else set()
    fields = [f if public_images else _field(f) for f in original.schema if f.name not in omitted]
    if public_images and 'image_uri' not in names:
        fields.append(pa.field('image_uri',pa.string()))
    if artifacts:
        fields.append(pa.field('object_uri',pa.string(),nullable=False))
    desired = pa.schema(fields, metadata=original.schema.metadata)
    affected = {f.name for f in original.schema if not _field(f).equals(f)} - omitted
    if not public_images and not article_illustrations:
        strings = [f.name for f in original.schema if pa.types.is_string(f.type) or pa.types.is_large_string(f.type)]
        for batch in original.scanner(columns=strings,batch_size=512,batch_readahead=1,fragment_readahead=1).to_batches():
            for name in strings:
                if (name not in affected and any(pc.any(pc.match_substring(batch[name], token)).as_py()
                        for token in ('blob_ref','blob_uri','relative_uri'))
                        and any(contains_legacy_image_ref(value) for value in batch[name].to_pylist())):
                    affected.add(name)
    if public_images:
        affected = {'image_uri'}
    if article_illustrations:
        if public_images or 'illustrations' not in names:
            raise ValueError('Article illustration migration requires its own column')
        affected = {'illustrations'}
    if artifacts:
        affected.add('object_uri')
    # Only URI fields and JSON fields containing actual legacy asset references
    # are rewritten. All annotations, scores and their physical files stay intact.
    outputs = [_field(original.schema.field(n)) if n in names and not public_images else desired.field(n)
               for n in original.schema.names if n in affected]
    outputs += [desired.field(n) for n in desired.names if n in affected and n not in names]
    temporary = {f.name:'__object_migration_' + str(i) for i,f in enumerate(outputs)}
    output_schema = pa.schema([pa.field(temporary[f.name],f.type,nullable=f.nullable,metadata=f.metadata) for f in outputs])
    read_names = list(dict.fromkeys([n for n in affected if n in names] +
        ([n for n in ('sha256','availability','byte_size') if n in names] if public_images else []) +
        (['sha256','blob_uri','blob_version'] + (['blob_column'] if 'blob_column' in names else []) if artifacts else [])))
    lock = threading.Lock()

    def transformed(batch):
        with lock:
            rows = batch.to_pylist()
            objects.prepare(rows)
            result = []
            for row in rows:
                if article_illustrations:
                    illustrations = []
                    for item in row.get('illustrations') or []:
                        ref = objects.reference(item['sha256'])
                        evidence = json.loads(item['evidence_json'])
                        info = evidence.get('bytes') or {}
                        if info.get('sha256') != ref.sha256:
                            raise ValueError('Article illustration content identity differs')
                        evidence['bytes'] = {**info, 'image_uri':ref.uri, 'path':ref.uri}
                        original_record = evidence.get('record') or {}
                        record = {**original_record, 'image_uri':ref.uri}
                        details = record.get('byte_details')
                        if details:
                            if details.get('sha256') != ref.sha256:
                                raise ValueError('Article byte details content identity differs')
                            record['byte_details'] = {**details, 'image_uri':ref.uri, 'path':ref.uri}
                        evidence['record'] = record
                        illustrations.append({**item, 'evidence_json':json.dumps(evidence,ensure_ascii=False)})
                    value = {'illustrations':illustrations if row.get('illustrations') is not None else None}
                elif public_images:
                    missing = row.get('availability') in {'metadata_only','missing','unavailable'} or row.get('byte_size') == 0 or objects.is_absent(row['sha256'])
                    value = {'image_uri':None if missing else objects.reference(row['sha256']).uri}
                else:
                    value = objects.value(row)
                result.append({temporary[f.name]:value.get(f.name) for f in outputs})
            return pa.RecordBatch.from_pylist(result, schema=output_schema)

    stage = clone_local_snapshot(original, target)
    udf = lance.batch_udf(output_schema=output_schema)(transformed)
    stage.add_columns(udf, read_columns=read_names, batch_size=512)
    stage = lance.dataset(str(target))
    drops = list((affected & names) | omitted)
    if drops:
        stage.drop_columns(drops)
        stage = lance.dataset(str(target))
    stage.alter_columns(*[{'path':temporary[f.name],'name':f.name} for f in outputs])
    stage = reorder_columns(lance.dataset(str(target)), desired.names)
    if not stage.schema.equals(desired, check_metadata=True):
        raise ValueError('Column migration schema differs')
    unchanged = [n for n in original.schema.names if n not in affected and n not in omitted]
    checked = equal_streams(original.scanner(columns=unchanged,batch_size=512,batch_readahead=1,fragment_readahead=1).to_batches(),
                            stage.scanner(columns=unchanged,batch_size=512,batch_readahead=1,fragment_readahead=1).to_batches())
    def expected():
        for batch in original.scanner(columns=read_names,batch_size=512,batch_readahead=1,fragment_readahead=1).to_batches():
            value = transformed(batch)
            yield pa.RecordBatch.from_arrays(list(value.columns), schema=pa.schema(outputs))
    if checked != original.count_rows() or equal_streams(expected(),stage.scanner(columns=[f.name for f in outputs],batch_size=512).to_batches()) != checked:
        raise ValueError('Column migration did not verify every row')
    receipt = {'source':str(source),'source_version':version,'target':str(target),
        'preserves_history':True, 'verified_versions':[v['version'] for v in original.versions()],
        'head':{'version':stage.version,'rows':checked,'schema_hash':schema_hash(stage.schema)},
        'changed_columns':[f.name for f in outputs], 'verified':True}
    atomic_json(record,receipt)
    objects.database.close()
    print(json.dumps(receipt,ensure_ascii=False),flush=True)
    return receipt
