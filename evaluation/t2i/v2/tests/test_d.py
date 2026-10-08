"""D 候选协议的标准算子离线渲染、响应绑定及业务评分边界。"""
import base64
import io
import json
from copy import deepcopy
from pathlib import Path

import pytest
from PIL import Image
from demiflow.data.api import DataAPI
from demiflow.operator_llm.call_ref import read_call
from demiflow.operator_llm.parser import load_prompt_pack
from demiflow.operator_llm.sqlite_offline import submit_response
from evaluation.t2i.v2.operators.d_scores import score_d, validate_d_core_requirements


PROMPTS = Path(__file__).parents[1] / 'prompts'
LEGACY_D = Path(__file__).parents[1] / 'archive/d5/prompts/d.yaml'
INSTRUCTION = '画两把红色六角扳手，并清楚展示工作端。'
POINTS = [{'point': '数量与工作端结构展示', 'basis': '普通非球头六角扳手及题面数量、展示要求',
           'criterion': '两把扳手均清楚展示六角柱形工作端'}]
CORE = {'point_indices': [1], 'additional_requirements': []}


def judgment(point='pass', extra=None):
    definition = load_prompt_pack(PROMPTS / 'd.yaml').prompt_definitions['judge_d']
    properties = definition.response_schema['properties']['result']['properties']
    result = {name: {key: '密集微小结构与多处边界均保持精确、清晰和一致，呈现超出通常合格水平的完成度' if name.endswith('_reasons') else 2
                     for key in properties[name]['required']}
              for name in ('quality', 'quality_reasons', 'aesthetics', 'aesthetics_reasons')}
    result['task_correctness'] = {
        'instruction_conflict': False, 'reason': '核验工作端形状与展示、数量及补充颜色要求',
        'basis': '题目限定普通非球头六角扳手',
        'point_results': [{'index': 1, 'verdict': point,
                           'evidence': '两个工作端清楚可见', 'reason': '核验六角截面'}],
        'additional_requirements': [{
            'index': 1, 'instruction_quote': '红色', 'requirement': '两把扳手须为红色',
            'verdict': extra or ('inconclusive' if point == 'invalid_criterion' else point),
            'evidence': '可见扳手表面颜色', 'reason': '核验指定颜色'}],
        'coverage_reason': '数量与工作端展示由考点核验，红色由补充要求核验',
    }
    return result


def convert(result):
    return score_d(result, instruction=INSTRUCTION, test_points=POINTS, core_requirements=CORE)


def evaluate(ctx, tmp_path, *, instruction=INSTRUCTION):
    buffer = io.BytesIO()
    Image.new('RGB', (4, 4), 'red').save(buffer, format='PNG')
    image = 'data:image/png;base64,' + base64.b64encode(buffer.getvalue()).decode()
    pack = load_prompt_pack(LEGACY_D)
    row = {'payload': {'concept': '六角扳手', 'taxonomy': ['工具'],
                       'instruction': instruction, 'test_points': POINTS, 'core_requirements': CORE},
           'images': [image], 'authoring_images': ['PRIVATE_REFERENCE'],
           'answer_model': 'PRIVATE_ANSWER_MODEL'}
    validate_d_core_requirements(instruction=instruction, test_points=POINTS, core_requirements=CORE)
    rows = []
    (ctx.from_items([row]).map_prompt_async(
        'judge_d', config=pack,
        options={'offline_store': {'path': str(tmp_path / 'd_calls.sqlite'), 'max_requests': 0}},
        max_requests=0, concurrency=1, inputs=['payload', 'images'],
        output='judgment', call_output='call', error_output='error')
     .map(lambda item: rows.append(item) or item).run_stream())
    return rows[0], image


