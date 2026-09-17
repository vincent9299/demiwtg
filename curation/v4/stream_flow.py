"""Concept/document/image Dataset chains executed by demiflow, without a database."""
import itertools
import json
from pathlib import Path
from demiflow.standalone import local_data
from .contracts import ROOT,digest,immutable,read,run_lock,snapshot,source_code,runtime_version
from .sources import discover,open_bytes
from .ops.cleaning import clean_document
from .ops.operators import safe_local,verify_image

from .ops.dataset_operators import (union, merge_concept, merge_document, distinct,
    ReadDocument, CleanDocument, CheckImage, model_input)

SOURCE_KINDS={'legacy_concepts','qid_concepts','qid_concepts_base','legacy_docs','wiki_pages','legacy_images','qid_images'}
















class StreamFlow:
    def __init__(self,run,dataset=ROOT/'datasets/demiwtg',sources=None,ids=None,sample_rate=1.,seed=42,max_records_per_source=None,group_size=32,project=ROOT):
        from .flow import check_run_location
        self.run=Path(run).resolve();self.dataset=Path(dataset).resolve();self.project=Path(project).resolve()
        check_run_location(self.run,self.project,self.dataset)
        if not 0<=sample_rate<=1:raise ValueError('invalid sample rate')
        if group_size<1 or (max_records_per_source is not None and max_records_per_source<1):raise ValueError('invalid size')
        found=[s for s in discover(self.dataset,self.project) if s['kind'] in SOURCE_KINDS|{'image_roles','gallery_captions','qid_graph'}]
        if sources is not None:
            wanted={str(Path(p).resolve()) for p in sources}
            if wanted-{s['path'] for s in found}:raise ValueError('unsupported source')
            found=[s for s in found if s['path'] in wanted]
        self.sources=found
        self.config={'ids':ids,'sample_rate':sample_rate,'seed':seed,'max_records_per_source':max_records_per_source,'group_size':group_size}
        manifest={'schema':'demiflow-stream/1','dataset':str(self.dataset),'project':str(self.project),'sources':found,'config':self.config,
                  'source_code':source_code(),'runtime':runtime_version()}
        self.version=digest(manifest)
        with run_lock(self.run):immutable(self.run/'stream_manifest.json',manifest)
        self.ds={}

    @classmethod
    def open_saved(cls,run):
        obj=cls.__new__(cls);obj.run=Path(run).resolve();m=read(obj.run/'stream_manifest.json')
        obj.project=Path(m['project']);obj.dataset=Path(m['dataset']);obj.config=m['config'];obj.sources=m['sources'];obj.version=digest(m);obj.ds={}
        return obj

    def saved(self,name):
        path=self.run/'datasets'/f'{name}.jsonl'
        if not path.exists():raise ValueError(f'Run producing {name} first')
        def rows():
            with path.open() as f:
                for line in f:yield json.loads(line)
        return local_data().from_iter(rows)

    def _check(self):
        m=read(self.run/'stream_manifest.json')
        if m['source_code']!=source_code() or m['runtime']!=runtime_version() or m['config']!=self.config:
            raise ValueError('Code/runtime/config changed; use a new run')
        for source in self.sources:
            expected={k:source[k] for k in ('path','exists','size','mtime_ns','inode') if k in source}
            if snapshot(source['path'])!=expected:raise ValueError('Source changed; use a new run')

    def save(self,name,dataset):
        self._check()
        result=dataset.checkpoint(self.run/'datasets'/f'{name}.jsonl',version=self.version)
        self.ds[name]=result;return result

    def _source_rows(self,source):
        from .ops.dataset_operators import read_source_rows
        return read_source_rows(source,self.run,self.config['max_records_per_source'])

    def prepare_sources(self):
        groups={'concepts':[],'documents':[],'images':[],'auxiliary':[]}
        for source in self.sources:
            k=source['kind'];group='concepts' if k in {'legacy_concepts','qid_concepts','qid_concepts_base'} else 'documents' if k in {'legacy_docs','wiki_pages'} else 'images' if k in {'legacy_images','qid_images'} else 'auxiliary'
            raw=local_data().from_iter(lambda s=source:self._source_rows(s))
            groups[group].append(self.save('source_'+digest(source)[:16],raw))
        concepts=self.save('concepts',union(groups['concepts']).reduce_by_key('concept_ref',merge_concept))
        pages=self.save('page_refs',concepts.flat_map(lambda c:c['page_refs']).reduce_by_key(['lang','page_id','mapped_concept_ref'],distinct))
        docs=union(groups['documents'])
        wiki=docs.filter(lambda d:d['format']=='wiki_sections').join(pages,on=['lang','page_id'],how='left').reduce_by_key('doc_id',merge_document)
        self.save('documents',union([docs.filter(lambda d:d['format']!='wiki_sections'),wiki]))
        self.save('images',union(groups['images']))
        self.save('auxiliary',union(groups['auxiliary']))
        return {'stage':'prepare_sources'}

    def select(self):
        def choose(c):
            ids=self.config['ids'];rate=self.config['sample_rate'];ref=c['concept_ref'];reason='selected'
            if ids is not None and ref not in ids:reason='id_filter'
            elif rate<1 and int(digest({'concept_ref':ref,'seed':self.config['seed']})[:16],16)>=int(rate*2**64):reason='concept_sample'
            return {**c,'selected':reason=='selected','selection_reason':reason}
        chosen=self.save('concepts_selected',self.saved('concepts').map(choose))
        selected=chosen.filter(lambda c:c['selected']).select_columns(['concept_ref'])
        if self.config['ids'] is not None:
            self.save('missing_concepts',local_data().from_iter(lambda:({'concept_ref':r} for r in self.config['ids'])).join(self.saved('concepts').select_columns(['concept_ref']),on='concept_ref',how='anti'))
        for name,key in [('documents','doc_id'),('images','image_id')]:
            objects=self.saved(name)
            links=objects.flat_map(lambda r,k=key:[{'concept_ref':ref,k:r[k]} for ref in r['concept_refs'] if r.get('association_status')!='ambiguous_mapping'])
            chosen_links=self.save(name+'_links',links.join(selected,on='concept_ref',how='semi'))
            selected_ids=chosen_links.select_columns([key]).reduce_by_key(key,distinct)
            self.save(name+'_selected',objects.join(selected_ids,on=key,how='semi'))
            # Unmatched material is retained separately for later concept discovery.
            linked_ids=links.join(self.saved('concepts').select_columns(['concept_ref']),on='concept_ref',how='semi').select_columns([key]).reduce_by_key(key,distinct)
            self.save(name+'_unmatched',objects.join(linked_ids,on=key,how='anti'))
        return {'stage':'select'}

    def document_chain(self):
        return (self.saved('documents_selected')
            .map_cached(ReadDocument(self.dataset),cache_dir=self.run/'cache/read_documents',version=self.version)
            .map_cached(CleanDocument(),cache_dir=self.run/'cache/clean_documents',version=self.version))

    def image_chain(self):
        return self.saved('images_selected').map_cached(CheckImage(self.dataset),cache_dir=self.run/'cache/check_images',version=self.version)

    def process_documents(self):self.save('documents_processed',self.document_chain());return {'stage':'process_documents'}
    def process_images(self):self.save('images_processed',self.image_chain());return {'stage':'process_images'}

    def summarize(self):
        concepts=self.saved('concepts_selected').filter(lambda c:c['selected'])
        for name,key,count,status in [('documents','doc_id','document_count','read_status'),('images','image_id','image_count','byte_status')]:
            # Both saved branches are required; saved() raises if either is unfinished.
            processed=self.saved(name+'_processed').select_columns([key,status])
            enriched=self.saved(name+'_links').join(processed,on=key)
            def add(acc,row,col=count,check=status):
                return {'concept_ref':row['concept_ref'],col:(acc or {}).get(col,0)+1,
                        'readable_documents' if col=='document_count' else 'verified_images':(acc or {}).get('readable_documents' if col=='document_count' else 'verified_images',0)+int(row[check] in {'readable','verified_bytes'})}
            counts=enriched.reduce_by_key('concept_ref',add)
            concepts=concepts.join(counts,on='concept_ref',how='left')
        def fill(c):
            return {**c,**{k:c.get(k,0) for k in ('document_count','image_count','readable_documents','verified_images')},
                    'material_status':'materials_available' if c.get('document_count',0)+c.get('image_count',0) else 'no_materials_in_read_scope','knowledge_status':'not_extracted'}
        self.save('concepts_ready',concepts.map(fill));return {'stage':'summarize'}

    def gather(self):
        datasets=[]
        for name,key in [('documents','doc_id'),('images','image_id')]:
            ds=self.saved(name+'_processed').map(lambda row,n=name,k=key:{k:row[k],'material_type':n,'material':row})
            datasets.append(self.saved(name+'_links').join(ds,on=key))
        batches=union(datasets).group_batches('concept_ref',max_rows=self.config['group_size'],output='materials')
        self.save('knowledge_inputs',self.saved('concepts_ready').join(batches,on='concept_ref',how='left'))
        return {'stage':'gather'}

    def knowledge(self,stage,config=None,model_factory=None):
        """One explicitly requested model stage; cached files, no identity database."""
        from .pipeline import DEFAULT
        from .local_model import LocalModel
        from .ops.knowledge_stages import ResolveIdentity,OrganizeMaterials,ExtractKnowledge,ConsolidateKnowledge,CheckImageSupport,ExportCandidates
        classes=[ResolveIdentity,OrganizeMaterials,ExtractKnowledge,ConsolidateKnowledge,CheckImageSupport,ExportCandidates]
        names=[c.label for c in classes]
        if stage not in names:raise ValueError('unknown knowledge stage')
        self._check();config={**DEFAULT,**(config or {})}
        with run_lock(self.run):
            immutable(self.run/'knowledge_config.json',config)
            index=names.index(stage)
            inputs=self.saved('knowledge_inputs').map(model_input) if index==0 else self.saved('knowledge_'+names[index-1])
            # Stage already retains input hashes, requests, responses and uncertain calls.
            op=classes[index](self.run/'knowledge',config,(model_factory or LocalModel)(self.run/'knowledge',config))
            result=self.save('knowledge_'+stage,inputs.map_async(op))
        return result




