"""Business preparation/application for native demiflow prompt operators.

No model client or Dataset scheduling lives here. HTTP and offline transports
receive the same native template, schema and ordered pixels.
"""
from preparation.operaters.runfiles import run_manifest
import json
import os
from pathlib import Path

import yaml
from demiflow.operator_llm.client import request_messages
from demiflow.operator_llm.model import OperatorLLMRequest
from demiflow.operator_llm.lance_journal import materialize as materialize_offline
from demiflow.operator_llm.parser import parse_prompt_pack
from demiflow.operator_llm.template import render_template

from preparation.prompts import validate_local_endpoint
from preparation.operaters.inputs import model_content, add_image
from demiflow.execution.artifacts import digest
from preparation.operaters.runfiles import run_records, prompt_store, read_record

PROMPTS = Path(__file__).resolve().parents[1] / 'prompts'


def execution_options(run, config):
    """Provider options use the platform HTTP client; keys stay in the environment."""
    model = config['model']
    if config['mode'] == 'local':
        validate_local_endpoint(model['base_url'], model['model'])
        os.environ.setdefault('CURATION_DOWNSTREAM_MODEL_KEY', 'local-no-auth')
        provider = {'chat_template_kwargs': {'enable_thinking': False}}
        verify = True
    elif config['mode'] == 'modelhub':
        from urllib.parse import urlparse
        endpoint = urlparse(model['base_url'])
        if (endpoint.scheme != 'http' or endpoint.hostname not in ('localhost', '127.0.0.1', '::1')
                or endpoint.path.rstrip('/') != '/v1' or endpoint.username or endpoint.password
                or endpoint.query or endpoint.fragment):
            raise ValueError('modelhub requires an explicit loopback /v1 gateway URL')
        # This installation's gateway is unauthenticated; actual upstream keys live in modelhub.
        os.environ.setdefault(model.get('api_key_env', 'MODELHUB_API_KEY'), 'anything')
        provider, verify = {}, 'listed'
    elif config['mode'] == 'offline':
        return {'offline_store': prompt_store(run)}
    else:
        raise ValueError('Unknown T2I prompt mode')
    return {'lance_journal': prompt_store(run, 'calls'), 'timeout_s': model['timeout_s'],
            'verify_model': verify, 'require_finish_reason_stop': True, 'trust_env': False,
            'request_options': {'temperature': 0, 'max_tokens': model['max_output_tokens'],
                'response_format': {'type': 'json_object'}, **provider, **model.get('request_options', {})}}


def prompt_config(run, config):
    spec = yaml.safe_load((PROMPTS / 'tasks.yaml').read_text())
    model = config['model']
    options = execution_options(run, config)
    name = config['author']['model'] if config['mode'] == 'offline' else model['model']
    for prompt in spec['prompts'].values():
        prompt['model'] = {**prompt['model'], 'name': name, 'base_url': model['base_url'],
                           'api_key_env': model.get('api_key_env', 'CURATION_DOWNSTREAM_MODEL_KEY')}
    text = yaml.safe_dump(spec, allow_unicode=True, sort_keys=False)
    run_records(run).put('prompt_config', {'yaml': text, 'execution_options': options})
    return parse_prompt_pack(text), options