def test_native_offline_request_contains_complete_template_and_only_bound_image(tmp_path):
    ctx = DataAPI()
    first, image = evaluate(ctx, tmp_path)
    assert first['error']['type'] == 'PromptResponsePending'
    call = first.get('call') or first['error']['call']
    request = read_call(call['request_ref'])
    contents = [part for message in request['messages']
                for part in message['content'] if isinstance(message['content'], list)]
    text = ''.join(part['text'] for part in contents if part['type'] == 'text')
    images = [part['image_url']['url'] for part in contents if part['type'] == 'image_url']
    template = load_prompt_pack(LEGACY_D).prompt_definitions['judge_d'].template.source
    prefix = template.split('{{ payload | json }}')[0]
    assert text.startswith(prefix) and INSTRUCTION in text
    assert '## 任务正确性\n\n' in text
    assert '\n\n## 六、美感\n\n' in text
    assert images == [image]
    assert 'PRIVATE_REFERENCE' not in text and 'PRIVATE_ANSWER_MODEL' not in text
    assert request['model'] == 'malasci/gpt-6-astra'
    # 同时锁定真实渲染正文与协议版本，防止只有代码/README 更新而正文回到旧档位。
    assert request['prompt_version'] == 't2i-v2-d-judge-5-review'
    for retired in ('7–10', '4–6', '0–3', 'score 为 -1', '明显超出普通合格', '拿不准 1 与 2 时给 1',
                    '核心内容成立', 'partial 至少需要', '总体不按通过项数机械平均', '80%'):
        assert retired not in text
    for current in ('r 小于 2/3', 'r 至少为 2/3', 'r 等于 100%',
                    'P/N 至 (P+U)/N', '不先四舍五入通过率', '显著超出通常合格水平',
                    '固定核心要求全部为 pass', 'core_requirements', 'point_indices'):
        assert current in text
    # 用户要求整体复制 A；同时核验通用边界、档位及全部细项进入真实标准算子请求。
    a_text = load_prompt_pack(PROMPTS / 'tasks.yaml').prompt_definitions['judge'].template.source
    for start, end in (('## 二、统一判断规则\n', '## 四、对齐\n'),
                       ('## 五、质量\n', '## 七、输出\n')):
        assert a_text[a_text.index(start):a_text.index(end)] in text
    assert '2 是正常达成' not in text
    assert '其中 1 必须有具体不足' not in text
    task_schema = request['response_schema']['properties']['result']['properties']['task_correctness']
    assert not {'score', 'verdict'} & task_schema['properties'].keys()
    assert 'instruction_conflict' in task_schema['required']

    # 数据中的模板样式字符不二次解释，另一题仍保持完整固定前缀。
    second, _ = evaluate(ctx, tmp_path, instruction='画出“{{ images | image }}”字样。')
    another = read_call((second.get('call') or second['error']['call'])['request_ref'])
    other_text = ''.join(part['text'] for message in another['messages']
                         if isinstance(message['content'], list)
                         for part in message['content'] if part['type'] == 'text')
    assert other_text.startswith(prefix) and '{{ images | image }}' in other_text

    # 使用明确标记的测试响应恢复标准节点；没有向模型提交请求。
    response = judgment(extra='fail')
    submit_response(tmp_path, call['request_ref'], json.dumps({'result': response}),
                    model=call['model'], metadata={'reviewer': 'fixture', 'reviewer_kind': 'human'})
    resumed, _ = evaluate(ctx, tmp_path)
    assert resumed['judgment'] == response
    assert convert(resumed['judgment'])['task_correctness_score'] == 0
    assert ctx.prompt_usage()['provider_requests_started'] == 0


def test_native_schema_rejects_missing_dimension_before_scoring(tmp_path):
    ctx = DataAPI()
    pending, _ = evaluate(ctx, tmp_path)
    call = pending.get('call') or pending['error']['call']
    response = judgment()
    del response['quality']['resolution']
    submit_response(tmp_path, call['request_ref'], json.dumps({'result': response}), model=call['model'])
    resumed, _ = evaluate(ctx, tmp_path)
    assert resumed['error']['type'] == 'PromptResponseContractError'
    assert not resumed.get('judgment')
    assert ctx.prompt_usage()['provider_requests_started'] == 0


