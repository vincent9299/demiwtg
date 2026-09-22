"""Source identity preparation and checks for the material pipeline."""
import json
import re
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit, unquote, parse_qs, urlencode
from curation.preparation.contracts import digest
from curation.preparation.ops import identity_checks as check
from curation.preparation.ops.quality_policy import material_disposition, defer_invalid_identity_quotes, complete_identity_membership
from curation.preparation.ops.prompt_loader import load_instruction
SYSTEM = load_instruction('system')
IDENTITY = load_instruction('identity')

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
        if row.get('blocked') and self.label != 'export':
            return {**row,'skipped_stages':row.get('skipped_stages',[])+[self.label]}
        return await self.process(row)
    async def aclose(self):
        if self.model is not None:await self.model.aclose()

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
