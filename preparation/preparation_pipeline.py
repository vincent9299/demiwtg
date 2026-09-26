"""Preparation：读采集表，按概念关联文档与图片，调用审核模型并写文章/视觉结果表。"""

from preparation.operaters.transforms import (
    material_status,
    selection_reason,
)

import lance
import pyarrow as pa
from functools import partial
from demiflow.lance.refs import DatasetRef
from demiflow.lance.registry import Catalog
from demiflow.lance.storage import schema_hash
from preparation.operaters.runfiles import run_relative, run_records
from project import resolve_root
from collect.material_schema import IMAGE_METADATA
import argparse
import json
from pathlib import Path
from preparation.operaters.results import EncodeStage, from_stage_row, PIPELINE_STAGE_ROWS
from preparation.operaters.runfiles import stage_ref, freeze_run, DEFAULT
from preparation.operaters.documents import FilterDocumentBlocks
from preparation.operaters.inputs import (
    SelectSourceRecords,
    DecodeMaterial,
    read_document,
    clean_document_record,
    check_image,
    merge_document,
    model_input,
)
from preparation.operaters.images import (
    SelectAvailableImages,
    SelectRelatedMaterials,
    IMAGE_FILTER_DEFAULTS,
    save_image_filter_policy,
    ReuseImageAnnotations,
)
from demiflow import data
from preparation.operaters.routing import (
    PrepareRoutingMaterials,
    RawPassageRows,
    EncodeImageTextMaterials,
    BuildRoutedJointRequest,
    EmbedParagraphBatch,
    RouteByTokenBudget,
)
from preparation.operaters.identity import PrepareIdentity, ApplyIdentity
from preparation.operaters.text import (
    BuildSourceBlocks,
    BatchSourceBlocks,
    ApplyBlockSelection,
    merge_block_decisions,
)
from preparation.prompts import material_prompt_pack, prompt_execution_options, save_prompt_config
from demiflow.execution.artifacts import run_lock, digest, read
from project import PROJECT_ROOT as ROOT
from preparation.operaters.article import (
    PrepareArticleInput,
    ApplyArticle,
    PrepareFinalReview,
    FinalizeArticle,
    ArticleTokenBudget,
    PrepareSelectionScope,
    EnsureConceptLabel,
)
from preparation.operaters.images import (
    RecordPrimaryImageSelection,
    PrepareImageReview,
    ApplyConfirmedImageSelection,
    BatchImageSelection,
    merge_image_decisions,
    FinalizeVisualMaterials,
    ExportVisualMetadata,
    PrepareVisualInput,
    visual_pack,
    merge_visual_packs,
    image_prompt_config,
    review_needed,
    image_review_service,
)

from preparation.operaters.runfiles import freeze_visual_run


def default_config(**overrides):
    return {
        **IMAGE_FILTER_DEFAULTS,
        'image_identity_definitions': {},
        'article_mode': True,
        'final_image_selection_only': True,
        'final_review_notes_required': True,
        'final_draft_outline_only': False,
        'joint_thinking': True,
        'joint_reasoning_effort': 'low',
        'final_review_thinking': True,
        'final_review_effort': 'medium',
        'final_max_output_tokens': 65536,
        'final_timeout_s': 1200,
        'final_input_tokens': 131072,
        'joint_input_tokens': 32768,
        'joint_image_target': 4,
        'text_mode': 'multimodal',
        'body_only': True,
        'max_calls': None,
        'max_output_tokens': 16384,
        'temperature': 0,
        'timeout_s': 900,
        'block_unit_chars': 1800,
        'block_batch_chars': 8000,
        'comparison_group_chars': 16000,
        'image_batch_size': 4,
        'text_embedding_model': str(ROOT.parent / 'models/Qwen3-Embedding-0.6B'),
        'image_embedding_model': str(ROOT.parent / 'models/siglip2-base-patch16-224'),
        **overrides,
    }


