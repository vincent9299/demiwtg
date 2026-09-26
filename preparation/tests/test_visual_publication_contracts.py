"""Preserved publication and image-role regressions from the training draft."""
import copy

import pytest
from PIL import Image, ImageOps

from preparation.tests.publication_fixtures import inputs, description, reviewed_image, write_rows
from preparation.tests.test_confirm_images import decision
from preparation.operaters.images import ReuseImageAnnotations, FinalizeVisualMaterials, VISUAL_PROTOCOL
from preparation.operaters.images import ApplyConfirmedImageSelection
from preparation.operaters.inputs import material_record
from preparation.operaters.inputs import duplicate
from demiflow.execution.artifacts import digest


def test_readonly_annotation_snapshot_reuses_and_freezes_missing(tmp_path):
    from preparation.operaters.images import write_curation
    from preparation.tests.conftest import raw_metadata_snapshot
    from project import resolve_root
    def encoded(sha):
        return {'sha256':sha,'descriptions':[{'annotation_id':sha,'config_id':'fixture','status':'done','description':description()}]}
    source=raw_metadata_snapshot(resolve_root(),['a'*64,'b'*64])
    ref = write_curation(resolve_root(),[encoded('a'*64)],source_ref=source)
    reuse = ReuseImageAnnotations(ref.to_dict())
    assert reuse({'sha256':'a'*64})['image_index_status']=='available'
    assert reuse({'sha256':'b'*64})['image_index_status']=='missing'
    write_curation(resolve_root(),[encoded('b'*64)],source_ref=source)
    assert reuse({'sha256':'b'*64})['image_index_status']=='missing'
    assert ReuseImageAnnotations()(reuse({'sha256':'a'*64}))['image_index_status']=='available'


def test_explicit_visual_publication_survives_failed_article(inputs):
    image = reviewed_image(inputs[4][0])
    result = FinalizeVisualMaterials()({'identity':{'target_label':'流程图'},
                                      'material_pack':{'images':[image]}})
    assert len(result['visual_materials']) == 1
    delivered = material_record({'concept':'流程图', 'status':'failed',
        '_knowledge_run':str(inputs[0]), '_knowledge_sha256':'fixture', **result})
    assert len(delivered['materials']) == 1
    assert delivered['materials'][0]['review']['publisher'] == 'PublishVisualMaterials'
    assert delivered['materials'][0]['asset']['description'] == description()['caption']
    legacy = copy.deepcopy(image)
    del legacy['selection_review']['visual_publication']
    assert FinalizeVisualMaterials()({'identity':{'target_label':'流程图'},
        'material_pack':{'images':[legacy]}})['visual_materials'] == []


@pytest.mark.parametrize('bad', ['missing_metadata', 'disagree'])
def test_incomplete_visual_review_cannot_publish(inputs, bad):
    image = reviewed_image(inputs[4][0])
    result = image['selection_review']
    item = copy.deepcopy(result['primary_review'])
    if bad == 'missing_metadata':
        item['image_metadata'] = None
    else:
        item = {**decision('a','pending'), 'image_metadata':description(), 'visual_support':None}
    row = dict(case_id='C', batch_id='B',
        image_prompt={'image_ids':['a'], 'selection_protocol':VISUAL_PROTOCOL, 'metadata_required_ids':['a']},
        pixel_images=['fixture'], pixel_roles=[{'image_id':'a','original_sha256':inputs[4][0]['sha256']}],
        primary_selection={'image_decisions':[result['primary_review']], 'image_selection_calls':[]},
        review_required=True, prompt_result={'images':[item]})
    reviewed = ApplyConfirmedImageSelection()(row)['image_decisions'][0]
    assert reviewed['decision'] == 'pending'
    assert 'visual_publication' not in reviewed


def test_mirrored_resized_target_is_excluded(inputs):
    original = inputs[4][0]
    path = inputs[0] / 'mirror.png'
    with Image.open(original['path']) as im:
        ImageOps.mirror(im).resize((180,160)).save(path)
    variant = {**original,'path':str(path),'sha256':digest(path.read_bytes())}
    assert duplicate(original, variant)


def test_evaluation_does_not_partition_visual_materials(inputs):
    image = reviewed_image(inputs[4][0])
    publication = FinalizeVisualMaterials()({'identity':{'target_label':'流程图'}, 'material_pack':{'images':[image]}})
    path = inputs[0] / 'knowledge_base.jsonl'
    row = {'concept':'流程图', 'status':'failed', **publication}
    write_rows(path,[row])
    out = material_record({**row, '_knowledge_sha256':digest(row)})
    assert len(out['materials']) == 1
    assert out['materials'][0]['asset']['sha256'] == inputs[4][0]['sha256']


def test_v2_nested_limitations_do_not_require_legacy_duplicate():
    from preparation.operaters.images import ApplyImageSelection
    item = {**decision('a','keep'), 'image_metadata':description(),
            'visual_support':{'supports':'判断分支','region':'中央','limitations':'仅此图'}}
    del item['limitations']
    row = {'case_id':'C','pixel_roles':[], 'image_prompt':{'image_ids':['a'],
        'selection_protocol':VISUAL_PROTOCOL,'metadata_required_ids':['a']},
        'prompt_result':{'images':[item]}}
    result = ApplyImageSelection()(row)['image_decisions'][0]
    assert result['protocol_valid'] and result['limitations'] == '仅此图'
    assert 'limitations' not in item  # The recorded model response is not edited.
    item.pop('visual_support')
    assert not ApplyImageSelection()(row)['image_decisions'][0]['protocol_valid']


def test_selected_target_exclusion_removes_dependent_text_but_not_independent_text(inputs):
    from preparation.operaters.inputs import reference_options
    from preparation.operaters.inputs import SplitGuard, retrieve
    text, image = copy.deepcopy(inputs[3])
    image['image_id'] = 'figure'
    dependent = {**text, 'item_id': 'dependent', 'publication': {'visual_dependencies': ['figure']}}
    items = [text, dependent, image]
    options = reference_options(items, [image['asset']])
    assert options['evidence'] == [1]
    selected, _ = retrieve(items, '流程图', SplitGuard(inputs[2]), excluded=[image['asset']])
    assert [m['item_id'] for m in selected] == [text['item_id']]


def test_configured_result_tables_do_not_write_defaults(tmp_path):
    """文章和图片结果各自写指定表，仍按主键合并且返回准确版本。"""
    from preparation.operaters.article import article_entity, write_articles, ARTICLES_URI
    from preparation.operaters.images import write_curation, IMAGES_URI
    from preparation.tests.conftest import raw_metadata_snapshot
    from project import resolve_root
    root = resolve_root()
    articles = 'demiwtg/preparation/datasets/custom_articles.lance'
    images = 'demiwtg/preparation/datasets/custom_images.lance'
    raw = raw_metadata_snapshot(root, ['a' * 64])
    row = article_entity({'concept': '测试', 'status': 'reviewed'}, run_id='fixture')
    ref = write_articles(root, [row], target_uri=articles)
    assert ref.relative_uri == articles and ref.open(root).count_rows() == 1
    assert write_articles(root, [row], target_uri=articles).lance_version == ref.lance_version
    ref = write_curation(root, [{'sha256': 'a' * 64}], source_ref=raw, target_uri=images)
    assert ref.relative_uri == images and ref.open(root).count_rows() == 1
    assert not (root / ARTICLES_URI).exists() and not (root / IMAGES_URI).exists()
