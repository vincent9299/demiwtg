"""Run a declared final-review candidate and restore the owned background pause."""
import argparse,hashlib,json,shutil,sys,time
from pathlib import Path
ROOT=Path('/yzp/zhaozy/yangzepeng/0905/demiwtg');sys.path.insert(0,str(ROOT))
from curation.v4.local_review_service import ANNOT,matching,command,ready,spawn,wait_until
p=argparse.ArgumentParser();p.add_argument('plan');args=p.parse_args()
planpath=Path(args.plan).resolve();plan=json.loads(planpath.read_text());up=planpath.parent;base=ROOT/'state/curation/v4'
for proc in Path('/proc').iterdir():
 if not proc.name.isdigit():continue
 argv=command(int(proc.name))
 if '-m' in argv and argv[argv.index('-m')+1] in {'curation.v4.run_notebook_pipeline','curation.v4.article_trial'}:raise RuntimeError('Another model experiment is active')
assert ready(8000,'qwen3.8-27b') and not (ANNOT/'STOP').exists()
candidate=Path(plan['prompt']);candidate_hash=hashlib.sha256(candidate.read_bytes()).hexdigest()
assert candidate_hash==plan['prompt_sha256']
live=ROOT/'curation/v4/ops/prompts/final_review.yaml';shutil.copy2(candidate,live)
if plan.get('joint_prompt'):
 joint=Path(plan['joint_prompt']);assert hashlib.sha256(joint.read_bytes()).hexdigest()==plan['joint_prompt_sha256']
 shutil.copy2(joint,ROOT/'curation/v4/ops/prompts/article_joint.yaml')
if plan.get('intended_config'):
 from curation.v4.run_notebook_pipeline import load_pipeline
 current=load_pipeline().__globals__['MODEL_CONFIG']
 assert all(current.get(k)==v for k,v in plan['intended_config'].items()),'Notebook configuration differs from declared plan'
 from transformers import AutoTokenizer
 tokenizer=AutoTokenizer.from_pretrained(ROOT.parent/'models/Qwen3.8-27B',local_files_only=True)
 for effort in [current.get('joint_reasoning_effort','low'),current.get('final_review_effort','low')]:
  tokenizer.apply_chat_template([{'role':'user','content':'configuration validation'}],tokenize=False,add_generation_prompt=True,enable_thinking=True,reasoning_effort=effort)
 for entry in plan.get('diagnostics', []):
  tokenizer.apply_chat_template([{'role':'user','content':'configuration validation'}],tokenize=False,add_generation_prompt=True,enable_thinking=True,reasoning_effort=entry['effort'])
name=plan['name'];owner=name+'\n';audit={'time':time.time(),'stop':str(ANNOT/'STOP'),'owner':owner,'qwen_server_untouched':True,'plan':str(planpath),'worker':matching('curation.image_preannotate run --run '+str(ANNOT))};ap=up/(name+'_resource.json')
(ANNOT/'STOP').write_text(owner);ap.write_text(json.dumps(audit,indent=2)+'\n')
processes=[]
try:
 wait_until(lambda:not matching('curation.image_preannotate run --run '+str(ANNOT)),600,'preannotation drain')
 wait_until(lambda:not matching('curation.image_supervisor --run '+str(ANNOT)) and not matching('bash '+str(ROOT/'curation/run_image_pipeline.sh')),120,'preannotation supervisor/launcher pause')
 for entry in plan['runs']:
  target=entry['target'];parent=entry['parent']
  reuse_stage=entry.get('reuse_stage','extraction');assert reuse_stage in {'materials','extraction'}
  cmd=[sys.executable,'-m','curation.v4.run_notebook_pipeline','--run',str(base/target),'--reuse-'+reuse_stage,str(base/parent),'--ids',*entry['ids'],'--source-scope','collected','--through','export']
  processes.append((target,spawn(cmd,up/(target+'.log'))))
 for entry in plan.get('diagnostics', []):
  target=entry['target'];assert not (base/target).exists()
  cmd=[sys.executable,'-m','curation.v4.article_trial','--run',str(base/target),'--parent',str(base/entry['parent']),
       '--mode','reuse-extraction','--concepts',*entry['concepts'],'--thinking','--reasoning-effort',entry['effort'],
       '--max-output-tokens','65536','--timeout-s','1800','--review-prompt',str(candidate)]
  processes.append((target,spawn(cmd,up/(target+'.log'))))
 print('Started '+', '.join(n for n,_ in processes),flush=True)
 codes={name:p.wait() for name,p in processes};audit['exit_codes']=codes
 if any(codes.values()):raise RuntimeError('A native run failed: '+str(codes))
finally:
 # Never restore a competing worker while any client from this plan still runs.
 for _,client in processes:client.wait()
 if (ANNOT/'STOP').exists() and (ANNOT/'STOP').read_text()==owner:
  (ANNOT/'STOP').unlink();p=spawn(['bash',str(ROOT/'curation/run_image_pipeline.sh')],up/(name+'_restore.log'))
  wait_until(lambda:bool(matching('curation.image_preannotate run --run '+str(ANNOT))),120,'preannotation restore')
  audit.update(restored_time=time.time(),restored_launcher=p.pid)
 audit.update(qwen_healthy=ready(8000,'qwen3.8-27b'),worker_after=matching('curation.image_preannotate run --run '+str(ANNOT)),stop_exists=(ANNOT/'STOP').exists());ap.write_text(json.dumps(audit,indent=2)+'\n')
print('Completed; preannotation restored',flush=True)
