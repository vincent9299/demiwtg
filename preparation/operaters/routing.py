"""Whole-material routing by token capacity, native image links and embeddings."""
import io,json,math,time
from pathlib import Path
from collections import defaultdict
from demiflow.execution.artifacts import digest
from preparation.operaters.identity import material_id
from preparation.operaters.images import oriented_pixels


class PrepareRoutingMaterials:
    def __call__(self,row):
        pack=row['material_pack'];passages=pack['passages'];images=pack['images']
        background={r['material_id'] for r in row['identity'].get('material_reviews', [])
                    if r.get('basis') == 'text' and r.get('relation') == 'related_context'}
        aliases={a for m in row.get('bundle', {}).get('materials', []) if m.get('kind') == 'legacy_concepts'
                 for a in m.get('record', {}).get('aliases', []) if isinstance(a, str) and a}
        context={'aliases':sorted(aliases), 'background_titles':sorted({p['title'] for p in passages
                 if p.get('material_id') in background and p.get('title')})}
        image_urls={m['record'].get(k):m['image_id'] for m in images for k in ['content_url','url'] if m['record'].get(k)}
        by_material=defaultdict(list)
        for p in passages:by_material[p['material_id']].append(p)
        native=[];unmatched=[]
        for material in row['cleaned_materials']:
            mid=material_id(material)
            if mid not in by_material:continue
            for b in material.get('cleaning',{}).get('blocks',[]):
                if not b.get('images'):continue
                nearby=[]
                for p in by_material[mid]:
                    spans=[(x.get('raw_start'),x.get('raw_end')) for x in p.get('source_blocks',[]) if isinstance(x.get('raw_start'),int) and isinstance(x.get('raw_end'),int)]
                    distance=min((max(a-b.get('raw_end',0),b.get('raw_start',0)-z,0) for a,z in spans),default=10**9)
                    if distance<=500 and (not b.get('section') or b.get('section')==p.get('sections')):nearby.append((distance,p['source_id']))
                for im in b['images']:
                    edge={'material_id':mid,'url':im.get('target'),'original_caption':im.get('label'),'raw_start':b.get('raw_start'),'raw_end':b.get('raw_end'),'section':b.get('section'),'block_kind':b.get('kind')}
                    if im.get('target') in image_urls and nearby:
                        native.append({**edge,'source_id':min(nearby)[1],'image_id':image_urls[im['target']],'association':'exact_image_url_and_nearby_source_block; not semantic support certification'})
                    else:unmatched.append({**edge,'reason':'no_selected_image_url_match' if im.get('target') not in image_urls else 'no_nearby_selected_passage'})
        return {'case_id':row['case_id'],'concept':row['identity']['target_label'],'scope_context':context,'passages':passages,'images':images,'native_links':native,'unmatched_native_references':unmatched,
                'input_scope':{'selected_passages':len(passages),'selected_images':len(images),'other_images_not_added':True}}


class RawPassageRows:
    def __call__(self,row):
        return [{'concept':row['concept'],'case_id':row['case_id'],'paragraph_id':p['source_id'],'source_id':p['source_id'],
                 'title':' / '.join(p.get('sections') or [row['concept']]),'text':p['text'],'citations':[],'images':[],
                 'embedding_bucket':'all'} for p in row['passages']]


