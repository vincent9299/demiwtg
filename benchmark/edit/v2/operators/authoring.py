"""单行材料与响应契约：绑定初始 ObjectRef 或本会话实际附图回执。"""
import json
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlsplit
from PIL import UnidentifiedImageError

from demiflow.execution.artifacts import resolve_local_artifact
from demiflow.objects import ObjectRef
from demiflow.schema import SchemaValidationError, validate_instance
from benchmark.edit.v2.operators.images import image_data_url, image_bytes


def prepare_request(row, *, max_context_chars, prompt_chars, generate_source, max_generation_attempts,
                    api_configuration=None):
    refs = json.loads(row['references_json'])
    images = [ref for ref in refs if ref['kind'] == 'image']
    if any(ref['role'] != 'concept_reference' for ref in images):
        raise ValueError('Initial Edit images must be concept positives; retrieve scene candidates through tools')
    documents = {}
    for item in refs:
        if item['kind'] != 'document' or not item['document'].get('eligible', True):
            continue
        document = item['document']
        ref = ObjectRef(**document['document_ref'])
        uri = urlsplit(ref.uri)
        if uri.scheme != 'file':
            raise ValueError('Edit documents require local file snapshots for Codex to read')
        path = resolve_local_artifact(Path(unquote(uri.path)))
        ref.verify()  # 分块核验固定快照；正文不放进初始模型上下文。
        documents[item['document_id']] = {
            'local_path': str(path), 'sha256': ref.sha256, 'url': document['url'],
            **{key: document[key] for key in ('title', 'evidence') if key in document},
        }
    concept = {'concept': row['concept'], 'taxonomy': row['taxonomy']}
    for item in refs:
        if item['kind'] == 'concept':
            concept.update({key: item[key] for key in ('concept', 'definition', 'original_name')})
    materials = {
        'concept_material': concept,
        'evidence_materials': {
            'excerpts': [{'id': f'A{i}', 'title': ref['title'], 'text': ref['text']}
                         for i, ref in enumerate((r for r in refs if r['kind'] == 'text'), 1)],
            'documents': documents,
        },
        'positive_examples': [{'image_number': i} for i in range(1, len(images) + 1)],
        'execution_context': {
            'generate_source': generate_source, 'max_generation_attempts': max_generation_attempts,
        },
    }
    result = {**row, **materials, 'prompt_images': [],
              'api_configuration': api_configuration or []}
    if row['status'] == 'ready':
        if prompt_chars + len(json.dumps([materials, api_configuration or []], ensure_ascii=False)) > max_context_chars:
            result.update(status='needs_context_budget', reason='Complete prompt exceeds max_context_chars; no truncation')
        else:
            result['prompt_images'] = [image_data_url(ref['object_ref']) for ref in images]
    return result


