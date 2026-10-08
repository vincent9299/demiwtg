"""隔离湖、真实 Dataset/ObjectRef/app-server 边界；不调用真实出题或生图服务。"""
import io
import json
import os
from pathlib import Path
import sys

import lance
import pyarrow as pa
from PIL import Image
import pytest
import yaml
from demiflow import data
from demiflow.objects import LocalObjectStore, ObjectRef
from project import resolve_root
from benchmark.edit.v2.edit_v2_benchmark_pipeline import config, run_pipeline, DATASETS, QUESTIONS, PROMPTS


def rows(ref):
    return data.read_lance(**ref).take_all()


def question():
    return {'seed_image': 1, 'seed_image_id': None, 'source_image_edit': None, 'source_image_checks': ['判断节点及两条出边可见'],
            'source_artifact': None,
            'source_image_check': {'status': 'passed', 'observations': [
                {'check': 1, 'passed': True, 'evidence': '可见一个菱形及两条出边'}], 'reason': ''},
            'instruction': '将条件判断改成处理步骤，保持流程连接合理。',
            'test_points': [{'point': '处理步骤的节点形状', 'basis': '处理步骤以矩形表示。',
                             'criterion': '判断节点改为矩形处理框，相关连接保持合理。'}]}


@pytest.fixture
def materials():
    root = resolve_root()
    raw = io.BytesIO()
    Image.new('RGB', (24, 16), 'white').save(raw, format='PNG')
    ref = LocalObjectStore(root / 'objects').put(raw.getvalue()).to_dict()
    schema = pa.schema([('sha256', pa.string()), ('image_uri', pa.string()),
        ('published_concepts', pa.list_(pa.string())), ('concept_assessments', pa.list_(pa.struct([
            ('concept', pa.string()), ('published', pa.bool_()), ('review_status', pa.string())])))])
    uri = root / 'demiwtg/preparation/images/catalog/datasets/images.lance'
    data.from_items([{'sha256': ref['sha256'], 'image_uri': ref['uri'], 'published_concepts': ['流程图'],
        'concept_assessments': [{'concept': '流程图', 'published': True, 'review_status': 'keep'}]}]).write_lance(
            str(uri), mode='overwrite', schema=schema)
    return {'uri': str(uri), 'version': 1}, ref


def settings(materials, **kw):
    spec = yaml.safe_load((PROMPTS / 'agent_codex.yaml').read_text())
    spec['model']['name'] = 'fixture-codex'
    settings = spec['options']['codex_agent']
    settings['bin'] = str(kw.pop('codex_bin', os.environ['EDIT_TEST_CODEX_BIN']))
    settings['image_generation'] = kw.pop('generate_source', False)
    if settings['image_generation']:
        settings['artifact_store'] = {'directory': str(resolve_root() / 'objects')}
    else:
        settings.pop('artifact_store', None)
        settings.pop('max_artifact_files', None)
        settings.pop('max_artifact_bytes', None)
    for key in ('reasoning_effort', 'max_artifact_bytes', 'model_revision', 'max_input_bytes', 'max_scratch_bytes'):
        if key in kw:
            settings[key] = kw.pop(key)
    if 'max_context_chars' in kw:
        spec['budgets']['max_context_chars'] = kw.pop('max_context_chars')
    if kw.pop('no_search', False):
        spec['operators'] = []
        spec.pop('operator_settings', None)
    path = resolve_root() / 'test_agent.yaml'
    path.write_text(yaml.safe_dump(spec, allow_unicode=True, sort_keys=False))
    kw['agent_config'] = str(path)
    return config(**{'run': resolve_root() / DATASETS / 'test', 'concepts': ['流程图'],
                     'visual_source': materials[0], **kw})


