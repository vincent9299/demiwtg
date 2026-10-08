"""四维边界的隔离验证：外围项不稀释正确性、未知不送分、实际标准节点与查看。"""
from copy import deepcopy
import json
from pathlib import Path

import pytest
from demiflow.operator_llm.call_ref import read_call
from demiflow.operator_llm.parser import load_prompt_pack
from demiflow.operator_llm.sqlite_offline import submit_response
from project import resolve_root
from evaluation.t2i.v2 import t2i_v2_eval_pipeline as pipeline
from evaluation.t2i.v2.operators.d_scores import score_d, d6_requirements
from evaluation.t2i.v2.operators.case_viewer import build_case_browser
from .test_d import INSTRUCTION, POINTS, judgment, PROMPTS
from .test_d_evaluation import fixture_run, read_rows


def requirement(index, quote, dimension='task_correctness', core=False, refs=None):
    return {'index': index, 'instruction_quote': quote, 'requirement': '核验' + quote,
            'source_point_indices': [1] if refs is None else refs, 'dimension': dimension,
            'is_core': core, 'applicability': None,
            'reason': '工作端结构用于判断目标身份；红色是题面另加的配色要求'}


def plan():
    return {'protocol': 'd6', 'requirements': [requirement(1, '两把', core=True),
        requirement(2, '红色', 'other_instruction_following', refs=[])],
        'coverage_reason': '原考点承接两把工具的结构与可判断性；颜色独立归其他题面遵循'}


def response(*states, manifest=None):
    result = {k: v for k, v in judgment().items() if k != 'task_correctness'}
    manifest = manifest or plan()
    result.update(task_correctness={'checks': [], 'issues': []},
                  other_instruction_following={'checks': [], 'issues': []})
    states = states or ('pass',) * len(manifest['requirements'])
    assert len(states) == len(manifest['requirements'])
    for item, state in zip(manifest['requirements'], states):
        dimension = item['dimension']
        check = {key: item[key] for key in ('instruction_quote', 'requirement', 'source_point_indices', 'applicability')}
        if dimension == 'task_correctness':
            check['is_core'] = item['is_core']
        elif item['is_core']:
            # 用于校验其他遵循不得带核心标记。
            check['is_core'] = True
        check.update(verdict=state, reason='工作端结构用于核验工具身份；图中相应结构和颜色可见，逐项对照题面判断')
        result[dimension]['checks'].append(check)
    if not result['other_instruction_following']['checks']:
        result['other_instruction_following'] = None
    return result


def score(result=None, manifest=None, instruction=INSTRUCTION, points=POINTS):
    return score_d(result or response(manifest=manifest), instruction=instruction, test_points=points)


def test_other_failure_never_changes_correctness_denominator_or_core_gate():
    passed, failed = score(), score(response('pass', 'fail'))
    assert passed['task_correctness_score'] == failed['task_correctness_score'] == 100
    assert failed['task_correctness_counts']['total'] == 1
    assert failed['other_instruction_following_score'] == 0
    assert passed['overall_score'] == pytest.approx(100)
    assert failed['overall_score'] == pytest.approx(90)
    core_failure = score(response('fail', 'pass'))
    assert core_failure['task_correctness_score'] == 0
    assert core_failure['task_correctness_verdict'] == 'fail'
    assert core_failure['overall_score'] == pytest.approx(30)


def test_missing_knowledge_requirement_can_be_added_and_veto_original_point_pass():
    instruction = '生成U形谷，冰川已完全消退。'
    manifest = {'protocol': 'd6', 'requirements': [requirement(1, 'U形谷', core=True),
        requirement(2, '冰川已完全消退', core=True, refs=[])], 'coverage_reason': '补充原考点遗漏的退冰状态'}
    points = [{'point': '槽形断面', 'basis': '冰川侵蚀谷的形态', 'criterion': '谷地断面呈U形槽状'}]
    scored = score(response('pass', 'fail', manifest=manifest), instruction=instruction, points=points)
    assert scored['task_correctness_score'] == 0
    assert scored['other_instruction_following_status'] == 'not_applicable'


