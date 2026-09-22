"""Image attributes, deterministic concept projections and reviewed publications."""
import hashlib
import json
from pathlib import Path
import pyarrow as pa

from .image_schema import DESCRIPTION, IMAGE_CURATION

CONFIG_PATH = 'curation/preparation/configs/image_annotation_v2.json'

def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)

def identity(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()

def description(value):
    if value is None: return None
    result = dict(value)
    for key in ('objects', 'text_regions', 'observability_issues'):
        if isinstance(result.get(key),str): result[key] = json.loads(result[key])
    return pa.array([result],type=DESCRIPTION)[0].as_py()

def provenance(row, *, run_id=None, config_id=None, config_path=None):
    return dict(run_id=run_id, config_id=config_id, config_path=config_path,
        source_file=row.get('source_file'), source_row=row.get('source_row'),
        record_sha256=identity(row), details_json=canonical(row))

def description_annotation(row):
    cid=row['protocol_id']
    return dict(annotation_id=identity(['description',row['sha256'],cid]),config_id=cid,
        status=row['status'],description=description(row),attempts=row.get('attempts'),
        provenance=provenance(row,config_id=cid,config_path=CONFIG_PATH))

def concept_annotation(row):
    cid=row['protocol_id']
    return dict(annotation_id=identity(['match',row['sha256'],row['concept'],cid]),
        concept=row['concept'],config_id=cid,status=row.get('match_status'),reason=row.get('reason'),
        provenance=provenance(row,config_id=cid,config_path=CONFIG_PATH))

def assessment(record, release_id, *, source_file=None, source_row=None, run_id=None):
    review=record.get('concept_review') or {}
    published=record.get('publication_status')=='reviewed'
    if published and review.get('decision')!='keep':
        raise ValueError('Publication contradicts concept review')
    metadata=record.get('image_metadata') or {}
    observation={k:v for k,v in record.items() if k not in ('concept_review','visual_support')}
    return dict(assessment_id=identity([release_id,record['concept'],record['sha256']]),
        concept=record['concept'],image_id=record.get('image_id'),
        review_status=review.get('decision') or 'unknown',published=published,
        release_ids=[release_id],identity_relation=review.get('concept_relation'),reason=review.get('reason'),
        visual_support=record.get('visual_support'),description=description(metadata.get('description')),
        human_reviewed=record.get('human_reviewed',False),
        provenance=provenance({'source_file':source_file,'source_row':source_row},run_id=run_id),
        review_json=canonical(review),observation_json=canonical(observation))

def assessment_record(image, item):
    """Project one concept assessment into the business visual-publication contract."""
    record=json.loads(item['observation_json'])
    record.update(sha256=image['sha256'],concept=item['concept'],
        publication_status='reviewed' if item['published'] else 'not_published',
        concept_review=json.loads(item['review_json']), visual_support=item['visual_support'])
    return record

def merged_records(old, new, key):
    result={r[key]:r for r in old or []}
    for row in new or []:
        previous=result.get(row[key])
        if previous is not None and previous!=row:raise ValueError('Conflicting image attribute: '+str(row[key]))
        result[row[key]]=row
    return [result[k] for k in sorted(result)]

def project_image(row):
    """Search lists are projections; callers never update them independently."""
    result=dict(row)
    for name in ('descriptions','concept_matches','concept_assessments'):
        result[name]=result.get(name) or []
    assessments=result['concept_assessments']
    result['concepts']=sorted(set(result.get('concepts') or []) |
        {r['concept'] for r in result['concept_matches']} | {r['concept'] for r in assessments})
    result['published_concepts']=sorted({r['concept'] for r in assessments if r['published']})
    result['release_ids']=sorted({rid for r in assessments for rid in r['release_ids']})
    return result

def merge_curation(previous, incoming):
    out=dict(previous)
    for field,key in [('descriptions','annotation_id'),('concept_matches','annotation_id'),('concept_assessments','assessment_id')]:
        out[field]=merged_records(previous.get(field),incoming.get(field),key)
    return project_image(out)

def latest_images(root):
    from demiflow.lance.registry import Catalog
    from .image_schema import IMAGES_URI
    refs=[r for r in Catalog(root).registered() if r.relative_uri==IMAGES_URI]
    if not refs:raise ValueError('Curated image table is not registered')
    return max(refs,key=lambda r:r.lance_version)


def write_curation(root, rows, *, source_ref):
    """Write only curated images, binding each update to an explicit raw snapshot.

    The source table is never mutated. A record can accumulate results from
    multiple fixed source versions; each result retains its run/config provenance.
    """
    from demiflow.lance.transaction import registered_table_edit
    from demiflow.lance.refs import DatasetRef
    from collect.material_schema import IMAGES_URI as RAW_IMAGES_URI
    from .image_schema import IMAGES_URI, IMAGES
    import lance
    source_ref = source_ref if isinstance(source_ref,DatasetRef) else DatasetRef.from_dict(source_ref)
    if source_ref.relative_uri != RAW_IMAGES_URI or source_ref.schema_name != 'raw_images':
        raise ValueError('Curation requires a fixed raw image DatasetRef')
    source=source_ref.open(root)
    binding=canonical(source_ref.to_dict())
    grouped={}
    for row in rows:
        row={**row,**pa.Table.from_pylist([row],schema=IMAGE_CURATION).to_pylist()[0]}
        key=row['sha256']
        if len(key)!=64 or any(c not in '0123456789abcdef' for c in key):raise ValueError('Invalid image SHA')
        grouped[key]=merge_curation(grouped.get(key,{'sha256':key,'concepts':[]}),row)
    if not grouped:return latest_images(root)
    keys=', '.join("'"+k+"'" for k in grouped)
    found=set(source.to_table(columns=['sha256'],filter=f'sha256 IN ({keys})')['sha256'].to_pylist())
    if found != set(grouped):raise ValueError('Curated images absent from the fixed raw source')
    with registered_table_edit(root,IMAGES_URI,schema_name='curated_images',schema_version='v1') as ds:
        existing={r['sha256']:r for r in ds.to_table(filter=f'sha256 IN ({keys})').to_pylist()} if ds else {}
        changed=[]
        for key,row in grouped.items():
            prior=existing.get(key,{'sha256':key,'concepts':[]})
            merged=merge_curation(prior,row)
            merged['source_refs']=sorted(set(prior.get('source_refs') or [])|{binding})
            if merged!=existing.get(key):changed.append(merged)
        uri=str(Path(root)/IMAGES_URI)
        if changed:
            table=pa.Table.from_pylist(changed,schema=IMAGES)
            if ds is None:lance.write_dataset(table,uri)
            else:ds.merge_insert('sha256').when_matched_update_all().when_not_matched_insert_all().execute(table)
        if ds is None:
            current=lance.dataset(uri)
            for col,kind in [('sha256','BTREE'),('concepts','LABEL_LIST'),('published_concepts','LABEL_LIST'),('release_ids','LABEL_LIST')]:
                current.create_scalar_index(col,kind)
                current=lance.dataset(uri)
    return latest_images(root)
