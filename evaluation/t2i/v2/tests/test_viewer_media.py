"""合并浏览在小字节预算下仍保留完整请求、参考图顺序及所有作答路。"""
import base64
import io
import json
import random

import lance
import pyarrow as pa
import pytest
from PIL import Image
from demiflow.objects import LocalObjectStore
from evaluation.t2i.v2.operators import case_viewer
from evaluation.t2i.v2.operators.answers import historical_answer_identity
from evaluation.t2i.v2.operators.run_tables import RunTables
from evaluation.t2i.v2.tests.test_paired import question
from evaluation.t2i.v2.tests.test_pipeline import source
from evaluation.t2i.v2.t2i_v2_eval_pipeline import config, DATASETS
from project import resolve_root


def test_denoising_log_deduplicates_bars_and_next_loop_overlap():
    log = '\r'.join([
        'Loading: 100%|x| 5/5 [00:08<00:00, 1it/s]',
        '100%|x| 40/40 [00:08<00:00, 5it/s]',
        '100%|x| 40/40 [00:08<00:00, 4it/s]',
        '  0%| | 0/40 [00:00<?, ?it/s]',
        '2%|x| 1/40 [00:00<00:08, 5it/s][serve] t2i ok 1024x1024 seed=42 model=Qwen-Image-2.1 steps=40 140.5s',
        'INFO: "POST /v1/images/generations HTTP/1.1" 200 OK',
        '100%|x| 40/40 [01:02<00:00, 1it/s]',
        '[serve] t2i ok 1024x1024 seed=42 model=Qwen-Image-2.1 steps=40 150.5s',
        'INFO: "POST /v1/images/edits HTTP/1.1" 200 OK',
        '  5%|x| 2/40 [00:00<00:08, 5it/s]',
    ])
    result = case_viewer._denoise_log_samples(log)
    assert [s['denoise_s'] for s in result['samples']] == [8, 62]
    assert [s['route'] for s in result['samples']] == ['generations', 'edits']
    assert result['counts']['completed_loops'] == 2
    assert result['counts']['duplicate_completed_bars'] == 1
    assert result['counts']['unmatched_completed_loops'] == 0


def test_denoising_log_rejects_ambiguous_and_incomplete_receipts():
    log = '\n'.join([
        '100%|x| 49/49 [00:30<00:00, 1it/s]',
        '0%| | 0/49 [00:00<?, ?it/s]',
        '100%|x| 49/49 [00:31<00:00, 1it/s]',
        '[serve] t2i ok 1024x1024 seed=42 model=BAGEL-7B-MoT steps=50 90s',
        'INFO: "POST /v1/images/edits HTTP/1.1" 200 OK',
        '100%|x| 49/49 [00:32<00:00, 1it/s]',
    ])
    result = case_viewer._denoise_log_samples(log)
    assert result['samples'] == []
    assert result['counts']['ambiguous_server_receipts'] == 1
    assert result['counts']['unmatched_http_successes'] == 1
    assert result['counts']['unmatched_completed_loops'] == 1


@pytest.mark.parametrize('legacy_service,journal_suffix', [(False, ''), (True, '__1234abcd')])
def test_denoising_manifest_scope_never_claims_final_question_binding(tmp_path, legacy_service, journal_suffix):
    root = tmp_path
    manifest = root / '_demiflow/run_manifests/demiwtg/evaluation/t2i/v2/datasets/records__old.json'
    manifest.parent.mkdir(parents=True)
    log = root / 'service.log'
    log.write_text('100%|x| 49/49 [00:30<00:00, 1it/s]\n'
        '[serve] t2i ok 1024x1024 seed=42 model=BAGEL-7B-MoT steps=50 200s\n'
        'INFO: "POST /v1/images/edits HTTP/1.1" 200 OK\n')
    model = {'answer_mode': 'positive_images', 'shared_service': {'configuration': {'log_path': str(log)}}}
    if legacy_service:
        model['service'] = model.pop('shared_service')['configuration']
    manifest.write_text(json.dumps({'expected_questions': 299, 'config': {'answers': [model]}}))
    event = {'identity': 'one', 'request_ref': {'journal_path': 'calls_answers__old__arm00'+journal_suffix+'.sqlite'}}
    groups = [{'generation_model': 'BAGEL-7B-MoT', 'stages': {'generation': {'call_evidence': [event, event]}}}]
    result = case_viewer._denoise_timing(groups, root)
    row = result['groups'][0]
    assert row['samples'] == 1 and row['mean_s'] == 30
    assert row['selected_generation_calls'] == 1
    assert row['configured_steps'] == [50]
    assert row['runs']['old']['expected_questions'] == 299
    assert row['exact_question_binding'] is False and result['strict_gpu_s'] is None