@pytest.mark.parametrize('write_mode', ['append', 'overwrite'])
def test_codex_roundtrip_and_resume(materials, write_mode):
    cfg = settings(materials, write_mode=write_mode, max_calls=0)
    first = run_pipeline(cfg)
    assert first['counts'] == {'failed': 1} and first['candidate_count'] == 0
    assert 'request budget exhausted' in rows(first['designs'])[0]['reason']
    cfg = settings(materials, write_mode=write_mode, max_calls=1)
    done = run_pipeline(cfg)
    assert done['complete'] and done['counts'] == {'candidate': 1}
    candidate = rows(done['candidates'])[0]
    assert candidate['status'] == 'unreviewed'
    assert candidate['edit_source'] == materials[1] == candidate['seed_asset']
    assert candidate['instruction'] == question()['instruction']
    assert lance.dataset(done['candidates']['uri']).schema == QUESTIONS
    again = run_pipeline(cfg)
    assert len(rows(again['candidates'])) == 1
    assert json.loads(rows(again['designs'])[0]['call_json'])['reused']


def test_audited_cohort_preserves_identity_documents_and_positive_pixels(materials, fake_cli):
    root = resolve_root()
    from demiflow.collect.documents import parse_document
    store = LocalObjectStore(root / 'objects')
    body = b'<html><title>Flow symbols</title><p>SECRET_BODY: processing uses rectangles.</p></html>'
    doc = parse_document(body, url='https://example.org/flow', final_url='https://example.org/flow',
                         content_type='text/html')
    doc['raw_ref'] = store.put(body).to_dict()
    ref = store.put(json.dumps(doc).encode()).to_dict()
    block = doc['blocks'][0]['block_id']
    eid = ref['sha256'] + ':' + block
    audit = {'uri': str(root / 'adopted.lance'), 'version': 3}
    record = {'concept_id': 'C1', 'assessment_id': 'R1', 'canonical_name': '流程图',
        'original_name': 'Flowchart', 'definition': '表示步骤和条件关系的图。', 'adopted_source': audit,
        'identity_evidence_ids': [eid], 'core_facts': [],
        'evidence': [{'evidence_id': eid, 'document_ref': ref, 'block_id': block}]}
    row = {'concept': 'Flowchart', 'concept_id': 'C1', 'assessment_id': 'R1',
        'taxonomy': ['图形 / 流程图'], 'selection_rank': 1, 'concept_record': record,
        'positive_images': [{'sha256': materials[1]['sha256'], 'image_uri': materials[1]['uri'],
                             'review_sources': [audit]}]}
    uri = root / 'cohort.lance'
    data.from_items([row, {**row, 'selection_rank': 2}]).write_lance(str(uri), mode='overwrite')
    cfg = settings(materials, concepts=None, visual_source=None,
                   cohort_source={'uri': str(uri), 'version': 1}, sample_size=1)
    state = run_pipeline(cfg)
    assert state['complete'] and state['candidate_count'] == 1
    candidate = rows(state['candidates'])[0]
    assert candidate['concept'] == '流程图' and candidate['seed_asset'] == materials[1]
    assert candidate['taxonomy'] == ['图形 / 流程图']
    references = json.loads(candidate['references_json'])
    assert references[0]['original_name'] == 'Flowchart'
    assert references[0]['cohort_source'] == {'uri': str(uri), 'version': 1}
    prompt = json.loads(fake_cli[1].read_text())['prompt']
    assert record['definition'] in prompt and 'SECRET_BODY' not in prompt
    assert 'https://example.org/flow' in prompt and ref['sha256'] in prompt
    assert 'E1' in prompt and block in prompt
    with pytest.raises(ValueError, match='excludes other material'):
        settings(materials, cohort_source={'uri': str(uri), 'version': 1}, sample_size=1)


def test_pending_synthesis_is_not_delivered(materials, monkeypatch):
    cfg = settings(materials)
    q = question()
    q.update(source_image_edit='删除判断节点，保留连接和布局。', source_image_check={
        'status': 'not_run', 'observations': [], 'reason': '本轮未执行原图合成'})
    monkeypatch.setenv('EDIT_QUESTION', json.dumps(q))
    state = run_pipeline(cfg)
    assert state['counts'] == {'needs_source_synthesis': 1} and not state['complete']
    row = rows(state['designs'])[0]
    assert row['question'] == {**q, 'seed_image_id': None} and row['seed_asset'] == materials[1] and row['edit_source'] is None
    assert not rows(state['candidates'])


