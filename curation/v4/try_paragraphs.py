"""Native demiflow paragraph trial on frozen joint inputs, no upstream reruns."""
import argparse,json,html
from pathlib import Path
from demiflow.standalone import local_data
from .contracts import immutable,digest,source_code,runtime_version,run_lock
from .pipeline import DEFAULT
from .ops.prompt_config import knowledge_prompt_pack,prompt_execution_options,save_prompt_config
from .ops.paragraphs import ApplyParagraphs,ApplyParagraphReview


def render(run):
    from .paragraph_results import export_results
    export_results([run],run/'published')


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--source',type=Path,required=True);p.add_argument('--case-id',required=True,action='append');p.add_argument('--batch-id',action='append');p.add_argument('--run',type=Path,required=True);a=p.parse_args()
    config={**DEFAULT,'max_calls':None,'max_output_tokens':12000,'timeout_s':600,'temperature':0}
    with run_lock(a.run):
        manifest={'source':str(a.source.resolve()),'source_sha256':digest(a.source.read_bytes()),'case_ids':a.case_id,'batch_ids':a.batch_id,'source_code':source_code(),'runtime':runtime_version(),'config':config,'scope':'Frozen-input paragraph extraction comparison; no cross-batch merge'}
        immutable(a.run/'manifest.json',manifest);version=digest(manifest)
        pack,text=knowledge_prompt_pack(config);options=prompt_execution_options(a.run,config);save_prompt_config(a.run,text,options)
        data=local_data(prompt_packs={'knowledge.yaml':pack},prompt_options=options)
        req=(data.read_json(str(a.source)).filter(lambda r:r['case_id'] in a.case_id and (a.batch_id is None or r['batch_id'] in a.batch_id))
             .map(lambda r:{k:r[k] for k in ['case_id','batch_id','joint_prompt','pixel_images','pixel_roles']})
             .checkpoint(a.run/'requests.jsonl',version=version))
        extracted=(req.map_prompt_async('joint_paragraphs',config='knowledge.yaml',inputs={'payload':'joint_prompt','images':'pixel_images'},output='prompt_result',call_output='prompt_call',error_output='prompt_error',concurrency=1,queue_depth=1)
                   .map_cached(ApplyParagraphs(),cache_dir=a.run/'cache/extract',version=version).checkpoint(a.run/'extracted.jsonl',version=version))
        print('extraction completed',flush=True)
        (extracted.map_prompt_async('verify_paragraphs',config='knowledge.yaml',inputs={'payload':'verify_payload','images':'pixel_images'},output='prompt_result',call_output='prompt_call',error_output='prompt_error',concurrency=1,queue_depth=1)
         .map_cached(ApplyParagraphReview(),cache_dir=a.run/'cache/verify',version=version).checkpoint(a.run/'paragraphs.jsonl',version=version))
        render(a.run);print(a.run/'published/preview.html',flush=True)

if __name__=='__main__':main()
