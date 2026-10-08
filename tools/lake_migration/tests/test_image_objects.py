import hashlib
import json
import shutil

import lance
import pyarrow as pa
import pytest

from demiflow.objects import ObjectRef
from tools.lake_migration.image_objects import migrate_table


def raw_table(root):
    raw = b'independent image bytes'
    sha = hashlib.sha256(raw).hexdigest()
    schema = pa.schema([('sha256', pa.string()), lance.blob_field('data')])
    lance.write_dataset(pa.Table.from_arrays([pa.array([sha]), lance.blob_array([raw])], schema=schema),
                        str(root / 'raw.lance'))
    return raw, sha


def test_public_reference_survives_removal_of_source_table(tmp_path):
    raw, sha = raw_table(tmp_path)
    source = json.dumps({'relative_uri': 'raw.lance', 'lance_version': 1})
    lance.write_dataset(pa.Table.from_pylist([{'sha256': sha, 'source_refs': [source], 'score': 9}]),
                        str(tmp_path / 'public.lance'))
    receipt = migrate_table(tmp_path, 'public.lance', 1, 'new_public.lance', batch_size=1)
    row = lance.dataset(receipt['target']['uri']).to_table().to_pylist()[0]
    assert row['score'] == 9 and row['source_refs'] == [source]
    assert lance.dataset(str(tmp_path / 'public.lance')).version == 1
    shutil.rmtree(tmp_path / 'raw.lance')
    assert ObjectRef(row['image_uri'], sha).read() == raw


def test_nested_and_json_references_share_one_independent_file(tmp_path):
    raw, sha = raw_table(tmp_path)
    old = {'relative_uri': 'raw.lance', 'version': 1, 'sha256': sha, 'column': 'data'}
    row = {'concept': 'A', 'blob_ref': old, 'references_json': json.dumps([{'blob_ref': old}])}
    lance.write_dataset(pa.Table.from_pylist([row]), str(tmp_path / 'generated.lance'))
    receipt = migrate_table(tmp_path, 'generated.lance', 1, 'new_generated.lance')
    result = lance.dataset(receipt['target']['uri']).to_table().to_pylist()[0]
    assert 'blob_ref' not in result
    assert set(result['object_ref']) == {'uri', 'sha256'}
    assert json.loads(result['references_json'])[0]['object_ref'] == result['object_ref']
    assert len([p for p in (tmp_path / 'objects').rglob('*') if p.is_file()]) == 1
    shutil.rmtree(tmp_path / 'raw.lance')
    assert ObjectRef(**result['object_ref']).read() == raw


def test_raw_export_and_source_protection(tmp_path):
    raw, sha = raw_table(tmp_path)
    with pytest.raises(ValueError, match='new target'):
        migrate_table(tmp_path, 'raw.lance', 1, 'raw.lance')
    receipt = migrate_table(tmp_path, 'raw.lance', 1, 'objects_catalog.lance')
    dataset = lance.dataset(receipt['target']['uri'])
    assert 'data' not in dataset.schema.names
    row = dataset.to_table().to_pylist()[0]
    assert ObjectRef(row['image_uri'], sha).read() == raw
    assert lance.dataset(str(tmp_path / 'raw.lance')).take_blobs('data', indices=[0])[0].read() == raw


def test_artifact_manifest_migration_is_usable_by_current_reader(tmp_path):
    from demiflow.lance.artifacts import ARTIFACTS, ArtifactSet, _LEGACY_ARTIFACTS
    from demiflow.lance.refs import DatasetRef
    from demiflow.lance.storage import schema_hash
    raw, sha = raw_table(tmp_path)
    entry = {'path': 'image.png', 'sha256': sha, 'byte_size': len(raw),
             'blob_uri': 'raw.lance', 'blob_version': 1, 'blob_column': 'data'}
    lance.write_dataset(pa.Table.from_pylist([entry], schema=_LEGACY_ARTIFACTS), str(tmp_path / 'old.lance'))
    receipt = migrate_table(tmp_path, 'old.lance', 1, 'artifacts.lance')
    dataset = lance.dataset(receipt['target']['uri'])
    assert dataset.schema.equals(ARTIFACTS, check_metadata=True)
    ref = DatasetRef('artifacts', 'artifacts.lance', dataset.version, 'named_artifacts', 'v1',
                     schema_hash(dataset.schema), 1)
    saved = ArtifactSet(tmp_path, ref)
    shutil.rmtree(tmp_path / 'raw.lance')
    assert saved.read_bytes('image.png') == raw
    assert saved.object_ref('image.png').read() == raw
