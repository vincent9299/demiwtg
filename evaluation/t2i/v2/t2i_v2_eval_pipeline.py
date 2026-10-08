"""两步评测：答题写入目标表；独立读取答案表打分，写入指定目标表。"""
from demiflow.operator_llm.call_ref import journal_options as sqlite_call_options

from functools import partial

from evaluation.t2i.v2.operators.transforms import (
    judge_response_fields,
    validate_scores,
    score_values,
    mean_scores,
)

import argparse
import json
import os
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import lance
import pyarrow as pa
import yaml
import demiflow
from demiflow.execution.artifacts import digest, run_lock
from demiflow.objects import LocalObjectStore
from evaluation.t2i.v2.operators.run_tables import RunTables
from demiflow.operator_llm.parser import parse_prompt_pack
from demiflow.execution.dataset_commit import DatasetCommit
from demiflow import data
from demiflow.services import ManagedHTTPService
from project import resolve_root
from evaluation.t2i.v2.operators.answers import PrepareAnswer, FinishAnswer, answer_template, answer_bindings, answer_service, ANSWER_MODES, answer_mode, answer_identity
from evaluation.t2i.v2.operators.images import image_data_url
from evaluation.t2i.v2.operators.paired_scores import PAIRED_FIELDS, prepare_judging, a_score, b_score, FailureLimit, ValidateQuestions
from evaluation.t2i.v2.operators import imagerag
from evaluation.t2i.v2.operators import d_evaluation
from evaluation.t2i.v2.operators.notebook import DStreamProgress

DATASETS = Path('demiwtg/evaluation/t2i/v2/datasets')
PROMPTS = Path(__file__).parent / 'prompts'
# 每题×模型×作答方式一行，图片保存独立对象；scores 保留答案及参考图溯源。
ANSWERS = pa.schema(
    [
        (name, pa.string())
        for name in ('task_id', 'concept', 'instruction', 'answer_model', 'answer_mode',
                     'reference_images_json', 'status', 'reason', 'image_json', 'answer_call_json')
    ]
    + [('reference_image_count', pa.int64()), ('generation_seconds', pa.float64())]
)
SCORES = pa.schema(
    [
        *ANSWERS,
        ('judge_model', pa.string()),
        ('judge_json', pa.large_string()),
        ('judge_call_json', pa.large_string()),
        ('alignment_score', pa.float64()),
        ('quality_score', pa.float64()),
        ('aesthetics_score', pa.float64()),
    ]
)
PAIRED_SCORES = pa.schema([*ANSWERS, *PAIRED_FIELDS])
D_SCORES = pa.schema([*ANSWERS, *d_evaluation.D_FIELDS])


def config(*, answer_models=None, judge_model=None, stream_judging=False, arm_concurrency=1,
           max_questions=300, max_consecutive_failures=3, d_evaluation=None, parallel_answers=False,
           codex_comparison=None):
    """分别配置答题或 judge；answer_mode 区分原题作答和仅给正例图作答。"""
    if codex_comparison is not None:
        from .operators.codex_judging import configuration
        if answer_models is not None or judge_model is not None or d_evaluation is not None:
            raise ValueError('Codex comparison uses the frozen base judge configuration')
        return {'codex_comparison': configuration(codex_comparison)}
    result = {}
    if answer_models is not None:
        answers = [
            {
                'parameters': {},
                'seed': 42,
                'device': 'cuda:0',
                'api': 'images',
                'use_references': False,
                'max_reference_images': 5,
                'concurrency': 1,
                'queue_depth': 4,
                'enabled': True,
                'base_url': 'http://127.0.0.1:4001/v1',
                'api_key_env': 'MODELHUB_API_KEY',
                'timeout_s': 600,
                **model,
            }
            for model in answer_models
        ]
        for answer, original in zip(answers, answer_models):
            if type(answer['concurrency']) is not int or not 1 <= answer['concurrency'] <= 8:
                raise ValueError('Answer concurrency must be in 1..8')
            if type(answer['queue_depth']) is not int or not 1 <= answer['queue_depth'] <= 8:
                raise ValueError('Answer queue_depth must be in 1..8')
            if answer['backend'] not in {'modelhub', 'codex_exec'} and answer['concurrency'] != 1:
                raise ValueError('Local Diffusers requires one actor per GPU; do not share mutable pipeline state')
            if type(answer['enabled']) is not bool:
                raise ValueError('Answer enabled must be boolean')
            if answer.get('reuse_answers_from') and (type(answer['reuse_answers_from'].get('version')) is not int
                                                      or answer['reuse_answers_from']['version'] < 1):
                raise ValueError('Reused answers require an explicit fixed version')
            mode = answer_mode(answer)
            if mode not in ANSWER_MODES:
                raise ValueError('answer_mode must be text_only, positive_images, legacy_references or imagerag')
            if ('answer_mode' in original and 'use_references' in original
                    and original['use_references'] != (mode != 'text_only')):
                raise ValueError('answer_mode conflicts with use_references')
            if type(answer['use_references']) is not bool:
                raise ValueError('use_references must be true or false')
            answer.update(answer_mode=mode, use_references=mode != 'text_only')
            if answer.get('reference_preprocessing') is not None and (
                    mode != 'imagerag' or answer['reference_preprocessing'] != 'oversized_to_3840_jpeg90_v1'):
                raise ValueError('reference_preprocessing requires the explicit ImageRAG oversized-image policy')
            # 编码、格式校验和传输由平台负责；业务仅声明本路策略。
            answer.setdefault('image_encoding', 'preserve' if mode == 'imagerag' else 'png')
            if answer['image_encoding'] not in ('png', 'preserve'):
                raise ValueError('image_encoding must be png or preserve')
            limits = answer.get('image_limits')
            if limits is not None:
                if (not isinstance(limits, dict) or set(limits) - {'max_pixels', 'max_total_pixels'}
                        or any(type(v) is not int or not 1 <= v <= 128_000_000 for v in limits.values())):
                    raise ValueError('image_limits requires explicit pixel budgets in 1..128000000')
                if limits.get('max_pixels', 24_000_000) > limits.get('max_total_pixels', 48_000_000):
                    raise ValueError('Image aggregate pixel budget must cover a single image')
                if answer['backend'] == 'codex_exec':
                    raise ValueError('image_limits requires the native image generation node')
            if answer['backend'] == 'codex_exec' and answer['image_encoding'] != 'png':
                raise ValueError('image_encoding=preserve requires the native image node')
            answer['prompt_template'] = answer_template(answer)
            if type(answer['max_reference_images']) is not int or not 1 <= answer['max_reference_images'] <= 8:
                raise ValueError('max_reference_images must be an integer in 1..8; excess images fail the row')
            if mode == 'imagerag':
                if answer.get('reuse_answers_from'):
                    raise ValueError('ImageRAG uses imagerag.initial_answers; final answers have a distinct request identity')
                answer['imagerag'] = imagerag.configuration(answer.get('imagerag'), max_images=answer['max_reference_images'])
            elif answer.get('imagerag'):
                raise ValueError('imagerag configuration requires answer_mode=imagerag')
            if any(key in answer['parameters'] for key in ('prompt', 'image', 'input_references', 'generator', 'messages', 'model', 'n')):
                raise ValueError('Model, prompt, images and one output are supplied by the answer configuration, not parameters')
        names = [(answer.get('model'), answer['answer_mode']) for answer in answers]
        if not names or any(not name for name, _ in names) or len(set(names)) != len(names):
            raise ValueError('Specify unique model/answer_mode combinations')
        for answer in answers:
            if type(answer['use_references']) is not bool:
                raise ValueError('use_references must be true or false')
            if answer['answer_mode'] == 'legacy_references' and answer['backend'] == 'modelhub':
                raise ValueError('Legacy mixed references are supported by local answer backends only; use positive_images')
            if answer.get('backend') not in {'diffusers', 'modelhub', 'codex_exec'}:
                raise ValueError('answer backend must be diffusers or modelhub; BAGEL requires a native platform backend')
            if answer['backend'] == 'diffusers' and not answer.get('model_path'):
                raise ValueError('Local generation requires model_path')
            if answer['api'] not in {'images', 'images_json', 'chat', 'openrouter_images'} or answer['timeout_s'] <= 0:
                raise ValueError('Invalid API or timeout')
            if answer['api'] == 'openrouter_images' and answer['parameters'].get('stream'):
                raise ValueError('OpenRouter image answers require a single non-streaming response')
            if answer_service(answer) is not None:
                service = ManagedHTTPService(**(answer.get('service') or answer['shared_service']['configuration']))
                if answer['backend'] != 'modelhub' or service.base_url != answer['base_url'].rstrip('/'):
                    raise ValueError('Managed answer service must match the modelhub endpoint')
                if service.expected_model != answer.get('provider_model', answer['model']):
                    raise ValueError('Managed answer service must verify the declared model')
                if not answer.get('revision'):
                    raise ValueError('Managed answer service requires an explicit deployment revision')
        for answer in answers:
            if answer['backend'] == 'codex_exec':
                if answer['answer_mode'] not in {'text_only', 'positive_images'}:
                    raise ValueError('Codex answers support only text_only and positive_images')
                if not parallel_answers or answer.get('service') or answer.get('shared_service'):
                    raise ValueError('Codex generation requires parallel_answers and no image HTTP service')
                spec = answer.get('codex_prompt_pack') or yaml.safe_load((PROMPTS / 'answer_codex.yaml').read_text())
                parse_prompt_pack(yaml.safe_dump(spec, allow_unicode=True))
                answer['codex_prompt_pack'] = spec
                answer['base_url'] = spec['prompts']['answer_codex']['model']['base_url']
                opts = answer.get('codex_exec', {})
                if opts.get('image_generation') is not True or not opts.get('artifact_store'):
                    raise ValueError('Codex answers require image_generation and durable artifact_store')
                if opts.get('max_artifact_files') != 1 or not 1 <= opts.get('max_artifact_bytes', 0) <= 32*1024*1024:
                    raise ValueError('Codex must export one image within 32MiB')
                if not 1 <= answer.get('max_context_chars', 0) <= 60000:
                    raise ValueError('Codex answer context must be within 60000 characters')
        shared = {}
        for answer in answers:
            if spec := answer.get('shared_service'):
                key = (spec['root'], spec['name'])
                if key in shared and shared[key] != spec:
                    raise ValueError('Shared answer service declarations must agree across arms')
                shared[key] = spec
        result['answers'] = answers
    if judge_model is not None:
        judge = {
            'mode': 'online',
            'base_url': 'http://127.0.0.1:4001/v1',
            'api_key_env': 'MODELHUB_API_KEY',
            'timeout_s': 600,
            'max_output_tokens': 8192,
            'concurrency': 1,
            'max_context_chars': 60000,
            'stream': True,
            'stream_include_usage': True,
            **judge_model,
        }
        if not judge.get('model') or judge['mode'] not in {'online', 'offline', 'codex_exec'}:
            raise ValueError('Specify judge model and online/offline/codex_exec mode')
        if judge['mode'] == 'codex_exec':
            if judge['model'] != 'codex/gpt-6-astra' or judge.get('reasoning_effort') != 'xhigh':
                raise ValueError('Codex D7 CLI requires codex/gpt-6-astra + xhigh')
            opts = judge.get('codex_exec') or {}
            if set(opts) - {'bin', 'reasoning_effort', 'web_search', 'image_generation'}:
                raise ValueError('D7 Codex CLI accepts only explicit execution settings')
            if (opts.get('reasoning_effort', 'xhigh') != 'xhigh'
                    or opts.get('web_search', 'disabled') != 'disabled'
                    or opts.get('image_generation', False) is not False):
                raise ValueError('D7 CLI requires xhigh, no web search and no image generation')
            judge['codex_exec'] = {**opts, 'reasoning_effort': 'xhigh',
                                   'web_search': 'disabled', 'image_generation': False}
        if judge['max_output_tokens'] < 1 or judge['timeout_s'] <= 0:
            raise ValueError('Token limit and timeouts must be positive')
        if type(judge['concurrency']) is not int or not 1 <= judge['concurrency'] <= 8 or judge['max_context_chars'] < 1:
            raise ValueError('Judge concurrency must be in 1..8 and context budget positive')
        if judge.get('reasoning_effort') not in (None, 'low', 'medium', 'high', 'xhigh'):
            raise ValueError('Invalid judge reasoning_effort')
        if type(judge['stream']) is not bool or type(judge['stream_include_usage']) is not bool:
            raise ValueError('Judge stream options must be boolean')
        result['judge'] = judge
    if type(parallel_answers) is not bool or (parallel_answers and stream_judging):
        raise ValueError('parallel_answers is boolean and excludes stream_judging')
    if parallel_answers and not result.get('answers'):
        raise ValueError('parallel_answers requires answer_models')
    if stream_judging or parallel_answers:
        if stream_judging and (not result.get('answers') or not result.get('judge')):
            raise ValueError('stream_judging requires answer_models and judge_model')
        if type(arm_concurrency) is not int or not 1 <= arm_concurrency <= 8:
            raise ValueError('arm_concurrency must be in 1..8')
        if any(type(v) is not int or v < 1 for v in (max_questions, max_consecutive_failures)):
            raise ValueError('Question budget and failure limit must be positive integers')
        if arm_concurrency > 1:
            gpus = [m.get('cuda_visible_devices') for m in result['answers'] if m['backend'] not in {'modelhub', 'codex_exec'}]
            if any(gpu is None for gpu in gpus) or len(set(map(str, gpus))) != len(gpus):
                raise ValueError('Concurrent local arms require explicitly distinct cuda_visible_devices')
        result.update(stream_judging=stream_judging, parallel_answers=parallel_answers, arm_concurrency=arm_concurrency,
                      max_questions=max_questions, max_consecutive_failures=max_consecutive_failures)
    if d_evaluation is not None:
        if answer_models is not None or stream_judging or not result.get('judge'):
            raise ValueError('D rescoring requires only existing answer references and a judge')
        spec = dict(d_evaluation)
        limit = spec.get('question_limit')
        if type(limit) is not int or not 1 <= limit <= 300:
            raise ValueError('D requires an explicit question_limit in 1..300')
        if type(spec.get('arm_concurrency', 2)) is not int or spec.get('arm_concurrency', 2) not in (1, 2):
            raise ValueError('D arm_concurrency must be 1 or 2')
        if ('max_consecutive_failures' in spec and (type(spec['max_consecutive_failures']) is not int
                or spec['max_consecutive_failures'] < 1)):
            raise ValueError('D max_consecutive_failures must be a positive integer')
        models = spec.get('answers')
        if not isinstance(models, list) or len(models) != 2:
            raise ValueError('D comparison requires two existing answer arms')
        for model in models:
            if (not model.get('model') or model.get('answer_mode') not in {'text_only', 'positive_images', 'imagerag'}
                    or type(model.get('source', {}).get('version')) is not int
                    or model['source']['version'] < 1 or not model['source'].get('uri')):
                raise ValueError('D answer arms require model, condition and fixed source version')
        if models[0]['answer_mode'] != 'text_only' or models[1]['answer_mode'] not in {'positive_images', 'imagerag'}:
            raise ValueError('D arm order must be text_only then positive_images or imagerag')
        for model in models:
            for item in model.get('additional_sources', []):
                ref = item['source']
                if type(ref.get('version')) is not int or ref['version'] < 1 or not ref.get('uri'):
                    raise ValueError('Additional answer sources require fixed versions')
            if ref := model.get('rag_inputs'):
                if (model['answer_mode'] != 'imagerag' or set(ref) != {'uri', 'version'}
                        or not ref.get('uri') or type(ref.get('version')) is not int or ref['version'] < 1):
                    raise ValueError('RAG score arms require a fixed retrieval-input reference')
        if spec.get('codex_export') and (result['judge']['mode'] != 'offline'
                or result['judge']['model'] != 'codex/gpt-6-astra'
                or result['judge'].get('reasoning_effort') != 'xhigh'):
            raise ValueError('Codex export requires offline Codex gpt-6-astra + xhigh')
        for ref in spec.get('reuse_scores', []):
            if type(ref.get('version')) is not int or ref['version'] < 1 or not ref.get('uri'):
                raise ValueError('Reused D7 scores require fixed versions')
        if type(spec.get('reuse_initial_scores', False)) is not bool:
            raise ValueError('reuse_initial_scores must be boolean')
        pack = spec.get('judge_prompt_pack') or yaml.safe_load((PROMPTS / 'd7.yaml').read_text())
        version = pack['prompts']['judge_d']['version']
        if (version not in {'t2i-v2-d-judge-4-review', 't2i-v2-d-judge-5-review',
                           't2i-v2-d-judge-6-review', 't2i-v2-d-judge-7-review'}
                or pack['prompts']['judge_d'].get('schema_retries') != 0):
            raise ValueError('D requires a reviewed judge prompt and zero schema retries')
        parse_prompt_pack(yaml.safe_dump(pack, allow_unicode=True, sort_keys=False))
        spec['judge_prompt_pack'] = pack
        if version == 't2i-v2-d-judge-7-review':
            result['judge'].setdefault('reasoning_effort', 'xhigh')
        if version in {'t2i-v2-d-judge-6-review', 't2i-v2-d-judge-7-review'}:
            if 'core_prompt_pack' in spec:
                raise ValueError('D6/D7 use one judge call; core_prompt_pack is not supported')
        else:
            core = spec.get('core_prompt_pack') or yaml.safe_load(
                (PROMPTS.parent / 'archive/d5/prompts/d_core.yaml').read_text())
            definition = core['prompts']['prepare_d_core']
            if definition['version'] != 't2i-v2-d-core-1' or definition.get('schema_retries') != 0:
                raise ValueError('Legacy D requires its archived core-1 prompt with zero schema retries')
            parse_prompt_pack(yaml.safe_dump(core, allow_unicode=True, sort_keys=False))
            spec['core_prompt_pack'] = core
        result.update(d_evaluation=spec, answers=models)
    return result