def frozen_case_prompts(row, config, *, stages=('design_candidates', 'review_sample')):
    """Rebuild the *saved* model-visible messages, checking exact equality before calls.

    The review arm always judges the original candidate, independently of the new
    design response. Current prompt files and current material selection are unused.
    """
    if config['mode'] not in {'local', 'modelhub'}:
        raise ValueError('Frozen HTTP comparison requires local or modelhub mode')
    if not stages or len(set(stages)) != len(stages) or set(stages) - {'design_candidates', 'review_sample'}:
        raise ValueError('Select design_candidates and/or review_sample once each')
    model = config['model']
    definitions, inputs = {}, []
    for stage in stages:
        source_ref = row[stage + '_call']['request_ref']
        source = read_record(source_ref)
        messages = source['payload']['messages']
        bound = read_record(row[stage + '_binding']['request_ref'])
        if (messages != bound['messages'] or digest(messages) != bound['input_sha256']
                or source['stage'] != stage or source['schema_attempt'] != 1):
            raise ValueError('Saved request binding differs or contains a schema-retry prompt')
        content = messages[1]['content']
        if not isinstance(content, list) or content[0]['type'] != 'text' or content[-1]['type'] != 'text':
            raise ValueError('Expected the saved T2I numbered-image request')
        images = [part['image_url']['url'] for part in content if part['type'] == 'image_url']
        values = {'body': content[0]['text'], 'images': images, 'tail': content[-1]['text']}
        definitions[stage] = {
            'version': source['prompt_version'],
            'model': {'name': model['model'], 'transport': 'openai_compatible',
                      'base_url': model['base_url'],
                      'api_key_env': model.get('api_key_env', 'CURATION_DOWNSTREAM_MODEL_KEY')},
            'schema_retries': 0,
            'template': '{{ body }}{{ images | numbered_image }}{{ tail }}',
            'response_schema': source['response_schema']}
        original_options = {k: v for k, v in source['payload'].items() if k not in {'model', 'messages'}}
        effective = {**original_options}
        if config['mode'] == 'modelhub':
            effective.pop('chat_template_kwargs', None)  # vLLM-only option
        effective.update(model.get('request_options', {}))
        inputs.append({'concept': row['concept'], 'task_id': row['task_id'],
            'status': 'comparison_requested', 'stage': stage, 'source_request_ref': source_ref,
            'input_sha256': digest(messages), 'source_options': original_options,
            'effective_options': effective, 'baseline_model': source['payload']['model'],
            'baseline_result': row['candidate_design' if stage == 'design_candidates' else 'review_sample'],
            'prompt_body': values['body'], 'prompt_images': images, 'prompt_tail': values['tail']})
    pack = parse_prompt_pack(yaml.safe_dump({'schema_version': 'demiflow_prompt_pack_v2',
                                           'prompts': definitions}, allow_unicode=True, sort_keys=False))
    for item in inputs:
        prompt = pack.prompt_definitions[item['stage']]
        request = OperatorLLMRequest(prompt.name, prompt.version, prompt.model.name,
            render_template(prompt.template, {k: item['prompt_' + k] for k in ('body', 'images', 'tail')}),
            response_schema=prompt.response_schema)
        if request_messages(request) != read_record(item['source_request_ref'])['payload']['messages']:
            raise ValueError('Comparison must preserve exact system/user text, image bytes and ordering')
    return pack, inputs



def inputs_for(row, stage):
    if stage == 'review_sample':
        return review_inputs(row)
    if stage != 'design_candidates':
        raise ValueError('Unknown T2I prompt stage: ' + stage)
    if row['design_policy']['task_types'] != ['t2i'] or row.get('edit_source'):
        raise ValueError('T2I design requires T2I policy and no edit source')
    materials = row['materials']
    task = {'concept': row['concept'], 'consumer': row['branch'], 'policy': row['design_policy'],
            'previous_samples': row.get('previous_samples', []),
            'material_roles': {'evidence': 'construction_and_review_only',
                               'input_materials': 'explicit_answer_selection_may_be_empty',
                               'target_candidates': 'supervision_only'}}
    if row['design_policy'].get('target_aware'):
        task['target_reference_options'] = row['target_reference_options']
    # Author from final knowledge, not the repeatedly embedded source articles.
    # Full cited passages remain in the checkpoint for independent review.
    materials = [{**m, 'sources': [{k: s[k] for k in ('source_id', 'title', 'url') if k in s}
                                  for s in m.get('sources', [])]} for m in materials]
    content, roles = model_content(json.dumps(task, ensure_ascii=False), materials)
    for role in roles:
        role['role'] = 'construction_material'
    for part in content:
        if part['type'] == 'text':
            part['text'] = part['text'].replace('检索参考图；', '可选构题材料；不自动进入作答输入；')
    for number, asset in enumerate(row.get('design_targets', []), 1):
        add_image(content, asset, f'候选监督目标 {number}：仅用于设计训练任务，不是知识证据或作答参考。'
                  + json.dumps({'source':asset['source'], 'origin':asset.get('origin'),
                                'publication':asset.get('candidate_publication')}, ensure_ascii=False))
        roles.append({'role':'target_candidate', 'candidate_number':number,
                      'path':asset.get('path'), 'sha256':asset['sha256']})
    payload, images = [], []
    for part in content:
        if part['type'] == 'text':
            payload.append(part)
        else:
            images.append(part['image_url']['url'])
            payload.append({'type': 'image', 'number': len(images), 'role': roles[len(images)-1]['role']})
    return {'payload': payload, 'images': images}, roles


def review_inputs(row):
    from curation.t2i.operaters.operators import training_content
    task = {'concept': row['concept'], 'learning_objective': row['learning_objective'],
            'criteria': row['criteria'], 'condition': row['draft'].get('condition', ''),
            'construction_claims_not_verdicts': row['target_design_binding']}
    content, roles = model_content(json.dumps(task, ensure_ascii=False), row['materials'])
    content.insert(1, {'type': 'text', 'text': 'construction_evidence：以下仅供审核，不自动进入作答输入。'})
    for role in roles:
        role['role'] = 'construction_evidence'
    answer, answer_roles = training_content(row['draft']['instruction'], row['answer_materials'])
    content += [{'type': 'text', 'text': 'answer_input 开始：以下才是作答者实际收到的完整内容。'}]
    content += answer
    content += [{'type': 'text', 'text': 'answer_input 结束。以下目标仅用于审核和监督。'}]
    roles += [{**role, 'role': 'answer_reference'} for role in answer_roles]
    for asset in row['design_targets']:
        add_image(content, asset, 'supervision_target：逐判据检查这张目标图的实际像素。')
        roles.append({'role': 'supervision_target', 'sha256': asset['sha256']})
    # A figure used as both evidence and answer reference is sent only once;
    # its numbered placeholder still appears in both clearly labeled sections.
    payload, images, mapping, positions = [], [], [], {}
    role_iter = iter(roles)
    for part in content:
        if part['type'] == 'text':
            payload.append(part)
            continue
        role, url = next(role_iter), part['image_url']['url']
        if url not in positions:
            positions[url] = len(images) + 1
            images.append(url)
            mapping.append({'number': positions[url], 'uses': []})
        number = positions[url]
        mapping[number - 1]['uses'].append(role)
        payload.append({'type': 'image', 'number': number, 'role': role['role']})
    return {'payload': payload, 'images': images}, mapping


