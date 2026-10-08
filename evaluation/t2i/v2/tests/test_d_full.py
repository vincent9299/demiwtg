"""两位判官共享来源、独立执行及旧分复用回归。"""
import json
import io
import pyarrow as pa
from PIL import Image
from demiflow.objects import LocalObjectStore
from pathlib import Path
import lance
import pytest
from demiflow import data
from project import resolve_root
from evaluation.t2i.v2 import t2i_v2_eval_pipeline as pipeline
from evaluation.t2i.v2.operators import d_full
from .test_d_evaluation import fixture_run, read_rows
from .test_d7 import directions, response


def distinct_answers(cfg, root):
    for index, model in enumerate(cfg['answers']):
        rows = data.read_lance(**model['source']).take(3)
        for n, row in enumerate(rows):
            image = io.BytesIO()
            Image.new('RGB', (16, 16), (index * 80, n * 60, 10)).save(image, format='PNG')
            row['image_json'] = json.dumps(LocalObjectStore(root / 'objects').put(image.getvalue()).to_dict())
        schema = lance.dataset(**model['source']).schema
        data.from_arrow(pa.Table.from_pylist(rows, schema=schema)).write_lance(
            model['source']['uri'], schema=schema, mode='overwrite')
        model['source']['version'] = 2


def test_codex_does_not_require_malasci_and_completed_scores_are_reused():
    run, source, cfg = fixture_run(legacy=False, protocol='d7', requirements=directions())
    root = resolve_root()
    distinct_answers(cfg, root)
    cfg['judge'].update(model='codex/gpt-6-astra', reasoning_effort='xhigh')
    cfg['d_evaluation'].update(codex_export=True)
    first = pipeline.run_pipeline(run, source, cfg)
    assert first['phase'] == 'awaiting_d_responses' and not first['complete']
    directory = root / 'demiwtg/evaluation/t2i/v2/runs' / run.name
    manifest = json.loads((directory / 'manifest.json').read_text())
    assert len(manifest['cases']) == 4
    for case in manifest['cases']:
        path = directory / case['case']
        receipt = json.loads((path / 'input_receipt.json').read_text())
        assert receipt['historical_request_ref'] is None
        assert receipt['input_sha256'] and len(receipt['images']) == 1
        (path / 'response.json').write_text(json.dumps({'result': response()}))
        (path / 'execution.json').write_text(json.dumps({'transport': 'codex_subagent',
            'fork_turns': 'none', 'model': 'gpt-6-astra', 'reasoning_effort': 'xhigh', 'agent_name': 'fixture'}))
    final = pipeline.run_pipeline(run, source, cfg)
    assert final['complete']
    previous = read_rows(final['target'])
    cfg['d_evaluation'].update(reuse_scores=[final['target']])
    second = pipeline.run_pipeline(run.with_name('reuse_codex'), source, cfg)
    assert second['complete']
    key = lambda row: (row['task_id'], row['answer_mode'])
    assert sorted(read_rows(second['target']), key=key) == sorted(previous, key=key)
    assert not list((directory.parent / 'reuse_codex').glob('*/input_receipt.json'))


def test_reuse_rejects_changed_judge_and_changed_answer():
    row = dict(task_id='q', answer_mode='text_only', answer_model='qwen', instruction='test',
        question_revision='v1', d_protocol='d7', image_json='{"sha256":"a"}',
        core_requirements_json='{"requirements":[]}', d_status='ready', answer_request_id='a')
    old = {**row, 'd_status': 'reviewed', 'judge_model': 'codex/gpt-6-astra'}
    with pytest.raises(ValueError, match='judge differs'):
        d_full.reuse_score({**row, 'prior_score': old}, judge={'model': 'malasci/gpt-6-astra'})
    with pytest.raises(ValueError, match='image_json'):
        d_full.reuse_score({**row, 'image_json': '{}', 'prior_score': old}, judge={'model': old['judge_model']})
    old.update(judge_model='codex-subagent/gpt-6-astra', d_call_json=json.dumps({
        'transport': 'codex_subagent', 'model': 'gpt-6-astra', 'reasoning_effort': 'xhigh', 'fork_turns': 'none'}))
    reused = d_full.reuse_score({**row, 'prior_score': old}, judge={'model': 'codex/gpt-6-astra'})
    assert reused['judge_model'] == old['judge_model'] and reused['_reused_score']


