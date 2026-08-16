#!/usr/bin/env python3
"""
fix_abs_paths.py —— 一次性修复 images.jsonl 中残留的绝对路径。

背景：早期迁移批次把 path 写成旧湖的绝对路径（/root/data/demiwtg/dataset_*/blobs/...），
该目录已归档，路径全部失效；实际字节已在现行 data/dataset/blobs/<sha前两位>/ 下。
本脚本把这些行统一改写为相对路径（相对 data/dataset/ 根），与其余 3 万余条保持一致。

约定（AGENTS.md）：images.jsonl 是唯一权威主清单；修改前先备份；.meta.lock 写锁。

用法：
    python3 curation/fix_abs_paths.py --dry-run   # 预览，不落盘
    python3 curation/fix_abs_paths.py             # 落盘（自动备份 .bak.<时间戳>）
"""

import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
META = ROOT / "data" / "dataset" / "meta"
MANIFEST = META / "images.jsonl"
LOCK = META / ".meta.lock"


def rel_of(sha: str, old_path: str) -> str:
    """由 sha256 + 扩展名重建标准相对路径（blobs/<aa>/<sha>.<ext>）。"""
    ext = os.path.splitext(old_path)[1]
    return f"blobs/{sha[:2]}/{sha}{ext}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="只预览，不写文件")
    args = ap.parse_args()

    if LOCK.exists():
        print(f"[error] 写锁存在：{LOCK}（其他进程在写 meta/），稍后重试", file=sys.stderr)
        return 1

    fixed, missing = [], []
    rows = []
    with open(MANIFEST, encoding="utf-8") as f:
        for line in f:
            d = json.loads(line)
            p = d.get("path", "")
            if p.startswith("/"):
                new_p = rel_of(d["sha256"], p)
                if not (ROOT / "data" / "dataset" / new_p).exists():
                    missing.append((d["sha256"][:12], new_p))
                else:
                    d["path"] = new_p
                    fixed.append((d["sha256"][:12], p, new_p))
            rows.append(d)

    print(f"扫描 {len(rows)} 行，绝对路径 {len(fixed)} 条，相对化后文件缺失 {len(missing)} 条")
    for sha, old, new in fixed[:5]:
        print(f"  {sha}  {old}\n        -> {new}")
    if len(fixed) > 5:
        print(f"  ...（共 {len(fixed)} 条）")
    if missing:
        print("[warn] 以下条目相对化后在 dataset/ 下找不到文件，未修改其 path：")
        for sha, p in missing[:10]:
            print(f"  {sha}  {p}")

    if args.dry_run:
        print("dry-run，未写文件")
        return 0
    if not fixed:
        print("无需修改")
        return 0

    LOCK.touch()
    try:
        bak = MANIFEST.with_suffix(f".jsonl.bak.{time.strftime('%Y%m%d%H%M%S')}")
        shutil.copy2(MANIFEST, bak)
        tmp = MANIFEST.with_suffix(".jsonl.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            for d in rows:
                f.write(json.dumps(d, ensure_ascii=False) + "\n")
        os.replace(tmp, MANIFEST)
        print(f"已写入 {len(rows)} 行（备份：{bak.name}），其中修正 path {len(fixed)} 条")
    finally:
        LOCK.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
