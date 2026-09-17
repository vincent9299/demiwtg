"""Validate and freeze a completed isolated edit judging run."""
import argparse, datetime, hashlib, json, os, subprocess, sys, time
from pathlib import Path
from PIL import Image
EDIT=Path(__file__).resolve().parent
BASE=Path(os.environ.get('EDIT_JUDGE_RUN', str(EDIT/'bench200/scores_qib_v22_astra_medium_20260908'))).resolve()
GROUPS=os.environ.get('EDIT_JUDGE_GROUPS', 'a,b').split(',')
EXPECTED=200*len(GROUPS)
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def rows(p):return [json.loads(s) for s in Path(p).read_text().splitlines() if s.strip()]
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--wait',action='store_true');args=ap.parse_args()
 while True:
  p=BASE/'runtime/dispatch_summary.json'
  d=json.loads(p.read_text()) if p.exists() else {}
  if d.get('jobs')==EXPECTED:
   assert not d.get('stopped'), 'dispatcher stopped; resolve reported errors before freezing'
   break
  if not args.wait:raise RuntimeError('full dispatch has not completed')
  time.sleep(10)
 assert len(list((BASE/'runtime/provenance').glob('*.json')))==EXPECTED
 thread_ids=[];artifacts={};configs=set()
 for g in GROUPS:
  manifest=rows(BASE/g/'blind_manifest.jsonl')
  assert len(manifest)==len({r['qid'] for r in manifest})==200
  assert len(list((BASE/g/'raw').glob('*.txt')))==200
  for r in manifest:
   p=BASE/'runtime/provenance'/f'{g}_{r["qid"]}.json';v=json.loads(p.read_text())
   assert v['context_verified'] and v['tool_calls']==0 and v['input_hashes']==r['inputs']
   assert v['raw_sha256']==sha(BASE/g/'raw'/f'{r["qid"]}.txt')
   assert v['prompt_sha256']==sha(BASE/g/'prompts'/f'{r["qid"]}.txt')
   assert v['session_sha256']==sha(v['session_path'])
   for role,key in [('before','source_sha256'),('after','output_sha256')]:
    assert sha(r[role])==r['inputs'][key]
    with Image.open(r[role]) as image:image.verify()
   configs.add((v['model'],v['reasoning_effort'],v['provider']))
   thread_ids.append(v['thread_id'])
  artifacts[f'{g}/blind_manifest.jsonl']=sha(BASE/g/'blind_manifest.jsonl')
 assert len(set(thread_ids))==EXPECTED and configs=={('gpt-6-astra','medium','openai')}
 assert sha(BASE/'prompt_snapshot.md')=='283d2af57c859df26b96c64149b1182a5f8a4c69da9115af39a5e9a3351da751'
 pipeline=EDIT/'eval_codex_score.py';questions=EDIT/'bench200/questions.jsonl'
 def run(label,cmd):
  p=subprocess.run([sys.executable,str(pipeline),*map(str,cmd)],capture_output=True,text=True)
  (BASE/'runtime'/f'finalize_{label}.txt').write_text(p.stdout+'\n'+p.stderr)
  assert p.returncode==0,f'{label} failed: {p.stderr[-1500:]}'
 for g in GROUPS:
  root=BASE/g
  run(g+'_ingest',['ingest','--format','json','--judge','gpt-6-astra-medium-built-in','--manifest',root/'blind_manifest.jsonl','--raw-dir',root/'raw','--out-dir',root/'parts'])
  run(g+'_aggregate',['aggregate','--questions',questions,'--manifest',root/'blind_manifest.jsonl','--scores-dir',root/'parts','--out-dir',root])
  assert len(rows(root/'scores.jsonl'))==200
  for f in ['scores.jsonl','report.json']:artifacts[g+'/'+f]=sha(root/f)
 if GROUPS==['a','b']:
  run('compare',['compare','--questions',questions,'--left-scores',BASE/'a/scores.jsonl','--right-scores',BASE/'b/scores.jsonl','--left-name','Qwen-Image-Edit-2511','--right-name','BAGEL-7B-MoT','--out-dir',BASE/'comparison'])
  for f in ['paired_scores.jsonl','report.json']:artifacts['comparison/'+f]=sha(BASE/'comparison'/f)
  report=json.loads((BASE/'comparison/report.json').read_text())
  assert report['overall']['n']+len(report['excluded'])==200
 else:
  statuses=[r['validity']['status'] for g in GROUPS for r in rows(BASE/g/'scores.jsonl')]
  excluded=sum(v not in ['ok','model_failure'] for v in statuses)
  report={'overall':{'n':200-excluded},'excluded':[None]*excluded}
 frozen={'status':'frozen','frozen_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'n_questions':200,'n_candidates':EXPECTED,'n_unique_contexts':EXPECTED,'judge_model':'gpt-6-astra','reasoning_effort':'medium','provider':'openai','launcher':'fresh Codex exec child process','template_sha256':sha(BASE/'prompt_snapshot.md'),'questions_sha256':sha(questions),'pipeline_sha256':sha(pipeline),'artifacts':artifacts,'paired_n':report['overall']['n'],'excluded_n':len(report['excluded']),'setup_probes_excluded':True}
 p=BASE/'runtime/frozen.json';t=p.with_suffix('.tmp');t.write_text(json.dumps(frozen,ensure_ascii=False,indent=2));t.replace(p)
 print(json.dumps({'status':'frozen','candidates':EXPECTED,'report':report['overall'],'excluded':len(report['excluded'])},ensure_ascii=False),flush=True)
if __name__=='__main__':main()
