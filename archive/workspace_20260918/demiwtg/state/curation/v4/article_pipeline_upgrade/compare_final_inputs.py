"""Compare two stored final requests without printing embedded pixels."""
import argparse,json,re,hashlib
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('left');p.add_argument('right');p.add_argument('concept');a=p.parse_args()
def request(run):
 for path in Path(run).glob('**/calls/*.request.json'):
  r=json.loads(path.read_text())
  if r.get('stage')!='final_review':continue
  text=[]
  for m in r['payload']['messages']:
   c=m['content'];text += [c] if isinstance(c,str) else [x.get('text','') for x in c if x.get('type')=='text']
  match=re.search('目标概念：([^\n]+)','\n'.join(text))
  if match and match[1]==a.concept:return r
 raise RuntimeError('No actual final request for '+a.concept+' in '+run)
l,r=request(a.left),request(a.right)
assert l['payload']['messages']==r['payload']['messages'],'Actual text/pixel messages differ'
assert l['payload']['model']==r['payload']['model']
assert l['payload']['temperature']==r['payload']['temperature']==0
assert l['payload']['max_tokens']==r['payload']['max_tokens']==65536
report={'concept':a.concept,'left':str(Path(a.left).resolve()),'right':str(Path(a.right).resolve()),'actual_messages_identical':True,'same_model_temperature_output_limit':True,'left_effort':l['payload']['chat_template_kwargs'],'right_effort':r['payload']['chat_template_kwargs'],'messages_sha256':hashlib.sha256(json.dumps(l['payload']['messages'],sort_keys=True).encode()).hexdigest()}
(Path(a.right)/'actual_input_comparison.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n');print(json.dumps(report,ensure_ascii=False,indent=2))