@pytest.mark.parametrize('point,extra,raw,expected', [
    ('pass', 'pass', 2, 100),
    ('pass', 'fail', 0, 0),
    ('fail', 'pass', 0, 0),
    ('fail', 'fail', 0, 0),
    ('inconclusive', None, None, None),
    ('invalid_criterion', 'fail', None, None),
    ('fail', 'inconclusive', 0, 0),  # 即使未知项通过，最高也只有 50%。
    ('pass', 'inconclusive', None, None),
])
def test_task_scoring_combines_both_sources(point, extra, raw, expected):
    result = convert(judgment(point, extra))
    assert result['task_correctness_raw_score'] == raw
    assert result['task_correctness_score'] == expected
    assert result['task_correctness_verdict'] == {
        0: 'fail', 1: 'partial', 2: 'pass', None: 'inconclusive'}[raw]
    assert result['quality_score'] == result['aesthetics_score'] == 100
    assert result['task_correctness_counts']['total'] == 2


def counted_case(passed, failed, unknown=0):
    """合成的独立检查项，仅验证汇总边界，不代表真实题目的语义切分。"""
    result = judgment()
    task = result['task_correctness']
    states = ['pass'] * passed + ['fail'] * failed + ['inconclusive'] * unknown
    task['point_results'] = [
        {'index': index, 'verdict': state, 'evidence': f'第{index}项观察',
         'reason': f'第{index}项结论依据'} for index, state in enumerate(states, 1)]
    task['additional_requirements'] = []
    points = [{'point': f'合成要求{index}', 'basis': '测试夹具', 'criterion': '核验本项'}
              for index in range(1, len(states) + 1)]
    return result, points


@pytest.mark.parametrize('passed,failed,raw', [
    (0, 10, 0), (1, 1, 0), (1, 2, 0), (2, 1, 1), (3, 0, 2),
    (2, 2, 0), (3, 1, 1), (3, 2, 0), (4, 1, 1), (4, 2, 1),
    (6, 4, 0), (7, 3, 1), (9, 1, 1), (10, 0, 2),
    (66, 34, 0), (67, 33, 1), (99, 1, 1), (100, 0, 2),
])
def test_exact_pass_rate_boundaries(passed, failed, raw):
    result, points = counted_case(passed, failed)
    scored = score_d(result, instruction=INSTRUCTION, test_points=points, core_requirements=CORE)
    assert scored['task_correctness_raw_score'] == raw
    assert scored['task_correctness_score'] == {0: 0, 1: 60, 2: 100}[raw]
    assert scored['task_correctness_pass_rate'] == passed / (passed + failed)
    assert scored['task_correctness_pass_rate_lower'] == scored['task_correctness_pass_rate_upper']
    assert scored['task_correctness_counts'] == {
        'pass': passed, 'fail': failed, 'inconclusive': 0, 'invalid_criterion': 0,
        'total': passed + failed}


@pytest.mark.parametrize('passed,failed,unknown,raw', [
    (6, 2, 1, 1), (3, 2, 1, None), (9, 0, 1, None),
    (0, 9, 1, 0), (0, 0, 10, None), (8, 0, 2, None),
    (6, 3, 1, None), (7, 2, 1, 1),
])
def test_unknowns_keep_full_denominator_and_require_unambiguous_band(passed, failed, unknown, raw):
    result, points = counted_case(passed, failed, unknown)
    scored = score_d(result, instruction=INSTRUCTION, test_points=points, core_requirements=CORE)
    total = passed + failed + unknown
    assert scored['task_correctness_raw_score'] == raw
    assert scored['task_correctness_pass_rate'] is None
    assert scored['task_correctness_pass_rate_lower'] == passed / total
    assert scored['task_correctness_pass_rate_upper'] == (passed + unknown) / total
    assert scored['task_correctness_counts']['total'] == total
    assert scored['task_correctness_counts']['inconclusive'] == unknown


