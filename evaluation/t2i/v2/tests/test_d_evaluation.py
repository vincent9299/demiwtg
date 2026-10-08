"""隔离 Lance 与原生离线请求验证 D 有限配对试评；不调用线上模型。"""
import io
import json
from pathlib import Path
import lance
import pyarrow as pa
import yaml
from PIL import Image
from demiflow import data
from demiflow.objects import LocalObjectStore
from demiflow.operator_llm.call_ref import read_call
from demiflow.operator_llm.sqlite_offline import submit_response
from project import resolve_root
from evaluation.t2i.v2 import t2i_v2_eval_pipeline as pipeline
from evaluation.t2i.v2.operators import d_evaluation
from evaluation.t2i.v2.operators.case_viewer import build_case_browser
from .test_d import INSTRUCTION, POINTS, judgment


def read_rows(ref):
    return data.read_lance(**ref).take(10)


def core_result():
    return {'status': 'ready', 'reason': '目标工具与工作端展示决定任务成立',
            'point_roles': [{'index': 1, 'is_core': True, 'instruction_quote': '六角扳手',
                             'reason': '目标身份、数量及结构展示是本题核心'}],
            'core_additional_requirements': [], 'coverage_reason': '颜色为独立非核心要求，仍须后续检查'}



def test_d_prompt_uses_sse_without_changing_prompt_transport_contract(tmp_path):
    spec = yaml.safe_load((Path(__file__).parents[1] / 'prompts/d7.yaml').read_text())
    judge = {
        'mode': 'online', 'model': 'fixture-judge',
        'base_url': 'http://127.0.0.1:4001/v1',
        'api_key_env': 'MODELHUB_API_KEY', 'timeout_s': 900,
        'max_output_tokens': 32768, 'max_context_chars': 60000,
        'reasoning_effort': 'xhigh', 'stream': True,
        'stream_include_usage': True,
    }
    _, options, _ = pipeline.d_prompt(spec, 'judge_d', judge, tmp_path, 'stream-canary', 1)
    assert options['stream'] is True
    assert options['stream_include_usage'] is True
    assert 'stream' not in options['request_options']
    assert options['request_options']['response_format'] == {'type': 'json_object'}


def test_d_prompt_keeps_offline_fixture_transport_without_http_stream(tmp_path):
    spec = yaml.safe_load((Path(__file__).parents[1] / 'prompts/d7.yaml').read_text())
    judge = {
        'mode': 'offline', 'model': 'fixture-judge',
        'base_url': 'http://127.0.0.1:4001/v1',
        'api_key_env': 'MODELHUB_API_KEY', 'timeout_s': 900,
        'max_output_tokens': 32768, 'max_context_chars': 60000,
        'reasoning_effort': 'xhigh', 'stream': True,
        'stream_include_usage': True,
    }
    _, options, _ = pipeline.d_prompt(spec, 'judge_d', judge, tmp_path, 'offline-canary', 1)
    assert 'stream' not in options
    assert 'offline_store' in options

def fixture_run(*, legacy=True, protocol='d6', requirements=None):
    root = resolve_root()
    questions = [{'task_id': f'q{i}', 'concept': f'夹具{i}', 'instruction': INSTRUCTION,
                  'taxonomy': ['工具'], 'test_points': POINTS, 'authoring_variant': 'paired',
                  'authoring_images_json': '[]'} for i in range(3)]
    schema = d_evaluation.QUESTION_SCHEMA
    if protocol == 'd7':
        schema = d_evaluation.D7_QUESTION_SCHEMA
        for row in questions:
            row.update(requirements_json=json.dumps(requirements, ensure_ascii=False),
                       question_revision='fixture-reviewed-1', review_status='ready')
    source_uri = str(root / 'questions.lance')
    data.from_arrow(pa.Table.from_pylist(questions, schema=schema)).write_lance(
        source_uri, schema=schema, mode='create')
    models = []
    for index, mode in enumerate(('text_only', 'positive_images')):
        buffer = io.BytesIO()
        Image.new('RGB', (16, 16), ('red', 'green')[index]).save(buffer, format='PNG')
        ref = LocalObjectStore(root / 'objects').put(buffer.getvalue()).to_dict()
        rows = [{'request_id': f'{index}-{q["task_id"]}', **{k: q[k] for k in ('task_id', 'concept', 'instruction')},
                 'answer_model': f'fixture-{index}', 'answer_mode': mode, 'status': 'generated', 'reason': '',
                 'reference_images_json': '[]', 'reference_image_count': index, 'generation_seconds': 0.0,
                 'image_json': json.dumps(ref), 'answer_call_json': None} for q in questions]
        uri = str(root / f'answers{index}.lance')
        schema = pa.schema([('request_id', pa.string()), *pipeline.ANSWERS])
        data.from_arrow(pa.Table.from_pylist(rows, schema=schema)).write_lance(uri, schema=schema, mode='create')
        models.append({'model': f'fixture-{index}', 'answer_mode': mode, 'source': {'uri': uri, 'version': 1}})
    spec = {'question_limit': 2, 'answers': models}
    if legacy:
        archive = Path(__file__).parents[1] / 'archive/d5/prompts'
        spec.update(judge_prompt_pack=yaml.safe_load((archive / 'd.yaml').read_text()),
                    core_prompt_pack=yaml.safe_load((archive / 'd_core.yaml').read_text()))
    elif protocol == 'd6':
        spec['judge_prompt_pack'] = yaml.safe_load((Path(__file__).parents[1] / 'archive/d6/prompts/d.yaml').read_text())
    cfg = pipeline.config(judge_model={'model': 'fixture-judge', 'mode': 'offline', 'concurrency': 2},
                          d_evaluation=spec)
    return root / pipeline.DATASETS / 'd_pilot', {'uri': source_uri, 'version': 1}, cfg


