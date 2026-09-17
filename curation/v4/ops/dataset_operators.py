"""Business row operators and source readers; no Dataset scheduling or stage lookup."""
import itertools
import json
from pathlib import Path
from demiflow.standalone import local_data
from curation.v4.contracts import digest, immutable, snapshot
from demiflow.data.records import iter_file_records
from demiflow.data.datasource import Datasource, ReadTask, BlockMetadata
from curation.v4.ops.cleaning import clean_document
from curation.v4.ops.operators import safe_local, verify_image

def union(datasets):
    datasets=list(datasets)
    return datasets[0].union(*datasets[1:]) if datasets else local_data().from_items([])

def merge_concept(acc,row):
    if acc is None:return row
    return {**acc,'source_records':acc['source_records']+row['source_records'],
        'page_refs':acc['page_refs']+row['page_refs']}

def merge_document(acc,row):
    base=acc or {**row,'concept_refs':list(row['concept_refs'])}
    if not row.get('source_qid') and row.get('mapped_concept_ref'):
        base['concept_refs']=sorted(set(base['concept_refs']+[row['mapped_concept_ref']]))
    base.pop('mapped_concept_ref',None)
    base['association_status']=('ambiguous_mapping' if len(base['concept_refs'])>1 and base['format']=='wiki_sections'
                                else 'source_only' if base['concept_refs'] else 'unassociated')
    return base

def distinct(acc,row):return row

class ReadDocument:
    concurrency=1;queue_depth=4;catch=()
    def __init__(self,dataset):self.dataset=dataset
    async def __call__(self,doc):
        import asyncio
        try:
            if doc['format']=='wiki_sections':
                text='\n\n'.join((s.get('title','')+'\n'+s.get('text','')).strip() for s in doc['sections']);raw=text.encode()
            else:
                if not doc.get('path'):raise ValueError('missing document path')
                raw=await asyncio.to_thread(safe_local(self.dataset,doc['path']).read_bytes);text=raw.decode('utf-8')
            return {**doc,'raw_text':text,'raw_sha256':digest(raw),'read_status':'readable','read_error':None}
        except (OSError,ValueError,UnicodeError) as e:
            return {**doc,'raw_text':'','raw_sha256':None,'read_status':'read_error','read_error':str(e)}

class CleanDocument:
    concurrency=1;queue_depth=4;catch=()
    async def __call__(self,doc):
        import asyncio
        c=await asyncio.to_thread(clean_document,doc['raw_text'],title=doc.get('title'),source_sections=doc.get('sections'))
        from curation.v4.ops.quality_policy import material_disposition
        return {**doc,'clean_text':c['text'],'clean_blocks':c['blocks'],'clean_counts':c['counts'],
            'clean_status':c['status'] if doc['read_status']=='readable' else 'unavailable',
            'clean_warnings':c['warnings']+([doc['read_error']] if doc['read_error'] else []),
            'clean_extraction':c['extraction'],'source_media':c['media'],'source_supplements':c['supplements'],'clean_version':c['version'],'knowledge_eligibility':material_disposition(c)}

class CheckImage:
    concurrency=1;queue_depth=4;catch=()
    def __init__(self,dataset):self.dataset=dataset
    async def __call__(self,img):
        import asyncio
        result=await asyncio.to_thread(verify_image,img,self.dataset,[])
        return {**img,'byte_status':result['status'],'byte_details':result}

