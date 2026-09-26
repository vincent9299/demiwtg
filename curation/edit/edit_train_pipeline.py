"""Edit 训练：读材料，逐图出题、合成与审核，分别写题目表和训练样本表。"""

from curation.edit.operaters.transforms import (
    combine_concept_materials,
    build_visual_item,
    apply_figure_review,
    deduplicate_figures,
    select_published_materials,
    material_image_hashes,
    apply_visual_review,
    build_text_item,
    bind_material_blobs,
    decode_article,
    make_edit_visit,
    mark_missing_evidence,
    build_figure_item,
    decode_visual_record,
    check_visual_identity,
    paragraph_evidence,
    check_figure_selection,
)

import argparse
import gc
import json
import os
import time
from functools import partial
from pathlib import Path

import lance
import pyarrow as pa
from demiflow.execution.artifacts import digest, run_lock
from demiflow.lance.records import LanceRecordStore
from demiflow import data
from preparation.operaters.inputs import source_asset, attach_identity, material_review
from preparation.operaters.inputs import freeze_material_source, SplitGuard, pixels, duplicate
from project import resolve_root
from curation.edit.operaters.contracts import (
    QUESTIONS,
    SAMPLES,
    question_row,
    restore_question,
    training_sample,
)
from curation.edit.operaters.prompting import (
    prompt_pack,
    prepare_design,
    apply_design,
    prepare_review,
    apply_review,
)
from curation.edit.operaters.synthesis import GeneratePair


def config(
    *,
    concepts=None,
    samples_per_concept=5,
    max_target_cycles=2,
    max_attempts_per_concept=20,
    reference_batch_size=8,
    model=None,
    synthesis=None,
    split_registry=None,
):
    limits = (samples_per_concept, max_target_cycles, max_attempts_per_concept, reference_batch_size)
    if any(type(n) is not int or n < 1 for n in limits):
        raise ValueError('Limits must be positive integers')
    return dict(
        concepts=concepts,
        samples_per_concept=samples_per_concept,
        max_target_cycles=max_target_cycles,
        max_attempts_per_concept=max_attempts_per_concept,
        reference_batch_size=reference_batch_size,
        model={
            'name': 'qwen3.8-27b',
            'base_url': 'http://127.0.0.1:8000/v1',
            'timeout_s': 240,
            'max_tokens': 4096,
            **(model or {}),
        },
        synthesis={
            'model_path': str(Path(__file__).resolve().parents[3] / 'models/Qwen-Image-2.1'),
            'device': 'cuda:1',
            'steps': 40,
            'resolution': 1024,
            'seed': 42,
            **(synthesis or {}),
        },
        split_registry=split_registry
        or {
            'schema': 'v4-split-registry/1',
            'scope': 'development_only',
            'formal_test': {'concepts': [], 'rule_families': [], 'images': []},
        },
    )


