"""Completed partitions use the exact judge bindings later consumed by the full graph."""
from pathlib import Path
import json

from preparation.tests.publication_fixtures import inputs
from evaluation.tests.test_native_evaluation import prepared, complete_rubrics
from evaluation.tests.test_judge_pipeline import synthetic_score, install_fixture_generation
from preparation.operaters.runfiles import read_record, run_records, run_state, prompt_store, saved_stage
from demiflow.execution.artifacts import read
from preparation.prompts.responses import ingest
from evaluation.evaluation_pipeline import run_partition_judging
from evaluation.operaters.rubrics import verify_rubrics


def test_prompt_configuration_resume_across_working_directories(tmp_path, monkeypatch):
    from evaluation.operaters.contracts import default_config
    from evaluation.operaters.prompting import prompt_config
    monkeypatch.chdir(tmp_path)
    run = tmp_path / 'evaluation/runs/relative-run'
    _, first = prompt_config('evaluation/runs/relative-run', default_config())
    elsewhere = tmp_path / 'notebook-cwd'
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    _, resumed = prompt_config(run, default_config())
    assert first == resumed
    assert Path(first['offline_store']['root']).is_absolute()


def test_partial_judging_then_full_graph_reuses_scores_and_generation(prepared, inputs, tmp_path):
    run, graph, questions, upstream, config = prepared
    complete_rubrics(prepared)
    generated = []
    class Actor:
        concurrency = 1
        def __init__(self, *args): pass
        async def __call__(self, job):
            generated.append(job['job_id'])
            return {**{k: v for k, v in job.items() if k != 'request'},
                    'status': 'generated', 'image': inputs[4][2]}
    graph.run_backend(run, 'gemini', actor_factory=Actor)
    partial = run_partition_judging
    partial(run, 'gemini')
    folder = run / 'partition_evaluations/gemini'
    assert 'answers' not in run_state(run)['stages']
    assert {r['judge_status'] for r in saved_stage(folder, 'scores')} == {'pending'}
    packets = {k: read_record(v['record_ref']) for k, v in verify_rubrics(run).items()}
    for row in saved_stage(folder, 'judge_requests'):
        request = read_record(row['judge_binding']['request_ref'])
        if offline_response_exists(run, request): continue
        raw = tmp_path / (row['candidate_id'] + '.json')
        raw.write_text(json.dumps(synthetic_score(packets[row['task_id']]), ensure_ascii=False))
        ingest(row['judge_binding']['request_ref'], raw, run, 'gpt-6-astra', 'medium')
    partial(run, 'gemini')
    scores = {r['job_id']: r for r in saved_stage(folder, 'scores')}
    assert len(scores) == 20 and {r['judge_status'] for r in scores.values()} == {'scored'}
    assert not partial(run, 'gemini')['new_stages']
    calls = install_fixture_generation(prepared, inputs[4][2])
    graph.run_pipeline(run, questions, upstream, config, through='judge')
    assert len(generated) == 20 and len(calls) == 80
    final = {r['job_id']: r for r in saved_stage(run, 'scores') if r['backend'] == 'gemini'}
    for key, row in scores.items():
        assert final[key]['judge_binding'] == row['judge_binding']
        assert final[key]['score'] == row['score']


def offline_response_exists(run, request):
    from demiflow.lance.records import LanceRecordStore
    binding = read_record(request['native_offline']['request_ref'])
    return LanceRecordStore(**prompt_store(run)).get('response/'+binding['request_sha256']) is not None
