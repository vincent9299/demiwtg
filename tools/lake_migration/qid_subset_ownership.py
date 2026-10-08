"""Move the frozen 100k subset with all Lance versions; never rewrite table rows."""
import argparse
from contextlib import ExitStack
import hashlib
import json
from pathlib import Path

import lance
from demiflow.execution.artifacts import run_lock
from demiflow.lance.storage import resolve_local_uri, schema_hash
from preparation.qid_images.operators.contracts import atomic_json, table_lock

NAME='qid_sub_100k_bucket_v1'
PAIRS={f'datasets/{NAME}_{kind}.lance':f'demiwtg/subset/datasets/{NAME}_{kind}.lance' for kind in ('concepts','images')}


def inventory(path):
    files=[]
    for file in sorted(path.rglob('*')):
        if file.is_symlink(): raise ValueError('Refusing symlink inside a frozen table')
        if file.is_file():
            with file.open('rb') as stream: digest=hashlib.file_digest(stream,'sha256').hexdigest()
            files.append([file.relative_to(path).as_posix(),file.stat().st_size,digest])
    versions=[]
    for version in lance.dataset(str(path)).versions():
        ds=lance.dataset(str(path),version=version['version'])
        versions.append({'version':ds.version,'rows':ds.count_rows(),'schema_hash':schema_hash(ds.schema)})
    return {'files':files,'versions':versions}


def relocate(root):
    root=Path(root).resolve()
    control=root/'_demiflow/qid_subset_ownership_20261001'
    manifest=root/'_demiflow/lance_locations.json'
    with ExitStack() as stack:
        stack.enter_context(run_lock(control))
        stack.enter_context(run_lock(root/'_demiflow/lance_location_migration'))
        stack.enter_context(run_lock(root/'_demiflow/subset_target'/NAME))
        for old,new in sorted(PAIRS.items()):
            stack.enter_context(table_lock(root/old))
            stack.enter_context(table_lock(root/new))
        mapping=json.loads(manifest.read_text()) if manifest.exists() else {'version':1,'tables':{}}
        if mapping.get('version')!=1: raise ValueError('Unsupported location mapping')
        for old,new in PAIRS.items():
            if old in mapping['tables'] and mapping['tables'][old]!=new: raise ValueError('Existing relocation conflicts')
            source,target=root/old,root/new
            if source.exists() and target.exists(): raise ValueError('Both old and new physical tables exist')
            proof=control/(source.stem+'.json')
            if proof.exists(): before=json.loads(proof.read_text())['before']
            else:
                if not source.exists(): raise ValueError('Missing original table and migration evidence')
                before=inventory(source)
                atomic_json(proof,{'old':old,'new':new,'before':before,'complete':False})
            if source.exists():
                if inventory(source)!=before: raise ValueError('Original table changed after inventory')
                target.parent.mkdir(parents=True,exist_ok=True)
                source.rename(target)
            if inventory(target)!=before: raise ValueError('Relocated file or history mismatch')
            mapping['tables'][old]=new
            atomic_json(manifest,mapping)
            if resolve_local_uri(source)!=target: raise ValueError('Frozen reference resolver did not follow the mapping')
            atomic_json(proof,{'old':old,'new':new,'before':before,'complete':True})
        result={'complete':True,'tables':PAIRS,'row_values_changed':False,'versions_preserved':True}
        atomic_json(control/'result.json',result)
        return result


def validate_release_coverage(root, release_id):
    """Check frozen subset membership against the paired public release before moving it."""
    from demiflow import data
    from preparation.qid_concepts.operators.publication import resolve_release
    from preparation.qid_images.operators.contracts import table_ref
    root=Path(root).resolve()
    release=resolve_release(root,release_id)
    concepts=table_ref(root,f'datasets/{NAME}_concepts.lance',1)
    images=table_ref(root,f'datasets/{NAME}_images.lance',5)
    if concepts.row_count!=100000 or images.row_count!=490575:
        raise ValueError('Unexpected frozen 100k subset size')
    with data.local_execution(workers=8,partitions=32,memory_bytes=1024**3):
        old_c=data.read_lance(concepts.resolve(root),version=concepts.lance_version,columns=['qid','image_sha256s'])
        new_c=data.read_lance(release['concepts'].resolve(root),version=release['concepts'].lance_version,columns=['qid','image_sha256s'])
        old_i=data.read_lance(images.resolve(root),version=images.lance_version,columns=['sha256'])
        new_i=data.read_lance(release['images'].resolve(root),version=release['images'].lance_version,columns=['sha256'])
        missing_qids=old_c.select_columns(['qid']).join(new_c.select_columns(['qid']),on='qid',how='anti').count()
        missing_shas=old_i.join(new_i,on='sha256',how='anti').count()
        old_pairs=old_c.flat_map(lambda r:[{'qid':r['qid'],'sha256':sha} for sha in r['image_sha256s']])
        new_pairs=new_c.join(old_c.select_columns(['qid']),on='qid',how='semi').flat_map(
            lambda r:[{'qid':r['qid'],'sha256':sha} for sha in r['image_sha256s']])
        missing_pairs=old_pairs.join(new_pairs,on=['qid','sha256'],how='anti').count()
    result={'release_id':release_id,'concepts_ref':concepts.to_dict(),'images_ref':images.to_dict(),
            'public_concepts_ref':release['concepts'].to_dict(),'public_images_ref':release['images'].to_dict(),
            'missing_qids':missing_qids,'missing_shas':missing_shas,'missing_selected_relations':missing_pairs,
            'passed':not (missing_qids or missing_shas or missing_pairs)}
    atomic_json(root/'_demiflow/qid_subset_ownership_20261001/coverage.json',result)
    if not result['passed']: raise ValueError('Public release does not cover the frozen subset')
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',required=True,type=Path)
    parser.add_argument('--release-id',required=True)
    args=parser.parse_args()
    validate_release_coverage(args.root,args.release_id)
    print(json.dumps(relocate(args.root),ensure_ascii=False,indent=2))


if __name__=='__main__': main()
