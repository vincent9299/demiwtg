"""Identify bounded cross-batch review candidates; never delete or certify duplication."""
import json,re
from itertools import combinations
from ..contracts import digest
from .paragraph_similarity import pair_candidates


class PlanCrossBatchReview:
    def __init__(self,threshold=.8,top_k=3,max_pair_chars=24000,include_same_batch=False,skip_single_batch=False):
        self.skip_single_batch=skip_single_batch
        self.threshold=threshold;self.top_k=top_k;self.max_pair_chars=max_pair_chars;self.include_same_batch=include_same_batch
    def __call__(self,row):
        items=row['items'];lookup={x['paragraph_id']:x for x in items}
        if len(lookup)!=len(items):raise ValueError('Paragraph identities must be unique')
        if any(x['concept']!=row['concept'] for x in items):raise ValueError('Cross-concept review is not allowed')
        if self.skip_single_batch and len({x.get('extraction_batch_id',x.get('batch_id')) for x in items}) <= 1:
            return {'concept':row['concept'],'review_requests':[],'pending':[],
                    'passthrough_paragraph_ids':list(lookup),'all_paragraph_ids':list(lookup),
                    'missing_semantic_inputs':[],'scope':'Single extraction group; skip cross-group integration'}
        reasons={};semantic_missing=[]
        def add(a,b,why):reasons.setdefault(tuple(sorted((a,b))),set()).add(why)
        for field in ['embedding_title_body','embedding_body']:
            if items and all(field in x for x in items):
                pairs,_=pair_candidates(items,field=field,threshold=self.threshold,top_k=self.top_k)
                for p in pairs:
                    if p['candidate']:add(p['left'],p['right'],field)
            else:semantic_missing.append(field)
        def norm(s):return re.sub(r'\s+',' ',s).strip()
        for a,b in combinations(items,2):
            if not self.include_same_batch and a.get('run')==b.get('run') and a.get('batch_id')==b.get('batch_id'):continue
            if norm(a['text']) and norm(a['text'])==norm(b['text']):add(a['paragraph_id'],b['paragraph_id'],'identical_prose')
            if a.get('topic_group_id') and a.get('topic_group_id')==b.get('topic_group_id') and (a.get('split_for_capacity') or b.get('split_for_capacity')):
                add(a['paragraph_id'],b['paragraph_id'],'same_topic_capacity_split')
            for x in a.get('citations',[]):
                for y in b.get('citations',[]):
                    q=x.get('quote','');r=y.get('quote','')
                    if x.get('source_id')==y.get('source_id') and min(len(q),len(r))>=24 and (q in r or r in q):
                        add(a['paragraph_id'],b['paragraph_id'],'overlapping_source_quote')
        requests=[];pending=[];involved=set()
        for (a,b),why in sorted(reasons.items()):
            left,right=lookup[a],lookup[b]
            if not self.include_same_batch and left.get('run')==right.get('run') and left.get('batch_id')==right.get('batch_id'):continue
            involved.update([a,b])
            pair={'request_id':'R'+digest([a,b,sorted(why)])[:16],'concept':row['concept'],
                  'paragraph_ids':[a,b],'reasons':sorted(why),'action':'review_relationship_not_assumed_duplicate'}
            # Freeze bounded candidate evidence, not one unbounded connected component.
            material=[{k:x[k] for k in ['paragraph_id','run','batch_id','title','text','citations','image_refs','images'] if k in x} for x in [left,right]]
            if len(json.dumps(material,ensure_ascii=False))>self.max_pair_chars:
                pending.append({**pair,'reason':'requires_finer_evidence_window','items':material})
            else:requests.append({**pair,'items':material})
        return {'concept':row['concept'],'review_requests':requests,'pending':pending,
                'passthrough_paragraph_ids':[x['paragraph_id'] for x in items if x['paragraph_id'] not in involved],
                'all_paragraph_ids':list(lookup),'missing_semantic_inputs':semantic_missing,
                'scope':'Candidate planning only. Preserve originals until semantic review; related/duplicate/complementary/conditional/conflicting/unrelated are model decisions. Pair requests must be reconciled before any overlapping rewrites.'}


