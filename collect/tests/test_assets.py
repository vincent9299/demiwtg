"""Collection-owned object reads and publication; source snapshots and content integrity."""
import hashlib
import shutil
import lance
import pyarrow as pa
import pytest
from collect.assets import AssetReader, AssetMissing, AssetCorrupted, AssetError


def write(uri,values,mode='create'):
    schema=pa.schema([('sha256',pa.string()),lance.blob_field('data')])
    table=pa.Table.from_arrays([pa.array([hashlib.sha256(v).hexdigest() for v in values]),lance.blob_array(values)],schema=schema)
    return lance.write_dataset(table,str(uri),mode=mode)


def test_single_table_read_pin_and_deleted_row_ids(tmp_path):
    uri=tmp_path/'assets.lance';ds=write(uri,[b'a',b'b'])
    key=lambda b:hashlib.sha256(b).hexdigest()
    reader=AssetReader(uri)
    assert reader.read(key(b'b'))[0]==b'b'
    ds.delete("sha256 = '"+key(b'a')+"'")
    assert reader.resolve(key(b'a')).status=='missing'
    assert reader.read(key(b'b'))[0]==b'b'
    assert reader.read(key(b'a'),version=1)[0]==b'a'
    pinned=AssetReader(uri,version=1)
    write(uri,[b'c'],mode='append')
    assert pinned.resolve(key(b'c')).status=='missing'
    assert reader.read(key(b'c'))[1]['table']==str(uri)


def test_missing_null_duplicate_corrupt_are_distinct(tmp_path):
    uri=tmp_path/'assets.lance';key=hashlib.sha256(b'a').hexdigest();reader=AssetReader(uri)
    assert reader.resolve(key).status=='missing'
    ds=write(uri,[b'a'])
    schema=ds.schema
    def overwrite(values):
        table=pa.Table.from_arrays([pa.array([key]*len(values)),lance.blob_array(values)],schema=schema)
        lance.write_dataset(table,str(uri),mode='overwrite')
    overwrite([None]);assert reader.resolve(key).status=='missing'
    overwrite([b'wrong']);assert reader.resolve(key).status=='corrupt'
    with pytest.raises(AssetCorrupted):reader.read(key)
    overwrite([b'a',b'a']);assert reader.resolve(key).status=='read_error'
    with pytest.raises(AssetError):reader.read('invalid')


def test_published_object_survives_source_table_removal(tmp_path):
    uri = tmp_path / 'assets.lance'
    write(uri, [b'pixels'])
    key = hashlib.sha256(b'pixels').hexdigest()
    reader = AssetReader(uri, datasets_root=tmp_path, version=1)
    ref = reader.publish(key)
    assert reader.publish(key) == ref
    shutil.rmtree(uri)
    assert ref.read() == b'pixels'
    with pytest.raises(AssetMissing):
        reader.read(key)


def test_reader_accepts_explicit_independent_object_table(tmp_path):
    from demiflow.objects import LocalObjectStore
    ref = LocalObjectStore(tmp_path / 'objects').put(b'pixels')
    uri = tmp_path / 'assets.lance'
    lance.write_dataset(pa.Table.from_pylist([{'sha256': ref.sha256, 'image_uri': ref.uri}]), str(uri))
    reader = AssetReader(uri, datasets_root=tmp_path, version=1)
    assert reader.read_bytes(ref.sha256) == b'pixels'
    assert reader.publish(ref.sha256) == ref


def test_arbitrary_content_columns(tmp_path):
    uri=tmp_path/'audio.lance';key=hashlib.sha256(b'audio').hexdigest()
    table=pa.Table.from_arrays([pa.array([key]),lance.blob_array([b'audio'])],names=['content_hash','payload'])
    lance.write_dataset(table,str(uri))
    assert AssetReader(uri,id_column='content_hash',column='payload').read(key)[0]==b'audio'
