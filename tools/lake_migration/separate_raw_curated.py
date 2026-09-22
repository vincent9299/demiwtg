"""Correct the over-broad entity merge, preserving raw/curated ownership.

prepare clones immutable raw files, projects only curation attributes into a
separate table, and verifies all values. cutover requires consumer/pixel proof.
"""
import argparse
import hashlib
import io
import json
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

import lance
from PIL import Image
from demiflow.lance.consolidate import consolidate_local_tables
from demiflow.lance.records import LanceRecordStore
from demiflow.lance.refs import DatasetRef
from demiflow.lance.registry import Catalog, ReleaseRegistry, write_registered_table
from demiflow.lance.storage import schema_hash
from demiflow.lance.maintenance import retire_tables
from collect.material_schema import IMAGES as RAW_IMAGES, IMAGES_URI as RAW_URI, DOCUMENTS_URI
from curation.preparation.image_schema import IMAGES, IMAGES_URI, IMAGE_CURATION
from curation.preparation.articles import ARTICLES_URI
from curation.preparation.images import canonical, project_image, identity

OP = 'raw_curated_separation_20260921'
VISUAL_RELEASE = 'visual_curated_20260921'
ARTICLE_RELEASE = 'knowledge_curated_20260921'
RAW_RELEASE = 'raw_materials_20260921'
OLD_RELEASES = ['visual_images_20260921','knowledge_articles_20260921','material_entities_20260921']
OLD_URIS = ['images.lance','documents.lance','articles.lance']


def journal(root):
    return LanceRecordStore(root, f'runs/maintenance/{OP}/records.lance')


def register(root, uri, name):
    ds=lance.dataset(str(Path(root)/uri))
    ref=DatasetRef(uri.removesuffix('.lance'),uri,ds.version,name,'v1',schema_hash(ds.schema),ds.count_rows())
    Catalog(root).register(ref)
    return ref


def add_indexes(root, uri, indices):
    for col, kind in indices:
        lance.dataset(str(Path(root)/uri)).create_scalar_index(col,kind,replace=True)


def raw_fragments(ds):
    # Original raw fields occupy IDs through the raw schema; check every shared
    # immutable file plus deletion mask, not just the row counts.
    return [dict(id=f.metadata.to_json()['id'],files=f.metadata.to_json()['files'],
                 deletion_file=f.metadata.to_json().get('deletion_file')) for f in ds.get_fragments()]


def rows(ds, columns=None):
    for batch in ds.scanner(columns=columns,batch_size=256).to_batches():
        yield from batch.to_pylist()


def checksum(records):
    count=0; total=0
    for row in records:
        count+=1; total=(total+int(identity(row),16)) % (1<<256)
    return {'rows':count,'multiset_sha256_sum':f'{total:064x}'}


def prepare(root):
    root=Path(root); j=journal(root)
    if not j.get('inputs'):
        refs=Catalog(root).registered()
        j.put('inputs',{uri:max((r for r in refs if r.relative_uri==uri),key=lambda r:r.lance_version).to_dict() for uri in OLD_URIS})
    inputs=j.get('inputs')
    def source(uri):return DatasetRef.from_dict(inputs[uri]).open(root)
    if not j.get('raw_images'):
        current=source('images.lance')
        # This saved version is the exact raw snapshot before attributes were added.
        candidates=[v['version'] for v in current.versions() if v['version'] < current.version]
        original=next((lance.dataset(current.uri,version=v) for v in sorted(candidates,reverse=True)
            if lance.dataset(current.uri,version=v).schema.equals(RAW_IMAGES,check_metadata=True)),None)
        if original is None:raise ValueError('No exact raw snapshot; explicit projection required')
        before=raw_fragments(original);after=raw_fragments(current)
        if len(before)!=len(after) or original.count_rows()!=current.count_rows():raise ValueError('Raw snapshot changed')
        for a,b in zip(before,after):
            if a['id']!=b['id'] or a['deletion_file']!=b['deletion_file'] or any(f not in b['files'] for f in a['files']):
                raise ValueError('Raw source files changed since attribute merge')
        print('Raw snapshot verified; cloning independent raw table',flush=True)
        ds=consolidate_local_tables([original],root/RAW_URI)
        add_indexes(root,RAW_URI,[('sha256','BTREE'),('concepts','LABEL_LIST')])
        ref=register(root,RAW_URI,'raw_images')
        j.put('raw_images',{'ref':ref.to_dict(),'original_version':original.version,'raw_files_and_masks_equal':True})
        print('raw images ready',ref.row_count,flush=True)
    raw_ref=DatasetRef.from_dict(j.get('raw_images')['ref'])
    if not j.get('documents'):
        consolidate_local_tables([source('documents.lance')],root/DOCUMENTS_URI)
        add_indexes(root,DOCUMENTS_URI,[('document_id','BTREE'),('concepts','LABEL_LIST')])
        ref=register(root,DOCUMENTS_URI,'raw_documents')
        j.put('documents',{'ref':ref.to_dict(),'same_immutable_files':True})
        print('raw documents ready',ref.row_count,flush=True)
    if not j.get('curated_images'):
        counts={k:0 for k in ('descriptions','concept_matches','concept_assessments')}
        binding=canonical(raw_ref.to_dict())
        def projected():
            scanned=0
            for row in rows(source('images.lance'),['sha256',*IMAGE_CURATION.names]):
                if not any(row[k] for k in counts):continue
                for k in counts:counts[k]+=len(row[k] or [])
                # concepts are derived from curation, not copied from collection.
                value=project_image({**row,'concepts':[],'source_refs':[binding]})
                scanned+=1
                if scanned%100000==0:print('curated images',scanned,flush=True)
                yield value
        ref,n,_=write_registered_table(root,IMAGES_URI,schema=IMAGES,schema_name='curated_images',schema_version='v1',
            rows_factory=projected,fingerprint=identity([inputs['images.lance'],raw_ref.to_dict(),'separate-curation-v1']),max_rows_per_batch=256)
        # Full source/result equality, including immutable publication membership.
        print('checking all curated values',flush=True)
        expected=checksum(projected())
        actual=checksum(rows(ref.open(root)))
        if actual!=expected:raise ValueError('Curated content mismatch')
        counts={k:sum(len(row[k] or []) for row in rows(ref.open(root),[k])) for k in counts}
        add_indexes(root,IMAGES_URI,[('sha256','BTREE'),('concepts','LABEL_LIST'),('published_concepts','LABEL_LIST'),('release_ids','LABEL_LIST')])
        ref=register(root,IMAGES_URI,'curated_images')
        j.put('curated_images',{'ref':ref.to_dict(),'full_content_verified':actual,'annotation_counts':counts,
            'source_binding':'migration snapshot; original historical runtime identity is preserved in provenance'})
        print('curated ready',n,counts,flush=True)
    if not j.get('articles'):
        consolidate_local_tables([source('articles.lance')],root/ARTICLES_URI)
        add_indexes(root,ARTICLES_URI,[('article_id','BTREE'),('concept','BTREE'),('release_ids','LABEL_LIST')])
        ref=register(root,ARTICLES_URI,'articles')
        if checksum(rows(ref.open(root)))!=checksum(rows(source('articles.lance'))):raise ValueError('Article content mismatch')
        j.put('articles',{'ref':ref.to_dict(),'full_content_verified':True})
        print('articles ready',ref.row_count,flush=True)
    return {k:j.get(k) for k in ('raw_images','curated_images','documents','articles')}


