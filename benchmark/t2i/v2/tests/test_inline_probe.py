"""Real Dataset stream, journals and Lance; all model HTTP is isolated MockTransport."""
import asyncio
import base64
import io
import json
from pathlib import Path

import httpx
import lance
import pytest
import yaml
from PIL import Image
from demiflow import data
from demiflow.objects import ObjectRef
from project import resolve_root
from benchmark.t2i.v2 import t2i_v2_benchmark_pipeline as pipeline
from benchmark.t2i.v2.operators.probe import check_review, probe_config
from benchmark.t2i.v2.tests.agent_fixture import agent_arguments


def question(concept):
    return {'instruction': '画出' + concept + '的结构。', 'test_points': [
        {'point': '核心结构', 'basis': '仅评审可见的知识依据', 'criterion': '仅评审可见的正确结构判据'}]}


def review():
    return {'verdict': 'pass', 'score': 10, 'reason': '本次核心结构清楚且正确',
        'basis': '给定依据与已知结构一致', 'failure_type': 'none',
        'point_results': [{'index': 1, 'verdict': 'pass', 'evidence': '中心部件连接清楚', 'reason': '符合核心关系'}],
        'case_annotation': {'knowledge_level': 'everyday', 'common_cn': 'yes', 'visual_support': 'strong',
            'confidence': 'high', 'reason': '可按基本常识核对部件', 'caveat': '', 'sources': []}}


def png():
    buffer = io.BytesIO()
    Image.new('RGB', (64, 64), 'red').save(buffer, format='PNG')
    return base64.b64encode(buffer.getvalue()).decode()


def read(ref):
    return data.read_lance(**ref).take_all()


def cfg(name='probe', **kwargs):
    arguments = agent_arguments(resolve_root(), mode='modelhub', model='fixture-author')
    path = Path(arguments['agent_config'])
    raw = yaml.safe_load(path.read_text())
    raw['operators'] = []
    raw.pop('resources', None)
    path.write_text(yaml.safe_dump(raw, allow_unicode=True))
    return pipeline.config(run=resolve_root()/pipeline.DATASETS/name, concepts=['先完成', '后完成', '空题'],
        **arguments, concurrency=3, through='probe',
        probe={'revision':'fixture-weights-v1', 'image_size':'64x64', **kwargs})


def transport(monkeypatch, config, *, fail_image=False, gate=True):
    calls=[]
    # Created lazily inside the running stream's loop; forces a real pipeline overlap.
    released=None
    original=httpx.AsyncClient
    async def handler(request):
        nonlocal released
        if request.method=='GET':
            return httpx.Response(200,json={'data':[{'id':'fixture-author'},{'id':'malasci/gpt-6-astra'}]})
        body=json.loads(request.content)
        if request.url.path.endswith('/images/generations'):
            calls.append('generate')
            assert body['model']=='Z-Image-Turbo'
            assert body['prompt'] in [question(c)['instruction'] for c in config['concepts']]
            assert '仅评审' not in json.dumps(body,ensure_ascii=False)
            committed=lance.dataset(str(Path(config['run']).with_name('candidates__'+Path(config['run']).name+'.lance')))
            assert committed.count_rows()>=1  # Candidate commits before generation starts.
            if fail_image:
                if released:released.set()
                return httpx.Response(500,text='fixture GPU failure')
            return httpx.Response(200,json={'data':[{'b64_json':png()}]})
        if body['model']=='malasci/gpt-6-astra':
            calls.append('review')
            assert body['reasoning_effort']=='xhigh'
            sent=json.dumps(body['messages'],ensure_ascii=False)
            assert '仅评审可见的正确结构判据' in sent and 'data:image/png;base64,' in sent
            if released:released.set()
            return httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'content':json.dumps({'result':review()},ensure_ascii=False)}}]})
        sent=json.dumps(body['messages'],ensure_ascii=False)
        concept=next(c for c in config['concepts'] if c in sent)
        calls.append('author:'+concept)
        if concept=='后完成' and gate:
            if released is None:released=asyncio.Event()
            await asyncio.wait_for(released.wait(),15)
        elif gate and released is None:
            released=asyncio.Event()
        result=({'question':None,'reason':'材料不足，不出题'} if concept=='空题' else {'question':question(concept)})
        answer={'result':result}
        if config.get('agent_config'):
            answer={'api_calls':[],'response':answer}
        return httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'content':json.dumps(answer,ensure_ascii=False)}}]})
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kwargs:original(transport=httpx.MockTransport(handler),**kwargs))
    return calls


