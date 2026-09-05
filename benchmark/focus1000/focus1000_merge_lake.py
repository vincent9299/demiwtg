#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""focus1000 生成图并入数据湖：blobs 内容寻址落盘 + metadata.jsonl 追加。

契约（AGENTS.md 2.1/2.2）：
- blobs/<aa>/<sha256>.<ext>，先算内容哈希，同名已存在则跳过（只增不删）；
- 清单走 v2 拍板：只写 metadata.jsonl（legacy images.jsonl 不碰，避免双写漂移）；
- 生成图行保持既有 schema 子集：source=qwen-image-3.0-pro 标识来源，
  无打标字段（caption/quality 任务已取消），landing/content_url 置 null；
- 跨进程写锁用 .meta.lock（flock），写前扫描既有 sha 集合防重复。

用法：python3 focus1000_merge_lake.py [--dry-run]
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import shutil
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = Path(__file__).resolve().parent / "data"
RESULTS_F = DATA_DIR / "gen_results.jsonl"
DATASET = REPO_ROOT / "datasets" / "demiwtg"
BLOBS = DATASET / "blobs"
META = DATASET / "meta"
MANIFEST_F = META / "metadata.jsonl"
LOCK_F = META / ".meta.lock"

GEN_SOURCE = "qwen-image-3.0-pro"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    rows = [json.loads(l) for l in RESULTS_F.read_text().splitlines() if l.strip()]
    ok, seen = [], set()
    for r in rows:
        if r.get("error") or r["sha256"] in seen:
            continue
        seen.add(r["sha256"])
        ok.append(r)
    print(f"[merge] 成功图 {len(ok)} 张（去重后）")

    # 既有 sha 集合（防重复追加）
    existing = set()
    with open(MANIFEST_F, encoding="utf-8") as f:
        for line in f:
            try:
                existing.add(json.loads(line)["sha256"])
            except Exception:  # noqa: BLE001
                continue
    print(f"[merge] 清单既有 {len(existing)} sha；命中待并入 {sum(1 for r in ok if r['sha256'] in existing)}")

    lock = open(LOCK_F, "a+")
    fcntl.flock(lock, fcntl.LOCK_EX)
    try:
        copied = skipped_blob = appended = sha_mismatch = 0
        out_rows = []
        for r in ok:
            src = DATA_DIR / r["file"]
            if not src.exists():
                print(f"[merge] MISS 文件 {r['file']}")
                continue
            data = src.read_bytes()
            sha = hashlib.sha256(data).hexdigest()
            if sha != r["sha256"]:
                sha_mismatch += 1
                print(f"[merge] SHA 不符 跳过 {r['file']}")
                continue
            if sha in existing:
                skipped_blob += 1
                continue
            dst = BLOBS / sha[:2] / f"{sha}.png"
            if not args.dry_run:
                dst.parent.mkdir(parents=True, exist_ok=True)
                if not dst.exists():
                    shutil.copyfile(src, dst)
                    copied += 1
            rec = {
                "sha256": sha,
                "ext": "png",
                "source": GEN_SOURCE,
                "license": None,
                "author": None,
                "width": r["width"],
                "height": r["height"],
                "orig_width": r["width"],
                "orig_height": r["height"],
                "size_bytes": r["bytes"],
                "mime": "image/png",
                "instances": [r["instance"]],
                "queries": {},
                "query_langs": {},
                "content_url": None,
                "landing_url": None,
                "fetched_at": float(r["ts"]),
                "path": f"blobs/{sha[:2]}/{sha}.png",
            }
            out_rows.append(rec)
            existing.add(sha)
            appended += 1
        if not args.dry_run and out_rows:
            with open(MANIFEST_F, "a", encoding="utf-8") as f:
                for rec in out_rows:
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    finally:
        fcntl.flock(lock, fcntl.LOCK_UN)
        lock.close()
    tag = "[dry-run] " if args.dry_run else ""
    print(f"{tag}[merge] 完成：落盘 {copied} | 已存在跳过 {skipped_blob} | "
          f"清单追加 {appended} | sha不符 {sha_mismatch}")


if __name__ == "__main__":
    main()
