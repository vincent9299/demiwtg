"""验证真实 demiflow/Lance 执行链；图像生成和 judge 响应使用模拟数据。"""
import ast
import base64
import io
import json
import sys
from types import SimpleNamespace
from pathlib import Path

import lance
import pyarrow as pa
from PIL import Image
import pytest
import importlib
import yaml
from demiflow.errors import LanceWriteError
from demiflow.lance.blobs import BlobRef, LanceBlobStore
from demiflow.lance.records import LanceRecordStore
from demiflow.operator_llm.lance_journal import submit_response
from demiflow import data
from evaluation.t2i.v2.t2i_v2_eval_pipeline import DATASETS, PROMPTS, SCORES, config, run_pipeline, run_judging
from evaluation.t2i.v2.operaters.answers import GenerateImage
from project import resolve_root


def picture():
    """在内存中创建小图，不触碰真实图片表。"""
    buffer = io.BytesIO()
    Image.new('RGB', (16, 16), 'red').save(buffer, format='PNG')
    return buffer.getvalue()


def rows(ref):
    """按返回的固定版本读取测试结果。"""
    return data.read_lance(ref['uri'], version=ref['version']).take_all()


def settings():
    """答题走模拟 HTTP 边界，judge 走标准离线请求。"""
    return config(answer_models=[{'backend': 'modelhub', 'model': 'fixture-image'}],
                  judge_model={'model': 'fixture-judge', 'mode': 'offline'})


def source(questions=None):
    """写出带有私有考点和材料的题表，以验证 judge 不接收它们。"""
    uri = resolve_root() / 'questions.lance'
    uri.parent.mkdir(parents=True, exist_ok=True)
    questions = questions if questions is not None else [
        {'task_id': 'q1', 'concept': '测试概念', 'instruction': '画一个红色圆形。',
         'status': 'unreviewed', 'test_points': ['PRIVATE_POINT'],
         'criteria': 'PRIVATE_RUBRIC', 'references_json': 'PRIVATE_REFERENCE'}]
    ds = lance.write_dataset(pa.Table.from_pylist(questions), str(uri))
    return {'uri': str(uri), 'version': ds.version}


def judgment():
    """构造带 N/A 的评分，覆盖映射和分母排除规则。"""
    schema = yaml.safe_load((PROMPTS / 'tasks.yaml').read_text())['prompts']['judge']['response_schema']['properties']['result']
    result = {group: {key: '可见证据' if group.endswith('_reasons') else 1 for key in spec['required']}
              for group, spec in schema['properties'].items()}
    result['alignment'].update(subject_presence=0, form_structure=2, style='N/A')
    result['aesthetics'] = {key: 'N/A' for key in result['aesthetics']}
    return result


@pytest.mark.parametrize('separate', [False, True])
def test_roundtrip_input_privacy_scoring_and_resume(monkeypatch, separate):
    calls = []
    def generate(self, instruction, key):
        calls.append(instruction)
        return picture()
    monkeypatch.setattr(GenerateImage, 'remote', generate)
    run, cfg, src = resolve_root() / DATASETS / 'roundtrip', settings(), source()
    answered = run_pipeline(run, src, cfg)
    judge_run = run.with_name(run.name + '_judge')
    output = str(run.parent / 'custom_scores.lance') if separate else answered['target']['uri']
    pending = run_judging(judge_run, answered['target'], cfg, target_uri=output)
    assert answered['counts'] == {'generated': 1}
    assert rows(answered['target'])[0]['judge_json'] is None
    assert pending['counts'] == {'pending_judge': 1}
    answer = rows(answered['target'])[0]
    assert BlobRef(**json.loads(answer['image_json'])).read(resolve_root()) == picture()
    call = json.loads(rows(pending['target'])[0]['judge_call_json'])
    request = LanceRecordStore(**pending['calls']).get(call['request_ref']['key'])
    text = json.dumps(request, ensure_ascii=False)
    assert '画一个红色圆形。' in text and 'image' in text
    assert all(secret not in text for secret in ('PRIVATE_POINT', 'PRIVATE_RUBRIC', 'PRIVATE_REFERENCE', 'fixture-image'))
    submit_response(resolve_root(), call['request_ref'], json.dumps({'result': judgment()}, ensure_ascii=False),
                    model=call['model'], metadata={'reviewer': 'fixture', 'reviewer_kind': 'human'})
    complete = run_judging(judge_run, answered['target'], cfg, target_uri=output)
    row = rows(complete['target'])[0]
    assert complete['target']['uri'] == output
    if separate:
        assert lance.dataset(answered['target']['uri']).version == answered['target']['version']
        assert rows(answered['target'])[0]['status'] == 'generated'
    assert complete['complete'] and complete['counts'] == {'scored': 1}
    assert row['alignment_score'] == 57.78 and row['quality_score'] == 60
    assert row['aesthetics_score'] is None
    assert calls == ['画一个红色圆形。']
    assert run_judging(judge_run, answered['target'], cfg, target_uri=output) == complete
    assert run_pipeline(run, src, cfg) == answered
    with pytest.raises(ValueError, match='new run name'):
        run_judging(judge_run, answered['target'], {**cfg, 'judge': {**cfg['judge'], 'max_output_tokens': 99}}, target_uri=output)


