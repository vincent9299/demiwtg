"""config → run_pipeline：Codex 编辑出题 → 可选本地 Qwen 作答和 GPT 联合评审。

CLI 与 notebook 共用唯一入口。只读公共图文和可选原图池；所有写入属于 Edit V2。
输入/设计每概念一行，候选每题一行；prompt 和业务响应规则由本目录维护。
"""
from demiflow.operator_llm.call_ref import journal_options as sqlite_call_options
import argparse
import json
from pathlib import Path
from time import perf_counter

import lance
import pyarrow as pa
from demiflow import data
from demiflow.agent import load_agent_config
from demiflow.operator_api import definitions, tool_definition
from demiflow.execution.artifacts import digest, run_lock
from demiflow.operator_llm.parser import load_prompt_pack
from demiflow.objects import LocalObjectStore, ObjectRef
from project import resolve_root
from benchmark.edit.v2.operators.authoring import prepare_request, check_response
from benchmark.edit.v2.operators.images import image_object_ref
from benchmark.edit.v2.operators.materials import cohort_input
from benchmark.edit.v2.operators.probe import (
    probe_config, candidate_row, answer_input, AnswerImage, prepare_review, check_review,
    GENERATIONS, REVIEWS,
)

DATASETS = Path('demiwtg/benchmark/edit/v2/datasets')
PROMPTS = Path(__file__).parent / 'prompts'
BLOB = pa.struct([('uri', pa.string()), ('sha256', pa.string())])
REF = pa.struct([('uri', pa.string()), ('version', pa.int64())])
CHECK = pa.struct([('check', pa.int64()), ('passed', pa.bool_()), ('evidence', pa.string())])
SOURCE_CHECK = pa.struct([('status', pa.string()), ('observations', pa.list_(CHECK)), ('reason', pa.string())])
QUESTION = pa.struct([
    ('seed_image', pa.int64()), ('seed_image_id', pa.string()), ('source_image_edit', pa.string()),
    ('source_image_checks', pa.list_(pa.string())), ('source_artifact', pa.string()),
    ('source_image_check', SOURCE_CHECK), ('instruction', pa.string()),
    ('test_points', pa.list_(pa.struct([('point', pa.string()), ('basis', pa.string()), ('criterion', pa.string())]))),
])
INPUTS = pa.schema([('concept', pa.string()), ('taxonomy', pa.list_(pa.string())),
                    ('status', pa.string()), ('reason', pa.large_string()), ('references_json', pa.large_string())])
DESIGNS = pa.schema([*INPUTS, ('question', QUESTION), ('seed_asset', BLOB), ('edit_source', BLOB),
                     ('reasoning', pa.large_string()), ('call_json', pa.large_string())])
QUESTIONS = pa.schema([('task_id', pa.string()), ('concept', pa.string()), ('taxonomy', pa.list_(pa.string())),
                       ('status', pa.string()), *QUESTION, ('seed_asset', BLOB), ('edit_source', BLOB),
                       ('references_json', pa.large_string()), ('reasoning', pa.large_string()),
                       ('call_json', pa.large_string())])
SUMMARY = pa.schema([('run', pa.string()), ('status', pa.string()), ('error', pa.large_string()),
                     ('complete', pa.bool_()), ('config_json', pa.large_string()),
                     ('counts', pa.list_(pa.struct([('status', pa.string()), ('count', pa.int64())]))),
                     ('candidate_count', pa.int64()), ('inputs', REF), ('designs', REF), ('candidates', REF),
                     ('generations', REF), ('reviews', REF), ('probe_json', pa.large_string())])


