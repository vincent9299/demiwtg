"""Cut over fully checked single tables and typed historical evidence."""
import json
from pathlib import Path
from demiflow.lance.registry import Catalog, ReleaseRegistry
from demiflow.lance.refs import DatasetRef
from demiflow.lance.legacy import read_legacy_record
from demiflow.execution.artifacts import immutable, read
from demiflow.lance.maintenance import retire_tables
from project import resolve_root


def run(root, acceptance_path):
    root = Path(root)
    materials = (Path(root) / '_demiflow' / 'maintenance' / Path('datasets/records__single_material_tables_20260921.lance').stem.removeprefix('records__'), 'datasets/records__single_material_tables_20260921.lance')
    evidence = (Path(root) / '_demiflow' / 'maintenance' / Path('datasets/records__source_evidence_20260921.lance').stem.removeprefix('records__'), 'datasets/records__source_evidence_20260921.lance')
    acceptance = json.loads(Path(acceptance_path).read_text())
    if acceptance.get('status') != 'passed' or acceptance['real_model_calls'] or acceptance['formal_training_data_published']:
        raise ValueError('Isolated, no-model cross-chain acceptance required')
    pixels = read(materials[0] / ('pixels' + '.json')) if (materials[0] / ('pixels' + '.json')).exists() else read_legacy_record(root, materials[1], 'pixels')
    if not pixels or pixels['errors']:raise ValueError('Published pixel verification required')
    kinds = ('knowledge_drafts','image_observations','concept_selections','taxonomy_history',
             'annotation_protocols','image_descriptions','concept_matches')
    for kind in kinds:
        check = read(evidence[0] / (kind + '.json')) if (evidence[0] / (kind + '.json')).exists() else read_legacy_record(root, evidence[1], kind)
        if not check or not check['all_typed_rows_verified']:raise ValueError('Unverified evidence: '+kind)
    raw_refs = [read(materials[0] / ('images' + '.json')) if (materials[0] / ('images' + '.json')).exists() else read_legacy_record(root, materials[1], 'images')['ref'], read(materials[0] / ('indexed_documents' + '.json')) if (materials[0] / ('indexed_documents' + '.json')).exists() else read_legacy_record(root, materials[1], 'indexed_documents')['ref']]
    registry = ReleaseRegistry(root)
    registry.register('raw_materials_single_table_20260921',release_kind='raw_materials',table_refs=raw_refs,
        pipeline_run='single_material_tables_20260921',validation={'one_table_per_entity':True,
        'audit_record':materials.relative_uri,'all_published_pixels_verified':True})
    registry.register('annotations_current_20260921', release_kind='annotations', table_refs=[(read(evidence[0] / (k + '.json')) if (evidence[0] / (k + '.json')).exists() else read_legacy_record(root, evidence[1], k))['ref'] for k in ('annotation_protocols', 'image_descriptions', 'concept_matches')], pipeline_run='source_evidence_20260921', validation={'audit_record': evidence.relative_uri, 'all_typed_rows_verified': True})
    immutable(materials[0] / ('cross_chain_acceptance' + '.json'), acceptance)
    old = [r['relative_uri'] for values in (read(materials[0] / ('inputs' + '.json')) if (materials[0] / ('inputs' + '.json')).exists() else read_legacy_record(root, materials[1], 'inputs')).values() for r in values]
    old += list(read(evidence[0] / ('inputs' + '.json')) if (evidence[0] / ('inputs' + '.json')).exists() else read_legacy_record(root, evidence[1], 'inputs'))
    result = retire_tables(root,table_uris=old,release_ids=['raw_materials_current_20260921'],
        operation_id='single_tables_retirement_20260921',reason='User requires actual one-table images/documents and clear typed evidence; manifests, source rows, all published pixels and current pipelines verified')
    for uri in old:
        directory=(root/uri).parent
        while directory!=root:
            try:directory.rmdir()
            except OSError:break
            directory=directory.parent
    # Surviving fixed refs and releases must all open after source deletion.
    refs=Catalog(root).registered()
    for ref in refs:ref.open(root)
    for row in registry.rows():
        for spec in json.loads(row['table_refs']):DatasetRef.from_dict(spec).open(root)
    proof={'retirement':result,'registered_versions':len(refs),
        'registered_tables':len({r.relative_uri for r in refs}),
        'releases':[r['release_id'] for r in registry.rows()]}
    immutable(materials[0] / ('complete' + '.json'), proof)
    print(json.dumps(proof,ensure_ascii=False),flush=True)
    return proof

if __name__=='__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--acceptance', type=Path, required=True)
    args = parser.parse_args()
    run(resolve_root(), args.acceptance)
