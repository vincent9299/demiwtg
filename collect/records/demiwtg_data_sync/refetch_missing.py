#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""refetch_missing.py — 湖侧缺图行按 URL 补采（demiwtg-data 集群侧，2026-09-08）。

背景：湖机器图片丢失后恢复全量 preloss 清单（meta/images.jsonl 285 万行），
其中 ~196.6 万 sha 的 blob 缺失。湖侧导出补采清单，本工具按 URL 重下：

    下载 content_url（按源防盗链头，复用 operators/download.download_headers_for）
    → sha256 复验（内容寻址：哈希不符即丢弃，错内容进不了湖）
    → 落 blob（blobs/<aa>/<sha>.<ext>，原子写）
    → 追加 image.jsonl 行（原行元数据随行，fetched_at 刷新；flock 串行追加）

清单格式（自动识别）：
  全字段版 lake/refetch_worklist.jsonl    —— 本仓 schema（concepts 键 + 全元数据）
  极简版 refetch_min.jsonl[.gz]           —— {c: [概念], u: url, s: sha256,
                                              e: ext, src: source}（gzip 157MB，
                                              同 sha 多概念已合并；回写行补全
                                              RECORD_FIELDS 缺省值）
断点续跑：state 文件记 done/dead sha；重跑跳过已完成与死链（--retry-dead
重试死链）。产出统计与死信清单（refetch_dead.jsonl）供巡检。

用法（集群 VM，本仓根）：
  python3 refetch_missing.py refetch_min.jsonl.gz                    # 极简版全量
  python3 refetch_missing.py refetch_worklist.jsonl --limit 1000     # 试跑
  python3 refetch_missing.py refetch_min.jsonl.gz --workers 64
  python3 refetch_missing.py refetch_min.jsonl.gz --retry-dead       # 死链重试轮
