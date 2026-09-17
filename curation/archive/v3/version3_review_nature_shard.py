"""Persist explicitly authored blinded reviews with frozen criterion coverage checks."""

# Archive relocation: resolve the project, not a fixed directory depth.
from pathlib import Path as _ArchivePath
import sys as _archive_sys
_ARCHIVE_ROOT = next(p for p in _ArchivePath(__file__).resolve().parents if (p / "AGENTS.md").is_file() and (p / "curation").is_dir())
_archive_sys.path.insert(0, str(_ARCHIVE_ROOT))
_archive_sys.path.insert(0, str(_ARCHIVE_ROOT / "curation/archive/compat/knowledge_application_v1"))
import json,sys,math
from pathlib import Path
ROOT=_ARCHIVE_ROOT
B=ROOT/'state/curation/knowledge_application_v1/version3_20/blind'
def save(r):
 packets=json.loads((B/'packets_0.json').read_text())['packets'];p=next(x for x in packets if x['review_id']==r['review_id']);c=p['case']
 r['reviewer']='assistant /root/evidence_candidates';r['output_sha256']=p['output_sha256']
 assert set(r['knowledge'])=={x['id'] for x in c['knowledge_checks']}
 assert set(r['knowledge_reasons'])==set(r['knowledge'])
 assert set(r['execution_items'])=={x['id'] for x in c['execution_checks']}
 r['execution_pass']=all(x['status']=='pass' for x in r['execution_items'].values())
 r['quality']=int(sum(x['score'] for x in r['quality_items'].values())/3+0.5) if all(x['score'] is not None for x in r['quality_items'].values()) else None
 d=B/'review_items_0';d.mkdir(exist_ok=True)
 with (d/(r['review_id']+'.json')).open('x') as f:json.dump(r,f,ensure_ascii=False,indent=2)
 print('saved',r['review_id'])
if __name__=='__main__':
 for r in json.load(sys.stdin):save(r)