def prepare_imagerag(questions, *, model, root, name, maximum, failure_limit=3):
    """本评测的固定检索图：三阶段诊断 → 文本编码 → 图片向量检索 → 按题关联。

    每题保留初图与诊断；每caption一条查询及命中记录。编码物化后服务已释放，
    返回只含独立对象引用的Dataset供后续生图。模型调用、读写和关联均在此主线可见。
    """
    from demiflow.embeddings import EmbeddingModel
    from demiflow.services import VLLMService
    cfg = model['imagerag']
    all_questions, reusable_inputs = questions, None
    if cfg.get('reuse'):
        imagerag.validate_reuse_model(model, root=root)
        refs = cfg['reuse']
        def fixed(ref):
            return {**ref, 'uri': str((root / ref['uri']).resolve())}
        old_inputs = data.read_lance(**fixed(refs['inputs'])).map(
            lambda r: {'task_id': r['task_id'], 'reused_rag_input': r})
        old_answers = data.read_lance(**fixed(refs['answers'])).filter(
            lambda r: r['status'] == 'generated').map(
            lambda r: {'task_id': r['task_id'], 'rag_previous_answer': r})
        all_questions = (questions.join(old_inputs, on='task_id', how='left')
            .join(old_answers, on='task_id', how='left')
            .map(partial(imagerag.restore_input, model=model)).materialize())
        reusable_inputs = all_questions.filter(lambda r: bool(r.get('reused_rag_input'))).map(
            lambda r: {k: r[k] for k in imagerag.INPUTS.names})
        questions = all_questions.filter(lambda r: not r.get('reused_rag_input'))
        if questions.count() == 0:
            uri = str(root / DATASETS / f'rag_inputs__{name}.lance')
            reusable_inputs.write_lance(uri, mode='overwrite', schema=imagerag.INPUTS)
            return all_questions
    pool = {**cfg['pool'], 'uri': str((root / cfg['pool']['uri']).resolve())}
    encoder = EmbeddingModel(**cfg['encoder'])
    table = lance.dataset(pool['uri'], version=pool['version'])
    encoded_contract = (table.schema.metadata or {}).get(b'image_embeddings.contract')
    if not encoded_contract or json.loads(encoded_contract) != encoder.contract():
        raise ValueError('ImageRAG query encoder must match the complete frozen image embedding contract')
    if table.schema.field('embedding').type != pa.list_(pa.float32(), encoder.dimensions):
        raise ValueError('ImageRAG image embedding column differs from its declared dimension/type')
    if table.count_rows() == 0:
        raise ValueError('ImageRAG retrieval pool is empty')
    image_captions = None
    for ref in cfg['caption_sources']:
        fixed = {**ref, 'uri': str((root / ref['uri']).resolve())}
        schema = lance.dataset(**fixed).schema
        for key in ('sha256', 'status', 'caption', 'error'):
            if not pa.types.is_string(schema.field(key).type) and not pa.types.is_large_string(schema.field(key).type):
                raise ValueError('ImageRAG caption source requires text column ' + key)
        rows = data.read_lance(**fixed, columns=['sha256', 'status', 'caption', 'error']).map(
            partial(imagerag.caption_record, source=ref))
        image_captions = rows if image_captions is None else image_captions.union(rows)
    if image_captions is not None:
        image_captions = image_captions.reduce_by_key('sha256', imagerag.latest_caption)
    prompt_pack = parse_prompt_pack(yaml.safe_dump(cfg['prompt_pack'], allow_unicode=True, sort_keys=False))
    vlm = cfg['vlm']
    initial_sources = []
    for priority, ref in enumerate([cfg['initial_answers'], *cfg['additional_initial_answers']]):
        fixed = {**ref, 'uri': str((root / ref['uri']).resolve())}
        initial_sources.append(data.read_lance(**fixed, columns=['request_id', *ANSWERS.names])
            .filter(lambda row: row['answer_model'] == model['model'] and row['answer_mode'] == 'text_only')
            .map(lambda row, priority=priority, ref=ref: {'task_id': row['task_id'],
                 'initial_answer': {**row, 'source': ref}, 'initial_priority': priority}))
    initial = initial_sources[0].union(*initial_sources[1:]).reduce_by_key('task_id', imagerag.one_initial)
    diagnosed = (questions.map(lambda row: {'task_id': row['task_id'], 'instruction': row['instruction']})
        .join(initial, on='task_id', how='left')
        .map(partial(imagerag.prepare_initial, model=model, root=root)))
    if recovery := cfg.get('reuse', {}).get('caption_recovery'):
        previous = data.read_lance(**{**recovery, 'uri': str((root / recovery['uri']).resolve())})
        diagnosed = diagnosed.join(previous.map(lambda row: {
            'task_id': row['task_id'], 'caption_recovery': row}), on='task_id', how='left').map(imagerag.recover_caption)
    from .operators import imagerag_original
    original = cfg.get('prompt_protocol') == 'upstream_16c9502'
    stages = [('decision', 'ready', 1), ('concepts', 'needs_concepts', 1)]
    if original:
        stages += [('concepts', 'needs_concepts', 2), ('concepts', 'needs_concepts', 3)]
    stages += [('captions', 'needs_captions', 1)]
    # 固定有界图，业务控制条件；每个标准节点仍只发一次 completion。
    for stage, status, attempt in stages:
        step = stage + ('_' + str(attempt) if original and stage == 'concepts' else '')
        declared_model = cfg['prompt_pack']['prompts'][stage]['model']
        calls = {'root': str(root), 'relative_uri': str(DATASETS / f'calls_rag_{step}__{name}.lance')}
        journal = {**sqlite_call_options(**calls), 'max_requests': max(1, maximum)}
        if vlm['mode'] == 'offline':
            options = {'offline_store': journal}
        else:
            os.environ.setdefault(declared_model['api_key_env'], 'anything')
            options = {'sqlite_journal': journal, 'timeout_s': vlm['timeout_s'], 'verify_model': False,
                'trust_env': False, 'require_finish_reason_stop': True,
                'request_options': {'temperature': 0, 'max_tokens': vlm['max_output_tokens'],
                    'response_format': {'type': 'text' if original else 'json_object'},
                    **({'reasoning_effort': vlm['reasoning_effort']} if vlm.get('reasoning_effort') else {})}}
        prepare = (partial(imagerag_original.prepare_messages, cfg=cfg, stage=stage, attempt=attempt)
                   if original else partial(imagerag.prompt_payload, cfg=cfg, stage=stage))
        finish = (partial(imagerag_original.finish_stage, cfg=cfg, stage=stage, attempt=attempt)
                  if original else partial(imagerag.finish_stage, cfg=cfg, stage=stage))
        diagnosed = (diagnosed.map(prepare)
            .map_prompt_async(stage, config=prompt_pack, options=options,
                inputs={'messages': 'rag_messages'} if original else {'payload': 'rag_payload', 'images': 'rag_images'},
                output='rag_result', call_output='rag_call', error_output='rag_error',
                when=lambda row, status=status: row['rag_status'] == status,
                max_requests=max(1, maximum), concurrency=vlm['concurrency'], queue_depth=vlm['concurrency'])
            .map(finish))
    diagnostic_stats = (diagnosed.map(lambda row: {k: row.get(k) for k in imagerag.DIAGNOSES.names})
        .save_lance(str(root / DATASETS / f'rag_diagnoses__{name}.lance'), schema=imagerag.DIAGNOSES,
                    key='task_id', stage='diagnoses', max_batch=1, queue_depth=1)
        .map(FailureLimit(failure_limit, 'rag_status', ['keep_initial', 'needs_retrieval', 'diagnosis_pending']))
        .run_stream(log_every=1))
    diagnoses_ref = diagnostic_stats.outputs['diagnoses']
    diagnoses = data.read_lance(**diagnoses_ref)
    service = (VLLMService(cfg['query_service'], root=root,
        log_path=root / '_demiflow/evaluation_t2i_v2' / name / 'query_encoder.log')
        if cfg['query_service'] else None)
    execution = dict(cfg['embedding_execution'])
    execution['max_requests'] = min(execution.get('max_requests') or max(1, maximum * cfg['max_queries']),
                                    max(1, maximum * cfg['max_queries']))
    execution['options'] = {**execution['options'], 'sqlite_journal': {
        'path': str(root / DATASETS / f'calls_rag_embeddings__{name}.sqlite')}}
    # 查询由唯一task_id和概念序号生成；本地分组按键输出，使编码分批在重跑时稳定。
    # Dataset.sort仅用于Ray，不能用于本地编码链。
    encoded = (diagnoses.flat_map(imagerag.queries).reduce_by_key('query_id', lambda acc, row: row)
        .map_embeddings(model=encoder, inputs={'text': 'caption'}, output='embedding',
            call_output='embedding_call', error_output='embedding_error', service=service, **execution)
        .map(lambda row: {**row, 'embedding_call_json': imagerag.dumps(row.get('embedding_call'))})
        .map(lambda row: {k: row.get(k) for k in imagerag.query_schema(encoder).names})
        .materialize())
    query_uri = str(root / DATASETS / f'rag_queries__{name}.lance')
    receipt = encoded.write_lance(query_uri, mode='overwrite', schema=imagerag.query_schema(encoder), return_receipt=True)
    query_ref = {'uri': query_uri, 'version': receipt.committed_version}
    queries = data.read_lance(**query_ref)
    # 全固定池精确cosine top1；不按题目/概念/审核正例名单过滤，不增加重排与阈值。
    hits = (queries.filter(lambda row: not row.get('embedding_error'))
        .search_vectors(query='embedding', output='hits', **pool, vector_column='embedding',
            columns=['sha256', 'image_uri', 'encoder_id'], metric='cosine', top_k=1,
            concurrency=4, queue_depth=4, options={'use_index': False}))
    if image_captions is not None:
        hits = (hits.map(imagerag.hit_row).materialize().join(image_captions, on='sha256', how='left')
            .map(imagerag.captioned_hits))
    hits = (hits.map(lambda row: {**row, 'hits_json': imagerag.dumps(row['hits'])})
        .map(lambda row: {k: row.get(k) for k in imagerag.RETRIEVALS.names})
        .materialize())
    failed_queries = queries.filter(lambda row: bool(row.get('embedding_error'))).map(
        lambda row: {k: ('[]' if k == 'hits_json' else row.get(k)) for k in imagerag.RETRIEVALS.names})
    retrieval_uri = str(root / DATASETS / f'rag_retrievals__{name}.lance')
    receipt = hits.union(failed_queries).write_lance(retrieval_uri, mode='overwrite',
        schema=imagerag.RETRIEVALS, return_receipt=True)
    retrieval_ref = {'uri': retrieval_uri, 'version': receipt.committed_version}
    grouped = data.read_lance(**retrieval_ref).reduce_by_key(
        'task_id', partial(imagerag.group_hits, maximum=cfg['max_queries']))
    prepared = (diagnoses.join(grouped, on='task_id', how='left')
        .map(partial(imagerag.assemble, cfg=cfg,
            stage_refs={'diagnoses': diagnoses_ref, 'queries': query_ref, 'retrievals': retrieval_ref,
                        **({'caption_recovery': cfg['reuse']['caption_recovery']}
                           if cfg.get('reuse', {}).get('caption_recovery') else {})})))
    if reusable_inputs is not None:
        prepared = prepared.union(reusable_inputs)
    input_uri = str(root / DATASETS / f'rag_inputs__{name}.lance')
    receipt = prepared.write_lance(input_uri, mode='overwrite', schema=imagerag.INPUTS, return_receipt=True)
    input_ref = {'uri': input_uri, 'version': receipt.committed_version}
    print(json.dumps({'stage': 'imagerag_prepared', 'arm': name, 'inputs': input_ref,
                      'diagnoses': diagnoses_ref, 'queries': query_ref, 'retrievals': retrieval_ref}), flush=True)
    return all_questions.map(lambda r: {k: v for k, v in r.items() if k != 'rag_json'}).join(
        data.read_lance(**input_ref), on='task_id', how='left')


