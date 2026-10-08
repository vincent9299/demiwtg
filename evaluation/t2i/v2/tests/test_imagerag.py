"""隔离HTTP响应＋真实Lance检索验证ImageRAG；不访问线上模型或生产图库。"""
import base64
import io
import json
import threading
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import lance
import pyarrow as pa
import pytest
from PIL import Image
from demiflow import data
from demiflow.embeddings import EmbeddingModel
from demiflow.execution.file_ref import JsonArtifactRef
from demiflow.image_generation import ImageGenerator
from demiflow.objects import LocalObjectStore, ObjectRef
from evaluation.t2i.v2.operators import imagerag
from evaluation.t2i.v2.operators.answers import answer_identity
from evaluation.t2i.v2.operators.run_tables import RunTables
from evaluation.t2i.v2.t2i_v2_eval_pipeline import config, DATASETS, run_pipeline, run_paired_arm
from evaluation.t2i.v2.tests.test_pipeline import picture, source, judgment
from evaluation.t2i.v2.tests.test_paired import question, probe_response
from project import resolve_root


@pytest.fixture
def rag_server():
    state = {'prompts': [], 'embeddings': [], 'malformed_captions': False}
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            if self.path.endswith('/embeddings'):
                state['embeddings'].append(body)
                values = body.get('input') or [m[0]['content'][0]['text'] for m in body['messages']]
                result = {'data': [{'index': i, 'embedding': [0., 1., 0.] if 'blue' in text else [1., 0., 0.]}
                                   for i, text in enumerate(values)], 'model': 'fixture-encoder'}
            else:
                state['prompts'].append(body)
                text = json.dumps(body['messages'], ensure_ascii=False)
                original = body.get('response_format') == {'type': 'text'}
                if original and len(body['messages']) == 5 and state.get('caption_http_408'):
                    state['caption_http_408'] = False
                    payload = b'{"error":{"message":"fixture timeout"}}'
                    self.send_response(408)
                    self.send_header('Content-Length', str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)
                    return
                if original:
                    response = {1: 'no', 3: 'blue dog\nred ball', 5: 'a blue dog\na red ball'}[len(body['messages'])]
                elif 'Decide whether' in text:
                    response = {'match': 'yes' if 'KEEP_INITIAL' in text else 'no', 'reason': 'visible mismatch'}
                elif 'Identify the visual concepts' in text:
                    response = {'concepts': [] if 'FALLBACK' in text else ['blue dog', 'red ball'], 'reason': 'missing subjects'}
                else:
                    response = {'captions': [{'concept_index': 1, 'caption': 'a blue dog'},
                                             {'concept_index': 2, 'caption': 'a red ball'}]}
                    if state['malformed_captions']:
                        response['captions'][1]['concept_index'] = 1
                result = {'choices': [{'message': {'role': 'assistant', 'content': response if original else json.dumps({'result': response})},
                                       'finish_reason': 'stop'}], 'usage': {'prompt_tokens': 10, 'completion_tokens': 10}}
            payload = json.dumps(result).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        def log_message(self, *args):
            pass
    http = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    worker = threading.Thread(target=http.serve_forever, daemon=True)
    worker.start()
    state['base_url'] = f'http://127.0.0.1:{http.server_port}/v1'
    yield state
    http.shutdown()
    http.server_close()
    worker.join()


