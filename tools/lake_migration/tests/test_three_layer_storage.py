"""隔离验证整表迁移：历史快照、旧引用、控制文件及失败回滚。"""
import fcntl
import json
from pathlib import Path

import lance
import pyarrow as pa
import pytest
from demiflow.lance.control import control_directory, table_lock_path
from demiflow.lance.refs import DatasetRef
from demiflow.lance.storage import schema_hash

from tools.lake_migration import three_layer_storage as migration


@pytest.fixture
def lake(tmp_path, monkeypatch):
    plan = [migration.PLAN[0], migration.PLAN[-1]]
    monkeypatch.setattr(migration, 'PLAN', plan)
    for item in plan:
        uri = tmp_path / item['old']
        uri.parent.mkdir(parents=True, exist_ok=True)
        table = pa.table({'id': [1], 'text': ['保留原始内容']})
        lance.write_dataset(table, uri)
        lance.write_dataset(table, uri, mode='append')
        control = control_directory(uri)
        control.mkdir(parents=True)
        (control / 'saved.json').write_text('{"version": 1}')
    manifest = tmp_path / '_demiflow/lance_locations.json'
    manifest.parent.mkdir()
    manifest.write_text(json.dumps({'version': 1, 'tables': {
        'curated/articles.lance': plan[1]['old'],
        'unrelated.lance': 'datasets/unrelated.lance',
    }}))
    return tmp_path, plan, manifest


def test_versions_frozen_reference_and_controls_survive(lake):
    root, plan, manifest = lake
    table = lance.dataset(root / plan[1]['old'], version=1)
    ref = DatasetRef(dataset_id='curated/articles', store_id='local',
                     relative_uri='curated/articles.lance', lance_version=1,
                     schema_name='fixture', schema_version='v1',
                     schema_hash=schema_hash(table.schema), row_count=1)
    expected = ref.open(root).to_table()
    before = {item['old']: migration.inventory(root / item['old']) for item in plan}
    assert migration.run(root)['apply'] is False
    result = migration.run(root, apply=True)
    assert result['tables'] == 2 and result['versions'] == 4
    for item in plan:
        assert not (root / item['old']).exists()
        assert not control_directory(root / item['old']).exists()
        assert migration.inventory(root / item['new']) == before[item['old']]
        assert (control_directory(root / item['new']) / 'saved.json').read_text() == '{"version": 1}'
        assert lance.dataset(root / item['new'], version=1).count_rows() == 1
        assert lance.dataset(root / item['new'], version=2).count_rows() == 2
    assert ref.open(root).to_table().equals(expected)
    assert ref.lance_version == 1
    mapping = json.loads(manifest.read_text())['tables']
    assert mapping['curated/articles.lance'] == plan[1]['new']
    assert mapping['unrelated.lance'] == 'datasets/unrelated.lance'
    assert migration.run(root, apply=True)['already_complete'] is True


def test_failed_second_move_restores_tables_and_mapping(lake, monkeypatch):
    root, plan, manifest = lake
    before = manifest.read_bytes()
    rename = Path.rename

    def fail_second(source, target):
        if source == root / plan[1]['old']:
            raise OSError('simulated move failure')
        return rename(source, target)

    monkeypatch.setattr(Path, 'rename', fail_second)
    with pytest.raises(OSError, match='simulated'):
        migration.run(root, apply=True)
    assert manifest.read_bytes() == before
    for item in plan:
        assert not (root / item['new']).exists()
        assert lance.dataset(root / item['old']).count_rows() == 2
        assert (control_directory(root / item['old']) / 'saved.json').is_file()
    assert not (root / '_demiflow/three_layer_storage_20260927/receipt.json').exists()


@pytest.mark.parametrize('blocker', ['destination', 'writer'])
def test_preflight_refuses_collision_or_active_writer(lake, blocker):
    root, plan, manifest = lake
    before = manifest.read_bytes()
    with table_lock_path(root / plan[0]['old']).open('a') as lock:
        if blocker == 'writer':
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        else:
            (root / plan[1]['new']).mkdir(parents=True)
        with pytest.raises((ValueError, BlockingIOError)):
            migration.run(root, apply=True)
    assert manifest.read_bytes() == before
    assert all(lance.dataset(root / item['old']).count_rows() == 2 for item in plan)
