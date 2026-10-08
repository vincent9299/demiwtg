"""D 候选协议的计分；不调用模型，也不改写 A/B 结果。

D7按固定Review清单和逐项数字算分；D6按历史返回的结论、核心标记算分。
D4/D5保留历史协议的校验与换算。
"""
from functools import lru_cache
from math import fsum
from pathlib import Path

from demiflow.operator_llm.parser import load_prompt_pack
from demiflow.schema import validate_instance


@lru_cache(maxsize=3)
def _schema(protocol='legacy'):
    filename = {'d7': 'prompts/d7.yaml', 'd6': 'archive/d6/prompts/d.yaml',
                'legacy': 'archive/d5/prompts/d.yaml'}[protocol]
    pack = load_prompt_pack(Path(__file__).parents[1] / filename)
    return pack.prompt_definitions['judge_d'].response_schema


def _nonblank(value):
    if isinstance(value, str) and not value.strip():
        raise ValueError('D explanations and evidence must be nonblank')
    if isinstance(value, dict):
        for item in value.values():
            _nonblank(item)
    elif isinstance(value, list):
        for item in value:
            _nonblank(item)


def validate_d_core_requirements(*, instruction, test_points, core_requirements):
    """仅供D4/D5历史协议：校验原准备记录。"""
    if not isinstance(instruction, str) or not instruction.strip():
        raise ValueError('D requires the complete original instruction')
    if not isinstance(test_points, list) or not test_points:
        raise ValueError('D requires the original nonempty test_points')
    if not isinstance(core_requirements, dict) or set(core_requirements) != {
            'point_indices', 'additional_requirements'}:
        raise ValueError('D requires a frozen core_requirements manifest')
    indices = core_requirements['point_indices']
    extras = core_requirements['additional_requirements']
    if (not isinstance(indices, list)
            or any(type(index) is not int or not 1 <= index <= len(test_points) for index in indices)
            or indices != sorted(set(indices))):
        raise ValueError('D core point_indices must be unique original indices in order')
    if not isinstance(extras, list) or not (indices or extras):
        raise ValueError('D requires at least one frozen core requirement')
    identities = set()
    for item in extras:
        if not isinstance(item, dict) or set(item) != {'instruction_quote', 'requirement'}:
            raise ValueError('D core additional requirements need their frozen quote and requirement')
        if not all(isinstance(value, str) and value.strip() for value in item.values()):
            raise ValueError('D core requirement text must be nonblank')
        quote, requirement = item['instruction_quote'], item['requirement']
        identity = (quote, requirement)
        if quote not in instruction or identity in identities:
            raise ValueError('D core additional requirements must be unique and instruction-bound')
        identities.add(identity)
    return set(indices), identities


def score_d(result, *, instruction, test_points, core_requirements=None):
    """按响应的实际协议计算；D7五组，D6四维，旧协议保持三维。

    无法判定的任务分为 None。D6只校验响应格式并按返回值计算；旧协议
    保留其历史绑定校验。解析失败不能转成作答失败或零分。仅处理单行，
    时间和附加内存随单行检查项数增长；请求字节预算由标准节点约束。
    """
    if 'requirement_scores' in result or (isinstance(core_requirements, dict) and core_requirements.get('protocol') == 'd7'):
        from .d_rubric import score_d7
        return score_d7(result, core_requirements, schema=_schema('d7'), visual_scores=visual_scores)
    if core_requirements is None or (isinstance(core_requirements, dict) and core_requirements.get('protocol') == 'd6'):
        return score_d6(result)
    core_indices, core_extras = validate_d_core_requirements(
        instruction=instruction, test_points=test_points, core_requirements=core_requirements)
    validate_instance({'result': result}, _schema(), label='D response')
    _nonblank(result)
    task = result['task_correctness']
    points, extras = task['point_results'], task['additional_requirements']
    if len(points) != len(test_points):
        raise ValueError('D point_results must match the original test_points count')
    for name, entries in (('point_results', points), ('additional_requirements', extras)):
        if [item['index'] for item in entries] != list(range(1, len(entries) + 1)):
            raise ValueError(f'D {name} indices must preserve the original order')
    positions, seen = [], set()
    for item in extras:
        quote = item['instruction_quote']
        if quote not in instruction:
            raise ValueError('D instruction_quote must occur verbatim in the instruction')
        positions.append(instruction.index(quote))
        identity = (quote, item['requirement'])
        if identity in seen:
            raise ValueError('D additional_requirements must not repeat the same requirement')
        seen.add(identity)
    if positions != sorted(positions):
        raise ValueError('D additional_requirements must follow first occurrence in the instruction')
    if not core_extras <= seen:
        raise ValueError('D response must preserve every frozen core additional requirement verbatim')

    states = [item['verdict'] for item in points + extras]
    core_states = [item['verdict'] for item in points if item['index'] in core_indices]
    core_states += [item['verdict'] for item in extras
                    if (item['instruction_quote'], item['requirement']) in core_extras]
    item_counts = {state: states.count(state)
                   for state in ('pass', 'fail', 'inconclusive', 'invalid_criterion')}
    core_counts = {state: core_states.count(state) for state in item_counts}
    core_counts['total'] = len(core_states)
    item_counts['total'] = total = len(states)
    passed, unknown = item_counts['pass'], item_counts['inconclusive']

    def band(count):
        # 精确使用 2/3；不写成 60%、67%，也不先四舍五入通过率。
        return 2 if count == total else 1 if 3 * count >= 2 * total else 0

    raw = lower = upper = rate = None
    if not task['instruction_conflict'] and not item_counts['invalid_criterion']:
        lower, upper = passed / total, (passed + unknown) / total
        lower_band, upper_band = band(passed), band(passed + unknown)
        if core_counts['fail']:
            raw = 0
        elif core_counts['inconclusive']:
            # 未知核心项可能失败；只有其全部通过也无法到达门槛时，才可确定 0。
            if upper_band == 0:
                raw = 0
        elif lower_band == upper_band:
            raw = lower_band
        if not unknown:
            rate = lower
    verdict = {0: 'fail', 1: 'partial', 2: 'pass', None: 'inconclusive'}[raw]
    scores = {'task_correctness_verdict': verdict,
              'task_correctness_raw_score': raw,
              'task_correctness_score': {0: 0, 1: 60, 2: 100}[raw] if raw is not None else None}
    scores.update(task_correctness_counts=item_counts, task_correctness_core_counts=core_counts,
                  task_correctness_pass_rate=rate,
                  task_correctness_pass_rate_lower=lower, task_correctness_pass_rate_upper=upper)
    return {**scores, **visual_scores(result)}