def test_generation_failure_is_not_zero_or_judged(monkeypatch):
    def fail(*args):
        raise RuntimeError('fixture unavailable')
    monkeypatch.setattr(GenerateImage, 'remote', fail)
    answered = run_pipeline(resolve_root() / DATASETS / 'failed', source(), settings())
    state = run_judging(resolve_root() / DATASETS / 'failed_judge', answered['target'], settings(), target_uri=answered['target']['uri'])
    row = rows(state['target'])[0]
    assert row['status'] == 'generation_failed' and 'fixture unavailable' in row['reason']
    assert row['alignment_score'] is None and row['image_json'] is None
    assert not LanceRecordStore(**state['calls']).keys(prefix='request/')


@pytest.mark.parametrize('invalid', [False, True])
def test_judge_append_reuses_unchanged_snapshot(monkeypatch, invalid):
    """追加只提交变化后的结果；待响应、失败和完成状态均可重复查看。"""
    monkeypatch.setattr(GenerateImage, 'remote', lambda *args: picture())
    root, cfg = resolve_root(), settings()
    answered = run_pipeline(root / DATASETS / 'append_answers', source(), cfg)
    judge_run = root / DATASETS / 'append_judge'
    output = str(root / DATASETS / 'append_scores.lance')
    pending = run_judging(judge_run, answered['target'], cfg, target_uri=output, write_mode='append')
    assert run_judging(judge_run, answered['target'], cfg, target_uri=output, write_mode='append') == pending
    assert lance.dataset(output).count_rows() == 1
    call = json.loads(rows(pending['target'])[0]['judge_call_json'])
    response = judgment()
    if invalid:
        response['alignment']['subject_presence'] = 3
    submit_response(root, call['request_ref'], json.dumps({'result': response}),
                    model=call['model'], metadata={'reviewer': 'fixture', 'reviewer_kind': 'human'})
    scored = run_judging(judge_run, answered['target'], cfg, target_uri=output, write_mode='append')
    assert scored['counts'] == {('judge_failed' if invalid else 'scored'): 1}
    assert lance.dataset(output).count_rows() == 2
    assert run_judging(judge_run, answered['target'], cfg, target_uri=output, write_mode='append') == scored
    assert lance.dataset(output).version == scored['target']['version']
    # 追加保留待评分行；更新原行时应选择覆盖。
    assert [row['status'] for row in rows(scored['target'])] == [
        'pending_judge', 'judge_failed' if invalid else 'scored']


