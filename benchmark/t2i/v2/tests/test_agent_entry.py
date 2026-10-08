"""Configuration-only checks: no datasets, models or external tools execute."""
import json
from pathlib import Path

import pytest
import yaml

from demiflow.agent import load_agent_config
from demiflow.operator_llm.parser import load_prompt_pack
from benchmark.t2i.v2.t2i_v2_benchmark_pipeline import PROMPTS, config


def test_current_agent_entry_resolves_without_http_model_fields():
    agent = load_agent_config(PROMPTS / 'agent_codex.yaml')
    cfg = config(run='/unused', concepts=['fixture'], agent_config=PROMPTS / 'agent_codex.yaml')
    assert cfg['mode'] == 'codex' and cfg['model'] == 'gpt-6-astra'
    assert cfg['base_url'] is None and cfg['api_key_env'] is None
    assert cfg['max_calls'] == 1 and agent.max_requests == 300
    assert len(agent.environment.operators) == 1 and agent.environment.resources == 'document_resources'
    assert not agent.options['codex_agent']['shell_tool'] and agent.options['codex_agent']['web_search'] == 'live'
    assert config(**cfg) == cfg
    prompt = load_prompt_pack(PROMPTS / 'tasks.yaml')
    assert set(prompt.prompt_definitions) == {'review_answer'}
    assert set(agent.prompt_pack.prompt_definitions) == {'design_question'}
    assert prompt.prompt_definitions['review_answer'].model.name == 'malasci/gpt-6-astra'


@pytest.mark.parametrize('overrides', [
    {'model': 'wrong-model'}, {'base_url': 'https://wrong.invalid'},
    {'codex_web_search': 'disabled'}, {'timeout_s': 1}, {'max_context_chars': 1},
    {'document_reads': False}, {'mode': 'offline'}, {'model_stream': True},
    {'model_deployment_id': 'hidden-deployment'},
])
def test_agent_entry_cannot_be_silently_overridden(overrides):
    with pytest.raises(ValueError):
        config(run='/unused', concepts=['fixture'], agent_config=PROMPTS / 'agent_codex.yaml', **overrides)


def test_yaml_changes_determine_effective_model_and_budget(tmp_path):
    path = tmp_path / 'agent.yaml'
    raw = yaml.safe_load((PROMPTS / 'agent_codex.yaml').read_text())
    raw['model']['name'] = 'fixture-selected-in-yaml'
    raw['budgets']['max_requests'] = 2
    raw['options']['codex_agent']['web_search'] = 'disabled'
    path.write_text(yaml.safe_dump(raw))
    # An unrelated/broken ordinary prompt cannot influence this complete agent file.
    (tmp_path / 'tasks.yaml').write_text('invalid ordinary prompt')
    cfg = config(run='/unused', concepts=['a', 'b', 'c'], agent_config=path, max_calls=9)
    assert cfg['model'] == 'fixture-selected-in-yaml' and cfg['max_calls'] == 2
    assert cfg['codex_web_search'] == 'disabled'


def test_notebook_keeps_single_entry_and_does_not_execute():
    book = json.loads((PROMPTS.parent / 't2i_v2_benchmark_debug.ipynb').read_text())
    code = ''.join(book['cells'][0]['source'])
    assert 'RUN_PIPELINE = False' in code and "CONFIG['agent_config']" in code
    assert 'PARAMETERS = json.loads(CONFIG_PATH.read_text())' in code
    assert 'CONFIG = pipeline.config(**PARAMETERS)' in code
    review = ''.join(book['cells'][2]['source'])
    view = ''.join(book['cells'][3]['source'])
    assert 'RUN_QUESTION_REVIEW = False' in review and 't2i_v2_benchmark_pipeline' in review
    assert 'latest_browser_controls' in view and 'height=4000' in view
    assert 'run_pipeline(' not in view and 'subprocess' not in view


@pytest.mark.parametrize('mode', [None, 'offline', 'modelhub', 'codex'])
def test_retired_authoring_entry_fails_before_execution(mode):
    with pytest.raises(ValueError, match='requires agent_config'):
        config(run='/unused', concepts=['fixture'], mode=mode)
