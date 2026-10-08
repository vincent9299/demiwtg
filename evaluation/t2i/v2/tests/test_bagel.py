"""BAGEL 服务与标准算子集成；模拟官方 inferencer，不加载 GPU 或请求真实判官。"""
import base64
import io
import json
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest
from PIL import Image
from fastapi.testclient import TestClient

from demiflow import data
from demiflow.objects import LocalObjectStore
from demiflow.execution.file_ref import JsonArtifactRef
from evaluation.t2i.case_annotation.tests.test_generation_models import service
from evaluation.t2i.v2.tests.test_pipeline import source
from evaluation.t2i.v2.tests.test_paired import question
from evaluation.t2i.v2.t2i_v2_eval_pipeline import config, DATASETS, run_paired_arm
from project import resolve_root


def png(color):
    stream = io.BytesIO()
    Image.new('RGB', (8, 4), color).save(stream, format='PNG')
    return stream.getvalue()


@pytest.fixture
def bagel(service, tmp_path):
    service.STATE['args'] = service.parse_args(['--backend', 'bagel', '--offload-dir', str(tmp_path)])
    calls = []
    class Inferencer:
        def interleave_inference(self, inputs, **kwargs):
            calls.append((inputs[0], [im.getpixel((0, 0)) for im in inputs[1:]], kwargs))
            assert kwargs['think'] is False and kwargs['understanding_output'] is False
            return [Image.new('RGB', (1024, 1024), 'white')]
    service.STATE['pipe'] = Inferencer()
    service.bagel_calls = calls
    return service


def test_parallel_bagel_inputs_remain_text_then_ordered_images(bagel):
    client = TestClient(bagel.app)
    refs = ['data:image/png;base64,' + base64.b64encode(png(c)).decode() for c in ('red', 'blue')]
    def send(mode):
        body = {'model': 'BAGEL-7B-MoT', 'prompt': '  原始题面\n' + mode, 'seed': 42}
        if mode == 'ref':
            body['image'] = refs
        return client.post('/v1/images/edits' if mode == 'ref' else '/v1/images/generations', json=body)
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(send, ['text', 'ref']))
    assert [r.status_code for r in responses] == [200, 200]
    observed = {text: colors for text, colors, _ in bagel.bagel_calls}
    assert observed == {'  原始题面\ntext': [], '  原始题面\nref': [(255, 0, 0), (0, 0, 255)]}
    assert all(options['num_timesteps'] == 50 for _, _, options in bagel.bagel_calls)
    assert len(responses[1].json()['usage']['source_sha256s']) == 2


@pytest.mark.parametrize('change', [{'size': '512x512'}, {'num_inference_steps': 0},
                                     {'think': True}, {'model': 'other'}])
def test_unsupported_bagel_request_never_silently_changes_protocol(bagel, change):
    r = TestClient(bagel.app).post('/v1/images/generations', json={'prompt': '题面', **change})
    assert r.status_code == 400 and not bagel.bagel_calls


def test_bagel_native_pipeline_saves_actual_prompt_and_images(bagel, monkeypatch):
    local = TestClient(bagel.app)
    original = httpx.Client
    def respond(request):
        response = local.post(request.url.path, json=json.loads(request.content))
        return httpx.Response(response.status_code, json=response.json())
    monkeypatch.setattr(httpx, 'Client', lambda **kw: original(transport=httpx.MockTransport(respond), **kw))
    root = resolve_root()
    refs = [LocalObjectStore(root / 'objects').put(png(c)).to_dict() for c in ('red', 'blue')]
    q = {**question(), 'instruction': '  原始题目，不应被修改。\n',
         'authoring_images_json': json.dumps([{'kind': 'image', 'object_ref': ref} for ref in refs])}
    cfg = config(answer_models=[{
        'backend': 'modelhub', 'model': 'BAGEL-7B-MoT', 'api': 'images_json',
        'base_url': 'http://bagel.test/v1', 'revision': 'fixture', 'answer_mode': mode,
        'parameters': {'num_inference_steps': 50, 'size': '1024x1024', 'seed': 42},
    } for mode in ('text_only', 'positive_images')],
        judge_model={'model': 'fixture-judge', 'mode': 'offline'}, stream_judging=True)
    manifest = {'source': source([q]), 'config': cfg, 'expected_questions': 1}
    for i in range(2):
        state = run_paired_arm(root / DATASETS / 'bagel_contract', manifest, i)
        row = data.read_lance(**state['outputs']['answers']).take(1)[0]
        assert row['status'] == 'generated'
        call = json.loads(row['answer_call_json'])
        saved = JsonArtifactRef(**call['input_ref']).read()
        body = saved['body']
        assert body['prompt'] == bagel.bagel_calls[i][0]
        assert body['prompt'].startswith(q['instruction'])
        assert ('参考图：' in body['prompt']) == bool(i)
        images = body.get('image', [])
        assert len(images) == 2 * i
        colors = []
        for url in images:
            with Image.open(io.BytesIO(base64.b64decode(url.split(',', 1)[1]))) as im:
                colors.append(im.getpixel((0, 0)))
        assert colors == bagel.bagel_calls[i][1]
        assert set(state['outputs']) == {'answers', 'scores_a', 'scores_b'}
    # 重入同一正式运行必须复用原调用，不能再次出图。
    run_paired_arm(root / DATASETS / 'bagel_contract', manifest, 1)
    assert len(bagel.bagel_calls) == 2