def test_d_two_arms_fixed_scope_core_freeze_real_inputs_and_pause():
    run, source, cfg = fixture_run()
    first = pipeline.run_pipeline(run, source, cfg)
    assert first['phase'] == 'awaiting_core_responses'
    assert first['expected_questions'] == 2 and first['expected_answers'] == 4
    plans = read_rows(first['outputs']['d_core'])
    assert {r['task_id'] for r in plans} == {'q0', 'q1'}
    for row in plans:
        call = json.loads(row['core_call_json'])
        request = read_call(call['request_ref'])
        serialized = json.dumps(request, ensure_ascii=False)
        assert 'image_url' not in serialized and 'answer_model' not in serialized
        assert 'authoring_images_json' not in serialized
        submit_response(resolve_root(), call['request_ref'], json.dumps({'result': core_result()}, ensure_ascii=False),
                        model=call['model'])
    second = pipeline.run_pipeline(run, source, cfg)
    assert second['phase'] == 'awaiting_d_responses'
    frozen = second['outputs']['d_core']
    for index in range(2):
        rows = read_rows(second['outputs'][f'arm{index}'])
        assert {r['task_id'] for r in rows} == {'q0', 'q1'}
        for row in rows:
            call = json.loads(row['d_call_json'])
            request = read_call(call['request_ref'])
            assert request['prompt_version'] == 't2i-v2-d-judge-5-review'
            parts = [part for message in request['messages'] if isinstance(message['content'], list)
                     for part in message['content']]
            assert sum(part['type'] == 'image_url' for part in parts) == 1
            text = ''.join(part['text'] for part in parts if part['type'] == 'text')
            assert 'fixture-0' not in text and 'fixture-1' not in text
            assert 'core_requirements' in text
            result = judgment() if index else judgment(extra='fail')
            if index == 0 and row['task_id'] == 'q1':
                result = judgment(point='inconclusive', extra='pass')
            submit_response(resolve_root(), call['request_ref'], json.dumps({'result': result}, ensure_ascii=False),
                            model=call['model'])
    final = pipeline.run_pipeline(run, source, cfg)
    assert final['complete'] and final['phase'] == 'paused_after_sample'
    assert final['outputs']['d_core'] == frozen
    assert len(read_rows(final['target'])) == 4
    assert final['counts_by_arm']['text_only']['dimensions']['task_correctness'] == {'valid': 1, 'mean': 0.0}
    assert final['counts_by_arm']['positive_images']['dimensions']['task_correctness'] == {'valid': 2, 'mean': 100.0}
    assert final['counts_by_arm']['text_only']['dimensions']['quality'] == {'valid': 2, 'mean': 100.0}
    control = resolve_root() / '_demiflow/evaluation_t2i_v2/d_pilot'
    comparison = json.loads((control / 'd_comparison.json').read_text())
    assert comparison['paired_dimensions']['task_correctness']['paired_count'] == 1
    assert comparison['paired_dimensions']['quality']['paired_count'] == 2
    assert (control / 'pause_requested.json').exists()
    assert pipeline.run_pipeline(run, source, cfg) == final
    assert all(lance.dataset(m['source']['uri']).version == 1 for m in cfg['answers'])
    html, meta = build_case_browser(resolve_root() / 'demiwtg', run.name)
    assert meta['scoring_scheme'] == 'D' and meta['questions'] == 2 and meta['complete']
    assert meta['prompt_version'] == 't2i-v2-d-judge-5-review'
    assert 'D 判分 · 实际完整输入与作答图' in html
    assert '没有保存完整消息' not in html
