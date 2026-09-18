"""Sequential native dual-image trials; service lifecycle stays with native pipeline."""
import argparse,hashlib,json,shutil,subprocess,sys,time
from pathlib import Path
ROOT=Path('/yzp/zhaozy/yangzepeng/0905/demiwtg');sys.path.insert(0,str(ROOT))
from curation.v4.local_review_service import ANNOT,matching,command,ready
from curation.v4.notebook_io import frozen_filter_inputs
from curation.v4.run_notebook_pipeline import load_pipeline
p=argparse.ArgumentParser();p.add_argument('plan');a=p.parse_args()
pp=Path(a.plan).resolve();plan=json.loads(pp.read_text());base=ROOT/'state/curation/v4';up=pp.parent
for proc in Path('/proc').iterdir():
 if not proc.name.isdigit():continue
 argv=command(int(proc.name))
 if '-m' in argv and argv[argv.index('-m')+1] in {'curation.v4.run_notebook_pipeline','curation.v4.article_trial'}:raise RuntimeError('Another model experiment is active')
assert ready(8000,'qwen3.8-27b') and not (ANNOT/'STOP').exists()
g=load_pipeline().__globals__;cfg={**g['DEFAULT'],**g['IMAGE_FILTER_DEFAULTS'],**g['MODEL_CONFIG'],'reuse_text_selection':True}
audit={'plan':str(pp),'started':time.time(),'runs':[],'outer_stop_created':False}
for entry in plan['runs']:
 assert not (base/entry['target']).exists(),entry['target']
 entry['reuse_validation']=frozen_filter_inputs(base/entry['parent'],entry['ids'],cfg)
for name,source in plan['prompts'].items():
 path=Path(source);assert hashlib.sha256(path.read_bytes()).hexdigest()==plan['sha256'][name]
for name,source in plan['prompts'].items():shutil.copy2(source,ROOT/'curation/v4/ops/prompts'/name)
ap=up/(plan['name']+'_resource.json')
def save():ap.write_text(json.dumps(audit,ensure_ascii=False,indent=2)+'\n')
save()
try:
 for entry in plan['runs']:
  target=entry['target']; cmd=[sys.executable,'-m','curation.v4.run_notebook_pipeline','--run',str(base/target),'--reuse-filter-inputs',str(base/entry['parent']),'--reuse-text-selection','--ids',*entry['ids'],'--source-scope','collected','--through','export']
  row={'target':target,'parent':entry['parent'],'start':time.time(),'reuse_validation':entry['reuse_validation']};audit['runs'].append(row);save();print('Starting '+target,flush=True)
  with (up/(target+'.log')).open('w') as log:
   result=subprocess.run(cmd,cwd=ROOT,stdout=log,stderr=subprocess.STDOUT)
  row.update(exit_code=result.returncode,end=time.time(),qwen_healthy=ready(8000,'qwen3.8-27b'),stop_exists=(ANNOT/'STOP').exists(),worker=matching('curation.image_preannotate run --run '+str(ANNOT)));save()
  if result.returncode or not row['qwen_healthy'] or row['stop_exists']:raise RuntimeError('Native run failed or resource restoration incomplete: '+target)
  print('Completed '+target,flush=True)
finally:
 audit.update(ended=time.time(),qwen_healthy=ready(8000,'qwen3.8-27b'),stop_exists=(ANNOT/'STOP').exists(),worker=matching('curation.image_preannotate run --run '+str(ANNOT)));save()
