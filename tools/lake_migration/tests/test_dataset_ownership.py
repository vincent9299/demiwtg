import json

import lance
import pyarrow as pa
import pytest

from demiflow.collect.document_library import DocumentLibrary
from demiflow.collect.documents import read_document, store_document
from demiflow.lance.storage import resolve_local_uri
from demiflow.objects import ObjectRef
from tools.lake_migration.dataset_ownership import relocate


def test_table_move_preserves_versions_controls_and_incoming_alias(tmp_path):
    old, new = 'datasets/items.lance', 'owner/datasets/items.lance'
    table = tmp_path / old
    lance.write_dataset(pa.table({'id': [1]}), str(table))
    lance.write_dataset(pa.table({'id': [2, 3]}), str(table), mode='overwrite')
    control = tmp_path / 'datasets/_demiflow/items.lance'
    control.mkdir(parents=True)
    (control / 'checkpoint.json').write_text('{"preserved": true}')
    manifest = tmp_path / '_demiflow/lance_locations.json'
    manifest.parent.mkdir()
    manifest.write_text(json.dumps({'version': 1, 'tables': {'ancient.lance': old}}))
    result = relocate(tmp_path, 'core', {old: new})
    assert result['complete'] and not table.exists() and not control.exists()
    assert (tmp_path / 'owner/datasets/_demiflow/items.lance/checkpoint.json').read_text() == '{"preserved": true}'
    for uri in (old, 'ancient.lance'):
        assert resolve_local_uri(tmp_path / uri) == tmp_path / new
        assert lance.dataset(str(resolve_local_uri(tmp_path / uri)), version=1).to_table()['id'].to_pylist() == [1]
    assert relocate(tmp_path, 'core', {old: new}) == result


def test_document_move_preserves_receipts_objects_and_declared_identity(tmp_path):
    url = 'https://example.test/article'
    original = store_document(tmp_path / 'source', b'Preserved document.', url=url,
                              final_url=url, content_type='text/plain',
                              retrieved_at='2026-01-01T00:00:00+00:00')
    library = DocumentLibrary(index_path=tmp_path / 'datasets/document_library/index.sqlite',
                              object_directory=tmp_path / 'datasets/document_objects')
    receipt = library.register({'document_ref': original}, max_bytes=10000, max_document_bytes=10000)
    identity = library.identity
    before = library.lookup(url, max_bytes=10000, max_document_bytes=10000)
    sqlite = (tmp_path / 'datasets/document_library/index.sqlite').read_bytes()
    relocate(tmp_path, 'documents')
    new_index = tmp_path / 'demiwtg/preparation/wiki_documents/datasets/document_library/index.sqlite'
    assert new_index.read_bytes() == sqlite
    assert library.identity == identity
    assert library.lookup(url, max_bytes=10000, max_document_bytes=10000) == before
    current = DocumentLibrary(index_path=new_index,
        object_directory=tmp_path / 'demiwtg/preparation/wiki_documents/datasets/document_objects')
    assert current.lookup(url, max_bytes=10000, max_document_bytes=10000) == before
    assert read_document(receipt['document_ref'])['blocks']
    assert ObjectRef(**receipt['raw_ref']).read() == b'Preserved document.'
    assert not (tmp_path / 'datasets/document_objects').exists()
    assert not (tmp_path / 'datasets/document_library').exists()


def test_live_concept_run_blocks_move_and_old_release_survives_rename(tmp_path):
    from collect.concepts import resolve_concept_release
    from demiflow.execution.artifacts import run_lock
    from demiflow.lance.registry import ReleaseRegistry, write_registered_table
    old = 'datasets/master_concepts.lance'
    ref, _, _ = write_registered_table(tmp_path, old,
        schema=pa.schema([('name', pa.string())]), schema_name='concepts',
        schema_version='v1', fingerprint='fixture', rows_factory=lambda: iter([{'name': 'kept'}]))
    ReleaseRegistry(tmp_path).register('fixed', release_kind='master_data', table_refs=[ref])
    new = 'demiwtg/collect/datasets/concepts.lance'
    with run_lock(tmp_path / '_demiflow/concepts/active_run'):
        with pytest.raises(RuntimeError, match='Another writer'):
            relocate(tmp_path, 'master', {old: new})
        assert (tmp_path / old).exists() and not (tmp_path / new).exists()
    relocate(tmp_path, 'master', {old: new})
    resolved = resolve_concept_release(tmp_path, 'fixed')['dataset_ref']
    assert resolved == ref
    assert resolved.resolve(tmp_path) == str(tmp_path / new)
    assert resolved.open(tmp_path).to_table()['name'].to_pylist() == ['kept']
