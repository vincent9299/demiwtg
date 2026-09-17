#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""harvest_blobs.py — 从本地残留图片副本收割回灌 blobs（图片还原补充，2026-09-08）。

背景：2026-09-06 图片全量丢失；2026-09-08 从 Sep 5 tar 备份还原了 blobs 的
前 19 个 sha 前缀目录（150,633 个）。评测/实验目录里留存的样本图（benchmark/
各赛道 data/、_staging/benchmark/、bagel/results/ 等）是当年从湖里**拷出的
副本**——内容寻址天然可回灌：算内容 sha256，命中 preloss 清单 sha 集者按
blobs/<aa>/<sha>.<ext> 归位（AGENTS 2.1：只增不删、文件名=内容哈希）。

只收割 preloss 清单在册 sha——生成图（wkbench 产物等非湖成员）不进 blobs。
收割后重跑 curation/replay_manifest.py --apply 刷新清单。

用法：
  python3 curation/harvest_blobs.py --dry-run     # 只统计可收割量
  python3 curation/harvest_blobs.py               # 收割回灌
  python3 curation/harvest_blobs.py --extra /path # 追加扫描目录（可多次）
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BLOBS = ROOT / "datasets" / "demiwtg" / "blobs"
PRELOSS = ROOT / "state" / "collect" / "instance_images.preloss_20260906.jsonl"

IMG_EXT = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tiff", ".bin"}

DEFAULT_SCAN_DIRS = [
    ROOT / "benchmark",
    ROOT.parent / "_staging" / "benchmark",
    ROOT / "bagel" / "results",
]

# 跳过：模型/环境重物与明确生成目录（preloss sha 过滤是真闸门，这里只省扫描）
SKIP_PARTS = {".venv", "models", "node_modules", "__pycache__",
              "VLMEvalKit", "qib_official", "gen_imgs"}


def preloss_shas() -> set[str]:
    out: set[str] = set()
    with PRELOSS.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.add(json.loads(line)["sha256"])
            except (json.JSONDecodeError, KeyError):
                continue
    return out


def iter_images(roots: list[Path]):
    for root in roots:
        if not root.exists():
            print(f"[skip] 扫描根不存在：{root}")
            continue
        for p in root.rglob("*"):
            if not p.is_file() or p.suffix.lower() not in IMG_EXT:
                continue
            if SKIP_PARTS & set(p.parts):
                continue
            yield p


def main() -> None:
    ap = argparse.ArgumentParser(description="本地残留图片副本收割回灌 blobs")
    ap.add_argument("--dry-run", action="store_true", help="只统计，不写 blobs")
    ap.add_argument("--extra", type=Path, action="append", default=[],
                    help="追加扫描目录（可多次）")
    args = ap.parse_args()

    shas = preloss_shas()
    print(f"[preloss] 在册 sha {len(shas):,} 个（收割目标集）")
    roots = DEFAULT_SCAN_DIRS + list(args.extra)
    print(f"[scan] 根：{[str(r) for r in roots]}")

    n_scanned = n_hit = n_skip_existing = 0
    for p in iter_images(roots):
        n_scanned += 1
        h = hashlib.sha256()
        with p.open("rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        sha = h.hexdigest()
        if sha not in shas:
            continue
        dst = BLOBS / sha[:2] / f"{sha}{p.suffix.lower()}"
        if dst.exists():
            n_skip_existing += 1
            continue
        n_hit += 1
        if not args.dry_run:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, dst)
        if n_hit % 2000 == 0:
            print(f"  … 收割 {n_hit:,} 张", flush=True)
    print(f"[done] 扫描 {n_scanned:,} 张图；命中 preloss 在册 "
          f"{n_hit:,} 张（已存在跳过 {n_skip_existing:,}）"
          + ("；dry-run 未写盘" if args.dry_run else "；已回灌 blobs"))
    if n_hit and not args.dry_run:
        print("[next] 重跑 curation/replay_manifest.py --apply 刷新清单")


if __name__ == "__main__":
    main()
