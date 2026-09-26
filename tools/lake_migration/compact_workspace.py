"""Preserve selected benchmark evidence before retiring approved work directories.

No collection or _staging files are selected. This maintenance operation never
runs a model. Deletion is a separate step after byte verification and consumer
migration; the immutable inventory is the allowlist for that step.
"""
from pathlib import Path
import argparse
import hashlib
import json
import mimetypes
from concurrent.futures import ThreadPoolExecutor
import lance
import pyarrow as pa
from demiflow.lance.artifacts import ARTIFACTS, ArtifactSet
from demiflow.lance.registry import write_registered_table
from demiflow.lance.records import LanceRecordStore
from project import PROJECT_ROOT, resolve_root

RUN_ID = 'workspace_cleanup_20260923'
IMAGES = {'.png','.jpg','.jpeg','.webp','.gif','.bmp','.tif','.tiff','.avif'}
RETAIN_ROOTS = ('benchmark/focus1000/data', 'benchmark/t2i/v1/bench200',
                'benchmark/edit/v1/bench200', 'evaluation/bagel/data',
                'evaluation/reviews', 'curation/edit/reviews')
RETIRE_ROOTS = ('benchmark/focus1000','benchmark/vlm','benchmark/t2i/v1/bench200',
                'benchmark/t2i/v1/archive','benchmark/edit/v1/bench200',
                'benchmark/edit/v1/archive','benchmark/edit/v1/focus200',
                'benchmark/edit/v1/synth_v61_pilot')

def sha(raw): return hashlib.sha256(raw).hexdigest()
def rows(path):return [json.loads(s) for s in Path(path).read_text().splitlines() if s.strip()]

def selected_files(repo):
    selected = {}
    def add(path, role):
        path = Path(path)
        if path.is_symlink(): raise ValueError('Evidence may not silently follow symlinks: '+str(path))
        if not path.is_file(): raise FileNotFoundError(path)
        rel = path.relative_to(repo).as_posix()
        if {'collect','_staging','.git','__pycache__'} & set(Path(rel).parts):
            raise ValueError('Excluded source: '+rel)
        selected[rel] = (path, role)
    for relative in RETAIN_ROOTS:
        for path in sorted((repo/relative).rglob('*')):
            if not path.is_file() or path.is_symlink() or {'__pycache__','.git'} & set(path.parts):continue
            if relative.endswith('/reviews') and path.suffix in {'.zip','.html'}:continue
            add(path, 'benchmark_result' if relative.startswith('benchmark/') else 'evaluation_evidence')
    # The formal edit source closure includes plan images, not the entire candidate pool.
    base = repo/'benchmark/edit/v1'
    for q in rows(base/'bench200/questions.jsonl'):
        add(base/q['_sample_image'], 'benchmark_source')
    for record in rows(base/'bench200/plan.jsonl'):
        for im in record.get('images',[]):add(base/'focus200'/im['file'], 'benchmark_source')
    # Pilot questions contributed to the formal 200; preserve compact source and
    # adopted rubric evidence, not duplicated pilot images or pending task books.
    pilot=base/'synth_v61_pilot'
    for name in ['questions.jsonl','plan.jsonl','qib_v22_revision_validation_20260907.md',
                 'qib_v22_astra_vs_sol_gemini_20260907.md','qib_judge_model_history_20260907.json']:
        if (pilot/name).is_file():add(pilot/name,'method_provenance')
    for folder in ['drafts','check_a','check_b','check_c']:
        for path in (pilot/folder).rglob('*'):
            if path.is_file():add(path,'authoring_provenance')
    # Preserve the exact interpreter-independent source/template bytes checked by
    # the frozen score verifier, without keeping old experiment executables active.
    for relative in ['evaluation/edit/v1/eval_codex_score.py',
                     'evaluation/edit/v1/prompts/judge_prompt_edit_qib_v2.2.md']:
        add(repo/relative,'frozen_protocol')
    return selected


