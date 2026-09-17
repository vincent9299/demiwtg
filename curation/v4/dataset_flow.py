"""Explicit concepts/documents/images datasets, relational joins, shared processing."""
import asyncio
import json
import sqlite3
from pathlib import Path
from demiflow.standalone import local_data
from .contracts import ROOT,read,immutable,run_lock,digest,snapshot,source_code,code_fingerprint
from .concept_flow import ConceptFlow
from .record_flow import RecordStore,CONCEPT_KINDS
from .datasets import Tables,SCHEMAS,SaveTable
from .ops.operators import safe_local,verify_image
from .ops.cleaning import clean_document,VERSION

# Joins are executable SQL, also displayed verbatim in the notebook.
JOINS={
 'selected_document_links':'''SELECT l.concept_ref,l.doc_id,l.method
 FROM document_links l JOIN selected_concepts c ON c.concept_ref=l.concept_ref
 WHERE c.selected=1 AND l.status='source_association_only' ''',
 'selected_image_links':'''SELECT l.concept_ref,l.image_id,l.method
 FROM image_links l JOIN selected_concepts c ON c.concept_ref=l.concept_ref
 WHERE c.selected=1 AND l.status='source_association_only' ''',
 'documents_to_read':'''SELECT d.* FROM documents d WHERE EXISTS
 (SELECT 1 FROM selected_document_links l WHERE l.doc_id=d.doc_id)''',
 'images_to_check':'''SELECT i.* FROM images i WHERE EXISTS
 (SELECT 1 FROM selected_image_links l WHERE l.image_id=i.image_id)''',
 'concept_coverage':'''SELECT c.concept_ref,
 (SELECT count(*) FROM selected_document_links l WHERE l.concept_ref=c.concept_ref),
 (SELECT count(*) FROM selected_image_links l WHERE l.concept_ref=c.concept_ref),
 (SELECT count(*) FROM selected_document_links l JOIN document_texts d USING(doc_id)
  WHERE l.concept_ref=c.concept_ref AND d.status='readable'),
 (SELECT count(*) FROM selected_image_links l JOIN image_checks i USING(image_id)
  WHERE l.concept_ref=c.concept_ref AND i.status='verified_bytes'),
 CASE WHEN EXISTS(SELECT 1 FROM selected_document_links l WHERE l.concept_ref=c.concept_ref)
 OR EXISTS(SELECT 1 FROM selected_image_links l WHERE l.concept_ref=c.concept_ref)
 THEN 'materials_available' ELSE 'no_materials_in_read_scope' END, 'not_extracted'
 FROM selected_concepts c WHERE c.selected=1'''
}
STEPS=['read_sources','select_concepts','join_documents','join_images','read_documents',
       'clean_documents','check_images','summarize_concepts']


class AdaptSource:
    """One adapter instance per source schema; raw preservation is private provenance IO."""
    concurrency=1;queue_depth=8;catch=()
    def __init__(self,tables,kind):self.t=tables;self.kind=kind;self.n=0
    async def __call__(self,input_row):
        r={k:v for k,v in input_row.items() if k not in {'_source_id','_provenance'}}
        p=input_row['_provenance'];sid=input_row['_source_id'];k=self.kind
        common={'source_id':sid,'source_path':p['source_path'],'source_row':p.get('line',p.get('record_index'))}
        self.t.db.execute('INSERT OR IGNORE INTO source_originals VALUES (?,?,?,?)',
            (sid,k,json.dumps(r,ensure_ascii=False),json.dumps(p,ensure_ascii=False)))
        if k in CONCEPT_KINDS:
            value=r.get('name') if k=='legacy_concepts' else r.get('qid')
            if not value:raise ValueError(f'{k}: missing concept name/QID at {p}')
            ref=('legacy:' if k=='legacy_concepts' else 'qid:')+value
            self.t.put('concept_sources',{**common,'concept_ref':ref,'name':r.get('name') or (r.get('zh') or {}).get('title') or (r.get('en') or {}).get('title'),
                'aliases':r.get('aliases',[]),'qid':r.get('qid'),'taxonomy':r.get('taxonomy',[])})
            self.t.put('concepts',{'concept_ref':ref})
            for lang in ('en','zh'):
                page=r.get(lang) or {}
                if page.get('page_id') is not None:self.t.put('concept_pages',{'concept_ref':ref,'lang':lang,'page_id':str(page['page_id']),'source_id':sid})
        elif k in {'legacy_docs','wiki_pages'}:
            self.t.put('documents',{**common,'doc_id':sid,'title':r.get('title'),'url':r.get('url'),
                'path':r.get('path'),'format':'wiki_sections' if k=='wiki_pages' else 'saved_text',
                'lang':r.get('lang'),'page_id':str(r['page_id']) if r.get('page_id') is not None else None,
                'qid':r.get('qid'),'sections':r.get('sections',[])})
            if k=='legacy_docs':refs=['legacy:'+v for v in (r.get('concepts') or r.get('instances') or [])]
            else:refs=['qid:'+r['qid']] if r.get('qid') else []
            for ref in refs:self.t.put('document_links',{'concept_ref':ref,'doc_id':sid,'method':'source_fields','status':'source_association_only'})
        elif k in {'legacy_images','qid_images'}:
            self.t.put('images',{**common,'image_id':sid,'path':r.get('path') or r.get('blob_path'),
                'sha256':r.get('sha256'),'url':r.get('content_url') or r.get('url'),'caption':r.get('caption')})
            refs=['qid:'+r['qid']] if k=='qid_images' and r.get('qid') else ['legacy:'+v for v in (r.get('concepts') or r.get('instances') or [])]
            for ref in refs:self.t.put('image_links',{'concept_ref':ref,'image_id':sid,'method':'source_fields','status':'source_association_only'})
        elif k=='image_roles':self.t.put('image_roles',{**common,**{v:r.get(v) for v in ('qid','property','role','commons_file')}})
        elif k=='gallery_captions':self.t.put('gallery_captions',{**common,**{v:r.get(v) for v in ('qid','commons_file','caption')}})
        elif k=='qid_graph':self.t.put('concept_edges',{**common,'from_qid':r.get('from'),'to_qid':r.get('to'),'relation':r})
        else:raise ValueError(f'Unsupported schema {k}')
        self.n+=1
        if self.n%100==0:self.t.db.commit()
        return None
    async def aclose(self):self.t.db.commit()


