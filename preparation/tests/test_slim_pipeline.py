import ast
import json
from pathlib import Path
from preparation.operaters.inputs import SelectSourceRecords




def test_pushdown_shared_material_sampling_and_audit():
    shared={'error':None,'value':{'instances':['other','target']}}
    op=SelectSourceRecords('legacy_images',['legacy:target'])
    assert op(shared)
    assert SelectSourceRecords('qid_concepts',['qid:Q1'])({'value':{'qid':'Q2','en':{'page_id':123}}})
    assert not op({'error':None,'value':{'concepts':['other']}})
    assert op({'error':'bad_json','value':None})
    assert SelectSourceRecords('legacy_images',enabled=False)({'error':None,'value':{'concepts':['other']}})
    from preparation.operaters.inputs import SelectConcept
    for name in ['target','other','third']:
        assert SelectSourceRecords('legacy_docs',sample_rate=.5)({'value':{'concepts':[name]}})==SelectConcept(sample_rate=.5)({'concept_ref':'legacy:'+name})['selected']








def test_notebooks_have_one_pass_and_no_residual_model_calls():
    for name in ['preparation/debug']:
        s = (Path(__file__).resolve().parents[1] / 'preparation_pipeline.py').read_text()
        calls = {n.args[0].value for n in ast.walk(ast.parse(s))
                 if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                 and n.func.attr == 'map_prompt_async' and n.args
                 and isinstance(n.args[0], ast.Constant)}
        assert {'final_review', 'joint_paragraphs'} <= calls
        assert "map_prompt_async('review_relationships'" not in s
        assert 'SelectSourceRecords(' in s
        assert 'round_2_' not in s
        assert "stage='residual'" not in s
        assert "map_prompt_async('verify_merged_paragraphs'" not in s
        assert "map_prompt_async('merge_paragraphs'" not in s
