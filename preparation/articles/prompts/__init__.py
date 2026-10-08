"""文章提示词与本地模型选项；正文、版本和响应协议见 tasks.yaml。"""
from demiflow.operator_llm.call_ref import journal_options as sqlite_call_options
from pathlib import Path
import os
import yaml
from demiflow.operator_llm.parser import parse_prompt_pack
from preparation.articles.operators.runfiles import prompt_store, run_records

from urllib.parse import urlparse

def require(ok, message):
    if not ok:
        raise ValueError(message)

def validate_local_endpoint(base_url, model):
    """User scope: direct local Qwen only. Paid gateways require a new explicit decision."""
    url = urlparse(base_url)
    require(url.scheme == 'http' and url.hostname in ('127.0.0.1', 'localhost', '::1')
            and url.port in (8000, 8001) and url.path.rstrip('/') == '/v1'
            and not url.username and not url.password and not url.query and not url.fragment,
            'only direct local Qwen on 8000/8001 is authorized; paid gateways (including 4001) are blocked')
    require(model.lower() == 'qwen3.8-27b', 'only local qwen3.8-27b is authorized')



def material_prompt_pack(config):
    """加载文章任务；运行时只覆盖模型端点、选择已存在的正文筛选版本。"""
    spec = yaml.safe_load(Path(__file__).with_name('tasks.yaml').read_text())
    prompts = spec['prompts']
    block = ('select_blocks' if config.get('relevance_only', True)
             else 'select_blocks_strict' if config.get('body_only') else 'select_blocks_base')
    selected = {'identity': prompts['identity'], 'select_blocks': prompts[block]}
    if config.get('article_mode', False):
        selected.update({name: prompts[name] for name in ('joint_paragraphs', 'final_review')})
    for prompt in selected.values():
        prompt['model'].update(name=config['model'], base_url=config['base_url'])
    spec['prompts'] = selected
    text = yaml.safe_dump(spec, allow_unicode=True, sort_keys=False)
    return parse_prompt_pack(text), text

LOCAL_REVIEW_MODELS = frozenset({'qwen3.8-27b','qwen3.6-35b-a3b','gemma-4-26b-a4b-it','gemma-4-31b-it'})


def validate_review_comparison_endpoint(base_url, model):
    """Explicitly authorized local four-model comparison; preannotation policy unchanged."""
    url = urlparse(base_url)
    if not (url.scheme == 'http' and url.hostname in {'127.0.0.1','localhost','::1'}
            and url.port in {8000,8001} and url.path.rstrip('/') == '/v1'
            and not (url.username or url.password or url.query or url.fragment)
            and model in LOCAL_REVIEW_MODELS):
        raise ValueError('Comparison requires a named local review model on 8000/8001')


def prompt_execution_options(run,config):
    if config.get('local_model_comparison'):
        validate_review_comparison_endpoint(config['base_url'],config['model'])
    else:
        validate_local_endpoint(config['base_url'],config['model'])
    # Local endpoint has no secret; this named key is only a protocol placeholder.
    os.environ.setdefault('CURATION_LOCAL_MODEL_KEY','local-no-auth')
    return {'sqlite_journal':sqlite_call_options(**prompt_store(run, 'calls')),'timeout_s':config['timeout_s'],
            'trust_env':False,'verify_model':True,'require_finish_reason_stop':True,
            'max_keepalive_connections':0,
            'request_options':{'max_tokens':config['max_output_tokens'],'response_format':{'type':'json_object'},
                               'chat_template_kwargs':{'enable_thinking':config.get('enable_thinking',False), **({'reasoning_effort':config.get('reasoning_effort','low')} if config.get('enable_thinking') and config['model'].startswith('qwen') else {})},
                               **({'temperature':config['temperature']} if 'temperature' in config else {})}}


def save_prompt_config(run, text, options):
    run_records(run).save_configuration('prompt_config', {'yaml': text, 'execution_options': options})
