"""单概念编辑出题：编号现有图片、构造请求、检查单题并绑定选中的原图。"""
import json
import os
from pathlib import Path

import yaml
from demiflow.execution.artifacts import digest
from demiflow.operator_llm.client import request_messages
from demiflow.operator_llm.lance_journal import materialize as materialize_offline
from demiflow.operator_llm.model import OperatorLLMRequest
from demiflow.operator_llm.parser import parse_prompt_pack
from demiflow.operator_llm.template import render_template
from demiflow.schema import SchemaValidationError, validate_instance
from preparation.operaters.inputs import asset_for_item, add_image
from preparation.operaters.runfiles import (
    run_manifest, run_records, prompt_store, read_record, response_records,
)
from preparation.prompts import validate_local_endpoint

PROMPTS = Path(__file__).resolve().parents[1] / 'prompts'
STAGE = 'design_question'


def prompt_config(run, config):
    """绑定本次模型配置和原生调用日志；固定正文及响应结构直接读取 YAML。"""
    spec = yaml.safe_load((PROMPTS / 'tasks.yaml').read_text())
    model = config['model']
    if config['mode'] == 'local':
        validate_local_endpoint(model['base_url'], model['model'])
        os.environ.setdefault('CURATION_DOWNSTREAM_MODEL_KEY', 'local-no-auth')
        options = {
            'lance_journal': prompt_store(run, 'calls'), 'timeout_s': model['timeout_s'],
            'verify_model': True, 'require_finish_reason_stop': True, 'trust_env': False,
            'request_options': {
                'temperature': 0, 'max_tokens': model['max_output_tokens'],
                'response_format': {'type': 'json_object'},
                'chat_template_kwargs': {'enable_thinking': False},
            },
        }
        name = model['model']
    else:
        name = config['author']['model']
        options = {'offline_store': prompt_store(run)}
    spec['prompts'][STAGE]['model'].update(name=name, base_url=model['base_url'])
    text = yaml.safe_dump(spec, allow_unicode=True, sort_keys=False)
    run_records(run).put('prompt_config', {'yaml': text, 'execution_options': options})
    return parse_prompt_pack(text), options


def inputs_for(row):
    """完整交付当前概念材料；所有已交付图片均可由作者选为原图，按实际发送顺序编号。"""
    references, images, source_candidates = [], [], []
    for number, item in enumerate(row['materials'], 1):
        reference = {'number': number, 'kind': item['kind']}
        if item['kind'] == 'text':
            reference.update(
                text=item['text'], sources=item.get('sources', []),
                references=item.get('references', []), limitations=item.get('review', {}).get('limitations'),
            )
        else:
            asset = asset_for_item(item)
            content = []
            add_image(content, asset, '')
            images.append(content[-1]['image_url']['url'])
            reference.update(
                image_number=len(images), source=asset.get('source'), origin=asset.get('origin'),
                support=item.get('review', {}).get('support_scope'),
                limitations=item.get('review', {}).get('limitations'), placement=item.get('placement'),
            )
            source_candidates.append({
                'image_number': len(images), 'material_number': number,
                'item_id': item['item_id'], 'asset': asset,
            })
        references.append(reference)
    return {'payload': {'concept': row['concept'], 'references': references}, 'images': images}, source_candidates


def fail(row, status, reason):
    """保留本概念材料及失败原因，不生成题目行。"""
    return {**row, 'status': status, 'question': None, 'reason': reason,
            'issues': row.get('issues', []) + [reason]}


