"""Real Dataset/Lance/offline-transport integration; verdicts are synthetic fixtures."""
import copy
from pathlib import Path
import json

import pytest

from demiflow.lance.blobs import BlobRef
from demiflow.lance.refs import DatasetRef
from demiflow.operator_llm.lance_journal import submit_response
from demiflow import data
from preparation.operaters.inputs import iter_material_rows
from preparation.operaters.runfiles import run_records, read_record
from preparation.tests.publication_fixtures import inputs, republish_fixture
from project import resolve_root
from curation.t2i.t2i_train_pipeline import config, run_pipeline
from curation.t2i.operaters.review import CHECKS
from curation.t2i.operaters.results import sample_messages, TRAINING_SAMPLES, decode_audit
from curation.t2i.tests.fixtures import visual_only_training


def sources_for(inputs, concepts=('流程图',)):
    source, _ = visual_only_training(inputs)
    rows, _ = iter_material_rows(source)
    template = next(rows)
    rows = []
    for concept in concepts:
        row = copy.deepcopy(template)
        row['concept'] = concept
        for visual in row['visual_materials']:
            visual['concept'] = concept
        rows.append(row)
    return [republish_fixture(source, rows)]


def test_config_and_notebook_have_no_execution_side_effects(tmp_path, monkeypatch):
    import ast
    import nbformat
    from pathlib import Path
    NOTEBOOK = Path(__file__).resolve().parents[1] / 't2i_train_debug.ipynb'
    monkeypatch.setenv('DEMIWTG_DATASETS_ROOT', str(tmp_path / 'empty'))
    note = nbformat.read(NOTEBOOK, as_version=4)
    nbformat.validate(note)
    assert len(note.cells) == 1
    compile(note.cells[0].source, str(NOTEBOOK), 'exec', flags=ast.PyCF_ALLOW_TOP_LEVEL_AWAIT)
    assert not (tmp_path / 'empty').exists()
    assert config()['samples_per_concept'] == 5
    for kwargs in ({'samples_per_concept': 0}, {'max_target_cycles': True}, {'concepts': 'A'}):
        with pytest.raises(ValueError):
            config(**kwargs)


def fixture_response(stage, task_id, *, accept=True):
    if stage == 'design_candidates':
        return {'status': 'ok', 'candidates': [{
            'task_type': 't2i', 'learning_objective': 'FIXTURE_LEARNING_ONLY',
            'evidence': [1], 'input_materials': [],
            'reference_selection_reason': 'Fixture has instruction only',
            'knowledge_gap': 'Fixture signal', 'knowledge_application': 'FIXTURE_HIDDEN_APPLICATION',
            'target_candidates': [1], 'target_support': 'Fixture claims visible signal; not real review',
            'draft': {'status': 'ok', 'instruction': 'Synthetic instruction ' + task_id,
                      'condition': '', 'edit_type': None, 'anchor': '', 'preserve': [],
                      'criteria': [{'requirement': 'FIXTURE_HIDDEN_CRITERION', 'evidence': [1],
                                    'observable_region': 'centre', 'allowed_variation': 'colour'}]}}]}
    return {'checks': {name: accept for name in CHECKS},
            'criteria': [{'criterion': 1, 'satisfied': accept, 'observation': 'Synthetic test verdict only'}],
            'reason': 'Synthetic test review; not semantic evidence'}


def submit_request(request, *, accept=True, with_reference=False):
    ref = request['native_offline']['request_ref']
    native = read_record(ref)
    response = fixture_response(request['stage'], request['task_id'], accept=accept)
    if with_reference and request['stage'] == 'design_candidates':
        response['candidates'][0]['input_materials'] = [1]
    submit_response(resolve_root(), ref,
        json.dumps({'result': response}),
        model=native['model'], metadata={'reviewer': 'unit_test_fixture', 'reviewer_kind': 'assistant'})


def auto_responses(monkeypatch, *, accept=True, seen=None, with_reference=False):
    from curation.t2i.operaters import prompting
    original = prompting.prepare_prompt
    def prepare(row, stage, required_status, run, pack):
        out = original(row, stage, required_status, run, pack)
        if stage + '_binding' not in out:
            return out
        request = read_record(out[stage + '_binding']['request_ref'])
        submit_request(request, accept=accept, with_reference=with_reference)
        if seen is not None:
            seen.append((row['concept'], stage, row['task_id']))
        return out
    monkeypatch.setattr(prompting, 'prepare_prompt', prepare)