class SelectConcept:
    concurrency=1;queue_depth=8;catch=()
    def __init__(self,config):self.config=config
    async def __call__(self,row):
        ref=row['concept_ref'];ids=self.config['ids'];rate=self.config['sample_rate'];reason='selected'
        if ids is not None and ref not in ids:reason='id_filter'
        elif rate<1 and int(digest({'concept_ref':ref,'seed':self.config['seed']})[:16],16)>=int(rate*2**64):reason='concept_sample'
        return {'concept_ref':ref,'selected':int(reason=='selected'),'reason':reason}


class ReadDocument:
    concurrency=1;queue_depth=8;catch=()
    def __init__(self,dataset):self.dataset=dataset
    async def __call__(self,doc):
        try:
            if doc['format']=='wiki_sections':
                text='\n\n'.join((s.get('title','')+'\n'+s.get('text','')).strip() for s in doc['sections']);raw=text.encode()
            else:
                if not doc['path']:raise ValueError('missing document path')
                raw=await asyncio.to_thread(safe_local(self.dataset,doc['path']).read_bytes);text=raw.decode('utf-8')
            return {'doc_id':doc['doc_id'],'text':text,'sha256':digest(raw),'status':'readable','error':None}
        except (OSError,ValueError,UnicodeError) as e:
            return {'doc_id':doc['doc_id'],'text':'','sha256':None,'status':'read_error','error':str(e)}


class CleanDocument:
    concurrency=1;queue_depth=8;catch=()
    async def __call__(self,doc):
        result=await asyncio.to_thread(clean_document,doc['text'])
        return {'doc_id':doc['doc_id'],'text':result['text'],
            'status':result['status'] if doc['status']=='readable' else 'unavailable',
            'warnings':result['warnings']+([doc['error']] if doc['error'] else []),
            'blocks':result['blocks'],'counts':result['counts'],'source_sha256':doc['sha256'],'version':VERSION}


class CheckImage:
    concurrency=1;queue_depth=8;catch=()
    def __init__(self,dataset):self.dataset=dataset
    async def __call__(self,img):
        result=await asyncio.to_thread(verify_image,img,self.dataset,[])
        return {'image_id':img['image_id'],'status':result['status'],'details':result}


