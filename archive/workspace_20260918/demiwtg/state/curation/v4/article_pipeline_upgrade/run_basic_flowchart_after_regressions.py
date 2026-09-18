"""Finish the owned pause, then execute the predeclared unseen concept once."""
import json
from pathlib import Path
import subprocess
import sys
import time
ROOT=Path('/yzp/zhaozy/yangzepeng/0905/demiwtg')
sys.path.insert(0,str(ROOT))
from curation.v4.local_review_service import ANNOT,matching,command,ready,spawn,wait_until
base=ROOT/'state/curation/v4';up=base/'article_pipeline_upgrade'
parents=['bench200_sample5_article_v11','article_holdout_population_v11']
def active_clients():
    modules={'curation.v4.run_notebook_pipeline','curation.v4.article_trial'}
    for proc in Path('/proc').iterdir():
        if not proc.name.isdigit():continue
        argv=command(int(proc.name))
        if '-m' in argv and argv.index('-m')+1<len(argv) and argv[argv.index('-m')+1] in modules:return True
    return False
print('Waiting for both frozen final-review regressions and their clients',flush=True)
wait_until(lambda:all((base/n/'knowledge_base.jsonl').exists() for n in parents) and not active_clients(),2400,'Qwen regression completion')
assert ready(8000,'qwen3.8-27b')
p=up/'qwen_experiment_pause_round3.json';audit=json.loads(p.read_text())
assert 'restored_time' not in audit
assert Path(audit['stop'])==ANNOT/'STOP'
assert (ANNOT/'STOP').read_text()=='article_pipeline_upgrade round3\n'
assert not matching('curation.image_preannotate run --run '+str(ANNOT))
assert not matching('curation.image_supervisor --run '+str(ANNOT))
assert not matching('bash '+str(ROOT/'curation/run_image_pipeline.sh'))
(ANNOT/'STOP').unlink()
launcher=spawn(['bash',str(ROOT/'curation/run_image_pipeline.sh')],up/'restore_after_qwen_experiments_round3.log')
wait_until(lambda:bool(matching('curation.image_preannotate run --run '+str(ANNOT))),120,'preannotation restoration')
audit.update(restored_time=time.time(),restored_launcher=launcher.pid)
p.write_text(json.dumps(audit,ensure_ascii=False,indent=2)+'\n')
print('Round3 pause restored; starting the predeclared full basic-flowchart run',flush=True)
run=base/'article_holdout_basic_flowchart_v2'
cmd=[sys.executable,'-m','curation.v4.run_notebook_pipeline','--run',str(run),'--reuse-filter-inputs',str(base/'article_holdout_basic_flowchart_v1'),'--ids','legacy:基本流程图','--source-scope','collected','--through','export']
with (up/'holdout_basic_flowchart_v2.log').open('ab') as out:result=subprocess.run(cmd,cwd=ROOT,stdout=out,stderr=out)
(run/'manager_result.json').write_text(json.dumps({'exit_code':result.returncode,'qwen_healthy':ready(8000,'qwen3.8-27b'),'worker':matching('curation.image_preannotate run --run '+str(ANNOT)),'stop_exists':(ANNOT/'STOP').exists(),'time':time.time()},indent=2)+'\n')
print('Basic-flowchart run finished, exit='+str(result.returncode),flush=True)
raise SystemExit(result.returncode)
