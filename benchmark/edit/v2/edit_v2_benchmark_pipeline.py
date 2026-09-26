"""从固定输入读取出题单位，展开构题请求、检查响应，按配置写基准题表。"""

from benchmark.edit.v2.operaters.transforms import (
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
    paragraph_evidence,
    check_figure_selection,
)

import lance
import pyarrow as pa
from demiflow.execution.artifacts import digest
from demiflow.lance.refs import DatasetRef
from demiflow.lance.registry import Catalog
from demiflow.lance.storage import schema_hash
from preparation.operaters.results import PIPELINE_STAGE_ROWS, to_stage_row, from_stage_row
import argparse
from project import resolve_root
import json
from pathlib import Path
from demiflow.execution.artifacts import read, run_lock
from functools import partial
from demiflow import data
from preparation.operaters.inputs import source_asset, attach_identity, material_review
from benchmark.edit.v2.operaters.authoring import (
    graph_version,
    AuthoringRunFiles,
    split_guard,
)
from benchmark.edit.v2.operaters.prompting import (
    prompt_config, prompt_responses, prepare_design, apply_design,
)

DEFAULT_MODEL = {
    "base_url": "http://127.0.0.1:8000/v1",
    "model": "qwen3.8-27b",
    "max_output_tokens": 4096,
    "max_calls": 12,
    "timeout_s": 240,
}


def config(
    mode,
    registry=None,
    max_calls=12,
    author_model=None,
    author_effort=None,
    *,
    seed=0,
    max_units=2,
    task_types=('edit',),
    max_context_chars=60000,
    max_reference_images=16,
    concepts=None,
):
    """配置概念范围、图片/上下文及调用预算；每概念固定选一张原图并出一道题。"""
    if mode not in {'offline', 'local'}:
        raise ValueError('mode must be offline or local')
    if mode == 'local' and author_model not in {None, DEFAULT_MODEL['model']}:
        raise ValueError('Local transport uses the configured local Qwen, not an offline author')
    if max_units is not None and (type(max_units) is not int or max_units < 1):
        raise ValueError('max_units must be a positive integer or None')
    if any(type(value) is not int or value < 1 for value in (max_calls, max_reference_images)):
        raise ValueError('Run budgets must be positive integers')
    if list(task_types) != ['edit'] or max_context_chars < 100:
        raise ValueError('Invalid task types or context limits')
    if concepts is not None and (
        not concepts or isinstance(concepts, str)
        or any(not isinstance(c, str) or not c.strip() for c in concepts)
        or len(set(concepts)) != len(concepts)
    ):
        raise ValueError('Concept selection must be nonempty unique names, or None for all')
    return {
        'mode': mode,
        'split_registry': read(registry) if registry else None,
        'concepts': list(concepts) if concepts is not None else None,
        'seed': seed, 'max_units': max_units, 'task_types': list(task_types),
        'max_context_chars': max_context_chars, 'max_reference_images': max_reference_images,
        'author': {
            'backend': mode,
            'model': author_model or (DEFAULT_MODEL['model'] if mode == 'local' else 'external'),
            'reasoning_effort': author_effort,
        },
        'model': {**DEFAULT_MODEL, 'max_calls': max_calls},
    }


