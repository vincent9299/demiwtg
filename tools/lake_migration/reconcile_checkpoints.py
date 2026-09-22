"""一次性对账：回执记录的旧绝对 URI vs 搬迁后当前位置（只读）。

R4 修复后重放绑定调用方解析的 URI，回执中的 uri 仅作溯源；本脚本逐份
核验数据根下所有 checkpoint 回执：按其固定版本打开当前位置的表，比对
schema_hash 与行数。不重写任何回执、不执行上游工厂、不产生写入。

用法（需 demiflow 源码仓 + env 组合 PYTHONPATH）：
  python -m tools.lake_migration.reconcile_checkpoints [--datasets-root <root>]
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from project import resolve_root


def reconcile(datasets_root=None) -> dict:
    from demiflow.lance.storage import open_lance_dataset, schema_hash

    root = Path(datasets_root) if datasets_root else resolve_root()
    results = {"checked": 0, "stale_uri": 0, "verified": 0, "failures": []}
    receipts = []
    for parent, directories, files in os.walk(root):
        if '_demiflow' in directories:
            receipts.extend((Path(parent)/'_demiflow').glob('*.lance/checkpoint.json'))
        directories[:] = [d for d in directories if not d.endswith('.lance') and d not in {'_demiflow','_staging'}]
    for sidecar in sorted(receipts):
        record = json.loads(sidecar.read_text())
        table_uri = str(sidecar.parent.parent.parent / sidecar.parent.name)
        results["checked"] += 1
        if record.get("uri") != table_uri:
            results["stale_uri"] += 1
        try:
            ds = open_lance_dataset(table_uri, record["committed_version"], ())
            ok = (schema_hash(ds.schema) == record["schema_hash"]
                  and ds.count_rows() == record["row_count"])
        except Exception as exc:  # noqa: BLE001 - report, never repair
            results["failures"].append({"table": table_uri, "error": str(exc)})
            continue
        if not ok:
            results["failures"].append({
                "table": table_uri,
                "error": "schema_hash or row_count drifted from receipt",
            })
        else:
            results["verified"] += 1
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--datasets-root", type=Path, default=None)
    args = parser.parse_args()
    print(json.dumps(reconcile(args.datasets_root), ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