def visual_scores(result, *, dimensions=('quality', 'aesthetics'), validate_applicability=True):
    """A 原档位的质量、美感换算，历史与 D6 共用，不随正确性边界改变。"""
    counts, scores = {}, {}
    always_applicable = {
        'quality': {'detail_richness', 'artifacts', 'resolution', 'edge_clarity', 'naturalness'},
        'aesthetics': {'composition', 'color_harmony'},
    }
    for dimension in dimensions:
        values = result[dimension]
        if not all((type(value) is int and value in (0, 1, 2)) or value == 'N/A'
                   for value in values.values()):
            raise ValueError('D quality/aesthetics accept only integer 0/1/2 or N/A')
        if validate_applicability and any(values[key] == 'N/A' for key in always_applicable[dimension]):
            raise ValueError(f'D always-applicable {dimension} items cannot be N/A')
        valid = [{0: 0, 1: 60, 2: 100}[value] for value in values.values() if value != 'N/A']
        counts[dimension] = len(valid)
        scores[dimension + '_score'] = sum(valid) / len(valid) if valid else None
    return {**scores, 'valid_items': counts}


DIMENSION_WEIGHTS = {'task_correctness': 0.7, 'quality': 0.1,
                     'aesthetics': 0.1, 'other_instruction_following': 0.1}


def d6_requirements(result):
    """仅为查看保留同次响应中的要求；不参与计分校验。"""
    entries = []
    for dimension in ('task_correctness', 'other_instruction_following'):
        for item in (result[dimension] or {}).get('checks', []):
            entries.append({**{key: item[key] for key in (
                'source_point_indices', 'instruction_quote', 'requirement', 'applicability', 'reason')},
                'index': len(entries) + 1, 'dimension': dimension, 'is_core': item.get('is_core', False)})
    return {'protocol': 'd6', 'requirements': entries}


def score_d6(result):
    """只按返回值计算四维分数，格式由prompt schema约束，业务判断由判官负责。"""
    validate_instance({'result': result}, _schema('d6'), label='D6 response')
    scores = {'protocol': 'd6', 'scoring_revision': 'd6-calculation-only-v1',
              'weights': dict(DIMENSION_WEIGHTS)}
    for dim in ('task_correctness', 'other_instruction_following'):
        results = (result[dim] or {}).get('checks', [])
        states = [r['verdict'] for r in results]
        counts = {state: states.count(state) for state in
                  ('pass', 'fail', 'inconclusive', 'invalid_criterion', 'not_applicable')}
        counts['total'] = n = len(states) - counts['not_applicable']
        counts['planned'] = len(states)
        core_states = [r['verdict'] for r in results if r.get('is_core', False) and r['verdict'] != 'not_applicable']
        invalid = bool((result[dim] or {}).get('issues')) or counts['invalid_criterion']
        p, u = counts['pass'], counts['inconclusive']
        low = p / n if n and not invalid else None
        high = (p + u) / n if n and not invalid else None
        score = raw = None
        status = 'inconclusive'
        if dim == 'task_correctness':
            if n and core_states and not invalid:
                band = lambda count: 2 if count == n else 1 if 3 * count >= 2 * n else 0
                if 'fail' in core_states:
                    raw = 0
                elif 'inconclusive' in core_states:
                    raw = 0 if band(p + u) == 0 else None
                elif band(p) == band(p + u):
                    raw = band(p)
            status = {0: 'fail', 1: 'partial', 2: 'pass', None: 'inconclusive'}[raw]
            score = {0: 0, 1: 60, 2: 100, None: None}[raw]
            scores['task_correctness_raw_score'] = raw
            scores['task_correctness_verdict'] = status
            scores['task_correctness_core_counts'] = {
                **{s: core_states.count(s) for s in ('pass', 'fail', 'inconclusive', 'invalid_criterion')},
                'total': len(core_states)}
        elif not invalid:
            if not n:
                status = 'not_applicable'
            elif not u:
                status, score = 'reviewed', 100 * p / n
        scores.update({dim + '_score': score, dim + '_status': status,
            dim + '_counts': counts, dim + '_pass_rate': low if not u else None,
            dim + '_pass_rate_lower': low, dim + '_pass_rate_upper': high})
    scores.update(visual_scores(result, validate_applicability=False))
    active = dict(DIMENSION_WEIGHTS)
    if scores['other_instruction_following_status'] == 'not_applicable':
        active.pop('other_instruction_following')
    denominator = fsum(active.values())
    scores['effective_weights'] = {key: value / denominator for key, value in active.items()}
    scores['overall_score'] = (fsum(scores[key + '_score'] * weight for key, weight in active.items()) / denominator
        if all(scores[key + '_score'] is not None for key in active) else None)
    return scores
