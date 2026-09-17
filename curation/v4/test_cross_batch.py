from .ops.cross_batch import PlanCrossBatchReview


def item(i,text,batch,vector):
    return {'paragraph_id':i,'concept':'C','run':'r','batch_id':batch,'title':'Same title',
            'text':text,'citations':[],'embedding_title_body':vector,'embedding_body':vector}


def test_title_alone_does_not_trigger_and_unrelated_passes_through():
    rows=[item('A','Friction','b1',[1.,0.]),item('B','Refraction','b2',[0.,1.])]
    out=PlanCrossBatchReview()({'concept':'C','items':rows})
    assert not out['review_requests'] and len(out['passthrough_paragraph_ids'])==2


def test_explicit_split_triggers_review_without_claiming_duplicate():
    rows=[item('A','First condition','b1',[1.,0.]),item('B','Second condition','b2',[0.,1.])]
    for r in rows:r.update(topic_group_id='T',split_for_capacity=True)
    out=PlanCrossBatchReview()({'concept':'C','items':rows})
    assert out['review_requests'][0]['reasons']==['same_topic_capacity_split']
    assert not out['passthrough_paragraph_ids']


def test_same_batch_is_not_remerged_and_oversized_evidence_is_not_lost():
    a=item('A','Repeated','b1',[1.,0.]);b=item('B','Repeated','b1',[1.,0.])
    assert not PlanCrossBatchReview()({'concept':'C','items':[a,b]})['review_requests']
    b['batch_id']='b2'
    out=PlanCrossBatchReview(max_pair_chars=1)({'concept':'C','items':[a,b]})
    assert len(out['pending'])==1 and out['all_paragraph_ids']==['A','B']


def test_relationship_review_does_not_directly_rewrite_or_delete():
    from .ops.cross_batch import ApplyCrossBatchReview
    row={'paragraph_ids':['A','B'],'prompt_result':{'relationship':'unrelated','paragraph_ids':['A','B'],'reason':'Different topics'}}
    assert ApplyCrossBatchReview()(row)['next_action']=='passthrough'
    row['prompt_result']['relationship']='partial_overlap'
    assert ApplyCrossBatchReview()(row)['next_action']=='local_integration_candidate'
    row['prompt_result']['paragraph_ids']=['A','unknown']
    assert ApplyCrossBatchReview()(row)['next_action']=='pending'


def test_empty_native_review_flow_makes_no_requests(tmp_path,monkeypatch):
    from demiflow.standalone import local_data
    from .ops.cross_batch import review_cross_batch_requests
    from .ops.prompt_config import knowledge_prompt_pack
    from .pipeline import DEFAULT
    import httpx
    async def forbidden(*args,**kwargs):raise AssertionError('Empty plan must not call model')
    monkeypatch.setattr(httpx.AsyncClient,'request',forbidden)
    pack,_=knowledge_prompt_pack(DEFAULT)
    d=local_data(prompt_packs={'knowledge.yaml':pack})
    result=review_cross_batch_requests(d.from_iter(lambda:iter([])),run=tmp_path,version='test')
    assert result.take_all()==[]


def test_same_batch_review_is_enabled_in_quality_trial():
    a=item('A','Repeated','b1',[1.,0.]);b=item('B','Repeated','b1',[1.,0.])
    result=PlanCrossBatchReview(include_same_batch=True)({'concept':'C','items':[a,b]})
    assert len(result['review_requests'])==1