def fixture_setup(monkeypatch, server, *, instructions=None, with_captions=False):
    root = resolve_root()
    calls = []
    def generate(self, request, key):
        calls.append(request)
        return picture()
    monkeypatch.setattr(ImageGenerator, 'remote', generate)
    instructions = instructions or ['RETRIEVE {{ untouched }}', 'KEEP_INITIAL', 'FALLBACK']
    questions = [{**question(), 'task_id': f'q{i}', 'instruction': instruction,
                  'test_points': [{'point': 'HIDDEN_TEST_POINT', 'basis': 'HIDDEN_BASIS', 'criterion': 'HIDDEN_CRITERION'}],
                  'references_json': 'HIDDEN_AUTHORING_MATERIAL'} for i, instruction in enumerate(instructions)]
    fixed = source(questions)
    model = {'backend': 'modelhub', 'model': 'fixture-image', 'api': 'images_json', 'revision': 'fixture-v1'}
    baseline = config(answer_models=[model])
    run_pipeline(root / DATASETS / 'rag_baseline', fixed, baseline)
    uri = str(root / DATASETS / 'answer_results__rag_baseline.lance')
    initial = {'uri': uri, 'version': lance.dataset(uri).version}
    encoder = EmbeddingModel(name='fixture-encoder', revision='fixture-1', dimensions=3,
                             base_url=server['base_url'], input_format='chat')
    images = []
    for color, vector in [('red', [1., 0., 0.]), ('blue', [0., 1., 0.])]:
        buf = io.BytesIO()
        Image.new('RGB', (10, 10), color).save(buf, format='PNG')
        ref = LocalObjectStore(root / 'objects').put(buf.getvalue())
        images.append({'sha256': ref.sha256, 'image_uri': ref.uri, 'encoder_id': encoder.fingerprint, 'embedding': vector})
    schema = pa.schema([('sha256', pa.string()), ('image_uri', pa.string()), ('encoder_id', pa.string()),
                        ('embedding', pa.list_(pa.float32(), 3))],
                       metadata={b'image_embeddings.contract': json.dumps(encoder.contract()).encode()})
    pool_uri = str(root / 'rag_pool.lance')
    table = lance.write_dataset(pa.Table.from_pylist(images, schema=schema), pool_uri)
    rag = {'initial_answers': initial, 'pool': {'uri': pool_uri, 'version': table.version},
           'encoder': asdict(encoder),
           'vlm': {'model': 'fixture-vlm', 'base_url': server['base_url'], 'concurrency': 1},
           'embedding_execution': {'batch_size': 2, 'concurrency': 1, 'queue_depth': 2}}
    if with_captions:
        caption_schema = pa.schema([(key, pa.string()) for key in ('sha256', 'status', 'caption', 'error')])
        caption_rows = [
            {'sha256': images[0]['sha256'], 'status': 'ok', 'caption': 'LIBRARY_ONLY red object', 'error': ''},
            {'sha256': images[1]['sha256'], 'status': 'failed', 'caption': None, 'error': 'original caption failed'},
        ]
        rag['caption_sources'] = []
        for name, rows in [('full', caption_rows), ('retry', [{
                'sha256': images[1]['sha256'], 'status': 'ok',
                'caption': 'LIBRARY_ONLY blue object after retry', 'error': ''}])]:
            uri = str(root / ('captions_' + name + '.lance'))
            caption_table = lance.write_dataset(pa.Table.from_pylist(rows, schema=caption_schema), uri)
            rag['caption_sources'].append({'uri': uri, 'version': caption_table.version})
    cfg = config(answer_models=[{**model, 'answer_mode': 'imagerag', 'imagerag': rag}],
                 judge_model={'model': 'fixture-judge', 'mode': 'offline'}, stream_judging=True,
                 max_consecutive_failures=4)
    calls.clear()
    return cfg, fixed, questions, calls, images


def stage(root, name, stage_name):
    uri = str(root / DATASETS / f'{stage_name}__{name}__arm00.lance')
    return data.read_lance(uri, version=lance.dataset(uri).version).take_all()


