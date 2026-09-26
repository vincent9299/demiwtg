"""Frozen-case comparison uses original messages and isolated Lance journals."""
import copy
import asyncio
import json

import httpx
import pytest

from demiflow.execution.artifacts import digest
from demiflow.operator_llm.parser import parse_prompt_pack
from demiflow import data
from project import resolve_root
from preparation.operaters.runfiles import run_records, read_record
from preparation.tests.publication_fixtures import inputs
from curation.t2i.t2i_train_pipeline import config
from curation.t2i.tests.test_pipeline import stage_rows
from curation.t2i.operaters.prompting import frozen_case_prompts, execution_options
from curation.t2i.tests.test_pipeline import setup_run, auto_responses


def saved_case(inputs, monkeypatch):
    run, sources, pipeline = setup_run(inputs, monkeypatch)
    auto_responses(monkeypatch, with_reference=True)
    pipeline(run, [], config(samples_per_concept=1), visual_sources=sources)
    rows = stage_rows(run_records(run).get("latest"), "attempts")
    row = next(r for r in rows if r.get("export_ready"))
    pack = parse_prompt_pack(run_records(run).get('prompt_config')['yaml'])
    originals = {}
    # Reify synthetic offline fixtures as historical HTTP envelopes, without calls.
    for stage in ('design_candidates', 'review_sample'):
        bound = read_record(row[stage + '_binding']['request_ref'])
        request = {'stage': stage, 'prompt_version': bound['prompt_version'],
            'schema_attempt': 1, 'response_schema': dict(pack.prompt_definitions[stage].response_schema),
            'endpoint': 'http://fixture.invalid/v1/chat/completions', 'params': None,
            'payload': {'messages': bound['messages'], 'model': 'fixture-qwen',
                'temperature': 0, 'max_tokens': 4096, 'response_format': {'type': 'json_object'},
                'chat_template_kwargs': {'enable_thinking': False}}}
        ref = run_records(run).put('fixture_http_request/' + stage, request)
        row[stage + '_call'] = {'request_ref': ref.to_dict()}
        originals[stage] = request
    return run, row, originals


def test_frozen_messages_independent_of_current_prompts_and_detect_corruption(inputs, monkeypatch):
    run, row, originals = saved_case(inputs, monkeypatch)
    from curation.t2i.operaters import prompting
    monkeypatch.setattr(prompting, 'PROMPTS', inputs[0] / 'nonexistent_prompts')
    cfg = config(mode='modelhub', model={'max_calls': 2})
    pack, prepared = frozen_case_prompts(row, cfg)
    assert pack.prompt_definitions['review_sample'].model.name == 'glm/glm-5.3-flash'
    assert len(prepared) == 2
    for item in prepared:
        assert item['input_sha256'] == digest(originals[item['stage']]['payload']['messages'])
        assert len(item['prompt_images']) == 2
        assert item['effective_options']['max_tokens'] == 4096
        assert 'chat_template_kwargs' not in item['effective_options']
    changed = copy.deepcopy(originals['review_sample'])
    changed['payload']['messages'][1]['content'][0]['text'] += ' different candidate'
    altered = copy.deepcopy(row)
    altered['review_sample_call']['request_ref'] = run_records(run).put('fixture_changed', changed).to_dict()
    with pytest.raises(ValueError, match='binding differs'):
        frozen_case_prompts(altered, cfg)


def test_native_comparison_is_independent_replayable_and_never_exports(inputs, monkeypatch):
    source_run, row, originals = saved_case(inputs, monkeypatch)
    cfg = config(mode='modelhub', model={'max_calls': 2})
    before = run_records(source_run).keys()
    posted = []
    def handler(request):
        if request.method == 'GET':
            return httpx.Response(200, json={'data': [{'id': 'other'}, {'id': cfg['model']['model']}]})
        payload = json.loads(request.content)
        posted.append(payload)
        stage = next(stage for stage, original in originals.items()
                     if original['payload']['messages'] == payload['messages'])
        result = copy.deepcopy(row['candidate_design' if stage == 'design_candidates' else 'review_sample'])
        if stage == 'design_candidates':
            result = {'status': 'insufficient', 'reason': 'Synthetic design rejection'}
        else:
            result['checks']['grounded'] = False
        return httpx.Response(200, json={'model': cfg['model']['model'], 'choices': [
            {'message': {'content': json.dumps({'result': result})}, 'finish_reason': 'stop'}]})
    original_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kwargs: original_client(
        **kwargs, transport=httpx.MockTransport(handler)))
    results = {}
    for _ in range(2):
        for stage in ('design_candidates', 'review_sample'):
            run = resolve_root() / 'demiwtg/curation/t2i/datasets' / ('manual_' + stage)
            pack, prepared = frozen_case_prompts(row, cfg, stages=[stage])
            options = execution_options(run, cfg)
            options['request_options'] = prepared[0]['effective_options']
            received = []
            flow = (data.from_items(prepared)
                .map_prompt_async(stage, config=pack, options=options,
                    inputs={'body': 'prompt_body', 'images': 'prompt_images', 'tail': 'prompt_tail'},
                    output='new_result', call_output='model_call', error_output='model_error',
                    concurrency=1, queue_depth=1)
                .map_async(received.append))
            asyncio.run(asyncio.to_thread(flow.run_stream))
            results[stage] = received[0]
    assert len(posted) == 2
    reviewed = results['review_sample']
    assert reviewed['new_result']['checks']['grounded'] is False
    assert reviewed['baseline_result']['checks']['grounded'] is True
    assert reviewed['model_call']['response_ref']
    assert run_records(source_run).keys() == before
    assert not list(run.parent.glob('training_samples__manual_*.lance'))



def test_modelhub_mode_is_explicit_and_keeps_local_validation(inputs):
    run = inputs[0] / 'config_only'
    local = config(mode='local')
    local['model']['base_url'] = 'http://127.0.0.1:4001/v1'
    with pytest.raises(ValueError, match='only direct local Qwen'):
        execution_options(run, local)
    hub = config(mode='modelhub')
    options = execution_options(run, hub)
    assert options['verify_model'] == 'listed'
    assert options['request_options']['thinking'] == {'type': 'disabled'}
    assert 'chat_template_kwargs' not in options['request_options']