class EncodeImageTextMaterials:
    """All text windows and real pixels, CPU SigLIP2; retain window attribution."""
    def __init__(self,model_path,batch_size=8):self.path=str(model_path);self.batch_size=batch_size;self.model=None
    def __call__(self,row):
        import torch
        import torch.nn.functional as F
        from transformers import AutoModel,AutoProcessor
        from PIL import Image
        started=time.monotonic();torch.set_num_threads(8)
        if self.model is None:
            self.processor=AutoProcessor.from_pretrained(self.path,local_files_only=True)
            self.model=AutoModel.from_pretrained(self.path,local_files_only=True,dtype=torch.float32).to('cpu').eval()
        tokenizer=self.processor.tokenizer;limit=self.model.config.text_config.max_position_embeddings
        # Overflow windows retain all text; no head-only truncation.
        enc=tokenizer([p['text'] for p in row['passages']],padding='max_length',max_length=limit,truncation=True,stride=16,return_overflowing_tokens=True,return_tensors='pt') if row['passages'] else {}
        mapping=enc.pop('overflow_to_sample_mapping').tolist() if enc else [];vectors=[]
        def pooled(x):return x if isinstance(x,torch.Tensor) else x.pooler_output
        for start in range(0,len(mapping),self.batch_size):
            inputs={k:v[start:start+self.batch_size] for k,v in enc.items() if k in ['input_ids','attention_mask']}
            with torch.inference_mode():v=F.normalize(pooled(self.model.get_text_features(**inputs)),p=2,dim=-1)
            vectors.extend(v.tolist())
        windows=[{'source_id':row['passages'][idx]['source_id'],'input_ids':enc['input_ids'][i].tolist(),'text':tokenizer.decode(enc['input_ids'][i],skip_special_tokens=True),'embedding':vectors[i]} for i,idx in enumerate(mapping)]
        image_vectors=[];metadata_warnings=[]
        for start in range(0,len(row['images']),self.batch_size):
            batch=row['images'][start:start+self.batch_size];pics=[]
            for m in batch:
                # 路径只是定位提示；按 sha 统一资产解析（文件或湖内 Lance Blob）。
                from preparation.operaters.images import asset_bytes
                raw,_origin=asset_bytes(m['bytes'].get('path'),m['record']['sha256'])
                if digest(raw)!=m['record']['sha256']:raise ValueError('Image bytes changed')
                with Image.open(io.BytesIO(raw)) as im:
                    oriented,warning=oriented_pixels(im);pics.append(oriented.convert('RGB'))
                    if warning:metadata_warnings.append({'image_id':m['image_id'],**warning})
            inputs=self.processor(images=pics,return_tensors='pt')
            with torch.inference_mode():v=F.normalize(pooled(self.model.get_image_features(**inputs)),p=2,dim=-1)
            image_vectors.extend({'image_id':m['image_id'],'sha256':m['record']['sha256'],'embedding':vec} for m,vec in zip(batch,v.tolist()))
        return {'case_id':row['case_id'],'concept':row['concept'],'text_windows':windows,'image_vectors':image_vectors,'text_window_tokens':limit,'elapsed_s':time.monotonic()-started,
                **({'metadata_warnings':metadata_warnings} if metadata_warnings else {})}


class BuildRoutedJointRequest:
    def __call__(self,row):
        from preparation.operaters.images import pixels
        urls,roles=pixels(row['images'])
        return {'case_id':row['case_id'],'batch_id':row['batch_id'],'joint_prompt':joint_payload(row),
                'pixel_images':urls,'pixel_roles':roles,'routing_kind':row['kind'],'routing_edges':row['routing_edges']}


import json


class EmbedParagraphBatch:
    """Lazy CPU encoder following Qwen last-token pooling; bounded native batches."""
    def __init__(self,model_path,max_tokens=2048,threads=8):
        self.model_path=str(model_path);self.max_tokens=max_tokens;self.threads=threads;self.model=None
    def __call__(self,row):
        import torch
        import torch.nn.functional as F
        from transformers import AutoTokenizer,AutoModel
        if self.model is None:
            torch.set_num_threads(self.threads)
            self.tokenizer=AutoTokenizer.from_pretrained(self.model_path,local_files_only=True,padding_side='left')
            self.model=AutoModel.from_pretrained(self.model_path,local_files_only=True,dtype=torch.float32,attn_implementation='sdpa').to('cpu').eval()
        texts=[]
        for p in row['items']:texts.extend([p['title']+'\n'+p['text'],p['text']])
        lengths=[len(self.tokenizer.encode(t)) for t in texts]
        if max(lengths,default=0)>self.max_tokens:raise ValueError('Oversized paragraph; split explicitly instead of silent truncation')
        inputs=self.tokenizer(texts,padding=True,truncation=False,return_tensors='pt')
        with torch.inference_mode():
            vectors=F.normalize(self.model(**inputs).last_hidden_state[:,-1,:],p=2,dim=1).float().tolist()
        out=[]
        for i,p in enumerate(row['items']):
            evidence_text=json.dumps({k:p[k] for k in ['title','text','citations']},ensure_ascii=False)
            out.append({**p,'embedding_title_body':vectors[i*2],'embedding_body':vectors[i*2+1],
                        'embedding_tokens':lengths[i*2],'merge_content_tokens':len(self.tokenizer.encode(evidence_text))})
        return {**row,'items':out}


from types import SimpleNamespace


def joint_payload(row):
    return {'concept':row['concept'], 'scope_context':row.get('scope_context', {}),
        'passages':[{k:p.get(k) for k in ['source_id','title','text','sections','source_family','reference_notes','context_before','context_after']} for p in row['passages']],
        'image_ids':[m['image_id'] for m in row['images']],
        'scope':'Capacity-first concept materials; associations are not identity or support certification'}