def test_two_of_three_task_requirements_map_to_sixty_without_other_dilution():
    manifest = plan()
    manifest['requirements'] += [requirement(3, '六角扳手', refs=[]),
                                  requirement(4, '清楚展示工作端', refs=[])]
    result = score(response('pass', 'fail', 'pass', 'fail', manifest=manifest))
    assert result['task_correctness_raw_score'] == 1
    assert result['task_correctness_score'] == 60
    assert result['task_correctness_pass_rate'] == 2 / 3
    assert result['other_instruction_following_score'] == 0
    assert result['overall_score'] == pytest.approx(62)


def test_unknown_other_is_not_na_and_does_not_trigger_weight_redistribution():
    result = score(response('pass', 'inconclusive'))
    assert result['task_correctness_score'] == 100
    assert result['other_instruction_following_score'] is None
    assert result['other_instruction_following_pass_rate_lower'] == 0
    assert result['other_instruction_following_pass_rate_upper'] == 1
    assert result['overall_score'] is None
    assert result['effective_weights']['task_correctness'] == pytest.approx(.7)
    manifest = plan(); manifest['requirements'].pop()
    absent_response = response('pass', manifest=manifest)
    assert absent_response['other_instruction_following'] is None
    absent = score(absent_response)
    assert absent['other_instruction_following_status'] == 'not_applicable'
    assert absent['other_instruction_following_score'] is None
    assert absent['effective_weights']['task_correctness'] == pytest.approx(7 / 9)
    assert absent['overall_score'] == pytest.approx(100)


def test_returned_not_applicable_is_excluded_without_reviewing_condition_text():
    result = score(response('pass', 'not_applicable'))
    assert result['other_instruction_following_status'] == 'not_applicable'
    assert result['other_instruction_following_score'] is None
    assert result['task_correctness_score'] == 100
    assert result['overall_score'] == pytest.approx(100)
    assert result['effective_weights']['task_correctness'] == pytest.approx(7 / 9)
    raw = response('pass', 'not_applicable')
    raw['other_instruction_following']['checks'][0]['applicability'] = '题面没有的条件'
    assert score(raw) == result


@pytest.mark.parametrize('dimension', ['task_correctness', 'other_instruction_following'])
def test_plan_problem_keeps_affected_dimension_unknown_and_overall_empty(dimension):
    result = response()
    result[dimension]['issues'] = ['考点义项与题面存在无法解决的冲突']
    scored = score(result)
    assert scored[dimension + '_score'] is None and scored['overall_score'] is None
    assert scored['quality_score'] == 100
    if dimension == 'other_instruction_following':
        assert scored['task_correctness_score'] == 100


@pytest.mark.parametrize('mutation', ['missing', 'duplicate', 'source', 'quote', 'condition', 'reason'])
def test_explanations_and_source_annotations_never_gate_calculation(mutation):
    raw = response()
    expected = score(raw)
    task = raw['task_correctness']['checks'][0]
    other = raw['other_instruction_following']['checks'][0]
    if mutation == 'missing': task['source_point_indices'] = []
    elif mutation == 'duplicate': other['requirement'] = task['requirement']
    elif mutation == 'source': task['source_point_indices'] = [5, 1, 5]
    elif mutation == 'quote': other['instruction_quote'] = '两把；红色；其他文字'
    elif mutation == 'condition': other['applicability'] = '题面没有的条件'
    else: task['reason'] = '判官给出的说明'
    assert score(raw, instruction=None, points=None) == expected


def test_order_does_not_affect_score_and_original_response_is_preserved():
    manifest = {'protocol': 'd6', 'requirements': [
        requirement(1, '清楚展示工作端', refs=[]),
        requirement(2, '六角扳手', refs=[]),
        requirement(3, '两把', core=True)]}
    raw = response('fail', 'pass', 'pass', manifest=manifest)
    before = deepcopy(raw)
    result = score(raw)
    assert raw == before
    assert result['task_correctness_score'] == 60
    assert result['task_correctness_core_counts']['pass'] == 1
    assert 'validation_errors' not in result and 'requirement_order' not in result
    assert result['scoring_revision'] == 'd6-calculation-only-v1'
    raw['task_correctness']['checks'].reverse()
    assert score(raw) == result


def test_calculation_requires_known_verdicts_and_numeric_grades():
    for change in ('wrong_verdict', 'wrong_grade'):
        raw = response()
        if change == 'wrong_verdict': raw['task_correctness']['checks'][0]['verdict'] = 'maybe'
        else: raw['quality']['resolution'] = 3
        with pytest.raises(ValueError): score(raw)


