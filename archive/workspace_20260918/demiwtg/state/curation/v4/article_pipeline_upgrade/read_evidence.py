"""Print the actual evidence rendering, including the supplied context paragraphs."""
import argparse,json,sys
from pathlib import Path
ROOT=Path('/yzp/zhaozy/yangzepeng/0905/demiwtg');sys.path.insert(0,str(ROOT))
from curation.v4.ops.article import source_text
p=argparse.ArgumentParser();p.add_argument('run');p.add_argument('--concept',required=True);p.add_argument('--numbers',nargs='*',type=int);p.add_argument('--contexts-only',action='store_true');a=p.parse_args()
r=next(r for r in map(json.loads,(Path(a.run)/'datasets/final_review_requests.jsonl').read_text().splitlines()) if r['concept']==a.concept)
for n,s in enumerate(r['source_catalog'],1):
 if a.numbers and n not in a.numbers:continue
 s=dict(s)
 if a.contexts_only:
  s.pop('text',None)
  if not s.get('context_before') and not s.get('context_after'):continue
 print(f'\n【资料{n}】 {s["source_id"]}\n'+source_text(s))
