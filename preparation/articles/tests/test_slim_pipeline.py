import ast
import json
from pathlib import Path
from preparation.articles.operators.inputs import SelectSourceRecords




def test_pushdown_shared_material_sampling_and_audit():
    shared={'error':None,'value':{'instances':['other','target']}}
    op=SelectSourceRecords('legacy_images',['legacy:target'])
    assert op(shared)
    assert SelectSourceRecords('qid_concepts',['qid:Q1'])({'value':{'qid':'Q2','en':{'page_id':123}}})
    assert not op({'error':None,'value':{'concepts':['other']}})
    assert op({'error':'bad_json','value':None})
    assert SelectSourceRecords('legacy_images',enabled=False)({'error':None,'value':{'concepts':['other']}})
    from preparation.articles.operators.inputs import SelectConcept
    for name in ['target','other','third']:
        assert SelectSourceRecords('legacy_docs',sample_rate=.5)({'value':{'concepts':[name]}})==SelectConcept(sample_rate=.5)({'concept_ref':'legacy:'+name})['selected']








