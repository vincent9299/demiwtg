"""Run the frozen v30 candidate on all seven concepts after the unseen full run."""
import hashlib,json,shutil,subprocess,sys,time
from pathlib import Path
ROOT=Path('/yzp/zhaozy/yangzepeng/0905/demiwtg');sys.path.insert(0,str(ROOT))
from curation.v4.local_review_service import ANNOT,matching,command,ready,spawn,wait_until
base=ROOT/'state/curation/v4';up=base/'article_pipeline_upgrade';holdout=base/'article_holdout_basic_flowchart_v2'
candidate=up/'final_review_v30.yaml';candidate_hash=hashlib.sha256(candidate.read_bytes()).hexdigest()
plan=[('bench200_sample5_article_v12','bench200_sample5_article_v10',['legacy:OK手势','legacy:瓶式台球','legacy:白花芍药','legacy:高原','legacy:高锰酸钾']),('article_holdout_population_v12','article_holdout_population_v10',['legacy:人口分布图']),('article_holdout_basic_flowchart_v3','article_holdout_basic_flowchart_v2',['legacy:基本流程图'])]
(up/'final_v30_predeclared.json').write_text(json.dumps({'candidate_sha256':candidate_hash,'plan':plan,'time':time.time(),'holdout_status_at_plan':'v2 upstream image filtering; full final text/images not yet produced or read','criteria':['previous scope/identity/reference regressions','no image-only frequency/general claim','map visual encoding justified separately from variable title','same-condition source contradictions omitted, not arbitrarily chosen','preserve supported product forms/specifications','predeclared basic-flowchart six criteria']},ensure_ascii=False,indent=2)+'\n')
def clients():
    mods={'curation.v4.run_notebook_pipeline','curation.v4.article_trial'}
    return any('-m' in (a:=command(int(p.name))) and a.index('-m')+1<len(a) and a[a.index('-m')+1] in mods for p in Path('/proc').iterdir() if p.name.isdigit())
def parent_done():
    p=holdout/'manager_result.json'
    if p.exists() and json.loads(p.read_text())['exit_code']!=0:raise RuntimeError('Basic-flowchart parent failed; no automatic promotion')
    return p.exists() and (holdout/'knowledge_base.jsonl').exists() and not clients()
print('Waiting for the full basic-flowchart run; v30 candidate was frozen before seeing its output',flush=True)
wait_until(parent_done,5400,'full basic-flowchart completion')
assert ready(8000,'qwen3.8-27b') and not (ANNOT/'STOP').exists()
assert hashlib.sha256(candidate.read_bytes()).hexdigest()==candidate_hash
live=ROOT/'curation/v4/ops/prompts/final_review.yaml';shutil.copy2(live,up/'final_review_v29_before_v30.yaml');shutil.copy2(candidate,live)
owner='article_pipeline_upgrade round4\n';audit={'time':time.time(),'stop':str(ANNOT/'STOP'),'qwen_server_untouched':True,'reason':'Frozen v30 final-review regression on all seven concepts','worker':matching('curation.image_preannotate run --run '+str(ANNOT))};ap=up/'qwen_experiment_pause_round4.json'
(ANNOT/'STOP').write_text(owner);ap.write_text(json.dumps(audit,indent=2)+'\n')
try:
    wait_until(lambda:not matching('curation.image_preannotate run --run '+str(ANNOT)),600,'preannotation drain')
    wait_until(lambda:not matching('curation.image_supervisor --run '+str(ANNOT)) and not matching('bash '+str(ROOT/'curation/run_image_pipeline.sh')),120,'preannotation supervisor/launcher pause')
    processes=[]
    for target,parent,ids in plan:
        cmd=[sys.executable,'-m','curation.v4.run_notebook_pipeline','--run',str(base/target),'--reuse-extraction',str(base/parent),'--ids',*ids,'--source-scope','collected','--through','export']
        processes.append((target,spawn(cmd,up/(target+'.log'))))
    codes={name:p.wait() for name,p in processes}
    (up/'final_v30_exit_codes.json').write_text(json.dumps(codes,indent=2)+'\n')
    if any(codes.values()):raise RuntimeError('A v30 run failed: '+str(codes))
finally:
    if (ANNOT/'STOP').exists() and (ANNOT/'STOP').read_text()==owner:
        (ANNOT/'STOP').unlink();p=spawn(['bash',str(ROOT/'curation/run_image_pipeline.sh')],up/'restore_after_qwen_experiments_round4.log')
        wait_until(lambda:bool(matching('curation.image_preannotate run --run '+str(ANNOT))),120,'preannotation restore')
        audit.update(restored_time=time.time(),restored_launcher=p.pid);ap.write_text(json.dumps(audit,indent=2)+'\n')
    (up/'resource_after_v30.json').write_text(json.dumps({'qwen_healthy':ready(8000,'qwen3.8-27b'),'worker':matching('curation.image_preannotate run --run '+str(ANNOT)),'stop_exists':(ANNOT/'STOP').exists(),'time':time.time()},indent=2)+'\n')
print('All v30 final reviews completed; preannotation restored',flush=True)
