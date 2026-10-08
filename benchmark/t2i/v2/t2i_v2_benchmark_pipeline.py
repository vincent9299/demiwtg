"""config → run_pipeline：固定粗筛 keep 表选样或显式概念 → 可选图文 → 独立出题 → 设计与候选表。"""
from demiflow.operator_llm.call_ref import journal_options as sqlite_call_options

import argparse
import json
import math
from functools import partial
from pathlib import Path
from time import perf_counter

import lance
import pyarrow as pa
from demiflow.execution.artifacts import digest, run_lock
from benchmark.t2i.v2.operators.run_tables import RunTables
from benchmark.t2i.v2.operators import question_review as review
from demiflow.operator_llm.parser import load_prompt_pack
from demiflow.execution.dataset_commit import DatasetCommit
from demiflow import data
from demiflow.agent import load_agent_config
from demiflow.objects import LocalObjectStore
from benchmark.t2i.v2.operators.probe import (
    probe_config, candidate_row, answer_input, AnswerImage, prepare_review, check_review,
    GENERATIONS as PROBE_GENERATIONS, REVIEWS as PROBE_REVIEWS,
)
from project import resolve_root
from benchmark.t2i.v2.operators.authoring import prepare_request, check_response, select_screened_concepts, adopted_concept
from benchmark.t2i.v2.operators.images import image_object_ref
from benchmark.t2i.v2.operators.concept_context import AUTHORING_CONTEXT
from benchmark.t2i.v2.operators.comparison import (
    COHORT, VARIANTS, merge_review, collect_positives, sampling_key,
    collect_sampling_keys, choose_balanced, authoring_images,
)
from benchmark.t2i.v2.operators.sampling_taxonomy import (
    snapshot as taxonomy_snapshot, collect_nodes, sampling_tree, unique_placement, placement_category,
)

DATASETS = Path('demiwtg/benchmark/t2i/v2/datasets')
PROMPTS = Path(__file__).parent / 'prompts'

# 与下方三个 writer 对应：每概念的输入、每概念的设计结果、每题一行的候选。
# 出题字段及类型由 agent_config 检查，表结构不承担额外业务审核。
# 每项同时保存考点、知识依据和针对最终任务的可观察判据；候选仍需语义审核。
TEST_POINT = pa.struct([('point', pa.string()), ('basis', pa.string()), ('criterion', pa.string())])
QUESTION = pa.struct([('instruction', pa.string()), ('test_points', pa.list_(TEST_POINT))])
INPUTS = pa.schema(
    [
        ('concept', pa.string()),
        ('taxonomy', pa.list_(pa.string())),
        ('concept_record', AUTHORING_CONTEXT),
        ('status', pa.string()),
        ('reason', pa.large_string()),
        ('evidence_json', pa.large_string()),
        ('authoring_variant', pa.string()),
        ('authoring_images_json', pa.large_string()),
    ]
)
DESIGNS = pa.schema([*INPUTS, ('question', QUESTION), ('reasoning', pa.large_string()), ('call_json', pa.large_string()),
                     ('authoring_context_json', pa.large_string())])
QUESTIONS = pa.schema(
    [
        ('task_id', pa.string()),
        ('concept', pa.string()),
        ('taxonomy', pa.list_(pa.string())),
        ('concept_record', AUTHORING_CONTEXT),
        ('status', pa.string()),
        *QUESTION,
        ('reasoning', pa.large_string()),
        ('evidence_json', pa.large_string()),
        ('authoring_variant', pa.string()), ('authoring_images_json', pa.large_string()),
        ('authoring_context_json', pa.large_string()),
    ]
)


# 待审名单与覆盖统计都是每概念一行；来源快照随行保存，未审图片不冒充已发布材料。
REFERENCE_CONCEPTS = pa.schema([
    ('concept', pa.string()), ('taxonomy', pa.list_(pa.string())), ('category', pa.string()),
    ('candidate_image_count', pa.int64()), ('published_image_count', pa.int64()),
    ('reviewed_text_count', pa.int64()), ('eligible', pa.bool_()), ('material_status', pa.string()),
    ('selection_rank', pa.int64()), ('sources_json', pa.large_string()),
])


def reference_selection_config(*, run, screening_source, image_source, article_source=None,
                               sample_size=200, sample_seed=0, min_candidate_images=3, category_depth=3):
    """配置审核名单：固定双模型通过表和图文版本、数量、种子、候选图下限与分类深度。

    至少有已发布图片/已审核正文，或满足 min_candidate_images，才参与选样。
    本入口只统计目录信息并保存名单，不读取图片字节、不提交审核或出题模型。
    """
    for name, source in [('screening_source', screening_source), ('image_source', image_source),
                         ('article_source', article_source)]:
        if name == 'article_source' and source is None:
            continue
        if (not isinstance(source, dict) or not isinstance(source.get('uri'), str)
                or not source['uri'].strip() or type(source.get('version')) is not int or source['version'] < 1):
            raise ValueError(name + ' requires a fixed uri/version')
    if any(type(v) is not int or v < 1 for v in (sample_size, min_candidate_images, category_depth)):
        raise ValueError('sample_size, min_candidate_images and category_depth must be positive integers')
    if type(sample_seed) is not int:
        raise ValueError('sample_seed must be an integer')
    return dict(run=str(run), screening_source=dict(screening_source), image_source=dict(image_source),
                article_source=dict(article_source) if article_source else None,
                sample_size=sample_size, sample_seed=sample_seed,
                min_candidate_images=min_candidate_images, category_depth=category_depth)


def run_reference_selection(config):
    """固定通过概念 → 关联图文数量 → 材料优先、分类轮转选样 → 保存覆盖表与待审名单。

    图片按采集 concepts 召回，要求 available、已知正尺寸、存储引用及非已知生成图。
    published/keep 和 reviewed 正文另计数；这些目录条件不等于像素/身份审核通过。
    不修改公共图文或粗筛表；数量不足时在写名单前报错，返回实际提交的固定版本。
    """
    from benchmark.t2i.v2.operators.reference_selection import reference_coverage, choose_reference_concepts
    root = resolve_root()
    run = Path(config['run']).absolute()
    if run.parent != root / DATASETS or run.suffix:
        raise ValueError('run must be a suffix-free name under benchmark/t2i/v2/datasets')
    sources = {name: {'uri': str((root / ref['uri']).resolve()), 'version': ref['version']}
               for name in ('screening_source', 'image_source', 'article_source')
               if (ref := config[name]) is not None}
    outputs = {name: str(run.parent / f'{name}__{run.name}.lance')
               for name in ('reference_coverage', 'reference_concepts')}
    if set(outputs.values()) & {ref['uri'] for ref in sources.values()}:
        raise ValueError('Selection outputs must differ from source tables')
    print(f'[Reference selection] 固定来源={sources}；选 {config["sample_size"]} 个，'
          f'候选图下限={config["min_candidate_images"]}；零模型调用', flush=True)
    with run_lock(root / '_demiflow' / 'reference_selection' / run.name):
        # 1. 固定筛选结果只取有效 keep，去重并保留全部分类；小批名称仅用于 reader 谓词。
        screened = (data.read_lance(**sources['screening_source'], columns=['concept', 'taxonomy'],
                                   filter="status = 'screened' AND decision = 'keep'")
            .reduce_by_key('concept', lambda acc, row: {
                'concept': row['concept'],
                'taxonomy': list(dict.fromkeys((acc['taxonomy'] if acc else []) + (row['taxonomy'] or []))),
            }).materialize())
        names = {row['concept'] for row in screened.take_all()}
        if not names:
            raise ValueError('No screened/keep concepts')
        quoted = ["'" + name.replace("'", "''") + "'" for name in sorted(names)]
        # 2. 每 SHA 按概念展开，只累计有字节标记、尺寸及引用的候选；已发布数量独立统计。
        image_counts = (data.read_lance(**sources['image_source'],
            columns=['concepts', 'image_uri', 'concept_assessments', 'published_concepts'],
            filter="availability = 'available' AND width > 0 AND height > 0 AND "
                   "(generation_origin IS NULL OR generation_origin NOT IN ('generated','synthetic','ai_generated')) AND ("
                   + ' OR '.join('array_contains(concepts, ' + name + ')' for name in quoted) + ')')
            .filter(lambda row: bool(row['image_uri']))
            .flat_map(lambda row: [
                {'concept': name, 'candidate_image_count': 1,
                 'published_image_count': int(name in (row['published_concepts'] or []) and any(
                     a['concept'] == name and a['published'] and a['review_status'] == 'keep'
                     for a in row['concept_assessments'] or []))}
                for name in sorted(set(row['concepts'] or []) & names)])
            .reduce_by_key('concept', lambda acc, row: {
                'concept': row['concept'], **{key: (acc[key] if acc else 0) + row[key]
                    for key in ('candidate_image_count', 'published_image_count')},
            }))
        if 'article_source' in sources:
            text_counts = (data.read_lance(**sources['article_source'], columns=['concept', 'content'],
                filter="review_status = 'reviewed' AND concept IN (" + ','.join(quoted) + ')')
                .map(lambda row: {'concept': row['concept'], 'reviewed_text_count': sum(
                    bool(paragraph and paragraph.strip()) for topic in row['content'] or []
                    for paragraph in topic['content']['paragraphs'] or [])})
                .reduce_by_key('concept', lambda acc, row: {'concept': row['concept'],
                    'reviewed_text_count': (acc['reviewed_text_count'] if acc else 0) + row['reviewed_text_count']}))
        else:
            text_counts = data.from_arrow(pa.table({'concept': pa.array([], type=pa.string())}))
        # 3. 保留全部通过概念的覆盖情况，选样仅在满足材料条件的行中进行；不足不降门槛。
        coverage = (screened.join(image_counts, on='concept', how='left')
            .join(text_counts, on='concept', how='left')
            .map(reference_coverage, fn_kwargs={
                'min_candidate_images': config['min_candidate_images'], 'category_depth': config['category_depth'],
                'sources_json': json.dumps({**sources, **{key: config[key] for key in (
                    'sample_size', 'sample_seed', 'min_candidate_images', 'category_depth')}}, ensure_ascii=False),
            }).materialize())
        selection = (coverage.map(lambda row: {'group': 'all', 'row': row})
            .reduce_by_key('group', lambda acc, row: {'group': 'all', 'rows': (acc['rows'] if acc else []) + [row['row']]})
            .map(choose_reference_concepts, fn_kwargs={
                'sample_size': config['sample_size'], 'sample_seed': config['sample_seed']}).materialize())
        summary = selection.take(1)[0]
        print(f'[Reference selection] 通过={summary["screened_count"]}，材料条件合格={summary["eligible_count"]}；保存名单', flush=True)
        coverage.write_lance(outputs['reference_coverage'], mode='overwrite', schema=REFERENCE_CONCEPTS)
        selection.flat_map(lambda row: row['selected']).write_lance(
            outputs['reference_concepts'], mode='overwrite', schema=REFERENCE_CONCEPTS)
        refs = {name: {'uri': uri, 'version': lance.dataset(uri).version} for name, uri in outputs.items()}
        selected = summary['selected']
        result = {**refs, 'sources': sources, 'screened_count': summary['screened_count'],
                  'eligible_count': summary['eligible_count'], 'selected_count': len(selected),
                  'category_count': len({row['category'] for row in selected}),
                  'candidate_image_relations': sum(row['candidate_image_count'] for row in selected),
                  'with_published_references': sum(row['material_status'] == 'published_reference' for row in selected)}
        print(f'[Reference selection] 已提交 {len(selected)} 个，分类={result["category_count"]}；{refs}', flush=True)
        return result



