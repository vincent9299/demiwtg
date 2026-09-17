"""Concept -> associated source materials -> shared processing -> knowledge.

RecordFlow supplies source IO and storage; selection here is over concepts.
Source references remain separate until verified identity mapping is implemented.
"""
import asyncio
import sqlite3
from pathlib import Path
from demiflow.standalone import local_data
from .contracts import ROOT, digest, immutable, read, run_lock, source_code
from .record_flow import (RecordFlow, RecordStore, PersistRecords, dataset_records,
    CONCEPT_KINDS, MATERIAL_KINDS, IndexConceptPages, AttachSourceConceptIds,
    ReadDocumentBytes, CleanDocumentRecord, LinkCleanedMaterials)

STEPS = ['read_records', 'index_concepts', 'select_concepts', 'attach_concept_ids',
         'associate_materials', 'read_documents', 'clean_documents',
         'group_materials', 'summarize_concepts']


class SelectConcepts:
    concurrency=1; queue_depth=8; catch=()
    def __init__(self, ids=None, sample_rate=1.0, seed=42):
        self.ids=None if ids is None else set(ids)
        self.rate=sample_rate; self.seed=seed
        if not 0 <= sample_rate <= 1: raise ValueError('sample_rate must be within [0,1]')
    async def __call__(self, row):
        ref=row['concept_ref']; reason='selected'
        if self.ids is not None and ref not in self.ids: reason='id_filter'
        elif self.rate < 1 and int(digest({'concept_ref':ref,'seed':self.seed})[:16],16) >= int(self.rate*2**64):
            reason='concept_sample'
        return {**row, 'selection':{'selected':reason=='selected','reason':reason}}


class AssociateConceptMaterials:
    concurrency=1; queue_depth=8; catch=()
    def __init__(self,store): self.store=store
    async def __call__(self,row):
        selected=[]; unknown=[]
        for ref in row['concept_refs']:
            concept=self.store.get('select_concepts',ref)
            if concept is None: unknown.append(ref)
            elif concept['selection']['selected']: selected.append(ref)
        ambiguous=row['association_status']=='ambiguous_page_mapping'
        active=[] if ambiguous else selected
        if ambiguous: status='ambiguous_mapping'
        elif active: status='associated'
        elif not row['concept_refs'] or unknown: status='needs_concept_identification'
        else: status='outside_selected_concepts'
        if ambiguous or unknown or not row['concept_refs']:
            self.store.put('unresolved_materials',{'record_id':row['record_id'],
                'source_record_id':row['record_id'],'concept_refs':row['concept_refs'],
                'unknown_concept_refs':unknown,'reason':status if not active else 'partly_unknown_concepts'})
        return {**row,'selected_concept_refs':active,'association_decision':status}


class LinkSelectedMaterials(LinkCleanedMaterials):
    async def __call__(self,row):
        # Original concept_refs stay in source data; task membership follows selected concepts only.
        result=await super().__call__({**row,'concept_refs':row['selected_concept_refs']})
        return {**result,'source_record_id':row['record_id']}


class SummarizeConceptMaterials:
    concurrency=1; queue_depth=8; catch=()
    def __init__(self,store,group_size): self.store=store; self.group_size=group_size
    async def __call__(self,row):
        ref=row['concept_ref']
        count=self.store.db.execute('SELECT COUNT(*) FROM associations WHERE ref=?',(ref,)).fetchone()[0]
        reports=[readrow[0] for readrow in self.store.db.execute('SELECT body FROM source_status')]
        import json
        incomplete=[json.loads(s)['path'] for s in reports if not json.loads(s)['complete']]
        return {**row,'material_count':count,'window_count':(count+self.group_size-1)//self.group_size,
            'material_status':'materials_available' if count else 'no_materials_in_read_scope',
            'knowledge_status':'not_extracted','incomplete_sources':incomplete,
            'scope':'Counts refer to associated materials in the recorded source scope, not verified evidence.'}