def run_answers(run, source, model, *, answer_target=None, failure_limit=3):
    """执行一个模型的标准生成链；逐题保存答案记录，最后统一写目标表。

    独立模型 Python 环境也调用此函数；每个模型处理完全部题目后释放资源。
    """
    root = resolve_root()
    records = RunTables(root, str(DATASETS / f'records__{run.name}.lance'))
    blobs = LocalObjectStore(root / 'objects')
    maximum = data.read_lance(**source, columns=['task_id']).count()
    questions = (data.read_lance(source['uri'], version=source['version'])
        .map(
            lambda row: {key: row.get(key) for key in (
                'task_id', 'concept', 'instruction', 'references_json',
                'authoring_variant', 'authoring_images_json')}
        ))
    if model['answer_mode'] == 'imagerag':
        questions = prepare_imagerag(questions, model=model, root=root,
            name=run.name if answer_target else f'{run.name}__{digest(model)[:16]}', maximum=maximum,
            failure_limit=failure_limit)
    if model.get('reuse_answers_from'):
        prior = data.read_lance(**model['reuse_answers_from']).filter(lambda row: row['status'] == 'generated').map(
            lambda row: {'task_id': row['task_id'], 'reused_answer': row})
        questions = questions.join(prior, on='task_id', how='left')
    prepared = questions.map(PrepareAnswer(model, records))
    if model['backend'] == 'codex_exec':
        from evaluation.t2i.v2.operators import codex_answers
        pack = parse_prompt_pack(yaml.safe_dump(model['codex_prompt_pack'], allow_unicode=True))
        options = {'codex_exec': model['codex_exec'], 'timeout_s': model['timeout_s'],
            'sqlite_journal': sqlite_call_options(root=str(root),
                relative_uri=str(DATASETS / f'calls_codex_answers__{run.name}.lance'))}
        answers = (prepared.map(partial(codex_answers.prepare_inputs, model=model))
            .map_prompt_async('answer_codex', config=pack, options=options,
                inputs={'answer_text': 'codex_answer_text', 'images': 'codex_images'},
                output='codex_result', call_output='codex_call', error_output='codex_error',
                when=lambda row: row['answer_ready'], max_requests=maximum,
                concurrency=model['concurrency'], queue_depth=model['queue_depth'])
            .map(partial(codex_answers.finish_generation,
                max_artifact_bytes=model['codex_exec']['max_artifact_bytes']))
            .map(FinishAnswer(records)))
    else:
        answers = (prepared.map_image_async(template=answer_template(model), model=model, inputs=answer_bindings(model),
                output='generated_image', call_output='answer_call', error_output='answer_error',
                journal_path=root / DATASETS / f'calls_answers__{run.name}__{digest(model)[:16]}.sqlite', object_store=root / 'objects',
                max_requests=maximum, when=lambda row: row['answer_ready'],
                concurrency=model['concurrency'], queue_depth=model['queue_depth'],
                service=answer_service(model), image_encoding=model.get('image_encoding', 'png'),
                limits=model.get('image_limits'))
            .map(FinishAnswer(records)))
    if model['answer_mode'] == 'imagerag' and answer_target is None:
        answers = answers.save_lance(str(root / DATASETS / f'answers__{run.name}__{digest(model)[:16]}.lance'),
            schema=ANSWERS, key='task_id', stage='answers', max_batch=1, queue_depth=1)
    if answer_target is not None:
        answers = (answers.save_lance(answer_target, schema=ANSWERS, key='task_id',
            stage='answers', max_batch=1, queue_depth=1)
            .map(FailureLimit(failure_limit, 'status', ['generated'])))
    return answers.run_stream(log_every=1 if answer_target else 0)


