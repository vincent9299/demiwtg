"""四路并发和A/B逐题持久化的隔离验证，不加载GPU或访问服务。"""
import json
import threading
from pathlib import Path
from types import SimpleNamespace
import pytest
from demiflow import data
from demiflow.operator_llm.sqlite_offline import submit_response
from evaluation.t2i.v2.t2i_v2_eval_pipeline import config, DATASETS, run_paired_arm, run_pipeline
from demiflow.image_generation import ImageGenerator as GenerateImage
from evaluation.t2i.v2.operators.paired_scores import a_score, b_score
from evaluation.t2i.v2.tests.test_pipeline import picture, source, judgment
from project import resolve_root


def probe_response(verdict='pass', score=8):
    return {'verdict': verdict, 'score': score, 'reason': '图中可见红色区域', 'basis': '像素证据',
            'failure_type': 'none' if verdict == 'pass' else 'insufficient_evidence',
            'point_results': [{'index': 1, 'verdict': verdict, 'evidence': '主体完整可见', 'reason': '符合判据'}],
            'case_annotation': {'knowledge_level': 'everyday', 'common_cn': 'yes', 'visual_support': 'strong',
                'confidence': 'high', 'reason': '可按普通视觉判断', 'caveat': '', 'sources': []}}


def question():
    return {'task_id': 'q1', 'concept': '红球', 'instruction': '画一个红球', 'taxonomy': ['物体'], 'authoring_images_json': '[]', 'authoring_variant':'with_positive_images',
            'test_points': [{'point': '红色', 'basis': '指定颜色', 'criterion': '球的可见表面为红色'}]}


def test_question_identity_validation_is_bounded_and_rejects_duplicate_ids():
    from evaluation.t2i.v2.operators.paired_scores import ValidateQuestions
    check = ValidateQuestions(2, max_id_bytes=4)
    assert check(question()) == question()
    with pytest.raises(ValueError, match='Duplicate'):
        check(question())
    with pytest.raises(ValueError, match='byte budget'):
        check({**question(), 'task_id': 'too-long'})


def test_a_hierarchical_mean_and_b_inconclusive():
    result = judgment()
    scored = a_score({'a_status': 'ready', 'a_result': result})
    assert scored['a_status'] == 'scored'
    assert scored['a_score'] == pytest.approx(((520 / 9) + 60) / 2)
    assert scored['aesthetics_score'] is None
    assert json.loads(scored['a_valid_items_json']) == {'alignment': 9, 'quality': 8, 'aesthetics': 0}
    for verdict, raw, expected in [('pass', 8, 80), ('inconclusive', -1, None)]:
        b = b_score({**question(), 'b_status': 'ready', 'b_reason': '', 'b_result': probe_response(verdict, raw)})
        assert b['b_status'] == 'reviewed' and b['b_raw_score'] == raw and b['b_score'] == expected


