"""Small resolution/renormalization sanity check; old outputs remain immutable."""

# Archive relocation: resolve the project, not a fixed directory depth.
from pathlib import Path as _ArchivePath
import sys as _archive_sys
_ARCHIVE_ROOT = next(p for p in _ArchivePath(__file__).resolve().parents if (p / "AGENTS.md").is_file() and (p / "curation").is_dir())
_archive_sys.path.insert(0, str(_ARCHIVE_ROOT))
import argparse
import json
import base64
import html
from pathlib import Path
from PIL import Image,ImageOps,ImageDraw
from curation.rag_diagnostic import REPO,sha,write_json,read_rows
RUN=REPO/'state/curation/bagel_parameter_probe_v1'
ORIGINAL=REPO/'state/curation/knowledge_probe_v1'
CONFIGS={'r512_n1':{'image_size':512,'cfg_renorm_min':1},'r512_n0':{'image_size':512,'cfg_renorm_min':0},'r1024_n1':{'image_size':1024,'cfg_renorm_min':1},'r1024_n0':{'image_size':1024,'cfg_renorm_min':0}}
CASES=['cashew_whole','wombat_scat']

def render():
 chunks=['<h2>BAGEL参数健全性检查</h2><p>两题、原题/知识两输入、每题一个配对种子、四配置。12张新图加4张旧图。下方是条件可见的助手诊断，不是盲评。</p>']
 if (RUN/'assessment.json').exists():
  chunks.append('<pre style="white-space:pre-wrap">'+html.escape((RUN/'assessment.json').read_text())+'</pre>')
 for case in CASES:
  p=RUN/(case+'.jpg')
  if p.exists(): chunks.append('<h3>'+case+'</h3><img style="width:100%" src="data:image/jpeg;base64,'+base64.b64encode(p.read_bytes()).decode()+'">')
 return ''.join(chunks)

def prepare():
 RUN.mkdir(exist_ok=True)
 if (RUN/'plan.json').exists(): raise ValueError('Plan already frozen')
 old=json.loads((ORIGINAL/'plan.json').read_text());runner=REPO/'curation/archive/pre_v1/probe_bagel.py'
 frozen={str(runner):sha(runner),str(ORIGINAL/'plan.json'):sha(ORIGINAL/'plan.json'),str(ORIGINAL/'cases.json'):sha(ORIGINAL/'cases.json')}
 jobs=[]
 for config in list(CONFIGS)[1:]:
  d=RUN/'conditions'/config;d.mkdir(parents=True,exist_ok=True);rows=[]
  for case in CASES:
   for condition in ['baseline','knowledge']:
    oldqid=case+'_r1';q=read_rows(ORIGINAL/'conditions'/condition/'questions.jsonl')[oldqid]
    newqid=case+'_'+condition
    row=dict(qid=newqid,task='t2i',gen_prompt=q['gen_prompt'],_seed_key=oldqid)
    rows.append(row)
    oldjob=next(j for j in old['jobs'] if j['qid']==oldqid and j['condition']==condition)
    jobs.append(dict(qid=newqid,parent_id=case,condition=config,input_condition=condition,repeat=1,seed=oldjob['seed'],prompt_sha256=oldjob['prompt_sha256']))
    p=ORIGINAL/'conditions'/condition/'outputs/imgs'/f'{oldqid}.png';frozen[str(p)]=sha(p)
  qp=d/'questions.jsonl';qp.write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in rows));frozen[str(qp)]=sha(qp)
 for f in ['inferencer.py','data/transforms.py']:
  p=REPO/'bagel/Bagel'/f;frozen[str(p)]=sha(p)
 plan={k:old[k] for k in ['python','model_path','image_size','num_timesteps','seed_base']}
 plan.update(schema='bagel-parameter-probe-v1',role='development_configuration_check',runner=str(runner),conditions=list(CONFIGS)[1:],condition_parameters=CONFIGS,jobs=jobs,n_images=len(jobs),frozen_files=frozen,cases=CASES,
             interpretation='2 cases × 2 input conditions × 4 profiles, one paired seed per case. Reuse original512/renorm1 four images; generate12. This checks configuration sensitivity, not final benchmark quality or statistical effects.')
 write_json(RUN/'plan.json',plan);print('Prepared12 new images +4 preserved originals')

def panels():
 plan=json.loads((RUN/'plan.json').read_text())
 for p,h in plan['frozen_files'].items():
  if sha(p)!=h:raise ValueError(p)
 index=[]
 for case in CASES:
  canvas=Image.new('RGB',(2000,1060),'white');draw=ImageDraw.Draw(canvas)
  for col,config in enumerate(CONFIGS):
   for row,condition in enumerate(['baseline','knowledge']):
    if config=='r512_n1':p=ORIGINAL/'conditions'/condition/'outputs/imgs'/f'{case}_r1.png'
    else:
     d=RUN/'conditions'/config/'outputs';qid=case+'_'+condition
     responses={k:v for f in d.glob('responses*.jsonl') for k,v in read_rows(f).items()}
     r=responses[qid];j=next(j for j in plan['jobs'] if j['qid']==qid and j['condition']==config)
     assert r['ok'] and r['seed']==j['seed'] and r['output_sha256']==sha(d/r['image'])
     p=d/r['image']
    with Image.open(p) as im:t=ImageOps.contain(im.convert('RGB'),(490,490))
    x=col*500;y=row*530;canvas.paste(t,(x+(500-t.width)//2,y+32));draw.text((x+4,y+6),config+' '+condition,fill='black')
    index.append(dict(case=case,configuration=config,input_condition=condition,path=str(p),sha256=sha(p)))
  canvas.save(RUN/(case+'.jpg'),quality=96)
 write_json(RUN/'image_index.json',index)
 print(RUN)

if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('command',choices=['prepare','panels']);a=p.parse_args();globals()[a.command]()