def run_pipeline(run, source, config, *, target_uri=None, write_mode='overwrite'):
    """第一步：读取 benchmark，答题后写目标表，所有评分字段为空。

    返回 target 的 uri/version，供独立打分使用；本入口不会调用 judge。
    """
    if config.get('codex_comparison'):
        return run_codex_comparison(run, config['codex_comparison'])
    if config.get('d_evaluation'):
        return run_d_evaluation(run, source, config, target_uri=target_uri, write_mode=write_mode)
    if config.get('stream_judging') or config.get('parallel_answers'):
        return run_paired_evaluation(run, source, config, target_uri=target_uri, write_mode=write_mode)
    root = resolve_root()
    if write_mode not in {'append', 'overwrite'}:
        raise ValueError('write_mode must be append or overwrite')
    run = Path(run).absolute()
    if run.parent != root / DATASETS or not run.name or run.suffix:
        raise ValueError('run must be a suffix-free name under ' + str(root / DATASETS))
    if type(source.get('version')) is not int or source['version'] < 1:
        raise ValueError('Specify a fixed input table version')
    source = {'uri': str((root / source['uri']).resolve()), 'version': source['version']}
    target_uri = (
        str((root / target_uri).resolve()) if target_uri else str(run.parent / f'results__{run.name}.lance')
    )
    with run_lock(root / '_demiflow' / 'evaluation_t2i_v2' / run.name):
        records = RunTables(root, str(DATASETS / f'records__{run.name}.lance'))
        directory = Path(__file__).parent
        paths = [directory / 't2i_v2_eval_pipeline.py', directory / 'operators/answers.py']
        code = {str(path.relative_to(directory)): path.read_text() for path in paths if path.is_file()}
        if any(model['answer_mode'] in ('positive_images', 'imagerag') for model in config['answers']):
            # 同一预处理算子由 benchmark 维护；这里只使用其单图I/O，不调用上游流程。
            image_operator = directory.parents[2] / 'benchmark/t2i/v2/operators/images.py'
            code['benchmark/t2i/v2/operators/images.py'] = image_operator.read_text()
        if any(model['answer_mode'] == 'imagerag' for model in config['answers']):
            for filename in ('imagerag.py', 'imagerag_original.py'):
                code['operators/' + filename] = (directory / 'operators' / filename).read_text()
        if any(model['backend'] == 'bagel' for model in config['answers']):
            code['evaluation/bagel/adapter.py'] = (directory.parents[1] / 'bagel/adapter.py').read_text()
        request = {
            'source': source,
            'target_uri': target_uri,
            'write_mode': write_mode,
            'config': {'answers': config['answers']},
            'code': code,
        }
        previous = records.load_manifest()
        if previous is not None and previous != request:
            raise ValueError('Input, configuration or code changed; use a new run name')
        records.save_manifest(request)
        state = records.load()
        if state:
            return state
        # 参考信息按模型配置启用；考点与判据始终不进入答题输入。
        questions = data.read_lance(source['uri'], version=source['version']).materialize()
        invalid = questions.filter(
            lambda row: not isinstance(row.get('task_id'), str)
            or not row['task_id'].strip()
            or not isinstance(row.get('instruction'), str)
            or not row['instruction'].strip()
        ).take(1)
        duplicate_ids = (
            (
                questions.reduce_by_key(
                    'task_id',
                    lambda acc, row: {'task_id': row['task_id'], 'count': (acc['count'] if acc else 0) + 1},
                )
                .filter(lambda row: row['count'] > 1)
                .take(1)
            )
            if not invalid
            else []
        )
        if invalid or duplicate_ids:
            raise ValueError('Input requires unique nonempty task_id and nonempty instruction')
        if any(model['answer_mode'] == 'legacy_references' for model in config['answers']):
            if questions.filter(lambda row: not isinstance(json.loads(row['references_json']), list)).take(1):
                raise ValueError('Reference answers require references_json containing a list')
        # 逐题结果已持久化到 records；所有答题结束后一次写表，重试 writer 不重生成。
        for index, model in enumerate(config['answers']):
            if model.get('python'):
                # 独立环境仅用于解决模型依赖冲突；子进程仍执行同一个正式生成链。
                environment = dict(os.environ)
                # 两个模型环境共用本次运行的业务源码与 demiflow，避免旧安装覆盖当前 API。
                environment['PYTHONPATH'] = os.pathsep.join(
                    [
                        str(directory.parents[2]),
                        str(Path(demiflow.__file__).resolve().parents[1]),
                        # Qwen-Image-2.1 的专用依赖只进入该模型的子进程。
                        *model.get('pythonpath', []),
                        environment.get('PYTHONPATH', ''),
                    ]
                )
                environment['DEMIWTG_DATASETS_ROOT'] = str(root)
                if model.get('cuda_visible_devices') is not None:
                    environment['CUDA_VISIBLE_DEVICES'] = str(model['cuda_visible_devices'])
                subprocess.run(
                    [
                        model['python'],
                        '-m',
                        'evaluation.t2i.v2.t2i_v2_eval_pipeline',
                        '--run',
                        run.name,
                        '--generate-model',
                        str(index),
                    ],
                    env=environment,
                    check=True,
                )
            else:
                run_answers(run, source, model)
        # 题目×模型×方式的答案按实际请求身份定位；两路图分别保存，评分字段显式置空。
        answer_sources = [
            (data.read_lance(str(root / DATASETS / f'answers__{run.name}__{digest(model)[:16]}.lance'),
                version=lance.dataset(str(root / DATASETS / f'answers__{run.name}__{digest(model)[:16]}.lance')).version)
             if model['answer_mode'] == 'imagerag' else questions.map(
                lambda row, model=model: records.answer(
                    answer_identity(model, row)
                )
            ))
            for model in config['answers']
        ]
        answers = (
            answer_sources[0]
            .union(*answer_sources[1:])
            .map(lambda row: {name: row.get(name) for name in SCORES.names})
            .materialize()
        )
        answers.write_lance(target_uri, mode=write_mode, schema=SCORES)
        # 仅收集按 status 聚合后的计数；不将全部答案拉回 Python 逐条统计。
        counts = {
            row['status']: row['count']
            for row in answers.reduce_by_key(
                'status', lambda acc, row: {'status': row['status'], 'count': (acc['count'] if acc else 0) + 1}
            ).take_all()
        }
        arm_counts = answers.reduce_by_key(
            ['answer_model', 'answer_mode', 'status'],
            lambda acc, row: {'answer_model': row['answer_model'], 'answer_mode': row['answer_mode'], 'status': row['status'],
                              'count': (acc['count'] if acc else 0) + 1},
        ).take_all()
        state = {
            'complete': set(counts) <= {'generated'},
            'counts': counts,
            'counts_by_arm': {
                name: {row['status']: row['count'] for row in arm_counts if row['answer_model'] == name}
                for name in sorted({row['answer_model'] for row in arm_counts})
            },
            'target': {'uri': target_uri, 'version': lance.dataset(target_uri).version},
        }
        records.save(state)
        return state


def run_judging(run, source, config, *, target_uri, write_mode='overwrite'):
    """第二步：读取源表固定版本的答案，judge 后写入指定目标表。

    源表和目标表分别配置；目标可以与源相同，也可以是另一张表。

    不调用答题模型。每次独立打分使用自己的运行名和调用日志；同名可续跑。
    所有行处理完成后才提交目标表的新版本，执行中断不会提前清空原表。
    """
    root = resolve_root()
    if write_mode not in {'append', 'overwrite'}:
        raise ValueError('write_mode must be append or overwrite')
    run = Path(run).absolute()
    if run.parent != root / DATASETS or not run.name or run.suffix:
        raise ValueError('run must be a suffix-free name under ' + str(root / DATASETS))
    if type(source.get('version')) is not int or source['version'] < 1:
        raise ValueError('Specify a fixed input table version')
    source = {'uri': str((root / source['uri']).resolve()), 'version': source['version']}
    target_uri = str((root / target_uri).resolve())
    with run_lock(root / '_demiflow' / 'evaluation_t2i_v2' / run.name):
        records = RunTables(root, str(DATASETS / f'records__{run.name}.lance'))
        calls = {'root': str(root), 'relative_uri': str(DATASETS / f'calls__{run.name}.lance')}
        directory = Path(__file__).parent
        paths = [
            directory / 't2i_v2_eval_pipeline.py',
            directory / 'operators/images.py',
            *sorted(PROMPTS.glob('*')),
        ]
        request = {
            'source': source,
            'target_uri': target_uri,
            'write_mode': write_mode,
            'judge': config['judge'],
            'code': {str(path.relative_to(directory)): path.read_text() for path in paths if path.is_file()},
        }
        previous = records.load_manifest()
        if previous is not None and previous != request:
            raise ValueError('Input, output, judge configuration or code changed; use a new run name')
        records.save_manifest(request)
        state = records.load()
        if state and state['complete']:
            return state
        # judge 配置直接进入原生 prompt pack；保留用户指定模型名，不依赖网关目录完整性。
        judge = config['judge']
        spec = yaml.safe_load((PROMPTS / 'tasks.yaml').read_text())
        spec['prompts']['judge']['model'].update(
            name=judge['model'], base_url=judge['base_url'], api_key_env=judge['api_key_env']
        )
        pack = parse_prompt_pack(yaml.safe_dump(spec, allow_unicode=True, sort_keys=False))
        if judge['mode'] == 'offline':
            options = {'offline_store': sqlite_call_options(**calls)}
        else:
            os.environ.setdefault(judge['api_key_env'], 'anything')
            options = {
                'sqlite_journal': sqlite_call_options(**calls),
                'timeout_s': judge['timeout_s'],
                'verify_model': False,
                'trust_env': False,
                'require_finish_reason_stop': True,
                'request_options': {
                    'temperature': 0,
                    'max_tokens': judge['max_output_tokens'],
                    'response_format': {'type': 'json_object'},
                    **({'reasoning_effort': judge['reasoning_effort']} if judge.get('reasoning_effort') else {}),
                },
            }
        # 每行是一张既有答案；已有图片可以重新打分。输入只包含题面和图片，不发送模型名、考点或参考。
        scored = (
            data.read_lance(source['uri'], version=source['version'])
            .map(
                lambda row: (
                    {
                        **row,
                        'status': 'generated',
                        'reason': '',
                        'prompt_images': [image_data_url(json.loads(row['image_json']), root)],
                    }
                    if row.get('image_json')
                    else row
                )
            )
            .map_prompt_async(
                'judge',
                config=pack,
                options=options,
                inputs={'instruction': 'instruction', 'images': 'prompt_images'},
                output='judge_result',
                call_output='judge_call',
                error_output='judge_error',
                when=lambda row: row['status'] == 'generated',
                concurrency=1,
                queue_depth=1,
            )
            .map(partial(judge_response_fields, ANSWERS=ANSWERS, judge=judge))
            .map(
                lambda row: (
                    {
                        **row,
                        'status': (
                            'pending_judge'
                            if row['judge_error']['type'] == 'PromptResponsePending'
                            else 'judge_failed'
                        ),
                        'reason': row['judge_error']['detail'],
                    }
                    if row['status'] == 'generated' and row['judge_error']
                    else row
                )
            )
            # 分值只接受整数 0/1/2 或 N/A；不把字符串、浮点或布尔值转换成合法分数。
            .map(validate_scores)
            .map(
                lambda row: (
                    {**row, 'status': 'judge_failed', 'reason': row['invalid_scores'][0]}
                    if row['status'] == 'generated' and row['invalid_scores']
                    else row
                )
            )
            # 各维度排除 N/A 后，将 0/1/2 映射为 0/60/100 求均值；全 N/A 的维度仍为空。
            .map(score_values)
            .map(mean_scores)
            .map(lambda row: {name: row.get(name) for name in SCORES.names})
            # 固定异步结果供指纹、写表和计数复用；缓存仅含题面、Blob 引用和评分。
            .materialize()
        )
        # 相同评分快照只写一次；待响应或失败的结果再次查看时不重复追加。
        output_key = 'scores/' + digest(scored.take_all())
        output = records.find_commit(*output_key.split("/", 1))
        if output is None:
            with DatasetCommit(records.manifest_path.with_suffix('') / 'append_intents', output_key, target_uri, mode=write_mode) as commit:
                if commit.output is None:
                    receipt = scored.write_lance(target_uri, mode=write_mode, schema=SCORES, return_receipt=True)
                    commit.confirm(receipt)
                output = commit.output
            records.save_commit(*output_key.split("/", 1), output)
        counts = {
            row['status']: row['count']
            for row in scored.reduce_by_key(
                'status', lambda acc, row: {'status': row['status'], 'count': (acc['count'] if acc else 0) + 1}
            ).take_all()
        }
        state = {'complete': set(counts) <= {'scored'}, 'counts': counts, 'target': output, 'calls': calls}
        records.save(state)
        return state


def paired_arm(run, index):
    """每路独立缓存、流式表和调用日志；四路不竞争同一张表的writer。"""
    name = f'{run.name}__arm{index:02d}'
    return {'name': name, **{stage: str(run.parent / f'{stage}__{name}.lance')
                            for stage in ('answers', 'scores_a', 'scores_b')}}


def paired_prompt(standard, judge, root, arm_name, maximum):
    """主线中显式装配原生prompt节点；每路每标准最多maximum次请求且不重试。"""
    task = 'judge' if standard == 'a' else 'probe_judge'
    path = PROMPTS / ('tasks.yaml' if standard == 'a' else 'probe.yaml')
    spec = yaml.safe_load(path.read_text())
    spec['prompts'][task]['model'].update(name=judge['model'], base_url=judge['base_url'], api_key_env=judge['api_key_env'])
    pack = parse_prompt_pack(yaml.safe_dump(spec, allow_unicode=True, sort_keys=False))
    calls = {'root': str(root), 'relative_uri': str(DATASETS / f'calls_{standard}__{arm_name}.lance')}
    journal = {**sqlite_call_options(**calls), 'max_requests': maximum}
    if judge['mode'] != 'offline':
        # 本地网关无鉴权，但原生prompt客户端要求声明的密钥变量非空。
        os.environ.setdefault(judge['api_key_env'], 'anything')
    options = {'offline_store': journal} if judge['mode'] == 'offline' else {
        'sqlite_journal': journal, 'timeout_s': judge['timeout_s'], 'verify_model': False,
        'trust_env': False, 'require_finish_reason_stop': True,
        'request_options': {'max_tokens': judge['max_output_tokens'],
                            'response_format': {'type': 'json_object'},
                            **({'reasoning_effort': judge['reasoning_effort']} if judge.get('reasoning_effort') else {})},
    }
    return pack, options


