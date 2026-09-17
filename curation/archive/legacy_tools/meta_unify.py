#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""meta_unify.py — meta 真相区统一收口（2026-09-05：EN 并入中文湖 + images.jsonl 并入 instance_images.jsonl）。

  images [--apply]   images.jsonl 逐实例炸开并入 instance_images.jsonl（本次不打标：identity/focus/quality=null
                     待 annotate_backfill 补；v1 采集字段 tiers/credit/source_rank/asset_ids/
                     source_kind/source_authorized/source_score 照 migrate.py 先例丢弃）
  en [--apply]       metadata_en.jsonl 并入 instance_images.jsonl：EN 实例名按 en_entity_merge 对齐结果归一
                     （matched/variant→中文正名；new→保留 EN 名，apply 已入库挂树）；(sha256, instance) 去重
  report             只打印两边可并入统计，不写任何文件

报告落 state/taxonomy/unify_report.json；retire（移出 meta/）前物理备份 state/taxonomy/backup_pre_unify/。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
META = ROOT / "datasets/demiwtg/meta"
REPORT = ROOT / "state/taxonomy/unify_report.json"

# migrate.py 先例：并入时丢弃的 v1 采集字段（保留全部核心溯源：source/license/author/urls/path）
V1_DROP = {"tiers", "source_rank", "source_score", "asset_ids", "credit",
           "source_kind", "source_authorized"}