def config(*, run, concepts=None, screening_source=None, cohort_source=None, sample_size=None, sample_seed=0,
           article_source=None, visual_source=None,
           target_uri=None, write_mode='overwrite', agent_config=None,
           document_resources=None, max_generation_attempts=2, max_calls=None,
           max_reference_images=8,
           concurrency=1, queue_depth=1, progress_every=1, through='author', probe=None):
    """只接固定 uri/version。显式概念、keep 抽样或固定审定名单三选一。

    初始图片只接已审核的概念正例；场景候选图由 agent 按需检索获得。
    max_calls 限制新增 Codex 会话数；max_generation_attempts 是作者指令中的工具尝试预算，
    不冒充后端硬调用配额。timeout_s 为一次完整会话时限（秒）。
    Codex 模型、工具、执行预算和任务均由单份 agent_config 提供；业务节点不覆盖。
    """
    if agent_config is None:
        raise ValueError('Edit authoring requires agent_config; no fallback authoring backend')
    agent_config = str(Path(agent_config).resolve())
    agent = load_agent_config(agent_config)
    if agent.environment.runtime != 'codex':
        raise ValueError('Edit agent_config requires runtime: codex')
    if agent.environment.resources is not None:
        raise ValueError('Edit documents are local paths in the prompt, not operator resources')
    model = agent.prompt_pack.prompt_definitions['design_question'].model.name
    max_context_chars = agent.environment.max_context_chars
    document_resources = document_resources or {}
    if (not isinstance(document_resources, dict) or len(document_resources) > 4096
            or len(json.dumps(document_resources, ensure_ascii=False)) > 8 * 1024**2):
        raise ValueError('document_resources exceeds the finite concept/resource catalog budget')
    for concept, resources in document_resources.items():
        if not isinstance(concept, str) or not isinstance(resources, dict) or len(resources) > 64:
            raise ValueError('document_resources maps concepts to at most 64 fixed local documents')
        for name, document in resources.items():
            if (not isinstance(name, str) or not name.strip() or not isinstance(document, dict)
                    or not isinstance(document.get('url'), str)):
                raise ValueError('Each document requires an identifier, document_ref and source url')
            ref = ObjectRef(**document['document_ref'])
            if not ref.uri.startswith('file:'):
                raise ValueError('Edit documents require local file snapshots for Codex to read')
            if 'eligible' in document and type(document['eligible']) is not bool:
                raise ValueError('Document eligible must be a boolean')
    for name, source in [('screening_source', screening_source), ('cohort_source', cohort_source), ('article_source', article_source),
                         ('visual_source', visual_source)]:
        if source is not None and (not isinstance(source, dict) or set(source) != {'uri', 'version'}
                or not isinstance(source['uri'], str) or not source['uri'].strip()
                or type(source['version']) is not int or source['version'] < 1):
            raise ValueError(name + ' requires a fixed uri/version')
    if cohort_source is not None:
        if (any(value is not None for value in (concepts, screening_source, article_source, visual_source))
                or document_resources or type(sample_size) is not int or not 1 <= sample_size <= 4096):
            raise ValueError('cohort_source requires sample_size (1..4096) and excludes other material sources')
    elif screening_source is not None:
        if concepts is not None or type(sample_size) is not int or sample_size < 1:
            raise ValueError('screening_source requires sample_size and excludes concepts')
    elif (sample_size is not None or not isinstance(concepts, (list, tuple)) or not concepts
          or any(not isinstance(c, str) or not c.strip() for c in concepts) or len(set(concepts)) != len(concepts)):
        raise ValueError('Select nonempty unique concepts or screening_source/sample_size')
    if type(sample_seed) is not int:
        raise ValueError('sample_seed must be an integer')
    if max_calls is None:
        max_calls = sample_size if screening_source or cohort_source else len(concepts)
        max_calls = min(max_calls, agent.max_requests)
    for value in (max_generation_attempts, max_reference_images,
                  max_context_chars, concurrency, queue_depth, progress_every):
        if type(value) is not int or value < 1:
            raise ValueError('Counts and budgets must be positive integers')
    if type(max_calls) is not int or max_calls < 0:
        raise ValueError('max_calls must be nonnegative')
    if max_calls > agent.max_requests:
        raise ValueError('max_calls can only tighten agent budgets.max_requests')
    if write_mode not in {'append', 'overwrite'}:
        raise ValueError('write_mode must be append or overwrite')
    if through not in {'author', 'probe'}:
        raise ValueError('through must be author or probe')
    if through == 'probe' and (probe is None or write_mode != 'overwrite'):
        raise ValueError('through=probe requires probe settings and run-owned overwrite outputs')
    if probe is not None:
        probe = probe_config(probe, maximum=sample_size if screening_source or cohort_source else len(concepts))
    return dict(run=str(run), concepts=list(concepts) if concepts is not None else None,
                screening_source=screening_source, cohort_source=cohort_source, sample_size=sample_size, sample_seed=sample_seed,
                article_source=article_source, visual_source=visual_source,
                target_uri=str(target_uri) if target_uri else None,
                write_mode=write_mode, mode='codex', model=model, agent_config=agent_config,
                agent_config_digest=agent.prompt_pack.content_hash,
                document_resources=document_resources,
                generate_source=bool(agent.options.get('codex_agent', {}).get('image_generation')),
                max_generation_attempts=max_generation_attempts,
                max_calls=max_calls, max_reference_images=max_reference_images, max_context_chars=max_context_chars,
                timeout_s=agent.options.get('timeout_s', 900),
                concurrency=concurrency, queue_depth=queue_depth, progress_every=progress_every,
                through=through, probe=probe)