def config(
    *,
    run,
    concepts=None,
    screening_source=None,
    concept_audit_source=None,
    cohort_source=None,
    positive_review_sources=None,
    authoring_variants=None,
    min_positive_images=5,
    sampling_depth=2,
    sampling_taxonomy_source=None,
    cohort_revision=None,
    authoring_variant='standard',
    through='author',
    probe=None,
    review_source=None,
    review_task_ids=None,
    question_review=None,
    sample_size=None,
    sample_seed=0,
    article_source=None,
    visual_source=None,
    target_uri=None,
    write_mode='overwrite',
    mode=None,
    model=None,
    base_url=None,
    api_key_env=None,
    max_calls=None,
    max_reference_images=8,
    max_context_chars=None,
    max_output_tokens=None,
    timeout_s=None,
    concurrency=1,
    queue_depth=1,
    temperature=None,
    codex_bin=None,
    reasoning_effort=None,
    codex_web_search=None,
    document_reads=None,
    agent_config=None,
    document_read_max_turns=None,
    document_read_max_calls_per_turn=None,
    document_read_max_chars=None,
    document_read_max_bytes=None,
    document_read_timeout_s=None,
    model_revision=None,
    model_stream=None,
    model_deployment_id=None,
):
    """统一运行位置、材料来源与输出；agent_config 是 agent 的完整配置。

    出题必须指定完整 agent_config；不再提供普通 prompt 出题分支。

    concepts、screening_source、concept_audit_source 三选一。表来源必须固定 uri/version 并显式给 sample_size，
    仅从 status=screened、decision=keep 的唯一概念按 seed/name 哈希选 N 个；不足报错。
    上游顶层 keep 已表示全部粗筛模型通过；保留概念名与完整 taxonomy，不传模型判断或候选考点。
    codex_web_search 只控制 Codex 工具：live 允许按需联网核验，cached 使用索引，
    disabled 关闭检索。默认 live 不强制每题检索；HTTP 模式不会因此获得工具。
    文档工具由 agent YAML 声明；提供 resources 的配置按行绑定固定文档并使用回调。
    旧 Codex 文件工具配置仍可提供本地路径，不在运行参数中注入工具。
    cohort_revision 只用于输入选样：从固定旧cohort保留名额，按原case分类优先纳入第一类，
    可显式配置其正例下限、未挂载taxonomy例外及有原因的图片SHA排除；通过CLI --config和notebook共用。
    """
    if through == 'review':
        if any(v is not None for v in (concepts, screening_source, concept_audit_source, cohort_source,
                positive_review_sources, authoring_variants, probe, article_source, visual_source,
                question_review, target_uri)) or write_mode != 'overwrite':
            raise ValueError('through=review only accepts fixed review sources and review runtime settings')
        if any(v is not None for v in (model, base_url, api_key_env, temperature, timeout_s,
                max_context_chars, max_output_tokens, codex_bin, reasoning_effort, codex_web_search,
                document_reads, model_revision, model_stream, model_deployment_id,
                document_read_max_turns, document_read_max_calls_per_turn, document_read_max_chars,
                document_read_max_bytes, document_read_timeout_s)):
            raise ValueError('Review model/tools/budgets belong in its agent YAML')
        return review_config(run=run, review_source=review_source,
            sample_size=sample_size, agent_config=agent_config, max_calls=max_calls,
            concurrency=concurrency, queue_depth=queue_depth, mode=mode, review_task_ids=review_task_ids)
    if review_source is not None or review_task_ids is not None:
        raise ValueError('review sources and review_task_ids require through=review')
    if question_review is not None:
        if not isinstance(question_review, dict) or not {'agent_config', 'max_calls'} <= set(question_review) or set(question_review) - {
                'agent_config', 'max_calls', 'concurrency', 'queue_depth', 'mode'}:
            raise ValueError('question_review requires agent_config/max_calls and optional scheduling settings')
        if through == 'inputs':
            raise ValueError('question_review requires author or probe output')
        question_review_runtime(**question_review)
    supplied = dict(locals())
    # agent 的模型、工具和预算仅从一个配置入口解析；运行参数不能覆盖它。
    if agent_config is None:
        raise ValueError('T2I V2 authoring requires agent_config; use prompts/agent_codex.yaml')
    agent_config = str(Path(agent_config).resolve())
    agent = load_agent_config(agent_config)
    definition = agent.prompt_pack.prompt_definitions['design_question']
    if model is not None and model != definition.model.name:
        raise ValueError('model conflicts with agent_config; change the agent YAML')
    if model_deployment_id is not None:
        raise ValueError('Agent deployment belongs in agent_config.model, not pipeline overrides')
    model = definition.model.name
    document_reads = True
    if agent.environment.runtime == 'codex':
        if mode not in (None, 'codex'):
            raise ValueError('mode conflicts with Codex agent_config')
        mode = 'codex'
        settings = agent.options['codex_agent']
        codex_bin = settings.get('bin', 'codex')
        reasoning_effort = settings.get('reasoning_effort')
        codex_web_search = settings.get('web_search', 'disabled')
        model_revision = settings.get('model_revision')
        base_url = api_key_env = None
    elif mode == 'codex':
        raise ValueError('mode conflicts with demiflow agent_config')
    else:
        mode = mode or 'modelhub'
        base_url, api_key_env = definition.model.base_url, definition.model.api_key_env
    timeout_s = agent.options.get('timeout_s', 600)
    max_context_chars = agent.environment.max_context_chars
    document_read_max_turns = agent.environment.max_turns
    document_read_max_calls_per_turn = agent.environment.max_calls_per_turn
    document_read_max_chars = agent.environment.max_material_chars
    document_read_max_bytes = agent.environment.max_document_bytes
    document_read_timeout_s = agent.environment.timeout_s
    request_options = agent.options.get('request_options', {})
    temperature = request_options.get('temperature', 0)
    max_output_tokens = request_options.get('max_tokens', 8192)
    model_stream = agent.options.get('stream', False)
    if agent.environment.runtime != 'codex':
        codex_bin, reasoning_effort, codex_web_search = 'codex', None, 'live'
        model_revision = agent.options.get('model_revision')
    resolved = dict(locals())
    for field in ('model', 'base_url', 'api_key_env', 'timeout_s', 'max_context_chars',
                  'codex_bin', 'reasoning_effort', 'codex_web_search', 'model_revision', 'model_stream',
                  'temperature', 'max_output_tokens', 'document_reads', 'document_read_max_turns',
                  'document_read_max_calls_per_turn', 'document_read_max_chars',
                  'document_read_max_bytes', 'document_read_timeout_s'):
        if supplied[field] is not None and supplied[field] != resolved[field]:
            raise ValueError(f'{field} conflicts with agent_config; change the agent YAML')
    if through not in {'inputs', 'author', 'probe'}:
        raise ValueError('through must be inputs, author or probe')
    if through == 'probe' and probe is None:
        raise ValueError('through=probe requires explicit probe settings')
    if through == 'probe' and write_mode != 'overwrite':
        raise ValueError('Streaming probe uses run-owned overwrite snapshots; append is not supported')
    if type(model_stream) is not bool:
        raise ValueError('model_stream must be boolean')
    if model_revision is not None and (not isinstance(model_revision, str) or not model_revision.strip()):
        raise ValueError('model_revision must be a nonempty string or None')
    if model_deployment_id is not None and (mode != 'modelhub' or not isinstance(model_deployment_id, str)
            or not model_deployment_id.strip() or not model_revision):
        raise ValueError('model_deployment_id requires modelhub, a nonempty ID and explicit model_revision')
    if authoring_variant not in ('standard', *VARIANTS):
        raise ValueError('Unknown authoring_variant')
    if authoring_variants is not None:
        if tuple(authoring_variants) != VARIANTS or concept_audit_source is None or not positive_review_sources:
            raise ValueError('Paired authoring requires both variants, adopted concepts and positive review snapshots')
        if not document_reads or article_source is not None or visual_source is not None:
            raise ValueError('Paired authoring uses document agent and dedicated author examples only')
    if positive_review_sources is not None:
        if authoring_variants is None:
            raise ValueError('positive_review_sources requires paired authoring_variants')
        if not isinstance(positive_review_sources, (list, tuple)) or not 1 <= len(positive_review_sources) <= 64:
            raise ValueError('Use 1..64 fixed review sources')
        for ref in positive_review_sources:
            if not isinstance(ref, dict) or not ref.get('uri') or type(ref.get('version')) is not int or ref['version'] < 1:
                raise ValueError('Positive review sources require uri/version')
    if any(type(n) is not int or n < 1 for n in (min_positive_images, sampling_depth)):
        raise ValueError('min_positive_images and sampling_depth must be positive integers')
    if sampling_taxonomy_source is not None:
        if (authoring_variants is None or not isinstance(sampling_taxonomy_source, dict)
                or set(sampling_taxonomy_source) != {'uri', 'version'}
                or not isinstance(sampling_taxonomy_source['uri'], str) or not sampling_taxonomy_source['uri']
                or type(sampling_taxonomy_source['version']) is not int or sampling_taxonomy_source['version'] < 1):
            raise ValueError('sampling_taxonomy_source requires paired authoring and fixed summary uri/version')
    if cohort_revision is not None:
        if (authoring_variants is None or not isinstance(cohort_revision, dict)
                or not {'base_cohort', 'case_category_sources', 'common_limit',
                        'min_common_positive_images'} <= set(cohort_revision)
                or set(cohort_revision) - {'base_cohort', 'case_category_sources', 'common_limit',
                                          'min_common_positive_images', 'common_allow_unassigned_taxonomy',
                                          'image_exclusions'}):
            raise ValueError('cohort_revision requires paired selection, fixed sources, common_limit and min_common_positive_images')
        if (type(cohort_revision['common_limit']) is not int
                or not 1 <= cohort_revision['common_limit'] <= (sample_size or 0)
                or type(cohort_revision['min_common_positive_images']) is not int
                or not 0 <= cohort_revision['min_common_positive_images'] <= min_positive_images):
            raise ValueError('Invalid common_limit or min_common_positive_images')
        if type(cohort_revision.get('common_allow_unassigned_taxonomy', False)) is not bool:
            raise ValueError('common_allow_unassigned_taxonomy must be boolean')
        exclusions = cohort_revision.get('image_exclusions', [])
        if (not isinstance(exclusions, list) or len(exclusions) > 1024
                or any(not isinstance(item, dict) or set(item) != {'sha256', 'reason'}
                       or not isinstance(item['sha256'], str) or len(item['sha256']) != 64
                       or any(char not in '0123456789abcdef' for char in item['sha256'])
                       or not isinstance(item['reason'], str) or not 1 <= len(item['reason']) <= 1024
                       for item in exclusions)):
            raise ValueError('image_exclusions requires at most 1024 SHA256/reason records')
        category_sources = cohort_revision['case_category_sources']
        if not isinstance(category_sources, list) or not 1 <= len(category_sources) <= 8:
            raise ValueError('cohort_revision requires 1..8 fixed category sources')
        for ref in [cohort_revision['base_cohort'], *category_sources]:
            if (not isinstance(ref, dict) or not isinstance(ref.get('uri'), str) or not ref['uri']
                    or type(ref.get('version')) is not int or ref['version'] < 1):
                raise ValueError('cohort_revision sources require fixed uri/version')
        if any(ref.get('kind') not in ('classifications', 'adjudications') for ref in category_sources):
            raise ValueError('case_category_sources require classifications or adjudications kind')
    if mode not in {'offline', 'local', 'modelhub', 'codex'}:
        raise ValueError('mode must be offline, local, modelhub or codex')
    if mode == 'codex' and (not isinstance(model, str) or not model.strip()):
        raise ValueError('Codex mode requires an explicit model')
    if reasoning_effort is not None and reasoning_effort not in {'minimal', 'low', 'medium', 'high', 'xhigh'}:
        raise ValueError('Unsupported Codex reasoning_effort')
    if codex_web_search not in {'disabled', 'cached', 'live'}:
        raise ValueError('codex_web_search must be disabled, cached or live')
    if type(document_reads) is not bool:
        raise ValueError('document_reads must be boolean')
    if cohort_source is not None:
        if any(source is not None for source in (concepts, screening_source, concept_audit_source)):
            raise ValueError('cohort_source cannot be combined with other concept sources')
        if (not isinstance(cohort_source, dict) or not cohort_source.get('uri')
                or type(cohort_source.get('version')) is not int or cohort_source['version'] < 1
                or type(sample_size) is not int or sample_size < 1):
            raise ValueError('cohort_source requires fixed uri/version and sample_size')
    elif concept_audit_source is not None:
        if screening_source is not None or concepts is not None:
            raise ValueError('Use exactly one of concepts, screening_source and concept_audit_source')
        if (not isinstance(concept_audit_source, dict) or not isinstance(concept_audit_source.get('uri'), str)
                or not concept_audit_source['uri'].strip() or type(concept_audit_source.get('version')) is not int
                or concept_audit_source['version'] < 1):
            raise ValueError('concept_audit_source requires a fixed uri/version')
        if type(sample_size) is not int or sample_size < 1:
            raise ValueError('concept_audit_source requires an explicit positive sample_size')
    elif screening_source is not None:
        if concepts is not None:
            raise ValueError('Use concepts or screening_source, not both')
        if (not isinstance(screening_source, dict) or not isinstance(screening_source.get('uri'), str)
                or not screening_source['uri'].strip() or type(screening_source.get('version')) is not int
                or screening_source['version'] < 1):
            raise ValueError('screening_source requires a fixed uri/version')
        if type(sample_size) is not int or sample_size < 1:
            raise ValueError('screening_source requires an explicit positive sample_size')
    elif sample_size is not None:
        raise ValueError('sample_size requires screening_source or concept_audit_source')
    elif (
        not isinstance(concepts, (list, tuple))
        or not concepts
        or any(not isinstance(c, str) or not c.strip() for c in concepts)
        or len(set(concepts)) != len(concepts)
    ):
        raise ValueError('Select nonempty, unique concept names')
    if type(sample_seed) is not int:
        raise ValueError('sample_seed must be an integer')
    # agent 的全部行内模型轮次共享节点预算；Codex 单次路径按 exec 次数计数。
    max_calls = ((sample_size if any(s is not None for s in (screening_source, concept_audit_source, cohort_source)) else len(concepts))
                 * (document_read_max_turns if document_reads and mode != 'codex' else 1)) if max_calls is None else max_calls
    if agent is not None and type(max_calls) is int:
        max_calls = min(max_calls, agent.max_requests)
    if probe is not None:
        probe = probe_config(probe, maximum=sample_size if sample_size is not None else len(concepts))
    for value in (max_calls, max_reference_images, max_context_chars, max_output_tokens, concurrency):
        if type(value) is not int or value < 1:
            raise ValueError('Counts and budgets must be positive integers')
    if queue_depth is not None and (type(queue_depth) is not int or queue_depth < 1):
        raise ValueError('queue_depth must be a positive integer or None')
    if type(temperature) not in (int, float) or not math.isfinite(temperature) or temperature < 0:
        raise ValueError('temperature must be a finite nonnegative number')
    if timeout_s <= 0:
        raise ValueError('timeout_s must be positive')
    if write_mode not in {'append', 'overwrite'}:
        raise ValueError('write_mode must be append or overwrite')
    return {
        'run': str(run),
        'article_source': article_source,
        'visual_source': visual_source,
        'target_uri': str(target_uri) if target_uri is not None else None,
        'write_mode': write_mode,
        'concepts': list(concepts) if concepts is not None else None,
        'screening_source': dict(screening_source) if screening_source is not None else None,
        'concept_audit_source': dict(concept_audit_source) if concept_audit_source is not None else None,
        'cohort_source': dict(cohort_source) if cohort_source is not None else None,
        'positive_review_sources': list(positive_review_sources) if positive_review_sources else None,
        'authoring_variants': list(authoring_variants) if authoring_variants else None,
        'min_positive_images': min_positive_images, 'sampling_depth': sampling_depth,
        'sampling_taxonomy_source': dict(sampling_taxonomy_source) if sampling_taxonomy_source else None,
        'cohort_revision': cohort_revision,
        'authoring_variant': authoring_variant, 'through': through, 'probe': probe,
        'question_review': question_review,
        'model_revision': model_revision, 'model_stream': model_stream,
        'model_deployment_id': model_deployment_id,
        'sample_size': sample_size,
        'sample_seed': sample_seed,
        'mode': mode,
        'model': model,
        'base_url': base_url,
        'api_key_env': api_key_env,
        'max_calls': max_calls,
        'max_reference_images': max_reference_images,
        'max_context_chars': max_context_chars,
        'max_output_tokens': max_output_tokens,
        'timeout_s': timeout_s,
        'concurrency': concurrency,
        'queue_depth': queue_depth,
        'temperature': temperature,
        'codex_bin': str(codex_bin),
        'reasoning_effort': reasoning_effort,
        'codex_web_search': codex_web_search,
        'document_reads': document_reads, 'agent_config': agent_config,
        'document_read_max_turns': document_read_max_turns,
        'document_read_max_calls_per_turn': document_read_max_calls_per_turn,
        'document_read_max_chars': document_read_max_chars,
        'document_read_max_bytes': document_read_max_bytes,
        'document_read_timeout_s': document_read_timeout_s,
    }


