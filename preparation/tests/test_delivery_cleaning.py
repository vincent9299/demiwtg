"""Preparation 出口交付独立文字和可用图片，下游不重复清洗。"""
import json
from copy import deepcopy

import pytest

from preparation.operaters.article import article_entity
from preparation.operaters.images import assessment, write_curation
from preparation.tests.conftest import raw_metadata_snapshot


def article(paragraphs=None):
    paragraphs = paragraphs or ['独立正文。']
    return {'concept': '示例', 'status': 'reviewed', 'audit': {},
        'published_passages': [{'source_id': 'S1', 'text': '可靠资料正文'}],
        'knowledge': [{'title': '正文', 'content': {'paragraphs': paragraphs, 'images': []},
            'references': [{'kinds': ['text'], 'source_ids': ['S1'],
                            'paragraph_indices': list(range(len(paragraphs)))}]}]}


@pytest.mark.parametrize('failure', ['preflight', 'missing_citation', 'missing_evidence'])
def test_invalid_article_is_not_delivered_as_reviewed(failure):
    row = article()
    if failure == 'preflight':
        row['audit']['preflight_error'] = 'failed_extraction_batches'
    elif failure == 'missing_citation':
        row['published_passages'] = []
    else:
        row['knowledge'][0]['references'] = []
    result = article_entity(row, release_id='test')
    assert result['review_status'] == 'failed'
    assert result['status_reason'] and not result['release_ids']


def test_figure_dependent_paragraphs_are_excluded_without_rewriting_other_text():
    row = article(['独立事实。', '如图1所示，右侧存在该结构。', '流程图中箭头表示方向。', '图2'])
    original = deepcopy(row)
    result = article_entity(row)
    topic = result['content'][0]
    assert result['review_status'] == 'reviewed'
    assert topic['content']['paragraphs'] == ['独立事实。', '流程图中箭头表示方向。']
    assert topic['references'][0]['paragraph_indices'] == [0, 1]
    audit = json.loads(result['context_json'])['audit']
    assert [p['paragraph_index'] for p in audit['excluded_figure_paragraphs']] == [1, 3]
    assert row == original
    assert article_entity({**row, 'knowledge': [original['knowledge'][0]]}) == result


def test_no_independent_text_is_not_published():
    result = article_entity(article(['如图所示。', '图1']))
    assert result['content'] == []
    assert result['review_status'] == 'insufficient_materials'


def visual(sha):
    return {'sha256': sha, 'concept': '示例', 'publication_status': 'reviewed',
        'concept_review': {'decision': 'keep'},
        'visual_support': {'supports': '可见结构', 'region': 'whole', 'limitations': ''}}


def test_image_source_is_normalized_at_export_and_reexport_is_idempotent(tmp_path):
    raw = raw_metadata_snapshot(tmp_path, ['a' * 64])
    rows = [{'sha256': 'a' * 64, 'concept_assessments': [assessment(visual('a' * 64), 'test')]}]
    ref = write_curation(tmp_path, rows, source_ref=raw)
    record = ref.open(tmp_path).to_table().to_pylist()[0]
    source = json.loads(record['concept_assessments'][0]['observation_json'])['source']
    assert source['dataset_ref'] == raw.to_dict()
    assert record['published_concepts'] == ['示例']
    assert write_curation(tmp_path, rows, source_ref=raw) == ref


def test_known_generated_image_is_removed_from_public_usable_status(tmp_path):
    from collect.material_writer import write_images
    from collect.materials import source_record
    from collect.material_schema import IMAGES_URI
    from demiflow.lance.registry import Catalog
    sha = 'a' * 64
    write_images(tmp_path, [{'sha256': sha, 'data': None, 'ext': 'png', 'byte_size': 0,
        'storage_mode': 'lance_blob', 'availability': 'metadata_only', 'concepts': [],
        'sources': [source_record({'generation_origin': 'generated'}, system='test')], 'resolution': None}])
    raw = max((r for r in Catalog(tmp_path).registered() if r.relative_uri == IMAGES_URI), key=lambda r: r.lance_version)
    ref = write_curation(tmp_path, [{'sha256': sha, 'concept_assessments': [assessment(visual(sha), 'test')]}], source_ref=raw)
    row = ref.open(tmp_path).to_table().to_pylist()[0]
    assert row['published_concepts'] == []
    item = row['concept_assessments'][0]
    assert item['review_status'] == 'reject' and not item['published']
    assert 'generated' in item['reason']


def test_missing_support_is_an_upstream_delivery_failure(tmp_path):
    raw = raw_metadata_snapshot(tmp_path, ['a' * 64])
    value = visual('a' * 64)
    value['visual_support']['region'] = ''
    ref = write_curation(tmp_path, [{'sha256': 'a' * 64, 'concept_assessments': [assessment(value, 'test')]}], source_ref=raw)
    row = ref.open(tmp_path).to_table().to_pylist()[0]
    assert row['published_concepts'] == []
    assert 'support' in row['concept_assessments'][0]['reason']
