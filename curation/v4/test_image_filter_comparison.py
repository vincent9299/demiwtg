from .analyze_image_filter import combine, metrics
from .ops.multimodal import ApplyImageSelection
import pytest


def test_disagreement_is_not_majority_certification():
    assert combine('keep', 'exclude', 'agreement') == 'pending'
    assert combine('keep', 'exclude', 'confirm_keep') == 'pending'
    assert combine('exclude', 'keep', 'confirm_keep') == 'exclude'
    assert combine('pending', 'keep', 'pending_cascade') == 'keep'
    # Pending-only cascade cannot catch confident first-model mistakes.
    assert combine('keep', 'exclude', 'pending_cascade') == 'keep'


def test_uncertain_reference_is_not_counted_as_known_negative():
    refs = [{'image_id': 'a', 'reference': 'keep'},
            {'image_id': 'b', 'reference': 'exclude'},
            {'image_id': 'c', 'reference': 'pending'}]
    result = metrics({'a': 'exclude', 'b': 'keep', 'c': 'keep'}, refs)
    assert result['false_exclude'] == result['false_keep'] == 1
    assert result['uncertain_kept'] == 1
    assert result['correct_keep'] == 0


@pytest.mark.parametrize('limitations', ['', None, 'one view'])
def test_optional_limitation_content_does_not_erase_valid_observation(limitations):
    item = {'image_id': 'I', 'relation': 'direct', 'concept_relation': 'target',
            'decision': 'keep', 'observability': 'usable', 'reason': 'target visible',
            'visible_information': 'a hand forming a ring', 'limitations': limitations}
    row = {'case_id': 'c', 'image_prompt': {'image_ids': ['I'], 'selection_protocol': 'image-relevance-v2'},
           'prompt_result': {'images': [item]}, 'pixel_roles': []}
    assert ApplyImageSelection()(row)['image_decisions'][0]['protocol_valid']
    item['visible_information'] = ''
    assert not ApplyImageSelection()(row)['image_decisions'][0]['protocol_valid']
    item['visible_information'] = 'a hand'
    item['concept_relation'] = 'uncertain'
    # Optional limitations cannot bypass contradictory decision/relation checks.
    assert not ApplyImageSelection()(row)['image_decisions'][0]['protocol_valid']
