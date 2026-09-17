import json
import pytest
from .ops.topic_quality import PrepareTopicRepairs,ApplyTopicRepairs,RetainReviewedTopics


def row():
    return {'run':'r','case_id':'C','batch_id':'B','concept':'C','joint_prompt':{'concept':'C','passages':[],'image_ids':['I']},'pixel_images':['data:image/png;base64,aA=='],'pixel_roles':[{'image_id':'I'}],
       'topics':[{'title':'Growth habits','topic_review':{'status':'supported'},'blocks':[
         {'block_id':'T','type':'text','text':'Unproved growth claim','citations':[],'status':'deferred','review':{'reason':'scope error'}},
         {'block_id':'P','type':'image','image_id':'I','caption':'Visible object','region':'all','limitations':'identity unknown','related_block_ids':['T'],'status':'candidate'}]}]}


def test_removed_prose_triggers_repair_without_reintroducing_rejected_claim():
    r=row();req=PrepareTopicRepairs()(r)
    assert len(req)==1 and req[0]['pixel_images']==r['pixel_images']
    assert [b['block_id'] for b in req[0]['repair_payload']['surviving_blocks']]==['P']
    assert req[0]['repair_payload']['upstream_exclusions'][0]['text']=='Unproved growth claim'
    assert all(b['status']=='deferred' for b in RetainReviewedTopics()(r)['topics'][0]['blocks'])


def test_missing_repair_is_not_silent_deletion():
    r=row();r['repair_requests']=PrepareTopicRepairs()(r)
    with pytest.raises(ValueError,match='Missing/extra'):ApplyTopicRepairs()(r)


@pytest.mark.parametrize("rounds",[0,1])
def test_native_trial_repairs_image_only_and_publishes_three_parts(tmp_path,monkeypatch,rounds):
    from demiflow.data.dataset import Dataset
    from . import try_topic_quality as trial
    async def identity(r):return r
    def fake_prompt(self,stage,**kwargs):
        async def invoke(r):
            if stage=='repair_topics':
                result={'topics':[{'title':'Visible shape','blocks':[
                  {'block_id':'V','type':'text','text':'The visible outline is round.','citations':[],
                   'image_refs':[{'image_id':'I','region':'center'}],'status':'candidate','reason':'actual pixels'},
                  {'block_id':'P','type':'image','image_id':'I','caption':'Round outline','region':'center',
                   'limitations':'one view','related_block_ids':['V'],'status':'candidate','reason':'complementary view'}]}],'coverage_note':'Old title rejected'}
            elif stage=='verify_paragraphs':
                topics=r['verify_payload']['topics'];result={'reviews':[{'block_id':b['block_id'],'status':'deferred' if b['block_id']=='T' else 'supported','reason':'checked'} for t in topics for b in t['blocks']],
                  'topic_reviews':[{'topic_index':i,'status':'repair' if t['title']=='Growth habits' else 'supported','reason':'check surviving content'} for i,t in enumerate(topics)]}
            elif stage=='review_cross_batch':
                result={'relationship':'duplicate','reason':'Same observed outline','paragraph_ids':r['paragraph_ids']}
            elif stage=='merge_paragraphs':
                items=r['merge_payload']['items'];texts=[i for i in items if i['type']=='text'];pics=[i for i in items if i['type']=='image']
                result={'topics':[{'title':'Visible shape','blocks':[
                    {'block_id':'V','type':'text','text':'The visible outline is round.','citations':[],
                     'image_refs':[{'image_id':'I','region':'center'}],'input_ids':[i['item_id'] for i in texts],'status':'candidate','reason':'merged duplicate'},
                    {'block_id':'P','type':'image','input_id':pics[0]['item_id'],'related_block_ids':['V']}]}],
                    'decisions':[{'input_id':i['item_id'],'action':'used','output_block_ids':['V' if i['type']=='text' else 'P'],'reason':'same content'} for i in items],'coverage_note':'merged'}
            else:raise AssertionError(stage)
            return {**r,'prompt_result':result,'prompt_call':{'fake_test':True},'prompt_error':None}
        return self.map_async(invoke,concurrency=1,queue_depth=1)
    monkeypatch.setattr(Dataset,'map_prompt_async',fake_prompt)
    class Embed:
        def __init__(self,*a,**kw):pass
        def __call__(self,r):return {**r,'items':[{**x,'embedding_title_body':[1.,0.],'embedding_body':[1.,0.]} for x in r['items']]}
    from . import quality_pipeline
    monkeypatch.setattr(quality_pipeline,'EmbedParagraphBatch',Embed)
    source=tmp_path/'source';source.mkdir();(source/'datasets').mkdir();r=row()
    files={'paragraphs.jsonl':[r],'requests.jsonl':[r],
      'knowledge_base.jsonl':[{'concept':'C','identity':{'target_label':'C'},'knowledge':[],'documents':[],'images':[]}],
      'datasets/source_catalog.jsonl':[{'concept':'C','sources':{},'image_sources':{'I':{'title':'Image','url':'https://example.org/image'}}}]}
    if rounds:
        import copy
        other=copy.deepcopy(r);other['batch_id']='B2'
        files['paragraphs.jsonl'].append(other);files['requests.jsonl'].append(other)
    for name,values in files.items():(source/name).write_text(''.join(json.dumps(v)+'\n' for v in values))
    out=trial.run_trial(source,tmp_path/'new',rounds=rounds)
    public=json.loads((out/'published/knowledge.json').read_text())
    article=public['concepts'][0]['articles'][0]
    assert article['title']=='Visible shape' and article['content']['paragraphs']
    assert len(article['content']['images'])==1 and article['references'][0]['kinds']==['image']
    assert 'Growth habits' not in (out/'published/preview.html').read_text()
