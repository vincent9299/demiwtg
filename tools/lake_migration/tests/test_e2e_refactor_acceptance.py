"""端到端小批验收（Review 建议第 5 步）：异步节点 → Lance checkpoint →
发布登记（含核验）→ 数据根搬迁 → 重放（不执行工厂）→ 下游按固定版本读取。

覆盖 R1（无主表不覆盖）、R4（重放绑定当前 URI）、R6（异步计划落盘）、
R7（发布可见边界）的组合行为。
"""
from demiflow.lance.checkpoint import checkpoint_sidecar_path
import json
import shutil
from pathlib import Path

import pytest

pytest.importorskip("lance")


def test_end_to_end_async_checkpoint_release_move_replay(tmp_path, monkeypatch):
    from demiflow.standalone import local_data

    root1 = tmp_path / "root1"
    root1.mkdir()
    monkeypatch.setenv("DEMIWTG_DATASETS_ROOT", str(root1))

    from tools.lake_migration.write import write_table
    from demiflow.lance.registry import ReleaseRegistry

    # 1) 异步算子链 + verbatim 入湖（R6 + write_table 集成）
    consumed = {"n": 0}

    def rows_factory():
        for i in range(25):
            consumed["n"] += 1
            yield {"asset_name": "e2e.jsonl", "record_key": str(i),
                   "payload": json.dumps({"i": i}), "source_file": "e2e",
                   "source_row": i, "raw_payload": json.dumps({"i": i}),
                   "migrated_at_us": 1}

    async def async_touch(row):
        import asyncio
        await asyncio.sleep(0)
        return row

    dataset = local_data().from_iter(rows_factory).map_async(async_touch)
    # write_table 吃零参工厂；用 checkpoint 语义直接驱动异步计划：
    from demiflow.lance.checkpoint import checkpoint_lance
    from tools.lake_migration.schemas import ALL_SCHEMAS
    relative = "releases/e2e/r1/materials.lance"
    uri = str(root1 / relative)
    checkpoint_lance(dataset, uri, schema=ALL_SCHEMAS["verbatim_records"],
                     fingerprint="e2e-r1")
    assert consumed["n"] == 25                      # 异步链真实执行

    # 2) 以回执固定版本登记引用并发布（R7 核验通过）
    from demiflow.lance.registry import Catalog
    catalog = Catalog(root1)
    record = json.loads(Path(checkpoint_sidecar_path(uri)).read_text())
    registered = catalog.get("releases/e2e/r1/materials",
                             relative_uri=relative,
                             lance_version=record["committed_version"])
    if registered is None:                          # checkpoint 未登记时补登记
        from demiflow.lance.refs import DatasetRef
        from demiflow.lance.storage import schema_hash
        catalog.register(DatasetRef(
            dataset_id="releases/e2e/r1/materials", relative_uri=relative,
            lance_version=record["committed_version"],
            schema_name="verbatim_records", schema_version="v1",
            schema_hash=schema_hash(
                __import__("lance").dataset(uri).schema),
            row_count=record["row_count"],
        ))
        registered = catalog.get("releases/e2e/r1/materials",
                                 relative_uri=relative,
                                 lance_version=record["committed_version"])
    registry = ReleaseRegistry(root1)
    registry.register("e2e-release", release_kind="e2e",
                      table_refs=[registered])
    row = registry.get("e2e-release")
    assert row is not None
    assert json.loads(row["validation"])["ref_checks"][0]["pinned_readback"]

    # 3) 搬迁数据根（表 + 回执 + 登记簿），重放不执行工厂（R4）
    root2 = tmp_path / "root2"
    shutil.copytree(root1, root2)
    consumed["n"] = 0

    def refusing_factory():
        raise AssertionError("replay must not execute the upstream factory")
        yield  # pragma: no cover

    replayed = checkpoint_lance(
        local_data().from_iter(refusing_factory),
        str(root2 / relative), schema=ALL_SCHEMAS["verbatim_records"],
        fingerprint="e2e-r1")
    rows = replayed.take_all()
    assert len(rows) == 25 and rows[0]["record_key"] == "0"

    # 4) 无主表拒覆盖（R1）：直接 lance 写的表不允许被 checkpoint 覆盖
    import lance as _lance
    plain_uri = str(root2 / "releases/e2e/plain.lance")
    _lance.write_dataset(
        __import__("pyarrow").Table.from_pylist(
            [{"asset_name": "x", "record_key": "0", "payload": "keep",
              "source_file": "f", "source_row": 0, "raw_payload": "keep",
              "migrated_at_us": 1}],
            schema=ALL_SCHEMAS["verbatim_records"]),
        plain_uri, mode="overwrite")
    with pytest.raises(Exception):
        checkpoint_lance(
            local_data().from_iter(refusing_factory), plain_uri,
            schema=ALL_SCHEMAS["verbatim_records"], fingerprint="evil")
    assert _lance.dataset(plain_uri).count_rows() == 1   # 原表保全
