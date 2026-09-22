"""Final publication adapter. Intermediate records resolve bytes/citations only.

The input is a knowledge pipeline run, never a downstream acceptance sidecar.
Publication is a machine review, not human factual certification.
"""
from pathlib import Path


from curation.preparation.delivery import attach_identity
from curation.preparation.records import digest, rows


def published_record(row):
    """One final KB row -> explicit eligible items and rejected-delivery reasons."""
    run = Path(row['_knowledge_run']) if row.get('_knowledge_run') else None
    concept = row['concept']
    result = {'concept': concept, 'materials': [], 'delivery_issues': [],
              'publication_status': row.get('status'), 'knowledge_run': str(run) if run is not None else None,
              'publication_source': row.get('_article_source') or row.get('_visual_sources')}
    # V2 visual publication is independent of text/article success. Legacy rows
    # have no visual_materials and retain their original publication boundary.
    for visual in row.get('visual_materials', []):
        publication = visual.get('publication', {})
        image = visual.get('image', {})
        support = publication.get('support', {})
        if (publication.get('schema') != 'concept-visual-publication/1'
                or publication.get('status') != 'reviewed' or visual.get('concept') != concept
                or publication.get('sha256') != image.get('bytes', {}).get('sha256')
                or not support.get('supports') or not support.get('region')):
            result['delivery_issues'].append('Invalid independent visual publication')
            continue
        asset = source_asset(image)
        if not asset:
            result['delivery_issues'].append('Independent visual pixels/provenance unavailable')
            continue
        metadata = publication.get('metadata', {})
        asset['image_metadata'] = metadata
        asset['description'] = (metadata.get('description') or {}).get('caption') or publication.get('visible_information', '')
        item = attach_identity({'kind':'image', 'concept':concept, 'image_id':visual['image_id'],
            'asset':asset, 'upstream_status':'published', 'upstream_run':str(run),
            'visual_support':support, 'image_metadata':metadata,
            'selection_review':image.get('selection_review', {}),
            'publication':{'status':'published', 'adapter':'visual-publication/1',
                'knowledge_sha256':row['_knowledge_sha256']}})
        item['review'] = {'decision':'accept', 'reviewer_kind':'upstream_model',
            'reviewer':'knowledge.visual_review', 'publisher':'PublishVisualMaterials',
            'publication_status':'reviewed', 'sha256':asset['sha256'],
            'reason':publication['identity_reason'], 'scope':support['supports'],
            'support_scope':support['supports'], 'region':support['region'],
            'limitations':support.get('limitations', ''), 'evidence':[digest(publication)]}
        result['materials'].append(item)
    if row.get('publication_kind') == 'visual_materials':
        if row.get('knowledge'):
            raise ValueError('Visual-only publication cannot assert article knowledge')
        return result
    if row.get('status') != 'reviewed' or row.get('audit', {}).get('preflight_error'):
        result['delivery_issues'].append('Final knowledge not published: ' + str(row.get('status_reason') or row.get('status')))
        return result
    # Neither membership in this pool nor a keep decision grants eligibility.
    passages = {p['source_id']: p for p in row.get('published_passages', [])}
    images = {}
    for raw in row.get('published_images', []):
        iid = raw.get('image_id') or raw.get('record', {}).get('image_id')
        if iid:
            images.setdefault(iid, raw)
    selected = set(row.get('audit', {}).get('selected_image_ids', []))
    for ti, topic in enumerate(row.get('knowledge', [])):
        texts = topic.get('content', {}).get('paragraphs', [])
        placements = topic.get('content', {}).get('images', [])
        for pi, paragraph in enumerate(texts):
            refs = [r for r in topic.get('references', []) if pi in r.get('paragraph_indices', [])]
            ids = {s for r in refs for s in r.get('source_ids', []) if 'text' in r.get('kinds', [])}
            missing = sorted(ids - passages.keys())
            # Visual-only paragraphs are allowed only with their final published figure.
            figures = [p for p in placements if p.get('paragraph_index') == pi and p['image_id'] in selected]
            if not paragraph.strip() or not refs or missing or (not ids and not figures):
                result['delivery_issues'].append({'position': [ti, pi], 'reason': 'Missing cited source context or final visual evidence', 'missing_source_ids': missing})
                continue
            item = attach_identity({'kind': 'text', 'concept': concept, 'case_id': row.get('case_id', concept),
                'text': paragraph, 'title': topic['title'], 'position': [ti, pi], 'references': refs,
                'sources': [passages[s] for s in sorted(ids)], 'upstream_status': 'reviewed',
                'upstream_run': str(run), 'publication': {'status': 'published', 'adapter': 'final-publication/2',
                    'knowledge_sha256': row['_knowledge_sha256'], 'visual_dependencies': [p['image_id'] for p in figures]}})
            item['review'] = publication_review(topic['title'], row)
            result['materials'].append(item)
        for placement in placements:
            iid = placement['image_id']
            if any(m.get('image_id') == iid for m in result['materials'] if m['kind']=='image'):
                continue  # Keep the richer independent visual record once.
            image = images.get(iid)
            if iid not in selected or not image:
                result['delivery_issues'].append({'image_id': iid, 'reason': 'Figure lacks final selection or resolvable source'})
                continue
            asset = source_asset(image)
            if not asset:
                result['delivery_issues'].append({'image_id': iid, 'reason': 'Final figure bytes/provenance unavailable'})
                continue
            pi = placement.get('paragraph_index')
            support = texts[pi] if type(pi) is int and 0 <= pi < len(texts) else ''
            item = attach_identity({'kind': 'image', 'concept': concept, 'image_id': iid,
                'asset': asset, 'upstream_status': 'published', 'upstream_run': str(run),
                'position': [ti, pi], 'placement': placement, 'selection_review': image.get('selection_review', {}),
                'publication': {'status': 'published', 'adapter': 'final-publication/2', 'knowledge_sha256': row['_knowledge_sha256']}})
            item['review'] = {**publication_review(support, row), 'support_scope': support,
                              'limitations': placement.get('limitations'), 'sha256': asset['sha256']}
            result['materials'].append(item)
    # A text reference to unavailable pixels must not become text-only evidence.
    delivered_images = {i['image_id'] for i in result['materials'] if i['kind'] == 'image'}
    result['materials'] = [i for i in result['materials'] if not set(i['publication'].get('visual_dependencies', [])) - delivered_images]
    return result


