"""Persist assistant visual reviews; reads only blinded shard 1 packets."""

# Archive relocation: resolve the project, not a fixed directory depth.
from pathlib import Path as _ArchivePath
import sys as _archive_sys
_ARCHIVE_ROOT = next(p for p in _ArchivePath(__file__).resolve().parents if (p / "AGENTS.md").is_file() and (p / "curation").is_dir())
_archive_sys.path.insert(0, str(_ARCHIVE_ROOT))
_archive_sys.path.insert(0, str(_ARCHIVE_ROOT / "curation/archive/compat/knowledge_application_v1"))
import json,math,hashlib
from pathlib import Path
ROOT=_ARCHIVE_ROOT
B=ROOT/'state/curation/knowledge_application_v1/version3_20/blind'
def save(rid,knowledge,execution,quality,observations):
 packets=json.loads((B/'packets_1.json').read_text())['packets'];p=next(p for p in packets if p['review_id']==rid)
 assert len(knowledge)==len(p['case']['knowledge_checks'])
 assert len(execution)==len(p['case']['execution_checks'])
 r={'review_id':rid,'output_sha256':p['output_sha256'],'reviewer':'assistant /root/evidence_objects','knowledge':{},'knowledge_reasons':{},'execution_items':{},'quality_items':{},'observations':observations}
 for check,(status,reason) in zip(p['case']['knowledge_checks'],knowledge):r['knowledge'][check['id']]=status;r['knowledge_reasons'][check['id']]=reason
 for check,(status,reason) in zip(p['case']['execution_checks'],execution):r['execution_items'][check['id']]={'status':status,'reason':reason}
 for name,(score,reason) in zip(['clarity','artifacts','coherence'],quality):r['quality_items'][name]={'score':score,'reason':reason}
 r['execution_pass']=all(x['status']=='pass' for x in r['execution_items'].values());r['quality']=int(math.floor(sum(q[0] for q in quality)/3+.5))
 d=B/'review_items_1';d.mkdir(exist_ok=True);f=d/(rid+'.json');assert not f.exists(),f;f.write_text(json.dumps(r,ensure_ascii=False,indent=2));print('saved',rid)
