"""Reapply complete frozen merge outputs in a new run, without model calls."""
import argparse,json
from pathlib import Path
from demiflow.standalone import local_data
from .contracts import ROOT,digest,immutable,source_code,runtime_version,run_lock
from .ops.multimodal import ApplyJointMerge
from .ops.multimodal_replay import RestoreExactJointMerge
from .ops.prompt_operators import BuildCandidateRecords
from .final_results import BuildKnowledgeRecord


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--source-run',type=Path,required=True);p.add_argument('--run',type=Path,required=True);a=p.parse_args()
    source=a.source_run/'datasets/joint_knowledge.jsonl';parent=json.loads((a.source_run/'dataset_manifest.json').read_text())
    config=parent['config']['model_config'];inputs={str(source):digest(source.read_bytes())}
    for line in source.open():
        call=json.loads(line)['joint_merge_review']['call']
        for key in ['request_path','response_path']:
            path=Path(call[key]);path=path if path.is_absolute() else ROOT/path
            inputs[str(path)]=digest(path.read_bytes())
    with run_lock(a.run):
        manifest={'purpose':'Known singleton merge groups are no-ops; reuse exact complete inputs/responses',
                  'source_run':str(a.source_run),'source_manifest':parent,'inputs':inputs,'code':source_code(),
                  'runtime':runtime_version(),'model_calls':0}
        immutable(a.run/'manifest.json',manifest);version=digest(manifest);data=local_data()
        reviewed=(data.read_json(str(source)).map(RestoreExactJointMerge()).map(ApplyJointMerge())
                  .checkpoint(a.run/'datasets/joint_knowledge.jsonl',version=version))
        exported=(reviewed.map_cached(BuildCandidateRecords(a.run,config),cache_dir=a.run/'cache/export',version=version)
                  .checkpoint(a.run/'datasets/knowledge_export.jsonl',version=version))
        exported.map(BuildKnowledgeRecord()).checkpoint(a.run/'knowledge_base.jsonl',version=version)
        print(a.run/'knowledge_base.jsonl',flush=True)
if __name__=='__main__':main()