def verify_pixels(root, after=False):
    from collect.assets import AssetReader
    j=journal(root);raw=DatasetRef.from_dict(j.get('raw_images')['ref']);ds=raw.open(root)
    cur=DatasetRef.from_dict(j.get('curated_images')['ref']).open(root)
    keys=set(cur.to_table(columns=['sha256'],filter='array_length(published_concepts) > 0')['sha256'].to_pylist())
    visual_count=len(keys);figures=0
    art=DatasetRef.from_dict(j.get('articles')['ref']).open(root)
    for row in rows(art,['illustrations']):
        for image in row['illustrations'] or []:keys.add(image['sha256']);figures+=1
    for fragment in ds.get_fragments():
        keys.update(ds.scanner(columns=['sha256'],fragments=[fragment],filter="availability = 'available'",limit=1).to_table()['sha256'].to_pylist())
    reader=AssetReader(datasets_root=root,version=raw.lance_version)
    def check(key):
        item=reader.resolve(key)
        if item.status!='ok':return {'sha256':key,'error':item.status}
        try:
            with Image.open(io.BytesIO(item.data)) as image:image.verify()
        except Exception as exc:return {'sha256':key,'error':str(exc)}
    errors=[]
    with ThreadPoolExecutor(max_workers=8) as pool:
        for i,error in enumerate(pool.map(check,sorted(keys)),1):
            if error:errors.append(error)
            if i%1000==0:print('pixels',i,'/',len(keys),flush=True)
    if errors:raise ValueError(str(errors))
    proof={'raw_ref':raw.to_dict(),'visual_unique':visual_count,'article_figures':figures,'verified_unique':len(keys),'errors':errors}
    j.put('pixels_after' if after else 'pixels',proof)
    return proof


def cutover(root):
    root=Path(root);j=journal(root)
    if not j.get('acceptance') or not j.get('pixels'):raise ValueError('Acceptance and pixel proofs required')
    if not j.get('retired'):
        for uri,spec in j.get('inputs').items():
            if (root/uri).exists() and lance.dataset(str(root/uri)).version!=spec['lance_version']:raise ValueError('Source advanced: '+uri)
        # Retire obsolete publication refs before registering successors. Their
        # selection IDs stay in immutable historical records inside the new tables.
        result=retire_tables(root,table_uris=OLD_URIS,release_ids=OLD_RELEASES,operation_id=OP+'_retirement',
            reason='User corrected scope: raw source tables must be separate from curated output; full value, byte and consumer verification passed')
        j.put('retired',result)
    refs={k:DatasetRef.from_dict(j.get(k)['ref']) for k in ('raw_images','curated_images','documents','articles')}
    for rid,kind,keys,selection in [(RAW_RELEASE,'materials',['raw_images','documents'],None),
        (VISUAL_RELEASE,'visual_materials',['curated_images'],'visual_images_20260921'),
        (ARTICLE_RELEASE,'knowledge',['articles'],'knowledge_articles_20260921')]:
        ReleaseRegistry(root).register(rid,release_kind=kind,table_refs=[refs[k] for k in keys],pipeline_run=OP,
            validation={'selection_release_id':selection,'audit_record':j.relative_uri})
    for ref in Catalog(root).registered():ref.open(root)
    j.put('complete',{'all_surviving_refs_open':True,'old_tables_retired':OLD_URIS})
    return j.get('complete')


if __name__=='__main__':
    from project import resolve_root
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('action',choices=['prepare','pixels','pixels_after','cutover']);a=p.parse_args()
    root=resolve_root()
    print(canonical(prepare(root) if a.action=='prepare' else verify_pixels(root,a.action=='pixels_after') if a.action.startswith('pixels') else cutover(root)))
