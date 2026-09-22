import json
import pytest
from curation.preparation.records import run_records
from demiflow.standalone import local_data
import pyarrow as pa
from curation.preparation.ops.image_filter import RecordPrimaryImageSelection, PrepareImageReview, ApplyConfirmedImageSelection
from curation.preparation.image_filter_runtime import review_needed, validate_material_reuse, save_image_filter_policy
from curation.preparation.ops.image_filter import IMAGE_FILTER_DEFAULTS


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
    tmp_path=tmp_path/'curation/preparation/runs/review'
    p=tmp_path/'review.lance'
    local_data().from_items([]).checkpoint_lance(str(p), schema=pa.schema([('id',pa.string())]), fingerprint='old').take_all()
    with pytest.raises(ValueError):review_needed(Rows(),p,'new')
    assert not review_needed(Rows(),p,'old')
    from curation.preparation.ops.prompt_config import material_prompt_pack
    cfg={**IMAGE_FILTER_DEFAULTS,'model':'qwen3.8-27b','base_url':'http://127.0.0.1:8000/v1'}
    with pytest.raises(ValueError):validate_material_reuse(tmp_path,cfg)
    save_image_filter_policy(tmp_path,cfg)
    with pytest.raises(ValueError,match='text selection'):validate_material_reuse(tmp_path,cfg)
    _,text=material_prompt_pack(cfg)
    run_records(tmp_path/'knowledge').put('prompt_config', {'yaml':text})
    validate_material_reuse(tmp_path,cfg)
    with pytest.raises(ValueError):validate_material_reuse(tmp_path,{**cfg,'image_identity_definitions':{'x':'new'}})
    with pytest.raises(ValueError,match='Text selection'):validate_material_reuse(tmp_path,{**cfg,'relevance_only':False})


def test_service_is_untouched_when_checkpoint_complete(monkeypatch,tmp_path):
    from curation.preparation import local_review_service as service
    monkeypatch.setattr(service,'ready',lambda *args:pytest.fail('No service check on cached stage'))
    with service.image_review_service(tmp_path,{},needed=False):pass


def test_changed_image_prompt_rejects_selected_materials_but_allows_text_reuse(tmp_path):
    import yaml
    from curation.preparation.image_filter_runtime import validate_text_selection_reuse
    from curation.preparation.ops.prompt_config import material_prompt_pack
    cfg={**IMAGE_FILTER_DEFAULTS,'model':'qwen3.8-27b','base_url':'http://127.0.0.1:8000/v1'}
    tmp_path=tmp_path/'curation/preparation/runs/review'
    save_image_filter_policy(tmp_path,cfg)
    _,text=material_prompt_pack(cfg)
    snapshot=run_records(tmp_path/'knowledge')
    snapshot.put('prompt_config', {'yaml':text})
    validate_material_reuse(tmp_path,cfg)
    old=yaml.safe_load(text)
    old['prompts']['select_images']['template']='Previous, less strict image identity policy'
    snapshot.put('prompt_config', {'yaml':yaml.safe_dump(old)}, immutable=False)
    validate_text_selection_reuse(tmp_path,cfg)
    with pytest.raises(ValueError,match='Image selection prompt'):
        validate_material_reuse(tmp_path,cfg)


def test_external_review_service_not_stopped_on_failure(monkeypatch,tmp_path):
    from curation.preparation import local_review_service as service
    monkeypatch.setattr(service,'ready',lambda *args:True)
    monkeypatch.setattr(service,'stop_owned',lambda *args:pytest.fail('External server must be left alone'))
    with pytest.raises(RuntimeError,match='body failed'):
        with service.image_review_service(tmp_path,IMAGE_FILTER_DEFAULTS):raise RuntimeError('body failed')


def test_neutral_selection_removes_prior_judgment_and_caption(monkeypatch):
    from curation.preparation.ops import image_selection as multimodal
    monkeypatch.setattr(multimodal, 'pixels', lambda images: (['actual-pixels'], []))
    row = {'case_id':'C', 'concept_ref':'legacy:棒',
           'identity':{'target_label':'棒', 'reason':'previous keep', 'identity_groups':['previous judgment']},
           'available_images':[{'image_id':'a','record':{'caption':'thermometer caption'}}]}
    result = multimodal.BatchImageSelection(4, {'legacy:棒':'实心棒'}, neutral=True)(row)[0]
    assert result['image_prompt']['identity_context'] == {'definition':'实心棒'}
    assert 'metadata' not in result['image_prompt']
    assert 'previous' not in json.dumps(result['image_prompt'])
    assert result['pixel_images'] == ['actual-pixels']
