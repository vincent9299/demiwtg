#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""migrate_concepts.py — instances.json → concepts.json 概念化迁移（架构决策 2026-09-07）。

契约（AGENTS.md 1.5，概念行四字段）：
  name     概念主键（全局唯一，收“词”不收“算式”）
  aliases  别名数组（身份字段：判重/英文源路由；无别名归一为 []）
  carriers 载体（"image+text" | "text"；图像采集线据此跳过 text-only 概念）
  taxonomy 分类树路径快照（' / ' 分隔、从域起算、树遍历序；仅供消歧与源路由，
           不承载知识——挂载真相在 taxonomy.json 节点 instances 名单）

退役字段去向（先导出不丢数据）：
  desc  → state/collect/concepts_docs_draft.jsonl 追加（kind=summary；
         草稿已有同名条目优先，精修版不被旧 desc 覆盖）
  query → state/collect/query_terms_cache.json（{name: [检索词]}，采集
         planner 冷启动先验）
  source → 行内退役，分布统计存 concepts.json meta.source_stats（明细溯 git 历史）

用法：
  python3 curation/migrate_concepts.py                      # 干跑：校验+预览
  python3 curation/migrate_concepts.py --apply              # 执行迁移（幂等可重跑）
  python3 curation/migrate_concepts.py --refresh-taxonomy   # 树变更后刷新快照
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
META = ROOT / "datasets" / "demiwtg" / "meta"
INSTANCES_F = META / "instances.json"
CONCEPTS_F = META / "concepts.json"
TAXONOMY_F = META / "taxonomy.json"
LOCK_F = META / ".meta.lock"
STATE_COLLECT = ROOT / "state" / "collect"
DOCS_DRAFT_F = STATE_COLLECT / "concepts_docs_draft.jsonl"
QUERY_CACHE_F = STATE_COLLECT / "query_terms_cache.json"

ROOT_PREFIX = "demiwtg / "


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def domain_mounts() -> dict[str, list[str]]:
    """taxonomy.json → {概念名: [域起算路径, ...]}（树遍历序去重）。

    根节点直挂（path=='demiwtg'）无法从域起算，跳过并告警——v3.1 底稿根节点
    为空名单，正常为 0。
    """
    tree = json.loads(TAXONOMY_F.read_text(encoding="utf-8"))["tree"]
    mounts: dict[str, list[str]] = {}
    root_hits = 0

    def walk(node: dict) -> None:
        nonlocal root_hits
        path = node.get("path", "")
        for nm in node.get("instances") or []:
            if isinstance(nm, dict):
                nm = nm.get("name")
            nm = str(nm).strip() if nm is not None else ""
            if not nm:
                continue
            if path.startswith(ROOT_PREFIX):
                p = path[len(ROOT_PREFIX):]
            elif path and path != "demiwtg":
                p = path
            else:
                root_hits += 1
                continue
            bucket = mounts.setdefault(nm, [])
            if p not in bucket:
                bucket.append(p)
        for ch in node.get("children") or []:
            walk(ch)

    walk(tree)
    if root_hits:
        print(f"[warn] {root_hits} 个根节点直挂无法从域起算，已跳过", file=sys.stderr)
    return mounts


def draft_names() -> set[str]:
    if not DOCS_DRAFT_F.exists():
        return set()
    return {json.loads(line)["name"]
            for line in DOCS_DRAFT_F.read_text(encoding="utf-8").splitlines()
            if line.strip()}


def build() -> tuple[dict, dict[str, list[str]], list[dict]]:
    """读 instances.json，产出 (concepts 文档, query 缓存, desc 导出行)。"""
    doc = json.loads(INSTANCES_F.read_text(encoding="utf-8"))
    rows = doc["instances"]
    names = [r["name"] for r in rows]
    names_set = set(names)
    if len(names) != len(names_set):
        dupes = [n for n, c in Counter(names).items() if c > 1]
        sys.exit(f"中止：name 唯一性破坏，重复 {len(dupes)} 个（样例 {dupes[:5]}）")

    mounts = domain_mounts()
    dangling = [n for n in mounts if n not in names_set]
    if dangling:
        sys.exit(f"中止：树引用了 {len(dangling)} 个不在册名字（样例 {dangling[:5]}）")

    concepts = [
        {
            "name": r["name"],
            "aliases": r.get("aliases") or [],
            "carriers": "image+text",
            "taxonomy": mounts.get(r["name"], []),
        }
        for r in rows
    ]
    query_cache = {r["name"]: r["query"] for r in rows if r.get("query")}
    have_draft = draft_names()
    desc_export = [
        {"name": r["name"], "kind": "summary", "body": r["desc"]}
        for r in rows
        if r.get("desc") and r["name"] not in have_draft
    ]
    src_stats = Counter(r.get("source", "<none>") for r in rows)
    mounted = sum(1 for c in concepts if c["taxonomy"])
    mount_dist = Counter(len(c["taxonomy"]) for c in concepts)
    out = {
        "schema_version": "2.0.0",
        "meta": {
            "generated_at": _now(),
            "vocabulary": "概念（concept，SKOS 语义：统一主键空间，不区分 class/individual）",
            "note": (
                "概念行四字段契约：name/aliases/carriers/taxonomy。taxonomy=分类树路径快照"
                "（' / ' 分隔，从域起算，树遍历序；真相在 taxonomy.json 节点 instances 名单，"
                "树变更后重跑 curation/migrate_concepts.py --refresh-taxonomy 刷新；多挂>3 处"
                "按树实况全量保留，≤3 为新概念策展纪律）。未挂载概念合法（taxonomy=[]）。"
                "2026-09-07 由 instances.json 概念化迁移：desc→state/collect/"
                "concepts_docs_draft.jsonl（docs 层草稿）；query→state/collect/"
                "query_terms_cache.json（planner 冷启动先验）；source 退役（分布见 "
                "source_stats，明细溯 git 历史）。carriers 存量默认 image+text。"
            ),
            "source": f"instances.json 概念化迁移 2026-09-07（{len(concepts)} 行）",
            "source_stats": dict(sorted(src_stats.items(), key=lambda kv: -kv[1])),
            "stats": {
                "concepts": len(concepts),
                "mounted": mounted,
                "mounts_per_concept": dict(sorted(mount_dist.items())),
            },
        },
        "concepts": concepts,
    }
    return out, query_cache, desc_export