def test_rag_kept_initial_reuses_scores_but_keeps_rag_answer_identity():
    old = dict(task_id='q', answer_mode='text_only', answer_model='qwen', instruction='test',
        question_revision='v1', d_protocol='d7', image_json='{"sha256":"a"}',
        core_requirements_json='{"requirements":[]}', d_status='reviewed',
        answer_request_id='initial', judge_model='codex/gpt-6-astra', d_overall_score=60,
        d_call_json='{"request_ref":{"id":"actual-call"}}')
    row = {**old, 'answer_mode': 'imagerag', 'answer_model': 'qwen+ImageRAG',
           'answer_request_id': 'rag', 'd_status': 'ready', 'initial_score': old}
    judge = {'model': 'codex/gpt-6-astra'}
    reused = d_full.reuse_initial_score(row, judge=judge)
    assert reused['_reused_score'] and reused['answer_mode'] == 'imagerag'
    assert reused['answer_request_id'] == 'rag' and reused['d_overall_score'] == 60
    assert json.loads(reused['d_call_json'])['request_ref'] == {'id': 'actual-call'}
    other = {**row, 'image_json': '{"sha256":"b"}'}
    assert d_full.reuse_initial_score(other, judge=judge) == other
    with pytest.raises(ValueError, match='question_revision'):
        d_full.reuse_initial_score({**row, 'question_revision': 'changed'}, judge=judge)


def test_native_cli_judging_keeps_images_records_and_resume_cache(tmp_path):
    """Exercise the real platform CLI transport with a local fixture executable."""
    import sys
    from demiflow.operator_llm.call_ref import read_call
    run, source, initial = fixture_run(legacy=False, protocol='d7', requirements=directions())
    distinct_answers(initial, resolve_root())
    initial['d_evaluation']['arm_concurrency'] = 1
    invocations = tmp_path / 'invocations.jsonl'
    executable = tmp_path / 'codex-fixture'
    executable.write_text(f'''#!{sys.executable}
import json,sys
from pathlib import Path
args=sys.argv[1:]
assert args[0]=='exec' and '--ephemeral' in args and '--ignore-user-config' in args
assert args[args.index('--model')+1]=='gpt-6-astra'
assert args[args.index('--sandbox')+1]=='read-only'
assert 'model_reasoning_effort="xhigh"' in args
assert 'features.image_generation=false' in args and 'web_search="disabled"' in args
image=Path(args[args.index('--image')+1]).read_bytes()
assert image.startswith(bytes.fromhex('89504e470d0a1a0a'))
prompt=sys.stdin.read()
assert '核心' in prompt and '质量' in prompt
with Path({str(invocations)!r}).open('a') as f:f.write(json.dumps({{'args':args,'prompt':prompt}})+'\\n')
Path(args[args.index('--output-last-message')+1]).write_text({json.dumps({'result': response()}, ensure_ascii=False)!r})
print(json.dumps({{'type':'turn.completed','usage':{{'input_tokens':100,'output_tokens':100}}}}))
''')
    executable.chmod(0o700)
    cfg = pipeline.config(judge_model={'mode': 'codex_exec', 'model': 'codex/gpt-6-astra',
        'reasoning_effort': 'xhigh', 'concurrency': 2, 'codex_exec': {'bin': str(executable)}},
        d_evaluation=initial['d_evaluation'])
    state = pipeline.run_pipeline(run, source, cfg)
    assert state['complete']
    calls = invocations.read_text().splitlines()
    assert calls
    for row in read_rows(state['target']):
        call = json.loads(row['d_call_json'])
        assert call['transport'] == 'codex_exec' and call['model'] == 'gpt-6-astra'
        request = read_call(call['request_ref'], resolve_root())
        assert request['reasoning_effort'] == 'xhigh'
        assert request['execution']['ephemeral']
        assert row['d_status'] == 'reviewed' and row['d_overall_score'] is not None
    assert pipeline.run_pipeline(run, source, cfg)['complete']
    assert invocations.read_text().splitlines() == calls
    cfg['d_evaluation']['reuse_scores'] = [state['target']]
    assert pipeline.run_pipeline(run.with_name('cli_reuse'), source, cfg)['complete']
    assert invocations.read_text().splitlines() == calls


def test_cli_config_rejects_model_or_capability_drift():
    for extra in ({'model': 'other'}, {'reasoning_effort': 'high'},
                  {'codex_exec': {'image_generation': True}},
                  {'codex_exec': {'web_search': 'live'}}):
        with pytest.raises(ValueError):
            pipeline.config(judge_model={'mode': 'codex_exec', 'model': 'codex/gpt-6-astra',
                'reasoning_effort': 'xhigh', **extra})
