"""Native graph regressions on synthetic data only; no GPU, HTTP or model calls."""
import copy
import json
from pathlib import Path

import nbformat
import pytest
from demiflow import data

from preparation.tests.publication_fixtures import inputs, article_publication, write_rows, publish_fixture
from demiflow.execution.artifacts import read, digest, immutable
from preparation.operaters.runfiles import saved_stage, read_record, run_records, rows, run_state
from evaluation.operaters.requests import verify_request
from evaluation.operaters import contracts
from evaluation.operaters.contracts import default_config
from evaluation import evaluation_pipeline as graph
from evaluation.operaters.operators import normalize_question, record_value, group_answers, verify_result
from evaluation.operaters.adapters import GenerateImage, validate_execution_config


@pytest.fixture
def prepared(inputs, monkeypatch):
    for name in ('RunBackendGraph', 'validate_execution_config'):
        monkeypatch.setattr(graph, name, getattr(graph, name))
    base, _, _, _, assets, plan, draft = inputs
    upstream = article_publication(inputs, 'benchmark')
    monkeypatch.setattr(contracts, 'ROOT', base.parents[2])
    monkeypatch.setattr(contracts, 'implementation', lambda: {'fixture': 1})
    cases = []
    for n in range(20):
        kind = 't2i' if n < 10 else 'edit'
        cases.append({'task_id': 'same-id', '_run': str(base / f'author{n}'),
                      'concept': '流程图', 'plan': {**plan, 'task_type': kind},
                      'draft': {**draft, 'edit_type': 'replace' if kind == 'edit' else None,
                                'criteria': [{'requirement': 'secret_rubric', 'knowledge_ids': ['oracle_only']}]},
                      'criteria': [{'requirement': 'secret_rubric', 'knowledge_ids': ['oracle_only'],
                                    'observable_region': 'center', 'allowed_variation': 'fixture variations'}],
                      'materials': inputs[3],
                      'status': 'accepted_task' if n < 8 else 'rejected_task', 'export_ready': n < 8,
                      'review_task': {'reason': 'secret_review'},
                      'edit_source': assets[1] if kind == 'edit' else None})
    questions = publish_fixture(base / 'questions_input', 'selected', [{**r,'number':i+1} for i,r in enumerate(cases)])
    run = base / 'answering'
    cfg = default_config()
    # Explicit historical coverage; new default runs only the Qwen pair.
    cfg['backends'] = {'t2i': ['bagel', 'qwen2512', 'gemini'],
                       'edit': ['bagel', 'qwen2511', 'gemini']}
    graph.run_pipeline(run, questions, upstream, cfg)
    return run, graph, questions, upstream, cfg


def complete_rubrics(prepared, *, target_uri=None, write_mode='overwrite'):
    """Submit labeled synthetic model responses through the real native transport."""
    from demiflow.operator_llm.lance_journal import submit_response
    from project import resolve_root
    run, graph, questions, upstream, cfg = prepared
    for row in saved_stage(run, 'rubric_requests'):
        assert row['rubric_status'] == 'prepared', row.get('rubric_reason')
        request = read_record(row['rubric_binding']['request_ref'])
        native = request['native_offline']
        kind = row['question']['task_type']
        result = {'status': 'ready', 'audit': {'status': 'ok', 'detail': 'Synthetic fixture only'},
                  'criteria': [{'id': 'R1', 'source_criterion_ids': ['A1'],
                      'dimension': 'alignment' if kind == 't2i' else 'd1',
                      'check_category': 'form_structure' if kind == 't2i' else '目标结构',
                      'requirement': 'secret_rubric', 'importance': 'core', 'basis': 'explicit',
                      'evidence_ids': [], 'condition': 'fixture condition',
                      'observable_region': 'center', 'visibility_required': True,
                      'allowed_variation': 'fixture variations'}],
                  'author_dispositions': [{'id': 'A1', 'decision': 'retained',
                                            'criterion_ids': ['R1'], 'reason': 'Fixture mapping'}]}
        if True:
            submit_response(resolve_root(), native['request_ref'], {'result': result},
                            model=cfg['judge']['model'], metadata={'reviewer': 'fixture_only',
                            'reviewer_kind': 'assistant', 'reasoning_effort': cfg['judge']['reasoning_effort']})
    graph.run_pipeline(run, questions, upstream, cfg, through='rubrics', target_uri=target_uri, write_mode=write_mode)
    assert all(r['rubric_status'] == 'frozen' for r in saved_stage(run, 'rubrics'))


