"""发布输入：发布 ID 或运行定位符先解析为固定 Lance 引用，再按概念合并。

文章冲突显式报错；独立视觉材料保留所有概念关系。候选图读取所有声明的
发布源，字节去重和题目级参考隔离由下游算子执行。
"""
from __future__ import annotations

import json
from pathlib import Path

from curation.preparation.records import digest


def _ref_open(spec: dict):
    from project import resolve_root
    from demiflow.lance.refs import DatasetRef
    return DatasetRef.from_dict(spec['dataset_ref']).open(resolve_root())


def freeze_publication_source(spec):
    """A publication is a fixed entity table plus an explicit release selection."""
    from demiflow.lance.refs import DatasetRef
    if isinstance(spec, dict):
        if 'dataset_ref' in spec:
            if set(spec) != {'dataset_ref', 'release_id'}:
                raise ValueError('Expected dataset_ref and release_id')
            DatasetRef.from_dict(spec['dataset_ref'])
            return dict(spec)
        ref = DatasetRef.from_dict(spec)
        if ref.schema_name in {'curated_images', 'articles'}:
            raise ValueError('Entity publication requires an explicit release_id selection')
        return {'dataset_ref': ref.to_dict(), 'release_id': None}
    candidate = Path(spec)
    if candidate.is_absolute() or '/' in str(spec):
        from curation.preparation.stages import stage_ref
        return {'dataset_ref': stage_ref(candidate, 'knowledge_base').to_dict(), 'release_id': None}
    from project import resolve_root
    from demiflow.lance.registry import ReleaseRegistry
    record = ReleaseRegistry(resolve_root()).get(str(spec))
    if record is None or record['status'] != 'registered':
        raise ValueError('Not a knowledge run or registered release: ' + str(spec))
    supported = {'articles', 'curated_images', 'pipeline_stage_rows'}
    refs = [r for r in json.loads(record['table_refs']) if r['schema_name'] in supported]
    if len(refs) != 1:
        raise ValueError('Publication must select one entity table')
    selection = json.loads(record.get('validation') or '{}').get('selection_release_id') or str(spec)
    return {'dataset_ref': refs[0], 'release_id': selection}


def _visual_meta_row(record):
    """Restore the existing publication contract from explicit per-concept release metadata."""
    review = record.get('concept_review') or {}
    if record.get('publication_status') != 'reviewed' or review.get('decision') != 'keep':
        raise ValueError('Published visual row contradicts its saved final review')
    sha, iid = record['sha256'], record.get('image_id') or 'I' + record['sha256'][:12]
    image = {'image_id': iid, 'record': record.get('source') or {},
             'selection_review': review,
             'bytes': {'path': record.get('path'), 'sha256': sha, 'resolution': record.get('resolution')}}
    publication = {'schema': 'concept-visual-publication/1', 'status': 'reviewed', 'sha256': sha,
                   'metadata': record.get('image_metadata') or {}, 'support': record.get('visual_support') or {},
                   'identity_reason': review.get('reason') or '', 'visible_information': review.get('visible_information') or ''}
    return {'concept': record['concept'], 'status': 'reviewed', 'publication_kind': 'visual_materials',
            'knowledge': [], 'curated_images': [image],
            'visual_materials': [{'concept': record['concept'], 'image_id': iid,
                                  'image': image, 'publication': publication}]}


def iter_publication_rows(spec):
    spec = freeze_publication_source(spec)
    ds = _ref_open(spec)
    kind = spec['dataset_ref']['schema_name']
    release = spec['release_id']
    if kind in {'articles', 'curated_images'} and not release:
        raise ValueError('Entity publication requires release_id')
    predicate = "array_contains(release_ids, '" + release.replace("'", "''") + "')" if release else None
    def gen():
        if kind == 'curated_images':
            from curation.preparation.images import assessment_record
            for batch in ds.scanner(columns=['sha256', 'concept_assessments'], filter=predicate).to_batches():
                for image in batch.to_pylist():
                    for item in image['concept_assessments'] or []:
                        if item['published'] and release in item['release_ids']:
                            yield _visual_meta_row(assessment_record(image, item))
        elif kind == 'articles':
            from curation.preparation.articles import article_record
            for batch in ds.scanner(filter=predicate + " AND review_status = 'reviewed'").to_batches():
                for row in batch.to_pylist(): yield article_record(row)
        elif kind == 'pipeline_stage_rows':
            for batch in ds.scanner(columns=['payload']).to_batches():
                for value in batch.column(0).to_pylist(): yield json.loads(value)
        else:
            raise ValueError('Unsupported publication entity: ' + kind)
    return gen(), spec


def combined_input_records(article_sources, visual_sources=()):
    """文章源 + 视觉源 → 按概念组合的发布行（含完整来源标注）。"""
    articles = {}   # concept -> (row, source_identity, row_sha)
    conflicts = []
    article_order = []
    for spec in article_sources:
        gen, identity = iter_publication_rows(spec)
        for row in gen:
            concept = row["concept"]
            row_sha = digest(row)
            prior = articles.get(concept)
            if prior is not None:
                if prior[2] != row_sha:
                    conflicts.append({
                        "concept": concept,
                        "publications": [
                            {"source": prior[1], "content_sha256": prior[2]},
                            {"source": identity, "content_sha256": row_sha},
                        ],
                        "reason": "Multiple distinct article publications for one concept require explicit resolution",
                    })
                    continue
                continue  # identical publication duplicated across sources
            enriched = {**row, "_article_source": identity, "_knowledge_sha256": row_sha}
            articles[concept] = (enriched, identity, row_sha)
            article_order.append(concept)
    if conflicts:
        raise ValueError("Conflicting article publications: " + json.dumps(conflicts, ensure_ascii=False))

    visuals = {}    # concept -> list[(visual_row, identity)]
    visual_order = []
    for spec in visual_sources:
        gen, identity = iter_publication_rows(spec)
        for row in gen:
            concept = row["concept"]
            visuals.setdefault(concept, []).append((row, identity))
            if concept not in visual_order:
                visual_order.append(concept)

    def generate():
        for concept in article_order:
            row, identity, _sha = articles[concept]
            merged_visuals = list(row.get("visual_materials", []))
            visual_sources_used = []
            candidate_specs = [identity]
            for visual_row, visual_identity in visuals.get(concept, []):
                merged_visuals.extend(visual_row.get("visual_materials", []))
                visual_sources_used.append(visual_identity)
                if visual_identity not in candidate_specs:
                    candidate_specs.append(visual_identity)
            yield {**row, "visual_materials": merged_visuals,
                   "_visual_sources": visual_sources_used,
                   "_candidate_specs": candidate_specs}
        for concept in visual_order:
            if concept in articles:
                continue
            entries = visuals[concept]
            merged_visuals = []
            candidate_specs = []
            for visual_row, visual_identity in entries:
                merged_visuals.extend(visual_row.get("visual_materials", []))
                if visual_identity not in candidate_specs:
                    candidate_specs.append(visual_identity)
            base = entries[0][0]
            yield {**base, "knowledge": [], "publication_kind": "visual_materials",
                   "visual_materials": merged_visuals,
                   "_visual_sources": [identity for _row, identity in entries],
                   "_candidate_specs": candidate_specs,
                   "_knowledge_sha256": digest([r for r, _ in entries])}

    return generate()


__all__ = ["combined_input_records", "iter_publication_rows"]