@pytest.mark.parametrize('mutation', ['seed', 'blank', 'claim', 'duplicate_check', 'missing_check', 'failed_claim', 'old_field'])
def test_invalid_response_is_not_candidate(materials, mutation, monkeypatch):
    cfg = settings(materials)
    q = question()
    if mutation == 'seed': q['seed_image'] = 2
    elif mutation == 'blank': q['test_points'][0]['point'] = ' '
    elif mutation == 'claim': q['source_image_edit'] = '删除节点'
    elif mutation == 'duplicate_check': q['source_image_check']['observations'] *= 2
    elif mutation == 'missing_check': q['source_image_check']['observations'] = []
    elif mutation == 'failed_claim': q['source_image_check']['observations'][0]['passed'] = False
    elif mutation == 'old_field': q['edit_type'] = 'modify'
    monkeypatch.setenv('EDIT_QUESTION', json.dumps(q))
    state = run_pipeline(cfg)
    assert state['counts'] == {'invalid_response': 1} and not rows(state['candidates'])


def test_null_and_failed_checks_are_distinct(materials, monkeypatch):
    cfg = settings(materials)
    monkeypatch.setenv('EDIT_QUESTION', 'null')
    monkeypatch.setenv('EDIT_REASON', '当前图片没有可用的可见考点')
    assert run_pipeline(cfg)['counts'] == {'insufficient': 1}
    cfg = settings(materials, run=resolve_root() / DATASETS / 'failure')
    q = question()
    q['source_image_check'] = {'status': 'failed', 'observations': [
        {'check': 1, 'passed': False, 'evidence': '节点被裁切，无法识别'}], 'reason': '必要线索不足'}
    monkeypatch.setenv('EDIT_QUESTION', json.dumps(q))
    monkeypatch.delenv('EDIT_REASON')
    state = run_pipeline(cfg)
    assert state['counts'] == {'source_check_failed': 1} and not state['complete']
    assert rows(state['designs'])[0]['question'] == {**q, 'seed_image_id': None}


def test_no_images_and_budget_skip_model(materials):
    cfg = settings(materials, visual_source=None, no_search=True)
    state = run_pipeline(cfg)
    assert state['counts'] == {'needs_seed_images': 1}
    assert not (resolve_root() / state['calls']['relative_uri']).exists()
    cfg = settings(materials, max_context_chars=1)
    state = run_pipeline(cfg)
    assert state['counts'] == {'needs_context_budget': 1}
    assert not (resolve_root() / state['calls']['relative_uri']).exists()


def test_fixed_sources_and_output_collision(materials):
    with pytest.raises(ValueError, match='fixed uri/version'):
        settings(materials, visual_source={'uri': 'a', 'version': None})
    with pytest.raises(ValueError, match='distinct paths'):
        run_pipeline(settings(materials, target_uri=materials[0]['uri']))


def test_screened_sample_preserves_taxonomy(materials):
    uri = resolve_root() / 'fixtures/screening.lance'
    data.from_items([{'concept': c, 'taxonomy': t, 'status': 'screened', 'decision': d}
        for c, t, d in [('流程图', ['A/B', 'C/D'], 'keep'), ('排除', ['Z'], 'reject')]]).write_lance(str(uri), mode='overwrite')
    cfg = settings(materials, concepts=None, screening_source={'uri': str(uri), 'version': 1}, sample_size=1)
    state = run_pipeline(cfg)
    assert rows(state['inputs'])[0]['taxonomy'] == ['A/B', 'C/D']
    assert 'candidate_point' not in rows(state['inputs'])[0]['references_json']


