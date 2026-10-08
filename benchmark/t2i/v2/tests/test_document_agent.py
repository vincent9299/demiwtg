from benchmark.t2i.v2.tests.agent_fixture import agent_arguments
"""T2I authoring delegates document interaction to the native platform node."""
import json

import httpx
import lance
import pyarrow as pa
import pytest
from demiflow.operator_llm.call_ref import read_call
from demiflow.operator_llm.sqlite_offline import submit_response
from preparation.concepts.operators.schema import ADOPTED
from project import resolve_root
from benchmark.t2i.v2.operators import authoring
from benchmark.t2i.v2.t2i_v2_benchmark_pipeline import config, run_pipeline, DATASETS
from test_concept_context import adopted, input_row, rows


def request_more(adopted):
    return {'api_calls': [{'method': 'read_documents', 'arguments': {'request': {
        'documents': [{'document_ref': adopted['selected'][0]['document_ref'],
                       'url': 'https://fixture.invalid/tool', 'bindings': [], 'eligible': True}], 'questions': [], 'retained': [],
        'requests': [{'request_id': 'extra', 'document_ref': adopted['selected'][0]['document_ref'],
                      'block_ids': ['b000002'], 'bindings': ['scope']}],
        'new_chars': 6000, 'total_chars': 6000}}}], 'response': None}


def setup_config(adopted, **kwargs):
    root = resolve_root()
    uri = str(root / 'adopted.lance')
    lance.write_dataset(pa.Table.from_pylist([adopted], schema=ADOPTED), uri)
    return config(run=root / DATASETS / 'document_agent',
        concept_audit_source={'uri': uri, 'version': lance.dataset(uri).version},
        sample_size=1, **agent_arguments(root, **kwargs))


def test_prepare_binds_same_fixed_document_without_answer_materials(adopted):
    prepared = authoring.prepare_request(input_row(adopted), max_context_chars=60000,
                                        prompt_chars=100, document_reads=True)
    assert '可补读文档：D1' in prepared['concept_context']
    assert '未引用的正文' not in prepared['concept_context']
    assert prepared['document_resources']['D1']['document_ref'] == adopted['selected'][0]['document_ref']
    assert prepared['evidence_json'] == '[]' and 'references' not in prepared['prompt_payload']


def test_prepare_native_document_paths_without_callback_resources(adopted):
    from pathlib import Path
    prepared = authoring.prepare_request(input_row(adopted), max_context_chars=60000,
        prompt_chars=100, document_reads=True, document_paths=True)
    context = prepared['concept_context']
    paths = [json.loads(line.removeprefix('固定文档文件：'))
             for line in context.splitlines() if line.startswith('固定文档文件：')]
    assert len(paths) == 1 and Path(paths[0]).is_absolute()
    document = json.loads(Path(paths[0]).read_text())
    assert next(b for b in document['blocks'] if b['block_id'] == 'b000002')['text'] == '未引用的正文。'
    assert adopted['selected'][0]['document_ref']['sha256'] in context
    assert all(block['text'] not in context for block in document['blocks'])
    assert '原文块：b000000' in context and '原文块：b000001' in context
    assert 'https://fixture.invalid/tool' in context
    assert adopted['assessment']['definition'] in context
    for value in ('测试结构事实。', '仅适用于指定实例', '允许不同合法外观', '精确角度尚未核实'):
        assert value not in context
    assert prepared['document_resources'] == {} and 'references' not in prepared['prompt_payload']
    bounded = authoring.prepare_request(input_row(adopted), max_context_chars=len(context),
        prompt_chars=100, document_reads=True, document_paths=True)
    assert bounded['status'] == 'needs_context_budget'


def test_offline_multi_turn_resume_keeps_pending_and_full_provenance(adopted):
    cfg = setup_config(adopted)
    assert cfg['max_calls'] == 4
    state = run_pipeline(cfg)
    first = rows(state['designs'])[0]
    call = json.loads(first['call_json'])
    assert state['counts'] == {'pending': 1} and not state['complete']
    submit_response(resolve_root(), call['request_ref'], json.dumps(request_more(adopted)),
                    model=call['model'], metadata={'reviewer': 'fixture', 'reviewer_kind': 'human'})
    state = run_pipeline(cfg)
    second = rows(state['designs'])[0]
    call = json.loads(second['call_json'])
    assert state['counts'] == {'pending': 1}
    observed = json.loads(second['authoring_context_json'])
    assert observed[0]['result']['materials'][0]['text'] == '未引用的正文。'
    assert observed[0]['result']['receipts'][0]['status'] == 'read'
    request = json.dumps(read_call(call['request_ref'], resolve_root()), ensure_ascii=False)
    assert '未引用的正文。' in request
    submit_response(resolve_root(), call['request_ref'], json.dumps({
        'api_calls': [], 'response': {'result': {'question': None, 'reason': '原文仍不足以形成可靠判据'}}}),
        model=call['model'], metadata={'reviewer': 'fixture', 'reviewer_kind': 'human'})
    completed = run_pipeline(cfg)
    assert completed['complete'] and completed['counts'] == {'insufficient': 1}
    final = rows(completed['designs'])[0]
    assert final['authoring_context_json'] == second['authoring_context_json']
    assert json.loads(final['call_json'])['reused']
    assert rows(completed['candidates']) == [] and final['evidence_json'] == '[]'


