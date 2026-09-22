"""Published references and notebook rendering use committed Lance content."""
import json

import lance
import pytest
from demiflow.lance.registry import ReleaseRegistry
from project import resolve_root

from curation.preparation.publication_sources import iter_publication_rows
from curation.preparation.releases import publish_stage
from curation.training.tests.test_authoring import inputs, publish_fixture


def test_release_is_consumable_and_does_not_follow_table_head(inputs):
    run = inputs[0] / 'published_delivery'
    row = {'concept': '流程图', 'status': 'reviewed', 'knowledge': []}
    ref = publish_fixture(run, 'knowledge_base', [row])
    published = publish_stage('fixed_knowledge', ref, kind='knowledge', run=run)
    original = published.open(resolve_root()).to_table()
    lance.write_dataset(original, published.resolve(resolve_root()), mode='append')
    records, frozen = iter_publication_rows('fixed_knowledge')
    assert list(records) == [row]
    assert frozen == {'dataset_ref':ref,'release_id':'fixed_knowledge'}
    record = ReleaseRegistry(resolve_root()).get('fixed_knowledge')
    assert json.loads(record['table_refs']) == [ref]


@pytest.mark.parametrize('kind,row', [
    ('training', {'task_id': 'unapproved', 'export_ready': False, 'training_sample': {}}),
    ('knowledge', {'concept': 'unapproved', 'status': 'failed'}),
])
def test_unapproved_output_is_not_registered(inputs, kind, row):
    run = inputs[0] / ('rejected_' + kind)
    ref = publish_fixture(run, 'output', [row])
    with pytest.raises(ValueError):
        publish_stage('rejected_' + kind, ref, kind=kind, run=run)
    assert ReleaseRegistry(resolve_root()).get('rejected_' + kind) is None


def test_independent_visual_display_reads_lance_after_file_is_gone(inputs, monkeypatch):
    import IPython.display
    from pathlib import Path
    from curation.preparation.current_results import show_current_results
    asset = inputs[4][0]
    pixels = Path(asset['path']).read_bytes()
    Path(asset['path']).unlink()
    run = inputs[0] / 'visual_display'
    publish_fixture(run, 'knowledge_base', [{
        'concept': '流程图', 'status': 'visual_only', 'knowledge': [],
        'visual_materials': [{'image_id': 'I1', 'image': {'bytes': asset},
                              'publication': {'status': 'reviewed'}}],
    }])
    displayed = []
    monkeypatch.setattr(IPython.display, 'display', displayed.append)
    show_current_results(run)
    assert any(isinstance(value, IPython.display.Image) and value.data == pixels
               for value in displayed)
    displayed.clear()
    show_current_results(run, concepts=['不存在'])
    assert any('没有匹配' in str(getattr(value, 'data', '')) for value in displayed)