def run_pipeline(config):
    """从 preparation 固定版本的图文结果出题，返回输入、设计和候选表引用。

    config 统一提供概念/粗筛固定来源、选样规模/种子、图文来源、目标及模型参数。
    两类来源各指定一张表的 uri/version；不提供某类材料时设为 None。
    每概念一行输入；agent 按需补读后返回一道题或明确不足。
    概念间并发数由 concurrency 控制，同概念材料不拆批。
    write_mode 只控制候选目标表；每次按当前参数读取材料，原生模型调用日志复用相同请求。
    """
    if config.get('through') == 'review':
        return run_question_review(config)
    if not config.get('agent_config'):
        raise ValueError('T2I V2 authoring requires agent_config; ordinary prompt authoring is retired')
    if config.get('authoring_variants'):
        return run_comparison(config)
    root = resolve_root()
    run = Path(config['run']).absolute()
    if run.parent != root / DATASETS or not run.name or run.suffix:
        raise ValueError('run must be a suffix-free name under ' + str(root / DATASETS))
    target_uri = (
        str((root / config['target_uri']).resolve()) if config['target_uri'] else str(run.parent / f'candidates__{run.name}.lance')
    )
    screening_source = config.get('screening_source')
    concept_audit_source = config.get('concept_audit_source')
    cohort_source = config.get('cohort_source')
    if concept_audit_source is not None:
        audit_uri = str((root / concept_audit_source['uri']).resolve())
        outputs = [target_uri, *(str(run.parent / f'{name}__{run.name}.lance')
                                for name in ('inputs', 'designs', 'calls', 'records'))]
        if audit_uri in outputs:
            raise ValueError('concept_audit_source must differ from all output tables')
    if screening_source is not None:
        screening_uri = str((root / screening_source['uri']).resolve())
        outputs = [target_uri, *(str(run.parent / f'{name}__{run.name}.lance')
                                for name in ('inputs', 'designs', 'calls', 'records'))]
        if screening_uri in outputs:
            raise ValueError('screening_source must differ from all output tables')
    started = perf_counter()
    request_times = {}

    def log(message):
        """立即输出到 CLI/notebook；只记阶段摘要，不打印材料、图片字节或凭据。"""
        print(f'[T2I V2][{run.name}][+{perf_counter() - started:.1f}s] {message}', flush=True)

    def request_started(concept, image_count):
        """由原生调用的 when 在执行时记录起点；不把上游预取时间算作请求耗时。"""
        request_times[concept] = perf_counter()
        log(f'开始出题：{concept}，图片={image_count}，超时={config["timeout_s"]}s')
        return True

    def log_response(row):
        """记录实际返回/跳过并原样传递该行；计时不参与模型输入、判据或输出表。"""
        if row['status'] == 'ready':
            error = row.get('design_error') or {}
            call = row.get('design_call') or error.get('call', {})
            log(
                f'出题返回：{row["concept"]}，本次等待={perf_counter() - request_times[row["concept"]]:.1f}s，'
                f'复用={call.get("reused", "未知")}，错误={error.get("type", "无")} {error.get("detail", "")}'
            )
        else:
            log(f'跳过出题：{row["concept"]}，状态={row["status"]}，原因={row["reason"]}')
        return row

    log(
        f'开始运行，等待运行锁；概念数={config["sample_size"] if screening_source or concept_audit_source or cohort_source else len(config["concepts"])}，模型={config["model"]}，'
        f'模式={config["mode"]}，每概念交付一题，补读={config.get("document_reads", False)}、并发={config["concurrency"]}；续跑先查调用日志'
    )
    with run_lock(root / '_demiflow' / 'benchmark_t2i_v2' / run.name):
        # 锁住同名运行，避免并发覆盖同一组输出；不限制修改配置或代码。
        records = RunTables(root, str(DATASETS / f'records__{run.name}.lance'))
        call_store = {'root': str(root), 'relative_uri': str(DATASETS / f'calls__{run.name}.lance')}
        records.save({'complete': False, 'phase': 'running', 'calls': call_store})
        log('已取得运行锁，使用本次传入的来源和参数')
        # 1. 上游合并 keep 是通过契约；固定结果版本过滤、去重后选样，保留完整 taxonomy。
        # Dataset 保留选中行供材料 join；仅小批概念名称用于材料 reader 过滤及运行摘要。
        selection = None
        if cohort_source is not None:
            concept_dataset = data.read_lance(str(root / cohort_source['uri']), version=cohort_source['version'])
            names = concept_dataset.select_columns(['concept', 'concept_id']).take(config['sample_size'] + 1)
            if len(names) != config['sample_size'] or len({r['concept_id'] for r in names}) != len(names):
                raise ValueError('Cohort cardinality/keys differ from the declared sample')
            concepts = [row['concept'] for row in names]
            selection = {'kind': 'paired_cohort', 'source': cohort_source, 'concepts': concepts,
                         'sample_size': len(concepts), 'variant': config['authoring_variant']}
        elif concept_audit_source is not None:
            # 已采纳结果才进入新主线；任务 hold 留在上游，不混作模型调用失败。
            # 原名关联材料，规范定义/核心事实随输入持久化，保留完整版本来源。
            adopted = (data.read_lance(audit_uri, version=concept_audit_source['version'], columns=[
                'source_record_id','concept_id','original_name','assessment_id','status','adoption_status',
                'review_note','assessment','taxonomy','selected'])
                .filter(lambda row: row['adoption_status'] == 'accepted' and row['assessment']['task_status'] == 'ready')
                .map(adopted_concept, fn_kwargs={'source': {'uri':audit_uri,'version':concept_audit_source['version']}})
                .materialize())
            duplicates = (adopted.reduce_by_key('concept', lambda acc, row:
                {'concept':row['concept'], 'count':(acc['count'] if acc else 0)+1})
                .filter(lambda row: row['count'] != 1).take(1))
            if duplicates:
                raise ValueError('Duplicate original concept in adopted snapshot')
            selected = (adopted.map(lambda row: {'group':'keep', **row})
                .reduce_by_key('group', partial(select_screened_concepts,
                    sample_size=config['sample_size'], sample_seed=config['sample_seed'])).materialize())
            summary = selected.take(1)
            eligible_count = summary[0]['eligible_count'] if summary else 0
            if eligible_count < config['sample_size']:
                raise ValueError(f'Only {eligible_count} adopted/task-ready concepts; need {config["sample_size"]}')
            concept_dataset = selected.flat_map(lambda row: [
                {key:item[key] for key in ('concept','taxonomy','concept_record')} for item in row['selected']])
            concepts = [item['concept'] for item in summary[0]['selected']]
            selection = {'source': {'uri':audit_uri,'version':concept_audit_source['version']},
                         'kind':'concept_audit', 'eligible_count':eligible_count, 'sample_size':len(concepts),
                         'sample_seed':config['sample_seed'], 'concepts':concepts}
            log(f'审定采纳且可出题={eligible_count}，本批={concepts}；按原名关联图文')
        elif screening_source is not None:
            log(f'读取粗筛通过表：{screening_uri}@{screening_source["version"]}；'
                f'仅 screened/keep，选 {config["sample_size"]} 个，seed={config["sample_seed"]}')
            selected = (
                data.read_lance(screening_uri, version=screening_source['version'], columns=['concept', 'taxonomy'],
                                filter="status = 'screened' AND decision = 'keep'")
                .reduce_by_key('concept', lambda acc, row: {
                    'concept': row['concept'],
                    'taxonomy': list(dict.fromkeys((acc['taxonomy'] if acc else []) + (row['taxonomy'] or []))),
                })
                .map(lambda row: {'group': 'keep', **row})
                .reduce_by_key('group', partial(select_screened_concepts,
                    sample_size=config['sample_size'], sample_seed=config['sample_seed']))
                .materialize()
            )
            summary = selected.take(1)
            eligible_count = summary[0]['eligible_count'] if summary else 0
            if eligible_count < config['sample_size']:
                raise ValueError(f'Only {eligible_count} screened/keep concepts; need {config["sample_size"]}')
            concept_dataset = selected.flat_map(lambda row: [
                {'concept': item['concept'], 'taxonomy': item['taxonomy']} for item in row['selected']])
            concepts = [item['concept'] for item in summary[0]['selected']]
            selection = {'source': {'uri': screening_uri, 'version': screening_source['version']},
                         'eligible_count': eligible_count, 'sample_size': len(concepts),
                         'sample_seed': config['sample_seed'], 'concepts': concepts}
            log(f'粗筛通过概念={eligible_count}，本批={concepts}；名单随 inputs 落表')
        else:
            concepts = config['concepts']
            # 历史显式名称入口未提供分类，不凭名称推造；粗筛表入口携带其固定版本完整 taxonomy。
            concept_dataset = data.from_items([{'concept': c, 'taxonomy': []} for c in concepts])
        # 出题仅走完整 agent 配置；普通 prompt 文件只用于后续评审。
        agent = load_agent_config(config['agent_config'])
        pack = agent.prompt_pack
        options = {('offline_store' if config['mode'] == 'offline' else 'sqlite_journal'):
                   sqlite_call_options(**call_store)}
        input_uri = str(run.parent / f'inputs__{run.name}.lance')
        materials_started = perf_counter()
        log(
            f'开始准备材料：文章={config["article_source"]}，图片={config["visual_source"]}；按概念取正文和独立图片'
        )

        quoted_concepts = ["'" + c.replace("'", "''") + "'" for c in concepts]

        # 文章每行是一篇已审核文章；同概念的多篇文章均作为材料，不去重或判冲突。
        # preparation 负责正文清洗；这里仅展开主题正文，忽略引用、配图和内部审核字段。
        if config['article_source'] is not None:
            texts = (
                data.read_lance(
                    str(root / config['article_source']['uri']),
                    version=config['article_source']['version'], columns=['concept', 'content'],
                    filter="review_status = 'reviewed' AND concept IN (" + ','.join(quoted_concepts) + ')',
                )
                .flat_map(lambda row: [
                    {'concept': row['concept'], 'text': {
                        'kind': 'text', 'title': topic['title'], 'text': paragraph,
                    }}
                    for topic in row['content'] or [] for paragraph in topic['content']['paragraphs'] or []
                ])
                .reduce_by_key('concept', lambda acc, row: {
                    'concept': row['concept'], 'texts': (acc['texts'] if acc else []) + [row['text']],
                })
            )
        else:
            texts = data.from_arrow(pa.table({'concept': pa.array([], type=pa.string())}))

        # 图片每行包含多个概念关系；仅按公开发布/审核状态选取本次概念的图片。
        # 使用 preparation 已交付的存储引用，不读取来源或审核 JSON，不与文章匹配。
        if config['visual_source'] is not None:
            images = (
                data.read_lance(
                    str(root / config['visual_source']['uri']),
                    version=config['visual_source']['version'],
                    columns=['sha256', 'image_uri', 'concept_assessments'],
                    filter=' OR '.join('array_contains(published_concepts, ' + c + ')' for c in quoted_concepts),
                )
                .flat_map(lambda row: [
                    {'concept': assessment['concept'], 'sha256': row['sha256'],
                     'image_uri': row['image_uri']}
                    for assessment in row['concept_assessments'] or []
                    if assessment['concept'] in concepts
                    and assessment['published'] and assessment['review_status'] == 'keep'
                ])
                # 在聚合中限制图片数量，不为未选中的图片读取字节或解析引用。
                .reduce_by_key('concept', lambda acc, row: {
                    'concept': row['concept'],
                    'images': ((acc['images'] if acc else []) + [row])[:config['max_reference_images']],
                })
                .map(lambda row: {
                    'concept': row['concept'],
                    'images': [
                        {'kind': 'image', 'object_ref': image_object_ref(im)} for im in row['images']
                    ],
                })
            )
        else:
            images = data.from_arrow(pa.table({'concept': pa.array([], type=pa.string())}))

        # 配置中的每个概念均进入模型节点；缺少文字或图片时列表为空，不生成缺失状态。
        # 文字依据、正例图分别编号；正例图顺序与随后编码顺序一致。
        log(f'开始执行材料链并写输入表：{input_uri}')
        (
            concept_dataset
            .join(texts, on='concept', how='left')
            .join(images, on='concept', how='left')
            .map(lambda row: {
                'concept': row['concept'], 'taxonomy': row['taxonomy'],
                'concept_record': row.get('concept_record'),
                'status': 'ready', 'reason': '',
                'evidence_json': json.dumps([
                    {'number': i, **ref}
                    for i, ref in enumerate(row.get('texts', []), 1)
                ], ensure_ascii=False),
                'authoring_variant': config.get('authoring_variant', 'standard'),
                'authoring_images_json': authoring_images(row, config['authoring_variant']) if cohort_source else
                    json.dumps([{'number': i, **ref} for i, ref in enumerate(row.get('images', []), 1)],
                               ensure_ascii=False),
            })
            .write_lance(input_uri, mode='overwrite', schema=INPUTS)
        )
        inputs = {'uri': input_uri, 'version': lance.dataset(input_uri).version}
        log(f'材料准备并落表完成：耗时={perf_counter() - materials_started:.1f}s，版本={inputs["version"]}')
        if config.get('through') == 'inputs':
            state = {'complete': False, 'phase': 'inputs', 'inputs': inputs, 'selection': selection, 'calls': call_store}
            records.save(state)
            return state
        # 3. 每概念一行；启用 agent 时行内多轮补读，最终仍交付一个业务结果。
        # materialize 执行异步链，完成后按明确 schema 写设计表。
        # 请求用的图片字节在缓存前投影掉；空结果也能写成有效空表。
        designs_uri = str(run.parent / f'designs__{run.name}.lance')
        log(f'开始执行出题，交付设计表：{designs_uri}；模式={config["through"]}')
        prepared = (
            data.read_lance(inputs['uri'], version=inputs['version'])
            .map(
                lambda row: log(f'准备概念输入：{row["concept"]}，状态={row["status"]}；读取审定证据与可用图片') or row
            )
            .map(
                prepare_request,
                fn_kwargs={
                    'max_context_chars': config['max_context_chars'],
                    'prompt_chars': len(pack.prompt_definitions['design_question'].template.source),
                    'document_reads': config.get('document_reads', False),
                    'document_paths': config['mode'] == 'codex' and agent.environment.resources is None,
                },
            )
        )
        # 外层仍是一行一概念；按 YAML 的资源绑定提供文档回调或旧文件入口。
        # 业务行函数只提供资料，不启动子数据流或实现工具循环。
        node_arguments = dict(
                config=agent,
                options=options,
                max_requests=config['max_calls'],
                inputs={'payload': 'prompt_payload', 'concept_context': 'concept_context', 'images': 'prompt_images'},
                output='design_result',
                call_output='design_call',
                error_output='design_error',
                when=lambda row: row['status'] == 'ready'
                and request_started(row['concept'], len(row['prompt_images'])),
                concurrency=config['concurrency'],
                queue_depth=config['queue_depth'],
        )
        designed = prepared.agentmap_async('design_question', **node_arguments)
        checked = (
            designed
            .map(log_response)
            .map(check_response, fn_kwargs={
                'question_schema': pack.prompt_definitions['design_question'].response_schema[
                    'properties']['result']['properties']['question'],
            })
            # 只留下设计表字段，去掉用于请求的图片字节和原始响应；字段顺序与 Arrow schema 一致。
            .map(lambda row: {name: row[name] for name in DESIGNS.names})
            .map(
                lambda row: log(
                    f'概念结果：{row["concept"]}，状态={row["status"]}，'
                    f'题数={int(row["question"] is not None)}，原因={row["reason"] or "无"}'
                )
                or row
            )
        )
        if config['through'] == 'probe':
            probe = config['probe']
            review_pack = load_prompt_pack(PROMPTS / 'tasks.yaml')
            review_definition = review_pack.prompt_definitions['review_answer']
            review_calls = {'root': str(root), 'relative_uri': str(DATASETS / f'probe_calls__{run.name}.lance')}
            review_options = ({'offline_store': sqlite_call_options(**review_calls)}
                if config['mode'] == 'offline' else {
                    'sqlite_journal': {**sqlite_call_options(**review_calls), 'max_requests': probe['max_review_calls']},
                    'timeout_s': probe['review_timeout_s'], 'verify_model': 'listed',
                    'require_finish_reason_stop': True, 'trust_env': False, 'gateway': 'litellm',
                    'request_options': {'max_tokens': probe['review_max_output_tokens'],
                                        'reasoning_effort': 'xhigh'},
                })
            stage_uris = {'designs': designs_uri, 'candidates': target_uri,
                'generations': str(run.parent / f'probe_generations__{run.name}.lance'),
                'reviews': str(run.parent / f'probe_reviews__{run.name}.lance')}
            start_versions = {name: lance.dataset(uri).version if Path(uri).exists() else 0
                              for name, uri in stage_uris.items()}
            probe_state = {'stage_uris': stage_uris, 'start_versions': start_versions,
                'settings': probe, 'calls': review_calls,
                'review_model': review_definition.model.name, 'review_version': review_definition.version,
                'review_reasoning_effort': 'xhigh'}
            state = {'complete': False, 'phase': 'probing', 'inputs': inputs, 'calls': call_store,
                'selection': selection, 'probe': probe_state,
                'sources': {'article': config['article_source'], 'visual': config['visual_source'],
                            'concept_audit': concept_audit_source}}
            records.save(state)
            # 缓存身份只包含作答相关设置；换评审 prompt/模型或并发不重画。
            generation_identity = digest({'implementation': 't2i-inline-probe-1', **{
                key: probe[key] for key in ('model', 'revision', 'endpoints', 'image_size',
                                            'steps', 'run_seed', 'max_image_bytes')}})
            log(f'流水探测已接入：Z-Image → {review_definition.model.name}，每题一次联合评审')
            stats = (
                checked
                .save_lance(designs_uri, schema=DESIGNS, key='concept', stage='designs', max_batch=1, queue_depth=1)
                .filter(lambda row: row['status'] == 'candidate')
                .map(candidate_row)
                .save_lance(target_uri, schema=QUESTIONS, key='task_id', stage='candidates', max_batch=1, queue_depth=1)
                .map(answer_input)
                .map_cached(AnswerImage(probe, LocalObjectStore(root / 'objects')),
                    cache_dir=root / '_demiflow' / 'benchmark_t2i_v2' / run.name / 'generation_cache',
                    version=generation_identity)
                .map(lambda row: log(f'探测生图：{row["concept"]}，状态={row["status"]}，原因={row["reason"]}') or row)
                .save_lance(stage_uris['generations'], schema=PROBE_GENERATIONS, key='task_id',
                    stage='generations', max_batch=1, queue_depth=1, output_ref='generation_source')
                .map(prepare_review, fn_kwargs={'max_context_chars': probe['review_max_context_chars'],
                    'prompt_chars': len(review_definition.template.source)})
                .map_prompt_async('review_answer', config=review_pack, options=review_options,
                    max_requests=probe['max_review_calls'], inputs={'payload': 'review_payload', 'images': 'review_images'},
                    output='review_result', call_output='review_call', error_output='review_error',
                    when=lambda row: row['review_status'] == 'ready',
                    concurrency=probe['review_concurrency'], queue_depth=probe['review_queue_depth'])
                .map(check_review)
                .map(lambda row: log(f'探测评审：{row["concept"]}，状态={row["review_status"]}，'
                    f'结论={(row["review"] or {}).get("verdict", "无")}') or row)
                .save_lance(stage_uris['reviews'], schema=PROBE_REVIEWS, key='task_id',
                    stage='reviews', max_batch=1, queue_depth=1)
                .run_stream(on_drain=lambda stats: records.save({**state,
                    'probe': {**probe_state, 'outputs': stats.outputs, 'miss': stats.miss}}))
            )
            outputs = stats.outputs
            design_keys = data.read_lance(**outputs['designs'], columns=['concept', 'status']).take(len(concepts)+1)
            if len(design_keys) != len(concepts) or {r['concept'] for r in design_keys} != set(concepts):
                raise ValueError('Probe design output keys do not match this run input; partial snapshots retained')
            keys = {}
            for stage in ('candidates', 'generations', 'reviews'):
                rows = data.read_lance(**outputs[stage], columns=['task_id']).take(len(concepts)+1)
                keys[stage] = {r['task_id'] for r in rows}
                if len(rows) != len(keys[stage]) or len(rows) > len(concepts):
                    raise ValueError('Duplicate or excess probe task IDs: ' + stage)
            if keys['candidates'] != keys['generations'] or keys['candidates'] != keys['reviews']:
                raise ValueError('Probe output keys differ across stages; partial snapshots retained')
            counts = {}
            for row in design_keys:
                counts[row['status']] = counts.get(row['status'], 0) + 1
            probe_counts = {}
            for stage, field in (('generations', 'status'), ('reviews', 'review_status')):
                probe_counts[stage] = {row[field]: row['count'] for row in
                    data.read_lance(**outputs[stage], columns=[field]).reduce_by_key(field,
                        lambda acc, row, field=field: {field: row[field], 'count': (acc['count'] if acc else 0)+1}).take_all()}
            state.update(phase='probe', counts=counts, candidate_count=len(keys['candidates']),
                designs=outputs['designs'], candidates=outputs['candidates'],
                complete=(not stats.miss and set(counts) <= {'candidate', 'insufficient'}
                    and set(probe_counts['generations']) <= {'generated'}
                    and set(probe_counts['reviews']) <= {'reviewed'}),
                probe={**probe_state, 'outputs': outputs, 'counts': probe_counts, 'miss': stats.miss})
            records.save(state)
            log(f'出题及流水探测结束：出题={counts}，探测={probe_counts}，complete={state["complete"]}')
            return append_question_review(config, state, records)
        checked.materialize().write_lance(designs_uri, mode='overwrite', schema=DESIGNS)
        designs_version = lance.dataset(designs_uri).version
        log(f'出题执行及设计表写入完成：版本={designs_version}')
        designs = data.read_lance(designs_uri, version=designs_version)
        delivered = designs.select_columns(['concept']).take(len(concepts) + 1)
        if len(delivered) != len(concepts) or {r['concept'] for r in delivered} != set(concepts):
            raise ValueError('Design output keys do not match this run input')
        # 每概念只有一个 question；有题的行直接投影，空题及失败原因留在设计表。
        questions = (
            designs.filter(lambda row: row['status'] == 'candidate')
            .map(candidate_row)
            .materialize()
        )

        # 4. 候选 Dataset 物化后供内容指纹和目标 writer 复用；不取出行再重建 Dataset。
        # 指纹仍汇总本批候选（文字和 Blob 引用，不含图片字节），保留续跑防重复追加的提交边界。
        # append 复用同目标同内容的提交记录，避免重复追加；overwrite 每次执行覆盖。
        # 零候选也写出具有 QUESTIONS schema 的空结果，覆盖模式下目标表会被清空。
        candidate_keys = questions.map(lambda row: {'task_id': row['task_id'], 'digest': digest(row)}).take(len(concepts) + 1)
        output_key = 'candidates/' + digest([target_uri, config['write_mode'], sorted(candidate_keys, key=lambda r:r['task_id'])])
        output = records.find_commit(*output_key.split("/", 1)) if config['write_mode'] == 'append' else None
        if output is None:
            log(f'开始写候选表：题数={len(candidate_keys)}，模式={config["write_mode"]}，目标={target_uri}')
            with DatasetCommit(records.manifest_path.with_suffix('') / 'append_intents', output_key, target_uri, mode=config['write_mode']) as commit:
                if commit.output is None:
                    receipt = questions.write_lance(target_uri, mode=config['write_mode'], schema=QUESTIONS, return_receipt=True)
                    commit.confirm(receipt)
                output = commit.output
            if config['write_mode'] == 'append':
                records.save_commit(*output_key.split("/", 1), output)
            log(f'候选表写入完成：版本={output["version"]}')
        else:
            log(f'复用已写候选表：{output["uri"]}，版本={output["version"]}')

        # 按设计表的 status 统计概念数，将统计、本次题数和已提交的表路径/版本保存到 state。
        counts = {
            row['status']: row['count']
            for row in designs.reduce_by_key(
                'status', lambda acc, row: {'status': row['status'], 'count': (acc['count'] if acc else 0) + 1}
            ).take_all()
        }
        # 所有概念均为 candidate（已出题）或 insufficient（有理由的零题）时才标记 complete；不表示审题通过。
        state = {
            'complete': set(counts) <= {'candidate', 'insufficient'},
            'counts': counts,
            'candidate_count': len(candidate_keys),
            'inputs': inputs,
            'designs': {'uri': designs_uri, 'version': designs_version},
            'candidates': output,
            'calls': call_store,
            'selection': selection,
            'sources': {'article': config['article_source'], 'visual': config['visual_source'],
                        'concept_audit':concept_audit_source},
        }
        # 此处只在前面的写表成功后执行；offline 的 pending 可补交响应后同名续跑。
        records.save(state)
        log(f'运行结束：状态={counts}，候选={len(candidate_keys)} 题，complete={state["complete"]}')
        return append_question_review(config, state, records)