class ConceptFlow(RecordFlow):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        with run_lock(self.run):
            immutable(self.run/'concept_manifest.json',{
                'schema':'concept-flow/1','selection_unit':'concept_ref',
                'source_manifest':'record_manifest.json','code_hash':self.code})
            immutable(self.run/'record_code_snapshot.json',source_code())

    @classmethod
    def open_saved(cls,run):
        read(Path(run)/'concept_manifest.json')
        return super().open_saved(run)

    def reuse_raw_records(self, previous_run):
        """Reuse only frozen raw-source parsing, never old cleaning or selection outputs."""
        import ast
        from .contracts import snapshot
        previous=Path(previous_run).resolve()
        if previous==self.run: raise ValueError('raw source run must differ')
        old=read(previous/'record_manifest.json')
        if old['sources']!=self.sources or old['config']['max_records_per_source']!=self.config['max_records_per_source']:
            raise ValueError('Raw source snapshots/read scope differ')
        def parser(source):
            return ast.dump(next(n for n in ast.parse(source).body if isinstance(n,ast.FunctionDef) and n.name=='dataset_records'))
        old_code=read(previous/'record_code_snapshot.json')
        if digest(old_code)!=old['code_hash'] or parser(old_code['record_flow.py'])!=parser(source_code()['record_flow.py']):
            raise ValueError('Raw parser changed; read datasets again')
        for source in self.sources:
            if snapshot(source['path'])!={k:source[k] for k in ('path','exists','size','mtime_ns','inode') if k in source}:
                raise ValueError('Raw source changed; use a new run')
        self._check()
        with run_lock(previous),run_lock(self.run):
            store=RecordStore(self.run)
            try:
                if store.count('read_records'): raise ValueError('Reuse requires an empty destination read stage')
                store.db.execute('ATTACH DATABASE ? AS previous',((previous/'records.sqlite').as_uri()+'?mode=ro',))
                if not store.db.execute("SELECT 1 FROM previous.completed WHERE stage='read_records'").fetchone():
                    raise ValueError('Previous raw read is incomplete')
                immutable(self.run/'raw_reuse.json',{'run':str(previous),'manifest_hash':digest(old),
                    'scope':'Only read_records, parse_errors and source_status; no derived materials reused.'})
                store.db.execute("INSERT INTO outputs SELECT * FROM previous.outputs WHERE stage IN ('read_records','parse_errors')")
                store.db.execute('INSERT INTO source_status SELECT * FROM previous.source_status')
                count=store.count('read_records')
                store.db.execute('INSERT INTO completed VALUES (?,?)',('read_records',count));store.db.commit()
                return {'stage':'read_records','rows':count,'reused_raw_run':str(previous)}
            finally:store.close()

    def _concept_rows(self,store,selected_only=False):
        if selected_only:
            for row in store.iter('select_concepts'):
                if row['selection']['selected']: yield row
        else:
            with sqlite3.connect(store.path) as db:
                for (ref,) in db.execute('SELECT DISTINCT ref FROM concepts ORDER BY ref'):
                    yield {'record_id':ref,'concept_ref':ref,
                        'identity_status':'source_identity_not_adjudicated'}

    def _run(self,stage):
        self._check()
        with run_lock(self.run):
            store=RecordStore(self.run)
            try:
                if stage not in STEPS: raise ValueError('unknown stage')
                index=STEPS.index(stage)
                if index and not store.db.execute('SELECT 1 FROM completed WHERE stage=?',(STEPS[index-1],)).fetchone():
                    raise ValueError(f'Run {STEPS[index-1]} first')
                if store.db.execute('SELECT 1 FROM completed WHERE stage=?',(stage,)).fetchone():
                    return {'stage':stage,'rows':store.count(stage),'reused':True}
                if stage=='read_records':
                    rows=lambda:dataset_records(self.sources,store,self.config['max_records_per_source']); op=None
                elif stage=='index_concepts':
                    rows=lambda:(r for r in store.iter('read_records') if r['kind'] in CONCEPT_KINDS); op=IndexConceptPages(store)
                elif stage=='select_concepts':
                    rows=lambda:self._concept_rows(store); op=SelectConcepts(self.config['ids'],self.config['sample_rate'],self.config['seed'])
                elif stage=='attach_concept_ids':
                    rows=lambda:(r for r in store.iter('read_records') if r['kind'] in MATERIAL_KINDS); op=AttachSourceConceptIds(store)
                elif stage=='associate_materials':
                    rows=lambda:store.iter('attach_concept_ids'); op=AssociateConceptMaterials(store)
                elif stage=='read_documents':
                    rows=lambda:(r for r in store.iter('associate_materials') if r['selected_concept_refs']); op=ReadDocumentBytes(self.dataset)
                elif stage=='clean_documents':
                    rows=lambda:store.iter('read_documents'); op=CleanDocumentRecord()
                elif stage=='group_materials':
                    rows=lambda:store.iter('clean_documents'); op=LinkSelectedMaterials(store)
                else:
                    rows=lambda:self._concept_rows(store,True); op=SummarizeConceptMaterials(store,self.config['group_size'])
                def pending():
                    for row in rows():
                        if not store.get(stage,row['record_id']): yield row
                ds=local_data().from_iter(pending)
                if op: ds=ds.map_async(op)
                ds.map_async(PersistRecords(store,stage)).run_stream(log_every=0)
                if stage=='select_concepts':
                    for ref in self.config['ids'] or []:
                        if not store.get(stage,ref):
                            store.put('missing_concepts',{'record_id':ref,'concept_ref':ref,
                                'reason':'not_in_read_concept_scope'})
                count=store.count(stage)
                store.db.execute('INSERT INTO completed VALUES (?,?)',(stage,count));store.db.commit()
                return {'stage':stage,'rows':count,'reused':False}
            finally: store.close()

    def iter_groups(self):
        # Existing bounded window processing is reusable, but it is an intermediate.
        store=RecordStore(self.run)
        try:
            if not store.db.execute("SELECT 1 FROM completed WHERE stage='summarize_concepts'").fetchone():
                raise ValueError('Run summarize_concepts first')
        finally: store.close()
        for row in super().iter_groups():
            yield {**row,'task_id':row['case_id'],'concept_ref':row['bundle']['request']['kind']+':'+row['bundle']['request']['value']}
        # Concepts without material must survive as explicit blocked tasks, without a model call.
        from .contracts import IdentityRegistry
        store=RecordStore(self.run); registry=IdentityRegistry(self.project/'state/curation/v4/identities.sqlite')
        try:
            for concept in store.iter('summarize_concepts'):
                if concept['material_count']: continue
                ref=concept['concept_ref'];kind,value=ref.split(':',1)
                request={'kind':kind,'value':value};cid=registry.get(request)
                task_id=digest({'concept_id':cid,'empty':True})[:20]
                yield {'record_id':task_id,'task_id':task_id,'case_id':task_id,'concept_ref':ref,
                    'bundle':{'concept_id':cid,'request':request,'materials':[]},'cleaned_materials':[],
                    'blocked':{'stage':'associate_materials','reason':'no_materials_in_read_scope'}}
        finally: registry.close();store.close()

    async def collect_knowledge(self):
        """Group result pointers and gaps. This does not adjudicate cross-window claims."""
        def work():
            self._check()
            with run_lock(self.run):
                store=RecordStore(self.run)
                try:
                    if not store.db.execute("SELECT 1 FROM completed WHERE stage='export'").fetchone():
                        raise ValueError('Run export first')
                    store.db.execute("CREATE INDEX IF NOT EXISTS export_concept ON outputs(json_extract(body,'$.concept_ref')) WHERE stage='export'")
                    class Collect:
                        concurrency=1;queue_depth=8;catch=()
                        async def __call__(self,concept):
                            tasks=[];blocked=[]
                            import json
                            for (body,) in store.db.execute("SELECT body FROM outputs WHERE stage='export' AND json_extract(body,'$.concept_ref')=? ORDER BY id",(concept['concept_ref'],)):
                                row=json.loads(body)
                                tasks.append(row['task_id'])
                                if row.get('blocked'):blocked.append({'task_id':row['task_id'],**row['blocked']})
                            return {**concept,'task_ids':tasks,'blocked_tasks':blocked,
                                'knowledge_status':'needs_joint_review' if tasks and not blocked else 'blocked_or_incomplete',
                                'scope':'Window outputs remain candidates; grouping is not conflict resolution or knowledge approval.'}
                    def pending():
                        for row in store.iter('summarize_concepts'):
                            if not store.get('concept_knowledge',row['record_id']):yield row
                    local_data().from_iter(pending).map_async(Collect()).map_async(PersistRecords(store,'concept_knowledge')).run_stream(log_every=0)
                    count=store.count('concept_knowledge')
                    store.db.execute('INSERT OR IGNORE INTO completed VALUES (?,?)',('concept_knowledge',count));store.db.commit()
                    return {'stage':'concept_knowledge','rows':count}
                finally:store.close()
        return await asyncio.to_thread(work)


