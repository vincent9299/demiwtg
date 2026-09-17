import asyncio
import json
import pytest
from .test_flow import data,write_rows
from .dataset_flow import DatasetFlow,STEPS,ReadDocument
from .datasets import SCHEMAS


def run(f):
    async def work():return [await f.step(s) for s in STEPS]
    return asyncio.run(work())


def test_typed_datasets_joins_shared_docs_and_empty_concepts(data):
    p,d=data
    (d/'meta/concepts.json').write_text(json.dumps({'concepts':[{'name':'同名概念'},{'name':'其他'},{'name':'缺资料'}]}))
    write_rows(d/'meta/docs.jsonl',[{'concepts':['同名概念','其他'],'path':'pages/a.md'}, {'concepts':['未知'],'path':'pages/a.md'}])
    f=DatasetFlow(p/'state/curation/typed',d,project=p)
    try:
        run(f)
        assert all(not {'record','kind','record_id'} & set(f.describe(t)[i]['field'] for i in range(len(f.describe(t)))) for t in SCHEMAS)
        assert len(f.view('document_texts'))==2 # shared legacy doc + Q1 wiki
        assert len(f.view('selected_document_links'))==3
        assert any(r['reason']=='unknown_concept' for r in f.view('unmatched_documents'))
        assert next(r for r in f.view('concept_coverage') if r['concept_ref']=='legacy:缺资料')['document_count']==0
        assert all(x['reused'] for x in run(f))
        from demiflow.data.dataset import Dataset
        assert isinstance(f.dataset_for('documents'),Dataset)
    finally:f.close()


def test_selection_does_not_select_other_shared_concepts(data):
    p,d=data
    (d/'meta/concepts.json').write_text(json.dumps({'concepts':[{'name':'同名概念'},{'name':'其他'}]}))
    write_rows(d/'meta/docs.jsonl',[{'concepts':['同名概念','其他'],'path':'pages/a.md'}])
    f=DatasetFlow(p/'state/curation/limited',d,project=p,ids=['legacy:同名概念','qid:Q999'])
    try:
        run(f)
        assert len(f.view('document_texts'))==1
        assert len(f.view('selected_document_links'))==1
        assert len(f.view('concept_coverage'))==1
        assert f.view('missing_concepts')[0]['concept_ref']=='qid:Q999'
    finally:f.close()


def test_schema_rejects_envelopes_and_knowledge_bridge(data,monkeypatch):
    p,d=data
    from .test_knowledge import FakeModel
    monkeypatch.setattr('curation.v4.local_model.LocalModel',FakeModel)
    f=DatasetFlow(p/'state/curation/models',d,project=p,ids=['legacy:同名概念'])
    try:
        with pytest.raises(ValueError):f.tables.put('documents',{'record':{}})
        run(f)
        async def knowledge():
            for s in ['identity','organize','extract','consolidate','evidence','export']:await f.knowledge_step(s,{})
            assert (await f.knowledge_step('export',{}))['reused']
        asyncio.run(knowledge())
        assert f.view('knowledge')[0]['statement']
        evidence=f.view('knowledge_sources')[0]
        assert evidence['doc_id']==f.view('selected_document_links')[0]['doc_id']
        assert f.view('concept_knowledge')[0]['concept_ref']=='legacy:同名概念'
    finally:f.close()


def test_raw_cache_and_fresh_files_produce_same_typed_tables(data):
    p,d=data
    fresh=DatasetFlow(p/'state/curation/fresh',d,project=p,ids=['legacy:同名概念'])
    cached=None
    try:
        run(fresh)
        cached=DatasetFlow(p/'state/curation/cached',d,project=p,ids=['legacy:同名概念'],raw_run=fresh.run)
        run(cached)
        for table in ['concept_sources','documents','images','selected_document_links','clean_documents','concept_coverage']:
            assert sorted(fresh.view(table),key=str)==sorted(cached.view(table),key=str)
    finally:
        fresh.close()
        if cached:cached.close()


def test_partial_clean_resume_and_ambiguous_join(data,monkeypatch):
    p,d=data
    from .dataset_flow import CleanDocument
    write_rows(d/'meta/qid_concepts.fat.jsonl.gz',[{'qid':'Q1','en':{'page_id':10}}, {'qid':'Q2','en':{'page_id':10}}])
    write_rows(d/'meta/docs.jsonl',[{'concepts':['同名概念'],'path':'pages/a.md'}, {'concepts':['同名概念'],'path':'pages/b.md'}])
    (d/'pages/b.md').write_text('第二篇原文')
    f=DatasetFlow(p/'state/curation/partial',d,project=p)
    original=CleanDocument.__call__;calls=[]
    async def fail(self,row):
        calls.append(row['doc_id'])
        if len(calls)==2:raise RuntimeError('interrupted')
        return await original(self,row)
    async def work():
        for s in STEPS[:5]:await f.step(s)
        monkeypatch.setattr(CleanDocument,'__call__',fail)
        with pytest.raises(Exception):await f.step('clean_documents')
        saved={r['doc_id'] for r in f.view('clean_documents')}
        assert len(saved)==1
        async def resume(self,row):
            assert row['doc_id'] not in saved
            return await original(self,row)
        monkeypatch.setattr(CleanDocument,'__call__',resume)
        await f.step('clean_documents')
        assert len(f.view('clean_documents'))==2
        assert any(r['reason']=='ambiguous_mapping' for r in f.view('unmatched_documents'))
        assert not any(r['concept_ref']=='qid:Q2' for r in f.view('selected_document_links'))
    try:asyncio.run(work())
    finally:f.close()