@pytest.mark.parametrize('paired', [False, True])
def test_explicit_reference_pixel_budget_reaches_native_node(monkeypatch, paired):
    root = resolve_root()
    ref = LocalObjectStore(root / 'objects').put(picture()).to_dict()
    fixed = source([{**question(), 'authoring_images_json': json.dumps([
        {'kind': 'image', 'object_ref': ref}])}])
    calls = []
    def remote(self, request, key):
        calls.append(request)
        return picture()
    monkeypatch.setattr(ImageGenerator, 'remote', remote)
    for pixels in (128, 512):
        name = f'pixel_budget_{paired}_{pixels}'
        cfg = config(answer_models=[{'backend': 'modelhub', 'model': 'fixture',
            'answer_mode': 'positive_images', 'image_limits': {'max_pixels': pixels}}],
            judge_model={'model': 'fixture-judge', 'mode': 'offline'}, stream_judging=paired)
        if paired:
            run_paired_arm(root / DATASETS / name,
                {'config': cfg, 'source': fixed, 'expected_questions': 1}, 0)
            row = stage(root, name, 'answers')[0]
        else:
            result = run_pipeline(root / DATASETS / name, fixed, cfg)
            row = data.read_lance(**result['target']).take(1)[0]
        if pixels == 128:
            assert row['status'] == 'generation_failed' and 'max_pixels' in row['reason']
            assert not calls and row['image_json'] is None
        else:
            assert row['status'] == 'generated' and len(calls) == 1
            assert json.loads(row['reference_images_json'])[0]['object_ref'] == ref
    for invalid in ([], {'max_pixels': True}, {'max_pixels': 128_000_001},
                    {'max_pixels': 60_000_000}, {'unknown': 1}):
        with pytest.raises(ValueError, match='pixel'):
            config(answer_models=[{'backend': 'modelhub', 'model': 'fixture', 'image_limits': invalid}])


def test_oversized_rag_reference_uses_platform_resize_and_preserves_source_identity():
    from evaluation.t2i.v2.operators.answers import PrepareAnswer
    root = resolve_root()
    buffer = io.BytesIO()
    Image.new('RGB', (5001, 4800), 'blue').save(buffer, format='JPEG')
    original, small = buffer.getvalue(), picture()
    store = LocalObjectStore(root / 'objects')
    refs = [{'number': i+1, 'caption': 'fixed caption', 'object_ref': store.put(raw).to_dict()}
            for i, raw in enumerate((original, small))]
    row = {**question(), 'rag_json': json.dumps({'status': 'retrieved', 'references': refs})}
    model = {'model': 'fixture', 'backend': 'modelhub', 'answer_mode': 'imagerag',
             'max_reference_images': 3, 'imagerag': {}}
    policy = {**model, 'reference_preprocessing': 'oversized_to_3840_jpeg90_v1'}
    assert answer_identity(model, row) != answer_identity(policy, row)
    assert answer_identity(imagerag.initial_model({**model, 'imagerag': {'initial_template': {'name': 'initial'}}}), row) == answer_identity(
        imagerag.initial_model({**policy, 'imagerag': {'initial_template': {'name': 'initial'}}}), row)
    prepared = PrepareAnswer(policy, RunTables(root, str(DATASETS/'records__resize_fixture.lance')))(row)
    assert prepared['answer_ready']
    assert prepared['answer_images'][1] == small
    with Image.open(io.BytesIO(prepared['answer_images'][0])) as image:
        assert image.format == 'JPEG' and max(image.size) == 3840
        assert image.width/image.height == pytest.approx(5001/4800, abs=.001)
    actual_refs = json.loads(prepared['reference_images_json'])
    assert [r['object_ref'] for r in actual_refs] == [r['object_ref'] for r in refs]
    assert actual_refs[0]['transport_preprocessing']['original_size'] == [5001, 4800]
    assert 'transport_preprocessing' not in actual_refs[1]
    assert ObjectRef(**refs[0]['object_ref']).read() == original


