"""Print only final answer text and compact usage, never pixel/base64 payloads."""
import argparse,json,re
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('run');p.add_argument('--concept');p.add_argument('--list',action='store_true');a=p.parse_args()
for req in sorted(Path(a.run).glob('**/calls/*.request.json')):
 r=json.loads(req.read_text())
 if r.get('stage')!='final_review':continue
 parts=[]
 for m in r['payload']['messages']:
  c=m['content']
  parts += [c] if isinstance(c,str) else [x.get('text','') for x in c if x.get('type')=='text']
 match=re.search('目标概念：([^\n]+)','\n'.join(parts));concept=match[1] if match else req.stem
 if a.concept and a.concept!=concept:continue
 response=req.with_name(req.name.replace('.request.json','.response.json'))
 if not response.exists():print(concept,'pending');continue
 b=json.loads(response.read_text()).get('body',{});choice=b.get('choices',[{}])[0]
 print(concept,'finish',choice.get('finish_reason'),'usage',b.get('usage'))
 if not a.list:
  content=choice.get('message',{}).get('content','')
  print(content.split('</think>')[-1].strip())
