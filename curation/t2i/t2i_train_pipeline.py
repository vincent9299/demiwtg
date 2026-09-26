"""T2I 训练：读固定材料表，逐概念执行有限次出题/审核，写已接受样本。"""

from curation.t2i.operaters.transforms import (
    combine_concept_materials,
    build_visual_item,
    apply_figure_review,
    deduplicate_figures,
    select_published_materials,
    apply_visual_review,
    build_text_item,
    decode_article,
    mark_missing_evidence,
    build_figure_item,
    decode_visual_record,
    check_visual_identity,
    filter_missing_figures,
    bind_target_pool,
    paragraph_evidence,
    check_figure_selection,
)

import argparse
from project import resolve_root
import json
import lance
import pyarrow as pa
from demiflow.lance.refs import DatasetRef
from demiflow.lance.registry import Catalog
from demiflow.lance.storage import schema_hash
from pathlib import Path
from demiflow.execution.artifacts import read, digest, run_lock
from functools import partial
from demiflow import data
from preparation.operaters.inputs import source_asset, attach_identity, material_review
from curation.t2i.operaters.runfiles import TrainingRun
from preparation.operaters.runfiles import response_records
from curation.t2i.operaters.materials import design_concepts
from curation.t2i.operaters.image_pool import ExistingImagePool
from curation.t2i.operaters.loop import (
    training_targets,
    training_attempt,
    loop_progress,
    advance_progress,
    loop_stop,
    attempt_waiting,
)
from curation.t2i.operaters.candidates import ExpandCandidates
from curation.t2i.operaters.review import ValidateSample
from curation.t2i.operaters.operators import BindTrainingInputs, fail
from curation.t2i.operaters.results import (
    PinMaterials,
    ExportSample,
    sample_key,
    AUDIT_ROWS,
    TRAINING_SAMPLES,
    encode_audit,
    decode_audit,
)
from curation.t2i.operaters.prompting import (
    prompt_config,
    prepare_design,
    apply_design,
    prepare_review,
    apply_review,
)

DEFAULT_MODEL = {
    'base_url': 'http://127.0.0.1:8000/v1',
    'model': 'qwen3.8-27b',
    'timeout_s': 240,
    'max_output_tokens': 8192,
    'max_calls': None,
}


def config(
    *,
    mode='offline',
    samples_per_concept=5,
    concepts=None,
    max_units=None,
    seed=0,
    max_target_cycles=2,
    max_training_attempts=None,
    reference_batch_size=8,
    max_reference_images=16,
    max_context_chars=60000,
    split_registry=None,
    author_model=None,
    model=None
):
    if mode not in {'offline', 'local', 'modelhub'}:
        raise ValueError('mode must be offline, local or modelhub')
    positive = [
        samples_per_concept,
        max_target_cycles,
        reference_batch_size,
        max_reference_images,
        max_context_chars,
    ]
    if (
        any(type(n) is not int or n < 1 for n in positive)
        or max_reference_images < 2
        or max_context_chars < 100
    ):
        raise ValueError('Invalid per-concept or context limits')
    for n in (max_units, max_training_attempts):
        if n is not None and (type(n) is not int or n < 1):
            raise ValueError('Optional limits must be positive integers')
    if concepts is not None and (
        not isinstance(concepts, (list, tuple))
        or not concepts
        or any(not isinstance(c, str) or not c.strip() for c in concepts)
        or len(set(concepts)) != len(concepts)
    ):
        raise ValueError('concepts must be unique nonempty names')
    defaults = (
        {
            **DEFAULT_MODEL,
            'base_url': 'http://127.0.0.1:4001/v1',
            'model': 'glm/glm-5.3-flash',
            'api_key_env': 'MODELHUB_API_KEY',
            'request_options': {'thinking': {'type': 'disabled'}},
        }
        if mode == 'modelhub'
        else DEFAULT_MODEL
    )
    model = {**defaults, **(model or {})}
    if model['max_calls'] is not None and (type(model['max_calls']) is not int or model['max_calls'] < 1):
        raise ValueError('max_calls must be positive or None')
    if mode != 'offline' and author_model not in (None, model['model']):
        raise ValueError('Author identity must match the configured model')
    return {
        'mode': mode,
        'samples_per_concept': samples_per_concept,
        'concepts': list(concepts) if concepts is not None else None,
        'max_units': max_units,
        'seed': seed,
        'max_target_cycles': max_target_cycles,
        'max_training_attempts': max_training_attempts,
        'reference_batch_size': reference_batch_size,
        'max_reference_images': max_reference_images,
        'max_context_chars': max_context_chars,
        'split_registry': split_registry,
        'author': {'model': author_model or (model['model'] if mode != 'offline' else 'external')},
        'model': model,
        'task_types': ['t2i'],
        'tasks_per_unit': 1,
        'training_design': 'target_aware',
    }