def prepare_design(row, *, run, pack):
    return prepare_prompt(row, 'design_candidates', 'knowledge_available', run, pack)


def prepare_review(row, *, run, pack):
    return prepare_prompt(row, 'review_sample', 'valid_sample', run, pack)


def prepare_prompt(row, stage, required_status, run, pack):
    """一次组装图文输入，用同一份内容做预算检查、请求留档和实际调用。"""
    if row['status'] != required_status:
        return row
    from curation.t2i.operaters.operators import fail
    cfg = run_manifest(Path(run))['config']
    values, roles = inputs_for(row, stage)
    chars = len(json.dumps(values['payload'], ensure_ascii=False)) + len(pack.prompt_definitions[stage].template.source)
    if chars > cfg['max_context_chars'] or len(roles) > cfg['max_reference_images']:
        return fail(row, 'needs_context_budget',
                    f'{stage} requires {chars} characters/{len(roles)} images; '
                    'the indivisible input exceeds the explicit context budget.')
    prompt = pack.prompt_definitions[stage]
    native = OperatorLLMRequest(stage, prompt.version, prompt.model.name,
        render_template(prompt.template, values), response_schema=prompt.response_schema)
    messages = request_messages(native)
    request = {'stage': stage, 'task_id': row['task_id'], 'messages': messages,
               'image_roles': roles, 'input_sha256': digest(messages),
               'prompt_version': prompt.version, 'response_envelope': 'result'}
    if cfg['mode'] == 'offline':
        _, source = materialize_offline(native, prompt_store(run))
        request['native_offline'] = {'request_ref': source.to_dict()}
    ref = run_records(run).put('request/' + stage + '/' + row['task_id'] + '/' + request['input_sha256'], request)
    return {**row, **{'prompt_' + k: v for k, v in values.items()},
            stage + '_binding': {'request_ref': ref.to_dict(), 'input_sha256': request['input_sha256']}}


def _apply(row, stage, required_status, result_status, field, run):
    from curation.t2i.operaters.operators import fail
    if row['status'] != required_status:
        return row
    # Large rendered inputs belong in the frozen request, not every checkpoint.
    row = {k: v for k, v in row.items() if k not in {'prompt_payload', 'prompt_images'}}
    error = row.get(stage + '_error')
    if error:
        status = 'pending_' if error['type'] == 'PromptResponsePending' else 'failed_'
        return fail(row, status + stage, error['detail'])
    result = row[stage + '_result']
    call = row[stage + '_call']
    try:
        if call.get('mode') == 'offline':
            provenance = {**call.get('offline_metadata', {}), 'mode': 'offline',
                          'model': call['model'], 'response_ref': call['response_ref'],
                          'response_sha256': call['response_sha256']}
            if not provenance.get('reviewer') or provenance.get('reviewer_kind') not in {'human', 'assistant', 'independent'}:
                raise ValueError('Offline response must identify its actual author/reviewer')
            if provenance.get('raw_ref') and digest(read_record(provenance['raw_ref'])) != provenance.get('raw_sha256'):
                raise ValueError('Offline raw response changed')
        else:
            provenance = {**call, 'mode': run_manifest(Path(run))['config']['mode'], 'reviewer_kind': 'model'}
        saved = {'binding': row[stage + '_binding'], 'result': result, 'provenance': provenance}
        run_records(run).put('response/' + stage + '/' + row['task_id'] + '/' + digest(saved), saved)
    except (ValueError, OSError, KeyError) as error:
        return fail(row, 'failed_' + stage, str(error))
    return {**row, field: result, stage + '_provenance': provenance, 'status': result_status}


def apply_design(row, *, run):
    return _apply(row, 'design_candidates', 'knowledge_available', 'candidates_designed', 'candidate_design', run)


def apply_review(row, *, run):
    from curation.t2i.operaters.review import accept_review
    return accept_review(_apply(row, 'review_sample', 'valid_sample',
                                'sample_reviewed', 'review_sample', run))
