# -*- coding: utf-8 -*-
"""缺口分析（六步闭环第 1 步，纯确定性代码，无 LLM、无网络）。

本质：instances 全集 − images.jsonl 达标集（taxonomy 仅作聚簇元数据）。
- 达标判据：该实例在 images.jsonl 中的图数 >= min_images_per_instance（config.DEFAULTS）。
- 聚簇：按实例首个 taxonomy_path 的根下一级节点分组（缺口以簇为单位喂给 discover，
  而非单实例——同一簇的缺口通常能被同一批源覆盖）。
产物：state/collect/auto/gap_report.json（人工过目 + discover 输入）。

用法：python3 collect/cli.py gap [--threshold N] [--top N]
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
from collections import defaultdict
from typing import Dict, List

from . import auto_dir
from ..config import DEFAULTS


def _count_images(meta_dir: str) -> Dict[str, int]:
    """images.jsonl → {实例名: 图数}（一条记录多实例时各自计数）。"""
    counts: Dict[str, int] = defaultdict(int)
    path = os.path.join(meta_dir, "images.jsonl")
    if not os.path.exists(path):
        return counts
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            for t in rec.get("instances") or []:
                counts[t] += 1
    return counts


def _cluster_of(instance: dict) -> str:
    """首个 taxonomy_path 的根下一级节点；无路径归入 (未挂载)。"""
    paths = instance.get("taxonomy_paths") or []
    if not paths:
        return "(未挂载)"
    parts = [p.strip() for p in paths[0].split(" / ")]
    return parts[1] if len(parts) >= 2 else parts[0]


def build_report(taxonomy_path: str, meta_dir: str,
                 threshold: int, top: int) -> dict:
    with open(taxonomy_path, encoding="utf-8") as f:
        data = json.load(f)
    counts = _count_images(meta_dir)

    clusters: Dict[str, List[dict]] = defaultdict(list)
    achieved = 0
    for it in data.get("instances") or []:
        name = it.get("name")
        if not name:
            continue
        have = counts.get(name, 0)
        if have >= threshold:
            achieved += 1
            continue
        clusters[_cluster_of(it)].append({
            "name": name,
            "have": have,
            "need": threshold - have,
            # 探针查询词素材：discover/probe 用，不新增数据字段
            "query": (it.get("query") or [None])[0] or "",
            "aliases": list(it.get("aliases") or [])[:3],
        })

    out_clusters = []
    for cname, items in sorted(clusters.items(),
                               key=lambda kv: -sum(i["need"] for i in kv[1])):
        items.sort(key=lambda i: i["have"])
        out_clusters.append({
            "cluster": cname,
            "gap_instances": len(items),
            "total_need": sum(i["need"] for i in items),
            "top_starved": items[:top],
        })

    total_instances = achieved + sum(c["gap_instances"] for c in out_clusters)
    return {
        "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "threshold": threshold,
        "total_instances": total_instances,
        "achieved": achieved,
        "gap_instances": total_instances - achieved,
        "total_need": sum(c["total_need"] for c in out_clusters),
        "clusters": out_clusters,
    }


def main(argv=None):
    ap = argparse.ArgumentParser(prog="collect gap", description="缺口分析（纯代码）")
    ap.add_argument("--taxonomy", default="data/taxonomy/instances.json")
    ap.add_argument("--meta", default="data/dataset/meta")
    ap.add_argument("--threshold", type=int,
                    default=DEFAULTS["min_images_per_instance"],
                    help="达标图数阈值（默认取 config.min_images_per_instance）")
    ap.add_argument("--top", type=int, default=8,
                    help="每簇列出的最饥渴实例数（discover 上下文素材）")
    args = ap.parse_args(argv)

    report = build_report(args.taxonomy, args.meta, args.threshold, args.top)
    out = os.path.join(auto_dir(args.meta), "gap_report.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=1)

    print("缺口报告: %s" % out)
    print("实例总数 %d | 达标 %d | 缺口 %d（共缺 %d 张，阈值 %d）" % (
        report["total_instances"], report["achieved"],
        report["gap_instances"], report["total_need"], report["threshold"]))
    for c in report["clusters"][:10]:
        sample = "、".join(i["name"] for i in c["top_starved"][:3])
        print("  [%s] 缺口实例 %d / 缺 %d 张，如: %s" % (
            c["cluster"], c["gap_instances"], c["total_need"], sample))
    if len(report["clusters"]) > 10:
        print("  ...（其余 %d 簇见报告文件）" % (len(report["clusters"]) - 10))


if __name__ == "__main__":
    main()
