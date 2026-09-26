"""Native image-model evaluation: prepare, generate and judge explicit Dataset stages."""

from evaluation.operaters.transforms import (
    combine_concept_materials,
    build_visual_item,
    apply_figure_review,
    deduplicate_figures,
    select_published_materials,
    make_answer_job,
    group_question_answers,
    apply_visual_review,
    build_text_item,
    decode_article,
    mark_missing_evidence,
    build_figure_item,
    decode_visual_record,
    check_visual_identity,
    paragraph_evidence,
    check_figure_selection,
)

import lance
import json
import pyarrow as pa
from functools import partial
from demiflow.lance.refs import DatasetRef
from demiflow.lance.registry import Catalog
from demiflow.lance.storage import schema_hash
from preparation.operaters.runfiles import run_relative, run_records
from project import resolve_root
from demiflow.execution.artifacts import digest
from preparation.operaters.results import PIPELINE_STAGE_ROWS, to_stage_row, from_stage_row
import argparse
from demiflow.execution.artifacts import read, run_lock
from pathlib import Path
from demiflow import data
from preparation.operaters.inputs import source_asset, attach_identity, material_review
from preparation.operaters.runfiles import rows, run_state
from evaluation.operaters.contracts import (
    EvaluationFiles,
    verify_run,
    frozen_stage,
    default_config,
    graph_version,
)
from evaluation.operaters.operators import (
    normalize_question,
    RetrieveKnowledge,
    verify_result,
)
from evaluation.operaters.requests import MODELS, make_request
from evaluation.operaters.adapters import GenerateImage, RunBackendGraph, validate_execution_config
from evaluation.operaters.prompting import prompt_config, prompt_responses
from evaluation.operaters.rubrics import (
    PrepareRubric,
    ApplyRubric,
    freeze_rubrics,
    verify_rubrics,
    verify_rubric_inputs,
)
from evaluation.operaters.judging import PrepareJudge, ApplyJudge, verify_answer_assets
from evaluation.operaters.scores import compare_group, aggregate


