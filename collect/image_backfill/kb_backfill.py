#!/usr/bin/env python3
"""kb 图池第 1 批重收执行器（2026-09-18，复用夜间验证链路）。

替代 backfill_orig.py/flow_images_batch（asyncio/httpx 系，VM 上有静默断连
前科）：本工具走 fleet_curl 同款短命 curl 子进程 + AIMD 动态节拍 + COS
签名直传 + 出口唯一诚实 UA。

与 fleet_curl 的差异（毒行/缩略没有预期 sha，闸门换型）：
- 输入行 = kb 账本行（qid, commons_file, license/author/... 完整元数据）
- URL 本地构造：upload.wikimedia.org MD5 路径，不打 API
- 闸门 = HTTP 200 + 图片魔数 + 尺寸上限（拒绝 HTML 错误页=毒行根因）
- 账本 = kb schema（新 sha256、tier=orig，元数据从旧行继承），按
  (qid,commons_file) 断点续跑；blob 仍内容寻址 kb/blobs/<2>/<sha>.<ext>

用法：
  python3 kb_backfill.py --tasks poison_rows.jsonl.gz --shard 0/8 \
      --manifest run_kb0/manifest.jsonl --cos-prefix lhcos-data/demiwtg-data/datasets/demiwtg/kb/blobs \
      [--proxy http://u:p@ip:port] [--rps-start 0.25] [--hard-cap-mb 64]
"""
from __future__ import annotations

import argparse
import fcntl
import gzip
import hashlib
import importlib.util
import json
import os
import subprocess
import time
import urllib.parse

_DIR = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location("fleet_curl", os.path.join(_DIR, "fleet_curl.py"))
_fc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_fc)          # 复用 RateGovernor/curl_header_args/default_ua/parse_retry_after
import cos_util

CURL_TIMEOUT = 180                      # 64MB 大图余量
MAGIC = [b"\xff\xd8\xff", b"\x89PNG", b"GIF8", b"II*\x00", b"MM\x00*",
         b"%PDF", b"AT&TFORM", b"<svg", b"<?xml", b"RIFF", b"BM", b"\x1a\x45\xdf\xa3"]


def commons_orig_url(filename: str) -> str:
    """Commons 原图 MD5 直链：空格→下划线后取 md5 定位 <a>/<ab>/。"""
    norm = filename.strip().replace(" ", "_")
    h = hashlib.md5(norm.encode("utf-8")).hexdigest()
    quoted = urllib.parse.quote(norm)
    return f"https://upload.wikimedia.org/wikipedia/commons/{h[0]}/{h[0:2]}/{quoted}"


def is_image(head: bytes) -> bool:
    return any(head.startswith(m) for m in MAGIC)


