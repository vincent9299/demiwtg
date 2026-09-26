"""Import saved visual metadata into image entities and register a fixed selection."""
import argparse
import json
from pathlib import Path
from project import resolve_root
from tools.lake_migration.visual_publication import publish_visual_records


def export_visual_release(meta_jsonl,release_id,*,published_jsonl=None,datasets_root=None,
                          pipeline_run=None,previous_release_id=None,config=None,source_ref=None):
    path=Path(meta_jsonl)
    records=[json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    reviewed={(r['concept'],r['sha256']) for r in records if r.get('publication_status')=='reviewed'}
    if published_jsonl:
        published={(r['concept'],r['sha256']) for r in map(json.loads,Path(published_jsonl).read_text().splitlines()) if r}
        if reviewed!=published:raise ValueError('publication binding mismatch')
    if source_ref is None:raise ValueError('Explicit fixed raw image source_ref is required')
    return publish_visual_records(datasets_root or resolve_root(),records,release_id,source_file=str(path),
        source_ref=source_ref,run_id=pipeline_run,previous=previous_release_id,binding={'records':records,'config':config or {},'source':str(path)})

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source-ref',required=True,help='JSON file containing the fixed raw image DatasetRef');p.add_argument('--meta',required=True);p.add_argument('--release-id',required=True)
    p.add_argument('--published');p.add_argument('--datasets-root');p.add_argument('--run');p.add_argument('--previous')
    a=p.parse_args()
    print(json.dumps(export_visual_release(a.meta,a.release_id,published_jsonl=a.published,datasets_root=a.datasets_root,
        pipeline_run=a.run,previous_release_id=a.previous,source_ref=json.loads(Path(a.source_ref).read_text())).to_dict()))