def _write_atomic(path: Path, payload: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(payload, encoding="utf-8")
    os.replace(tmp, path)


def apply_migration(out: dict, query_cache: dict[str, list[str]],
                    desc_export: list[dict]) -> None:
    STATE_COLLECT.mkdir(parents=True, exist_ok=True)
    # 1) desc → docs 草稿追加（flock 互斥；重跑时 draft_names 去重，天然幂等）
    if desc_export:
        with DOCS_DRAFT_F.open("a", encoding="utf-8") as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            try:
                for rec in desc_export:
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            finally:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    # 2) query → 采集缓存（整文件覆写，幂等）
    _write_atomic(QUERY_CACHE_F, json.dumps(query_cache, ensure_ascii=False, indent=1))
    # 3) concepts.json 落盘 + instances.json 移除（meta 锁内原子）
    with LOCK_F.open("a") as lf:
        fcntl.flock(lf, fcntl.LOCK_EX)
        try:
            _write_atomic(CONCEPTS_F, json.dumps(out, ensure_ascii=False, indent=1))
            INSTANCES_F.unlink()
        finally:
            fcntl.flock(lf, fcntl.LOCK_UN)


def refresh_taxonomy() -> None:
    doc = json.loads(CONCEPTS_F.read_text(encoding="utf-8"))
    mounts = domain_mounts()
    changed = 0
    for c in doc["concepts"]:
        new = mounts.get(c["name"], [])
        if new != c["taxonomy"]:
            c["taxonomy"] = new
            changed += 1
    doc["meta"]["generated_at"] = _now()
    with LOCK_F.open("a") as lf:
        fcntl.flock(lf, fcntl.LOCK_EX)
        try:
            _write_atomic(CONCEPTS_F, json.dumps(doc, ensure_ascii=False, indent=1))
        finally:
            fcntl.flock(lf, fcntl.LOCK_UN)
    print(f"[refresh] taxonomy 快照刷新完成：{changed}/{len(doc['concepts'])} 行变化")


def main() -> None:
    ap = argparse.ArgumentParser(description="instances.json → concepts.json 概念化迁移")
    ap.add_argument("--apply", action="store_true", help="执行迁移（默认干跑）")
    ap.add_argument("--refresh-taxonomy", action="store_true",
                    help="树变更后刷新 concepts.json 的 taxonomy 快照")
    args = ap.parse_args()

    if args.refresh_taxonomy:
        if not CONCEPTS_F.exists():
            sys.exit("concepts.json 不存在；先跑 --apply 完成迁移")
        refresh_taxonomy()
        return

    if not INSTANCES_F.exists():
        sys.exit("instances.json 不存在（已迁移？）；刷新快照用 --refresh-taxonomy")
    if CONCEPTS_F.exists():
        sys.exit("concepts.json 与 instances.json 并存——状态异常，人工检查后再动")

    out, query_cache, desc_export = build()
    stats = out["meta"]["stats"]
    print(f"[plan] 概念行 {stats['concepts']}（挂载 {stats['mounted']}，"
          f"多挂分布 {stats['mounts_per_concept']}）")
    print(f"[plan] desc 导出 {len(desc_export)} 条 → {DOCS_DRAFT_F.relative_to(ROOT)}"
          f"（草稿既有条目优先跳过）")
    print(f"[plan] query 导出 {len(query_cache)} 条 → {QUERY_CACHE_F.relative_to(ROOT)}")
    print(f"[plan] source 退役，分布 {out['meta']['source_stats']}")
    if not args.apply:
        print("[plan] 干跑完成；加 --apply 执行")
        return
    apply_migration(out, query_cache, desc_export)
    print(f"[done] {CONCEPTS_F.relative_to(ROOT)} 写入完成，instances.json 已移除；"
          f"消费端同步见 AGENTS.md 1.5")


if __name__ == "__main__":
    main()
