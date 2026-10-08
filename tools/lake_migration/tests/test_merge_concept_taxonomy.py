"""隔离验证无损合并、删除边界、固定旧版本与失败重入；不访问生产数据。"""
from datetime import datetime, timezone
import json

import lance
import pyarrow as pa
import pytest

from collect.concepts import resolve_concept_release
from collect.schemas import REFERENCE_CONCEPTS, TAXONOMY_METADATA
from demiflow.lance.refs import DatasetRef
from demiflow.lance.registry import Catalog, ReleaseRegistry
from demiflow.lance.storage import schema_hash
from tools.lake_migration import merge_concept_taxonomy as migration


@pytest.fixture
def lake(tmp_path):
    """主表顺序与关系表顺序故意不同，包含多路径概念和空分类概念。"""
    now = datetime(2026, 9, 21, tzinfo=timezone.utc)
    concepts = pa.Table.from_pylist([
        {'name': name, 'aliases': [name.upper()], 'carriers': ['text'],
         'taxonomy': json.dumps(paths), 'source_file': 'concepts.json',
         'source_row': i, 'raw_payload': '{}', 'migrated_at_us': now}
        for i, (name, paths) in enumerate([('a', ['P', 'Q']), ('b', ['P']), ('empty', [])])
    ], schema=REFERENCE_CONCEPTS)
    relation_schema = pa.schema([pa.field('concept_key', pa.string(), nullable=False),
                                 *TAXONOMY_METADATA.type.value_type])
    relations = pa.Table.from_pylist([
        {'concept_key': name, 'taxonomy_node_key': 'demiwtg / ' + path, 'ordinal': ordinal,
         'source_ref': 'master/taxonomy/v1/nodes.lance@1', 'source_record_key': record,
         'migrated_at_us': now}
        for name, path, ordinal, record in [('b', 'P', 0, 11), ('a', 'Q', 7, 12), ('a', 'P', 1, 11)]
    ], schema=relation_schema)
    aliases = {'master/concepts/v1/concepts.lance': migration.MASTER,
               'master/memberships/v1/concept_taxonomy.lance': migration.RELATIONS,
               'datasets/master/memberships/v1/concept_taxonomy.lance': migration.RELATIONS}
    (tmp_path / '_demiflow').mkdir()
    (tmp_path / '_demiflow/lance_locations.json').write_text(json.dumps({'version': 1, 'tables': aliases}))
    refs = []
    for table, uri, alias in [(concepts, migration.MASTER, 'master/concepts/v1/concepts.lance'),
                              (relations, migration.RELATIONS, 'master/memberships/v1/concept_taxonomy.lance')]:
        for _ in range(2):
            ds = lance.write_dataset(table, str(tmp_path / uri), mode='overwrite')
            ref = DatasetRef(dataset_id=alias.removesuffix('.lance'), relative_uri=alias,
                             lance_version=ds.version, schema_name='fixture', schema_version='v1',
                             schema_hash=schema_hash(ds.schema), row_count=ds.count_rows())
            Catalog(tmp_path).register(ref)
        refs.append(ref)
    # 模拟发布内的分类节点、边，不因合并关系而丢掉它们。
    for name in ['taxonomy_nodes', 'taxonomy_edges']:
        uri = 'datasets/' + name + '.lance'
        ds = lance.write_dataset(pa.table({'path': ['P', 'Q']}), str(tmp_path / uri))
        ref = DatasetRef(dataset_id=name, relative_uri=uri, lance_version=1,
                         schema_name=name, schema_version='v1', schema_hash=schema_hash(ds.schema), row_count=2)
        Catalog(tmp_path).register(ref)
        refs.append(ref)
    ReleaseRegistry(tmp_path).register(migration.OLD_RELEASE, release_kind='master_data', table_refs=refs)
    return tmp_path, concepts, relations