def test_every_returned_check_counts_without_semantic_deduplication():
    raw = response('pass', 'pass')
    extra = deepcopy(raw['task_correctness']['checks'][0])
    extra.update(is_core=False, verdict='fail')
    raw['task_correctness']['checks'].append(extra)
    result = score(raw)
    assert result['task_correctness_counts']['total'] == 2
    assert result['task_correctness_score'] == 0
    assert result['other_instruction_following_score'] == 100


@pytest.mark.parametrize('dimension,key', [('quality', 'resolution'), ('aesthetics', 'composition')])
def test_visual_na_is_excluded_as_returned_without_rejudging_applicability(dimension, key):
    raw = response()
    count = len(raw[dimension])
    raw[dimension][key] = 'N/A'
    result = score(raw)
    assert result[dimension + '_score'] == 100
    assert result['valid_items'][dimension] == count - 1
    assert result['overall_score'] == pytest.approx(100)
    assert 'validation_errors' not in result


def test_all_visual_na_leaves_score_unknown_without_redistributing_its_weight():
    raw = response()
    raw['quality'] = {key: 'N/A' for key in raw['quality']}
    result = score(raw)
    assert result['quality_score'] is None
    assert result['valid_items']['quality'] == 0
    assert result['aesthetics_score'] == 100
    assert result['overall_score'] is None
    assert result['effective_weights']['quality'] == .1


def test_finish_score_calculates_and_preserves_unmatched_quotes_as_returned():
    from evaluation.t2i.v2.operators.d_evaluation import finish_score
    raw = response()
    raw['other_instruction_following']['checks'][0]['instruction_quote'] = '不存在的题面'
    result = finish_score({'instruction': INSTRUCTION, 'test_points': POINTS,
        'd_status': 'ready', 'd_protocol': 'd6', 'd_result': raw,
        'd_call': {'fixture': 'original-request'}, 'd_error': None})
    assert result['d_status'] == 'reviewed' and result['d_reason'] == ''
    assert json.loads(result['d_json']) == raw
    assert json.loads(result['d_call_json']) == {'fixture': 'original-request'}
    for field in ('d_task_score', 'd_quality_score', 'd_aesthetics_score', 'd_other_score', 'd_overall_score'):
        assert result[field] == 100
    assert 'validation_errors' not in json.loads(result['d_metrics_json'])