def test_paired_stream_writes_current_cases_and_reuses_generation(monkeypatch):
    root = resolve_root()
    requests = []
    def generate(self, prompt, key):
        requests.append(prompt['body']['prompt'])
        return picture()
    monkeypatch.setattr(GenerateImage, 'remote', generate)
    cfg = config(answer_models=[{'backend': 'modelhub', 'model': 'fixture'}],
                 judge_model={'model': 'fixture-judge', 'mode': 'offline'}, stream_judging=True)
    manifest = {'config': cfg, 'source': source([question()]), 'expected_questions': 1}
    run = root / DATASETS / 'paired'
    state = run_paired_arm(run, manifest, 0)
    assert not state['complete']
    assert set(state['outputs']) == {'answers', 'scores_a', 'scores_b'}
    pending = data.read_lance(**state['outputs']['scores_b']).take(1)[0]
    assert pending['a_status'] == pending['b_status'] == 'pending'
    for standard, response in [('a', judgment()), ('b', probe_response())]:
        call = json.loads(pending[standard + '_call_json'])
        submit_response(root, call['request_ref'], json.dumps({'result': response}),
                        model=call['model'], metadata={'reviewer': 'fixture', 'reviewer_kind': 'human'})
    done = run_paired_arm(run, manifest, 0)
    row = data.read_lance(**done['outputs']['scores_b']).take(1)[0]
    assert done['complete'] and requests == ['画一个红球']
    assert row['b_score'] == 80 and row['a_score'] == pytest.approx(58.888888888888886)
    assert row['image_json'] == pending['image_json']
    assert data.read_lance(**done['outputs']['answers']).count() == 1
    # 新评分运行通过显式固定版本复用旧成功图片；不依赖新运行的本地缓存。
    import lance
    prior_uri = root / DATASETS / 'answer_results__paired__arm00.lance'
    cfg['answers'][0]['reuse_answers_from'] = {'uri': str(prior_uri), 'version': lance.dataset(str(prior_uri)).version}
    monkeypatch.setattr(GenerateImage, 'remote', lambda *args: pytest.fail('Must reuse frozen generated image'))
    reused = run_paired_arm(root / DATASETS / 'paired_rejudge', manifest, 0)
    reused_row = data.read_lance(**reused['outputs']['answers']).take(1)[0]
    assert reused_row['image_json'] == row['image_json']


def test_all_four_children_start_before_wait_and_keep_failed_scope(monkeypatch):
    import evaluation.t2i.v2.t2i_v2_eval_pipeline as pipeline
    barrier = threading.Barrier(4)
    submitted = []
    class Process:
        def __init__(self, command, **kwargs):
            submitted.append((command, kwargs['env']['CUDA_VISIBLE_DEVICES']))
        def wait(self):
            barrier.wait(timeout=10)
            return 1  # 模拟四路启动后故障；不得把缺少结果的四题判为完成。
    monkeypatch.setattr(pipeline, 'subprocess', SimpleNamespace(
        Popen=Process, DEVNULL=pipeline.subprocess.DEVNULL, STDOUT=pipeline.subprocess.STDOUT))
    cfg = config(answer_models=[
        {'backend': 'diffusers', 'model': 'qwen', 'model_path': '/fixture', 'cuda_visible_devices': str(i), 'answer_mode': mode}
        for i, mode in enumerate(['text_only', 'positive_images'])] + [
        {'backend': 'modelhub', 'model': 'flare', 'answer_mode': mode} for mode in ['text_only', 'positive_images']],
        judge_model={'model': 'fixture'}, stream_judging=True, arm_concurrency=4)
    state = run_pipeline(resolve_root() / DATASETS / 'parallel', source([question()]), cfg)
    assert len(submitted) == 4 and sorted(gpu for _, gpu in submitted) == ['', '', '0', '1']
    assert state['expected_answers'] == 4 and not state['complete']
    assert state['phase'] == 'stopped_with_errors'
    assert all(arm['exit_code'] == 1 for arm in state['arms'])


def test_browser_pagination_search_and_full_instruction_without_kernel():
    from evaluation.t2i.v2.operators.case_viewer import build_case_browser, notebook_browser
    from evaluation.t2i.v2.operators.run_tables import RunTables
    from playwright.sync_api import sync_playwright
    root = resolve_root()
    cfg = config(answer_models=[{'backend': 'modelhub', 'model': 'fixture'}],
                 judge_model={'model': 'fixture-judge'}, stream_judging=True)
    questions = [question(), {**question(), 'task_id': 'q2', 'concept': '最后一题',
                              'instruction': '完整内容' * 300 + '<script>BAD</script>末尾标记'}]
    manifest = {'config': cfg, 'source': source(questions), 'expected_questions': 2}
    RunTables(root, str(DATASETS / 'records__viewer.lance')).save_manifest(manifest)
    document, meta = build_case_browser(root / 'demiwtg', 'viewer')
    assert meta['expected'] == 2 and 'iframe' in notebook_browser(document, 'viewer.html')
    with sync_playwright() as play:
        browser = play.chromium.launch(executable_path=play.chromium.executable_path, args=['--no-sandbox'])
        page = browser.new_page()
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.set_content(document)
        assert page.locator('article.card').count() == 1
        page.click('#next')
        assert page.locator('[data-field=instruction]').inner_text().endswith('<script>BAD</script>末尾标记')
        page.fill('#search', '红球')
        assert page.locator('article.card').get_attribute('data-concept') == '红球'
        page.select_option('#concept', 'q2')
        assert page.locator('article.card').get_attribute('data-concept') == '最后一题'
        assert not errors
        browser.close()


