"""One-time typed migration of historical evidence and annotation protocols.

Creates and verifies replacements. Retirement is a separate, release-checked step.
"""
import csv
import hashlib
import io
import json
from pathlib import Path

from demiflow.lance.registry import Catalog, write_registered_table
from demiflow.lance.records import LanceRecordStore
from demiflow.lance.refs import DatasetRef
from tools.lake_migration.material_history import (KNOWLEDGE_DRAFTS, IMAGE_OBSERVATIONS,
    CONCEPT_SELECTIONS, TAXONOMY_HISTORY, ANNOTATION_PROTOCOLS)
from collect.materials import source_record
from project import resolve_root

OP = 'source_evidence_20260921'
OLD = 'raw/collect_records/v1/'
ANNOTATIONS = 'derived/annotations/preannotation/v1/'


def rows(ds):
    for batch in ds.to_batches(batch_size=4096):
        yield from batch.to_pylist()


def run(root):
    root = Path(root)
    journal = LanceRecordStore(root, f'runs/maintenance/{OP}/records.lance')
    specs = journal.get('inputs')
    if specs is None:
        refs = {}
        for ref in Catalog(root).registered():
            if ref.relative_uri.startswith((OLD, ANNOTATIONS, 'raw/taxonomy_sources/')):
                prior = refs.get(ref.relative_uri)
                if prior is None or prior.lance_version < ref.lance_version:
                    refs[ref.relative_uri] = ref
        specs = {k: v.to_dict() for k, v in refs.items()}
        if len(specs) != 7:
            raise ValueError('Unexpected source inventory')
        journal.put('inputs', specs)

    names = ('knowledge_drafts','image_observations','concept_selections','taxonomy_history',
             'annotation_protocols','image_descriptions','concept_matches')
    completed = {name:journal.get(name) for name in names}
    if all(completed.values()):
        for saved in completed.values(): DatasetRef.from_dict(saved['ref']).open(root)
        return completed

    def source(uri):
        return DatasetRef.from_dict(specs[uri]).open(root)

    def migrate(name, uri, schema, factory, expected_count):
        saved = journal.get(name)
        if saved:
            DatasetRef.from_dict(saved['ref']).open(root)
            return
        fingerprint = OP + ':' + name + ':' + hashlib.sha256(json.dumps(specs, sort_keys=True).encode()).hexdigest()
        ref, count, replayed = write_registered_table(root, uri, schema=schema,
            schema_name=name, schema_version='v2', rows_factory=factory, fingerprint=fingerprint)
        if count != expected_count:
            raise ValueError(f'{name}: row count differs: {count} != {expected_count}')
        # Compare every reconstructed typed row against the committed output in order.
        # In particular this preserves complete source attributes, not just counts.
        import pyarrow as pa
        expected = iter(factory())
        checked = 0
        for batch in ref.open(root).to_batches(batch_size=4096):
            values = [next(expected) for _ in range(batch.num_rows)]
            if not batch.equals(pa.RecordBatch.from_pylist(values, schema=schema)):
                raise ValueError(f'{name}: readback differs after row {checked}')
            checked += batch.num_rows
        if next(expected, None) is not None:
            raise ValueError('Unexpected extra source rows')
        journal.put(name, {'ref': ref.to_dict(), 'rows': count, 'all_typed_rows_verified': True})
        print(name, count, 'verified', flush=True)

    draft = source(OLD + 'docs_draft.lance')
    def drafts():
        for row in rows(draft):
            p = json.loads(row['payload'])
            if set(p) != {'name', 'kind', 'body'}:
                raise ValueError('Unexpected draft fields')
            yield dict(concept=p['name'], draft_kind=p['kind'], text=p['body'],
                review_status='unreviewed', source_file=row['source_file'], source_row=row['source_row'])
    migrate('knowledge_drafts', 'derived/knowledge_drafts.lance', KNOWLEDGE_DRAFTS, drafts, draft.count_rows())

    preloss = source(OLD + 'preloss_images.lance')
    def observations():
        for row in rows(preloss):
            payload = json.loads(row['payload'])
            s = source_record(payload, system='preloss_collection', source_file=row['source_file'], source_row=row['source_row'])
            yield dict(sha256=payload.get('sha256'), ext=payload.get('ext'), concepts=s['concepts'],
                source=s, source_payload_sha256=hashlib.sha256(row['payload'].encode()).hexdigest())
    migrate('image_observations', 'runs/collection_history/image_observations_before_loss_20260906.lance',
            IMAGE_OBSERVATIONS, observations, preloss.count_rows())

    roster = list(rows(source(OLD + 'rosters.lance')))
    selections = []
    metadata = {}
    for row in roster:
        p = json.loads(row['payload'])
        entries = p['concepts'] if isinstance(p, dict) else p
        name = Path(row['asset_name']).stem
        metadata[name] = {k: v for k, v in p.items() if k != 'concepts'} if isinstance(p, dict) else {}
        for i, entry in enumerate(entries):
            item = {'name': entry} if isinstance(entry, str) else entry
            if set(item) - {'name', 'aliases', 'carriers', 'taxonomy'}:
                raise ValueError('Unexpected concept selection fields')
            carriers = item.get('carriers', [])
            if isinstance(carriers, str):
                carriers = carriers.split('+')
            selections.append(dict(selection_name=name, ordinal=i, concept=item['name'],
                aliases=item.get('aliases', []), carriers=carriers,
                taxonomy_paths=item.get('taxonomy', []), source_file=row['source_file']))
    journal.put('selection_metadata', metadata)
    migrate('concept_selections', 'runs/collection_history/concept_selections_20260906.lance',
            CONCEPT_SELECTIONS, lambda: iter(selections), len(selections))

    taxonomy_uri = next(u for u in specs if u.startswith('raw/taxonomy_sources/'))
    parts = {}
    for row in rows(source(taxonomy_uri)):
        parts.setdefault(row['source_file'], []).append(row)
    joined = {}
    mapping = {'node_path_en':'node_path_en', 'instance清单':'instances_text',
        'instance清单(英文)':'instances_en_text', 'source清单':'source_names_text',
        'source详情(名称;URL;类型)':'source_details_text'}
    for filename, records in sorted(parts.items()):
        records.sort(key=lambda r: r['source_row'])
        reader = csv.DictReader(io.StringIO('\n'.join(r['payload'] for r in records).lstrip('\ufeff')))
        seen = set()
        for i, item in enumerate(reader, 1):
            key = item.pop('node_path')
            if key in seen:
                raise ValueError('Duplicate node within taxonomy source')
            seen.add(key)
            out = joined.setdefault(key, {'node_path':key, 'origins':[]})
            out['origins'].append({'source_file':filename, 'source_row':i})
            for k, value in item.items():
                field = mapping[k]
                if field in out:
                    raise ValueError('Conflicting taxonomy field')
                out[field] = value
    values = list(joined.values())
    migrate('taxonomy_history', 'runs/taxonomy_history/classification_v31_20260824.lance',
            TAXONOMY_HISTORY, lambda: iter(values), len(values))

    protocol_row, = list(rows(source(ANNOTATIONS + 'protocol.lance')))
    protocol = json.loads(protocol_row['payload'])
    protocol_id = hashlib.sha256(json.dumps(protocol, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    fields = ['version', 'describe_prompt', 'match_prompt', 'model', 'author_type', 'human_reviewed']
    typed_protocol = {k: protocol[k] for k in fields}
    typed_protocol.update(protocol_id=protocol_id, source_file=protocol_row['source_file'],
        configuration_json=json.dumps({k:v for k,v in protocol.items() if k not in fields}, ensure_ascii=False, sort_keys=True))
    migrate('annotation_protocols', 'derived/annotations/protocols.lance', ANNOTATION_PROTOCOLS,
            lambda: iter([typed_protocol]), 1)
    import pyarrow as pa
    for name in ('image_descriptions', 'concept_matches'):
        ds = source(ANNOTATIONS + name + '.lance')
        schema = ds.schema.append(pa.field('protocol_id', pa.string(), nullable=False))
        def annotated(ds=ds):
            for row in rows(ds):
                yield {**row, 'protocol_id':protocol_id}
        migrate(name, 'derived/annotations/' + name + '.lance', schema, annotated, ds.count_rows())
    return {k:v for k,v in journal.items().items() if isinstance(v,dict) and 'ref' in v}

if __name__ == '__main__':
    run(resolve_root())
