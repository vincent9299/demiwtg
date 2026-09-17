"""Topic-level checks and bounded repair. Model chooses prose, title and images."""
import copy
from ..contracts import digest
from .paragraphs import verification_payload


class PrepareTopicVerification:
    def __call__(self,row):
        return {**row,'topic_gate_required':True,'verify_payload':verification_payload(row['joint_prompt'],row['topics'])}


class PrepareTopicRepairs:
    """Request only invalid combinations; preserve excluded originals in audit."""
    def __call__(self,row):
        out=[]
        for index,t in enumerate(row['topics']):
            kept=[b for b in t['blocks'] if b.get('status')=='candidate']
            if not kept:continue
            textids={b['block_id'] for b in kept if b['type']=='text'}
            removed_text=any(b['type']=='text' and b.get('status')!='candidate' for b in t['blocks'])
            orphan=any(b['type']=='image' and b.get('related_block_ids') and not textids.intersection(b['related_block_ids']) for b in kept)
            review=t.get('topic_review',{})
            if textids and not removed_text and not orphan and review.get('status')=='supported':continue
            image_ids={b['image_id'] for b in kept if b['type']=='image'} | {i['image_id'] for b in kept for i in b.get('image_refs',[])}
            pixels=dict(zip(row['joint_prompt']['image_ids'],row['pixel_images']))
            if not image_ids<=pixels.keys():raise ValueError('Repair image without original pixels')
            ids=sorted(image_ids);roles={r['image_id']:r for r in row['pixel_roles']}
            exclusions=[{'block_id':b['block_id'],'text':b.get('text',''),'reason':b.get('review',{}).get('reason') or b.get('reason')} for b in t['blocks'] if b.get('status')!='candidate']
            joint={**{k:row['joint_prompt'][k] for k in ['concept','concept_identity','passages'] if k in row['joint_prompt']},'image_ids':ids}
            payload={**joint,'original_title':t['title'],'surviving_blocks':[{k:b[k] for k in ['block_id','type','text','citations','image_refs','image_id','caption','region','limitations','related_block_ids'] if k in b} for b in kept],
                'upstream_exclusions':exclusions,'repair_reason':review.get('reason','Content removed or missing prose')}
            out.append({'case_id':row.get('case_id',row['joint_prompt']['concept']),'concept':row['joint_prompt']['concept'],
                'batch_id':'repair:'+digest([row['batch_id'],index,payload])[:16],'parent_batch_id':row['batch_id'],'parent_topic_index':index,
                'joint_prompt':joint,'repair_payload':payload,'pixel_images':[pixels[i] for i in ids],'pixel_roles':[roles[i] for i in ids],
                'topic_gate_required':True,'run':row['run']})
        return out


class ApplyTopicRepairs:
    """Every requested repair replaces only its own topic; failures stay auditable."""
    def __call__(self,row):
        results=row.get('repairs',[]);requests=row.get('repair_requests',[])
        requested={r['parent_topic_index'] for r in requests};done={r['parent_topic_index'] for r in results}
        if requested!=done:raise ValueError('Missing/extra repair result; do not silently discard topic')
        if len(done)!=len(results):raise ValueError('Duplicate repair result')
        original={k:v for k,v in row.items() if k not in {'repairs','repair_requests'}}
        original['topics']=[copy.deepcopy(t) for i,t in enumerate(row['topics']) if i not in requested]
        rows=([original] if original['topics'] else [])+results
        return {'concept':row['joint_prompt']['concept'],'rows':rows,'repair_count':len(requested),
            'removed_topic_indices':sorted(requested),'original_batch_id':row['batch_id']}


class RetainReviewedTopics:
    """Mechanical publication gate; never regenerate prose or choose images by score."""
    def __call__(self,row):
        r=copy.deepcopy(row);r['topic_gate_required']=True
        for t in r['topics']:
            kept=[b for b in t['blocks'] if b.get('status')=='candidate']
            if not any(b['type']=='text' for b in kept) or t.get('topic_review',{}).get('status')!='supported':
                for b in t['blocks']:
                    if b.get('status')=='candidate':b['status']='deferred';b['topic_gate_reason']='Topic requires valid prose and supported title/content/image combination'
        return r
