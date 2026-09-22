"""Knowledge-first authoring boundaries: real graph, synthetic evidence only."""
import copy
import json

import pytest

from curation.training.tests.test_authoring import inputs, autonomous_inputs, ingest, write_rows, start, complete, design_result, republish_fixture, request_record
from curation.preparation import records as storage
from curation.training.candidates import ExpandCandidates
from curation.preparation.materials import SplitGuard
from curation.training.prompting import request_for
from curation.training.tests.pipeline_runtime import load_pipeline
from curation.preparation.records import saved_stage, read


def test_final_failed_article_cannot_export_intermediate_keep_images(inputs):
    runs, cfg, *_ = autonomous_inputs(inputs, 'benchmark')
    row = list(storage.rows(runs[0]))[0]
    row['status'] = 'failed'
    runs[0] = republish_fixture(runs[0], [row])
    run = inputs[0] / 'failed_publication'
    load_pipeline('benchmark')(run, runs, cfg)
    assert saved_stage(run, 'knowledge')[0]['status'] == 'needs_materials'
    assert saved_stage(run, 'incomplete') == []
    assert not (run / 'requests/design_candidates').exists()


def test_only_final_selected_illustrations_are_references(inputs):
    runs, cfg, *_ = autonomous_inputs(inputs, 'benchmark')
    row = list(storage.rows(runs[0]))[0]
    row['audit']['selected_image_ids'] = []
    runs[0] = republish_fixture(runs[0], [row])
    run = inputs[0] / 'no_selected_figure'
    load_pipeline('benchmark')(run, runs, cfg, through='knowledge')
    delivered = saved_stage(run, 'knowledge')[0]
    assert [m['kind'] for m in delivered['materials']] == ['text']
    assert any('final selection' in str(x) for x in delivered['delivery_issues'])


def test_no_handwritten_intent_target_or_hidden_plan_in_author_requests(inputs):
    run, graph, runs, _, cfg = start(inputs)
    assert 'plans' not in storage.run_manifest(run)
    request = request_record(run, 'design_candidates')
    original = list(storage.rows(runs[0]))[0]
    for image in original['images']:
        if image['image_id'] not in original['audit']['selected_image_ids']:
            assert image['bytes']['sha256'] not in json.dumps(request)
    assert {i['role'] for i in request['image_roles']} == {'reference'}
    assert 'Test source with full context' not in json.dumps(request)  # final knowledge only in design
    ingest(run, 'design_candidates', design_result(inputs[-1]))
    graph(run, runs, cfg)
    row = saved_stage(run, 'candidates')[0]
    assert row['plan']['origin'] == 'pipeline.design_candidates' and 'intent' not in row['plan']
    assert not (run / 'requests/construct').exists()


def test_insufficient_design_retains_reason_and_stops(inputs):
    run, graph, runs, _, cfg = start(inputs)
    ingest(run, 'design_candidates', {'status': 'insufficient', 'reason': 'Cannot establish visible consequence'})
    graph(run, runs, cfg)
    assert saved_stage(run, 'incomplete')[0]['status'] == 'no_valid_candidates'
    assert not (run / 'requests/review_task').exists()


@pytest.mark.parametrize('mutation', ['bad_evidence', 'edit_without_query', 'answer_quota', 'draft_outside_evidence'])
def test_invalid_candidates_fail_before_search_or_review(inputs, mutation):
    run, graph, runs, _, cfg = start(inputs)
    design = design_result(inputs[-1])
    item = design['candidates'][0]
    if mutation == 'bad_evidence': item['evidence'] = [999]
    elif mutation == 'edit_without_query': item.update(task_type='edit', draft=None)
    elif mutation == 'answer_quota': item['combo_type'] = 'old'
    else: item['draft']['criteria'][0]['evidence'] = [999]
    ingest(run, 'design_candidates', design)
    graph(run, runs, cfg)
    assert saved_stage(run, 'incomplete')[0]['status'] == 'invalid_candidate'
    assert not (run / 'observed_assets').exists()



def test_both_branches_share_publication_version_and_select_independently(inputs):
    runs, cfg, *_ = autonomous_inputs(inputs)
    for branch in ('benchmark', 'training'):
        load_pipeline(branch)(inputs[0] / ('shared_' + branch), runs, cfg,
                              through='training_materials' if branch == 'training' else 'knowledge')
    b = saved_stage(inputs[0] / 'shared_benchmark', 'knowledge')[0]
    t = saved_stage(inputs[0] / 'shared_training', 'training_materials')[0]
    assert b['knowledge_version'] == t['knowledge_version']
    assert b['unit_id'] != t['unit_id']


def test_sampling_budget_keeps_unexplored_concepts_in_knowledge(inputs):
    runs, cfg, *_ = autonomous_inputs(inputs, 'benchmark')
    path = runs[0]
    row = list(storage.rows(path))[0]
    other = copy.deepcopy(row); other['concept'] = '另一概念'
    runs[0] = republish_fixture(path, [row, other])
    cfg['max_units'] = 1
    run = inputs[0] / 'sampling'
    load_pipeline('benchmark')(run, runs, cfg)
    assert len(saved_stage(run, 'knowledge')) == 2
    assert len(saved_stage(run, 'design')) == 1
    assert len(list(storage.run_records(run).items(prefix='request/design_candidates/').values())) == 1


