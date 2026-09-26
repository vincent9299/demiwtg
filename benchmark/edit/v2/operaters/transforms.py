"""声明式数据流的复杂行/分组函数；不另建 Dataset、不执行整表或调度模型。"""

from demiflow.execution.artifacts import digest
from pathlib import Path
from preparation.operaters.inputs import attach_identity, material_review
import json


def combine_concept_materials(row):
    """将本概念已关联的文章与图片字段合并为材料记录。"""
    return {
        **row['record'],
        '_article_source': row['source'],
        '_knowledge_sha256': row['content_sha256'],
        'visual_materials': row['record'].get('visual_materials', []) + row.get('visual_materials', []),
        '_visual_sources': row.get('visual_sources', []),
        '_candidate_specs': [row['source']]
        + [
            v
            for i, v in enumerate(row.get('visual_sources', []))
            if v != row['source'] and v not in row.get('visual_sources', [])[:i]
        ],
    }


def build_visual_item(row):
    """把一条独立图片证据构造成带来源、身份及限制的材料项。"""
    return (
        {
            **row,
            'item': attach_identity(
                {
                    'kind': 'image',
                    'concept': row['concept'],
                    'image_id': row['visual']['image_id'],
                    'asset': {
                        **row['asset'],
                        'image_metadata': row['publication'].get('metadata', {}),
                        'description': (row['publication'].get('metadata', {}).get('description') or {}).get(
                            'caption'
                        )
                        or row['publication'].get('visible_information', ''),
                    },
                    'upstream_status': 'published',
                    'upstream_run': (
                        str(Path(row['parent']['_knowledge_run']))
                        if row['parent'].get('_knowledge_run')
                        else 'None'
                    ),
                    'visual_support': row['support'],
                    'image_metadata': row['publication'].get('metadata', {}),
                    'selection_review': row['image'].get('selection_review', {}),
                    'publication': {
                        'status': 'published',
                        'adapter': 'visual-publication/1',
                        'knowledge_sha256': row['parent']['_knowledge_sha256'],
                    },
                }
            ),
        }
        if not row['issue']
        else row
    )


def apply_figure_review(row):
    """为有效文章配图绑定支持范围和审核结论。"""
    return (
        {
            **row,
            'item': {
                **row['item'],
                'review': {
                    **material_review(row['support'], row['parent']),
                    'support_scope': row['support'],
                    'limitations': row['placement'].get('limitations'),
                    'sha256': row['asset']['sha256'],
                },
            },
        }
        if not row['issue']
        else row
    )


def deduplicate_figures(row):
    """按已有顺序去除本概念材料中的重复配图。"""
    return {
        **row,
        'entries': [
            entry
            for i, entry in enumerate(row['entries'])
            if not entry['item']
            or entry['item']['kind'] != 'image'
            or entry['item']['image_id']
            not in [
                previous['item']['image_id']
                for previous in row['entries'][:i]
                if previous['item'] and previous['item']['kind'] == 'image'
            ]
        ],
    }


def select_published_materials(row):
    """按发布引用和审核范围筛选本概念已组合的材料。"""
    return {
        'concept': row['concept'],
        '_candidate_specs': row['_candidate_specs'],
        'materials': [
            item
            for item in row['materials']
            if row.get('publication_kind') == 'visual_materials'
            or row.get('status') != 'reviewed'
            or row.get('audit', {}).get('preflight_error')
            or not set(item['publication'].get('visual_dependencies', []))
            - {image['image_id'] for image in row['materials'] if image['kind'] == 'image'}
        ],
        'delivery_issues': row.get('visual_issues', [])
        + (
            ['Final knowledge not published: ' + str(row.get('status_reason') or row.get('status'))]
            if row.get('publication_kind') != 'visual_materials'
            and (row.get('status') != 'reviewed' or row.get('audit', {}).get('preflight_error'))
            else [entry['issue'] for entry in row.get('entries', []) if entry['issue']]
        ),
        'publication_status': row.get('status'),
        'knowledge_run': str(Path(row['_knowledge_run'])) if row.get('_knowledge_run') else None,
        'publication_source': row.get('_article_source') or row.get('_visual_sources'),
    }


