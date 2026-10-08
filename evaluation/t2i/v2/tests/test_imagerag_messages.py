"""原版 messages 实际 HTTP 比对及完整检索/再生成图的隔离验证。"""
import base64
import copy
import json
import threading
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
import lance
import pyarrow as pa
import yaml
from demiflow import data
from demiflow.execution.file_ref import JsonArtifactRef
from demiflow.objects import ObjectRef
from demiflow.operator_llm.parser import parse_prompt_pack
from evaluation.t2i.v2.operators import imagerag, imagerag_original as original
from evaluation.t2i.v2.operators.answers import answer_template, answer_identity
from evaluation.t2i.v2.operators.run_tables import RunTables
from evaluation.t2i.v2.t2i_v2_eval_pipeline import config, DATASETS, run_paired_arm
from evaluation.t2i.v2.tests.test_imagerag import fixture_setup, stage
from evaluation.t2i.v2.tests.test_imagerag_original import official_calls, BASE, SPEC
from project import resolve_root


@pytest.fixture
def server(monkeypatch):
    monkeypatch.setenv("MODELHUB_API_KEY", "fixture")
    state = {'prompts': [], 'embeddings': [], 'responses': [], 'counts': {}}
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            if self.path.endswith('/embeddings'):
                state['embeddings'].append(body)
                texts = body.get('input') or [m[0]['content'][0]['text'] for m in body['messages']]
                result = {'data': [{'index': i, 'embedding': [0., 1., 0.] if 'blue' in t else [1., 0., 0.]}
                                    for i, t in enumerate(texts)], 'model': 'fixture-encoder'}
            else:
                state['prompts'].append(body)
                if state['responses']:
                    response = state['responses'].pop(0)
                else:
                    messages = body['messages']
                    instruction = messages[0]['content'][0]['text']
                    if len(messages) == 1:
                        response = 'YES.' if 'KEEP_INITIAL' in instruction else 'no'
                    elif len(messages) == 3:
                        state['counts'][instruction] = state['counts'].get(instruction, 0) + 1
                        response = ('unable' if 'FALLBACK' in instruction else
                                    "can't" if 'RETRY' in instruction and state['counts'][instruction] < 3 else
                                    'blue dog\nred ball\ngreen tree\nyellow cat')
                    else:
                        response = '1. a blue dog\n- a red ball\n3. a green tree\n4. a yellow cat'
                result = {'choices': [{'message': {'role': 'assistant', 'content': response}, 'finish_reason': 'stop'}],
                          'usage': {'prompt_tokens': 10, 'completion_tokens': 10}}
            payload = json.dumps(result).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        def log_message(self, *args):
            pass
    http = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=http.serve_forever, daemon=True)
    thread.start()
    state['base_url'] = f'http://127.0.0.1:{http.server_port}/v1'
    yield state
    http.shutdown()
    http.server_close()
    thread.join()


def strict_cfg(base_url):
    raw = json.loads((BASE / 'configs/qwen21_imagerag_20261007.json').read_text())['answers'][0]['imagerag']
    raw['vlm'].update(model='fixture-vlm', base_url=base_url, concurrency=1)
    return imagerag.configuration(raw, max_images=3)


@pytest.mark.parametrize('responses', [
    ['no', 'oil painting style\na sheep', 'Oil painting.\nA sheep.'],
    ['Yesterday'], ['no', ''], ['no', 'unable', "can't", 'unable'],
    ['no', 'Unable', 'caption'], ['no', 'unable', "can't", 'a sheep', 'A sheep.'],
])
def test_wire_requests_equal_official_including_history_and_duplicate_images(tmp_path, server, responses):
    prompt = 'A sheep {{ literal }} "oil"'
    result, expected = official_calls(tmp_path, responses, prompt)
    cfg = strict_cfg(server['base_url'])
    server['responses'] = list(responses)
    row = {'task_id': 'one', 'instruction': prompt, 'rag_status': 'ready', 'rag_reason': '',
           'fallback_prompt': False, 'rag_images': [expected[0]['messages'][0]['content'][1]['image_url']['url']]}
    pack = parse_prompt_pack(yaml.safe_dump(cfg['prompt_pack']))
    graph = data.from_items([row])
    for name, status, attempt in [('decision', 'ready', 1), ('concepts', 'needs_concepts', 1),
            ('concepts', 'needs_concepts', 2), ('concepts', 'needs_concepts', 3), ('captions', 'needs_captions', 1)]:
        graph = (graph.map(partial(original.prepare_messages, cfg=cfg, stage=name, attempt=attempt))
            .map_prompt_async(name, config=pack, inputs={'messages': 'rag_messages'}, output='rag_result',
                call_output='rag_call', error_output='rag_error', max_requests=1, concurrency=1,
                options={'verify_model': False, 'trust_env': False, 'request_options': {'temperature': 0, 'response_format': {'type': 'text'}}},
                when=lambda r, status=status: r['rag_status'] == status)
            .map(partial(original.finish_stage, cfg=cfg, stage=name, attempt=attempt)))
    actual = graph.checkpoint(tmp_path / 'result.jsonl', version='v1').take_all()[0]
    assert len(server['prompts']) == len(expected)
    for request, official in zip(server['prompts'], expected):
        assert request['messages'] == official['messages']
        assert request['temperature'] == official['temperature']
        assert request['response_format'] == official['response_format']
        assert all(m['role'] != 'system' for m in request['messages'])
    if result is True:
        assert actual['rag_status'] == 'keep_initial'
    else:
        assert actual['rag_status'] == 'needs_retrieval'
        assert [r['caption'] for r in imagerag.queries(actual)] == original.convert_res_to_captions(result)