def test_online_paired_prompt_sets_gateway_placeholder_without_request(monkeypatch):
    import os
    from evaluation.t2i.v2.t2i_v2_eval_pipeline import paired_prompt
    monkeypatch.delenv('MODELHUB_API_KEY', raising=False)
    judge = config(judge_model={'model': 'malasci/gpt-6-astra', 'reasoning_effort': 'xhigh'})['judge']
    pack, options = paired_prompt('a', judge, resolve_root(), 'fixture', 1)
    assert os.environ['MODELHUB_API_KEY'] == 'anything'
    assert pack.prompt_definitions['judge'].model.name == 'malasci/gpt-6-astra'
    assert options['request_options']['reasoning_effort'] == 'xhigh'
    assert options['sqlite_journal']['max_requests'] == 1


@pytest.mark.parametrize('with_images', [False, True])
def test_openrouter_image_api_preserves_model_prompt_and_reference_order(monkeypatch, with_images):
    import base64, io, httpx
    from PIL import Image
    from evaluation.t2i.v2.tests.test_pipeline import native_actor
    from demiflow.execution.file_ref import JsonArtifactRef
    cfg=config(answer_models=[{'backend':'modelhub','model':'openrouter/openai/gpt-image-2.5-flare',
        'provider_model':'openai/gpt-image-2.5-flare','api':'openrouter_images','base_url':'http://fixture/openrouter',
        'answer_mode':'positive_images' if with_images else 'text_only'}])['answers'][0]
    op=native_actor(cfg);client=httpx.Client;seen=[]
    def respond(req):
        seen.append(json.loads(req.content));return httpx.Response(200,json={'data':[{'b64_json':base64.b64encode(picture()).decode()}]})
    monkeypatch.setattr(httpx,'Client',lambda **kw:client(transport=httpx.MockTransport(respond),**kw))
    refs=[]
    for color in ('red','blue') if with_images else ():
        b=io.BytesIO();Image.new('RGB',(3,4),color).save(b,format='PNG');refs.append(b.getvalue())
    _,call,_=op.generate({'instruction':'原始题面',**({'images':refs} if with_images else {})})
    payload=seen[0];saved=JsonArtifactRef(**call['input_ref']).read()
    assert payload==saved['body'] and payload['model']=='openai/gpt-image-2.5-flare'
    if with_images:
        assert payload['prompt'].startswith('原始题面\n\n参考图：所附图像')
        assert [base64.b64decode(r['image_url']['url'].split(',')[1]) for r in payload['input_references']]==refs
    else: assert payload['prompt']=='原始题面' and 'input_references' not in payload
    op.journal.close()