def run_attempt(attempt, files, pack, options):
    """每次只处理一个目标和一页参考，最多一个候选；依次保存出题、审核和导出结果。"""
    run, cfg, guard = files.run, files.config, files.guard
    stages = files.attempt_files(attempt)
    completed = stages.records.get("completed")
    if completed:
        records = (
            data.read_lance(
                str(resolve_root() / (completed['dataset_ref'])["relative_uri"]),
                version=(completed['dataset_ref'])["lance_version"],
            )
            .map(decode_audit)
            .take_all()
        )
        if len(records) != 1:
            raise ValueError("Completed attempt must contain exactly one outcome")
        return records[0], completed["dataset_ref"]
    # 一次尝试只给作者一个目标和当前参考批次；响应按候选展开后再审核。
    stage_name = 'design'
    version = digest(
        {
            "upstream": stages.previous,
            "stage": stage_name,
            "extra": response_records(run, 'design_candidates', attempt['task_id']),
            "implementation": stages.manifest.get("implementation"),
        }
    )
    relative = str(
        Path(stages.relative).parent
        / (stage_name + '__' + Path(stages.relative).name + '__' + version + '.lance')
    )
    uri = str(stages.storage_root / relative)
    entry = stages.records.get("stage/" + stage_name + "/" + version)
    reused = entry is not None
    if not reused:
        if Path(uri).exists():
            raise ValueError("Unfinished stage table; inspect it before retrying: " + uri)
        (
            data.from_items([attempt])
            .map(partial(prepare_design, run=run, pack=pack))
            .map_prompt_async(
                'design_candidates',
                config=pack,
                options=options,
                max_requests=cfg['model']['max_calls'],
                inputs={'payload': 'prompt_payload', 'images': 'prompt_images'},
                output='design_candidates_result',
                call_output='design_candidates_call',
                error_output='design_candidates_error',
                when=lambda row: row['status'] == 'knowledge_available',
                concurrency=1,
                queue_depth=1,
            )
            .map(partial(apply_design, run=run))
            .map(encode_audit)
            .materialize()
            .write_lance(uri, mode='overwrite', schema=AUDIT_ROWS)
        )
        committed = lance.dataset(uri)
        ref = DatasetRef(
            relative[:-6],
            relative,
            committed.version,
            stages.schema_name,
            stages.schema_version,
            schema_hash(AUDIT_ROWS),
            committed.count_rows(),
        )
        Catalog(stages.storage_root).register(ref)
        entry = {"version": version, "dataset_ref": ref.to_dict()}
        stages.records.put("stage/" + stage_name + "/" + version, entry)
    stages.stages[stage_name] = entry
    (stages.reused if reused else stages.new).append(stage_name)
    stages.previous = {"stage": stage_name, "stage_version": version}
    designed = data.read_lance(uri, version=entry["dataset_ref"]["lance_version"]).map(decode_audit)
    stage_name = 'candidates'
    version = digest(
        {
            "upstream": stages.previous,
            "stage": stage_name,
            "extra": None,
            "implementation": stages.manifest.get("implementation"),
        }
    )
    relative = str(
        Path(stages.relative).parent
        / (stage_name + '__' + Path(stages.relative).name + '__' + version + '.lance')
    )
    uri = str(stages.storage_root / relative)
    entry = stages.records.get("stage/" + stage_name + "/" + version)
    reused = entry is not None
    if not reused:
        if Path(uri).exists():
            raise ValueError("Unfinished stage table; inspect it before retrying: " + uri)
        designed.flat_map(ExpandCandidates(guard, cfg)).map(encode_audit).write_lance(
            uri, mode='overwrite', schema=AUDIT_ROWS
        )
        committed = lance.dataset(uri)
        ref = DatasetRef(
            relative[:-6],
            relative,
            committed.version,
            stages.schema_name,
            stages.schema_version,
            schema_hash(AUDIT_ROWS),
            committed.count_rows(),
        )
        Catalog(stages.storage_root).register(ref)
        entry = {"version": version, "dataset_ref": ref.to_dict()}
        stages.records.put("stage/" + stage_name + "/" + version, entry)
    stages.stages[stage_name] = entry
    (stages.reused if reused else stages.new).append(stage_name)
    stages.previous = {"stage": stage_name, "stage_version": version}
    candidates = data.read_lance(uri, version=entry["dataset_ref"]["lance_version"]).map(decode_audit)
    # 每个候选单独审核：先检查字段/材料绑定，再把题目、目标和实际训练输入送入模型。
    stage_name = 'review'
    version = digest(
        {
            "upstream": stages.previous,
            "stage": stage_name,
            "extra": response_records(run, 'review_sample', attempt['task_id']),
            "implementation": stages.manifest.get("implementation"),
        }
    )
    relative = str(
        Path(stages.relative).parent
        / (stage_name + '__' + Path(stages.relative).name + '__' + version + '.lance')
    )
    uri = str(stages.storage_root / relative)
    entry = stages.records.get("stage/" + stage_name + "/" + version)
    reused = entry is not None
    if not reused:
        if Path(uri).exists():
            raise ValueError("Unfinished stage table; inspect it before retrying: " + uri)
        (
            candidates.map(ValidateSample(guard))
            .map(partial(prepare_review, run=run, pack=pack))
            .map_prompt_async(
                "review_sample",
                config=pack,
                options=options,
                max_requests=cfg['model']['max_calls'],
                inputs={"payload": "prompt_payload", "images": "prompt_images"},
                output="review_sample_result",
                call_output="review_sample_call",
                error_output="review_sample_error",
                when=lambda r: r["status"] == "valid_sample",
                concurrency=1,
                queue_depth=1,
            )
            .map(partial(apply_review, run=run))
            .map(encode_audit)
            .materialize()
            .write_lance(uri, mode='overwrite', schema=AUDIT_ROWS)
        )
        committed = lance.dataset(uri)
        ref = DatasetRef(
            relative[:-6],
            relative,
            committed.version,
            stages.schema_name,
            stages.schema_version,
            schema_hash(AUDIT_ROWS),
            committed.count_rows(),
        )
        Catalog(stages.storage_root).register(ref)
        entry = {"version": version, "dataset_ref": ref.to_dict()}
        stages.records.put("stage/" + stage_name + "/" + version, entry)
    stages.stages[stage_name] = entry
    (stages.reused if reused else stages.new).append(stage_name)
    stages.previous = {"stage": stage_name, "stage_version": version}
    reviewed = data.read_lance(uri, version=entry["dataset_ref"]["lance_version"]).map(decode_audit)
    prior = {item["sample_id"] for item in attempt.get("previous_samples", [])}

    def distinct_sample(row):
        if row["status"] == "accepted_task" and "t2i_" + sample_key(row) in prior:
            return fail(
                row,
                "duplicate_sample",
                "Identical instruction, input and target already accepted for this concept",
            )
        return row

    stage_name = 'export'
    version = digest(
        {
            "upstream": stages.previous,
            "stage": stage_name,
            "extra": None,
            "implementation": stages.manifest.get("implementation"),
        }
    )
    relative = str(
        Path(stages.relative).parent
        / (stage_name + '__' + Path(stages.relative).name + '__' + version + '.lance')
    )
    uri = str(stages.storage_root / relative)
    entry = stages.records.get("stage/" + stage_name + "/" + version)
    reused = entry is not None
    if not reused:
        if Path(uri).exists():
            raise ValueError("Unfinished stage table; inspect it before retrying: " + uri)
        (
            reviewed.map(BindTrainingInputs(guard))
            .map(distinct_sample)
            .map(ExportSample(run))
            .map(encode_audit)
        ).write_lance(uri, mode='overwrite', schema=AUDIT_ROWS)
        committed = lance.dataset(uri)
        ref = DatasetRef(
            relative[:-6],
            relative,
            committed.version,
            stages.schema_name,
            stages.schema_version,
            schema_hash(AUDIT_ROWS),
            committed.count_rows(),
        )
        Catalog(stages.storage_root).register(ref)
        entry = {"version": version, "dataset_ref": ref.to_dict()}
        stages.records.put("stage/" + stage_name + "/" + version, entry)
    stages.stages[stage_name] = entry
    (stages.reused if reused else stages.new).append(stage_name)
    stages.previous = {"stage": stage_name, "stage_version": version}
    exported = data.read_lance(uri, version=entry["dataset_ref"]["lance_version"]).map(decode_audit)
    records = exported.take_all()
    if len(records) != 1:
        raise ValueError("One target visit must produce exactly one outcome row")
    stages.records.put(
        'latest',
        {
            'branch': 'training_t2i_attempt',
            'stages': stages.stages,
            'reused_stages': stages.reused,
            'new_stages': stages.new,
        },
        immutable=False,
    )
    ref = stages.stages["export"]["dataset_ref"]
    if not attempt_waiting(records):
        stages.records.put("completed", {"dataset_ref": ref})
    return records[0], ref


