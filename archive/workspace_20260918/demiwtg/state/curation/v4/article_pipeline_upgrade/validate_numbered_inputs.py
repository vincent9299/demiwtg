"""Verify each sent image has its exact adjacent label and unchanged pixels."""
import json,sys
from pathlib import Path
run=Path(sys.argv[1]);rows=[json.loads(l) for l in (run/'datasets/final_review.jsonl').open()]
diagnostic='--diagnostic-no-preflight' in sys.argv[2:]
if diagnostic:
 policy=json.loads((run/'capacity_policy.json').read_text());assert policy['not_formal_token_budget_validation'] and policy['truncate_prompt_tokens'] is None
report=[]
for row in rows:
 call=row.get('article_call')
 if not call:continue
 req=json.loads(Path(call['request_path']).read_text());content=req['payload']['messages'][1]['content']
 if isinstance(content,str):content=[{'type':'text','text':content}]
 positions=[i for i,p in enumerate(content) if p['type']=='image_url']
 assert len(positions)==len(row['article_image_ids'])==len(row['pixel_images'])
 for n,pos in enumerate(positions,1):
  assert pos>0 and content[pos-1]=={'type':'text','text':f'\nImage {n}:\n'}
  assert content[pos]['image_url']['url']==row['pixel_images'][n-1]
 if not diagnostic:assert row['input_token_budget']-call['usage']['prompt_tokens']==256
 report.append({'concept':row['concept'],'images':len(positions),'labels_adjacent':True,'pixel_bytes_unchanged':True,'actual_token_budget_matches':None if diagnostic else True})
(run/'numbered_image_validation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
print(json.dumps(report,ensure_ascii=False,indent=2))
