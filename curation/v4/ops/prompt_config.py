"""Business prompt pack and local call policy; demiflow owns HTTP and execution."""
import os
from urllib.parse import urlparse
from pathlib import Path
import yaml
from demiflow.operator_llm.parser import parse_prompt_pack
from curation.common import validate_local_endpoint
from curation.v4.contracts import immutable
from curation.v4.ops.prompt_loader import load_instruction
from curation.v4.ops.knowledge_prompts import SYSTEM, IDENTITY, EXTRACT, CONSOLIDATE, EVIDENCE, FIDELITY
from curation.v4.ops.source_blocks import SELECT_BLOCKS, SELECT_BLOCKS_STRICT, COMPARE_BLOCKS


def knowledge_prompt_pack(config):
    # Typed outer schema; detailed source/quote/conflict checks stay in business ops.
    shapes={
        'review_relationships':{'reviews':'array'},
        'repair_topics':{'topics':'array','coverage_note':'string'},
        'review_cross_batch':{'relationship':'string','reason':'string','paragraph_ids':'array'},
        'identity':{'status':'string','target_label':'string','reason':'string','accepted_material_ids':'array',
                    'rejected_materials':'array','identity_groups':'array','material_reviews':'array'},
        'extract':{'facts':'array','unresolved_conflicts':'array','coverage_note':'string'},
        'consolidate':{'facts':'array','unresolved_conflicts':'array','coverage_note':'string','changes':'array'},
        'evidence':{'images':'array','support':'array'},
        'fidelity':{'reviews':'array'},
        'select_blocks':{'decisions':'array'},
        'compare_blocks':{'pairs':'array','issues':'array'},
        'select_images':{'images':'array'},'joint_extract':{'facts':'array','conflicts':'array','coverage_note':'string'},
        'merge_paragraphs':{'topics':'array','decisions':'array','coverage_note':'string'},'verify_merged_paragraphs':{'reviews':'array','topic_reviews':'array'},
        'joint_paragraphs':{'topics':'array','coverage_note':'string'},'verify_paragraphs':{'reviews':'array','topic_reviews':'array'},
        'joint_verify':{'reviews':'array'},'merge_joint':{'duplicate_groups':'array','conflicts':'array','exclusions':'array'},
    }
    prompts={}
    for name,text in [('identity',IDENTITY),('extract',EXTRACT),('consolidate',CONSOLIDATE),('evidence',EVIDENCE),('fidelity',FIDELITY),('select_blocks',SELECT_BLOCKS),('compare_blocks',COMPARE_BLOCKS)]+[(name,load_instruction(name)) for name in ['select_images','joint_extract','joint_verify','merge_joint','joint_paragraphs','verify_paragraphs','merge_paragraphs','verify_merged_paragraphs','review_cross_batch','review_relationships','repair_topics']]:
        if name == 'select_blocks' and config.get('relevance_only', True):
            text = load_instruction('relevance')
        elif name == 'select_blocks' and config.get('body_only'):
            text = SELECT_BLOCKS_STRICT
        properties={key:({'type':'array','items':{}} if kind=='array' else {'type':kind}) for key,kind in shapes[name].items()}
        if name=='evidence':
            properties['images']['items']={'type':'object','required':['image_id','caption'],
                'properties':{'image_id':{'type':'string'},'caption':{'type':'string'}},'additionalProperties':True}
            properties['support']['items']={'type':'object',
                'required':['image_id','fact_id','status','region','supports','limitations'],
                'properties':{key:{'type':'string'} for key in ['image_id','fact_id','status','region','supports','limitations']},
                'additionalProperties':True}
        inner={'type':'object','required':list(properties),'properties':properties,'additionalProperties':True}
        template=(SYSTEM+'\n'+text+'\n将以上要求的完整对象放入响应的result属性。\n输入数据：\n{{ payload | json }}')
        if name=='evidence':
            template+='\n知识输出只从result中读取；所有images和support放在result内部。外层附加状态不构成知识或审核结论。\nimages中的每个对象必须同时有image_id和caption，image_id逐字使用输入image_ids；support每项也必须包含image_id与fact_id。\n以下图片顺序与image_ids一致；必须查看像素，图注不是证据。\n{{ images | image }}'
        if name in {'select_images','joint_extract','joint_verify','joint_paragraphs','verify_paragraphs','merge_paragraphs','verify_merged_paragraphs','repair_topics'}:
            template+='\n图片顺序与image_ids一致；以下是本次实际输入像素：\n{{ images | image }}'
        prompts[name]={'version':'joint-materials-v1' if name in {'select_images','joint_extract','joint_verify','merge_joint'} else 'concept-relevance-v1' if name == 'select_blocks' and config.get('relevance_only', True) else 'knowledge-source-blocks-strict-v3' if name == 'select_blocks' and config.get('body_only') else 'knowledge-source-blocks-v1' if name in {'select_blocks','compare_blocks'} else 'knowledge-native-evidence-v4' if name=='evidence' else 'knowledge-native-fidelity-v3' if name=='fidelity' else 'knowledge-native-extract-v2' if name=='extract' else 'knowledge-native-prompt-v1','model':{
            'name':config['model'],'transport':'openai_compatible','base_url':config['base_url'],
            'api_key_env':'CURATION_LOCAL_MODEL_KEY'},'schema_retries':0,
            'response_schema':{'type':'object','required':['result'],'additionalProperties':name in {'evidence','joint_verify'},'properties':{'result':inner}},
            'template':template}
    for name in ['joint_paragraphs', 'select_images']:
        prompts[name]['version']=yaml.safe_load((Path(__file__).parent/'prompts'/f'{name}.yaml').read_text())['version']
    text=yaml.safe_dump({'schema_version':'demiflow_prompt_pack_v2','prompts':prompts},allow_unicode=True,sort_keys=False)
    return parse_prompt_pack(text),text


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
    return {'journal_dir':str(Path(run)/'knowledge/calls'),'timeout_s':config['timeout_s'],
            'trust_env':False,'verify_model':True,'require_finish_reason_stop':True,
            'request_options':{'max_tokens':config['max_output_tokens'],'response_format':{'type':'json_object'},
                               'chat_template_kwargs':{'enable_thinking':False},
                               **({'temperature':config['temperature']} if 'temperature' in config else {})}}


def save_prompt_config(run,text,options):
    # Full prompt text/model/request policy frozen alongside the run's code manifest.
    immutable(Path(run)/'knowledge/prompt_config.json',{'yaml':text,'execution_options':options})
    path=Path(run)/'knowledge/prompts.yaml'
    if path.exists() and path.read_text()!=text:
        raise ValueError('Prompt YAML changed; use a new run')
    if not path.exists():
        temporary=path.with_suffix('.yaml.tmp');temporary.write_text(text);temporary.replace(path)