def load_keys():
    """instance_images.jsonl 现有 (sha256, instance) 键集 + sha 集（一次流式扫描）。"""
    keys, shas = set(), set()
    with open(META / "instance_images.jsonl", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            shas.add(r["sha256"])
            for i in r.get("instances") or []:
                keys.add((r["sha256"], i))
    return keys, shas


def explode(row: dict, rewrite=None) -> list[dict]:
    """一行 → 逐实例行列表（空实例行原样保留为无主资产）。rewrite(name)->canon 归一实例名。"""
    def canon_of(n):
        return rewrite(n) if rewrite else n
    insts = row.get("instances") or []
    queries = row.get("queries") or {}
    langs = row.get("query_langs") or {}
    if not insts:
        base = {k: v for k, v in row.items() if k not in V1_DROP}
        for k in ("identity", "focus", "quality"):
            base.setdefault(k, None)
        return [base]
    out, seen = [], set()
    for inst in insts:
        canon = canon_of(inst)
        if canon in seen:
            continue
        seen.add(canon)
        new = {k: v for k, v in row.items() if k not in V1_DROP}
        new["instances"] = [canon]
        q = {s: queries[s] for s in insts if canon_of(s) == canon and s in queries}
        lg = {s: langs[s] for s in insts if canon_of(s) == canon and s in langs}
        if q:
            new["queries"] = q
        else:
            new.pop("queries", None)
        if lg:
            new["query_langs"] = lg
        else:
            new.pop("query_langs", None)
        for k in ("identity", "focus", "quality"):
            new.setdefault(k, None)
        out.append(new)
    return out


def _append(rows: list[dict], op: str, extra: dict):
    with open(META / "instance_images.jsonl", "a", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps({
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "op": op,
        "rows_appended": len(rows), **extra,
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"已追加 {len(rows):,} 行 → instance_images.jsonl")


def _validate_registry(rows: list[dict]):
    reg = {r["name"] for r in json.loads(
        (META / "instances.json").read_text(encoding="utf-8"))["instances"]}
    bad = {r["instances"][0] for r in rows
           if r.get("instances") and r["instances"][0] not in reg}
    if bad:
        sys.exit(f"中止：{len(bad)} 个实例名不在 instances.json 在册集（样例 {sorted(bad)[:5]}）")


def cmd_images(args):
    t0 = time.time()
    keys, shas = load_keys()
    print(f"metadata 现有 {len(shas):,} sha / {len(keys):,} (sha,instance) 键（{time.time()-t0:.0f}s）")
    rows_out, skip_pair = [], 0
    with open(META / "images.jsonl", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            for new in explode(r):
                insts = new.get("instances") or []
                k = (new["sha256"], insts[0] if insts else None)
                if k[1] is not None and k in keys:
                    skip_pair += 1
                    continue
                if k[1] is not None:
                    keys.add(k)
                rows_out.append(new)
    _validate_registry(rows_out)
    n_new_sha = len({r["sha256"] for r in rows_out} - shas)
    print(f"images→metadata：新增 {len(rows_out):,} 行 | 跳过重复 {skip_pair:,} | 涉及新进 sha {n_new_sha:,}")
    if not args.apply:
        print("（dry-run，未写）")
        return
    _append(rows_out, "images", {"pairs_skipped": skip_pair})


def _en2zh_map():
    """对齐检查点 → EN→中文正名 映射（new 实体不在映射里，保留原名）。"""
    sys.path.insert(0, str(ROOT))
    from curation.en_entity_merge import merge_ops
    alias_ops, _new, _t0 = merge_ops()
    en2zh = {}
    for zh, ens in alias_ops.items():
        for e in ens:
            en2zh.setdefault(e, zh)
    return en2zh


def cmd_en(args):
    t0 = time.time()
    en2zh = _en2zh_map()
    print(f"对齐映射 {len(en2zh):,} EN 名 → 中文正名（{time.time()-t0:.0f}s）")
    keys, shas = load_keys()
    rows_out, skip_pair, row_all_dup = [], 0, 0
    name_stats = Counter()
    with open(META / "metadata_en.jsonl", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            def rewrite(n):
                m = en2zh.get(n)
                name_stats["mapped"] += 1 if m else 0
                name_stats["kept_en"] += 0 if m else 1
                return m or n
            added = False
            for new in explode(r, rewrite=rewrite):
                insts = new.get("instances") or []
                k = (new["sha256"], insts[0] if insts else None)
                if k[1] is not None and k in keys:
                    skip_pair += 1
                    continue
                if k[1] is not None:
                    keys.add(k)
                rows_out.append(new)
                added = True
            if not added and (r.get("instances") or []):
                row_all_dup += 1
    _validate_registry(rows_out)
    print(f"en→metadata：新增 {len(rows_out):,} 行 | 跳过重复 {skip_pair:,} | 整行全重复 {row_all_dup:,} | "
          f"实例名出现 mapped {name_stats['mapped']:,} / kept {name_stats['kept_en']:,}")
    if not args.apply:
        print("（dry-run，未写）")
        return
    _append(rows_out, "en", {"pairs_skipped": skip_pair, "rows_all_dup": row_all_dup})


def cmd_report(_args):
    keys, shas = load_keys()
    print(f"instance_images.jsonl：{len(shas):,} sha / {len(keys):,} (sha,instance)")
    for name in ("images.jsonl", "metadata_en.jsonl"):
        n = dup = empty = 0
        with open(META / name, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                r = json.loads(line)
                n += 1
                if not (r.get("instances") or []):
                    empty += 1
                    continue
                for i in r["instances"]:
                    if (r["sha256"], i) in keys:
                        dup += 1
        print(f"{name}：{n:,} 行 | 空 instances {empty:,} | 与 metadata 重复 (sha,instance) {dup:,}")


def main():
    sys.exit("已收官（2026-09-05/06 执行完毕，历史一次性脚本）：EN 并入与 v1 images.jsonl "
             "退役均已落库，清单现为 meta/images.jsonl（2026-09-08 由 "
             "instance_images.jsonl 更名）；本脚本留作迁移记录，勿再运行。")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    ip = sub.add_parser("images"); ip.add_argument("--apply", action="store_true")
    ep = sub.add_parser("en"); ep.add_argument("--apply", action="store_true")
    sub.add_parser("report")
    args = ap.parse_args()
    dict(images=cmd_images, en=cmd_en, report=cmd_report)[args.cmd](args)


if __name__ == "__main__":
    main()