def run_concept(row, files, pack, options):
    """逐次执行一个概念的尝试，保存每次结果；接受数达到配置上限即停止。"""
    cfg = files.config
    targets = training_targets([row], cfg)
    progress, previous, attempt_refs = loop_progress(), [], []
    # 每次尝试依赖上次审核结果；达到样本数、访问预算或等待响应时立即停止。
    # 这里是流程控制，不伪装成 Dataset 数据源，避免 limit 的预取触发额外模型调用。
    state = {"stop_reason": None}
    accepted = []
    if not targets:
        state["stop_reason"] = (
            row["status"] if row["status"] != "knowledge_available" else "no_eligible_targets"
        )
        unavailable = {**row, "status": state["stop_reason"], "export_ready": False}
        stage_name = 'unavailable_' + row['unit_id']
        version = digest(
            {
                "upstream": files.previous,
                "stage": stage_name,
                "extra": None,
                "implementation": files.manifest.get("implementation"),
            }
        )
        relative = str(
            Path(files.relative).parent
            / (stage_name + '__' + Path(files.relative).name + '__' + version + '.lance')
        )
        uri = str(files.storage_root / relative)
        entry = files.records.get("stage/" + stage_name + "/" + version)
        reused = entry is not None
        if not reused:
            if Path(uri).exists():
                raise ValueError("Unfinished stage table; inspect it before retrying: " + uri)
            data.from_items([unavailable]).map(encode_audit).write_lance(
                uri, mode='overwrite', schema=AUDIT_ROWS
            )
            committed = lance.dataset(uri)
            ref = DatasetRef(
                relative[:-6],
                relative,
                committed.version,
                files.schema_name,
                files.schema_version,
                schema_hash(AUDIT_ROWS),
                committed.count_rows(),
            )
            Catalog(files.storage_root).register(ref)
            entry = {"version": version, "dataset_ref": ref.to_dict()}
            files.records.put("stage/" + stage_name + "/" + version, entry)
        files.stages[stage_name] = entry
        (files.reused if reused else files.new).append(stage_name)
        files.previous = {"stage": stage_name, "stage_version": version}
        attempt_refs.append(files.stages["unavailable_" + row["unit_id"]]["dataset_ref"])

    while targets and len(accepted) < cfg["samples_per_concept"]:
        stop = loop_stop(progress, len(targets), cfg)
        if stop:
            state["stop_reason"] = stop
            break
        attempt = {**training_attempt(targets, progress, cfg), "previous_samples": list(previous)}
        result, ref = run_attempt(attempt, files, pack, options)
        attempt_refs.append(ref)
        if attempt_waiting([result]):
            state["stop_reason"] = result["status"]
            break  # 调用待响应或失败时停在本次尝试，续跑不跳到下一张图。
        progress = advance_progress(progress, result, len(targets))
        if result.get("export_ready"):
            previous.append(
                {
                    "sample_id": result["training_sample"]["sample_id"],
                    "instruction": result["draft"]["instruction"],
                    "learning_objective": result["learning_objective"],
                    "target_sha256": result["design_targets"][0]["sha256"],
                }
            )
            accepted.append(result)
    if len(accepted) == cfg["samples_per_concept"]:
        state["stop_reason"] = "sample_limit_reached"
    name = "ready_" + row["unit_id"]
    stage_name = name
    version = digest(
        {
            "upstream": files.previous,
            "stage": stage_name,
            "extra": attempt_refs,
            "implementation": files.manifest.get("implementation"),
        }
    )
    relative = str(
        Path(files.relative).parent
        / (stage_name + '__' + Path(files.relative).name + '__' + version + '.lance')
    )
    uri = str(files.storage_root / relative)
    entry = files.records.get("stage/" + stage_name + "/" + version)
    reused = entry is not None
    if not reused:
        if Path(uri).exists():
            raise ValueError("Unfinished stage table; inspect it before retrying: " + uri)
        data.from_items(accepted).map(encode_audit).write_lance(uri, mode='overwrite', schema=AUDIT_ROWS)
        committed = lance.dataset(uri)
        ref = DatasetRef(
            relative[:-6],
            relative,
            committed.version,
            files.schema_name,
            files.schema_version,
            schema_hash(AUDIT_ROWS),
            committed.count_rows(),
        )
        Catalog(files.storage_root).register(ref)
        entry = {"version": version, "dataset_ref": ref.to_dict()}
        files.records.put("stage/" + stage_name + "/" + version, entry)
    files.stages[stage_name] = entry
    (files.reused if reused else files.new).append(stage_name)
    files.previous = {"stage": stage_name, "stage_version": version}
    return {
        "concept": row["concept"],
        "task_id": row["unit_id"],
        "status": state["stop_reason"],
        "accepted_samples": len(accepted),
        "attempts": len(attempt_refs),
        "attempt_refs": attempt_refs,
        "ready_ref": files.stages[name]["dataset_ref"],
    }