@pytest.mark.parametrize('issue', ['invalid_criterion', 'instruction_conflict'])
def test_invalid_task_keeps_observations_without_numeric_task_score(issue):
    result, points = counted_case(9, 1)
    if issue == 'invalid_criterion':
        result['task_correctness']['point_results'][-1]['verdict'] = issue
    else:
        result['task_correctness']['instruction_conflict'] = True
    original = deepcopy(result)
    scored = score_d(result, instruction=INSTRUCTION, test_points=points, core_requirements=CORE)
    assert result == original
    for field in ('raw_score', 'score', 'pass_rate', 'pass_rate_lower', 'pass_rate_upper'):
        assert scored['task_correctness_' + field] is None
    assert scored['task_correctness_verdict'] == 'inconclusive'
    assert scored['quality_score'] == scored['aesthetics_score'] == 100


def test_additional_failure_has_the_same_weight_as_original_point_failure():
    result, points = counted_case(4, 1)
    original = score_d(result, instruction=INSTRUCTION, test_points=points, core_requirements=CORE)
    result['task_correctness']['point_results'].pop()
    result['task_correctness']['additional_requirements'] = judgment(extra='fail')[
        'task_correctness']['additional_requirements']
    moved = score_d(result, instruction=INSTRUCTION, test_points=points[:-1], core_requirements=CORE)
    assert moved == original
    assert moved['task_correctness_raw_score'] == 1
    assert moved['task_correctness_pass_rate'] == 0.8


def test_core_failure_cannot_be_averaged_away_by_nine_passes():
    result, points = counted_case(9, 1)
    ordinary_failure = score_d(result, instruction=INSTRUCTION, test_points=points,
                               core_requirements=CORE)
    core_failure = score_d(result, instruction=INSTRUCTION, test_points=points,
                           core_requirements={'point_indices': [10], 'additional_requirements': []})
    assert ordinary_failure['task_correctness_raw_score'] == 1
    assert core_failure['task_correctness_raw_score'] == 0
    assert core_failure['task_correctness_core_counts']['fail'] == 1
    assert ordinary_failure['task_correctness_pass_rate'] == core_failure['task_correctness_pass_rate'] == 0.9


def test_core_additional_failure_has_the_same_veto_as_core_point_failure():
    result, points = counted_case(9, 1)
    core = {'point_indices': [10], 'additional_requirements': []}
    before = score_d(result, instruction=INSTRUCTION, test_points=points, core_requirements=core)
    result['task_correctness']['point_results'].pop()
    extra = judgment(extra='fail')['task_correctness']['additional_requirements'][0]
    result['task_correctness']['additional_requirements'] = [extra]
    frozen = {key: extra[key] for key in ('instruction_quote', 'requirement')}
    core = {'point_indices': [], 'additional_requirements': [frozen]}
    after = score_d(result, instruction=INSTRUCTION, test_points=points[:-1], core_requirements=core)
    assert before == after
    assert after['task_correctness_raw_score'] == 0
    # 判官不能通过省略或改写核心补充项绕过门槛。
    for changed_extras in ([], [{**extra, 'requirement': '改写后的普通要求'}]):
        changed = deepcopy(result)
        changed['task_correctness']['additional_requirements'] = changed_extras
        with pytest.raises(ValueError, match='frozen core additional'):
            score_d(changed, instruction=INSTRUCTION, test_points=points[:-1], core_requirements=core)


@pytest.mark.parametrize('passed,failed,unknown,core_index,raw', [
    (6, 2, 1, 1, 1),          # 已通过核心，未知范围仍完全落在 1 档。
    (6, 2, 1, 9, None),       # 同一未知项若为核心，不能推定核心完成。
    (6, 2, 1, 7, 0),          # 核心明确失败，其他未知不能抵消。
    (0, 4, 1, 5, 0),          # 即使未知核心通过，总体最多 1/5，仍为 0。
])
def test_unknown_core_gate_precedes_rate_band(passed, failed, unknown, core_index, raw):
    result, points = counted_case(passed, failed, unknown)
    scored = score_d(result, instruction=INSTRUCTION, test_points=points,
                     core_requirements={'point_indices': [core_index], 'additional_requirements': []})
    assert scored['task_correctness_raw_score'] == raw
    assert scored['task_correctness_pass_rate'] is None


