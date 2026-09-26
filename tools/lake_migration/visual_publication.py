"""Import historical visual releases without rewriting their selection identity."""
import json
from pathlib import Path
from demiflow.lance.registry import ReleaseRegistry
from demiflow.lance.refs import DatasetRef
from preparation.operaters.images import identity, assessment, write_curation


def publish_visual_records(root, records, release_id, *, source_ref, source_file=None, run_id=None, binding=None, previous=None):
    records=list(records)
    source_spec=source_ref.to_dict() if isinstance(source_ref,DatasetRef) else source_ref
    fingerprint=identity({'input':binding if binding is not None else records,'raw_source':source_spec})
    registry=ReleaseRegistry(root)
    existing=registry.get(release_id)
    if existing:
        if json.loads(existing['validation']).get('input_fingerprint')!=fingerprint:
            raise ValueError('Release already has a different fingerprint')
        return DatasetRef.from_dict(json.loads(existing['table_refs'])[0])
    values=[]
    for i,record in enumerate(records,1):
        values.append({'sha256':record['sha256'],'concept_assessments':[
            assessment(record,release_id,source_file=source_file,source_row=i,run_id=run_id)]})
    ref=write_curation(root,values,source_ref=source_ref)
    registry.register(release_id,release_kind='visual_materials',table_refs=[ref],pipeline_run=run_id,
        validation={'input_fingerprint':fingerprint,'selection_release_id':release_id},previous_release_id=previous)
    return ref