def test_bad_judge_score_is_rejected(monkeypatch):
    monkeypatch.setattr(GenerateImage, 'remote', lambda *args: picture())
    run, src, cfg = resolve_root() / DATASETS / 'invalid', source(), settings()
    answered = run_pipeline(run, src, cfg)
    judge_run = run.with_name(run.name + '_judge')
    pending = run_judging(judge_run, answered['target'], cfg, target_uri=answered['target']['uri'])
    call = json.loads(rows(pending['target'])[0]['judge_call_json'])
    response = judgment()
    response['alignment']['subject_presence'] = 3
    submit_response(resolve_root(), call['request_ref'], json.dumps({'result': response}),
                    model=call['model'], metadata={'reviewer': 'fixture', 'reviewer_kind': 'human'})
    state = run_judging(judge_run, answered['target'], cfg, target_uri=answered['target']['uri'])
    assert state['counts'] == {'judge_failed': 1}
    assert rows(state['target'])[0]['alignment_score'] is None
    # 新的 judge 运行从已失败的目标表恢复，只读旧图，不复用上次错误响应。
    monkeypatch.setattr(GenerateImage, 'generate', lambda *args: pytest.fail('Unexpected generation'))
    retry_run = run.with_name('judge_retry')
    retry_cfg = config(judge_model={'model': 'another-judge', 'mode': 'offline'})
    retried = run_judging(retry_run, state['target'], retry_cfg, target_uri=state['target']['uri'])
    assert retried['counts'] == {'pending_judge': 1}
    call = json.loads(rows(retried['target'])[0]['judge_call_json'])
    submit_response(resolve_root(), call['request_ref'], json.dumps({'result': judgment()}),
                    model=call['model'], metadata={'reviewer': 'fixture', 'reviewer_kind': 'human'})
    completed = run_judging(retry_run, state['target'], retry_cfg, target_uri=state['target']['uri'])
    result = rows(completed['target'])[0]
    assert result['status'] == 'scored' and result['judge_model'] == 'another-judge'
    assert result['image_json'] == rows(answered['target'])[0]['image_json']


@pytest.mark.parametrize('api', ['images', 'chat'])
def test_modelhub_exact_model_endpoint_and_single_request(monkeypatch, api):
    """测试两种 API 的请求体和图片解析，不向网关发送请求。"""
    calls = []
    raw = picture()
    encoded = base64.b64encode(raw).decode()
    body = ({'data': [{'b64_json': encoded}]} if api == 'images' else
            {'choices': [{'message': {'images': [{'image_url': {'url': 'data:image/png;base64,' + encoded}}]}}]})
    class Response:
        status_code = 200
        text = json.dumps(body)
        def raise_for_status(self):
            pass
        def json(self):
            return body
    class Session:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def post(self, url, **kwargs):
            calls.append((url, kwargs['json']))
            return Response()
    monkeypatch.setattr('requests.Session', Session)
    cfg = settings()['answers'][0] | {'api': api, 'model': 'openrouter/openai/gpt-image-2'}
    root = resolve_root()
    actor = GenerateImage(cfg, LanceRecordStore(root, 'records.lance'), LanceBlobStore(root, 'images.lance'), root / 'offload')
    assert actor.remote('原始题面', 'q1') == raw
    assert len(calls) == 1 and calls[0][1]['model'] == 'openrouter/openai/gpt-image-2'
    assert calls[0][0].endswith('/images/generations' if api == 'images' else '/chat/completions')
    assert calls[0][1].get('prompt', calls[0][1].get('messages')) == ('原始题面' if api == 'images' else [{'role': 'user', 'content': '原始题面'}])


def test_notebook_has_two_independent_steps():
    book = json.loads((Path(__file__).parents[1] / 't2i_v2_eval_debug.ipynb').read_text())
    assert len(book['cells']) == 2
    sources = []
    for cell in book['cells']:
        assert not cell['outputs'] and cell['execution_count'] is None
        text = ''.join(cell['source'])
        compile(text, 't2i_v2_eval_debug.ipynb', 'exec', flags=ast.PyCF_ALLOW_TOP_LEVEL_AWAIT)
        sources.append(text)
    assert 'asyncio.to_thread(run_pipeline' in sources[0]
    assert 'run_judging' not in sources[0]
    assert 'asyncio.to_thread(run_judging' in sources[1]
    assert 'run_pipeline' not in sources[1] and 'ANSWER_MODELS' not in sources[1]
    assert "INPUT_TABLE_URI =" in sources[0] and "INPUT_VERSION =" in sources[0]
    assert "OUTPUT_TABLE_URI =" in sources[0] and 'target_uri=OUTPUT_TABLE_URI' in sources[0]
    assert 'INPUT_TABLE_URI =' in sources[1] and 'INPUT_VERSION =' in sources[1]
    assert 'OUTPUT_TABLE_URI =' in sources[1] and 'target_uri=OUTPUT_TABLE_URI' in sources[1]