def main():
    import argparse
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--run',type=Path,required=True)
    p.add_argument('--dataset',type=Path,default=ROOT/'datasets/demiwtg')
    p.add_argument('--source',type=Path,action='append')
    p.add_argument('--id',dest='ids',action='append',help='Source concept reference, legacy:name or qid:QID')
    p.add_argument('--sample-rate',type=float,default=1.0,help='Sample concepts, not material records')
    p.add_argument('--seed',type=int,default=42)
    p.add_argument('--max-records-per-source',type=int,default=None)
    p.add_argument('--group-size',type=int,default=32)
    knowledge=['identity','organize','extract','consolidate','evidence','export','concept_knowledge']
    p.add_argument('--through',choices=STEPS+knowledge,default='summarize_concepts')
    p.add_argument('--model-config',type=Path)
    a=p.parse_args()
    if a.through in knowledge and a.model_config is None:p.error('knowledge stages require --model-config')
    flow=ConceptFlow(a.run,a.dataset,a.source,a.ids,a.sample_rate,a.seed,a.max_records_per_source,a.group_size)
    async def run():
        for stage in STEPS:
            print(await flow.step(stage))
            if stage==a.through:return
        for stage in knowledge:
            print(await flow.collect_knowledge() if stage=='concept_knowledge' else await flow.knowledge_step(stage,read(a.model_config)))
            if stage==a.through:return
    asyncio.run(run())

if __name__=='__main__':main()