def test_merge_is_lossless_and_retirement_preserves_pinned_concepts(lake):
    root, concepts, relations = lake
    before_registry = lance.dataset(str(root / '_demiflow/registry/releases.lance')).version
    result = migration.run(root, apply=True)
    master = lance.dataset(str(root / migration.MASTER))
    assert master.version == 3
    assert master.to_table(columns=concepts.column_names).equals(concepts)
    assert lance.dataset(str(root / migration.MASTER), version=2).to_table().equals(concepts)
    rows = master.to_table(columns=['name', 'taxonomy_metadata']).to_pylist()
    reconstructed = [{'concept_key': r['name'], **item} for r in rows for item in r['taxonomy_metadata']]
    assert sorted(reconstructed, key=lambda r: (r['concept_key'], r['taxonomy_node_key'])) == sorted(
        relations.to_pylist(), key=lambda r: (r['concept_key'], r['taxonomy_node_key']))
    assert rows[-1]['taxonomy_metadata'] == []
    assert not (root / migration.RELATIONS).exists()
    assert not (root / 'datasets/_demiflow/concept_taxonomy.lance').exists()
    assert result['relationships'] == 3
    assert ReleaseRegistry(root).get(migration.OLD_RELEASE) is None
    historical = lance.dataset(str(root / '_demiflow/registry/releases.lance'), version=before_registry)
    assert historical.to_table()['release_id'].to_pylist() == [migration.OLD_RELEASE]
    current = ReleaseRegistry(root).get(migration.NEW_RELEASE)
    assert len(json.loads(current['table_refs'])) == 3
    assert resolve_concept_release(root, migration.NEW_RELEASE)['dataset_ref'].lance_version == 3
    for ref in Catalog(root).registered():
        ref.open(root)
    mapping = json.loads((root / '_demiflow/lance_locations.json').read_text())['tables']
    assert set(mapping) == {'master/concepts/v1/concepts.lance'}
    assert migration.run(root, apply=True)['already_complete']
    assert lance.dataset(str(root / migration.MASTER)).version == 3


@pytest.mark.parametrize('kind', ['missing', 'duplicate', 'orphan'])
def test_mismatched_relationships_fail_before_any_migration(lake, kind):
    root, concepts, relations = lake
    rows = relations.to_pylist()
    if kind == 'missing':
        rows.pop()
    elif kind == 'duplicate':
        rows.append(rows[0])
    else:
        rows[0]['concept_key'] = 'unknown'
    with pytest.raises(ValueError):
        migration.metadata_for_concepts(concepts, pa.Table.from_pylist(rows, schema=relations.schema))
    assert lance.dataset(str(root / migration.MASTER)).version == 2
    assert (root / migration.RELATIONS).exists()


def test_other_release_blocks_deletion_before_master_changes(lake):
    root, _, _ = lake
    relation = next(ref for ref in Catalog(root).registered() if 'memberships' in ref.relative_uri)
    ReleaseRegistry(root).register('another_release', release_kind='fixture', table_refs=[relation])
    with pytest.raises(ValueError, match='Another release'):
        migration.run(root, apply=True)
    assert lance.dataset(str(root / migration.MASTER)).version == 2
    assert (root / migration.RELATIONS).exists()


def test_resume_after_delete_failure_does_not_add_another_master_version(lake, monkeypatch):
    root, _, _ = lake
    original = migration.shutil.rmtree
    def fail_delete(path):
        if path == root / migration.RELATIONS:
            raise OSError('simulated delete failure')
        return original(path)
    monkeypatch.setattr(migration.shutil, 'rmtree', fail_delete)
    with pytest.raises(OSError, match='simulated'):
        migration.run(root, apply=True)
    assert lance.dataset(str(root / migration.MASTER)).version == 3
    assert (root / migration.RELATIONS).exists()
    monkeypatch.setattr(migration.shutil, 'rmtree', original)
    result = migration.run(root, apply=True)
    assert result['master_ref']['lance_version'] == 3
    assert not (root / migration.RELATIONS).exists()
