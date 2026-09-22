"""Target rotation, sample counting and continuation in the real notebook chain."""
import copy
from curation.training.tests.test_authoring import inputs, design_result, TASK_CHECKS, TARGET_CHECKS
from curation.training.tests.test_v2_visual_training import visual_only_training
from curation.training.tests.pipeline_runtime import load_pipeline
from curation.preparation.records import saved_stage, read_record, run_records
from curation.training.loop import (
    training_targets, training_attempt, material_batches, loop_progress, advance_progress, loop_stop)


def submit_pending(run, row, result):
    from demiflow.operator_llm.lance_journal import submit_response
    from project import resolve_root
    stage = row['status'].removeprefix('pending_')
    request = read_record(row[stage + '_binding']['request_ref'])
    native = request['native_offline']['request_ref']
    submit_response(resolve_root(), native, {'result': result}, model=read_record(native)['model'],
                    metadata={'reviewer': 'fixture_author', 'reviewer_kind': 'assistant'})


def test_actual_loop_counts_samples_rotates_reuses_targets_and_resumes(inputs):
    ref, cfg = visual_only_training(inputs)
    cfg.update(training_sample_goal=3, max_target_cycles=2, reference_batch_size=1)
    run = inputs[0] / 'three_samples_two_targets'
    graph = load_pipeline('training')
    # At most nine model requests: design / task review / target review per sample.
    for _ in range(10):
        state = graph(run, [], cfg, visual_runs=[ref])
        if state['training_progress']['stop_reason'] == 'sample_goal_reached':
            break
        pending = [r for r in saved_stage(run, 'incomplete') if r['status'].startswith('pending_')]
        assert len(pending) == 1
        row = pending[0]
        if row['status'] == 'pending_design_candidates':
            evidence = list(range(1, len(row['materials']) + 1))
            draft = copy.deepcopy(inputs[-1])
            draft['criteria'][0]['evidence'] = evidence
            # The same instruction is deliberately valid for every sample.
            result = design_result(draft)
            result['candidates'][0].update(evidence=evidence, target_candidates=[1],
                                          reference_selection_reason='fixture visual support')
        else:
            checks = TASK_CHECKS if row['status'] == 'pending_review_task' else TARGET_CHECKS
            result = {'checks': {k: True for k in checks}, 'reason': 'fixture only'}
        submit_pending(run, row, result)
    else:
        raise AssertionError('Loop did not reach the requested sample count')
    ready = saved_stage(run, 'ready')
    assert len(ready) == state['training_progress']['accepted_samples'] == 3
    shas = [r['target']['sha256'] for r in ready]
    assert shas[0] != shas[1] and shas[2] == shas[0]
    assert len({r['draft']['instruction'] for r in ready}) == 1
    assert len({r['training_sample']['sample_id'] for r in ready}) == 3
    requests_before = run_records(run).items(prefix='request/')
    assert len(requests_before) == 9
    resumed = graph(run, [], cfg, visual_runs=[ref])
    assert resumed['new_stages'] == []
    assert saved_stage(run, 'ready') == ready
    assert run_records(run).items(prefix='request/') == requests_before


def test_failed_page_advances_page_success_advances_target_and_batches_cover_all(inputs):
    ref, cfg = visual_only_training(inputs)
    cfg.update(reference_batch_size=1, training_sample_goal=3)
    run = inputs[0] / 'page_rules'
    load_pipeline('training')(run, [], cfg, visual_runs=[ref], through='training_materials')
    catalog = saved_stage(run, 'training_materials')
    targets = training_targets(catalog, cfg)
    row, target = targets[0]
    # Text is explicit independent evidence; pagination must not rank it away.
    row['materials'] += [{**copy.deepcopy(inputs[3][0]), 'item_id': f'other_text_{n}',
                           'text': 'unrelated indexing words ' * 150} for n in range(5)]
    cfg['max_context_chars'] = 10000
    batches = material_batches(row, target, cfg, 0)
    assert len(batches) > 1
    assert {m['item_id'] for b in batches for m in b} >= {f'other_text_{n}' for n in range(5)}
    progress = loop_progress()
    attempted = training_attempt(targets, progress, cfg)
    failed = {**attempted, 'export_ready': False}
    following = advance_progress(progress, failed, len(targets))
    assert following['batch_index'] == 1 and following['target_index'] == 0
    assert following['accepted_samples'] == 0
    success = {**training_attempt(targets, following, cfg), 'export_ready': True}
    following = advance_progress(following, success, len(targets))
    assert following['batch_index'] == 0 and following['target_index'] == 1
    assert following['accepted_samples'] == 1
    assert not loop_stop(following, len(targets), cfg)


def test_empty_target_pool_commits_zero_samples(inputs):
    ref, cfg = visual_only_training(inputs)
    cfg['split_registry']['formal_test']['concepts'] = ['流程图']
    run = inputs[0] / 'empty_targets'
    result = load_pipeline('training')(run, [], cfg, visual_runs=[ref])
    assert result['training_progress']['stop_reason'] == 'no_eligible_targets'
    assert saved_stage(run, 'ready') == []
