"""Publish fixed selections of entity tables, without copying release tables."""
import json
from pathlib import Path
from demiflow.lance.registry import ReleaseRegistry
from demiflow.lance.refs import DatasetRef
from .images import identity, assessment, write_curation
from .articles import article_entity, write_articles


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


def publish_entity_stage(run, kind):
    """Materialize a finished stage into its entity table and pin its selection."""
    from project import resolve_root
    from curation.preparation.stages import stage_ref
    root=resolve_root()
    name='visual_image_meta' if kind=='visual' else 'knowledge_base'
    source=stage_ref(run,name)
    if source.row_count == 0:return None
    rid=kind+'_'+identity(str(Path(run).resolve()))[:24]
    binding={'stage':source.to_dict(),'entity_schema':'v1'}
    raw=None
    if kind=='visual':
        from curation.preparation.records import run_manifest
        from collect.material_schema import IMAGES_URI
        manifest=run_manifest(run)
        raw=manifest.get('config',{}).get('raw_images_ref')
        if raw is None:
            raw=next((s['dataset_ref'] for s in manifest.get('sources',[])
                if s.get('dataset_ref',{}).get('relative_uri')==IMAGES_URI),None)
        if raw is None:raise ValueError('Visual publication requires its frozen raw image source')
    fingerprint=identity({'input':binding,'raw_source':raw}) if kind=='visual' else identity(binding)
    registry=ReleaseRegistry(root)
    prior=registry.get(rid)
    if prior:
        if json.loads(prior['validation']).get('input_fingerprint')!=fingerprint:
            raise ValueError('Published run stage changed')
        return DatasetRef.from_dict(json.loads(prior['table_refs'])[0])
    records=[json.loads(v) for b in source.open(root).scanner(columns=['payload']).to_batches() for v in b.column(0).to_pylist()]
    if not records:return None
    if kind=='visual':
        return publish_visual_records(root,records,rid,source_ref=raw,run_id=str(run),binding=binding)
    ref=write_articles(root,[article_entity(r,release_id=rid,run_id=str(run)) for r in records])
    registry.register(rid,release_kind='knowledge',table_refs=[ref],pipeline_run=str(run),
        validation={'input_fingerprint':fingerprint,'selection_release_id':rid})
    return ref
