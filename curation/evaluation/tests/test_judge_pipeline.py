"""Full native graph with explicitly synthetic responses, never real model calls."""
import asyncio
import copy
import json
from pathlib import Path

import pytest

from curation.training.tests.test_authoring import inputs
from curation.evaluation.tests.test_native_evaluation import prepared, complete_rubrics
from curation.preparation.records import read_record, run_records, run_state, prompt_store, read, saved_stage, file_record
from curation.preparation.responses import ingest
from curation.evaluation.native.rubrics import verify_rubrics
from curation.evaluation.native.scores import (
    T2I_DIMS, EDIT_DIMS, PROTOCOLS, normalize_score, failure_score,
    compare_group, aggregate,
)


def synthetic_score(packet, value=1):
    q = packet['question']
    results = []
    for c in packet['rubric']['criteria']:
        row = {k: c[k] for k in ('id', 'dimension', 'check_category', 'importance', 'evidence_ids')}
        row.update(result='met', region='center', reason_code='none')
        row.update({'observation': 'Synthetic visible observation'} if q['task_type'] == 't2i'
                   else {'before': 'Synthetic original', 'after': 'Synthetic edited structure'})
        results.append(row)
    result = {'protocol': PROTOCOLS[q['task_type']], 'validity': {'status': 'ok', 'detail': 'Fixture only'},
              'requirement_results': results, 'confidence': 'high'}
    if q['task_type'] == 't2i':
        result['criterion_refs'] = {}
        for dim, keys in T2I_DIMS.items():
            result[dim] = {key: value for key in keys}
            result[dim + '_reasons'] = {key: 'Synthetic image evidence' for key in keys}
            result['criterion_refs'][dim] = {key: [r['id'] for r in results
                if r['dimension'] == dim and r['check_category'] == key] for key in keys}
    else:
        result.update(edit_type=q['edit_type'], critical_failures=[],
                      observations={k: [] for k in ('explicit_edit', 'necessary_consequences', 'preservation', 'artifacts')})
        result['raw_dimensions'] = [{'dimension': f'd{i+1}', 'label': label, 'tier': value,
            'criterion_ids': [r['id'] for r in results if r['dimension'] == f'd{i+1}'],
            'observation_ids': [], 'reason': 'Synthetic BEFORE/AFTER evidence'}
            for i, label in enumerate(EDIT_DIMS[q['edit_type']])]
    return result


def install_fixture_generation(prepared, asset):
    run, graph, *_ = prepared
    calls = []
    class Actor:
        concurrency = 1
        def __init__(self, run, backend, config): pass
        async def __call__(self, job):
            calls.append(job['job_id'])
            return {**{k: v for k, v in job.items() if k != 'request'}, 'status': 'generated', 'image': asset}
    class Partition:
        concurrency = 1
        def __init__(self, run, config): self.run = run
        async def __call__(self, partition):
            record = await asyncio.to_thread(graph['run_backend'], self.run, partition['backend'], actor_factory=Actor)
            return {**partition, **record}
    graph['RunBackendGraph'] = Partition
    graph['validate_execution_config'] = lambda *args: None
    return calls


def submit_judges(run):
    packets = {key: read_record(entry['record_ref']) for key, entry in verify_rubrics(run).items()}
    for row in saved_stage(run, 'judge_requests'):
        assert row['judge_status'] == 'prepared', row.get('judge_reason')
        request_path = row['judge_binding']['request_ref']
        request = read_record(request_path)
        # Identical native contexts share a content-addressed response safely.
        if offline_response_exists(run, request):
            continue
        raw = run / 'fixture_responses' / (row['candidate_id'] + '.json')
        raw.parent.mkdir(exist_ok=True)
        raw.write_text(json.dumps(synthetic_score(packets[row['task_id']]), ensure_ascii=False))
        ingest(request_path, raw, run, 'gpt-6-astra', 'medium')


