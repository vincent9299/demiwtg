"""Frozen concept-conditioned image selection model comparison, native demiflow."""
import argparse,json
from pathlib import Path
from demiflow.standalone import local_data
from .contracts import immutable,digest,source_code,runtime_version,run_lock
from .pipeline import DEFAULT
from .compare_fidelity import MODEL_DIRS
from .ops.prompt_config import knowledge_prompt_pack,prompt_execution_options,save_prompt_config
from .ops.multimodal import ApplyImageSelection

def main():
 p=argparse.ArgumentParser();p.add_argument('--inputs',type=Path,required=True);p.add_argument('--run',type=Path,required=True);p.add_argument('--model',choices=MODEL_DIRS,required=True);p.add_argument('--port',type=int,default=8001);p.add_argument('--concurrency',type=int,default=2);a=p.parse_args()
 if a.concurrency<1:raise ValueError('concurrency must be positive')
 cfg={**DEFAULT,'model':a.model,'base_url':f'http://127.0.0.1:{a.port}/v1','local_model_comparison':True,'max_calls':None,'max_output_tokens':4096,'timeout_s':600,'temperature':0,'comparison_concurrency':a.concurrency}
 with run_lock(a.run):
  pack,text=knowledge_prompt_pack(cfg)
  model=Path(__file__).resolve().parents[3]/'models'/MODEL_DIRS[a.model]
  manifest={'input':str(a.inputs.resolve()),'sha256':digest(a.inputs.read_bytes()),'config':cfg,'prompt':text,'code':source_code(),'runtime':runtime_version(),'model_config':(model/'config.json').read_text()}
  immutable(a.run/'manifest.json',manifest);version=digest(manifest)
  options=prompt_execution_options(a.run,cfg);save_prompt_config(a.run,text,options)
  data=local_data(prompt_packs={'knowledge.yaml':pack},prompt_options=options)
  (data.read_json(str(a.inputs)).map_prompt_async('select_images',config='knowledge.yaml',inputs={'payload':'image_prompt','images':'pixel_images'},output='prompt_result',call_output='prompt_call',error_output='prompt_error',concurrency=a.concurrency,queue_depth=a.concurrency)
   .map_cached(ApplyImageSelection(),cache_dir=a.run/'cache/apply',version=version).checkpoint(a.run/'decisions.jsonl',version=version))
 print('Completed image filtering',a.model,flush=True)
if __name__=='__main__':main()