def test_training_with_rejected_target_stays_incomplete(inputs):
    run, graph, runs, _, cfg = start(inputs)
    from curation.training.operators import TASK_CHECKS, TARGET_CHECKS
    ingest(run, 'design_candidates', design_result(inputs[-1]))
    graph(run, runs, cfg)
    ingest(run, 'review_task', {'checks': {k: True for k in TASK_CHECKS}, 'reason': 'fixture'})
    graph(run, runs, cfg)
    ingest(run, 'review_target', {'checks': {k: k != 'instruction_satisfied' for k in TARGET_CHECKS}, 'reason': 'Wrong target'})
    graph(run, runs, cfg)
    assert saved_stage(run, 'ready') == []
    assert saved_stage(run, 'incomplete')[0]['status'] == 'rejected_target'


def test_formal_concept_reservation_blocks_discovery_for_training(inputs):
    runs, cfg, *_ = autonomous_inputs(inputs)
    registry = read(inputs[2])
    registry['formal_test']['concepts'] = ['流程图']
    inputs[2].write_text(json.dumps(registry))
    cfg['split_registry'] = registry
    run = inputs[0] / 'test_reservation'
    load_pipeline('training')(run, runs, cfg)
    assert saved_stage(run, 'training_materials')[0]['status'] == 'needs_materials'
    assert not (run / 'requests/design_candidates').exists()


def designed_row(inputs):
    run, _, _, _, cfg = start(inputs)
    row = copy.deepcopy(saved_stage(run, 'design')[0])
    design = design_result(copy.deepcopy(inputs[-1]))
    design['candidates'][0]['evidence'] = [1]
    row.update(status='candidates_designed', candidate_design=design)
    return row, cfg


def test_required_figure_travels_with_text_even_if_model_cites_only_text(inputs):
    row, cfg = designed_row(inputs)
    seed = list(ExpandCandidates(SplitGuard(inputs[2]), cfg)(row))[0]
    assert [m['kind'] for m in seed['materials']] == ['text', 'image']
    assert seed['focus']['evidence'] == [1, 2]


def test_oversize_package_is_not_silently_split_or_truncated(inputs):
    runs, cfg, *_ = autonomous_inputs(inputs, 'benchmark')
    cfg['max_context_chars'] = 100
    run = inputs[0] / 'oversize'
    load_pipeline('benchmark')(run, runs, cfg)
    assert saved_stage(run, 'incomplete')[0]['status'] == 'needs_context_budget'
    assert len(saved_stage(run, 'knowledge')[0]['materials']) == 2
    assert not (run / 'requests/design_candidates').exists()


def test_formal_family_reservation_uses_bound_visual_dependencies(inputs):
    row, cfg = designed_row(inputs)
    seed = list(ExpandCandidates(SplitGuard(inputs[2]), cfg)(row))[0]
    registry = read(inputs[2]); registry['formal_test']['rule_families'] = [seed['plan']['rule_family']]
    inputs[2].write_text(json.dumps(registry))
    assert list(ExpandCandidates(SplitGuard(inputs[2]), cfg)(row))[0]['status'] == 'invalid_candidate'


def test_invalid_candidate_does_not_discard_valid_sibling(inputs):
    row, cfg = designed_row(inputs)
    cfg['tasks_per_unit'] = 2
    row['design_policy']['max_candidates'] = 2
    bad = copy.deepcopy(row['candidate_design']['candidates'][0]); bad['evidence'] = [999]
    row['candidate_design']['candidates'].append(bad)
    assert [r['status'] for r in ExpandCandidates(SplitGuard(inputs[2]), cfg)(row)] == ['constructed', 'invalid_candidate']


def test_conflicting_publications_do_not_silently_share_one_task_id(inputs):
    runs, cfg, *_ = autonomous_inputs(inputs)
    path = runs[0]
    row = list(storage.rows(path))[0]
    other = copy.deepcopy(row); other['knowledge'][0]['title'] = 'conflicting'
    runs[0] = republish_fixture(path, [row, other])
    with pytest.raises(ValueError, match='Conflicting article publications'):
        load_pipeline('training')(inputs[0] / 'duplicate_publication', runs, cfg)


def test_null_target_caption_is_a_ranking_gap_not_a_pipeline_crash(inputs):
    from curation.training.authoring import SelectTargetCandidate
    run, *_ = complete(inputs)
    row = copy.deepcopy(saved_stage(run, 'targets')[0])
    row['status'] = 'accepted_task'
    for asset in row['target_pool']:
        asset['description'] = None
    result = SelectTargetCandidate(SplitGuard(inputs[2]))(row)
    assert result['status'] == 'target_attached'
    assert all(r['score'] == 0 for r in result['target_search']['ranks'])