@pytest.fixture(autouse=True)
def fake_cli(tmp_path, monkeypatch):
    cli = tmp_path / 'fake-codex'
    capture = tmp_path / 'capture.jsonl'
    monkeypatch.setenv('EDIT_CAPTURE', str(capture))
    monkeypatch.setenv('EDIT_TEST_CODEX_BIN', str(cli))
    monkeypatch.setenv('EDIT_QUESTION', json.dumps(question()))
    cli.write_text('#!' + sys.executable + '\n' + r'''
import base64, json, os, pathlib, sys, time
args = sys.argv[1:]
assert args[:3] == ['app-server', '--listen', 'stdio://']
def receive():
    return json.loads(sys.stdin.readline())
def send(value):
    print(json.dumps(value), flush=True)
def notify(method, params):
    send({'method': method, 'params': {'threadId': 'thread', **params}})
req = receive()
assert req['method'] == 'initialize'
send({'id': req['id'], 'result': {'userAgent': 'fixture'}})
assert receive()['method'] == 'initialized'
req = receive()
assert req['method'] == 'thread/start'
thread = req['params']
assert thread['ephemeral'] and thread['approvalPolicy'] == 'never'
assert {tool['name'] for tool in thread.get('dynamicTools', [])} in (set(), {'map_embeddings', 'search_vectors'})
send({'id': req['id'], 'result': {'thread': {'id': 'thread'}, 'model': thread['model']}})
req = receive()
assert req['method'] == 'turn/start'
prompt = '\n'.join(p['text'] for p in req['params']['input'] if p['type'] == 'text')
if os.environ.get('EDIT_EXPECT_DOCUMENT'):
    section = prompt.split('### 10.2 依据材料\n', 1)[1].split('### 10.3 正例图', 1)[0]
    document = json.loads(section)['documents']['D1']
    path = pathlib.Path(document['local_path'])
    assert path.is_absolute() and path.read_text() == os.environ['EDIT_EXPECT_DOCUMENT']
    assert os.environ['EDIT_EXPECT_DOCUMENT'] not in prompt
    assert 'document_ref' not in document and 'bindings' not in document
images = [base64.b64decode(p['url'].split(',', 1)[1]).hex() for p in req['params']['input'] if p['type'] == 'image']
with open(os.environ['EDIT_CAPTURE'], 'a') as stream:
    stream.write(json.dumps({'prompt': prompt, 'args': args, 'images': images, 'thread': thread}) + '\n')
notify('turn/started', {'turn': {'id': 'turn', 'status': 'inProgress', 'items': []}})
send({'id': req['id'], 'result': {'turn': {'id': 'turn', 'status': 'inProgress', 'items': []}}})
question = json.loads(os.environ['EDIT_QUESTION'])
if os.environ.get('EDIT_RETRIEVAL'):
    def tool(name, arguments, ident):
        send({'id': ident, 'method': 'item/tool/call', 'params': {
            'threadId': 'thread', 'turnId': 'turn', 'callId': str(ident), 'tool': name, 'arguments': arguments}})
        reply = receive()
        assert reply['id'] == ident and reply['result']['success'], reply
        return reply['result']
    encoded = json.loads(tool('map_embeddings', {'text': 'a visible flowchart'}, 501)['contentItems'][0]['text'])['result']
    found = tool('search_vectors', {'query_ref': encoded['embedding_ref'], 'top_k': 1}, 502)
    from PIL import Image
    import io
    image, = [p for p in found['contentItems'] if p['type'] == 'inputImage']
    pixels = Image.open(io.BytesIO(base64.b64decode(image['imageUrl'].split(',', 1)[1])))
    assert pixels.size == (24, 16) and pixels.getpixel((0, 0)) == (255, 255, 255)
    receipt, = json.loads(found['contentItems'][0]['text'])['images']
    assert receipt['status'] == 'attached' and pathlib.Path(receipt['local_path']).is_file()
    question.update(seed_image=None, seed_image_id=receipt['image_id'])
artifact_kind = os.environ.get('EDIT_ARTIFACT', '')
if artifact_kind:
    assert 'features.image_generation=true' in args
    assert 'features.view_image=true' in args and 'features.shell_tool=true' in args
    assert thread['sandbox'] == 'workspace-write'
    context = json.loads(prompt.rsplit('CODEX_EXEC_FILE_CONTEXT\n', 1)[1])
    assert pathlib.Path(context['input_images'][0]['path']).read_bytes().hex() == images[0]
    destination = pathlib.Path(context['artifact_directory']) / 'source.png'
    if artifact_kind == 'generated':
        from PIL import Image
        Image.new('RGB', (24, 16), 'red').save(destination)
    elif artifact_kind == 'invalid':
        destination.write_bytes(b'not an image')
    elif artifact_kind == 'identical':
        destination.write_bytes(pathlib.Path(context['input_images'][0]['path']).read_bytes())
notify('thread/tokenUsage/updated', {'turnId': 'turn', 'tokenUsage': {'total': {'inputTokens': 10, 'outputTokens': 5}}})
result = {'question': question}
if os.environ.get('EDIT_REASON'): result['reason'] = os.environ['EDIT_REASON']
notify('item/completed', {'item': {'id': 'answer', 'type': 'agentMessage', 'phase': 'final_answer',
    'text': json.dumps({'result': result})}})
notify('turn/completed', {'turn': {'id': 'turn', 'status': 'completed', 'items': []}})
time.sleep(20)  # Runtime must reap the session on success too.

''')
    cli.chmod(0o755)
    return cli, capture