def run_comparison(cfg):
    """正式配对编排：同版审核图关联 → 按配置均衡选样 → 同一cohort的两次正式出题。

    仅消费已提交的有效图片判断，允许来源批次整体未完成；失败行不计正例。
    concept_id/assessment_id/SHA 去重，跨批有效判断冲突的图片不计正例。
    每个分支使用独立日志与额度；只有两版均交付完整概念键才完成。
    """
    root = resolve_root()
    run = Path(cfg['run']).absolute()
    if run.parent != root / DATASETS or run.suffix:
        raise ValueError('Comparison run must be a suffix-free name under this pipeline datasets')
    audit = {**cfg['concept_audit_source'], 'uri': str(root / cfg['concept_audit_source']['uri'])}
    review_refs = [{**ref, 'uri': str(root / ref['uri'])} for ref in cfg['positive_review_sources']]
    cohort_uri = str(run.parent / f'cohort__{run.name}.lance')
    with run_lock(root / '_demiflow' / 'benchmark_t2i_v2' / run.name):
        records = RunTables(root, str(DATASETS / f'records__{run.name}.lance'))
        records.save({'complete': False, 'phase': 'selecting'})
        print(f'[T2I paired] 固定采纳来源={audit}，图审快照={len(review_refs)}；'
              f'门槛={cfg["min_positive_images"]}张，选择={cfg["sample_size"]}概念', flush=True)
        if cfg.get('cohort_revision'):
            print(f'[T2I cohort revision] 原第一类目标={cfg["cohort_revision"]["common_limit"]}，'
                  f'第一类正例下限={cfg["cohort_revision"]["min_common_positive_images"]}；'
                  '优先替换纯第3类，全部规则和固定来源保存到修订摘要', flush=True)
        taxonomy = None
        if cfg.get('sampling_taxonomy_source'):
            source = {**cfg['sampling_taxonomy_source'], 'uri': str(root / cfg['sampling_taxonomy_source']['uri'])}
            summaries = data.read_lance(**source, columns=[
                'run', 'phase', 'complete', 'quality_passed', 'outputs']).take(2)
            if len(summaries) != 1:
                raise ValueError('Sampling taxonomy summary must contain exactly one run')
            taxonomy = taxonomy_snapshot(summaries[0], source)
            trees = (data.read_lance(**taxonomy['outputs']['nodes'], batch_size=8,
                    batch_readahead=1, fragment_readahead=1)
                .map(lambda row: {'group': 'tree', 'node': row})
                .reduce_by_key('group', collect_nodes).map(sampling_tree).take(1))
            if not trees:
                raise ValueError('Sampling taxonomy tree is empty')
            tree = trees[0]
            taxonomy['tree_hash'] = tree['tree_hash']
            assignments = (data.read_lance(**taxonomy['outputs']['placements'], columns=[
                'source_record_id', 'assessment_id', 'status', 'node_id', 'tree_hash'])
                .reduce_by_key(['source_record_id', 'assessment_id'], unique_placement)
                .filter(lambda row: row['status'] == 'assigned')
                .map(placement_category, fn_kwargs={'tree': tree}).materialize())
        adopted = (data.read_lance(**audit, columns=[
            'source_record_id', 'concept_id', 'original_name', 'assessment_id', 'status',
            'adoption_status', 'review_note', 'assessment', 'taxonomy', 'old_taxonomy', 'selected'])
            .filter(lambda row: row['adoption_status'] == 'accepted' and row['assessment']['task_status'] == 'ready')
            .map(lambda row: {**adopted_concept(row, source=audit),
                'concept_id': row['concept_id'], 'assessment_id': row['assessment_id'],
                'source_record_id': row['source_record_id'], 'sampling_node_id': None,
                'sampling_taxonomy': row['taxonomy'] or row['old_taxonomy'] or []}).materialize())
        duplicate = (adopted.reduce_by_key('concept_id', lambda a,r: {
            'concept_id':r['concept_id'], 'count':(a['count'] if a else 0)+1})
            .filter(lambda r:r['count'] != 1).take(1))
        if duplicate:
            raise ValueError('Duplicate concept_id in adopted source')
        # 同类已提交图审快照的显式 union。只读判断、键及独立对象，不读像素、监督材料或调用正文。
        review_sets = []
        for source in review_refs:
            review_sets.append(data.read_lance(**source,
                columns=['concept_id','assessment_id','sha256','image_uri','status','aligned','combined_review'],
                filter="status = 'reviewed'")
                .map(lambda row, ref=source: {**{key:row[key] for key in (
                    'concept_id','assessment_id','sha256','image_uri','aligned')},
                    'decision': row['combined_review']['decision'], 'review_source': ref}))
        excluded_images = {item['sha256'] for item in (cfg.get('cohort_revision') or {}).get('image_exclusions', [])}
        positive_images = (review_sets[0].union(*review_sets[1:])
            .reduce_by_key(['concept_id','assessment_id','sha256'], merge_review)
            .filter(lambda row: row['positive'] and bool(row['image_uri']) and row['sha256'] not in excluded_images)
            .reduce_by_key(['concept_id','assessment_id'], partial(collect_positives,
                maximum=cfg['max_reference_images'], seed=cfg['sample_seed'])))
        revision = cfg.get('cohort_revision')
        covered = adopted.join(positive_images, on=['concept_id','assessment_id'], how='left')
        if revision:
            category_sets = []
            for source in revision['case_category_sources']:
                ref = {key: source[key] for key in ('uri', 'version')}
                category_sets.append(data.read_lance(**ref, columns=['concept', 'final_category'],
                    filter="final_selected = true" if source['kind'] == 'adjudications' else None)
                    .filter(lambda r: r['final_category'] in ('1', '2', '3')))
            categories = (category_sets[0].union(*category_sets[1:])
                .reduce_by_key('concept', lambda acc, row: {'concept': row['concept'],
                    'original_categories': sorted(set((acc['original_categories'] if acc else [])
                                                     + [row['final_category']]))}))
            covered = covered.join(categories, on='concept', how='left')
        eligible = (covered.filter(lambda row: (row.get('positive_image_count') or 0) >= (
            revision['min_common_positive_images'] if revision and '1' in (row.get('original_categories') or [])
            else cfg['min_positive_images']))
            .map(lambda row: {**row, 'positive_image_count': row.get('positive_image_count') or 0,
                              'positive_images': row.get('positive_images') or []}).materialize())
        before_taxonomy = eligible.count()
        unassigned_common = []
        if taxonomy:
            if revision and revision.get('common_allow_unassigned_taxonomy'):
                matched = (eligible.drop_columns(['sampling_taxonomy', 'sampling_node_id'])
                    .join(assignments, on=['source_record_id', 'assessment_id'], how='left')
                    .filter(lambda row: bool(row.get('sampling_node_id'))
                            or '1' in (row.get('original_categories') or [])).materialize())
                unassigned_common = [row['concept'] for row in matched
                    .filter(lambda row: not row.get('sampling_node_id')).select_columns(['concept']).take(4097)]
                if len(unassigned_common) > 4096:
                    raise ValueError('Too many unmatched common concepts')
                eligible = matched.map(lambda row: {**row,
                    'sampling_node_id': row.get('sampling_node_id'),
                    'sampling_taxonomy': row.get('sampling_taxonomy') or ['未匹配固定 taxonomy']}).materialize()
            else:
                eligible = (eligible.drop_columns(['sampling_taxonomy', 'sampling_node_id'])
                    .join(assignments, on=['source_record_id', 'assessment_id'], how='inner').materialize())
        keys = eligible.map(lambda row: {**sampling_key(row, depth=cfg['sampling_depth']),
            **({'original_categories': row.get('original_categories') or []} if revision else {})})
        if revision:
            from benchmark.t2i.v2.operators.comparison import revise_cohort
            previous = (data.read_lance(**revision['base_cohort'], columns=[
                'concept', 'concept_id', 'assessment_id', 'selection_rank'])
                .map(lambda row: {'concept_id': row['concept_id'], 'assessment_id': row['assessment_id'],
                                  'base_concept': row['concept'], 'base_rank': row['selection_rank']}).materialize())
            ranks = previous.select_columns(['concept_id', 'base_concept', 'base_rank']).take(cfg['sample_size'] + 1)
            if (len(ranks) != cfg['sample_size'] or len({r['concept_id'] for r in ranks}) != len(ranks)
                    or {r['base_rank'] for r in ranks} != set(range(1, cfg['sample_size'] + 1))):
                raise ValueError('Revision base must have exactly sample_size unique concepts and consecutive ranks')
            keys = keys.join(previous, on=['concept_id', 'assessment_id'], how='left')
        picked = (keys
            .map(lambda row:{'group':'all', **row})
            .reduce_by_key('group', collect_sampling_keys)
            .map(revise_cohort if revision else choose_balanced,
                 fn_kwargs={'size':cfg['sample_size'], 'seed':cfg['sample_seed'],
                            **({'common_limit': revision['common_limit']} if revision else {})}).materialize())
        control = picked.take(1)
        if not control:
            raise ValueError('No eligible ready concepts with sufficient reviewed images')
        if revision:
            selected_ids = {r['concept_id'] for r in control[0]['selected']}
            previous_ids = {r['concept_id'] for r in ranks}
            control[0]['revision'].update(
                retained_count=len(selected_ids & previous_ids),
                added=[r['concept'] for r in control[0]['selected'] if r['concept_id'] not in previous_ids],
                removed=[r['base_concept'] for r in sorted(ranks, key=lambda r:r['base_rank'])
                         if r['concept_id'] not in selected_ids])
        chosen = picked.flat_map(lambda row: row['selected'])
        (eligible.join(chosen.select_columns(['concept_id','assessment_id','sampling_category','selection_rank']),
                       on=['concept_id','assessment_id'], how='inner')
            .map(lambda row:{name:row[name] for name in COHORT.names})
            .write_lance(cohort_uri, schema=COHORT, mode='overwrite'))
        cohort = {'uri':cohort_uri, 'version':lance.dataset(cohort_uri).version}
        selection = {'kind':'positive_image_paired', 'eligible_count':control[0]['count'],
            'ready_with_positive_images':before_taxonomy,
            'excluded_without_matching_taxonomy':before_taxonomy-control[0]['count'],
            'sample_size':cfg['sample_size'], 'sample_seed':cfg['sample_seed'],
            'min_positive_images':cfg['min_positive_images'], 'sampling_depth':cfg['sampling_depth'],
            'sampling_policy':('fixed taxonomy primary path; exact source_record_id/assessment_id; hierarchical round robin'
                if taxonomy else 'first supplied taxonomy path; fallback old_taxonomy; hierarchical round robin'),
            'cohort':cohort, 'variants':{}, 'max_calls_per_variant':cfg['max_calls'],
            'max_calls_total':cfg['max_calls'] * len(cfg['authoring_variants'])}
        if revision:
            selection['revision'] = {**revision, **control[0]['revision'],
                'unassigned_common_taxonomy': sorted(unassigned_common),
                'policy': 'include original category 1; retain base cohort; replace pure category 3 from highest rank'}
        state = {'complete':False, 'phase':'inputs', 'inputs':cohort, 'selection':selection,
                 'sources':{'concept_audit':audit, 'positive_reviews':review_refs,
                            **({'sampling_taxonomy':taxonomy} if taxonomy else {})}}
        records.save(state)
        print(f'[T2I paired] 合格={control[0]["count"]}，已提交共同输入={cfg["sample_size"]}，{cohort}', flush=True)
        if cfg['through'] == 'inputs':
            return state
        for variant in cfg['authoring_variants']:
            branch = {**cfg, 'run':str(run.with_name(run.name + '__' + variant)),
                'concept_audit_source':None, 'cohort_source':cohort, 'authoring_variants':None,
                'sampling_taxonomy_source':None,
                'cohort_revision':None,
                'positive_review_sources':None, 'authoring_variant':variant, 'target_uri':None}
            selection['variants'][variant] = run_pipeline(branch)
            records.save({**state, 'phase':'authoring'})
        refs = [selection['variants'][v]['designs'] for v in cfg['authoring_variants']]
        combined_uri = str(run.parent / f'comparisons__{run.name}.lance')
        data.read_lance(**refs[0]).union(data.read_lance(**refs[1])).write_lance(
            combined_uri, schema=DESIGNS, mode='overwrite')
        state.update(phase='comparison', designs={'uri':combined_uri,'version':lance.dataset(combined_uri).version},
            complete=all(s['complete'] for s in selection['variants'].values()),
            candidate_count=sum(s['candidate_count'] for s in selection['variants'].values()),
            counts={v:s['counts'] for v,s in selection['variants'].items()})
        records.save(state)
        return state


