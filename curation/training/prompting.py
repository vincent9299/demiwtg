"""Business preparation/application for native demiflow prompt operators.

No model client or Dataset scheduling lives here. HTTP and offline transports
receive the same native template, schema and ordered pixels.
"""
from curation.preparation.records import run_manifest
import json
import os
from pathlib import Path

import yaml
from demiflow.operator_llm.client import request_messages
from demiflow.operator_llm.model import OperatorLLMRequest
from demiflow.operator_llm.lance_journal import materialize as materialize_offline
from demiflow.operator_llm.parser import parse_prompt_pack
from demiflow.operator_llm.template import render_template

from curation.preparation.common import validate_local_endpoint
from curation.preparation.materials import model_content, add_image
from curation.preparation.records import digest, run_records, prompt_store, read_record, response_records

PROMPTS = Path(__file__).parent / 'prompts'


def prompt_config(run, config):
    spec = yaml.safe_load((PROMPTS / 'tasks.yaml').read_text())
    model = config['model']
    if config['mode'] == 'local':
        validate_local_endpoint(model['base_url'], model['model'])
        os.environ.setdefault('CURATION_DOWNSTREAM_MODEL_KEY', 'local-no-auth')
        options = {'lance_journal': prompt_store(run, 'calls'), 'timeout_s': model['timeout_s'],
                   'verify_model': True, 'require_finish_reason_stop': True, 'trust_env': False,
                   'request_options': {'temperature': 0, 'max_tokens': model['max_output_tokens'],
                                       'response_format': {'type': 'json_object'},
                                       'chat_template_kwargs': {'enable_thinking': False}}}
        name = model['model']
    else:
        name = config['author']['model']
        options = {'offline_store': prompt_store(run)}
    for prompt in spec['prompts'].values():
        prompt['model'] = {**prompt['model'], 'name': name, 'base_url': model['base_url']}
    text = yaml.safe_dump(spec, allow_unicode=True, sort_keys=False)
    run_records(run).put('prompt_config', {'yaml': text, 'execution_options': options})
    return parse_prompt_pack(text), options


def inputs_for(row, stage):
    plan = row.get('plan', {})
    materials = row['materials']
    if stage == 'design_candidates':
        task = {'concept': row['concept'], 'consumer': row['branch'], 'policy': row['design_policy']}
        if row['design_policy'].get('target_aware'):
            task['target_reference_options'] = row['target_reference_options']
        # Author from final knowledge, not the repeatedly embedded source articles.
        # Full cited passages remain in the checkpoint for independent review.
        materials = [{**m, 'sources': [{k: s[k] for k in ('source_id', 'title', 'url') if k in s}
                                      for s in m.get('sources', [])]} for m in materials]
    elif stage in {'select_edit_source', 'select_edit_source_external'}:
        task = {'task_type': 'edit', 'consumer': row['branch'], 'selected_focus': row['focus']}
    elif stage == 'construct' and row.get('focus'):
        task = {'task_type': plan['task_type'], 'concept': plan['concept'], 'consumer': row['branch'],
                'selected_focus': row['focus']}
    elif stage == 'construct':
        task = {k: plan[k] for k in ('task_type', 'concept', 'intent') if k in plan}
        task['consumer'] = row['branch']
    else:
        task = {'task_type': plan['task_type'], 'candidate': row['draft']}
    if row.get('training_input_binding') and stage in {'construct', 'review_task', 'review_target'}:
        task['input_material_policy'] = 'These are the training references selected with the task; no later retrieval or replacement'
    if stage in {'construct', 'review_task', 'review_target'} and row.get('edit_source'):
        # The source selector's content-bound evidence is part of author/reviewer
        # context. Keep it out of model_content's public answering inputs.
        source = row['edit_source']
        task['edit_source_provenance'] = {k: source[k] for k in
            ('sha256', 'source', 'origin', 'review') if k in source}
    content, roles = model_content(json.dumps(task, ensure_ascii=False), materials,
        row.get('edit_source'), row.get('target') if stage == 'review_target' else None)
    if stage in {'design_candidates', 'construct'}:
        for number, asset in enumerate(row.get('design_targets', []), 1):
            add_image(content, asset, f'候选监督目标 {number}：仅用于设计训练任务，不是知识证据或作答参考。'
                      + json.dumps({'source':asset['source'], 'origin':asset.get('origin'),
                                    'publication':asset.get('candidate_publication')}, ensure_ascii=False))
            roles.append({'role':'target_candidate', 'candidate_number':number,
                          'path':asset.get('path'), 'sha256':asset['sha256']})
    if stage in {'select_edit_source', 'select_edit_source_external'}:
        for number, asset in enumerate(row.get('source_candidates', []), 1):
            add_image(content, asset, f'编辑原图候选{number}（不是知识配图；须据像素和来源核验身份、锚点及非生成来源）：'
                      + json.dumps({'source': asset['source'], 'origin': asset['origin']}, ensure_ascii=False))
            roles.append({'role': 'edit_source_candidate', 'candidate_number': number,
                          'path': asset.get('path'), 'blob_ref': asset.get('blob_ref'), 'sha256': asset['sha256']})
    if stage == 'review_target':
        extra, extra_roles = model_content('下面是本题已绑定的实际作答输入；不得更换参考或据目标改题。\n' + row['draft']['instruction'],
            row.get('answer_materials', []), row.get('edit_source'))
        content.extend(extra)
        roles.extend(extra_roles)
    payload, images = [], []
    for part in content:
        if part['type'] == 'text':
            payload.append(part)
        else:
            images.append(part['image_url']['url'])
            payload.append({'type': 'image', 'number': len(images), 'role': roles[len(images)-1]['role']})
    return {'instructions': (PROMPTS / f"{'select_edit_source' if stage == 'select_edit_source_external' else stage}.md").read_text(), 'payload': payload, 'images': images}, roles


