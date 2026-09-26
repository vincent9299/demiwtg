"""出题数据流的行函数：构造单概念请求、检查单概念响应；不调度 Dataset 或调用模型。"""
import json
from demiflow.schema import SchemaValidationError, validate_instance

from benchmark.t2i.v2.operaters.images import image_data_url


def prepare_request(row, *, max_context_chars, prompt_chars):
    """一行概念及可选材料 → 模型载荷；空材料正常出题，超上下文预算才跳过。"""
    references = json.loads(row['references_json']) if row['status'] == 'ready' else []
    image_references = [ref for ref in references if ref['kind'] == 'image']
    payload = {
        'concept': row['concept'],
        # 图片按原有材料顺序发送；image_number 只指实际图片序号，材料 number 不变。
        'references': [ref for ref in references if ref['kind'] == 'text'] + [
            {**{k: v for k, v in ref.items() if k not in {'blob_ref', 'mime'}}, 'image_number': i}
            for i, ref in enumerate(image_references, 1)
        ],
    }
    result = {**row, 'prompt_payload': payload, 'prompt_images': []}
    # 只检查原有正文字符预算，不截断、不拆分、不改变图片数量限制。
    if row['status'] == 'ready' and prompt_chars + len(json.dumps(payload, ensure_ascii=False)) > max_context_chars:
        result.update(status='needs_context_budget',
                      reason='Complete reference text exceeds max_context_chars; no text was truncated')
    else:
        # 实际交付前只读取一次图片字节；BlobRef 校验 SHA，pixels 校验解码和 MIME。
        result['prompt_images'] = [image_data_url(ref['blob_ref']) for ref in image_references]
    return result


def check_response(row, *, question_schema):
    """一行模型响应 → 一道题或不足原因及调用引用；不做语义审题。"""
    status, reason = row['status'], row['reason']
    error = row.get('design_error')
    call = dict(row.get('design_call') or (error or {}).get('call', {}))
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
                        'reasoning': reasoning, 'call_json': call_json}
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
        'reasoning': reasoning, 'call_json': call_json,
    }