def test_codex_uses_native_node_image_order_and_cached_response(materials, fake_cli):
    cli, capture = fake_cli
    cfg = settings(materials, codex_bin=cli, reasoning_effort='high')
    state = run_pipeline(cfg)
    assert state['complete'] and state['counts'] == {'candidate': 1}
    execution = json.loads(capture.read_text())
    assert execution['images'] == [ObjectRef(**materials[1]).read().hex()]
    call = json.loads(rows(state['designs'])[0]['call_json'])
    assert call['transport'] == 'codex_app_server' and call['runtime'] == 'codex'
    assert call['budget_unit'] == 'codex_session'
    assert execution['thread']['sandbox'] == 'read-only'
    assert 'web_search="live"' in execution['args']
    assert 'image_number' in execution['prompt'] and '概念核心内容' in execution['prompt']
    again = run_pipeline(cfg)
    assert json.loads(rows(again['designs'])[0]['call_json'])['reused']
    assert len(capture.read_text().splitlines()) == 1


def test_text_material_numbers_do_not_shift_pixel_numbers(materials, fake_cli):
    from preparation.articles.operators.article import ARTICLES
    uri = resolve_root() / 'demiwtg/preparation/articles/datasets/articles.lance'
    article = {'article_id': 'fixture-article', 'concept': '流程图', 'review_status': 'reviewed', 'content': [
        {'title': '节点约定', 'content': {'paragraphs': ['菱形表示条件判断，矩形表示处理步骤。']}}]}
    data.from_arrow(pa.Table.from_pylist([article], schema=ARTICLES)).write_lance(str(uri), mode='overwrite', schema=ARTICLES)
    cli, capture = fake_cli
    state = run_pipeline(settings(materials, codex_bin=cli,
        article_source={'uri': str(uri), 'version': 1}))
    assert state['complete']
    refs = json.loads(rows(state['inputs'])[0]['references_json'])
    assert [r['number'] for r in refs] == [1, 2]
    assert rows(state['candidates'])[0]['seed_image'] == 1
    assert '菱形表示条件判断' in json.loads(capture.read_text())['prompt']


def test_failed_rerun_cannot_reuse_previous_success_summary(materials, fake_cli, monkeypatch):
    cli, _ = fake_cli
    cfg = settings(materials, codex_bin=cli)
    assert run_pipeline(cfg)['complete']
    def fail(_):
        raise OSError('fixture unreadable seed')
    monkeypatch.setattr('benchmark.edit.v2.operators.authoring.image_data_url', fail)
    with pytest.raises(OSError, match='unreadable seed'):
        run_pipeline(cfg)
    summary_uri = resolve_root() / DATASETS / 'summary__test.lance'
    saved = data.read_lance(str(summary_uri), version=lance.dataset(str(summary_uri)).version).take(1)[0]
    assert saved['status'] == 'failed' and not saved['complete']
    assert saved['candidates'] is None and saved['designs'] is None
    assert 'unreadable seed' in saved['error']


