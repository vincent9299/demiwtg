"""Cross-batch prose integration; preserve immutable image observations and provenance."""
import json
import re
from collections import Counter
from .paragraphs import ApplyParagraphs, SelectRetainedParagraphs, verification_payload
from ..contracts import digest


from .citation_alignment import align_source_quote


class PrepareParagraphMerge:
    def __init__(self, exclusions=(), max_payload_chars=120000):
        self.select=SelectRetainedParagraphs(exclusions)
        self.max_payload_chars=max_payload_chars

    def __call__(self, group):
        items=[];passages={};images={};pixels={};roles={};exclusion_notes=[]
        for row in group['rows']:
            for topic in row['topics']:
                for b in topic['blocks']:
                    reason=self.select.exclusions.get((row['run'],row['batch_id'],b['block_id']))
                    if reason:exclusion_notes.append({'text':b.get('text',b.get('caption','')),'reason':reason})
            public=self.select(row)['content']
            # Re-extract only citation-format failures against raw evidence; never
            # promote them directly or revive semantic exclusions.
            for topic in row['topics']:
                repair=[b for b in topic['blocks'] if b.get('type')=='text'
                        and b.get('status')=='deferred' and b.get('validation_issues')
                        and set(b['validation_issues'])=={'quote_not_in_input'}
                        and b.get('review',{}).get('status')=='supported'
                        and (row['run'],row['batch_id'],b['block_id']) not in self.select.exclusions]
                if repair:public['topics'].append({'title':topic['title'],'blocks':[{**b,'needs_reference_repair':True} for b in repair]})
            for p in row['joint_prompt']['passages']:
                if p['source_id'] in passages and passages[p['source_id']]['text']!=p['text']:
                    raise ValueError('Conflicting source IDs')
                passages[p['source_id']]=p
            for iid,pix,role in zip(row['joint_prompt']['image_ids'],row['pixel_images'],row['pixel_roles']):
                pixels[iid]=pix;roles[iid]=role
            for topic in public['topics']:
                for b in topic['blocks']:
                    item_id='B'+digest([row['run'],row['batch_id'],b['block_id']])[:16]
                    item={**b,'item_id':item_id,'title':topic['title'],'origin':{k:row[k] for k in ['run','batch_id']}}
                    items.append(item)
                    if b['type']=='image':images[item_id]=item
        allowed={b['image_id'] for b in images.values()} | {ref['image_id'] for b in items for ref in b.get('image_refs',[])}
        pixels={iid:pix for iid,pix in pixels.items() if iid in allowed}
        payload={'concept':group['concept'],'items':items,'passages':list(passages.values()),'image_ids':list(pixels),'exclusion_notes':exclusion_notes}
        # Explicit refusal rather than silently truncating or claiming unbounded scalability.
        if len(json.dumps(payload,ensure_ascii=False))>self.max_payload_chars:
            raise ValueError('Concept exceeds merge payload budget; subdivide related themes in a new run')
        return {'case_id':group['concept'],'batch_id':'merged:'+digest(payload)[:16],
                'merge_payload':payload,'joint_prompt':{**payload,'image_ids':list(pixels)},
                'pixel_images':list(pixels.values()),'pixel_roles':[roles[i] for i in pixels],
                'image_observations':images}