def run_pipeline(
    run,
    dataset,
    *,
    ids=None,
    sample_rate=1.0,
    seed=42,
    max_records_per_source=None,
    group_size=32,
    through='gather',
    model_config=None,
    project=ROOT,
    source_scope='collected',
    sources=None,
    target_uri=None,
    visual_target_uri=None,
    write_mode='merge',
    visual_write_mode='merge',
):
    """读取指定采集表，文章/图片分别配置目标和模式；merge 保留原有主键合并。"""
    if write_mode not in {'merge', 'append', 'overwrite'} or visual_write_mode not in {
        'merge',
        'append',
        'overwrite',
    }:
        raise ValueError('write_mode and visual_write_mode must be merge, append or overwrite')
    through = {'consolidate': 'final_review', 'fidelity': 'final_review', 'evidence': 'final_review'}.get(
        through, through
    )
    if through not in ['gather', 'identity', 'organize', 'extract', 'final_review', 'export']:
        raise ValueError('Unknown stopping stage')
    run, dataset = (Path(run), Path(dataset))
    if source_scope not in {'all', 'collected'}:
        raise ValueError('invalid source_scope')
    config = {**DEFAULT, **IMAGE_FILTER_DEFAULTS, **(model_config or {})}
    config.setdefault('image_annotations_ref', None)
    sources = sources or {}
    joint_concurrency = config.get('joint_concurrency', 1)
    if type(joint_concurrency) is not int or joint_concurrency < 1:
        raise ValueError('joint_concurrency must be a positive integer')
    final_review_concurrency = config.get('final_review_concurrency', 1)
    if type(final_review_concurrency) is not int or final_review_concurrency < 1:
        raise ValueError('final_review_concurrency must be a positive integer')
    global_audit = config.get('global_material_audit', False)
    settings = dict(
        pipeline_version='V2',
        visual_graph_sha256=digest(Path(__file__).with_name('preparation_pipeline.py').read_bytes()),
        ids=ids,
        sample_rate=sample_rate,
        seed=seed,
        max_records_per_source=max_records_per_source,
        group_size=group_size,
        model_config=config,
        source_scope=source_scope,
        target_uri=target_uri,
        visual_target_uri=visual_target_uri,
        write_mode=write_mode,
        visual_write_mode=visual_write_mode,
    )
    with run_lock(Path(run).parent / '_demiflow' / Path(run).name):
        from preparation.operaters.inputs import resolve_source, DecodeSourceRow

        legacy_concepts_ref, legacy_concepts_source = resolve_source(
            dataset, 'legacy_concepts', sources.get('concepts')
        )
        collected_documents_ref, collected_documents_source = resolve_source(
            dataset, 'legacy_docs', sources.get('documents')
        )
        collected_images_ref, collected_images_source = resolve_source(
            dataset, 'legacy_images', sources.get('images')
        )
        # 原始文档/图片按 concepts 列过滤；先限定读表范围，再解码来源字段，不读取 Blob 列。
        concept_filter = None
        if ids:
            concept_filter = ' OR '.join(
                "array_contains(concepts, '" + value.removeprefix('legacy:').replace("'", "''") + "')"
                for value in ids
                if value.startswith('legacy:')
            )
            concept_filter = '(' + concept_filter + ')' if concept_filter else 'false'
        active_sources = [legacy_concepts_source, collected_documents_source, collected_images_source]
        if source_scope == 'all':
            qid_concepts_ref, qid_concepts_source = resolve_source(
                dataset, 'qid_concepts', sources.get('documents')
            )
            wiki_ref, wiki_source = resolve_source(dataset, 'wiki_pages', sources.get('documents'))
            active_sources += [qid_concepts_source, wiki_source]
        version = freeze_run(run, dataset, active_sources, settings, run_pipeline, project)
        stage_name = 'read_legacy_concepts'
        relative = str(
            Path(run_relative(run)).parent / (stage_name + '__' + Path(run_relative(run)).name + '.lance')
        )
        table_uri = str(resolve_root() / relative)
        entry = run_records(run).get("stage/" + stage_name)
        if entry is not None and entry["fingerprint"] != version + ':' + 'read_legacy_concepts':
            raise ValueError("Stage inputs changed; use a new run: " + stage_name)
        if not Path(table_uri).exists():
            (
                data.read_lance(
                    legacy_concepts_ref.resolve(dataset),
                    version=legacy_concepts_ref.lance_version,
                    limit=max_records_per_source,
                )
                .map(DecodeSourceRow(legacy_concepts_source))
                .filter(
                    SelectSourceRecords('legacy_concepts', ids, sample_rate, seed, enabled=not global_audit)
                )
                .map(EncodeStage('read_legacy_concepts'))
            ).write_lance(table_uri, mode='overwrite', schema=PIPELINE_STAGE_ROWS)
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
            entry = {"fingerprint": version + ':' + 'read_legacy_concepts', "dataset_ref": ref.to_dict()}
            run_records(run).put("stage/" + stage_name, entry, immutable=False)
        elif entry is None:
            raise ValueError("Unfinished stage table; inspect it before retrying: " + table_uri)
        saved_rows = data.read_lance(table_uri, version=entry["dataset_ref"]["lance_version"])
        stage_name = 'input_legacy_concepts'
        relative = str(
            Path(run_relative(run)).parent / (stage_name + '__' + Path(run_relative(run)).name + '.lance')
        )
        table_uri = str(resolve_root() / relative)
        entry = run_records(run).get("stage/" + stage_name)
        if entry is not None and entry["fingerprint"] != version + ':' + 'input_legacy_concepts':
            raise ValueError("Stage inputs changed; use a new run: " + stage_name)
        if not Path(table_uri).exists():
            (
                saved_rows.map(from_stage_row)
                .filter(lambda r: r['error'] is None and isinstance(r['value'], dict))
                .map(DecodeMaterial(legacy_concepts_source))
                .map(EncodeStage('input_legacy_concepts'))
            ).write_lance(table_uri, mode='overwrite', schema=PIPELINE_STAGE_ROWS)
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
            entry = {"fingerprint": version + ':' + 'input_legacy_concepts', "dataset_ref": ref.to_dict()}
            run_records(run).put("stage/" + stage_name, entry, immutable=False)
        elif entry is None:
            raise ValueError("Unfinished stage table; inspect it before retrying: " + table_uri)
        saved_rows = data.read_lance(table_uri, version=entry["dataset_ref"]["lance_version"])
        legacy_concepts = saved_rows.map(from_stage_row)
        if source_scope == 'all':
            stage_name = 'read_qid_concepts'
            relative = str(
                Path(run_relative(run)).parent / (stage_name + '__' + Path(run_relative(run)).name + '.lance')
            )
            table_uri = str(resolve_root() / relative)
            entry = run_records(run).get("stage/" + stage_name)
            if entry is not None and entry["fingerprint"] != version + ':' + 'read_qid_concepts':
                raise ValueError("Stage inputs changed; use a new run: " + stage_name)
            if not Path(table_uri).exists():
                (
                    data.read_lance(
                        qid_concepts_ref.resolve(dataset),
                        version=qid_concepts_ref.lance_version,
                        columns=['qid', 'language', 'page_id', 'title'],
                        limit=max_records_per_source,
                        filter="document_type = 'wiki' AND qid IS NOT NULL AND page_id IS NOT NULL",
                    )
                    .map(DecodeSourceRow(qid_concepts_source))
                    .filter(
                        SelectSourceRecords('qid_concepts', ids, sample_rate, seed, enabled=not global_audit)
                    )
                    .map(EncodeStage('read_qid_concepts'))
                ).write_lance(table_uri, mode='overwrite', schema=PIPELINE_STAGE_ROWS)
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
                entry = {"fingerprint": version + ':' + 'read_qid_concepts', "dataset_ref": ref.to_dict()}
                run_records(run).put("stage/" + stage_name, entry, immutable=False)
            elif entry is None:
                raise ValueError("Unfinished stage table; inspect it before retrying: " + table_uri)
            saved_rows = data.read_lance(table_uri, version=entry["dataset_ref"]["lance_version"])
            stage_name = 'input_qid_concepts'
            relative = str(
                Path(run_relative(run)).parent / (stage_name + '__' + Path(run_relative(run)).name + '.lance')
            )
            table_uri = str(resolve_root() / relative)
            entry = run_records(run).get("stage/" + stage_name)
            if entry is not None and entry["fingerprint"] != version + ':' + 'input_qid_concepts':
                raise ValueError("Stage inputs changed; use a new run: " + stage_name)
            if not Path(table_uri).exists():
                (
                    saved_rows.map(from_stage_row)
                    .filter(lambda r: r['error'] is None and isinstance(r['value'], dict))
                    .map(DecodeMaterial(qid_concepts_source))
                    .map(EncodeStage('input_qid_concepts'))
                ).write_lance(table_uri, mode='overwrite', schema=PIPELINE_STAGE_ROWS)
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
                entry = {"fingerprint": version + ':' + 'input_qid_concepts', "dataset_ref": ref.to_dict()}
                run_records(run).put("stage/" + stage_name, entry, immutable=False)
            elif entry is None:
                raise ValueError("Unfinished stage table; inspect it before retrying: " + table_uri)
            saved_rows = data.read_lance(table_uri, version=entry["dataset_ref"]["lance_version"])
            qid_concepts = saved_rows.map(from_stage_row)
        else:
            qid_concepts = data.from_items([])
        stage_name = 'read_collected_documents'
        relative = str(
            Path(run_relative(run)).parent / (stage_name + '__' + Path(run_relative(run)).name + '.lance')
        )
        table_uri = str(resolve_root() / relative)
        entry = run_records(run).get("stage/" + stage_name)
        if entry is not None and entry["fingerprint"] != version + ':' + 'read_collected_documents':
            raise ValueError("Stage inputs changed; use a new run: " + stage_name)
        if not Path(table_uri).exists():
            (
                data.read_lance(
                    collected_documents_ref.resolve(dataset),
                    version=collected_documents_ref.lance_version,
                    columns=[f.name for f in collected_documents_ref.open(dataset).schema if f.name != 'data'],
                    limit=max_records_per_source,
                    filter="document_type = 'web'" + (" AND " + concept_filter if concept_filter else ""),
                )
                .map(DecodeSourceRow(collected_documents_source))
                .filter(SelectSourceRecords('legacy_docs', ids, sample_rate, seed, enabled=not global_audit))
                .map(EncodeStage('read_collected_documents'))
            ).write_lance(table_uri, mode='overwrite', schema=PIPELINE_STAGE_ROWS)
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
            entry = {"fingerprint": version + ':' + 'read_collected_documents', "dataset_ref": ref.to_dict()}
            run_records(run).put("stage/" + stage_name, entry, immutable=False)
        elif entry is None:
            raise ValueError("Unfinished stage table; inspect it before retrying: " + table_uri)
        saved_rows = data.read_lance(table_uri, version=entry["dataset_ref"]["lance_version"])
        stage_name = 'input_collected_documents'
        relative = str(
            Path(run_relative(run)).parent / (stage_name + '__' + Path(run_relative(run)).name + '.lance')
        )
        table_uri = str(resolve_root() / relative)
        entry = run_records(run).get("stage/" + stage_name)
        if entry is not None and entry["fingerprint"] != version + ':' + 'input_collected_documents':
            raise ValueError("Stage inputs changed; use a new run: " + stage_name)
        if not Path(table_uri).exists():
            (
                saved_rows.map(from_stage_row)
                .filter(lambda r: r['error'] is None and isinstance(r['value'], dict))
                .map(DecodeMaterial(collected_documents_source))
                .map(EncodeStage('input_collected_documents'))
            ).write_lance(table_uri, mode='overwrite', schema=PIPELINE_STAGE_ROWS)
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
            entry = {"fingerprint": version + ':' + 'input_collected_documents', "dataset_ref": ref.to_dict()}
            run_records(run).put("stage/" + stage_name, entry, immutable=False)
        elif entry is None:
            raise ValueError("Unfinished stage table; inspect it before retrying: " + table_uri)
        saved_rows = data.read_lance(table_uri, version=entry["dataset_ref"]["lance_version"])
        collected_documents = saved_rows.map(from_stage_row)
        stage_name = 'read_collected_images'
        relative = str(
            Path(run_relative(run)).parent / (stage_name + '__' + Path(run_relative(run)).name + '.lance')
        )
        table_uri = str(resolve_root() / relative)
        entry = run_records(run).get("stage/" + stage_name)
        if entry is not None and entry["fingerprint"] != version + ':' + 'read_collected_images':
            raise ValueError("Stage inputs changed; use a new run: " + stage_name)
        if not Path(table_uri).exists():
            (
                data.read_lance(
                    collected_images_ref.resolve(dataset),
                    version=collected_images_ref.lance_version,
                    columns=['sha256', 'ext', 'byte_size', 'storage_mode', *IMAGE_METADATA.names],
                    limit=max_records_per_source,
                    filter=concept_filter,
                )
                .map(DecodeSourceRow(collected_images_source))
                .filter(SelectSourceRecords('legacy_images', ids, sample_rate, seed, enabled=not global_audit))
                .map(EncodeStage('read_collected_images'))
            ).write_lance(table_uri, mode='overwrite', schema=PIPELINE_STAGE_ROWS)
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
            entry = {"fingerprint": version + ':' + 'read_collected_images', "dataset_ref": ref.to_dict()}
            run_records(run).put("stage/" + stage_name, entry, immutable=False)
        elif entry is None:
            raise ValueError("Unfinished stage table; inspect it before retrying: " + table_uri)
        saved_rows = data.read_lance(table_uri, version=entry["dataset_ref"]["lance_version"])
        stage_name = 'input_collected_images'
        relative = str(
            Path(run_relative(run)).parent / (stage_name + '__' + Path(run_relative(run)).name + '.lance')
        )
        table_uri = str(resolve_root() / relative)
        entry = run_records(run).get("stage/" + stage_name)
        if entry is not None and entry["fingerprint"] != version + ':' + 'input_collected_images':
            raise ValueError("Stage inputs changed; use a new run: " + stage_name)
        if not Path(table_uri).exists():
            (
                saved_rows.map(from_stage_row)
                .filter(lambda r: r['error'] is None and isinstance(r['value'], dict))
                .map(DecodeMaterial())
                .map(EncodeStage('input_collected_images'))
            ).write_lance(table_uri, mode='overwrite', schema=PIPELINE_STAGE_ROWS)
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
            entry = {"fingerprint": version + ':' + 'input_collected_images', "dataset_ref": ref.to_dict()}
            run_records(run).put("stage/" + stage_name, entry, immutable=False)
        elif entry is None:
            raise ValueError("Unfinished stage table; inspect it before retrying: " + table_uri)
        saved_rows = data.read_lance(table_uri, version=entry["dataset_ref"]["lance_version"])
        collected_images = saved_rows.map(from_stage_row)
        if source_scope == 'all':
            stage_name = 'read_wiki_pages'
            relative = str(
                Path(run_relative(run)).parent / (stage_name + '__' + Path(run_relative(run)).name + '.lance')
            )
            table_uri = str(resolve_root() / relative)
            entry = run_records(run).get("stage/" + stage_name)
            if entry is not None and entry["fingerprint"] != version + ':' + 'read_wiki_pages':
                raise ValueError("Stage inputs changed; use a new run: " + stage_name)
            if not Path(table_uri).exists():
                (
                    data.read_lance(
                        wiki_ref.resolve(dataset),
                        version=wiki_ref.lance_version,
                        columns=[f.name for f in wiki_ref.open(dataset).schema if f.name != 'data'],
                        limit=max_records_per_source,
                        filter="document_type = 'wiki'",
                    )
                    .map(DecodeSourceRow(wiki_source))
                    .map(EncodeStage('read_wiki_pages'))
                ).write_lance(table_uri, mode='overwrite', schema=PIPELINE_STAGE_ROWS)
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
                entry = {"fingerprint": version + ':' + 'read_wiki_pages', "dataset_ref": ref.to_dict()}
                run_records(run).put("stage/" + stage_name, entry, immutable=False)
            elif entry is None:
                raise ValueError("Unfinished stage table; inspect it before retrying: " + table_uri)
            saved_rows = data.read_lance(table_uri, version=entry["dataset_ref"]["lance_version"])
            stage_name = 'input_wiki_pages'
            relative = str(
                Path(run_relative(run)).parent / (stage_name + '__' + Path(run_relative(run)).name + '.lance')
            )
            table_uri = str(resolve_root() / relative)
            entry = run_records(run).get("stage/" + stage_name)
            if entry is not None and entry["fingerprint"] != version + ':' + 'input_wiki_pages':
                raise ValueError("Stage inputs changed; use a new run: " + stage_name)
            if not Path(table_uri).exists():
                (
                    saved_rows.map(from_stage_row)
                    .filter(lambda r: r['error'] is None and isinstance(r['value'], dict))
                    .map(DecodeMaterial())
                    .map(EncodeStage('input_wiki_pages'))
                ).write_lance(table_uri, mode='overwrite', schema=PIPELINE_STAGE_ROWS)
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
                entry = {"fingerprint": version + ':' + 'input_wiki_pages', "dataset_ref": ref.to_dict()}
                run_records(run).put("stage/" + stage_name, entry, immutable=False)
            elif entry is None:
                raise ValueError("Unfinished stage table; inspect it before retrying: " + table_uri)
            saved_rows = data.read_lance(table_uri, version=entry["dataset_ref"]["lance_version"])
            wiki_pages = saved_rows.map(from_stage_row)
        else:
            wiki_pages = data.from_items([])
        stage_name = 'concepts'
        relative = str(
            Path(run_relative(run)).parent / (stage_name + '__' + Path(run_relative(run)).name + '.lance')
        )
        table_uri = str(resolve_root() / relative)
        entry = run_records(run).get("stage/" + stage_name)
        if entry is not None and entry["fingerprint"] != version + ':' + 'concepts':
            raise ValueError("Stage inputs changed; use a new run: " + stage_name)
        if not Path(table_uri).exists():
            (
                legacy_concepts.union(qid_concepts)
                .reduce_by_key(
                    'concept_ref',
                    lambda acc, row: (
                        {
                            **acc,
                            'source_records': acc['source_records'] + row['source_records'],
                            'page_refs': acc['page_refs'] + row['page_refs'],
                        }
                        if acc
                        else row
                    ),
                )
                .map(EncodeStage('concepts'))
            ).write_lance(table_uri, mode='overwrite', schema=PIPELINE_STAGE_ROWS)
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
            entry = {"fingerprint": version + ':' + 'concepts', "dataset_ref": ref.to_dict()}
            run_records(run).put("stage/" + stage_name, entry, immutable=False)
        elif entry is None:
            raise ValueError("Unfinished stage table; inspect it before retrying: " + table_uri)
        saved_rows = data.read_lance(table_uri, version=entry["dataset_ref"]["lance_version"])
        concepts = saved_rows.map(from_stage_row)
        stage_name = 'images'
        relative = str(
            Path(run_relative(run)).parent / (stage_name + '__' + Path(run_relative(run)).name + '.lance')
        )
        table_uri = str(resolve_root() / relative)
        entry = run_records(run).get("stage/" + stage_name)
        if entry is not None and entry["fingerprint"] != version + ':' + 'images':
            raise ValueError("Stage inputs changed; use a new run: " + stage_name)
        if not Path(table_uri).exists():
            collected_images.map(EncodeStage('images')).write_lance(
                table_uri, mode='overwrite', schema=PIPELINE_STAGE_ROWS
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
            entry = {"fingerprint": version + ':' + 'images', "dataset_ref": ref.to_dict()}
            run_records(run).put("stage/" + stage_name, entry, immutable=False)
        elif entry is None:
            raise ValueError("Unfinished stage table; inspect it before retrying: " + table_uri)
        saved_rows = data.read_lance(table_uri, version=entry["dataset_ref"]["lance_version"])
        images = saved_rows.map(from_stage_row)
        stage_name = 'page_refs'
        relative = str(
            Path(run_relative(run)).parent / (stage_name + '__' + Path(run_relative(run)).name + '.lance')
        )
        table_uri = str(resolve_root() / relative)
        entry = run_records(run).get("stage/" + stage_name)
        if entry is not None and entry["fingerprint"] != version + ':' + 'page_refs':
            raise ValueError("Stage inputs changed; use a new run: " + stage_name)
        if not Path(table_uri).exists():
            (
                concepts.flat_map(lambda c: c['page_refs'])
                .reduce_by_key(['lang', 'page_id', 'mapped_concept_ref'], lambda acc, row: row)
                .map(EncodeStage('page_refs'))
            ).write_lance(table_uri, mode='overwrite', schema=PIPELINE_STAGE_ROWS)
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
            entry = {"fingerprint": version + ':' + 'page_refs', "dataset_ref": ref.to_dict()}
            run_records(run).put("stage/" + stage_name, entry, immutable=False)
        elif entry is None:
            raise ValueError("Unfinished stage table; inspect it before retrying: " + table_uri)
        saved_rows = data.read_lance(table_uri, version=entry["dataset_ref"]["lance_version"])
        page_refs = saved_rows.map(from_stage_row)
        wiki_documents = wiki_pages.join(page_refs, on=['lang', 'page_id'], how='left').reduce_by_key(
            'doc_id', merge_document
        )
        stage_name = 'documents'
        relative = str(
            Path(run_relative(run)).parent / (stage_name + '__' + Path(run_relative(run)).name + '.lance')
        )
        table_uri = str(resolve_root() / relative)
        entry = run_records(run).get("stage/" + stage_name)
        if entry is not None and entry["fingerprint"] != version + ':' + 'documents':
            raise ValueError("Stage inputs changed; use a new run: " + stage_name)
        if not Path(table_uri).exists():
            collected_documents.union(wiki_documents).map(EncodeStage('documents')).write_lance(
                table_uri, mode='overwrite', schema=PIPELINE_STAGE_ROWS
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
            entry = {"fingerprint": version + ':' + 'documents', "dataset_ref": ref.to_dict()}
            run_records(run).put("stage/" + stage_name, entry, immutable=False)
        elif entry is None:
            raise ValueError("Unfinished stage table; inspect it before retrying: " + table_uri)
        saved_rows = data.read_lance(table_uri, version=entry["dataset_ref"]["lance_version"])
        documents = saved_rows.map(from_stage_row)
        stage_name = 'concepts_selected'
        relative = str(
            Path(run_relative(run)).parent / (stage_name + '__' + Path(run_relative(run)).name + '.lance')
        )
        table_uri = str(resolve_root() / relative)
        entry = run_records(run).get("stage/" + stage_name)
        if entry is not None and entry["fingerprint"] != version + ':' + 'concepts_selected':
            raise ValueError("Stage inputs changed; use a new run: " + stage_name)
        if not Path(table_uri).exists():
            (
                concepts.map(partial(selection_reason, ids=ids, sample_rate=sample_rate, seed=seed))
                .map(lambda row: {**row, 'selected': row['selection_reason'] == 'selected'})
                .map(EncodeStage('concepts_selected'))
            ).write_lance(table_uri, mode='overwrite', schema=PIPELINE_STAGE_ROWS)
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
            entry = {"fingerprint": version + ':' + 'concepts_selected', "dataset_ref": ref.to_dict()}
            run_records(run).put("stage/" + stage_name, entry, immutable=False)
        elif entry is None:
            raise ValueError("Unfinished stage table; inspect it before retrying: " + table_uri)
        saved_rows = data.read_lance(table_uri, version=entry["dataset_ref"]["lance_version"])
        selected_concepts = saved_rows.map(from_stage_row).filter(lambda c: c['selected'])
        selected_keys = selected_concepts.select_columns(['concept_ref'])
        if ids is not None:
            stage_name = 'missing_concepts'
            relative = str(
                Path(run_relative(run)).parent / (stage_name + '__' + Path(run_relative(run)).name + '.lance')
            )
            table_uri = str(resolve_root() / relative)
            entry = run_records(run).get("stage/" + stage_name)
            if entry is not None and entry["fingerprint"] != version + ':' + 'missing_concepts':
                raise ValueError("Stage inputs changed; use a new run: " + stage_name)
            if not Path(table_uri).exists():
                (
                    data.from_items([{'concept_ref': ref} for ref in ids])
                    .join(concepts.select_columns(['concept_ref']), on='concept_ref', how='anti')
                    .map(EncodeStage('missing_concepts'))
                ).write_lance(table_uri, mode='overwrite', schema=PIPELINE_STAGE_ROWS)
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
                entry = {"fingerprint": version + ':' + 'missing_concepts', "dataset_ref": ref.to_dict()}
                run_records(run).put("stage/" + stage_name, entry, immutable=False)
            elif entry is None:
                raise ValueError("Unfinished stage table; inspect it before retrying: " + table_uri)
            saved_rows = data.read_lance(table_uri, version=entry["dataset_ref"]["lance_version"])
            saved_rows.map(from_stage_row)
        # 将每份材料的 concept_refs 展成关联边；排除歧义映射，随后按材料 ID 关联内容。
        all_document_links = documents.flat_map(
            lambda row: [
                {'concept_ref': ref, 'doc_id': row['doc_id']}
                for ref in row['concept_refs']
                if row.get('association_status') != 'ambiguous_mapping'
            ]
        )
        all_image_links = images.flat_map(
            lambda row: [
                {'concept_ref': ref, 'image_id': row['image_id']}
                for ref in row['concept_refs']
                if row.get('association_status') != 'ambiguous_mapping'
            ]
        )
        stage_name = 'documents_links'
        relative = str(
            Path(run_relative(run)).parent / (stage_name + '__' + Path(run_relative(run)).name + '.lance')
        )
        table_uri = str(resolve_root() / relative)
        entry = run_records(run).get("stage/" + stage_name)
        if entry is not None and entry["fingerprint"] != version + ':' + 'documents_links':
            raise ValueError("Stage inputs changed; use a new run: " + stage_name)
        if not Path(table_uri).exists():
            (
                all_document_links.join(selected_keys, on='concept_ref', how='semi').map(
                    EncodeStage('documents_links')
                )
            ).write_lance(table_uri, mode='overwrite', schema=PIPELINE_STAGE_ROWS)
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
            entry = {"fingerprint": version + ':' + 'documents_links', "dataset_ref": ref.to_dict()}
            run_records(run).put("stage/" + stage_name, entry, immutable=False)
        elif entry is None:
            raise ValueError("Unfinished stage table; inspect it before retrying: " + table_uri)
        saved_rows = data.read_lance(table_uri, version=entry["dataset_ref"]["lance_version"])
        document_links = saved_rows.map(from_stage_row)
        stage_name = 'images_links'
        relative = str(
            Path(run_relative(run)).parent / (stage_name + '__' + Path(run_relative(run)).name + '.lance')
        )
        table_uri = str(resolve_root() / relative)
        entry = run_records(run).get("stage/" + stage_name)
        if entry is not None and entry["fingerprint"] != version + ':' + 'images_links':
            raise ValueError("Stage inputs changed; use a new run: " + stage_name)
        if not Path(table_uri).exists():
            (
                all_image_links.join(selected_keys, on='concept_ref', how='semi').map(
                    EncodeStage('images_links')
                )
            ).write_lance(table_uri, mode='overwrite', schema=PIPELINE_STAGE_ROWS)
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
            entry = {"fingerprint": version + ':' + 'images_links', "dataset_ref": ref.to_dict()}
            run_records(run).put("stage/" + stage_name, entry, immutable=False)
        elif entry is None:
            raise ValueError("Unfinished stage table; inspect it before retrying: " + table_uri)
        saved_rows = data.read_lance(table_uri, version=entry["dataset_ref"]["lance_version"])
        image_links = saved_rows.map(from_stage_row)
        stage_name = 'documents_selected'
        relative = str(
            Path(run_relative(run)).parent / (stage_name + '__' + Path(run_relative(run)).name + '.lance')
        )
        table_uri = str(resolve_root() / relative)
        entry = run_records(run).get("stage/" + stage_name)
        if entry is not None and entry["fingerprint"] != version + ':' + 'documents_selected':
            raise ValueError("Stage inputs changed; use a new run: " + stage_name)
        if not Path(table_uri).exists():
            (
                documents.join(
                    document_links.select_columns(['doc_id']).reduce_by_key('doc_id', lambda acc, row: row),
                    on='doc_id',
                    how='semi',
                ).map(EncodeStage('documents_selected'))
            ).write_lance(table_uri, mode='overwrite', schema=PIPELINE_STAGE_ROWS)
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
            entry = {"fingerprint": version + ':' + 'documents_selected', "dataset_ref": ref.to_dict()}
            run_records(run).put("stage/" + stage_name, entry, immutable=False)
        elif entry is None:
            raise ValueError("Unfinished stage table; inspect it before retrying: " + table_uri)
        saved_rows = data.read_lance(table_uri, version=entry["dataset_ref"]["lance_version"])
        selected_documents = saved_rows.map(from_stage_row)
        stage_name = 'images_selected'
        relative = str(
            Path(run_relative(run)).parent / (stage_name + '__' + Path(run_relative(run)).name + '.lance')
        )
        table_uri = str(resolve_root() / relative)
        entry = run_records(run).get("stage/" + stage_name)
        if entry is not None and entry["fingerprint"] != version + ':' + 'images_selected':
            raise ValueError("Stage inputs changed; use a new run: " + stage_name)
        if not Path(table_uri).exists():
            (
                images.join(
                    image_links.select_columns(['image_id']).reduce_by_key('image_id', lambda acc, row: row),
                    on='image_id',
                    how='semi',
                ).map(EncodeStage('images_selected'))
            ).write_lance(table_uri, mode='overwrite', schema=PIPELINE_STAGE_ROWS)
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
            entry = {"fingerprint": version + ':' + 'images_selected', "dataset_ref": ref.to_dict()}
            run_records(run).put("stage/" + stage_name, entry, immutable=False)
        elif entry is None:
            raise ValueError("Unfinished stage table; inspect it before retrying: " + table_uri)
        saved_rows = data.read_lance(table_uri, version=entry["dataset_ref"]["lance_version"])
        selected_images = saved_rows.map(from_stage_row)
        if global_audit:
            for objects, links, key, name in [
                (documents, all_document_links, 'doc_id', 'documents'),
                (images, all_image_links, 'image_id', 'images'),
            ]:
                associated = (
                    links.join(concepts.select_columns(['concept_ref']), on='concept_ref', how='semi')
                    .select_columns([key])
                    .reduce_by_key(key, lambda acc, row: row)
                )
                stage_name = f'{name}_unmatched'
                relative = str(
                    Path(run_relative(run)).parent
                    / (stage_name + '__' + Path(run_relative(run)).name + '.lance')
                )
                table_uri = str(resolve_root() / relative)
                entry = run_records(run).get("stage/" + stage_name)
                if entry is not None and entry["fingerprint"] != version + ':' + f'{name}_unmatched':
                    raise ValueError("Stage inputs changed; use a new run: " + stage_name)
                if not Path(table_uri).exists():
                    (
                        objects.join(associated, on=key, how='anti').map(EncodeStage(f'{name}_unmatched'))
                    ).write_lance(table_uri, mode='overwrite', schema=PIPELINE_STAGE_ROWS)
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
                    entry = {"fingerprint": version + ':' + f'{name}_unmatched', "dataset_ref": ref.to_dict()}
                    run_records(run).put("stage/" + stage_name, entry, immutable=False)
                elif entry is None:
                    raise ValueError("Unfinished stage table; inspect it before retrying: " + table_uri)
                saved_rows = data.read_lance(table_uri, version=entry["dataset_ref"]["lance_version"])
                saved_rows.map(from_stage_row)
        stage_name = 'documents_processed'
        relative = str(
            Path(run_relative(run)).parent / (stage_name + '__' + Path(run_relative(run)).name + '.lance')
        )
        table_uri = str(resolve_root() / relative)
        entry = run_records(run).get("stage/" + stage_name)
        if entry is not None and entry["fingerprint"] != version + ':' + 'documents_processed':
            raise ValueError("Stage inputs changed; use a new run: " + stage_name)
        if not Path(table_uri).exists():
            (
                selected_documents.map_async(read_document, concurrency=1, queue_depth=4)
                .map_async(clean_document_record, concurrency=1, queue_depth=4)
                .map(FilterDocumentBlocks())
                .map(EncodeStage('documents_processed'))
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
            entry = {"fingerprint": version + ':' + 'documents_processed', "dataset_ref": ref.to_dict()}
            run_records(run).put("stage/" + stage_name, entry, immutable=False)
        elif entry is None:
            raise ValueError("Unfinished stage table; inspect it before retrying: " + table_uri)
        saved_rows = data.read_lance(table_uri, version=entry["dataset_ref"]["lance_version"])
        processed_documents = saved_rows.map(from_stage_row)
        stage_name = 'images_processed'
        relative = str(
            Path(run_relative(run)).parent / (stage_name + '__' + Path(run_relative(run)).name + '.lance')
        )
        table_uri = str(resolve_root() / relative)
        entry = run_records(run).get("stage/" + stage_name)
        if entry is not None and entry["fingerprint"] != version + ':' + 'images_processed':
            raise ValueError("Stage inputs changed; use a new run: " + stage_name)
        if not Path(table_uri).exists():
            (
                selected_images.map_async(check_image, concurrency=1, queue_depth=4)
                .map(EncodeStage('images_processed'))
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
            entry = {"fingerprint": version + ':' + 'images_processed', "dataset_ref": ref.to_dict()}
            run_records(run).put("stage/" + stage_name, entry, immutable=False)
        elif entry is None:
            raise ValueError("Unfinished stage table; inspect it before retrying: " + table_uri)
        saved_rows = data.read_lance(table_uri, version=entry["dataset_ref"]["lance_version"])
        stage_name = 'images_indexed'
        relative = str(
            Path(run_relative(run)).parent / (stage_name + '__' + Path(run_relative(run)).name + '.lance')
        )
        table_uri = str(resolve_root() / relative)
        entry = run_records(run).get("stage/" + stage_name)
        if entry is not None and entry["fingerprint"] != version + ':' + 'images_indexed':
            raise ValueError("Stage inputs changed; use a new run: " + stage_name)
        if not Path(table_uri).exists():
            (
                saved_rows.map(from_stage_row)
                .map(
                    ReuseImageAnnotations(
                        config.get('image_annotations_ref'), config.get('image_annotation_config_id')
                    )
                )
                .map(EncodeStage('images_indexed'))
            ).write_lance(table_uri, mode='overwrite', schema=PIPELINE_STAGE_ROWS)
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
            entry = {"fingerprint": version + ':' + 'images_indexed', "dataset_ref": ref.to_dict()}
            run_records(run).put("stage/" + stage_name, entry, immutable=False)
        elif entry is None:
            raise ValueError("Unfinished stage table; inspect it before retrying: " + table_uri)
        saved_rows = data.read_lance(table_uri, version=entry["dataset_ref"]["lance_version"])
        processed_images = saved_rows.map(from_stage_row)
        document_counts = document_links.join(
            processed_documents.select_columns(['doc_id', 'read_status']), on='doc_id'
        ).reduce_by_key(
            'concept_ref',
            lambda acc, row: {
                'concept_ref': row['concept_ref'],
                'document_count': (acc['document_count'] if acc else 0) + 1,
                'readable_documents': (acc['readable_documents'] if acc else 0)
                + int(row['read_status'] in {'readable', 'verified_bytes'}),
            },
        )
        image_counts = image_links.join(
            processed_images.select_columns(['image_id', 'byte_status']), on='image_id'
        ).reduce_by_key(
            'concept_ref',
            lambda acc, row: {
                'concept_ref': row['concept_ref'],
                'image_count': (acc['image_count'] if acc else 0) + 1,
                'verified_images': (acc['verified_images'] if acc else 0)
                + int(row['byte_status'] in {'readable', 'verified_bytes'}),
            },
        )
        stage_name = 'concepts_ready'
        relative = str(
            Path(run_relative(run)).parent / (stage_name + '__' + Path(run_relative(run)).name + '.lance')
        )
        table_uri = str(resolve_root() / relative)
        entry = run_records(run).get("stage/" + stage_name)
        if entry is not None and entry["fingerprint"] != version + ':' + 'concepts_ready':
            raise ValueError("Stage inputs changed; use a new run: " + stage_name)
        if not Path(table_uri).exists():
            (
                selected_concepts.join(document_counts, on='concept_ref', how='left')
                .join(image_counts, on='concept_ref', how='left')
                .map(material_status)
                .map(EncodeStage('concepts_ready'))
            ).write_lance(table_uri, mode='overwrite', schema=PIPELINE_STAGE_ROWS)
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
            entry = {"fingerprint": version + ':' + 'concepts_ready', "dataset_ref": ref.to_dict()}
            run_records(run).put("stage/" + stage_name, entry, immutable=False)
        elif entry is None:
            raise ValueError("Unfinished stage table; inspect it before retrying: " + table_uri)
        saved_rows = data.read_lance(table_uri, version=entry["dataset_ref"]["lance_version"])
        concept_images = image_links.join(
            processed_images.map(
                lambda row: {'image_id': row['image_id'], 'material_type': 'images', 'material': row}
            ),
            on='image_id',
        )
        material_batches = (
            document_links.join(
                processed_documents.map(
                    lambda row: {'doc_id': row['doc_id'], 'material_type': 'documents', 'material': row}
                ),
                on='doc_id',
            )
            .union(concept_images)
            .group_batches('concept_ref', max_rows=group_size, output='materials')
        )
        stage_name = 'knowledge_inputs'
        relative = str(
            Path(run_relative(run)).parent / (stage_name + '__' + Path(run_relative(run)).name + '.lance')
        )
        table_uri = str(resolve_root() / relative)
        entry = run_records(run).get("stage/" + stage_name)
        if entry is not None and entry["fingerprint"] != version + ':' + 'knowledge_inputs':
            raise ValueError("Stage inputs changed; use a new run: " + stage_name)
        if not Path(table_uri).exists():
            (
                saved_rows.map(from_stage_row)
                .join(material_batches, on='concept_ref', how='left')
                .map(EncodeStage('knowledge_inputs'))
            ).write_lance(table_uri, mode='overwrite', schema=PIPELINE_STAGE_ROWS)
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
            entry = {"fingerprint": version + ':' + 'knowledge_inputs', "dataset_ref": ref.to_dict()}
            run_records(run).put("stage/" + stage_name, entry, immutable=False)
        elif entry is None:
            raise ValueError("Unfinished stage table; inspect it before retrying: " + table_uri)
        saved_rows = data.read_lance(table_uri, version=entry["dataset_ref"]["lance_version"])
        batches = saved_rows.map(from_stage_row)
        if through == 'gather':
            return batches
        knowledge_run = run / 'knowledge'
        pack, prompt_text = material_prompt_pack(config)
        options = prompt_execution_options(run, config)
        save_prompt_config(run, prompt_text, options)
        batches = data.read_lance(
            str(
                resolve_root()
                / Path(run_relative(run)).parent
                / ('knowledge_inputs__' + Path(run_relative(run)).name + '.lance')
            ),
            version=stage_ref(run, 'knowledge_inputs').lance_version,
        ).map(from_stage_row)
        stage_name = 'knowledge_identity'
        relative = str(
            Path(run_relative(run)).parent / (stage_name + '__' + Path(run_relative(run)).name + '.lance')
        )
        table_uri = str(resolve_root() / relative)
        entry = run_records(run).get("stage/" + stage_name)
        if entry is not None and entry["fingerprint"] != version + ':' + 'knowledge_identity':
            raise ValueError("Stage inputs changed; use a new run: " + stage_name)
        if not Path(table_uri).exists():
            (
                batches.map(model_input)
                .map_async(PrepareIdentity(knowledge_run, config))
                .map_prompt_async(
                    'identity',
                    config=pack,
                    options=options,
                    max_requests=config['max_calls'],
                    inputs={'payload': 'identity_prompt'},
                    output='prompt_result',
                    call_output='prompt_call',
                    error_output='prompt_error',
                    when=lambda r: not r.get('blocked') and 'identity_prompt' in r,
                    concurrency=1,
                    queue_depth=1,
                )
                .map_async(ApplyIdentity(knowledge_run, config))
                .map(EncodeStage('knowledge_identity'))
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
            entry = {"fingerprint": version + ':' + 'knowledge_identity', "dataset_ref": ref.to_dict()}
            run_records(run).put("stage/" + stage_name, entry, immutable=False)
        elif entry is None:
            raise ValueError("Unfinished stage table; inspect it before retrying: " + table_uri)
        saved_rows = data.read_lance(table_uri, version=entry["dataset_ref"]["lance_version"])
        identified = saved_rows.map(from_stage_row)
        if through == 'identity':
            return identified
        if config.get('text_mode') == 'multimodal':
            stage_name = 'multimodal_materials'
            relative = str(
                Path(run_relative(run)).parent / (stage_name + '__' + Path(run_relative(run)).name + '.lance')
            )
            table_uri = str(resolve_root() / relative)
            entry = run_records(run).get("stage/" + stage_name)
            if entry is not None and entry["fingerprint"] != version + ':' + 'multimodal_materials':
                raise ValueError("Stage inputs changed; use a new run: " + stage_name)
            if not Path(table_uri).exists():
                (
                    identified.map(EnsureConceptLabel())
                    .map(BuildSourceBlocks(config.get('block_unit_chars', 1800), body_only=True))
                    .map(SelectAvailableImages())
                    .map(EncodeStage('multimodal_materials'))
                ).write_lance(table_uri, mode='overwrite', schema=PIPELINE_STAGE_ROWS)
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
                entry = {"fingerprint": version + ':' + 'multimodal_materials', "dataset_ref": ref.to_dict()}
                run_records(run).put("stage/" + stage_name, entry, immutable=False)
            elif entry is None:
                raise ValueError("Unfinished stage table; inspect it before retrying: " + table_uri)
            saved_rows = data.read_lance(table_uri, version=entry["dataset_ref"]["lance_version"])
            blocks = saved_rows.map(from_stage_row)
            text_requests = (
                blocks.flat_map(BatchSourceBlocks(config.get('block_batch_chars', 8000)))
                .map(PrepareSelectionScope(config['image_identity_definitions']))
                .materialize()
            )
            stage_name = 'text_relevance'
            relative = str(
                Path(run_relative(run)).parent / (stage_name + '__' + Path(run_relative(run)).name + '.lance')
            )
            table_uri = str(resolve_root() / relative)
            entry = run_records(run).get("stage/" + stage_name)
            if entry is not None and entry["fingerprint"] != version + ':' + 'text_relevance':
                raise ValueError("Stage inputs changed; use a new run: " + stage_name)
            if not Path(table_uri).exists():
                (
                    text_requests.map_prompt_async(
                        'select_blocks',
                        config=pack,
                        options=options,
                        max_requests=config['max_calls'],
                        inputs={'payload': 'block_prompt'},
                        output='prompt_result',
                        call_output='prompt_call',
                        error_output='prompt_error',
                        concurrency=1,
                        queue_depth=1,
                    )
                    .map(ApplyBlockSelection(relevance_only=True))
                    .map(EncodeStage('text_relevance'))
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
                entry = {"fingerprint": version + ':' + 'text_relevance', "dataset_ref": ref.to_dict()}
                run_records(run).put("stage/" + stage_name, entry, immutable=False)
            elif entry is None:
                raise ValueError("Unfinished stage table; inspect it before retrying: " + table_uri)
            saved_rows = data.read_lance(table_uri, version=entry["dataset_ref"]["lance_version"])
            text_decisions = saved_rows.map(from_stage_row).reduce_by_key('case_id', merge_block_decisions)
            image_decisions = run_image_review(blocks, run, config, version)
            stage_name = 'related_materials'
            relative = str(
                Path(run_relative(run)).parent / (stage_name + '__' + Path(run_relative(run)).name + '.lance')
            )
            table_uri = str(resolve_root() / relative)
            entry = run_records(run).get("stage/" + stage_name)
            if entry is not None and entry["fingerprint"] != version + ':' + 'related_materials':
                raise ValueError("Stage inputs changed; use a new run: " + stage_name)
            if not Path(table_uri).exists():
                (
                    blocks.join(text_decisions, on='case_id', how='left')
                    .join(image_decisions, on='case_id', how='left')
                    .map(SelectRelatedMaterials())
                    .map(EncodeStage('related_materials'))
                ).write_lance(table_uri, mode='overwrite', schema=PIPELINE_STAGE_ROWS)
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
                entry = {"fingerprint": version + ':' + 'related_materials', "dataset_ref": ref.to_dict()}
                run_records(run).put("stage/" + stage_name, entry, immutable=False)
            elif entry is None:
                raise ValueError("Unfinished stage table; inspect it before retrying: " + table_uri)
            saved_rows = data.read_lance(table_uri, version=entry["dataset_ref"]["lance_version"])
            related = saved_rows.map(from_stage_row)
            save_image_filter_policy(run, config)
            related = finalize_visual_branch(
                related, run, version, target_uri=visual_target_uri, write_mode=visual_write_mode
            )
            if through == 'organize':
                return related
            stage_name = 'routing_materials'
            relative = str(
                Path(run_relative(run)).parent / (stage_name + '__' + Path(run_relative(run)).name + '.lance')
            )
            table_uri = str(resolve_root() / relative)
            entry = run_records(run).get("stage/" + stage_name)
            if entry is not None and entry["fingerprint"] != version + ':' + 'routing_materials':
                raise ValueError("Stage inputs changed; use a new run: " + stage_name)
            if not Path(table_uri).exists():
                related.map(PrepareRoutingMaterials()).map(EncodeStage('routing_materials')).write_lance(
                    table_uri, mode='overwrite', schema=PIPELINE_STAGE_ROWS
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
                entry = {"fingerprint": version + ':' + 'routing_materials', "dataset_ref": ref.to_dict()}
                run_records(run).put("stage/" + stage_name, entry, immutable=False)
            elif entry is None:
                raise ValueError("Unfinished stage table; inspect it before retrying: " + table_uri)
            saved_rows = data.read_lance(table_uri, version=entry["dataset_ref"]["lance_version"])
            routing_materials = saved_rows.map(from_stage_row)
            stage_name = 'material_text_embeddings'
            relative = str(
                Path(run_relative(run)).parent / (stage_name + '__' + Path(run_relative(run)).name + '.lance')
            )
            table_uri = str(resolve_root() / relative)
            entry = run_records(run).get("stage/" + stage_name)
            if entry is not None and entry["fingerprint"] != version + ':' + 'material_text_embeddings':
                raise ValueError("Stage inputs changed; use a new run: " + stage_name)
            if not Path(table_uri).exists():
                (
                    routing_materials.flat_map(RawPassageRows())
                    .group_batches('embedding_bucket', max_rows=2, output='items')
                    .map(EmbedParagraphBatch(config['text_embedding_model']))
                    .map(EncodeStage('material_text_embeddings'))
                ).write_lance(table_uri, mode='overwrite', schema=PIPELINE_STAGE_ROWS)
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
                entry = {
                    "fingerprint": version + ':' + 'material_text_embeddings',
                    "dataset_ref": ref.to_dict(),
                }
                run_records(run).put("stage/" + stage_name, entry, immutable=False)
            elif entry is None:
                raise ValueError("Unfinished stage table; inspect it before retrying: " + table_uri)
            saved_rows = data.read_lance(table_uri, version=entry["dataset_ref"]["lance_version"])
            text_embeddings = (
                saved_rows.map(from_stage_row)
                .flat_map(lambda r: r['items'])
                .reduce_by_key(
                    'case_id',
                    lambda acc, r: {
                        'case_id': r['case_id'],
                        'passage_embeddings': {**acc['passage_embeddings'], r['source_id']: r},
                    },
                    initial={'passage_embeddings': {}},
                )
            )
            stage_name = 'material_image_embeddings'
            relative = str(
                Path(run_relative(run)).parent / (stage_name + '__' + Path(run_relative(run)).name + '.lance')
            )
            table_uri = str(resolve_root() / relative)
            entry = run_records(run).get("stage/" + stage_name)
            if entry is not None and entry["fingerprint"] != version + ':' + 'material_image_embeddings':
                raise ValueError("Stage inputs changed; use a new run: " + stage_name)
            if not Path(table_uri).exists():
                (
                    routing_materials.map(EncodeImageTextMaterials(config['image_embedding_model'])).map(
                        EncodeStage('material_image_embeddings')
                    )
                ).write_lance(table_uri, mode='overwrite', schema=PIPELINE_STAGE_ROWS)
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
                entry = {
                    "fingerprint": version + ':' + 'material_image_embeddings',
                    "dataset_ref": ref.to_dict(),
                }
                run_records(run).put("stage/" + stage_name, entry, immutable=False)
            elif entry is None:
                raise ValueError("Unfinished stage table; inspect it before retrying: " + table_uri)
            saved_rows = data.read_lance(table_uri, version=entry["dataset_ref"]["lance_version"])
            stage_name = 'material_routing'
            relative = str(
                Path(run_relative(run)).parent / (stage_name + '__' + Path(run_relative(run)).name + '.lance')
            )
            table_uri = str(resolve_root() / relative)
            entry = run_records(run).get("stage/" + stage_name)
            if entry is not None and entry["fingerprint"] != version + ':' + 'material_routing':
                raise ValueError("Stage inputs changed; use a new run: " + stage_name)
            if not Path(table_uri).exists():
                (
                    routing_materials.join(text_embeddings, on='case_id', how='left')
                    .join(
                        saved_rows.map(from_stage_row).select_columns(
                            ['case_id', 'text_windows', 'image_vectors']
                        ),
                        on='case_id',
                        how='left',
                    )
                    .map(
                        RouteByTokenBudget(
                            ROOT.parent / 'models/Qwen3.8-27B',
                            config.get('joint_input_tokens', 32768),
                            counter=ArticleTokenBudget(
                                ROOT.parent / 'models/Qwen3.8-27B',
                                {
                                    **config,
                                    'enable_thinking': config.get('joint_thinking', True),
                                    'reasoning_effort': config.get('joint_reasoning_effort', 'low'),
                                },
                                'joint_paragraphs',
                            ),
                            max_images=config.get('joint_image_target', 4),
                        )
                    )
                    .map(EncodeStage('material_routing'))
                ).write_lance(table_uri, mode='overwrite', schema=PIPELINE_STAGE_ROWS)
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
                entry = {"fingerprint": version + ':' + 'material_routing', "dataset_ref": ref.to_dict()}
                run_records(run).put("stage/" + stage_name, entry, immutable=False)
            elif entry is None:
                raise ValueError("Unfinished stage table; inspect it before retrying: " + table_uri)
            saved_rows = data.read_lance(table_uri, version=entry["dataset_ref"]["lance_version"])
            stage_name = 'joint_requests'
            relative = str(
                Path(run_relative(run)).parent / (stage_name + '__' + Path(run_relative(run)).name + '.lance')
            )
            table_uri = str(resolve_root() / relative)
            entry = run_records(run).get("stage/" + stage_name)
            if entry is not None and entry["fingerprint"] != version + ':' + 'joint_requests':
                raise ValueError("Stage inputs changed; use a new run: " + stage_name)
            if not Path(table_uri).exists():
                (
                    saved_rows.map(from_stage_row)
                    .flat_map(lambda r: r['requests'])
                    .map(BuildRoutedJointRequest())
                    .map(EncodeStage('joint_requests'))
                ).write_lance(table_uri, mode='overwrite', schema=PIPELINE_STAGE_ROWS)
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
                entry = {"fingerprint": version + ':' + 'joint_requests', "dataset_ref": ref.to_dict()}
                run_records(run).put("stage/" + stage_name, entry, immutable=False)
            elif entry is None:
                raise ValueError("Unfinished stage table; inspect it before retrying: " + table_uri)
            saved_rows = data.read_lance(table_uri, version=entry["dataset_ref"]["lance_version"])
            joint_requests = saved_rows.map(from_stage_row)
            joint_config = {
                **config,
                'enable_thinking': config.get('joint_thinking', True),
                'reasoning_effort': config.get('joint_reasoning_effort', 'low'),
            }
            joint_options = prompt_execution_options(run, joint_config)
            save_prompt_config(run / 'joint_extraction', prompt_text, joint_options)
            stage_name = 'article_inputs'
            relative = str(
                Path(run_relative(run)).parent / (stage_name + '__' + Path(run_relative(run)).name + '.lance')
            )
            table_uri = str(resolve_root() / relative)
            entry = run_records(run).get("stage/" + stage_name)
            if entry is not None and entry["fingerprint"] != version + ':' + 'article_inputs':
                raise ValueError("Stage inputs changed; use a new run: " + stage_name)
            if not Path(table_uri).exists():
                (
                    data.read_lance(
                        str(
                            resolve_root()
                            / Path(run_relative(run)).parent
                            / ('joint_requests__' + Path(run_relative(run)).name + '.lance')
                        ),
                        version=stage_ref(run, 'joint_requests').lance_version,
                    )
                    .map(from_stage_row)
                    .map(PrepareArticleInput(config['image_identity_definitions']))
                    .map(EncodeStage('article_inputs'))
                ).write_lance(table_uri, mode='overwrite', schema=PIPELINE_STAGE_ROWS)
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
                entry = {"fingerprint": version + ':' + 'article_inputs', "dataset_ref": ref.to_dict()}
                run_records(run).put("stage/" + stage_name, entry, immutable=False)
            elif entry is None:
                raise ValueError("Unfinished stage table; inspect it before retrying: " + table_uri)
            saved_rows = data.read_lance(table_uri, version=entry["dataset_ref"]["lance_version"])
            stage_name = 'paragraph_extract'
            relative = str(
                Path(run_relative(run)).parent / (stage_name + '__' + Path(run_relative(run)).name + '.lance')
            )
            table_uri = str(resolve_root() / relative)
            entry = run_records(run).get("stage/" + stage_name)
            if entry is not None and entry["fingerprint"] != version + ':' + 'paragraph_extract':
                raise ValueError("Stage inputs changed; use a new run: " + stage_name)
            if not Path(table_uri).exists():
                (
                    saved_rows.map(from_stage_row)
                    .map_prompt_async(
                        'joint_paragraphs',
                        config=pack,
                        options=joint_options,
                        max_requests=config['max_calls'],
                        inputs={'payload': 'article_input', 'images': 'pixel_images'},
                        output='prompt_result',
                        call_output='prompt_call',
                        error_output='prompt_error',
                        concurrency=joint_concurrency,
                        queue_depth=joint_concurrency,
                    )
                    .map(ApplyArticle())
                    .map(EncodeStage('paragraph_extract'))
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
                entry = {"fingerprint": version + ':' + 'paragraph_extract', "dataset_ref": ref.to_dict()}
                run_records(run).put("stage/" + stage_name, entry, immutable=False)
            elif entry is None:
                raise ValueError("Unfinished stage table; inspect it before retrying: " + table_uri)
            saved_rows = data.read_lance(table_uri, version=entry["dataset_ref"]["lance_version"])
            extracted = saved_rows.map(from_stage_row)
            if through == 'extract':
                return extracted
            review_config = {
                **config,
                'enable_thinking': config.get('final_review_thinking', True),
                'reasoning_effort': config.get('final_review_effort', 'low'),
                'max_output_tokens': config.get('final_max_output_tokens', 32768),
                'timeout_s': config.get('final_timeout_s', 1200),
            }
            review_options = prompt_execution_options(run, review_config)
            save_prompt_config(run / 'final_review', prompt_text, review_options)
            stage_name = 'final_review_requests'
            relative = str(
                Path(run_relative(run)).parent / (stage_name + '__' + Path(run_relative(run)).name + '.lance')
            )
            table_uri = str(resolve_root() / relative)
            entry = run_records(run).get("stage/" + stage_name)
            if entry is not None and entry["fingerprint"] != version + ':' + 'final_review_requests':
                raise ValueError("Stage inputs changed; use a new run: " + stage_name)
            if not Path(table_uri).exists():
                (
                    extracted.reduce_by_key(
                        'concept',
                        lambda acc, r: {'concept': r['concept'], 'drafts': acc['drafts'] + [r]},
                        initial={'drafts': []},
                    )
                    .map(
                        PrepareFinalReview(
                            config['image_identity_definitions'],
                            ArticleTokenBudget(
                                ROOT.parent / 'models/Qwen3.8-27B', review_config, 'final_review'
                            ),
                            config.get('final_input_tokens', 131072),
                            draft_outline_only=config.get('final_draft_outline_only', False),
                        )
                    )
                    .map(EncodeStage('final_review_requests'))
                ).write_lance(table_uri, mode='overwrite', schema=PIPELINE_STAGE_ROWS)
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
                entry = {"fingerprint": version + ':' + 'final_review_requests', "dataset_ref": ref.to_dict()}
                run_records(run).put("stage/" + stage_name, entry, immutable=False)
            elif entry is None:
                raise ValueError("Unfinished stage table; inspect it before retrying: " + table_uri)
            saved_rows = data.read_lance(table_uri, version=entry["dataset_ref"]["lance_version"])
            review_requests = saved_rows.map(from_stage_row)
            stage_name = 'final_review'
            relative = str(
                Path(run_relative(run)).parent / (stage_name + '__' + Path(run_relative(run)).name + '.lance')
            )
            table_uri = str(resolve_root() / relative)
            entry = run_records(run).get("stage/" + stage_name)
            if entry is not None and entry["fingerprint"] != version + ':' + 'final_review':
                raise ValueError("Stage inputs changed; use a new run: " + stage_name)
            if not Path(table_uri).exists():
                (
                    data.read_lance(
                        str(
                            resolve_root()
                            / Path(run_relative(run)).parent
                            / ('final_review_requests__' + Path(run_relative(run)).name + '.lance')
                        ),
                        version=stage_ref(run, 'final_review_requests').lance_version,
                    )
                    .map(from_stage_row)
                    .map_prompt_async(
                        'final_review',
                        config=pack,
                        options=review_options,
                        max_requests=config['max_calls'],
                        inputs={'payload': 'article_input', 'images': 'pixel_images'},
                        when=lambda r: not r['preflight_error'],
                        output='prompt_result',
                        call_output='prompt_call',
                        error_output='prompt_error',
                        concurrency=final_review_concurrency,
                        queue_depth=final_review_concurrency,
                    )
                    .map(
                        ApplyArticle(
                            final=True,
                            image_selection_only=config.get('final_image_selection_only', False),
                            review_notes_required=config.get('final_review_notes_required', False),
                        )
                    )
                    .map(EncodeStage('final_review'))
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
                entry = {"fingerprint": version + ':' + 'final_review', "dataset_ref": ref.to_dict()}
                run_records(run).put("stage/" + stage_name, entry, immutable=False)
            elif entry is None:
                raise ValueError("Unfinished stage table; inspect it before retrying: " + table_uri)
            saved_rows = data.read_lance(table_uri, version=entry["dataset_ref"]["lance_version"])
            reviewed = saved_rows.map(from_stage_row)
            if through == 'final_review':
                return reviewed
            stage_name = 'knowledge_base'
            relative = str(
                Path(run_relative(run)).parent / (stage_name + '__' + Path(run_relative(run)).name + '.lance')
            )
            table_uri = str(resolve_root() / relative)
            entry = run_records(run).get("stage/" + stage_name)
            if entry is not None and entry["fingerprint"] != version + ':' + 'knowledge_base':
                raise ValueError("Stage inputs changed; use a new run: " + stage_name)
            if not Path(table_uri).exists():
                (
                    related.map(lambda r: {'concept': r['identity']['target_label'], 'material': r})
                    .reduce_by_key(
                        'concept',
                        lambda acc, r: {
                            'concept': r['concept'],
                            'materials': acc['materials'] + [r['material']],
                        },
                        initial={'materials': []},
                    )
                    .join(
                        reviewed.map(lambda r: {'concept': r['concept'], 'review': r}), on='concept', how='left'
                    )
                    .map(FinalizeArticle())
                    .map(EncodeStage('knowledge_base'))
                ).write_lance(table_uri, mode='overwrite', schema=PIPELINE_STAGE_ROWS)
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
                entry = {"fingerprint": version + ':' + 'knowledge_base', "dataset_ref": ref.to_dict()}
                run_records(run).put("stage/" + stage_name, entry, immutable=False)
            elif entry is None:
                raise ValueError("Unfinished stage table; inspect it before retrying: " + table_uri)
            saved_rows = data.read_lance(table_uri, version=entry["dataset_ref"]["lance_version"])
            result = saved_rows.map(from_stage_row)
            from preparation.operaters.results import save_results

            # 出口统一处理预检/引用完整性，并排除明确依赖配图的正文段落；下游只按公开状态读正文。
            save_results(run, 'article', target_uri=target_uri, write_mode=write_mode)
            return result
        raise ValueError('Preparation supports text_mode=multimodal only')


def run_image_review(blocks, run, config, version, *, through='review'):
    """Shared graph: callers own the run lock and immutable input/config version."""
    run = Path(run)
    stage_name = 'image_requests'
    relative = str(
        Path(run_relative(run)).parent / (stage_name + '__' + Path(run_relative(run)).name + '.lance')
    )
    table_uri = str(resolve_root() / relative)
    entry = run_records(run).get("stage/" + stage_name)
    if entry is not None and entry["fingerprint"] != version + ':' + 'image_requests':
        raise ValueError("Stage inputs changed; use a new run: " + stage_name)
    if not Path(table_uri).exists():
        (
            blocks.flat_map(
                BatchImageSelection(
                    config.get('image_batch_size', 4),
                    config['image_identity_definitions'],
                    neutral=True,
                    visual_publication=True,
                )
            ).map(EncodeStage('image_requests'))
        ).write_lance(table_uri, mode='overwrite', schema=PIPELINE_STAGE_ROWS)
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
        entry = {"fingerprint": version + ':' + 'image_requests', "dataset_ref": ref.to_dict()}
        run_records(run).put("stage/" + stage_name, entry, immutable=False)
    elif entry is None:
        raise ValueError("Unfinished stage table; inspect it before retrying: " + table_uri)
    saved_rows = data.read_lance(table_uri, version=entry["dataset_ref"]["lance_version"])
    image_requests = saved_rows.map(from_stage_row)
    if through == 'prepare':
        return image_requests
    primary_pack, primary_options = image_prompt_config(run, config)
    stage_name = 'image_primary'
    relative = str(
        Path(run_relative(run)).parent / (stage_name + '__' + Path(run_relative(run)).name + '.lance')
    )
    table_uri = str(resolve_root() / relative)
    entry = run_records(run).get("stage/" + stage_name)
    if entry is not None and entry["fingerprint"] != version + ':' + 'image_primary':
        raise ValueError("Stage inputs changed; use a new run: " + stage_name)
    if not Path(table_uri).exists():
        (
            data.read_lance(
                str(
                    resolve_root()
                    / Path(run_relative(run)).parent
                    / ('image_requests__' + Path(run_relative(run)).name + '.lance')
                ),
                version=stage_ref(run, 'image_requests').lance_version,
            )
            .map(from_stage_row)
            .map_prompt_async(
                'select_images',
                config=primary_pack,
                options=primary_options,
                max_requests=config['max_calls'],
                inputs={'payload': 'image_prompt', 'images': 'pixel_images'},
                output='prompt_result',
                call_output='prompt_call',
                error_output='prompt_error',
                concurrency=config.get('image_primary_concurrency', 1),
                queue_depth=2 * config.get('image_primary_concurrency', 1),
            )
            .map(RecordPrimaryImageSelection())
            .map(EncodeStage('image_primary'))
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
        entry = {"fingerprint": version + ':' + 'image_primary', "dataset_ref": ref.to_dict()}
        run_records(run).put("stage/" + stage_name, entry, immutable=False)
    elif entry is None:
        raise ValueError("Unfinished stage table; inspect it before retrying: " + table_uri)
    saved_rows = data.read_lance(table_uri, version=entry["dataset_ref"]["lance_version"])
    stage_name = 'image_review_inputs'
    relative = str(
        Path(run_relative(run)).parent / (stage_name + '__' + Path(run_relative(run)).name + '.lance')
    )
    table_uri = str(resolve_root() / relative)
    entry = run_records(run).get("stage/" + stage_name)
    if entry is not None and entry["fingerprint"] != version + ':' + 'image_review_inputs':
        raise ValueError("Stage inputs changed; use a new run: " + stage_name)
    if not Path(table_uri).exists():
        (
            saved_rows.map(from_stage_row).map(PrepareImageReview()).map(EncodeStage('image_review_inputs'))
        ).write_lance(table_uri, mode='overwrite', schema=PIPELINE_STAGE_ROWS)
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
        entry = {"fingerprint": version + ':' + 'image_review_inputs', "dataset_ref": ref.to_dict()}
        run_records(run).put("stage/" + stage_name, entry, immutable=False)
    elif entry is None:
        raise ValueError("Unfinished stage table; inspect it before retrying: " + table_uri)
    saved_rows = data.read_lance(table_uri, version=entry["dataset_ref"]["lance_version"])
    review_rows = saved_rows.map(from_stage_row)
    review_pack, review_options = image_prompt_config(run, config, review=True)
    review_requests = (
        data.read_lance(
            str(
                resolve_root()
                / Path(run_relative(run)).parent
                / ('image_review_inputs__' + Path(run_relative(run)).name + '.lance')
            ),
            version=stage_ref(run, 'image_review_inputs').lance_version,
        )
        .map(from_stage_row)
        .filter(lambda r: r['review_required'])
    )
    review_path = str(
        resolve_root()
        / Path(run_relative(run)).parent
        / ('image_review_responses__' + Path(run_relative(run)).name + '.lance')
    )
    with image_review_service(
        run,
        config,
        needed=review_needed(
            review_requests,
            run_records(run).get('stage/image_review_responses'),
            version + ':image_review_responses',
        ),
    ):
        stage_name = Path(review_path).stem
        relative = str(
            Path(run_relative(run)).parent / (stage_name + '__' + Path(run_relative(run)).name + '.lance')
        )
        table_uri = str(resolve_root() / relative)
        entry = run_records(run).get("stage/" + stage_name)
        if entry is not None and entry["fingerprint"] != version + ':' + Path(review_path).stem:
            raise ValueError("Stage inputs changed; use a new run: " + stage_name)
        if not Path(table_uri).exists():
            (
                review_requests.map_prompt_async(
                    'select_images',
                    config=review_pack,
                    options=review_options,
                    max_requests=config['max_calls'],
                    inputs={'payload': 'image_prompt', 'images': 'pixel_images'},
                    output='prompt_result',
                    call_output='prompt_call',
                    error_output='prompt_error',
                    concurrency=config['image_review_concurrency'],
                    queue_depth=config['image_review_concurrency'],
                )
                .map(EncodeStage(Path(review_path).stem))
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
            entry = {"fingerprint": version + ':' + Path(review_path).stem, "dataset_ref": ref.to_dict()}
            run_records(run).put("stage/" + stage_name, entry, immutable=False)
        elif entry is None:
            raise ValueError("Unfinished stage table; inspect it before retrying: " + table_uri)
        saved_rows = data.read_lance(table_uri, version=entry["dataset_ref"]["lance_version"])
    stage_name = 'image_relevance'
    relative = str(
        Path(run_relative(run)).parent / (stage_name + '__' + Path(run_relative(run)).name + '.lance')
    )
    table_uri = str(resolve_root() / relative)
    entry = run_records(run).get("stage/" + stage_name)
    if entry is not None and entry["fingerprint"] != version + ':' + 'image_relevance':
        raise ValueError("Stage inputs changed; use a new run: " + stage_name)
    if not Path(table_uri).exists():
        (
            saved_rows.map(from_stage_row)
            .union(review_rows.filter(lambda r: not r['review_required']))
            .map(ApplyConfirmedImageSelection())
            .map(EncodeStage('image_relevance'))
        ).write_lance(table_uri, mode='overwrite', schema=PIPELINE_STAGE_ROWS)
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
        entry = {"fingerprint": version + ':' + 'image_relevance', "dataset_ref": ref.to_dict()}
        run_records(run).put("stage/" + stage_name, entry, immutable=False)
    elif entry is None:
        raise ValueError("Unfinished stage table; inspect it before retrying: " + table_uri)
    saved_rows = data.read_lance(table_uri, version=entry["dataset_ref"]["lance_version"])
    image_decisions = saved_rows.map(from_stage_row).reduce_by_key('case_id', merge_image_decisions)
    save_image_filter_policy(run, config)
    return image_decisions


def finalize_visual_branch(related, run, version, *, target_uri=None, write_mode='merge'):
    """固定视觉审核阶段，按指定模式写入图片结果表。"""
    stage_name = 'visual_materials'
    relative = str(
        Path(run_relative(run)).parent / (stage_name + '__' + Path(run_relative(run)).name + '.lance')
    )
    table_uri = str(resolve_root() / relative)
    entry = run_records(run).get("stage/" + stage_name)
    if entry is not None and entry["fingerprint"] != version + ':' + 'visual_materials':
        raise ValueError("Stage inputs changed; use a new run: " + stage_name)
    if not Path(table_uri).exists():
        related.map(FinalizeVisualMaterials()).map(EncodeStage('visual_materials')).write_lance(
            table_uri, mode='overwrite', schema=PIPELINE_STAGE_ROWS
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
        entry = {"fingerprint": version + ':' + 'visual_materials', "dataset_ref": ref.to_dict()}
        run_records(run).put("stage/" + stage_name, entry, immutable=False)
    elif entry is None:
        raise ValueError("Unfinished stage table; inspect it before retrying: " + table_uri)
    saved_rows = data.read_lance(table_uri, version=entry["dataset_ref"]["lance_version"])
    published = saved_rows.map(from_stage_row)
    stage_name = 'visual_image_meta'
    relative = str(
        Path(run_relative(run)).parent / (stage_name + '__' + Path(run_relative(run)).name + '.lance')
    )
    table_uri = str(resolve_root() / relative)
    entry = run_records(run).get("stage/" + stage_name)
    if entry is not None and entry["fingerprint"] != version + ':' + 'visual_image_meta':
        raise ValueError("Stage inputs changed; use a new run: " + stage_name)
    if not Path(table_uri).exists():
        published.flat_map(ExportVisualMetadata()).map(EncodeStage('visual_image_meta')).write_lance(
            table_uri, mode='overwrite', schema=PIPELINE_STAGE_ROWS
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
        entry = {"fingerprint": version + ':' + 'visual_image_meta', "dataset_ref": ref.to_dict()}
        run_records(run).put("stage/" + stage_name, entry, immutable=False)
    elif entry is None:
        raise ValueError("Unfinished stage table; inspect it before retrying: " + table_uri)
    saved_rows = data.read_lance(table_uri, version=entry["dataset_ref"]["lance_version"])
    saved_rows.map(from_stage_row)
    from preparation.operaters.results import save_results

    # 出口按绑定的原图元数据统一来源、生成标记和可用状态；下游不再解释审核/来源 JSON。
    save_results(run, 'visual', target_uri=target_uri, write_mode=write_mode)
    return published


def run_visual_pipeline(
    run,
    input_path,
    dataset=None,
    *,
    through='prepare',
    model_config=None,
    project=ROOT,
    target_uri=None,
    write_mode='merge',
):
    """独立视觉审核：按指定模式写图片结果，不执行文章模型调用。"""
    if write_mode not in {'merge', 'append', 'overwrite'}:
        raise ValueError('write_mode must be merge, append or overwrite')
    if through not in {'prepare', 'review', 'export'}:
        raise ValueError('Expected prepare, review or export')
    run, dataset = Path(run).resolve(), Path(dataset or resolve_root()).resolve()
    from demiflow.lance.refs import DatasetRef

    input_ref = DatasetRef.from_dict(input_path)
    config = {**DEFAULT, **IMAGE_FILTER_DEFAULTS, **(model_config or {})}
    config.setdefault('image_annotations_ref', None)
    config['target_uri'] = target_uri
    config['write_mode'] = write_mode
    from preparation.operaters.runfiles import check_run_location

    check_run_location(run, project, dataset)
    with run_lock(Path(run).parent / '_demiflow' / Path(run).name):
        version, source = freeze_visual_run(run, input_path, dataset, config, Path(project))
        from preparation.operaters.images import read_visual_input

        stage_name = 'visual_inputs'
        relative = str(
            Path(run_relative(run)).parent / (stage_name + '__' + Path(run_relative(run)).name + '.lance')
        )
        table_uri = str(resolve_root() / relative)
        entry = run_records(run).get("stage/" + stage_name)
        if entry is not None and entry["fingerprint"] != version + ':' + 'visual_inputs':
            raise ValueError("Stage inputs changed; use a new run: " + stage_name)
        if not Path(table_uri).exists():
            (
                read_visual_input(input_ref, dataset, config.get('concepts'))
                .map(
                    PrepareVisualInput(
                        run,
                        dataset,
                        source,
                        config.get('image_annotations_ref'),
                        config.get('image_annotation_config_id'),
                    )
                )
                .map(SelectAvailableImages())
                .map(EncodeStage('visual_inputs'))
            ).write_lance(table_uri, mode='overwrite', schema=PIPELINE_STAGE_ROWS)
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
            entry = {"fingerprint": version + ':' + 'visual_inputs', "dataset_ref": ref.to_dict()}
            run_records(run).put("stage/" + stage_name, entry, immutable=False)
        elif entry is None:
            raise ValueError("Unfinished stage table; inspect it before retrying: " + table_uri)
        saved_rows = data.read_lance(table_uri, version=entry["dataset_ref"]["lance_version"])
        blocks = saved_rows.map(from_stage_row)
        decisions = run_image_review(blocks, run, config, version, through=through)
        if through == 'prepare':
            return decisions
        stage_name = 'visual_reviewed'
        relative = str(
            Path(run_relative(run)).parent / (stage_name + '__' + Path(run_relative(run)).name + '.lance')
        )
        table_uri = str(resolve_root() / relative)
        entry = run_records(run).get("stage/" + stage_name)
        if entry is not None and entry["fingerprint"] != version + ':' + 'visual_reviewed':
            raise ValueError("Stage inputs changed; use a new run: " + stage_name)
        if not Path(table_uri).exists():
            (
                blocks.join(decisions, on='case_id', how='left')
                .map(SelectRelatedMaterials())
                .map(EncodeStage('visual_reviewed'))
            ).write_lance(table_uri, mode='overwrite', schema=PIPELINE_STAGE_ROWS)
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
            entry = {"fingerprint": version + ':' + 'visual_reviewed', "dataset_ref": ref.to_dict()}
            run_records(run).put("stage/" + stage_name, entry, immutable=False)
        elif entry is None:
            raise ValueError("Unfinished stage table; inspect it before retrying: " + table_uri)
        saved_rows = data.read_lance(table_uri, version=entry["dataset_ref"]["lance_version"])
        related = saved_rows.map(from_stage_row)
        if through == 'review':
            return related
        published = finalize_visual_branch(related, run, version, target_uri=target_uri, write_mode=write_mode)
        stage_name = 'knowledge_base'
        relative = str(
            Path(run_relative(run)).parent / (stage_name + '__' + Path(run_relative(run)).name + '.lance')
        )
        table_uri = str(resolve_root() / relative)
        entry = run_records(run).get("stage/" + stage_name)
        if entry is not None and entry["fingerprint"] != version + ':' + 'knowledge_base':
            raise ValueError("Stage inputs changed; use a new run: " + stage_name)
        if not Path(table_uri).exists():
            (
                published.map(visual_pack)
                .reduce_by_key('concept', merge_visual_packs)
                .map(EncodeStage('knowledge_base'))
            ).write_lance(table_uri, mode='overwrite', schema=PIPELINE_STAGE_ROWS)
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
            entry = {"fingerprint": version + ':' + 'knowledge_base', "dataset_ref": ref.to_dict()}
            run_records(run).put("stage/" + stage_name, entry, immutable=False)
        elif entry is None:
            raise ValueError("Unfinished stage table; inspect it before retrying: " + table_uri)
        saved_rows = data.read_lance(table_uri, version=entry["dataset_ref"]["lance_version"])
        return saved_rows.map(from_stage_row)


def replay_visual_pipeline(run, parent, *, project=ROOT, target_uri=None, write_mode='merge'):
    """重放已有视觉响应，按指定模式写目标表，不发起新的模型调用。"""
    if write_mode not in {'merge', 'append', 'overwrite'}:
        raise ValueError('write_mode must be merge, append or overwrite')
    from preparation.operaters.runfiles import freeze_visual_replay
    from preparation.operaters.images import ReplayVisualResponse
    from preparation.operaters.runfiles import check_run_location

    run, parent = (Path(run).resolve(), Path(parent).resolve())
    check_run_location(run, project, Path(project) / 'datasets')
    if run == parent:
        raise ValueError('Replay must use a new run')
    with run_lock(Path(run).parent / '_demiflow' / Path(run).name):
        run_records(run).put(
            'output', {'uri': str(target_uri) if target_uri else None, 'write_mode': write_mode}
        )
        version = freeze_visual_replay(run, parent, project)
        blocks = data.read_lance(
            str(
                resolve_root()
                / Path(run_relative(parent)).parent
                / ('visual_inputs__' + Path(run_relative(parent)).name + '.lance')
            ),
            version=stage_ref(parent, 'visual_inputs').lance_version,
        ).map(from_stage_row)
        responses = (
            data.read_lance(
                str(
                    resolve_root()
                    / Path(run_relative(parent)).parent
                    / ('image_review_responses__' + Path(run_relative(parent)).name + '.lance')
                ),
                version=stage_ref(parent, 'image_review_responses').lance_version,
            )
            .map(from_stage_row)
            .take_all()
        )
        stage_name = 'image_relevance'
        relative = str(
            Path(run_relative(run)).parent / (stage_name + '__' + Path(run_relative(run)).name + '.lance')
        )
        table_uri = str(resolve_root() / relative)
        entry = run_records(run).get("stage/" + stage_name)
        if entry is not None and entry["fingerprint"] != version + ':' + 'image_relevance':
            raise ValueError("Stage inputs changed; use a new run: " + stage_name)
        if not Path(table_uri).exists():
            (
                data.read_lance(
                    str(
                        resolve_root()
                        / Path(run_relative(parent)).parent
                        / ('image_primary__' + Path(run_relative(parent)).name + '.lance')
                    ),
                    version=stage_ref(parent, 'image_primary').lance_version,
                )
                .map(from_stage_row)
                .map(ReplayVisualResponse(responses))
                .map(EncodeStage('image_relevance'))
            ).write_lance(table_uri, mode='overwrite', schema=PIPELINE_STAGE_ROWS)
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
            entry = {"fingerprint": version + ':' + 'image_relevance', "dataset_ref": ref.to_dict()}
            run_records(run).put("stage/" + stage_name, entry, immutable=False)
        elif entry is None:
            raise ValueError("Unfinished stage table; inspect it before retrying: " + table_uri)
        saved_rows = data.read_lance(table_uri, version=entry["dataset_ref"]["lance_version"])
        decisions = saved_rows.map(from_stage_row).reduce_by_key('case_id', merge_image_decisions)
        stage_name = 'visual_reviewed'
        relative = str(
            Path(run_relative(run)).parent / (stage_name + '__' + Path(run_relative(run)).name + '.lance')
        )
        table_uri = str(resolve_root() / relative)
        entry = run_records(run).get("stage/" + stage_name)
        if entry is not None and entry["fingerprint"] != version + ':' + 'visual_reviewed':
            raise ValueError("Stage inputs changed; use a new run: " + stage_name)
        if not Path(table_uri).exists():
            (
                blocks.join(decisions, on='case_id', how='left')
                .map(SelectRelatedMaterials())
                .map(EncodeStage('visual_reviewed'))
            ).write_lance(table_uri, mode='overwrite', schema=PIPELINE_STAGE_ROWS)
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
            entry = {"fingerprint": version + ':' + 'visual_reviewed', "dataset_ref": ref.to_dict()}
            run_records(run).put("stage/" + stage_name, entry, immutable=False)
        elif entry is None:
            raise ValueError("Unfinished stage table; inspect it before retrying: " + table_uri)
        saved_rows = data.read_lance(table_uri, version=entry["dataset_ref"]["lance_version"])
        related = saved_rows.map(from_stage_row)
        published = finalize_visual_branch(related, run, version, target_uri=target_uri, write_mode=write_mode)
        stage_name = 'knowledge_base'
        relative = str(
            Path(run_relative(run)).parent / (stage_name + '__' + Path(run_relative(run)).name + '.lance')
        )
        table_uri = str(resolve_root() / relative)
        entry = run_records(run).get("stage/" + stage_name)
        if entry is not None and entry["fingerprint"] != version + ':' + 'knowledge_base':
            raise ValueError("Stage inputs changed; use a new run: " + stage_name)
        if not Path(table_uri).exists():
            (
                published.map(visual_pack)
                .reduce_by_key('concept', merge_visual_packs)
                .map(EncodeStage('knowledge_base'))
            ).write_lance(table_uri, mode='overwrite', schema=PIPELINE_STAGE_ROWS)
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
            entry = {"fingerprint": version + ':' + 'knowledge_base', "dataset_ref": ref.to_dict()}
            run_records(run).put("stage/" + stage_name, entry, immutable=False)
        elif entry is None:
            raise ValueError("Unfinished stage table; inspect it before retrying: " + table_uri)
        saved_rows = data.read_lance(table_uri, version=entry["dataset_ref"]["lance_version"])
        return saved_rows.map(from_stage_row)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--flow', choices=['article', 'visual'], default='article')
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--sources', type=Path, help='文章流程 JSON: concepts/documents/images 的 uri/version')
    parser.add_argument('--target', help='当前流程最终结果表路径')
    parser.add_argument('--visual-target', help='文章流程中的图片结果表路径')
    parser.add_argument(
        '--write-mode', choices=['merge', 'append', 'overwrite'], default='merge', help='当前流程目标表写入模式'
    )
    parser.add_argument(
        '--visual-write-mode',
        choices=['merge', 'append', 'overwrite'],
        default='merge',
        help='文章流程中图片目标表的写入模式',
    )
    parser.add_argument('--ids', nargs='+')
    parser.add_argument('--input', type=Path, help='Visual flow: JSON containing a fixed Lance DatasetRef')
    parser.add_argument('--dataset', type=Path, default=resolve_root())
    parser.add_argument('--source-scope', choices=['all', 'collected'], default='collected')
    parser.add_argument(
        '--through',
        choices=['gather', 'identity', 'organize', 'extract', 'final_review', 'prepare', 'review', 'export'],
    )
    parser.add_argument('--group-size', type=int, default=256)
    parser.add_argument('--config', type=Path, help='JSON overrides for model and material settings')
    args = parser.parse_args()
    overrides = read(args.config) if args.config else {}
    if args.flow == 'visual':
        if args.input is None or args.ids:
            parser.error('--flow visual requires --input and does not accept --ids')
        through = args.through or 'prepare'
        if through not in {'prepare', 'review', 'export'}:
            parser.error('Visual stages: prepare, review, export')
        result = run_visual_pipeline(
            args.run,
            read(args.input),
            args.dataset,
            through=through,
            model_config=overrides,
            target_uri=args.target,
            write_mode=args.write_mode,
        )
    else:
        if not args.ids or args.input:
            parser.error('--flow article requires --ids and does not accept --input')
        through = args.through or 'gather'
        if through not in {'gather', 'identity', 'organize', 'extract', 'final_review', 'export'}:
            parser.error('Article stages: gather, identity, organize, extract, final_review, export')
        result = run_pipeline(
            args.run,
            args.dataset,
            ids=args.ids,
            group_size=args.group_size,
            through=through,
            model_config=default_config(**overrides),
            source_scope=args.source_scope,
            sources=read(args.sources) if args.sources else None,
            target_uri=args.target,
            visual_target_uri=args.visual_target,
            write_mode=args.write_mode,
            visual_write_mode=args.visual_write_mode,
        )
    print(
        json.dumps({'run': str(args.run), 'through': through, 'output_type': type(result).__name__}), flush=True
    )


if __name__ == '__main__':
    main()
