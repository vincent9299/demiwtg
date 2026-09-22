"""出题／训练／评测 run 产物 → Lance 权威入口（P3 三线接线，通用 verbatim）。

把已完成 run 的指定 JSONL 产物按行原样写入 runs 层并登记 catalog。
零模型调用、字段不改写；行身份 = 源文件行号。与 knowledge_lance_export
（知识线，带结构化计数字段）同族：本模块走 verbatim_records schema，
面向字段形态随版本变化的产物；结构化拆表随各线节点级接入细化。

CLI：python -m tools.lake_migration.run_lance_export --line benchmark|training|evaluation
     --artifact <file.jsonl> --run-id <id> [--datasets-root <root>]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import time
from pathlib import Path

from project import resolve_root
from demiflow.lance.registry import write_registered_table as write_table
from tools.lake_migration import legacy_schemas as curation_schemas
from collect import schemas as collect_schemas
from demiflow.lance.refs import DatasetRef

LINES = ("benchmark", "training", "evaluation")


def export_run_artifact(line: str, artifact, run_id: str, *, datasets_root=None) -> DatasetRef:
    if line not in LINES:
        raise ValueError(f"line must be one of {LINES}")
    artifact_path = Path(artifact)
    if not artifact_path.is_file():
        raise FileNotFoundError(artifact_path)
    root = Path(datasets_root) if datasets_root else resolve_root()
    migrated = int(time.time() * 1_000_000)
    source_digest = hashlib.sha256(artifact_path.read_bytes()).hexdigest()[:32]
    asset_name = artifact_path.name

    def iter_rows():
        stats = {"rows": 0, "blank": 0}
        with artifact_path.open(encoding="utf-8") as stream:
            for row_index, line_text in enumerate(stream):
                if not line_text.strip():
                    stats["blank"] += 1
                    continue
                stats["rows"] += 1
                yield {
                    "asset_name": asset_name,
                    "record_key": str(row_index),
                    "payload": line_text.rstrip("\n"),
                    "source_file": str(artifact_path),
                    "source_row": row_index,
                    "raw_payload": line_text.rstrip("\n"),
                    "migrated_at_us": migrated,
                }
        print(json.dumps(stats, ensure_ascii=False), flush=True)

    relative = f"runs/{line}/{run_id}/{artifact_path.stem}.lance"
    ref, rows, replayed = write_table(
        root, relative, schema_name="verbatim_records", schema=collect_schemas.VERBATIM_RECORDS, schema_version=collect_schemas.SCHEMA_VERSION,
        rows_factory=iter_rows,
        fingerprint=f"run-artifact-v1:{line}:{run_id}:{asset_name}:{source_digest}",
    )
    print(json.dumps({"line": line, "run_id": run_id, "artifact": asset_name,
                      "rows": rows, "replayed": replayed, "ref": ref.to_dict()},
                     ensure_ascii=False))
    return ref


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--line", required=True, choices=LINES)
    parser.add_argument("--artifact", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--datasets-root", type=Path, default=None)
    args = parser.parse_args()
    export_run_artifact(args.line, args.artifact, args.run_id,
                        datasets_root=args.datasets_root)


if __name__ == "__main__":
    main()