def test_scene_near_duplicates_are_retained_as_possible_edit_pairs(inputs):
    from curation.training.authoring import DeliverPublishedKnowledge
    from curation.preparation.materials import pixels, duplicate
    from PIL import Image
    runs, cfg, *_ = autonomous_inputs(inputs)
    row = list(storage.rows(runs[0]))[0]
    original = copy.deepcopy(row['images'][-1])
    changed = copy.deepcopy(original)
    path = inputs[0] / 'one_pixel_changed.png'
    with Image.open(original['bytes']['path']) as image:
        rgb = image.convert('RGB')
        r, g, b = rgb.getpixel((0, 0))
        rgb.putpixel((0, 0), ((r+1)%256, g, b))
        rgb.save(path)
    changed['bytes'].update(path=str(path), sha256=storage.digest(path.read_bytes()))
    changed['image_id'] = 'near_scene'
    row['images'] = [original, changed]
    runs[0] = republish_fixture(runs[0], [row])
    delivery = DeliverPublishedKnowledge('fixture')({**row, '_candidate_specs': [runs[0]], '_knowledge_sha256': 'fixture'})
    assert 'asset_candidates' not in delivery
    runs[0] = republish_fixture(runs[0], [row])
    from curation.training.scene_assets import ExistingImagePool
    pool = ExistingImagePool(inputs[0] / 'pool', SplitGuard(inputs[2]), cfg)
    candidates, trace = pool.load({**delivery, 'branch': 'benchmark'}, 'edit_source')
    assert len(candidates) == 2
    assert duplicate(*candidates)


def test_t2i_authoring_never_opens_raw_scene_pool(inputs, monkeypatch):
    from curation.benchmark import scene_search
    def forbidden(*args, **kwargs):
        raise AssertionError('T2I authoring must not open raw scene image bytes')
    monkeypatch.setattr(scene_search.SceneSearch, 'local', forbidden)
    monkeypatch.setattr(scene_search.SceneSearch, 'external', forbidden)
    run, *_ = complete(inputs, 'benchmark')
    assert len(saved_stage(run, 'ready')) == 1
    assert not (run / 'observed_assets').exists()
    for stage in ['knowledge', 'design', 'candidates']:
        row = saved_stage(run, stage)[0]
        assert not {'asset_candidates', 'source_candidates', 'target_pool'} & row.keys()


def test_concept_selection_limits_authors_not_the_retrieval_catalog(inputs):
    runs, cfg, *_ = autonomous_inputs(inputs, 'benchmark')
    path = runs[0]
    original = list(storage.rows(path))[0]
    other = copy.deepcopy(original)
    other['concept'] = '另一概念'
    runs[0] = republish_fixture(path, [original, other])
    cfg.update(concepts=[original['concept']], max_units=2)
    run = inputs[0] / 'concept_scope'
    load_pipeline('benchmark')(run, runs, cfg, through='design')
    assert {r['concept'] for r in saved_stage(run, 'knowledge')} == {original['concept'], '另一概念'}
    assert {r['concept'] for r in saved_stage(run, 'design')} == {original['concept']}
    requests = list(storage.run_records(run).items(prefix='request/design_candidates/').values())
    assert len(requests) == 1


def test_bound_input_still_requires_independent_target_review(inputs):
    from curation.training.authoring import SelectTargetCandidate
    from curation.training.operators import accept_target, export_record, TARGET_CHECKS
    run, *_ = complete(inputs)
    row = copy.deepcopy(saved_stage(run, 'targets')[0])
    row.update(status='accepted_task')
    proposed = SelectTargetCandidate(SplitGuard(inputs[2]))(row)
    assert proposed['status'] == 'target_attached'
    assert proposed['answer_materials'] == proposed['materials']
    assert export_record(proposed)['export_ready'] is False
    rejected = accept_target({**proposed, 'status': 'target_reviewed',
        'review_target': {'checks': {k: k != 'all_criteria_satisfied' for k in TARGET_CHECKS},
                          'reason': 'Synthetic fixture: core criterion not satisfied'}})
    assert rejected['status'] == 'rejected_target' and export_record(rejected)['export_ready'] is False
    accepted = accept_target({**proposed, 'status': 'target_reviewed',
        'review_target': {'checks': {k: True for k in TARGET_CHECKS},
                          'reason': 'Synthetic fixture: target satisfies the full frozen instruction'}})
    assert export_record(accepted)['export_ready'] is True


def test_multiple_target_proposals_preserve_task_and_require_individual_review(inputs):
    from curation.training.authoring import SelectTargetCandidate
    from curation.training.operators import export_record
    run, *_ = complete(inputs)
    row = copy.deepcopy(saved_stage(run, 'targets')[0])
    row['status'] = 'accepted_task'
    row['target_pool'].append({**inputs[4][2], 'description': ''})
    alternatives = list(SelectTargetCandidate(SplitGuard(inputs[2]), limit=2).expand(row))
    assert len(alternatives) == 2
    assert len({x['task_id'] for x in alternatives}) == len({x['target']['sha256'] for x in alternatives}) == 2
    for x in alternatives:
        assert x['source_task_id'] == row['task_id']
        assert x['draft'] == row['draft'] and x['criteria'] == row['criteria']
        assert x['task_sha256'] == row['task_sha256']
        assert x['status'] == 'target_attached' and not export_record(x)['export_ready']