def model_input(row):
    """Boundary adapter only: three public schemas stay flat until this step."""
    ref=row['concept_ref'];prefix,value=ref.split(':',1);materials=[]
    for item in row['source_records']:
        materials.append({'kind':item['source']['snapshot']['kind'],'record':item['fields'],'provenance':item['source']})
    for item in row.get('materials',[]):
        r=item['material'];source=r['source']
        m={'kind':source['snapshot']['kind'],'record':r,'provenance':source}
        if item['material_type']=='documents':
            m['document']={'text':r['raw_text'],'sha256':r['raw_sha256']}
            m['cleaning']={'text':r['clean_text'],'status':r['clean_status'],'blocks':r['clean_blocks'],
                'extraction':r.get('clean_extraction'),'media':r.get('source_media',[]),'supplements':r.get('source_supplements',[]),'counts':r['clean_counts'],'warnings':r['clean_warnings'],'version':r['clean_version'],
                'source_sha256':r['raw_sha256'],'source_locator':source}
        else:m['bytes']=r['byte_details']
        materials.append(m)
    case=digest({'concept_ref':ref,'group_index':row.get('group_index',0),'materials':materials})[:20]
    bundle={'concept_id':'trial-'+digest(ref)[:24],'request':{'kind':'legacy' if prefix=='legacy' else 'qid','value':value},
        'materials':materials,'identity_status':'source_only','knowledge_status':'not_extracted'}
    return {'case_id':case,'concept_ref':ref,'bundle':bundle,'cleaned_materials':materials,
        'input_scope':{'group_index':row.get('group_index',0),'group_last':row.get('group_last',True),
                       'cross_group_integration':'not_implemented','concept_id_contract':'source-bound trial ID; no authority migration'}}

def read_source_rows(source, run, max_records_per_source=None):
        limit=max_records_per_source;path=source['path'];kind=source['kind']
        status={'path':path,'kind':kind,'rows':0,'invalid_rows':0,'complete':False}
        expected={k:source[k] for k in ('path','exists','size','mtime_ns','inode') if k in source}
        if snapshot(path)!=expected:raise ValueError('source changed; use a new run')
        if not source['exists']:status['status']='missing'
        else:
            fmt='json' if kind=='legacy_concepts' else 'text' if kind in {'image_roles','gallery_captions'} else 'jsonl'
            rows=iter_file_records(path,format=fmt,item_prefix='concepts.item' if fmt=='json' else None)
            try:
                for item in rows:
                    i=item['row']
                    if limit is not None and i>limit:break
                    status['rows']=i
                    try:
                        if item['error']:raise ValueError(item['error']['detail'])
                        r=item['value']
                        if kind in {'image_roles','gallery_captions'}:
                            cells=r.split('\t')
                            if kind=='image_roles':
                                if len(cells)!=4:raise ValueError('invalid role columns')
                                r=dict(zip(['qid','property','role','commons_file'],cells))
                            else:
                                if len(cells)<3:raise ValueError('invalid caption columns')
                                r={'qid':cells[0],'commons_file':cells[1],'caption':'\t'.join(cells[2:])}
                        if not isinstance(r,dict):raise ValueError('expected object')
                    except (ValueError,UnicodeError) as e:
                        status['invalid_rows']+=1
                        with (run/'parse_errors.jsonl').open('a') as errors:
                            errors.write(json.dumps({'source':path,'row':i,'error':str(e),'raw':item['raw'] if item['error'] else item['value']},ensure_ascii=False)+'\n')
                        continue
                    yield source_record({**item,'value':r},source)
                else:status['complete']=True
            finally:rows.close()
            if snapshot(path)!=expected:raise ValueError('source changed during read')
            status['status']='read' if status['complete'] else 'budget_limited'
        immutable(run/'source_status'/f'{digest(source)}.json',status)


class ReadSource(Datasource):
    """Native Datasource: generic file decoding in demiflow, business schema here."""
    def get_read_tasks(self, parallelism, per_task_row_limit=None, data_context=None):
        def blocks():
            for row in self():yield [row]
        return [ReadTask(blocks, BlockMetadata(input_files=(self.source['path'],)),
                         per_task_row_limit=per_task_row_limit)]
    def __init__(self, source, run, max_records_per_source=None, *, kind=None):
        if kind is not None:source={'kind':kind,**snapshot(source)}
        self.source=source; self.run=Path(run); self.limit=max_records_per_source
    def __call__(self):
        return read_source_rows(self.source, self.run, self.limit)


class SelectConcept:
    """Concept row -> same row plus selected and selection_reason."""
    def __init__(self, ids=None, sample_rate=1., seed=42):
        self.ids=None if ids is None else set(ids); self.rate=sample_rate; self.seed=seed
    def __call__(self, c):
        ref=c['concept_ref']; reason='selected'
        if self.ids is not None and ref not in self.ids:reason='id_filter'
        elif self.rate<1 and int(digest({'concept_ref':ref,'seed':self.seed})[:16],16)>=int(self.rate*2**64):reason='concept_sample'
        return {**c,'selected':reason=='selected','selection_reason':reason}