def test_initial_scene_image_cannot_be_presented_as_a_positive(materials):
    from benchmark.edit.v2.operators.authoring import prepare_request
    row = {'concept': '流程图', 'taxonomy': [], 'status': 'ready', 'references_json': json.dumps([
        {'kind': 'image', 'role': 'source_candidate', 'object_ref': materials[1]}])}
    with pytest.raises(ValueError, match='Initial Edit images must be concept positives'):
        prepare_request(row, max_context_chars=60000, prompt_chars=10,
                        generate_source=False, max_generation_attempts=2)


def constructed_question():
    q = question()
    q.update(source_image_edit='删除需要测试的节点，保留两条连接线和其他上下文。', source_artifact='source.png')
    return q


@pytest.mark.parametrize('write_mode', ['append', 'overwrite'])
def test_codex_constructs_checks_delivers_and_resumes_real_image_bytes(materials, fake_cli, monkeypatch, write_mode):
    cli, capture = fake_cli
    monkeypatch.setenv('EDIT_QUESTION', json.dumps(constructed_question()))
    monkeypatch.setenv('EDIT_ARTIFACT', 'generated')
    cfg = settings(materials, codex_bin=cli, generate_source=True, write_mode=write_mode)
    first = run_pipeline(cfg)
    assert first['complete'] and first['counts'] == {'candidate': 1}
    candidate = rows(first['candidates'])[0]
    assert candidate['status'] == 'unreviewed' and candidate['seed_asset'] == materials[1]
    assert candidate['edit_source']['sha256'] != materials[1]['sha256']
    pixels = ObjectRef(**candidate['edit_source']).read()
    with Image.open(io.BytesIO(pixels)) as image:
        assert image.getpixel((0, 0)) == (255, 0, 0)
    call = json.loads(candidate['call_json'])
    assert call['artifacts'][0]['object_ref'] == candidate['edit_source']
    execution = json.loads(capture.read_text())
    context = json.loads(execution['prompt'].rsplit('CODEX_EXEC_FILE_CONTEXT\n', 1)[1])
    assert not Path(context['artifact_directory']).exists()
    assert 'referenced_image_paths' in execution['prompt']
    assert '最多遵守 execution_context.max_generation_attempts 次' in execution['prompt']
    again = run_pipeline(cfg)
    assert len(rows(again['candidates'])) == 1
    repeated = rows(again['candidates'])[0]
    assert repeated['task_id'] == candidate['task_id'] and repeated['edit_source'] == candidate['edit_source']
    assert json.loads(rows(again['designs'])[0]['call_json'])['reused']
    assert len(capture.read_text().splitlines()) == 1
    assert ObjectRef(**repeated['edit_source']).read() == pixels


@pytest.mark.parametrize('kind', ['missing', 'invalid', 'identical', 'unbound_filename'])
def test_generated_source_must_be_a_real_bound_image(materials, fake_cli, monkeypatch, kind):
    cli, _ = fake_cli
    q = constructed_question()
    if kind == 'unbound_filename': q['source_artifact'] = '../../outside.png'
    monkeypatch.setenv('EDIT_QUESTION', json.dumps(q))
    monkeypatch.setenv('EDIT_ARTIFACT', 'generated' if kind == 'unbound_filename' else kind)
    state = run_pipeline(settings(materials, codex_bin=cli, generate_source=True))
    assert state['counts'] == {'invalid_response': 1}
    assert not state['complete'] and not rows(state['candidates'])


