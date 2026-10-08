import json

import lance
import pyarrow as pa

from demiflow.lance.storage import resolve_local_uri
from demiflow.operator_llm.call_ref import read_call
from demiflow.operator_llm.sqlite_journal import SQLitePromptJournal
from tools.lake_migration.curation_ownership import relocate


def test_directory_move_preserves_versions_journal_and_prior_aliases(tmp_path):
    old, new = 'old/datasets', 'curation/example/datasets'
    table = tmp_path / old / 'items.lance'
    lance.write_dataset(pa.table({'id': [1]}), str(table))
    lance.write_dataset(pa.table({'id': [2]}), str(table), mode='overwrite')
    journal = SQLitePromptJournal(tmp_path / old / 'calls.sqlite')
    request = {'case': 'preserved'}
    journal.reserve(request)
    refs = journal.response(request, {'body': 'saved'})
    journal.close()
    before = (tmp_path / old / 'calls.sqlite').read_bytes()
    manifest = tmp_path / '_demiflow/lance_locations.json'
    manifest.parent.mkdir()
    manifest.write_text(json.dumps({'version': 1, 'tables': {'ancient/items.lance': old + '/items.lance'}}))
    result = relocate(tmp_path, {old: new})
    assert result['complete'] and not (tmp_path / old).exists()
    assert (tmp_path / new / 'calls.sqlite').read_bytes() == before
    assert read_call(refs['response_ref']) == {'body': 'saved'}
    replay = SQLitePromptJournal(tmp_path / old / 'calls.sqlite', read_only=True)
    assert replay.lookup(request) == {'body': 'saved'}
    replay.close()
    for alias in ('ancient/items.lance', old + '/items.lance'):
        resolved = resolve_local_uri(tmp_path / alias)
        assert resolved == tmp_path / new / 'items.lance'
        assert lance.dataset(str(resolved), version=1).to_table()['id'].to_pylist() == [1]
        assert lance.dataset(str(resolved), version=2).to_table()['id'].to_pylist() == [2]
    assert relocate(tmp_path, {old: new}) == result