def run_paired_arm(run, manifest, index):
    """单路固定Dataset图：生图即落表 → A评审即落表 → B评审即落表。"""
    root = resolve_root()
    settings, source = manifest['config'], manifest['source']
    model = settings['answers'][index]
    arm = paired_arm(run, index)
    if settings.get('parallel_answers'):
        stats = run_answers(Path(run).with_name(arm['name']), source, model,
            answer_target=arm['answers'], failure_limit=settings['max_consecutive_failures'])
        count = data.read_lance(**stats.outputs['answers']).filter(lambda r: r['status'] == 'generated').count()
        state = {'complete': count == manifest['expected_questions'], 'outputs': stats.outputs}
        RunTables(root, str(DATASETS / f"records__{arm['name']}.lance")).save(state)
        return state
    judge = settings['judge']
    records = RunTables(root, str(DATASETS / f'records__{arm["name"]}.lance'))
    a_pack, a_options = paired_prompt('a', judge, root, arm['name'], manifest['expected_questions'])
    b_pack, b_options = paired_prompt('b', judge, root, arm['name'], manifest['expected_questions'])
    prompt_chars = max(len(p.template.source) for pack in (a_pack, b_pack) for p in pack.prompt_definitions.values())
    available_chars = judge['max_context_chars'] - prompt_chars
    if available_chars <= 0:
        raise ValueError('Judge prompt exceeds complete input budget')
    def log_stage(row, stage):
        state = row['status'] if stage == 'answer' else row[stage + '_status']
        print(json.dumps({'arm': index, 'stage': stage, 'concept': row['concept'], 'status': state}, ensure_ascii=False), flush=True)
        return row
    with run_lock(root / '_demiflow/evaluation_t2i_v2' / arm['name']):
        questions = data.read_lance(source['uri'], version=source['version'], batch_size=1).map(
            lambda row: {key: row.get(key) for key in (
                'task_id', 'concept', 'instruction', 'references_json', 'authoring_variant',
                'authoring_images_json', 'test_points', 'taxonomy')})
        if model.get('reuse_answers_from'):
            # 只关联显式固定版本中的成功答案；逐行再核对完整生成请求身份。
            prior = data.read_lance(**model['reuse_answers_from']).filter(lambda row: row['status'] == 'generated').map(
                lambda row: {'task_id': row['task_id'], 'reused_answer': row})
            questions = questions.join(prior, on='task_id', how='left')
        if model['answer_mode'] == 'imagerag':
            questions = prepare_imagerag(questions, model=model, root=root,
                name=arm['name'], maximum=manifest['expected_questions'],
                failure_limit=settings['max_consecutive_failures'])
        stats = (
            questions
            .map(PrepareAnswer(model, records))
            .map_image_async(template=answer_template(model), model=model, inputs=answer_bindings(model),
                output='generated_image', call_output='answer_call', error_output='answer_error',
                journal_path=root / DATASETS / f"calls_answers__{arm['name']}.sqlite", object_store=root / 'objects',
                max_requests=manifest['expected_questions'], when=lambda row: row['answer_ready'],
                concurrency=model['concurrency'], queue_depth=model['queue_depth'],
                service=answer_service(model), image_encoding=model.get('image_encoding', 'png'),
                limits=model.get('image_limits'))
            .map(FinishAnswer(records))
            .save_lance(arm['answers'], schema=ANSWERS, key='task_id', stage='answers', max_batch=1, queue_depth=1)
            .map(lambda row: log_stage(row, 'answer'))
            .map(FailureLimit(settings['max_consecutive_failures'], 'status', ['generated']))
            .map(partial(prepare_judging, root=root, judge=judge, max_context_chars=available_chars))
            .map_prompt_async('judge', config=a_pack, options=a_options,
                inputs={'instruction': 'instruction', 'images': 'prompt_images'},
                output='a_result', call_output='a_call', error_output='a_error',
                when=lambda row: row['a_status'] == 'ready', max_requests=manifest['expected_questions'],
                concurrency=judge['concurrency'], queue_depth=judge['concurrency'])
            .map(a_score)
            .save_lance(arm['scores_a'], schema=PAIRED_SCORES, key='task_id', stage='scores_a', max_batch=1, queue_depth=1)
            .map(lambda row: log_stage(row, 'a'))
            .map_prompt_async('probe_judge', config=b_pack, options=b_options,
                inputs={'payload': 'probe_payload', 'images': 'prompt_images'},
                output='b_result', call_output='b_call', error_output='b_error',
                when=lambda row: row['b_status'] == 'ready', max_requests=manifest['expected_questions'],
                concurrency=judge['concurrency'], queue_depth=judge['concurrency'])
            .map(b_score)
            .save_lance(arm['scores_b'], schema=PAIRED_SCORES, key='task_id', stage='scores_b', max_batch=1, queue_depth=1)
            .map(lambda row: log_stage(row, 'b'))
            .map(FailureLimit(settings['max_consecutive_failures'], 'a_status', ['scored', 'generation_failed']))
            .map(FailureLimit(settings['max_consecutive_failures'], 'b_status', ['reviewed', 'generation_failed']))
            .run_stream(log_every=0)
        )
        valid = data.read_lance(**stats.outputs['scores_b']).filter(
            lambda row: row['a_status'] == 'scored' and row['b_status'] == 'reviewed').count()
        state = {'complete': valid == manifest['expected_questions'], 'outputs': stats.outputs}
        records.save(state)
        return state


def run_paired_evaluation(run, source, settings, *, target_uri=None, write_mode='overwrite'):
    """同一评测流程的多个模型分支并发；子进程仍执行本文件的固定Dataset图。"""
    root = resolve_root()
    run = Path(run).absolute()
    if run.parent != root / DATASETS or run.suffix or write_mode != 'overwrite':
        raise ValueError('Paired evaluation requires a new suffix-free run under datasets and overwrite delivery')
    if type(source.get('version')) is not int or source['version'] < 1:
        raise ValueError('Specify a fixed question version')
    source = {'uri': str((root / source['uri']).resolve()), 'version': source['version']}
    target_uri = str((root / target_uri).resolve()) if target_uri else str(run.parent / f'scores_ab__{run.name}.lance')
    control = root / '_demiflow/evaluation_t2i_v2' / run.name
    records = RunTables(root, str(DATASETS / f'records__{run.name}.lance'))
    with run_lock(control):
        questions = data.read_lance(**source, columns=['task_id', 'instruction', 'test_points'])
        count = questions.count()
        if count < 1 or count > settings['max_questions']:
            raise ValueError('Question count is outside the explicit 1..max_questions budget')
        questions.map(ValidateQuestions(settings['max_questions'])).count()
        directory = Path(__file__).parent
        paths = [directory / 't2i_v2_eval_pipeline.py', *sorted((directory / 'operators').glob('*.py')),
                 *sorted(PROMPTS.glob('*.yaml')),
                 directory.parents[2] / 'benchmark/t2i/v2/operators/images.py',
                 directory.parents[2] / 'benchmark/t2i/v2/operators/probe.py']
        manifest = {'source': source, 'target_uri': target_uri, 'write_mode': write_mode,
                    'config': settings, 'expected_questions': count,
                    'code': {str(path): path.read_text() for path in paths}}
        previous = records.load_manifest()
        if previous is not None and previous != manifest:
            raise ValueError('Input, configuration or code changed; use a new run name')
        records.save_manifest(manifest)
        prior_state = records.load()
        if prior_state and prior_state.get('phase') == 'finished':
            return prior_state
        arms = [{**paired_arm(run, i), 'model': m['model'], 'answer_mode': m['answer_mode'],
                 'gpu': m.get('cuda_visible_devices'), 'state': 'queued' if m['enabled'] else 'waiting_service'}
                for i, m in enumerate(settings['answers'])]
        state = {'complete': False, 'phase': 'running', 'expected_questions': count,
                 'expected_answers': count * len(arms), 'arms': arms}
        records.save(state)
        print(json.dumps({'phase': 'start', 'run': run.name, 'questions': count, 'arms': len(arms),
                          'parallelism': settings['arm_concurrency']}, ensure_ascii=False), flush=True)
        def execute(index):
            model = settings['answers'][index]
            environment = dict(os.environ)
            environment['PYTHONPATH'] = os.pathsep.join([str(directory.parents[2]),
                str(Path(demiflow.__file__).resolve().parents[1]), *model.get('pythonpath', []), environment.get('PYTHONPATH', '')])
            environment['DEMIWTG_DATASETS_ROOT'] = str(root)
            environment['PYTHONUNBUFFERED'] = '1'
            if model['backend'] == 'codex_exec':
                # codex exec忽略用户配置；显式沿用prompt声明的现有Codex服务地址。
                environment['OPENAI_BASE_URL'] = model['base_url']
            environment['CUDA_VISIBLE_DEVICES'] = str(model.get('cuda_visible_devices', ''))
            log = control / f'arm{index:02d}.log'
            command = [model.get('python', sys.executable), '-u', '-m', 'evaluation.t2i.v2.t2i_v2_eval_pipeline',
                       '--run', run.name, '--generate-model', str(index)]
            with log.open('ab') as output:
                process = subprocess.Popen(command, env=environment, cwd=directory.parents[2],
                                           stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT)
                return process.wait(), str(log)
        with ThreadPoolExecutor(max_workers=settings['arm_concurrency']) as pool:
            futures = {pool.submit(execute, i): i for i, model in enumerate(settings['answers']) if model['enabled']}
            for arm in arms:
                if arm['state'] == 'queued':
                    arm['state'] = 'submitted'
            records.save(state)
            for future in as_completed(futures):
                index = futures[future]
                try:
                    code, log = future.result()
                    arms[index].update(state='finished' if code == 0 else 'failed', exit_code=code, log=log)
                except Exception as error:
                    arms[index].update(state='failed', error=str(error))
                records.save(state)
                print(json.dumps({'arm': index, **arms[index]}, ensure_ascii=False), flush=True)
        # 最终交付只合并本轮所有作答路；中途失败保留各路固定快照和未完成分母。
        if all(arm['state'] == 'finished' for arm in arms):
            stage = 'answers' if settings.get('parallel_answers') else 'scores_b'
            refs = [{'uri': arm[stage], 'version': lance.dataset(arm[stage]).version} for arm in arms]
            final = data.read_lance(**refs[0]).union(*[data.read_lance(**ref) for ref in refs[1:]])
            final.write_lance(target_uri, mode='overwrite', schema=ANSWERS if settings.get('parallel_answers') else PAIRED_SCORES)
            complete_count = (final.filter(lambda row: row['status'] == 'generated').count()
                if settings.get('parallel_answers') else
                final.filter(lambda row: row['a_status'] == 'scored' and row['b_status'] == 'reviewed').count())
            state.update(complete=complete_count == state['expected_answers'], phase='finished',
                         counts={'generated' if settings.get('parallel_answers') else 'fully_scored': complete_count, 'total': final.count()},
                         target={'uri': target_uri, 'version': lance.dataset(target_uri).version})
        else:
            state['phase'] = 'stopped_with_errors' if any(arm['state'] == 'failed' for arm in arms) else 'waiting_service'
        records.save(state)
        print(json.dumps(state, ensure_ascii=False), flush=True)
        return state