def test_historical_timing_deduplicates_embedding_batches_and_kept_images():
    def call(key, seconds):
        return {'request_ref': {'identity': key}, 'elapsed_s': seconds, 'model': 'stage-model'}
    def initial(seconds):
        return {'answer_call_json': json.dumps(call('initial'+str(seconds), seconds)),
                'generation_seconds': seconds}
    attempts = [{'call': call('concept1', 3)}, {'call': call('concept2', 4)}]
    first = {'generation_model': 'Qwen', 'answer_mode': 'imagerag',
        'generation_seconds': 20, 'answer_call_json': json.dumps(call('generation', 20)),
        'd_call_json': json.dumps(call('judge', 9)),
        'rag': {'status': 'retrieved', 'initial_answer': initial(100),
            'concept_attempts': attempts,
            'stages': {'decision': {'call': call('decision1', 2)},
                       'concepts': {'call': attempts[-1]['call']}, 'captions': {'call': call('caption', 5)}},
            'retrievals': [{'query_id': 'q'+str(i), 'embedding_call_json': json.dumps({
                **call('one-batch', 8), 'index': i})} for i in range(2)]}}
    second = {'generation_model': 'Qwen', 'answer_mode': 'imagerag',
        'generation_seconds': 0, 'answer_call_json': initial(50)['answer_call_json'],
        'd_call_json': json.dumps({'score_reuse': {'reason': 'identical_frozen_d7_input'}}),
        'rag': {'status': 'keep_initial', 'initial_answer': initial(50),
                'stages': {'decision': {'call': call('decision2', 1)}}}}
    summary = case_viewer._timing_summary([{'task_id':'one','arms':[first]}, {'task_id':'two','arms':[second]}])
    group = summary['groups'][0]
    stages = group['stages']
    assert stages['embedding']['calls'] == 1 and stages['embedding']['sum_s'] == 8
    assert stages['decision']['models'] == ['stage-model']
    assert stages['retrieval']['calls'] == 2 and stages['retrieval']['missing_calls'] == 2
    assert stages['retrieval']['sum_s'] is None
    assert stages['concepts']['calls'] == 2 and stages['concepts']['sum_s'] == 7
    assert stages['generation']['mean_s'] == 20 and stages['generation']['skipped_questions'] == 1
    assert stages['initial_generation']['sum_s'] == 150
    assert stages['judge']['timed_calls'] == 1 and stages['judge']['missing_calls'] == 1
    assert stages['judge']['mean_s'] == 9 and group['judge_reused_initial'] == 1
    assert stages['decision']['p50_s'] == 1.5 and stages['decision']['p95_s'] == pytest.approx(1.95)
    assert group['recorded_answer_model_call_sum_s'] == 193
    assert group['recorded_answer_model_call_s_per_question'] == 96.5
    assert group['pipeline_wall_s'] is None


@pytest.mark.parametrize('field', ['seconds', 'elapsed_s'])
def test_historical_timing_uses_original_response_for_zero_time_cache_hit(monkeypatch, field):
    from demiflow.operator_llm import call_ref
    read = []
    def original(ref, root):
        read.append(ref)
        return {field: 17.5}
    monkeypatch.setattr(call_ref, 'read_call', original)
    call = {'request_ref': {'identity': 'cached'}, 'response_ref': {'identity': 'original'},
            'elapsed_s': 0, 'reused': True}
    arm = {'generation_model':'BAGEL','answer_mode':'positive_images','answer_call_json':json.dumps(call),
           'generation_seconds': 0, 'd_call_json': 'null'}
    group = case_viewer._timing_summary([{'task_id':'x','arms':[arm]}])['groups'][0]
    assert group['stages']['generation']['mean_s'] == 17.5
    assert group['stages']['judge']['mean_s'] is None
    assert group['stages']['judge']['missing_calls'] == 1
    assert len(read) == 1


