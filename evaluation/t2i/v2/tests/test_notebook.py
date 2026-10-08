"""Three-cell views, honest input/output counters, and native offline D execution."""
import json
from pathlib import Path
from types import SimpleNamespace

import lance
import pytest
from demiflow import data
from project import resolve_root
from evaluation.t2i.v2 import t2i_v2_eval_pipeline as pipeline
from evaluation.t2i.v2.operators.notebook import DStreamProgress, monitor_run, render_monitor
from .test_d_evaluation import fixture_run
from .test_d7 import directions


def test_counter_units_separate_started_finished_and_emitted():
    root = resolve_root()
    progress = DStreamProgress(root, 'fixture', 0, 10)
    stats = SimpleNamespace(stages={'judge_d': {'in': 4, 'emitted': 3}}, outputs={}, miss={},
        timing_summary=lambda: {'judge_d': {'count': 2}})
    progress.progress(stats)
    snapshot = json.loads(progress.path.read_text())
    judge = next(s for s in snapshot['operators'] if s['operator'] == 'judge_d')
    assert judge['entered_input_rows'] == 4
    assert judge['completed_input_rows'] == 2
    assert judge['pending_input_rows'] == 8
    assert judge['in_flight_input_rows'] == 2
    assert judge['emitted_output_rows'] == 3
    progress.drained(stats)
    interrupted = json.loads(progress.path.read_text())['operators'][1]
    assert interrupted['completed_input_rows'] is None
    assert interrupted['pending_input_rows'] is None
    assert interrupted['emitted_output_rows'] == 3


def test_native_d_stream_counters_and_readonly_monitor(monkeypatch):
    run, source, cfg = fixture_run(legacy=False, protocol='d7', requirements=directions())
    state = pipeline.run_pipeline(run, source, cfg)
    assert state['phase'] == 'awaiting_d_responses'
    root = resolve_root()
    versions = {str(p): lance.dataset(str(p)).version for p in root.rglob('*.lance') if p.is_dir()}
    monkeypatch.setattr(pipeline, 'run_pipeline', lambda *a, **k: pytest.fail('monitor may not execute models'))
    report = monitor_run(root / 'demiwtg', run.name)
    assert report['expected'] == 4 and report['questions'] == 2
    for arm in report['arms']:
        assert arm['committed_rows'] == 2 and arm['valid_rows'] == 0
        assert arm['states'] == {'pending': 2}
        assert arm['telemetry']['phase'] == 'finished'
        for op in arm['telemetry']['operators']:
            assert op['completed_input_rows'] == op['emitted_output_rows'] == 2, op
            assert op['pending_input_rows'] == op['in_flight_input_rows'] == 0, op
    html = render_monitor(report)
    assert '已处理完（输入行）' in html and '产出（输出行）' in html
    assert '评测完整：否' in html
    assert versions == {str(p): lance.dataset(str(p)).version for p in root.rglob('*.lance') if p.is_dir()}
    for path in (root / '_demiflow/evaluation_t2i_v2' / run.name).glob('operators_arm*.json'):
        path.unlink()  # Fixture a historical run which never recorded native counters.
    legacy = monitor_run(root / 'demiwtg', run.name)
    assert all(a['telemetry'] is None and a['committed_rows'] == 2 for a in legacy['arms'])
    assert '此运行未记录逐算子遥测' in render_monitor(legacy)


def test_unknown_run_does_not_fall_back_and_bad_id_is_rejected():
    project = resolve_root() / 'demiwtg'
    assert monitor_run(project, 'absent')['found'] is False
    for value in ('../bad', '', '/absolute', '.'):
        with pytest.raises(ValueError):
            monitor_run(project, value)


def test_readonly_cli_rejects_submission_arguments(monkeypatch):
    import sys
    monkeypatch.setattr(sys, 'argv', ['pipeline', '--stage', 'monitor', '--run', 'fixture', '--table', 'input'])
    monkeypatch.setattr(pipeline, 'run_pipeline', lambda *a, **k: pytest.fail('monitor must not run models'))
    with pytest.raises(SystemExit) as error:
        pipeline.main()
    assert error.value.code == 2


def test_summary_detail_navigation_and_notebook_size():
    owner = Path(__file__).parents[1]
    notebook = owner / 't2i_v2_eval_debug.ipynb'
    assert notebook.stat().st_size <= 96 * 1024 * 1024  # candidate.3: persisted self-contained output
    book = json.loads(notebook.read_text())
    assert len(book['cells']) == 3
    assert book['metadata']['demiforge']['historical_sha256']
    viewer = (owner / 'operators/case_viewer.html').read_text()
    assert 'id="overview"' in viewer and 'id="open-details"' in viewer
    assert 'id="detail-body" hidden' in viewer and 'id="back-summary"' in viewer
    assert '技术失败不计为0分' in viewer


