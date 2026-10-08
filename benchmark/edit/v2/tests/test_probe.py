"""Real pixel/request and Dataset boundaries, with no GPU or paid model calls."""
import asyncio
import base64
import hashlib
import importlib.util
import io
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import lance
from PIL import Image
import pytest

from demiflow.agent import load_agent_config
from demiflow.image_edit import edit_image
from demiflow.objects import LocalObjectStore
from demiflow.operator_llm.parser import load_prompt_pack
from demiflow.operator_llm.template import render_template
from project import resolve_root
from benchmark.edit.v2.edit_v2_benchmark_pipeline import PROMPTS, run_pipeline
from benchmark.edit.v2.operators.probe import prepare_review, check_review, probe_config
from benchmark.edit.v2.operators.authoring import prepare_request, check_response
from benchmark.edit.v2.tests.test_pipeline import materials, fake_cli, settings, rows, question
from demiflow import data


def png(color='red', size=(256, 256)):
    out = io.BytesIO()
    Image.new('RGB', size, color).save(out, format='PNG')
    return out.getvalue()


def review():
    return {'verdict': 'pass', 'score': 9, 'reason': '节点已修改且关系保持。',
        'basis': '本题普通处理步骤使用矩形节点。', 'failure_type': 'none',
        'point_results': [{'index': 1, 'verdict': 'pass', 'evidence': '结果节点为矩形，前图为菱形。', 'reason': '改变符合要求。'}],
        'case_annotation': {'knowledge_level': 'direct_reference', 'common_cn': 'yes',
            'visual_support': 'strong', 'confidence': 'high', 'reason': '可以按明确符号约定核对。',
            'caveat': '', 'sources': []}}


def test_author_prompt_prefix_and_new_contract():
    agent = load_agent_config(PROMPTS / 'agent_codex.yaml')
    d = agent.prompt_pack.prompt_definitions['design_question']
    first = d.template.placeholders[0].start
    assert d.template.source.index('## 9. 最终输出') < first
    assert '核心判断' in d.template.source and '补充边界' in d.template.source
    parts = [render_template(d.template, {'api_configuration': [], 'concept_material': {'concept': c},
             'evidence_materials': {'excerpts': [], 'documents': {}}, 'positive_examples': [],
             'execution_context': {'generate_source': False, 'max_generation_attempts': 2}, 'images': []})
             for c in ['流程图', '{{ literal_not_a_template }}']]
    assert parts[0][0].text[:first] == parts[1][0].text[:first]
    assert '{{ literal_not_a_template }}' in parts[1][0].text
    schema = d.response_schema['properties']['result']['properties']['question']
    assert 'criterion' in schema['properties']['test_points']['items']['required']
    assert '"criterion"' in d.template.source


