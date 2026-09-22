"""V2 publication and target-aware authoring: real graphs, synthetic pixels."""
import copy
import json
import sqlite3

import pytest
from PIL import Image, ImageOps

from curation.training.tests.test_authoring import (request_record, inputs, autonomous_inputs, design_result, ingest, write_rows,
                              TASK_CHECKS, TARGET_CHECKS)
from curation.preparation.tests.test_confirm_images import decision
from curation.preparation.ops.visual_materials import (
    ReuseImageAnnotations, PublishVisualMaterials, VISUAL_PROTOCOL)
from curation.preparation.ops.image_filter import (
    RecordPrimaryImageSelection, PrepareImageReview, ApplyConfirmedImageSelection)
from curation.preparation.records import digest, read, rows, saved_stage, run_manifest
from curation.training.tests.pipeline_runtime import load_pipeline
from curation.preparation.published import published_record
from curation.preparation.materials import duplicate


def description():
    return dict(caption='图示有分支', representation='diagram', view_tags=['front'],
                objects=[], text_regions=[], observability_issues=[], uncertainties=[])


def image_record(asset):
    return {'image_id':'a', 'bytes':{'path':asset['path'], 'sha256':asset['sha256']},
            'record':{'url':'test://fixture'}}


def reviewed_image(asset):
    item = {**decision('a', 'keep'), 'image_metadata':description(),
            'visual_support':{'supports':'判断分支', 'region':'中央', 'limitations':'仅此图'}}
    row = dict(case_id='C', batch_id='B',
        image_prompt={'image_ids':['a'], 'selection_protocol':VISUAL_PROTOCOL,
                      'metadata_required_ids':['a']},
        pixel_images=['fixture'], pixel_roles=[{'image_id':'a','original_sha256':asset['sha256']}],
        prompt_result={'images':[item]})
    second = PrepareImageReview()(RecordPrimaryImageSelection()(row))
    second['prompt_result'] = {'images':[item]}
    result = ApplyConfirmedImageSelection()(second)['image_decisions'][0]
    return {**image_record(asset), 'selection_review':result}