def test_twenty_drafts_five_arms_blindness_roles_and_resume(prepared):
    run, graph, questions, upstream, cfg = prepared
    normalized = saved_stage(run, 'questions')
    jobs = saved_stage(run, 'jobs')
    assert len(normalized) == 20 and len({r['question']['task_id'] for r in normalized}) == 20
    assert sum(r['accepted_by_machine'] for r in normalized) == 8
    assert len(jobs) == 100 and len({r['job_id'] for r in jobs}) == 100
    assert {r['backend']: r['jobs'] for r in saved_stage(run, 'partitions')} == {
        'bagel': 40, 'gemini': 20, 'qwen2511': 20, 'qwen2512': 20}
    for job in jobs:
        verify_request(job)
        prompt = json.dumps(job['request'])
        assert 'secret_rubric' not in prompt and 'secret_review' not in prompt and 'oracle_only' not in prompt
        if job['task_type'] == 'edit':
            assert job['request']['image_roles'][0]['role'] == 'edit_source'
        if job['backend'] == 'gemini':
            assert job['condition'] == 'without_knowledge'
        if job['backend'] == 'qwen2512':
            assert not job['request']['image_roles']
            if job['condition'] == 'with_knowledge':
                assert job['actual_modalities'] == ['text'] and job['omitted_image_ids']
        if job['condition'] == 'without_knowledge':
            assert not job['knowledge_ids']
        assert job['retrieval_gap'] is True
    before = run_state(run)['stages']
    again = graph.run_pipeline(run, questions, upstream, cfg)
    assert not again['new_stages'] and before == again['stages']
    assert not (run / 'generations').exists()


def test_empty_retrieval_is_not_disguised_as_knowledge(prepared, monkeypatch):
    run, _, questions, upstream, cfg = prepared
    monkeypatch.setattr(graph, 'RetrieveKnowledge', lambda *args: lambda row: {
        **row, 'materials': [], 'retrieval_gap': True, 'missing_criterion_knowledge_ids': []})
    empty_run = run.with_name('empty_retrieval')
    graph.run_pipeline(empty_run, questions, upstream, cfg)
    jobs = saved_stage(empty_run, 'jobs')
    enhanced = [job for job in jobs if job['condition'] == 'with_knowledge']
    assert enhanced and all(job['status'] == 'skipped_missing_knowledge' for job in enhanced)


def test_backend_native_cache_and_grouping(prepared):
    run, graph, *_ = prepared
    complete_rubrics(prepared)
    calls = []
    class FixtureActor:
        concurrency = 1
        queue_depth = 1
        def __init__(self, run, backend, config): pass
        async def __call__(self, job):
            calls.append(job['job_id'])
            return {**{k:v for k,v in job.items() if k != 'request'}, 'status': 'fixture_only'}
    records = [graph.run_backend(run, b, actor_factory=FixtureActor)
               for b in ['bagel', 'qwen2511', 'qwen2512', 'gemini']]
    assert len(calls) == 100
    # A committed fixed-version stage replays without running actors.
    graph.run_backend(run, 'bagel', actor_factory=FixtureActor)
    assert len(calls) == 100
    combined = (data.from_iter(lambda: (row for ref in records for row in rows(ref['dataset_ref'])))
                .reduce_by_key('task_id', group_answers).take_all())
    assert len(combined) == 20 and all(len(r['answers']) == 5 for r in combined)


def test_single_call_cache_and_uncertain_attempt(prepared, monkeypatch):
    run, *_ = prepared
    jobs = saved_stage(run, 'jobs')
    job = next(j for j in jobs if j['backend'] == 'gemini')
    actor = GenerateImage(run, 'gemini', default_config())
    calls = []
    def fake_gemini(job, directory, result):
        from PIL import Image
        import io
        calls.append(job['job_id'])
        buffer = io.BytesIO()
        Image.new('RGB', (32, 32), 'blue').save(buffer, format='PNG')
        return buffer.getvalue()
    monkeypatch.setattr(actor, 'gemini', fake_gemini)
    one = actor.generate_one(job)
    assert one['status'] == 'generated'
    assert actor.generate_one(job) == one and len(calls) == 1
    unknown = next(j for j in jobs if j['backend'] == 'gemini' and j['job_id'] != job['job_id'])
    run_records(run).put('generation/' + unknown['job_id'] + '/attempt', {'job':unknown})
    assert actor.generate_one(unknown)['status'] == 'interrupted' and len(calls) == 1
    broken = copy.deepcopy(one)
    broken['image']['sha256'] = '0'*64
    with pytest.raises(ValueError, match='identity'):
        verify_result(broken)