def run_pipeline(
    run, knowledge_runs, config, through="candidates", visual_runs=None, *, target_uri=None, write_mode='overwrite'
):
    """每概念一次请求完成选图及单题设计；待审题按 write_mode 写目标表，失败留设计表。"""
    if write_mode not in {'append', 'overwrite'}:
        raise ValueError('write_mode must be append or overwrite')
    if through not in {'knowledge', 'design', 'candidates'}:
        raise ValueError('through must be knowledge, design or candidates')
    with run_lock(Path(run).parent / '_demiflow' / Path(run).name):
        # 冻结来源表版本和出题配置；下面分别读取文章与视觉条目，再按 concept 关联。
        files = AuthoringRunFiles(
            run, knowledge_runs, "benchmark", config, graph_version("benchmark"), visual_runs=visual_runs
        )
        # 最终题表路径和写入模式独立配置；同名续跑固定输出配置。
        target_uri = str((files.storage_root / target_uri).resolve()) if target_uri else None
        files.records.put('output', {'uri': target_uri, 'write_mode': write_mode})
        guard = split_guard(files)
        pack, options = prompt_config(run, config)
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
            for source in files.knowledge_runs
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
            for source in files.visual_runs
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
        # 解码发布条目后，显式排除保留集材料；文字依赖的配图被排除时，文字也不交给作者。
        version = digest(
            {
                "upstream": files.previous,
                "stage": 'knowledge',
                "extra": None,
                "implementation": files.manifest.get("implementation"),
            }
        )
        relative = str(
            Path(files.relative).parent
            / ('knowledge' + '__' + Path(files.relative).name + '__' + version + '.lance')
        )
        uri = str(files.storage_root / relative)
        entry = files.records.get("stage/" + 'knowledge' + "/" + version)
        reused = entry is not None
        if not reused:
            if Path(uri).exists():
                raise ValueError("Unfinished stage table; inspect it before retrying: " + uri)
            (
                delivered_materials.map(
                    lambda row: {
                        **{k: v for k, v in row.items() if k != '_candidate_specs'},
                        'knowledge_version': files.knowledge_version,
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
                            if not guard.allows_item(item, False)
                        ],
                        'materials': [item for item in row['materials'] if guard.allows_item(item, False)],
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
                        + digest(['benchmark', 'edit', row['concept'], row['knowledge_version']])[:20],
                        'branch': 'benchmark',
                        'status': 'knowledge_available' if row['materials'] else 'needs_materials',
                        'sampling_key': digest([config['seed'], row['concept']]),
                        'edit_source': None,
                    }
                )
                .map(lambda row: {**row, 'task_id': row['unit_id']})
                .drop_columns(['figure_ids'])
                .map(partial(to_stage_row, stage='knowledge', upstream_identity=files.previous, migrated_us=0))
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
            files.records.put("stage/" + 'knowledge' + "/" + version, entry)
        files.stages['knowledge'] = entry
        (files.reused if reused else files.new).append('knowledge')
        files.previous = {"stage": 'knowledge', "stage_version": version}
        knowledge = data.read_lance(uri, version=entry["dataset_ref"]["lance_version"]).map(from_stage_row)
        if through == "knowledge":
            return files.finish()
        scope = knowledge
        # 每个选中概念保留一行；缺材料/缺图片也留状态，不在模型调用前丢失。
        if config.get("concepts"):
            scope = scope.filter(lambda r: r["concept"] in config["concepts"])
        if config["max_units"] is not None:
            ranks = scope.map(lambda r: (r["sampling_key"], r["unit_id"])).take_all()
            selected_ids = {unit for _, unit in sorted(ranks)[: config["max_units"]]}
            scope = scope.filter(lambda r: r["unit_id"] in selected_ids)
        version = digest(
            {
                "upstream": files.previous,
                "stage": 'design',
                "extra": prompt_responses(run),
                "implementation": files.manifest.get("implementation"),
            }
        )
        relative = str(
            Path(files.relative).parent
            / ('design' + '__' + Path(files.relative).name + '__' + version + '.lance')
        )
        uri = str(files.storage_root / relative)
        entry = files.records.get("stage/" + 'design' + "/" + version)
        reused = entry is not None
        if not reused:
            if Path(uri).exists():
                raise ValueError("Unfinished stage table; inspect it before retrying: " + uri)
            (
                scope.map(partial(prepare_design, run=run, pack=pack))
                .map_prompt_async(
                    "design_question",
                    config=pack,
                    options=options,
                    max_requests=config['model']['max_calls'],
                    inputs={"payload": "prompt_payload", "images": "prompt_images"},
                    output="design_result",
                    call_output="design_call",
                    error_output="design_error",
                    when=lambda r: r["status"] == "knowledge_available",
                    concurrency=1,
                    queue_depth=1,
                )
                .map(apply_design, fn_kwargs={
                    'run': run, 'guard': guard,
                    'question_schema': pack.prompt_definitions['design_question'].response_schema[
                        'properties']['result']['properties']['question'],
                })
                .map(partial(to_stage_row, stage='design', upstream_identity=files.previous, migrated_us=0))
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
            files.records.put("stage/" + 'design' + "/" + version, entry)
        files.stages['design'] = entry
        (files.reused if reused else files.new).append('design')
        files.previous = {"stage": 'design', "stage_version": version}
        designed = data.read_lance(uri, version=entry["dataset_ref"]["lance_version"]).map(from_stage_row)
        if through == "design":
            return files.finish()
        # 每个成功概念直接投影成一道待审题；绑定已发送图片的原图引用，不再检索、定稿或审题。
        version = digest(
            {
                "upstream": files.previous,
                "stage": 'candidates',
                "extra": None,
                "implementation": files.manifest.get("implementation"),
            }
        )
        relative = str(
            Path(files.relative).parent
            / ('candidates' + '__' + Path(files.relative).name + '__' + version + '.lance')
        )
        if target_uri:
            relative = str(Path(target_uri).relative_to(files.storage_root))
        uri = str(files.storage_root / relative)
        entry = files.records.get("stage/" + 'candidates' + "/" + version)
        reused = entry is not None
        if not reused:
            if not target_uri and Path(uri).exists():
                raise ValueError("Unfinished stage table; inspect it before retrying: " + uri)
            (
                designed.filter(lambda row: row['status'] == 'unreviewed').map(
                    lambda row: {
                        'task_id': 'edit_' + digest([row['concept'], row['question'], row['edit_source']['sha256'],
                                                   [item['item_id'] for item in row['materials']]]),
                        'concept': row['concept'], 'status': 'unreviewed', 'task_type': 'edit',
                        **row['question'], 'edit_source': row['edit_source'],
                        'source_material_number': row['source_material_number'],
                        'materials': row['materials'], 'knowledge_version': row['knowledge_version'],
                        'design_provenance': row['design_provenance'],
                    }
                ).map(
                    partial(to_stage_row, stage='candidates', upstream_identity=files.previous, migrated_us=0)
                )
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
            files.records.put("stage/" + 'candidates' + "/" + version, entry)
        files.stages['candidates'] = entry
        (files.reused if reused else files.new).append('candidates')
        files.previous = {"stage": 'candidates', "stage_version": version}
        return files.finish()


def main():
    branch = "benchmark"
    parser = argparse.ArgumentParser(
        description=f"V2 independent EDIT {branch} pipeline (demiflow Python pipeline)"
    )
    parser.add_argument("--run", required=True, type=Path)
    parser.add_argument('--sources', type=Path, help='JSON: articles/visuals 的 uri/version 列表')
    parser.add_argument('--target', help='最终题表路径')
    parser.add_argument(
        '--write-mode', choices=['append', 'overwrite'], default='overwrite', help='目标表追加或覆盖'
    )
    parser.add_argument('--knowledge-runs', nargs='*', default=[])
    parser.add_argument(
        '--visual-runs',
        nargs='*',
        default=[],
        help='Independent visual publication runs/releases; combined per concept with articles',
    )
    parser.add_argument(
        "--split-registry", type=Path, help='Existing formal-test reservation; omitted means development only'
    )
    parser.add_argument('--concepts', nargs='+', help='Published concept scope; omitted means all')
    parser.add_argument('--seed', type=int, default=0)
    parser.add_argument('--max-units', type=int, default=2)
    parser.add_argument('--task-types', choices=['edit'], nargs='+', default=['edit'])
    parser.add_argument("--mode", choices=["offline", "local"], default="offline")
    parser.add_argument('--through', choices=['knowledge', 'design', 'candidates'], default='candidates')
    parser.add_argument('--max-context-chars', type=int, default=60000)
    parser.add_argument('--max-reference-images', type=int, default=16)
    parser.add_argument("--max-calls", type=int, default=12)
    parser.add_argument("--author-model", help="Offline author identity recorded in the frozen run")
    parser.add_argument("--author-effort", help="Offline author reasoning effort")
    args = parser.parse_args()
    if args.max_calls < 1:
        parser.error("--max-calls must be positive")
    sources = (
        read(args.sources) if args.sources else {'articles': args.knowledge_runs, 'visuals': args.visual_runs}
    )
    result = run_pipeline(
        args.run,
        sources.get('articles', []),
        config(
            args.mode,
            args.split_registry,
            args.max_calls,
            args.author_model,
            args.author_effort,
            concepts=args.concepts,
            seed=args.seed,
            max_units=args.max_units,
            task_types=args.task_types,
            max_context_chars=args.max_context_chars,
            max_reference_images=args.max_reference_images,
        ),
        through=args.through,
        visual_runs=sources.get('visuals', []),
        target_uri=args.target,
        write_mode=args.write_mode,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
