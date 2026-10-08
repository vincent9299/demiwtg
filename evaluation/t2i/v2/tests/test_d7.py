"""固定清单、五组算分和原生离线请求回归；不访问真实模型。"""
import json

import pyarrow as pa
import pytest
from demiflow import data
from demiflow.data.api import DataAPI
from demiflow.operator_llm.call_ref import read_call
from demiflow.operator_llm.parser import load_prompt_pack
from demiflow.operator_llm.sqlite_offline import submit_response
from project import resolve_root
from evaluation.t2i.v2 import t2i_v2_eval_pipeline as pipeline
from evaluation.t2i.v2.operators import d_evaluation, score_table
from evaluation.t2i.v2.operators.case_viewer import build_case_browser
from evaluation.t2i.v2.operators.d_rubric import frozen_requirements
from evaluation.t2i.v2.operators.d_scores import score_d
from .test_d import INSTRUCTION, POINTS, judgment, PROMPTS
from .test_d_evaluation import fixture_run, read_rows


def directions():
    return [
        {'id': 'r001', 'requirement': '两把工具均为六角扳手。', 'dimension': 'task_correctness',
         'is_core': True, 'basis': '题面限定工具身份。', 'criterion': '可辨认为六角扳手。', 'applicability': None},
        {'id': 'r002', 'requirement': '清楚呈现工作端结构。', 'dimension': 'task_correctness',
         'is_core': True, 'basis': '端面用于判断六角柱形。', 'criterion': '工作端呈可辨认的六角柱形。', 'applicability': None},
        {'id': 'r003', 'requirement': '非核心知识细节符合题面。', 'dimension': 'task_correctness',
         'is_core': False, 'basis': '固定夹具中的次要知识要求。', 'criterion': '相应细节可见且正确。', 'applicability': None},
        {'id': 'r004', 'requirement': '两把扳手均为红色。', 'dimension': 'other_instruction_following',
         'is_core': None, 'basis': '题面明确指定红色。', 'criterion': '主体为红色。', 'applicability': None},
    ]


def manifest(items=None):
    return frozen_requirements({'review_status': 'ready', 'question_revision': 'fixture-reviewed-1',
                                'requirements_json': json.dumps(directions() if items is None else items)})


def response(values=(2, 2, 2, 2), *, items=None, issue='inconclusive'):
    result = {k: v for k, v in judgment().items() if k != 'task_correctness'}
    result['requirement_scores'] = [{'id': item['id'], 'score': value,
        'issue': issue if value is None else None, 'reason': '固定测试返回的可见证据。'}
        for item, value in zip(directions() if items is None else items, values)]
    return result


def score(result=None, items=None):
    return score_d(result or response(items=items), instruction=INSTRUCTION, test_points=POINTS,
                   core_requirements=manifest(items))


def test_mapping_group_weights_core_gate_and_general_independence():
    all_two = score()
    assert all_two['overall_score'] == 100
    assert all_two['task_correctness_raw_score'] is None
    all_one = score(response((1, 1, 1, 1)))
    assert all_one['task_correctness_score'] == pytest.approx(60)
    # Quality and aesthetics retain A's returned 2; no accidental rescaling.
    assert all_one['quality_score'] == all_one['aesthetics_score'] == 100
    assert all_one['overall_score'] == pytest.approx(68)
    mixed = score(response((1, 2, 0, 1)))
    assert mixed['task_correctness_core_score'] == 80
    assert mixed['task_correctness_noncore_score'] == 0
    assert mixed['general_instruction_following_score'] == 60
    assert mixed['task_correctness_score'] == pytest.approx(480 / 7)
    assert mixed['overall_score'] == pytest.approx(74)
    core_failure = score(response((0, 2, 2, 2)))
    assert core_failure['task_correctness_core_score'] == 0
    assert core_failure['overall_score'] == pytest.approx(40)
    general_failure = score(response((2, 2, 2, 0)))
    assert general_failure['task_correctness_score'] == 100
    assert general_failure['overall_score'] == pytest.approx(90)


def test_empty_groups_and_conditional_na_normalize_only_applicable_weights():
    items = directions()[:2]
    result = score(response((1, 1), items=items), items)
    assert result['task_correctness_noncore_status'] == 'not_applicable'
    assert result['general_instruction_following_status'] == 'not_applicable'
    assert result['task_correctness_score'] == 60
    assert result['overall_score'] == pytest.approx(70)
    assert result['effective_weights']['task_correctness_core'] == pytest.approx(.75)
    items = directions()[2:]
    items[1]['applicability'] = '若出现背景文字'
    result = score(response((1, 'N/A'), items=items), items)
    assert result['task_correctness_core_status'] == 'not_applicable'
    assert result['general_instruction_following_status'] == 'not_applicable'
    assert result['overall_score'] == pytest.approx(260 / 3)


