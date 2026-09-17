"""Structured interleaved paragraphs; local validation never certifies truth."""
from collections import Counter
import copy
from .citation_alignment import align_source_quote


class ApplyParagraphs:
    def __call__(self,row):
        result=row.get('prompt_result') or {}
        raw=copy.deepcopy(result.get('topics',[]));topics=[];quote_repairs=[]
        passages={p['source_id']:p['text'] for p in row['joint_prompt']['passages']}
        images=set(row['joint_prompt']['image_ids'])
        raw=raw if isinstance(raw,list) else []
        ids=Counter(b.get('block_id') for t in raw if isinstance(t,dict) for b in t.get('blocks',[]) if isinstance(b,dict) and isinstance(b.get('block_id'),str))
        for topic in raw:
            if not isinstance(topic,dict) or not isinstance(topic.get('blocks'),list):continue
            blocks=[];text_ids={b.get('block_id') for b in topic['blocks'] if isinstance(b,dict) and b.get('type')=='text' and isinstance(b.get('block_id'),str)}
            for b in topic['blocks']:
                if not isinstance(b,dict):continue
                issues=[];bid=b.get('block_id')
                if not isinstance(bid,str) or not bid or ids[bid]!=1:issues.append('invalid_or_duplicate_block_id')
                if row.get('prompt_error'):issues.append('prompt_error')
                if b.get('status') not in {'candidate','deferred'}:issues.append('invalid_status')
                if not isinstance(b.get('reason'),str):issues.append('invalid_reason')
                if b.get('type')=='text':
                    citations=b.setdefault('citations',[])
                    refs=b.setdefault('image_refs',[])
                    if not isinstance(refs,list):issues.append('invalid_image_refs');refs=[]
                    for ref in refs:
                        if not isinstance(ref,dict) or ref.get('image_id') not in images or not isinstance(ref.get('region'),str) or not ref['region'].strip():issues.append('invalid_image_ref')
                    if not isinstance(b.get('text'),str) or not b['text'].strip():issues.append('missing_text')
                    if not isinstance(citations,list) or (not citations and not refs):issues.append('missing_citations')
                    else:
                        for c in citations:
                            if isinstance(c,dict) and isinstance(c.get('quote'),str):
                                before=c['quote'];after=align_source_quote(before,passages.get(c.get('source_id'),''))
                                if before!=after:
                                    quote_repairs.append({'block_id':bid,'source_id':c.get('source_id'),'before':before,'after':after})
                                    c['quote']=after
                            if not isinstance(c,dict) or not isinstance(c.get('source_id'),str) or not isinstance(c.get('quote'),str) or not c['quote'].strip() or c['quote'] not in passages.get(c['source_id'],''):issues.append('quote_not_in_input')
                elif b.get('type')=='image':
                    if not isinstance(b.get('image_id'),str) or b['image_id'] not in images:issues.append('unknown_image')
                    for key in ['caption','region','limitations']:
                        if not isinstance(b.get(key),str) or not b[key].strip():issues.append('missing_'+key)
                    links=b.get('related_block_ids')
                    if not isinstance(links,list) or any(not isinstance(x,str) or x not in text_ids for x in links):issues.append('invalid_related_blocks')
                else:issues.append('invalid_block_type')
                blocks.append({**b,'status':'deferred' if issues or b.get('status')=='deferred' else 'candidate',
                               'validation_issues':issues,'truth_status':'not_verified'})
            topics.append({'title':topic.get('title','未命名主题'),'blocks':blocks})
        clean={k:v for k,v in row.items() if k not in {'prompt_result','prompt_call','prompt_error'}}
        return {**clean,'topic_gate_required':True,'topics':topics,'coverage_note':result.get('coverage_note',''),
                'extraction_call':row.get('prompt_call'),'extraction_error':row.get('prompt_error'),
                'raw_extraction':result,'citation_format_repairs':quote_repairs,'verify_payload':verification_payload(row['joint_prompt'],topics)}


