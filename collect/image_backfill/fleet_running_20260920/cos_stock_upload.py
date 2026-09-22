#!/usr/bin/env python3
"""r 机存量 blob → COS 直传（回收 370GB 的替代路径）。

用法（在 r 机上）：
  python3 cos_stock_upload.py [--root ~/wk_backfill] \
      [--prefix lhcos-data/demiwtg-data/datasets/demiwtg/blobs] \
      [--purge] [--workers 3] [--dry-run]

行为：遍历 root 下所有 blobs/<2>/<sha>.<ext>；HEAD 已存在的跳过（幂等，
可反复重跑）；PUT 后 ETag(=md5) 校验字节一致才算成功；--purge 时成功后
删本地文件腾盘。输出进度的同时把成功清单写 <root>/cos_stock.done.tsv
（sha、key、size），失败写 cos_stock.fail.tsv 便于补传。"""
from __future__ import annotations

import argparse
import hashlib
import os
import re
import sys
import threading
from concurrent.futures import ThreadPoolExecutor

import cos_util

NAME_RE = re.compile(r"^([0-9a-f]{64})\.([a-z0-9]+)$")


def scan(root: str):
    """只收 <root>/**/blobs/<sha前2>/<sha>.<ext> 两层布局，meta/日志不碰。"""
    for dirpath, _dirs, files in os.walk(root):
        if os.path.basename(os.path.dirname(dirpath)) != "blobs":
            continue
        for fn in files:
            m = NAME_RE.match(fn)
            if m:
                yield os.path.join(dirpath, fn), m.group(1), m.group(2)


def upload_one(path: str, sha: str, ext: str, prefix: str, purge: bool):
    key = cos_util.blob_key(prefix, sha, ext)
    sz = os.path.getsize(path)
    if cos_util.head(key) == sz:
        return "skip", key, sz
    blob = open(path, "rb").read()
    if len(blob) != sz:
        return "fail", key, sz
    if hashlib.sha256(blob).hexdigest() != sha:
        return "corrupt", key, sz          # 本地坏文件，绝不传
    etag = cos_util.put(key, blob)
    if etag is None or etag != hashlib.md5(blob).hexdigest():
        return "fail", key, sz
    if purge:
        os.unlink(path)
    return "ok", key, sz


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=os.path.expanduser("~/wk_backfill"))
    ap.add_argument("--prefix", default="lhcos-data/demiwtg-data/datasets/demiwtg/blobs")
    ap.add_argument("--purge", action="store_true", help="上传成功后删本地文件")
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    files = list(scan(args.root))
    total = sum(os.path.getsize(p) for p, _, _ in files)
    print(f"[stock] {len(files)} 个文件 / {total / 1e9:.1f} GB；"
          f"purge={args.purge} workers={args.workers}", flush=True)
    if args.dry_run:
        for p, s, e in files[:10]:
            print("  样例:", p, s[:12], e)
        return

    done_tsv = open(os.path.join(args.root, "cos_stock.done.tsv"), "a")
    fail_tsv = open(os.path.join(args.root, "cos_stock.fail.tsv"), "a")
    lock = threading.Lock()
    stat = {"ok": 0, "skip": 0, "fail": 0, "corrupt": 0, "bytes": 0}

    def work(item):
        path, sha, ext = item
        try:
            r, key, sz = upload_one(path, sha, ext, args.prefix, args.purge)
        except Exception as e:                    # noqa: BLE001 - 单文件失败不拖全局
            r, key, sz = "fail", path, 0
            print(f"[stock] 异常 {path}: {e}", file=sys.stderr, flush=True)
        with lock:
            stat[r] = stat.get(r, 0) + 1
            if r in ("ok", "skip"):
                stat["bytes"] += sz
                done_tsv.write(f"{sha}\t{key}\t{sz}\t{r}\n")
            elif r == "fail":
                fail_tsv.write(f"{sha}\t{key}\t{sz}\n")
            n = stat["ok"] + stat["skip"] + stat["fail"] + stat["corrupt"]
            if n % 50 == 0:
                done_tsv.flush(); fail_tsv.flush()
                print(f"[stock] {n}/{len(files)} ok={stat['ok']} skip={stat['skip']} "
                      f"fail={stat['fail']} corrupt={stat['corrupt']} "
                      f"{stat['bytes'] / 1e9:.1f}GB", flush=True)

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        list(ex.map(work, files))
    done_tsv.close(); fail_tsv.close()
    print(f"[stock] DONE {stat}", flush=True)


if __name__ == "__main__":
    main()