def run_attempt(row, *, run, cfg, stage='all', image_model=None, model_load_seconds=0.0):
    """执行一道题的出题、合成和审核；各阶段先读已保存版本，避免重复调用。"""
    root = resolve_root()
    run = Path(run)
    suffix = '__' + run.name + '__' + row['task_id'] + '.lance'
    run.parent.mkdir(parents=True, exist_ok=True)
    guard = SplitGuard(cfg['split_registry'])
    os.environ.setdefault('EDIT_LOCAL_MODEL_KEY', 'local-no-auth')
    pack = prompt_pack(cfg['model'])
    options = {
        'lance_journal': {
            'root': str(root),
            'relative_uri': str((run.parent / ('calls' + suffix)).relative_to(root)),
        },
        'timeout_s': cfg['model']['timeout_s'],
        'trust_env': False,
        'verify_model': True,
        'require_finish_reason_stop': True,
        'request_options': {
            'temperature': 0,
            'max_tokens': cfg['model']['max_tokens'],
            'response_format': {'type': 'json_object'},
            'chat_template_kwargs': {'enable_thinking': False},
        },
    }
    question_uri = str(run.parent / ('question' + suffix))
    if Path(question_uri).exists():
        row = restore_question(data.read_lance(question_uri, version=1).take_all()[0])
    else:
        # 一次访问设计一道题；等异步响应完整返回后，按 QUESTIONS 字段类型写出版本 1。
        (
            data.from_items([row])
            .map(prepare_design)
            .map_prompt_async(
                'design_edit',
                config=pack,
                options=options,
                inputs={'payload': 'prompt_payload', 'images': 'prompt_images'},
                output='design_result',
                call_output='design_call',
                error_output='design_error',
                concurrency=1,
            )
            .map(partial(apply_design, guard=guard))
            .map(question_row)
            .materialize()
            .write_lance(question_uri, schema=QUESTIONS)
        )
        row = restore_question(data.read_lance(question_uri, version=1).take_all()[0])
    if stage == 'design' or row['status'] != 'designed':
        return row

    pair_uri = str(run.parent / ('pair' + suffix))
    if Path(pair_uri).exists():
        row = restore_question(data.read_lance(pair_uri, version=1).take_all()[0])
    else:
        if image_model is None:
            raise ValueError('Supply a loaded official QwenImage21Pipeline for synthesis')
        reservation_uri = str(run.parent / ('synthesis_request' + suffix))
        if Path(reservation_uri).exists():
            raise RuntimeError(
                'Unfinished synthesis reservation: inspect this case before retrying; use a new run'
            )
        data.from_items(
            [
                {
                    'task_id': row['task_id'],
                    'config': json.dumps(cfg['synthesis']),
                    'question_uri': question_uri,
                    'question_version': 1,
                }
            ]
        ).write_lance(reservation_uri)
        # 一行一题；actor 持有共享 GPU 模型，异步执行后直接写题表。
        (
            data.read_lance(question_uri, version=1)
            .map(restore_question)
            .map_async(
                GeneratePair(
                    model=image_model,
                    config=cfg['synthesis'],
                    root=root,
                    blob_uri=run.parent / ('generated' + suffix),
                    load_seconds=model_load_seconds,
                ),
                concurrency=1,
            )
            .map(question_row)
            .materialize()
            .write_lance(pair_uri, schema=QUESTIONS)
        )
        row = restore_question(data.read_lance(pair_uri, version=1).take_all()[0])
    if stage == 'synthesis' or row['status'] != 'pair_ready':
        return row

    review_uri = str(run.parent / ('review' + suffix))
    if not Path(review_uri).exists():
        (
            data.read_lance(pair_uri, version=1)
            .map(restore_question)
            .map(prepare_review)
            .map_prompt_async(
                'review_edit',
                config=pack,
                options=options,
                inputs={'payload': 'prompt_payload', 'images': 'prompt_images'},
                output='review_result',
                call_output='review_call',
                error_output='review_error',
                concurrency=1,
            )
            .map(apply_review)
            .map(question_row)
            .materialize()
            .write_lance(review_uri, schema=QUESTIONS)
        )
    return restore_question(data.read_lance(review_uri, version=1).take_all()[0])