class ApplyParagraphReview:
    def __call__(self,row):
        result=row.get('prompt_result') or {};reviews=result.get('reviews',[]);topics=[]
        for t in row['topics']:
            blocks=[]
            for b in t['blocks']:
                matches=[r for r in reviews if isinstance(r,dict) and r.get('block_id')==b['block_id']] if isinstance(reviews,list) else []
                review=matches[0] if len(matches)==1 else {}
                valid=not row.get('prompt_error') and review.get('status') in {'supported','deferred'} and isinstance(review.get('reason'),str) and bool(review['reason'].strip())
                blocks.append({**b,'status':'candidate' if b['status']=='candidate' and valid and review['status']=='supported' else 'deferred',
                               'review':review if valid else {'status':'deferred','reason':'Missing/invalid review'}})
            checks=result.get('topic_reviews',[])
            matches=[x for x in checks if isinstance(x,dict) and x.get('topic_index')==len(topics)] if isinstance(checks,list) else []
            topic_review=matches[0] if len(matches)==1 and matches[0].get('status') in {'supported','repair','deferred'} and matches[0].get('reason') else {'status':'repair','reason':'Missing/invalid topic review'}
            topics.append({**t,'blocks':blocks,'topic_review':topic_review})
        return {**{k:v for k,v in row.items() if k not in {'pixel_images','prompt_result','prompt_call','prompt_error','verify_payload'}},
                'topics':topics,'verification_call':row.get('prompt_call'),'verification_error':row.get('prompt_error'),'raw_verification':result}


class SelectRetainedParagraphs:
    """Machine gates plus explicit scoped exclusions; never promotes rejected blocks."""
    def __init__(self,exclusions=()):
        self.exclusions={(x['run'],x['batch_id'],x['block_id']):x['reason'] for x in exclusions}
    def __call__(self,row):
        topics=[];decisions=[]
        for t in row['topics']:
            blocks=[]
            for b in t['blocks']:
                reason=self.exclusions.get((row['run'],row['batch_id'],b['block_id']))
                keep=b.get('status')=='candidate' and not reason
                decisions.append({'block_id':b['block_id'],'keep':keep,'reason':reason or (b.get('review') or {}).get('reason',''),
                                  'machine_status':b.get('status')})
                if keep:
                    keys=['block_id','type','text','citations','image_refs'] if b['type']=='text' else ['block_id','type','image_id','caption','region','limitations','related_block_ids']
                    blocks.append({k:b[k] for k in keys if k in b})
            if row.get('topic_gate_required') and (t.get('topic_review',{}).get('status')!='supported' or not any(b['type']=='text' for b in blocks)):
                blocks=[]
            if blocks:
                # If a related paragraph is removed, retain the independent image observation,
                # but never leave a dangling association in the public document.
                ids={b['block_id'] for b in blocks}
                for b in blocks:
                    if b['type']=='image':b['related_block_ids']=[x for x in b.get('related_block_ids',[]) if x in ids]
                topics.append({'title':t['title'],'blocks':blocks})
        return {'content':{'concept':row['joint_prompt']['concept'],'batch_id':row['batch_id'],'run':row['run'],'extraction_batch_id':row.get('parent_batch_id',row['batch_id']),'topics':topics},
                'decisions':{'run':row['run'],'batch_id':row['batch_id'],'blocks':decisions}}



def verification_payload(joint_prompt,topics):
    """Evidence and claims only; previous acceptance/reasons must not anchor review."""
    fields=['block_id','type','text','citations','image_refs','image_id','caption','region','limitations','related_block_ids']
    return {**{k:joint_prompt[k] for k in ['concept','concept_identity','passages','image_ids'] if k in joint_prompt},
        'topics':[{'title':t['title'],'blocks':[{k:b[k] for k in fields if k in b} for b in t['blocks']]} for t in topics]}
