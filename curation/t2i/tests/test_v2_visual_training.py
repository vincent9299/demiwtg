"""Retained material selection and input-binding regressions; no old full graph."""
import copy

import pytest

from preparation.tests.publication_fixtures import inputs, republish_fixture
from preparation.operaters.runfiles import rows
from curation.t2i.tests.fixtures import visual_only_training, material_catalog, design_result, binding_row


def test_target_pool_does_not_promote_raw_article_candidates(inputs):
    from preparation.tests.publication_fixtures import article_publication
    from curation.t2i.tests.fixtures import draft_config
    from curation.t2i.operaters.image_pool import candidate_images
    sources = article_publication(inputs)
    article = list(rows(sources[0]))[0]
    # Even being carried in published_images is insufficient without actual placement.
    article['published_images'].append(article['images'][1])
    article['audit']['selected_image_ids'].append('scene1')
    assert [i['image_id'] for i in candidate_images(article)] == ['figure1']
    source = republish_fixture(sources[0], [article])
    delivered = material_catalog(inputs[0] / 'published_targets', [source], draft_config(inputs[2]))[0]
    assert [a['sha256'] for a in delivered['target_pool']] == [inputs[4][0]['sha256']]
    assert delivered['target_pool'][0]['candidate_publication']['status'] == 'published_article_figure'


def test_visual_only_shared_assets_can_swap_roles_and_exclude_target_from_answer(inputs):
    """A and B remain reference candidates; each can supervise a different task."""
    from curation.t2i.operaters.candidates import ExpandCandidates
    from preparation.operaters.inputs import SplitGuard
    from curation.t2i.operaters.operators import BindTrainingInputs
    ref, cfg = visual_only_training(inputs)
    run = inputs[0] / 'swappable_visuals'
    row = material_catalog(run, [], cfg, visual_runs=[ref])[0]
    row['design_targets'] = row['target_pool']
    from preparation.operaters.inputs import reference_options
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
                         input_materials=option['evidence'],
                         target_support='目标中可见判断节点及两条分支，仅作为测试响应。',
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


def test_training_input_selects_configured_concepts_and_excludes_formal_test_images(inputs):
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
    delivered = material_catalog(run, [], cfg, visual_runs=[ref])
    assert len(delivered) == 1
    assert {r['concept'] for r in delivered if r['authoring_selected']} == {'流程图'}
    for r in delivered:
        assert len(r['materials']) == 1
        assert r['materials'][0]['asset']['sha256'] != inputs[4][0]['sha256']
        assert all(a['sha256'] != inputs[4][0]['sha256'] for a in r.get('target_pool', []))


def test_binding_rejects_changed_materials_or_missing_evidence(inputs):
    from curation.t2i.operaters.operators import BindTrainingInputs
    from preparation.operaters.inputs import SplitGuard
    row = binding_row(inputs)
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


@pytest.mark.parametrize('edit_input', ['task', 'source'])
def test_t2i_binding_rejects_edit_inputs(inputs, edit_input):
    from curation.t2i.operaters.operators import BindTrainingInputs
    from preparation.operaters.inputs import SplitGuard
    row = binding_row(inputs)
    if edit_input == 'task':
        row['plan']['task_type'] = 'edit'
    else:
        row['edit_source'] = inputs[4][0]
    out = BindTrainingInputs(SplitGuard(inputs[2]))(row)
    assert out['status'] == 'invalid_training_inputs'
    assert 'answer_input' not in out
