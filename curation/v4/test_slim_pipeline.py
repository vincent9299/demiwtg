import json
from pathlib import Path
from .ops.select_source_records import SelectSourceRecords
from .ops.cross_batch import BatchRelationshipReviews, ApplyRelationshipReviews


def pair(i):
    return {'request_id':str(i),'concept':'c','paragraph_ids':[f'a{i}',f'b{i}'],
            'reasons':['overlapping_source_quote'],'items':[{'paragraph_id':f'a{i}','text':'hello'},{'paragraph_id':f'b{i}','text':'world'}]}


def test_pushdown_shared_material_sampling_and_audit():
    shared={'error':None,'value':{'instances':['other','target']}}
    op=SelectSourceRecords('legacy_images',['legacy:target'])
    assert op(shared)
    assert SelectSourceRecords('qid_concepts',['qid:Q1'])({'value':{'qid':'Q2','en':{'page_id':123}}})
    assert not op({'error':None,'value':{'concepts':['other']}})
    assert op({'error':'bad_json','value':None})
    assert SelectSourceRecords('legacy_images',enabled=False)({'error':None,'value':{'concepts':['other']}})
    from .ops.dataset_operators import SelectConcept
    for name in ['target','other','third']:
        assert SelectSourceRecords('legacy_docs',sample_rate=.5)({'value':{'concepts':[name]}})==SelectConcept(sample_rate=.5)({'concept_ref':'legacy:'+name})['selected']


def test_batch_capacity_covers_every_pair_without_truncation():
    pairs=[pair(i) for i in range(17)]
    batches=BatchRelationshipReviews(max_pairs=8)({'concept':'c','review_requests':pairs})
    assert [len(b['pairs']) for b in batches]==[8,8,1]
    assert [p for b in batches for p in b['pairs']]==pairs
    oversized=BatchRelationshipReviews(max_chars=1)({'concept':'c','review_requests':[pair(1)]})[0]
    assert oversized['batch_error']
    assert ApplyRelationshipReviews()(oversized)['pair_reviews'][0]['next_action']=='pending'
    assert BatchRelationshipReviews()({'concept':'c','review_requests':[]})==[]


def test_batch_missing_duplicate_unknown_and_independent_relations():
    b=BatchRelationshipReviews()({'concept':'c','review_requests':[pair(1),pair(2)]})[0]
    answer={'request_id':'1','paragraph_ids':['a1','b1'],'relationship':'unrelated','reason':'Independent uses'}
    rows=ApplyRelationshipReviews()({**b,'prompt_result':{'reviews':[answer]}})['pair_reviews']
    assert [r['next_action'] for r in rows]==['passthrough','pending']
    for invalid in [[answer,answer],[{**answer,'request_id':'unknown'}]]:
        assert all(r['next_action']=='pending' for r in ApplyRelationshipReviews()({**b,'prompt_result':{'reviews':invalid}})['pair_reviews'])


def test_native_batch_application_checkpoint(tmp_path):
    from demiflow.standalone import local_data
    data=local_data();b=BatchRelationshipReviews()({'concept':'c','review_requests':[pair(1)]})[0]
    b['prompt_result']={'reviews':[{'request_id':'1','paragraph_ids':['a1','b1'],'relationship':'conflict','reason':'Same conditions, incompatible values'}]}
    ds=data.from_items([b]).map_cached(ApplyRelationshipReviews(),cache_dir=tmp_path/'cache',version='1').checkpoint(tmp_path/'batches.jsonl',version='1')
    rows=ds.flat_map(lambda r:r['pair_reviews']).checkpoint(tmp_path/'pairs.jsonl',version='1').take_all()
    assert rows[0]['next_action']=='local_integration_candidate'


def test_notebooks_have_one_pass_and_no_residual_model_calls():
    for name in ['knowledge_debug','glass_operator_debug']:
        n=json.loads(Path(__file__).with_name(name+'.ipynb').read_text())
        s='\n'.join(''.join(c['source']) for c in n['cells'] if c['cell_type']=='code')
        assert "map_prompt_async('final_review'" in s
        assert "map_prompt_async('joint_paragraphs'" in s
        assert "map_prompt_async('review_relationships'" not in s
        assert 'SelectSourceRecords(' in s
        assert 'round_2_' not in s
        assert "stage='residual'" not in s
        assert "map_prompt_async('verify_merged_paragraphs'" not in s
        assert "map_prompt_async('merge_paragraphs'" not in s