@pytest.mark.parametrize('backend', ['diffusers', 'bagel'])
@pytest.mark.parametrize('with_references', [False, True])
def test_local_model_receives_original_prompt_and_explicit_parameters(monkeypatch, backend, with_references):
    """验证本地权重加载及推理接线；不导入真实 torch，不分配 GPU。"""
    calls = []
    class Generator:
        def __init__(self, device):
            self.device = device
        def manual_seed(self, seed):
            self.seed = seed
            return self
    fake_torch = SimpleNamespace(bfloat16='bf16', Generator=Generator,
                                 manual_seed=lambda seed: None,
                                 cuda=SimpleNamespace(manual_seed_all=lambda seed: None))
    monkeypatch.setitem(sys.modules, 'torch', fake_torch)
    class Model:
        def to(self, device):
            calls.append(('device', device))
            return self
        def __call__(self, **kwargs):
            calls.append(('infer', kwargs))
            return SimpleNamespace(images=[Image.new('RGB', (16, 16))])
        def interleave_inference(self, inputs, **kwargs):
            calls.append(('infer', dict(inputs=inputs, **kwargs)))
            return ['模型输出文字', Image.new('RGB', (16, 16))]
    def from_pretrained(path, **kwargs):
        calls.append(('load', path, kwargs))
        return Model()
    monkeypatch.setitem(sys.modules, 'diffusers', SimpleNamespace(
        DiffusionPipeline=SimpleNamespace(from_pretrained=from_pretrained)))
    monkeypatch.setattr('evaluation.bagel.adapter.load_model',
                        lambda path, offload: (from_pretrained(path), fake_torch))
    cfg = config(answer_models=[{'backend': backend, 'model': 'local-fixture', 'model_path': '/fixture/weights',
                               'device': 'cuda:1', 'seed': 7, 'use_references': with_references,
                               'parameters': {'num_timesteps': 3} if backend == 'bagel' else {'num_inference_steps': 3, 'output_resolution': 1024}}],
                  judge_model={'model': 'fixture'})['answers'][0]
    root = resolve_root()
    actor = GenerateImage(cfg, LanceRecordStore(root, 'records.lance'), LanceBlobStore(root, 'images.lance'), root / 'offload')
    row = {'task_id': 'q1', 'concept': '概念', 'instruction': '完整题面'}
    if with_references:
        blob = actor.blobs.put(picture())
        row['references_json'] = json.dumps([
            {'kind': 'text', 'number': 1, 'title': '标题', 'text': '参考知识'},
            {'kind': 'image', 'number': 2, 'blob_ref': blob.to_dict()}])
    result = actor.generate(row)
    assert result['status'] == 'generated' and calls[0][1] == '/fixture/weights'
    actual = next(call[1] for call in calls if call[0] == 'infer')
    if backend == 'bagel':
        prompt, images = actual['inputs'][-1], actual['inputs'][:-1]
        assert actual['num_timesteps'] == 3
    else:
        prompt, images = actual['prompt'], actual.get('image', [])
        assert actual['num_inference_steps'] == 3 and actual['output_resolution'] == 1024
        assert actual['generator'].seed == 7 and ('device', 'cuda:1') in calls
    if with_references:
        assert '参考知识' in prompt and prompt.endswith('完整题面')
        assert len(images) == 1 and images[0].getpixel((0, 0)) == (255, 0, 0)
    else:
        assert prompt == '完整题面' and images == []


def test_judge_writer_failure_preserves_target_and_never_regenerates(monkeypatch):
    """评分写入失败时原目标表不变；独立评分不接触答题模型。"""
    monkeypatch.setattr(GenerateImage, 'remote', lambda *args: picture())
    run, src, cfg = resolve_root() / DATASETS / 'writer', source(), settings()
    answered = run_pipeline(run, src, cfg)
    target = answered['target']
    original = rows(target)
    assert original[0]['alignment_score'] is None and original[0]['judge_model'] is None
    monkeypatch.setattr(GenerateImage, 'generate', lambda *args: pytest.fail('Judge generated an answer'))
    writer = lance.write_dataset
    def fail_target(data, uri, **kwargs):
        if str(uri) == target['uri']:
            raise OSError('injected writer failure')
        return writer(data, uri, **kwargs)
    judge_run = run.with_name('writer_judge')
    with monkeypatch.context() as patch:
        patch.setattr(lance, 'write_dataset', fail_target)
        with pytest.raises(LanceWriteError, match='injected'):
            run_judging(judge_run, target, config(judge_model=cfg['judge']), target_uri=target['uri'])
    assert lance.dataset(target['uri']).version == target['version']
    assert rows(target) == original
    records = LanceRecordStore(resolve_root(), str(DATASETS / 'records__writer_judge.lance'))
    assert records.get('state') is None
    state = run_judging(judge_run, target, config(judge_model=cfg['judge']), target_uri=target['uri'])
    assert state['counts'] == {'pending_judge': 1}
    assert state['target']['uri'] == target['uri'] and state['target']['version'] > target['version']


