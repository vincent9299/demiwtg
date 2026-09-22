"""Batch ingestion into the authoritative image/document entity tables.

Transport artifacts are inputs only. Source associations are merged by stable
source record ID, and additional concepts never create duplicate image rows.
"""
from pathlib import Path
import lance
import re
import pyarrow as pa
from demiflow.lance.transaction import registered_table_edit
from .material_schema import IMAGES,DOCUMENTS,IMAGES_URI,DOCUMENTS_URI
from .materials import sha


def image_table(rows):
    return pa.Table.from_arrays([lance.blob_array([r.get(f.name) for r in rows]) if f.name=='data'
        else pa.array([r.get(f.name) for r in rows],type=f.type) for f in IMAGES],schema=IMAGES)


def write_images(root,rows):
    """Rows follow IMAGES; data is bytes or None, sources contain stable IDs."""
    root=Path(root);batch=[]
    for row in rows:
        row={**pa.Table.from_pylist([{k:v for k,v in row.items() if k!='data'}],schema=pa.schema([f for f in IMAGES if f.name!='data'])).to_pylist()[0], 'data':row.get('data')}
        if not isinstance(row['sha256'], str) or not re.fullmatch(r'[0-9a-f]{64}', row['sha256']):
            raise ValueError('Invalid image SHA')
        payload = row.get('data')
        if payload is not None and sha(payload) != row['sha256']: raise ValueError('Image content SHA mismatch')
        if row['availability'] not in {'available', 'metadata_only'} or row['storage_mode'] != 'lance_blob':
            raise ValueError('Invalid image storage status')
        if (payload is not None) != (row['availability'] == 'available') or row['byte_size'] != (len(payload) if payload is not None else 0):
            raise ValueError('Image bytes contradict availability or size')
        if row['concepts'] is None or row['sources'] is None: raise ValueError('Image associations must be lists')
        batch.append(row)
    if not batch:return
    uri=IMAGES_URI
    with registered_table_edit(root,uri,schema_name='raw_images',schema_version='v1') as ds:
        current={}
        if ds:
            keys=', '.join("'"+r['sha256']+"'" for r in batch)
            for r in ds.to_table(columns=[f.name for f in IMAGES if f.name!='data'],filter=f'sha256 IN ({keys})').to_pylist():current[r['sha256']]=r
        pending={}
        for row in batch:
            key=row['sha256'];previous=pending.get(key) or current.get(key)
            if previous:
                sources={r['source_record_id']:r for r in previous['sources']}
                for source in row['sources']:
                    if source['source_record_id'] in sources and sources[source['source_record_id']]!=source:
                        raise ValueError('Source ID content conflict')
                    sources[source['source_record_id']]=source
                merged={**previous,'sources':sorted(sources.values(),key=lambda r:r['source_record_id']),
                        'concepts':sorted(set(previous['concepts'])|set(row['concepts']))}
                if previous['availability']!='available' and row.get('data') is not None:
                    merged.update({k:row[k] for k in ('data','byte_size','ext','availability','resolution')})
                pending[key]=merged
            else:pending[key]=row
        new=[r for key,r in pending.items() if key not in current]
        changed=[{k:v for k,v in r.items() if k!='data'} for key,r in pending.items() if key in current and {k:v for k,v in r.items() if k!='data'}!=current[key]]
        if ds is None:
            created = lance.write_dataset(image_table(new),str(root/uri))
            for column,kind in [('sha256','BTREE'),('concepts','LABEL_LIST')]:
                created.create_scalar_index(column,kind)
                created=lance.dataset(str(root/uri))
            return
        if new:lance.write_dataset(image_table(new),str(root/uri),mode='append')
        if changed:
            update_schema=pa.schema([f for f in IMAGES if f.name!='data'])
            lance.dataset(str(root/uri)).merge_insert('sha256').when_matched_update_all().write_mode('rewrite_columns').execute(pa.Table.from_pylist(changed,schema=update_schema))
        supplied=[{'sha256':key,'data':row['data']} for key,row in pending.items() if key in current and current[key]['availability']!='available' and row.get('data') is not None]
        if supplied:
            blob_table=pa.Table.from_arrays([pa.array([r['sha256'] for r in supplied]),lance.blob_array([r['data'] for r in supplied])],names=['sha256','data'])
            lance.dataset(str(root/uri)).merge_insert('sha256').when_matched_update_all().execute(blob_table)

def write_documents(root,rows):
    root=Path(root)
    rows = list(rows)
    if not rows: return
    uri=DOCUMENTS_URI
    with registered_table_edit(root,uri,schema_name='raw_documents',schema_version='v1') as ds:
        typed = pa.Table.from_pylist(rows,schema=DOCUMENTS).to_pylist()
        for row in typed:
            if not isinstance(row['document_id'], str) or not re.fullmatch(r'[0-9a-f]{64}', row['document_id']): raise ValueError('Invalid document ID')
        current = {}
        if ds:
            keys = ', '.join("'"+row['document_id']+"'" for row in typed)
            current = {r['document_id']:r for r in ds.to_table(filter=f'document_id IN ({keys})').to_pylist()}
        merged={}
        for row in typed:
            key=row['document_id']
            if not isinstance(key, str) or not re.fullmatch(r'[0-9a-f]{64}', key): raise ValueError('Invalid document ID')
            if row['concepts'] is None or row['sources'] is None: raise ValueError('Document associations must be lists')
            text=row.get('text')
            if text is None:text='\n\n'.join((s['title']+'\n'+s['text']).strip() for s in (row.get('sections') or []))
            if row['content_status']=='available' and sha(text)!=row['content_sha256']:raise ValueError('Document content SHA mismatch')
            prior=merged.get(key)
            if prior is None: prior = current.get(key)
            if prior:
                if prior['content_sha256']!=row['content_sha256'] or prior['source_identity']!=row['source_identity']:
                    raise ValueError('Document version identity conflict')
                sources={s['source_record_id']:s for s in prior['sources']}
                for source in row['sources']:
                    if source['source_record_id'] in sources and sources[source['source_record_id']]!=source:raise ValueError('Source ID content conflict')
                    sources[source['source_record_id']]=source
                row={**prior,'sources':sorted(sources.values(),key=lambda r:r['source_record_id']),
                     'concepts':sorted(set(prior['concepts'])|set(row['concepts']))}
            merged[key]=row
        table=pa.Table.from_pylist(list(merged.values()),schema=DOCUMENTS)
        if ds is None:
            created = lance.write_dataset(table,str(root/uri))
            created.create_scalar_index('document_id', 'BTREE', name='document_id_lookup')
            created.create_scalar_index('concepts', 'LABEL_LIST')
        else:ds.merge_insert('document_id').when_matched_update_all().when_not_matched_insert_all().execute(table)