def check_response(row, *, question_schema, generate_source):
    """结构/字节绑定检查。作者自检仍非独立审题，候选保持 unreviewed。"""
    error = row.get('design_error') or {}
    call = dict(row.get('design_call') or error.get('call', {}))
    reasoning = call.pop('reasoning', None)
    if 'attempts' in call:
        call['attempts'] = [{k: v for k, v in item.items() if k != 'reasoning'} for item in call['attempts']]
    output = {**row, 'question': None, 'seed_asset': None, 'edit_source': None,
              'reasoning': reasoning, 'call_json': json.dumps(call, ensure_ascii=False)}
    if row['status'] != 'ready':
        return output
    if error:
        return {**output, 'status': 'pending' if error['type'] == 'PromptResponsePending' else 'failed',
                'reason': error['detail']}
    result = row['design_result']
    question = result['question']
    if question is None:
        reason = result.get('reason', '').strip()
        return {**output, 'status': 'insufficient' if reason else 'invalid_response',
                'reason': reason or 'Null question requires a reason'}
    try:
        validate_instance(question, {**question_schema, 'type': 'object'}, label='question')
        if result.get('reason', '').strip():
            raise ValueError('Question and insufficient reason are mutually exclusive')
        for key in ('source_image_edit', 'source_artifact'):
            if question[key] is not None and (not isinstance(question[key], str) or not question[key].strip()):
                raise ValueError(key + ' must be null or a nonempty string')
        texts = [question['instruction'], *question['source_image_checks'],
                 *(v for point in question['test_points'] for v in point.values())]
        if any(not text.strip() for text in texts):
            raise ValueError('Whitespace-only question fields')
        images = [r for r in json.loads(row['references_json']) if r['kind'] == 'image']
        image_id = question.get('seed_image_id')
        if image_id is not None:
            if question['seed_image'] is not None or call.get('transport') != 'codex_app_server':
                raise ValueError('Retrieved seed requires only seed_image_id from a native Codex callback')
            receipts = [item for observation in call.get('environment', {}).get('observations', [])
                        for item in observation.get('images', [])
                        if item.get('status') == 'attached' and item.get('image_id') == image_id]
            if not receipts:
                raise ValueError('seed_image_id must select an actually attached tool image')
            seed = receipts[0]['object_ref']
            if image_id != 'sha256:' + seed['sha256'] or any(item['object_ref']['sha256'] != seed['sha256'] for item in receipts):
                raise ValueError('Ambiguous or invalid tool image identity')
            try:
                image_bytes(seed)  # 回执中确实附图，交付前再次核对实际字节。
            except (UnidentifiedImageError, OSError) as exc:
                raise ValueError('Retrieved seed is not a decodable image: ' + str(exc)) from exc
        else:
            if type(question['seed_image']) is not int or not 1 <= question['seed_image'] <= len(images):
                raise ValueError('seed_image must select an actually supplied image')
            seed = images[question['seed_image'] - 1]['object_ref']
        output.update(question=question, seed_asset=seed)
        audit = question['source_image_check']
        checks = audit['observations']
        if any(not obs['evidence'].strip() for obs in checks):
            raise ValueError('Each check requires a visible observation')
        if audit['status'] in {'passed', 'failed'}:
            if sorted(obs['check'] for obs in checks) != list(range(1, len(question['source_image_checks']) + 1)):
                raise ValueError('Check observations must cover each source_image_checks item exactly once')
        elif checks:
            raise ValueError('not_run cannot claim observations')
        if audit['status'] == 'passed' and (not checks or not all(obs['passed'] for obs in checks)):
            raise ValueError('Passed source requires all observations to pass')
        if audit['status'] == 'failed' and (all(obs['passed'] for obs in checks) or not audit['reason'].strip()):
            raise ValueError('Failed source requires a failed observation and a reason')
        if audit['status'] == 'not_run' and not audit['reason'].strip():
            raise ValueError('not_run requires a reason')
        if question['source_image_edit'] is None:
            if question['source_artifact'] is not None:
                raise ValueError('Direct source must bind the supplied seed, not an artifact')
            output['edit_source'] = seed
        elif question['source_artifact'] is not None:
            if not generate_source:
                raise ValueError('Generation disabled; unexpected source artifact')
            name = question['source_artifact']
            if PurePosixPath(name).name != name or name in {'.', '..'} or '\\' in name:
                raise ValueError('source_artifact must be a single exported filename')
            # 仅接收本次原生 Codex 调用的实际持久化产物，不读取模型返回的任意路径。
            if call.get('transport') not in {'codex_exec', 'codex_app_server'} or call.get('image_generation') is not True:
                raise ValueError('Generated source requires the enabled native Codex transport')
            artifacts = call.get('artifacts', [])
            matches = [a for a in artifacts if a['name'] == name]
            if len(matches) != 1:
                raise ValueError('source_artifact is not a persisted artifact of this Codex call')
            ref = matches[0]['object_ref']
            try:
                image_bytes(ref)  # 校验实际可解码图片及 SHA；不把路径存在当作图片验收。
            except (UnidentifiedImageError, OSError) as exc:
                raise ValueError('Generated artifact is not a decodable image: ' + str(exc)) from exc
            if ref['sha256'] == seed['sha256']:
                raise ValueError('Constructed source is byte-identical to seed')
            output['edit_source'] = ref
        if audit['status'] in {'passed', 'failed'} and output['edit_source'] is None:
            raise ValueError('Completed image check requires an actual source image')
        if audit['status'] == 'passed':
            output.update(status='candidate', reason='')
        elif question['source_image_edit'] is not None and not generate_source and audit['status'] == 'not_run':
            output.update(status='needs_source_synthesis', reason=audit['reason'])
        else:
            output.update(status='source_check_failed', reason=audit['reason'])
    except (SchemaValidationError, ValueError, TypeError, KeyError) as exc:
        output.update(status='invalid_response', reason=str(exc), question=None, edit_source=None)
    return output
