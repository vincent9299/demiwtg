#!/usr/bin/env python3
"""Import generated focus1000 pixels and provenance into the shared image table.

This is explicit transport ingestion. The lake owns bytes after import; source
results remain historical experiment evidence, never a runtime fallback.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import sys

REPO_ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(REPO_ROOT))
from project import resolve_root
from collect.import_materials import ingest

DATA_DIR=Path(__file__).resolve().parent/'data'
RESULTS_F=DATA_DIR/'gen_results.jsonl'
GEN_SOURCE='qwen-image-3.0-pro'


def import_results(path,root,*,dry_run=False):
    path=Path(path);pending=[];count=0
    with path.open() as stream:
        for index,line in enumerate(stream):
            if not line.strip():continue
            row=json.loads(line)
            if row.get('error'):continue
            source=path.parent/row['file']
            raw=source.read_bytes()
            if hashlib.sha256(raw).hexdigest()!=row['sha256']:raise ValueError('Generated image SHA mismatch: '+str(source))
            pending.append({'sha256':row['sha256'],'bytes_path':str(source),'concepts':[row['instance']],
                'source':GEN_SOURCE,'generation_origin':'generated','source_result_file':str(path),
                'source_result_row':index,'fetched_at':row.get('ts'),'content_url':'urn:sha256:'+row['sha256']})
            count+=1
            if len(pending)>=64:
                if not dry_run:ingest(pending,'images',root)
                pending=[]
    if pending and not dry_run:ingest(pending,'images',root)
    return {'source_records':count,'dry_run':dry_run,'destination':'raw/images.lance'}


def main():
    p=argparse.ArgumentParser();p.add_argument('--dry-run',action='store_true');args=p.parse_args()
    if not RESULTS_F.is_file():raise SystemExit('Missing results: '+str(RESULTS_F))
    print(json.dumps(import_results(RESULTS_F,resolve_root(),dry_run=args.dry_run),ensure_ascii=False))
if __name__=='__main__':main()