class RouteByTokenBudget:
    """Try the whole concept first. Never truncate an oversized indivisible unit."""
    def __init__(self,model_path,max_input_tokens=32768,*,counter,max_images=None):
        if max_images is not None and (not isinstance(max_images,int) or max_images<1):
            raise ValueError('max_images must be a positive request target or None')
        self.model_path=model_path;self.limit=max_input_tokens;self.counter=counter
        self.max_images=max_images

    def __call__(self,row):
        import numpy as np
        def request(passages,images):
            return {'case_id':row['case_id'],'concept':row['concept'],'scope_context':row.get('scope_context', {}),'passages':passages,'images':images,'routing_edges':[],
                    'kind':'joint' if passages and images else 'text_only' if passages else 'image_only'}
        whole=request(row['passages'],row['images'])
        groups=[]
        def image_capacity(images):
            return self.max_images is None or len(images)<=self.max_images
        if image_capacity(whole['images']) and self.counter(whole)<=self.limit:groups=[whole] if row['passages'] or row['images'] else []
        else:
            byimage={m['image_id']:m for m in row['images']}
            native={p['source_id']:{e['image_id'] for e in row['native_links'] if e['source_id']==p['source_id']} for p in row['passages']}
            remaining=list(row['passages']);covered=set()
            embeddings=row.get('passage_embeddings',{})
            def similarity(p,g):
                a=embeddings.get(p['source_id'],{}).get('embedding_title_body')
                bs=[embeddings.get(q['source_id'],{}).get('embedding_title_body') for q in g['passages']]
                return max((float(np.dot(a,b)) for b in bs if a is not None and b is not None),default=0.)
            while remaining:
                if not groups:groups.append(request([],[]))
                g=groups[-1]
                ordered=sorted(remaining,key=lambda p:(-similarity(p,g),p['source_id']))
                chosen=None
                for p in ordered:
                    ids={m['image_id'] for m in g['images']}|native[p['source_id']]
                    candidate=request(g['passages']+[p],[byimage[i] for i in sorted(ids)])
                    # Keep one source passage's exact native links together. A
                    # required group may exceed the image target, never the token
                    # limit; do not append unrelated images to such a group.
                    required_overflow=(not g['passages'] and ids==native[p['source_id']]) or ids=={m['image_id'] for m in g['images']}
                    if (image_capacity(candidate['images']) or required_overflow) and self.counter(candidate)<=self.limit:
                        chosen=(p,candidate);break
                if chosen:
                    p,g=chosen;groups[-1]=g;remaining.remove(p);covered.update(m['image_id'] for m in g['images'])
                elif g['passages']:groups.append(request([],[]))
                else:raise ValueError('A whole passage with native images exceeds joint_input_tokens; explicit finer splitting required, no truncation')
            # Additional images are offered to the closest text group with available capacity.
            windows=row.get('text_windows',[]);ivs={v['image_id']:v['embedding'] for v in row.get('image_vectors',[])}
            for m in row['images']:
                if m['image_id'] in covered:continue
                def score(g):
                    ids={p['source_id'] for p in g['passages']};v=ivs.get(m['image_id'])
                    return max((float(np.dot(v,w['embedding'])) for w in windows if w['source_id'] in ids and v is not None),default=-2.)
                for g in sorted(groups,key=score,reverse=True):
                    candidate=request(g['passages'],g['images']+[m])
                    if image_capacity(candidate['images']) and self.counter(candidate)<=self.limit:g['images'].append(m);break
                else:
                    candidate=request([],[m])
                    if self.counter(candidate)>self.limit:raise ValueError('Single image exceeds joint_input_tokens')
                    groups.append(candidate)
        for i,g in enumerate(groups):
            g['batch_id']=row['case_id']+f':token_routed:{i}'
            g['input_token_budget']=self.counter(g);g['input_token_limit']=self.limit
            g['image_request_target']=self.max_images
            g['native_image_target_exceeded']=not image_capacity(g['images'])
            if g['input_token_budget']>self.limit:raise ValueError('Joint input capacity exceeded')
        return {'case_id':row['case_id'],'concept':row['concept'],'requests':groups,'edges':row['native_links'],'overflow_edges':[],
                'metrics':{'calls':len(groups),'input_token_limit':self.limit,'input_tokens':[g['input_token_budget'] for g in groups],
                           'image_request_target':self.max_images,'native_image_target_exceeded':sum(g['native_image_target_exceeded'] for g in groups),
                           'image_presentations':sum(len(g['images']) for g in groups)}}
