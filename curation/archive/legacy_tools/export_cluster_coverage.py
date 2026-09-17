#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""export_cluster_coverage.py — 湖侧覆盖导出（demiwtg-data 集群，2026-09-08）。

背景：meta/images.jsonl 已恢复全量 preloss 清单（2,849,013 行）；其中 blob 实存
152,081 个。集群 demiwtg-data 清单契约字段是 `concepts`（湖侧沿袭 `instances`）。

产物（state/collect/demiwtg_data_sync/）：
  lake_coverage_for_cluster.jsonl  blob 实存行（集群 schema）——merge_lake_coverage.py
                                  合并进集群本地清单后，flow 的 --skip-covered /
                                  配额 / (sha,concept) 去重按湖内覆盖工作
  lake_concept_coverage.json       概念覆盖摘要（人读/巡检）

补采工作清单（缺 blob 行的下载任务）不在本脚本——极简版（gzip 157MB，
{c,u,s,e,src} 五键）见 curation/export_refetch_min.py。

用法：python3 curation/export_cluster_coverage.py
"""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

from blob_presence import blob_shas

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "datasets" / "demiwtg" / "meta" / "images.jsonl"
OUT_DIR = ROOT / "state" / "collect" / "demiwtg_data_sync"
OUT_COVERAGE = OUT_DIR / "lake_coverage_for_cluster.jsonl"
OUT_SUMMARY = OUT_DIR / "lake_concept_coverage.json"

# demiwtg-data operators/annotate.py RECORD_FIELDS（集群清单契约面）
CLUSTER_FIELDS = (
    "sha256", "ext", "source", "license", "author",
    "width", "height", "orig_width", "orig_height",
    "size_bytes", "mime", "concepts", "queries", "query_langs",
    "content_url", "landing_url", "fetched_at", "path",
    "kb_match", "richness", "caption", "identity", "focus", "quality",
)


def convert(r: dict) -> dict:
    rec = {k: r[k] for k in CLUSTER_FIELDS if k != "concepts" and k in r}
    rec["concepts"] = list(r.get("instances") or [])
    return rec


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    have = blob_shas()
    print(f"[blobs] 实存 {len(have):,} sha（行过滤基准）")

    n_cov = 0
    cov: dict[str, dict] = defaultdict(lambda: {"rows": 0, "qualified": 0})
    with OUT_COVERAGE.open("w", encoding="utf-8") as f_cov:
        for line in MANIFEST.open(encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            if r.get("sha256") not in have:
                continue
            rec = convert(r)
            gated = (r.get("quality") or 0) >= 8 and r.get("identity") is True
            n_cov += 1
            for c in rec["concepts"]:
                cov[c]["rows"] += 1
                cov[c]["qualified"] += gated
            f_cov.write(json.dumps(rec, ensure_ascii=False) + "\n")
    OUT_SUMMARY.write_text(
        json.dumps({k: dict(v) for k, v in sorted(cov.items())},
                   ensure_ascii=False), encoding="utf-8")

    print(f"[coverage] blob 实存行 {n_cov:,} → {OUT_COVERAGE.name}"
          f"（{OUT_COVERAGE.stat().st_size/1e6:.0f} MB；"
          f"概念摘要 {len(cov):,}，质量门合格概念 "
          f"{sum(1 for v in cov.values() if v['qualified']):,}）")


if __name__ == "__main__":
    main()
