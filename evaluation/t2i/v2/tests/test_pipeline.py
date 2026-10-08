from evaluation.t2i.v2.operators.run_tables import RunTables
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
from demiflow.objects import ObjectRef, LocalObjectStore
from demiflow.operator_llm.call_ref import read_call, journal_options, PromptRecordRef
from demiflow.operator_llm.sqlite_journal import SQLitePromptJournal
from demiflow.operator_llm.sqlite_offline import submit_response
from demiflow import data
from evaluation.t2i.v2.t2i_v2_eval_pipeline import DATASETS, PROMPTS, SCORES, config, run_pipeline, run_judging
from evaluation.t2i.v2.operators.answers import PrepareAnswer, answer_template, answer_bindings, answer_identity
from demiflow.image_generation import ImageGenerator as GenerateImage
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
        calls.append(instruction['body']['prompt'])
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
    assert ObjectRef(**json.loads(answer['image_json'])).read() == picture()
    call = json.loads(rows(pending['target'])[0]['judge_call_json'])
    request = SQLitePromptJournal(**journal_options(**pending['calls'])).read(call['request_ref']['request_id'], 'request')
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
    assert not SQLitePromptJournal(**journal_options(**state['calls'])).request_ids()


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
    import httpx
    cfg=settings()['answers'][0] | {'api':api,'model':'openrouter/openai/gpt-image-2'}
    op=native_actor(cfg); original=httpx.Client; calls=[]
    def respond(request):
        calls.append(request)
        b64=base64.b64encode(picture()).decode()
        body={'data':[{'b64_json':b64}]} if api=='images' else {'choices':[{'message':{'images':[{'image_url':{'url':'data:image/png;base64,'+b64}}]}}]}
        return httpx.Response(200,json=body)
    monkeypatch.setattr(httpx,'Client',lambda **kw:original(transport=httpx.MockTransport(respond),**kw))
    _,call,_=op.generate({'instruction':'原始题面'})
    assert len(calls)==1
    body=json.loads(calls[0].content)
    assert body['model']==cfg['model']
    assert calls[0].url.path.endswith('/images/generations' if api=='images' else '/chat/completions')
    assert body.get('prompt')=='原始题面' if api=='images' else body['messages'][0]['content']==[{'type':'text','text':'原始题面'}]
    op.journal.close()



def test_notebook_keeps_three_roles_and_submission_separate_from_readonly_views():
    book = json.loads((Path(__file__).parents[1] / 't2i_v2_eval_debug.ipynb').read_text())
    assert [cell['cell_type'] for cell in book['cells']] == ['code'] * 3
    assert [cell['metadata']['tags'] for cell in book['cells']] == [
        ['pipeline-run'], ['pipeline-monitor'], ['pipeline-results']]
    sources = [''.join(cell['source']) for cell in book['cells']]
    for text in sources:
        tree = ast.parse(text)
        assert not any(isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) for n in ast.walk(tree))
    assert 'subprocess.Popen' in sources[0] and 't2i_v2_eval_pipeline' in sources[0]
    assert all('run_pipeline' not in text and 'subprocess.Popen' not in text for text in sources[1:])
    assert '"monitor"' in sources[1] and '"view"' in sources[2]
    assert 'IFrame' in sources[2] and 'height=4000' in sources[2]
    assert 'notebook_browser' not in sources[2] and 'srcdoc' not in sources[2]
    assignments = [node for node in ast.parse(sources[0]).body if isinstance(node, ast.Assign)
                   and any(isinstance(t, ast.Name) and t.id == 'RUN_PIPELINE' for t in node.targets)]
    assert len(assignments) == 1 and ast.literal_eval(assignments[0].value) is False


