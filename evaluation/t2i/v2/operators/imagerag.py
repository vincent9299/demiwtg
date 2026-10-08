"""ImageRAG 的配置、行校验和溯源字段；Dataset 图及全部模型调用在正式入口。

检索描述来自原题和初图；图库 caption 只关联命中图片用于查看和追溯。
复用 preparation 的公共向量契约，由本流程冻结图库版本和查询编码配置。
"""
import json
from dataclasses import asdict
from pathlib import Path

import pyarrow as pa
import yaml
from demiflow.embeddings import EmbeddingModel, embedding_execution_config
from demiflow.objects import ObjectRef
from demiflow.operator_llm.parser import parse_prompt_pack
from demiflow.schema import validate_instance


STAGES = ('decision', 'concepts', 'captions')
DIAGNOSES = pa.schema([
    ('task_id', pa.string()), ('instruction', pa.large_string()),
    ('rag_status', pa.string()), ('rag_reason', pa.large_string()),
    ('initial_answer_json', pa.large_string()), ('fallback_prompt', pa.bool_()),
    ('concept_attempts_json', pa.large_string()),
    *[(s + suffix, pa.large_string()) for s in STAGES for suffix in ('_json', '_call_json')],
])
INPUTS = pa.schema([('task_id', pa.string()), ('rag_json', pa.large_string())])
RETRIEVALS = pa.schema([
    ('query_id', pa.string()), ('task_id', pa.string()), ('concept_index', pa.int64()),
    ('concept', pa.string()), ('caption', pa.large_string()),
    ('embedding_call_json', pa.large_string()), ('embedding_error', pa.large_string()),
    ('hits_json', pa.large_string()),
])