def test_results_cell_renders_viewer_inline_in_notebook():
    owner = Path(__file__).parents[1]
    book = json.loads((owner / 't2i_v2_eval_debug.ipynb').read_text())
    cell = book['cells'][2]
    source = ''.join(cell['source'])
    assert cell['metadata']['tags'] == ['pipeline-results']
    assert 'notebook_browser(' in source
    assert 'IFrame' not in source
    assert 'artifact_url' not in source
    assert 'view_result["viewer_path"]' in source


@pytest.mark.parametrize('compressed', [False, True])
def test_three_conditions_keep_models_denominators_and_navigation_separate(tmp_path, compressed):
    import base64
    import gzip
    from playwright.sync_api import sync_playwright, expect
    from evaluation.t2i.v2.operators import case_viewer, d_evaluation
    modes = ('text_only', 'positive_images', 'imagerag')
    values = {'Qwen': [[10, 40, 30], [20, 60, None]], 'BAGEL': [[90, 30, 10], [30, 10, 40]]}
    cases = []
    for i in range(2):
        arms = []
        for model, scores in values.items():
            for j, mode in enumerate(modes):
                n = scores[i][j]
                arms.append({'index': len(arms), 'task_id': str(i), 'answer_mode': mode,
                    'answer_model': model + ('+正例参考图' if j == 1 else '+ImageRAG' if j == 2 else ''),
                    'generation_model': model, 'label': model + ' ' + mode, 'scoring_scheme': 'D',
                    'status': 'generated', 'd_status': 'reviewed', 'd_overall_score': n,
                    'd_metrics_result': {'overall_score': n}, 'core_requirements': {'requirements': []}})
        cases.append({'rank': i + 1, 'task_id': str(i), 'concept': ['甲题', '乙题'][i],
                      'instruction': '完整题面', 'test_points': [], 'references': [], 'arms': arms})
    summaries = [{**d_evaluation.summarize([c['arms'][i] for c in cases], 2),
                  'index': i, 'label': a['label']} for i, a in enumerate(cases[0]['arms'])]
    payload = {'meta': {'run': 'fixture', 'questions': 2, 'expected': 12, 'arms': summaries,
                       'scoring_scheme': 'D', 'judge': 'codex/gpt-6-astra',
                       'prompt_version': 't2i-v2-d-judge-7-review', 'complete': True},
               'cases': cases, 'media': {}}
    doc, meta = case_viewer._render(payload)
    assert meta['comparisons_by_model']['Qwen']['three_modes']['overall']['valid'] == 1
    assert meta['comparisons_by_model']['BAGEL']['pairs']['rag_positive']['dimensions']['overall']['arm1_minus_arm0'] == 5
    if compressed:
        prefix = '<script id="case-data" type="application/json">'
        data_text = doc.split(prefix)[1].split('</script>')[0]
        encoded = json.dumps({'encoding': 'gzip-base64', 'payload': base64.b64encode(gzip.compress(data_text.encode())).decode()})
        doc = doc.replace(prefix + data_text, prefix + encoded)
    page_path = tmp_path / 'viewer.html'
    page_path.write_text(doc)
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=p.chromium.executable_path,
                                    headless=True, args=['--no-sandbox'])
        page = browser.new_page()
        errors, network = [], []
        page.on('pageerror', lambda e: errors.append(str(e)))
        page.on('request', lambda r: network.append(r.url) if r.url.startswith(('http:', 'https:')) else None)
        page.goto(page_path.as_uri())
        expect(page.locator('#case-score-summary')).to_contain_text('全量同题配对 1/2')
        expect(page.locator('#case-score-triple')).to_contain_text('10.00 → 正例图 40.00 → 按缺点 RAG 30.00')
        page.select_option('#case-score-model', 'BAGEL')
        expect(page.locator('#case-score-summary')).to_contain_text('+5.00')
        page.select_option('#case-score-filter', 'higher')
        expect(page.locator('#case-score-table tbody tr')).to_have_count(1)
        page.locator('.case-score-link').click()
        expect(page.locator('#detail-body')).to_be_visible()
        expect(page.locator('#cases h2')).to_have_text('2. 乙题')
        page.locator('#back-summary').click()
        expect(page.locator('#case-score-model')).to_have_value('BAGEL')
        expect(page.locator('#case-score-filter')).to_have_value('higher')
        page.select_option('#case-score-comparison', 'rag_text')
        expect(page.locator('#case-score-summary')).to_contain_text('-35.00')
        page.fill('#case-score-search', '不存在')
        expect(page.locator('#case-score-table tfoot')).to_contain_text('没有匹配的题目')
        assert not errors and not network
        browser.close()


