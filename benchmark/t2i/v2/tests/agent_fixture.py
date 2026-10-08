"""Write isolated agent entries for local protocol fixtures, never real services."""
from pathlib import Path
import yaml
from benchmark.t2i.v2.t2i_v2_benchmark_pipeline import PROMPTS


def agent_arguments(root, **arguments):
    arguments = dict(arguments)
    arguments.setdefault('mode', 'offline')
    codex = arguments.get('mode') == 'codex'
    raw = yaml.safe_load((PROMPTS / 'agent_codex.yaml').read_text())
    if not codex:
        raw['runtime'] = 'demiflow'
        raw['model'] = {'name':'fixture-author', 'transport':'openai_compatible',
                        'base_url':'http://127.0.0.1:4001/v1', 'api_key_env':'MODELHUB_API_KEY'}
        raw['operators'] = ['read_documents']
        raw['resources'] = 'document_resources'
        raw['budgets'] = {'max_requests':1200, 'max_turns':4, 'max_calls_per_turn':2, 'max_context_chars':60000}
        raw['options'] = {'timeout_s':600, 'verify_model':'listed', 'require_finish_reason_stop':True,
            'trust_env':False, 'gateway':'litellm', 'request_options':{'temperature':0, 'max_tokens':8192}}
    folder = Path(root) / 'agent_fixture'
    folder.mkdir(parents=True, exist_ok=True)
    if arguments.get('model'):
        raw['model']['name'] = arguments.pop('model')
    deployment = arguments.pop('model_deployment_id', None)
    revision = arguments.pop('model_revision', None)
    if deployment:
        raw['model']['name'] = deployment
        raw['options']['verify_model'] = False
    if revision:
        (raw['options']['codex_agent'] if codex else raw['options'])['model_revision'] = revision
    for field in ('codex_bin', 'reasoning_effort', 'codex_web_search'):
        if field in arguments:
            raw['options']['codex_agent'][{'codex_bin':'bin', 'codex_web_search':'web_search'}.get(field,field)] = arguments.pop(field)
    if 'timeout_s' in arguments:
        raw['options']['timeout_s'] = arguments.pop('timeout_s')
    path = folder / 'agent.yaml'
    path.write_text(yaml.safe_dump(raw, allow_unicode=True, sort_keys=False))
    arguments['agent_config'] = str(path)
    return arguments


def single_turn_arguments(root, **arguments):
    """Explicit tool-free offline agent for business I/O tests; no legacy prompt copy."""
    arguments = dict(arguments)
    overrides = {name: arguments.pop(name) for name in ('max_context_chars', 'temperature', 'max_output_tokens')
                 if name in arguments}
    arguments = agent_arguments(root, **arguments)
    path = Path(arguments['agent_config'])
    raw = yaml.safe_load(path.read_text())
    raw['operators'] = []
    raw.pop('resources', None)
    raw['budgets']['max_turns'] = 1
    if 'max_context_chars' in overrides:
        raw['budgets']['max_context_chars'] = overrides['max_context_chars']
    for name, field in (('temperature', 'temperature'), ('max_output_tokens', 'max_tokens')):
        if name in overrides:
            raw['options']['request_options'][field] = overrides[name]
    path.write_text(yaml.safe_dump(raw, allow_unicode=True, sort_keys=False))
    return arguments


def business_config(**arguments):
    from project import resolve_root
    from benchmark.t2i.v2.t2i_v2_benchmark_pipeline import config
    return config(**single_turn_arguments(resolve_root(), **arguments))


def submit_design_response(root, request_ref, content, **kwargs):
    import json
    from demiflow.operator_llm.sqlite_offline import submit_response
    result = {'api_calls': [], 'response': json.loads(content)}
    return submit_response(root, request_ref, json.dumps(result, ensure_ascii=False), **kwargs)