def apply_visual_review(row):
    """将独立图片的已发布审核结论写入当前材料项。"""
    return (
        {
            **row,
            'item': {
                **row['item'],
                'review': {
                    'decision': 'accept',
                    'reviewer_kind': 'upstream_model',
                    'reviewer': 'knowledge.visual_review',
                    'publisher': 'PublishVisualMaterials',
                    'publication_status': 'reviewed',
                    'sha256': row['asset']['sha256'],
                    'reason': row['publication']['identity_reason'],
                    'scope': row['support']['supports'],
                    'support_scope': row['support']['supports'],
                    'region': row['support']['region'],
                    'limitations': row['support'].get('limitations', ''),
                    'evidence': [digest(row['publication'])],
                },
            },
        }
        if not row['issue']
        else row
    )


def build_text_item(row):
    """将一段正文及其已关联引用构造成带身份的文字材料项。"""
    return (
        {
            **row,
            'item': attach_identity(
                {
                    'kind': 'text',
                    'concept': row['concept'],
                    'case_id': row['parent'].get('case_id', row['concept']),
                    'text': row['text'],
                    'title': row['topic']['title'],
                    'position': [row['topic_index'], row['paragraph_index']],
                    'references': row['refs'],
                    'sources': [row['passages'][sid] for sid in row['source_ids']],
                    'upstream_status': 'reviewed',
                    'upstream_run': (
                        str(Path(row['parent']['_knowledge_run']))
                        if row['parent'].get('_knowledge_run')
                        else 'None'
                    ),
                    'publication': {
                        'status': 'published',
                        'adapter': 'final-publication/2',
                        'knowledge_sha256': row['parent']['_knowledge_sha256'],
                        'visual_dependencies': [p['image_id'] for p in row['figures']],
                    },
                }
            ),
        }
        if not row['issue']
        else row
    )


def decode_article(row):
    """解析 preparation 文章行的正文、引用、配图和上下文字段。"""
    if 'payload' in row:
        return json.loads(row['payload'])
    return {
        **json.loads(row.get('context_json') or '{}'),
        'concept': row['concept'],
        'case_id': row.get('case_id'),
        'status': row['review_status'],
        'status_reason': row.get('status_reason'),
        'knowledge': [
            {
                **topic,
                'references': [
                    {k: v for k, v in ref.items() if v is not None} for ref in topic['references'] or []
                ],
            }
            for topic in row.get('content') or []
        ],
        'published_passages': row.get('citations') or [],
        'published_images': [json.loads(image['evidence_json']) for image in row.get('illustrations') or []],
    }


def mark_missing_evidence(row):
    """为缺少来源、正文或有效配图的段落记录原因。"""
    return {
        **row,
        'issue': (
            {
                'position': [row['topic_index'], row['paragraph_index']],
                'reason': 'Missing cited source context or final visual evidence',
                'missing_source_ids': row['missing'],
            }
            if not row['text'].strip()
            or not row['refs']
            or row['missing']
            or not (row['source_ids'] or row['figures'])
            else None
        ),
    }


def build_figure_item(row):
    """将一条文章配图构造成带身份和来源的图片材料项。"""
    return (
        {
            **row,
            'item': attach_identity(
                {
                    'kind': 'image',
                    'concept': row['concept'],
                    'image_id': row['placement']['image_id'],
                    'asset': row['asset'],
                    'upstream_status': 'published',
                    'upstream_run': (
                        str(Path(row['parent']['_knowledge_run']))
                        if row['parent'].get('_knowledge_run')
                        else 'None'
                    ),
                    'position': [row['topic_index'], row['placement'].get('paragraph_index')],
                    'placement': row['placement'],
                    'selection_review': row['image'].get('selection_review', {}),
                    'publication': {
                        'status': 'published',
                        'adapter': 'final-publication/2',
                        'knowledge_sha256': row['parent']['_knowledge_sha256'],
                    },
                }
            ),
        }
        if not row['issue']
        else row
    )


