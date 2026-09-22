"""knowledge_base.approved.jsonl → releases 层无损镜像（111 概念交付）。

冻结交付按行 verbatim 入湖（raw_payload 全行），并附可查询计数字段；
文章／陈述的规范化拆表属 P4 知识重构，不在本批。来源只读。

用法（env 组合）：
  PYTHONPATH=<demiflow> python -m tools.lake_migration.ingest_knowledge
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

from project import resolve_root
from tools.lake_migration.write import write_table

_SOURCE = "curation/knowledge_base/knowledge_base.approved.jsonl"
_RELEASE_ID = "knowledge_approved_20260920"
_TABLE = f"releases/knowledge/{_RELEASE_ID}/articles.lance"


def _now_us() -> int:
    return int(time.time() * 1_000_000)


def iter_rows(repo_root: Path, migrated, stats):
    with (repo_root / _SOURCE).open(encoding="utf-8") as stream:
        for row_index, line in enumerate(stream):
            if not line.strip():
                stats["blank_lines"] += 1
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                stats["parse_errors"] += 1
                continue
            concept = record.get("concept")
            if not isinstance(concept, str) or not concept:
                stats["conceptless"] += 1
                continue
            stats["rows"] += 1
            yield {
                "release_id": _RELEASE_ID,
                "concept": concept,
                "case_id": record.get("case_id"),
                "status": record.get("status") or "unknown",
                "knowledge_count": len(record.get("knowledge") or []),
                "image_count": len(record.get("images") or []),
                "document_count": len(record.get("documents") or []),
                "source_file": _SOURCE,
                "source_row": row_index,
                "raw_payload": line.rstrip("\n"),
                "migrated_at_us": migrated,
            }


def run(repo_root: Path | None = None, datasets_root: Path | None = None) -> dict:
    repo = Path(repo_root) if repo_root else Path(__file__).resolve().parents[2]
    root = Path(datasets_root) if datasets_root else resolve_root()
    source = repo / _SOURCE
    with source.open("rb") as stream:
        source_sha = hashlib.file_digest(stream, "sha256").hexdigest()
    stats = {"rows": 0, "parse_errors": 0, "conceptless": 0, "blank_lines": 0}
    migrated = _now_us()
    # 行均 ~11MB：小批写入，避免整表进内存
    ref, rows, replayed = write_table(
        root, _TABLE, schema_name="knowledge_release",
        rows_factory=lambda: iter_rows(repo, migrated, stats),
        fingerprint=f"v1:knowledge:{_RELEASE_ID}:{source_sha}",
        max_rows_per_batch=8,
    )
    if replayed:
        for _ in iter_rows(repo, migrated, stats):
            pass
    report = {
        "release_id": _RELEASE_ID,
        "source": {"path": _SOURCE, "sha256": source_sha,
                   "size_bytes": source.stat().st_size},
        "stats": stats, "table": ref.to_dict(),
        "reconciled": rows == stats["rows"] and stats["parse_errors"] == 0
        and stats["conceptless"] == 0,
    }
    return report


def main():
    report = run()
    print(json.dumps(report, ensure_ascii=False, indent=1, sort_keys=True))
    target = (
        Path(__file__).parent / "reports/ingest_materials.json"
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(report, ensure_ascii=False, indent=1, sort_keys=True),
        encoding="utf-8",
    )
    print(f"report -> {target}")
    if not report["reconciled"]:
        raise SystemExit("knowledge ingest reconciliation FAILED")


if __name__ == "__main__":
    main()