def dumps(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def query_schema(encoder):
    return pa.schema([*list(RETRIEVALS)[:-1],
        ('embedding', pa.list_(pa.float32(), encoder.dimensions))],
        metadata={b'image_embeddings.contract': dumps(encoder.contract()).encode()})


def configuration(value, *, max_images):
    """只校验声明并冻结 prompt；不读业务表、不启动服务。"""
    if not isinstance(value, dict):
        raise ValueError('imagerag requires an explicit configuration')
    protocol = value.get('prompt_protocol', 'adapted_json_v1')
    original = protocol == 'upstream_16c9502'
    if protocol not in ('adapted_json_v1', 'upstream_16c9502'):
        raise ValueError('Unknown ImageRAG prompt_protocol: ' + str(protocol))
    allowed = {'initial_answers', 'pool', 'encoder', 'embedding_execution', 'query_service',
               'vlm', 'max_concepts', 'max_context_chars', 'prompt_pack', 'initial_template',
               'caption_sources', 'prompt_protocol', 'additional_initial_answers',
               'max_queries', 'max_initial_bytes', 'original_prompt_spec', 'reuse'}
    if set(value) - allowed:
        raise ValueError('Unknown imagerag options: ' + str(set(value) - allowed))
    cfg = {'max_concepts': 3, 'max_context_chars': 30000, 'query_service': None, **value}
    if cfg.get('reuse'):
        reuse = cfg['reuse']
        if (set(reuse) - {'inputs', 'answers', 'model', 'additional_models', 'caption_recovery'}
                or not {'inputs', 'answers', 'model'} <= set(reuse) or not isinstance(reuse['model'], dict)):
            raise ValueError('ImageRAG reuse requires fixed inputs, answers and frozen generation model')
        if not isinstance(reuse.get('additional_models', []), list) or len(reuse.get('additional_models', [])) > 8:
            raise ValueError('At most eight additional frozen ImageRAG configurations are allowed')
        for old_model in [reuse['model'], *reuse.get('additional_models', [])]:
            if old_model.get('imagerag', {}).get('reuse'):
                raise ValueError('Nested ImageRAG reuse configurations are not allowed')
        for ref in [reuse['inputs'], reuse['answers'], *([reuse['caption_recovery']] if reuse.get('caption_recovery') else [])]:
            if (set(ref) != {'uri', 'version'} or not isinstance(ref['uri'], str)
                    or type(ref['version']) is not int or ref['version'] < 1):
                raise ValueError('ImageRAG reuse sources require fixed uri/version')
        if reuse.get('caption_recovery') and not original:
            raise ValueError('Caption recovery requires the original messages protocol')
    for key in ('initial_answers', 'pool'):
        ref = cfg.get(key, {})
        if (set(ref) != {'uri', 'version'} or not isinstance(ref.get('uri'), str)
                or not ref['uri'] or type(ref.get('version')) is not int or ref['version'] < 1):
            raise ValueError('imagerag ' + key + ' requires a fixed uri/version')
    cfg['additional_initial_answers'] = cfg.get('additional_initial_answers', [])
    if not isinstance(cfg['additional_initial_answers'], list) or len(cfg['additional_initial_answers']) > 8:
        raise ValueError('At most eight additional initial answer sources are allowed')
    cfg['caption_sources'] = cfg.get('caption_sources', [])
    if not isinstance(cfg['caption_sources'], list):
        raise ValueError('imagerag caption_sources must be a list of fixed references')
    for ref in cfg['caption_sources'] + cfg['additional_initial_answers']:
        if (not isinstance(ref, dict) or set(ref) != {'uri', 'version'}
                or not isinstance(ref['uri'], str) or not ref['uri']
                or type(ref['version']) is not int or ref['version'] < 1):
            raise ValueError('imagerag caption_sources requires fixed uri/version references')
    if type(cfg['max_concepts']) is not int or not 1 <= cfg['max_concepts'] <= min(3, max_images):
        raise ValueError('imagerag max_concepts must be in 1..min(3, max_reference_images)')
    if type(cfg['max_context_chars']) is not int or cfg['max_context_chars'] < 1:
        raise ValueError('imagerag max_context_chars must be positive')
    cfg['encoder'] = asdict(EmbeddingModel(**cfg.get('encoder', {})))
    cfg['embedding_execution'] = embedding_execution_config(**cfg.get('embedding_execution', {}))
    # 每次正式运行由入口绑定日志和总请求额度，避免配置指向别的流程的调用日志。
    if cfg['embedding_execution']['options'].get('sqlite_journal'):
        raise ValueError('imagerag embedding journal is owned by this evaluation run')
    cfg['vlm'] = {'mode': 'online', 'concurrency': 4, 'timeout_s': 600,
                  'max_output_tokens': 4096, 'reasoning_effort': None, **cfg.get('vlm', {})}
    vlm = cfg['vlm']
    if set(vlm) - {'mode', 'concurrency', 'timeout_s', 'max_output_tokens', 'reasoning_effort',
                   'model', 'base_url', 'api_key_env'}:
        raise ValueError('Unknown imagerag VLM setting')
    if vlm['mode'] not in ('online', 'offline') or type(vlm['concurrency']) is not int or not 1 <= vlm['concurrency'] <= 8:
        raise ValueError('Invalid imagerag VLM mode/concurrency')
    if vlm['timeout_s'] <= 0 or type(vlm['max_output_tokens']) is not int or vlm['max_output_tokens'] < 1:
        raise ValueError('Invalid imagerag VLM budget')
    if vlm['reasoning_effort'] not in (None, 'low', 'medium', 'high', 'xhigh'):
        raise ValueError('Invalid imagerag VLM reasoning_effort')
    if original:
        if max_images < 3:
            raise ValueError('Original ImageRAG requires capacity for three references')
        if 'max_concepts' in value:
            raise ValueError('Original ImageRAG has no concept cap; use operational max_queries')
        cfg['max_queries'] = cfg.get('max_queries', 32)
        cfg['max_initial_bytes'] = cfg.get('max_initial_bytes', 8 * 1024 * 1024)
        if type(cfg['max_queries']) is not int or not 1 <= cfg['max_queries'] <= 64:
            raise ValueError('max_queries must be in 1..64; exceeding it fails without truncation')
        if type(cfg['max_initial_bytes']) is not int or not 1 <= cfg['max_initial_bytes'] <= 8 * 1024 * 1024:
            raise ValueError('max_initial_bytes must be in 1..8MiB')
        path = Path(__file__).parents[1] / 'prompts/imagerag_original.yaml'
        canonical = yaml.safe_load(path.read_text())
        original_spec = cfg.get('original_prompt_spec') or canonical
        if original_spec != canonical:
            raise ValueError('Original prompt spec differs from the pinned upstream asset')
        cfg['original_prompt_spec'] = json.loads(dumps(original_spec))
        spec = json.loads(dumps(cfg.get('prompt_pack') or original_spec['call_config']))
    else:
        cfg['max_queries'] = cfg['max_concepts']
        path = Path(__file__).parents[1] / 'prompts/imagerag.yaml'
        spec = json.loads(dumps(cfg.get('prompt_pack') or yaml.safe_load(path.read_text())))
    for prompt in spec['prompts'].values():
        prompt['model'].update({{'model': 'name'}.get(k, k): vlm[k]
                                for k in ('model', 'base_url', 'api_key_env') if k in vlm})
    pack = parse_prompt_pack(yaml.safe_dump(spec, allow_unicode=True, sort_keys=False))
    if set(pack.prompt_definitions) != set(STAGES):
        raise ValueError('imagerag requires decision/concepts/captions prompts')
    if original and any(p.input_mode != 'messages' or p.response_format != 'text'
                        for p in pack.prompt_definitions.values()):
        raise ValueError('Original protocol requires messages input and plain text output')
    if original:
        cfg.pop('max_concepts', None)
    cfg['prompt_pack'] = spec
    if cfg.get('query_service'):
        from demiflow.services import vllm_config
        service = vllm_config(cfg['query_service'])
        if service['runner'] != 'pooling':
            raise ValueError('imagerag query service must use runner=pooling')
        cfg['query_service'] = service
    from .answers import answer_template
    cfg.setdefault('initial_template', answer_template({'answer_mode': 'text_only'}))
    return cfg


def initial_model(model):
    """以同一模型、seed、参数和冻结的无图模板核对初图身份。"""
    return {**model, 'answer_mode': 'text_only', 'use_references': False,
            'prompt_template': model['imagerag']['initial_template']}


def validate_reuse_model(model, *, root):
    """Explicit reuse permits historical transport, while freezing generation and RAG semantics."""
    from .answers import historical_answer_identity, answer_template
    sample = {'task_id': 'identity-check', 'instruction': 'identity-check'}
    def semantic(cfg):
        cfg = json.loads(dumps(cfg))
        for key in ('reuse', 'query_service', 'embedding_execution'):
            cfg.pop(key, None)
        for key in ('concurrency', 'timeout_s'):
            cfg['vlm'].pop(key, None)
        for ref in [cfg['initial_answers'], cfg['pool'], *cfg['additional_initial_answers'], *cfg['caption_sources']]:
            ref['uri'] = str((root / ref['uri']).resolve())
        return cfg
    reuse = model['imagerag']['reuse']
    for previous in [reuse['model'], *reuse.get('additional_models', [])]:
        if (historical_answer_identity(model, sample) != historical_answer_identity(previous, sample)
                or answer_template(model) != answer_template(previous)):
            raise ValueError('ImageRAG reuse generation model, parameters or template differs')
        if semantic(model['imagerag']) != semantic(previous['imagerag']):
            raise ValueError('ImageRAG reuse diagnostic/retrieval configuration differs')


def restore_input(row, *, model):
    """Validate frozen reusable input against the current question and its original request."""
    from .answers import answer_identity
    prior = row.get('reused_rag_input')
    if not prior:
        if row.get('rag_previous_answer'):
            raise ValueError('Reusable ImageRAG answer has no frozen retrieval input')
        return row
    trace = json.loads(prior['rag_json'])
    initial = trace.get('initial_answer') or {}
    if (trace.get('status') not in ('keep_initial', 'retrieved')
            or initial.get('task_id') != row['task_id']
            or initial.get('instruction') != row['instruction']
            or initial.get('request_id') != answer_identity(initial_model(model), row)):
        raise ValueError('Reusable ImageRAG input differs from current question/initial answer')
    restored = {**row, 'rag_json': prior['rag_json']}
    previous = row.get('rag_previous_answer')
    if previous:
        old_model = model['imagerag']['reuse']['model']
        declared = [old_model, model, *model['imagerag']['reuse'].get('additional_models', [])]
        if (previous['request_id'] not in {answer_identity(m, restored) for m in declared}
                or previous['instruction'] != row['instruction']
                or previous['answer_model'] != model['model'] + '+ImageRAG'
                or previous['answer_mode'] != 'imagerag' or previous['status'] != 'generated'):
            raise ValueError('Reusable ImageRAG answer differs from frozen generation request')
        ObjectRef(**json.loads(previous['image_json'])).read(max_bytes=32 * 1024 * 1024)
    return restored


def one_initial(acc, row):
    if acc is not None:
        if acc.get('initial_priority', 0) == row.get('initial_priority', 0):
            raise ValueError('Initial answer source has multiple answers for task_id: ' + row['task_id'])
        return max((acc, row), key=lambda r: r.get('initial_priority', 0))
    return row


def recover_caption(row):
    """Resume an explicitly selected caption HTTP 408 without repeating successful diagnosis calls."""
    previous = row.get('caption_recovery')
    if not previous:
        return row
    if row['rag_status'] != 'ready':
        raise ValueError('Caption recovery requires a valid current initial answer')
    initial = json.loads(row['initial_answer_json'])
    old_initial = json.loads(previous.get('initial_answer_json') or '{}')
    if (previous['task_id'] != row['task_id'] or previous['instruction'] != row['instruction']
            or any(initial.get(k) != old_initial.get(k) for k in
                   ('request_id', 'image_json', 'instruction', 'answer_model', 'answer_mode'))):
        raise ValueError('Caption recovery initial answer or question differs')
    failed = json.loads(previous.get('captions_call_json') or '{}')
    if (previous['rag_status'] != 'diagnosis_failed'
            or not previous['rag_reason'].startswith('captions: HTTP 408')
            or failed.get('http_status') != 408 or not failed.get('response_ref')
            or previous['fallback_prompt']):
        raise ValueError('Caption recovery only accepts saved caption HTTP 408 responses')
    for step in ('decision', 'concepts'):
        call = json.loads(previous.get(step + '_call_json') or '{}')
        result = json.loads(previous.get(step + '_json') or '{}')
        if call.get('http_status') != 200 or not isinstance(result.get('raw_text'), str):
            raise ValueError('Caption recovery requires successful prior decision and concepts')
    return {**row, **{k: previous.get(k) for k in DIAGNOSES.names},
            'rag_status': 'needs_captions', 'rag_reason': '',
            'captions_json': None, 'captions_call_json': None}


def prepare_initial(row, *, model, root):
    """每题左关联一张固定无图答案；缺失、失败和身份不符保留在本轮分母。"""
    from .answers import answer_identity
    from benchmark.t2i.v2.operators.images import positive_image_data_url
    result = {name: None for name in DIAGNOSES.names}
    result.update(task_id=row['task_id'], instruction=row['instruction'], rag_status='ready',
                  rag_reason='', fallback_prompt=False, rag_images=[])
    initial = row.get('initial_answer')
    if initial:
        result['initial_answer_json'] = dumps(initial)
    try:
        if not initial or initial.get('status') != 'generated' or not initial.get('image_json'):
            raise ValueError('Missing successful initial text-only answer')
        if (initial.get('request_id') != answer_identity(initial_model(model), row)
                or initial.get('answer_mode') != 'text_only'
                or initial.get('answer_model') != model['model']
                or initial.get('instruction') != row['instruction']
                or initial.get('reference_image_count') != 0):
            raise ValueError('Initial answer does not match the same model/text-only request')
        if len(row['instruction']) > model['imagerag']['max_context_chars']:
            raise ValueError('Initial instruction exceeds imagerag context budget')
        # 使用 benchmark 维护的有字节/像素上限的单图编码器；调用中记录实际缩放像素。
        if model['imagerag'].get('prompt_protocol') == 'upstream_16c9502':
            from .imagerag_original import original_image
            url = original_image(json.loads(initial['image_json']), model['imagerag']['max_initial_bytes'])
        else:
            url = positive_image_data_url(json.loads(initial['image_json']))
        result['rag_images'] = [url]
    except (ValueError, TypeError, KeyError, OSError) as error:
        result.update(rag_status='initial_failed', rag_reason=str(error))
    return result


def prompt_payload(row, *, cfg, stage):
    """仅投影本阶段所需上下文；不会传入考点、图库概念标签或判分。"""
    payload = {'instruction': row['instruction']}
    if stage != 'decision':
        payload['decision'] = json.loads(row.get('decision_json') or 'null')
        payload['max_concepts'] = cfg['max_concepts']
    if stage == 'captions':
        payload['concepts'] = json.loads(row.get('concepts_json') or 'null')
    size = len(dumps(payload)) + len(cfg['prompt_pack']['prompts'][stage]['template'])
    if row['rag_status'] in ('ready', 'needs_concepts', 'needs_captions') and size > cfg['max_context_chars']:
        return {**row, 'rag_status': 'diagnosis_failed', 'rag_reason': stage + ' exceeds complete context budget',
                'rag_payload': payload}
    return {**row, 'rag_payload': payload}


def finish_stage(row, *, cfg, stage):
    expected = {'decision': 'ready', 'concepts': 'needs_concepts', 'captions': 'needs_captions'}[stage]
    if row['rag_status'] != expected:
        return row
    result, error = row.get('rag_result'), row.get('rag_error')
    call = row.get('rag_call') or (error or {}).get('call')
    row = {**row, stage + '_call_json': dumps(call) if call else None,
           stage + '_json': dumps(result) if result is not None else None}
    if error:
        return {**row, 'rag_status': 'diagnosis_pending' if error.get('type') == 'PromptResponsePending' else 'diagnosis_failed',
                'rag_reason': stage + ': ' + error['detail']}
    try:
        validate_instance({'result': result}, cfg['prompt_pack']['prompts'][stage]['response_schema'])
        if stage == 'decision':
            status = 'keep_initial' if result['match'] == 'yes' else 'needs_concepts'
        elif stage == 'concepts':
            concepts = result['concepts']
            if len(concepts) > cfg['max_concepts'] or any(not c.strip() for c in concepts):
                raise ValueError('Concept list exceeds budget or contains blank values')
            if not concepts:
                # 原版识别不出概念时，以原题为检索描述；与技术失败明确区分。
                row.update(fallback_prompt=True, captions_json=dumps({'captions': [
                    {'concept_index': 1, 'caption': row['instruction']}]}))
            status = 'needs_captions' if concepts else 'needs_retrieval'
        else:
            captions = result['captions']
            count = len(json.loads(row['concepts_json'])['concepts'])
            if ([c['concept_index'] for c in captions] != list(range(1, count + 1))
                    or any(not c['caption'].strip() for c in captions)):
                raise ValueError('Captions must match each missing concept exactly once in order')
            status = 'needs_retrieval'
        return {**row, 'rag_status': status}
    except (ValueError, TypeError, KeyError) as error:
        return {**row, 'rag_status': 'diagnosis_failed', 'rag_reason': stage + ': ' + str(error)}


def queries(row):
    """仅成功诊断展开为已校验的有限文本查询；不把初图编码为查询，也不读图库caption。"""
    if row['rag_status'] != 'needs_retrieval':
        return []
    concepts = json.loads(row.get('concepts_json') or '{"concepts": []}')['concepts']
    return [{'query_id': row['task_id'] + ':' + str(c['concept_index']), 'task_id': row['task_id'],
             'concept_index': c['concept_index'],
             'concept': concepts[c['concept_index'] - 1] if c['concept_index'] <= len(concepts) else '', 'caption': c['caption']}
            for c in json.loads(row['captions_json'])['captions']]


def group_hits(acc, row, *, maximum=3):
    """按题汇总有限查询；排序恢复caption原序，同一图片重复命中保持对应关系。"""
    items = (acc['retrievals'] if acc else []) + [row]
    if len(items) > maximum:
        raise ValueError('Retrieval queries exceed the declared task budget')
    return {'task_id': row['task_id'], 'retrievals': items}


def caption_record(row, *, source):
    """固定caption结果按SHA关联；保留失败，缺失不缩小向量池。"""
    available = row['status'] == 'ok'
    if available and (not isinstance(row['caption'], str) or not row['caption'].strip()):
        raise ValueError('Generated image caption must contain nonblank text')
    return {'sha256': row['sha256'], 'image_caption': row['caption'] if available else None,
            'image_caption_status': 'available' if available else row['status'],
            'image_caption_error': row['error'], 'image_caption_source': source}


def latest_caption(acc, row):
    """按配置来源顺序选成功结果；后续失败不覆盖已成功的caption。"""
    return acc if acc and acc['image_caption_status'] == 'available' and row['image_caption_status'] != 'available' else row


def hit_row(row):
    hits = row['hits']
    if len(hits) > 1:
        raise ValueError('ImageRAG requires exactly top1 per query')
    return {**row, 'sha256': hits[0]['sha256'] if hits else None}


def captioned_hits(row):
    details = {key: row.get(key) for key in
               ('image_caption', 'image_caption_status', 'image_caption_error', 'image_caption_source')}
    details['image_caption_status'] = details['image_caption_status'] or 'missing'
    return {**row, 'hits': [{**hit, **details} for hit in row['hits']]}


def assemble(row, *, cfg, stage_refs):
    initial = json.loads(row.get('initial_answer_json') or 'null')
    trace = {'status': row['rag_status'], 'reason': row['rag_reason'],
             'initial_answers': cfg['initial_answers'], 'pool': cfg['pool'],
             'caption_sources': cfg.get('caption_sources', []),
             'additional_initial_answers': cfg.get('additional_initial_answers', []),
             'prompt_protocol': cfg.get('prompt_protocol', 'adapted_json_v1'),
             'concept_attempts': json.loads(row.get('concept_attempts_json') or '[]'),
             'encoder_id': EmbeddingModel(**cfg['encoder']).fingerprint,
             'initial_answer': initial, 'fallback_prompt': row['fallback_prompt'],
             'stages': {s: {'result': json.loads(row.get(s + '_json') or 'null'),
                             'call': json.loads(row.get(s + '_call_json') or 'null')} for s in STAGES},
             'stage_refs': stage_refs, 'references': [], 'retrievals': []}
    if row['rag_status'] == 'needs_retrieval':
        found = sorted(row.get('retrievals') or [], key=lambda r: r['concept_index'])
        expected = json.loads(row['captions_json'])['captions']
        try:
            if not expected:
                raise ValueError('No retrieval captions')
            if [r['concept_index'] for r in found] != [c['concept_index'] for c in expected]:
                raise ValueError('Missing retrieval results for one or more captions')
            for item in found:
                hits = json.loads(item['hits_json'])
                trace['retrievals'].append({**item, 'hits': hits})
                if item.get('embedding_error'):
                    raise ValueError('Caption embedding failed: ' + item['embedding_error'])
                if len(hits) != 1:
                    raise ValueError('No retrieved image for caption ' + str(item['concept_index']))
                hit = hits[0]
                if hit['encoder_id'] != trace['encoder_id']:
                    raise ValueError('Retrieved image encoder differs from query encoder')
                trace['references'].append({'number': item['concept_index'], 'caption': item['caption'],
                    'concept': item['concept'], 'distance': hit['_distance'],
                    **{key: hit.get(key) for key in ('image_caption', 'image_caption_status',
                                                    'image_caption_error', 'image_caption_source')},
                    'object_ref': ObjectRef(uri=hit['image_uri'], sha256=hit['sha256']).to_dict()})
            if cfg.get('prompt_protocol') == 'upstream_16c9502':
                trace['references'] = trace['references'][:3]
            trace['status'] = 'retrieved'
        except (ValueError, TypeError, KeyError) as error:
            trace.update(status='retrieval_failed', reason=str(error), references=[])
    return {'task_id': row['task_id'], 'rag_json': dumps(trace)}


def semantic_input(row):
    """答案缓存绑定实际初图、诊断结论、caption与命中顺序；不包含重放计时等可变字段。"""
    trace = json.loads(row.get('rag_json') or '{}')
    return {k: trace.get(k) for k in ('status', 'pool', 'encoder_id', 'references')} | {
        'initial_image': (trace.get('initial_answer') or {}).get('image_json'),
        'captions': trace.get('stages', {}).get('captions', {}).get('result')}