def test_author_materials_render_without_references_and_keep_image_binding():
    store = LocalObjectStore(resolve_root() / 'objects')
    positive_one, positive_two = [store.put(png(color)).to_dict() for color in ('red', 'blue')]
    document = {'document_ref': store.put(b'fixed document').to_dict(),
                'url': 'https://example.org/document', 'bindings': ['concept'], 'eligible': True}
    pack = load_agent_config(PROMPTS / 'agent_codex.yaml').prompt_pack
    definition = pack.prompt_definitions['design_question']
    row = {'concept': '流程图', 'taxonomy': ['图形/流程图', '技术/图表/流程图'], 'status': 'ready',
           'references_json': json.dumps([
               {'number': 1, 'kind': 'text', 'title': '节点约定', 'text': '{{ literal_content }}'},
               {'number': 2, 'kind': 'image', 'role': 'concept_reference', 'object_ref': positive_one},
               {'number': 3, 'kind': 'document', 'document_id': 'D1', 'document': document},
               {'number': 4, 'kind': 'image', 'role': 'concept_reference', 'object_ref': positive_two}])}
    prepared = prepare_request(row, max_context_chars=60000, prompt_chars=len(definition.template.source),
                               generate_source=False, max_generation_attempts=2)
    values = {name: prepared['prompt_images' if name == 'images' else name]
              for name in definition.template.arguments}
    parts = render_template(definition.template, values)
    text = ''.join(part.text for part in parts if hasattr(part, 'text'))
    assert 'references' not in text and 'concept_reference' not in text
    assert '{{ literal_content }}' in text
    assert prepared['concept_material']['taxonomy'] == row['taxonomy']
    supplied = prepared['evidence_materials']['documents']['D1']
    assert supplied['sha256'] == document['document_ref']['sha256']
    assert supplied['url'] == document['url']
    assert Path(supplied['local_path']).read_bytes() == b'fixed document'
    assert 'fixed document' not in text and 'document_ref' not in text
    assert prepared['evidence_materials']['excerpts'][0]['id'] == 'A1'
    assert prepared['positive_examples'] == [{'image_number': 1}, {'image_number': 2}]
    assert 'source_candidates' not in prepared['execution_context']
    pixels = [base64.b64decode(part.image.uri.split(',', 1)[1])
              for part in parts if hasattr(part, 'image')]
    assert [hashlib.sha256(raw).hexdigest() for raw in pixels] == [positive_one['sha256'], positive_two['sha256']]
    schema = definition.response_schema['properties']['result']['properties']['question']
    for number, expected in [(1, positive_one), (2, positive_two)]:
        value = {**question(), 'seed_image': number}
        checked = check_response({**prepared, 'design_result': {'question': value}},
                                 question_schema=schema, generate_source=False)
        assert checked['status'] == 'candidate'
        assert checked['edit_source'] == expected
    blocked = prepare_request(row, max_context_chars=len(definition.template.source),
                              prompt_chars=len(definition.template.source),
                              generate_source=False, max_generation_attempts=2)
    assert blocked['status'] == 'needs_context_budget' and blocked['prompt_images'] == []


def test_review_images_order_and_no_authoring_leak(materials):
    ref = LocalObjectStore(resolve_root() / 'objects').put(png()).to_dict()
    row = {'concept': '流程图', 'taxonomy': [], **question(), 'status': 'generated', 'reason': '',
        'edit_source': materials[1], 'object_ref': ref, 'model': 'SECRET_MODEL',
        'source_image_edit': 'SECRET_CONSTRUCTION', 'reasoning': 'SECRET_REASONING'}
    prepared = prepare_review(row, max_context_chars=60000, prompt_chars=4000)
    actual = [base64.b64decode(url.split(',', 1)[1]) for url in prepared['review_images']]
    assert [hashlib.sha256(raw).hexdigest() for raw in actual] == [materials[1]['sha256'], ref['sha256']]
    assert 'SECRET' not in json.dumps(prepared['review_payload'])
    definition = load_prompt_pack(PROMPTS / 'review.yaml').prompt_definitions['review_answer']
    first = definition.template.placeholders[0].start
    assert definition.template.source.index('只返回符合 schema 的 JSON') < first
    parts = render_template(definition.template,
                            {'payload': prepared['review_payload'], 'images': prepared['review_images']})
    changed = render_template(definition.template,
                              {'payload': {**prepared['review_payload'], 'concept': '{{ literal_concept }}'},
                               'images': prepared['review_images']})
    assert parts[0].text[:first] == changed[0].text[:first]
    assert '{{ literal_concept }}' in changed[0].text
    assert 'SECRET' not in ''.join(p.text for p in parts if hasattr(p, 'text'))
    assert [p.image.uri for p in parts if hasattr(p, 'image')] == prepared['review_images']
    assert prepare_review(row, max_context_chars=1, prompt_chars=4000)['review_status'] == 'needs_context_budget'