def question_review_runtime(agent_config, max_calls, concurrency=1, queue_depth=1, mode=None):
    """提前核对审题agent与运行预算；尚无候选表时也能验证配置。"""
    if agent_config is None:
        raise ValueError('Question review requires agent_config; use prompts/question_review.yaml')
    path = str(Path(agent_config).resolve())
    agent = load_agent_config(path)
    if set(agent.prompt_pack.prompt_definitions) != {'review_question'}:
        raise ValueError('Question review requires the review_question agent task')
    if agent.environment.runtime != 'codex' and mode != 'offline':
        raise ValueError('Question review requires native Codex file tools; offline is for fixtures only')
    expected = 'codex' if agent.environment.runtime == 'codex' else 'modelhub'
    mode = mode or expected
    if mode not in ({'codex'} if expected == 'codex' else {'modelhub', 'offline'}):
        raise ValueError('Question review mode conflicts with agent runtime')
    if type(max_calls) is not int or not 0 <= max_calls <= agent.max_requests:
        raise ValueError('Question review needs an explicit max_calls within agent budget')
    if type(concurrency) is not int or not 1 <= concurrency <= 8:
        raise ValueError('Question review concurrency must be 1..8')
    if type(queue_depth) is not int or not 1 <= queue_depth <= 8:
        raise ValueError('Question review queue_depth must be 1..8')
    return path, mode


