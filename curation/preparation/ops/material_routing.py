"""Raw material grouping and sparse image routing; no knowledge extraction."""
import io,json,math,time
from pathlib import Path
from collections import defaultdict
from curation.preparation.contracts import digest
from curation.preparation.ops.identity import material_id
from curation.preparation.ops.image_pixels import oriented_pixels


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
                from curation.preparation.asset_io import asset_bytes
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


def text_groups(passages,embeddings,threshold=.7,max_chars=2500):
    """Whole passages only; complete-link topic groups with the old char budget."""
    import numpy as np
    a=np.array([embeddings[p['source_id']]['embedding_title_body'] for p in passages]);scores=a@a.T
    groups=[{i} for i in range(len(passages))]
    edges=sorted(((float(scores[i,j]),i,j) for i in range(len(passages)) for j in range(i+1,len(passages))),reverse=True)
    for score,i,j in edges:
        if score<threshold:break
        x=next(g for g in groups if i in g);y=next(g for g in groups if j in g)
        if x is y:continue
        if sum(len(passages[k]['text']) for k in x|y)>max_chars:continue
        if any(scores[a,b]<threshold for a in x for b in y):continue
        groups.remove(x);groups.remove(y);groups.append(x|y)
    return [[passages[i] for i in sorted(g)] for g in sorted(groups,key=lambda x:min(x))]


def packed_text_groups(passages,embeddings,max_chars=2500):
    """Similarity orders whole passages; capacity, not a hard topic label, ends a batch."""
    import numpy as np
    a=np.array([embeddings[p['source_id']]['embedding_title_body'] for p in passages]);scores=a@a.T
    remaining=set(range(len(passages)));groups=[]
    while remaining:
        seed=min(remaining,key=lambda i:(-len(passages[i]['text']),passages[i]['source_id']))
        group=[seed];remaining.remove(seed);size=len(passages[seed]['text'])
        while True:
            candidates=[i for i in remaining if size+len(passages[i]['text'])<=max_chars]
            if not candidates:break
            chosen=min(candidates,key=lambda i:(-float(np.mean(scores[i,group])),passages[i]['source_id']))
            group.append(chosen);remaining.remove(chosen);size+=len(passages[chosen]['text'])
        groups.append([passages[i] for i in group])
    return groups


def route_materials(material,vectors,embeddings,threshold=.1,top_groups=1,text_threshold=.7,max_images=4,max_chars=2500,packing='complete_link'):
    import numpy as np
    groups=(text_groups(material['passages'],embeddings,text_threshold,max_chars) if packing=='complete_link' else packed_text_groups(material['passages'],embeddings,max_chars))
    textvec=np.array([w['embedding'] for w in vectors['text_windows']]);imagevec=np.array([v['embedding'] for v in vectors['image_vectors']]);scores=imagevec@textvec.T if len(imagevec) and len(textvec) else np.zeros((len(imagevec),len(textvec)))
    assigned=defaultdict(list);edges=[]
    for i,iv in enumerate(vectors['image_vectors']):
        iid=iv['image_id'];rank=[]
        native={next(j for j,g in enumerate(groups) if any(p['source_id']==e['source_id'] for p in g)) for e in material['native_links'] if e['image_id']==iid}
        for j,g in enumerate(groups):
            ids={p['source_id'] for p in g};matches=[k for k,w in enumerate(vectors['text_windows']) if w['source_id'] in ids]
            best=max(matches,key=lambda k:scores[i,k]);rank.append({'image_id':iid,'group':j,'score':float(scores[i,best]),'matched_source_id':vectors['text_windows'][best]['source_id'],'matched_window':vectors['text_windows'][best]['text'],'native':j in native})
        rank.sort(key=lambda x:(not x['native'],-x['score'],x['group']))
        picked=set(native)|{x['group'] for x in [x for x in rank if not x['native'] and x['score']>=threshold][:max(0,top_groups-len(native))]}
        for e in rank:
            e['selected']=e['group'] in picked;edges.append(e)
            if e['selected']:assigned[e['group']].append(e)
    byimage={m['image_id']:m for m in material['images']};requests=[];covered_images=set();overflow=[]
    for j,g in enumerate(groups):
        selected=sorted(assigned[j],key=lambda e:(not e['native'],-e['score'],e['image_id']))
        # Native links are not dropped to make room for embedding matches.
        native=[e for e in selected if e['native']];extra=[e for e in selected if not e['native']]
        capacity=max(max_images,len(native));used=native+extra[:max(0,capacity-len(native))]
        overflow.extend({**e,'reason':'group_image_capacity','included_in_group':False} for e in selected if e not in used)
        ids=[e['image_id'] for e in used];covered_images.update(ids)
        requests.append({'case_id':material['case_id'],'batch_id':material['case_id']+f':routed:{j}','concept':material['concept'],'passages':g,'images':[byimage[i] for i in ids],'routing_edges':used,'kind':'joint' if ids else 'text_only','native_over_image_target':len(native)>max_images})
    left=[m for m in material['images'] if m['image_id'] not in covered_images]
    for j in range(0,len(left),max_images):requests.append({'case_id':material['case_id'],'batch_id':material['case_id']+f':image_only:{j//max_images}','concept':material['concept'],'passages':[],'images':left[j:j+max_images],'routing_edges':[],'kind':'image_only'})
    assert {p['source_id'] for r in requests for p in r['passages']}=={p['source_id'] for p in material['passages']}
    assert {m['image_id'] for r in requests for m in r['images']}==set(byimage)
    assert sum(len(r['passages']) for r in requests)==len(material['passages'])
    return {'case_id':material['case_id'],'concept':material['concept'],'requests':requests,'edges':edges,'overflow_edges':overflow,
            'metrics':{'calls':len(requests),'text_groups':len(groups),'text_chars':sum(len(p['text']) for r in requests for p in r['passages']),
                       'image_presentations':sum(len(r['images']) for r in requests),'text_only':sum(r['kind']=='text_only' for r in requests),'image_only':sum(r['kind']=='image_only' for r in requests),'native_links':len(material['native_links'])}}


class BuildRoutedJointRequest:
    def __call__(self,row):
        from curation.preparation.ops.image_selection import pixels
        from curation.preparation.ops.token_routing import joint_payload
        urls,roles=pixels(row['images'])
        return {'case_id':row['case_id'],'batch_id':row['batch_id'],'joint_prompt':joint_payload(row),
                'pixel_images':urls,'pixel_roles':roles,'routing_kind':row['kind'],'routing_edges':row['routing_edges']}
