import asyncio
import json
import sqlite3
import pytest
from .test_flow import data,write_rows
from .record_flow import RecordFlow,RecordStore,STEPS,SelectInputRecords


def run_to_end(flow):
    async def execute():
        return [await flow.step(s) for s in STEPS]
    return asyncio.run(execute())


def test_unfiltered_and_id_filtered_share_record_outputs(data):
    p,d=data
    write_rows(d/'meta/docs.jsonl',[
        {'concepts':['同名概念','另一个概念'],'path':'pages/a.md','url':'https://example.org/a'},
        {'concepts':['别的概念'],'path':'pages/a.md','url':'https://example.org/b'},
        {'concepts':[],'path':'pages/a.md','url':'https://example.org/c'}])
    full=RecordFlow(p/'state/curation/full',d,project=p,group_size=1)
    limited=RecordFlow(p/'state/curation/limited',d,project=p,ids=['legacy:同名概念'],group_size=1)
    run_to_end(full);run_to_end(limited)
    a={r['record_id']:r for r in full.view('clean_documents',100)}
    b=limited.view('clean_documents',100)
    assert len(b)==1
    key=b[0]['record_id']
    # Entry decision is identical for a selected row; all processing is the same.
    assert a[key]==b[0]
    assert b[0]['concept_refs']==['legacy:另一个概念','legacy:同名概念']
    assert len(list(limited.iter_groups()))==2 # fan-out happens after one cleaning
    groups=list(full.iter_groups())
    assert any(g['bundle']['request']=={'kind':'qid','value':'Q1'} for g in groups)
    assert any(r['status']=='unassociated' for r in full.view('group_materials',100))
    assert len(full.view('clean_documents',100))>len(b)
    all_material_ids={r['record_id'] for r in full.view('attach_concept_ids',100)}
    for stage in ['select_input','read_documents','clean_documents','group_materials']:
        assert {r['record_id'] for r in full.view(stage,100)}==all_material_ids
    assert all(r['entry_selection']['selected'] for r in full.view('select_input',100))


def test_resumption_and_sampling_are_deterministic(data,monkeypatch):
    p,d=data
    flow=RecordFlow(p/'state/curation/resume',d,project=p)
    first=run_to_end(flow)
    import curation.v4.record_flow as module
    def fail(*a,**kw):raise AssertionError('successful work repeated')
    monkeypatch.setattr(module,'clean_materials',fail)
    second=run_to_end(flow)
    assert all(s['reused'] for s in second)
    assert [s['rows'] for s in first]==[s['rows'] for s in second]
    async def select():
        a=SelectInputRecords(sample_rate=.5,seed=7)
        rows=[{'record_id':str(i),'concept_refs':[]} for i in range(40)]
        forward=[await a(r) for r in rows]
        reverse=[await a(r) for r in reversed(rows)]
        assert forward==list(reversed(reverse))
    asyncio.run(select())
    with pytest.raises(ValueError,match='new run'):
        RecordFlow(flow.run,d,project=p,sample_rate=.5)


def test_read_budget_ambiguity_and_invalid_lines_preserved(data):
    p,d=data
    write_rows(d/'meta/qid_concepts.fat.jsonl.gz',[
        {'qid':'Q1','en':{'page_id':10}}, {'qid':'Q2','en':{'page_id':10}}])
    with (d/'meta/docs.jsonl').open('a') as f:f.write('{broken json\n')
    flow=RecordFlow(p/'state/curation/conflict',d,project=p)
    run_to_end(flow)
    assert flow.view('parse_errors',100)
    assert any(r['status']=='ambiguous_page_mapping' for r in flow.view('group_materials',100))
    assert not any(g['bundle']['request']=={'kind':'qid','value':'Q2'} for g in flow.iter_groups())
    limited=RecordFlow(p/'state/curation/budget',d,project=p,max_records_per_source=1)
    run_to_end(limited)
    with sqlite3.connect(limited.run/'records.sqlite') as db:
        reports=[json.loads(r[0]) for r in db.execute('SELECT body FROM source_status')]
    assert any(r['status']=='budget_limited' for r in reports)


def test_record_flow_reuses_actual_downstream_operators(data,monkeypatch):
    p,d=data
    from .test_knowledge import FakeModel
    monkeypatch.setattr('curation.v4.local_model.LocalModel',FakeModel)
    flow=RecordFlow(p/'state/curation/models',d,project=p,ids=['legacy:同名概念'])
    run_to_end(flow)
    async def work():
        for s in ['identity','organize','extract','consolidate','evidence','export']:
            await flow.knowledge_step(s,{})
        assert (await flow.knowledge_step('export',{}))['reused']
    asyncio.run(work())
    outputs=flow.view('export',10)
    assert outputs[0]['export']['status']=='machine_candidates_ready_for_review'
    assert outputs[0]['export']['facts']


def test_record_stage_resumes_after_partial_failure(data,monkeypatch):
    p,d=data
    flow=RecordFlow(p/'state/curation/partial',d,project=p)
    import curation.v4.record_flow as module
    original=module.CleanDocumentRecord.__call__
    seen=[]
    async def fail_after_first(self,row):
        seen.append(row['record_id'])
        if len(seen)==2:raise RuntimeError('test interruption')
        return await original(self,row)
    async def first():
        for stage in STEPS[:5]:await flow.step(stage)
        monkeypatch.setattr(module.CleanDocumentRecord,'__call__',fail_after_first)
        with pytest.raises(Exception):await flow.step('clean_documents')
    asyncio.run(first())
    store=RecordStore(flow.run)
    try:completed_ids={r['record_id'] for r in store.iter('clean_documents')}
    finally:store.close()
    assert completed_ids
    async def resume(self,row):
        assert row['record_id'] not in completed_ids
        return await original(self,row)
    monkeypatch.setattr(module.CleanDocumentRecord,'__call__',resume)
    asyncio.run(flow.step('clean_documents'))
    assert len(flow.view('clean_documents',100))==len(flow.view('read_documents',100))
