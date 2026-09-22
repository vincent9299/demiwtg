#!/usr/bin/env python3
"""kb 图池第 1 批重收执行器（2026-09-18，复用夜间验证链路）。

同步 libcurl 下载，--transport pycurl 每车道复用连接；curl 子进程后端
保留作回退。每次请求计量、出口共享 429 冷却、COS 签名直传与 ETag 校验。
舰队入口 rebuild_fleet.py 使用 pycurl，不依赖旧 asyncio/httpx 下载事件循环。

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
import queue
import threading
import gzip
import hashlib
import importlib.util
import json
import os
import subprocess
import time
import urllib.parse
import signal
from kb_transport import CurlTransport
from kb_rate import RequestGovernor

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


# 永久性死信：重试不可能成功，重启时直接跳过（暂时性死信 interrupted/curl:*/http:5xx/403 仍重试）
PERMANENT_MISS = {"http:404", "not_image", "over_cap"}


def load_manifest_keys(path: str) -> set:
    done = set()
    if not os.path.exists(path):
        return done
    with open(path, encoding="utf-8") as f:
        for line in f:
            try:
                r = json.loads(line)
                m = r.get("miss")
                # 跳过成功行与永久死信；暂时死信行重启后仍可重试
                if m is None or m in PERMANENT_MISS:
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
    ap.add_argument("--lanes", type=int, default=1,
                    help="并发车道数（每 IP 并发连接数；2=礼貌红线上限，AIMD 全局记账）")
    ap.add_argument("--transport", choices=("curl", "pycurl"), default="curl")
    ap.add_argument("--limit", type=int, default=0, help="小批验收行数，0=全部")
    args = ap.parse_args()
    if not 1 <= args.lanes <= 8:
        ap.error("lanes 超出允许范围 1-8（上限测试已获用户授权；AIMD 仍兜底 429）")
    si, sn = (int(x) for x in args.shard.split("/"))
    if not args.ua:
        args.ua = _fc.default_ua(args.proxy, os.path.dirname(args.manifest) or ".")
        print(f"[kb] UA（按出口分配表）: {args.ua}", flush=True)
    cos_util.creds()                    # 起步即验凭据
    mdir = os.path.dirname(args.manifest) or "."
    os.makedirs(mdir, exist_ok=True)
    os.makedirs(os.path.join(mdir, "meta"), exist_ok=True)
    owner = open(args.manifest + ".owner.lock", "a")
    try:
        fcntl.flock(owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit("该账本已有下载进程，拒绝重复启动")

    done_keys = load_manifest_keys(args.manifest)
    seen = set()
    opener = gzip.open if args.tasks.endswith(".gz") else open
    # 流式两遍：第一遍计数（不驻留行，5台高密度部署的内存前提）
    n = 0
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
            n += 1
    seen = None
    print(f"[kb] 分片 {args.shard} 待收 {n}（账本已收 {len(done_keys)}）", flush=True)
    if not n:
        print("[kb] DONE 无待办", flush=True)
        owner.close()
        return

    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())

    def iter_rows():
        scheduled = set()
        emitted = 0
        with opener(args.tasks, "rt", encoding="utf-8") as f:
            for idx, line in enumerate(f):
                if stop.is_set() or (args.limit and emitted >= args.limit):
                    break
                if idx % sn != si:
                    continue
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                key = (r["qid"], r["commons_file"])
                if key not in done_keys and key not in scheduled:
                    scheduled.add(key)
                    emitted += 1
                    yield r

    gov = RequestGovernor(mdir, args.rps_start, args.rps_min, args.rps_max)
    cap = args.hard_cap_mb << 20
    mf = open(args.manifest, "a", encoding="utf-8")
    lock = open(args.manifest + ".lock", "a")
    manifest_lock = threading.Lock()
    telemetry_lock = threading.Lock()
    telemetry = open(os.path.join(mdir, "meta", "requests.jsonl"), "a", buffering=1)
    stat = {"sunk": 0, "miss": 0, "i": 0}
    stat_lock = threading.Lock()
    RETRY = (0, 5, 15, 30)

    def process_row(row, transport):
        """单行完整流水线（车道线程内执行）；节拍在 curl 发起前全局串行化。"""
        url = commons_orig_url(row["commons_file"])
        ext = row.get("ext") or row["commons_file"].rsplit(".", 1)[-1].lower()
        tmp = f"{args.manifest}.tmp{os.getpid()}.{threading.get_ident()}"
        hdr = tmp + ".hdr"
        reason = None
        data = None
        for delay in RETRY:
            if stop.is_set() or (delay and stop.wait(delay)) or not gov.pace(stop):
                reason = "interrupted"
                break
            result = transport.fetch(url, tmp, hdr)
            status = int(result["http_code"] or 0)
            ra = _fc.parse_retry_after(hdr)
            # Account for EVERY response immediately, including intermediate retries.
            gov.response(status, ra)
            with telemetry_lock:
                telemetry.write(json.dumps(dict(ts=time.time(), event="request", retry_after=ra,
                                                **result)) + "\n")
            if status == 429 or status >= 500:
                reason = f"http:{status}"; continue
            if result["curl_code"] != 0:
                reason = f"curl:{result['curl_code']}"; continue
            if status != 200:
                reason = f"http:{status}"; break
            if not os.path.exists(tmp):
                reason = "no_output"; continue
            with open(tmp, "rb") as body:
                data = body.read()
            if len(data) == 0:
                reason = "empty"; data = None; continue
            if len(data) > cap:
                reason = "over_cap"; data = None; break
            if not is_image(data[:16]):
                reason = "not_image"
                data = None; break
            reason = None
            break
        for p in (tmp, hdr):
            if os.path.exists(p):
                os.unlink(p)
        rec = dict(row)
        rec.update({"tier": "orig", "fetched_at": time.time(), "err_code": None})
        if data is not None:
            sha = hashlib.sha256(data).hexdigest()
            key = cos_util.blob_key(args.cos_prefix, sha, ext)
            upload_started = time.monotonic()
            etag = cos_util.put(key, data)
            with telemetry_lock:
                telemetry.write(json.dumps(dict(ts=time.time(), event="upload",
                                                seconds=time.monotonic()-upload_started,
                                                size=len(data), ok=etag == hashlib.md5(data).hexdigest())) + "\n")
            if etag is not None and etag == hashlib.md5(data).hexdigest():
                rec.update({"sha256": sha, "ext": ext, "page_bytes": len(data),
                            "path": f"blobs/{sha[:2]}/{sha}.{ext}", "miss": None})
                ok = True
            else:
                rec["miss"] = "cos_fail"; ok = False
        else:
            rec["miss"] = reason; ok = False
        # flock on a shared descriptor does not serialize threads.
        with manifest_lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                mf.write(json.dumps(rec, ensure_ascii=False) + "\n")
                mf.flush(); os.fsync(mf.fileno())
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)
        with stat_lock:
            stat["i"] += 1
            stat["sunk" if ok else "miss"] += 1
            i = stat["i"]
            if i % 50 == 0:
                el = time.time() - t0
                print(f"[kb 进度] {i}/{n}（{i/el:.2f} 行/s）sunk={stat['sunk']} miss={stat['miss']}",
                      flush=True)

    t0 = time.time()
    q = queue.Queue(maxsize=args.lanes * 4)
    errors = queue.Queue()
    finished = threading.Event()
    def feeder():
        try:
            for row in iter_rows():
                while not stop.is_set():
                    try:
                        q.put(row, timeout=0.5)
                        break
                    except queue.Full:
                        pass
        except Exception as e:
            errors.put(e); stop.set()
        finally:
            finished.set()
    def lane():
        transport = None
        try:
            transport = CurlTransport(args.proxy, _fc.curl_header_args("wikimedia", args.ua),
                                      CURL_TIMEOUT, cap, args.transport)
            while not stop.is_set():
                try:
                    row = q.get(timeout=0.5)
                except queue.Empty:
                    if finished.is_set():
                        break
                    continue
                process_row(row, transport)
        except Exception as e:
            errors.put(e); stop.set()
        finally:
            if transport is not None:
                transport.close()
    ft = threading.Thread(target=feeder, daemon=True)
    ft.start()
    ts = [threading.Thread(target=lane) for _ in range(args.lanes)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    ft.join()
    mf.close(); lock.close(); telemetry.close(); owner.close()
    if not errors.empty():
        raise RuntimeError("下载线程失败；退出供 systemd 恢复") from errors.get()
    sunk, miss = stat["sunk"], stat["miss"]
    state = "STOPPED" if stop.is_set() else "DONE"
    print(f"[kb] {state} sunk={sunk} miss={miss} 耗时={(time.time()-t0)/60:.1f} 分钟",
          flush=True)


if __name__ == "__main__":
    main()