@pytest.mark.parametrize('name', ['qwen21', 'bagel'])
def test_full_original_route_retrieves_all_captions_then_first_three_and_resumes(monkeypatch, server, name):
    cfg, fixed, questions, calls, images = fixture_setup(monkeypatch, server,
        instructions=['RETRY {{ untouched }}', 'KEEP_INITIAL', 'FALLBACK'], with_captions=True)
    model = cfg['answers'][0]
    raw = {k: v for k, v in model['imagerag'].items() if k not in ('max_concepts', 'prompt_pack')}
    raw.update(prompt_protocol='upstream_16c9502', max_queries=32)
    model['imagerag'] = imagerag.configuration(raw, max_images=3)
    model.pop('prompt_template')
    model['prompt_template'] = answer_template(model)
    # 两种部署均经相同平台 images_json 传输；模型端渲染不在这个 fixture 内运行。
    preset = json.loads((BASE / f'configs/{name}_imagerag_20261007.json').read_text())
    assert preset['answers'][0]['api'] == model['api'] == 'images_json'
    model['model'] = preset['answers'][0]['model']
    table = lance.dataset(**model['imagerag']['initial_answers'])
    initial_rows = table.to_table().to_pylist()
    by_id = {q['task_id']: q for q in questions}
    for r in initial_rows:
        r.update(answer_model=model['model'], request_id=answer_identity(imagerag.initial_model(model), by_id[r['task_id']]))
    uri = str(resolve_root() / 'model_initial.lance')
    committed = lance.write_dataset(pa.Table.from_pylist(initial_rows, schema=table.schema), uri)
    model['imagerag']['initial_answers'] = {'uri': uri, 'version': committed.version}
    cfg = config(answer_models=[model], parallel_answers=True, max_consecutive_failures=4)
    root, run = resolve_root(), name + '_original_fixture'
    manifest = {'config': cfg, 'source': fixed, 'expected_questions': 3}
    RunTables(root, str(DATASETS / f'records__{run}.lance')).save_manifest(manifest)
    state = run_paired_arm(root / DATASETS / run, manifest, 0)
    assert state['complete']
    traces = {r['task_id']: json.loads(r['rag_json']) for r in stage(root, run, 'rag_inputs')}
    retrieval = traces['q0']
    assert len(retrieval['concept_attempts']) == 3
    assert len(retrieval['retrievals']) == 4 and len(retrieval['references']) == 3
    assert len(stage(root, run, 'rag_queries')) == 5
    assert traces['q1']['status'] == 'keep_initial'
    assert traces['q2']['fallback_prompt'] and len(traces['q2']['concept_attempts']) == 3
    assert len(calls) == 2 and len(server['prompts']) == 10
    # 图库 caption 仅用于 trace；不参与 VLM 或查询编码。
    assert 'LIBRARY_ONLY' not in json.dumps(server['prompts']) + json.dumps(server['embeddings'])
    assert 'HIDDEN_' not in json.dumps(server['prompts'])
    answers = {r['task_id']: r for r in stage(root, run, 'answers')}
    answer = answers['q0']
    saved = JsonArtifactRef(**json.loads(answer['answer_call_json'])['input_ref']).read()['body']
    assert saved['prompt'] == original.render(model['imagerag'], 'generation',
        examples=original.generation_examples(retrieval['references'], model['imagerag']), prompt=questions[0]['instruction'])
    assert [base64.b64decode(url.split(',', 1)[1]) for url in saved['image']] == [
        ObjectRef(**ref['object_ref']).read() for ref in retrieval['references']]
    assert answer['answer_model'] == model['model'] + '+ImageRAG'
    assert saved['model'] == model['model']
    assert answers['q1']['reference_image_count'] == 0 and answers['q1']['generation_seconds'] == 0
    initial = json.loads(retrieval['initial_answer']['image_json'])
    original_bytes = ObjectRef(**initial).read()
    assert base64.b64decode(server['prompts'][0]['messages'][0]['content'][1]['image_url']['url'].split(',', 1)[1]) == original_bytes
    before = (len(calls), len(server['prompts']), len(server['embeddings']))
    assert run_paired_arm(root / DATASETS / run, manifest, 0)['complete']
    assert before == (len(calls), len(server['prompts']), len(server['embeddings']))


def test_operational_query_cap_fails_without_changing_prompt(server):
    cfg = strict_cfg(server['base_url'])
    cfg['max_queries'] = 3
    row = original.finish_stage({'rag_status': 'needs_captions', 'rag_result': 'one\ntwo\nthree\nfour'},
                                cfg=cfg, stage='captions')
    assert row['rag_status'] == 'diagnosis_failed' and 'no truncation' in row['rag_reason']
    assert 'Return up to' not in original.render(cfg, 'concepts')