def native_request(row, stage, pack=None):
    if pack is None:
        pack = parse_prompt_pack((PROMPTS / 'tasks.yaml').read_text())
    prompt = pack.prompt_definitions[stage]
    values, roles = inputs_for(row, stage)
    request = OperatorLLMRequest(stage, prompt.version, prompt.model.name,
        render_template(prompt.template, values), response_schema=prompt.response_schema)
    return request, values, roles


def request_for(row, stage, pack=None):
    request, _, roles = native_request(row, stage, pack)
    messages = request_messages(request)
    return {'stage': stage, 'task_id': row['task_id'], 'messages': messages,
            'image_roles': roles, 'input_sha256': digest(messages),
            'prompt_version': request.prompt_version, 'response_envelope': 'result'}


def _prepare(row, stage, required_status, run, pack):
    if row['status'] != required_status:
        return row
    native, values, _ = native_request(row, stage, pack)
    request = request_for(row, stage, pack)
    if run_manifest(Path(run))['config']['mode'] == 'offline':
        _, source = materialize_offline(native, prompt_store(run))
        request['native_offline'] = {'request_ref': source.to_dict()}
    ref = run_records(run).put('request/' + stage + '/' + row['task_id'] + '/' + request['input_sha256'], request)
    return {**row, **{'prompt_' + k: v for k, v in values.items()},
            stage + '_binding': {'request_ref': ref.to_dict(), 'input_sha256': request['input_sha256']}}


def prepare_construct(row, *, run, pack):
    return _prepare(row, 'construct', 'selected', run, pack)


def prepare_design(row, *, run, pack):
    if row['status'] != 'knowledge_available':
        return row
    from curation.training.operators import fail
    cfg = run_manifest(Path(run))['config']
    values, roles = inputs_for(row, 'design_candidates')
    chars = len(json.dumps(values['payload'], ensure_ascii=False)) + len(values['instructions'])
    if chars > cfg['max_context_chars'] or len(roles) > cfg['max_reference_images']:
        return fail(row, 'needs_context_budget',
                    f'Complete published knowledge requires {chars} characters/{len(roles)} images; '
                    'not silently split or truncated. Increase explicit context budget or refine upstream publication.')
    return _prepare(row, 'design_candidates', 'knowledge_available', run, pack)






def prepare_edit_source(row, *, run, pack):
    return _prepare(row, 'select_edit_source', 'edit_candidates_ready', run, pack)


def prepare_external_edit_source(row, *, run, pack):
    return _prepare(row, 'select_edit_source_external', 'external_edit_candidates_ready', run, pack)


def apply_external_edit_source(row, *, run):
    from curation.training.scene_assets import accept_edit_source
    return accept_edit_source(_apply(row, 'select_edit_source_external', 'external_edit_candidates_ready',
        'edit_source_proposed', 'edit_source_selection', run), stage='select_edit_source_external')


def prepare_task_review(row, *, run, pack):
    return _prepare(row, 'review_task', 'valid_task', run, pack)


def prepare_target_review(row, *, run, pack):
    return _prepare(row, 'review_target', 'target_attached', run, pack)


def prompt_responses(run, stage, task_prefix=None):
    return response_records(run, stage, task_prefix)


def _apply(row, stage, required_status, result_status, field, run):
    from curation.training.operators import fail
    if row['status'] != required_status:
        return row
    # Large rendered inputs belong in the frozen request, not every checkpoint.
    row = {k: v for k, v in row.items() if k not in {'prompt_instructions', 'prompt_payload', 'prompt_images'}}
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
            provenance = {**call, 'mode': 'local', 'reviewer_kind': 'model'}
        saved = {'binding': row[stage + '_binding'], 'result': result, 'provenance': provenance}
        run_records(run).put('response/' + stage + '/' + row['task_id'] + '/' + digest(saved), saved)
    except (ValueError, OSError, KeyError) as error:
        return fail(row, 'failed_' + stage, str(error))
    return {**row, field: result, stage + '_provenance': provenance, 'status': result_status}


def apply_construct(row, *, run):
    return _apply(row, 'construct', 'selected', 'constructed', 'draft', run)


def apply_design(row, *, run):
    return _apply(row, 'design_candidates', 'knowledge_available', 'candidates_designed', 'candidate_design', run)






def apply_edit_source(row, *, run):
    from curation.training.scene_assets import accept_edit_source
    return accept_edit_source(_apply(row, 'select_edit_source', 'edit_candidates_ready',
                                    'edit_source_proposed', 'edit_source_selection', run))


def apply_task_review(row, *, run):
    from curation.training.operators import accept_task
    return accept_task(_apply(row, 'review_task', 'valid_task', 'task_reviewed', 'review_task', run))


def apply_target_review(row, *, run):
    from curation.training.operators import accept_target
    return accept_target(_apply(row, 'review_target', 'target_attached', 'target_reviewed', 'review_target', run))