def test_d_failure_limit_preserves_cached_failures_without_retriggering():
    progress = DStreamProgress(resolve_root(), 'failure_limit', 0, 10, 2)
    row = dict(concept='fixture', d_status='failed', d_task_score=None, d_quality_score=None,
               d_aesthetics_score=None, d_other_score=None, d_overall_score=None)
    progress.report_result({**row, 'd_call_json': '{"reused":true}'})
    assert progress.consecutive_failures == 0
    progress.report_result(row)
    with pytest.raises(RuntimeError, match='committed rows and raw calls retained'):
        progress.report_result(row)


def test_existing_d7_scores_bind_actual_images_and_keep_rag_unscored(monkeypatch):
    import copy
    from evaluation.t2i.v2.operators import case_viewer, codex_judging
    root = resolve_root()
    question = {'task_id': 'q', 'instruction': 'draw', 'question_revision': 'r1', 'review_status': 'ready',
                'requirements_json': json.dumps(directions())}
    models, scores, arms = [], [], []
    for i, mode in enumerate(('text_only', 'positive_images')):
        score = {'task_id': 'q', 'instruction': 'draw', 'question_revision': 'r1',
                 'answer_model': 'qwen', 'answer_mode': mode, 'image_json': json.dumps({'sha256': str(i)}),
                 'd_protocol': 'd7', 'd_status': 'reviewed', 'd_overall_score': 60.0,
                 'judge_model': ('codex/gpt-6-astra', 'codex-subagent/gpt-6-astra')[i]}
        scores.append(score)
        arms.append({k: score[k] for k in ('answer_model', 'image_json')})
        models.append({'model': 'qwen', 'answer_mode': mode,
                       'view_scores_from': {'uri': 'scores.lance', 'version': 1}})
    models.append({'model': 'qwen', 'answer_mode': 'imagerag'})
    arms.append({'status': 'generated'})
    monkeypatch.setattr(case_viewer.data, 'read_lance', lambda **kwargs: data.from_items(scores))
    monkeypatch.setattr(codex_judging, 'input_view', lambda *args: {'available': True})
    original = {'meta': {'sources': {}, 'arms': [{}, {}, {}]},
                'cases': [{'task_id': 'q', 'arms': arms}]}
    result = copy.deepcopy(original)
    case_viewer._attach_existing_scores(result, models, [question], root, None)
    assert [a['recorded'] for a in result['meta']['arms']] == [1, 1, 0]
    assert result['meta']['arms'][2]['dimensions']['overall'] == {'mean': None, 'valid': 0}
    assert result['cases'][0]['arms'][0]['d_overall_score'] == 60
    assert 'd_overall_score' not in result['cases'][0]['arms'][2]
    assert result['cases'][0]['arms'][1]['judge_model'] == 'codex-subagent/gpt-6-astra'
    result = copy.deepcopy(original)
    result['cases'][0]['arms'][0]['image_json'] = json.dumps({'sha256': 'wrong'})
    with pytest.raises(ValueError, match='actual question, model and image'):
        case_viewer._attach_existing_scores(result, models, [question], root, None)


