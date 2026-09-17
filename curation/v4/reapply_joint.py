"""New immutable run, saved complete responses, zero model transport calls."""
import argparse,json
from pathlib import Path
from demiflow.standalone import local_data
from .contracts import digest,immutable,source_code,runtime_version,run_lock
from .ops.multimodal import ApplyJointVerification,merge_joint_batches,PrepareJointMerge,ApplyJointMerge,merge_joint_scope
from .ops.multimodal_replay import RestoreJointVerification,RestoreJointMerge
from .ops.prompt_operators import BuildCandidateRecords
from .final_results import BuildKnowledgeRecord


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--source-run',type=Path,required=True);p.add_argument('--run',type=Path,required=True);a=p.parse_args()
    with run_lock(a.run):
        files=list((a.source_run/'datasets').glob('*.jsonl'))+list((a.source_run/'knowledge/calls').glob('*.json'))
        parent=json.loads((a.source_run/'manifest.json').read_text());config=parent['config']
        manifest={'purpose':'Revalidate saved result ignoring auxiliary outer scope; no new model calls',
                  'inputs':{str(f):digest(f.read_bytes()) for f in files},'source_manifest':parent,
                  'code':source_code(),'runtime':runtime_version(),'model_calls':0}
        immutable(a.run/'manifest.json',manifest);version=digest(manifest);data=local_data();old=a.source_run/'datasets';out=a.run/'datasets'
        extracted=data.read_json(str(old/'joint_extract.jsonl'))
        saved=(data.read_json(str(old/'joint_verify.jsonl'))
            .map(lambda r:{'case_id':r['case_id'],'saved_verification_calls':r['joint_verification_calls']}))
        # This normalization trial is explicitly one joint group, so case_id join is one-to-one.
        if len(extracted.take(2))!=1 or len(saved.take(2))!=1:raise ValueError('Use batch-keyed replay for multiple joint groups')
        verified=(extracted.join(saved,on='case_id').map(RestoreJointVerification()).map(ApplyJointVerification())
                  .checkpoint(out/'joint_verify.jsonl',version=version).reduce_by_key('case_id',merge_joint_batches))
        scope=(data.read_json(str(old/'joint_requests.jsonl')).map(lambda r:{'case_id':r['case_id'],'planned_joint_batch_ids':[r['batch_id']]})
               .reduce_by_key('case_id',merge_joint_scope))
        merge_saved=data.read_json(str(old/'joint_knowledge.jsonl')).map(lambda r:{'case_id':r['case_id'],'saved_merge_review':r['joint_merge_review']})
        related=data.read_json(str(old/'related_materials.jsonl'))
        requests=related.join(verified,on='case_id',how='left').join(scope,on='case_id',how='left').join(merge_saved,on='case_id')
        requests=requests.map(lambda r:{**r,'joint_batch_limit':config.get('joint_batch_limit')}).map(PrepareJointMerge())
        reviewed=requests.map(RestoreJointMerge()).map(ApplyJointMerge()).checkpoint(out/'joint_knowledge.jsonl',version=version)
        exported=reviewed.map_cached(BuildCandidateRecords(a.run,config),cache_dir=a.run/'cache/export',version=version).checkpoint(out/'knowledge_export.jsonl',version=version)
        exported.map(BuildKnowledgeRecord()).checkpoint(a.run/'knowledge_base.jsonl',version=version)
        print(a.run/'knowledge_base.jsonl')
if __name__=='__main__':main()