def run_pipeline(config):
    """读公共交付而不重跑 preparation；完成落表才发布固定版本回执。

    每个概念收到全部入选种子，独立上下文一次出题；实际图片只在需要请求时按 Blob 读入。
    不足/缺图/合成失败/技术失败保留在 designs；仅原图已绑定且作者自检通过的题进入 candidates。
    """
    root = resolve_root()
    if not config.get('agent_config') or config.get('mode') != 'codex':
        raise ValueError('Edit authoring requires agent_config and the Codex agent runtime')
    agent = load_agent_config(config['agent_config'])
    if agent.prompt_pack.content_hash != config['agent_config_digest']:
        raise ValueError('agent_config changed after config(); rebuild config to use the new complete declaration')
    can_search = 'search_vectors' in agent.environment.operators
    api_configuration = []
    for name, api in definitions(agent.environment.operators)[0].items():
        api_configuration.append({**tool_definition(api),
            'fixed_arguments': agent.environment.operator_settings.get(name, {}).get('arguments', {}),
            'platform_arguments': {key: getattr(agent.environment, source[7:])
                if source.startswith('limits.') else {'bound_from': source}
                for key, source in api['bindings'].items()},
            'result_images': agent.environment.image_output(name)})
    run = Path(config['run']).resolve()
    if run.parent != (root / DATASETS).resolve() or run.suffix:
        raise ValueError('run must be a suffix-free name under ' + str(root / DATASETS))
    outputs = {name: str(run.parent / f'{name}__{run.name}.lance')
               for name in ('inputs', 'designs', 'candidates', 'summary', 'calls', 'generations', 'reviews', 'probe_calls')}
    if config['target_uri']:
        outputs['candidates'] = str((root / config['target_uri']).resolve())
    sources = {name: {'uri': str((root / source['uri']).resolve()), 'version': source['version']}
               for name in ('screening_source', 'cohort_source', 'article_source', 'visual_source')
               if (source := config[name]) is not None}
    if len(set(outputs.values())) != len(outputs) or set(outputs.values()) & {s['uri'] for s in sources.values()}:
        raise ValueError('Sources and each output table must have distinct paths')
    started = perf_counter()
    progress = {'done': 0, 'reused': 0}

    def log(message):
        print(f'[Edit V2][{run.name}][+{perf_counter() - started:.1f}s] {message}', flush=True)

    def observe(row):
        progress['done'] += 1
        progress['reused'] += bool(json.loads(row['call_json']).get('reused'))
        if progress['done'] % config['progress_every'] == 0 or row['status'] not in {'candidate', 'insufficient'}:
            log(f'已处理={progress["done"]}/{len(names)}，响应复用={progress["reused"]}；'
                f'{row["concept"]}: {row["status"]} {row["reason"]}')
        return {name: row[name] for name in DESIGNS.names}

    log(f'开始；mode={config["mode"]}，model={config["model"]}，并发={config["concurrency"]}；输出={run.parent}')
    with run_lock(root / '_demiflow' / 'benchmark_edit_v2' / run.name):
        # New criterion/probe columns require a fresh run; never silently rewrite old business schemas.
        for name, schema in [('designs', DESIGNS), ('candidates', QUESTIONS), ('summary', SUMMARY),
                             ('generations', GENERATIONS), ('reviews', REVIEWS)]:
            if Path(outputs[name]).exists() and not lance.dataset(outputs[name]).schema.equals(schema, check_metadata=False):
                raise ValueError('Incompatible existing ' + name + ' schema; use a new run/target')
        # 本次开始即替换旧摘要；异常或中断不会把上轮成功回执冒充本次完成。
        summary = {'run': run.name, 'status': 'running', 'error': '', 'complete': False,
                   'config_json': json.dumps(config, ensure_ascii=False), 'counts': [], 'candidate_count': 0,
                   'inputs': None, 'designs': None, 'candidates': None,
                   'generations': None, 'reviews': None, 'probe_json': None}
        data.from_items([summary]).write_lance(outputs['summary'], mode='overwrite', schema=SUMMARY)
        try:
            # 1. 固定 keep 名单，按 seed/name 稳定抽样；只传名称和全部 taxonomy。
            if 'cohort_source' in sources:
                # 固定名单已完成审定与选图；按生产者的 selection_rank 取前 N 项。
                cohort = data.read_lance(**sources['cohort_source'],
                    columns=['concept', 'concept_id', 'assessment_id', 'taxonomy', 'selection_rank',
                             'positive_images', 'concept_record'],
                    filter=f'selection_rank >= 1 AND selection_rank <= {config["sample_size"]}')
                prepared_inputs = cohort.map(cohort_input, fn_kwargs={
                    'source': sources['cohort_source'], 'max_reference_images': config['max_reference_images']}).materialize()
                names = [row['concept'] for row in prepared_inputs.select_columns(['concept']).take(config['sample_size'] + 1)]
                if len(names) != config['sample_size'] or len(set(names)) != len(names):
                    raise ValueError('Cohort selected count or canonical names differ from the declared sample')
                concepts = prepared_inputs.select_columns(['concept', 'taxonomy'])
            elif 'screening_source' in sources:
                selected = (data.read_lance(**sources['screening_source'], columns=['concept', 'taxonomy'],
                    filter="status = 'screened' AND decision = 'keep'")
                    .reduce_by_key('concept', lambda acc, row: {'concept': row['concept'],
                        'taxonomy': list(dict.fromkeys((acc['taxonomy'] if acc else []) + (row['taxonomy'] or [])))})
                    .map(lambda row: {**row, 'group': 'keep', 'rank': digest([config['sample_seed'], row['concept']])})
                    .reduce_by_key('group', lambda acc, row: {'group': 'keep',
                        'count': (acc['count'] if acc else 0) + 1,
                        'selected': sorted((acc['selected'] if acc else []) + [row],
                                           key=lambda item: (item['rank'], item['concept']))[:config['sample_size']]})
                    .materialize())
                counts = selected.take(1)
                if not counts or counts[0]['count'] < config['sample_size']:
                    raise ValueError('Not enough screened/keep concepts for sample_size')
                concepts = selected.flat_map(lambda row: [
                    {'concept': r['concept'], 'taxonomy': r['taxonomy']} for r in row['selected']]).materialize()
                names = [r['concept'] for r in concepts.take_all()]
            else:
                names = config['concepts']
                concepts = data.from_items([{'concept': name, 'taxonomy': []} for name in names])
            log(f'本批={len(names)} 个概念；读取固定材料版本 {sources}')
            empty = data.from_arrow(pa.table({'concept': pa.array([], type=pa.string())}))
            # 小批概念只用于 reader 谓词；材料仍留在原生 Dataset 中聚合/关联。
            quoted = ["'" + name.replace("'", "''") + "'" for name in names]
            texts = empty
            if 'article_source' in sources:
                texts = (data.read_lance(**sources['article_source'], columns=['concept', 'content'],
                    filter="review_status = 'reviewed' AND concept IN (" + ','.join(quoted) + ')')
                    .flat_map(lambda row: [{'concept': row['concept'], 'text': {'kind': 'text',
                        'title': topic['title'], 'text': paragraph}}
                        for topic in row['content'] or [] for paragraph in topic['content']['paragraphs'] or []])
                    .reduce_by_key('concept', lambda acc, row: {'concept': row['concept'],
                        'texts': (acc['texts'] if acc else []) + [row['text']]}))
            image_relations = empty
            if 'visual_source' in sources:
                image_relations = (data.read_lance(**sources['visual_source'],
                    columns=['sha256', 'image_uri', 'concept_assessments'],
                    filter=' OR '.join('array_contains(published_concepts, ' + c + ')' for c in quoted))
                    .flat_map(lambda row: [{'concept': a['concept'], 'sha256': row['sha256'],
                        'role': 'concept_reference', 'image_uri': row['image_uri'], 'object_ref': None}
                        for a in row['concept_assessments'] or [] if a['concept'] in names
                        and a['published'] and a['review_status'] == 'keep']))
            images = (image_relations.reduce_by_key(['concept', 'sha256'], lambda acc, row: acc or row)
                .reduce_by_key('concept', lambda acc, row: {'concept': row['concept'],
                    'images': sorted((acc['images'] if acc else []) + [row], key=lambda r: r['sha256'])[:config['max_reference_images']]})
                .map(lambda row: {'concept': row['concept'], 'images': [{'kind': 'image', 'role': im['role'],
                    'object_ref': im['object_ref'] or image_object_ref(im)} for im in row['images']]}))
            if 'visual_source' not in sources:
                images = empty
            # 2. 编号冻结到 inputs：文字材料号与实际图片号独立；缺图保留明确状态。
            if 'cohort_source' not in sources:
                prepared_inputs = (concepts.join(texts, on='concept', how='left').join(images, on='concept', how='left')
                .map(lambda row: {'concept': row['concept'], 'taxonomy': row['taxonomy'],
                    'status': 'ready' if row.get('images') or can_search else 'needs_seed_images',
                    'reason': '' if row.get('images') or can_search else 'No seed images in the selected fixed sources',
                    'references_json': json.dumps([{'number': i, **ref} for i, ref in enumerate(
                        (row.get('texts') or []) + (row.get('images') or []) + [
                            {'kind': 'document', 'document_id': key, 'document': value}
                            for key, value in config['document_resources'].get(row['concept'], {}).items()], 1)], ensure_ascii=False)}))
            prepared_inputs.write_lance(outputs['inputs'], mode='overwrite', schema=INPUTS)
            inputs = {'uri': outputs['inputs'], 'version': lance.dataset(outputs['inputs']).version}
            summary['inputs'] = inputs
            log(f'输入表已提交 @{inputs["version"]}；开始出题，会话超时={config["timeout_s"]}s')
            # 3. 唯一出题节点：agentmap + Codex，每概念独立会话。
            definition = agent.prompt_pack.prompt_definitions['design_question']
            result_schema = definition.response_schema['properties']['result']
            question_schema = result_schema['properties']['question']
            calls = {'root': str(root), 'relative_uri': str(DATASETS / f'calls__{run.name}.lance')}
            options = {'sqlite_journal': sqlite_call_options(**calls)}
            prepared = (data.read_lance(**inputs)
                .map(prepare_request, fn_kwargs={'max_context_chars': config['max_context_chars'],
                    'prompt_chars': len(definition.template.source), 'generate_source': config['generate_source'],
                    'max_generation_attempts': config['max_generation_attempts'],
                    'api_configuration': api_configuration}))
            node = dict(config=agent, options=options, max_requests=config['max_calls'],
                    inputs={'concept_material': 'concept_material', 'evidence_materials': 'evidence_materials',
                            'positive_examples': 'positive_examples', 'execution_context': 'execution_context',
                            'images': 'prompt_images', 'api_configuration': 'api_configuration'}, output='design_result',
                    call_output='design_call', error_output='design_error', when=lambda row: row['status'] == 'ready',
                    concurrency=config['concurrency'], queue_depth=config['queue_depth'])
            designed = prepared.agentmap_async('design_question', **node)
            checked = (designed
                .map(check_response, fn_kwargs={'question_schema': question_schema, 'generate_source': config['generate_source']})
                .map(observe))
            if config['through'] == 'probe':
                probe = config['probe']
                review_pack = load_prompt_pack(PROMPTS / 'review.yaml')
                review_definition = review_pack.prompt_definitions['review_answer']
                review_calls = {'root': str(root), 'relative_uri': str(DATASETS / f'probe_calls__{run.name}.lance')}
                review_options = {
                    'sqlite_journal': {**sqlite_call_options(**review_calls), 'max_requests': probe['max_review_calls']},
                    'timeout_s': probe['review_timeout_s'], 'verify_model': 'listed',
                    'require_finish_reason_stop': True, 'trust_env': False, 'gateway': 'litellm',
                    'request_options': {'max_tokens': probe['review_max_output_tokens'],
                        'reasoning_effort': probe['review_reasoning_effort']},
                }
                probe_state = {'settings': probe, 'calls': review_calls,
                    'review_model': review_definition.model.name, 'review_version': review_definition.version}
                summary['probe_json'] = json.dumps(probe_state, ensure_ascii=False)

                def drained(stats):
                    summary.update({name: ref for name, ref in stats.outputs.items() if name in SUMMARY.names})
                    summary['probe_json'] = json.dumps({**probe_state, 'miss': stats.miss}, ensure_ascii=False)
                    data.from_items([summary]).write_lance(outputs['summary'], mode='overwrite', schema=SUMMARY)

                # Preserve the existing online cache identity; this constant is not an execution mode.
                identity = digest({'implementation': 'edit-inline-probe-1', 'offline': False,
                    **{key: probe[key] for key in ('model', 'revision', 'base_url', 'image_size',
                                                  'steps', 'run_seed', 'max_image_bytes')}})
                log(f'流水探测：{probe["model"]} 编辑 → {review_definition.model.name} / xhigh')
                # All four sinks belong to this run. A committed candidate immediately advances to answering.
                stats = (checked
                    .save_lance(outputs['designs'], schema=DESIGNS, key='concept', stage='designs', max_batch=1, queue_depth=1)
                    .filter(lambda row: row['status'] == 'candidate').map(candidate_row)
                    .save_lance(outputs['candidates'], schema=QUESTIONS, key='task_id', stage='candidates', max_batch=1, queue_depth=1)
                    .map(answer_input)
                    .map_cached(AnswerImage(probe, LocalObjectStore(root / 'objects')),
                        cache_dir=root / '_demiflow' / 'benchmark_edit_v2' / run.name / 'generation_cache', version=identity,
                        cache_when=lambda row: row['status'] not in {'budget_exhausted', 'pending_generation'})
                    .map(lambda row: log(f'试作答：{row["concept"]} {row["status"]} {row["reason"]}') or row)
                    .save_lance(outputs['generations'], schema=GENERATIONS, key='task_id', stage='generations',
                        max_batch=1, queue_depth=1, output_ref='generation_source')
                    .map(prepare_review, fn_kwargs={'max_context_chars': probe['review_max_context_chars'],
                        'prompt_chars': len(review_definition.template.source)})
                    .map_prompt_async('review_answer', config=review_pack, options=review_options,
                        max_requests=probe['max_review_calls'], inputs={'payload': 'review_payload', 'images': 'review_images'},
                        output='review_result', call_output='review_call', error_output='review_error',
                        when=lambda row: row['review_status'] == 'ready',
                        concurrency=probe['review_concurrency'], queue_depth=probe['review_queue_depth'])
                    .map(check_review, fn_kwargs={'response_schema': review_definition.response_schema})
                    .map(lambda row: log(f'评审：{row["concept"]} {row["review_status"]} {row["review_reason"]}') or row)
                    .save_lance(outputs['reviews'], schema=REVIEWS, key='task_id', stage='reviews', max_batch=1, queue_depth=1)
                    .run_stream(on_drain=drained))
                refs = stats.outputs
                # Only bounded keys/status summaries leave Dataset; model payloads stay in typed tables.
                design_keys = data.read_lance(**refs['designs'], columns=['concept', 'status']).take(len(names)+1)
                if len(design_keys) != len(names) or {r['concept'] for r in design_keys} != set(names):
                    raise ValueError('Design outputs do not cover this run input exactly')
                keys = {}
                for stage in ('candidates', 'generations', 'reviews'):
                    rows = data.read_lance(**refs[stage], columns=['task_id']).take(len(names)+1)
                    keys[stage] = {r['task_id'] for r in rows}
                    if len(rows) != len(keys[stage]) or len(rows) > len(names):
                        raise ValueError('Duplicate or excess task IDs: ' + stage)
                if keys['candidates'] != keys['generations'] or keys['candidates'] != keys['reviews']:
                    raise ValueError('Probe stages do not cover the same candidate tasks')
                counts = {}
                for row in design_keys:
                    counts[row['status']] = counts.get(row['status'], 0)+1
                probe_counts = {}
                for stage, field in (('generations', 'status'), ('reviews', 'review_status')):
                    probe_counts[stage] = {r[field]: r['count'] for r in data.read_lance(**refs[stage], columns=[field])
                        .reduce_by_key(field, lambda acc, row, field=field: {field: row[field],
                            'count': (acc['count'] if acc else 0)+1}).take_all()}
                complete = (not stats.miss and set(counts) <= {'candidate', 'insufficient'}
                    and set(probe_counts['generations']) <= {'generated'}
                    and set(probe_counts['reviews']) <= {'reviewed'})
                state = {'complete': complete, 'counts': counts, 'candidate_count': len(keys['candidates']),
                    'inputs': inputs, **refs, 'calls': calls, 'sources': sources,
                    'probe': {**probe_state, 'counts': probe_counts, 'miss': stats.miss}}
                summary.update(status='complete' if complete else 'incomplete', complete=complete,
                    counts=[{'status': k, 'count': v} for k,v in counts.items()], candidate_count=state['candidate_count'],
                    probe_json=json.dumps(state['probe'], ensure_ascii=False), **refs)
                data.from_items([summary]).write_lance(outputs['summary'], mode='overwrite', schema=SUMMARY)
                log(f'出题与探测落表：{counts}；{probe_counts}；complete={complete}')
                return state
            checked.materialize().write_lance(outputs['designs'], mode='overwrite', schema=DESIGNS)
            designs_ref = {'uri': outputs['designs'], 'version': lance.dataset(outputs['designs']).version}
            summary['designs'] = designs_ref
            designs = data.read_lance(**designs_ref)
            log(f'设计表已提交 @{designs_ref["version"]}；开始写候选')
            # 4. 候选只交付已存在且作者检查通过的原图。内部种子/判据留审计列，不作为作答输入。
            questions = (designs.filter(lambda row: row['status'] == 'candidate').map(candidate_row)
                .materialize())
            count = questions.count()
            # 同目标写锁内按 task_id anti join，避免追加续跑重复；不冻结旧配置或复制旧业务封装。
            with run_lock(root / '_demiflow' / 'edit_v2_targets' / digest(outputs['candidates'])):
                delivery = questions
                if config['write_mode'] == 'append' and Path(outputs['candidates']).exists():
                    delivery = questions.join(data.read_lance(outputs['candidates'],
                        version=lance.dataset(outputs['candidates']).version, columns=['task_id']), on='task_id', how='anti')
                delivery.write_lance(outputs['candidates'], mode=config['write_mode'], schema=QUESTIONS)
                candidates = {'uri': outputs['candidates'], 'version': lance.dataset(outputs['candidates']).version}
            counts = {r['status']: r['count'] for r in designs.reduce_by_key('status', lambda acc, row: {
                'status': row['status'], 'count': (acc['count'] if acc else 0) + 1}).take_all()}
            state = {'complete': set(counts) <= {'candidate', 'insufficient'}, 'counts': counts, 'candidate_count': count,
                     'inputs': inputs, 'designs': designs_ref, 'candidates': candidates, 'calls': calls, 'sources': sources}
            summary.update(status='complete' if state['complete'] else 'incomplete', complete=state['complete'],
                           counts=[{'status': k, 'count': v} for k, v in counts.items()],
                           candidate_count=count, candidates=candidates)
            data.from_items([summary]).write_lance(outputs['summary'], mode='overwrite', schema=SUMMARY)
            log(f'完成落表；{counts}，本批候选={count}，complete={state["complete"]}（不表示独立审核通过）')
            return state
        except BaseException as exc:
            summary.update(status='failed', error=f'{type(exc).__name__}: {exc}', complete=False)
            # 摘要写失败也保留原始异常，不能覆盖其定位信息。
            try:
                data.from_items([summary]).write_lance(outputs['summary'], mode='overwrite', schema=SUMMARY)
            except Exception as summary_error:
                log(f'失败摘要写入失败：{summary_error}')
            log(f'运行失败：{type(exc).__name__}: {exc}；本次未发布完成回执')
            raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', required=True)
    parser.add_argument('--concept', action='append')
    for name in ('screening', 'cohort', 'article', 'visual'):
        parser.add_argument('--' + name + '-table')
        parser.add_argument('--' + name + '-version', type=int)
    parser.add_argument('--sample-size', type=int)
    parser.add_argument('--sample-seed', type=int, default=0)
    parser.add_argument('--target-uri')
    parser.add_argument('--write-mode', choices=['overwrite', 'append'], default='overwrite')
    parser.add_argument('--agent-config', required=True, help='唯一出题配置：完整 demiflow_agent_v2 YAML')
    parser.add_argument('--document-resources-json', help='concept → 材料编号 → 固定本地 document_ref 与来源 url 的 JSON 文件')
    parser.add_argument('--through', choices=['author', 'probe'], default='author')
    parser.add_argument('--probe-json', help='本地 Qwen-Image-2.1 作答与 GPT 联合评审配置')
    for name, default in [('max-generation-attempts', 2), ('max-calls', None), ('max-reference-images', 8),
                          ('concurrency', 1), ('queue-depth', 1), ('progress-every', 1)]:
        parser.add_argument('--' + name, type=int, default=default)
    args = vars(parser.parse_args())
    for name in ('screening', 'cohort', 'article', 'visual'):
        uri, version = args.pop(name + '_table'), args.pop(name + '_version')
        if bool(uri) != (version is not None):
            parser.error(name + ' table and version must be supplied together')
        args[name + '_source'] = {'uri': uri, 'version': version} if uri else None
    document_file = args.pop('document_resources_json')
    args['document_resources'] = json.loads(Path(document_file).read_text()) if document_file else None
    probe_file = args.pop('probe_json')
    args['probe'] = json.loads(Path(probe_file).read_text()) if probe_file else None
    args['concepts'] = args.pop('concept')
    args['run'] = resolve_root() / DATASETS / args['run']
    run_pipeline(config(**args))


if __name__ == '__main__':
    main()