def test_historical_timing_does_not_count_zero_cache_hit_when_original_is_missing(monkeypatch):
    from demiflow.operator_llm import call_ref
    monkeypatch.setattr(call_ref, 'read_call', lambda *args: {})
    call = {'request_ref': {'identity': 'cache'}, 'response_ref': {'identity': 'missing'},
            'seconds': 0, 'reused': True}
    arm = {'generation_model':'Qwen','answer_mode':'text_only', 'generation_seconds':0,
           'answer_call_json':json.dumps(call), 'input':{'parameters':{'num_inference_steps':40,'seed':42}}}
    group = case_viewer._timing_summary([{'task_id':'one','arms':[arm]}])['groups'][0]
    assert group['stages']['generation']['mean_s'] is None
    assert group['stages']['generation']['missing_calls'] == 1
    assert group['generation_parameters'] == [{'num_inference_steps':40,'seed':42}]


def test_historical_timing_keeps_models_separate_and_missing_times_null():
    cases = [{'task_id':'one','arms':[
        {'generation_model':model,'answer_mode':'text_only','generation_seconds':seconds}
        for model,seconds in [('Qwen', 12), ('BAGEL', float('nan'))]]}]
    groups = {g['generation_model']:g for g in case_viewer._timing_summary(cases)['groups']}
    assert groups['Qwen']['stages']['generation']['mean_s'] == 12
    assert groups['BAGEL']['stages']['generation']['mean_s'] is None
    assert groups['BAGEL']['recorded_answer_model_call_sum_s'] is None
    assert groups['BAGEL']['missing_answer_model_timing_calls'] == 1


def test_historical_timing_names_embedding_model_from_one_original_batch(monkeypatch):
    from demiflow.operator_llm import call_ref
    reads = []
    def original(ref, root):
        reads.append(ref)
        return {'body': {'model': 'actual-encoder'}}
    monkeypatch.setattr(call_ref, 'read_call', original)
    call = {'request_ref': {'identity': 'one-batch'}, 'elapsed_s': 3}
    arm = {'generation_model': 'Qwen', 'answer_mode': 'imagerag', 'generation_seconds': 10,
           'rag': {'status': 'retrieved', 'retrievals': [
               {'query_id': str(i), 'embedding_call_json': json.dumps({**call, 'index': i})}
               for i in range(2)]}}
    stages = case_viewer._timing_summary([{'task_id': 'one', 'arms': [arm]}])['groups'][0]['stages']
    assert stages['embedding']['models'] == ['actual-encoder']
    assert stages['embedding']['calls'] == 1 and stages['embedding']['sum_s'] == 3
    assert stages['generation']['models'] == ['Qwen']
    assert len(reads) == 1