def run_pipeline(
    run,
    questions_path,
    knowledge_runs,
    config,
    *,
    through='prepare',
    visual_runs=(),
    target_uri=None,
    write_mode='overwrite'
):
    """从指定题表和材料表评测，最终评分按 write_mode 追加或覆盖 target_uri；中间结果随运行保存。"""
    if write_mode not in {'append', 'overwrite'}:
        raise ValueError('write_mode must be append or overwrite')
    if through not in {'prepare', 'rubrics', 'generate', 'judge'}:
        raise ValueError('Unknown stopping point')
    with run_lock(Path(run).parent / '_demiflow' / Path(run).name):
        files = EvaluationFiles(run, questions_path, knowledge_runs, config, graph_version(), visual_runs)
        # 固定最终评分表与写入模式；后续独立 judge 复用本次输出配置。
        target_uri = str((files.storage_root / target_uri).resolve()) if target_uri else None
        files.records.put('output', {'uri': target_uri, 'write_mode': write_mode})
        pack, options = prompt_config(run, config)
        # 直接读指定题表；旧阶段表仅在确有 payload 列时还原业务字段。
        question_source = (
            data.read_lance(str(resolve_root() / files.questions['uri']), version=files.questions['version'])
            if 'uri' in files.questions
            else data.read_lance(
                str(resolve_root() / files.questions.get('dataset_ref', files.questions)['relative_uri']),
                version=files.questions.get('dataset_ref', files.questions)['lance_version'],
            )
        )
        # 保留题表已有编号；没有编号时 normalize_question 使用 task_id，不另造行顺序。
        questions = (
            question_source.map(lambda row: from_stage_row(row) if 'payload' in row else row)
            .map(lambda row: {**row, 'publication_ref': files.questions})
            .map(normalize_question)
        )
        version = digest(
            {
                "upstream": files.previous,
                "stage": 'questions',
                "extra": None,
                "implementation": files.manifest.get("implementation"),
            }
        )
        relative = str(
            Path(files.relative).parent
            / ('questions' + '__' + Path(files.relative).name + '__' + version + '.lance')
        )
        uri = str(files.storage_root / relative)
        entry = files.records.get("stage/" + 'questions' + "/" + version)
        reused = entry is not None
        if not reused:
            if Path(uri).exists():
                raise ValueError("Unfinished stage table; inspect it before retrying: " + uri)
            (
                questions.map(
                    partial(to_stage_row, stage='questions', upstream_identity=files.previous, migrated_us=0)
                )
            ).write_lance(uri, mode='overwrite', schema=PIPELINE_STAGE_ROWS)
            committed = lance.dataset(uri)
            ref = DatasetRef(
                relative[:-6],
                relative,
                committed.version,
                files.schema_name,
                files.schema_version,
                schema_hash(PIPELINE_STAGE_ROWS),
                committed.count_rows(),
            )
            Catalog(files.storage_root).register(ref)
            entry = {"version": version, "dataset_ref": ref.to_dict()}
            files.records.put("stage/" + 'questions' + "/" + version, entry)
        files.stages['questions'] = entry
        (files.reused if reused else files.new).append('questions')
        files.previous = {"stage": 'questions', "stage_version": version}
        questions = data.read_lance(uri, version=entry["dataset_ref"]["lance_version"]).map(from_stage_row)
        # 1. 文章：从固定版本读取，保留审核通过的正文；历史阶段表只解码已有 payload。
        article_sources = [
            data.read_lance(
                str(
                    resolve_root()
                    / (source['uri'] if 'uri' in source else source['dataset_ref']['relative_uri'])
                ),
                version=source['version'] if 'uri' in source else source['dataset_ref']['lance_version'],
                filter=(
                    "array_contains(release_ids, '" + source['release_id'].replace("'", "''") + "')"
                    if source.get('release_id')
                    else None
                ),
            )
            .filter(lambda row: 'payload' in row or row['review_status'] == 'reviewed')
            .map(decode_article)
            .map(
                lambda row, source=source: {
                    'concept': row['concept'],
                    'record': row,
                    'source': source,
                    'content_sha256': digest(row),
                }
            )
            for source in files.knowledge_files
        ]
        articles = (
            article_sources[0].union(*article_sources[1:])
            if article_sources
            else data.from_arrow(pa.table({'concept': pa.array([], type=pa.string())}))
        )
        articles = (
            (articles)
            .reduce_by_key(
                'concept',
                lambda acc, row: {
                    **(acc or row),
                    'conflict': bool(
                        acc and (acc['conflict'] or acc['content_sha256'] != row['content_sha256'])
                    ),
                },
            )
            .materialize()
        )
        conflicts = articles.filter(lambda row: row['conflict']).select_columns(['concept']).take(1)
        if conflicts:
            raise ValueError('Conflicting article publications: ' + conflicts[0]['concept'])

        # 2. 图片：展开一图的各概念审核结果，只保留发布且审核通过的关系，再解码图片证据。
        visual_sources = [
            data.read_lance(
                str(
                    resolve_root()
                    / (source['uri'] if 'uri' in source else source['dataset_ref']['relative_uri'])
                ),
                version=source['version'] if 'uri' in source else source['dataset_ref']['lance_version'],
                filter=(
                    "array_contains(release_ids, '" + source['release_id'].replace("'", "''") + "')"
                    if source.get('release_id')
                    else None
                ),
            )
            .flat_map(
                lambda row: (
                    [row]
                    if 'payload' in row
                    else [
                        {**assessment, 'sha256': row['sha256']}
                        for assessment in row['concept_assessments'] or []
                    ]
                )
            )
            .filter(
                lambda row, release_id=source.get('release_id'): 'payload' in row
                or (
                    row['published']
                    and (release_id in row['release_ids'] if release_id else row['review_status'] == 'keep')
                )
            )
            .map(decode_visual_record)
            .map(
                lambda row, source=source: {
                    'concept': row['concept'],
                    'record': row,
                    'source': source,
                    'content_sha256': digest(row),
                }
            )
            for source in files.visual_files
        ]
        visuals = (
            visual_sources[0].union(*visual_sources[1:])
            if visual_sources
            else data.from_arrow(pa.table({'concept': pa.array([], type=pa.string())}))
        )
        visuals = (
            (visuals)
            .reduce_by_key(
                'concept',
                lambda acc, row: {
                    'concept': row['concept'],
                    'visual_records': (acc['visual_records'] if acc else []) + [row['record']],
                    'visual_sources': (acc['visual_sources'] if acc else []) + [row['source']],
                    'visual_materials': (acc['visual_materials'] if acc else [])
                    + row['record'].get('visual_materials', []),
                },
            )
            .materialize()
        )

        # 3. 按 concept 关联文章与图片；anti 分支保留只有图片的概念。
        visual_inputs = visuals.join(articles.select_columns(['concept']), on='concept', how='anti').map(
            lambda row: {
                **row['visual_records'][0],
                'knowledge': [],
                'publication_kind': 'visual_materials',
                'visual_materials': row['visual_materials'],
                '_visual_sources': row['visual_sources'],
                '_candidate_specs': [
                    v for i, v in enumerate(row['visual_sources']) if v not in row['visual_sources'][:i]
                ],
                '_knowledge_sha256': digest(row['visual_records']),
            }
        )
        source_materials = (
            articles.join(visuals, on='concept', how='left').map(combine_concept_materials)
        ).union(visual_inputs)
        # 独立图片不受文章失败影响：先检查发布身份与支持范围，再验证图片来源和字节。
        source_materials = source_materials.materialize()
        if source_materials.filter(
            lambda row: row.get('publication_kind') == 'visual_materials' and bool(row.get('knowledge'))
        ).take(1):
            raise ValueError('Visual-only publication cannot assert article knowledge')
        visual_items = (
            source_materials.flat_map(
                lambda row: [
                    {
                        'concept': row['concept'],
                        'parent': row,
                        'visual': visual,
                        'publication': visual.get('publication', {}),
                        'image': visual.get('image', {}),
                    }
                    for visual in row.get('visual_materials', [])
                ]
            )
            .map(lambda row: {**row, 'support': row['publication'].get('support', {})})
            .map(check_visual_identity)
            .map(lambda row: {**row, 'asset': source_asset(row['image']) if not row['issue'] else None})
            .map(
                lambda row: {
                    **row,
                    'issue': row['issue']
                    or (None if row['asset'] else 'Independent visual pixels/provenance unavailable'),
                }
            )
            .materialize()
        )
        visual_items = visual_items.map(build_visual_item)
        visual_items = visual_items.map(apply_visual_review).materialize()
        visual_groups = visual_items.reduce_by_key(
            'concept',
            lambda acc, row: {
                'concept': row['concept'],
                'visual_items': (acc['visual_items'] if acc else []) + ([] if row['issue'] else [row['item']]),
                'visual_issues': (acc['visual_issues'] if acc else [])
                + ([row['issue']] if row['issue'] else []),
            },
        )
        # 文章只交付最终审核通过、且未预检失败的内容；每段文字必须有自己的引用或被选中的配图。
        topics = (
            source_materials.filter(
                lambda row: row.get('publication_kind') != 'visual_materials'
                and row.get('status') == 'reviewed'
                and not row.get('audit', {}).get('preflight_error')
            )
        ).flat_map(
            lambda row: [
                {'concept': row['concept'], 'parent': row, 'topic': topic, 'topic_index': ti}
                for ti, topic in enumerate(row.get('knowledge', []))
            ]
        )
        # 同 image_id 的独立图片优先于文章配图；文章中重复放置的图片仅第一次成功交付后保留。
        # 主题/段落位置来自文章原数组，用于保留材料编号的已有顺序，不引入概念排序约束。
        article_items = (
            topics.flat_map(
                lambda row: [
                    {**row, 'paragraph_index': pi, 'text': text}
                    for pi, text in enumerate(row['topic'].get('content', {}).get('paragraphs', []))
                ]
            )
            .map(paragraph_evidence)
            .map(
                lambda row: {
                    **row,
                    'source_ids': sorted(
                        {
                            sid
                            for ref in row['refs']
                            if 'text' in ref.get('kinds', [])
                            for sid in ref.get('source_ids', [])
                        }
                    ),
                    'passages': {p['source_id']: p for p in row['parent'].get('published_passages', [])},
                }
            )
            .map(
                lambda row: {
                    **row,
                    'missing': [sid for sid in row['source_ids'] if sid not in row['passages']],
                }
            )
            .map(mark_missing_evidence)
            .map(build_text_item)
            .map(
                lambda row: (
                    {
                        **row,
                        'item': {
                            **row['item'],
                            'review': material_review(row['topic']['title'], row['parent']),
                        },
                    }
                    if not row['issue']
                    else row
                )
            )
            .map(
                lambda row: {
                    'concept': row['concept'],
                    'position': [row['topic_index'], 0, row['paragraph_index']],
                    'item': row.get('item'),
                    'issue': row['issue'],
                }
            )
            .union(
                topics.flat_map(
                    lambda row: [
                        {**row, 'placement': placement, 'placement_index': pi}
                        for pi, placement in enumerate(row['topic'].get('content', {}).get('images', []))
                    ]
                )
                .join(visual_groups, on='concept', how='left')
                .filter(
                    lambda row: row['placement']['image_id']
                    not in [item['image_id'] for item in row.get('visual_items', [])]
                )
                .map(
                    lambda row: {
                        **row,
                        'image': next(
                            (
                                image
                                for image in row['parent'].get('published_images', [])
                                if (image.get('image_id') or image.get('record', {}).get('image_id'))
                                == row['placement']['image_id']
                            ),
                            None,
                        ),
                    }
                )
                .map(check_figure_selection)
                .map(lambda row: {**row, 'asset': source_asset(row['image']) if not row['issue'] else None})
                .map(
                    lambda row: {
                        **row,
                        'issue': row['issue']
                        or (
                            None
                            if row['asset']
                            else {
                                'image_id': row['placement']['image_id'],
                                'reason': 'Final figure bytes/provenance unavailable',
                            }
                        ),
                    }
                )
                .map(build_figure_item)
                .map(
                    lambda row: {
                        **row,
                        'support': (
                            row['topic']['content']['paragraphs'][row['placement']['paragraph_index']]
                            if type(row['placement'].get('paragraph_index')) is int
                            and 0
                            <= row['placement']['paragraph_index']
                            < len(row['topic'].get('content', {}).get('paragraphs', []))
                            else ''
                        ),
                    }
                )
                .map(apply_figure_review)
                .map(
                    lambda row: {
                        'concept': row['concept'],
                        'position': [row['topic_index'], 1, row['placement_index']],
                        'item': row.get('item'),
                        'issue': row['issue'],
                    }
                )
            )
            .reduce_by_key(
                'concept',
                lambda acc, row: {
                    'concept': row['concept'],
                    'entries': (acc['entries'] if acc else []) + [row],
                },
            )
            .map(lambda row: {**row, 'entries': sorted(row['entries'], key=lambda item: item['position'])})
            .map(deduplicate_figures)
        )
        delivered_materials = (
            source_materials.join(visual_groups, on='concept', how='left')
            .join(article_items, on='concept', how='left')
            .map(
                lambda row: {
                    **row,
                    'materials': row.get('visual_items', [])
                    + [entry['item'] for entry in row.get('entries', []) if entry['item']],
                }
            )
            .map(select_published_materials)
        )
        version = digest(
            {
                "upstream": files.previous,
                "stage": 'published',
                "extra": None,
                "implementation": files.manifest.get("implementation"),
            }
        )
        relative = str(
            Path(files.relative).parent
            / ('published' + '__' + Path(files.relative).name + '__' + version + '.lance')
        )
        uri = str(files.storage_root / relative)
        entry = files.records.get("stage/" + 'published' + "/" + version)
        reused = entry is not None
        if not reused:
            if Path(uri).exists():
                raise ValueError("Unfinished stage table; inspect it before retrying: " + uri)
            (
                delivered_materials.drop_columns(['_candidate_specs']).map(
                    partial(to_stage_row, stage='published', upstream_identity=files.previous, migrated_us=0)
                )
            ).write_lance(uri, mode='overwrite', schema=PIPELINE_STAGE_ROWS)
            committed = lance.dataset(uri)
            ref = DatasetRef(
                relative[:-6],
                relative,
                committed.version,
                files.schema_name,
                files.schema_version,
                schema_hash(PIPELINE_STAGE_ROWS),
                committed.count_rows(),
            )
            Catalog(files.storage_root).register(ref)
            entry = {"version": version, "dataset_ref": ref.to_dict()}
            files.records.put("stage/" + 'published' + "/" + version, entry)
        files.stages['published'] = entry
        (files.reused if reused else files.new).append('published')
        files.previous = {"stage": 'published', "stage_version": version}
        published = data.read_lance(uri, version=entry["dataset_ref"]["lance_version"]).map(from_stage_row)
        version = digest(
            {
                "upstream": files.previous,
                "stage": 'catalog',
                "extra": None,
                "implementation": files.manifest.get("implementation"),
            }
        )
        relative = str(
            Path(files.relative).parent
            / ('catalog' + '__' + Path(files.relative).name + '__' + version + '.lance')
        )
        uri = str(files.storage_root / relative)
        entry = files.records.get("stage/" + 'catalog' + "/" + version)
        reused = entry is not None
        if not reused:
            if Path(uri).exists():
                raise ValueError("Unfinished stage table; inspect it before retrying: " + uri)
            (
                published.flat_map(lambda row: row['materials']).map(
                    partial(to_stage_row, stage='catalog', upstream_identity=files.previous, migrated_us=0)
                )
            ).write_lance(uri, mode='overwrite', schema=PIPELINE_STAGE_ROWS)
            committed = lance.dataset(uri)
            ref = DatasetRef(
                relative[:-6],
                relative,
                committed.version,
                files.schema_name,
                files.schema_version,
                schema_hash(PIPELINE_STAGE_ROWS),
                committed.count_rows(),
            )
            Catalog(files.storage_root).register(ref)
            entry = {"version": version, "dataset_ref": ref.to_dict()}
            files.records.put("stage/" + 'catalog' + "/" + version, entry)
        files.stages['catalog'] = entry
        (files.reused if reused else files.new).append('catalog')
        files.previous = {"stage": 'catalog', "stage_version": version}
        catalog = data.read_lance(uri, version=entry["dataset_ref"]["lance_version"]).map(from_stage_row)
        version = digest(
            {
                "upstream": files.previous,
                "stage": 'retrieval',
                "extra": None,
                "implementation": files.manifest.get("implementation"),
            }
        )
        relative = str(
            Path(files.relative).parent
            / ('retrieval' + '__' + Path(files.relative).name + '__' + version + '.lance')
        )
        uri = str(files.storage_root / relative)
        entry = files.records.get("stage/" + 'retrieval' + "/" + version)
        reused = entry is not None
        if not reused:
            if Path(uri).exists():
                raise ValueError("Unfinished stage table; inspect it before retrying: " + uri)
            (
                questions.map(RetrieveKnowledge(catalog.take_all(), config['split_registry'], config)).map(
                    partial(to_stage_row, stage='retrieval', upstream_identity=files.previous, migrated_us=0)
                )
            ).write_lance(uri, mode='overwrite', schema=PIPELINE_STAGE_ROWS)
            committed = lance.dataset(uri)
            ref = DatasetRef(
                relative[:-6],
                relative,
                committed.version,
                files.schema_name,
                files.schema_version,
                schema_hash(PIPELINE_STAGE_ROWS),
                committed.count_rows(),
            )
            Catalog(files.storage_root).register(ref)
            entry = {"version": version, "dataset_ref": ref.to_dict()}
            files.records.put("stage/" + 'retrieval' + "/" + version, entry)
        files.stages['retrieval'] = entry
        (files.reused if reused else files.new).append('retrieval')
        files.previous = {"stage": 'retrieval', "stage_version": version}
        retrieved = data.read_lance(uri, version=entry["dataset_ref"]["lance_version"]).map(from_stage_row)
        # 每道题按配置展开模型和开/闭卷条件；Qwen-2512 只接文字，其他模型保留所选图文。
        version = digest(
            {
                "upstream": files.previous,
                "stage": 'jobs',
                "extra": None,
                "implementation": files.manifest.get("implementation"),
            }
        )
        relative = str(
            Path(files.relative).parent
            / ('jobs' + '__' + Path(files.relative).name + '__' + version + '.lance')
        )
        uri = str(files.storage_root / relative)
        entry = files.records.get("stage/" + 'jobs' + "/" + version)
        reused = entry is not None
        if not reused:
            if Path(uri).exists():
                raise ValueError("Unfinished stage table; inspect it before retrying: " + uri)
            (
                retrieved.flat_map(
                    lambda row: [
                        {**row, 'backend': backend, 'condition': condition}
                        for backend in config.get('backends', {}).get(
                            row['question']['task_type'],
                            ['qwen2512' if row['question']['task_type'] == 't2i' else 'qwen2511'],
                        )
                        for condition in (
                            config['gemini_conditions']
                            if backend == 'gemini'
                            else ['without_knowledge', 'with_knowledge']
                        )
                    ]
                )
                .map(
                    lambda row: {
                        **row,
                        'answer_materials': [
                            m
                            for m in row['materials']
                            if row['condition'] == 'with_knowledge'
                            and (row['backend'] != 'qwen2512' or m['kind'] == 'text')
                        ],
                    }
                )
                .map(partial(make_answer_job, config=config))
                .map(partial(to_stage_row, stage='jobs', upstream_identity=files.previous, migrated_us=0))
            ).write_lance(uri, mode='overwrite', schema=PIPELINE_STAGE_ROWS)
            committed = lance.dataset(uri)
            ref = DatasetRef(
                relative[:-6],
                relative,
                committed.version,
                files.schema_name,
                files.schema_version,
                schema_hash(PIPELINE_STAGE_ROWS),
                committed.count_rows(),
            )
            Catalog(files.storage_root).register(ref)
            entry = {"version": version, "dataset_ref": ref.to_dict()}
            files.records.put("stage/" + 'jobs' + "/" + version, entry)
        files.stages['jobs'] = entry
        (files.reused if reused else files.new).append('jobs')
        files.previous = {"stage": 'jobs', "stage_version": version}
        jobs = data.read_lance(uri, version=entry["dataset_ref"]["lance_version"]).map(from_stage_row)
        version = digest(
            {
                "upstream": files.previous,
                "stage": 'partitions',
                "extra": None,
                "implementation": files.manifest.get("implementation"),
            }
        )
        relative = str(
            Path(files.relative).parent
            / ('partitions' + '__' + Path(files.relative).name + '__' + version + '.lance')
        )
        uri = str(files.storage_root / relative)
        entry = files.records.get("stage/" + 'partitions' + "/" + version)
        reused = entry is not None
        if not reused:
            if Path(uri).exists():
                raise ValueError("Unfinished stage table; inspect it before retrying: " + uri)
            (
                jobs.reduce_by_key(
                    'backend',
                    lambda acc, row: {'backend': row['backend'], 'jobs': (acc['jobs'] if acc else 0) + 1},
                ).map(
                    partial(to_stage_row, stage='partitions', upstream_identity=files.previous, migrated_us=0)
                )
            ).write_lance(uri, mode='overwrite', schema=PIPELINE_STAGE_ROWS)
            committed = lance.dataset(uri)
            ref = DatasetRef(
                relative[:-6],
                relative,
                committed.version,
                files.schema_name,
                files.schema_version,
                schema_hash(PIPELINE_STAGE_ROWS),
                committed.count_rows(),
            )
            Catalog(files.storage_root).register(ref)
            entry = {"version": version, "dataset_ref": ref.to_dict()}
            files.records.put("stage/" + 'partitions' + "/" + version, entry)
        files.stages['partitions'] = entry
        (files.reused if reused else files.new).append('partitions')
        files.previous = {"stage": 'partitions', "stage_version": version}
        partitions = data.read_lance(uri, version=entry["dataset_ref"]["lance_version"]).map(from_stage_row)
        files.finish()  # Seal jobs before child Dataset graphs read them.
        version = digest(
            {
                "upstream": files.previous,
                "stage": 'rubric_requests',
                "extra": None,
                "implementation": files.manifest.get("implementation"),
            }
        )
        relative = str(
            Path(files.relative).parent
            / ('rubric_requests' + '__' + Path(files.relative).name + '__' + version + '.lance')
        )
        uri = str(files.storage_root / relative)
        entry = files.records.get("stage/" + 'rubric_requests' + "/" + version)
        reused = entry is not None
        if not reused:
            if Path(uri).exists():
                raise ValueError("Unfinished stage table; inspect it before retrying: " + uri)
            (
                questions.map(PrepareRubric(files.run, pack, config)).map(
                    partial(
                        to_stage_row, stage='rubric_requests', upstream_identity=files.previous, migrated_us=0
                    )
                )
            ).write_lance(uri, mode='overwrite', schema=PIPELINE_STAGE_ROWS)
            committed = lance.dataset(uri)
            ref = DatasetRef(
                relative[:-6],
                relative,
                committed.version,
                files.schema_name,
                files.schema_version,
                schema_hash(PIPELINE_STAGE_ROWS),
                committed.count_rows(),
            )
            Catalog(files.storage_root).register(ref)
            entry = {"version": version, "dataset_ref": ref.to_dict()}
            files.records.put("stage/" + 'rubric_requests' + "/" + version, entry)
        files.stages['rubric_requests'] = entry
        (files.reused if reused else files.new).append('rubric_requests')
        files.previous = {"stage": 'rubric_requests', "stage_version": version}
        rubric_requests = data.read_lance(uri, version=entry["dataset_ref"]["lance_version"]).map(
            from_stage_row
        )
        if through == 'prepare':
            return files.finish()
        rubric_requests.map(lambda row: verify_rubric_inputs([row])).count()
        version = digest(
            {
                "upstream": files.previous,
                "stage": 'rubrics',
                "extra": prompt_responses(files.run, 'rubric'),
                "implementation": files.manifest.get("implementation"),
            }
        )
        relative = str(
            Path(files.relative).parent
            / ('rubrics' + '__' + Path(files.relative).name + '__' + version + '.lance')
        )
        uri = str(files.storage_root / relative)
        entry = files.records.get("stage/" + 'rubrics' + "/" + version)
        reused = entry is not None
        if not reused:
            if Path(uri).exists():
                raise ValueError("Unfinished stage table; inspect it before retrying: " + uri)
            (
                rubric_requests.map_prompt_async(
                    'rubric',
                    config=pack,
                    options=options,
                    max_requests=config['judge']['max_calls'],
                    inputs={
                        'judge_protocol': 'prompt_judge_protocol',
                        'payload': 'prompt_payload',
                        'images': 'prompt_images',
                    },
                    output='rubric_result',
                    call_output='rubric_call',
                    error_output='rubric_error',
                    when=lambda r: r.get('rubric_status') == 'prepared',
                    concurrency=1,
                    queue_depth=1,
                )
                .map(ApplyRubric(files.run, config))
                .map(partial(to_stage_row, stage='rubrics', upstream_identity=files.previous, migrated_us=0))
                .materialize()
                .write_lance(uri, mode='overwrite', schema=PIPELINE_STAGE_ROWS)
            )
            committed = lance.dataset(uri)
            ref = DatasetRef(
                relative[:-6],
                relative,
                committed.version,
                files.schema_name,
                files.schema_version,
                schema_hash(PIPELINE_STAGE_ROWS),
                committed.count_rows(),
            )
            Catalog(files.storage_root).register(ref)
            entry = {"version": version, "dataset_ref": ref.to_dict()}
            files.records.put("stage/" + 'rubrics' + "/" + version, entry)
        files.stages['rubrics'] = entry
        (files.reused if reused else files.new).append('rubrics')
        files.previous = {"stage": 'rubrics', "stage_version": version}
        rubrics = data.read_lance(uri, version=entry["dataset_ref"]["lance_version"]).map(from_stage_row)
        rubrics_ready = freeze_rubrics(files.run, rubrics.take_all())
        files.finish()
        if through == 'rubrics' or not rubrics_ready:
            return files.finish()
        validate_execution_config(config, [r['backend'] for r in partitions.take_all()])
        version = digest(
            {
                "upstream": files.previous,
                "stage": 'backend_results',
                "extra": None,
                "implementation": files.manifest.get("implementation"),
            }
        )
        relative = str(
            Path(files.relative).parent
            / ('backend_results' + '__' + Path(files.relative).name + '__' + version + '.lance')
        )
        uri = str(files.storage_root / relative)
        entry = files.records.get("stage/" + 'backend_results' + "/" + version)
        reused = entry is not None
        if not reused:
            if Path(uri).exists():
                raise ValueError("Unfinished stage table; inspect it before retrying: " + uri)
            (
                partitions.map_async(RunBackendGraph(files.run, config))
                .map(
                    partial(
                        to_stage_row, stage='backend_results', upstream_identity=files.previous, migrated_us=0
                    )
                )
                .materialize()
                .write_lance(uri, mode='overwrite', schema=PIPELINE_STAGE_ROWS)
            )
            committed = lance.dataset(uri)
            ref = DatasetRef(
                relative[:-6],
                relative,
                committed.version,
                files.schema_name,
                files.schema_version,
                schema_hash(PIPELINE_STAGE_ROWS),
                committed.count_rows(),
            )
            Catalog(files.storage_root).register(ref)
            entry = {"version": version, "dataset_ref": ref.to_dict()}
            files.records.put("stage/" + 'backend_results' + "/" + version, entry)
        files.stages['backend_results'] = entry
        (files.reused if reused else files.new).append('backend_results')
        files.previous = {"stage": 'backend_results', "stage_version": version}
        completed = data.read_lance(uri, version=entry["dataset_ref"]["lance_version"]).map(from_stage_row)
        # 这里只收集每个后端的一条结果表引用；图片字节仍在各自 Blob 表。
        result_files = completed.take_all()
        # 每个后端返回固定结果表引用；union 合并各表，每行仍是一题、一模型、一个作答条件。
        answer_sources = [
            data.read_lance(
                str(resolve_root() / record['dataset_ref']['relative_uri']),
                version=record['dataset_ref']['lance_version'],
            ).map(from_stage_row)
            for record in result_files
        ]
        answers = (
            answer_sources[0].union(*answer_sources[1:])
            if answer_sources
            else data.from_arrow(pa.Table.from_pylist([], schema=PIPELINE_STAGE_ROWS))
        ).map(verify_result)
        version = digest(
            {
                "upstream": files.previous,
                "stage": 'answers',
                "extra": None,
                "implementation": files.manifest.get("implementation"),
            }
        )
        relative = str(
            Path(files.relative).parent
            / ('answers' + '__' + Path(files.relative).name + '__' + version + '.lance')
        )
        uri = str(files.storage_root / relative)
        entry = files.records.get("stage/" + 'answers' + "/" + version)
        reused = entry is not None
        if not reused:
            if Path(uri).exists():
                raise ValueError("Unfinished stage table; inspect it before retrying: " + uri)
            (
                answers.map(
                    partial(to_stage_row, stage='answers', upstream_identity=files.previous, migrated_us=0)
                )
            ).write_lance(uri, mode='overwrite', schema=PIPELINE_STAGE_ROWS)
            committed = lance.dataset(uri)
            ref = DatasetRef(
                relative[:-6],
                relative,
                committed.version,
                files.schema_name,
                files.schema_version,
                schema_hash(PIPELINE_STAGE_ROWS),
                committed.count_rows(),
            )
            Catalog(files.storage_root).register(ref)
            entry = {"version": version, "dataset_ref": ref.to_dict()}
            files.records.put("stage/" + 'answers' + "/" + version, entry)
        files.stages['answers'] = entry
        (files.reused if reused else files.new).append('answers')
        files.previous = {"stage": 'answers', "stage_version": version}
        answers = data.read_lance(uri, version=entry["dataset_ref"]["lance_version"]).map(from_stage_row)
        version = digest(
            {
                "upstream": files.previous,
                "stage": 'comparison',
                "extra": None,
                "implementation": files.manifest.get("implementation"),
            }
        )
        relative = str(
            Path(files.relative).parent
            / ('comparison' + '__' + Path(files.relative).name + '__' + version + '.lance')
        )
        uri = str(files.storage_root / relative)
        entry = files.records.get("stage/" + 'comparison' + "/" + version)
        reused = entry is not None
        if not reused:
            if Path(uri).exists():
                raise ValueError("Unfinished stage table; inspect it before retrying: " + uri)
            (
                answers.reduce_by_key(
                    'task_id',
                    group_question_answers,
                ).map(
                    partial(to_stage_row, stage='comparison', upstream_identity=files.previous, migrated_us=0)
                )
            ).write_lance(uri, mode='overwrite', schema=PIPELINE_STAGE_ROWS)
            committed = lance.dataset(uri)
            ref = DatasetRef(
                relative[:-6],
                relative,
                committed.version,
                files.schema_name,
                files.schema_version,
                schema_hash(PIPELINE_STAGE_ROWS),
                committed.count_rows(),
            )
            Catalog(files.storage_root).register(ref)
            entry = {"version": version, "dataset_ref": ref.to_dict()}
            files.records.put("stage/" + 'comparison' + "/" + version, entry)
        files.stages['comparison'] = entry
        (files.reused if reused else files.new).append('comparison')
        files.previous = {"stage": 'comparison', "stage_version": version}
        comparison = data.read_lance(uri, version=entry["dataset_ref"]["lance_version"]).map(from_stage_row)
        state = files.finish()
        if through == 'generate':
            return state
        answers.map(lambda row: verify_answer_assets([row])).count()
        version = digest(
            {
                "upstream": files.previous,
                "stage": 'judge_requests',
                "extra": None,
                "implementation": files.manifest.get("implementation"),
            }
        )
        relative = str(
            Path(files.relative).parent
            / ('judge_requests' + '__' + Path(files.relative).name + '__' + version + '.lance')
        )
        uri = str(files.storage_root / relative)
        entry = files.records.get("stage/" + 'judge_requests' + "/" + version)
        reused = entry is not None
        if not reused:
            if Path(uri).exists():
                raise ValueError("Unfinished stage table; inspect it before retrying: " + uri)
            (
                answers.map(PrepareJudge(files.run, pack, config)).map(
                    partial(
                        to_stage_row, stage='judge_requests', upstream_identity=files.previous, migrated_us=0
                    )
                )
            ).write_lance(uri, mode='overwrite', schema=PIPELINE_STAGE_ROWS)
            committed = lance.dataset(uri)
            ref = DatasetRef(
                relative[:-6],
                relative,
                committed.version,
                files.schema_name,
                files.schema_version,
                schema_hash(PIPELINE_STAGE_ROWS),
                committed.count_rows(),
            )
            Catalog(files.storage_root).register(ref)
            entry = {"version": version, "dataset_ref": ref.to_dict()}
            files.records.put("stage/" + 'judge_requests' + "/" + version, entry)
        files.stages['judge_requests'] = entry
        (files.reused if reused else files.new).append('judge_requests')
        files.previous = {"stage": 'judge_requests', "stage_version": version}
        judge_requests = data.read_lance(uri, version=entry["dataset_ref"]["lance_version"]).map(from_stage_row)
        version = digest(
            {
                "upstream": files.previous,
                "stage": 'scores',
                "extra": prompt_responses(files.run, 'judge'),
                "implementation": files.manifest.get("implementation"),
            }
        )
        relative = str(
            Path(files.relative).parent
            / ('scores' + '__' + Path(files.relative).name + '__' + version + '.lance')
        )
        uri = str(files.storage_root / relative)
        entry = files.records.get("stage/" + 'scores' + "/" + version)
        reused = entry is not None
        if not reused:
            if Path(uri).exists():
                raise ValueError("Unfinished stage table; inspect it before retrying: " + uri)
            (
                judge_requests.map_prompt_async(
                    'judge',
                    config=pack,
                    options=options,
                    max_requests=config['judge']['max_calls'],
                    inputs={
                        'instructions': 'prompt_instructions',
                        'payload': 'prompt_payload',
                        'images': 'prompt_images',
                    },
                    output='judge_result',
                    call_output='judge_call',
                    error_output='judge_error',
                    when=lambda r: r.get('judge_status') == 'prepared',
                    concurrency=1,
                    queue_depth=1,
                )
                .map(ApplyJudge(files.run, config))
                .map(partial(to_stage_row, stage='scores', upstream_identity=files.previous, migrated_us=0))
                .materialize()
                .write_lance(uri, mode='overwrite', schema=PIPELINE_STAGE_ROWS)
            )
            committed = lance.dataset(uri)
            ref = DatasetRef(
                relative[:-6],
                relative,
                committed.version,
                files.schema_name,
                files.schema_version,
                schema_hash(PIPELINE_STAGE_ROWS),
                committed.count_rows(),
            )
            Catalog(files.storage_root).register(ref)
            entry = {"version": version, "dataset_ref": ref.to_dict()}
            files.records.put("stage/" + 'scores' + "/" + version, entry)
        files.stages['scores'] = entry
        (files.reused if reused else files.new).append('scores')
        files.previous = {"stage": 'scores', "stage_version": version}
        scored = data.read_lance(uri, version=entry["dataset_ref"]["lance_version"]).map(from_stage_row)
        version = digest(
            {
                "upstream": files.previous,
                "stage": 'evaluation',
                "extra": None,
                "implementation": files.manifest.get("implementation"),
            }
        )
        relative = str(
            Path(files.relative).parent
            / ('evaluation' + '__' + Path(files.relative).name + '__' + version + '.lance')
        )
        if target_uri:
            relative = str(Path(target_uri).relative_to(files.storage_root))
        uri = str(files.storage_root / relative)
        entry = files.records.get("stage/" + 'evaluation' + "/" + version)
        reused = entry is not None
        if not reused:
            if not target_uri and Path(uri).exists():
                raise ValueError("Unfinished stage table; inspect it before retrying: " + uri)
            (
                scored.reduce_by_key(
                    'task_id',
                    group_question_answers,
                )
                .map(compare_group)
                .map(partial(to_stage_row, stage='evaluation', upstream_identity=files.previous, migrated_us=0))
            ).write_lance(uri, mode=write_mode, schema=PIPELINE_STAGE_ROWS)
            committed = lance.dataset(uri)
            ref = DatasetRef(
                relative[:-6],
                relative,
                committed.version,
                files.schema_name,
                files.schema_version,
                schema_hash(PIPELINE_STAGE_ROWS),
                committed.count_rows(),
            )
            Catalog(files.storage_root).register(ref)
            entry = {"version": version, "dataset_ref": ref.to_dict()}
            files.records.put("stage/" + 'evaluation' + "/" + version, entry)
        files.stages['evaluation'] = entry
        (files.reused if reused else files.new).append('evaluation')
        files.previous = {"stage": 'evaluation', "stage_version": version}
        evaluation = data.read_lance(uri, version=entry["dataset_ref"]["lance_version"]).map(from_stage_row)
        summary_value = aggregate(evaluation.take_all())
        summary = data.from_items([summary_value])
        version = digest(
            {
                "upstream": files.previous,
                "stage": 'summary',
                "extra": None,
                "implementation": files.manifest.get("implementation"),
            }
        )
        relative = str(
            Path(files.relative).parent
            / ('summary' + '__' + Path(files.relative).name + '__' + version + '.lance')
        )
        uri = str(files.storage_root / relative)
        entry = files.records.get("stage/" + 'summary' + "/" + version)
        reused = entry is not None
        if not reused:
            if Path(uri).exists():
                raise ValueError("Unfinished stage table; inspect it before retrying: " + uri)
            (
                summary.map(
                    partial(to_stage_row, stage='summary', upstream_identity=files.previous, migrated_us=0)
                )
            ).write_lance(uri, mode='overwrite', schema=PIPELINE_STAGE_ROWS)
            committed = lance.dataset(uri)
            ref = DatasetRef(
                relative[:-6],
                relative,
                committed.version,
                files.schema_name,
                files.schema_version,
                schema_hash(PIPELINE_STAGE_ROWS),
                committed.count_rows(),
            )
            Catalog(files.storage_root).register(ref)
            entry = {"version": version, "dataset_ref": ref.to_dict()}
            files.records.put("stage/" + 'summary' + "/" + version, entry)
        files.stages['summary'] = entry
        (files.reused if reused else files.new).append('summary')
        files.previous = {"stage": 'summary', "stage_version": version}
        summary = data.read_lance(uri, version=entry["dataset_ref"]["lance_version"]).map(from_stage_row)
        state = files.finish()
        return state