def test_comparison_browser_merges_five_arms_by_identity_and_rejects_different_source():
    from evaluation.t2i.v2.operators.case_viewer import build_comparison_browser
    from evaluation.t2i.v2.operators.run_tables import RunTables
    root = resolve_root()
    fixed = source([question()])
    for run, model in [('qwen', 'qwen'), ('flare', 'openrouter/flare'), ('zimage', 'Z-Image-Turbo')]:
        cfg = config(answer_models=[{'backend': 'modelhub', 'model': model, 'answer_mode': mode}
            for mode in (('text_only',) if run == 'zimage' else ('text_only', 'positive_images'))], judge_model={'model': 'judge', 'reasoning_effort': 'xhigh'},
            stream_judging=True, arm_concurrency=2)
        RunTables(root, str(DATASETS / f'records__{run}.lance')).save_manifest(
            {'config': cfg, 'source': fixed, 'expected_questions': 1})
    document, meta = build_comparison_browser(root / 'demiwtg', ['qwen', 'flare', 'zimage'])
    assert meta['expected'] == 5 and [arm['index'] for arm in meta['arms']] == [0, 1, 2, 3, 4]
    from playwright.sync_api import sync_playwright
    with sync_playwright() as play:
        browser = play.chromium.launch(executable_path=play.chromium.executable_path, args=['--no-sandbox'])
        page = browser.new_page()
        page.set_content(document)
        assert page.locator('#arm option').count() == 6
        page.select_option('#arm', '3')
        assert 'openrouter/flare' in page.locator('article.card').inner_text()
        assert 'qwen' not in page.locator('article.card').inner_text()
        page.select_option('#arm', '4')
        assert 'Z-Image-Turbo · 无图' in page.locator('article.card').inner_text()
        assert 'openrouter/flare' not in page.locator('article.card').inner_text()
        browser.close()
    RunTables(root, str(DATASETS / 'records__different.lance')).save_manifest(
        {'config': cfg, 'source': {**fixed, 'version': fixed['version'] + 1}, 'expected_questions': 1})
    with pytest.raises(ValueError, match='same frozen source'):
        build_comparison_browser(root / 'demiwtg', ['qwen', 'different'])


def test_next_run_configs_raise_all_judge_arms_and_remote_generation_without_old_cache():
    directory=Path(__file__).parents[1]/'configs'
    for name in ('qwen21_ab_prompt1_c8_20261004','flare_openrouter_ab_prompt1_c8_20261004','zimage_ab_text_c8_20261004'):
        raw=json.loads((directory/(name+'.json')).read_text())
        cfg=config(answer_models=raw['answers'],judge_model=raw['judge'],stream_judging=True,arm_concurrency=raw['arm_concurrency'])
        assert cfg['judge']['concurrency']==8
        assert all(a['queue_depth']==8 and 'reuse_answers_from' not in a for a in cfg['answers'])
        assert [a['concurrency'] for a in cfg['answers']]==([8] if name.startswith('zimage') else [8,8])
        if name.startswith('qwen'):
            assert cfg['arm_concurrency']==2 and all(a['cuda_visible_devices']=='1' for a in cfg['answers'])
            assert cfg['answers'][0]['shared_service']==cfg['answers'][1]['shared_service']
            assert cfg['answers'][0]['shared_service']['configuration']['gpus']==[1]
        if name.startswith('zimage'):
            answer=cfg['answers'][0]
            assert answer['service']['gpus']==[0] and answer['answer_mode']=='text_only'
            assert answer['parameters']['seed']==42 and answer['prompt_template']['template']=='{{ instruction }}'


def test_qwen_two_children_share_gpu_and_start_before_either_finishes(monkeypatch):
    import evaluation.t2i.v2.t2i_v2_eval_pipeline as pipeline
    raw=json.loads((Path(__file__).parents[1]/'configs/qwen21_ab_prompt1_c8_20261004.json').read_text())
    cfg=config(answer_models=raw['answers'],judge_model=raw['judge'],stream_judging=True,arm_concurrency=raw['arm_concurrency'])
    barrier=threading.Barrier(2)
    devices=[]
    class Process:
        def __init__(self, command, **kwargs): devices.append(kwargs['env']['CUDA_VISIBLE_DEVICES'])
        def wait(self):
            barrier.wait(timeout=5)
            return 1
    monkeypatch.setattr(pipeline,'subprocess',SimpleNamespace(
        Popen=Process,DEVNULL=pipeline.subprocess.DEVNULL,STDOUT=pipeline.subprocess.STDOUT))
    state=run_pipeline(resolve_root()/DATASETS/'qwen_shared',source([question()]),cfg)
    assert devices==['1','1'] and state['expected_answers']==2
    assert not state['complete']
    raw['answers'][1]['shared_service']['configuration']['gpus']=[0]
    with pytest.raises(ValueError,match='declarations must agree'):
        config(answer_models=raw['answers'])
