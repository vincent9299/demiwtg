"""Standalone visual orchestration, its shared graph and frozen-run boundaries."""
import json
from contextlib import nullcontext

import pytest
from demiflow.standalone import local_data

from curation.training.tests.test_authoring import inputs, write_rows, publish_fixture
from curation.training.tests.test_v2_visual_training import description
from curation.preparation.tests.test_confirm_images import decision
from curation.preparation.visual_pipeline import load_graph
from curation.preparation.published import published_record
from curation.preparation.contracts import digest


def fixture_run(inputs):
    source = inputs[0]/'visual_source'
    asset = inputs[4][0]
    source = publish_fixture(source, 'knowledge_base', [{'concept':'流程图', 'status':'failed', 'knowledge':['OLD TEXT MUST NOT ENTER'],
        'images':[{'kind':'legacy_images', 'record':{'sha256':asset['sha256'], 'url':'test://fixture'},
                   'bytes':{'sha256':asset['sha256'], 'path':asset['path'], 'generation_origin':'existing'}}]}])
    return source, inputs[0]/'visual_run', inputs[0].parents[3]


def test_prepare_runs_without_any_model_or_text_branch(inputs, monkeypatch):
    source, run, project = fixture_run(inputs)
    graph = load_graph()
    graph['image_prompt_data'] = lambda *a, **kw: pytest.fail('prepare must not call a model')
    result = graph['run_visual_pipeline'](run, source, project/'datasets', project=project,
                                        model_config={'image_annotations_ref':None})
    records = list(result.iter_rows())
    assert len(records) == 1 and records[0]['pixel_images']
    assert 'OLD TEXT MUST NOT ENTER' not in json.dumps(records)
    assert not (run/'knowledge_base.jsonl').exists()
    assert not (run/'datasets/documents_processed.jsonl').exists()
    assert not (run/'image_index_snapshot').exists()
    # Changing input cannot silently resume a frozen run.
    source = {**source, 'row_count':source['row_count']+1}
    with pytest.raises(ValueError, match='Immutable record differs|row count|row_count'):
        graph['run_visual_pipeline'](run, source, project/'datasets', project=project,
                                     model_config={'image_annotations_ref':None})


def test_export_reuses_shared_review_and_is_consumable(inputs, monkeypatch):
    source, run, project = fixture_run(inputs)
    graph = load_graph()
    calls = []
    dataset_type = type(local_data().from_iter(lambda: iter([])))

    def fixed_prompt(self, name, **kwargs):
        assert name == 'select_images', 'Article/text model accidentally called'
        def respond(row):
            calls.append(row['batch_id'])
            items = [{**decision(i, 'keep'), 'image_metadata':description(),
                      'visual_support':{'supports':'分支形态', 'region':'中央', 'limitations':'仅此图'}}
                     for i in row['image_prompt']['image_ids']]
            return {**row, 'prompt_result':{'images':items}, 'prompt_call':{'fixture':True}}
        return self.map(respond)

    monkeypatch.setattr(dataset_type, 'map_prompt_async', fixed_prompt)
    graph['image_prompt_data'] = lambda *a, **kw: local_data()
    graph['image_review_service'] = lambda *a, **kw: nullcontext()
    kwargs = dict(project=project, model_config={'image_annotations_ref':None})
    graph['run_visual_pipeline'](run, source, project/'datasets', **kwargs)
    exported = list(graph['run_visual_pipeline'](run, source, project/'datasets', through='export', **kwargs).iter_rows())
    assert len(calls) == 2  # Primary and independent review, no article calls.
    assert exported[0]['knowledge'] == [] and exported[0]['publication_kind'] == 'visual_materials'
    assert len(exported[0]['visual_materials']) == 1
    from curation.preparation.stages import read_stage
    meta = read_stage(run,'visual_image_meta').take_all()
    assert len(meta) == 1 and meta[0]['resolution']['width'] == 90
    assert meta[0]['resolution']['height'] == 80 and meta[0]['byte_size'] > 0
    assert meta[0]['publication_status'] == 'reviewed'
    assert meta[0]['image_metadata']['description'] == description()
    delivered = published_record({**exported[0], '_knowledge_run':str(run),
                                   '_knowledge_sha256':digest(exported[0])})
    assert len(delivered['materials']) == 1
    assert delivered['materials'][0]['kind'] == 'image'
    graph['run_visual_pipeline'](run, source, project/'datasets', through='export', **kwargs)
    assert len(calls) == 2  # Same version reuses completed model stages.
    # Materialized provenance files cannot change the authoritative Lance bytes.
    image_path = inputs[4][0]['path']
    from pathlib import Path
    Path(image_path).write_bytes(b'changed')
    graph['run_visual_pipeline'](run, source, project/'datasets', through='export', **kwargs)
    assert len(calls) == 2
    # Losing the authoritative Blob must still invalidate cached output.
    import shutil
    from project import resolve_root
    sha = inputs[4][0]['sha256']
    shutil.rmtree(resolve_root()/'raw/images.lance')
    with pytest.raises(ValueError, match='visual bytes changed'):
        graph['run_visual_pipeline'](run, source, project/'datasets', through='export', **kwargs)


def test_full_knowledge_uses_the_same_review_graph():
    from pathlib import Path
    note = json.loads((Path(__file__).resolve().parents[2]/'preparation/debug.ipynb').read_text())
    body = next(''.join(c['source']) for c in note['cells'] if ''.join(c['source']).startswith('def run_pipeline'))
    assert 'image_decisions = run_image_review(blocks, run, config, version)' in body
    assert 'related = publish_visual_branch(related, run, version)' in body
    assert 'visual_graph_sha256=visual_graph_hash()' in body
    assert "map_prompt_async('select_images'" not in body


def test_resolution_records_stored_and_oriented_dimensions(tmp_path):
    from PIL import Image
    from curation.preparation.ops.image_pixels import inspect_lake_image
    path = tmp_path/'rotated.jpg'
    exif = Image.Exif(); exif[274] = 6
    Image.new('RGB', (120, 80), 'white').save(path, exif=exif)
    result = inspect_lake_image({'path':path.name, 'sha256':digest(path.read_bytes())})
    assert result['status'] == 'verified_bytes'
    assert result['dimensions'] == [120, 80]
    assert result['resolution']['width'] == 80 and result['resolution']['height'] == 120
    assert result['resolution']['stored_width'] == 120
    assert result['resolution']['megapixels'] == .0096
    assert result['byte_size'] == path.stat().st_size


def test_replay_never_invents_a_missing_independent_review():
    from curation.preparation.tests.test_confirm_images import request
    from curation.preparation.ops.visual_inputs import ReplayVisualResponse
    result = ReplayVisualResponse([])(request())
    assert result['image_decisions'][0]['decision'] == 'pending'
    assert 'visual_publication' not in result['image_decisions'][0]
