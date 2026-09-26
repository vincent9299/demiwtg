"""Whole-table relocation preserves versions, valid empty tables and incomplete writes."""
import json
from pathlib import Path
import lance
import pyarrow as pa
from tools.lake_migration import flatten_datasets as migration
from demiflow.lance.storage import resolve_local_uri


def test_relocate_empty_and_versioned_tables_without_rewriting(tmp_path, monkeypatch):
    plan = [{'old': 'raw/full.lance', 'new': 'demiwtg/collect/datasets/full.lance'},
            {'old': 'raw/empty.lance', 'new': 'demiwtg/collect/datasets/empty.lance'},
            {'old': 'raw/incomplete.lance', 'new': 'demiwtg/collect/datasets/incomplete.lance'}]
    file = tmp_path / 'plan.json'
    file.write_text(json.dumps(plan))
    monkeypatch.setattr(migration, 'PLAN', file)
    full = tmp_path / 'datasets/raw/full.lance'
    lance.write_dataset(pa.table({'id': [1]}), full)
    lance.write_dataset(pa.table({'id': [2]}), full, mode='append')
    lance.write_dataset(pa.table({'id': pa.array([], type=pa.int64())}), tmp_path/'datasets/raw/empty.lance')
    unfinished = tmp_path/'datasets/raw/incomplete.lance'
    unfinished.mkdir()
    (unfinished/'pending').write_bytes(b'uncommitted')
    result = migration.run(tmp_path)
    assert result['tables'] == 3 and result['versions'] == 3
    assert not (tmp_path/'datasets/raw').exists()
    relocated = resolve_local_uri(tmp_path/'raw/full.lance')
    assert lance.dataset(relocated, version=1).to_table()['id'].to_pylist() == [1]
    assert lance.dataset(relocated, version=2).count_rows() == 2
    empty = lance.dataset(tmp_path/plan[1]['new'])
    assert empty.version == 1 and empty.count_rows() == 0
    assert (tmp_path/plan[2]['new']/'pending').read_bytes() == b'uncommitted'
    assert migration.run(tmp_path)['already_complete']