def test_full_graph_freezes_then_blind_judges_and_resume_without_generating(prepared, inputs):
    run, graph, questions, upstream, cfg = prepared
    # Missing rubric replies prevent any generation, including direct backend use.
    calls = install_fixture_generation(prepared, inputs[4][2])
    blocked = graph['run_pipeline'](run, questions, upstream, cfg, through='judge')
    assert not calls and 'answers' not in blocked['stages']
    assert {r['rubric_status'] for r in saved_stage(run, 'rubrics')} == {'pending'}
    with pytest.raises(ValueError, match='rubric index missing'):
        graph['run_backend'](run, 'gemini')
    complete_rubrics(prepared)
    original_packets = run_records(run).get('rubrics_index')
    graph['run_pipeline'](run, questions, upstream, cfg, through='judge')
    assert len(calls) == 100
    assert {r['judge_status'] for r in saved_stage(run, 'scores')} == {'pending'}
    requests = saved_stage(run, 'judge_requests')
    for row in requests:
        request = read_record(row['judge_binding']['request_ref'])
        model_text = json.dumps(request['messages'], ensure_ascii=False)
        assert 'secret_rubric' in model_text and 'Test source with full context' in model_text
        assert 'secret_review' not in model_text and 'without_knowledge' not in model_text
        assert 'with_knowledge' not in model_text and row['job_id'] not in model_text
        assert not any(arm in model_text for arm in ('bagel', 'qwen2512', 'gemini'))
        actual = [r['role'] for r in request['image_roles']]
        assert actual[:2] == ['before', 'after'] if row['task_type'] == 'edit' else actual[0] == 'result'
        assert all('gemini' not in r['path'] and 'bagel' not in r['path'] for r in request['image_roles'])
    # Same question, same frozen rubric and evidence, across all five arms.
    for task in {r['task_id'] for r in requests}:
        group = [r for r in requests if r['task_id'] == task]
        assert len({r['judge_packet_sha256'] for r in group}) == 1
    submit_judges(run)
    graph['run_judging'](run)
    assert len(calls) == 100 and run_records(run).get('rubrics_index') == original_packets
    scores = saved_stage(run, 'scores')
    assert len(scores) == 100 and {r['judge_status'] for r in scores} == {'scored'}
    assert len(saved_stage(run, 'evaluation')) == 20
    summary = saved_stage(run, 'summary')[0]
    assert summary['counts']['machine_accepted_questions'] == 8
    assert summary['counts']['machine_rejected_questions'] == 12
    assert all(r['mean_delta'] == 0 for r in summary['paired_deltas'])
    assert all(r['mean_gap_reduction'] == 0 for r in summary['closed_reference_gaps'])
    before = run_state(run)['stages']
    resumed = graph['run_judging'](run)
    assert not resumed['new_stages'] and resumed['stages'] == before and len(calls) == 100
    # Replaying the full graph also uses every existing per-item generation cache.
    graph['run_pipeline'](run, questions, upstream, cfg, through='judge')
    assert len(calls) == 100


@pytest.fixture
def packets(prepared):
    complete_rubrics(prepared)
    run = prepared[0]
    return [read_record(entry['record_ref']) for entry in verify_rubrics(run).values()]


@pytest.mark.parametrize('task_type', ['t2i', 'edit'])
def test_na_explanation_may_cite_own_criterion_without_changing_scores(packets, task_type):
    packet = next(p for p in packets if p['question']['task_type'] == task_type)
    raw = synthetic_score(packet)
    criterion = raw['requirement_results'][0]
    criterion['result'] = 'not_applicable'
    rid, dim, category = criterion['id'], criterion['dimension'], criterion['check_category']
    if task_type == 't2i':
        raw[dim][category] = 'N/A'
        refs = lambda value: value['criterion_refs'][dim][category]
    else:
        refs = lambda value: next(d['criterion_ids'] for d in value['raw_dimensions']
                                  if d['dimension'] == dim)
    minimal = copy.deepcopy(raw)
    refs(minimal).remove(rid)
    original = copy.deepcopy(raw)
    assert normalize_score(raw, packet)['metrics'] == normalize_score(minimal, packet)['metrics']
    assert raw == original  # Original observations, tiers and references remain untouched.
    broken = copy.deepcopy(raw)
    if task_type == 't2i':
        other = next((d, k) for d, keys in T2I_DIMS.items() for k in keys
                     if (d, k) != (dim, category))
        broken['criterion_refs'][other[0]][other[1]].append(rid)
    else:
        next(d for d in broken['raw_dimensions'] if d['dimension'] != dim)['criterion_ids'].append(rid)
    with pytest.raises(ValueError, match='every applicable'):
        normalize_score(broken, packet)