def test_reuse_frozen_rag_inputs_and_images_only_generates_missing_rows(monkeypatch, rag_server):
    import copy
    from evaluation.t2i.v2.t2i_v2_eval_pipeline import run_answers, ANSWERS
    cfg, fixed, questions, calls, images = fixture_setup(monkeypatch, rag_server)
    root = resolve_root()
    model = copy.deepcopy(cfg['answers'][0])
    model['image_encoding'] = 'png'
    subset_uri = str(root / 'subset.lance')
    data.read_lance(**fixed).limit(2).write_lance(subset_uri, mode='create')
    run_answers(root / DATASETS / 'reuse_first', {'uri': subset_uri, 'version': 1}, model,
                answer_target=str(root / DATASETS / 'answers__reuse_first.lance'))
    previous = data.read_lance(str(root / DATASETS / 'answer_results__reuse_first.lance'), version=2).take_all()
    before = len(calls), len(rag_server['prompts'])
    new = copy.deepcopy(model)
    new['image_encoding'] = 'preserve'
    new['imagerag']['reuse'] = {
        'model': model,
        'inputs': {'uri': str(root / DATASETS / 'rag_inputs__reuse_first.lance'), 'version': 1},
        'answers': {'uri': str(root / DATASETS / 'answer_results__reuse_first.lance'), 'version': 2}}
    result = run_answers(root / DATASETS / 'reuse_full', fixed, new,
                         answer_target=str(root / DATASETS / 'answers__reuse_full.lance'))
    rows = data.read_lance(**result.outputs['answers']).take_all()
    assert len(rows) == 3 and all(r['status'] == 'generated' for r in rows)
    assert (len(calls) - before[0], len(rag_server['prompts']) - before[1]) == (1, 2)
    for old in previous:
        assert next(r for r in rows if r['task_id'] == old['task_id']) == {k: old[k] for k in ANSWERS.names}
    # An altered question must be rejected before issuing any new model call.
    trace = data.read_lance(**new['imagerag']['reuse']['inputs']).take(1)[0]
    with pytest.raises(ValueError, match='current question'):
        imagerag.restore_input({**questions[0], 'instruction': 'changed', 'reused_rag_input': trace}, model=new)


def test_caption_408_recovery_reuses_successful_messages_and_preserves_failure(monkeypatch, rag_server):
    import copy
    from evaluation.t2i.v2.operators.answers import answer_template
    from evaluation.t2i.v2.t2i_v2_eval_pipeline import run_answers
    cfg, fixed, questions, calls, _ = fixture_setup(monkeypatch, rag_server, instructions=['RECOVER'])
    root = resolve_root()
    model = cfg['answers'][0]
    raw = {k: v for k, v in model['imagerag'].items() if k not in ('max_concepts', 'prompt_pack')}
    raw['prompt_protocol'] = 'upstream_16c9502'
    model['imagerag'] = imagerag.configuration(raw, max_images=3)
    model.pop('prompt_template')
    model['prompt_template'] = answer_template(model)
    rag_server['caption_http_408'] = True
    first = run_answers(root / DATASETS / 'caption_failed', fixed, model,
                        answer_target=str(root / DATASETS / 'answers__caption_failed.lance'))
    assert data.read_lance(**first.outputs['answers']).take(1)[0]['status'] == 'generation_failed'
    diagnosis_uri = str(root / DATASETS / 'rag_diagnoses__caption_failed.lance')
    diagnosis = {'uri': diagnosis_uri, 'version': lance.dataset(diagnosis_uri).version}
    old = data.read_lance(**diagnosis).take(1)[0]
    assert old['rag_reason'].startswith('captions: HTTP 408')
    empty_uri = str(root / 'empty_rag_inputs.lance')
    data.from_arrow(pa.Table.from_pylist([], schema=imagerag.INPUTS)).write_lance(
        empty_uri, mode='create', schema=imagerag.INPUTS)
    new = copy.deepcopy(model)
    new['imagerag']['reuse'] = {'model': copy.deepcopy(model),
        'inputs': {'uri': empty_uri, 'version': 1},
        'answers': {'uri': str(root / DATASETS / 'answer_results__caption_failed.lance'), 'version': 1},
        'caption_recovery': diagnosis}
    before = len(rag_server['prompts'])
    result = run_answers(root / DATASETS / 'caption_recovered', fixed, new,
                         answer_target=str(root / DATASETS / 'answers__caption_recovered.lance'))
    assert len(rag_server['prompts']) == before + 1
    assert rag_server['prompts'][-1]['messages'] == rag_server['prompts'][before - 1]['messages']
    assert len(calls) == 1
    assert data.read_lance(**result.outputs['answers']).take(1)[0]['status'] == 'generated'
    assert data.read_lance(**diagnosis).take(1)[0] == old
    trace = json.loads(data.read_lance(str(root / DATASETS / 'rag_inputs__caption_recovered.lance')).take(1)[0]['rag_json'])
    assert trace['stage_refs']['caption_recovery'] == diagnosis
    assert trace['stages']['decision']['call'] == json.loads(old['decision_call_json'])
    assert trace['stages']['concepts']['call'] == json.loads(old['concepts_call_json'])
    initial = imagerag.prepare_initial({**questions[0], 'initial_answer': json.loads(old['initial_answer_json'])},
                                       model=new, root=root)
    with pytest.raises(ValueError, match='question differs'):
        imagerag.recover_caption({**initial, 'instruction': 'changed', 'caption_recovery': old})


