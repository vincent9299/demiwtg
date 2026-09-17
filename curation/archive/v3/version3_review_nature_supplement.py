
# Archive relocation: resolve the project, not a fixed directory depth.
from pathlib import Path as _ArchivePath
import sys as _archive_sys
_ARCHIVE_ROOT = next(p for p in _ArchivePath(__file__).resolve().parents if (p / "AGENTS.md").is_file() and (p / "curation").is_dir())
_archive_sys.path.insert(0, str(_ARCHIVE_ROOT))
_archive_sys.path.insert(0, str(_ARCHIVE_ROOT / "curation/archive/compat/knowledge_application_v1"))
import json
from pathlib import Path
B=_ARCHIVE_ROOT/'state/curation/knowledge_application_v1/version3_20/blind'
def r(shard,i,k,kr,e,q,o):
 p=next(x for x in json.load(open(B/f'packets_{shard}.json'))['packets'] if x['review_id']=='output_'+i)
 obj={'review_id':'output_'+i,'output_sha256':p['output_sha256'],'reviewer':'assistant /root/evidence_candidates','knowledge':{'K'+str(n+1):s for n,s in enumerate(k)},'knowledge_reasons':{'K'+str(n+1):s for n,s in enumerate(kr)},'execution_items':{'E'+str(n+1):{'status':s,'reason':t} for n,(s,t) in enumerate(e)},'quality_items':{key:{'score':v,'reason':t} for key,(v,t) in zip(['clarity','artifacts','coherence'],q)},'observations':o}
 obj['execution_pass']=all(x['status']=='pass' for x in obj['execution_items'].values());obj['quality']=int(sum(x['score'] for x in obj['quality_items'].values())/3+0.5)
 assert set(obj['knowledge'])=={x['id'] for x in p['case']['knowledge_checks']};assert set(obj['execution_items'])=={x['id'] for x in p['case']['execution_checks']}
 with (B/f'review_items_{shard}'/f'output_{i}.json').open('x') as f:json.dump(obj,f,ensure_ascii=False,indent=2)
 print('saved',i)
