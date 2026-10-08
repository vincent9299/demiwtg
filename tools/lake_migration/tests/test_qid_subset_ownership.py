import json
import lance
import pyarrow as pa
import pytest
from demiflow.lance.refs import DatasetRef
from demiflow.lance.storage import schema_hash
from tools.lake_migration.qid_subset_ownership import PAIRS,relocate


def test_relocation_keeps_all_versions_and_old_fixed_refs(tmp_path):
    refs=[]
    for old,new in PAIRS.items():
        first=lance.write_dataset(pa.table({'id':[1],'value':['old']}),str(tmp_path/old))
        refs.append(DatasetRef(old,old,1,'test','v1',schema_hash(first.schema),1))
        lance.write_dataset(pa.table({'id':[2],'value':['new']}),str(tmp_path/old),mode='overwrite')
    assert relocate(tmp_path)['complete']
    assert relocate(tmp_path)['complete']
    for ref in refs:
        assert ref.open(tmp_path).to_table()['value'].to_pylist()==['old']
    for old,new in PAIRS.items():
        assert not (tmp_path/old).exists()
        assert lance.dataset(str(tmp_path/new),version=2).to_table()['value'].to_pylist()==['new']
        assert [v['version'] for v in lance.dataset(str(tmp_path/new)).versions()]==[1,2]


def test_conflicting_mapping_rejects_before_moving(tmp_path):
    old,new=next(iter(PAIRS.items()))
    lance.write_dataset(pa.table({'id':[1]}),str(tmp_path/old))
    manifest=tmp_path/'_demiflow/lance_locations.json'
    manifest.parent.mkdir()
    manifest.write_text(json.dumps({'version':1,'tables':{old:'wrong.lance'}}))
    with pytest.raises(ValueError,match='conflicts'): relocate(tmp_path)
    assert (tmp_path/old).exists() and not (tmp_path/new).exists()