def run_pipeline(
    run,
    article_sources,
    config,
    *,
    visual_sources=(),
    through="samples",
    target_uri=None,
    write_mode='overwrite'
):
    """从指定材料表生成训练样本，按 write_mode 追加或覆盖目标表；中间表随运行保存。"""
    if config['task_types'] != ['t2i']:
        raise ValueError('t2i accepts only task_types=["t2i"]')
    if write_mode not in {'append', 'overwrite'}:
        raise ValueError('write_mode must be append or overwrite')
    if through not in {"materials", "samples"}:
        raise ValueError("through must be materials or samples")
    with run_lock(Path(run).parent / '_demiflow' / Path(run).name):
        files = TrainingRun(run, article_sources, visual_sources, config)
        # 固定目标路径与写入模式，避免同名续跑误用其他输出配置的完成记录。
        target_uri = str((files.storage_root / target_uri).resolve()) if target_uri else None
        files.records.put('output', {'uri': target_uri, 'write_mode': write_mode})
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
            for source in files.article_sources
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
            for source in files.visual_sources
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
        # 只用概念名执行已有 seed/max_units 抽样；先过滤概念，再读取其图片与交付材料。
        source_materials = (
            articles.join(visuals, on='concept', how='left').map(combine_concept_materials).union(visual_inputs)
        )
        selected = design_concepts(source_materials.select_columns(['concept']).take_all(), config)
        source_materials = source_materials.filter(lambda row: row['concept'] in selected)
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
        # 解码发布条目后，显式排除保留集材料；文字依赖的配图被排除时，文字也不交给作者。
        pool = ExistingImagePool(run, files.guard, config)
        stage_name = 'materials'
        version = digest(
            {
                "upstream": files.previous,
                "stage": stage_name,
                "extra": None,
                "implementation": files.manifest.get("implementation"),
            }
        )
        relative = str(
            Path(files.relative).parent
            / (stage_name + '__' + Path(files.relative).name + '__' + version + '.lance')
        )
        uri = str(files.storage_root / relative)
        entry = files.records.get("stage/" + stage_name + "/" + version)
        reused = entry is not None
        if not reused:
            if Path(uri).exists():
                raise ValueError("Unfinished stage table; inspect it before retrying: " + uri)
            (
                delivered_materials.map(
                    lambda row: {
                        **{k: v for k, v in row.items() if k != '_candidate_specs'},
                        'knowledge_version': files.knowledge_version,
                        'asset_source': {
                            'specs': row['_candidate_specs'],
                            'concept': row['concept'],
                            'role': 'declared_publication_candidates',
                        },
                    }
                )
                .map(
                    lambda row: {
                        **row,
                        'materials': [
                            {**item, 'knowledge_version': files.knowledge_version} for item in row['materials']
                        ],
                    }
                )
                .map(
                    lambda row: {
                        **row,
                        'issues': row['delivery_issues']
                        + [
                            {'item_id': item['item_id'], 'reason': 'Formal-test reservation'}
                            for item in row['materials']
                            if not files.guard.allows_item(item, True)
                        ],
                        'materials': [item for item in row['materials'] if files.guard.allows_item(item, True)],
                    }
                )
                .map(
                    lambda row: {
                        **row,
                        'figure_ids': {
                            item['image_id'] for item in row['materials'] if item['kind'] == 'image'
                        },
                    }
                )
                .map(filter_missing_figures)
                .map(
                    lambda row: {
                        **row,
                        'unit_id': 'unit_'
                        + digest(['training', row['concept'], row['knowledge_version']])[:20],
                        'branch': 'training',
                        'status': 'knowledge_available' if row['materials'] else 'needs_materials',
                        'sampling_key': digest([config['seed'], row['concept']]),
                        'design_policy': {
                            'task_types': config['task_types'],
                            'max_candidates': config['tasks_per_unit'],
                        },
                        'edit_source': None,
                    }
                )
                .map(lambda row: {**row, 'task_id': row['unit_id']})
                .drop_columns(['figure_ids'])
                .map(
                    lambda row: {
                        **row,
                        'authoring_selected': row['concept'] in selected,
                        'material_policy': {
                            'roles': 'task_local',
                            'text_required': False,
                            'target_source': 'declared_publications',
                            'target_eligibility': 'candidate_only',
                        },
                    }
                )
                .map(
                    lambda row: (
                        {**row, 'target_search': pool.load(row, 'target')}
                        if row['authoring_selected']
                        and row['status'] == 'knowledge_available'
                        and config.get('training_design', 'target_aware') != 'knowledge_first'
                        else row
                    )
                )
                .map(bind_target_pool)
                .map(
                    lambda row: (
                        fail(row, 'needs_target', 'No source-backed target candidates before task design')
                        if 'target_search' in row and not row['target_pool']
                        else row
                    )
                )
                .map(lambda row: {key: value for key, value in row.items() if key != 'target_search'})
                .map(PinMaterials(files.raw_ref))
                .map(encode_audit)
            ).write_lance(uri, mode='overwrite', schema=AUDIT_ROWS)
            committed = lance.dataset(uri)
            ref = DatasetRef(
                relative[:-6],
                relative,
                committed.version,
                files.schema_name,
                files.schema_version,
                schema_hash(AUDIT_ROWS),
                committed.count_rows(),
            )
            Catalog(files.storage_root).register(ref)
            entry = {"version": version, "dataset_ref": ref.to_dict()}
            files.records.put("stage/" + stage_name + "/" + version, entry)
        files.stages[stage_name] = entry
        (files.reused if reused else files.new).append(stage_name)
        files.previous = {"stage": stage_name, "stage_version": version}
        if through == "materials":
            return files.finish()
        pack, options = prompt_config(run, config)
        # 从已提交的材料版本逐概念执行；每个概念的预算、已接受样本独立保存。
        material_ref = files.stages['materials']['dataset_ref']
        summaries = []
        for row in (
            data.read_lance(
                str(resolve_root() / material_ref['relative_uri']), version=material_ref['lance_version']
            )
            .map(decode_audit)
            .iter_rows()
        ):
            summaries.append(run_concept(row, files, pack, options))
        stage_name = 'concepts'
        version = digest(
            {
                "upstream": files.previous,
                "stage": stage_name,
                "extra": digest(summaries),
                "implementation": files.manifest.get("implementation"),
            }
        )
        relative = str(
            Path(files.relative).parent
            / (stage_name + '__' + Path(files.relative).name + '__' + version + '.lance')
        )
        uri = str(files.storage_root / relative)
        entry = files.records.get("stage/" + stage_name + "/" + version)
        reused = entry is not None
        if not reused:
            if Path(uri).exists():
                raise ValueError("Unfinished stage table; inspect it before retrying: " + uri)
            data.from_items(summaries).map(encode_audit).write_lance(uri, mode='overwrite', schema=AUDIT_ROWS)
            committed = lance.dataset(uri)
            ref = DatasetRef(
                relative[:-6],
                relative,
                committed.version,
                files.schema_name,
                files.schema_version,
                schema_hash(AUDIT_ROWS),
                committed.count_rows(),
            )
            Catalog(files.storage_root).register(ref)
            entry = {"version": version, "dataset_ref": ref.to_dict()}
            files.records.put("stage/" + stage_name + "/" + version, entry)
        files.stages[stage_name] = entry
        (files.reused if reused else files.new).append(stage_name)
        files.previous = {"stage": stage_name, "stage_version": version}
        # summaries 只保存各概念的固定表引用；按引用合并全部尝试，失败与待响应保留在 incomplete。
        attempt_sources = [
            data.read_lance(str(resolve_root() / ref['relative_uri']), version=ref['lance_version'])
            for summary in summaries
            for ref in summary['attempt_refs']
        ]
        attempts = (
            attempt_sources[0].union(*attempt_sources[1:])
            if attempt_sources
            else data.from_arrow(pa.Table.from_pylist([], schema=AUDIT_ROWS))
        ).map(decode_audit)
        stage_name = 'attempts'
        version = digest(
            {
                "upstream": files.previous,
                "stage": stage_name,
                "extra": None,
                "implementation": files.manifest.get("implementation"),
            }
        )
        relative = str(
            Path(files.relative).parent
            / (stage_name + '__' + Path(files.relative).name + '__' + version + '.lance')
        )
        uri = str(files.storage_root / relative)
        entry = files.records.get("stage/" + stage_name + "/" + version)
        reused = entry is not None
        if not reused:
            if Path(uri).exists():
                raise ValueError("Unfinished stage table; inspect it before retrying: " + uri)
            attempts.map(encode_audit).write_lance(uri, mode='overwrite', schema=AUDIT_ROWS)
            committed = lance.dataset(uri)
            ref = DatasetRef(
                relative[:-6],
                relative,
                committed.version,
                files.schema_name,
                files.schema_version,
                schema_hash(AUDIT_ROWS),
                committed.count_rows(),
            )
            Catalog(files.storage_root).register(ref)
            entry = {"version": version, "dataset_ref": ref.to_dict()}
            files.records.put("stage/" + stage_name + "/" + version, entry)
        files.stages[stage_name] = entry
        (files.reused if reused else files.new).append(stage_name)
        files.previous = {"stage": stage_name, "stage_version": version}
        attempts = data.read_lance(uri, version=entry["dataset_ref"]["lance_version"]).map(decode_audit)
        stage_name = 'incomplete'
        version = digest(
            {
                "upstream": files.previous,
                "stage": stage_name,
                "extra": None,
                "implementation": files.manifest.get("implementation"),
            }
        )
        relative = str(
            Path(files.relative).parent
            / (stage_name + '__' + Path(files.relative).name + '__' + version + '.lance')
        )
        uri = str(files.storage_root / relative)
        entry = files.records.get("stage/" + stage_name + "/" + version)
        reused = entry is not None
        if not reused:
            if Path(uri).exists():
                raise ValueError("Unfinished stage table; inspect it before retrying: " + uri)
            attempts.filter(lambda r: not r.get("export_ready", False)).map(encode_audit).write_lance(
                uri, mode='overwrite', schema=AUDIT_ROWS
            )
            committed = lance.dataset(uri)
            ref = DatasetRef(
                relative[:-6],
                relative,
                committed.version,
                files.schema_name,
                files.schema_version,
                schema_hash(AUDIT_ROWS),
                committed.count_rows(),
            )
            Catalog(files.storage_root).register(ref)
            entry = {"version": version, "dataset_ref": ref.to_dict()}
            files.records.put("stage/" + stage_name + "/" + version, entry)
        files.stages[stage_name] = entry
        (files.reused if reused else files.new).append(stage_name)
        files.previous = {"stage": stage_name, "stage_version": version}
        ready_sources = [
            data.read_lance(
                str(resolve_root() / summary['ready_ref']['relative_uri']),
                version=summary['ready_ref']['lance_version'],
            )
            for summary in summaries
        ]
        ready = (
            ready_sources[0].union(*ready_sources[1:])
            if ready_sources
            else data.from_arrow(pa.Table.from_pylist([], schema=AUDIT_ROWS))
        ).map(decode_audit)
        fingerprint = digest({'run': files.version, 'concepts': summaries})
        relative = str(
            Path(files.relative).parent
            / ('training_samples__' + Path(files.relative).name + '__' + fingerprint + '.lance')
        )
        if target_uri:
            relative = str(Path(target_uri).relative_to(files.storage_root))
        uri = files.storage_root / relative
        saved = files.records.get('training_samples/' + fingerprint)
        reused = saved is not None
        if not reused:
            ready.map(
                lambda row: {name: row['training_sample'].get(name) for name in TRAINING_SAMPLES.names}
            ).write_lance(str(uri), mode=write_mode, schema=TRAINING_SAMPLES)
            saved = {'version': lance.dataset(str(uri)).version}
            files.records.put('training_samples/' + fingerprint, saved)
        committed = lance.dataset(str(uri), version=saved['version'])
        ref = DatasetRef(
            relative[:-6],
            relative,
            committed.version,
            't2i_training_samples',
            'v1',
            schema_hash(TRAINING_SAMPLES),
            committed.count_rows(),
        )
        Catalog(files.storage_root).register(ref)
        files.stages['training_samples'] = {'dataset_ref': ref.to_dict(), 'fingerprint': fingerprint}
        (files.reused if reused else files.new).append('training_samples')
        return files.finish()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', required=True, type=Path)
    parser.add_argument(
        '--sources',
        required=True,
        type=Path,
        help='Configuration JSON: articles/visuals lists of fixed material references',
    )
    parser.add_argument(
        '--config', type=Path, help='Keyword arguments for config(), including explicit model settings'
    )
    parser.add_argument('--mode', choices=['offline', 'local', 'modelhub'], default=None)
    parser.add_argument('--target', help='训练样本输出表路径')
    parser.add_argument(
        '--write-mode', choices=['append', 'overwrite'], default='overwrite', help='目标表追加或覆盖'
    )
    parser.add_argument('--through', choices=['materials', 'samples'], default='samples')
    args = parser.parse_args()
    options = read(args.config) if args.config else {}
    if args.mode is not None:
        options['mode'] = args.mode
    sources = read(args.sources)
    result = run_pipeline(
        args.run,
        sources.get('articles', []),
        config(**options),
        visual_sources=sources.get('visuals', []),
        through=args.through,
        target_uri=args.target,
        write_mode=args.write_mode,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
