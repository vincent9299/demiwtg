"""出题数据流的行函数：构造单概念请求、检查单概念响应；不调度 Dataset 或调用模型。"""
import json
import hashlib
from demiflow.schema import SchemaValidationError, validate_instance

from benchmark.t2i.v2.operators.images import image_data_url
from benchmark.t2i.v2.operators.concept_context import (
    ConceptEvidenceError, project_context, render_concept_context, document_resources,
)


def select_screened_concepts(acc, row, *, sample_size, sample_seed):
    """按 seed/name 哈希选 N 个 keep 概念，随选中行保留完整 taxonomy。"""
    concept = row['concept']
    if not isinstance(concept, str) or not concept.strip():
        raise ValueError('Screening result contains an empty concept')
    rank = hashlib.sha256(json.dumps([sample_seed, concept], ensure_ascii=False).encode()).hexdigest()
    item = {'concept': concept, 'taxonomy': row['taxonomy'] or [], 'rank': rank}
    if row.get('concept_record') is not None:
        item['concept_record'] = row['concept_record']
    selected = (acc['selected'] if acc else []) + [item]
    return {'group': 'keep', 'eligible_count': (acc['eligible_count'] if acc else 0) + 1,
            'selected': sorted(selected, key=lambda item: (item['rank'], item['concept']))[:sample_size]}


def adopted_concept(row, *, source):
    """读取明确采纳且可出题的审定，原名称仍是图文表关联键。"""
    assessment = row['assessment']
    if (row['status'] != 'assessed' or row['adoption_status'] != 'accepted' or not row['review_note']
            or assessment['identity_status'] != 'resolved' or assessment['task_status'] != 'ready'
            or assessment['name_relation'] not in {'unchanged', 'equivalent'} or not row['assessment_id']
            or not assessment['core_facts'] or not assessment['task_sketch']):
        raise ValueError('Authoring requires an adopted, resolved, task-ready assessment without scope change')
    record = project_context(row, source=source)
    return {'concept': row['original_name'], 'taxonomy': row['taxonomy'] or [], 'concept_record': record}


def prepare_request(row, *, max_context_chars, prompt_chars, document_reads=False, document_paths=False):
    """一行概念 → 模型载荷；审定证据按固定引用读取，失败留行，超预算不截断。"""
    evidence = json.loads(row.get('evidence_json') or '[]') if row['status'] == 'ready' else []
    examples = json.loads(row.get('authoring_images_json') or '[]')
    payload = {
        'concept': row['concept'],
        'taxonomy': row.get('taxonomy') or [],
        'positive_examples': [
            {'number': ref['number'], 'image_number': i}
            for i, ref in enumerate(examples, 1)
        ],
    }
    result = {**row, 'prompt_payload': payload, 'concept_context': '', 'prompt_images': [], 'document_resources': {}}
    if row['status'] != 'ready':
        return result
    if row.get('concept_record') is not None:
        payload['concept'] = row['concept_record']['canonical_name']
        try:
            result['concept_context'] = render_concept_context(row['concept_record'],
                include_document_ids=document_reads and not document_paths,
                include_document_paths=document_reads and document_paths)
            if document_reads and not document_paths:
                result['document_resources'] = document_resources(row['concept_record'])
        except ConceptEvidenceError as error:
            return {**result, 'status': 'invalid_evidence', 'reason': str(error)}
    if evidence:
        # 独立文章正文与审定原文共同作为作者依据，保留各自来源编号。
        sections = ['## 依据材料']
        for item in evidence:
            sections.extend([f'### 文章材料 A{item["number"]} · {item["title"]}', item['text']])
        result['concept_context'] = '\n\n'.join(filter(None, [result['concept_context'], *sections]))
    # 审定资料及原文也计入预算；全量交付或明确跳过，不悄悄删除条件和依据。
    context_chars = prompt_chars + len(json.dumps(payload, ensure_ascii=False)) + len(result['concept_context'])
    if context_chars > max_context_chars:
        result.update(status='needs_context_budget',
                      reason='Complete concept context and evidence text exceed max_context_chars; no text was truncated')
    else:
        # 实际交付前只读取一次图片字节；ObjectRef 校验 SHA，pixels 校验解码和 MIME。
        if row.get('authoring_variant', 'standard') == 'standard':
            result['prompt_images'] = [image_data_url(ref['object_ref']) for ref in examples]
        elif examples:
            from benchmark.t2i.v2.operators.images import positive_image_data_url
            try:
                result['prompt_images'] += [positive_image_data_url(ref['object_ref']) for ref in examples]
            except (OSError, ValueError) as error:
                return {**result, 'status':'invalid_positive_image', 'reason':str(error), 'prompt_images':[]}
    return result


def check_response(row, *, question_schema):
    """一行模型响应 → 含考点/依据/判据的单题或不足原因；只验结构，不做语义审题。"""
    status, reason = row['status'], row['reason']
    error = row.get('design_error')
    call = dict(row.get('design_call') or (error or {}).get('call', {}))
    environment = call.pop('environment', {})
    context_json = json.dumps(environment.get('observations', []), ensure_ascii=False, sort_keys=True)
    if environment:
        call['environment'] = {k: v for k, v in environment.items() if k != 'observations'}
        call['environment']['turns'] = [
            {**{k: v for k, v in turn.items() if k not in {'reasoning', 'attempts'}},
             'attempts': [{k: v for k, v in attempt.items() if k != 'reasoning'}
                          for attempt in turn.get('attempts', [])]}
            for turn in environment.get('turns', [])]
    reasoning = call.pop('reasoning', None)
    # reasoning 单独落列；调用引用及重试轨迹无需再重复保存这段大文本。
    if 'attempts' in call:
        call['attempts'] = [{k: v for k, v in attempt.items() if k != 'reasoning'}
                            for attempt in call['attempts']]
    call_json = json.dumps(call, ensure_ascii=False)
    question = None
    if status == 'ready' and error:
        status = 'pending' if error['type'] == 'PromptResponsePending' else 'failed'
        reason = error['detail']
    elif status == 'ready':
        result = row['design_result']
        question = result['question']
        # 空题是业务结果；非空时用 YAML 的同一字段契约检查单个题目，拒绝列表及旧字段。
        if question is not None:
            try:
                validate_instance(question, {**question_schema, 'type': 'object'}, label='question')
            except SchemaValidationError as error:
                return {**row, 'status': 'invalid_response', 'reason': str(error), 'question': None,
                        'reasoning': reasoning, 'call_json': call_json, 'authoring_context_json': context_json}
        if question is None:
            reason = result.get('reason', '').strip()
            status = 'insufficient' if reason else 'invalid_response'
            reason = reason or 'A null question requires a concrete reason'
        elif result.get('reason', '').strip():
            status, reason = 'invalid_response', 'A question cannot also report an insufficient reason'
        elif any(not value.strip() for value in [
            question['instruction'], *(v for point in question['test_points'] for v in point.values())
        ]):
            status, reason = 'invalid_response', 'Whitespace-only question fields'
        else:
            status = 'candidate'
    return {
        **row, 'status': status, 'reason': reason,
        'question': question if status == 'candidate' else None,
        'reasoning': reasoning, 'call_json': call_json, 'authoring_context_json': context_json,
    }