def review_config(*, run, review_source, sample_size, agent_config,
                  max_calls, concurrency, queue_depth, mode, review_task_ids=None):
    """同一入口的后续审题配置；固定候选范围与独立预算，不进入出题/生图阶段。"""
    if type(sample_size) is not int or not 0 <= sample_size <= 1000:
        raise ValueError('Question review requires an explicit sample_size in 0..1000')
    if (not isinstance(review_source, dict) or set(review_source) != {'uri', 'version'}
            or not review_source['uri'] or type(review_source['version']) is not int
            or review_source['version'] < 1):
        raise ValueError('Question review requires a fixed review_source uri/version')
    if review_task_ids is not None:
        if (not isinstance(review_task_ids, list) or len(review_task_ids) != sample_size
                or any(not isinstance(key, str) or not key.strip() for key in review_task_ids)
                or len(set(review_task_ids)) != len(review_task_ids)):
            raise ValueError('review_task_ids must contain sample_size unique nonempty task IDs')
        review_task_ids = sorted(review_task_ids)
    path, mode = question_review_runtime(agent_config, max_calls, concurrency, queue_depth, mode)
    return {'run': str(run), 'through': 'review', 'review_source': review_source,
            'review_task_ids': review_task_ids, 'sample_size': sample_size,
            'agent_config': path, 'max_calls': max_calls, 'concurrency': concurrency,
            'queue_depth': queue_depth, 'mode': mode}


