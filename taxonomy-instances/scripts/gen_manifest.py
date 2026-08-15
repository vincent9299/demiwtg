#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""汇总各次运行的成功落盘清单，生成带标签/来源/尺寸/许可证的单一 CSV。

单流模式：授权源与未授权源统一写入 downloads_success.jsonl，本脚本把它们
（以及历史遗留的 downloads_unauthorized.jsonl，若存在）合并为一张清单：
  data/image_manifest.csv   （全量，含 source_authorized 列，可按其切分纯 CC 子集）

每张图都保留 source / license / source_authorized 字段，下游可自由过滤。

按 (tag, sha256) 去重；同一张图被多个标签共用时分别保留其标签归属。

用法:
  python3 scripts/gen_manifest.py
  python3 scripts/gen_manifest.py --runs data/multimodal_pilot
"""

import argparse
import csv
import glob
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _default_runs():
    runs = []
    for d in sorted(glob.glob(os.path.join(ROOT, "data", "multimodal*"))):
        if os.path.isdir(d) and (
            os.path.exists(os.path.join(d, "downloads_success.jsonl"))
            or os.path.exists(os.path.join(d, "downloads_unauthorized.jsonl"))
        ):
            runs.append(d)
    return runs


def _tier_name(t):
    if t is None:
        return "original"
    if t == 0:
        return "rmax"
    return f"r{t}"


COLUMNS = [
    "tag", "leaf", "query", "query_lang",
    "source", "source_kind", "source_authorized",
    "selected_tier", "tier_file",
    "source_rank", "source_score",
    "width", "height", "orig_width", "orig_height",
    "mime", "size_bytes", "license", "author", "sha256", "local_path",
]


def _row(d):
    return {
        "tag": d.get("tag", ""),
        "leaf": d.get("tag", "").rsplit(" / ", 1)[-1],
        "query": d.get("query", ""),
        "query_lang": d.get("query_lang") or "",
        "source": d.get("source", ""),
        "source_kind": d.get("source_kind") or "",
        "source_authorized": d.get("source_authorized", True),
        "selected_tier": d.get("selected_tier"),
        "tier_file": _tier_name(d.get("selected_tier")),
        "source_rank": d.get("source_rank"),
        "source_score": d.get("source_score"),
        "width": d.get("actual_width"),
        "height": d.get("actual_height"),
        "orig_width": d.get("orig_width"),
        "orig_height": d.get("orig_height"),
        "mime": d.get("actual_mime") or d.get("declared_mime"),
        "size_bytes": d.get("actual_size"),
        "license": d.get("license_raw") or "",
        "author": d.get("author") or "",
        "sha256": d.get("sha256") or "",
        "local_path": d.get("local_path") or "",
    }


def _read(path):
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                yield json.loads(line)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", nargs="*", default=None,
                    help="运行目录列表（默认：扫描 data/multimodal*）")
    ap.add_argument("--out-dir", default=os.path.join(ROOT, "data"))
    args = ap.parse_args()

    runs = args.runs or _default_runs()
    if not runs:
        print("未发现任何运行目录（需含 downloads_success.jsonl / downloads_unauthorized.jsonl）")
        return

    rows = []
    seen = set()
    n_cc = n_unauth = 0
    for run in runs:
        # 单流：当前运行只有 downloads_success.jsonl（已含授权+未授权）。
        # 历史运行可能还有 downloads_unauthorized.jsonl，一并合并。
        for fname in ("downloads_success.jsonl", "downloads_unauthorized.jsonl"):
            for d in _read(os.path.join(run, fname)):
                key = (d.get("tag", ""), d.get("sha256"))
                if key in seen:
                    continue
                seen.add(key)
                r = _row(d)
                if r["source_authorized"]:
                    n_cc += 1
                else:
                    n_unauth += 1
                rows.append(r)

    rows.sort(key=lambda r: (r["tag"], str(r["selected_tier"])))

    out = os.path.join(args.out_dir, "image_manifest.csv")
    with open(out, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(rows)

    print(f"运行目录: {len(runs)} | 合并清单 {len(rows)} 行 "
          f"(授权 {n_cc} + 未授权 {n_unauth}) -> {out}")


if __name__ == "__main__":
    main()