def test_five_arms_share_bounded_thumbnails_without_dropping_input_images(monkeypatch):
    root = resolve_root()
    refs = []
    for seed in range(3):
        buffer = io.BytesIO()
        Image.frombytes('RGB', (640, 480), random.Random(seed).randbytes(640 * 480 * 3)).save(buffer, format='PNG')
        refs.append(LocalObjectStore(root / 'objects').put(buffer.getvalue()).to_dict())
    questions = [{**question(), 'task_id': str(i), 'instruction': '完整题面' * 300 + f'<script>不执行</script>末尾{i}',
        'authoring_images_json': json.dumps([{'object_ref': ref} for ref in refs + refs[:1]])}
        for i in range(2)]
    fixed = source(questions)
    runs = ['qwen', 'flare', 'zimage']
    for run in runs:
        models = config(answer_models=[{'backend': 'modelhub', 'model': run, 'answer_mode': mode}
            for mode in (('text_only',) if run == 'zimage' else ('text_only', 'positive_images'))],
            judge_model={'model': 'fixture-judge'}, stream_judging=True)
        RunTables(root, str(DATASETS / f'records__{run}.lance')).save_manifest(
            {'config': models, 'source': fixed, 'expected_questions': 2})
        for index, model in enumerate(models['answers']):
            records = RunTables(root, str(DATASETS / f'records__{run}__arm{index:02d}.lance'))
            selected = [refs[1], refs[0], refs[1]] if index else []
            for q in questions:
                records.start_answer(historical_answer_identity(model, q), {
                    'instruction': q['instruction'], 'reference_images': [{'object_ref': ref} for ref in selected],
                    'config': model})
            rows = [{'task_id': q['task_id'], 'status': 'generated', 'reason': '',
                     'image_json': json.dumps(refs[2]), 'reference_image_count': len(selected),
                     'generation_seconds': 1.} for q in questions]
            lance.write_dataset(pa.Table.from_pylist(rows), str(root / DATASETS / f'answers__{run}__arm{index:02d}.lance'))
    pause = root / '_demiflow/evaluation_t2i_v2/flare/pause_requested.json'
    pause.parent.mkdir(parents=True, exist_ok=True)
    pause.write_text(json.dumps({'reason': '用户要求暂停，预算耗尽'}))
    monkeypatch.setattr(case_viewer, 'MEDIA_BUDGET', 12 * 1024)
    document, meta = case_viewer.build_comparison_browser(root / 'demiwtg', runs)
    payload = json.loads(document.split('<script id="case-data" type="application/json">')[1].split('</script>')[0])
    assert meta['expected'] == 10 and len(payload['cases']) == 2
    assert [arm['paused'] for arm in meta['arms']] == [False, False, True, True, False]
    assert meta['runs'][1]['phase'] == 'paused'
    assert len(payload['media']) == 3  # 128/320/640预览及五路复用只保存三个实际对象。
    assert 0 < meta['thumbnails']['encoded_bytes'] <= 12 * 1024
    assert meta['thumbnails']['adjusted_previews'] > 0
    assert meta['thumbnails']['image_errors'] == 0
    assert len(payload['prompt_texts']) == 2
    for case in payload['cases']:
        assert case['references'] == [ref['sha256'] for ref in refs + refs[:1]]
        assert len(case['arms']) == 5
        for arm in case['arms']:
            assert payload['prompt_texts'][arm['input']['text_index']] == case['instruction']
            expected = [refs[1]['sha256'], refs[0]['sha256'], refs[1]['sha256']] if arm['index'] in (1, 3) else []
            assert arm['input']['images'] == expected
            assert arm['image'] == refs[2]['sha256']
    for key, item in payload['media'].items():
        with Image.open(io.BytesIO(base64.b64decode(item['src'].split(',')[1]))) as preview:
            assert preview.format == 'WEBP' and preview.size == (item['width'], item['height'])
            assert preview.width / preview.height == pytest.approx(4 / 3, abs=.02)
        assert item['uri'] == next(ref['uri'] for ref in refs if ref['sha256'] == key)
    from playwright.sync_api import sync_playwright
    with sync_playwright() as play:
        browser = play.chromium.launch(executable_path=play.chromium.executable_path, args=['--no-sandbox'])
        page = browser.new_page()
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.set_content(document)
        assert page.locator('#overview').is_visible()
        assert not page.locator('#detail-body').is_visible()
        page.click('#open-details')
        for index in range(2):
            assert page.locator('[data-field=actual-prompt]').all_text_contents() == [questions[index]['instruction']] * 5
            assert page.locator('.actual-references figure').count() == 6
            if index == 0:
                page.click('#next')
        assert not errors
        browser.close()


def test_impossible_thumbnail_budget_rejects_instead_of_omitting_images(monkeypatch, tmp_path):
    monkeypatch.setattr(case_viewer, 'MEDIA_BUDGET', 1)
    with pytest.raises(ValueError, match='未发布部分案例'):
        case_viewer._encode_thumbnails({'one': {'side': 128, 'cache': str(tmp_path), 'ref': {}}})


def test_unreadable_thumbnail_keeps_reference_and_error(tmp_path):
    media, metadata = case_viewer._encode_thumbnails({
        '0' * 64: {'side': 128, 'cache': str(tmp_path),
                   'ref': {'uri': (tmp_path / 'missing.png').as_uri(), 'sha256': '0' * 64}}})
    assert len(media) == 1 and metadata['image_errors'] == 1
    assert media['0' * 64]['uri'].endswith('missing.png') and media['0' * 64]['error']