def test_no_implicit_gpu_allocation_and_frozen_input_changes(prepared):
    run, graph, questions, upstream, cfg = prepared
    import sys
    execution_cfg = copy.deepcopy(cfg)
    execution_cfg['python']['bagel'] = sys.executable
    with pytest.raises(ValueError, match='Explicitly assign'):
        validate_execution_config(execution_cfg, ['bagel'])
    questions = {**questions, 'row_count':questions['row_count']+1}
    with pytest.raises(ValueError):
        graph.run_pipeline(run, questions, upstream, cfg)


def test_parent_graph_collects_all_backend_results(prepared):
    import asyncio
    run, graph, questions, upstream, cfg = prepared
    complete_rubrics(prepared)
    class FixtureActor:
        concurrency = 1
        def __init__(self, run, backend, config): pass
        async def __call__(self, job):
            return {**{k: v for k, v in job.items() if k != 'request'}, 'status': 'fixture_only'}
    class FixturePartition:
        concurrency = 1
        def __init__(self, run, config): self.run = run
        async def __call__(self, partition):
            record = await asyncio.to_thread(graph.run_backend, self.run,
                                            partition['backend'], actor_factory=FixtureActor)
            return {**partition, **record}
    graph.RunBackendGraph = FixturePartition
    graph.validate_execution_config = lambda *args: None
    result = graph.run_pipeline(run, questions, upstream, cfg, through='generate')
    assert 'comparison' in result['stages']
    groups = saved_stage(run, 'comparison')
    assert len(groups) == 20 and all(len(g['answers']) == 5 for g in groups)
    assert not (run / 'generations').exists()


def test_single_debug_notebook_keeps_graph_and_readonly_stage_views():
    root = Path(__file__).resolve().parents[2]
    whole = nbformat.read(root / 'evaluation/evaluation_debug.ipynb', as_version=4)
    assert not (root / 'evaluation/stepbystep.ipynb').exists()
    for book in (whole,):
        nbformat.validate(book)
        for cell in book.cells:
            if cell.cell_type == 'code': compile(cell.source, '<notebook>', 'exec')
            assert 'text/html' not in json.dumps(cell.get('outputs', []))
        assert not any('def run_pipeline(' in c.source for c in book.cells)
    source = Path(graph.__file__).read_text()
    assert '.map_async(' in source and '.write_lance(' in source
    assert 'checkpoint_lance' not in source and 'lance_checkpoint' not in source
    assert 'run_session' not in source and 'run_worker' not in source
    import ast
    function = next(n for n in ast.parse(source).body if isinstance(n, ast.FunctionDef) and n.name == 'run_pipeline')
    main_source = ast.get_source_segment(source, function)
    assert '.write_lance(' in main_source and '.read_lance(' in main_source


def test_new_default_has_only_same_model_qwen_pairs(prepared):
    run, _, questions, upstream, _ = prepared
    cfg = default_config()
    run = run.with_name('default_backends')
    graph.run_pipeline(run, questions, upstream, cfg)
    jobs = saved_stage(run, 'jobs')
    assert len(jobs) == 40
    assert {j['backend'] for j in jobs} == {'qwen2511', 'qwen2512'}
    for task_id in {j['task_id'] for j in jobs}:
        pair = [j for j in jobs if j['task_id'] == task_id]
        assert len({j['backend'] for j in pair}) == len({j['seed'] for j in pair}) == 1
        assert {j['condition'] for j in pair} == {'without_knowledge', 'with_knowledge'}