@pytest.mark.parametrize('issue', ['invalid_criterion', 'instruction_conflict'])
def test_invalid_task_still_overrides_known_core_failure(issue):
    result, points = counted_case(1, 2)
    if issue == 'invalid_criterion':
        result['task_correctness']['point_results'][0]['verdict'] = issue
    else:
        result['task_correctness']['instruction_conflict'] = True
    scored = score_d(result, instruction=INSTRUCTION, test_points=points,
                     core_requirements={'point_indices': [2], 'additional_requirements': []})
    assert scored['task_correctness_raw_score'] is None
    assert scored['task_correctness_core_counts']['fail'] == 1


@pytest.mark.parametrize('core', [
    None,
    {'point_indices': [], 'additional_requirements': []},
    {'point_indices': [True], 'additional_requirements': []},
    {'point_indices': [2], 'additional_requirements': []},
    {'point_indices': [1, 1], 'additional_requirements': []},
    {'point_indices': [], 'additional_requirements': [
        {'instruction_quote': '白色背景', 'requirement': '必须有白色背景'}]},
])
def test_core_manifest_is_required_and_cannot_reference_invented_requirements(core):
    with pytest.raises(ValueError):
        score_d(judgment(), instruction=INSTRUCTION, test_points=POINTS, core_requirements=core)


@pytest.mark.parametrize('field,value', [('score', -1), ('score', 1), ('score', 9),
                                         ('verdict', 'partial'), ('verdict', 'pass')])
def test_model_cannot_supply_derived_task_score_or_overall_verdict(field, value):
    result = judgment()
    result['task_correctness'][field] = value
    with pytest.raises(ValueError):
        convert(result)


@pytest.mark.parametrize('mutation', ['count', 'index', 'invented_quote', 'repeated', 'quote_order', 'blank'])
def test_original_points_and_explicit_instruction_binding(mutation):
    result = judgment(extra='pass')
    task = result['task_correctness']
    if mutation == 'count':
        task['point_results'].append({**task['point_results'][0], 'index': 2})
    elif mutation == 'index':
        task['point_results'][0]['index'] = 2
    elif mutation == 'invented_quote':
        task['additional_requirements'][0]['instruction_quote'] = '白色背景'
    elif mutation == 'repeated':
        task['additional_requirements'].append({**task['additional_requirements'][0], 'index': 2})
    elif mutation == 'quote_order':
        task['additional_requirements'].append({**task['additional_requirements'][0], 'index': 2,
                                               'instruction_quote': '两把', 'requirement': '核对两把的数量'})
    else:
        task['point_results'][0]['evidence'] = '  '
    with pytest.raises(ValueError):
        convert(result)


def test_quality_aesthetics_keep_a_items_and_exclude_only_conditional_na():
    a = load_prompt_pack(PROMPTS / 'tasks.yaml').prompt_definitions['judge'].response_schema
    result = judgment()
    for dimension in ('quality', 'aesthetics'):
        assert set(result[dimension]) == set(a['properties']['result']['properties'][dimension]['required'])
    result['quality'].update(physical_logic='N/A', material_texture='N/A', anatomical_fidelity='N/A',
                             detail_richness=2, artifacts=0, resolution=1, edge_clarity=1, naturalness=1)
    result['aesthetics'].update(lighting_atmosphere='N/A', emotional_expression='N/A',
                                composition=2, color_harmony=1)
    scored = convert(result)
    assert scored['quality_score'] == 56  # (100+0+60+60+60)/5
    assert scored['aesthetics_score'] == 80
    assert scored['valid_items'] == {'quality': 5, 'aesthetics': 2}
    for bad in (True, 1.0, '1', 'N/A'):
        changed = deepcopy(result)
        changed['quality']['resolution'] = bad
        with pytest.raises(ValueError):
            convert(changed)