def test_three_prompts_text_image_retrieval_skip_fallback_and_resume(monkeypatch, rag_server):
    cfg, fixed, questions, calls, images = fixture_setup(monkeypatch, rag_server, with_captions=True)
    root, name = resolve_root(), 'rag_paired'
    manifest = {'config': cfg, 'source': fixed, 'expected_questions': 3}
    RunTables(root, str(DATASETS / f'records__{name}.lance')).save_manifest(manifest)
    run_paired_arm(root / DATASETS / name, manifest, 0)
    answers = stage(root, name, 'answers')
    traces = {r['task_id']: json.loads(r['rag_json']) for r in stage(root, name, 'rag_inputs')}
    assert len(answers) == 3 and all(r['status'] == 'generated' for r in answers)
    assert traces['q1']['status'] == 'keep_initial' and traces['q1']['references'] == []
    assert traces['q2']['fallback_prompt'] and traces['q2']['references'][0]['caption'] == 'FALLBACK'
    refs = traces['q0']['references']
    assert [r['object_ref']['sha256'] for r in refs] == [images[1]['sha256'], images[0]['sha256']]
    assert [r['caption'] for r in refs] == ['a blue dog', 'a red ball']
    assert [r['image_caption'] for r in refs] == ['LIBRARY_ONLY blue object after retry', 'LIBRARY_ONLY red object']
    assert all(r['image_caption_status'] == 'available' for r in refs)
    assert refs[0]['image_caption_source'] == cfg['answers'][0]['imagerag']['caption_sources'][1]
    assert len(calls) == 2 and len(rag_server['prompts']) == 6  # 3 + 1 + 2
    assert len(stage(root, name, 'rag_queries')) == 3
    for body in rag_server['prompts']:
        wire = json.dumps(body, ensure_ascii=False)
        assert all(secret not in wire for secret in ['HIDDEN_', 'fixture-image', '正例参考图', 'LIBRARY_ONLY'])
        images_in_prompt = [p for m in body['messages'] for p in m.get('content', []) if isinstance(p, dict) and p.get('type') == 'image_url']
        assert len(images_in_prompt) == 1
    for row in answers:
        if row['task_id'] == 'q1':
            assert row['generation_seconds'] == 0 and row['reference_image_count'] == 0
            continue
        saved = JsonArtifactRef(**json.loads(row['answer_call_json'])['input_ref']).read()
        body = saved['body']
        assert 'LIBRARY_ONLY' not in body['prompt']
        assert questions[int(row['task_id'][1:])]['instruction'] in body['prompt']
        if row['task_id'] == 'q0':
            assert body['prompt'].index('a blue dog') < body['prompt'].index('a red ball')
            assert 'RETRIEVE {{ untouched }}' in body['prompt']
            assert [base64.b64decode(url.split(',', 1)[1]) for url in body['image']] == [
                ObjectRef(uri=r['image_uri'], sha256=r['sha256']).read() for r in [images[1], images[0]]]
            assert len(body['image']) == 2
    before = (len(calls), len(rag_server['prompts']), len(rag_server['embeddings']))
    from demiflow.operator_llm.sqlite_offline import submit_response
    for row in stage(root, name, 'scores_b'):
        for standard, response in [('a', judgment()), ('b', probe_response())]:
            call = json.loads(row[standard + '_call_json'])
            submit_response(root, call['request_ref'], json.dumps({'result': response}),
                model=call['model'], metadata={'reviewer': 'fixture', 'reviewer_kind': 'human'})
    done = run_paired_arm(root / DATASETS / name, manifest, 0)
    assert done['complete']
    assert (len(calls), len(rag_server['prompts']), len(rag_server['embeddings'])) == before
    assert len(stage(root, name, 'answers')) == 3
    # 新路线展示真实初图、诊断图像、caption配对和最终输入；浏览不触发模型请求。
    from evaluation.t2i.v2.operators.case_viewer import build_case_browser
    from playwright.sync_api import sync_playwright
    document, meta = build_case_browser(root / 'demiwtg', name)
    assert meta['arms'][0]['imagerag'] == {'retrieved': 2, 'keep_initial': 1}
    with sync_playwright() as play:
        browser = play.chromium.launch(executable_path=play.chromium.executable_path, args=['--no-sandbox'])
        page = browser.new_page()
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.set_content(document)
        page.select_option('#concept', 'q0')
        page.locator('.rag-trace > summary').click()
        assert 'a blue dog' in page.locator('.rag-trace').inner_text()
        assert 'LIBRARY_ONLY blue object after retry' in page.locator('.rag-trace').inner_text()
        assert '摘要与作答图不一致' not in page.locator('.rag-trace').inner_text()
        assert page.locator('.actual-references img').count() == 2
        assert 'RETRIEVE {{ untouched }}' in page.locator('[data-field=actual-prompt]').inner_text()
        page.select_option('#concept', 'q1')
        page.locator('.rag-trace > summary').click()
        assert '最终沿用初图' in page.locator('.rag-trace').inner_text()
        assert page.locator('.actual-references img').count() == 0
        assert not errors
        browser.close()
    assert (len(calls), len(rag_server['prompts']), len(rag_server['embeddings'])) == before