def plan(repo=PROJECT_ROOT):
    repo=Path(repo); selected=selected_files(repo)
    extra={}
    # Only explicitly referenced, hash-bound judge sessions are read; tool state
    # and all unrelated sessions remain untouched.
    for path,role in list(selected.values()):
        if path.suffix!='.json' or path.parent.name!='provenance':continue
        obj=json.loads(path.read_text());session=obj.get('session_path');expected=obj.get('session_sha256')
        if not session or not expected:continue
        p=Path(session).resolve()
        allowed=repo.parent/'.codex-home/sessions'
        if not p.is_relative_to(allowed):raise ValueError('Unexpected judge evidence location: '+session)
        if sha(p.read_bytes())!=expected:raise ValueError('Judge session differs: '+session)
        name='judge_sessions/'+p.relative_to(allowed).as_posix()
        extra[name]=(p,'frozen_judge_session')
    selected.update(extra)
    def record(item):
        name,(path,role)=item;raw=path.read_bytes()
        return {'path':name,'source':str(path),'sha256':sha(raw),'byte_size':len(raw),
                'media_type':mimetypes.guess_type(name)[0] or 'application/octet-stream','role':role}
    with ThreadPoolExecutor(max_workers=4) as pool: entries=list(pool.map(record,sorted(selected.items())))
    return entries


def preserve(entries,root=None,repo=PROJECT_ROOT):
    root=resolve_root(root);repo=Path(repo);log=LanceRecordStore(root,'datasets/records__' + RUN_ID + '.lance')
    log.put('selected_sources',entries)
    # Keep original generation/collection provenance when restoring the few
    # formal source images that have not yet reached raw. No collection code is edited.
    raw=lance.dataset(str(root/'demiwtg/collect/datasets/images.lance'));existing={}
    wanted=sorted({e['sha256'] for e in entries if Path(e['path']).suffix.lower() in IMAGES})
    for at in range(0,len(wanted),200):
        query=','.join("'"+s+"'" for s in wanted[at:at+200])
        for row in raw.to_table(columns=['sha256','availability'],filter='sha256 IN ('+query+')').to_pylist():existing[row['sha256']]=row
    source_by_name={q['_sample_image']:q for q in rows(repo/'benchmark/edit/v1/bench200/questions.jsonl')}
    missing=[]
    for e in entries:
        if e['role']!='benchmark_source' or existing.get(e['sha256'],{}).get('availability')=='available':continue
        rel=e['path'].removeprefix('benchmark/edit/v1/');q=source_by_name.get(rel,{})
        missing.append({'sha256':e['sha256'],'bytes_path':e['source'],
            'concepts':[q['_instance']] if q.get('_instance') else [],
            'source':'historical_benchmark_source','source_result_file':e['path'],
            'generation_origin':'generated' if '/images/' in e['path'] or '/generated/' in e['path'] else 'unknown',
            'content_url':'urn:sha256:'+e['sha256']})
    if missing:
        from collect.import_materials import ingest
        ingest(missing,'images',root)
    raw=lance.dataset(str(root/'demiwtg/collect/datasets/images.lance'))
    for m in missing:existing[m['sha256']]={'availability':'available'}
    raw_bindings={s for s,r in existing.items() if r['availability']=='available'}
    by_sha={e['sha256']:e for e in entries}
    # A single streaming Blob table, deduplicated across names/roles. Existing
    # raw bytes use their original fixed table reference and are not recopied.
    new=[e for s,e in sorted(by_sha.items()) if s not in raw_bindings]
    schema=pa.schema([pa.field('sha256',pa.string(),nullable=False),lance.blob_field('data')])
    fingerprint=sha(json.dumps([(e['sha256'],e['byte_size']) for e in new],sort_keys=True).encode())
    def blobs():
        for e in new:
            data=Path(e['source']).read_bytes()
            if sha(data)!=e['sha256']:raise ValueError('Source changed during preservation: '+e['source'])
            yield {'sha256':e['sha256'],'data':data}
    ref,_,_=write_registered_table(root,'datasets/blobs__' + RUN_ID + '.lance',schema=schema,
        schema_name='evidence_blobs',schema_version='v1',rows_factory=blobs,fingerprint=fingerprint,max_rows_per_batch=8)
    index=[]
    for e in entries:
        in_raw=e['sha256'] in raw_bindings
        index.append({**{k:v for k,v in e.items() if k!='source'},
            'blob_uri':'demiwtg/collect/datasets/images.lance' if in_raw else ref.relative_uri,
            'blob_version':raw.version if in_raw else ref.lance_version,'blob_column':'data'})
    manifest,_,_=write_registered_table(root,'datasets/artifacts__' + RUN_ID + '.lance',schema=ARTIFACTS,
        schema_name='named_artifacts',schema_version='v1',rows_factory=lambda:iter(index),
        fingerprint=sha(json.dumps(index,sort_keys=True).encode()))
    evidence=ArtifactSet(root,manifest)
    result=evidence.verify()
    result.update(reference=manifest.to_dict(),restored_source_images=len(missing),
                  raw_version=raw.version,stored_unique_blobs=len(new),
                  logical_bytes=sum(e['byte_size'] for e in entries),
                  stored_blob_bytes=sum(e['byte_size'] for e in new))
    log.put('verified_preservation',result)
    return result