@pytest.mark.parametrize('index', range(4))
@pytest.mark.parametrize('issue', ['inconclusive', 'invalid_criterion'])
def test_anomaly_is_not_na_or_zero_and_never_redistributes_weights(index, issue):
    values = [2, 2, 2, 2]; values[index] = None
    result = score(response(values, issue=issue))
    group = ('task_correctness_core', 'task_correctness_core',
             'task_correctness_noncore', 'general_instruction_following')[index]
    assert result[group + '_score'] is None
    assert result[group + '_status'] == issue
    assert result['overall_score'] is None
    assert result['effective_weights'] == result['weights']
    assert result['quality_score'] == result['aesthetics_score'] == 100


def test_core_zero_does_not_hide_judging_anomaly():
    result = score(response((0, None, 2, 2)))
    assert result['task_correctness_core_score'] is None
    assert result['overall_score'] is None


def test_visual_na_keeps_a_item_mapping_and_group_denominators():
    raw = response()
    raw['quality'] = {k: 'N/A' for k in raw['quality']}
    raw['aesthetics']['composition'] = 1
    result = score(raw)
    assert result['quality_status'] == 'not_applicable'
    assert result['aesthetics_score'] == 90
    assert result['overall_score'] == pytest.approx(890 / 9)
    assert 'quality' not in result['effective_weights']


@pytest.mark.parametrize('change', ['missing', 'duplicate', 'extra', 'null_without_issue', 'score_with_issue',
                                   'bool', 'float', 'unconditional_na', 'unknown_issue'])
def test_only_numeric_contract_and_fixed_id_correspondence_are_checked(change):
    raw = response(); items = raw['requirement_scores']
    if change == 'missing': items.pop()
    elif change == 'duplicate': items[-1]['id'] = items[0]['id']
    elif change == 'extra': items[-1]['id'] = 'invented'
    elif change == 'null_without_issue': items[-1]['score'] = None
    elif change == 'score_with_issue': items[-1]['issue'] = 'inconclusive'
    elif change == 'bool': items[-1]['score'] = True
    elif change == 'float': items[-1]['score'] = 1.0
    elif change == 'unconditional_na': items[-1]['score'] = 'N/A'
    elif change == 'unknown_issue': items[-1].update(score=None, issue='other')
    with pytest.raises((ValueError, TypeError)):
        score(raw)
    # Output order and evidence wording do not change mathematical correspondence.
    raw = response(); raw['requirement_scores'].reverse()
    raw['requirement_scores'][0]['reason'] = '不含题面逐字引用，也不接受代码语义复核。'
    assert score(raw)['overall_score'] == 100


def test_fixed_manifest_keeps_original_review_text_and_alias_only():
    original = directions(); frozen = manifest(original)
    assert frozen['requirements'][:3] == original[:3]
    assert frozen['requirements'][3] == {**original[3], 'dimension': 'general_instruction_following'}
    for row in ({'review_status': 'hold', 'question_revision': 'x', 'requirements_json': '[]'},
                {'review_status': 'ready', 'question_revision': '', 'requirements_json': '[]'}):
        with pytest.raises(ValueError): frozen_requirements(row)


def test_prompt_preserves_a_visual_standards_schema_and_concise_contract():
    definition = load_prompt_pack(PROMPTS / 'd7.yaml').prompt_definitions['judge_d']
    old = load_prompt_pack(PROMPTS.parent / 'archive/d6/prompts/d.yaml').prompt_definitions['judge_d']
    text = definition.template.source
    visual = lambda t: t.split('## 视觉质量\n', 1)[1].split('## 输出要求\n', 1)[0]
    assert visual(text) == visual(old.template.source).replace('任务正确性或其他题面遵循', '任务正确性或通用题面遵循')
    a = load_prompt_pack(PROMPTS / 'tasks.yaml').prompt_definitions['judge']
    props = definition.response_schema['properties']['result']['properties']
    for name in ('quality', 'quality_reasons', 'aesthetics', 'aesthetics_reasons'):
        assert props[name] == a.response_schema['properties']['result']['properties'][name]
    assert set(props) == {'requirement_scores', 'quality', 'quality_reasons', 'aesthetics', 'aesthetics_reasons'}
    assert set(props['requirement_scores']['items']['properties']) == {'id', 'score', 'issue', 'reason'}
    for unnecessary in ('60%', '70%', '0/60/100', '归一化', '2/3', 'test_points', 'taxonomy',
                        'instruction_quote', 'source_point_indices', '统一判断规则', '要求准备'):
        assert unnecessary not in text
    assert '完整、清楚、准确满足' in text and '必要内容已经错误或遗漏，应判 0' in text
    levels = [len(s) - len(s.lstrip('#')) for s in text.splitlines() if s.startswith('#')]
    assert levels[0] == 2 and all(b <= a + 1 for a, b in zip(levels, levels[1:]))