class ApplyCrossBatchReview:
    def __call__(self,row):
        result=row.get('prompt_result') or {}
        relation=result.get('relationship');ids=result.get('paragraph_ids',[])
        valid=(not row.get('prompt_error') and isinstance(ids,list) and len(ids)==2
               and all(isinstance(i,str) for i in ids) and set(ids)==set(row['paragraph_ids'])
               and relation in {'duplicate','partial_overlap','complementary','conditional_difference','conflict','unrelated','uncertain'}
               and isinstance(result.get('reason'),str) and bool(result['reason'].strip()))
        action='pending' if not valid or relation=='uncertain' else 'passthrough' if relation=='unrelated' else 'local_integration_candidate'
        return {**row,'relationship_review':result,'next_action':action,'review_valid':valid,
                'policy':'Retain original content. Reconcile overlapping candidate pairs before bounded local integration; never blindly apply pairwise rewrites.'}


def review_cross_batch_requests(plans, *, run, version, config='knowledge.yaml'):
    """Native demiflow: zero requests means zero model calls; no new executor."""
    from pathlib import Path
    run=Path(run)
    return (plans.flat_map(lambda r:r['review_requests'])
            .map(lambda r:{**r,'review_payload':{'items':r['items'],'reasons':r['reasons']}})
            .map_prompt_async('review_cross_batch',config=config,inputs={'payload':'review_payload'},
                              output='prompt_result',call_output='prompt_call',error_output='prompt_error',concurrency=1,queue_depth=1)
            .map_cached(ApplyCrossBatchReview(),cache_dir=run/'cache/cross_batch_review',version=version)
            .checkpoint(run/'cross_batch_reviews.jsonl',version=version))


class BatchRelationshipReviews:
    """Plan row -> bounded multi-pair requests; no candidate or evidence truncation."""
    def __init__(self,max_pairs=8,max_chars=24000):
        if max_pairs<1 or max_chars<1:raise ValueError('Positive batch capacity required')
        self.max_pairs=max_pairs;self.max_chars=max_chars

    def __call__(self,row):
        batches=[];pending=[]
        def flush():
            if not pending:return
            pairs=list(pending)
            payload={'concept':row['concept'],'pairs':[
                {k:r[k] for k in ['request_id','paragraph_ids','reasons','items']} for r in pairs]}
            batches.append({'concept':row['concept'],'pairs':pairs,'review_payload':payload,
                            'batch_id':'relations:'+digest(payload)[:20]})
            pending.clear()
        def size(pairs):
            return len(json.dumps({'concept':row['concept'],'pairs':[
                {k:r[k] for k in ['request_id','paragraph_ids','reasons','items']} for r in pairs]},ensure_ascii=False))
        for pair in row['review_requests']:
            if size([pair])>self.max_chars:
                flush()
                batches.append({'concept':row['concept'],'pairs':[pair], 'review_payload':None,
                                'batch_id':'relations:oversize:'+pair['request_id'],
                                'batch_error':'relationship_evidence_exceeds_capacity'})
                continue
            if pending and (len(pending)>=self.max_pairs or size(pending+[pair])>self.max_chars):flush()
            pending.append(pair)
        flush()
        return batches


class ApplyRelationshipReviews:
    """Batch result -> original pair rows; omitted/invalid answers remain pending."""
    def __call__(self,row):
        result=row.get('prompt_result') or {};reviews=result.get('reviews',[])
        expected={p['request_id'] for p in row['pairs']};by={};bad=not isinstance(reviews,list)
        if isinstance(reviews,list):
            for review in reviews:
                if not isinstance(review,dict) or review.get('request_id') not in expected:
                    bad=True;continue
                key=review['request_id']
                if key in by:bad=True
                by[key]=review
        error=row.get('prompt_error') or row.get('batch_error') or ('invalid_batch_relationship_ids' if bad else None)
        out=[]
        for pair in row['pairs']:
            answer=by.get(pair['request_id'])
            item={**pair,'prompt_result':answer or {},
                  'prompt_error':error or (None if answer else 'missing_pair_review'),
                  'prompt_call':row.get('prompt_call'),'relationship_batch_id':row['batch_id']}
            out.append(ApplyCrossBatchReview()(item))
        return {**row,'pair_reviews':out}