def main():
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stage',choices=['prepare_sources','select','process_documents','process_images','summarize','gather','status'])
    parser.add_argument('--run',required=True);parser.add_argument('--dataset',default=str(ROOT/'datasets/demiwtg'))
    parser.add_argument('--sources',nargs='*');parser.add_argument('--ids',nargs='*')
    parser.add_argument('--max-records-per-source',type=int);parser.add_argument('--sample-rate',type=float,default=1.)
    parser.add_argument('--seed',type=int,default=42);parser.add_argument('--group-size',type=int,default=32)
    a=parser.parse_args()
    if (Path(a.run)/'stream_manifest.json').exists():
        f=StreamFlow.open_saved(a.run)
        if a.stage!='status':f._check()
    else:
        f=StreamFlow(a.run,dataset=a.dataset,sources=a.sources,ids=a.ids,sample_rate=a.sample_rate,seed=a.seed,
                     max_records_per_source=a.max_records_per_source,group_size=a.group_size)
    if a.stage=='status':
        print(json.dumps({'datasets':[p.stem for p in (f.run/'datasets').glob('*.jsonl')],
            'sources':[read(p) for p in (f.run/'source_status').glob('*.json')],
            'model_requests':len(list((f.run/'knowledge/calls').glob('*.request.json')))},ensure_ascii=False,indent=2))
    else:print(json.dumps(getattr(f,a.stage)(),ensure_ascii=False))

if __name__=='__main__':main()
