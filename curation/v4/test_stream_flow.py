"""The active Dataset pipeline must work even when SQLite is unavailable."""
import sqlite3
import pytest
from .test_flow import data, write_rows
from .stream_flow import StreamFlow


def test_no_database_and_late_gather(data, monkeypatch):
    p,d=data
    def forbidden(*a,**kw):raise AssertionError('SQLite must not be used')
    monkeypatch.setattr(sqlite3,'connect',forbidden)
    f=StreamFlow(p/'state/curation/stream',dataset=d,project=p,group_size=2)
    f.prepare_sources();f.select();f.process_documents()
    with pytest.raises(ValueError,match='images_processed'):f.summarize()
    f.process_images();f.summarize();f.gather()
    docs=list(f.saved('documents_processed').iter_rows())
    assert len(docs)==2 and all('clean_text' in x and 'materials' not in x for x in docs)
    assert len(list(f.saved('documents_unmatched').iter_rows()))==1
    concepts={c['concept_ref']:c for c in f.saved('concepts_ready').iter_rows()}
    assert concepts['qid:Q1']['document_count']==1
    assert concepts['qid:Q1']['image_count']==2
    assert concepts['qid:Q1']['verified_images']==1
    groups=list(f.saved('knowledge_inputs').iter_rows())
    assert all(len(r['materials'])<=2 for r in groups)
    assert sum(len(r['materials']) for r in groups)==4
    import curation.v4.stream_flow as module
    monkeypatch.setattr(module,'clean_document',forbidden)
    f.process_documents()  # checkpoint reuse, no repeat cleaning


def test_shared_material_is_processed_once(data):
    p,d=data
    write_rows(d/'meta/docs.jsonl',[{'concepts':['同名概念','other'],'path':'pages/a.md'}])
    (d/'meta/concepts.json').write_text('{"concepts":[{"name":"同名概念"},{"name":"other"}]}')
    f=StreamFlow(p/'state/curation/stream',dataset=d,project=p,ids=['legacy:同名概念','legacy:other','missing'])
    f.prepare_sources();f.select();f.process_documents();f.process_images();f.summarize();f.gather()
    assert len(list(f.saved('documents_processed').iter_rows()))==1
    assert len(list(f.saved('documents_links').iter_rows()))==2
    assert list(f.saved('missing_concepts').iter_rows())==[{'concept_ref':'missing'}]
    assert len(list(f.saved('concepts_ready').iter_rows()))==2


def test_knowledge_stages_without_database(data,monkeypatch):
    from .test_knowledge import FakeModel
    p,d=data
    def forbidden(*a,**kw):raise AssertionError('SQLite must not be used')
    monkeypatch.setattr(sqlite3,'connect',forbidden)
    f=StreamFlow(p/'state/curation/stream',dataset=d,project=p,ids=['legacy:同名概念'])
    f.prepare_sources();f.select();f.process_documents();f.process_images();f.summarize();f.gather()
    for stage in ['identity','organize','extract','consolidate','evidence','export']:
        f.knowledge(stage,model_factory=FakeModel)
    out=list(f.saved('knowledge_export').iter_rows())
    assert len(out)==1 and out[0]['export']['status']=='machine_candidates_ready_for_review'
    assert len(out[0]['export']['facts'])==1
    with pytest.raises(ValueError):f.knowledge('identity',{'max_docs':3},FakeModel)
