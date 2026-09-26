"""Native demiflow prompt transport, bindings and auditable response ingestion."""
import json
from pathlib import Path
import re

import yaml
from demiflow.operator_llm.client import request_messages
from demiflow.operator_llm.model import OperatorLLMRequest
from demiflow.operator_llm.lance_journal import materialize as materialize_offline
from demiflow.operator_llm.parser import parse_prompt_pack
from demiflow.operator_llm.template import render_template

from demiflow.execution.artifacts import digest
from preparation.operaters.runfiles import run_records, prompt_store, read_record, response_records

PROMPTS = Path(__file__).resolve().parents[1] / 'prompts'


def prompt_config(run, config):
    run = Path(run).resolve()
    cfg = config['judge']
    spec = yaml.safe_load((PROMPTS / 'evaluation.yaml').read_text())
    for definition in spec['prompts'].values():
        definition['model'] = {'name': cfg['model'], 'transport': 'openai_compatible',
                               'base_url': cfg['base_url'], 'api_key_env': cfg['api_key_env']}
    if cfg['mode'] == 'offline':
        options = {'offline_store': prompt_store(run)}
    else:
        options = {'lance_journal': prompt_store(run, 'calls'), 'timeout_s': cfg['timeout_s'],
                   'require_finish_reason_stop': True, 'trust_env': False,
                   'request_options': {'max_tokens': cfg['max_output_tokens'],
                                       'response_format': {'type': 'json_object'}}}
        if cfg.get('reasoning_effort'):
            options['request_options']['reasoning_effort'] = cfg['reasoning_effort']
    text = yaml.safe_dump(spec, allow_unicode=True, sort_keys=False)
    run_records(run).put('judge_config', {'yaml': text, 'execution_options': options,
                                              'identity': cfg})
    return parse_prompt_pack(text), options


def template(task_type, edit_type=None):
    text = (PROMPTS / f'judge_{task_type}_v1.md').read_text()
    system = re.search(r'^## SYSTEM\n\n```text\n(.*?)\n```', text, re.M | re.S).group(1)
    user = re.search(r'^## USER\n\n```text\n(.*?)\n```', text, re.M | re.S).group(1)
    if task_type == 'edit':
        types = dict(re.findall(r'<!--TYPE:(\w+)-->\s*(.*?)\s*<!--/TYPE-->', text, re.S))
        if edit_type not in types:
            raise ValueError('Unsupported frozen edit_type: ' + str(edit_type))
        user = user.replace('{{TYPE_NOTES}}', types[edit_type]).replace('{{EDIT_TYPE}}', edit_type)
    return system, user


def prepare_native(row, stage, identity, values, roles, run, pack, config):
    cfg = config['judge']
    definition = pack.prompt_definitions[stage]
    # 正文移入 YAML 后仍计入原有上下文预算；只统计文本，不把图片 data URL 当作正文。
    chars = len(definition.template.source) + sum(len(v) for k, v in values.items() if k != 'images')
    if chars > cfg['max_context_chars'] or len(roles) > cfg['max_images']:
        return {**row, stage + '_status': 'input_error',
                stage + '_reason': 'Complete judge context exceeds explicit budget; not truncated'}
    native = OperatorLLMRequest(stage, definition.version, definition.model.name,
                               render_template(definition.template, values),
                               response_schema=definition.response_schema)
    messages = request_messages(native)
    request = {'stage': stage, 'task_id': identity, 'messages': messages, 'image_roles': roles,
               'input_sha256': digest(messages), 'prompt_version': native.prompt_version,
               'response_envelope': 'result', 'model': cfg['model'],
               'reasoning_effort': cfg['reasoning_effort']}
    if cfg['mode'] == 'offline':
        _, source = materialize_offline(native, prompt_store(run))
        request['native_offline'] = {'request_ref': source.to_dict()}
    ref = run_records(run).put('request/' + stage + '/' + identity + '/' + request['input_sha256'], request)
    return {**row, **{'prompt_' + k: v for k, v in values.items()},
            stage + '_status': 'prepared', stage + '_binding': {'request_ref': ref.to_dict(), 'input_sha256': request['input_sha256']}}


def prompt_responses(run, stage):
    return response_records(run, stage)


def apply_native(row, stage, run, config):
    """Validate wire/provenance, not truth. Pending never becomes a model failure."""
    result = {k: v for k, v in row.items() if not k.startswith('prompt_')}
    if row.get(stage + '_status') != 'prepared':
        return result, None
    error = row.get(stage + '_error')
    if error:
        status = 'pending' if error['type'] == 'PromptResponsePending' else 'call_error'
        return {**result, stage + '_status': status, stage + '_reason': error['detail']}, None
    try:
        binding = row[stage + '_binding']
        request = read_record(binding['request_ref'])
        if digest(request['messages']) != binding['input_sha256']:
            raise ValueError('Prompt request binding changed')
        call = row[stage + '_call']
        cfg = config['judge']
        if call.get('model') != cfg['model']:
            raise ValueError('Judge model differs from frozen configuration')
        if call.get('mode') == 'offline':
            meta = call.get('offline_metadata', {})
            if not meta.get('reviewer') or meta.get('reviewer_kind') not in {'assistant', 'independent', 'human'}:
                raise ValueError('Offline response must identify its actual reviewer')
            if meta.get('reasoning_effort') != cfg['reasoning_effort']:
                raise ValueError('Offline reasoning effort differs from frozen configuration')
            if meta.get('raw_ref') and digest(read_record(meta['raw_ref'])) != meta.get('raw_sha256'):
                raise ValueError('Offline raw response changed')
        payload = row[stage + '_result']
        if call.get('mode') == 'offline' and meta.get('raw_ref'):
            raw = read_record(meta['raw_ref'])
            business = raw['result'] if isinstance(raw, dict) and set(raw) == {'result'} else raw
            if business != payload:
                raise ValueError('Offline result differs from original raw response')
        saved = {'binding': binding, 'result': payload, 'call': call}
        ref = run_records(run).put('response/' + stage + '/' + request['task_id'] + '/' + digest(saved), saved)
        return {**result, stage + '_provenance': {'record_ref': ref.to_dict(), 'sha256': digest(saved)}}, payload
    except (KeyError, ValueError, OSError, TypeError, AttributeError) as error:
        return {**result, stage + '_status': 'invalid_response', stage + '_reason': str(error)}, None


def fill(user, values):
    # Substitute only the original template; braces inside source text are data.
    def replace(match):
        name = match.group(1)
        if name not in values:
            raise ValueError('Missing judge input: ' + name)
        value = values[name]
        return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return re.sub(r'\{\{(\w+)\}\}', replace, user)
