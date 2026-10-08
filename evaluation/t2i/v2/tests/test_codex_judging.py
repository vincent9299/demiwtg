"""Same-answer Codex comparison: standard offline requests, recovery and inline UI."""
import hashlib
import io
import pyarrow as pa
from PIL import Image
from demiflow.objects import LocalObjectStore
import json
from copy import deepcopy
from pathlib import Path

import lance
import pytest
from demiflow import data
from demiflow.operator_llm.sqlite_offline import submit_response
from project import resolve_root
from evaluation.t2i.v2 import t2i_v2_eval_pipeline as pipeline
from evaluation.t2i.v2.operators import codex_judging as cj
from evaluation.t2i.v2.operators.case_viewer import build_case_browser, notebook_browser
from .test_d7 import directions, response
from .test_d_evaluation import fixture_run, read_rows


def test_comparison_never_pairs_different_answers_or_counts_pending_scores():
    base = dict(task_id='q', answer_mode='text_only', answer_model='qwen', instruction='test',
                question_revision='v1', d_protocol='d7', image_json='{"sha256":"a"}',
                core_requirements_json='{"requirements":[]}', d_status='reviewed', d_overall_score=80)
    other = {**base, 'judge_model': 'codex/gpt-6-astra', 'd_status': 'pending', 'd_overall_score': 100}
    summary = cj.comparison_summary([base], [other])['arms'][0]
    assert summary['reviewed'] == 0
    assert summary['dimensions']['overall'] == dict(paired_count=0, malasci_mean=None, codex_mean=None, delta=None)
    other['d_status'] = 'reviewed'
    assert cj.comparison_summary([base], [other])['arms'][0]['dimensions']['overall']['delta'] == 20
    for key in ('instruction', 'question_revision', 'image_json', 'core_requirements_json'):
        wrong = {**other, key: '"changed"'}
        with pytest.raises(ValueError, match='identity mismatch'):
            cj.comparison_summary([base], [wrong])
    with pytest.raises(ValueError, match='Duplicate'):
        cj.comparison_summary([base], [other, other])
    with pytest.raises(ValueError, match='not a Codex'):
        cj.comparison_summary([base], [{**other, 'judge_model': 'malasci/gpt-6-astra'}])


def test_actual_message_comparison_preserves_text_order_and_image_identity():
    uri = 'data:image/png;base64,aW1hZ2U='
    messages = [{'role':'user', 'content':[{'type':'text','text':'原始题面\n'},
                {'type':'image_url','image_url':{'url':uri}}]}]
    historical = deepcopy(messages)
    historical[0]['content'][1]['image_url']['url'] = {'data_uri_sha256':hashlib.sha256(uri.encode()).hexdigest()}
    assert cj.normalized_messages(messages) == cj.normalized_messages(historical)
    historical[0]['content'][0]['text'] = '原始题面'
    assert cj.normalized_messages(messages) != cj.normalized_messages(historical)
    assert messages[0]['content'][1]['image_url']['url'] == uri


