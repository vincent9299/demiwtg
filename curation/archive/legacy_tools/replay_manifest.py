#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""replay_manifest.py — preloss 权威清单按 blobs 实存回放（2026-09-08，已被同日
全量恢复决策取代，--apply 已封死）。

沿革：早间图片还原时本脚本把清单截回 blob 实存子集（194,449 行）；同日晚用户
拍板恢复**全量 preloss 清单**（2,849,013 行，缺 blob 行含下载链接供集群补采），
meta/images.jsonl 已直拷归档件恢复。本脚本自此仅作 blob 覆盖核查工具（干跑
报表），--apply 拒绝执行——重跑会把清单截断回子集，破坏补采工作面。

用法：
  python3 curation/replay_manifest.py                 # blob 覆盖报表（只读）
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import random
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
META = ROOT / "datasets" / "demiwtg" / "meta"
BLOBS = ROOT / "datasets" / "demiwtg" / "blobs"
MANIFEST = META / "images.jsonl"
CONCEPTS = META / "concepts.json"
DEFAULT_SRC = ROOT / "state" / "collect" / "instance_images.preloss_20260906.jsonl"
LOCK = META / ".meta.lock"


def blob_index() -> dict[str, str]:
    """blobs 现存 {sha: ext}（文件名即 sha，不逐文件哈希——抽样复验兜底）。"""
    out: dict[str, str] = {}
    if not BLOBS.exists():
        return out
    for sub in BLOBS.iterdir():
        if not sub.is_dir():
            continue
        for f in sub.iterdir():
            stem, dot, ext = f.name.partition(".")
            if dot and len(stem) == 64:
                out[stem] = ext
    return out


def sample_verify(blobs: dict[str, str], n: int = 64) -> tuple[int, int]:
    """抽样复验：文件名 sha == 内容 sha。返回 (通过, 抽样数)。"""
    names = sorted(blobs)
    if not names:
        return 0, 0
    picks = random.Random(20260908).sample(names, min(n, len(names)))
    ok = 0
    for sha in picks:
        p = BLOBS / sha[:2] / f"{sha}.{blobs[sha]}"
        h = hashlib.sha256()
        with p.open("rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        ok += (h.hexdigest() == sha)
    return ok, len(picks)


def main() -> None:
    ap = argparse.ArgumentParser(description="preloss 清单按 blobs 实存回放")
    ap.add_argument("--src", type=Path, default=DEFAULT_SRC,
                    help="preloss 清单归档（jsonl）")
    ap.add_argument("--apply", action="store_true", help="回放写盘（默认干跑）")
    args = ap.parse_args()
    if args.apply:
        sys.exit("已封死（2026-09-08 全量恢复决策）：images.jsonl 为全量 preloss 清单"
                 "（2,849,013 行，缺 blob 行是集群补采工作面），--apply 会截断回 "
                 "blob 实存子集。blob 覆盖报表跑干跑模式即可。")
    if not args.src.exists():
        sys.exit(f"清单归档不存在：{args.src}")

    blobs = blob_index()
    print(f"[blobs] 实存 {len(blobs):,} 个 sha")
    if not blobs:
        sys.exit("blobs 为空——先完成 tar 还原抽取再回放")
    ok, n = sample_verify(blobs)
    print(f"[blobs] 抽样复验 {ok}/{n} 文件名==内容 sha"
          + ("" if ok == n else "  ⚠️ 存在失配，中止；人工核查 blobs"))
    if ok != n:
        sys.exit(1)

    concept_names = {c["name"] for c in
                     json.loads(CONCEPTS.read_text(encoding="utf-8"))["concepts"]}
    kept_rows = 0
    drop_missing = 0
    covered: Counter[str] = Counter()
    qual_covered: Counter[str] = Counter()
    dangling = Counter()
    shas_used: set[str] = set()

    tmp = MANIFEST.with_suffix(".jsonl.tmp")
    out = tmp.open("w", encoding="utf-8") if args.apply else None
    try:
        with args.src.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                r = json.loads(line)
                sha = r.get("sha256") or ""
                if sha not in blobs:
                    drop_missing += 1
                    continue
                insts = r.get("instances") or []
                gated = (r.get("quality") or 0) >= 8 and r.get("identity") is True
                for i in insts:
                    covered[i] += 1
                    if gated:
                        qual_covered[i] += 1
                    if i not in concept_names:
                        dangling[i] += 1
                shas_used.add(sha)
                kept_rows += 1
                if out:
                    out.write(json.dumps(r, ensure_ascii=False) + "\n")
    finally:
        if out:
            out.close()

    orphan_blobs = len(blobs) - len(shas_used)
    print(f"[manifest] 回放 {kept_rows:,} 行 / {len(shas_used):,} sha"
          f"（丢弃 blob 缺失行 {drop_missing:,}；孤儿 blob {orphan_blobs:,} 个无清单行）")
    print(f"[manifest] 概念覆盖：{len(covered):,} 概念有图"
          f"（质量门合格口径 {len(qual_covered):,}）")
    if dangling:
        print(f"[manifest] ⚠️ 悬空概念名 {len(dangling)} 个（样例 "
              f"{list(dangling)[:5]}）——清单行保留，处置另议")
    if not args.apply:
        tmp.unlink(missing_ok=True)
        print("[manifest] 报表完成（--apply 已封死：全量清单是补采工作面，勿截断）")
        return
    with LOCK.open("a") as lf:
        fcntl.flock(lf, fcntl.LOCK_EX)
        try:
            import os
            os.replace(tmp, MANIFEST)
        finally:
            fcntl.flock(lf, fcntl.LOCK_UN)
    print(f"[manifest] 已写 {MANIFEST.relative_to(ROOT)}（{kept_rows:,} 行，原子替换）")


if __name__ == "__main__":
    main()