def load_manifest_keys(path: str) -> set:
    done = set()
    if not os.path.exists(path):
        return done
    with open(path, encoding="utf-8") as f:
        for line in f:
            try:
                r = json.loads(line)
                done.add((r["qid"], r["commons_file"]))
            except (json.JSONDecodeError, KeyError):
                continue
    return done


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tasks", required=True, help="任务清单 jsonl[.gz]（kb 账本行）")
    ap.add_argument("--shard", required=True, metavar="I/N")
    ap.add_argument("--manifest", required=True, help="重收账本（断点续跑键=qid+commons_file）")
    ap.add_argument("--cos-prefix", default="lhcos-data/demiwtg-data/datasets/demiwtg/kb/blobs")
    ap.add_argument("--proxy", default="")
    ap.add_argument("--ua", default="")
    ap.add_argument("--rps-start", type=float, default=0.25)
    ap.add_argument("--rps-min", type=float, default=0.08)
    ap.add_argument("--rps-max", type=float, default=0.45)
    ap.add_argument("--hard-cap-mb", type=int, default=64)
    args = ap.parse_args()
    si, sn = (int(x) for x in args.shard.split("/"))
    if not args.ua:
        args.ua = _fc.default_ua(args.proxy, os.path.dirname(args.manifest) or ".")
        print(f"[kb] UA（按出口分配表）: {args.ua}", flush=True)
    cos_util.creds()                    # 起步即验凭据
    mdir = os.path.dirname(args.manifest) or "."
    os.makedirs(mdir, exist_ok=True)
    os.makedirs(os.path.join(mdir, "meta"), exist_ok=True)

    done_keys = load_manifest_keys(args.manifest)
    rows = []
    seen = set()
    opener = gzip.open if args.tasks.endswith(".gz") else open
    with opener(args.tasks, "rt", encoding="utf-8") as f:
        for idx, line in enumerate(f):
            if idx % sn != si:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            k = (r["qid"], r["commons_file"])
            if k in done_keys or k in seen:
                continue
            seen.add(k)
            rows.append(r)
    n = len(rows)
    print(f"[kb] 分片 {args.shard} 待收 {n}（账本已收 {len(done_keys)}）", flush=True)
    if not n:
        print("[kb] DONE 无待办", flush=True)
        return

    gov = _fc.RateGovernor(os.path.dirname(args.manifest) or ".",
                           args.rps_start, args.rps_min, args.rps_max)
    cap = args.hard_cap_mb << 20
    mf = open(args.manifest, "a", encoding="utf-8")
    lock = open(args.manifest + ".lock", "a")
    sunk = miss = 0
    t0 = time.time()
    RETRY = (0, 5, 15, 30)
    for i, row in enumerate(rows):
        url = commons_orig_url(row["commons_file"])
        ext = row.get("ext") or row["commons_file"].rsplit(".", 1)[-1].lower()
        tmp = f"{args.manifest}.tmp{os.getpid()}"
        hdr = tmp + ".hdr"
        reason = None
        throttled = False
        data = None
        for delay in RETRY:
            if delay:
                time.sleep(delay)
            cmd = (["curl", "-sSLk", "--max-time", str(CURL_TIMEOUT),
                    "--max-filesize", str(cap), "-D", hdr]
                   + _fc.curl_header_args("wikimedia", args.ua)   # UA+同项目 Referer（夜间验证路径）
                   + ["-w", "%{http_code}", "-o", tmp, url])
            if args.proxy:
                cmd[1:1] = ["-x", args.proxy]
            try:
                r = subprocess.run(cmd, timeout=CURL_TIMEOUT + 15,
                                   capture_output=True, text=True)
            except subprocess.TimeoutExpired:
                reason = "subproc_timeout"; continue
            if r.returncode != 0:
                reason = f"curl:{r.returncode}"; continue
            status = (r.stdout or "").strip()
            if status == "429" or status.startswith("5"):
                throttled = True
                ra = _fc.parse_retry_after(hdr)
                if ra > 0:
                    time.sleep(ra)
                reason = f"http:{status}"; continue
            if status and status != "200":
                reason = f"http:{status}"; break
            if not os.path.exists(tmp):
                reason = "no_output"; continue
            data = open(tmp, "rb").read()
            if len(data) == 0:
                reason = "empty"; data = None; continue
            if len(data) > cap:
                reason = "over_cap"; data = None; break
            if not is_image(data[:16]):
                reason = "not_image"       # HTML 错误页/异常内容，拒收
                data = None; break
            reason = None
            break
        for p in (tmp, hdr):
            if os.path.exists(p):
                os.unlink(p)
        rec = dict(row)                    # 继承 license/author/width/height/qid...
        rec.update({"tier": "orig", "fetched_at": time.time(),
                    "err_code": None})
        if data is not None:
            sha = hashlib.sha256(data).hexdigest()
            key = cos_util.blob_key(args.cos_prefix, sha, ext)
            etag = cos_util.put(key, data)
            if etag is not None and etag == hashlib.md5(data).hexdigest():
                rec.update({"sha256": sha, "ext": ext, "page_bytes": len(data),
                            "path": f"blobs/{sha[:2]}/{sha}.{ext}", "miss": None})
                sunk += 1
            else:
                rec["miss"] = "cos_fail"
                miss += 1
        else:
            rec["miss"] = reason
            miss += 1
        fcntl.flock(lock, fcntl.LOCK_EX)
        mf.write(json.dumps(rec, ensure_ascii=False) + "\n")
        mf.flush(); os.fsync(mf.fileno())
        fcntl.flock(lock, fcntl.LOCK_UN)
        if throttled:
            gov.on_throttle()
        elif data is not None:
            gov.on_ok()
        gov.pace()
        if (i + 1) % 50 == 0:
            rate = (i + 1) / max(time.time() - t0, 1e-9)
            print(f"[kb 进度] {i+1}/{n}（{rate:.2f} 行/s）sunk={sunk} miss={miss}",
                  flush=True)
    print(f"[kb] DONE sunk={sunk} miss={miss} 耗时={(time.time()-t0)/60:.1f} 分钟",
          flush=True)


if __name__ == "__main__":
    main()
