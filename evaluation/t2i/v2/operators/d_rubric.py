"""D7 冻结检查方向的确定性分组与计算；不复核模型的事实判断。"""
import json
from math import fsum

from demiflow.schema import validate_instance

WEIGHTS = {'task_correctness_core': .6, 'task_correctness_noncore': .1,
           'general_instruction_following': .1, 'quality': .1, 'aesthetics': .1}
POINTS = {0: 0, 1: 60, 2: 100}


def frozen_requirements(row):
    """只读 Review 的 ready 交付；规范化旧字段名，不新增或改判要求。"""
    if row.get('review_status') != 'ready' or not row.get('question_revision'):
        raise ValueError('D7 requires ready Review questions with question_revision')
    requirements = json.loads(row['requirements_json'])
    if not isinstance(requirements, list) or not 1 <= len(requirements) <= 100:
        raise ValueError('D7 requires 1..100 frozen Review directions')
    normalized, seen = [], set()
    for item in requirements:
        for key in ('id', 'requirement', 'basis', 'criterion'):
            if not isinstance(item.get(key), str) or not item[key].strip():
                raise ValueError('D7 frozen directions require nonempty ' + key)
        if item['id'] in seen:
            raise ValueError('D7 frozen direction IDs must be unique')
        seen.add(item['id'])
        dim = item['dimension']
        if dim == 'other_instruction_following':
            dim = 'general_instruction_following'
        if dim not in ('task_correctness', 'general_instruction_following'):
            raise ValueError('D7 unknown frozen direction dimension')
        if dim == 'task_correctness' and type(item['is_core']) is not bool:
            raise ValueError('D7 correctness directions require a frozen boolean core flag')
        if dim == 'general_instruction_following' and item['is_core'] is not None:
            raise ValueError('D7 general following does not have a core flag')
        condition = item['applicability']
        if condition is not None and (not isinstance(condition, str) or not condition.strip()):
            raise ValueError('D7 applicability must be null or a nonempty condition')
        normalized.append({**item, 'dimension': dim})
    return {'protocol': 'd7', 'question_revision': row['question_revision'], 'requirements': normalized}


def group_key(item):
    return ('task_correctness_core' if item['is_core'] else 'task_correctness_noncore') \
        if item['dimension'] == 'task_correctness' else 'general_instruction_following'


def score_d7(result, manifest, *, schema, visual_scores):
    """ID 对应、数值合法性与算术；不检查引用、排序、证据或清单语义。"""
    validate_instance({'result': result}, schema, label='D7 response')
    if not isinstance(manifest, dict) or manifest.get('protocol') != 'd7':
        raise ValueError('D7 scoring requires its frozen Review manifest')
    requirements = manifest['requirements']
    expected = [r['id'] for r in requirements]
    checks = result['requirement_scores']
    ids = [r['id'] for r in checks]
    if len(set(expected)) != len(expected) or len(set(ids)) != len(ids) or set(ids) != set(expected):
        raise ValueError('D7 response IDs must match the frozen directions exactly once')
    conditions = {r['id']: r['applicability'] for r in requirements}
    for item in checks:
        value = item['score']
        if not ((type(value) is int and value in POINTS) or value == 'N/A' or value is None):
            raise ValueError('D7 direction score must be integer 0/1/2, N/A or null')
        if (value is None) != (item['issue'] is not None):
            raise ValueError('D7 null score requires an issue; normal scores require issue=null')
        if value == 'N/A' and conditions[item['id']] is None:
            raise ValueError('D7 N/A requires a condition in the frozen direction')
    by_id = {item['id']: item for item in checks}
    groups = {key: [] for key in list(WEIGHTS)[:3]}
    for item in requirements:
        groups[group_key(item)].append(by_id[item['id']])
    scores = {'protocol': 'd7', 'scoring_revision': 'd7-fixed-directions-v1',
              'weights': dict(WEIGHTS), 'question_revision': manifest['question_revision'],
              'task_correctness_raw_score': None}
    for key, items in groups.items():
        active = [r for r in items if r['score'] != 'N/A']
        issues = [r['issue'] for r in active if r['issue']]
        counts = {str(v): sum(r['score'] == v for r in items) for v in (*POINTS, 'N/A')}
        counts.update(planned=len(items), applicable=len(active), anomalies=len(issues))
        value = None
        if issues:
            status = 'invalid_criterion' if 'invalid_criterion' in issues else 'inconclusive'
        elif not active:
            status = 'not_applicable'
        else:
            status = 'reviewed'
            value = fsum(POINTS[r['score']] for r in active) / len(active)
            if key == 'task_correctness_core' and any(r['score'] == 0 for r in active):
                value = 0
        scores.update({key + '_score': value, key + '_status': status, key + '_counts': counts})
    scores.update(visual_scores(result, validate_applicability=False))
    for key in ('quality', 'aesthetics'):
        scores[key + '_status'] = 'reviewed' if scores['valid_items'][key] else 'not_applicable'
    active = {k: w for k, w in WEIGHTS.items() if scores[k + '_status'] != 'not_applicable'}
    denominator = fsum(active.values())
    scores['effective_weights'] = {k: w / denominator for k, w in active.items()}
    scores['overall_score'] = (fsum(scores[k + '_score'] * w for k, w in active.items()) / denominator
        if active and all(scores[k + '_score'] is not None for k in active) else None)
    scores['overall_status'] = ('not_applicable' if not active else
                               'reviewed' if scores['overall_score'] is not None else 'inconclusive')
    task = {k: w for k, w in active.items() if k.startswith('task_correctness_')}
    scores['task_correctness_score'] = (fsum(scores[k + '_score'] * w for k, w in task.items()) / fsum(task.values())
        if task and all(scores[k + '_score'] is not None for k in task) else None)
    scores['task_correctness_status'] = ('not_applicable' if not task else
        'invalid_criterion' if any(scores[k + '_status'] == 'invalid_criterion' for k in task) else
        'reviewed' if scores['task_correctness_score'] is not None else 'inconclusive')
    # D7 aggregates continuous group scores; do not invent an old 0/1/2 task verdict.
    scores['task_correctness_verdict'] = scores['task_correctness_status']
    return scores
