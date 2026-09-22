"""Embedding candidate discovery only: never delete, rewrite or certify knowledge."""
import json
from collections import defaultdict
from curation.preparation.contracts import digest


class ParagraphRows:
    def __call__(self,row):
        out=[]
        for topic in row['topics']:
            for block in topic['blocks']:
                if block['type']!='text':continue
                identity={k:row[k] for k in ['concept','run','batch_id']};identity['block_id']=block['block_id']
                related=[b for b in topic['blocks'] if b['type']=='image' and block['block_id'] in b.get('related_block_ids',[])]
                out.append({**identity,'paragraph_id':'P'+digest(identity)[:16],'title':topic['title'],'text':block['text'],
                            'citations':block.get('citations',[]),'image_refs':block.get('image_refs',[]),'images':related,'embedding_bucket':'all',
                            'topic_group_id':topic.get('topic_group_id',row.get('topic_group_id')),
                            'extraction_batch_id':row.get('extraction_batch_id',row['batch_id']),
                            'split_for_capacity':row.get('split_for_capacity',False)})
        return out


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


def pair_candidates(rows,field='embedding_title_body',threshold=.8,top_k=3):
    import numpy as np
    if top_k<1 or not -1<=threshold<=1:raise ValueError('Invalid candidate search configuration')
    if len({r['concept'] for r in rows})>1:raise ValueError('Candidate search must stay within one concept')
    if not rows:return [],[]
    a=np.asarray([r[field] for r in rows],dtype=float)
    norms=np.linalg.norm(a,axis=1)
    if not np.isfinite(a).all() or np.any(norms==0):raise ValueError('Invalid embedding')
    a=a/norms[:,None];scores=a@a.T;selected=set();rankings={}
    for i in range(len(rows)):
        order=sorted((j for j in range(len(rows)) if j!=i),key=lambda j:(-scores[i,j],rows[j]['paragraph_id']))
        rankings[i]={j:rank+1 for rank,j in enumerate(order)}
        selected.update(tuple(sorted((i,j))) for j in order[:top_k] if scores[i,j]>=threshold)
    pairs=[]
    for i in range(len(rows)):
        for j in range(i+1,len(rows)):
            pairs.append({'left':rows[i]['paragraph_id'],'right':rows[j]['paragraph_id'],'score':float(scores[i,j]),
                          'candidate':(i,j) in selected,'left_rank':rankings[i][j],'right_rank':rankings[j][i],
                          'shared_sources':sorted(set(c['source_id'] for c in rows[i]['citations']) & set(c['source_id'] for c in rows[j]['citations']))})
    return sorted(pairs,key=lambda p:-p['score']),scores.tolist()


def bounded_groups(rows,pairs,scores,threshold=.8,max_members=4,max_tokens=2000):
    """Complete-link, capacity-bounded proposals; record residual edges separately."""
    lookup={r['paragraph_id']:i for i,r in enumerate(rows)};groups=[{i} for i in range(len(rows))]
    for p in pairs:
        if not p['candidate']:continue
        i,j=lookup[p['left']],lookup[p['right']];gi=next(g for g in groups if i in g);gj=next(g for g in groups if j in g)
        if gi is gj:continue
        merged=gi|gj
        if len(merged)>max_members or sum(rows[k]['merge_content_tokens'] for k in merged)>max_tokens:continue
        if any(scores[x][y]<threshold for x in gi for y in gj):continue
        groups.remove(gi);groups.remove(gj);groups.append(merged)
    result=[];membership={}
    for n,g in enumerate(sorted(groups,key=lambda g:min(g))):
        ids=[rows[k]['paragraph_id'] for k in sorted(g)];membership.update({p:n for p in ids})
        result.append({'paragraph_ids':ids,'content_tokens':sum(rows[k]['merge_content_tokens'] for k in g),
                       'oversized_singleton':len(g)==1 and sum(rows[k]['merge_content_tokens'] for k in g)>max_tokens,
                       'action':'candidate_joint_review' if len(g)>1 else 'keep_independent'})
    residual=[p for p in pairs if p['candidate'] and membership[p['left']]!=membership[p['right']]]
    return result,residual


class FindParagraphNeighbors:
    def __init__(self,threshold=.8,top_k=3,max_members=4,max_tokens=2000):
        self.threshold=threshold;self.top_k=top_k;self.max_members=max_members;self.max_tokens=max_tokens
    def __call__(self,row):
        rows=row['items'];pairs,scores=pair_candidates(rows,threshold=self.threshold,top_k=self.top_k)
        groups,residual=bounded_groups(rows,pairs,scores,self.threshold,self.max_members,self.max_tokens)
        return {'concept':row['concept'],'pairs':pairs,'groups':groups,'residual_candidate_pairs':residual,
                'scope':'Candidate discovery only; no knowledge removed or merged. Quadratic exact search for this small calibration.',
                'budget_basis':'Paragraphs plus stored quotes; future prompt/source expansion/image tokens require separate sizing.'}
