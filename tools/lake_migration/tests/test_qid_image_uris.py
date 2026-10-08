"""实际文件核验、重复源记录、部分列发布及历史保护。"""
import hashlib
from pathlib import Path

import lance
import pyarrow as pa
import pytest

from tools.lake_migration.qid_image_uris import publish, verify, verify_image, verify_unchanged


def test_metadata_comparison_aligns_rows_by_sha_and_detects_nested_changes(tmp_path):
    rows = [{'sha256': 'b', 'metadata': [{'text': '原值二'}]},
            {'sha256': 'a', 'metadata': [{'text': '原值一'}]}]
    before = lance.write_dataset(pa.Table.from_pylist(rows), str(tmp_path/'before.lance'))
    after = lance.write_dataset(pa.Table.from_pylist(rows[::-1]), str(tmp_path/'after.lance'))
    assert verify_unchanged(before, after, before.schema.names) == 2
    changed = lance.write_dataset(pa.Table.from_pylist([{**rows[0], 'metadata': [{'text': '错误'}]}, rows[1]]),
                                  str(tmp_path/'changed.lance'))
    with pytest.raises(ValueError, match='Metadata differs'):
        verify_unchanged(before, changed, before.schema.names)


def test_publish_preserves_duplicates_metadata_and_snapshots(tmp_path):
    blobs, audit = tmp_path/'blobs', tmp_path/'audit'
    rows = []
    for content, ext in [(b'first object', 'png'), (b'second object', 'gif')]:
        sha = hashlib.sha256(content).hexdigest()
        path = blobs/sha[:2]/f'{sha}.{ext}'
        path.parent.mkdir(parents=True)
        path.write_bytes(content)
        rows.append({'sha256': sha, 'ext': ext, 'size_bytes': len(content),
                     'refs': [{'license': '原许可', 'variant': True}], 'qids': ['Q2', 'Q1']})
    source, subset = tmp_path/'source.lance', tmp_path/'subset.lance'
    missing = {**rows[0], 'sha256': 'f'*64}
    lance.write_dataset(pa.Table.from_pylist([*rows, rows[0], missing]), str(source))
    schema = pa.schema([*lance.dataset(str(source)).schema, ('image_uri', pa.string()),
                        ('availability', pa.string()), ('storage_mode', pa.string())])
    lance.write_dataset(pa.Table.from_pylist([{**r, 'image_uri': None,
        'availability': 'metadata_only', 'storage_mode': 'pending_cos'} for r in rows], schema=schema), str(subset))
    proof = verify(subset, 1, blobs, audit, concurrency=2)
    assert proof['manifest']['rows'] == 2
    result = publish(source, 1, audit, root=tmp_path)
    assert result['state'] == 'complete' and result['source_matched_rows'] == 3
    assert result['source_rows'] == 4 and result['subset_rows'] == 2
    actual = lance.dataset(str(source)).to_table().to_pylist()
    assert actual[0]['image_uri'] == actual[2]['image_uri']
    assert actual[3]['image_uri'] is None
    assert 'image_uri' not in lance.dataset(str(source), version=1).schema.names
    assert all(r['image_uri'] is None for r in lance.dataset(str(subset), version=1).to_table().to_pylist())
    assert publish(source, 1, audit, root=tmp_path)['subset_head'] == result['subset_head']
    assert sum(1 for p in blobs.rglob('*') if p.is_file()) == 2


def test_source_size_conflict_aborts_before_either_table_changes(tmp_path):
    content = b'original'
    sha = hashlib.sha256(content).hexdigest()
    blobs = tmp_path/'blobs'
    path = blobs/sha[:2]/f'{sha}.png'
    path.parent.mkdir(parents=True)
    path.write_bytes(content)
    source, subset = tmp_path/'source.lance', tmp_path/'subset.lance'
    row = {'sha256': sha, 'ext': 'png', 'size_bytes': len(content)}
    lance.write_dataset(pa.Table.from_pylist([{**row, 'size_bytes': 1}]), str(source))
    schema = pa.schema([*lance.dataset(str(source)).schema, ('image_uri', pa.string()),
                        ('availability', pa.string()), ('storage_mode', pa.string())])
    lance.write_dataset(pa.Table.from_pylist([{**row, 'image_uri': None,
        'availability': 'metadata_only', 'storage_mode': 'pending_cos'}], schema=schema), str(subset))
    audit = tmp_path/'audit'
    verify(subset, 1, blobs, audit)
    with pytest.raises(ValueError, match='Source size mismatch'):
        publish(source, 1, audit, root=tmp_path)
    assert lance.dataset(str(source)).version == lance.dataset(str(subset)).version == 1
    assert 'image_uri' not in lance.dataset(str(source)).schema.names


def test_missing_corrupt_and_wrong_size_are_not_published(tmp_path):
    content = b'original'
    sha = hashlib.sha256(content).hexdigest()
    row = {'sha256': sha, 'ext': 'jpeg', 'size_bytes': len(content)}
    with pytest.raises(FileNotFoundError):
        verify_image(row, blobs=tmp_path)
    path = tmp_path/sha[:2]/f'{sha}.jpeg'
    path.parent.mkdir()
    path.write_bytes(b'corrupt!')
    with pytest.raises(ValueError, match='SHA256 mismatch'):
        verify_image(row, blobs=tmp_path)
    path.write_bytes(b'short')
    with pytest.raises(ValueError, match='size differs'):
        verify_image(row, blobs=tmp_path)