def test_bad_caption_mapping_is_failed_without_retrieval_or_regeneration(monkeypatch, rag_server):
    rag_server['malformed_captions'] = True
    cfg, fixed, _, calls, _ = fixture_setup(monkeypatch, rag_server, instructions=['RETRIEVE'])
    root, name = resolve_root(), 'rag_bad_captions'
    run_paired_arm(root / DATASETS / name, {'config': cfg, 'source': fixed, 'expected_questions': 1}, 0)
    answer = stage(root, name, 'answers')[0]
    assert answer['status'] == 'generation_failed' and 'exactly once' in answer['reason']
    assert calls == [] and rag_server['embeddings'] == []
    assert stage(root, name, 'scores_b')[0]['b_status'] == 'generation_failed'


def test_pool_contract_mismatch_stops_before_vlm(monkeypatch, rag_server):
    cfg, fixed, _, calls, _ = fixture_setup(monkeypatch, rag_server, instructions=['RETRIEVE'])
    cfg['answers'][0]['imagerag']['encoder']['revision'] = 'different-space'
    with pytest.raises(ValueError, match='complete frozen'):
        run_paired_arm(resolve_root() / DATASETS / 'rag_wrong_space',
            {'config': cfg, 'source': fixed, 'expected_questions': 1}, 0)
    assert not calls and not rag_server['prompts']


def test_changed_initial_generation_is_not_silently_reused(monkeypatch, rag_server):
    cfg, fixed, _, calls, _ = fixture_setup(monkeypatch, rag_server, instructions=['RETRIEVE'])
    cfg['answers'][0]['seed'] += 1
    root, name = resolve_root(), 'rag_wrong_initial'
    run_paired_arm(root / DATASETS / name, {'config': cfg, 'source': fixed, 'expected_questions': 1}, 0)
    answer = stage(root, name, 'answers')[0]
    assert answer['status'] == 'generation_failed' and 'same model/text-only request' in answer['reason']
    assert not calls and not rag_server['prompts'] and not rag_server['embeddings']


