"""D 试评的逐行准备、结果校验与汇总；模型节点仅由正式入口编排。"""
import json
from collections import Counter
import pyarrow as pa
from .d_scores import score_d, d6_requirements, validate_d_core_requirements, _nonblank
from .images import image_data_url
from .d_rubric import frozen_requirements


QUESTION_SCHEMA = pa.schema([
    ('task_id', pa.string()), ('concept', pa.string()), ('instruction', pa.large_string()),
    ('taxonomy', pa.list_(pa.string())),
    ('test_points', pa.list_(pa.struct([(k, pa.string()) for k in ('point', 'basis', 'criterion')]))),
    ('authoring_variant', pa.string()), ('authoring_images_json', pa.large_string()),
])
D7_QUESTION_SCHEMA = pa.schema([*QUESTION_SCHEMA, *[(key, pa.large_string()) for key in
    ('requirements_json', 'question_revision', 'review_status')]])
CORE_SCHEMA = pa.schema([*QUESTION_SCHEMA, *[(key, pa.large_string()) for key in (
    'core_status', 'core_reason', 'core_requirements_json', 'core_plan_json', 'core_call_json')]])
D_FIELDS = [*[(key, pa.large_string()) for key in (
    'answer_request_id', 'source_answer_ref_json', 'core_requirements_json', 'core_plan_json',
    'core_call_json', 'd_status', 'd_reason', 'd_json', 'd_call_json', 'd_metrics_json',
    'd_verdict', 'judge_model', 'd_protocol', 'd_other_status', 'question_revision',
    'd_core_status', 'd_noncore_status', 'd_general_status')], ('d_raw_task_score', pa.int64()),
    *[(key, pa.float64()) for key in ('d_task_score', 'd_quality_score', 'd_aesthetics_score',
                                     'd_other_score', 'd_overall_score', 'd_core_score',
                                     'd_noncore_score', 'd_general_score')]]
DIMENSIONS = {'task_correctness': 'd_task_score', 'quality': 'd_quality_score',
              'aesthetics': 'd_aesthetics_score', 'other_instruction_following': 'd_other_score',
              'task_correctness_core': 'd_core_score', 'task_correctness_noncore': 'd_noncore_score',
              'general_instruction_following': 'd_general_score',
              'overall': 'd_overall_score'}


def payload(row, *, legacy=False):
    fields = ('concept', 'taxonomy', 'instruction', 'test_points') if legacy else ('instruction', 'test_points')
    return {key: row.get(key) for key in fields}


def validate_review_question(row):
    frozen_requirements(row)
    return row


def prepare_core(row, *, available_chars, legacy=False):
    out = {**row, 'core_payload': payload(row, legacy=legacy), 'core_status': 'ready', 'core_reason': '',
           'core_requirements_json': None, 'core_plan_json': None, 'core_call_json': None}
    if len(json.dumps(out['core_payload'], ensure_ascii=False)) > available_chars:
        out.update(core_status='needs_context_budget', core_reason='完整核心判定输入超预算，未截断')
    return out


def finish_core(row):
    error = row.get('core_error') or {}
    out = {**row, 'core_call_json': json.dumps(row.get('core_call') or error.get('call') or {}, ensure_ascii=False),
           'core_plan_json': json.dumps(row.get('core_result'), ensure_ascii=False)}
    if row['core_status'] != 'ready':
        return out
    if error:
        return {**out, 'core_status': 'pending' if error.get('type') == 'PromptResponsePending' else 'failed',
                'core_reason': error.get('detail', '')}
    result = row['core_result']
    try:
        _nonblank(result)
        if result['status'] == 'inconclusive':
            return {**out, 'core_status': 'inconclusive', 'core_reason': result['reason']}
        roles = result['point_roles']
        if [item['index'] for item in roles] != list(range(1, len(row['test_points']) + 1)):
            raise ValueError('核心准备须逐项覆盖原有考点且保留顺序')
        if any(type(item['is_core']) is not bool or item['instruction_quote'] not in row['instruction']
               for item in roles):
            raise ValueError('核心归属及其题面引用无效')
        extras = result['core_additional_requirements']
        positions = [row['instruction'].index(item['instruction_quote']) for item in extras]
        if positions != sorted(positions):
            raise ValueError('核心补充要求未按题面顺序排列')
        core = {'point_indices': [item['index'] for item in roles if item['is_core']],
                'additional_requirements': [{key: item[key] for key in ('instruction_quote', 'requirement')}
                                            for item in extras]}
        validate_d_core_requirements(instruction=row['instruction'], test_points=row['test_points'], core_requirements=core)
        out.update(core_status='prepared', core_reason=result['reason'],
                   core_requirements_json=json.dumps(core, ensure_ascii=False))
    except (KeyError, TypeError, ValueError, AttributeError) as error:
        out.update(core_status='invalid_response', core_reason=str(error))
    return out