def run_question_review(cfg):
    """固定题目与实际正例图 → Codex按需回调读文档并审题 → 全部审核结果与可用定稿。"""
    root, run = resolve_root(), Path(cfg['run']).absolute()
    if run.parent != root / DATASETS or not run.name or run.suffix:
        raise ValueError('review run must be a suffix-free name under ' + str(root / DATASETS))
    agent = load_agent_config(cfg['agent_config'])
    definition = agent.prompt_pack.prompt_definitions['review_question']
    source = {**cfg['review_source'], 'uri': str((root / cfg['review_source']['uri']).resolve())}
    review_uri = str(run.parent / f'question_reviews__{run.name}.lance')
    released_uri = str(run.parent / f'reviewed_questions__{run.name}.lance')
    if source['uri'] in {review_uri, released_uri}:
        raise ValueError('Question review cannot overwrite its source')
    result_schema = pa.schema([*QUESTIONS, *review.REVIEW_FIELDS])
    records = RunTables(root, str(DATASETS / f'records__{run.name}.lance'))
    calls = {'root': str(root), 'relative_uri': str(DATASETS / f'question_review_calls__{run.name}.lance')}
    with run_lock(root / '_demiflow' / 'benchmark_t2i_v2' / run.name):
        selected = cfg.get('review_task_ids')
        manifest = {'source': source,
            'sample_size': cfg['sample_size'], 'agent_sha256': digest(Path(cfg['agent_config']).read_text()),
            'review_uri': review_uri, 'released_uri': released_uri}
        if selected is not None:
            manifest['review_task_ids'] = selected
        records.save_manifest(manifest)
        # 显式小样本按固定题目身份筛选，键校验和实际请求使用完全相同的范围。
        candidates = data.read_lance(**source, columns=QUESTIONS.names, batch_size=1)
        if selected is not None:
            # 选择完成后再开始审核流，范围外的题目不计为本轮漏处理。
            candidates = candidates.filter(lambda r: r['task_id'] in selected).materialize()
        keys = candidates.select_columns(['task_id']).take(cfg['sample_size'] + 1)
        if (len(keys) != cfg['sample_size'] or len({r['task_id'] for r in keys}) != len(keys)
                or (selected is not None and {r['task_id'] for r in keys} != set(selected))):
            raise ValueError('Question review source differs from declared unique question scope')
        state = {'phase': 'question_review_running', 'complete': False, 'calls': calls,
                 'candidate_count': 0, 'sources': {'candidates': source},
                 'question_review': {'expected': len(keys), 'processed': 0, 'ready': 0,
                    'model': definition.model.name, 'prompt_version': definition.version,
                    'stage_uri': review_uri,
                    'start_version': lance.dataset(review_uri).version if Path(review_uri).exists() else 0}}
        records.save(state)
        print(f'[T2I question review] scope={len(keys)}, model={definition.model.name}, '
              f'concurrency={cfg["concurrency"]}, max_calls={cfg["max_calls"]}', flush=True)
        options = {('offline_store' if cfg['mode'] == 'offline' else 'sqlite_journal'):
                   sqlite_call_options(**calls)}
        stats = (candidates
            .map(partial(review.prepare, source=source, prompt_version=definition.version))
            .agentmap_async('review_question', config=agent, options=options,
                max_requests=cfg['max_calls'], concurrency=cfg['concurrency'], queue_depth=cfg['queue_depth'],
                inputs={'review_payload': 'review_payload', 'images': 'prompt_images'},
                output='question_review_result', call_output='question_review_call', error_output='question_review_error',
                when=lambda r: r['review_status'] == 'ready_to_review')
            .map(partial(review.finish, result_schema=definition.response_schema['properties']['result']))
            .map(lambda r: {k: r.get(k) for k in result_schema.names})
            .map(lambda r: print(f'[T2I question review] {r["concept"]}: {r["review_status"]}; '
                                f'requires_new_answer={r["requires_new_answer"]}', flush=True) or r)
            .save_lance(review_uri, schema=result_schema, key='source_task_id', stage='question_reviews',
                        max_batch=1, queue_depth=1)
            .run_stream(on_drain=lambda stat: records.save({**state, 'question_review': {
                **state['question_review'], 'outputs': stat.outputs, 'miss': stat.miss}})))
        output = stats.outputs['question_reviews']
        results = data.read_lance(**output)
        # 范围最多1000题，状态摘要仅读一个短字段；无需为小型控制统计启动关联执行器。
        counts = {}
        for row in results.select_columns(['review_status']).take(cfg['sample_size'] + 1):
            counts[row['review_status']] = counts.get(row['review_status'], 0) + 1
        # 仅ready进入定稿表；hold/技术失败仍完整保留在审核表。
        released = results.filter(lambda r: r['review_status'] == 'ready').write_lance(
            released_uri, mode='overwrite', schema=result_schema, return_receipt=True)
        released_ref = {'uri': released_uri, 'version': released.committed_version}
        state.update(phase='question_review', counts=counts, candidate_count=counts.get('ready', 0),
            candidates=released_ref,
            complete=not stats.miss and sum(counts.values()) == len(keys) and set(counts) <= {'ready', 'hold'},
            question_review={**state['question_review'], 'processed': sum(counts.values()),
                'ready': counts.get('ready', 0), 'outputs': {**stats.outputs, 'reviewed_questions': released_ref},
                'miss': stats.miss})
        records.save(state)
        print(f'[T2I question review] submitted: {counts}; complete={state["complete"]}', flush=True)
        return state