def test_notebook_view_refresh_ignores_cached_old_renderer(monkeypatch):
    """模拟旧kernel中仍会抛128MiB的模块，执行实际查看格仍使用磁盘新版。"""
    import os
    import re
    import IPython.display
    import nbformat
    from pathlib import Path
    from evaluation.t2i.v2.tests.test_pipeline import picture
    root = resolve_root()
    ref = LocalObjectStore(root / 'objects').put(picture()).to_dict()
    fixed = source([{**question(), 'authoring_images_json': json.dumps([{'object_ref': ref}])}])
    cfg = config(answer_models=[{'backend': 'modelhub', 'model': 'fixture', 'answer_mode': mode}
        for mode in ('text_only', 'positive_images')], judge_model={'model': 'fixture'}, stream_judging=True)
    run = 'qwen21_ab_prompt1_c8_20261004'
    RunTables(root, str(DATASETS / f'records__{run}.lance')).save_manifest(
        {'config': cfg, 'source': fixed, 'expected_questions': 1})
    versions = {str(p): lance.dataset(str(p)).version for p in root.rglob('*.lance') if p.is_dir()}
    (root / 'demiwtg').mkdir(exist_ok=True)
    def stale(*args, **kwargs):
        raise ValueError('合并查看缩略图超过128MiB')
    monkeypatch.setattr(case_viewer, 'build_comparison_browser', stale)
    with pytest.raises(ValueError, match='128MiB'):
        case_viewer.build_comparison_browser(root / 'demiwtg', [run])
    real_project = Path(__file__).resolve().parents[4]
    monkeypatch.setenv('PYTHONPATH', os.pathsep.join([str(real_project), str(real_project.parent / 'demiflow')]))
    book = nbformat.read(real_project / 'evaluation/t2i/v2/t2i_v2_eval_debug.ipynb', as_version=4)
    code = book.cells[2].source.replace(str(real_project), str(root / 'demiwtg'))
    code = re.sub(r'^RUN_ID = .*$', 'RUN_ID = ' + repr(run), code, flags=re.MULTILINE)
    code = re.sub(r'^VIEW_RUNS = .*$', 'VIEW_RUNS = ' + repr([run]), code, flags=re.MULTILINE)
    code = code.replace('str(PROJECT.parent / "env/bin/python")', repr(__import__('sys').executable))
    code = code.replace('str(PROJECT), str(PROJECT.parent / "demiflow")',
                        repr(str(real_project)) + ', ' + repr(str(real_project.parent / 'demiflow')))
    shown = []
    monkeypatch.setattr(IPython.display, 'display', shown.append)
    namespace = {}
    exec(code, namespace)
    assert namespace['view_metadata']['questions'] == 1
    assert namespace['view_metadata']['expected'] == 2
    assert namespace['view_metadata']['thumbnails']['unique_images'] == 1
    assert namespace['view_metadata']['thumbnails']['image_errors'] == 0
    rendered = shown[-1]._repr_html_()
    assert '4000' in rendered and 'srcdoc=' in rendered
    assert '/files/demiwtg/evaluation/t2i/v2/runs/' not in rendered
    assert len(rendered.encode()) <= 64 * 1024 * 1024
    assert case_viewer.build_comparison_browser is stale
    assert versions == {str(p): lance.dataset(str(p)).version for p in root.rglob('*.lance') if p.is_dir()}


@pytest.mark.parametrize('arguments', [
    ['--stage', 'view'],
    ['--stage', 'view', '--view-runs', 'fixture', '--generate-model', '0'],
    ['--stage', 'answers', '--view-runs', 'fixture'],
])
def test_view_cli_rejects_mixed_or_missing_arguments_before_model_work(monkeypatch, arguments):
    import sys
    from evaluation.t2i.v2 import t2i_v2_eval_pipeline as pipeline
    monkeypatch.setattr(sys, 'argv', ['t2i_v2_eval_pipeline', '--run', 'viewer', *arguments])
    monkeypatch.setattr(pipeline, 'run_pipeline', lambda *a, **k: pytest.fail('view must not run models'))
    monkeypatch.setattr(pipeline, 'run_paired_arm', lambda *a, **k: pytest.fail('view must not run models'))
    with pytest.raises(SystemExit) as error:
        pipeline.main()
    assert error.value.code == 2