def test_author_rubric_cannot_be_rewritten_split_or_given_new_evidence(prepared):
    from evaluation.operaters.scores import validate_rubric
    complete_rubrics(prepared)
    run, *_ = prepared
    row = saved_stage(run, 'rubrics')[0]
    packet = read_record(row['judge_packet']['record_ref'])
    context = {'rubric_policy': 'author_exact_v1', 'author_criteria': packet['author_criteria'],
               'evidence': packet['evidence']}
    original = packet['rubric']
    validate_rubric(original, packet['question'], context)
    for field in ('requirement', 'observable_region', 'allowed_variation'):
        changed = copy.deepcopy(original)
        changed['criteria'][0][field] += ' hidden new obligation'
        with pytest.raises(ValueError, match='Author criterion text changed'):
            validate_rubric(changed, packet['question'], context)
    changed = copy.deepcopy(original)
    changed['criteria'][0]['evidence_ids'] = ['S1']
    with pytest.raises(ValueError, match='Author criterion evidence changed'):
        validate_rubric(changed, packet['question'], context)
    changed = copy.deepcopy(original)
    extra = {**changed['criteria'][0], 'id': 'R2'}
    changed['criteria'].append(extra)
    changed['author_dispositions'][0].update(decision='split', criterion_ids=['R1', 'R2'])
    with pytest.raises(ValueError, match='one-to-one'):
        validate_rubric(changed, packet['question'], context)


def test_retrieval_uses_public_entity_and_topic_not_private_criteria(prepared):
    from evaluation.operaters.operators import RetrieveKnowledge
    run, *_, cfg = prepared
    catalog = saved_stage(run, 'catalog')
    template = next(i for i in catalog if i['kind'] == 'text')
    items = [
        {**template, 'item_id': 'morph', 'concept': '测试龟', 'title': '形态特征',
         'text': '测试龟拥有宽扁鳍状肢和连续的皮革质背甲。'},
        {**template, 'item_id': 'trade', 'concept': '测试龟', 'title': '保护状况',
         'text': '测试龟被列入保护名录，严禁贸易。'},
        {**template, 'item_id': 'distractor', 'concept': '点密度地图', 'title': '形态绘制展示',
         'text': '绘制全身外部形态自然插画，准确表现主体背甲和鳍状肢。'},
    ]
    row = copy.deepcopy(saved_stage(run, 'questions')[0])
    row['question']['instruction'] = '绘制测试龟的外部形态自然插画。'
    actor = RetrieveKnowledge(items, run_records(run).get('split_registry'), {**cfg, 'text_limit': 1})
    first = actor(row)
    assert [i['item_id'] for i in first['materials']] == ['morph']
    row['criteria'] = [{'knowledge_ids': ['distractor'], 'requirement': 'private demand for other source'}]
    row['concept'] = '点密度地图'
    second = actor(row)
    assert second['materials'] == first['materials']
    assert second['retrieval'] == first['retrieval']
    # Explicitly naming both entities leaves both available; no one-concept oracle.
    row['question']['instruction'] = '对比测试龟形态和点密度地图的展示。'
    both = RetrieveKnowledge(items, run_records(run).get('split_registry'), {**cfg, 'text_limit': 3})(row)
    assert {'测试龟', '点密度地图'} == set(both['retrieval']['matched_concepts'])


def test_insufficient_rubric_can_report_partial_audit_without_fake_dispositions():
    from evaluation.operaters.scores import validate_rubric
    context = {'rubric_policy': 'author_exact_v1', 'evidence': [],
               'author_criteria': [{'source_criterion_id': 'A1'}]}
    result = {'status': 'insufficient', 'audit': {'status': 'judge_unscorable',
              'detail': 'Missing a public observation condition'},
              'criteria': [], 'author_dispositions': []}
    validate_rubric(result, {'task_type': 't2i'}, context)
    result['status'] = 'ready'
    with pytest.raises(ValueError, match='Ready rubric has no criteria'):
        validate_rubric(result, {'task_type': 't2i'}, context)


def test_evaluation_retrieval_does_not_use_private_criterion_knowledge(prepared):
    from evaluation.operaters.operators import RetrieveKnowledge
    run, *_, cfg = prepared
    catalog = saved_stage(run, 'catalog')
    row = copy.deepcopy(saved_stage(run, 'questions')[0])
    retrieve = RetrieveKnowledge(catalog, run_records(run).get('split_registry'), cfg)
    baseline = retrieve(row)
    row['criteria'] = [{'knowledge_ids': ['unrelated_private_target']}]
    changed = retrieve(row)
    assert changed['materials'] == baseline['materials']
    assert changed['retrieval']['ranks'] == baseline['retrieval']['ranks']