def prepare_score(row, *, root, model, judge, available_chars, single_pass=False, protocol=None):
    answer = row.get('prior_answer') or {}
    out = {**row, **answer, 'answer_request_id': answer.get('request_id'),
           'source_answer_ref_json': json.dumps(answer.get('_source_answer_ref') or model['source'], ensure_ascii=False),
           'judge_model': judge['model'], 'd_status': 'ready', 'd_reason': '', 'd_images': [],
           'status': answer.get('status', 'answer_missing'), 'answer_mode': model['answer_mode'],
           'answer_model': model['model']}
    for field, _ in D_FIELDS:
        out.setdefault(field, None)
    out['d_protocol'] = protocol or ('d6' if single_pass else 'legacy')
    if protocol == 'd7':
        frozen = frozen_requirements(row)
        out['question_revision'] = row['question_revision']
        out['core_requirements_json'] = json.dumps(frozen, ensure_ascii=False)
    if not single_pass and row['core_status'] != 'prepared':
        return {**out, 'd_status': 'core_' + row['core_status'], 'd_reason': row['core_reason']}
    if not answer or answer.get('status') != 'generated':
        return {**out, 'd_status': 'answer_missing', 'd_reason': answer.get('reason') or '固定答案表没有成功作答'}
    if (answer['instruction'] != row['instruction'] or answer['answer_model'] != model['model']
            or answer['answer_mode'] != model['answer_mode']):
        return {**out, 'd_status': 'answer_mismatch', 'd_reason': '已有答案的题面、模型或作答条件与冻结配置不符'}
    count = answer.get('reference_image_count')
    if ((model['answer_mode'] == 'text_only' and count != 0)
            or (model['answer_mode'] == 'positive_images' and (count is None or count < 1))):
        return {**out, 'd_status': 'answer_mismatch', 'd_reason': '已有答案实际参考图数量与作答条件不符'}
    if single_pass:
        out['d_payload'] = ({'instruction': row['instruction'], 'requirements': frozen['requirements']}
                            if protocol == 'd7' else payload(row))
    else:
        core = json.loads(row['core_requirements_json'])
        out['d_protocol'] = 'legacy'
        validate_d_core_requirements(instruction=row['instruction'], test_points=row['test_points'], core_requirements=core)
        out['d_payload'] = {**payload(row, legacy=True), 'core_requirements': core}
    if len(json.dumps(out['d_payload'], ensure_ascii=False)) > available_chars:
        return {**out, 'd_status': 'needs_context_budget', 'd_reason': '完整 D 评审输入超预算，未截断'}
    try:
        out['d_images'] = [image_data_url(json.loads(answer['image_json']), root)]
    except (OSError, KeyError, ValueError, TypeError) as error:
        out.update(d_status='invalid_image', d_reason=str(error))
    return out


def finish_score(row):
    if row.get('_reused_score'):
        return row
    error = row.get('d_error') or {}
    out = {**row, 'd_call_json': json.dumps(row.get('d_call') or error.get('call') or {}, ensure_ascii=False),
           'd_json': json.dumps(row.get('d_result'), ensure_ascii=False)}
    if row['d_status'] != 'ready':
        return out
    if error:
        return {**out, 'd_status': 'pending' if error.get('type') == 'PromptResponsePending' else 'failed',
                'd_reason': error.get('detail', '')}
    try:
        core = (d6_requirements(row['d_result']) if row.get('d_protocol') == 'd6'
                else json.loads(row['core_requirements_json']))
        metrics = score_d(row['d_result'], instruction=row['instruction'], test_points=row['test_points'],
                          core_requirements=core)
        out['core_requirements_json'] = json.dumps(core, ensure_ascii=False)
        out.update(d_status='reviewed', d_reason='', d_metrics_json=json.dumps(metrics, ensure_ascii=False),
                   d_verdict=metrics['task_correctness_verdict'], d_raw_task_score=metrics['task_correctness_raw_score'],
                   d_task_score=metrics['task_correctness_score'], d_quality_score=metrics['quality_score'],
                   d_aesthetics_score=metrics['aesthetics_score'],
                   d_other_score=metrics.get('other_instruction_following_score'),
                   d_other_status=metrics.get('other_instruction_following_status'),
                   d_overall_score=metrics.get('overall_score'),
                   d_core_score=metrics.get('task_correctness_core_score'),
                   d_noncore_score=metrics.get('task_correctness_noncore_score'),
                   d_general_score=metrics.get('general_instruction_following_score'),
                   d_core_status=metrics.get('task_correctness_core_status'),
                   d_noncore_status=metrics.get('task_correctness_noncore_status'),
                   d_general_status=metrics.get('general_instruction_following_status'))
    except (KeyError, ValueError, TypeError, AttributeError) as error:
        out.update(d_status='invalid_response', d_reason=str(error))
    return out


def summarize(rows, expected):
    values = {dim: [r[field] for r in rows if r.get(field) is not None] for dim, field in DIMENSIONS.items()}
    return {'expected': expected, 'recorded': len(rows), 'states': dict(Counter(r['d_status'] for r in rows)),
            'verdicts': dict(Counter(r['d_verdict'] for r in rows if r.get('d_verdict'))),
            'other_statuses': dict(Counter(r['d_other_status'] for r in rows if r.get('d_other_status'))),
            'dimensions': {dim: {'mean': sum(items) / len(items) if items else None, 'valid': len(items)}
                           for dim, items in values.items()}}


def paired_summary(arms):
    """每维度只比较两路均有有效分的同题；同时保留各路全样本汇总。"""
    left, right = [{r['task_id']: r for r in rows} for rows in arms]
    summary = {}
    for dim, field in DIMENSIONS.items():
        ids = sorted(key for key in left.keys() & right.keys()
                     if left[key].get(field) is not None and right[key].get(field) is not None)
        means = [sum(rows[key][field] for key in ids) / len(ids) if ids else None for rows in (left, right)]
        summary[dim] = {'paired_count': len(ids), 'task_ids': ids, 'arm0_mean': means[0], 'arm1_mean': means[1],
                        'arm1_minus_arm0': means[1] - means[0] if ids else None}
    return summary