def run_codex_comparison(run, spec):
    """冻结失败题路 → 标准离线判分请求 → Codex 响应回填 → 原算分 → 同run对比。"""
    from .operators import codex_judging
    from demiflow.execution.artifacts import immutable
    root, run = resolve_root(), Path(run).absolute()
    if run.parent != root / DATASETS or run.suffix or run.name == spec['base_run']:
        raise ValueError('Codex comparison requires a distinct run under datasets')
    directory = root / 'demiwtg/evaluation/t2i/v2/runs' / run.name
    control = root / '_demiflow/evaluation_t2i_v2' / run.name
    base_records = RunTables(root, str(DATASETS / f'records__{spec["base_run"]}.lance'))
    base_manifest, base_state = base_records.load_manifest(), base_records.load()
    if not base_manifest or not base_state or not base_state.get('complete'):
        raise ValueError('Codex comparison requires a completed D7 base run')
    ids = base_manifest.get('retry_task_ids_by_arm')
    if spec.get('selection') == 'uncovered':
        covered = set()
        for ref in spec.get('reuse_sources', []):
            prior = data.read_lance(**ref).take(601)
            if len(prior) > 600:
                raise ValueError('Codex reuse row budget exceeded')
            if any(row.get('d_status') != 'reviewed' for row in prior):
                raise ValueError('Uncovered selection requires completed reuse sources')
            covered.update(codex_judging.identity(row) for row in prior)
        ids = [[task for task in base_manifest['task_ids'] if (task, mode) not in covered]
               for mode in ('text_only', 'positive_images')]
    if not ids or sum(map(len, ids)) != spec['expected_requests']:
        raise ValueError('Selected scope differs from explicit Codex request budget')
    frozen_prompt = base_manifest['config']['d_evaluation']['judge_prompt_pack']
    if frozen_prompt['prompts']['judge_d']['version'] != 't2i-v2-d-judge-7-review':
        raise ValueError('Codex comparison requires D7')
    sources = [base_state['outputs'][f'arm{i}'] for i in range(2)]
    cases = [{'case': f'case{n + 1:02d}', 'task_id': task, 'answer_mode': mode}
             for n, (mode, task) in enumerate((mode, task) for mode, arm_ids in
                zip(('text_only', 'positive_images'), ids) for task in arm_ids)]
    manifest = {'config': spec, 'sources': sources, 'cases': cases, 'prompt_pack': frozen_prompt,
                'model': 'gpt-6-astra', 'reasoning_effort': 'xhigh'}
    with run_lock(control):
        directory.mkdir(parents=True, exist_ok=True)
        immutable(directory / 'manifest.json', manifest)
        submitted = codex_judging.submit_available(root, directory, manifest)
        judge = {**base_manifest['config']['judge'], 'mode': 'offline', 'model': 'codex/gpt-6-astra'}
        pack, options, _ = d_prompt(frozen_prompt, 'judge_d', judge, root, run.name, 0)
        by_key = {codex_judging.identity(entry): entry for entry in cases}
        source = data.read_lance(**sources[0]).union(data.read_lance(**sources[1]))
        fresh_target = str(root / DATASETS / f'scores_d__{run.name}__new.lance')
        stats = (source.filter(lambda r: codex_judging.identity(r) in by_key)
            .map(partial(codex_judging.prepare, root=root, cases=by_key))
            .map_prompt_async('judge_d', config=pack, options=options, max_requests=0,
                inputs={'payload': 'd_payload', 'images': 'd_images'}, output='d_result',
                call_output='d_call', error_output='d_error', concurrency=3)
            .map(partial(codex_judging.export_input, root=root, directory=directory))
            .map(d_evaluation.finish_score)
            .save_lance(fresh_target, schema=D_SCORES, key=['task_id', 'answer_mode'],
                        stage='codex_scores', max_batch=1, queue_depth=1).run_stream())
        rows = data.read_lance(**stats.outputs['codex_scores']).take(spec['expected_requests'] + 1)
        if len(rows) != spec['expected_requests']:
            raise ValueError('Codex request row count differs from frozen scope')
        for ref in spec.get('reuse_sources', []):
            reused = data.read_lance(**ref).take(601)
            if len(reused) > 600:
                raise ValueError('Codex reuse row budget exceeded')
            rows.extend(reused)
        base_rows = source.take(601)
        summary = codex_judging.comparison_summary(base_rows, rows)
        target = str(root / DATASETS / f'scores_d__{run.name}.lance')
        data.from_arrow(pa.Table.from_pylist(rows, schema=D_SCORES)).write_lance(
            target, mode='overwrite', schema=D_SCORES)
        reference = {'uri': target, 'version': lance.dataset(target).version}
        result = {'run': run.name, 'base_run': spec['base_run'], 'submitted': submitted,
                  'requested': spec['expected_requests'], 'target': reference, 'summary': summary}
        result['complete'] = all(arm['reviewed'] == arm['expected'] for arm in summary['arms'])
        result['phase'] = 'complete' if result['complete'] else 'awaiting_codex_responses'
        from .operators.notebook import _save_json
        _save_json(directory / 'verification.json', result)
        _save_json(root / '_demiflow/evaluation_t2i_v2' / spec['base_run'] / 'codex_comparison.json',
                   {'run': run.name, 'target': reference, 'sources': sources,
                    'manifest': str(directory / 'manifest.json'), 'requested': spec['expected_requests']})
        return result


def d_prompt(spec, task, judge, root, name, maximum):
    """使用 config 冻结的标准模板；磁盘模板后续变化不改变本轮实际请求。"""
    spec = json.loads(json.dumps(spec))
    cli = judge['mode'] == 'codex_exec'
    spec['prompts'][task]['model'].update(name='gpt-6-astra' if cli else judge['model'],
                                        base_url=judge['base_url'], api_key_env=judge['api_key_env'])
    pack = parse_prompt_pack(yaml.safe_dump(spec, allow_unicode=True, sort_keys=False))
    calls = {'root': str(root), 'relative_uri': str(DATASETS / f'calls_{task}__{name}.lance')}
    journal = {**sqlite_call_options(**calls), 'max_requests': maximum}
    if judge['mode'] == 'offline':
        options = {'offline_store': journal}
    elif cli:
        options = {'codex_exec': judge['codex_exec'], 'sqlite_journal': sqlite_call_options(**calls),
                   'timeout_s': judge['timeout_s']}
    else:
        os.environ.setdefault(judge['api_key_env'], 'anything')
        options = {'sqlite_journal': journal, 'timeout_s': judge['timeout_s'], 'verify_model': False,
            'trust_env': False, 'require_finish_reason_stop': True,
            'request_options': {'max_tokens': judge['max_output_tokens'], 'response_format': {'type': 'json_object'},
                                **({'reasoning_effort': judge['reasoning_effort']} if judge.get('reasoning_effort') else {})}}
        if judge.get('stream'):
            options.update(stream=True, stream_include_usage=judge.get('stream_include_usage', True))
    definition = pack.prompt_definitions[task]
    available = judge['max_context_chars'] - len(definition.template.source) - len(json.dumps(definition.response_schema))
    if available <= 0:
        raise ValueError('D complete prompt and schema exceed declared context budget')
    return pack, options, available


def _retry_failed_task_ids(root, base_run, limit, expected_source, arm_count=2):
    """Return only the prior HTTP-408 task IDs, grouped by answer arm.

    The retry run is a new immutable run. It never changes the original tables;
    its output is merged with the original 20-row arm tables after the retry.
    """
    if not isinstance(base_run, str) or Path(base_run).name != base_run or base_run in {'.', '..'}:
        raise ValueError('retry_failed_from must be a single run name')
    base_records = RunTables(root, str(DATASETS / f'records__{base_run}.lance'))
    base_manifest = base_records.load_manifest()
    if not base_manifest or base_manifest.get('scoring_scheme') != 'D':
        raise ValueError('retry source must be an existing D run manifest')
    if base_manifest.get('question_source') != expected_source:
        raise ValueError('retry source question table/version differs from the new run')
    task_ids = set(base_manifest.get('task_ids', []))
    result = []
    for index in range(arm_count):
        path = root / DATASETS / f'scores_d__{base_run}__arm{index:02d}.lance'
        if not path.exists():
            raise ValueError(f'retry source arm table is missing: {path}')
        rows = data.read_lance(uri=str(path), version=lance.dataset(str(path)).version).take(limit + 1)
        if len(rows) > limit or {row['task_id'] for row in rows} != task_ids:
            raise ValueError('retry source arm table does not match its frozen cohort')
        failed = sorted(row['task_id'] for row in rows
                        if row.get('d_status') == 'failed' and '408' in (row.get('d_reason') or ''))
        result.append(failed)
    if not any(result):
        raise ValueError('retry source has no HTTP-408 failed rows')
    return result, base_manifest