@pytest.mark.parametrize('obtained_image', [True, False])
def test_generation_or_visual_check_failure_keeps_reason_and_available_image(materials, fake_cli, monkeypatch, obtained_image):
    cli, _ = fake_cli
    q = constructed_question()
    if obtained_image:
        q['source_image_check'] = {'status': 'failed', 'observations': [
            {'check': 1, 'passed': False, 'evidence': '关键连接线也被删除，缺少可解线索'}], 'reason': '原图构造破坏必要上下文'}
    else:
        q.update(source_artifact=None, source_image_check={'status': 'not_run', 'observations': [], 'reason': '图像工具调用失败，未返回图片'})
    monkeypatch.setenv('EDIT_QUESTION', json.dumps(q))
    monkeypatch.setenv('EDIT_ARTIFACT', 'generated' if obtained_image else 'missing')
    state = run_pipeline(settings(materials, codex_bin=cli, generate_source=True))
    assert state['counts'] == {'source_check_failed': 1} and not rows(state['candidates'])
    design = rows(state['designs'])[0]
    assert design['question'] == {**q, 'seed_image_id': None} and design['reason'] == q['source_image_check']['reason']
    assert (design['edit_source'] is not None) == obtained_image


def test_artifact_delivery_limit_is_a_technical_failure(materials, fake_cli, monkeypatch):
    cli, _ = fake_cli
    monkeypatch.setenv('EDIT_QUESTION', json.dumps(constructed_question()))
    monkeypatch.setenv('EDIT_ARTIFACT', 'generated')
    state = run_pipeline(settings(materials, codex_bin=cli, generate_source=True, max_artifact_bytes=16))
    assert state['counts'] == {'failed': 1} and not rows(state['candidates'])
    call = json.loads(rows(state['designs'])[0]['call_json'])
    assert call['execution_status'] == 'artifact_failed'


def test_direct_seed_still_works_when_generation_is_enabled(materials, fake_cli):
    cli, capture = fake_cli
    state = run_pipeline(settings(materials, codex_bin=cli, generate_source=True))
    assert state['complete'] and rows(state['candidates'])[0]['edit_source'] == materials[1]
    assert json.loads(rows(state['designs'])[0]['call_json'])['artifacts'] == []
    assert len(capture.read_text().splitlines()) == 1


def test_codex_retrieves_sees_and_binds_scene_without_initial_images(materials, fake_cli, monkeypatch):
    from dataclasses import asdict
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import threading
    from demiflow.embeddings import EmbeddingModel
    from demiflow.embeddings.model import canonical
    received = []
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            received.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"data":[{"index":0,"embedding":[3,4,0]}]}')
        def log_message(self, *args):
            pass
    http = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    worker = threading.Thread(target=http.serve_forever, daemon=True)
    worker.start()
    try:
        cli, capture = fake_cli
        cfg = settings(materials, codex_bin=cli, visual_source=None)
        path = Path(cfg['agent_config'])
        spec = yaml.safe_load(path.read_text())
        model = EmbeddingModel('fixture', 'fixed-revision', 3, f'http://127.0.0.1:{http.server_port}/v1')
        uri = str(resolve_root() / 'scene_vectors.lance')
        image = materials[1]
        schema = pa.schema([('sha256', pa.string()), ('image_uri', pa.string()), ('embedding', pa.list_(pa.float32(), 3))],
            metadata={b'embedding.contract': canonical(model.contract()).encode()})
        lance.write_dataset(pa.Table.from_pylist([{'sha256': image['sha256'], 'image_uri': image['uri'],
            'embedding': [.6, .8, 0.]}], schema=schema), uri)
        directory = str(resolve_root() / 'query_vectors')
        spec['operator_settings']['map_embeddings']['arguments'] = {'model': asdict(model), 'object_directory': directory}
        spec['operator_settings']['search_vectors']['arguments'] = {'uri': uri, 'version': 1,
            'vector_column': 'embedding', 'columns': ['sha256', 'image_uri'], 'encoder_id': model.fingerprint,
            'object_directory': directory, 'contract_metadata_key': 'embedding.contract', 'options': {'use_index': False}}
        path.write_text(yaml.safe_dump(spec, allow_unicode=True, sort_keys=False))
        cfg = config(run=cfg['run'], concepts=['流程图'], agent_config=path)
        monkeypatch.setenv('EDIT_RETRIEVAL', '1')
        state = run_pipeline(cfg)
        assert state['complete'] and state['counts'] == {'candidate': 1}
        candidate, = rows(state['candidates'])
        assert candidate['seed_image'] is None and candidate['seed_image_id'] == 'sha256:' + image['sha256']
        assert candidate['seed_asset'] == candidate['edit_source'] == image
        prompt = json.loads(capture.read_text())['prompt']
        assert uri in prompt and 'fixed_arguments' in prompt and 'query_ref' in prompt
        assert rows(state['inputs'])[0]['references_json'] == '[]'
        assert run_pipeline(cfg)['complete']
        assert len(received) == 1 and len(capture.read_text().splitlines()) == 1
    finally:
        http.shutdown()
        http.server_close()
        worker.join()