class DatasetFlow:
    table_type=Tables
    def __init__(self,run,dataset=ROOT/'datasets/demiwtg',sources=None,ids=None,sample_rate=1.0,seed=42,max_records_per_source=None,group_size=32,project=ROOT,raw_run=None):
        self.raw=ConceptFlow(run,dataset,sources,ids,sample_rate,seed,max_records_per_source,group_size,project)
        self.run=self.raw.run;self.dataset=self.raw.dataset;self.config=self.raw.config;self.raw_run=Path(raw_run).resolve() if raw_run else self.run
        with run_lock(self.run):
            if raw_run:
                old=read(self.raw_run/'record_manifest.json')
                if old['sources']!=self.raw.sources or old['config']['max_records_per_source']!=max_records_per_source:raise ValueError('raw cache scope differs')
                import ast
                def parser(s):return ast.dump(next(n for n in ast.parse(s).body if isinstance(n,ast.FunctionDef) and n.name=='dataset_records'))
                frozen=read(self.raw_run/'record_code_snapshot.json')
                if digest(frozen)!=old['code_hash'] or parser(frozen['record_flow.py'])!=parser(source_code()['record_flow.py']):raise ValueError('raw parser differs')
                for s in self.raw.sources:
                    if snapshot(s['path'])!={k:s[k] for k in ('path','exists','size','mtime_ns','inode') if k in s}:raise ValueError('source changed')
                with sqlite3.connect(f'file:{self.raw_run}/records.sqlite?mode=ro',uri=True) as db:
                    if not db.execute("SELECT 1 FROM completed WHERE stage='read_records'").fetchone():raise ValueError('raw read incomplete')
            immutable(self.run/'dataset_manifest.json',{'schema':'named-datasets/1','raw_run':str(self.raw_run),
                'raw_manifest_hash':digest(read(self.raw_run/'record_manifest.json')),'joins':JOINS,'schemas':SCHEMAS})
        self.tables=self.table_type(self.run/'datasets.sqlite')

    @classmethod
    def open_saved(cls,run):
        obj=cls.__new__(cls);obj.run=Path(run).resolve();m=read(obj.run/'dataset_manifest.json')
        obj.raw=ConceptFlow.open_saved(run);obj.dataset=obj.raw.dataset;obj.config=obj.raw.config;obj.raw_run=Path(m['raw_run'])
        obj.tables=obj.table_type(obj.run/'datasets.sqlite');return obj

    def dataset_for(self,table):return self.tables.dataset(table)
    def describe(self,table):return self.tables.describe(table)
    def view(self,table,limit=100):
        if limit<0:raise ValueError('limit must be nonnegative')
        return list(self.tables.rows(table,f'SELECT * FROM "{table}" LIMIT ?',(limit,)))
    def close(self):self.tables.close()

    def _source_dataset(self,source):
        # A separate lazy source Dataset per file schema. Generic cache stays private here.
        def rows():
            with sqlite3.connect(f'file:{self.raw_run}/records.sqlite?mode=ro',uri=True) as db:
                for (body,) in db.execute("SELECT body FROM outputs WHERE stage='read_records' AND json_extract(body,'$.provenance.source_path')=?",(source['path'],)):
                    row=json.loads(body)
                    if {'_source_id','_provenance'} & row['record'].keys():raise ValueError('reserved provenance field in source')
                    yield {**row['record'],'_source_id':row['record_id'],'_provenance':row['provenance']}
        return local_data().from_iter(rows)

    def _save(self,table,dataset,op=None):
        # Filter completed keys before expensive operators; same code for full/sample input.
        if op:dataset=dataset.map_async(op)
        dataset.map_async(SaveTable(self.tables,table)).run_stream(log_every=0)

    def _pending(self,input_table,output_table,sql=None):
        key=SCHEMAS[output_table][1][0]
        query=f'SELECT x.* FROM ({sql or "SELECT * FROM "+input_table}) x WHERE NOT EXISTS (SELECT 1 FROM {output_table} y WHERE y.{key}=x.{key})'
        return self.tables.dataset(input_table,query)

    async def step(self,step):
        if step not in STEPS:raise ValueError('unknown stage')
        if step=='read_sources' and self.raw_run==self.run:await self.raw.step('read_records')
        def work():
            self.raw._check()
            with run_lock(self.run):
                t=self.tables;idx=STEPS.index(step)
                if t.db.execute('SELECT 1 FROM completed WHERE step=?',(step,)).fetchone():return {'step':step,'reused':True}
                if idx and not t.db.execute('SELECT 1 FROM completed WHERE step=?',(STEPS[idx-1],)).fetchone():raise ValueError(f'Run {STEPS[idx-1]} first')
                if step=='read_sources':
                    with run_lock(self.raw_run) if self.raw_run!=self.run else _no_lock():
                        for source in self.raw.sources:
                            self._source_dataset(source).map_async(AdaptSource(t,source['kind'])).run_stream(log_every=0)
                    t.db.execute('CREATE INDEX IF NOT EXISTS pages_lookup ON concept_pages(lang,page_id)')
                    # Explicit language/page join; more than one distinct QID remains ambiguous.
                    t.db.execute('''INSERT OR IGNORE INTO document_links
                      SELECT DISTINCT p.concept_ref,d.doc_id,'language_page_id',
                      CASE WHEN (SELECT count(DISTINCT p2.concept_ref) FROM concept_pages p2
                           WHERE p2.lang=d.lang AND p2.page_id=d.page_id)>1 THEN 'ambiguous_mapping' ELSE 'source_association_only' END
                      FROM documents d JOIN concept_pages p ON d.lang=p.lang AND d.page_id=p.page_id
                      WHERE d.format='wiki_sections' AND d.qid IS NULL''')
                    for entity,idcol,links,unmatched in [('documents','doc_id','document_links','unmatched_documents'),('images','image_id','image_links','unmatched_images')]:
                        t.db.execute(f'CREATE INDEX IF NOT EXISTS {links}_material ON {links}({idcol})')
                        t.db.execute(f'''INSERT OR IGNORE INTO {unmatched}
                         SELECT e.{idcol},COALESCE(l.concept_ref,''),
                         CASE WHEN l.{idcol} IS NULL THEN 'no_source_link' WHEN l.status='ambiguous_mapping' THEN 'ambiguous_mapping' ELSE 'unknown_concept' END
                         FROM {entity} e LEFT JOIN {links} l ON l.{idcol}=e.{idcol}
                         LEFT JOIN concepts c ON c.concept_ref=l.concept_ref
                         WHERE l.{idcol} IS NULL OR c.concept_ref IS NULL OR l.status='ambiguous_mapping' ''')
                        t.db.execute(f'CREATE INDEX IF NOT EXISTS {links}_material ON {links}({idcol})')
                elif step=='select_concepts':
                    self._save('selected_concepts',self._pending('concepts','selected_concepts'),SelectConcept(self.config))
                    for ref in self.config['ids'] or []:
                        if not t.db.execute('SELECT 1 FROM concepts WHERE concept_ref=?',(ref,)).fetchone():t.put('missing_concepts',{'concept_ref':ref,'reason':'not_in_read_concept_scope'})
                elif step in {'join_documents','join_images'}:
                    table='selected_document_links' if step=='join_documents' else 'selected_image_links'
                    self._save(table,t.dataset(table,JOINS[table]))
                    key='doc_id' if step=='join_documents' else 'image_id'
                    t.db.execute(f'CREATE INDEX IF NOT EXISTS {table}_material ON {table}({key})')
                elif step=='read_documents':self._save('document_texts',self._pending('documents','document_texts',JOINS['documents_to_read']),ReadDocument(self.dataset))
                elif step=='clean_documents':self._save('clean_documents',self._pending('document_texts','clean_documents'),CleanDocument())
                elif step=='check_images':self._save('image_checks',self._pending('images','image_checks',JOINS['images_to_check']),CheckImage(self.dataset))
                else:self._save('concept_coverage',t.dataset('concept_coverage',JOINS['concept_coverage']))
                t.db.execute('INSERT INTO completed VALUES (?)',(step,));t.db.commit()
                return {'step':step,'reused':False}
        return await asyncio.to_thread(work)

    def iter_knowledge_inputs(self):
        """Compatibility boundary only: typed tables -> existing model operators."""
        from .contracts import IdentityRegistry
        from .ops.knowledge_stages import material_id
        t=self.tables
        if not t.db.execute("SELECT 1 FROM completed WHERE step='summarize_concepts'").fetchone():raise ValueError('Run summarize_concepts first')
        registry=IdentityRegistry(self.raw.project/'state/curation/v4/identities.sqlite')
        def original(sid):
            kind,raw,p=t.db.execute('SELECT source_kind,raw_json,provenance FROM source_originals WHERE source_id=?',(sid,)).fetchone()
            return {'record_id':sid,'kind':kind,'record':json.loads(raw),'provenance':json.loads(p)}
        try:
            for c in t.rows('concept_coverage'):
                ref=c['concept_ref'];kind,value=ref.split(':',1);request={'kind':kind,'value':value};cid=registry.get(request)
                identities=[original(r[0]) for r in t.db.execute('SELECT source_id FROM concept_sources WHERE concept_ref=?',(ref,))]
                def materials():
                    for (sid,) in t.db.execute('SELECT doc_id FROM selected_document_links WHERE concept_ref=? ORDER BY doc_id',(ref,)):
                        m=original(sid)
                        raw=next(t.rows('document_texts','SELECT * FROM document_texts WHERE doc_id=?',(sid,)))
                        clean=next(t.rows('clean_documents','SELECT * FROM clean_documents WHERE doc_id=?',(sid,)))
                        m['document']={'text':raw['text'],'status':raw['status'],'sha256':raw['sha256']}
                        m['cleaning']={**clean,'source_locator':m['provenance']}
                        yield m
                    for (sid,) in t.db.execute('SELECT image_id FROM selected_image_links WHERE concept_ref=? ORDER BY image_id',(ref,)):
                        m=original(sid);m['bytes']=next(t.rows('image_checks','SELECT * FROM image_checks WHERE image_id=?',(sid,)))['details'];yield m
                def wrap(batch):
                    task=digest({'concept_id':cid,'materials':[m['record_id'] for m in batch]})[:20]
                    mats=identities+batch
                    result={'record_id':task,'case_id':task,'task_id':task,'concept_ref':ref,
                        'bundle':{'concept_id':cid,'request':request,'materials':mats},'cleaned_materials':mats}
                    if not batch:result['blocked']={'stage':'join_materials','reason':'no_materials_in_read_scope'}
                    return result
                batch=[];had=False
                for m in materials():
                    batch.append(m);had=True
                    if len(batch)==self.config['group_size']:yield wrap(batch);batch=[]
                if batch or not had:yield wrap(batch)
        finally:registry.close()

    async def knowledge_step(self,stage,config):
        # Generic envelope remains private to old model prompt/caching compatibility.
        class Bridge(ConceptFlow):
            def iter_groups(bridge):return self.iter_knowledge_inputs()
        bridge=Bridge.__new__(Bridge);bridge.__dict__.update(self.raw.__dict__)
        result=await bridge.knowledge_step(stage,config)
        if stage=='export':await asyncio.to_thread(self._save_knowledge_tables)
        return result

    def _save_knowledge_tables(self):
        from .ops.knowledge_stages import material_id
        with run_lock(self.run):
            store=RecordStore(self.run);t=self.tables
            try:
                for row in store.iter('export'):
                    task=row['task_id'];ref=row['concept_ref'];pack=row.get('material_pack',{})
                    mats={material_id(m):m['record_id'] for m in row['cleaned_materials']}
                    passages={p['source_id']:p for p in pack.get('passages',[])}
                    facts=row.get('knowledge',{}).get('facts',[])
                    for f in facts:
                        kid=task+':'+f['fact_id']
                        t.put('knowledge',{'knowledge_id':kid,'task_id':task,'statement':f['statement'],
                            'conditions':f['conditions'],'exceptions':f['exceptions'],'review_status':'machine_candidate'})
                        t.put('concept_knowledge',{'concept_ref':ref,'knowledge_id':kid})
                        for e in f['evidence']:
                            p=passages[e['source_id']]
                            t.put('knowledge_sources',{'knowledge_id':kid,'doc_id':mats[p['material_id']],
                                'passage_id':p['source_id'],'quote':e['quote'],'start':p['start'],'end':p['end']})
                    images={i['image_id']:i['record_id'] for i in pack.get('images',[])}
                    for e in row.get('image_evidence',{}).get('support',[]):
                        t.put('image_support',{'knowledge_id':task+':'+e['fact_id'],'image_id':images[e['image_id']],
                            **{k:e[k] for k in ('status','region','supports','limitations')}})
                    t.put('knowledge_tasks',{'task_id':task,'concept_ref':ref,
                        'status':'blocked' if row.get('blocked') else 'needs_joint_review','blocked':row.get('blocked'),
                        'conflicts':row.get('knowledge',{}).get('unresolved_conflicts',[]),
                        'coverage_note':row.get('knowledge',{}).get('coverage_note','Not extracted')})
                t.db.commit()
            finally:store.close()


def _no_lock():
    from contextlib import nullcontext
    return nullcontext()


def main():
    import argparse
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--run',type=Path,required=True);p.add_argument('--raw-run',type=Path)
    p.add_argument('--dataset',type=Path,default=ROOT/'datasets/demiwtg')
    p.add_argument('--source',type=Path,action='append');p.add_argument('--id',action='append',dest='ids')
    p.add_argument('--sample-rate',type=float,default=1.0);p.add_argument('--seed',type=int,default=42)
    p.add_argument('--max-records-per-source',type=int);p.add_argument('--group-size',type=int,default=32)
    p.add_argument('--through',choices=STEPS,default='summarize_concepts')
    a=p.parse_args();f=DatasetFlow(a.run,a.dataset,a.source,a.ids,a.sample_rate,a.seed,a.max_records_per_source,a.group_size,raw_run=a.raw_run)
    async def run():
        for step in STEPS:
            print(await f.step(step),flush=True)
            if step==a.through:break
    try:asyncio.run(run())
    finally:f.close()

if __name__=='__main__':main()
