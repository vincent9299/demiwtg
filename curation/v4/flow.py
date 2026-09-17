"""Legacy query-oriented material preparation and historical review.

Current record-first entry: python -m curation.v4.record_flow.
This module remains for frozen query-pilot compatibility and shared execute().

No generation, knowledge extraction, canonical-data writes or implicit downloads.
Use the shared env Python. Run `python -m curation.v4.flow --help`.
"""
import argparse
from pathlib import Path
from demiflow.standalone import local_data
from .contracts import ROOT, SCHEMA, IdentityRegistry, code_fingerprint, digest, immutable, read, run_lock, source_code, runtime_version
from .sources import discover
from .ops.operators import InspectSource, CollectRows, ScanSource, AssembleMaterial, SaveBundle


def execute(rows,*stages):
    ds=local_data().from_iter(lambda:iter(rows))
    for stage in stages:ds=ds.map_async(stage)
    return ds.run_stream(log_every=0)


def check_run_location(run,project,dataset):
    run=Path(run).resolve();project=Path(project).resolve();dataset=Path(dataset).resolve()
    if not run.is_relative_to(project/'state/curation') or run.is_relative_to(dataset):
        raise ValueError('run must be under project/state/curation, outside the dataset')


def inventory(dataset,project,run):
    check_run_location(run,project,dataset)
    with run_lock(run):
        sources=discover(dataset,project);collector=CollectRows()
        execute(sources,InspectSource(),collector)
        immutable(Path(run)/'code_snapshot.json',source_code())
        report={'schema':SCHEMA,'phase':'inventory','code_hash':code_fingerprint(),'runtime':runtime_version(),
                'sources':sorted(collector.results,key=lambda x:x['path']),
                'scope':'File presence and sampled schema only; not a full corpus count or evidence quality audit.'}
        immutable(Path(run)/'inventory.json',report)
    return report


