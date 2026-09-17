"""Record-first dataset processing using demiflow and disk-backed stage outputs.

The only subset policy is SelectInputRecords. Disabling IDs, sampling and read
limits uses the same operators. Existing source associations are not adjudication.
"""
import asyncio
import json
import sqlite3
from pathlib import Path
from demiflow.standalone import local_data
from .contracts import (ROOT,code_fingerprint,digest,immutable,read,run_lock,
                        runtime_version,snapshot)
from .sources import discover,open_bytes
from .ops.operators import safe_local,verify_image
from .ops.cleaning import clean_materials

CONCEPT_KINDS={'legacy_concepts','qid_concepts','qid_concepts_base'}
MATERIAL_KINDS={'legacy_docs','legacy_images','wiki_pages','qid_images',
                'image_roles','gallery_captions','qid_graph'}
STEPS=['read_records','index_concepts','attach_concept_ids','select_input',
       'read_documents','clean_documents','group_materials']


def dumps(v):return json.dumps(v,ensure_ascii=False,separators=(',',':'))


class RecordStore:
    """Run-local material/association storage; cursors never materialize a whole table."""
    def __init__(self,run):
        self.path=Path(run)/'records.sqlite';self.path.parent.mkdir(parents=True,exist_ok=True)
        self.db=sqlite3.connect(self.path,check_same_thread=False)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS outputs(stage TEXT,id TEXT,body TEXT,PRIMARY KEY(stage,id));
        CREATE TABLE IF NOT EXISTS concepts(ref TEXT,record_id TEXT,PRIMARY KEY(ref,record_id));
        CREATE TABLE IF NOT EXISTS pages(lang TEXT,page_id TEXT,ref TEXT,PRIMARY KEY(lang,page_id,ref));
        CREATE TABLE IF NOT EXISTS associations(ref TEXT,record_id TEXT,PRIMARY KEY(ref,record_id));
        CREATE TABLE IF NOT EXISTS source_status(path TEXT PRIMARY KEY,body TEXT);
        CREATE TABLE IF NOT EXISTS completed(stage TEXT PRIMARY KEY,count INTEGER);
        ''')
    def close(self):self.db.close()
    def put(self,stage,row):
        key=row['record_id']
        # Source record/provenance lives once in read_records; later tables store additions.
        stored={k:v for k,v in row.items() if k not in {'kind','record','provenance'}} if stage in {'attach_concept_ids','select_input','associate_materials','read_documents','clean_documents'} else row
        body=dumps(stored)
        old=self.db.execute('SELECT body FROM outputs WHERE stage=? AND id=?',(stage,key)).fetchone()
        if old:
            if old[0]!=body:raise ValueError('Changed record output; use a new run')
            return
        self.db.execute('INSERT INTO outputs VALUES (?,?,?)',(stage,key,body))
    def get(self,stage,key):
        r=self.db.execute('SELECT body FROM outputs WHERE stage=? AND id=?',(stage,key)).fetchone()
        if not r:return None
        row=json.loads(r[0])
        if stage in {'attach_concept_ids','select_input','associate_materials','read_documents','clean_documents'}:
            return {**self.get('read_records',key),**row}
        return row
    def count(self,stage):return self.db.execute('SELECT COUNT(*) FROM outputs WHERE stage=?',(stage,)).fetchone()[0]
    def iter(self,stage):
        # Separate read connection so the sink can commit while this cursor streams.
        with sqlite3.connect(self.path) as db:
            for key,body in db.execute('SELECT id,body FROM outputs WHERE stage=? ORDER BY id',(stage,)):
                row=json.loads(body)
                if stage in {'attach_concept_ids','select_input','associate_materials','read_documents','clean_documents'}:
                    raw=db.execute("SELECT body FROM outputs WHERE stage='read_records' AND id=?",(key,)).fetchone()
                    row={**json.loads(raw[0]),**row}
                yield row
    def refs(self,row):
        r=row['record'];kind=row['kind'];refs=[]
        if kind=='legacy_concepts' and r.get('name'):refs=['legacy:'+r['name']]
        elif kind in CONCEPT_KINDS and r.get('qid'):refs=['qid:'+r['qid']]
        elif kind in {'legacy_docs','legacy_images'}:
            refs=['legacy:'+x for x in (r.get('concepts') or r.get('instances') or []) if isinstance(x,str)]
        elif kind=='wiki_pages':
            if r.get('qid'):refs=['qid:'+r['qid']]
            else:refs=[x[0] for x in self.db.execute('SELECT ref FROM pages WHERE lang=? AND page_id=?',(r.get('lang'),str(r.get('page_id'))))]
        elif kind=='qid_graph':refs=['qid:'+r[k] for k in ('from','to') if r.get(k)]
        elif r.get('qid'):refs=['qid:'+r['qid']]
        return sorted(set(refs))


class PersistRecords:
    concurrency=1;queue_depth=8;catch=()
    def __init__(self,store,stage):self.store=store;self.stage=stage;self.n=0
    async def __call__(self,row):
        self.store.put(self.stage,row);self.n+=1
        if self.n%100==0:self.store.db.commit()
        return row
    async def aclose(self):self.store.db.commit()


class IndexConceptPages:
    concurrency=1;queue_depth=8;catch=()
    def __init__(self,store):self.store=store
    async def __call__(self,row):
        refs=self.store.refs(row)
        for ref in refs:
            self.store.db.execute('INSERT OR IGNORE INTO concepts VALUES (?,?)',(ref,row['record_id']))
            if row['kind'] in {'qid_concepts','qid_concepts_base'}:
                for lang in ('en','zh'):
                    page=row['record'].get(lang) or {}
                    if page.get('page_id') is not None:
                        self.store.db.execute('INSERT OR IGNORE INTO pages VALUES (?,?,?)',(lang,str(page['page_id']),ref))
        return {'record_id':row['record_id'],'concept_refs':refs}


class AttachSourceConceptIds:
    concurrency=1;queue_depth=8;catch=()
    def __init__(self,store):self.store=store
    async def __call__(self,row):
        refs=self.store.refs(row)
        method='language_page_id' if row['kind']=='wiki_pages' and not row['record'].get('qid') else 'source_fields'
        # Multiple QIDs for a language/page key remain explicit ambiguity.
        state='unassociated' if not refs else ('ambiguous_page_mapping' if method=='language_page_id' and len(refs)>1 else 'source_association_only')
        return {**row,'concept_refs':refs,'association_method':method,'association_status':state}


class SelectInputRecords:
    """Optional entry filter only; retains all original associations of selected rows."""
    concurrency=1;queue_depth=8;catch=()
    def __init__(self,ids=None,sample_rate=1.0,seed=42):
        self.ids=set(ids) if ids is not None else None;self.rate=sample_rate;self.seed=seed
        if not 0<=sample_rate<=1:raise ValueError('sample_rate must be within [0,1]')
    async def __call__(self,row):
        reason='selected'
        if self.ids is not None and not self.ids.intersection(row['concept_refs']):reason='id_filter'
        elif int(digest({'id':row['record_id'],'seed':self.seed})[:16],16)/2**64>=self.rate:reason='hash_sample'
        return {**row,'entry_selection':{'selected':reason=='selected','reason':reason}}


class ReadDocumentBytes:
    concurrency=1;queue_depth=8;catch=()
    def __init__(self,dataset):self.dataset=Path(dataset)
    async def __call__(self,row):
        result=dict(row)
        if row['kind']=='legacy_docs':
            try:
                relative=row['record'].get('path')
                if not relative:raise ValueError('missing document path')
                p=safe_local(self.dataset,relative);raw=await asyncio.to_thread(p.read_bytes)
                result['document']={'path':str(p),'sha256':digest(raw),'text':raw.decode('utf-8'),'status':'readable'}
            except (OSError,ValueError,UnicodeError) as e:
                result['document']={'status':'read_error','error':str(e)}
        elif row['kind'] in {'legacy_images','qid_images'}:
            result['bytes']=await asyncio.to_thread(verify_image,row['record'],self.dataset,[])
        return result


class CleanDocumentRecord:
    concurrency=1;queue_depth=8;catch=()
    async def __call__(self,row):
        # Exactly one document record, before any concept fan-out.
        return (await asyncio.to_thread(clean_materials,{'materials':[row]}))[0]


class LinkCleanedMaterials:
    concurrency=1;queue_depth=8;catch=()
    def __init__(self,store):self.store=store
    async def __call__(self,row):
        # Do not route conflicting QID mappings into ordinary concept groups.
        refs=row['concept_refs'] if row['association_status']!='ambiguous_page_mapping' else []
        for ref in refs:
            self.store.db.execute('INSERT OR IGNORE INTO associations VALUES (?,?)',(ref,row['record_id']))
        return {'record_id':row['record_id'],'concept_refs':refs,
                'status':'linked' if refs else row['association_status']}


def dataset_records(sources,store,max_records_per_source=None):
    """Stream source rows once; no concepts/requests loop and no whole-file JSON load."""
    import ijson
    for source in sources:
        path=source['path'];kind=source['kind'];key='source:'+digest(source)
        prior=store.db.execute('SELECT body FROM source_status WHERE path=?',(path,)).fetchone()
        stats={'path':path,'kind':kind,'read':0,'valid':0,'invalid':0,'complete':False,'finished':False}
        expected={k:source[k] for k in ('path','exists','size','mtime_ns','inode') if k in source}
        if snapshot(path)!=expected:raise ValueError('Dataset source changed; use a new run')
        if not source['exists']:
            stats.update(status='missing',finished=True)
        else:
            with open_bytes(path) as f:
                if kind=='legacy_concepts':items=enumerate(ijson.items(f,'concepts.item',use_float=True),1)
                else:items=enumerate(f,1)
                for index,item in items:
                    if max_records_per_source is not None and index>max_records_per_source:break
                    stats['read']=index
                    provenance={'source_path':path,'record_index' if kind=='legacy_concepts' else 'line':index,
                                'source_snapshot':source}
                    try:
                        if kind=='legacy_concepts':record=item
                        elif kind in {'image_roles','gallery_captions'}:
                            cells=item.decode().rstrip('\r\n').split('\t')
                            if kind=='image_roles':
                                if len(cells)!=4:raise ValueError('invalid role columns')
                                record=dict(zip(('qid','property','role','commons_file'),cells))
                            else:
                                if len(cells)<3:raise ValueError('invalid caption columns')
                                record={'qid':cells[0],'commons_file':cells[1],'caption':'\t'.join(cells[2:])}
                        else:record=json.loads(item)
                        if not isinstance(record,dict):raise ValueError('record must be an object')
                        provenance['record_sha256']=digest(record)
                        if isinstance(item,bytes):provenance['raw_line_sha256']=digest(item)
                        row={'record_id':digest({'kind':kind,'provenance':provenance}),
                             'kind':kind,'record':record,'provenance':provenance}
                        stats['valid']+=1
                        if not store.get('read_records',row['record_id']):yield row
                    except (ValueError,UnicodeError) as e:
                        stats['invalid']+=1
                        store.put('parse_errors',{'record_id':digest({'source':path,'index':index}),
                            'provenance':provenance,'error':str(e),'raw':item.decode('utf-8',errors='replace') if isinstance(item,bytes) else item})
                else:stats['complete']=True
            if snapshot(path)!=expected:raise ValueError('Dataset source changed while reading; use a new run')
            stats.update(status='read' if stats['complete'] else 'budget_limited',finished=True)
        # Producer progress is only made durable with the stage's sink commit.
        store.db.execute('INSERT OR REPLACE INTO source_status VALUES (?,?)',(path,dumps(stats)))


class RecordFlow:
    def __init__(self,run,dataset=ROOT/'datasets/demiwtg',sources=None,ids=None,
                 sample_rate=1.0,seed=42,max_records_per_source=None,group_size=32,project=ROOT):
        import importlib.metadata
        from .flow import check_run_location
        self.run=Path(run).resolve();self.dataset=Path(dataset).resolve();self.project=Path(project).resolve()
        check_run_location(self.run,project,self.dataset)
        if max_records_per_source is not None and max_records_per_source<1:raise ValueError('read limit must be positive or None')
        if group_size<1:raise ValueError('group_size must be positive')
        if ids is not None and any(not (x.startswith('legacy:') or x.startswith('qid:')) for x in ids):raise ValueError('IDs use legacy:name or qid:QID')
        SelectInputRecords(ids,sample_rate,seed)
        selected=discover(self.dataset,project)
        selected=[s for s in selected if s['kind'] in CONCEPT_KINDS|MATERIAL_KINDS]
        if sources is not None:
            wanted={str(Path(x).resolve()) for x in sources}
            unknown=wanted-{s['path'] for s in selected}
            if unknown:raise ValueError(f'Unsupported dataset sources: {unknown}')
            selected=[s for s in selected if s['path'] in wanted]
        self.sources=sorted(selected,key=lambda s:(s['kind'] not in CONCEPT_KINDS,s['path']))
        self.config={'ids':sorted(ids) if ids is not None else None,'sample_rate':sample_rate,'seed':seed,
                     'max_records_per_source':max_records_per_source,'group_size':group_size}
        self.code=code_fingerprint()
        with run_lock(self.run):
            immutable(self.run/'record_manifest.json',{'schema':'record-flow/1','dataset':str(self.dataset),'project':str(self.project),
                'sources':self.sources,'config':self.config,'code_hash':self.code,'runtime':runtime_version(),
                'ijson':importlib.metadata.version('ijson'),
                'scope':'Record-first processing. Source associations and machine knowledge remain unreviewed.'})
    @classmethod
    def open_saved(cls,run):
        manifest=read(Path(run)/'record_manifest.json')
        obj=cls.__new__(cls);obj.run=Path(run).resolve()
        obj.dataset=Path(manifest['dataset']);obj.project=Path(manifest['project'])
        obj.sources=manifest['sources'];obj.config=manifest['config'];obj.code=manifest['code_hash']
        return obj

    def _check(self):
        if code_fingerprint()!=self.code:raise ValueError('Code changed; use a new run and reload code')
    def _run(self,stage):
        self._check()
        with run_lock(self.run):
            store=RecordStore(self.run)
            try:
                if stage not in STEPS:raise ValueError('unknown stage')
                index=STEPS.index(stage)
                if index and not store.db.execute('SELECT 1 FROM completed WHERE stage=?',(STEPS[index-1],)).fetchone():
                    raise ValueError(f'Run {STEPS[index-1]} first')
                if store.db.execute('SELECT 1 FROM completed WHERE stage=?',(stage,)).fetchone():
                    return {'stage':stage,'rows':store.count(stage),'reused':True}
                if stage=='read_records':
                    rows=lambda:dataset_records(self.sources,store,self.config['max_records_per_source']);op=None
                elif stage=='index_concepts':
                    rows=lambda:(r for r in store.iter('read_records') if r['kind'] in CONCEPT_KINDS);op=IndexConceptPages(store)
                elif stage=='attach_concept_ids':
                    rows=lambda:(r for r in store.iter('read_records') if r['kind'] in MATERIAL_KINDS);op=AttachSourceConceptIds(store)
                elif stage=='select_input':
                    rows=lambda:store.iter('attach_concept_ids');op=SelectInputRecords(self.config['ids'],self.config['sample_rate'],self.config['seed'])
                elif stage=='read_documents':
                    rows=lambda:(r for r in store.iter('select_input') if r['entry_selection']['selected']);op=ReadDocumentBytes(self.dataset)
                elif stage=='clean_documents':rows=lambda:store.iter('read_documents');op=CleanDocumentRecord()
                else:rows=lambda:store.iter('clean_documents');op=LinkCleanedMaterials(store)
                # Skip individually persisted results before expensive work; no list collection.
                def pending():
                    for row in rows():
                        if not store.get(stage,row['record_id']):yield row
                ds=local_data().from_iter(pending)
                if op:ds=ds.map_async(op)
                ds.map_async(PersistRecords(store,stage)).run_stream(log_every=0)
                count=store.count(stage)
                store.db.execute('INSERT INTO completed VALUES (?,?)',(stage,count));store.db.commit()
                return {'stage':stage,'rows':count,'reused':False}
            finally:store.close()
    async def step(self,stage):return await asyncio.to_thread(self._run,stage)
    def view(self,stage,limit=100,sample=False,seed=42):
        import random
        if limit<0:raise ValueError('limit must be nonnegative')
        if limit==0:return []
        # Reservoir sampling bounded by display limit, not dataset size.
        store=RecordStore(self.run);result=[];rng=random.Random(seed)
        try:
            for i,row in enumerate(store.iter(stage)):
                if len(result)<limit:result.append(row)
                elif not sample:break
                else:
                    j=rng.randrange(i+1)
                    if j<limit:result[j]=row
            return result
        finally:store.close()
    def counts(self):
        store=RecordStore(self.run)
        try:return [{'stage':s,'rows':n} for s,n in store.db.execute('SELECT stage,COUNT(*) FROM outputs GROUP BY stage')]
        finally:store.close()


    def iter_groups(self):
        """Bounded document windows; fan-out only after record-level cleaning."""
        from .contracts import IdentityRegistry
        store=RecordStore(self.run)
        registry=IdentityRegistry(self.project/'state/curation/v4/identities.sqlite')
        try:
            if not store.db.execute("SELECT 1 FROM completed WHERE stage='group_materials'").fetchone():
                raise ValueError('Run group_materials first')
            with sqlite3.connect(store.path) as db:
                for (ref,) in db.execute('SELECT DISTINCT ref FROM associations ORDER BY ref'):
                    kind,value=ref.split(':',1);request={'kind':kind,'value':value}
                    cid=registry.get(request)
                    identities=[store.get('read_records',r[0]) for r in db.execute('SELECT record_id FROM concepts WHERE ref=? ORDER BY record_id',(ref,))]
                    pending=[];window=0
                    cursor=db.execute("SELECT o.body FROM associations a JOIN outputs o ON o.id=a.record_id AND o.stage='clean_documents' WHERE a.ref=? ORDER BY a.record_id",(ref,))
                    def wrap(batch,number):
                        mats=[*identities,*batch]
                        batch_ids=[m['record_id'] for m in batch]
                        case_id=digest({'concept_id':cid,'record_ids':batch_ids})[:20]
                        raw=[{k:v for k,v in m.items() if k!='cleaning'} for m in mats]
                        return {'record_id':case_id,'case_id':case_id,
                            'bundle':{'concept_id':cid,'request':request,'materials':raw},
                            'cleaned_materials':mats,'record_window':number,
                            'scope':'This bounded input window only; not a complete concept or cross-window conflict adjudication.'}
                    for (body,) in cursor:
                        item=json.loads(body);pending.append({**store.get('read_records',item['record_id']),**item})
                        if len(pending)==self.config['group_size']:
                            yield wrap(pending,window);pending=[];window+=1
                    if pending:yield wrap(pending,window)
        finally:registry.close();store.close()

    async def knowledge_step(self,stage,model_config):
        """Same downstream operators for sampled and unfiltered data, streamed per window."""
        from .ops.knowledge_stages import (ResolveIdentity,OrganizeMaterials,ExtractKnowledge,
            ConsolidateKnowledge,CheckImageSupport,ExportCandidates)
        from .pipeline import DEFAULT
        from .local_model import LocalModel
        classes=[ResolveIdentity,OrganizeMaterials,ExtractKnowledge,ConsolidateKnowledge,CheckImageSupport,ExportCandidates]
        names=[c.label for c in classes]
        if stage not in names:raise ValueError('unknown knowledge stage')
        cfg={**DEFAULT,**model_config}
        # max_cases belongs to the retired query batch runner, not record-stream execution.
        def work():
            self._check()
            with run_lock(self.run):
                immutable(self.run/'knowledge_config.json',cfg)
                store=RecordStore(self.run)
                index=names.index(stage)
                try:
                    if index and not store.db.execute('SELECT 1 FROM completed WHERE stage=?',(names[index-1],)).fetchone():
                        raise ValueError('Run preceding knowledge stage first')
                    if store.db.execute('SELECT 1 FROM completed WHERE stage=?',(stage,)).fetchone():
                        return {'stage':stage,'rows':store.count(stage),'reused':True,'new_calls':0}
                    factory=self.iter_groups if not index else lambda:store.iter(names[index-1])
                    def pending():
                        for row in factory():
                            if not store.get(stage,row['record_id']):yield row
                    model=LocalModel(self.run,cfg);op=classes[index](self.run,cfg,model)
                    local_data().from_iter(pending).map_async(op).map_async(PersistRecords(store,stage)).run_stream(log_every=0)
                    count=store.count(stage)
                    store.db.execute('INSERT INTO completed VALUES (?,?)',(stage,count));store.db.commit()
                    return {'stage':stage,'rows':count,'reused':False,'new_calls':model.calls}
                finally:store.close()
        return await asyncio.to_thread(work)


def main():
    import argparse
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--run',type=Path,required=True)
    p.add_argument('--dataset',type=Path,default=ROOT/'datasets/demiwtg')
    p.add_argument('--source',type=Path,action='append')
    p.add_argument('--id',action='append',dest='ids',help='Optional legacy:name or qid:QID; omitted means all records')
    p.add_argument('--sample-rate',type=float,default=1.0)
    p.add_argument('--seed',type=int,default=42)
    p.add_argument('--max-records-per-source',type=int,default=None,help='Optional read budget; omitted scans entire sources')
    p.add_argument('--group-size',type=int,default=32)
    p.add_argument('--through',choices=STEPS+['identity','organize','extract','consolidate','evidence','export'],default='group_materials')
    p.add_argument('--model-config',type=Path,help='Explicit model configuration required for model stages')
    args=p.parse_args()
    model_stages=['identity','organize','extract','consolidate','evidence','export']
    if args.through in model_stages and args.model_config is None:p.error('model stages require --model-config')
    flow=RecordFlow(args.run,args.dataset,args.source,args.ids,args.sample_rate,args.seed,args.max_records_per_source,args.group_size)
    async def run():
        for stage in STEPS:
            print(await flow.step(stage))
            if stage==args.through:return
        for stage in model_stages:
            print(await flow.knowledge_step(stage,read(args.model_config)))
            if stage==args.through:return
    asyncio.run(run())

if __name__=='__main__':main()
