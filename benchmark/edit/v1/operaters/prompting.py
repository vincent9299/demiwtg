"""绑定 v6.1 编辑请求和原图；固定正文在 tasks.yaml，caption/desc 保持为待核假设。"""
import base64
import json
from pathlib import Path

import yaml
from demiflow.operator_llm.client import request_messages
from demiflow.operator_llm.lance_journal import materialize as materialize_offline
from demiflow.operator_llm.model import OperatorLLMRequest
from demiflow.operator_llm.parser import parse_prompt_pack
from demiflow.operator_llm.template import render_template

from demiflow.execution.artifacts import digest
from preparation.operaters.runfiles import prompt_store, run_manifest, run_records

PROMPTS = Path(__file__).resolve().parents[1] / 'prompts'


def prompt_config(run, config):
    spec = yaml.safe_load((PROMPTS / 'tasks.yaml').read_text())
    model = config['model']
    if config['mode'] == 'local':
        import os
        from preparation.prompts import validate_local_endpoint
        validate_local_endpoint(model['base_url'], model['name'])
        os.environ.setdefault('CURATION_DOWNSTREAM_MODEL_KEY', 'local-no-auth')
        options = {'lance_journal': prompt_store(run, 'calls'), 'timeout_s': model['timeout_s'],
                   'verify_model': True, 'require_finish_reason_stop': True, 'trust_env': False,
                   'request_options': {'temperature': config['temperature'],
                                       'max_tokens': model['max_output_tokens'],
                                       'response_format': {'type': 'json_object'},
                                       'chat_template_kwargs': {'enable_thinking': False}}}
        name = model['name']
    else:
        name = config['author']['model']
        options = {'offline_store': prompt_store(run)}
    for prompt in spec['prompts'].values():
        prompt['model'] = {**prompt['model'], 'name': name, 'base_url': model['base_url']}
    text = yaml.safe_dump(spec, allow_unicode=True, sort_keys=False)
    run_records(run).put('prompt_config', {'yaml': text, 'execution_options': options})
    return parse_prompt_pack(text), options


def _image_data_url(image):
    """Before-image bytes with SHA identity re-check; path is provenance only."""
    from preparation.operaters.inputs import asset_pixels
    data = asset_pixels(image)
    if digest(data) != image.get('sha256'):
        raise ValueError('source image bytes do not match recorded identity')
    return "data:image/png;base64," + base64.b64encode(data).decode('ascii')


def prepare_construct(row, *, run, pack, config):
    """v6.1 authors from the before image: instructions + plan text + one image."""
    if row['status'] != 'ready_to_author':
        return row
    payload = {'出题请求全文': row['author_text'], '实体名': row['instance'],
               '目标编辑类型': row['target_edit_type'], '目标层级': row['target_level'],
               'suite': row['suite'], '主域': row.get('main_domain'),
               '作者模型': row.get('author_model', config['author']['model'])}
    values = {'payload': payload,
              'images': [_image_data_url(row['source_image'])]}
    roles = [{'role': 'edit_source_before', 'sha256': row['source_image']['sha256']}]
    return _bind_request(row, 'construct', values, run, pack, roles)


def _bind_request(row, stage, values, run, pack, roles=None):
    prompt = pack.prompt_definitions[stage]
    native = OperatorLLMRequest(stage, prompt.version, prompt.model.name,
        render_template(prompt.template, values), response_schema=prompt.response_schema)
    messages = request_messages(native)
    request = {'stage': stage, 'task_id': row['task_id'], 'messages': messages,
               'image_roles': roles or [], 'input_sha256': digest(messages),
               'prompt_version': native.prompt_version, 'response_envelope': 'result'}
    if run_manifest(Path(run))['config']['mode'] == 'offline':
        _, source = materialize_offline(native, prompt_store(run))
        request['native_offline'] = {'request_ref': source.to_dict()}
    ref = run_records(run).put('request/' + stage + '/' + row['task_id'] + '/' + request['input_sha256'], request)
    return {**row, **{'prompt_' + k: v for k, v in values.items()},
            'image_roles': roles or [],
            stage + '_binding': {'request_ref': ref.to_dict(), 'input_sha256': request['input_sha256']}}


def apply_construct(row, *, run):
    """Join expected_meta exactly like the historical dispatch contract."""
    from benchmark.edit.v1.operaters.plan import expected_meta
    if row['status'] != 'ready_to_author':
        return row
    row = {k: v for k, v in row.items() if not k.startswith('prompt_')}
    error = row.get('construct_error')
    if error:
        status = 'pending_construct' if error['type'] == 'PromptResponsePending' else 'failed_construct'
        return {**row, 'status': status, 'fail_reason': error['detail']}
    question = row['construct_result']
    call = row.get('construct_call') or {}
    try:
        if isinstance(question, str):
            question = json.loads(question)
        status = question.get('status')
        if status == 'cannot_construct':
            saved = _save(run, row, question, call)
            return {**row, 'question': question, 'status': 'cannot_construct',
                    'construct_provenance': call, **saved}
        if status != 'constructed':
            return {**row, 'status': 'invalid_construct',
                    'fail_reason': f"unknown construct status: {status!r}"}
        if not str(question.get('edit_instruction') or '').strip():
            return {**row, 'status': 'invalid_construct', 'fail_reason': 'constructed without edit_instruction'}
    except (ValueError, json.JSONDecodeError, AttributeError) as exc:
        return {**row, 'status': 'parse_failed', 'fail_reason': str(exc)}
    meta = expected_meta(row)
    question = {**question, **meta}
    saved = _save(run, row, question, call)
    return {**row, 'question': question, 'status': 'authored',
            'construct_provenance': call, **saved}


def _save(run, row, question, call):
    saved = {'binding': row['construct_binding'], 'result': question, 'call': call}
    ref = run_records(run).put('response/construct/' + row['task_id'] + '/' + digest(saved), saved)
    return {'response_ref': ref.to_dict()}


def prompt_responses(run, stage='construct', task_prefix=None):
    from preparation.operaters.runfiles import response_records
    return response_records(run, stage, task_prefix)
