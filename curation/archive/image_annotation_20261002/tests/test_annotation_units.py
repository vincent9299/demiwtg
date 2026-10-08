import pytest
from demiflow.objects import ObjectRef
from preparation.images.annotation.image_annotation_pipeline import config
from preparation.images.annotation.operaters.images import encode_pixels
from preparation.images.annotation.operaters.merge import merge_by_id
from preparation.images.annotation.operaters.schema import semantic_config_id, validate_image_response
from preparation.images.annotation.prompts import annotation_prompt_pack
from preparation.images.annotation.tests.conftest import response


def test_retired_concept_configuration_is_not_an_alias():
    with pytest.raises(TypeError):
        config(run='test', image_source={'uri': 'images.lance', 'version': 1}, annotation_parts='all')
    with pytest.raises(TypeError):
        config(run='test', image_source={'uri': 'images.lance', 'version': 1}, concept_source={})


def test_only_neutral_prompt_and_schedule_independent_identity():
    values = config(run='one', image_source={'uri': 'images.lance', 'version': 1})
    pack, _ = annotation_prompt_pack(values)
    assert list(pack.prompt_definitions) == ['describe_image']
    changed = {**values, 'run': 'other', 'image_concurrency': 1}
    assert semantic_config_id(pack, values) == semantic_config_id(pack, changed)
    for name, value in [('max_edge', 768), ('jpeg_quality', 80), ('model_revision', 'new'), ('temperature', .1)]:
        assert semantic_config_id(pack, values) != semantic_config_id(pack, {**values, name: value})


@pytest.mark.parametrize('score', [True, -1, 11, '5', 5.0, None])
def test_invalid_richness_is_not_a_business_score(score):
    with pytest.raises(ValueError):
        validate_image_response({**response(), 'richness': score})


def test_same_id_conflict_and_old_provenance_preservation():
    a = {'annotation_id': 'a', 'value': 3, 'provenance': {'run': 'old'}}
    assert merge_by_id([a], [{**a, 'provenance': {'run': 'new'}}], field='descriptions') == [a]
    with pytest.raises(ValueError, match='conflicting'):
        merge_by_id([a], [{**a, 'value': 4}], field='descriptions')


def test_shared_pixel_interface_remains_deterministic(lake):
    row = lake['rows'][0]
    raw = ObjectRef(row['image_uri'], row['sha256']).read()
    a = encode_pixels(raw, max_edge=1536, jpeg_quality=90)
    assert a == encode_pixels(raw, max_edge=1536, jpeg_quality=90)
    assert a[0].startswith('data:image/jpeg;base64,') and len(a[1]) == 64
