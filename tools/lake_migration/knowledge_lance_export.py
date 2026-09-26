"""知识 run 产物 → Lance 权威入口（P3 知识线首个接线）。

把已完成知识 run 的 knowledge_base.jsonl 按行 verbatim 写为 runs 层
Lance 表并登记 catalog。零模型调用、业务字段不改写；run 全状态
（reviewed/failed/insufficient_materials…）保留，不冒充冻结交付
（冻结交付仍走 releases 层，如 knowledge_approved_20260920）。

CLI：python -m tools.lake_migration.knowledge_lance_export --kb <knowledge_base.jsonl>
     --run-id <id> [--datasets-root <root>]
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


def export_knowledge_run(kb_jsonl, run_id: str, *, datasets_root=None) -> DatasetRef:
    kb_path = Path(kb_jsonl)
    if not kb_path.is_file():
        raise FileNotFoundError(kb_path)
    root = Path(datasets_root) if datasets_root else resolve_root()
    migrated = int(time.time() * 1_000_000)
    source_digest = hashlib.sha256(kb_path.read_bytes()).hexdigest()[:32]

    def iter_rows():
        stats = {"rows": 0, "blank": 0, "parse_errors": 0}
        with kb_path.open(encoding="utf-8") as stream:
            for row_index, line in enumerate(stream):
                if not line.strip():
                    stats["blank"] += 1
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    stats["parse_errors"] += 1
                    continue
                concept = record.get("concept")
                if not isinstance(concept, str) or not concept:
                    stats["parse_errors"] += 1
                    continue
                stats["rows"] += 1
                yield {
                    "run_id": run_id,
                    "concept": concept,
                    "case_id": record.get("case_id"),
                    "status": record.get("status") or "unknown",
                    "status_reason": record.get("status_reason"),
                    "knowledge_count": len(record.get("knowledge") or []),
                    "image_count": len(record.get("images") or []),
                    "document_count": len(record.get("documents") or []),
                    "source_file": str(kb_path),
                    "source_row": row_index,
                    "raw_payload": line.rstrip("\n"),
                    "migrated_at_us": migrated,
                }
        print(json.dumps(stats, ensure_ascii=False), flush=True)

    relative = f"demiwtg/preparation/datasets/knowledge_base__{run_id}.lance"
    ref, rows, replayed = write_table(
        root, relative, schema_name="knowledge_run", schema=curation_schemas.KNOWLEDGE_RUN, schema_version=curation_schemas.SCHEMA_VERSION,
        rows_factory=iter_rows,
        fingerprint=f"knowledge-run-v1:{run_id}:{source_digest}",
    )
    print(json.dumps({"run_id": run_id, "rows": rows, "replayed": replayed,
                      "ref": ref.to_dict()}, ensure_ascii=False))
    return ref


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kb", type=Path, required=True,
                        help="已完成 run 的 knowledge_base.jsonl")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--datasets-root", type=Path, default=None)
    args = parser.parse_args()
    export_knowledge_run(args.kb, args.run_id, datasets_root=args.datasets_root)


if __name__ == "__main__":
    main()