"""
from __future__ import annotations

import argparse
import asyncio
import fcntl
import hashlib
import json
import os
import sys
import time
from pathlib import Path

import httpx

from operators.download import download_headers_for

MAX_BYTES = 20 * 1024 * 1024        # 对齐 operators.download.MAX_DOWNLOAD_BYTES
HARD_TIMEOUT = 60.0
UA_FALLBACK = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                             "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"}


def load_state(state_path: Path) -> tuple[set, set]:
    done, dead = set(), set()
    if state_path.exists():
        for line in state_path.open(encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            (done if r.get("ok") else dead).add(r["sha256"])
    return done, dead


async def fetch_one(client, sem, rec, blobs_root, dead_path, dead_lock, stats):
    sha = rec["sha256"]
    url = rec.get("content_url")
    ext = rec.get("ext") or "jpg"
    dst = Path(blobs_root) / sha[:2] / f"{sha}.{ext}"
    if dst.exists():
        async with dead_lock:
            stats["skip_blob"] += 1
        return rec, "blob_exists"
    headers = download_headers_for(rec.get("source") or "") or UA_FALLBACK
    try:
        async with sem:
            r = await client.get(url, headers=headers,
                                 timeout=httpx.Timeout(30, read=30),
                                 follow_redirects=True)
        if r.status_code != 200:
            raise RuntimeError(f"HTTP {r.status_code}")
        data = r.content
        if len(data) > MAX_BYTES:
            raise RuntimeError(f"size {len(data)} > {MAX_BYTES}")
        if hashlib.sha256(data).hexdigest() != sha:
            raise RuntimeError("sha256 mismatch（内容已变）")
        dst.parent.mkdir(parents=True, exist_ok=True)
        tmp = dst.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_bytes(data)
        os.replace(tmp, dst)
        async with dead_lock:
            stats["ok"] += 1
        return rec, "ok"
    except Exception as e:                      # noqa: BLE001
        async with dead_lock:
            stats["dead"] += 1
            with dead_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps({"sha256": sha, "url": url,
                                    "error": str(e)[:200]}, ensure_ascii=False) + "\n")
        return rec, "dead"


def norm_rec(rec: dict) -> dict:
    """极简行 {c,u,s,e,src} → 本仓清单行（RECORD_FIELDS 缺省补全）。

    湖侧全量清单在册：回灌后按 (sha256, concept) join 还原 license/author/
    打标等原元数据，精简清单只丢传输中的字段，不丢湖里的。
    """
    if "sha256" in rec:                       # 全字段版（已是本仓 schema）
        return rec
    return {
        "sha256": rec["s"], "ext": rec.get("e") or "jpg",
        "source": rec.get("src") or "", "license": None, "author": None,
        "width": None, "height": None, "orig_width": None, "orig_height": None,
        "size_bytes": None, "mime": None,
        "concepts": list(rec.get("c") or []),
        "queries": {}, "query_langs": {},
        "content_url": rec.get("u"), "landing_url": None,
        "fetched_at": time.time(),
        "path": f"blobs/{rec['s'][:2]}/{rec['s']}.{rec.get('e') or 'jpg'}",
        "kb_match": None, "richness": None, "caption": None,
        "identity": None, "focus": None, "quality": None,
    }


def open_maybe_gzip(path: str):
    """gzip 自动识别读（按魔数，不看后缀）。"""
    import gzip as _gzip
    import io as _io
    with open(path, "rb") as probe:
        magic = probe.read(2)
    if magic == b"\x1f\x8b":
        return _gzip.open(path, "rt", encoding="utf-8")
    return open(path, encoding="utf-8")


async def main_async(args) -> None:
    dataset = Path(args.dataset)
    blobs_root = Path(args.blob_root) if args.blob_root else dataset
    manifest = dataset / "meta" / "image.jsonl"
    state_path = Path(args.state)
    dead_path = state_path.with_suffix(".dead.jsonl")

    # 既有清单 sha（已采内容不重下）
    have = set()
    if manifest.exists():
        with manifest.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        have.add(json.loads(line)["sha256"])
                    except (json.JSONDecodeError, KeyError):
                        continue
    done, dead = load_state(state_path)
    if not args.retry_dead:
        done |= dead
    print(f"[state] 清单既有 {len(have):,} sha；断点 done {len(done):,}"
          f" / dead {len(dead):,}", flush=True)

    work = []
    with open_maybe_gzip(args.worklist) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = norm_rec(json.loads(line))
            sha = rec["sha256"]
            if sha in have or sha in done:
                continue
            work.append(rec)
            if args.limit and len(work) >= args.limit:
                break
    print(f"[work] 待补采 {len(work):,} 行（workers={args.workers}）", flush=True)
    if not work:
        return

    sem = asyncio.Semaphore(args.workers)
    dead_lock = asyncio.Lock()
    state_lock = asyncio.Lock()
    stats = {"ok": 0, "dead": 0, "skip_blob": 0}
    t0 = time.time()
    manifest_f = manifest.open("a", encoding="utf-8")
    lock_f = (dataset / "meta" / ".meta.lock").open("a")
    fcntl.flock(lock_f, fcntl.LOCK_EX)
    try:
        async with httpx.AsyncClient() as client:
            async def run_one(rec):
                out_rec, status = await fetch_one(
                    client, sem, rec, blobs_root, dead_path, dead_lock, stats)
                if status in ("ok", "blob_exists"):
                    out_rec = dict(out_rec)
                    out_rec["fetched_at"] = time.time()
                    out_rec["path"] = (f"blobs/{out_rec['sha256'][:2]}/"
                                       f"{out_rec['sha256']}.{out_rec.get('ext') or 'jpg'}")
                    async with state_lock:
                        manifest_f.write(json.dumps(out_rec, ensure_ascii=False) + "\n")
                        manifest_f.flush()
                        state_path.parent.mkdir(parents=True, exist_ok=True)
                        with state_path.open("a", encoding="utf-8") as sf:
                            sf.write(json.dumps({"sha256": out_rec["sha256"],
                                                 "ok": True}) + "\n")
            n = 0
            for fut in asyncio.as_completed([run_one(r) for r in work]):
                await fut
                n += 1
                if n % 500 == 0:
                    rate = n / (time.time() - t0)
                    print(f"[进度] {n}/{len(work)}（{rate:.1f}/s）"
                          f" ok={stats['ok']:,} dead={stats['dead']:,}", flush=True)
    finally:
        fcntl.flock(lock_f, fcntl.LOCK_UN)
        lock_f.close()
        manifest_f.close()
    print(f"[done] ok={stats['ok']:,} dead={stats['dead']:,} "
          f"skip={stats['skip_blob']:,}，耗时 {time.time()-t0:.0f}s；"
          f"死信 {dead_path}", flush=True)


def main() -> None:
    ap = argparse.ArgumentParser(description="湖侧缺图行按 URL 补采（sha256 复验）")
    ap.add_argument("worklist",
                    help="补采清单：极简版 refetch_min.jsonl[.gz]（{c,u,s,e,src}）"
                         "或全字段版 refetch_worklist.jsonl（本仓 schema）")
    ap.add_argument("--dataset", default=os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "datasets", "demiwtg"),
        help="清单/状态根（默认本仓 datasets/demiwtg）")
    ap.add_argument("--blob-root", default="",
                    help="blob 落盘根（缺省=--dataset）")
    ap.add_argument("--workers", type=int, default=32)
    ap.add_argument("--limit", type=int, default=0, help="试跑上限（0=全量）")
    ap.add_argument("--state", default="state/refetch_done.jsonl",
                    help="断点状态文件（done sha 逐行追加）")
    ap.add_argument("--retry-dead", action="store_true", help="重试死信轮")
    args = ap.parse_args()
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