@pytest.mark.parametrize('with_references', [False, True])
def test_local_model_receives_original_prompt_and_explicit_parameters(monkeypatch, with_references):
    calls=[]
    class Generator:
        def __init__(self, device): self.device=device
        def manual_seed(self, seed): self.seed=seed;return self
    monkeypatch.setitem(sys.modules,'torch',SimpleNamespace(bfloat16='bf16',Generator=Generator))
    class Model:
        def to(self, device): calls.append(('device',device));return self
        def __call__(self, **kwargs):
            if 'image' in kwargs: kwargs['image']=[im.copy() for im in kwargs['image']]
            calls.append(('infer',kwargs));return SimpleNamespace(images=[Image.new('RGB',(16,16))])
    def load(path,**kwargs): calls.append(('load',path,kwargs));return Model()
    monkeypatch.setitem(sys.modules,'diffusers',SimpleNamespace(DiffusionPipeline=SimpleNamespace(from_pretrained=load)))
    cfg=config(answer_models=[{'backend':'diffusers','model':'fixture','model_path':'/fixture/weights',
        'device':'cuda:1','seed':7,'parameters':{'num_inference_steps':3},
        'answer_mode':'positive_images' if with_references else 'text_only'}])['answers'][0]
    op=native_actor(cfg)
    values={'instruction':'完整题面',**({'images':[picture()]} if with_references else {})}
    op.generate(values)
    actual=next(c[1] for c in calls if c[0]=='infer')
    assert actual['prompt'].startswith('完整题面') and actual['generator'].seed==7
    assert actual['num_inference_steps']==3 and ('device','cuda:1') in calls
    if with_references:
        assert actual['prompt'].startswith('完整题面\n\n参考图：所附图像') and len(actual['image'])==1
        assert actual['image'][0].getpixel((0,0))==(255,0,0)
    else: assert actual['prompt']=='完整题面' and 'image' not in actual
    op.journal.close()



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
    records = RunTables(resolve_root(), str(DATASETS / 'records__writer_judge.lance'))
    assert records.load() is None
    state = run_judging(judge_run, target, config(judge_model=cfg['judge']), target_uri=target['uri'])
    assert state['counts'] == {'pending_judge': 1}
    assert state['target']['uri'] == target['uri'] and state['target']['version'] > target['version']