def test_pipeline_probes_before_all_authoring_finishes_and_reuses(monkeypatch):
    config=cfg()
    calls=transport(monkeypatch,config)
    state=pipeline.run_pipeline(config)
    assert state['complete'] and state['counts']=={'candidate':2,'insufficient':1}
    assert calls.count('generate')==calls.count('review')==2
    outputs=state['probe']['outputs']
    assert len(read(outputs['designs']))==3 and len(read(outputs['reviews']))==2
    for row in read(outputs['reviews']):
        assert row['case_category']==2 and row['review_status']=='reviewed'
        assert ObjectRef(**row['object_ref']).read()
        assert any(r['task_id']==row['task_id'] for r in read(row['generation_source']))
    calls.clear()
    again=pipeline.run_pipeline(config)
    assert again['complete'] and calls==[]
    # Changing only review settings reuses generation; native request identity changes.
    changed={**config,'probe':{**config['probe'],'review_max_output_tokens':4096,'max_review_calls':4}}
    rereview=pipeline.run_pipeline(changed)
    assert rereview['complete'] and calls==['review','review']


def test_failed_generation_keeps_row_and_skips_judge(monkeypatch):
    config=cfg('failure')
    calls=transport(monkeypatch,config,fail_image=True,gate=False)
    state=pipeline.run_pipeline(config)
    assert not state['complete'] and 'review' not in calls
    rows=read(state['probe']['outputs']['reviews'])
    assert len(rows)==2 and {r['review_status'] for r in rows}=={'generation_failed'}
    assert all(r['review'] is None for r in rows)


def test_generation_budget_is_visible_without_dropping_candidate(monkeypatch):
    config=cfg('budget',max_generation_calls=1)
    calls=transport(monkeypatch,config,gate=False)
    state=pipeline.run_pipeline(config)
    assert not state['complete'] and calls.count('generate')==1 and calls.count('review')==1
    assert state['probe']['counts']['generations']=={'generated':1,'budget_exhausted':1}
    assert len(read(state['probe']['outputs']['reviews']))==2


def test_probe_requires_revision_and_run_owned_snapshot():
    with pytest.raises(ValueError,match='revision'):probe_config({},maximum=20)
    with pytest.raises(ValueError,match='append'):
        pipeline.config(run='/unused',concepts=['x'],through='probe',probe={'revision':'v1'},write_mode='append',
            agent_config=pipeline.PROMPTS/'agent_codex.yaml')


def test_invalid_criterion_is_not_model_failure():
    result=review()
    result.update(verdict='inconclusive',score=-1,failure_type='insufficient_evidence')
    result['point_results'][0]['verdict']='invalid_criterion'
    row={'review_status':'ready','review_reason':'','review_result':result,'test_points':question('x')['test_points']}
    checked=check_review(row)
    assert checked['review_status']=='reviewed' and checked['review']['score']==-1
    result['verdict']='fail';result['score']=0
    assert check_review(row)['review_status']=='invalid_response'


def test_review_budget_keeps_unjudged_image(monkeypatch):
    config=cfg('review_budget',max_review_calls=1)
    calls=transport(monkeypatch,config,gate=False)
    state=pipeline.run_pipeline(config)
    assert not state['complete'] and calls.count('generate')==2 and calls.count('review')==1
    rows=read(state['probe']['outputs']['reviews'])
    assert len(rows)==2 and {r['review_status'] for r in rows}=={'reviewed','failed'}
    assert all(r['object_ref'] for r in rows)


def test_image_response_byte_budget_stops_reading(monkeypatch, tmp_path):
    from benchmark.t2i.v2.operators.probe import AnswerImage, answer_input
    from demiflow.objects import LocalObjectStore
    consumed=[]
    class LargeResponse(httpx.AsyncByteStream):
        async def __aiter__(self):
            for i in range(10):
                consumed.append(i)
                yield b' '*(64*1024)
    original=httpx.AsyncClient
    def handler(request):return httpx.Response(200,stream=LargeResponse())
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kwargs:original(transport=httpx.MockTransport(handler),**kwargs))
    settings=probe_config({'revision':'v1','max_image_bytes':1},maximum=1)
    row={'task_id':'t1','concept':'x','taxonomy':[],'authoring_variant':'standard',**question('x')}
    result=asyncio.run(AnswerImage(settings,LocalObjectStore(tmp_path/'objects'))(answer_input(row)))
    assert result['status']=='failed' and '字节预算' in result['reason']
    assert len(consumed)==3 and result['object_ref'] is None