class MaterialLinks:
    """Document/image row -> source concept associations; no joins here."""
    def __init__(self, key):self.key=key
    def __call__(self, row):
        return [{'concept_ref':ref,self.key:row[self.key]} for ref in row['concept_refs']
                if row.get('association_status')!='ambiguous_mapping']


class CountMaterial:
    """Reduce rows of one concept to total and readable/verified counts."""
    def __init__(self, count_column, status_column, valid_column):
        self.count=count_column; self.status=status_column; self.valid=valid_column
    def __call__(self, acc, row):
        return {'concept_ref':row['concept_ref'],self.count:(acc or {}).get(self.count,0)+1,
                self.valid:(acc or {}).get(self.valid,0)+int(row[self.status] in {'readable','verified_bytes'})}


def fill_material_counts(c):
    return {**c,**{k:c.get(k,0) for k in ('document_count','image_count','readable_documents','verified_images')},
            'material_status':'materials_available' if c.get('document_count',0)+c.get('image_count',0) else 'no_materials_in_read_scope',
            'knowledge_status':'not_extracted'}


class NestMaterial:
    """Processed row -> one typed material, only at the late gathering boundary."""
    def __init__(self, key, material_type):self.key=key; self.type=material_type
    def __call__(self, row):return {self.key:row[self.key],'material_type':self.type,'material':row}


def source_record(item, source):
    """Convert one decoded object to business fields; never open or scan a file."""
    path=source['path'];kind=source['kind'];i=item['row'];r=item['value']
    expected={k:source[k] for k in ('path','exists','size','mtime_ns','inode') if k in source}
    if item['snapshot']!=expected:raise ValueError('Read file differs from frozen input')
    if item['error'] or not isinstance(r,dict):raise ValueError('Expected a valid object record')
    origin={'path':path,'row':i,'snapshot':source,'content_sha256':digest(r)}
    sid=digest(origin)
    if kind in {'legacy_concepts','qid_concepts','qid_concepts_base'}:
        value=r.get('name') if kind=='legacy_concepts' else r.get('qid')
        if not isinstance(value,str) or not value:raise ValueError(f'missing concept identity: {path}:{i}')
        ref=('legacy:' if kind=='legacy_concepts' else 'qid:')+value
        return {'concept_ref':ref,'name':r.get('name') or (r.get('zh') or {}).get('title') or (r.get('en') or {}).get('title'),
            'aliases':r.get('aliases',[]),'qid':r.get('qid'),'identity_status':'source_only',
            'source_records':[{'fields':r,'source':origin}],
            'page_refs':[{'lang':lang,'page_id':str(r[lang]['page_id']),'mapped_concept_ref':ref} for lang in ('en','zh') if isinstance(r.get(lang),dict) and r[lang].get('page_id') is not None]}
    elif kind in {'legacy_docs','wiki_pages'}:
        refs=['legacy:'+x for x in (r.get('concepts') or r.get('instances') or [])] if kind=='legacy_docs' else ['qid:'+r['qid']] if r.get('qid') else []
        return {**r,'doc_id':sid,'concept_refs':refs,'format':'wiki_sections' if kind=='wiki_pages' else 'saved_text',
            'source_qid':r.get('qid'),'sections':r.get('sections',[]),'lang':r.get('lang'),
            'page_id':str(r['page_id']) if r.get('page_id') is not None else None,'source':origin,
            'association_status':'source_only' if refs else 'unassociated'}
    elif kind in {'legacy_images','qid_images'}:
        refs=['qid:'+r['qid']] if kind=='qid_images' and r.get('qid') else ['legacy:'+x for x in (r.get('concepts') or r.get('instances') or [])]
        return {**r,'image_id':sid,'path':r.get('path') or r.get('blob_path'),'concept_refs':refs,'source':origin}
    else:return {**r,'source':origin,'source_type':kind}

class ConceptFromRecord:
    """Decoded object + source locator -> concept identity and page references."""
    def __init__(self,source):self.source=source
    def __call__(self,row):return source_record(row,self.source)

class DocumentFromRecord(ConceptFromRecord):
    """Decoded object + source locator -> document fields and concept references."""

class ImageFromRecord(ConceptFromRecord):
    """Decoded object + source locator -> image fields and concept references."""