def test_three_models_answer_every_question_and_resume_independently(monkeypatch):
    """同题跨模型不能命中别人的缓存；全部结果进入一张评分表。"""
    models = ['Qwen-Image-2512', 'BAGEL-7B-MoT', 'openrouter/openai/gpt-image-2']
    generated = []
    def generate(self, instruction, key):
        generated.append((self.config['model'], instruction))
        buffer = io.BytesIO()
        Image.new('RGB', (16, 16), ['red', 'green', 'blue'][models.index(self.config['model'])]).save(buffer, format='PNG')
        return buffer.getvalue()
    monkeypatch.setattr(GenerateImage, 'remote', generate)
    cfg = config(answer_models=[{'backend': 'modelhub', 'model': name} for name in models],
                 judge_model={'model': 'openrouter/openai/gpt-6-sol', 'mode': 'offline'})
    src = source([{'task_id': 'q1', 'instruction': '第一题'}, {'task_id': 'q2', 'instruction': '第二题'}])
    run = resolve_root() / DATASETS / 'three_models'
    answered = run_pipeline(run, src, cfg)
    judge_run = run.with_name(run.name + '_judge')
    pending = run_judging(judge_run, answered['target'], cfg, target_uri=answered['target']['uri'])
    expected = [(model, instruction) for model in models for instruction in ('第一题', '第二题')]
    assert generated == expected
    assert pending['counts'] == {'pending_judge': 6}
    for row in rows(pending['target']):
        call = json.loads(row['judge_call_json'])
        submit_response(resolve_root(), call['request_ref'], json.dumps({'result': judgment()}),
                        model=call['model'], metadata={'reviewer': 'fixture', 'reviewer_kind': 'human'})
    completed = run_judging(judge_run, answered['target'], cfg, target_uri=answered['target']['uri'])
    assert completed['counts'] == {'scored': 6} and completed['complete']
    results = rows(completed['target'])
    assert {(row['answer_model'], row['task_id']) for row in results} == {(model, qid) for model in models for qid in ('q1', 'q2')}
    assert {row['judge_model'] for row in results} == {'openrouter/openai/gpt-6-sol'}
    assert generated == expected


def test_model_subprocess_reads_frozen_run_and_writes_shared_table():
    """用空题表验证真实子进程环境与 Lance 接线，不加载模型或调用 API。"""
    uri = resolve_root() / 'empty.lance'
    uri.parent.mkdir(parents=True, exist_ok=True)
    ds = lance.write_dataset(pa.table({'task_id': pa.array([], type=pa.string()),
                                      'instruction': pa.array([], type=pa.string())}), str(uri))
    dependency = resolve_root() / 'child_dependencies'
    dependency.mkdir()
    marker = resolve_root() / 'child_imported.txt'
    (dependency / 'sitecustomize.py').write_text(
        'from pathlib import Path\nPath(' + repr(str(marker)) + ').write_text(\"loaded\")\n')
    cfg = config(answer_models=[{'backend': 'bagel', 'model': 'BAGEL-7B-MoT',
                                 'model_path': '/unused', 'python': sys.executable, 'pythonpath': [str(dependency)], 'cuda_visible_devices': ''}],
                 judge_model={'model': 'fixture', 'mode': 'offline'})
    state = run_pipeline(resolve_root() / DATASETS / 'child', {'uri': str(uri), 'version': ds.version}, cfg)
    assert marker.read_text() == 'loaded'
    assert state['complete'] and rows(state['target']) == []


