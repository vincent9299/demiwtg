"""Bounded validation of the new operator on an immutable existing candidate checkpoint.

This is a stage calibration tool, not a replacement for the notebook's raw-data pipeline.
Usage: python -m curation.v4.review_saved_fidelity --source ... --run ...
"""
import argparse
from pathlib import Path
from demiflow.standalone import local_data
from .contracts import digest, immutable, source_code, runtime_version, run_lock
from .pipeline import DEFAULT
from .ops.prompt_config import knowledge_prompt_pack, prompt_execution_options, save_prompt_config
from .ops.fidelity import PrepareFidelity, ApplyFidelity
from .ops.prompt_operators import BuildCandidateRecords
from .final_results import BuildKnowledgeRecord


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,required=True)
    parser.add_argument('--run',type=Path,required=True)
    args=parser.parse_args();run=args.run.resolve();source=args.source.resolve()
    with run_lock(run):
        config={**DEFAULT,'max_calls':1,'max_output_tokens':6000}
        manifest={'purpose':'Existing candidate fidelity calibration only; no raw re-scan or upstream/image calls',
                  'source':str(source),'source_sha256':digest(source.read_bytes()),
                  'source_code':source_code(),'runtime':runtime_version(),'config':config}
        immutable(run/'manifest.json',manifest);version=digest(manifest)
        pack,text=knowledge_prompt_pack(config);options=prompt_execution_options(run,config)
        save_prompt_config(run,text,options)
        data=local_data(prompt_packs={'knowledge.yaml':pack},max_prompt_requests=1,prompt_options=options)
        reviewed=(data.read_json(str(source))
            .map_cached(PrepareFidelity(run,config),cache_dir=run/'cache/PrepareFidelity',version=version)
            .map_prompt_async('fidelity',config='knowledge.yaml',inputs={'payload':'fidelity_prompt'},
                output='prompt_result',call_output='prompt_call',error_output='prompt_error',
                when=lambda r:not r.get('blocked') and 'fidelity_prompt' in r,concurrency=1,queue_depth=1)
            .map_cached(ApplyFidelity(run,config),cache_dir=run/'cache/ApplyFidelity',version=version)
            .checkpoint(run/'datasets/knowledge_fidelity.jsonl',version=version))
        # Existing pixel judgments are preserved as historical relationships; positive selection
        # is recomputed against retained fact IDs. No fact content was rewritten.
        (reviewed.map_cached(BuildCandidateRecords(run,config),cache_dir=run/'cache/BuildCandidateRecords',version=version)
            .checkpoint(run/'datasets/knowledge_export.jsonl',version=version)
            .map(BuildKnowledgeRecord()).checkpoint(run/'knowledge_base.jsonl',version=version))
        print(run/'knowledge_base.jsonl')


if __name__=='__main__':main()