def test_readonly_annotation_snapshot_reuses_and_freezes_missing(tmp_path):
    from curation.preparation.images import write_curation
    from curation.preparation.tests.conftest import raw_metadata_snapshot
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
    result = PublishVisualMaterials()({'identity':{'target_label':'流程图'},
                                      'material_pack':{'images':[image]}})
    assert len(result['visual_materials']) == 1
    delivered = published_record({'concept':'流程图', 'status':'failed',
        '_knowledge_run':str(inputs[0]), '_knowledge_sha256':'fixture', **result})
    assert len(delivered['materials']) == 1
    assert delivered['materials'][0]['review']['publisher'] == 'PublishVisualMaterials'
    assert delivered['materials'][0]['asset']['description'] == description()['caption']
    legacy = copy.deepcopy(image)
    del legacy['selection_review']['visual_publication']
    assert PublishVisualMaterials()({'identity':{'target_label':'流程图'},
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


def test_target_aware_graph_freezes_then_reviews_without_answer_leakage(inputs):
    runs, cfg, *_ = autonomous_inputs(inputs)
    cfg['training_design'] = 'target_aware'
    run = inputs[0] / 'target_aware'
    graph = load_pipeline('training')
    graph(run, runs, cfg)
    materials = saved_stage(run, 'attempt_materials')[0]
    target_number = next(o['target_candidate'] for o in materials['target_reference_options'] if o['evidence'] == [1, 2])
    target = materials['design_targets'][target_number - 1]['sha256']
    request = request_record(run, 'design_candidates')
    assert {r['role'] for r in request['image_roles']} == {'reference','target_candidate'}
    design = design_result(inputs[-1])
    design['candidates'][0]['target_candidates'] = [target_number]
    design['candidates'][0]['reference_selection_reason'] = '参考说明符号和分支，任务应用到当前条件。'
    ingest(run,'design_candidates',design)
    graph(run,runs,cfg)
    request = request_record(run, 'review_task')
    assert target not in json.dumps(request)
    ingest(run,'review_task',{'checks':{k:True for k in TASK_CHECKS}, 'reason':'fixture'})
    graph(run,runs,cfg)
    ingest(run,'review_target',{'checks':{k:True for k in TARGET_CHECKS}, 'reason':'fixture'})
    graph(run,runs,cfg)
    ready = saved_stage(run,'ready')[0]
    sample = ready['training_sample']
    assert sample['pipeline_version'] == 'V2'
    assert sample['task_image_roles']['target_design_binding']['target_sha256'] == [target]
    assert [s['sha256'] for s in sample['sequence'] if s['loss']] == [target]
    assert all(s.get('sha256') != target for s in sample['sequence'][:-1])
    assert ready['task_sha256'] == ready['target_search']['task_sha256']
    assert run_manifest(run)['pipeline_version'] == 'V2'


def test_only_target_records_gap_and_stops_at_cycle_budget(inputs):
    runs, cfg, *_ = autonomous_inputs(inputs, missing_target=True)
    cfg['training_design'] = 'target_aware'
    run = inputs[0] / 'no_reference_v2'
    state = load_pipeline('training')(run, runs, cfg)
    assert state['training_progress']['accepted_samples'] == 0
    assert state['training_progress']['stop_reason'] == 'target_cycle_budget_exhausted'
    assert all(r['status'] == 'needs_materials' for r in saved_stage(run, 'incomplete'))
    assert not any(k.startswith('request/') for k in __import__('curation.preparation.records', fromlist=['run_records']).run_records(run).items())


def test_evaluation_does_not_partition_visual_materials(inputs):
    from curation.evaluation.native.operators import DeliverKnowledge
    image = reviewed_image(inputs[4][0])
    publication = PublishVisualMaterials()({'identity':{'target_label':'流程图'}, 'material_pack':{'images':[image]}})
    path = inputs[0] / 'knowledge_base.jsonl'
    row = {'concept':'流程图', 'status':'failed', **publication}
    write_rows(path,[row])
    out = DeliverKnowledge()({**row, '_knowledge_sha256':digest(row)})
    assert len(out['materials']) == 1
    assert out['materials'][0]['asset']['sha256'] == inputs[4][0]['sha256']


def test_v2_nested_limitations_do_not_require_legacy_duplicate():
    from curation.preparation.ops.image_selection import ApplyImageSelection
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


def visual_only_training(inputs):
    from curation.training.tests.test_authoring import publish_fixture
    from curation.training.tests.pipeline_runtime import config
    visuals = []
    for n, asset in enumerate(inputs[4][:2]):
        image = reviewed_image(asset)
        image['image_id'] = f'visual{n}'
        result = PublishVisualMaterials()({'identity': {'target_label': '流程图'},
                                          'material_pack': {'images': [image]}})
        visuals.extend(result['visual_materials'])
    row = {'concept': '流程图', 'status': 'reviewed', 'publication_kind': 'visual_materials',
           'knowledge': [], 'visual_materials': visuals, 'images': [v['image'] for v in visuals]}
    ref = publish_fixture(inputs[0] / 'visual_only_source', 'knowledge_base', [row])
    cfg = config('offline', inputs[2], max_units=None, scene_search={'external_providers': []})
    return ref, cfg


def test_visual_only_shared_assets_can_swap_roles_and_exclude_target_from_answer(inputs):
    """A and B remain reference candidates; each can supervise a different task."""
    from curation.training.candidates import ExpandCandidates
    from curation.preparation.materials import SplitGuard
    from curation.training.operators import BindTrainingInputs
    ref, cfg = visual_only_training(inputs)
    run = inputs[0] / 'swappable_visuals'
    load_pipeline('training')(run, [], cfg, visual_runs=[ref], through='training_materials')
    row = saved_stage(run, 'training_materials')[0]
    row['design_targets'] = row['target_pool']
    from curation.preparation.materials import reference_options
    row['target_reference_options'] = [dict(target_candidate=n, **reference_options(row['materials'], [a]))
                                       for n, a in enumerate(row['design_targets'], 1)]
    assert len(row['materials']) == len(row['design_targets']) == 2
    assert all(m['kind'] == 'image' for m in row['materials'])
    guard = SplitGuard(inputs[2])
    for option in row['target_reference_options']:
        assert len(option['evidence']) == 1
        target_n = option['target_candidate']
        target = row['design_targets'][target_n - 1]
        assert target['candidate_publication']['status'] == 'reviewed_visual'
        design = design_result(copy.deepcopy(inputs[-1]))
        candidate = design['candidates'][0]
        candidate.update(target_candidates=[target_n], evidence=option['evidence'],
                         reference_selection_reason='视觉参考支持分支形态，需应用到当前条件。')
        candidate['draft']['criteria'][0]['evidence'] = option['evidence']
        out = list(ExpandCandidates(guard, cfg)({**row, 'status': 'candidates_designed',
                                               'candidate_design': design}))[0]
        assert out['status'] == 'constructed'
        assert all(m['asset']['sha256'] != target['sha256'] for m in out['materials'])
        out.update(status='accepted_task', criteria=[{'knowledge_ids': [m['item_id'] for m in out['materials']]}])
        answered = BindTrainingInputs(guard)(out)
        assert answered['answer_materials']
        assert all(m['asset']['sha256'] != target['sha256'] for m in answered['answer_materials'])
        assert answered['answer_materials'] == out['materials']
        assert 'answer_retrieval' not in answered
        # A model ignoring the compatibility list cannot create a valid task.
        bad_n = next(n for n, m in enumerate(row['materials'], 1) if m['asset']['sha256'] == target['sha256'])
        candidate['evidence'] = [bad_n]
        candidate['draft']['criteria'][0]['evidence'] = [bad_n]
        rejected = list(ExpandCandidates(guard, cfg)({**row, 'status': 'candidates_designed',
                                                    'candidate_design': design}))[0]
        assert rejected['status'] == 'invalid_candidate'
        assert 'duplicates a selected target' in str(rejected['issues'])


def test_selected_target_exclusion_removes_dependent_text_but_not_independent_text(inputs):
    from curation.preparation.materials import reference_options
    from curation.preparation.materials import SplitGuard, retrieve
    text, image = copy.deepcopy(inputs[3])
    image['image_id'] = 'figure'
    dependent = {**text, 'item_id': 'dependent', 'publication': {'visual_dependencies': ['figure']}}
    items = [text, dependent, image]
    options = reference_options(items, [image['asset']])
    assert options['evidence'] == [1]
    selected, _ = retrieve(items, '流程图', SplitGuard(inputs[2]), excluded=[image['asset']])
    assert [m['item_id'] for m in selected] == [text['item_id']]


def test_training_input_preserves_unsampled_visuals_and_formal_test_exclusion(inputs):
    from curation.training.tests.test_authoring import republish_fixture
    ref, cfg = visual_only_training(inputs)
    row = list(rows(ref))[0]
    other = copy.deepcopy(row)
    other['concept'] = '另一概念'
    for visual in other['visual_materials']:
        visual['concept'] = other['concept']
    ref = republish_fixture(ref, [row, other])
    cfg['concepts'] = ['流程图']
    cfg['split_registry']['formal_test']['images'] = [inputs[4][0]]
    run = inputs[0] / 'scope_and_test'
    load_pipeline('training')(run, [], cfg, visual_runs=[ref], through='training_materials')
    delivered = saved_stage(run, 'training_materials')
    assert len(delivered) == 2
    assert {r['concept'] for r in delivered if r['authoring_selected']} == {'流程图'}
    for r in delivered:
        assert len(r['materials']) == 1
        assert r['materials'][0]['asset']['sha256'] != inputs[4][0]['sha256']
        assert all(a['sha256'] != inputs[4][0]['sha256'] for a in r.get('target_pool', []))
    assert 'target_pool' not in next(r for r in delivered if not r['authoring_selected'])


def test_training_exports_exact_selected_references_without_retrieval(inputs, monkeypatch):
    from curation.training.tests.test_authoring import complete
    from curation.benchmark import operators
    def forbidden(*args, **kwargs):
        raise AssertionError('Training must not retrieve or replace the selected references')
    monkeypatch.setattr(operators, 'retrieve_public', forbidden)
    run, *_ = complete(inputs)
    selected = saved_stage(run, 'candidates')[0]
    ready = saved_stage(run, 'ready')[0]
    assert ready['answer_materials'] == selected['materials']
    assert ready['training_input_binding'] == selected['training_input_binding']
    seq = ready['training_sample']['sequence']
    assert [s['sha256'] for s in seq if s['type'] == 'image' and not s['loss']] == selected['training_input_binding']['reference_sha256']
    assert [s['text'] for s in seq if s['role'] == 'knowledge'] == [m['text'] for m in selected['materials'] if m['kind'] == 'text']


def test_binding_rejects_changed_materials_or_missing_evidence(inputs):
    from curation.training.tests.test_authoring import complete
    from curation.training.operators import BindTrainingInputs
    from curation.preparation.materials import SplitGuard
    run, *_ = complete(inputs)
    row = saved_stage(run, 'review')[0]
    bind = BindTrainingInputs(SplitGuard(inputs[2]))
    changed = copy.deepcopy(row)
    changed['materials'][0]['text'] += '改写后的知识'
    assert bind(changed)['status'] == 'invalid_training_inputs'
    missing = copy.deepcopy(row)
    missing['criteria'][0]['knowledge_ids'].append('unselected_evidence')
    assert bind(missing)['status'] == 'invalid_training_inputs'
    original = bind(row)
    assert original['status'] == 'accepted_task'
    assert original['answer_materials'] == row['materials']