def run_backend(run, backend, actor_factory=GenerateImage):
    # 每个后端进程从已冻结 jobs 表读取自己的作业，再执行同一标准生成链。
    run = Path(run).resolve()
    manifest = verify_run(run)
    config = manifest['config']
    verify_rubrics(run)  # Enforced even for direct --backend execution.
    if actor_factory is GenerateImage:
        import os

        validate_execution_config(config, [backend])
        if backend != 'gemini' and os.environ.get('CUDA_VISIBLE_DEVICES') != str(config['cuda'][backend]):
            raise ValueError('CUDA device differs from the frozen explicit configuration')
    stage = frozen_stage(run, 'jobs')
    version = digest({'manifest': manifest, 'jobs': stage, 'backend': backend})
    folder = run / 'backends' / backend
    with run_lock(
        resolve_root() / Path(run_relative(folder)).parent / '_demiflow' / Path(run_relative(folder)).name
    ):
        from preparation.operaters.results import EncodeStage, PIPELINE_STAGE_ROWS
        from preparation.operaters.runfiles import stage_ref

        stage_name = 'results'
        relative = str(
            Path(run_relative(folder)).parent / (stage_name + '__' + Path(run_relative(folder)).name + '.lance')
        )
        table_uri = str(resolve_root() / relative)
        entry = run_records(folder).get("stage/" + stage_name)
        if entry is not None and entry["fingerprint"] != version:
            raise ValueError("Stage inputs changed; use a new run: " + stage_name)
        if not Path(table_uri).exists():
            (
                data.read_lance(
                    str(resolve_root() / stage['dataset_ref']['relative_uri']),
                    version=stage['dataset_ref']['lance_version'],
                )
                .map(from_stage_row)
                .filter(lambda job: job['backend'] == backend)
                .map_async(actor_factory(run, backend, config))
                .map(verify_result)
                .map(EncodeStage('results'))
                .materialize()
                .write_lance(table_uri, mode='overwrite', schema=PIPELINE_STAGE_ROWS)
            )
            committed = lance.dataset(table_uri)
            ref = DatasetRef(
                relative[:-6],
                relative,
                committed.version,
                "pipeline_stage_rows",
                "v1",
                schema_hash(PIPELINE_STAGE_ROWS),
                committed.count_rows(),
            )
            Catalog(resolve_root()).register(ref)
            entry = {"fingerprint": version, "dataset_ref": ref.to_dict()}
            run_records(folder).put("stage/" + stage_name, entry, immutable=False)
        elif entry is None:
            raise ValueError("Unfinished stage table; inspect it before retrying: " + table_uri)
        saved_rows = data.read_lance(table_uri, version=entry["dataset_ref"]["lance_version"])
        results = saved_rows
        return {'dataset_ref': stage_ref(folder, 'results').to_dict()}


