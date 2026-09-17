"""Dispatch isolated built-in Codex judges and verify their recorded context."""
import argparse, concurrent.futures, hashlib, json, os, random, subprocess, threading, time
from pathlib import Path
EDIT = Path(__file__).resolve().parent
BASE = Path(os.environ.get('EDIT_JUDGE_RUN', str(EDIT/'bench200/scores_qib_v22_astra_medium_20260908'))).resolve()
GROUPS = os.environ.get('EDIT_JUDGE_GROUPS', 'a,b').split(',')
CLI = os.environ.get('EDIT_JUDGE_CODEX', str(next(iter(sorted((Path.home()/'.vscode-server/extensions').glob('openai.chatgpt-*/bin/linux-x86_64/codex'), reverse=True)), 'codex')))
RUNTIME = BASE/'runtime'
CODEX_DIR = Path(os.environ.get('CODEX_HOME',str(Path.home()/'.codex')))
LOCK = threading.Lock()
STOP = threading.Event()
def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def write(p,x):
 p=Path(p);p.parent.mkdir(parents=True,exist_ok=True); t=p.with_suffix(p.suffix+'.tmp');t.write_text(json.dumps(x,ensure_ascii=False,indent=2));t.replace(p)
def rows(p):return [json.loads(s) for s in Path(p).read_text().splitlines() if s.strip()]
def command(row,out):
 cmd=[CLI,'exec','--ignore-user-config','--ignore-rules','--skip-git-repo-check','--strict-config','-C',str(RUNTIME/'empty'),'-m','gpt-6-astra']
 cfg={'model_reasoning_effort':'medium','project_doc_max_bytes':0,'developer_instructions':'','web_search':'disabled','model_catalog_json':str(RUNTIME/'model_catalog.json'),'suppress_unstable_features_warning':True}
 for k,v in cfg.items(): cmd += ['-c',k+'='+json.dumps(v)]
 skills=list((CODEX_DIR/'skills').rglob('SKILL.md'))
 cmd+=['-c','skills.config=['+','.join('{path='+json.dumps(str(p))+',enabled=false}' for p in skills)+']']
 for f in ['plugins','apps','memories','hooks','shell_tool','multi_agent','image_generation','browser_use','computer_use','workspace_dependencies','skill_search']:cmd+=['--disable',f]
 cmd+=['--enable','skip_host_skill_discovery','-s','read-only','--json','-i',row['before'],'-i',row['after'],'-o',str(out),'-']
 return cmd

def audit(event_path,prompt,row):
 events=rows(event_path)
 starts=[e for e in events if e['type']=='thread.started']
 assert len(starts)==1,starts
 tid=starts[0]['thread_id']
 paths=list((CODEX_DIR/'sessions').rglob('*'+tid+'*.jsonl'));assert len(paths)==1,(tid,paths)
 transcript=rows(paths[0]);context=[r['payload'] for r in transcript if r['type']=='turn_context'];assert len(context)==1
 ctx=context[0];assert ctx['model']=='gpt-6-astra' and ctx['effort']=='medium'
 assert not ctx.get('user_instructions') and not ctx.get('developer_instructions')
 messages=[r['payload'] for r in transcript if r['type']=='response_item' and r['payload'].get('type')=='message']
 users=[m for m in messages if m['role']=='user']
 assert len(users)==2,len(users)
 assert users[0]['content'][0]['text'].startswith('<environment_context>')
 u=users[1]['content']; texts=[c.get('text','') for c in u if c['type']=='input_text']
 assert texts[-1]==prompt,'prompt text changed'
 assert sum(c['type']=='input_image' for c in u)==2
 assert row['before'] in texts[0] and row['after'] in texts[2],texts[:4]
 assert len(texts)==5,len(texts)
 dev='\n'.join(c.get('text','') for m in messages if m['role']=='developer' for c in m['content'])
 assert '### Available skills' not in dev and 'AGENTS.md instructions' not in dev,'extra project/skills context'
 assert not [r for r in transcript if r['type']=='response_item' and r['payload'].get('type') in ['function_call','custom_tool_call']],'unexpected tool call'
 assert any(e['type']=='turn.completed' for e in events),'no completion'
 assert not [e for e in events if e.get('type')=='item.completed' and e.get('item',{}).get('type')=='error'], 'runtime error event'
 meta=next(r['payload'] for r in transcript if r['type']=='session_meta')
 assert meta['model_provider']=='openai'
 return {'thread_id':tid,'model':ctx['model'],'reasoning_effort':ctx['effort'],'provider':meta['model_provider'],'session_source':meta['source'],'session_path':str(paths[0]),'session_sha256':sha(paths[0]),'fork_turns':'none (fresh exec; no resume/fork)','prompt_sha256':hashlib.sha256(prompt.encode()).hexdigest(),'image_order':['BEFORE','AFTER'],'input_hashes':row['inputs'],'context_verified':True,'tool_calls':0,'usage':next(e.get('usage',{}) for e in events if e['type']=='turn.completed')}