def decode_visual_publication(row):
    """将图片审核行转成图片证据与发布范围字段。"""
    return {
        **row,
        'image_id': row.get('image_id') or 'I' + row['sha256'][:12],
        'image': {
            'image_id': row.get('image_id') or 'I' + row['sha256'][:12],
            'record': row.get('source') or {},
            'selection_review': row['concept_review'],
            'bytes': {
                'path': row.get('path'),
                'sha256': row['sha256'],
                'resolution': row.get('resolution'),
            },
        },
        'publication': {
            'schema': 'concept-visual-publication/1',
            'status': 'reviewed',
            'sha256': row['sha256'],
            'metadata': row.get('image_metadata') or {},
            'support': row.get('visual_support') or {},
            'identity_reason': row['concept_review'].get('reason') or '',
            'visible_information': row['concept_review'].get('visible_information') or '',
        },
    }


def check_visual_identity(row):
    """检查单条图片证据的概念、内容 SHA 与发布身份是否一致。"""
    return {
        **row,
        'issue': (
            'Invalid independent visual publication'
            if row['publication'].get('schema') != 'concept-visual-publication/1'
            or row['publication'].get('status') != 'reviewed'
            or row['visual'].get('concept') != row['concept']
            or row['publication'].get('sha256') != row['image'].get('bytes', {}).get('sha256')
            or not row['support'].get('supports')
            or not row['support'].get('region')
            else None
        ),
    }


def filter_missing_figures(row):
    """去掉依赖配图缺失的文字材料并保留原因。"""
    return {
        **row,
        'issues': row['issues']
        + [
            {'item_id': item['item_id'], 'reason': 'Required published figure excluded'}
            for item in row['materials']
            if set(item['publication'].get('visual_dependencies', [])) - row['figure_ids']
        ],
        'materials': [
            item
            for item in row['materials']
            if not set(item['publication'].get('visual_dependencies', [])) - row['figure_ids']
        ],
    }


def paragraph_evidence(row):
    """从本段所属主题中选择绑定到该段的引用和配图。"""
    return {
        **row,
        'refs': [
            ref
            for ref in row['topic'].get('references', [])
            if row['paragraph_index'] in ref.get('paragraph_indices', [])
        ],
        'figures': [
            image
            for image in row['topic'].get('content', {}).get('images', [])
            if image.get('paragraph_index') == row['paragraph_index']
            and image['image_id'] in row['parent'].get('audit', {}).get('selected_image_ids', [])
        ],
    }


def check_figure_selection(row):
    """检查本条配图是否被最终审核选中且有可解析来源。"""
    return {
        **row,
        'issue': (
            {
                'image_id': row['placement']['image_id'],
                'reason': 'Figure lacks final selection or resolvable source',
            }
            if row['placement']['image_id'] not in row['parent'].get('audit', {}).get('selected_image_ids', [])
            or not row['image']
            else None
        ),
    }


def decode_visual_record(row):
    """解码一条图片概念审核记录并检查最终审核一致性；兼容已冻结阶段表的 payload。"""
    if 'payload' in row:
        return json.loads(row['payload'])
    record = {
        **json.loads(row['observation_json']),
        'sha256': row['sha256'],
        'concept': row['concept'],
        'concept_review': json.loads(row['review_json']),
        'visual_support': row['visual_support'],
    }
    if record['concept_review'].get('decision') != 'keep':
        raise ValueError('Published visual row contradicts its saved final review')
    record = decode_visual_publication(record)
    return {
        'concept': record['concept'],
        'status': 'reviewed',
        'publication_kind': 'visual_materials',
        'knowledge': [],
        'curated_images': [record['image']],
        'visual_materials': [{key: record[key] for key in ('concept', 'image_id', 'image', 'publication')}],
    }