def test_http_candidate_carries_read_trace_and_replays(adopted, monkeypatch):
    cfg = setup_config(adopted, mode='modelhub')
    posted = []
    question = {'instruction': '画出指定对象的接合关系。', 'test_points': [{
        'point': '接合结构', 'basis': 'D1 的 b000002，https://fixture.invalid/tool；隔离测试原文。',
        'criterion': '两部件接合可见；分离的部件不满足。'}]}
    def handler(request):
        if request.method == 'GET':
            return httpx.Response(200, json={'data': [{'id': cfg['model']}]})
        posted.append(json.loads(request.content))
        answer = request_more(adopted) if len(posted) == 1 else {
            'api_calls': [], 'response': {'result': {'question': question}}}
        return httpx.Response(200, json={'choices': [{'finish_reason': 'stop', 'message': {
            'content': json.dumps(answer, ensure_ascii=False)}}]})
    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kwargs:
                        original(transport=httpx.MockTransport(handler), **kwargs))
    state = run_pipeline(cfg)
    assert state['complete'] and state['counts'] == {'candidate': 1}
    candidate = rows(state['candidates'])[0]
    assert candidate['evidence_json'] == '[]'
    assert json.loads(candidate['authoring_context_json'])[0]['result']['materials'][0]['text'] == '未引用的正文。'
    assert len(posted) == 2 and '未引用的正文。' in json.dumps(posted[-1], ensure_ascii=False)
    resumed = run_pipeline(cfg)
    assert len(posted) == 2
    assert rows(resumed['candidates'])[0]['task_id'] == candidate['task_id']


def test_codex_document_agent_configuration(tmp_path):
    cfg = config(run='/unused', concepts=['fixture'],
                 **agent_arguments(tmp_path, mode='codex', model='fixture'))
    assert cfg['max_calls'] == 1 and cfg['codex_web_search'] == 'live'


def test_codex_native_document_tools_through_business_pipeline(adopted, tmp_path):
    """Exercise the optional entry with a local protocol fixture, never Codex/model."""
    import sys
    fake = tmp_path / 'codex-fixture'
    marker = tmp_path / 'started'
    fake.write_text('#!' + sys.executable + '\n' + 'MARKER = ' + repr(str(marker)) + '\n' + r'''
import json, pathlib, sys, time
assert 'features.shell_tool=true' in sys.argv and 'web_search="live"' in sys.argv
with pathlib.Path(MARKER).open('a') as f: f.write('session\n')
def receive(): return json.loads(sys.stdin.readline())
def send(value): print(json.dumps(value), flush=True)
def reply(req, result): send({'id':req['id'], 'result':result})
def note(method, params): send({'method':method,'params':{'threadId':'t',**params}})
req = receive(); assert req['method'] == 'initialize'; reply(req, {})
assert receive()['method'] == 'initialized'
req = receive(); assert req['method'] == 'thread/start'
assert req['params']['dynamicTools'] == []
assert 'baseInstructions' not in req['params'] and 'developerInstructions' not in req['params']
reply(req, {'thread':{'id':'t'},'model':req['params']['model']})
req = receive(); assert req['method'] == 'turn/start'
assert 'anyOf' in req['params']['outputSchema']['properties']['result']
text = '\n'.join(p.get('text','') for p in req['params']['input'])
assert '当前执行环境：' not in text and 'read_documents' not in text
assert 'remaining_operator_calls' not in text and 'api_calls' not in text
assert '仅适用于指定实例的完整原文。' not in text
assert '另一段证据，允许不同合法外观。' not in text
assert '测试结构事实。' not in text and '精确角度尚未核实' not in text
assert '不限于某一种柄部外形' not in text
assert '仅作流程测试的对象类别。' in text
assert '不是该概念的完整资料或知识清单，不限定出题范围' in text
assert '也可联网检索其他可靠来源' in text
reply(req, {'turn':{'id':'u','status':'inProgress','items':[]}})
note('turn/started', {'turn':{'id':'u','status':'inProgress','items':[]}})
path = next(json.loads(line.removeprefix('固定文档文件：'))
            for line in text.splitlines() if line.startswith('固定文档文件：'))
document = json.loads(pathlib.Path(path).read_text())
block = next(b for b in document['blocks'] if b['block_id'] == 'b000002')
assert block['text'] == '未引用的正文。'
note('item/completed', {'item':{'id':'read','type':'commandExecution','aggregatedOutput':block['text']}})
note('item/started', {'item':{'id':'search','type':'webSearch','query':'fixture only'}})
note('item/completed', {'item':{'id':'search','type':'webSearch','query':'fixture only'}})
note('item/completed', {'item':{'id':'final','type':'agentMessage','phase':'final_answer',
     'text':json.dumps({'result':{'question':None,'reason':'fixture verified native reading'}})}})
note('turn/completed', {'turn':{'id':'u','status':'completed','items':[]}})
time.sleep(20)
''')
    fake.chmod(0o755)
    cfg = setup_config(adopted, mode='codex', model='fixture', codex_bin=str(fake), timeout_s=5)
    state = run_pipeline(cfg)
    assert state['complete'] and state['counts'] == {'insufficient': 1}, state
    row = rows(state['designs'])[0]
    call = json.loads(row['call_json'])
    assert call['runtime'] == 'codex' and call['budget_unit'] == 'codex_session'
    assert json.loads(row['authoring_context_json']) == []
    assert call['native_searches'] == 1
    response = json.dumps(read_call(call['response_ref'], resolve_root()), ensure_ascii=False)
    assert 'commandExecution' in response and '未引用的正文。' in response and 'webSearch' in response
    assert 'item/tool/call' not in response
    assert run_pipeline(cfg)['complete']
    assert marker.read_text() == 'session\n'