def test_offline_codex_completion_reuses_exact_input_and_inline_paired_details(tmp_path):
    run, source, cfg = fixture_run(legacy=False, protocol='d7', requirements=directions())
    root = resolve_root()
    # Distinct answer pixels model real per-question generations; identical fixture
    # images would correctly deduplicate the standard prompt requests.
    for index, model in enumerate(cfg['answers']):
        rows = data.read_lance(**model['source']).take(3)
        for n, row in enumerate(rows):
            image = io.BytesIO()
            Image.new('RGB', (16,16), (index * 80,n * 60,10)).save(image, format='PNG')
            row['image_json'] = json.dumps(LocalObjectStore(root/'objects').put(image.getvalue()).to_dict())
        schema = lance.dataset(**model['source']).schema
        data.from_arrow(pa.Table.from_pylist(rows,schema=schema)).write_lance(model['source']['uri'],schema=schema,mode='overwrite')
        model['source']['version'] = 2
    first = pipeline.run_pipeline(run, source, cfg)
    for index in range(2):
        for row in read_rows(first['outputs'][f'arm{index}']):
            call = json.loads(row['d_call_json'])
            submit_response(root, call['request_ref'], json.dumps({'result':response()}), model=call['model'])
    base = pipeline.run_pipeline(run, source, cfg)
    assert base['complete']
    original_versions = {r['uri']:lance.dataset(r['uri']).version for r in base['outputs'].values()}
    c_run = run.with_name('codex_fixture')
    settings = pipeline.config(codex_comparison={'base_run':run.name,'selection':'uncovered',
                                                'expected_requests':4,'reuse_sources':[]})
    pending = pipeline.run_pipeline(c_run, None, settings)
    assert pending['submitted'] == 0
    assert all(a['recorded']==a['expected']==2 and a['reviewed']==0 for a in pending['summary']['arms'])
    for row in read_rows(pending['target']):
        assert row['d_status']=='pending' and row['d_overall_score'] is None and json.loads(row['d_json']) is None
    directory = root/'demiwtg/evaluation/t2i/v2/runs'/c_run.name
    for case in sorted(directory.glob('case*')):
        receipt = json.loads((case/'input_receipt.json').read_text())
        assert receipt['same_actual_input'] is True
        for f in receipt['texts']+receipt['images']:
            assert hashlib.sha256((case/f['file']).read_bytes()).hexdigest()==f['sha256']
        (case/'response.json').write_text(json.dumps({'result':response((1,1,1,0))}))
        (case/'execution.json').write_text(json.dumps({'transport':'codex_subagent','fork_turns':'none',
             'model':'gpt-6-astra','agent_name':'fixture','reasoning_effort':'xhigh'}))
    finished = pipeline.run_pipeline(c_run, None, settings)
    assert finished['submitted']==4
    assert all(a['reviewed']==2 for a in finished['summary']['arms'])
    for a in finished['summary']['arms']:
        assert a['dimensions']['general_instruction_following']==dict(
            paired_count=2, malasci_mean=100, codex_mean=0, delta=-100)
    assert original_versions=={p:lance.dataset(p).version for p in original_versions}
    h, meta = build_case_browser(root/'demiwtg',run.name)
    assert meta['codex_comparison']['arms'][0]['reviewed']==2
    wrapper = notebook_browser(h,'unused-fallback.html',height=2400)
    assert 'srcdoc=' in wrapper
    path=tmp_path/'inline.html';path.write_text(wrapper)
    from playwright.sync_api import sync_playwright
    with sync_playwright() as play:
        browser=play.chromium.launch(executable_path=play.chromium.executable_path,args=['--no-sandbox'])
        page=browser.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
        page.goto(path.as_uri());frame=page.frame_locator('iframe')
        assert frame.locator('#codex-comparison').is_visible()
        assert frame.locator('#codex-comparison').inner_text().count('2 / 2')==6
        frame.locator('#open-details').click()
        assert frame.locator('.arm').count()==2
        assert frame.locator('.requirement-comparison').first.locator('th').all_text_contents()==[
            '固定检查方向与判据','Malasci · 分值与理由','Codex · 分值与理由']
        cells=frame.locator('.requirement-comparison').first.locator('tbody tr').first.locator('td')
        assert cells.nth(1).inner_text().startswith('2') and cells.nth(2).inner_text().startswith('1')
        assert 'Codex D 判分 · 实际完整输入与作答图' in frame.locator('.arm').first.inner_text()
        frame.locator('#next').click();assert '2 / 2' in frame.locator('#count').inner_text()
        frame.locator('#back-summary').click();assert frame.locator('#codex-comparison').is_visible()
        assert errors==[]
        browser.close()
    # A malformed scope must fail rather than silently omit an arm or change the denominator.
    bad = pipeline.config(codex_comparison={'base_run':run.name,'selection':'uncovered',
                                           'expected_requests':3,'reuse_sources':[]})
    with pytest.raises(ValueError,match='scope differs'):
        pipeline.run_pipeline(run.with_name('bad_scope'),None,bad)
