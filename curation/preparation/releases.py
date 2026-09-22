"""Register reviewed business outputs by reference; never copy them through files."""
from demiflow.lance.refs import DatasetRef
from demiflow.lance.registry import Catalog, ReleaseRegistry
from project import resolve_root
from curation.preparation.records import rows


def publish_stage(release_id, dataset_ref, *, kind, run, previous_release_id=None):
    """Freeze a complete selected output table after business-level validation."""
    if kind not in {'knowledge', 'visual_materials', 'training', 'benchmark', 'evaluation'}:
        raise ValueError('Unknown V2 release kind')
    ref = DatasetRef.from_dict(dataset_ref)
    if ref.schema_name != 'pipeline_stage_rows':
        raise ValueError('Expected a V2 pipeline stage table')
    count = 0
    for row in rows(dataset_ref):
        count += 1
        if kind == 'training' and (not row.get('export_ready') or not row.get('training_sample')):
            raise ValueError('Training release contains an unapproved/incomplete sample')
        if kind == 'benchmark' and not row.get('export_ready'):
            raise ValueError('Benchmark release contains an unapproved question')
        if kind == 'knowledge' and row.get('status') != 'reviewed':
            raise ValueError('Knowledge release contains an unpublished concept')
        if kind == 'visual_materials' and not row.get('visual_materials'):
            raise ValueError('Visual release must contain explicitly published visual materials')
    if count == 0: raise ValueError('Cannot publish an empty delivery')
    Catalog(resolve_root()).register(ref)
    ReleaseRegistry(resolve_root()).register(release_id, release_kind=kind, table_refs=[ref],
        pipeline_run=str(run), previous_release_id=previous_release_id,
        validation={'pipeline_version':'V2','rows':count,'policy':'reviewed_stage/1'})
    return ref