def run_partition_judging(run, backend):
    from evaluation.operaters.contracts import PartitionFiles
    from inspect import getsource
    from preparation.operaters.runfiles import stage_ref

    run = Path(run).resolve()
    manifest = verify_run(run)
    verify_rubrics(run)
    config = manifest['config']
    source = stage_ref(run / 'backends' / backend, 'results').to_dict()
    folder = run / 'partition_evaluations' / backend
    with run_lock(
        resolve_root() / Path(run_relative(folder)).parent / '_demiflow' / Path(run_relative(folder)).name
    ):
        files = PartitionFiles(run, backend, source, getsource(run_partition_judging))
        pack, options = prompt_config(run, config)
        answers = (
            data.read_lance(str(resolve_root() / source['relative_uri']), version=source['lance_version'])
            .map(from_stage_row)
            .map(verify_result)
        )
        answers.map(lambda row: verify_answer_assets([row])).count()
        version = digest(
            {
                "upstream": files.previous,
                "stage": 'judge_requests',
                "extra": None,
                "implementation": files.manifest.get("implementation"),
            }
        )
        relative = str(
            Path(files.relative).parent
            / ('judge_requests' + '__' + Path(files.relative).name + '__' + version + '.lance')
        )
        uri = str(files.storage_root / relative)
        entry = files.records.get("stage/" + 'judge_requests' + "/" + version)
        reused = entry is not None
        if not reused:
            if Path(uri).exists():
                raise ValueError("Unfinished stage table; inspect it before retrying: " + uri)
            (
                answers.map(PrepareJudge(run, pack, config)).map(
                    partial(
                        to_stage_row, stage='judge_requests', upstream_identity=files.previous, migrated_us=0
                    )
                )
            ).write_lance(uri, mode='overwrite', schema=PIPELINE_STAGE_ROWS)
            committed = lance.dataset(uri)
            ref = DatasetRef(
                relative[:-6],
                relative,
                committed.version,
                files.schema_name,
                files.schema_version,
                schema_hash(PIPELINE_STAGE_ROWS),
                committed.count_rows(),
            )
            Catalog(files.storage_root).register(ref)
            entry = {"version": version, "dataset_ref": ref.to_dict()}
            files.records.put("stage/" + 'judge_requests' + "/" + version, entry)
        files.stages['judge_requests'] = entry
        (files.reused if reused else files.new).append('judge_requests')
        files.previous = {"stage": 'judge_requests', "stage_version": version}
        requests = data.read_lance(uri, version=entry["dataset_ref"]["lance_version"]).map(from_stage_row)
        version = digest(
            {
                "upstream": files.previous,
                "stage": 'scores',
                "extra": prompt_responses(run, 'judge'),
                "implementation": files.manifest.get("implementation"),
            }
        )
        relative = str(
            Path(files.relative).parent
            / ('scores' + '__' + Path(files.relative).name + '__' + version + '.lance')
        )
        uri = str(files.storage_root / relative)
        entry = files.records.get("stage/" + 'scores' + "/" + version)
        reused = entry is not None
        if not reused:
            if Path(uri).exists():
                raise ValueError("Unfinished stage table; inspect it before retrying: " + uri)
            (
                requests.map_prompt_async(
                    'judge',
                    config=pack,
                    options=options,
                    max_requests=config['judge']['max_calls'],
                    inputs={
                        'instructions': 'prompt_instructions',
                        'payload': 'prompt_payload',
                        'images': 'prompt_images',
                    },
                    output='judge_result',
                    call_output='judge_call',
                    error_output='judge_error',
                    when=lambda r: r.get('judge_status') == 'prepared',
                    concurrency=1,
                    queue_depth=1,
                )
                .map(ApplyJudge(run, config))
                .map(partial(to_stage_row, stage='scores', upstream_identity=files.previous, migrated_us=0))
                .materialize()
                .write_lance(uri, mode='overwrite', schema=PIPELINE_STAGE_ROWS)
            )
            committed = lance.dataset(uri)
            ref = DatasetRef(
                relative[:-6],
                relative,
                committed.version,
                files.schema_name,
                files.schema_version,
                schema_hash(PIPELINE_STAGE_ROWS),
                committed.count_rows(),
            )
            Catalog(files.storage_root).register(ref)
            entry = {"version": version, "dataset_ref": ref.to_dict()}
            files.records.put("stage/" + 'scores' + "/" + version, entry)
        files.stages['scores'] = entry
        (files.reused if reused else files.new).append('scores')
        files.previous = {"stage": 'scores', "stage_version": version}
        _saved = data.read_lance(uri, version=entry["dataset_ref"]["lance_version"]).map(from_stage_row)
        return files.finish()