def test_actual_caption_and_reference_order_define_answer_identity(monkeypatch, rag_server):
    cfg, _, questions, _, images = fixture_setup(monkeypatch, rag_server, instructions=['RETRIEVE'])
    model = cfg['answers'][0]
    trace = {'status': 'retrieved', 'references': [{'caption': 'red', 'object_ref': images[0]}]}
    row = {**questions[0], 'rag_json': json.dumps(trace)}
    before = answer_identity(model, row)
    trace['references'][0]['caption'] = 'blue'
    assert answer_identity(model, {**row, 'rag_json': json.dumps(trace)}) != before
    trace['stage_refs'] = {'queries': {'version': 2}}
    second = answer_identity(model, {**row, 'rag_json': json.dumps(trace)})
    trace['stage_refs']['queries']['version'] = 3
    assert answer_identity(model, {**row, 'rag_json': json.dumps(trace)}) == second


def test_generation_only_entry_keeps_full_scope_and_uses_same_rag_graph(monkeypatch, rag_server):
    cfg, fixed, _, calls, _ = fixture_setup(monkeypatch, rag_server, instructions=['RETRIEVE'])
    cfg = config(answer_models=cfg['answers'])
    result = run_pipeline(resolve_root() / DATASETS / 'rag_answers_only', fixed, cfg)
    assert result['complete'] and result['counts'] == {'generated': 1}
    answer = data.read_lance(**result['target']).take(1)[0]
    assert answer['reference_image_count'] == 2 and answer['answer_mode'] == 'imagerag'
    assert answer['judge_json'] is None and len(calls) == 1


def test_pending_diagnosis_preserves_native_call_and_does_not_trigger_queries():
    call = {'request_ref': {'request_id': 'pending-fixture'}}
    row = imagerag.finish_stage({'rag_status': 'ready', 'rag_reason': '',
        'rag_error': {'type': 'PromptResponsePending', 'detail': 'response not submitted', 'call': call}},
        cfg={}, stage='decision')
    assert row['rag_status'] == 'diagnosis_pending'
    assert json.loads(row['decision_call_json']) == call
    assert imagerag.queries(row) == []


def test_failed_and_missing_library_captions_keep_vector_hits(monkeypatch, rag_server):
    cfg, fixed, _, calls, images = fixture_setup(monkeypatch, rag_server, instructions=['RETRIEVE'])
    uri = str(resolve_root() / 'partial_captions.lance')
    schema = pa.schema([(key, pa.string()) for key in ('sha256', 'status', 'caption', 'error')])
    table = lance.write_dataset(pa.Table.from_pylist([{
        'sha256': images[1]['sha256'], 'status': 'failed', 'caption': None,
        'error': 'caption generation failed'}], schema=schema), uri)
    cfg['answers'][0]['imagerag']['caption_sources'] = [{'uri': uri, 'version': table.version}]
    name = 'rag_partial_captions'
    run_paired_arm(resolve_root() / DATASETS / name,
        {'config': cfg, 'source': fixed, 'expected_questions': 1}, 0)
    trace = json.loads(stage(resolve_root(), name, 'rag_inputs')[0]['rag_json'])
    assert trace['status'] == 'retrieved'
    assert [r['image_caption_status'] for r in trace['references']] == ['failed', 'missing']
    assert [r['object_ref']['sha256'] for r in trace['references']] == [images[1]['sha256'], images[0]['sha256']]
    assert stage(resolve_root(), name, 'answers')[0]['reference_image_count'] == 2
    assert len(calls) == 1


def test_all_kept_initials_skip_queries_even_with_caption_sources(monkeypatch, rag_server):
    cfg, fixed, _, calls, _ = fixture_setup(monkeypatch, rag_server,
        instructions=['KEEP_INITIAL'], with_captions=True)
    name = 'rag_all_kept'
    run_paired_arm(resolve_root() / DATASETS / name,
        {'config': cfg, 'source': fixed, 'expected_questions': 1}, 0)
    assert not calls and not rag_server['embeddings'] and len(rag_server['prompts']) == 1
    assert stage(resolve_root(), name, 'rag_queries') == []
    assert stage(resolve_root(), name, 'rag_retrievals') == []
    answer = stage(resolve_root(), name, 'answers')[0]
    assert answer['status'] == 'generated' and answer['reference_image_count'] == 0
    assert json.loads(stage(resolve_root(), name, 'rag_inputs')[0]['rag_json'])['status'] == 'keep_initial'