def publication_review(scope, row):
    return {'decision': 'accept', 'reviewer_kind': 'upstream_model', 'reviewer': 'knowledge.final_review',
            'reason': 'Final article explicitly publishes this item; not human certification',
            'scope': scope, 'evidence': [row['_knowledge_sha256']], 'publisher': 'PublishArticle',
            'publication_status': 'reviewed'}


def source_asset(image):
    info, record = image.get('bytes', {}), image.get('record', {})
    path, sha = info.get('path'), info.get('sha256')
    source = {k: record[k] for k in ('landing_url', 'content_url', 'url', 'license', 'author', 'source') if record.get(k)}
    if record.get('sources'):
        source['records'] = record['sources']
    if not sha or not source:
        return None
    from curation.preparation.asset_io import asset_resolution
    resolution = asset_resolution(path, sha)
    if resolution.status == 'missing':
        return None
    if resolution.status == 'corrupt':
        # 冻结字节与内容身份不符是交付错误，不能静默降级为无图
        raise ValueError('Frozen image bytes changed: ' + str(path))
    if resolution.status == 'read_error':
        raise ValueError('Image bytes unreadable: ' + str(resolution.error))
    from collect.materials import generation_origin
    origin = generation_origin(record)
    if origin == 'not_verified':
        origin = info.get('generation_origin', origin)
    if origin in {'generated', 'synthetic', 'ai_generated'}:
        return None
    from curation.preparation.ops.visual_materials import index_metadata
    metadata = index_metadata(image)
    return {'path': path, 'sha256': sha, 'source': source, 'origin': origin,
            'asset_source': resolution.source,
            'image_metadata': metadata,
            'description': (metadata.get('description') or {}).get('caption') or record.get('caption', ''), 'image_id': image.get('image_id', record.get('image_id'))}
