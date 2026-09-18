import json,subprocess,sys
from pathlib import Path
ROOT=Path('/yzp/zhaozy/yangzepeng/0905/demiwtg');sys.path.insert(0,str(ROOT))
from curation.v4.local_review_service import wait_until,command
up=ROOT/'state/curation/v4/article_pipeline_upgrade';audit=up/'final_v31_resource.json'
def previous_done():
 r=json.loads(audit.read_text())
 if 'restored_time' not in r:return False
 if any(r.get('exit_codes',{}).values()):raise RuntimeError('Previous native run did not complete')
 for p in Path('/proc').iterdir():
  if not p.name.isdigit():continue
  a=command(int(p.name))
  if '-m' in a and a[a.index('-m')+1] in {'curation.v4.run_notebook_pipeline','curation.v4.article_trial'}:return False
 return True
print('Waiting for v31 completion/restoration before the declared v32 input experiment',flush=True)
wait_until(previous_done,3600,'v31 completion and owned pause restoration')
raise SystemExit(subprocess.run([sys.executable,str(up/'run_final_candidate.py'),str(up/'final_v32_plan.json')],cwd=ROOT).returncode)