def test_gateway_judge_uses_exact_name_without_catalog_gate(monkeypatch):
    """网关列表不完整时，仍按用户指定名称构造 judge 请求。"""
    cfg = config(answer_models=[{'backend': 'modelhub', 'model': 'fixture'}],
                 judge_model={'model': 'openrouter/openai/gpt-6-sol', 'base_url': 'http://127.0.0.1:4001/v1',
                              'api_key_env': 'MODELHUB_API_KEY'})
    captured = {}
    module = importlib.import_module('evaluation.t2i.v2.t2i_v2_eval_pipeline')
    from demiflow.data import Dataset
    original = Dataset.map_prompt_async
    def capture(self, *args, **kwargs):
        captured.update(kwargs)
        return original(self, *args, **kwargs)
    monkeypatch.setattr(Dataset, 'map_prompt_async', capture)
    uri = str(resolve_root() / 'empty_answers.lance')
    data.from_arrow(pa.Table.from_pylist([], schema=SCORES)).write_lance(uri, schema=SCORES)
    run_judging(resolve_root() / DATASETS / 'exact_model', {'uri': uri, 'version': 1}, cfg,
                target_uri=str(resolve_root() / 'empty_scores.lance'))
    pack, options = captured['config'], captured['options']
    assert pack.prompt_definitions['judge'].model.name == 'openrouter/openai/gpt-6-sol'
    assert pack.prompt_definitions['judge'].model.base_url == 'http://127.0.0.1:4001/v1'
    assert options['verify_model'] is False


def test_append_answers_preserves_existing_rows_and_does_not_repeat_on_resume(monkeypatch):
    """追加只写本次配置的答题模型；已保存的运行不会再次追加。"""
    calls = []
    def generate(self, instruction, key):
        calls.append(self.config['model'])
        return picture()
    monkeypatch.setattr(GenerateImage, 'remote', generate)
    root, src = resolve_root(), source()
    output = str(root / DATASETS / 'append_results.lance')
    first = run_pipeline(root / DATASETS / 'base', src, settings(), target_uri=output)
    original = rows(first['target'])
    cfg = config(answer_models=[{'backend': 'modelhub', 'model': 'added-model'}])
    run = root / DATASETS / 'added'
    appended = run_pipeline(run, src, cfg, target_uri=output, write_mode='append')
    assert rows(appended['target'])[:1] == original
    assert [r['answer_model'] for r in rows(appended['target'])] == ['fixture-image', 'added-model']
    assert appended['counts'] == {'generated': 1}
    assert run_pipeline(run, src, cfg, target_uri=output, write_mode='append') == appended
    assert calls == ['fixture-image', 'added-model']
    replaced = run_pipeline(root / DATASETS / 'replaced', src, cfg, target_uri=output, write_mode='overwrite')
    assert len(rows(replaced['target'])) == 1


def test_reference_inputs_use_frozen_text_and_pixels_without_private_rubrics(monkeypatch):
    """同模型两路分别缓存；图像字节、文字与题面完整交给参考信息路。"""
    root = resolve_root()
    blob = LanceBlobStore(root, 'reference_images.lance').put(picture())
    refs = [{'kind': 'text', 'number': 1, 'title': '资料标题', 'text': '完整参考知识'},
            {'kind': 'image', 'number': 2, 'blob_ref': blob.to_dict(), 'support': '外观', 'limitations': '局部'}]
    src = source([{'task_id': 'q1', 'instruction': '原始题面', 'references_json': json.dumps(refs),
                   'criteria': 'PRIVATE_CRITERIA', 'test_points': 'PRIVATE_POINTS'}])
    received = []
    monkeypatch.setattr(GenerateImage, 'load_model', lambda self: setattr(self, 'model', object()))
    monkeypatch.setattr(GenerateImage, 'aclose', _no_close)
    def local(self, prompt, images=None):
        received.append((prompt, images))
        return picture()
    monkeypatch.setattr(GenerateImage, 'local', local)
    cfg = config(answer_models=[{'backend': 'bagel', 'model': 'BAGEL-7B-MoT', 'model_path': '/fixture',
                                 'use_references': enabled} for enabled in [False, True]])
    result = run_pipeline(root / DATASETS / 'references', src, cfg)
    assert [r['answer_model'] for r in rows(result['target'])] == ['BAGEL-7B-MoT', 'BAGEL-7B-MoT+参考信息']
    assert received[0] == ('原始题面', None)
    prompt, images = received[1]
    assert all(t in prompt for t in ['完整参考知识', '参考图 1', '局部', '原始题面'])
    assert 'PRIVATE' not in prompt and len(images) == 1
    assert images[0].getpixel((0, 0)) == (255, 0, 0)


async def _no_close(self):
    """模拟模型不使用 torch，不需要释放 GPU。"""
    pass