def test_native_two_arm_fixed_checklist_actual_inputs_reuse_storage_and_viewer(tmp_path):
    run, source, cfg = fixture_run(legacy=False, protocol='d7', requirements=directions())
    assert cfg['judge']['reasoning_effort'] == 'xhigh'
    first = pipeline.run_pipeline(run, source, cfg)
    assert first['phase'] == 'awaiting_d_responses'
    actual = {}
    for i in range(2):
        for row in read_rows(first['outputs'][f'arm{i}']):
            call = json.loads(row['d_call_json']); request = read_call(call['request_ref'])
            assert request['prompt_version'] == 't2i-v2-d-judge-7-review'
            parts = [part for message in request['messages'] if isinstance(message['content'], list)
                     for part in message['content']]
            assert sum(p['type'] == 'image_url' for p in parts) == 1
            text = ''.join(p['text'] for p in parts if p['type'] == 'text')
            sent = json.loads(text.split('### 题目材料\n\n')[1].split('\n\n### 作答图')[0])
            assert sent == {'instruction': INSTRUCTION, 'requirements': manifest()['requirements']}
            assert 'fixture-0' not in text and 'fixture-1' not in text
            assert 'fixture-reviewed-1' not in text and 'taxonomy' not in text
            actual.setdefault(row['task_id'], []).append(sent)
            values = (1, 2, 2, 2) if i == 0 else (2, 2, 2, 0)
            submit_response(resolve_root(), call['request_ref'], json.dumps({'result': response(values)}), model=call['model'])
    assert all(values[0] == values[1] for values in actual.values())
    complete = pipeline.run_pipeline(run, source, cfg)
    assert complete['complete'] and complete['phase'] == 'paused_after_sample'
    rows = []
    for i in range(2):
        arm = read_rows(complete['outputs'][f'arm{i}']); rows += arm
        for row in arm:
            assert row['d_protocol'] == 'd7' and row['d_status'] == 'reviewed'
            assert row['question_revision'] == 'fixture-reviewed-1'
            assert json.loads(row['core_requirements_json']) == manifest()
            assert row['d_core_score'] == (80 if i == 0 else 100)
            assert row['d_general_score'] == (100 if i == 0 else 0)
            assert row['d_other_score'] is None and row['d_raw_task_score'] is None
            assert score_table.calculate_d(row)['recalculated']['overall_score'] == row['d_overall_score']
    scope = read_rows(complete['outputs']['arm0'])
    payload = score_table.build_payload(scope, [], [score_table.calculate_d(r) for r in rows], sources={})
    columns = {c[0] for c in payload['columns']}
    assert {'d_core', 'd_noncore', 'd_general'} <= columns and 'd_other' not in columns
    assert 'D 核心正确性' in score_table.export_csv(payload)
    html, meta = build_case_browser(resolve_root() / 'demiwtg', run.name)
    assert 'Review 固定检查方向与题目版本' in html and 'd7Arm' in html
    assert 'general_instruction_following' in html
    assert meta['prompt_version'] == 't2i-v2-d-judge-7-review'
    path = tmp_path / 'd7_cases.html'; path.write_text(html)
    from playwright.sync_api import sync_playwright
    with sync_playwright() as play:
        browser = play.chromium.launch(executable_path=play.chromium.executable_path, args=['--no-sandbox'])
        page = browser.new_page(); errors = []
        page.on('pageerror', lambda e: errors.append(str(e)))
        page.goto(path.as_uri())
        page.locator('#search').fill('夹具0')
        assert page.locator('.arm').count() == 2
        assert page.locator('h4', has_text='核心正确性 · 80.00').count() == 1
        assert page.locator('h4', has_text='通用题面遵循 · 0.00').count() == 1
        assert '固定检查方向 requirements' in ''.join(page.locator('.actual-prompt').all_text_contents())
        assert errors == []
        browser.close()
    assert pipeline.run_pipeline(run, source, cfg) == complete


def test_question_change_is_not_reused_and_unreviewed_source_is_rejected():
    run, source, cfg = fixture_run(legacy=False, protocol='d7', requirements=directions())
    questions = data.read_lance(**source).take(3)
    questions[0]['instruction'] += ' 新的题面要求。'
    data.from_arrow(pa.Table.from_pylist(questions, schema=d_evaluation.D7_QUESTION_SCHEMA)).write_lance(
        source['uri'], mode='overwrite', schema=d_evaluation.D7_QUESTION_SCHEMA)
    state = pipeline.run_pipeline(run, {**source, 'version': 2}, cfg)
    for i in range(2):
        rows = read_rows(state['outputs'][f'arm{i}'])
        by_id = {r['task_id']: r for r in rows}
        assert by_id['q0']['d_status'] == 'answer_mismatch'
        assert by_id['q0']['instruction'] == INSTRUCTION  # 保留旧图真正作答的题面。
        assert not json.loads(by_id['q0']['d_call_json'])
        assert by_id['q1']['d_status'] == 'pending'
    questions[0]['review_status'] = 'hold'
    data.from_arrow(pa.Table.from_pylist(questions, schema=d_evaluation.D7_QUESTION_SCHEMA)).write_lance(
        source['uri'], mode='overwrite', schema=d_evaluation.D7_QUESTION_SCHEMA)
    with pytest.raises(ValueError, match='ready Review'):
        pipeline.run_pipeline(run.with_name('unreviewed'), {**source, 'version': 3}, cfg)