@pytest.mark.parametrize('change,expected', [
    ('none', 'reviewed'), ('missing', 'invalid_response'), ('duplicate', 'invalid_response'),
    ('bad_score', 'invalid_response'), ('uncertain', 'reviewed'), ('bad_criterion', 'reviewed'),
])
def test_review_contract_and_unscored_sentinel(change, expected):
    value = review()
    if change == 'missing': value['point_results'] = []
    if change == 'duplicate': value['point_results'] *= 2
    if change == 'bad_score': value['score'] = 2
    if change in {'uncertain', 'bad_criterion'}:
        value.update(verdict='inconclusive', score=-1, failure_type='insufficient_evidence')
        value['point_results'][0]['verdict'] = 'inconclusive' if change == 'uncertain' else 'invalid_criterion'
    schema = load_prompt_pack(PROMPTS / 'review.yaml').prompt_definitions['review_answer'].response_schema
    result = check_review({'test_points': question()['test_points'], 'review_status': 'ready',
        'review_reason': '', 'review_result': value}, response_schema=schema)
    assert result['review_status'] == expected
    if change in {'uncertain', 'bad_criterion'}: assert result['review']['score'] == -1


def test_qwen_local_service_receives_pixels_and_rejects_text_only_backend():
    from fastapi.testclient import TestClient
    path = Path(__file__).resolve().parents[5] / 'models/serve_z_image.py'
    spec = importlib.util.spec_from_file_location('edit_local_service_test', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    calls = []

    class FakePipeline:
        def __call__(self, **kw):
            calls.append({**kw, 'pixels': kw['image'].getpixel((0, 0))})
            return SimpleNamespace(images=[Image.new('RGB', (kw['width'], kw['height']))])

    module.STATE.update(args=module.parse_args(['--backend', 'qwen-image-2.1']), pipe=FakePipeline())
    client = TestClient(module.app)
    raw = png('blue')
    body = {'model': 'Qwen-Image-2.1', 'prompt': 'change', 'size': '256x256', 'seed': 7,
        'image': 'data:image/png;base64,' + base64.b64encode(raw).decode()}
    response = client.post('/v1/images/edits', json=body)
    assert response.status_code == 200, response.text
    assert calls[0]['pixels'] == (0, 0, 255)
    assert response.json()['usage']['source_sha256'] == hashlib.sha256(raw).hexdigest()
    assert not {'max_sequence_length', 'guidance_scale'} & calls[0].keys()
    assert client.post('/v1/images/generations', json=body).status_code == 400
    assert client.post('/v1/images/edits', json={**body, 'image': 'file:///etc/passwd'}).status_code == 400
    module.STATE['args'] = module.parse_args(['--backend', 'z-image'])
    assert client.post('/v1/images/edits', json=body).status_code == 400
    assert len(calls) == 1


@pytest.mark.parametrize('failure', [False, True])
def test_probe_pipeline_real_images_and_cached_calls(materials, fake_cli, monkeypatch, failure):
    cli, capture = fake_cli
    cfg = settings(materials, codex_bin=str(cli), through='probe',
        probe={'revision': 'fixture-edit-v1', 'image_size': '256x256'})
    received = []
    original_client = httpx.AsyncClient
    monkeypatch.setenv('MODELHUB_API_KEY', 'fixture')

    def handle(request):
        if request.method == 'GET':
            return httpx.Response(200, json={'data': [{'id': 'malasci/gpt-6-astra'}]})
        body = json.loads(request.content)
        received.append((request.url.path, body))
        if request.url.path.endswith('/images/edits'):
            assert set(body) == {'model','prompt','size','n','response_format','num_inference_steps','seed','image'}
            assert body['prompt'] == question()['instruction']
            raw = base64.b64decode(body['image'].split(',', 1)[1])
            assert hashlib.sha256(raw).hexdigest() == materials[1]['sha256']
            if failure: return httpx.Response(503, json={'error': 'unavailable'})
            return httpx.Response(200, json={'model': body['model'], 'data': [{'b64_json': base64.b64encode(png()).decode()}],
                'usage': {'seed': body['seed'], 'steps': body['num_inference_steps'], 'source_sha256': materials[1]['sha256']}})
        assert request.url.path.endswith('/chat/completions')
        assert body['model'] == 'malasci/gpt-6-astra' and body['reasoning_effort'] == 'xhigh'
        parts = [p for m in body['messages'] if isinstance(m['content'], list)
                 for p in m['content'] if p['type'] == 'image_url']
        assert len(parts) == 2
        shas = [hashlib.sha256(base64.b64decode(p['image_url']['url'].split(',', 1)[1])).hexdigest() for p in parts]
        assert shas == [materials[1]['sha256'], hashlib.sha256(png()).hexdigest()]
        return httpx.Response(200, json={'model': body['model'], 'choices': [
            {'message': {'role': 'assistant', 'content': json.dumps({'result': review()})}, 'finish_reason': 'stop'}],
            'usage': {'prompt_tokens': 10, 'completion_tokens': 10, 'total_tokens': 20}})

    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kw: original_client(**kw, transport=httpx.MockTransport(handle)))
    state = run_pipeline(cfg)
    assert state['complete'] is not failure
    assert len(rows(state['candidates'])) == len(rows(state['generations'])) == len(rows(state['reviews'])) == 1
    result = rows(state['reviews'])[0]
    assert result['review_status'] == ('generation_failed' if failure else 'reviewed')
    if failure: assert result['review'] is None
    before = len(received)
    again = run_pipeline(cfg)
    assert again['complete'] == state['complete'] and len(received) == before
    assert len(capture.read_text().splitlines()) == 1


