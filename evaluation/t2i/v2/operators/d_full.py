"""全量 D7 的逐行来源核验和已评结果复用；模型调用留在正式入口。"""
import json
from .answers import answer_identity
from .codex_judging import ensure_same_answer


def tagged_answer(row, *, source, generation_config=None):
    return {**row, '_source_answer_ref': source,
            '_generation_config': json.dumps(generation_config) if generation_config else None}


def audit_answer(row):
    """按当前题面和参考图重算原生成请求身份；不生成或修补历史答案。"""
    answer = row.get('prior_answer')
    if answer and answer.get('_generation_config'):
        expected = answer_identity(json.loads(answer['_generation_config']), row)
        if answer.get('request_id') != expected:
            raise ValueError('Frozen generation request identity mismatch: ' + row['task_id'])
    return row


def reuse_score(row, *, judge):
    previous = row.get('prior_score')
    if not previous:
        return row
    if row['d_status'] != 'ready' or previous.get('d_status') != 'reviewed':
        raise ValueError('Only valid matching D7 scores may be reused')
    if previous.get('judge_model') != judge['model']:
        receipt = json.loads(previous.get('d_call_json') or '{}')
        legacy_codex = (previous.get('judge_model') == 'codex-subagent/gpt-6-astra'
            and judge['model'] == 'codex/gpt-6-astra'
            and receipt.get('transport') == 'codex_subagent'
            and receipt.get('model') == 'gpt-6-astra'
            and receipt.get('reasoning_effort') == 'xhigh' and receipt.get('fork_turns') == 'none')
        if not legacy_codex:
            raise ValueError('Reused judge differs from frozen judge')
    ensure_same_answer(row, previous)
    if previous.get('answer_request_id') != row.get('answer_request_id'):
        raise ValueError('Reused answer request identity differs')
    # 保留原始请求、响应和全部分数；writer不写入临时标志。
    return {**row, **previous, '_reused_score': True}


def one_score(previous, row):
    if previous is not None and previous != row:
        raise ValueError('Conflicting reusable scores for the same frozen task/condition')
    return row


def reuse_initial_score(row, *, judge):
    """RAG can keep its initial image. Identical D7 input reuses the original blind judgment."""
    previous = row.get('initial_score')
    if not previous or row.get('_reused_score') or row['d_status'] != 'ready':
        return row
    if json.loads(row['image_json']) != json.loads(previous['image_json']):
        return row
    if (row['answer_mode'] != 'imagerag' or previous['answer_mode'] != 'text_only'
            or row['answer_model'] != previous['answer_model'] + '+ImageRAG'):
        raise ValueError('Initial-score reuse requires the same generation model')
    # Validate the shared question/rubric/image and judge with the existing guard.
    check = {**row, 'answer_mode': previous['answer_mode'],
             'answer_model': previous['answer_model'],
             'answer_request_id': previous.get('answer_request_id'), 'prior_score': previous}
    reuse_score(check, judge=judge)
    result = {**row, **{k: v for k, v in previous.items() if k.startswith('d_')},
              'judge_model': previous['judge_model'], '_reused_score': True}
    call = json.loads(previous.get('d_call_json') or '{}')
    call['score_reuse'] = {'reason': 'identical_frozen_d7_input', 'answer_mode': 'text_only',
                           'answer_request_id': previous.get('answer_request_id')}
    result['d_call_json'] = json.dumps(call, ensure_ascii=False)
    return result


def case_name(row):
    return row['answer_mode'] + '_' + row['task_id']


def export_codex(row, *, root, directory):
    if row.get('_reused_score') or row['d_status'] != 'ready':
        return row
    from .codex_judging import export_input
    return export_input({**row, '_case': case_name(row), '_original_call': None},
                        root=root, directory=directory)