def test_standard_operator_two_arm_requests_and_persisted_scores_without_relation_slots(tmp_path):
    """隔离验证本次改变的节点契约；完整关系编排由上面的集成用例覆盖。"""
    run, source, cfg = fixture_run(legacy=False, protocol='d7', requirements=directions())
    ctx = DataAPI()
    questions = read_rows(source)[:2]
    rows = []
    for model in cfg['answers']:
        answers = {r['task_id']: r for r in read_rows(model['source'])}
        rows.extend(d_evaluation.prepare_score({**q, 'prior_answer': answers[q['task_id']]},
                    root=resolve_root(), model=model, judge=cfg['judge'], available_chars=50000,
                    single_pass=True, protocol='d7') for q in questions)
    pack = load_prompt_pack(PROMPTS / 'd7.yaml')
    def evaluate():
        outputs = []
        (ctx.from_items(rows).map_prompt_async('judge_d', config=pack,
            options={'offline_store': {'path': str(tmp_path / 'd7_calls.sqlite'), 'max_requests': 0}},
            max_requests=0, concurrency=2, inputs={'payload': 'd_payload', 'images': 'd_images'},
            output='d_result', call_output='d_call', error_output='d_error')
            .map(d_evaluation.finish_score).map(lambda r: outputs.append(r) or r).run_stream())
        return outputs
    first = evaluate()
    payloads = []
    for row in first:
        assert row['d_status'] == 'pending'
        call = json.loads(row['d_call_json']); request = read_call(call['request_ref'])
        assert request['prompt_version'] == 't2i-v2-d-judge-7-review'
        parts = [p for m in request['messages'] if isinstance(m['content'], list) for p in m['content']]
        text = ''.join(p['text'] for p in parts if p['type'] == 'text')
        assert [p['image_url']['url'] for p in parts if p['type'] == 'image_url'] == row['d_images']
        payloads.append(json.loads(text.split('### 题目材料\n\n')[1].split('\n\n### 作答图')[0]))
        assert payloads[-1] == {'instruction': INSTRUCTION, 'requirements': manifest()['requirements']}
        assert 'fixture-0' not in text and 'fixture-1' not in text
        submit_response(resolve_root(), call['request_ref'], json.dumps({'result': response((1, 2, 2, 0))}), model=call['model'])
    assert len(payloads) == 4
    scored = evaluate()
    assert all(r['d_status'] == 'reviewed' and r['d_core_score'] == 80 and r['d_general_score'] == 0 for r in scored)
    assert ctx.prompt_usage()['provider_requests_started'] == 0
    uri = str(tmp_path / 'scores.lance')
    data.from_arrow(pa.Table.from_pylist(scored, schema=pipeline.D_SCORES)).write_lance(
        uri, mode='create', schema=pipeline.D_SCORES)
    stored = read_rows({'uri': uri, 'version': 1})
    recalculated = [score_table.calculate_d(r) for r in stored]
    assert all(r['recalculated']['overall_score'] == pytest.approx(78) for r in recalculated)
    payload = score_table.build_payload(questions, [], recalculated, sources={})
    assert {'d_core', 'd_noncore', 'd_general'} <= {v[0] for v in payload['columns']}
    assert 'd_other' not in {v[0] for v in payload['columns']}
    html = score_table.render(payload)
    path = tmp_path / 'd7_table.html'; path.write_text(html)
    from playwright.sync_api import sync_playwright
    with sync_playwright() as play:
        browser = play.chromium.launch(executable_path=play.chromium.executable_path, args=['--no-sandbox'])
        page = browser.new_page(); errors = []
        page.on('pageerror', lambda e: errors.append(str(e)))
        page.goto(path.as_uri())
        assert page.locator('tbody td[data-column="d_core"]').all_text_contents() == ['80.00'] * 4
        assert page.locator('tbody td[data-column="d_general"]').all_text_contents() == ['0.00'] * 4
        page.locator('#search').fill('不存在的题目')
        assert page.locator('td.empty').inner_text() == '没有匹配的题目'
        assert errors == []
        browser.close()