def test_three_models_answer_every_question_and_resume_independently(monkeypatch):
    """同题跨模型不能命中别人的缓存；全部结果进入一张评分表。"""
    models = ['Qwen-Image-2512', 'BAGEL-7B-MoT', 'openrouter/openai/gpt-image-2']
    generated = []
    def generate(self, instruction, key):
        generated.append((self.config['model'], instruction['body']['prompt']))
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
    cfg = config(answer_models=[{'backend': 'diffusers', 'model': 'BAGEL-7B-MoT',
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
    blob = LocalObjectStore(root / 'objects').put(picture())
    refs = [{'kind': 'text', 'number': 1, 'title': '资料标题', 'text': '完整参考知识'},
            {'kind': 'image', 'number': 2, 'object_ref': blob.to_dict(), 'support': '外观', 'limitations': '局部'}]
    src = source([{'task_id': 'q1', 'instruction': '原始题面', 'references_json': json.dumps(refs),
                   'criteria': 'PRIVATE_CRITERIA', 'test_points': 'PRIVATE_POINTS'}])
    received = []
    monkeypatch.setattr(GenerateImage, 'load_model', lambda self: setattr(self, 'model', object()))
    monkeypatch.setattr(GenerateImage, 'aclose', _no_close)
    def local(self, prompt, images=None):
        received.append((prompt['body']['prompt'], [Image.open(io.BytesIO(raw)).copy() for raw in images] if images else None))
        return picture()
    monkeypatch.setattr(GenerateImage, 'local', local)
    cfg = config(answer_models=[{'backend': 'diffusers', 'model': 'BAGEL-7B-MoT', 'model_path': '/fixture',
                                 'use_references': enabled} for enabled in [False, True]])
    result = run_pipeline(root / DATASETS / 'references', src, cfg)
    assert [r['answer_model'] for r in rows(result['target'])] == ['BAGEL-7B-MoT', 'BAGEL-7B-MoT+参考信息']
    assert received[0] == ('原始题面', None)
    prompt, images = received[1]
    assert all(t in prompt for t in ['完整参考知识', '局部', '原始题面'])
    assert 'PRIVATE' not in prompt and len(images) == 1
    assert images[0].getpixel((0, 0)) == (255, 0, 0)


async def _no_close(self):
    """模拟模型不使用 torch，不需要释放 GPU。"""
    pass


@pytest.mark.parametrize('api', ['images', 'chat', 'openrouter_images'])
def test_remote_positive_images_keep_prompt_pixels_and_order(monkeypatch, api):
    cfg=config(answer_models=[{'backend':'modelhub','model':'fixture','api':api,'answer_mode':'positive_images'}])['answers'][0]
    op=native_actor(cfg); refs=[]
    for color in ('red','blue'):
        b=io.BytesIO();Image.new('RGB',(8,4),color).save(b,format='PNG');refs.append(b.getvalue())
    prompt,images=op.render({'instruction':'原始题面','images':refs})
    request=op.request(prompt,images)
    assert request['body'].get('prompt',prompt).startswith('原始题面\n\n参考图：所附图像')
    assert images==refs
    if api=='images': assert request['endpoint']=='/images/edits' and len(request['body']['image[]'])==2
    elif api=='chat': assert len(request['body']['messages'][0]['content'])==3
    else: assert len(request['body']['input_references'])==2
    calls=[]
    def reject(*args): calls.append(1);raise RuntimeError('fixture service rejected')
    monkeypatch.setattr(GenerateImage,'remote',reject)
    with pytest.raises(RuntimeError,match='service rejected'):op.generate({'instruction':'原始题面','images':refs})
    from demiflow.operator_llm.journal import UncertainPromptCall
    with pytest.raises(UncertainPromptCall):op.generate({'instruction':'原始题面','images':refs})
    assert len(calls)==1
    op.journal.close()



def test_four_answer_arms_preserve_failures_references_and_judge_privacy(monkeypatch):
    """两个模型×两种方式；缺图只影响有图路，六个成功答案复用图进入独立judge。"""
    root = resolve_root()
    objects = LocalObjectStore(root / 'objects')
    image_ref = objects.put(picture()).to_dict()
    refs = [{'kind': 'image', 'number': 1, 'object_ref': image_ref}]
    src = source([
        {'task_id': 'q1', 'concept': '概念', 'instruction': '原始题面一',
         'authoring_variant': 'with_positive_images', 'authoring_images_json': json.dumps(refs),
         'test_points': ['PRIVATE_POINTS'], 'references_json': 'PRIVATE_TEXT'},
        {'task_id': 'q2', 'concept': '概念', 'instruction': '原始题面二',
         'authoring_variant': 'with_positive_images', 'authoring_images_json': '[]',
         'test_points': ['PRIVATE_POINTS'], 'references_json': 'PRIVATE_TEXT'},
    ])
    received = []
    def local(self, prompt, images=None):
        received.append((self.config['model'], prompt['body']['prompt'], [Image.open(io.BytesIO(raw)).getpixel((0, 0)) for raw in images or []]))
        return picture()
    def remote(self, prompt, images):
        return local(self, prompt, images)
    monkeypatch.setattr(GenerateImage, 'local', local)
    monkeypatch.setattr(GenerateImage, 'remote', remote)
    monkeypatch.setattr(GenerateImage, 'load_model', lambda self: setattr(self, 'model', object()))
    monkeypatch.setattr(GenerateImage, 'aclose', _no_close)
    cfg = config(answer_models=[
        {**model, 'answer_mode': mode}
        for model in [
            {'backend': 'diffusers', 'model': 'Qwen-Image-2.1', 'model_path': '/fixture'},
            {'backend': 'modelhub', 'model': 'malasci/gpt-image-2.5-flare'},
        ] for mode in ['text_only', 'positive_images']],
        judge_model={'model': 'malasci/gpt-6-astra-xhigh', 'mode': 'offline'})
    run = root / DATASETS / 'four_arms'
    answered = run_pipeline(run, src, cfg)
    answers = rows(answered['target'])
    assert len(answers) == 8 and answered['counts'] == {'generated': 6, 'generation_failed': 2}
    assert not answered['complete'] and len(answered['counts_by_arm']) == 4
    assert len(received) == 6
    for name, prompt, pixels in received:
        assert prompt.startswith(('原始题面一', '原始题面二'))
        if pixels:
            assert prompt.startswith('原始题面一\n\n参考图：所附图像') and len(pixels) == 1 and pixels[0][0] > 250
    for row in answers:
        if row['answer_mode'] == 'text_only':
            assert row['status'] == 'generated' and row['reference_image_count'] == 0
        elif row['task_id'] == 'q1':
            assert row['reference_image_count'] == 1
            assert json.loads(row['reference_images_json'])[0]['object_ref'] == image_ref
        else:
            assert row['status'] == 'generation_failed' and row['image_json'] is None
    assert run_pipeline(run, src, cfg) == answered and len(received) == 6
    pending = run_judging(root / DATASETS / 'four_arms_judge', answered['target'], cfg,
                          target_uri=str(root / DATASETS / 'four_arms_scores.lance'))
    judged = rows(pending['target'])
    assert pending['counts'] == {'pending_judge': 6, 'generation_failed': 2}
    assert {(r['task_id'], r['answer_model'], r['image_json'], r['reference_images_json']) for r in judged} == {
        (r['task_id'], r['answer_model'], r['image_json'], r['reference_images_json']) for r in answers}
    journal = SQLitePromptJournal(**journal_options(**pending['calls']))
    for row in judged:
        if row['status'] != 'pending_judge':
            continue
        call = json.loads(row['judge_call_json'])
        assert call['model'] == 'malasci/gpt-6-astra-xhigh'
        request = json.dumps(journal.read(call['request_ref']['request_id'], 'request'), ensure_ascii=False)
        assert all(secret not in request for secret in [
            'PRIVATE_', 'Qwen-Image-2.1', 'gpt-image-2.5-flare', 'positive_images', image_ref['uri']])


@pytest.mark.parametrize('problem', ['empty', 'wrong_variant', 'text', 'too_many', 'bad_sha'])
def test_bad_positive_references_fail_before_model_load(monkeypatch, problem):
    root = resolve_root()
    blobs = LocalObjectStore(root / 'objects')
    ref = {'kind': 'image', 'object_ref': blobs.put(picture()).to_dict()}
    refs, variant = [ref], 'with_positive_images'
    if problem == 'empty':
        refs = []
    elif problem == 'wrong_variant':
        variant = 'without_positive_images'
    elif problem == 'text':
        refs = [{'kind': 'text', 'text': 'PRIVATE_TEXT'}]
    elif problem == 'too_many':
        refs = [ref] * 6
    else:
        refs = [{**ref, 'object_ref': {**ref['object_ref'], 'sha256': '0' * 64}}]
    monkeypatch.setattr(GenerateImage, 'load_model', lambda self: pytest.fail('Must reject before GPU load'))
    monkeypatch.setattr(GenerateImage, 'local', lambda *args: pytest.fail('No fallback inference'))
    cfg = config(answer_models=[{'backend': 'diffusers', 'model': 'fixture', 'model_path': '/fixture',
                                 'answer_mode': 'positive_images'}])['answers'][0]
    records = RunTables(root, 'records.lance')
    row = {'task_id': 'q1', 'instruction': '原题', 'authoring_variant': variant,
           'authoring_images_json': json.dumps(refs)}
    result = PrepareAnswer(cfg, records)(row)
    assert result['status'] == 'generation_failed' and result['reason'] and result['image_json'] is None
    assert records.answer_request(answer_identity(cfg, row)) is None


def test_answer_cache_tracks_semantic_changes_and_ignores_unused_references():
    cfg = config(answer_models=[{'backend': 'diffusers', 'model': 'fixture', 'model_path': '/fixture',
                                 'answer_mode': 'positive_images'}])['answers'][0]
    row = {'task_id': 'q1', 'instruction': '原始题面', 'authoring_variant': 'with_positive_images',
           'authoring_images_json': '["ref1", "ref2"]'}
    identity = answer_identity(cfg, row)
    assert identity != answer_identity(cfg, {**row, 'authoring_images_json': '["ref2", "ref1"]'})
    assert identity != answer_identity(cfg, {**row, 'instruction': '修改后的题面'})
    assert identity != answer_identity({**cfg, 'seed': 99}, row)
    assert identity != answer_identity({**cfg, 'parameters': {'num_inference_steps': 2}}, row)
    text_cfg = {**cfg, 'answer_mode': 'text_only', 'use_references': False}
    assert identity != answer_identity(text_cfg, row)
    assert answer_identity(text_cfg, row) == answer_identity(text_cfg, {**row, 'authoring_images_json': 'broken refs'})
    assert identity == answer_identity(cfg, {**row, 'test_points': 'PRIVATE_CHANGED_POINTS'})


def native_actor(cfg):
    root=resolve_root()
    return GenerateImage(template=answer_template(cfg),model=cfg,inputs=answer_bindings(cfg),
        output='generated_image',call_output='call',error_output='error',
        journal_path=root/'fixture.sqlite',object_store=root/'objects',max_requests=3)
