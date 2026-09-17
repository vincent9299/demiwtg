"""Frozen single-image blind/target-conditioned local vision comparison, native demiflow."""
import argparse,json
from pathlib import Path
import yaml
from demiflow.standalone import local_data
from demiflow.operator_llm.parser import parse_prompt_pack
from .contracts import immutable,digest,source_code,runtime_version,run_lock
from .pipeline import DEFAULT
from .compare_fidelity import MODEL_DIRS
from .ops.prompt_config import prompt_execution_options,save_prompt_config
from .ops.prompt_loader import load_instruction


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--inputs',type=Path,required=True);p.add_argument('--run',type=Path,required=True)
    p.add_argument('--model',choices=MODEL_DIRS,required=True);p.add_argument('--port',type=int,default=8001)
    a=p.parse_args();config={**DEFAULT,'model':a.model,'base_url':f'http://127.0.0.1:{a.port}/v1',
        'local_model_comparison':True,'max_calls':20,'max_output_tokens':1600,'timeout_s':300,'temperature':0}
    prompts={}
    for stage,fields in [('observe_image',{'objects':{'type':'array','items':{}},'summary':{'type':'string'}}),
                         ('identify_image',{k:{'type':'string'} for k in ['observed_object','visible_features','alternatives','relationship','reason','limitations']})]:
        prompts[stage]={'version':stage+'-v1','model':{'name':a.model,'transport':'openai_compatible','base_url':config['base_url'],'api_key_env':'CURATION_LOCAL_MODEL_KEY'},'schema_retries':0,
          'response_schema':{'type':'object','required':['result'],'properties':{'result':{'type':'object','required':list(fields),'properties':fields}},'additionalProperties':False},
          'template':load_instruction(stage)+'\n将完整对象放在result属性。输入：{{ payload | json }}\n实际图片：{{ images | image }}'}
    text=yaml.safe_dump({'schema_version':'demiflow_prompt_pack_v2','prompts':prompts},allow_unicode=True)
    model=Path(__file__).resolve().parents[3]/'models'/MODEL_DIRS[a.model]
    with run_lock(a.run):
        manifest={'input':str(a.inputs.resolve()),'input_sha256':digest(a.inputs.read_bytes()),'config':config,
          'prompt_text':text,'code':source_code(),'runtime':runtime_version(),'model_config':(model/'config.json').read_text(),
          'weights':[{'name':f.name,'size':f.stat().st_size,'mtime_ns':f.stat().st_mtime_ns} for f in sorted(model.glob('*.safetensors'))],
          'scope':'Ten fixed actual images, two separate conditions, no previous captions/decisions, no tuning per model. Diagnostic not representative benchmark.'}
        immutable(a.run/'manifest.json',manifest);version=digest(manifest);options=prompt_execution_options(a.run,config);save_prompt_config(a.run,text,options)
        data=local_data(prompt_packs={'vision.yaml':parse_prompt_pack(text)},prompt_options=options,max_prompt_requests=20)
        inputs=data.read_json(str(a.inputs)).checkpoint(a.run/'inputs.jsonl',version=version)
        for stage in ['observe_image','identify_image']:
            (inputs.map(lambda r:{'sample_id':r['sample_id'],'image_id':r['image_id'],'pixel_images':r['pixel_images'],
                'payload':{'sample_id':r['sample_id'],**({'concept':r['concept'],'identity':r['identity']} if stage=='identify_image' else {})}})
             .map_prompt_async(stage,config='vision.yaml',inputs={'payload':'payload','images':'pixel_images'},output='result',call_output='call',error_output='error',concurrency=1,queue_depth=1)
             .checkpoint(a.run/(stage+'.jsonl'),version=version))
        print('Completed vision comparison',a.model,flush=True)

if __name__=='__main__':main()
