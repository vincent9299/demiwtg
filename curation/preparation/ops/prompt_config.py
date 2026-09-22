from curation.preparation.records import prompt_store, run_records
"""Business prompt pack and local call policy; demiflow owns HTTP and execution."""
import os
from urllib.parse import urlparse
from pathlib import Path
import yaml
from demiflow.operator_llm.parser import parse_prompt_pack
from curation.preparation.common import validate_local_endpoint
from curation.preparation.contracts import immutable
from curation.preparation.ops.prompt_loader import load_instruction
SYSTEM = load_instruction('system')
IDENTITY = load_instruction('identity')
from curation.preparation.ops.source_blocks import SELECT_BLOCKS, SELECT_BLOCKS_STRICT, COMPARE_BLOCKS


def article_prompt_template(spec):
    image_template = '{{ images | numbered_image }}' if spec.get('numbered_images', False) else '{{ images | image }}'
    return (spec['instruction'] + '\n本次材料：\n{{ payload }}\n真实图片：\n' + image_template
            + ('\n' + spec['closing_instruction'] if spec.get('closing_instruction') else ''))


def material_prompt_pack(config):
    shapes = {
        'identity': {'status':'string', 'target_label':'string', 'reason':'string',
                     'accepted_material_ids':'array', 'rejected_materials':'array',
                     'identity_groups':'array', 'material_reviews':'array'},
        'select_blocks': {'decisions':'array'},
        'compare_blocks': {'pairs':'array', 'issues':'array'},
        'select_images': {'images':'array'},
    }
    model = {'name':config['model'], 'transport':'openai_compatible',
             'base_url':config['base_url'], 'api_key_env':'CURATION_LOCAL_MODEL_KEY'}
    prompts = {}
    for name, instruction in [('identity', IDENTITY), ('select_blocks', SELECT_BLOCKS),
                              ('compare_blocks', COMPARE_BLOCKS),
                              ('select_images', load_instruction('select_images'))]:
        version = 'knowledge-native-prompt-v1'
        if name in {'select_blocks', 'compare_blocks'}:
            version = 'knowledge-source-blocks-v1'
        if name == 'select_blocks':
            if config.get('relevance_only', True):
                instruction = load_instruction('relevance')
                version = yaml.safe_load((Path(__file__).parent/'prompts/relevance.yaml').read_text())['version']
            elif config.get('body_only'):
                instruction, version = SELECT_BLOCKS_STRICT, 'knowledge-source-blocks-strict-v3'
        properties = {key:({'type':'array', 'items':{}} if kind == 'array' else {'type':kind})
                      for key, kind in shapes[name].items()}
        template = SYSTEM + '\n' + instruction + '\n将以上要求的完整对象放入响应的result属性。\n输入数据：\n{{ payload | json }}'
        if name == 'select_images':
            template += '\n图片顺序与image_ids一致；以下是本次实际输入像素：\n{{ images | image }}'
            version = yaml.safe_load((Path(__file__).parent/'prompts/select_images.yaml').read_text())['version']
        prompts[name] = {'version':version, 'model':model, 'schema_retries':0,
            'response_schema':{'type':'object', 'required':['result'], 'additionalProperties':False,
                'properties':{'result':{'type':'object', 'required':list(properties),
                                        'properties':properties, 'additionalProperties':True}}},
            'template':template}
    if config.get('article_mode', False):
        for name, filename in [('joint_paragraphs', 'article_joint'), ('final_review', 'final_review')]:
            spec = yaml.safe_load((Path(__file__).parent/'prompts'/f'{filename}.yaml').read_text())
            prompts[name] = {'version':spec['version'], 'response_format':'text',
                'model':model, 'schema_retries':0,
                'response_schema':{'type':'object', 'required':['result'], 'additionalProperties':False,
                    'properties':{'result':{'type':'string', 'minLength':1}}},
                'template':article_prompt_template(spec)}
        prompts = {name:prompts[name] for name in ['identity','select_blocks','select_images','joint_paragraphs','final_review']}
    text = yaml.safe_dump({'schema_version':'demiflow_prompt_pack_v2', 'prompts':prompts}, allow_unicode=True, sort_keys=False)
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
    return {'lance_journal':prompt_store(run, 'calls'),'timeout_s':config['timeout_s'],
            'trust_env':False,'verify_model':True,'require_finish_reason_stop':True,
            'max_keepalive_connections':0,
            'request_options':{'max_tokens':config['max_output_tokens'],'response_format':{'type':'json_object'},
                               'chat_template_kwargs':{'enable_thinking':config.get('enable_thinking',False), **({'reasoning_effort':config.get('reasoning_effort','low')} if config.get('enable_thinking') and config['model'].startswith('qwen') else {})},
                               **({'temperature':config['temperature']} if 'temperature' in config else {})}}


def save_prompt_config(run, text, options):
    run_records(run).put('prompt_config', {'yaml': text, 'execution_options': options})