def run_judging(run):
    """复用本次冻结答案，将评分写入启动时指定的目标表。"""
    # 只读已保存答案版本并运行 judge；更换评分运行不重新生成图片。
    run = Path(run).resolve()
    with run_lock(Path(run).parent / '_demiflow' / Path(run).name):
        manifest = verify_run(run)
        output = run_records(run).get('output') or {}
        target_uri = output.get('uri')
        write_mode = output.get('write_mode', 'overwrite')
        verify_rubrics(run)
        previous = run_state(run)['stages']
        required = [
            'questions',
            'published',
            'catalog',
            'retrieval',
            'jobs',
            'partitions',
            'rubric_requests',
            'rubrics',
            'backend_results',
            'answers',
            'comparison',
        ]
        if any(name not in previous for name in required):
            raise ValueError('judge-only requires completed answer collection')
        config = manifest['config']
        files = EvaluationFiles(
            run, manifest['questions'], manifest['knowledge'], config, graph_version(), manifest['visual']
        )
        files.stages = {name: frozen_stage(run, name) for name in required}
        files.previous = files.stages['comparison']['version']
        pack, options = prompt_config(run, config)
        answer_ref = files.stages['answers']['dataset_ref']
        answers = data.read_lance(
            str(resolve_root() / answer_ref['relative_uri']), version=answer_ref['lance_version']
        ).map(from_stage_row)
        answers.map(lambda row: verify_answer_assets([row])).count()
        version = digest(
            {
                "upstream": files.previous,
                "stage": 'judge_requests',
                "extra": None,
                "implementation": files.manifest.get("implementation"),
            }
        )
        relative = str(
            Path(files.relative).parent
            / ('judge_requests' + '__' + Path(files.relative).name + '__' + version + '.lance')
        )
        uri = str(files.storage_root / relative)
        entry = files.records.get("stage/" + 'judge_requests' + "/" + version)
        reused = entry is not None
        if not reused:
            if Path(uri).exists():
                raise ValueError("Unfinished stage table; inspect it before retrying: " + uri)
            (
                answers.map(PrepareJudge(files.run, pack, config)).map(
                    partial(
                        to_stage_row, stage='judge_requests', upstream_identity=files.previous, migrated_us=0
                    )
                )
            ).write_lance(uri, mode='overwrite', schema=PIPELINE_STAGE_ROWS)
            committed = lance.dataset(uri)
            ref = DatasetRef(
                relative[:-6],
                relative,
                committed.version,
                files.schema_name,
                files.schema_version,
                schema_hash(PIPELINE_STAGE_ROWS),
                committed.count_rows(),
            )
            Catalog(files.storage_root).register(ref)
            entry = {"version": version, "dataset_ref": ref.to_dict()}
            files.records.put("stage/" + 'judge_requests' + "/" + version, entry)
        files.stages['judge_requests'] = entry
        (files.reused if reused else files.new).append('judge_requests')
        files.previous = {"stage": 'judge_requests', "stage_version": version}
        judge_requests = data.read_lance(uri, version=entry["dataset_ref"]["lance_version"]).map(from_stage_row)
        version = digest(
            {
                "upstream": files.previous,
                "stage": 'scores',
                "extra": prompt_responses(files.run, 'judge'),
                "implementation": files.manifest.get("implementation"),
            }
        )
        relative = str(
            Path(files.relative).parent
            / ('scores' + '__' + Path(files.relative).name + '__' + version + '.lance')
        )
        uri = str(files.storage_root / relative)
        entry = files.records.get("stage/" + 'scores' + "/" + version)
        reused = entry is not None
        if not reused:
            if Path(uri).exists():
                raise ValueError("Unfinished stage table; inspect it before retrying: " + uri)
            (
                judge_requests.map_prompt_async(
                    'judge',
                    config=pack,
                    options=options,
                    max_requests=config['judge']['max_calls'],
                    inputs={
                        'instructions': 'prompt_instructions',
                        'payload': 'prompt_payload',
                        'images': 'prompt_images',
                    },
                    output='judge_result',
                    call_output='judge_call',
                    error_output='judge_error',
                    when=lambda r: r.get('judge_status') == 'prepared',
                    concurrency=1,
                    queue_depth=1,
                )
                .map(ApplyJudge(files.run, config))
                .map(partial(to_stage_row, stage='scores', upstream_identity=files.previous, migrated_us=0))
                .materialize()
                .write_lance(uri, mode='overwrite', schema=PIPELINE_STAGE_ROWS)
            )
            committed = lance.dataset(uri)
            ref = DatasetRef(
                relative[:-6],
                relative,
                committed.version,
                files.schema_name,
                files.schema_version,
                schema_hash(PIPELINE_STAGE_ROWS),
                committed.count_rows(),
            )
            Catalog(files.storage_root).register(ref)
            entry = {"version": version, "dataset_ref": ref.to_dict()}
            files.records.put("stage/" + 'scores' + "/" + version, entry)
        files.stages['scores'] = entry
        (files.reused if reused else files.new).append('scores')
        files.previous = {"stage": 'scores', "stage_version": version}
        scored = data.read_lance(uri, version=entry["dataset_ref"]["lance_version"]).map(from_stage_row)
        version = digest(
            {
                "upstream": files.previous,
                "stage": 'evaluation',
                "extra": None,
                "implementation": files.manifest.get("implementation"),
            }
        )
        relative = str(
            Path(files.relative).parent
            / ('evaluation' + '__' + Path(files.relative).name + '__' + version + '.lance')
        )
        if target_uri:
            relative = str(Path(target_uri).relative_to(files.storage_root))
        uri = str(files.storage_root / relative)
        entry = files.records.get("stage/" + 'evaluation' + "/" + version)
        reused = entry is not None
        if not reused:
            if not target_uri and Path(uri).exists():
                raise ValueError("Unfinished stage table; inspect it before retrying: " + uri)
            (
                scored.reduce_by_key(
                    'task_id',
                    group_question_answers,
                )
                .map(compare_group)
                .map(partial(to_stage_row, stage='evaluation', upstream_identity=files.previous, migrated_us=0))
            ).write_lance(uri, mode=write_mode, schema=PIPELINE_STAGE_ROWS)
            committed = lance.dataset(uri)
            ref = DatasetRef(
                relative[:-6],
                relative,
                committed.version,
                files.schema_name,
                files.schema_version,
                schema_hash(PIPELINE_STAGE_ROWS),
                committed.count_rows(),
            )
            Catalog(files.storage_root).register(ref)
            entry = {"version": version, "dataset_ref": ref.to_dict()}
            files.records.put("stage/" + 'evaluation' + "/" + version, entry)
        files.stages['evaluation'] = entry
        (files.reused if reused else files.new).append('evaluation')
        files.previous = {"stage": 'evaluation', "stage_version": version}
        evaluation = data.read_lance(uri, version=entry["dataset_ref"]["lance_version"]).map(from_stage_row)
        summary_value = aggregate(evaluation.take_all())
        summary = data.from_items([summary_value])
        version = digest(
            {
                "upstream": files.previous,
                "stage": 'summary',
                "extra": None,
                "implementation": files.manifest.get("implementation"),
            }
        )
        relative = str(
            Path(files.relative).parent
            / ('summary' + '__' + Path(files.relative).name + '__' + version + '.lance')
        )
        uri = str(files.storage_root / relative)
        entry = files.records.get("stage/" + 'summary' + "/" + version)
        reused = entry is not None
        if not reused:
            if Path(uri).exists():
                raise ValueError("Unfinished stage table; inspect it before retrying: " + uri)
            (
                summary.map(
                    partial(to_stage_row, stage='summary', upstream_identity=files.previous, migrated_us=0)
                )
            ).write_lance(uri, mode='overwrite', schema=PIPELINE_STAGE_ROWS)
            committed = lance.dataset(uri)
            ref = DatasetRef(
                relative[:-6],
                relative,
                committed.version,
                files.schema_name,
                files.schema_version,
                schema_hash(PIPELINE_STAGE_ROWS),
                committed.count_rows(),
            )
            Catalog(files.storage_root).register(ref)
            entry = {"version": version, "dataset_ref": ref.to_dict()}
            files.records.put("stage/" + 'summary' + "/" + version, entry)
        files.stages['summary'] = entry
        (files.reused if reused else files.new).append('summary')
        files.previous = {"stage": 'summary', "stage_version": version}
        summary = data.read_lance(uri, version=entry["dataset_ref"]["lance_version"]).map(from_stage_row)
        state = files.finish()
        return state


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--target', help='最终评分表路径')
    parser.add_argument(
        '--write-mode', choices=['append', 'overwrite'], default='overwrite', help='目标表追加或覆盖'
    )
    parser.add_argument('--sources', type=Path, help='JSON: articles/visuals 的 uri/version 列表')
    parser.add_argument('--questions', type=Path)
    parser.add_argument('--knowledge-runs', nargs='+')
    parser.add_argument('--visual-runs', nargs='*', default=[])
    parser.add_argument('--config', type=Path)
    parser.add_argument('--through', choices=['prepare', 'rubrics', 'generate', 'judge'], default='prepare')
    parser.add_argument(
        '--judge-only', action='store_true', help='Score frozen answers without image-model execution'
    )
    parser.add_argument('--backend', choices=['bagel', 'qwen2511', 'qwen2512', 'gemini'])
    args = parser.parse_args()
    if args.judge_only:
        if args.questions or args.knowledge_runs or args.visual_runs or args.config:
            parser.error('--judge-only uses only the frozen run')
        print(run_partition_judging(args.run, args.backend) if args.backend else run_judging(args.run))
    elif args.backend:
        if args.through != 'generate':
            parser.error('--backend requires explicit --through generate')
        print(run_backend(args.run, args.backend))
    else:
        from preparation.operaters.runfiles import run_records

        frozen = run_records(args.run).get('manifest')
        config = read(args.config) if args.config else frozen['config'] if frozen else default_config()
        questions = read(args.questions) if args.questions else frozen['questions'] if frozen else None
        sources = read(args.sources) if args.sources else {}
        runs = sources.get('articles', args.knowledge_runs or (frozen['knowledge'] if frozen else []))
        if questions is None or not runs:
            parser.error(
                'Supply --questions (DatasetRef JSON configuration) and --knowledge-runs (release IDs)'
            )
        print(
            run_pipeline(
                args.run,
                questions,
                runs,
                config,
                through=args.through,
                visual_runs=sources.get('visuals', args.visual_runs),
                target_uri=args.target,
                write_mode=args.write_mode,
            )
        )


if __name__ == "__main__":
    main()
