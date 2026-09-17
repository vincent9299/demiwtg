"""Individually cached stages for the small-batch knowledge-candidate pipeline."""
import asyncio
import base64
import io
import json
import re
from pathlib import Path
from urllib.parse import urlsplit,urlunsplit,unquote,parse_qs,urlencode
import httpx
from curation.v4.contracts import digest,immutable,read
from curation.v4.local_model import CallBudgetExceeded,UncertainPreviousCall
from curation.v4.material_fetch import fetch_image
from . import knowledge_checks as check
from curation.v4.ops.quality_policy import material_disposition,quarantine_conflicts,image_followups,identity_followups,defer_invalid_identity_quotes,defer_unverifiable_facts,retain_extraction_scope,complete_identity_membership
from curation.v4.ops.knowledge_prompts import SYSTEM,IDENTITY,EXTRACT,CONSOLIDATE,EVIDENCE


def material_id(m):return 'M'+digest({'kind':m['kind'],'record':m['record'],'provenance':m['provenance']})[:12]
def message(prompt,data):return [{'role':'system','content':SYSTEM},{'role':'user','content':prompt+'\n输入数据：\n'+json.dumps(data,ensure_ascii=False)}]

def text_of(m):
    if 'cleaning' in m:return m['cleaning']['text']
    r=m['record']
    if m['kind']=='legacy_docs':return m.get('document',{}).get('text','')
    if m['kind']=='wiki_pages':return '\n\n'.join((s.get('title','')+'\n'+s.get('text','')).strip() for s in r.get('sections',[]))
    return ''

def family(m):
    r=m['record'];url=r.get('url')
    if not url and m['kind']=='wiki_pages':url=f'https://{r.get("lang","en")}.wikipedia.org/wiki/{r.get("title","")}'
    if url:
        u=urlsplit(url);host=u.netloc.lower();path=unquote(u.path).replace('_',' ');query=parse_qs(u.query)
        if host.endswith('.wikipedia.org') or host.endswith('.wikisource.org'):
            host=host.replace('.m.wikipedia.org','.wikipedia.org').replace('.m.wikisource.org','.wikisource.org')
            if path=='/w/index.php' and query.get('title'):path='/wiki/'+query['title'][0].replace('_',' ')
            path=re.sub(r'^/(?:zh(?:-(?:hans|hant|cn|tw|hk|mo|sg|my))?)/','/wiki/',path)
            if path.startswith('/wiki/'):return urlunsplit(('https',host,path,'',''))
        query={k:v for k,v in query.items() if not k.startswith('utm_') and k not in {'fbclid','gclid'}}
        return urlunsplit((u.scheme,host,path,urlencode(query,doseq=True),''))
    return m['material_id']

class Stage:
    concurrency=1;queue_depth=1;catch=()
    def __init__(self,run,config,model=None):self.run=Path(run);self.config=config;self.model=model;self.reused=0;self.executed=0
    async def __call__(self,row):
        path=self.run/'stages'/self.label/f'{row["case_id"]}.json'
        key=digest({'stage':self.label,'input':row,'config':self.config})
        if path.exists():
            saved=read(path)
            if saved['input_hash']!=key:raise ValueError('Stage inputs changed; use a new run')
            self.reused+=1;return saved['output']
        if row.get('blocked') and self.label!='export':out={**row,'skipped_stages':row.get('skipped_stages',[])+[self.label]}
        else:
            try:out=await self.process(row)
            except (ValueError,KeyError,TypeError,httpx.HTTPError,CallBudgetExceeded,UncertainPreviousCall) as e:
                out={**row,'blocked':{'stage':self.label,'reason':type(e).__name__,'detail':str(e)}}
        immutable(path,{'input_hash':key,'output':out});self.executed+=1;return out
    async def aclose(self):
        if self.model is not None:await self.model.aclose()

