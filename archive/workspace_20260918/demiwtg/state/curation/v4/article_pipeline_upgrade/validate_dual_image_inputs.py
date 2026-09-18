"""Verify real primary/review messages and accepted image intersection."""
import argparse,collections,hashlib,json
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('run');a=p.parse_args();run=Path(a.run)
def digest(x):return hashlib.sha256(json.dumps(x,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
by_model=collections.defaultdict(dict)
for path in run.glob('**/calls/*.request.json'):
 r=json.loads(path.read_text())
 if r.get('stage')!='select_images':continue
 payload=r['payload'];key=digest(payload['messages']);by_model[payload['model']][key]=str(path)
primary=by_model['qwen3.8-27b'];review=by_model['gemma-4-31b-it']
assert review.keys() <= primary.keys(),'Gemma saw different messages/pixels'
files=list((run/'datasets').glob('*.jsonl'));matches=[]
for path in files:
 if path.name not in ['image_relevance.jsonl','image_selection.jsonl','image_confirmed.jsonl']:continue
 for line in path.open():
  row=json.loads(line)
  for d in row.get('image_decisions',[]):
   if d.get('decision')=='keep':
    assert d['primary_review']['decision']=='keep' and d['independent_review']['decision']=='keep'
   matches.append({'image_id':d['image_id'],'decision':d['decision']})
assert matches,'Final image decisions not found'
assert review or not any(d['decision']=='keep' for d in matches)
report={'primary_batches':len(primary),'review_batches':len(review),'review_messages_and_pixels_identical':True,'primary_verdict_not_added_to_review':True,'accepted_only_when_both_keep':True,'image_decisions':dict(collections.Counter(x['decision'] for x in matches)),'decisions':matches}
(run/'dual_image_input_validation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({k:v for k,v in report.items() if k!='decisions'},ensure_ascii=False,indent=2))