def test_probe_requires_editing_model_and_deployment_identity():
    assert probe_config({'revision': 'fixture'}, maximum=1)['review_reasoning_effort'] == 'xhigh'
    with pytest.raises(ValueError, match='deployment revision'): probe_config({}, maximum=1)
    with pytest.raises(ValueError, match='editing deployment'):
        probe_config({'revision': 'fixture', 'model': 'Z-Image-Turbo'}, maximum=1)


@pytest.mark.parametrize('bad', ['source_receipt', 'bytes', 'pixels', 'model'])
def test_native_image_edit_rejects_unverified_response(materials, monkeypatch, bad):
    original_client = httpx.AsyncClient
    raw = png()

    def handler(request):
        body = {'model': 'wrong' if bad == 'model' else 'Qwen-Image-2.1',
            'data': [{'b64_json': base64.b64encode(b'bad pixels' if bad == 'pixels' else raw).decode()}],
            'usage': {'seed': 7, 'steps': 40, 'source_sha256': '0'*64 if bad == 'source_receipt' else materials[1]['sha256']}}
        if bad == 'bytes': body['extra'] = 'x' * 140000
        return httpx.Response(200, json=body)

    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kw: original_client(**kw, transport=httpx.MockTransport(handler)))
    result = asyncio.run(edit_image(source=materials[1], instruction='edit', model='Qwen-Image-2.1',
        revision='fixture', base_url='http://127.0.0.1:8005/v1', size='256x256', steps=40,
        seed=7, object_store=LocalObjectStore(resolve_root() / 'objects'), max_image_bytes=2048))
    assert result['status'] == 'failed' and result['object_ref'] is None
    assert 'data:image' not in result['call_json'] and base64.b64encode(raw).decode() not in result['call_json']


