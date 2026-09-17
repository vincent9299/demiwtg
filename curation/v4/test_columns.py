import asyncio
import json
import pytest
from .test_flow import data,write_rows
from .column_flow import ColumnFlow,ReadDocumentColumns,CleanDocumentColumns

async def prepare(f):
    for s in ['read_sources','select_concepts','join_documents','join_images']:await f.step(s)


def test_columns_chains_and_late_gather(data,monkeypatch):
    p,d=data
    (d/'meta/concepts.json').write_text(json.dumps({'concepts':[{'name':'同名概念'},{'name':'另一个'},{'name':'缺资料'}]}))
    write_rows(d/'meta/docs.jsonl',[{'concepts':['同名概念','另一个'],'path':'pages/a.md'}])
    f=ColumnFlow(p/'state/curation/columns',d,project=p)
    async def work():
        await prepare(f)
        before=f.view('documents')
        assert f.view('concepts')[0]['source_details']
        assert any(r['concept_refs'] for r in before)
        assert all('documents' not in r and 'images' not in r for r in f.view('concepts'))
        await f.step('process_documents');await f.step('process_images');await f.step('summarize_concepts')
        docs=f.view('documents')
        selected=[r for r in docs if r['read_status']]
        assert len(selected)==2 # one shared document and one wiki page
        assert all(r['raw_text'] and r['clean_text'] for r in selected)
        assert [r['doc_id'] for r in before]==[r['doc_id'] for r in docs]
        assert next(r for r in f.view('concepts') if r['concept_ref']=='legacy:缺资料')['document_count']==0
        assert f.tables.db.execute("SELECT type FROM sqlite_master WHERE name='clean_documents'").fetchone()[0]=='view'
        def fail(*a,**k):raise AssertionError('successful work repeated')
        monkeypatch.setattr(ReadDocumentColumns,'__call__',fail)
        assert (await f.step('process_documents'))['reused']
        inputs=list(f.iter_knowledge_inputs())
        assert any(r.get('blocked') for r in inputs)
    try:asyncio.run(work())
    finally:f.close()


def test_clean_failure_keeps_read_columns_and_resumes(data,monkeypatch):
    p,d=data;f=ColumnFlow(p/'state/curation/partial',d,project=p,ids=['legacy:同名概念'])
    original=CleanDocumentColumns.__call__
    async def fail(*a):raise RuntimeError('cleaning interrupted')
    async def work():
        await prepare(f);monkeypatch.setattr(CleanDocumentColumns,'__call__',fail)
        with pytest.raises(Exception):await f.step('process_documents')
        selected=[r for r in f.view('documents') if r['read_status']]
        assert len(selected)==1 and selected[0]['clean_version'] is None
        # The read operator itself checks existing columns and avoids IO on recovery.
        import curation.v4.dataset_flow as module
        def no_read(*a):raise AssertionError('read again')
        monkeypatch.setattr(module,'safe_local',no_read)
        monkeypatch.setattr(CleanDocumentColumns,'__call__',original)
        await f.step('process_documents')
        assert next(r for r in f.view('documents') if r['read_status'])['clean_version']
    try:asyncio.run(work())
    finally:f.close()


def test_existing_model_boundary_reads_expanded_columns(data,monkeypatch):
    p,d=data;f=ColumnFlow(p/'state/curation/models',d,project=p,ids=['legacy:同名概念'])
    from .test_knowledge import FakeModel
    monkeypatch.setattr('curation.v4.local_model.LocalModel',FakeModel)
    async def work():
        await prepare(f)
        for s in ['read_documents','clean_documents','check_images','summarize_concepts']:await f.step(s)
        for s in ['identity','organize','extract','consolidate','evidence','export']:await f.knowledge_step(s,{})
        assert f.view('knowledge')[0]['statement']
        assert f.view('knowledge_sources')[0]['doc_id'] in {d['doc_id'] for d in f.view('documents')}
    try:asyncio.run(work())
    finally:f.close()


def test_summary_requires_both_independent_branches(data):
    p,d=data;f=ColumnFlow(p/'state/curation/branches',d,project=p,ids=['legacy:同名概念'])
    async def work():
        await prepare(f)
        await f.step('process_images')
        with pytest.raises(ValueError,match='branches'):await f.step('summarize_concepts')
        await f.step('process_documents')
        await f.step('summarize_concepts')
        assert next(r for r in f.view('concepts') if r['selected'])['readable_documents']==1
    try:asyncio.run(work())
    finally:f.close()


def test_source_cache_does_not_import_processing_columns(data):
    p,d=data
    from .dataset_flow import DatasetFlow,STEPS
    old=DatasetFlow(p/'state/curation/source_tables',d,project=p)
    async def source():
        for s in STEPS:await old.step(s)
    asyncio.run(source())
    new=ColumnFlow(p/'state/curation/new_columns',d,project=p,raw_run=old.run)
    try:
        new.reuse_source_adaptation(old.run)
        assert all(r['raw_text'] is None and r['clean_text'] is None for r in new.view('documents'))
        async def run():
            await prepare(new)
            await new.step('process_documents');await new.step('process_images');await new.step('summarize_concepts')
        asyncio.run(run())
        assert any(r['clean_text'] for r in new.view('documents'))
        assert new.view('concepts')[0]['source_details']
    finally:old.close();new.close()
