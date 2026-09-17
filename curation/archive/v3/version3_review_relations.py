"""Persist per-image manual assistant assessments from blind packet 2 only."""

# Archive relocation: resolve the project, not a fixed directory depth.
from pathlib import Path as _ArchivePath
import sys as _archive_sys
_ARCHIVE_ROOT = next(p for p in _ArchivePath(__file__).resolve().parents if (p / "AGENTS.md").is_file() and (p / "curation").is_dir())
_archive_sys.path.insert(0, str(_ARCHIVE_ROOT))
_archive_sys.path.insert(0, str(_ARCHIVE_ROOT / "curation/archive/compat/knowledge_application_v1"))
import json, math, hashlib
from pathlib import Path
ROOT=_ARCHIVE_ROOT
B=ROOT/'state/curation/knowledge_application_v1/version3_20/blind'
OUT=B/'review_items_2'
OUT.mkdir(exist_ok=True)

def record(n, knowledge, execution, quality, observations):
    rid=f'output_{n:03d}'
    packet=next(p for p in json.loads((B/'packets_2.json').read_text())['packets'] if p['review_id']==rid)
    assert len(knowledge)==len(packet['case']['knowledge_checks'])
    assert len(execution)==len(packet['case']['execution_checks'])
    result=dict(review_id=rid,output_sha256=packet['output_sha256'],reviewer='assistant /root/evidence_relations',
      knowledge={c['id']:s for c,(s,r) in zip(packet['case']['knowledge_checks'],knowledge)},
      knowledge_reasons={c['id']:r for c,(s,r) in zip(packet['case']['knowledge_checks'],knowledge)},
      execution_items={c['id']:dict(status=s,reason=r) for c,(s,r) in zip(packet['case']['execution_checks'],execution)},
      execution_pass=all(s=='pass' for s,r in execution),
      quality_items={k:dict(score=s,reason=r) for k,(s,r) in zip(['clarity','artifacts','coherence'],quality)},
      quality=int(math.floor(sum(s for s,r in quality)/3+0.5)), observations=observations)
    p=OUT/f'{rid}.json'
    if p.exists():raise FileExistsError(p)
    p.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(rid,'saved')

def P(s):return ('pass',s)
def C(s):return ('conflict',s)
def U(s):return ('unobservable',s)