# Superseded drivers and viewers have no active imports; required protocol
# interpreters / scoring oracles remain beside the V1 ops and tests.
RETIRE_FILES = (
    'benchmark/edit/v1/dual_carrier_supplement.py',
    'benchmark/edit/v1/hard_plus_regen.py',
    'benchmark/edit/v1/audit_doublecheck.py',
    'benchmark/edit/v1/reviews/question_dev.ipynb',
    'benchmark/t2i/v1/reviews/question_dev.ipynb',
    'evaluation/t2i/v1/reviews/development_review.ipynb',
    'evaluation/t2i/v1/eval_t2i_gen.py',
    'evaluation/edit/v1/eval_edit_gen.py',
    'evaluation/edit/v1/gen_results_review.py',
    'evaluation/edit/v1/run_edit_judges.py',
    'evaluation/edit/v1/finalize_edit_judges.py',
    'evaluation/edit/v1/validate_edit_inputs.py',
    'evaluation/edit/v1/test_score_validity.py',
)


def retire(entries, inventory, root=None, repo=PROJECT_ROOT):
    """Delete only reviewed old files, after preserved bytes and consumers pass."""
    import os
    import stat
    from project import HISTORICAL_EVIDENCE
    root=resolve_root(root); repo=Path(repo).resolve()
    log=LanceRecordStore(root,'datasets/records__' + RUN_ID + '.lance')
    verified=log.get('verified_final_preservation')
    if not verified or verified['reference'] != HISTORICAL_EVIDENCE:
        raise ValueError('Require the verified fixed preservation reference')
    assets=ArtifactSet(root,HISTORICAL_EVIDENCE)
    retained={e['path']:e for e in entries}
    if set(retained) != set(assets.entries):raise ValueError('Preservation plan mismatch')
    for name,e in retained.items():
        if (e['sha256'],e['byte_size']) != (assets.entries[name]['sha256'],assets.entries[name]['byte_size']):
            raise ValueError('Preservation identity changed: '+name)
    original={r['path']:r for r in inventory['files']}
    candidates=set()
    roots=(*RETIRE_ROOTS,'evaluation/bagel/data')
    for relative in roots:
        start=repo/relative
        for directory,dirs,files in os.walk(start,followlinks=False):
            if {'collect','_staging','.git'} & set(Path(directory).relative_to(repo).parts):
                raise ValueError('Excluded directory in retirement tree')
            for name in dirs[:]:
                p=Path(directory)/name
                if p.is_symlink():candidates.add(p);dirs.remove(name)
            candidates.update(Path(directory)/name for name in files)
    for relative in ('evaluation/reviews','curation/edit/reviews'):
        for directory,dirs,files in os.walk(repo/relative,followlinks=False):
            if {'collect','_staging','.git'} & set(Path(directory).relative_to(repo).parts):
                raise ValueError('Excluded review directory')
            for name in files:
                p=Path(directory)/name
                # The small current notebook and its entry README are the only
                # retained filesystem review files; their originals are in Lance.
                if p.parent.parent==repo/relative and name in {'README.md','review.ipynb'}:continue
                candidates.add(p)
    candidates.update(repo/relative for relative in RETIRE_FILES if (repo/relative).exists())
    def inspect(path):
        name=path.relative_to(repo).as_posix();info=path.lstat()
        if {'collect','_staging','.git'} & set(Path(name).parts):raise ValueError('Excluded source: '+name)
        if path.is_symlink():
            # Unlink only the directory entry; model/cache targets are untouched.
            return {'path':name,'kind':'symlink','target':os.readlink(path),'byte_size':0,
                    'mtime_ns':info.st_mtime_ns,'inode':info.st_ino}
        if not stat.S_ISREG(info.st_mode):raise ValueError('Unexpected special file: '+name)
        old=original.get(repo.name+'/'+name)
        if '__pycache__' not in path.parts and (not old or old['bytes']!=info.st_size):
            raise ValueError('Unreviewed or resized file: '+name)
        raw=path.read_bytes();digest=sha(raw)
        if name in retained and digest!=retained[name]['sha256']:
            raise ValueError('Retained source changed before deletion: '+name)
        return {'path':name,'kind':'file','sha256':digest,'byte_size':len(raw),
                'preserved':name in retained,'mtime_ns':info.st_mtime_ns,'inode':info.st_ino}
    with ThreadPoolExecutor(max_workers=4) as pool: deletion=list(pool.map(inspect,sorted(candidates)))
    # Non-active source/notebook bytes remain available as exact provenance,
    # outside the working directory and without another executable entry point.
    snapshots=[]
    for e in deletion:
        if e['kind']=='file' and not e['preserved'] and Path(e['path']).suffix in {'.py','.sh','.ipynb'}:
            text=(repo/e['path']).read_text()
            snapshots.append({'path':e['path'],'sha256':e['sha256'],'text':text})
    log.put('final_retired_source_evidence',snapshots)
    receipt=log.put('final_retirement_inventory',deletion)
    print({'retirement_inventory':receipt.to_dict(),'files':len(deletion),
           'bytes':sum(e['byte_size'] for e in deletion)},flush=True)
    # The first preservation attempt restored these before the blob checkpoint
    # was retried; record the actual raw delta rather than the retry's zero.
    before=lance.dataset(str(root/'demiwtg/collect/datasets/images.lance'),version=4)
    after=lance.dataset(str(root/'demiwtg/collect/datasets/images.lance'),version=5)
    delta=after.count_rows()-before.count_rows()
    if delta!=79:raise ValueError('Unexpected raw restoration delta')
    formal={q['_sha256'] for q in assets.rows('benchmark/edit/v1/bench200/questions.jsonl')}
    predicate='sha256 IN ('+','.join("'"+key+"'" for key in sorted(formal))+')'
    old_formal=before.to_table(columns=['sha256'],filter=predicate).num_rows
    new_formal=after.to_table(columns=['sha256'],filter=predicate).num_rows
    if (old_formal,new_formal)!=(152,200):raise ValueError('Formal source restoration mismatch')
    log.put('raw_source_restoration',{'before_version':4,'after_version':5,'restored_images':delta,'formal_images':48,'additional_plan_images':31})
    for e in deletion:
        path=repo/e['path'];info=path.lstat()
        if (info.st_mtime_ns,info.st_ino)!=(e['mtime_ns'],e['inode']):
            raise ValueError('Source changed during deletion: '+e['path'])
        path.unlink()
    for relative in (*roots,'evaluation/reviews','curation/edit/reviews'):
        for directory,_,_ in os.walk(repo/relative,topdown=False,followlinks=False):
            try:Path(directory).rmdir()
            except OSError:pass  # retained current entry notebooks
    result={'deleted_entries':len(deletion),'removed_file_bytes':sum(e['byte_size'] for e in deletion),
            'preserved_deleted_files':sum(e.get('preserved',False) for e in deletion),
            'retired_source_snapshots':len(snapshots),'preservation':verified['reference'],
            'raw_images_restored':delta,'formal_images_restored':48,'plan_images_restored':31,'excluded':['collect','_staging'],'inventory':receipt.to_dict()}
    log.put('retirement_complete',result)
    return result

def main():
    p=argparse.ArgumentParser();p.add_argument('action',choices=['plan','preserve','retire']);p.add_argument('--plan',type=Path,required=True)
    p.add_argument('--inventory',type=Path)
    args=p.parse_args()
    if args.action=='plan':
        entries=plan();args.plan.write_text(json.dumps(entries,ensure_ascii=False,indent=2));print({'selected_files':len(entries),'bytes':sum(e['byte_size'] for e in entries)},flush=True)
    elif args.action=='retire':
        if not args.inventory:p.error('--inventory required for retire')
        print(json.dumps(retire(json.loads(args.plan.read_text()),json.loads(args.inventory.read_text())),ensure_ascii=False,indent=2),flush=True)
    else:print(json.dumps(preserve(json.loads(args.plan.read_text())),ensure_ascii=False,indent=2),flush=True)
if __name__=='__main__':main()
