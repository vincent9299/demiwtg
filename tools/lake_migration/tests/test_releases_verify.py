"""R7 验收：发布登记的可见边界——不存在表／不存在版本／矛盾元数据必须
失败且无可见发布；合法引用登记成功并冻结核验结果。"""
import pytest

pytest.importorskip("lance")


@pytest.fixture()
def root(tmp_path, monkeypatch):
    monkeypatch.setenv("DEMIWTG_DATASETS_ROOT", str(tmp_path))
    from project import resolve_root
    return resolve_root()


def _make_table(root, relative="releases/t/x.lance", rows=2):
    from tools.lake_migration.write import write_table
    schema = "verbatim_records"

    def factory():
        return iter({"asset_name": "a", "record_key": str(i),
                     "payload": "p", "source_file": "f", "source_row": i,
                     "raw_payload": "p", "migrated_at_us": 1}
                    for i in range(rows))

    ref, count, _ = write_table(root, relative, schema_name=schema,
                                rows_factory=factory,
                                fingerprint=f"r7:{relative}:{rows}")
    assert count == rows
    return ref


def test_register_rejects_absent_table(root):
    from demiflow.lance.refs import DatasetRef
    from demiflow.lance.registry import ReleaseRegistry, ReleaseConflict

    ref = DatasetRef(dataset_id="releases/absent", relative_uri="releases/absent.lance",
                     lance_version=9, schema_name="verbatim_records",
                     schema_version="v1", schema_hash="sha256:x", row_count=77)
    with pytest.raises(ReleaseConflict):
        ReleaseRegistry(root).register("bad-release", release_kind="test",
                                       table_refs=[ref])
    assert ReleaseRegistry(root).get("bad-release") is None   # 无可见发布


def test_register_rejects_wrong_version_and_contradictions(root):
    from demiflow.lance.registry import ReleaseRegistry, ReleaseConflict

    good = _make_table(root)

    wrong_version = good.__class__(**{**good.to_dict(), "lance_version": 99})
    with pytest.raises(ReleaseConflict):
        ReleaseRegistry(root).register("bad-v", release_kind="test",
                                       table_refs=[wrong_version])

    wrong_rows = good.__class__(**{**good.to_dict(), "row_count": good.row_count + 5})
    with pytest.raises(ReleaseConflict):
        ReleaseRegistry(root).register("bad-rows", release_kind="test",
                                       table_refs=[wrong_rows])

    registry = ReleaseRegistry(root)
    assert registry.get("bad-v") is None and registry.get("bad-rows") is None


def test_register_freezes_verification_results(root):
    import json

    from demiflow.lance.registry import ReleaseRegistry

    ref = _make_table(root)
    registry = ReleaseRegistry(root)
    registry.register("good-release", release_kind="test", table_refs=[ref],
                      pipeline_run="run-x")
    row = registry.get("good-release")
    assert row is not None and row["status"] == "registered"
    validation = json.loads(row["validation"])
    check = validation["ref_checks"][0]
    assert check["dataset_id"] == ref.dataset_id
    assert check["lance_version"] == ref.lance_version
    assert check["row_count"] == ref.row_count
    assert check["pinned_readback"] is True
    # 幂等重登记
    registry.register("good-release", release_kind="test", table_refs=[ref], pipeline_run="run-x")
    assert len([r for r in registry.rows() if r["release_id"] == "good-release"]) == 1