class CleanMaterials(Stage):
    label='clean'
    async def process(self,row):
        from curation.v4.ops.cleaning import clean_materials
        materials=await asyncio.to_thread(clean_materials,row['bundle'])
        return {**row,'cleaned_materials':materials,
                'cleaning_summary':[{'index':i,'kind':m['kind'],
                    'title':m['record'].get('title'),
                    'status':m['cleaning']['status'],'counts':m['cleaning']['counts'],
                    'warnings':m['cleaning']['warnings']} for i,m in enumerate(materials) if 'cleaning' in m]}

class ResolveIdentity(Stage):
    label='identity'
    async def prepare(self,row):
        bundle=row['bundle'];materials=[];identity=[];ineligible=[]
        for m in row['cleaned_materials']:
            if m['kind'] in ('legacy_concepts','qid_concepts','qid_concepts_base'):identity.append(m['record'])
            elif 'cleaning' in m and material_disposition(m['cleaning'])['status']=='pending':
                ineligible.append({'material_id':material_id(m),'reason':'pending_material_quality','disposition':material_disposition(m['cleaning']),
                                   'warnings':m.get('cleaning',{}).get('warnings',[])})
            elif text_of(m) or m['kind'] in ('legacy_images','qid_images'):
                materials.append({**m,'material_id':material_id(m)})
        docs=sorted([m for m in materials if text_of(m)],key=lambda m:m['material_id'])
        images=[m for m in materials if m['kind'] in ('legacy_images','qid_images')]
        # Spread the identity preview across page families before adding other versions.
        selected=[];seen=set()
        for m in docs:
            f=family(m)
            if f not in seen:selected.append(m);seen.add(f)
        # Fill spare preview slots with other versions after spreading across pages.
        represented={m['material_id'] for m in selected}
        selected.extend(m for m in docs if m['material_id'] not in represented)
        images.sort(key=lambda m:(m.get('bytes',{}).get('status')!='verified_bytes',m['material_id']))
        selected=selected[:self.config['identity_docs']]+images[:self.config.get('identity_images',self.config['identity_docs'])]
        if not selected:return {**row,'identity_ineligible':ineligible,'blocked':{'stage':self.label,'reason':'no_materials_in_scanned_scope'},'identity':{'status':'insufficient'}}
        previews=[{'material_id':m['material_id'],'kind':m['kind'],'title':m['record'].get('title'),
                   'url':m['record'].get('url',m['record'].get('content_url')),
                   'caption':m['record'].get('caption'),'pixels_provided':False,'text_preview':text_of(m)[:500],
                   'text_spans':[{'start':a,'end':min(a+500,len(text_of(m))),'text':text_of(m)[a:a+500]}
                       for a in sorted({0,max(0,len(text_of(m))//2-250),max(0,len(text_of(m))-500)}) if text_of(m)]} for m in selected]
        return {**row,'identity_ineligible':ineligible,'identity_materials':selected,
                'identity_unexamined':[m['material_id'] for m in materials if m not in selected],
                'identity_prompt':{'request':bundle['request'],'source_identity_records':identity,'materials':previews}}

    def apply(self,row,result,call):
        selected=row['identity_materials'];previews=row['identity_prompt']['materials']
        result=defer_invalid_identity_quotes(complete_identity_membership(result,previews),previews)
        check.identity(result,selected,previews)
        out={**{k:v for k,v in row.items() if k!='identity_prompt'},
             'identity':{**result,'reviewer':'local_model; not human identity adjudication','call':call}}
        if result['status']!='resolved':out['blocked']={'stage':self.label,'reason':result['status'],'detail':result['reason']}
        return out

    async def process(self,row):
        prepared=await self.prepare(row)
        if prepared.get('blocked'):return prepared
        result,call=await self.model.json(self.label,message(IDENTITY,prepared['identity_prompt']))
        return self.apply(prepared,result,call)

class OrganizeMaterials(Stage):
    label='organize'
    async def process(self,row):
        accepted=set(row['identity']['accepted_material_ids']);materials=[m for m in row['identity_materials'] if m['material_id'] in accepted]
        docs=[];images=[];duplicates=[];text_seen={};image_seen={};quality_deferred=[]
        for m in materials:
            if 'cleaning' in m and material_disposition(m['cleaning'])['status']=='pending':
                quality_deferred.append({'material_id':m['material_id'],'reason':'pending_material_quality','disposition':material_disposition(m['cleaning'])});continue
            text=text_of(m)
            if text:
                h=digest(' '.join(text.split()).encode())
                if h in text_seen:duplicates.append({'material_id':m['material_id'],'same_text_as':text_seen[h],'provenance':m['provenance']});continue
                text_seen[h]=m['material_id'];docs.append({**m,'text':text,'source_family':family(m)})
            elif m['kind'] in ('legacy_images','qid_images'):
                h=m['record'].get('sha256')
                if h in image_seen:duplicates.append({'material_id':m['material_id'],'same_bytes_as':image_seen[h],'provenance':m['provenance']});continue
                image_seen[h]=m['material_id'];images.append(m)
        # Prefer the most complete cleaned representation within the SAME page,
        # not as evidence of identity or publisher reliability. Other variants remain omissions.
        docs=sorted(docs,key=lambda m:(m['source_family'],-len(m['text']),m['material_id']))
        selected=[];seen=set()
        for m in docs:
            if m['source_family'] not in seen:selected.append(m);seen.add(m['source_family'])
        from curation.v4.ops.passage_selection import document_priority
        relations={r['material_id']:r['relation'] for r in row['identity'].get('material_reviews',[])}
        priorities={m['material_id']:document_priority(m,relations.get(m['material_id'],'same_identity')) for m in selected}
        selected.sort(key=lambda m:(not priorities[m['material_id']]['direct_subject'],-priorities[m['material_id']]['citation_band'],not priorities[m['material_id']]['encyclopedia_page'],m['source_family']))
        selection_review=[{'material_id':m['material_id'],'canonical_page':m['source_family'],**priorities[m['material_id']],
                           'selected':i<self.config['max_docs']} for i,m in enumerate(selected)]
        selected=selected[:self.config['max_docs']]
        passages=[];omissions=list(quality_deferred);remaining=self.config['max_input_chars']
        for m in docs:
            if m not in selected:
                omissions.append({'material_id':m['material_id'],'reason':'document/family budget; retained for later exploration'});continue
            from curation.v4.ops.passage_selection import select_passages,reference_notes,note_chars
            budget=min(self.config['max_chars_per_doc'],remaining)
            ranges,omitted=select_passages(m['cleaning'],budget)
            for start,end in ranges:
                text=m['text'][start:end]
                chosen=[b for b in m['cleaning']['blocks'] if 'clean_start' in b and start<=b['clean_start'] and b['clean_end']<=end]
                notes=reference_notes(m['cleaning'],chosen);remaining-=len(text)+note_chars(notes)
                passages.append({'source_id':'S'+digest({'material':m['material_id'],'start':start,'end':end})[:12],
                  'material_id':m['material_id'],'text':text,'start':start,'end':end,'original_chars':len(m['text']),
                  'source_family':m['source_family'],'provenance':m['provenance'],'document_sha256':digest(m['text'].encode()),
                  'quote_basis':'cleaned_text','selection':'whole_blocks_across_sections','reference_notes':notes,'sections':list(dict.fromkeys(h for b in chosen for h in b.get('section',[]))),
                  'cleaning_version':m['cleaning']['version'],'raw_source_sha256':m['cleaning']['source_sha256'],
                  'source_locator':m['cleaning']['source_locator'],
                  'source_blocks':[dict(b) for b in m['cleaning']['blocks']
                      if 'clean_start' in b and start<=b['clean_start'] and b['clean_end']<=end]})
            omissions.extend({'material_id':m['material_id'],'reason':'complete blocks outside character budget','start':a,'end':b}
                             for a,b in omitted)
            omissions.extend({'material_id':m['material_id'],'reason':b['reason'],'block_id':b['block_id'],
                              'raw_start':b['raw_start'],'raw_end':b['raw_end'],'next_action':'parse_source_markup'}
                             for b in m['cleaning']['blocks'] if b.get('decision')=='defer')
        omissions.extend({'material_id':m['material_id'],'image_id':'I'+m['record'].get('sha256','')[:12],
                          'reason':'image_support_budget','next_action':'check_remaining_image_support'}
                         for m in images[self.config['max_images']:])
        ready=[];image_gaps=[]
        for m in images[:self.config['max_images']]:
            status=m.get('bytes',{})
            if status.get('status')=='verified_bytes':
                p=Path(status['path'])
                if not p.is_file() or digest(p.read_bytes())!=m['record'].get('sha256'):status={'status':'changed_or_missing_bytes'}
            else:status=await fetch_image(m['record'],self.run,self.config)
            item={**m,'image_id':'I'+m['record'].get('sha256','')[:12],'bytes':status}
            if status.get('status')=='verified_bytes':ready.append(item)
            else:image_gaps.append(item)
        out={**row,'material_pack':{'passages':passages,'images':ready,'duplicates':duplicates,'omissions':omissions,'image_gaps':image_gaps,
               'document_selection':selection_review,
               'coverage':'Different pages are not automatically independent or reliable. Selected input only; no whole-concept completeness claim. Same-page variants are not independent sources.'}}
        if not passages:out['blocked']={'stage':self.label,'reason':'no_source_text_in_selected_materials','detail':'Images retained; image-only knowledge extraction not implemented in this pilot.'}
        return out

def model_passages(passages):
    # Provenance can include large source blocks; it stays on disk, outside model budgets.
    return [{**{k:p[k] for k in ('source_id','text','source_family','start','end')},
             'reference_notes':p.get('reference_notes',[]),'sections':p.get('sections',[])} for p in passages]


class ExtractKnowledge(Stage):
    label='extract'
    async def prepare(self,row):
        return {**row,'extract_prompt':{'concept':row['identity']['target_label'],
                                      'passages':model_passages(row['material_pack']['passages'])}}
    def apply(self,row,result,call):
        passages=row['material_pack']['passages']
        result=retain_extraction_scope(defer_unverifiable_facts(result,passages),passages)
        check.knowledge(result,passages)
        return {**{k:v for k,v in row.items() if k!='extract_prompt'},'extraction':result,'extraction_call':call}
    async def process(self,row):
        prepared=await self.prepare(row)
        result,call=await self.model.json(self.label,message(EXTRACT,prepared['extract_prompt']))
        return self.apply(prepared,result,call)

class ConsolidateKnowledge(Stage):
    label='consolidate'
    async def prepare(self,row):
        return {**row,'consolidate_prompt':{'concept':row['identity']['target_label'],
            'passages':model_passages(row['material_pack']['passages']),'candidates':row['extraction'],
            'previous_unresolved_conflicts':row['extraction'].get('unresolved_conflicts',[])}}
    def apply(self,row,result,call):
        passages=row['material_pack']['passages']
        result=retain_extraction_scope(defer_unverifiable_facts(result,passages,row['extraction']),passages)
        check.knowledge(result,passages)
        if not isinstance(result.get('changes'),list):raise ValueError('Consolidation must preserve change reasons')
        result=quarantine_conflicts(result,row['extraction'])
        return {**{k:v for k,v in row.items() if k!='consolidate_prompt'},'knowledge':result,
                'consolidation_call':call,'knowledge_review_status':'machine_candidate; semantic correctness and conflict resolution await review'}
    async def process(self,row):
        prepared=await self.prepare(row)
        result,call=await self.model.json(self.label,message(CONSOLIDATE,prepared['consolidate_prompt']))
        return self.apply(prepared,result,call)

class CheckImageSupport(Stage):
    label='evidence'
    async def prepare(self,row):
        images=row['material_pack']['images'];facts=row['knowledge']['facts']
        if not images or not facts:
            return {**row,'image_evidence':{'status':'not_run','reason':'no_available_images' if not images else 'no_knowledge_candidates','images':[],'support':[]}}
        from PIL import Image,ImageOps
        content=[{'type':'text','text':EVIDENCE+'\n知识：'+json.dumps(facts,ensure_ascii=False)}]
        roles=[]
        for im in images:
            path=Path(im['bytes']['path']);raw=path.read_bytes()
            if digest(raw)!=im['record']['sha256']:raise ValueError('Image changed before model input')
            with Image.open(io.BytesIO(raw)) as source:
                pic=ImageOps.exif_transpose(source).convert('RGB');pic.thumbnail((1024,1024));buf=io.BytesIO();pic.save(buf,format='JPEG',quality=90)
            content.extend([{'type':'text','text':im['image_id']+'：知识证据候选图，不是目标图。'},
                            {'type':'image_url','image_url':{'url':'data:image/jpeg;base64,'+base64.b64encode(buf.getvalue()).decode()}}])
            roles.append({'image_id':im['image_id'],'path':str(path),'original_sha256':digest(raw),'input_sha256':digest(buf.getvalue()),'role':'knowledge_evidence_candidate'})
        return {**row,'evidence_prompt':{'facts':facts,'image_ids':[im['image_id'] for im in images]},
                'evidence_images':[part['image_url']['url'] for part in content if part['type']=='image_url'],
                'prepared_image_roles':roles}

    def apply(self,row,result,call):
        check.evidence(result,row['material_pack']['images'],row['knowledge']['facts'])
        return {**{k:v for k,v in row.items() if k not in {'evidence_prompt','evidence_images','prepared_image_roles'}},
                'image_evidence':{'status':'machine_reviewed','result':result,'image_roles':row['prepared_image_roles'],
                                  'call':call,'note':'Captions are model observations, not source captions or human approval.'}}

    async def process(self,row):
        prepared=await self.prepare(row)
        if 'evidence_prompt' not in prepared:return prepared
        content=[{'type':'text','text':EVIDENCE+'\n知识：'+json.dumps(prepared['evidence_prompt']['facts'],ensure_ascii=False)}]
        for image_id,url in zip(prepared['evidence_prompt']['image_ids'],prepared['evidence_images']):
            content.extend([{'type':'text','text':image_id+'：知识证据候选图，不是目标图。'},
                            {'type':'image_url','image_url':{'url':url}}])
        result,call=await self.model.json(self.label,[{'role':'system','content':SYSTEM},{'role':'user','content':content}])
        return self.apply(prepared,result,call)

class ExportCandidates(Stage):
    label='export'
    def build(self,row):
        record={'case_id':row['case_id'],'concept_id':row['bundle']['concept_id'],'request':row['bundle']['request'],
                'status':'blocked' if row.get('blocked') else 'machine_candidates_ready_for_review' if row.get('knowledge',{}).get('facts') else 'pending_evidence_or_review',
                'blocked':row.get('blocked'),'identity':row.get('identity'),
                'facts':row.get('knowledge',{}).get('facts',[]),'unresolved_conflicts':row.get('knowledge',{}).get('unresolved_conflicts',[]),
                'deferred_facts':row.get('knowledge',{}).get('deferred_facts',[]),
                'fidelity_reviews':row.get('fidelity_reviews',[]),
                'source_relations':row.get('knowledge',{}).get('source_relations',[]),
                'material_followups':row.get('identity_ineligible',[])+identity_followups(row.get('identity',{}))+row.get('material_pack',{}).get('omissions',[])+row.get('knowledge',{}).get('remaining_knowledge_review',[]),
                'coverage_note':row.get('knowledge',{}).get('coverage_note'),
                'model_coverage_note_unverified':row.get('knowledge',{}).get('model_coverage_note'),
                'image_followups':image_followups(row.get('knowledge',{}).get('facts',[]),row.get('image_evidence') or {}),
                'image_evidence':row.get('image_evidence'),'scope':'Not a human-approved core, benchmark question or complete concept KB.'}
        return {**row,'export':record}

    async def process(self,row):
        out=self.build(row)
        immutable(self.run/'candidates'/f'{row["case_id"]}.json',out['export'])
        return out
