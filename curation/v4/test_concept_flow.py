import asyncio
import json
import pytest
from .test_flow import data,write_rows
from .concept_flow import ConceptFlow,SelectConcepts,STEPS


def run(flow):
    async def work():
        return [await flow.step(stage) for stage in STEPS]
    return asyncio.run(work())


def test_concept_selection_shared_materials_and_empty_concepts(data):
    p,d=data
    (d/'meta/concepts.json').write_text(json.dumps({'concepts':[
        {'name':'同名概念'},{'name':'其他概念'},{'name':'缺资料'}]}))
    write_rows(d/'meta/docs.jsonl',[
        {'concepts':['同名概念','其他概念'],'path':'pages/a.md'},
        {'concepts':['未识别'],'path':'pages/a.md'},
        {'concepts':[],'path':'pages/a.md'}])
    full=ConceptFlow(p/'state/curation/full',d,project=p)
    small=ConceptFlow(p/'state/curation/small',d,project=p,ids=['legacy:同名概念','legacy:缺资料','qid:Q999'])
    run(full);run(small)
    assert len(small.view('clean_documents'))==1
    only=small.view('clean_documents')[0]
    match=next(r for r in full.view('clean_documents') if r['record_id']==only['record_id'])
    assert only['cleaning']==match['cleaning']
    assert only['concept_refs']==['legacy:其他概念','legacy:同名概念']
    assert only['selected_concept_refs']==['legacy:同名概念']
    summary={r['concept_ref']:r for r in small.view('summarize_concepts')}
    assert set(summary)=={'legacy:同名概念','legacy:缺资料'}
    assert summary['legacy:缺资料']['material_count']==0
    assert small.view('missing_concepts')[0]['concept_ref']=='qid:Q999'
    assert len(small.view('unresolved_materials'))>=2
    groups=list(small.iter_groups())
    assert len(groups)==2
    assert any(g.get('blocked',{}).get('reason')=='no_materials_in_read_scope' for g in groups)
    assert all(g['concept_ref']!='legacy:其他概念' for g in groups)
    assert len([g for g in full.iter_groups() if g['concept_ref'].startswith('legacy:') and not g.get('blocked')])==2


def test_sampling_uses_concept_not_document_versions(data):
    async def check():
        op=SelectConcepts(sample_rate=.5,seed=42)
        rows=[{'record_id':f'legacy:C{i}','concept_ref':f'legacy:C{i}'} for i in range(100)]
        first=[await op(r) for r in rows]
        assert 0<sum(r['selection']['selected'] for r in first)<100
        assert first==list(reversed([await op(r) for r in reversed(rows)]))
        for row in rows:
            assert (await SelectConcepts(sample_rate=1)(row))['selection']['selected']
            assert not (await SelectConcepts(sample_rate=0)(row))['selection']['selected']
    asyncio.run(check())


def test_resume_and_concept_result_collection(data,monkeypatch):
    p,d=data
    (d/'meta/concepts.json').write_text(json.dumps({'concepts':[{'name':'同名概念'},{'name':'缺资料'}]}))
    flow=ConceptFlow(p/'state/curation/results',d,project=p,ids=['legacy:同名概念','legacy:缺资料'])
    run(flow)
    assert all(r['reused'] for r in run(flow))
    from .test_knowledge import FakeModel
    monkeypatch.setattr('curation.v4.local_model.LocalModel',FakeModel)
    async def work():
        for stage in ['identity','organize','extract','consolidate','evidence','export']:
            await flow.knowledge_step(stage,{})
        await flow.collect_knowledge()
        await flow.collect_knowledge()
    asyncio.run(work())
    results={r['concept_ref']:r for r in flow.view('concept_knowledge')}
    assert results['legacy:同名概念']['knowledge_status']=='needs_joint_review'
    assert results['legacy:缺资料']['knowledge_status']=='blocked_or_incomplete'
    assert results['legacy:缺资料']['blocked_tasks']
    with pytest.raises(ValueError):ConceptFlow(flow.run,d,project=p,ids=['legacy:同名概念'])


def test_ambiguous_mapping_does_not_become_evidence(data):
    p,d=data
    write_rows(d/'meta/qid_concepts.fat.jsonl.gz',[
        {'qid':'Q1','en':{'page_id':10}}, {'qid':'Q2','en':{'page_id':10}}])
    flow=ConceptFlow(p/'state/curation/ambiguous',d,project=p)
    run(flow)
    assert any(r['reason']=='ambiguous_mapping' for r in flow.view('unresolved_materials'))
    q2=next(r for r in flow.view('summarize_concepts') if r['concept_ref']=='qid:Q2')
    assert q2['material_count']==0


def test_reuse_only_raw_parse_snapshot(data):
    p,d=data
    old=ConceptFlow(p/'state/curation/old',d,project=p)
    run(old)
    new=ConceptFlow(p/'state/curation/new',d,project=p,ids=['legacy:同名概念'])
    result=new.reuse_raw_records(old.run)
    assert result['rows']>0
    assert new.view('clean_documents')==[]
    assert new.view('select_concepts')==[]
    run(new)
    assert len(new.view('clean_documents'))==1
    changed=ConceptFlow(p/'state/curation/changed',d,project=p,max_records_per_source=1)
    with pytest.raises(ValueError,match='scope differ'):changed.reuse_raw_records(old.run)
