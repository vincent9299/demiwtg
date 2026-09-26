"""Published references and notebook rendering use committed Lance content."""
import json

import lance
import pytest
from demiflow.lance.registry import ReleaseRegistry
from project import resolve_root

from preparation.operaters.inputs import iter_material_rows
from demiflow.lance.refs import DatasetRef
from preparation.operaters.results import save_results
from preparation.operaters.runfiles import run_records
from preparation.tests.publication_fixtures import inputs, publish_fixture


def test_release_is_consumable_and_does_not_follow_table_head(inputs):
    run = inputs[0] / 'published_delivery'
    row = {'concept': '流程图', 'status': 'reviewed', 'knowledge': []}
    ref = publish_fixture(run, 'knowledge_base', [row])
    # Existing releases remain readable; the current pipeline creates none.
    published = DatasetRef.from_dict(ref)
    ReleaseRegistry(resolve_root()).register('fixed_knowledge', release_kind='knowledge',
                                            table_refs=[published], pipeline_run=str(run))
    original = published.open(resolve_root()).to_table()
    lance.write_dataset(original, published.resolve(resolve_root()), mode='append')
    records, frozen = iter_material_rows({'dataset_ref': ref, 'release_id': 'fixed_knowledge'})
    assert list(records) == [row]
    assert frozen == {'dataset_ref':ref,'release_id':'fixed_knowledge'}
    record = ReleaseRegistry(resolve_root()).get('fixed_knowledge')
    assert json.loads(record['table_refs']) == [ref]


@pytest.mark.parametrize('write_mode', ['merge', 'append', 'overwrite'])
def test_results_are_saved_once_without_a_release(inputs, monkeypatch, write_mode):
    monkeypatch.setattr(ReleaseRegistry, 'register', lambda *a, **kw: pytest.fail('Results must not create releases'))
    run = inputs[0] / 'article_result'
    row = {'concept': '流程图', 'status': 'reviewed', 'knowledge': []}
    publish_fixture(run, 'knowledge_base', [row])
    ref = save_results(run, 'article', write_mode=write_mode)
    result = run_records(run).get('results/article')
    assert result['dataset_ref'] == ref.to_dict()
    assert ReleaseRegistry(resolve_root()).get(result['release_id']) is None
    assert save_results(run, 'article', write_mode=write_mode) == ref
    # Growing the entity table cannot move the run's pinned result.
    lance.write_dataset(ref.open(resolve_root()).to_table(), ref.resolve(resolve_root()), mode='append')
    assert save_results(run, 'article', write_mode=write_mode) == ref
    assert ref.open(resolve_root()).count_rows() == 1
    with pytest.raises(ValueError, match='Saved result stage changed'):
        save_results(run, 'article', write_mode='overwrite' if write_mode == 'append' else 'append')


def test_failed_article_result_cannot_be_consumed_as_reviewed(inputs):
    run = inputs[0] / 'failed_article'
    publish_fixture(run, 'knowledge_base', [{'concept': '流程图', 'status': 'failed', 'knowledge': []}])
    ref = save_results(run, 'article')
    result = run_records(run).get('results/article')
    rows, _ = iter_material_rows({'dataset_ref': ref.to_dict(), 'release_id': result['release_id']})
    assert list(rows) == []



def test_direct_result_table_without_release_is_reviewed_and_version_pinned(inputs, monkeypatch):
    """无发布登记的 preparation 结果可直接消费，且不会跟随表头或混入失败行。"""
    import pyarrow as pa
    from preparation.operaters.article import ARTICLES, article_entity
    from preparation.operaters.inputs import freeze_material_source
    monkeypatch.setattr(ReleaseRegistry, 'get', lambda *a, **kw: pytest.fail('No alias lookup'))
    uri = resolve_root() / 'demiwtg/preparation/datasets/articles.lance'
    uri.parent.mkdir(parents=True, exist_ok=True)
    good = {'concept': "概念'A", 'status': 'reviewed', 'knowledge': []}
    bad = {'concept': '失败概念', 'status': 'failed', 'knowledge': []}
    table = pa.Table.from_pylist([article_entity(row, run_id='direct') for row in (good, bad)], schema=ARTICLES)
    ds = lance.write_dataset(table, str(uri))
    spec = {'uri': str(uri.relative_to(resolve_root())), 'version': ds.version}
    frozen = freeze_material_source(spec)
    assert frozen == spec
    lance.write_dataset(table, str(uri), mode='append')
    records, identity = iter_material_rows(spec)
    assert [r['concept'] for r in records] == [good['concept']]
    assert identity == frozen
    assert len(list(iter_material_rows(spec, concepts=[good['concept']])[0])) == 1
    assert list(iter_material_rows(spec, concepts=['失败概念'])[0]) == []
    with pytest.raises(ValueError, match='aliases are not supported'):
        freeze_material_source('unneeded_alias')

def test_direct_source_validation_has_no_table_or_registry_conversion(tmp_path, monkeypatch):
    """显式路径和版本无需打开表或扩成 DatasetRef；读取操作留给实际消费时。"""
    from preparation.operaters.inputs import freeze_material_source
    monkeypatch.setattr(lance, 'dataset', lambda *a, **kw: pytest.fail('Validation must not open a table'))
    monkeypatch.setattr(DatasetRef, 'from_dict', lambda *a, **kw: pytest.fail('No reference roundtrip'))
    monkeypatch.setattr(ReleaseRegistry, 'get', lambda *a, **kw: pytest.fail('No registry lookup'))
    spec = {'uri': 'demiwtg/preparation/datasets/articles.lance', 'version': 4}
    assert freeze_material_source(spec) == spec
    assert freeze_material_source({'uri': str(resolve_root() / spec['uri']), 'version': 4}) == spec