@pytest.mark.parametrize('status, image_id', [('not_attached_budget', 'correct'), ('attached', 'forged')])
def test_tool_seed_requires_actual_attachment(materials, status, image_id):
    from demiflow.agent import load_agent_config
    from benchmark.edit.v2.operators.authoring import check_response
    schema = load_agent_config(PROMPTS / 'agent_codex.yaml').prompt_pack.prompt_definitions['design_question'].response_schema
    image = materials[1]
    q = question()
    q.update(seed_image=None, seed_image_id='sha256:' + (image['sha256'] if image_id == 'correct' else '0' * 64))
    result = check_response({'status': 'ready', 'references_json': '[]', 'design_result': {'question': q},
        'design_call': {'transport': 'codex_app_server', 'environment': {'observations': [{'images': [
            {'status': status, 'image_id': 'sha256:' + image['sha256'], 'object_ref': image}]}]}}},
        question_schema=schema['properties']['result']['properties']['question'], generate_source=False)
    assert result['status'] == 'invalid_response' and 'actually attached' in result['reason']


def test_agent_config_is_the_only_codex_execution_entry(materials):
    base = {'run': resolve_root() / DATASETS / 'test', 'concepts': ['流程图']}
    with pytest.raises(ValueError, match='requires agent_config'):
        config(**base)
    for legacy in ({'mode': 'offline'}, {'model': 'other'}, {'max_context_chars': 123}):
        with pytest.raises(TypeError, match='unexpected keyword'):
            config(**base, agent_config=PROMPTS / 'agent_codex.yaml', **legacy)
    with pytest.raises(ValueError, match='only tighten'):
        config(**base, agent_config=PROMPTS / 'agent_codex.yaml', max_calls=301)


def test_codex_reads_local_document_without_a_callback_or_inline_body(materials, fake_cli, monkeypatch):
    from benchmark.edit.v2.operators.authoring import prepare_request
    content = 'DOCUMENT_BODY_ONLY_ON_DISK'
    ref = LocalObjectStore(resolve_root() / '依据 文档').put(content.encode()).to_dict()
    document = {'document_ref': ref, 'url': 'https://example.org/fixed-source'}
    row = prepare_request({'concept': '流程图', 'taxonomy': [], 'status': 'ready',
        'references_json': json.dumps([{'number': 1, 'kind': 'document', 'document_id': 'D1', 'document': document}])},
        max_context_chars=60000, prompt_chars=10, generate_source=False, max_generation_attempts=2)
    assert 'document_resources' not in row
    supplied = row['evidence_materials']['documents']['D1']
    assert supplied['url'] == document['url'] and supplied['sha256'] == ref['sha256']
    assert Path(supplied['local_path']).is_absolute()
    assert Path(supplied['local_path']).read_text() == content
    assert row['prompt_images'] == []
    cli, capture = fake_cli
    monkeypatch.setenv('EDIT_EXPECT_DOCUMENT', content)
    state = run_pipeline(settings(materials, codex_bin=cli,
        document_resources={'流程图': {'D1': document}}))
    assert state['complete'] and state['counts'] == {'candidate': 1}
    assert content not in json.loads(capture.read_text())['prompt']
