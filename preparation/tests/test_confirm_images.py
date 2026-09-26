import json
import pytest
from preparation.operaters.runfiles import run_records
from demiflow import data
import pyarrow as pa
from preparation.operaters.images import RecordPrimaryImageSelection, PrepareImageReview, ApplyConfirmedImageSelection
from preparation.operaters.images import review_needed, save_image_filter_policy
from preparation.operaters.images import IMAGE_FILTER_DEFAULTS


def decision(i, d):
    relation = {'keep': 'direct', 'exclude': 'unrelated', 'pending': 'uncertain'}[d]
    return dict(image_id=i, relation=relation, concept_relation={'keep':'target','exclude':'unrelated','pending':'uncertain'}[d],
                decision=d, observability='usable', reason='visible', visible_information='object', limitations=None)


def request():
    return dict(case_id='C', batch_id='B', image_prompt={'image_ids':['a','b','c'], 'selection_protocol':'image-relevance-v2'},
                pixel_images=['pixel-a','pixel-b','pixel-c'], pixel_roles=[],
                prompt_result={'images':[decision('a','keep'),decision('b','exclude'),decision('c','pending')]})


def test_review_is_independent_keeps_batch_and_does_not_rescue_exclusions():
    initial=request();row=PrepareImageReview()(RecordPrimaryImageSelection()(initial))
    assert row['image_prompt']==initial['image_prompt'] and row['pixel_images']==initial['pixel_images']
    assert 'prompt_result' not in row and 'primary_selection' not in row['image_prompt']
    row['prompt_result']={'images':[decision('a','exclude'),decision('b','keep'),decision('c','keep')]}
    out=ApplyConfirmedImageSelection()(row)
    assert [d['decision'] for d in out['image_decisions']]==['pending','exclude','pending']
    assert len(out['image_selection_calls'])==2


def test_invalid_review_never_releases_primary_keep():
    row=PrepareImageReview()(RecordPrimaryImageSelection()(request()))
    row.update(prompt_error='HTTP failure',prompt_result={'images':[decision('a','keep')]})
    d=ApplyConfirmedImageSelection()(row)['image_decisions'][0]
    assert d['decision']=='pending' and not d['protocol_valid']


def test_no_keep_bypasses_review():
    row=request();row['prompt_result']['images'][0]=decision('a','exclude')
    row=PrepareImageReview()(RecordPrimaryImageSelection()(row))
    assert not row['review_required']
    assert len(ApplyConfirmedImageSelection()(row)['image_selection_calls'])==1


def test_checkpoint_and_policy_cannot_silently_reuse_old_selection(tmp_path):
    class Rows:
        def iter_rows(self):raise AssertionError('completed checkpoint should bypass enumeration')
    completed = {'fingerprint': 'old'}
    with pytest.raises(ValueError):review_needed(Rows(),completed,'new')
    assert not review_needed(Rows(),completed,'old')


def test_service_is_untouched_when_checkpoint_complete(monkeypatch,tmp_path):
    from preparation.operaters import images as service
    monkeypatch.setattr(service,'ready',lambda *args:pytest.fail('No service check on cached stage'))
    with service.image_review_service(tmp_path,{},needed=False):pass




def test_external_review_service_not_stopped_on_failure(monkeypatch,tmp_path):
    from preparation.operaters import images as service
    monkeypatch.setattr(service,'ready',lambda *args:True)
    monkeypatch.setattr(service,'stop_owned',lambda *args:pytest.fail('External server must be left alone'))
    with pytest.raises(RuntimeError,match='body failed'):
        with service.image_review_service(tmp_path,IMAGE_FILTER_DEFAULTS):raise RuntimeError('body failed')


def test_neutral_selection_removes_prior_judgment_and_caption(monkeypatch):
    from preparation.operaters import images as multimodal
    monkeypatch.setattr(multimodal, 'pixels', lambda images: (['actual-pixels'], []))
    row = {'case_id':'C', 'concept_ref':'legacy:棒',
           'identity':{'target_label':'棒', 'reason':'previous keep', 'identity_groups':['previous judgment']},
           'available_images':[{'image_id':'a','record':{'caption':'thermometer caption'}}]}
    result = multimodal.BatchImageSelection(4, {'legacy:棒':'实心棒'}, neutral=True)(row)[0]
    assert result['image_prompt']['identity_context'] == {'definition':'实心棒'}
    assert 'metadata' not in result['image_prompt']
    assert 'previous' not in json.dumps(result['image_prompt'])
    assert result['pixel_images'] == ['actual-pixels']