def run_pipeline(
    run,
    sources,
    cfg,
    *,
    stage='all',
    image_model=None,
    target_uri=None,
    questions_uri=None,
    write_mode='overwrite',
    questions_write_mode='overwrite',
):
    """生成 Edit 样本，训练表与题目表分别配置目标及写入模式；stage 支持分阶段运行。"""
    if write_mode not in {'append', 'overwrite'} or questions_write_mode not in {'append', 'overwrite'}:
        raise ValueError('write_mode and questions_write_mode must be append or overwrite')
    if stage not in {'all', 'design', 'synthesis', 'review'}:
        raise ValueError('Unknown stage')
    root = resolve_root()
    run = Path(run).resolve()
    if not run.is_relative_to(root / 'demiwtg/curation/edit/datasets'):
        raise ValueError('Use <workspace>/demiwtg/curation/edit/datasets/<run_id> as the run identity')
    with run_lock(run.parent / '_demiflow' / run.name):
        sources = {
            **sources,
            'articles': [freeze_material_source(s) for s in sources.get('articles', [])],
            'visuals': [freeze_material_source(s) for s in sources.get('visuals', [])],
        }
        if 'uri' in sources['raw_images_ref']:
            sources['raw_images_ref'] = freeze_material_source(sources['raw_images_ref'])
        if not sources['articles'] and not sources['visuals']:
            raise ValueError('At least one fixed material publication is required')
        code_root = Path(__file__).parent
        code = {
            str(p.relative_to(code_root)): p.read_text()
            for folder in ('operaters', 'prompts')
            for p in (code_root / folder).iterdir()
            if p.suffix in {'.py', '.md', '.yaml'}
        }
        code['edit_train_pipeline.py'] = Path(__file__).read_text()
        # 两张业务输出表分别配置路径与模式并冻结；阶段更新用 overwrite，新批次可 append。
        target_uri = str((root / target_uri).resolve()) if target_uri else None
        questions_uri = str((root / questions_uri).resolve()) if questions_uri else None
        manifest = {
            'sources': sources,
            'config': cfg,
            'implementation': code,
            'target_uri': target_uri,
            'questions_uri': questions_uri,
            'write_mode': write_mode,
            'questions_write_mode': questions_write_mode,
        }
        records = LanceRecordStore(
            root, str((run.parent / ('metadata__' + run.name + '.lance')).relative_to(root))
        )
        records.put('manifest', manifest)  # 同名运行不允许更换输入、配置或源码。
        guard = SplitGuard(cfg['split_registry'])
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
            for source in sources['articles']
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
            for source in sources['visuals']
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
        # 保留允许用于训练的条目；同概念同 SHA 的图片只保留首次出现的位置，文字保持原有顺序。
        prepared = (
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
            .map(lambda row: {**row, 'source_refs': row['_candidate_specs']})
            .map(
                lambda row: {
                    **row,
                    'materials': [item for item in row['materials'] if guard.allows_item(item, training=True)],
                }
            )
            .map(
                lambda row: {
                    **row,
                    'materials': [
                        item
                        for index, item in enumerate(row['materials'])
                        if item['kind'] != 'image'
                        or item['asset']['sha256']
                        not in [
                            prior['asset']['sha256']
                            for prior in row['materials'][:index]
                            if prior['kind'] == 'image'
                        ]
                    ],
                }
            )
            .map(partial(bind_material_blobs, sources=sources))
            .map(material_image_hashes)
        )
        owned_model = image_model is None
        load_seconds = 0.0
        if stage in {'all', 'synthesis'} and image_model is None:
            import torch
            from diffusers import QwenImage21Pipeline

            started = time.perf_counter()
            image_model = QwenImage21Pipeline.from_pretrained(
                cfg['synthesis']['model_path'], torch_dtype=torch.bfloat16, local_files_only=True
            ).to(cfg['synthesis']['device'])
            load_seconds = time.perf_counter() - started
        outcome_refs, sample_refs, summaries = [], [], []
        try:
            for concept in prepared.iter_rows():
                # 每个概念按图片和参考页逐次尝试；一张图本轮接受一题后换下一张。
                # 样本数与尝试数独立计数，阶段执行不为尚未设计的题创建合成请求。
                previous, accepted, count, stop_concept = [], [], 0, False
                # 一行是一张现有图片的一次访问；参考页排除目标同图/近似图，批内文字只保留配图齐全的条目。
                visits = (
                    data.from_items([concept])
                    .flat_map(
                        lambda row: [
                            {**row, 'existing': image['asset'], 'cycle': cycle}
                            for cycle in range(cfg['max_target_cycles'])
                            for image in row['materials']
                            if image['kind'] == 'image'
                        ]
                    )
                    .map(
                        lambda row: {
                            **row,
                            'materials': [
                                item
                                for item in row['materials']
                                if item['kind'] != 'image' or not duplicate(item['asset'], row['existing'])
                            ],
                        }
                    )
                    .map(
                        lambda row: {
                            **row,
                            'batches': [
                                row['materials'][i : i + cfg['reference_batch_size']]
                                for i in range(0, len(row['materials']), cfg['reference_batch_size'])
                            ]
                            or [[]],
                        }
                    )
                )
                for visit in visits.iter_rows():
                    if stop_concept or len(accepted) >= cfg['samples_per_concept']:
                        break
                    cases = (
                        data.from_items([visit])
                        .flat_map(
                            lambda row: [
                                {**row, 'page': page, 'materials': materials}
                                for page, materials in enumerate(row['batches'])
                            ]
                        )
                        .map(
                            lambda row: {
                                **row,
                                'figure_ids': {m['image_id'] for m in row['materials'] if m['kind'] == 'image'},
                            }
                        )
                        .map(
                            lambda row: {
                                **row,
                                'materials': [
                                    m
                                    for m in row['materials']
                                    if not set(m.get('publication', {}).get('visual_dependencies', []))
                                    - row['figure_ids']
                                ],
                            }
                        )
                        .map(make_edit_visit)
                    )
                    for row in cases.iter_rows():
                        if count >= cfg['max_attempts_per_concept']:
                            stop_concept = True
                            break
                        row = {**row, 'previous_samples': list(previous)}
                        # 只执行合成/审核时，必须已有这道题的设计结果；不偷偷创建新题。
                        if (
                            stage in {'synthesis', 'review'}
                            and not (
                                run.parent / ('question__' + run.name + '__' + row['task_id'] + '.lance')
                            ).exists()
                        ):
                            stop_concept = True
                            break
                        row = run_attempt(
                            row,
                            run=run,
                            cfg=cfg,
                            stage=stage,
                            image_model=image_model,
                            model_load_seconds=load_seconds,
                        )
                        count += 1
                        # 反馈循环只保留已落盘结果的引用；最终明细重新通过 reader 合并。
                        outcome_refs.append(
                            next(
                                run.parent / (name + '__' + run.name + '__' + row['task_id'] + '.lance')
                                for name in ('review', 'pair', 'question')
                                if (
                                    run.parent / (name + '__' + run.name + '__' + row['task_id'] + '.lance')
                                ).exists()
                            )
                        )
                        print(
                            f"{row['task_id']} {row['concept']} {row['status']} "
                            f"design={row.get('design_seconds')}s synthesis={row.get('synthesis_seconds')}s "
                            f"review={row.get('review_seconds')}s",
                            flush=True,
                        )
                        if row['status'] == 'accepted':
                            key = digest([row['input_content'], row['target']['blob_ref']])
                            if any(p['sample_key'] == key for p in previous):
                                break
                            previous.append({'instruction': row['instruction'], 'sample_key': key})
                            accepted.append(
                                run.parent / ('review__' + run.name + '__' + row['task_id'] + '.lance')
                            )
                            break  # 当前图片访问已接受一题，不再尝试后续参考页
                        if row['status'] in {
                            'designed',
                            'pair_ready',
                            'design_failed',
                            'review_failed',
                            'synthesis_failed',
                        }:
                            break
                sample_refs.extend(accepted)
                summaries.append({'concept': concept['concept'], 'attempts': count, 'accepted': len(accepted)})
        finally:
            if owned_model and image_model is not None:
                del image_model
                gc.collect()
                import torch

                torch.cuda.empty_cache()
        # 同一阶段结果只提交一次；两张表分别登记，避免第二张写入失败后重试重复追加第一张。
        question_sources = [data.read_lance(str(ref), version=1) for ref in outcome_refs]
        questions = (
            question_sources[0].union(*question_sources[1:])
            if question_sources
            else data.from_arrow(pa.Table.from_pylist([], schema=QUESTIONS))
        ).materialize()
        # 原有提交指纹需查看本轮题目元数据；明细写入仍使用同一个 Dataset。
        snapshot = digest([stage, questions.take_all()])[:20]
        result = records.get('outputs/' + snapshot)
        if result is None:
            questions_uri = questions_uri or str(
                run.parent / ('questions__' + run.name + '__' + snapshot + '.lance')
            )
            samples_uri = target_uri or str(
                run.parent / ('training_samples__' + run.name + '__' + snapshot + '.lance')
            )
            training_sources = [
                data.read_lance(str(ref), version=1)
                .map(restore_question)
                .map(partial(training_sample, audit_uri=ref))
                for ref in sample_refs
            ]
            training = (
                training_sources[0].union(*training_sources[1:])
                if training_sources
                else data.from_arrow(pa.Table.from_pylist([], schema=SAMPLES))
            )
            question_output = records.get('question_output/' + snapshot)
            if question_output is None:
                questions.write_lance(questions_uri, mode=questions_write_mode, schema=QUESTIONS)
                question_output = {'version': lance.dataset(questions_uri).version}
                records.put('question_output/' + snapshot, question_output)
            sample_output = records.get('sample_output/' + snapshot)
            if sample_output is None:
                training.write_lance(samples_uri, mode=write_mode, schema=SAMPLES)
                sample_output = {'version': lance.dataset(samples_uri).version}
                records.put('sample_output/' + snapshot, sample_output)
            result = {
                'questions_uri': questions_uri,
                'training_samples_uri': samples_uri,
                'version': question_output['version'],
                'training_samples_version': sample_output['version'],
                'summaries': summaries,
                'stage': stage,
            }
            records.put('outputs/' + snapshot, result)
        records.put('latest', result, immutable=False)
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', required=True, type=Path)
    parser.add_argument('--sources', required=True, type=Path)
    parser.add_argument('--config', type=Path)
    parser.add_argument('--target', help='训练样本输出表路径')
    parser.add_argument('--questions-target', help='题目及审核结果输出表路径')
    parser.add_argument(
        '--write-mode', choices=['append', 'overwrite'], default='overwrite', help='训练表追加或覆盖'
    )
    parser.add_argument(
        '--questions-write-mode', choices=['append', 'overwrite'], default='overwrite', help='题目表追加或覆盖'
    )
    parser.add_argument('--stage', choices=['all', 'design', 'synthesis', 'review'], default='all')
    args = parser.parse_args()
    cfg = config(**(json.loads(args.config.read_text()) if args.config else {}))
    result = run_pipeline(
        args.run,
        json.loads(args.sources.read_text()),
        cfg,
        stage=args.stage,
        target_uri=args.target,
        questions_uri=args.questions_target,
        write_mode=args.write_mode,
        questions_write_mode=args.questions_write_mode,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
