"""Run the visual notebook graph independently, or load its shared subgraph."""
import argparse
import json
import hashlib
from pathlib import Path

from curation.preparation.contracts import ROOT, digest, immutable, read, run_lock

from project import resolve_root

NOTEBOOK = Path(__file__).with_name('visual_materials_debug.ipynb')


def graph_sources():
    return [''.join(c['source']) for c in json.loads(NOTEBOOK.read_text())['cells']
            if c['cell_type'] == 'code' and 'visual-graph' in c.get('metadata', {}).get('tags', [])]


def graph_hash():
    return digest(graph_sources())


def load_graph():
    scope = {}
    for source in graph_sources():
        exec(compile(source, str(NOTEBOOK), 'exec'), scope)
    return scope


def _observed_bytes_valid(record) -> bool:
    """Revalidate content identity in Lance; old file paths are provenance only."""
    from curation.preparation.asset_io import bytes_unchanged
    expected = record.get('actual_sha256')
    return bool(expected) and bytes_unchanged(record.get('path'), expected)


def freeze_visual_run(run, input_path, dataset, config, project=ROOT):
    from curation.preparation.config import check_run_location
    from curation.preparation.notebook_io import preparation_source_code
    from curation.preparation.contracts import runtime_version
    check_run_location(run, project, dataset)
    from demiflow.lance.refs import DatasetRef
    from curation.preparation.records import run_records
    ref = DatasetRef.from_dict(input_path)
    if ref.schema_name not in {'raw_images','articles','pipeline_stage_rows'}:
        raise ValueError('Visual input must be an image/article entity or concept/images stage reference')
    ref.open(dataset)
    source = ref.to_dict()
    records = run_records(run)
    for key, record in records.items().items():
        if key.startswith('observed_asset/') and not _observed_bytes_valid(record):
            raise ValueError('Previously inspected visual bytes changed: ' + key)
    # Pin the byte/source table before review; publications reuse this exact ref.
    from curation.preparation.lake_inputs import resolve_source
    prior = records.get('manifest')
    saved_raw = (prior or {}).get('config',{}).get('raw_images_ref')
    raw_ref = (DatasetRef.from_dict(saved_raw) if saved_raw else
        ref if ref.schema_name == 'raw_images' else resolve_source(dataset, 'legacy_images')[0])
    raw_ref.open(dataset)
    config = {**config, 'raw_images_ref': raw_ref.to_dict()}
    manifest = {'pipeline_version':'V2', 'branch':'preparation.visual_materials',
                'source':source, 'store_id':'local', 'config':config,
                'graph_sha256':graph_hash(), 'code':preparation_source_code(),
                'runtime':runtime_version()}
    records.put('manifest', manifest)
    return digest(manifest), source


def freeze_visual_replay(run, parent, project=ROOT):
    from curation.preparation.records import run_records, run_manifest
    from curation.preparation.stages import stage_ref
    from curation.preparation.notebook_io import preparation_source_code
    from curation.preparation.config import check_run_location
    original = run_manifest(parent)
    check_run_location(run, project, resolve_root())
    if Path(run).resolve() == Path(parent).resolve(): raise ValueError('Replay must use a new run')
    # Explicitly verify model-visible prompt identity before parser-only replay.
    from curation.preparation.ops.prompt_config import material_prompt_pack
    import yaml
    for role, cfg in [('image_primary', original['config']), ('image_review', {
        **original['config'], 'model':original['config']['image_review_model'],
        'base_url':original['config']['image_review_base_url'], 'local_model_comparison':True})]:
        saved = run_records(Path(parent)/role).get('prompt_config')
        _, active = material_prompt_pack(cfg)
        if yaml.safe_load(saved['yaml'])['prompts']['select_images'] != yaml.safe_load(active)['prompts']['select_images']:
            raise ValueError('Replay prompt/model differs')
    refs = {name:stage_ref(parent,name).to_dict() for name in
            ('visual_inputs','image_primary','image_review_responses')}
    for key, record in run_records(parent).items().items():
        if key.startswith('observed_asset/') and not _observed_bytes_valid(record):
            raise ValueError('Recorded visual pixels changed before replay')
    manifest = {'pipeline_version':'V2', 'branch':'preparation.visual_response_revalidation',
                'parent':run_records(parent).reference('manifest').to_dict(), 'inputs':refs,
                'config':original['config'], 'code':preparation_source_code(),
                'graph_sha256':graph_hash(), 'new_model_calls':0}
    run_records(run).put('manifest',manifest)
    return digest(manifest)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True,
                        help='JSON configuration containing a fixed Lance DatasetRef with concept/images rows')
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--dataset', type=Path, default=resolve_root())
    parser.add_argument('--through', choices=['prepare','review','export'], default='prepare')
    parser.add_argument('--config', type=Path, help='Explicit model/annotation config JSON')
    args = parser.parse_args()
    config = read(args.config) if args.config else {}
    result = load_graph()['run_visual_pipeline'](args.run, read(args.input), args.dataset,
                                                through=args.through, model_config=config)
    print(json.dumps({'run':str(args.run), 'through':args.through,
                      'records':sum(1 for _ in result.iter_rows())}))


if __name__ == '__main__':
    main()