def run_d_evaluation(run, source, settings, *, target_uri=None, write_mode='overwrite'):
    """固定小批题目 → 两路已有答案各一次D评审 → 暂停；旧协议保留原准备节点。"""
    from demiflow.execution.artifacts import immutable
    from .operators import d_full, codex_judging
    root, run = resolve_root(), Path(run).absolute()
    cfg, judge = settings['d_evaluation'], settings['judge']
    limit = cfg['question_limit']
    prompt_version = cfg['judge_prompt_pack']['prompts']['judge_d']['version']
    protocol = {'t2i-v2-d-judge-6-review': 'd6', 't2i-v2-d-judge-7-review': 'd7'}.get(prompt_version, 'legacy')
    single_pass = protocol != 'legacy'
    question_schema = d_evaluation.D7_QUESTION_SCHEMA if protocol == 'd7' else d_evaluation.QUESTION_SCHEMA
    if run.parent != root / DATASETS or run.suffix or write_mode != 'overwrite':
        raise ValueError('D requires a suffix-free run under datasets and overwrite delivery')
    if type(source.get('version')) is not int or source['version'] < 1:
        raise ValueError('D requires a fixed question version')
    source = {'uri': str((root / source['uri']).resolve()), 'version': source['version']}
    target_uri = str((root / target_uri).resolve()) if target_uri else str(run.parent / f'scores_d__{run.name}.lance')
    control = root / '_demiflow/evaluation_t2i_v2' / run.name
    records = RunTables(root, str(DATASETS / f'records__{run.name}.lance'))
    retry_from = cfg.get('retry_failed_from')
    retry_task_ids_by_arm = None
    retry_base_manifest = None
    if retry_from:
        retry_task_ids_by_arm, retry_base_manifest = _retry_failed_task_ids(
            root, retry_from, limit, source, arm_count=len(cfg['answers']))
    with run_lock(control):
        # 固定版本的前 N 行先形成独立范围，不按答案表现、可评分性或旧分数挑题。
        selected = data.read_lance(**source, columns=question_schema.names).limit(limit).take(limit + 1)
        if len(selected) != limit:
            raise ValueError('D source has fewer questions than the explicitly requested sample')
        data.from_items(selected).map(ValidateQuestions(limit)).count()
        if protocol == 'd7':
            data.from_items(selected).map(d_evaluation.validate_review_question).count()
        scope_uri = str(run.parent / f'd_questions__{run.name}.lance')
        if cfg.get('shared_source'):
            if data.read_lance(**source, columns=['task_id']).count() != limit:
                raise ValueError('Shared D7 source must exactly match the declared full scope')
            scope = source
        else:
            if not Path(scope_uri).exists():
                data.read_lance(**source, columns=question_schema.names).limit(limit).write_lance(
                    scope_uri, mode='create', schema=question_schema)
            scope = {'uri': scope_uri, 'version': 1}
        directory = Path(__file__).parent
        paths = [directory / 't2i_v2_eval_pipeline.py', directory / 'operators/d_evaluation.py',
                 directory / 'operators/d_scores.py', directory / 'operators/d_rubric.py', directory / 'operators/images.py',
                 directory / 'operators/notebook.py', directory / 'operators/d_full.py',
                 directory / 'operators/codex_judging.py']
        manifest = {'source': scope, 'question_source': source, 'task_ids': [r['task_id'] for r in selected],
                    'target_uri': target_uri, 'write_mode': write_mode, 'config': settings,
                    'expected_questions': limit, 'scoring_scheme': 'D',
                    'retry_failed_from': retry_from,
                    'retry_task_ids_by_arm': retry_task_ids_by_arm,
                    'code': {str(p): p.read_text() for p in paths}}
        previous_manifest = records.load_manifest()
        if previous_manifest and previous_manifest != manifest:
            if {k: v for k, v in previous_manifest.items() if k != 'code'} != {
                    k: v for k, v in manifest.items() if k != 'code'}:
                raise ValueError('Frozen D7 scope/configuration changed; use a new run')
            immutable(control / ('resume_code_' + digest(manifest['code']) + '.json'),
                      {'original_code_sha256': digest(previous_manifest['code']), 'code': manifest['code'],
                       'reason': 'Same frozen inputs/configuration; standard model journal preserves request identity'})
            manifest = previous_manifest
        records.save_manifest(manifest)
        prior = records.load()
        if prior and prior.get('phase') == 'paused_after_sample':
            return prior
        codex_directory = root / 'demiwtg/evaluation/t2i/v2/runs' / run.name
        if cfg.get('codex_export'):
            reused_keys = set()
            for ref in cfg.get('reuse_scores', []):
                reused_keys.update((r['task_id'], r['answer_mode']) for r in data.read_lance(
                    **ref, columns=['task_id', 'answer_mode']).take(limit * 2 + 1))
            cases = [{'case': d_full.case_name({'task_id': task, 'answer_mode': mode}),
                      'task_id': task, 'answer_mode': mode}
                     for mode in [m['answer_mode'] for m in cfg['answers']] for task in manifest['task_ids']
                     if (task, mode) not in reused_keys]
            codex_manifest = {'cases': cases, 'model': 'gpt-6-astra', 'reasoning_effort': 'xhigh',
                              'source': source, 'config': settings,
                              'configured_concurrency_per_arm': judge['concurrency'],
                              'actual_subagent_slots_total': cfg.get('actual_subagent_slots', 3)}
            codex_directory.mkdir(parents=True, exist_ok=True)
            immutable(codex_directory / 'manifest.json', codex_manifest)
            codex_judging.submit_available(root, codex_directory, codex_manifest)
        arms = [{'index': i, 'model': m['model'], 'answer_mode': m['answer_mode'],
                 'scores_d': str(run.parent / f'scores_d__{run.name}__arm{i:02d}.lance')}
                for i, m in enumerate(cfg['answers'])]
        retry_expected = [len(ids) for ids in retry_task_ids_by_arm] if retry_task_ids_by_arm else [limit] * 2
        state = {'complete': False, 'phase': 'd_scoring' if single_pass else 'd_preparing_core', 'expected_questions': limit,
                 'expected_answers': sum(retry_expected), 'arms': arms}
        records.save(state)
        print(json.dumps({'run': run.name, 'phase': state['phase'], 'questions': limit,
                          **({} if single_pass else {'core_request_budget': limit}),
                          'judge_request_budget': sum(retry_expected),
                          'retry_failed_from': retry_from,
                          'retry_expected_by_arm': retry_expected}, ensure_ascii=False), flush=True)
        if single_pass:
            question_ref = scope
            state['outputs'] = {}
        else:
            frozen_core = control / 'core_snapshot.json'
            if frozen_core.exists():
                from demiflow.execution.artifacts import read
                core_ref = read(frozen_core)
            else:
                core_pack, core_options, core_chars = d_prompt(cfg['core_prompt_pack'], 'prepare_d_core', judge, root, run.name, limit)
                core_uri = str(run.parent / f'd_core__{run.name}.lance')
                core_stats = (data.read_lance(**scope, batch_size=1)
                    .map(partial(d_evaluation.prepare_core, available_chars=core_chars,
                        legacy=True))
                    .map_prompt_async('prepare_d_core', config=core_pack, options=core_options,
                        inputs={'payload': 'core_payload'}, output='core_result', call_output='core_call', error_output='core_error',
                        when=lambda r: r['core_status'] == 'ready', max_requests=limit,
                        concurrency=judge['concurrency'], queue_depth=judge['concurrency'])
                    .map(d_evaluation.finish_core)
                    .save_lance(core_uri, schema=d_evaluation.CORE_SCHEMA, key='task_id', stage='d_core', max_batch=1, queue_depth=1)
                    .map(lambda r: print(json.dumps({'phase': 'core', 'concept': r['concept'], 'status': r['core_status']}, ensure_ascii=False), flush=True) or r)
                    .run_stream(log_every=0))
                core_ref = core_stats.outputs['d_core']
            core_rows = data.read_lance(**core_ref).take(limit + 1)
            if len(core_rows) != limit:
                raise ValueError('D core preparation did not retain the complete fixed cohort')
            state['outputs'] = {'d_core': core_ref}
            if any(r['core_status'] == 'pending' for r in core_rows):
                state['phase'] = 'awaiting_core_responses'
                records.save(state)
                return state
            immutable(control / 'core_snapshot.json', core_ref)
            question_ref = core_ref
        state['phase'] = 'd_scoring'
        records.save(state)

        def evaluate_arm(index):
            model = cfg['answers'][index]
            arm_task_ids = set(retry_task_ids_by_arm[index]) if retry_task_ids_by_arm else set(manifest['task_ids'])
            arm_limit = len(arm_task_ids)
            if not arm_task_ids:
                raise ValueError(f'no retry task IDs for arm {index}')
            pack, options, available = d_prompt(cfg['judge_prompt_pack'], 'judge_d', judge, root,
                                                f'{run.name}__arm{index:02d}', arm_limit)
            answers_ref = {**model['source'], 'uri': str((root / model['source']['uri']).resolve())}
            selected_ids = set(manifest['task_ids'])
            answer_sources = [{'source': answers_ref, 'generation_config': model.get('generation_config')},
                              *model.get('additional_sources', [])]
            branches = [data.read_lance(**item['source']).filter(lambda r: r['task_id'] in selected_ids)
                        .map(partial(d_full.tagged_answer, source=item['source'],
                                     generation_config=item.get('generation_config'))) for item in answer_sources]
            answers = branches[0].union(*branches[1:]).materialize() if len(branches) > 1 else branches[0].materialize()
            duplicate = answers.reduce_by_key('task_id', lambda acc, r: {
                'task_id': r['task_id'], 'count': (acc['count'] if acc else 0) + 1}).filter(lambda r: r['count'] > 1).take(1)
            if duplicate:
                raise ValueError('D selected answer identities are not unique')
            answers = answers.map(lambda r: {'task_id': r['task_id'], 'prior_answer': r})
            progress = DStreamProgress(root, run.name, index, arm_limit, cfg.get('max_consecutive_failures'))
            question_rows = data.read_lance(**question_ref, batch_size=1).filter(
                lambda row: row['task_id'] in arm_task_ids)
            prepared = (question_rows.join(answers, on='task_id', how='left').map(d_full.audit_answer)
                .map(d_evaluation.prepare_score, fn_kwargs={'root': root, 'model': model, 'judge': judge,
                     'available_chars': available, 'single_pass': single_pass, 'protocol': protocol}))
            reuse_branches = [data.read_lance(**ref).filter(lambda r: r.get('d_status') == 'reviewed'
                              and r['task_id'] in arm_task_ids
                              and r['answer_mode'] == model['answer_mode'] and r['answer_model'] == model['model'])
                              for ref in cfg.get('reuse_scores', [])]
            if reuse_branches:
                reused = reuse_branches[0].union(*reuse_branches[1:]) if len(reuse_branches) > 1 else reuse_branches[0]
                reused = reused.reduce_by_key('task_id', d_full.one_score)
                prepared = prepared.join(reused.map(lambda r: {'task_id': r['task_id'], 'prior_score': r}),
                                         on='task_id', how='left').map(partial(d_full.reuse_score, judge=judge))
            if cfg.get('reuse_initial_scores') and model['answer_mode'] == 'imagerag':
                initial_branches = [data.read_lance(**ref).filter(lambda r: r.get('d_status') == 'reviewed'
                    and r['task_id'] in arm_task_ids and r['answer_mode'] == 'text_only'
                    and r['answer_model'] + '+ImageRAG' == model['model']) for ref in cfg.get('reuse_scores', [])]
                if initial_branches:
                    initial_scores = initial_branches[0].union(*initial_branches[1:]).reduce_by_key('task_id', d_full.one_score)
                    prepared = prepared.join(initial_scores.map(lambda r: {
                        'task_id': r['task_id'], 'initial_score': r}), on='task_id', how='left').map(
                        partial(d_full.reuse_initial_score, judge=judge))
            judged = prepared.map_prompt_async('judge_d', config=pack, options=options,
                    inputs={'payload': 'd_payload', 'images': 'd_images'},
                    output='d_result', call_output='d_call', error_output='d_error',
                    when=lambda r: r['d_status'] == 'ready', max_requests=arm_limit, label='judge_d',
                    concurrency=judge['concurrency'], queue_depth=judge['concurrency'])
            if cfg.get('codex_export'):
                judged = judged.map(partial(d_full.export_codex, root=root, directory=codex_directory))
            stats = (judged
                .map(d_evaluation.finish_score)
                .save_lance(arms[index]['scores_d'], schema=D_SCORES, key='task_id', stage='scores_d', max_batch=1, queue_depth=1)
                .map(progress.report_result)
                .run_stream(log_every=1, on_progress=progress.progress, on_drain=progress.drained))
            progress.finished(stats)
            return stats.outputs['scores_d']

        with ThreadPoolExecutor(max_workers=cfg.get('arm_concurrency', 2)) as pool:
            futures = [pool.submit(evaluate_arm, index) for index in range(2)]
            refs = [future.result() for future in futures]
        retry_rows = [data.read_lance(**ref).take((len(retry_task_ids_by_arm[i]) if retry_task_ids_by_arm else limit) + 1)
                      for i, ref in enumerate(refs)]
        if any(len(arm) != (len(retry_task_ids_by_arm[i]) if retry_task_ids_by_arm else limit)
               for i, arm in enumerate(retry_rows)):
            raise ValueError('D retry results do not match the retry task IDs')
        rows = retry_rows
        if retry_from:
            # The retry run owns a complete merged 20-row snapshot for each arm;
            # valid prior rows are retained, retry rows replace only prior failures.
            merged_refs = []
            merged_rows = []
            for index, retry_arm in enumerate(retry_rows):
                base_path = root / DATASETS / f'scores_d__{retry_from}__arm{index:02d}.lance'
                base_ref = {'uri': str(base_path), 'version': lance.dataset(str(base_path)).version}
                base_rows = data.read_lance(**base_ref).take(limit + 1)
                by_id = {row['task_id']: row for row in base_rows}
                by_id.update({row['task_id']: row for row in retry_arm})
                if len(by_id) != limit or set(by_id) != set(manifest['task_ids']):
                    raise ValueError('Merged D retry arm does not match the fixed cohort')
                ordered = [by_id[task_id] for task_id in manifest['task_ids']]
                merged_uri = arms[index]['scores_d']
                data.from_arrow(pa.Table.from_pylist(ordered, schema=D_SCORES)).write_lance(
                    merged_uri, mode='overwrite', schema=D_SCORES)
                merged_ref = {'uri': merged_uri, 'version': lance.dataset(merged_uri).version}
                merged_refs.append(merged_ref)
                merged_rows.append(ordered)
            refs = merged_refs
            rows = merged_rows
        else:
            if any(len(arm) != limit or {r['task_id'] for r in arm} != set(manifest['task_ids']) for arm in rows):
                raise ValueError('D results do not match the fixed paired cohort')
        final = data.read_lance(**refs[0]).union(data.read_lance(**refs[1]))
        final.write_lance(target_uri, mode='overwrite', schema=D_SCORES)
        target = {'uri': target_uri, 'version': lance.dataset(target_uri).version}
        summaries = {model['answer_mode']: d_evaluation.summarize(arm, limit) for model, arm in zip(cfg['answers'], rows)}
        matched = d_evaluation.paired_summary(rows)
        reviewed = sum(r['d_status'] == 'reviewed' for arm in rows for r in arm)
        pending = any(r['d_status'] == 'pending' for arm in rows for r in arm)
        state.update(complete=reviewed == limit * 2,
                     phase='awaiting_d_responses' if pending else ('paused_after_retry' if retry_from else 'paused_after_sample'),
                     counts={'reviewed': reviewed, 'expected': limit * 2,
                             'retry_expected': sum(retry_expected),
                             'retry_reviewed': sum(r.get('d_status') == 'reviewed' for arm in retry_rows for r in arm)},
                     counts_by_arm=summaries, target=target,
                     outputs={**state['outputs'], 'arm0': refs[0], 'arm1': refs[1]})
        records.save(state)
        if not pending:
            reason = (f'补评{sum(retry_expected)}条原HTTP408失败题路，已合并已有有效结果；不扩展范围。'
                      if retry_from else f'用户要求只评同一批{limit}题的已有两路答案，已到范围终点，不扩展运行。')
            immutable(control / 'pause_requested.json', {'reason': reason})
        if not pending:
            immutable(control / 'd_comparison.json', {'state': state, 'paired_dimensions': matched, 'task_ids': manifest['task_ids']})
        print(json.dumps({'phase': state['phase'], 'summary': summaries, 'paired': matched}, ensure_ascii=False), flush=True)
        return state