def test_probe_advances_before_second_author_finishes(materials, fake_cli, monkeypatch, tmp_path):
    """Second Codex turn waits for the first answer: whole-batch authoring would time out."""
    cli, _ = fake_cli
    marker = tmp_path / 'first_answer'
    monkeypatch.setenv('EDIT_FIRST_ANSWER', str(marker))
    cli.write_text(cli.read_text().replace("question = json.loads(os.environ['EDIT_QUESTION'])", """
if len(pathlib.Path(os.environ['EDIT_CAPTURE']).read_text().splitlines()) == 2:
    deadline = time.monotonic() + 10
    while not pathlib.Path(os.environ['EDIT_FIRST_ANSWER']).exists():
        assert time.monotonic() < deadline, 'answer did not start while author was pending'
        time.sleep(.05)
question = json.loads(os.environ['EDIT_QUESTION'])
"""))
    uri = resolve_root() / 'fixtures/positive_images.lance'
    positives = rows(materials[0])[0]
    names = ['流程图', '流程图B']
    positives.update(published_concepts=names,
        concept_assessments=[{'concept': name, 'published': True, 'review_status': 'keep'} for name in names])
    source_schema = lance.dataset(materials[0]['uri'], version=materials[0]['version']).schema
    data.from_items([positives]).write_lance(str(uri), mode='overwrite', schema=source_schema)
    cfg = settings(materials, codex_bin=str(cli), concepts=names,
        visual_source={'uri': str(uri), 'version': 1}, through='probe',
        probe={'revision': 'fixture-edit-v1', 'image_size': '256x256'})
    original_client = httpx.AsyncClient
    monkeypatch.setenv('MODELHUB_API_KEY', 'fixture')

    def handler(request):
        if request.method == 'GET': return httpx.Response(200, json={'data': [{'id':'malasci/gpt-6-astra'}]})
        body = json.loads(request.content)
        if request.url.path.endswith('/images/edits'):
            marker.touch()
            return httpx.Response(200, json={'model': body['model'], 'data': [{'b64_json':base64.b64encode(png()).decode()}],
                'usage': {'seed':body['seed'], 'steps':40, 'source_sha256':materials[1]['sha256']}})
        return httpx.Response(200, json={'model':body['model'], 'choices':[{'message':{'content':json.dumps({'result':review()})},
            'finish_reason':'stop'}]})

    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kw: original_client(**kw, transport=httpx.MockTransport(handler)))
    state = run_pipeline(cfg)
    assert state['complete'] and state['candidate_count'] == 2
    assert len(rows(state['reviews'])) == 2 and marker.exists()


def test_all_insufficient_preserves_empty_stages_without_probe_requests(materials, fake_cli, monkeypatch):
    cli, _ = fake_cli
    cli.write_text(cli.read_text().replace("{'question': question}", "{'question': None, 'reason': '当前条件无法构造可靠题目'}"))
    monkeypatch.setenv('MODELHUB_API_KEY', 'fixture')
    original_client = httpx.AsyncClient

    def forbidden(request):
        raise AssertionError('No candidates: no image/review service request is allowed')

    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kw: original_client(**kw, transport=httpx.MockTransport(forbidden)))
    state = run_pipeline(settings(materials, codex_bin=str(cli), through='probe',
        probe={'revision': 'fixture-edit-v1'}))
    assert state['complete'] and state['counts'] == {'insufficient': 1}
    assert not rows(state['candidates']) and not rows(state['generations']) and not rows(state['reviews'])


def test_image_storage_failure_stops_instead_of_scoring(materials, monkeypatch):
    original_client = httpx.AsyncClient

    def handler(request):
        return httpx.Response(200, json={'model':'Qwen-Image-2.1',
            'data':[{'b64_json':base64.b64encode(png()).decode()}],
            'usage':{'seed':7,'steps':40,'source_sha256':materials[1]['sha256']}})

    class FullStore:
        def put(self, raw): raise OSError('disk full')

    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kw: original_client(**kw, transport=httpx.MockTransport(handler)))
    with pytest.raises(OSError, match='disk full'):
        asyncio.run(edit_image(source=materials[1], instruction='edit', model='Qwen-Image-2.1',
            revision='fixture', base_url='http://127.0.0.1:8005/v1', size='256x256', steps=40,
            seed=7, object_store=FullStore()))