def setup_run(inputs, monkeypatch, concepts=('流程图',)):
    from curation.t2i.operaters import runfiles
    monkeypatch.setattr(runfiles, 'source_snapshot', lambda: {'test_code': 'fixture'})
    run = inputs[0].parents[2] / 'curation/t2i/runs/integration'
    return run, sources_for(inputs, concepts), run_pipeline


def stage_rows(state, name):
    ref = DatasetRef.from_dict(state['stages'][name]['dataset_ref'])
    return data.read_lance(ref.resolve(resolve_root()), version=ref.lance_version).map(decode_audit).take_all()


def sample_rows(state):
    ref = DatasetRef.from_dict(state['stages']['training_samples']['dataset_ref'])
    return ref, ref.open(resolve_root()).to_table().to_pylist()


@pytest.mark.parametrize('custom_target', [False, True])
def test_per_concept_limit_five_stops_calls_and_replays_fixed_lance(inputs, monkeypatch, custom_target):
    run, sources, pipeline = setup_run(inputs, monkeypatch, ('流程图', '概念B'))
    seen = []
    auto_responses(monkeypatch, seen=seen)
    target = str(resolve_root() / 'demiwtg/curation/t2i/datasets/chosen.lance') if custom_target else None
    cfg = config(max_target_cycles=4)  # two targets per concept; eight visits available
    state = pipeline(run, [], cfg, visual_sources=sources, target_uri=target)
    ref, samples = sample_rows(state)
    if custom_target:
        assert ref.resolve(resolve_root()) == target
    assert len(samples) == 10
    assert len(seen) == 20  # design+review; no speculative sixth visit
    assert {c: sum(s['concept'] == c for s in samples) for c in ('流程图', '概念B')} == {'流程图': 5, '概念B': 5}
    summaries = stage_rows(state, 'concepts')
    assert all(s['status'] == 'sample_limit_reached' and s['attempts'] == 5 for s in summaries)
    assert not stage_rows(state, 'incomplete')
    assert ref.open(resolve_root()).schema == TRAINING_SAMPLES
    for sample in samples:
        assert sample_messages(sample) == [{'role': 'user', 'content': [{'type': 'text', 'text': sample['instruction']}]}]
        assert 'FIXTURE_HIDDEN' not in json.dumps(sample)
        assert BlobRef(**sample['target']['blob_ref']).read(resolve_root())
        audit = read_record(sample['audit_ref'])
        assert audit['review_sample']['checks']['grounded'] is True
        assert audit['target_design_binding']['target_support']
    replay = pipeline(run, [], cfg, visual_sources=sources, target_uri=target)
    assert sample_rows(replay)[0] == ref
    assert len(seen) == 20
    with pytest.raises(ValueError, match='Immutable record differs'):
        pipeline(run, [], config(samples_per_concept=4, max_target_cycles=4), visual_sources=sources, target_uri=target)


def test_offline_wait_resume_and_exhaustion_delivers_actual_count(inputs, monkeypatch):
    run, sources, pipeline = setup_run(inputs, monkeypatch)
    cfg = config(max_target_cycles=1)
    first = pipeline(run, [], cfg, visual_sources=sources)
    assert sample_rows(first)[0].row_count == 0
    assert stage_rows(first, 'concepts')[0]['status'] == 'pending_design_candidates'
    requests = run_records(run).items(prefix='request/design_candidates/')
    assert len(requests) == 1
    submit_request(next(iter(requests.values())))
    second = pipeline(run, [], cfg, visual_sources=sources)
    assert sample_rows(second)[0].row_count == 0
    assert stage_rows(second, 'concepts')[0]['status'] == 'pending_review_sample'
    submit_request(next(iter(run_records(run).items(prefix='request/review_sample/').values())))
    third = pipeline(run, [], cfg, visual_sources=sources)
    _, partial = sample_rows(third)
    assert len(partial) == 1
    assert stage_rows(third, 'concepts')[0]['status'] == 'pending_design_candidates'
    for request in run_records(run).items(prefix='request/design_candidates/').values():
        submit_request(request)
    auto_responses(monkeypatch)
    final = pipeline(run, [], cfg, visual_sources=sources)
    _, samples = sample_rows(final)
    assert len(samples) == 2
    assert samples[0] == partial[0]
    assert stage_rows(final, 'concepts')[0]['status'] == 'target_cycle_budget_exhausted'
    # The old partial snapshot remains unchanged and independently readable.
    assert sample_rows(third)[1] == partial


def test_rejected_pixels_never_enter_sample_table(inputs, monkeypatch):
    run, sources, pipeline = setup_run(inputs, monkeypatch)
    auto_responses(monkeypatch, accept=False)
    state = pipeline(run, [], config(max_target_cycles=1), visual_sources=sources)
    assert sample_rows(state)[0].row_count == 0
    assert [r['status'] for r in stage_rows(state, 'incomplete')] == ['rejected_sample', 'rejected_sample']