def view_score_table(run_id, settings):
    """现有只读查看入口的逐题分数表；只生成HTML/CSV/JSON导出，不写评分表。"""
    from .operators import score_table
    from datetime import datetime
    from zoneinfo import ZoneInfo
    root = resolve_root()
    if Path(run_id).name != run_id or run_id in {'.', '..'}:
        raise ValueError('View run must be a single directory name')
    if set(settings) != {'questions', 'ab_scores', 'd_scores', 'question_limit'}:
        raise ValueError('Score table requires three fixed sources and a question_limit')
    limit = settings['question_limit']
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError('Score table question_limit must be 1..100')
    sources = {}
    for key in ('questions', 'ab_scores', 'd_scores'):
        ref = settings[key]
        if set(ref) != {'uri', 'version'} or type(ref['version']) is not int or ref['version'] < 1:
            raise ValueError('Score comparison sources require fixed positive versions')
        sources[key] = {**ref, 'uri': str((root / ref['uri']).resolve())}
    questions = data.read_lance(**sources['questions'], columns=['task_id', 'concept', 'instruction']).take(limit + 1)
    if len(questions) != limit or len({q['task_id'] for q in questions}) != limit:
        raise ValueError('Score comparison question scope differs from the configured count')
    ids = {q['task_id'] for q in questions}
    selected = lambda row: row['task_id'] in ids and row['answer_mode'] in score_table.MODES
    ab_rows = data.read_lance(**sources['ab_scores']).filter(selected).take(2 * limit + 1)
    d_rows = (data.read_lance(**sources['d_scores']).filter(selected)
              .map(score_table.calculate_d).take(2 * limit + 1))
    if max(len(ab_rows), len(d_rows)) > 2 * limit:
        raise ValueError('Score comparison source exceeds the paired scope')
    payload = score_table.build_payload(questions, ab_rows, d_rows, sources=sources)
    payload['meta']['run'] = run_id
    payload['meta']['updated_at'] = datetime.now(ZoneInfo('Asia/Shanghai')).isoformat()
    output = root / 'demiwtg/evaluation/t2i/v2/runs' / run_id
    output.mkdir(parents=True, exist_ok=True)
    artifacts = {'viewer_path': ('score_comparison.html', score_table.render(payload)),
                 'csv_path': ('per_question_scores.csv', score_table.export_csv(payload)),
                 'report_path': ('score_comparison.json', json.dumps(payload, ensure_ascii=False, indent=2) + '\n')}
    receipt = {'meta': payload['meta'], 'summaries': payload['summaries']}
    for key, (name, body) in artifacts.items():
        path = output / name
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=output,
                                         prefix='.' + name, suffix='.tmp', delete=False) as stream:
            stream.write(body)
            temporary = Path(stream.name)
        try:
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
        receipt[key] = str(path)
    return receipt


def prepare_reanswer_questions(run, baseline, settings, *, target_uri):
    """从固定 Review 快照按优先级交付最新题表和改题子集；不调用模型。"""
    from evaluation.t2i.v2.operators import reanswer
    root = resolve_root()
    refs = settings['review_sources']
    cohort = settings['cohort_source']
    if not 1 <= len(refs) <= 8:
        raise ValueError('Specify 1..8 ordered Review sources')
    for ref in [baseline, cohort, *refs]:
        if type(ref.get('version')) is not int or ref['version'] < 1:
            raise ValueError('Reanswer inputs require fixed versions')
    expected = settings['expected_ready']
    changed_expected = settings['expected_changed']
    if not 1 <= expected <= 300 or not 0 <= changed_expected <= expected:
        raise ValueError('Reanswer question budget must be within 300')
    refs = [{**ref, 'uri': str((root / ref['uri']).resolve())} for ref in refs]
    baseline = {**baseline, 'uri': str((root / baseline['uri']).resolve())}
    cohort = {**cohort, 'uri': str((root / cohort['uri']).resolve())}
    target_uri = str((root / target_uri).resolve())
    all_uri = str(Path(target_uri).with_name('latest_' + Path(target_uri).name))
    with run_lock(root / '_demiflow/evaluation_t2i_v2' / Path(run).name):
        records = RunTables(root, str(DATASETS / f'records__{Path(run).name}.lance'))
        records.save_manifest({'source': baseline, 'config': settings, 'target_uri': target_uri})
        if saved := records.load():
            return saved
        branches = [data.read_lance(**ref, columns=reanswer.QUESTION_FIELDS, batch_size=1)
            .map(partial(reanswer.tag_review, priority=priority, source=ref))
            for priority, ref in enumerate(refs)]
        latest = (branches[0].union(*branches[1:]).reduce_by_key('concept', reanswer.latest_review)
            .filter(lambda row: row['review_status'] == 'ready')
            .join(data.read_lance(**baseline, columns=['concept', 'task_id', 'instruction'])
                .map(reanswer.baseline_question), on='concept', how='left')
            .join(data.read_lance(**cohort, columns=['concept', 'selection_rank']), on='concept', how='left')
            .map(reanswer.revision_question).materialize())
        ready_count = latest.count()
        changed = latest.filter(lambda row: row['requires_new_answer'])
        changed_count = changed.count()
        if (ready_count, changed_count) != (expected, changed_expected):
            raise ValueError(f'Reanswer scope differs: ready={ready_count}, changed={changed_count}')
        schema = reanswer.question_schema(lance.dataset(**refs[0]).schema)
        latest.write_lance(all_uri, mode='overwrite', schema=schema)
        changed.write_lance(target_uri, mode='overwrite', schema=schema)
        state = {'complete': True, 'phase': 'prepared',
            'counts': {'ready': ready_count, 'changed': changed_count, 'unchanged': ready_count-changed_count},
            'outputs': {'latest_questions': {'uri': all_uri, 'version': lance.dataset(all_uri).version}},
            'target': {'uri': target_uri, 'version': lance.dataset(target_uri).version}}
        records.save(state)
        return state


def main():
    """CLI 与 notebook 共用入口；模型子进程只读取父进程已冻结的配置。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', required=True)
    parser.add_argument('--table')
    parser.add_argument('--version', type=int)
    parser.add_argument('--config', help='JSON file containing answers or judge')
    parser.add_argument('--stage', choices=['answers', 'judge', 'd', 'view', 'monitor', 'reanswer-source', 'codex-compare'], default='answers')
    parser.add_argument('--view-runs', nargs='+', help='Read-only comparison of 1..8 submitted runs; requires --stage view')
    parser.add_argument('--score-table-config', help='Fixed A/B/D sources for per-question dimension columns; requires --stage view')
    parser.add_argument('--target', help='Output table URI (required for judge)')
    parser.add_argument('--write-mode', choices=['append', 'overwrite'], default='overwrite')
    parser.add_argument(
        '--generate-model', type=int, help='Execute model at this index in the frozen configuration'
    )
    args = parser.parse_args()
    root = resolve_root()
    if args.stage == 'monitor':
        if any(value is not None for value in (args.table, args.version, args.config, args.target,
                args.generate_model, args.view_runs, args.score_table_config)):
            parser.error('--stage monitor accepts only --run; it cannot submit model work')
        from evaluation.t2i.v2.operators.notebook import monitor_run, render_monitor, _save_json
        report = monitor_run(root / 'demiwtg', args.run)
        output = root / 'demiwtg/evaluation/t2i/v2/runs' / args.run / 'monitor.html'
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(render_monitor(report), encoding='utf-8')
        _save_json(output.with_suffix('.json'), report)
        print(json.dumps({'run': args.run, 'monitor_path': str(output), 'found': report['found']}, ensure_ascii=False))
        return
    if args.stage == 'view':
        # notebook 每次通过本入口启动独立只读进程，避免长期 kernel 使用缓存的旧查看代码。
        if bool(args.view_runs) == bool(args.score_table_config) or args.generate_model is not None or any(
                value is not None for value in (args.table, args.version, args.config, args.target)):
            parser.error('--stage view requires either --view-runs or --score-table-config and cannot include model execution arguments')
        if Path(args.run).name != args.run or args.run in {'.', '..'}:
            parser.error('View run must be a single directory name')
        if args.score_table_config:
            settings = json.loads(Path(args.score_table_config).read_text())
            print(json.dumps(view_score_table(args.run, settings), ensure_ascii=False))
            return
        from evaluation.t2i.v2.operators.case_viewer import build_comparison_browser
        project = root / 'demiwtg'
        document, metadata = build_comparison_browser(project, args.view_runs)
        output = project / 'evaluation/t2i/v2/runs' / args.run / 'case_browser.html'
        output.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=output.parent,
                                             prefix='.case_browser.', suffix='.tmp', delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(document)
            temporary.replace(output)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        print(json.dumps({'viewer_path': str(output), 'meta': metadata}, ensure_ascii=False))
        return
    if args.view_runs or args.score_table_config:
        parser.error('--view-runs and --score-table-config require --stage view')
    run = root / DATASETS / args.run
    if args.generate_model is not None:
        manifest = RunTables(root, str(DATASETS / f'records__{run.name}.lance')).load_manifest()
        model = manifest['config']['answers'][args.generate_model]
        if manifest['config'].get('stream_judging') or manifest['config'].get('parallel_answers'):
            run_paired_arm(run, manifest, args.generate_model)
        else:
            run_answers(run, manifest['source'], model)
        return
    if args.stage == 'codex-compare':
        if not args.config or any(v is not None for v in (args.table, args.version, args.target, args.generate_model)):
            parser.error('codex-compare accepts only --run and --config')
        settings = json.loads(Path(args.config).read_text())
        result = run_pipeline(run, None, config(codex_comparison=settings['codex_comparison']))
        print(json.dumps(result, ensure_ascii=False))
        return
    if not args.table or args.version is None or not args.config:
        parser.error('--table, --version and --config are required')
    settings = json.loads(Path(args.config).read_text())
    source = {'uri': args.table, 'version': args.version}
    if args.stage == 'reanswer-source':
        if not args.target:
            parser.error('--target is required for reanswer-source')
        state = prepare_reanswer_questions(run, source, settings, target_uri=args.target)
    elif args.stage == 'd':
        settings = config(judge_model=settings['judge'], d_evaluation=settings['d_evaluation'])
        state = run_pipeline(run, source, settings, target_uri=args.target, write_mode=args.write_mode)
    elif args.stage == 'answers':
        settings = config(answer_models=settings['answers'], judge_model=settings.get('judge'),
            **{key: settings[key] for key in ('stream_judging', 'parallel_answers', 'arm_concurrency', 'max_questions', 'max_consecutive_failures') if key in settings})
        state = run_pipeline(run, source, settings, target_uri=args.target, write_mode=args.write_mode)
    else:
        settings = config(judge_model=settings['judge'])
        if not args.target:
            parser.error('--target is required for judge')
        state = run_judging(run, source, settings, target_uri=args.target, write_mode=args.write_mode)
    print(json.dumps(state, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
