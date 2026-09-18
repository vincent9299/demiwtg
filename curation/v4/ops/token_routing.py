"""Capacity-first joint material packing using the deployed Qwen tokenizer/config."""
import json
from pathlib import Path
from types import SimpleNamespace


def joint_payload(row):
    return {'concept':row['concept'], 'scope_context':row.get('scope_context', {}),
        'passages':[{k:p.get(k) for k in ['source_id','title','text','sections','source_family','reference_notes','context_before','context_after']} for p in row['passages']],
        'image_ids':[m['image_id'] for m in row['images']],
        'scope':'Capacity-first concept materials; associations are not identity or support certification'}


class JointTokenBudget:
    def __init__(self,model_path):
        from transformers import AutoTokenizer
        from .prompt_config import knowledge_prompt_pack
        from demiflow.operator_llm.client import _response_contract_instruction
        import yaml
        self.tokenizer=AutoTokenizer.from_pretrained(model_path,local_files_only=True)
        self.pre=json.loads((Path(model_path)/'preprocessor_config.json').read_text())
        if self.pre.get('processor_class')!='Qwen3VLProcessor':
            raise ValueError('Token accounting currently requires Qwen3VLProcessor')
        _,text=knowledge_prompt_pack({'model':'qwen3.8-27b','base_url':'http://127.0.0.1:8000/v1','article_mode':False})
        prompt=yaml.safe_load(text)['prompts']['joint_paragraphs']
        self.template=prompt['template']
        self.system=_response_contract_instruction(SimpleNamespace(response_schema=prompt['response_schema'],validation_feedback=None))
        self.image_cache={}

    def image_tokens(self,item):
        from PIL import Image,ImageOps
        from transformers.models.qwen2_vl.image_processing_qwen2_vl import smart_resize
        key=(item['bytes']['path'],item['record']['sha256'])
        if key not in self.image_cache:
            with Image.open(key[0]) as raw:
                im=ImageOps.exif_transpose(raw);im.thumbnail((1536,1536));w,h=im.size
            factor=self.pre['patch_size']*self.pre['merge_size']
            rh,rw=smart_resize(h,w,factor=factor,min_pixels=self.pre['size']['shortest_edge'],max_pixels=self.pre['size']['longest_edge'])
            self.image_cache[key]=rh*rw//factor**2
        return self.image_cache[key]

    def __call__(self,row):
        payload=json.dumps(joint_payload(row),ensure_ascii=False,sort_keys=True,separators=(',',':'))
        # Same prompt/response schema as demiflow. Expand visual placeholders by grid size.
        visual=''.join('<|vision_start|>'+'<|image_pad|>'*self.image_tokens(m)+'<|vision_end|>' for m in row['images'])
        user=self.template.replace('{{ payload | json }}',payload).replace('{{ images | image }}',visual)
        encoded=self.tokenizer.apply_chat_template([{'role':'system','content':self.system},{'role':'user','content':user}],tokenize=True,add_generation_prompt=True,enable_thinking=False)
        ids=encoded['input_ids'] if isinstance(encoded,dict) or hasattr(encoded,'keys') else encoded
        count=len(ids)
        return count+256  # Explicit conservative allowance for transport/template differences.


class RouteByTokenBudget:
    """Try the whole concept first. Never truncate an oversized indivisible unit."""
    def __init__(self,model_path,max_input_tokens=32768,counter=None,max_images=None):
        if max_images is not None and (not isinstance(max_images,int) or max_images<1):
            raise ValueError('max_images must be a positive request target or None')
        self.model_path=model_path;self.limit=max_input_tokens;self.counter=counter if counter is not None else JointTokenBudget(model_path)
        self.max_images=max_images

    def __call__(self,row):
        import numpy as np
        if self.counter is None:self.counter=JointTokenBudget(self.model_path)
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