def append_question_review(cfg, state, records):
    """显式启用时接在已提交候选之后；旧探测结果保持，审题不消费作答或分数。"""
    settings = cfg.get('question_review')
    if settings is None:
        return state
    if set(state['counts']) - {'candidate', 'insufficient'}:
        state = {**state, 'complete': False, 'question_review': {'status': 'awaiting_authoring'}}
        records.save(state)
        return state
    run = Path(cfg['run']).with_name(Path(cfg['run']).name + '__question_review')
    source = state['candidates']
    prior = RunTables(resolve_root(), str(DATASETS / f'records__{run.name}.lance')).load_manifest()
    if prior is not None:
        # 父流程重放会再提交版本；题目身份不变时继续使用首次交接的固定快照。
        # candidate_row 的身份绑定题面、考点及作者材料；这里仅读取有界的键摘要。
        current = data.read_lance(**source, columns=['task_id']).take(state['candidate_count'] + 1)
        frozen = data.read_lance(**prior['source'], columns=['task_id']).take(prior['sample_size'] + 1)
        if (state['candidate_count'] != prior['sample_size']
                or len(current) != state['candidate_count'] or len(frozen) != prior['sample_size']
                or sorted(r['task_id'] for r in current) != sorted(r['task_id'] for r in frozen)):
            raise ValueError('Question review handoff changed; use a new run')
        source = prior['source']
    nested = config(run=str(run), through='review', review_source=source,
        sample_size=state['candidate_count'], **settings)
    # 保存实际交接配置；只读 notebook 可在新 kernel 查看子阶段的真实范围。
    directory = resolve_root() / DATASETS.parent / 'runs' / run.name
    directory.mkdir(parents=True, exist_ok=True)
    (directory / 'config.json').write_text(json.dumps(nested, ensure_ascii=False, indent=2) + '\n')
    result = run_question_review(nested)
    state = {**state, 'complete': state['complete'] and result['complete'],
             'question_review': {'run': nested['run'], **result}}
    records.save(state)
    return state


def main():
    """命令行入口；与 notebook 调用同一个 run_pipeline，不另建执行链。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        '--run', help='New run name (tables are flat under this pipeline datasets/)'
    )
    parser.add_argument('--config', help='完整config JSON；包含配对版本、固定图审来源及所有预算，与notebook共用')
    parser.add_argument('--concept', action='append', help='显式概念；与 --screening-table 二选一')
    parser.add_argument('--screening-table', help='screening 合并结果表，只接 screened/keep')
    parser.add_argument('--screening-version', type=int, help='粗筛结果的固定 Lance 版本')
    parser.add_argument('--concept-audit-table', help='明确采纳的概念审定结果表，只选可出题记录')
    parser.add_argument('--concept-audit-version', type=int, help='采纳结果的固定 Lance 版本')
    parser.add_argument('--sample-size', type=int, help='从粗筛 keep 选取的数量，必须显式填写')
    parser.add_argument('--sample-seed', type=int, default=0, help='按 seed/name 哈希选样的种子')
    parser.add_argument('--article-table', help='preparation 文章结果表路径')
    parser.add_argument('--article-version', type=int, help='文章表固定 Lance 版本')
    parser.add_argument('--visual-table', help='preparation 图片结果表路径')
    parser.add_argument('--visual-version', type=int, help='图片表固定 Lance 版本')
    parser.add_argument('--mode', choices=['offline', 'local', 'modelhub', 'codex'], default=None)
    parser.add_argument('--model')
    parser.add_argument('--codex-bin', default=None, help='已安装的 Codex CLI 路径；不会下载')
    parser.add_argument('--reasoning-effort', choices=['minimal', 'low', 'medium', 'high', 'xhigh'])
    parser.add_argument('--codex-web-search', choices=['disabled', 'cached', 'live'], default=None,
                        help='Codex 按需检索模式，默认 live；不影响 HTTP 模式')
    parser.add_argument('--concurrency', type=int, default=1, help='同时执行的概念请求数')
    parser.add_argument('--queue-depth', type=int, default=1, help='模型节点队列深度')
    parser.add_argument('--max-calls', type=int, help='新请求/exec 上限；补读时默认概念数乘轮数，否则概念数')
    parser.add_argument('--agent-config', help='唯一 agent YAML 配置入口；模型、工具和预算均在其中')
    parser.add_argument('--document-reads', action='store_true', default=None, help='使用 agentmap_async 按需补读审定原文；支持 offline/local/modelhub/codex')
    parser.add_argument('--document-read-max-turns', type=int, default=None, help='每行模型轮数，含最终答复')
    parser.add_argument('--document-read-max-calls-per-turn', type=int, default=None)
    parser.add_argument('--document-read-max-chars', type=int, default=None)
    parser.add_argument('--document-read-max-bytes', type=int, default=None)
    parser.add_argument('--document-read-timeout-s', type=float, default=None)
    parser.add_argument('--timeout-s', type=float, default=None, help='每次请求/exec 的超时秒数')
    parser.add_argument('--temperature', type=float, default=None, help='模型采样温度')
    parser.add_argument('--target', help='候选题输出表路径')
    parser.add_argument(
        '--write-mode', choices=['append', 'overwrite'], default='overwrite', help='目标表追加或覆盖'
    )
    args = parser.parse_args()
    if args.config:
        cfg = config(**json.loads(Path(args.config).read_text()))
        print(json.dumps(run_pipeline(cfg), ensure_ascii=False, indent=2))
        return
    if not args.run:
        parser.error('--run or --config is required')
    for name in ('article', 'visual', 'screening', 'concept_audit'):
        if bool(getattr(args, name + '_table')) != (getattr(args, name + '_version') is not None):
            parser.error('--' + name + '-table and --' + name + '-version must be supplied together')
    article_source = (
        {'uri': args.article_table, 'version': args.article_version} if args.article_table else None
    )
    visual_source = {'uri': args.visual_table, 'version': args.visual_version} if args.visual_table else None
    cfg = config(
        run=resolve_root() / DATASETS / args.run,
        concepts=args.concept, mode=args.mode, model=args.model,
        screening_source={'uri': args.screening_table, 'version': args.screening_version} if args.screening_table else None,
        concept_audit_source={'uri':args.concept_audit_table,'version':args.concept_audit_version} if args.concept_audit_table else None,
        sample_size=args.sample_size, sample_seed=args.sample_seed,
        codex_bin=args.codex_bin, reasoning_effort=args.reasoning_effort,
        codex_web_search=args.codex_web_search,
        concurrency=args.concurrency, queue_depth=args.queue_depth, temperature=args.temperature,
        max_calls=args.max_calls, timeout_s=args.timeout_s,
        document_reads=args.document_reads, agent_config=args.agent_config,
        document_read_max_turns=args.document_read_max_turns,
        document_read_max_calls_per_turn=args.document_read_max_calls_per_turn,
        document_read_max_chars=args.document_read_max_chars,
        document_read_max_bytes=args.document_read_max_bytes,
        document_read_timeout_s=args.document_read_timeout_s,
        article_source=article_source,
        visual_source=visual_source,
        target_uri=args.target,
        write_mode=args.write_mode,
    )
    result = run_pipeline(cfg)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
