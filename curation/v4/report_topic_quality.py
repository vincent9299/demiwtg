"""Structural before/after report; counts do not certify knowledge correctness."""
import argparse,json
from pathlib import Path
from collections import Counter

def rows(p):
    return [json.loads(l) for l in p.open() if l.strip()] if p.exists() else []

def counts(run):
    return {r['concept']:{'topics':len(r['knowledge']),
      'paragraphs':sum(len(a['content']['paragraphs']) for a in r['knowledge']),
      'image_placements':sum(len(a['content']['images']) for a in r['knowledge']),
      'unique_images':len({i['image_id'] for a in r['knowledge'] for i in a['content']['images']}),
      'topics_without_prose':sum(not a['content']['paragraphs'] for a in r['knowledge']),
      'titles':[a['title'] for a in r['knowledge']]}
      for r in rows(run/'knowledge_base.jsonl')}

def report(source,run):
    source,run=Path(source),Path(run);calls=[]
    for p in (run/'knowledge/calls').glob('*.response.json'):
        r=json.loads(p.read_text());calls.append(r)
    relations={}
    for p in sorted((run/'datasets').glob('*_relations.jsonl')):
        rr=rows(p);relations[p.stem]={'counts':dict(Counter(r.get('relationship_review',{}).get('relationship','invalid') for r in rr)),
          'invalid':sum(not r.get('review_valid') for r in rr),'pending':sum(r.get('next_action')=='pending' for r in rr)}
    final=rows(run/'paragraphs.jsonl');visual=[]
    for r in final:
        for t in r['topics']:
            for b in t['blocks']:
                if b['type']=='text' and b.get('status')=='candidate' and b.get('image_refs'):
                    visual.append({'concept':r['joint_prompt']['concept'],'title':t['title'],'text':b['text'],'image_refs':b['image_refs'],'citations':b.get('citations',[])})
    result={'scope':'Frozen three-concept output trial; not a new raw-data E2E run; structural statistics, not correctness certification',
      'before':counts(source),'after':counts(run),'responses':len(calls),
      'total_tokens':sum(r.get('body',{}).get('usage',{}).get('total_tokens',0) for r in calls),
      'call_elapsed_s':sum(r.get('elapsed_s',0) for r in calls),'relations':relations,'visual_prose':visual}
    (run/'structural_report.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n');return result
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--source',required=True);p.add_argument('--run',required=True);a=p.parse_args();print(json.dumps(report(a.source,a.run),ensure_ascii=False,indent=2))
