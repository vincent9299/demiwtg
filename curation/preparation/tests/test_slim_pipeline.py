import json
from pathlib import Path
from curation.preparation.ops.select_source_records import SelectSourceRecords




def test_pushdown_shared_material_sampling_and_audit():
    shared={'error':None,'value':{'instances':['other','target']}}
    op=SelectSourceRecords('legacy_images',['legacy:target'])
    assert op(shared)
    assert SelectSourceRecords('qid_concepts',['qid:Q1'])({'value':{'qid':'Q2','en':{'page_id':123}}})
    assert not op({'error':None,'value':{'concepts':['other']}})
    assert op({'error':'bad_json','value':None})
    assert SelectSourceRecords('legacy_images',enabled=False)({'error':None,'value':{'concepts':['other']}})
    from curation.preparation.ops.dataset_operators import SelectConcept
    for name in ['target','other','third']:
        assert SelectSourceRecords('legacy_docs',sample_rate=.5)({'value':{'concepts':[name]}})==SelectConcept(sample_rate=.5)({'concept_ref':'legacy:'+name})['selected']








def test_notebooks_have_one_pass_and_no_residual_model_calls():
    for name in ['preparation/debug']:
        n=json.loads((Path(__file__).resolve().parents[2] / (name + '.ipynb') if (Path(__file__).resolve().parents[2] / (name + '.ipynb')).exists() else Path(__file__).resolve().parents[2] / 'legacy/notebooks' / (name + '.ipynb')).read_text())
        s='\n'.join(''.join(c['source']) for c in n['cells'] if c['cell_type']=='code')
        assert "map_prompt_async('final_review'" in s
        assert "map_prompt_async('joint_paragraphs'" in s
        assert "map_prompt_async('review_relationships'" not in s
        assert 'SelectSourceRecords(' in s
        assert 'round_2_' not in s
        assert "stage='residual'" not in s
        assert "map_prompt_async('verify_merged_paragraphs'" not in s
        assert "map_prompt_async('merge_paragraphs'" not in s
