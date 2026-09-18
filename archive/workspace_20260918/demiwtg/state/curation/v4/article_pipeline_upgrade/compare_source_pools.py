"""Report exact adopted-evidence changes after re-extraction, without pixel output."""
import argparse,json,hashlib
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('parent');p.add_argument('run');a=p.parse_args()
def rows(run):return {r['concept']:r for r in (json.loads(l) for l in (Path(run)/'datasets/final_review_requests.jsonl').read_text().splitlines())}
old,new=rows(a.parent),rows(a.run);report=[]
for concept,r in new.items():
 before=old.get(concept,{});previous={s['source_id']:s for s in before.get('source_catalog',[])};now={s['source_id']:s for s in r['source_catalog']}
 added=[{'input_number':n,'source_id':s['source_id'],'title':s.get('title'),'sections':s.get('sections'),'text_chars':len(s.get('text',''))} for n,s in enumerate(r['source_catalog'],1) if s['source_id'] not in previous]
 changed=[sid for sid in now.keys()&previous.keys() if now[sid]!=previous[sid]]
 report.append({'concept':concept,'old_source_count':len(previous),'new_source_count':len(now),'added':added,'omitted_source_ids':sorted(previous.keys()-now.keys()),'same_id_changed_content':changed,'new_image_ids':sorted(set(r['article_image_ids'])-set(before.get('article_image_ids',[]))),'omitted_image_ids':sorted(set(before.get('article_image_ids',[]))-set(r['article_image_ids']))})
 assert not changed,'Same source ID changed contents'
(Path(a.run)/'adopted_evidence_comparison.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n');print(json.dumps(report,ensure_ascii=False,indent=2))