def test_training_output_append_and_overwrite(inputs, monkeypatch):
    """使用真实写表与模拟响应验证目标模式，重复完成运行不追加第二遍。"""
    run, sources, pipeline = setup_run(inputs, monkeypatch)
    auto_responses(monkeypatch)
    cfg = config(samples_per_concept=1, max_target_cycles=1)
    target = str(resolve_root() / 'demiwtg/curation/t2i/datasets/shared_samples.lance')
    for name, mode, expected in [('first', 'append', 1), ('next', 'append', 2), ('replace', 'overwrite', 1)]:
        current = run.with_name(name)
        state = pipeline(current, [], cfg, visual_sources=sources, target_uri=target, write_mode=mode)
        ref, samples = sample_rows(state)
        assert len(samples) == expected
        resumed = pipeline(current, [], cfg, visual_sources=sources, target_uri=target, write_mode=mode)
        assert sample_rows(resumed)[0] == ref
        with pytest.raises(ValueError, match='Immutable record differs'):
            pipeline(current, [], cfg, visual_sources=sources, target_uri=target,
                     write_mode='overwrite' if mode == 'append' else 'append')


def test_failed_writer_is_not_registered_or_reused_as_a_completed_stage(inputs, monkeypatch):
    import lance
    from demiflow.errors import LanceWriteError
    run, sources, pipeline = setup_run(inputs, monkeypatch)
    original = lance.write_dataset

    def fail_design_commit(data, uri, **kwargs):
        if Path(uri).name.startswith('design__'):
            # 标准 writer 一次写入；模拟已落表但尚未登记完成的异常提交。
            original(data, uri, **kwargs)
            raise RuntimeError('Simulated writer interruption')
        return original(data, uri, **kwargs)

    monkeypatch.setattr(lance, 'write_dataset', fail_design_commit)
    cfg = config(samples_per_concept=1)
    with pytest.raises(LanceWriteError, match='Simulated writer interruption') as failure:
        pipeline(run, [], cfg, visual_sources=sources)
    assert failure.value.receipt.status == 'indeterminate'
    assert run_records(run).get('latest') is None
    monkeypatch.setattr(lance, 'write_dataset', original)
    with pytest.raises(ValueError, match='Unfinished stage table'):
        pipeline(run, [], cfg, visual_sources=sources)


def test_reference_pixels_round_trip_and_review_requires_full_unchanged_sample(inputs, monkeypatch):
    from curation.t2i.operaters.prompting import review_inputs
    from curation.t2i.operaters.operators import training_content
    from curation.t2i.operaters.review import accept_review
    run, sources, pipeline = setup_run(inputs, monkeypatch)
    auto_responses(monkeypatch, with_reference=True)
    state = pipeline(run, [], config(samples_per_concept=1), visual_sources=sources)
    sample = sample_rows(state)[1][0]
    row = stage_rows(state, 'attempts')[0]
    expected, _ = training_content(row['draft']['instruction'], row['answer_materials'])
    assert sample_messages(sample)[0]['content'] == expected
    assert [p['type'] for p in sample['input_content']] == ['text', 'text', 'image_blob']
    assert sample['input_content'][-1]['blob_ref']['sha256'] != sample['target']['blob_ref']['sha256']
    values, roles = review_inputs(row)
    assert len(values['images']) == 2  # same evidence/reference image sent once, plus target
    assert [r['role'] for r in roles[0]['uses']] == ['construction_evidence', 'answer_reference']
    for change in ('missing_criterion', 'duplicate_criterion', 'uncertain', 'changed_instruction', 'changed_target'):
        altered = copy.deepcopy(row)
        altered['status'] = 'sample_reviewed'
        if change == 'missing_criterion':
            altered['review_sample']['criteria'] = []
        elif change == 'duplicate_criterion':
            altered['review_sample']['criteria'] *= 2
        elif change == 'uncertain':
            altered['review_sample']['criteria'][0]['satisfied'] = False
        elif change == 'changed_instruction':
            altered['draft']['instruction'] += ' altered'
        else:
            altered['design_targets'][0]['sha256'] = '0' * 64
        assert accept_review(altered)['status'] in {'invalid_review', 'rejected_sample'}