def run(job):
 group,row=job;qid=row['qid'];name=group+'_'+qid
 if STOP.is_set():return None
 out=BASE/group/'raw'/f'{qid}.txt'; record=RUNTIME/'provenance'/f'{name}.json'
 if record.exists():
  prev=json.loads(record.read_text());assert out.exists() and sha(out)==prev['raw_sha256'];return {'job':name,'status':'already_complete'}
 prompt=(BASE/group/'prompts'/f'{qid}.txt').read_text()
 for attempt in range(2):
  if attempt and STOP.is_set():return None
  prefix=f'{name}_attempt{attempt}'
  evt=RUNTIME/'events'/f'{prefix}.jsonl';err=RUNTIME/'stderr'/f'{prefix}.txt';raw=RUNTIME/'attempts'/f'{prefix}.txt'
  for p in [evt,err,raw,out,record]:p.parent.mkdir(parents=True,exist_ok=True)
  cmd=command(row,raw);write(RUNTIME/'commands'/f'{prefix}.json',cmd)
  started=time.time()
  try:
   with evt.open('w') as fo,err.open('w') as fe:
    p=subprocess.run(cmd,input=prompt,text=True,stdout=fo,stderr=fe,timeout=600)
   assert p.returncode==0, f'exit={p.returncode}, stderr={err.read_text()[-1000:]}'
   data=json.loads(raw.read_text());assert data['validity']['status'] in ['ok','model_failure','invalid_question','judge_unscorable']
   assert len(data['raw_dimensions'])==3
   assert all(type(d['tier']) is int and d['tier'] in [0,1,2] and d['reason'] for d in data['raw_dimensions'])
   provenance=audit(evt,prompt,row)
   import sys;sys.path.insert(0,str(EDIT))
   from eval_codex_score import EDIT_DIMS
   assert [d['label'] for d in data['raw_dimensions']]==EDIT_DIMS[row['edit_type']]
   out.write_bytes(raw.read_bytes())
   provenance.update(job=name,attempt=attempt,raw_sha256=sha(out),seconds=round(time.time()-started,2),completed_at=time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),launcher='fresh Codex exec child process')
   write(record,provenance)
   with LOCK:print(json.dumps({'job':name,'status':'complete','seconds':provenance['seconds']},ensure_ascii=False),flush=True)
   return {'job':name,'status':'complete'}
  except Exception as e:
   error={'job':name,'attempt':attempt,'error':str(e),'seconds':round(time.time()-started,2)};write(RUNTIME/'errors'/f'{prefix}.json',error)
   with LOCK:print(json.dumps(error,ensure_ascii=False),flush=True)
   if attempt==1:STOP.set();return dict(error,status='blocked')

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--workers',type=int,default=3);ap.add_argument('--limit',type=int);args=ap.parse_args()
 jobs=[(g,r) for g in GROUPS for r in rows(BASE/g/'blind_manifest.jsonl')]
 random.Random(20260908).shuffle(jobs)
 if args.limit:jobs=jobs[:args.limit]
 with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
  results=list(pool.map(run,jobs))
 write(RUNTIME/'dispatch_summary.json',{'jobs':len(jobs),'results':results,'stopped':STOP.is_set()})
 if STOP.is_set():raise SystemExit(2)
if __name__=='__main__':main()