def test_pending_is_not_a_semantic_refusal(server):
    row = original.finish_stage({'rag_status': 'needs_concepts', 'rag_error': {
        'type': 'PromptResponsePending', 'detail': 'waiting', 'call': {'request_id': 'fixed'}}},
        cfg=strict_cfg(server['base_url']), stage='concepts')
    assert row['rag_status'] == 'diagnosis_pending' and len(json.loads(row['concept_attempts_json'])) == 1
    assert imagerag.queries(row) == []


def test_rag_answers_flow_through_existing_d7_without_exposing_retrieval_to_judge():
    from evaluation.t2i.v2 import t2i_v2_eval_pipeline as pipeline
    from evaluation.t2i.v2.tests.test_d_evaluation import fixture_run, read_rows
    from evaluation.t2i.v2.tests.test_d7 import directions
    from demiflow.operator_llm.call_ref import read_call
    _, source, cfg = fixture_run(legacy=False, protocol='d7', requirements=directions())
    model = cfg['d_evaluation']['answers'][1]
    previous = lance.dataset(**model['source'])
    rows = previous.to_table().to_pylist()
    for i, r in enumerate(rows):
        r.update(answer_mode='imagerag', answer_model='fixture-1+ImageRAG',
                 reference_image_count=0 if i == 0 else 3)
    committed = lance.write_dataset(pa.Table.from_pylist(rows, schema=previous.schema), model['source']['uri'], mode='overwrite')
    model.update(model='fixture-1+ImageRAG', answer_mode='imagerag')
    model['source']['version'] = committed.version
    cfg = config(judge_model=cfg['judge'], d_evaluation=cfg['d_evaluation'])
    run = resolve_root() / DATASETS / 'rag_d7'
    state = pipeline.run_pipeline(run, source, cfg)
    assert state['phase'] == 'awaiting_d_responses'
    scores = read_rows(state['outputs']['arm1'])
    assert len(scores) == 2 and all(r['d_status'] == 'pending' for r in scores)
    for row in scores:
        call = json.loads(row['d_call_json'])
        request = read_call(call['request_ref'], resolve_root())
        assert 'ImageRAG' not in json.dumps(request['messages'])
    from evaluation.t2i.v2.tests.test_d7 import response
    from demiflow.operator_llm.sqlite_offline import submit_response
    for ref in (state['outputs']['arm0'], state['outputs']['arm1']):
        for row in read_rows(ref):
            call = json.loads(row['d_call_json'])
            submit_response(resolve_root(), call['request_ref'], json.dumps({'result': response()}),
                model=call['model'], metadata={'reviewer': 'fixture', 'reviewer_kind': 'human'})
    assert pipeline.run_pipeline(run, source, cfg)['complete']


def test_d7_config_requires_completed_rag_snapshot_and_matching_baseline(tmp_path):
    from evaluation.t2i.v2.operators.notebook import imagerag_d_config
    project = tmp_path / 'demiwtg'
    project.mkdir()
    name = 'rag_config_fixture'
    with pytest.raises(ValueError, match='must be complete'):
        imagerag_d_config(project, name, {})
    records = RunTables(tmp_path, str(DATASETS / f'records__{name}.lance'))
    source = {'uri': str(tmp_path / 'questions.lance'), 'version': 1}
    records.save_manifest({'source': source, 'expected_questions': 299,
                          'config': {'answers': [{'model': 'fixture', 'answer_mode': 'imagerag'}]}})
    target = {'uri': str(tmp_path / 'rag_answers.lance'), 'version': 7}
    records.save({'complete': True, 'target': target})
    rag_uri = tmp_path / DATASETS / f'rag_inputs__{name}__arm00.lance'
    lance.write_dataset(pa.Table.from_pylist([{'task_id': 'fixture'}]), str(rag_uri))
    baseline = {'run': 'baseline_fixture', 'source': source, 'judge': {'model': 'reviewer'}, 'd_evaluation': {
        'answers': [{'model': 'fixture', 'answer_mode': 'text_only', 'source': {'uri': 'initial', 'version': 1}}]}}
    with pytest.raises(ValueError, match='complete committed score table'):
        imagerag_d_config(project, name, baseline)
    scores = {'uri': str(tmp_path / 'baseline_scores.lance'), 'version': 3}
    RunTables(tmp_path, str(DATASETS / 'records__baseline_fixture.lance')).save(
        {'complete': True, 'target': scores})
    result = imagerag_d_config(project, name, baseline)
    assert result['d_evaluation']['answers'][1]['source'] == target
    assert result['d_evaluation']['answers'][1]['answer_mode'] == 'imagerag'
    assert result['judge'] == baseline['judge']
    assert result['d_evaluation']['answers'][1]['rag_inputs'] == {'uri': str(rag_uri), 'version': 1}
    assert result['d_evaluation']['reuse_scores'] == [scores]
    assert result['d_evaluation']['reuse_initial_scores'] is True
    assert len(baseline['d_evaluation']['answers']) == 1