def test_d6_native_two_arm_requests_scores_and_viewer(tmp_path):
    run, source, cfg = fixture_run(legacy=False)
    assert 'core_prompt_pack' not in cfg['d_evaluation']
    first = pipeline.run_pipeline(run, source, cfg)
    assert first['phase'] == 'awaiting_d_responses'
    assert set(first['outputs']) == {'arm0', 'arm1'}
    assert not (run.parent / f'd_core__{run.name}.lance').exists()
    assert not (resolve_root() / '_demiflow/evaluation_t2i_v2' / run.name / 'core_snapshot.json').exists()
    a_text = load_prompt_pack(PROMPTS / 'tasks.yaml').prompt_definitions['judge'].template.source
    for index in range(2):
        for row in read_rows(first['outputs'][f'arm{index}']):
            call = json.loads(row['d_call_json']); request = read_call(call['request_ref'])
            assert request['prompt_version'] == 't2i-v2-d-judge-6-review'
            parts = [part for message in request['messages'] if isinstance(message['content'], list)
                     for part in message['content']]
            assert sum(p['type'] == 'image_url' for p in parts) == 1
            text = ''.join(p['text'] for p in parts if p['type'] == 'text')
            assert '其他题面遵循' in text and '不是完整清单' in text
            assert 'fixture-0' not in text and 'fixture-1' not in text
            # 判图请求只给核验所需字段；聚合策略和准备判断不得进入实际消息。
            sent = json.loads(text.split('### 题目材料\n\n', 1)[1].split('\n\n### 作答图', 1)[0])
            assert sent == {'instruction': INSTRUCTION, 'test_points': POINTS}
            assert 'concept' not in text and 'taxonomy' not in text and row['concept'] not in text
            for unused in ('70%', '2/3', '0/60/100', 'P/N', '归一化', '代码计算',
                           'protocol=d6', '沿用 A', '完整复制A', '新版本重评', '准备阶段', '固定清单'):
                assert unused not in text
            lines = text.splitlines()
            levels = [len(line) - len(line.lstrip('#')) for line in lines if line.startswith('#')]
            assert levels[0] == 2 and all(b <= a + 1 for a, b in zip(levels, levels[1:]))
            assert [line for line in lines if line.startswith('## ')] == [
                '## 任务与输入', '## 任务正确性', '## 其他题面遵循', '## 视觉质量',
                '## 美感', '## 输出要求', '## 本次输入']
            assert '统一判断规则' not in text
            assert not any('核心判断：' in line or '补充边界：' in line for line in lines if line.startswith('#'))
            # 按维度组织；各维度保留A细项原文、合格/出色档位和未知边界。
            body_only = lambda value: '\n'.join(line.strip() for line in value.splitlines()
                                               if line.strip() and not line.lstrip().startswith('#'))
            grade_section = a_text.split('## 三、评分档位\n', 1)[1].split('## 四、对齐\n', 1)[0]
            grades = {line.split('. ', 1)[0]: line.split('. ', 1)[1]
                      for line in grade_section.splitlines() if '. ' in line and line[0].isdigit()}
            for start, end, target, next_target in (
                    ('## 五、质量\n', '## 六、美感\n', '## 视觉质量\n', '## 美感\n'),
                    ('## 六、美感\n', '## 七、输出\n', '## 美感\n', '## 输出要求\n')):
                dimension = text.split(target, 1)[1].split(next_target, 1)[0]
                assert body_only(a_text[a_text.index(start):a_text.index(end)]) in body_only(dimension)
                for number in ('2', '3', '5', '6', '8', '9'):
                    assert grades[number] in dimension
                assert '`0`：存在该项准则所述的明显缺陷。' in dimension
                assert '按题目指定的风格判断；未指定时，按画面有明确依据的表达方式判断。' in dimension
            assert row['core_requirements_json'] is None
            assert row['core_call_json'] is None and row['core_plan_json'] is None
            submit_response(resolve_root(), call['request_ref'], json.dumps({'result': response(
                'pass', 'pass' if index else 'fail')}, ensure_ascii=False), model=call['model'])
    final = pipeline.run_pipeline(run, source, cfg)
    assert final['complete'] and final['phase'] == 'paused_after_sample'
    assert set(final['outputs']) == {'arm0', 'arm1'}
    for index in range(2):
        for row in read_rows(final['outputs'][f'arm{index}']):
            assert row['core_call_json'] is None and row['core_plan_json'] is None
            assert json.loads(row['core_requirements_json']) == d6_requirements(json.loads(row['d_json']))
    for mode, other, overall in [('text_only', 0, 90), ('positive_images', 100, 100)]:
        dims = final['counts_by_arm'][mode]['dimensions']
        assert dims['task_correctness'] == {'mean': 100, 'valid': 2}
        assert dims['other_instruction_following'] == {'mean': other, 'valid': 2}
        assert dims['overall']['mean'] == pytest.approx(overall)
    html, meta = build_case_browser(resolve_root() / 'demiwtg', run.name)
    assert meta['prompt_version'] == 't2i-v2-d-judge-6-review'
    assert meta['paired_dimensions']['other_instruction_following']['paired_count'] == 2
    assert '其他题面遵循' in html and '权重 70/10/10/10' in html
    (tmp_path / 'd6_viewer.html').write_text(html)
    from playwright.sync_api import sync_playwright
    with sync_playwright() as play:
        browser = play.chromium.launch(executable_path=play.chromium.executable_path, args=['--no-sandbox'])
        page = browser.new_page()
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.set_content(html)
        assert '四维评分' in page.locator('h1').inner_text()
        assert '其他题面遵循' in page.locator('#progress').inner_text()
        assert '核心门槛' in page.locator('#cases').inner_text()
        assert '来源：题面补充' in page.locator('#cases').inner_text()
        assert '要求准备' not in page.locator('#cases').inner_text()
        assert '评图前固定' not in page.locator('#cases').inner_text()
        assert '本次评审的要求与计分记录' in page.locator('#cases').inner_text()
        page.click('#next')
        assert '2. 夹具1' in page.locator('#cases h2').inner_text()
        assert not errors
        browser.close()
    assert pipeline.run_pipeline(run, source, cfg) == final