def prepare(dataset,project,run,requests,max_rows,source_paths=None,blob_roots=(),max_images=8):
    if max_rows<1 or max_images<0:raise ValueError('positive row budget and nonnegative image budget required')
    if not requests:raise ValueError('explicit entry-validation requests required; this is not broad sampling')
    if len({(r.get('kind'),r.get('value')) for r in requests})!=len(requests):raise ValueError('duplicate request identity')
    for r in requests:
        if set(r)!={'kind','value'}:raise ValueError('request fields are kind and value; no implicit identity merge')
    dataset=Path(dataset).resolve();project=Path(project).resolve();run=Path(run).resolve()
    check_run_location(run,project,dataset)
    with run_lock(run):
        sources=discover(dataset,project)
        eligible={'legacy_concepts','qid_concepts','qid_concepts_base','legacy_docs','legacy_images','qid_images','wiki_pages','image_roles','gallery_captions','qid_graph'}
        sources=[s for s in sources if s['kind'] in eligible]
        if source_paths is not None:
            selected={str(Path(p).resolve()) for p in source_paths}
            unknown=selected-{s['path'] for s in sources}
            if unknown:raise ValueError(f'Unknown source paths: {sorted(unknown)}')
            sources=[s for s in sources if s['path'] in selected]
        if not sources:raise ValueError('no sources selected')
        code=code_fingerprint()
        manifest={'schema':SCHEMA,'phase':'entry_validation','dataset':str(dataset),'project':str(project),
                  'requests':requests,'sources':sources,'max_rows_per_source':max_rows,'max_images_per_concept':max_images,
                  'blob_roots':[str(Path(p).resolve()) for p in blob_roots],'code_hash':code,'runtime':runtime_version(),
                  'scope':'Explicit engineering cases, bounded scans; not V4 question sampling or a whole-concept completeness claim.',
                  'snapshot_note':'Source size/mtime/inode lock this run; selected records also retain hashes and original fields. No whole-file content-hash claim.'}
        immutable(run/'manifest.json',manifest)
        immutable(run/'code_snapshot.json',source_code())
        # Separate persistent identities from display names; no source-name merging.
        registry=IdentityRegistry(project/'state/curation/v4/identities.sqlite')
        try:ids=[registry.get(r) for r in requests]
        finally:registry.close()
        identity_kinds={'legacy_concepts','qid_concepts','qid_concepts_base'}
        first=CollectRows();first_op=ScanSource(run,requests,{},max_rows,code)
        if any(s['kind'] in identity_kinds for s in sources):execute([s for s in sources if s['kind'] in identity_kinds],first_op,first)
        links={}
        for scan in first.results:
            if scan['source']['kind'] not in ('qid_concepts','qid_concepts_base'):continue
            for match in scan['matches']:
                record=match['record'];qid=record['qid']
                for lang in ['en','zh']:
                    site=record.get(lang) or {}
                    if site.get('page_id') is not None:links.setdefault(qid,[]).append((lang,site['page_id']))
        links={k:sorted(set(v)) for k,v in links.items()}
        second=CollectRows();second_op=ScanSource(run,requests,links,max_rows,code)
        if any(s['kind'] not in identity_kinds for s in sources):execute([s for s in sources if s['kind'] not in identity_kinds],second_op,second)
        scans=sorted(first.results+second.results,key=lambda r:r['source']['path'])
        tasks=[{'request':r,'concept_id':cid,'task_id':digest({'manifest':manifest,'request':r,'concept_id':cid})} for r,cid in zip(requests,ids)]
        # Skip completed bundles before any byte verification or assembly work.
        done=[t for t in tasks if (run/'bundles'/f'{t["task_id"]}.json').exists()]
        pending=[t for t in tasks if t not in done]
        sink=SaveBundle(run)
        if pending:execute(pending,AssembleMaterial(scans,dataset,blob_roots,max_images),sink)
        bundles=[{'task_id':t['task_id'],'concept_id':t['concept_id'],'request':t['request'],
                  'path':str(run/'bundles'/f'{t["task_id"]}.json')} for t in tasks]
        report={'schema':SCHEMA,'phase':'materials_prepared','bundles':bundles,'sources':[{k:s[k] for k in ['source','status','scan']} for s in scans],
                'limitations':['No cross-source identity adjudication.','COS/network byte fetching not connected.','No knowledge extraction or evidence approval.','Bounded source scans do not prove absence.']}
        immutable(run/'report.json',report)
        return {**report,'execution':{'source_tasks_reused':first_op.reused+second_op.reused,'source_tasks_executed':first_op.executed+second_op.executed,'bundles_reused':len(done),'bundles_executed':len(pending)}}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('action',choices=['inventory','prepare','review'])
    p.add_argument('--dataset',type=Path,default=ROOT/'datasets/demiwtg')
    p.add_argument('--project',type=Path,default=ROOT)
    p.add_argument('--run',type=Path,required=True)
    p.add_argument('--requests',type=Path,help='JSON list of explicit {kind: qid|legacy, value: ...} entry-validation requests')
    p.add_argument('--max-rows-per-source',type=int,default=500)
    p.add_argument('--max-images-per-concept',type=int,default=8)
    p.add_argument('--source',action='append',type=Path,help='Explicit source paths; omission scans all supported sources to the stated row budget')
    p.add_argument('--blob-root',action='append',type=Path,default=[],help='Additional local/mounted dataset roots containing blobs/...')
    a=p.parse_args()
    if a.action=='inventory':
        r=inventory(a.dataset,a.project,a.run);print('Inspected',len(r['sources']),'source entries; see',a.run/'inventory.json')
    elif a.action=='prepare':
        if not a.requests:p.error('prepare requires --requests')
        r=prepare(a.dataset,a.project,a.run,read(a.requests),a.max_rows_per_source,a.source,a.blob_root,a.max_images_per_concept)
        print(r['execution']);print('Material report:',a.run/'report.json')
    else:
        from .review import render
        print(render(a.run))

if __name__=='__main__':main()