def test_references_nullable_tiers_and_no_edit_clamp(packets):
    t2i = next(p for p in packets if p['question']['task_type'] == 't2i')
    raw = synthetic_score(t2i)
    raw['quality']['material_texture'] = 'N/A'
    assert normalize_score(raw, t2i)['metrics']['quality_score'] == 60
    raw['quality']['resolution'] = None
    assert normalize_score(raw, t2i)['metrics']['quality_score'] is None
    broken = copy.deepcopy(raw)
    broken['requirement_results'] = []
    with pytest.raises(ValueError, match='omitted'):
        normalize_score(broken, t2i)
    broken['requirement_results'] = ['malformed model output']
    with pytest.raises(ValueError, match='objects'):
        normalize_score(broken, t2i)
    broken = copy.deepcopy(raw)
    broken['requirement_results'][0]['dimension'] = 'quality'
    with pytest.raises(ValueError):
        normalize_score(broken, t2i)
    broken = copy.deepcopy(raw)
    broken['criterion_refs']['alignment']['form_structure'] = []
    with pytest.raises(ValueError, match='every applicable'):
        normalize_score(broken, t2i)
    broken = copy.deepcopy(raw)
    broken['requirement_results'][0]['evidence_ids'] = ['invented']
    with pytest.raises(ValueError):
        normalize_score(broken, t2i)
    broken = copy.deepcopy(raw)
    broken['alignment']['form_structure'] = True
    with pytest.raises(ValueError, match='tier'):
        normalize_score(broken, t2i)
    edit = next(p for p in packets if p['question']['task_type'] == 'edit')
    raw = synthetic_score(edit, 2)
    raw['raw_dimensions'][0]['tier'] = 0
    raw['requirement_results'][0]['result'] = 'violated'
    score = normalize_score(raw, edit)
    assert score['metrics'] == {'d1': 0, 'd2': 100, 'd3': 100}
    assert score['core_failure_ids'] == ['R1'] and 'official_total' not in score['metrics']
    raw['raw_dimensions'][1]['tier'] = None
    assert normalize_score(raw, edit)['metrics']['d2'] is None
    raw['observations']['preservation'] = {'id': 'wrong container'}
    with pytest.raises(ValueError, match='lists'):
        normalize_score(raw, edit)


def test_invalid_questions_failures_missing_arms_and_matched_closed_gaps(packets):
    packet = next(p for p in packets if p['question']['task_type'] == 't2i')
    def arm(backend, condition, value):
        return {'backend': backend, 'condition': condition, 'knowledge_mode': 'text_and_images',
                'status': 'generated', 'judge_status': 'scored',
                'score': normalize_score(synthetic_score(packet, value), packet)}
    answers = [arm('bagel', 'without_knowledge', 0), arm('bagel', 'with_knowledge', 1),
               arm('gemini', 'without_knowledge', 2)]
    group = {'task_id': packet['question']['task_id'], 'question': packet['question'],
             'accepted_by_machine': False, 'answers': answers}
    report = compare_group(group)
    assert all(p['delta'] == 60 for p in report['pairs'])
    assert all(p['gap_without'] == 100 and p['gap_with'] == 40 and p['gap_reduction'] == 60
               for p in report['closed_reference_gaps'])
    answers[0]['score'] = failure_score(packet)
    answers[0]['status'] = 'model_failure'
    report = compare_group(group)
    assert report['pairs'][0]['valid'] and report['pairs'][0]['includes_model_failure']
    assert not report['pairs'][1]['valid'] and not report['pairs'][2]['valid']
    summary = aggregate([report])
    assert summary['counts']['generation:model_failure'] == 1
    assert len(summary['excluded_pairs']) == 2
    # Any arm's invalid-question diagnosis excludes that question for every pair.
    answers[-1]['score']['validity'] = 'invalid_question'
    assert all(not p['valid'] for p in compare_group(group)['pairs'])
    # An invalid pre-answer audit cannot silently become valid at scoring time.
    invalid = copy.deepcopy(packet)
    invalid['rubric']['audit']['status'] = 'invalid_question'
    assert normalize_score(synthetic_score(invalid), invalid)['validity'] == 'invalid_question'