class ApplyParagraphMerge:
    def __call__(self,row):
        result=row.get('prompt_result') or {};items={b['item_id']:b for b in row['merge_payload']['items']}
        decisions=result.get('decisions',[]);decisions=list(decisions) if isinstance(decisions,list) else []
        # The output already declares ancestry. Recover only a missing inverse
        # mapping; never override an explicit exclusion or invent ancestry.
        accounted={d.get('input_id') for d in decisions if isinstance(d,dict)}
        repairs=[]
        for iid in items.keys()-accounted:
            links=[b.get('block_id') for t in result.get('topics',[]) for b in t.get('blocks',[])
                   if iid in (b.get('input_ids',[]) if b.get('type')=='text' else [b.get('input_id')])]
            if links and all(isinstance(x,str) and x for x in links):
                repair={'input_id':iid,'action':'used','output_block_ids':links,
                        'reason':'Program reconstructed missing inverse mapping from explicit output ancestry; prose unchanged'}
                decisions.append(repair);repairs.append(repair)
        counts=Counter(d.get('input_id') for d in decisions if isinstance(d,dict))
        issues=[]
        if set(counts)!=set(items) or any(n!=1 for n in counts.values()):issues.append('incomplete_input_accounting')
        topics=[];output_ids=set();image_ids=set();output_inputs={};quote_repairs=[]
        sources={p['source_id']:p['text'] for p in row['joint_prompt']['passages']}
        for t in result.get('topics',[]):
            blocks=[]
            for b in t.get('blocks',[]):
                if b.get('type')=='image':
                    source=row['image_observations'].get(b.get('input_id'))
                    if not source:
                        issues.append('unknown_image_observation');continue
                    if source['image_id'] in image_ids:
                        issues.append('duplicate_image');continue
                    image_ids.add(source['image_id'])
                    b={**{k:source[k] for k in ['type','image_id','caption','region','limitations']},
                       'block_id':b.get('block_id'),'related_block_ids':b.get('related_block_ids',[]),
                       'status':'candidate','reason':'Existing observation preserved; placement is not a new support claim',
                       'input_ids':[source['item_id']]}
                else:
                    b={**b,'citations':[dict(c) for c in b.get('citations',[])]}
                    for c in b['citations']:
                        before=c.get('quote');after=align_source_quote(before,sources.get(c.get('source_id'),''))
                        if before!=after:
                            quote_repairs.append({'block_id':b.get('block_id'),'source_id':c.get('source_id'),'before':before,'after':after,'reason':'Unique original span; only whitespace and Markdown table borders differ'})
                            c['quote']=after
                    ins=b.get('input_ids',[])
                    if not ins or any(i not in items or items[i]['type']!='text' for i in ins):issues.append('invalid_text_ancestry')
                blocks.append(b);output_ids.add(b.get('block_id'));output_inputs[b.get('block_id')]=b.get('input_ids',[])
            topics.append({'title':t.get('title',''),'blocks':blocks})
        for d in decisions:
            if not isinstance(d,dict):issues.append('invalid_decision');continue
            links=d.get('output_block_ids',[]);iid=d.get('input_id')
            if d.get('action')=='used':
                if not links or any(x not in output_ids for x in links):issues.append('invalid_used_mapping')
                elif iid in items:
                    for outid in links:
                        ancestors=output_inputs[outid]
                        if iid not in ancestors and not (items[iid]['type']=='image' and any(items.get(a,{}).get('image_id')==items[iid]['image_id'] for a in ancestors)):
                            issues.append('inconsistent_ancestry_mapping')
            elif d.get('action')!='excluded' or links or not d.get('reason'):issues.append('invalid_exclusion')
        used={d.get('input_id') for d in decisions if isinstance(d,dict) and d.get('action')=='used'}
        if any(i not in used for ids in output_inputs.values() for i in ids):issues.append('output_uses_excluded_input')
        mapped={x for d in decisions if isinstance(d,dict) and d.get('action')=='used' for x in d.get('output_block_ids',[])}
        if output_ids-mapped:issues.append('unaccounted_output')
        adapted={**row,'prompt_result':{**result,'topics':topics}}
        out=ApplyParagraphs()(adapted)
        out['merge_decisions']=decisions;out['accounting_repairs']=repairs;out['quote_alignment_repairs']=quote_repairs;out['merge_validation_issues']=sorted(set(issues))
        if issues:
            for t in out['topics']:
                for b in t['blocks']:b['status']='deferred';b['validation_issues']+=sorted(set(issues))
        out['verify_payload']=verification_payload(row['joint_prompt'],out['topics'])
        out['verify_payload']['items']=[{k:b[k] for k in ['item_id','title','type','text','citations','image_refs'] if k in b} for b in row['merge_payload']['items'] if b['type']=='text']
        out['verify_payload']['expected_block_ids']=[b['block_id'] for t in out['topics'] for b in t['blocks']]
        out['raw_merge']=result
        return out