@pytest.mark.parametrize('shadow_output', [False, True])
def test_notebook_clipboard_host_commands_paths_and_fallback(shadow_output):
    """Use real copy/paste, including a host copy with no inner-frame keydown."""
    import base64
    from playwright.sync_api import sync_playwright, expect
    from evaluation.t2i.v2.operators.notebook import notebook_browser
    from .test_pipeline import picture
    path = '/tmp/原图 空格/sha256'
    payload = {
        'meta': {'run': 'fixture', 'questions': 1, 'expected': 1, 'arms': [
            {'index': 0, 'label': 'fixture', 'expected': 1, 'generation': {'generated': 1}}]},
        'cases': [{'rank': 1, 'task_id': 'q', 'concept': '复制测试', 'instruction': '完整题面与原图',
                   'test_points': [], 'references': ['bad'], 'arms': [
                       {'index': 0, 'label': 'fixture', 'image': 'ok', 'status': 'generated'}]}],
        'media': {'ok': {'uri': 'file:///tmp/%E5%8E%9F%E5%9B%BE%20%E7%A9%BA%E6%A0%BC/sha256',
                         'src': 'data:image/png;base64,' + base64.b64encode(picture()).decode()},
                  'bad': {'uri': 'file:///tmp/unreadable', 'error': 'preview unavailable'}}}
    template = (Path(__file__).parents[1] / 'operators/case_viewer.html').read_text()
    wrapped = notebook_browser(template.replace('__CASE_DATA__', json.dumps(payload)), 'fixture.html')
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=pw.chromium.executable_path,
                                     args=['--no-sandbox', '--disable-dev-shm-usage'])
        try:
            page = browser.new_page(viewport={'width': 1500, 'height': 1000})
            errors = []
            page.on('pageerror', lambda e: errors.append(str(e)))
            page.route('**/*', lambda r: r.abort())
            if shadow_output:
                page.set_content('<div id="output"></div>')
                page.evaluate('''html => {
                    const root=document.querySelector('#output').attachShadow({mode:'open'});
                    root.innerHTML=html;
                    for(const old of root.querySelectorAll('script')){
                        const script=document.createElement('script');script.textContent=old.textContent;
                        old.replaceWith(script);
                    }
                }''', wrapped)
            else:
                page.set_content(wrapped)
            # Match VS Code's outer link handler, including its required event.view.
            page.evaluate('''() => {
                window.openedFiles=[];
                document.body.addEventListener('click',event=>{
                    if(!event.view?.document)return;
                    for(const node of event.composedPath())if(node instanceof HTMLAnchorElement&&node.href){
                        window.openedFiles.push(node.getAttribute('href'));
                        event.preventDefault();event.stopPropagation();return;
                    }
                });
            }''')
            frame = page.frames[1]
            frame.locator('#open-details').click()
            page.evaluate("const a=document.createElement('textarea');a.id='paste-check';document.body.append(a)")
            def pasted():
                area = page.locator('#paste-check')
                area.fill(''); area.focus(); page.keyboard.press('Control+v')
                return area.input_value()
            frame.locator('img[data-image="ok"]').scroll_into_view_if_needed()
            frame.wait_for_function("document.querySelector('img[data-image=ok]').naturalWidth > 0")
            href = '/tmp/%E5%8E%9F%E5%9B%BE%20%E7%A9%BA%E6%A0%BC/sha256'
            link = frame.locator('.media-item a[data-open-image]').first
            assert link.get_attribute('href') == href
            link.click()
            page.wait_for_function('window.openedFiles.length === 1')
            assert page.evaluate('window.openedFiles') == [href]
            page.evaluate("window.postMessage({type:'t2i-open-image-v1',href:'/tmp/forged'},'*')")
            frame.evaluate("parent.postMessage({type:'t2i-open-image-v1',href:'javascript:alert(1)'},'*')")
            frame.locator('[data-copy-image="ok"]').click()
            assert page.evaluate('window.openedFiles') == [href]
            assert pasted() == path, (frame.locator('#copy-status').inner_text(), errors, page.locator('[data-copy-text]').input_value())
            frame.locator('img[data-image="ok"]').click()
            frame.locator('#image-note a[data-open-image]').click()
            page.wait_for_function('window.openedFiles.length === 2')
            assert page.evaluate('window.openedFiles') == [href, href]
            frame.locator('#copy-image-path').click()
            assert pasted() == path
            frame.locator('#close-image').click()
            title = frame.locator('h1').inner_text()
            def select_title():
                frame.locator('h1').click()
                frame.evaluate('''() => {const r=document.createRange();r.selectNodeContents(document.querySelector('h1'));
                    getSelection().removeAllRanges();getSelection().addRange(r);}''')
                expect(page.locator('[data-copy-text]')).to_have_value(title)
            select_title()
            frame.evaluate("window.copyKeydowns=0;document.addEventListener('keydown',()=>window.copyKeydowns++)")
            page.evaluate("document.execCommand('copy')")
            assert frame.evaluate('window.copyKeydowns') == 0
            assert pasted() == title
            select_title();frame.locator('#copy-selection').click();assert pasted() == title
            select_title();page.keyboard.press('Control+c');assert pasted() == title
            # External messages cannot forge this frame's selection.
            page.evaluate("window.postMessage({type:'t2i-selection-v1',text:'forged'},'*')")
            assert page.locator('[data-copy-text]').input_value() != 'forged'
            area = page.locator('#paste-check');area.fill('outside text');area.select_text()
            page.evaluate("document.execCommand('copy')");assert pasted() == 'outside text'
            # A blocked copy exposes the requested path in the outer native textarea.
            frame.evaluate('document.execCommand=()=>false')
            frame.locator('[data-copy-image="ok"]').click()
            expect(page.locator('[data-copy-text]')).to_have_value(path)
            assert page.locator('[data-copy-panel]').evaluate('(e)=>e.open')
            page.locator('[data-copy-select]').click();page.keyboard.press('Control+c')
            assert pasted() == path
            frame.locator('summary').filter(has_text='全部正例参考图').click()
            frame.locator('[data-copy-image="bad"]').click()
            expect(page.locator('[data-copy-text]')).to_have_value('/tmp/unreadable')
            assert not errors
        finally:
            browser.close()
