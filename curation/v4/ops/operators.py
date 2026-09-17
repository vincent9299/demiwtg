"""V4 material stages: reusable business operators, no model calls."""
import asyncio
import hashlib
from pathlib import Path
from curation.v4.contracts import digest, immutable, read
from curation.v4.sources import inspect_source, scan_source

class InspectSource:
    label='inspect_sources';concurrency=2;queue_depth=2;catch=()
    async def __call__(self,row):return await asyncio.to_thread(inspect_source,row)

class CollectRows:
    label='collect_results';concurrency=1;queue_depth=2;catch=()
    def __init__(self):self.results=[]
    async def __call__(self,row):self.results.append(row);return row

class ScanSource:
    label='scan_materials';concurrency=2;queue_depth=2;catch=()
    def __init__(self,run,requests,links,max_rows,code_hash):
        self.run=Path(run);self.requests=requests;self.links=links;self.max_rows=max_rows;self.code_hash=code_hash
        self.reused=0;self.executed=0
    async def __call__(self,source):
        key=digest({'source':source,'requests':self.requests,'links':self.links,'max_rows':self.max_rows,'code':self.code_hash})
        p=self.run/'source_tasks'/f'{key}.json'
        if p.exists():self.reused+=1;return read(p)
        result=await asyncio.to_thread(scan_source,source,self.requests,self.links,self.max_rows)
        immutable(p,result);self.executed+=1;return result


def safe_local(base,relative):
    base=Path(base).resolve();p=(base/relative).resolve()
    if not p.is_relative_to(base):raise ValueError('material path escapes source root')
    return p


def verify_image(record,dataset,blob_roots):
    from PIL import Image
    relative=record.get('path')
    if not relative:return {'status':'missing_locator'}
    for base in [dataset,*blob_roots]:
        try:p=safe_local(base,relative)
        except ValueError:return {'status':'invalid_path'}
        if not p.is_file():continue
        try:
            h=hashlib.sha256()
            with p.open('rb') as f:
                for chunk in iter(lambda:f.read(1024*1024),b''):h.update(chunk)
            actual=h.hexdigest()
            if not record.get('sha256'):return {'status':'missing_expected_hash','path':str(p),'actual_sha256':actual}
            if actual!=record['sha256']:return {'status':'hash_mismatch','path':str(p),'actual_sha256':actual}
            with Image.open(p) as im:im.load();size=list(im.size);fmt=im.format
            return {'status':'verified_bytes','path':str(p),'sha256':actual,'dimensions':size,'format':fmt,
                    'knowledge_support':'not_reviewed','generation_origin':'not_verified'}
        except (OSError,ValueError,Image.DecompressionBombError) as e:return {'status':'decode_error','path':str(p),'error':str(e)}
    return {'status':'not_local','remote_locator':record.get('content_url'),
            'note':'A URL may be a Commons page, not image bytes. COS fetching is not yet connected; absence here is not absence of material.'}

class AssembleMaterial:
    label='assemble_material_bundle';concurrency=1;queue_depth=2;catch=()
    def __init__(self,scans,dataset,blob_roots,max_images):
        self.scans=scans;self.dataset=Path(dataset);self.blob_roots=blob_roots;self.max_images=max_images
    async def __call__(self,task):return await asyncio.to_thread(self.build,task)
    def build(self,task):
        request=task['request'];associations=[];coverage=[];verified=0
        for scan in self.scans:
            kind=scan['source']['kind']
            if kind in ('legacy_concepts','legacy_docs','legacy_images') and request['kind']!='legacy':continue
            if kind not in ('legacy_concepts','legacy_docs','legacy_images') and request['kind']!='qid':continue
            coverage.append({'source_path':scan['source']['path'],'kind':kind,'status':scan['status'],'scan':scan['scan']})
            for m in scan['matches']:
                if m['request']!=request:continue
                a={'kind':kind,**{k:v for k,v in m.items() if k!='request'},'knowledge_support':'not_reviewed'}
                if kind=='legacy_docs':
                    relative=m['record'].get('path')
                    try:
                        if not relative:raise ValueError('missing page path')
                        p=safe_local(self.dataset,relative)
                        raw=p.read_bytes()
                        a['document']={'path':str(p),'sha256':digest(raw),'text':raw.decode('utf-8'),'status':'readable'}
                    except (ValueError,OSError) as e:a['document']={'status':'read_error','error':str(e)}
                if kind in ('legacy_images','qid_images'):
                    if verified<self.max_images:
                        a['bytes']=verify_image(m['record'],self.dataset,self.blob_roots);verified+=1
                    else:a['bytes']={'status':'budget_limited','note':'Image verification limit reached; remaining candidates retained.'}
                    a['caption']={'existing':m['record'].get('caption'),'provenance':'source_record; origin not yet classified','supplemental':None}
                associations.append(a)
        return {**task,'schema':'curation-v4-material-bundle/1','stage':'materials_prepared',
                'knowledge_status':'not_extracted','identity_status':'source_identity_only; cross-source merge not adjudicated',
                'materials':associations,'coverage':coverage,
                'gaps':['Cross-source identity matching/merge remains a separate reviewed step.',
                        'No source or image is approved as knowledge evidence by this preparation flow.']}

class SaveBundle:
    label='save_material_bundle';concurrency=1;queue_depth=2;catch=()
    def __init__(self,run):self.run=Path(run);self.results=[]
    async def __call__(self,row):
        p=self.run/'bundles'/f'{row["task_id"]}.json'
        immutable(p,row)
        self.results.append({'task_id':row['task_id'],'concept_id':row['concept_id'],'request':row['request'],'path':str(p),'materials':len(row['materials'])})
        return row
