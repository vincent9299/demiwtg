"""绑定 v6.0 逐题载荷与图片，应用原有响应检查；固定正文在 tasks.yaml。"""
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

from benchmark.t2i.v1.operaters.samples import author_text

PROMPTS = Path(__file__).resolve().parents[1] / 'prompts'


def prompt_config(run, config):
    """Build this pipeline's prompt pack; the model identity comes from config."""
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


def _bind_request(row, stage, values, run, pack, roles=None):
    prompt = pack.prompt_definitions[stage]
    native = OperatorLLMRequest(stage, prompt.version, prompt.model.name,
        render_template(prompt.template, values), response_schema=prompt.response_schema)
    messages = request_messages(native)
    request = {'stage': stage, 'task_id': row['task_id'], 'messages': messages,
               'image_roles': roles or [],
               'input_sha256': digest(messages), 'prompt_version': native.prompt_version,
               'response_envelope': 'result'}
    if run_manifest(Path(run))['config']['mode'] == 'offline':
        _, source = materialize_offline(native, prompt_store(run))
        request['native_offline'] = {'request_ref': source.to_dict()}
    ref = run_records(run).put('request/' + stage + '/' + row['task_id'] + '/' + request['input_sha256'], request)
    return {**row, **{'prompt_' + k: v for k, v in values.items()},
            'image_roles': roles or [],
            stage + '_binding': {'request_ref': ref.to_dict(), 'input_sha256': request['input_sha256']}}


def prepare_synth(row, *, run, pack, config):
    """v6.0 authors without images: payload carries the composed authoring text."""
    if row['status'] != 'ready_to_author':
        return row
    payload = {'出题请求全文': author_text(row, config),
               '概念名': row['instance'], '主域': row.get('main_domain'),
               '作者模型': row['author_model']}
    values = {'payload': payload, 'images': []}
    return _bind_request(row, 'synthesize', values, run, pack)


# ---------------------------------------------------------------- 历史解析移植
import re


def _lenient_object(text: str, start: int) -> dict:
    """Ported from eval_synthesize._lenient_object: raw_decode + 尾逗号容错。"""
    seg = text[start:]
    for _ in range(10):
        try:
            obj, _ = json.JSONDecoder().raw_decode(seg)
            return obj
        except json.JSONDecodeError as e:
            p = e.pos - 1
            while p >= 0 and seg[p] in " \t\r\n":
                p -= 1
            if p >= 0 and seg[p] == ",":
                seg = seg[:p] + seg[p + 1:]
                continue
            raise
    raise ValueError("尾逗号修复超过 10 次仍失败")


def extract_json_object(content) -> dict:
    """Ported from eval_synthesize.extract_json_object：抠第一个完整 JSON 对象。"""
    text = str(content or '').strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```\s*$", "", text)
    start = text.find("{")
    if start < 0:
        raise ValueError(f"输出中无 JSON 对象: {text[:200]!r}")
    return _lenient_object(text, start)


def apply_synth(row, *, run):
    """Parse the authored question exactly like the historical pipeline."""
    if row['status'] != 'ready_to_author':
        return row
    row = {k: v for k, v in row.items() if not k.startswith('prompt_')}
    error = row.get('synthesize_error')
    if error:
        status = 'pending_synth' if error['type'] == 'PromptResponsePending' else 'failed_synth'
        return {**row, 'status': status, 'fail_reason': error['detail']}
    question = row['synthesize_result']
    call = row.get('synthesize_call') or {}
    try:
        if isinstance(question, str):
            question = extract_json_object(question)
        if question.get('status') == 'cannot_construct':
            return _finish(row, run, question, call, 'cannot_construct')
        if question.get('status') == 'reject':
            outcome = _finish(row, run, question, call, 'rejected')
            return {**outcome, 'reject_code': question.get('reject_code')}
        if not question.get('gen_prompt'):
            # reasoning 截断产生的空对象：与历史一致判失败，不混入题库
            return {**row, 'status': 'empty_design',
                    'fail_reason': '输出无 gen_prompt（reasoning 未落成/截断）'}
    except (ValueError, json.JSONDecodeError, AttributeError) as exc:
        return {**row, 'status': 'parse_failed', 'fail_reason': str(exc)}
    return _finish(row, run, question, call, 'authored')


def _finish(row, run, question, call, status):
    saved = {'binding': row['synthesize_binding'], 'result': question, 'call': call}
    ref = run_records(run).put('response/synthesize/' + row['task_id'] + '/' + digest(saved), saved)
    return {**row, 'question': question, 'status': status, 'synth_provenance': call,
            'response_ref': ref.to_dict()}


def prompt_responses(run, stage='synthesize', task_prefix=None):
    from preparation.operaters.runfiles import response_records
    return response_records(run, stage, task_prefix)
