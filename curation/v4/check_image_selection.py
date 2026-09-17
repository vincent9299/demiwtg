"""Recheck image selection on a frozen material boundary, without text calls."""
import argparse
from pathlib import Path
from demiflow.standalone import local_data
from .contracts import immutable,digest,source_code,runtime_version,run_lock
from .pipeline import DEFAULT
from .ops.prompt_config import knowledge_prompt_pack,prompt_execution_options,save_prompt_config
from .ops.multimodal import SelectAvailableImages,BatchImageSelection,ApplyImageSelection


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--source',type=Path,required=True);p.add_argument('--run',type=Path,required=True);a=p.parse_args()
    config={**DEFAULT,'max_calls':None,'max_output_tokens':8192,'timeout_s':600,'temperature':0}
    with run_lock(a.run):
        manifest={'source':str(a.source),'source_sha256':digest(a.source.read_bytes()),'source_code':source_code(),'runtime':runtime_version(),'config':config,'scope':'Image-filter regression on frozen material boundary only'}
        immutable(a.run/'manifest.json',manifest);version=digest(manifest)
        pack,text=knowledge_prompt_pack(config);options=prompt_execution_options(a.run,config);save_prompt_config(a.run,text,options)
        data=local_data(prompt_packs={'knowledge.yaml':pack},prompt_options=options)
        requests=(data.read_json(str(a.source)).map(SelectAvailableImages()).flat_map(BatchImageSelection())
                  .checkpoint(a.run/'requests.jsonl',version=version))
        (requests.map_prompt_async('select_images',config='knowledge.yaml',inputs={'payload':'image_prompt','images':'pixel_images'},output='prompt_result',call_output='prompt_call',error_output='prompt_error',concurrency=1,queue_depth=1)
         .map_cached(ApplyImageSelection(),cache_dir=a.run/'cache/apply',version=version).checkpoint(a.run/'image_selection.jsonl',version=version))
        print(a.run/'image_selection.jsonl',flush=True)

if __name__=='__main__':main()