def prepare_design(row, *, run, pack):
    """一行概念材料 → 一个包含候选原图的请求；缺图及超限不调用、不截断。"""
    if row['status'] != 'knowledge_available':
        return {**row, 'question': None}
    if not any(item['kind'] == 'image' for item in row['materials']):
        return fail(row, 'needs_source_images', 'No supplied image can be selected as an editing source')
    values, source_candidates = inputs_for(row)
    definition = pack.prompt_definitions[STAGE]
    cfg = run_manifest(Path(run))['config']
    chars = len(json.dumps(values['payload'], ensure_ascii=False)) + len(definition.template.source)
    if chars > cfg['max_context_chars'] or len(values['images']) > cfg['max_reference_images']:
        return fail(row, 'needs_context_budget',
                    f'Complete materials require {chars} characters/{len(values["images"])} images; '
                    'no materials were truncated')
    native = OperatorLLMRequest(
        STAGE, definition.version, definition.model.name,
        render_template(definition.template, values), response_schema=definition.response_schema,
    )
    messages = request_messages(native)
    request = {
        'stage': STAGE, 'task_id': row['task_id'], 'messages': messages,
        'image_roles': [{**{k: v for k, v in candidate.items() if k != 'asset'},
                         'role': 'source_candidate_and_reference',
                         'sha256': candidate['asset']['sha256']} for candidate in source_candidates],
        'input_sha256': digest(messages), 'prompt_version': definition.version, 'response_envelope': 'result',
    }
    if cfg['mode'] == 'offline':
        _, source = materialize_offline(native, prompt_store(run))
        request['native_offline'] = {'request_ref': source.to_dict()}
    ref = run_records(run).put('request/' + STAGE + '/' + row['task_id'] + '/' + request['input_sha256'], request)
    return {**row, 'prompt_payload': values['payload'], 'prompt_images': values['images'],
            'source_candidates': source_candidates,
            'design_binding': {'request_ref': ref.to_dict(), 'input_sha256': request['input_sha256']}}


def prompt_responses(run):
    """离线补交响应改变设计阶段指纹，允许同名续跑消费新响应。"""
    return response_records(run, STAGE)


def apply_design(row, *, run, guard, question_schema):
    """检查单题业务条件，把原图编号绑定到实际提供的图片；成功仍为待审题目。"""
    row = {k: v for k, v in row.items() if k not in {'prompt_payload', 'prompt_images'}}
    if row['status'] != 'knowledge_available':
        return row
    error = row.get('design_error')
    if error:
        return fail(row, 'pending' if error['type'] == 'PromptResponsePending' else 'failed', error['detail'])
    result, call = row['design_result'], row['design_call']
    # 原始响应及作者身份仍固定保存，不把结构有效当作审核通过。
    if call.get('mode') == 'offline':
        provenance = {**call.get('offline_metadata', {}), 'mode': 'offline', 'model': call['model'],
                      'response_ref': call['response_ref'], 'response_sha256': call['response_sha256']}
        if not provenance.get('reviewer') or provenance.get('reviewer_kind') not in {'human', 'assistant', 'independent'}:
            return fail(row, 'failed', 'Offline response must identify its actual author')
        if provenance.get('raw_ref') and digest(read_record(provenance['raw_ref'])) != provenance.get('raw_sha256'):
            return fail(row, 'failed', 'Offline raw response changed')
    else:
        provenance = {**call, 'mode': 'local', 'reviewer_kind': 'model'}
    saved = {'binding': row['design_binding'], 'result': result, 'provenance': provenance}
    run_records(run).put('response/' + STAGE + '/' + row['task_id'] + '/' + digest(saved), saved)
    row = {**row, 'design_provenance': provenance}
    question = result['question']
    if question is None:
        reason = result.get('reason', '').strip()
        return fail(row, 'insufficient' if reason else 'invalid_response',
                    reason or 'A null question requires a concrete reason')
    try:
        validate_instance(question, {**question_schema, 'type': 'object'}, label='question')
    except SchemaValidationError as error:
        return fail(row, 'invalid_response', str(error))
    if result.get('reason', '').strip():
        return fail(row, 'invalid_response', 'A question cannot also report an insufficient reason')
    if any(not value.strip() for value in [question['instruction'],
            *(value for point in question['test_points'] for value in point.values())]):
        return fail(row, 'invalid_response', 'Whitespace-only question fields')
    number = question['source_image']
    if not 1 <= number <= len(row['source_candidates']):
        return fail(row, 'invalid_response', 'source_image must identify a supplied image')
    selected = row['source_candidates'][number - 1]
    try:
        guard.check_instruction(question['instruction'], False)
    except ValueError as error:
        return fail(row, 'invalid_response', str(error))
    return {**row, 'question': question, 'edit_source': selected['asset'],
            'source_material_number': selected['material_number'], 'status': 'unreviewed', 'reason': ''}