def test_waiting_concept_does_not_prevent_other_concept_delivery(inputs, monkeypatch):
    from curation.t2i.operaters import prompting
    run, sources, pipeline = setup_run(inputs, monkeypatch, ('流程图', '概念B'))
    original = prompting.prepare_prompt
    def prepare(row, stage, required_status, run, pack):
        out = original(row, stage, required_status, run, pack)
        if row['concept'] == '概念B':
            submit_request(read_record(out[stage + '_binding']['request_ref']))
        return out
    monkeypatch.setattr(prompting, 'prepare_prompt', prepare)
    state = pipeline(run, [], config(max_target_cycles=1), visual_sources=sources)
    assert [r['concept'] for r in sample_rows(state)[1]] == ['概念B', '概念B']
    summaries = {r['concept']: r for r in stage_rows(state, 'concepts')}
    assert summaries['流程图']['status'] == 'pending_design_candidates'
    assert summaries['流程图']['attempts'] == 1
    assert summaries['概念B']['accepted_samples'] == 2


def test_current_curated_entity_source_and_raw_version_pin(inputs, monkeypatch):
    from preparation.operaters.inputs import resolve_source
    from tools.lake_migration.visual_publication import publish_visual_records
    from PIL import Image
    run, _, pipeline = setup_run(inputs, monkeypatch)
    raw = resolve_source(resolve_root(), 'legacy_images')[0]
    rows = [{'concept': '流程图', 'image_id': 'V' + str(n), 'sha256': a['sha256'], 'path': a['path'],
             'format': 'PNG', 'publication_status': 'reviewed',
             'concept_review': {'decision': 'keep', 'reason': 'Fixture only'},
             'visual_support': {'supports': 'Fixture geometry', 'region': 'whole image'},
             'source': {'content_url': 'test://fixture'}, 'image_metadata': {}}
            for n, a in enumerate(inputs[4][:2])]
    ref = publish_visual_records(resolve_root(), rows, 'fixture-visual', source_ref=raw)
    sources = [{'dataset_ref': ref.to_dict(), 'release_id': 'fixture-visual'}]
    auto_responses(monkeypatch)
    cfg = config(samples_per_concept=1)
    state = pipeline(run, [], cfg, visual_sources=sources)
    table, samples = sample_rows(state)
    assert len(samples) == 1
    assert samples[0]['target']['blob_ref']['version'] == raw.lance_version
    Image.new('RGB', (13, 17), 'red').save(inputs[0] / 'later-raw-arrival.png')
    assert resolve_source(resolve_root(), 'legacy_images')[0].lance_version > raw.lance_version
    assert sample_rows(pipeline(run, [], cfg, visual_sources=sources))[0] == table


def test_same_instruction_different_targets_counts_but_exact_rows_do_not(inputs, monkeypatch):
    run, sources, pipeline = setup_run(inputs, monkeypatch)
    original = fixture_response
    def repeated(stage, task_id, **kwargs):
        return original(stage, 'same_instruction', **kwargs)
    monkeypatch.setattr(__import__(__name__, fromlist=['fixture_response']), 'fixture_response', repeated)
    auto_responses(monkeypatch)
    state = pipeline(run, [], config(max_target_cycles=2), visual_sources=sources)
    samples = sample_rows(state)[1]
    assert len(samples) == 2
    assert samples[0]['instruction'] == samples[1]['instruction']
    assert samples[0]['sample_id'] != samples[1]['sample_id']
    assert [r['status'] for r in stage_rows(state, 'incomplete')] == ['duplicate_sample', 'duplicate_sample']


def test_concept_sampling_precedes_picture_delivery(inputs, monkeypatch):
    """max_units 之外的概念不触发图片读取；抽样不只是事后丢弃已处理的行。"""
    import curation.t2i.t2i_train_pipeline as pipeline
    from curation.t2i.operaters.materials import design_concepts
    run, sources, _ = setup_run(inputs, monkeypatch, ('流程图', '概念B'))
    cfg = config(max_units=1)
    upstream, _ = iter_material_rows(sources[0])
    material_rows = list(upstream)
    selected = design_concepts(material_rows, cfg)
    for row in material_rows:
        if row['concept'] not in selected:
            for visual in row['visual_materials']:
                visual['image']['unselected_fixture'] = True
    source = republish_fixture(sources[0], material_rows)
    read_asset = pipeline.source_asset
    calls = []
    def checked_read(image):
        assert not image.get('unselected_fixture'), 'Read pixels for an unselected concept'
        calls.append(image['bytes']['sha256'])
        return read_asset(image)
    monkeypatch.setattr(pipeline, 'source_asset', checked_read)
    state = pipeline.run_pipeline(run, [], cfg, visual_sources=[source], through='materials')
    assert {row['concept'] for row in stage_rows(state, 'materials')} == selected
    assert calls
