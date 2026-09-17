"""Bounded four-shard Gemini generation; 40 reserved jobs, no paid retries."""

# Archive relocation: resolve the project, not a fixed directory depth.
from pathlib import Path as _ArchivePath
import sys as _archive_sys
_ARCHIVE_ROOT = next(p for p in _ArchivePath(__file__).resolve().parents if (p / "AGENTS.md").is_file() and (p / "curation").is_dir())
_archive_sys.path.insert(0, str(_ARCHIVE_ROOT))
_archive_sys.path.insert(0, str(_ARCHIVE_ROOT / "curation/archive/compat/knowledge_application_v1"))
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from pipeline import ROOT, STATE, read
from bagel_runner import encoded, publish, validate_jobs, sha

RUN=STATE/'expansion20_v1'

def main():
    jobs=validate_jobs(RUN/'jobs.jsonl')
    # validate_jobs returns (jobs, raw) in the native adapter.
    if isinstance(jobs,tuple):jobs=jobs[0]
    if not isinstance(jobs,list):jobs=[json.loads(l) for l in (RUN/'jobs.jsonl').read_text().splitlines()]
    assert len(jobs)==40 and len({j['job_id'] for j in jobs})==40
    plans=[jobs[i::4] for i in range(4)]
    publish(RUN/'gemini_shard_plan.json',encoded({'total_request_cap':40,'shards':[[j['job_id'] for j in p] for p in plans]}))
    processes=[]
    for i,plan in enumerate(plans):
        out=RUN/f'gemini_shard_{i}';out.mkdir(parents=True,exist_ok=True)
        log=(out/'launch.log').open('ab')
        cmd=[sys.executable,str(ROOT/'curation/knowledge_application_v1/gemini_runner.py'),'--jobs',str(RUN/'jobs.jsonl'),'--out',str(out),'--limit',str(len(plan)),'--job-ids',','.join(j['job_id'] for j in plan)]
        p=subprocess.Popen(cmd,cwd=ROOT,stdout=log,stderr=subprocess.STDOUT);log.close();processes.append((p,out))
    statuses=[]
    for p,out in processes:
        statuses.append(p.wait())
        for d in (out/'jobs').glob('*'):
            target=RUN/'gemini/jobs'/d.name
            for source in d.rglob('*'):
                dest=target/source.relative_to(d)
                if source.is_dir():dest.mkdir(parents=True,exist_ok=True);continue
                dest.parent.mkdir(parents=True,exist_ok=True)
                if dest.exists():assert sha(dest.read_bytes())==sha(source.read_bytes())
                else:os.link(source,dest)
    results=[read(p) for p in (RUN/'gemini/jobs').glob('*/result.json')]
    publish(RUN/'gemini_run_result.json',encoded({'exit_codes':statuses,'results':len(results),'ok':sum(r['ok'] for r in results),'reported_cost_usd':sum(r.get('usage',{}).get('cost',0) or 0 for r in results),'note':'Canonical job directories hardlink original shard records; no request rewriting.'}))
    if any(statuses):raise SystemExit(1)

if __name__=='__main__':main()
