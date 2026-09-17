"""Run the same frozen fidelity inputs through one explicitly selected local model."""
import argparse
from pathlib import Path
from demiflow.standalone import local_data
from .contracts import digest, immutable, source_code, runtime_version, run_lock
from .pipeline import DEFAULT
from .ops.prompt_config import knowledge_prompt_pack, prompt_execution_options, save_prompt_config, LOCAL_REVIEW_MODELS
from .ops.fidelity import PrepareFidelity, ApplyFidelity

MODEL_DIRS={'qwen3.8-27b':'Qwen3.8-27B','qwen3.6-35b-a3b':'Qwen3.6-35B-A3B',
            'gemma-4-26b-a4b-it':'gemma-4-26B-A4B-it','gemma-4-31b-it':'gemma-4-31B-it'}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inputs',type=Path,required=True)
    parser.add_argument('--run',type=Path,required=True)
    parser.add_argument('--model',choices=sorted(LOCAL_REVIEW_MODELS),required=True)
    parser.add_argument('--port',type=int,default=8001)
    args=parser.parse_args();run=args.run.resolve();source=args.inputs.resolve()
    model_dir=Path(__file__).resolve().parents[3]/'models'/MODEL_DIRS[args.model]
    config={**DEFAULT,'model':args.model,'base_url':f'http://127.0.0.1:{args.port}/v1',
            'local_model_comparison':True,'max_calls':2,'max_output_tokens':8192,'timeout_s':600,'temperature':0}
    with run_lock(run):
        manifest={'purpose':'Two fixed fidelity batches, no prompt tuning or generation',
                  'source':str(source),'source_sha256':digest(source.read_bytes()),'config':config,
                  'source_code':source_code(),'runtime':runtime_version(),
                  'model_directory':str(model_dir),'model_config':(model_dir/'config.json').read_text(),
                  'weight_files':[{'name':p.name,'size':p.stat().st_size,'mtime_ns':p.stat().st_mtime_ns}
                                  for p in sorted(model_dir.glob('*.safetensors'))]}
        immutable(run/'manifest.json',manifest);version=digest(manifest)
        pack,text=knowledge_prompt_pack(config);options=prompt_execution_options(run,config)
        save_prompt_config(run,text,options)
        data=local_data(prompt_packs={'knowledge.yaml':pack},max_prompt_requests=2,prompt_options=options)
        (data.read_json(str(source))
            .map_cached(PrepareFidelity(run,config),cache_dir=run/'cache/prepare',version=version)
            .map_prompt_async('fidelity',config='knowledge.yaml',inputs={'payload':'fidelity_prompt'},
                output='prompt_result',call_output='prompt_call',error_output='prompt_error',
                when=lambda r:not r.get('blocked') and 'fidelity_prompt' in r,concurrency=1,queue_depth=1)
            .map_cached(ApplyFidelity(run,config),cache_dir=run/'cache/apply',version=version)
            .checkpoint(run/'reviewed.jsonl',version=version))
        print('Finished',args.model,flush=True)


if __name__=='__main__':main()
