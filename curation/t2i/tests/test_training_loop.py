"""Retained batch/visit helpers, tested without the removed global driver."""
import copy

from preparation.tests.publication_fixtures import inputs
from curation.t2i.tests.fixtures import visual_only_training, material_catalog
from curation.t2i.operaters.loop import (
    training_targets, training_attempt, material_batches, loop_progress, advance_progress, loop_stop)


def test_failed_page_advances_page_success_advances_target_and_batches_cover_all(inputs):
    ref, cfg = visual_only_training(inputs)
    cfg.update(reference_batch_size=1, training_sample_goal=3)
    run = inputs[0] / 'page_rules'
    catalog = material_catalog(run, [], cfg, visual_runs=[ref])
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