def test_source_and_response_binding_tampering_are_rejected(prepared, inputs, packets):
    run, graph, *_ = prepared
    entry = next(iter(run_records(run).get('rubrics_index').values()))
    index = run_records(run).get('rubrics_index')
    original = copy.deepcopy(index)
    next(iter(index.values()))['sha256'] = '0'*64
    run_records(run).put('rubrics_index', index, immutable=False)
    with pytest.raises(ValueError, match='packet changed'): verify_rubrics(run)
    run_records(run).put('rubrics_index', original, immutable=False)
    assert verify_rubrics(run)
    calls = install_fixture_generation(prepared, inputs[4][2])
    graph['run_pipeline'](run, prepared[2], prepared[3], prepared[4], through='judge')
    import shutil
    from project import resolve_root
    sha = inputs[4][2]['sha256']
    shutil.rmtree(resolve_root()/'raw/images.lance')
    with pytest.raises(ValueError, match='asset bytes not found'):
        graph['run_judging'](run)
    assert len(calls) == 100


def test_native_http_uses_frozen_messages_and_resume_never_calls_provider(prepared, inputs, packets, monkeypatch):
    """Loopback fixture, no real model or external HTTP endpoint."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Thread
    captured = []
    by_kind = {kind: next(p for p in packets if p['question']['task_type'] == kind) for kind in ('t2i', 'edit')}
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            captured.append(payload)
            text = json.dumps(payload['messages'], ensure_ascii=False)
            kind = 'edit' if 'v4-edit-result-judge/2-draft' in text else 't2i'
            value = (by_kind[kind]['rubric'] if '作答前逐条核验作者判据并冻结评分归属' in text
                     else synthetic_score(by_kind[kind]))
            raw = json.dumps({'id': 'fixture', 'model': 'fixture-judge',
                'choices': [{'message': {'role': 'assistant', 'content': json.dumps({'result': value}, ensure_ascii=False)},
                             'finish_reason': 'stop'}], 'usage': {'prompt_tokens': 1, 'completion_tokens': 1}}).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        _, graph, questions, upstream, config = prepared
        cfg = copy.deepcopy(config)
        cfg['judge'].update(mode='http', model='fixture-judge', base_url=f'http://127.0.0.1:{server.server_port}/v1',
                            api_key_env='CURATION_FIXTURE_KEY')
        monkeypatch.setenv('CURATION_FIXTURE_KEY', 'fixture-not-a-secret')
        run = prepared[0].parent / 'http_evaluation'
        calls = install_fixture_generation((run, graph, questions, upstream, cfg), inputs[4][2])
        graph['run_pipeline'](run, questions, upstream, cfg, through='judge')
        assert {r['judge_status'] for r in saved_stage(run, 'scores')} == {'scored'}
        frozen_messages = [r['messages'] for k,r in run_records(run).items().items() if k.startswith('request/')]
        assert captured and all(p['messages'] in frozen_messages for p in captured)
        count = len(captured)
        graph['run_judging'](run)
        assert len(captured) == count and len(calls) == 100
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def offline_response_exists(run, request):
    from demiflow.lance.records import LanceRecordStore
    binding = read_record(request['native_offline']['request_ref'])
    return LanceRecordStore(**prompt_store(run)).get('response/'+binding['request_sha256']) is not None
