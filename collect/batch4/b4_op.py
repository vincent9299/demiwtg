#!/usr/bin/env python3
"""第 4 批图源算子：TMDB（影视）+ GBIF（生物多样性）。

架构对齐 b2_op.py（第 2 批四源算子）：
- 队列/认领/完成/重试：demiflow cosqueue + queue_runner（成功才 complete）；
- 传输：exec_curl.curl_fetch（短命 curl、分类重试、字节数封顶）；
- 落盘：sha256 内容寻址 blob 直传 COS；
- 账本：run 目录 ledger.jsonl（done-set 断点续跑）+ dead.jsonl（行级死信，
  仅永久性失败：404/非图/过小/无图；瞬态失败抛 TransientFetchError 整批回队列）。

许可分区（B4_B5_PLAN）：
- TMDB CC BY-NC → `datasets/demiwtg/kb/blobs-nc/`，账本记 license_zone="nc"；
- GBIF 聚合源许可各异 → `datasets/demiwtg/kb/blobs/`，逐媒体记 API 给的 license。

任务行（jsonl）：
- tmdb:  {"src":"tmdb","extid":"movie:290639","kind":"movie|tv|person",
          "tmdb_id":"290639","qids":["Q…"]}
  两段式：API 解析海报/头像 path（tmdb_cache 跨批复用）→ image.tmdb.org 取原图。
- gbif:  {"src":"gbif","extid":"taxon:5128269","kind":"taxon",
          "gbif_id":"5128269","qid":"Q…"}
  P846 物种（species/{key}/media 优先，空则回落带图 occurrence 搜索，
  ≤3 张/物种）。P3151 是 iNat taxon ID，归 b2-iNat 线，不经本算子。

用法：
  金丝雀（湖侧直跑，不经队列）：python3 b4_op.py --worker canary --tasks xx.jsonl
  队列（r 机）：python3 b4_op.py --worker r1 --src tmdb [--queue queue-b4-tmdb]
部署目录：~/wk_b4/{demiflow_collect/, b4_op.py, .cos_creds, .tmdb_token}
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

try:                                        # r 机 vendor 包优先，湖侧回退真包
    from demiflow_collect.cosio import COSCreds, COSIO, build_host
    from demiflow_collect.cosqueue import COSQueue
    from demiflow_collect.queue_runner import run as qr_run
    from demiflow_collect.exec_curl import curl_fetch
except ImportError:
    for _p in (os.environ.get("DEMIFLOW_PATH"),
               "/yzp/zhaozy/yangzepeng/0905/demiflow"):
        if _p and os.path.isdir(_p):
            sys.path.insert(0, _p)
            break
    from demiflow.collect.cosio import COSCreds, COSIO, build_host
    from demiflow.collect.cosqueue import COSQueue
    from demiflow.collect.queue_runner import run as qr_run
    from demiflow.collect.exec_curl import curl_fetch

BUCKET, REGION = "lhcos-368f6-1256345599", "ap-singapore"
BLOB_BASE = "lhcos-data/demiwtg-data/datasets/demiwtg/kb/blobs"
BLOB_NC_BASE = "lhcos-data/demiwtg-data/datasets/demiwtg/kb/blobs-nc"
UA = ("concept-corpus-b4/1.0 (academic concept-image corpus; "
      "https://github.com/vincent9299/demiwtg-data)")
CAP = 64 << 20
MIN_BYTES = 1024                            # 1KB 以下视为残页
TMDB_API = "https://api.themoviedb.org/3/{}/{}"
TMDB_IMG = "https://image.tmdb.org/t/p/original{}"
GBIF_TAXON_MEDIA_API = "https://api.gbif.org/v1/species/{}/media?limit=20"
GBIF_OCC_SEARCH_API = ("https://api.gbif.org/v1/occurrence/search"
                       "?taxonKey={}&mediaType=StillImage&limit=10")
TMDB_LICENSE = "CC BY-NC 4.0 (TMDB terms)"  # 许可分区 nc 的依据
TAXON_IMG_LIMIT = 3                         # 每个物种首期取几张代表图

_MAGIC = ((b"\xff\xd8\xff", "jpg"), (b"\x89PNG\r\n\x1a\n", "png"),
          (b"GIF87a", "gif"), (b"GIF89a", "gif"), (b"II*\x00", "tif"),
          (b"MM\x00*", "tif"), (b"BM", "bmp"))


def sniff(data: bytes):
    for magic, ext in _MAGIC:
        if data.startswith(magic):
            return ext
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


class WorkerState:
    """进程级单例：ledger done-set 断点续跑 + TMDB path 缓存（跨批复用）。"""

    def __init__(self, rundir, io):
        self.io = io
        self.rundir = rundir
        os.makedirs(rundir, exist_ok=True)
        self.ledger_path = os.path.join(rundir, "ledger.jsonl")
        self.dead_path = os.path.join(rundir, "dead.jsonl")
        self.finished_path = os.path.join(rundir, "finished.jsonl")
        self.done: set = set()               # 已收图媒体（tmdb 行 / gbif #子行）
        self.dead_extids: set = set()        # 行级死信 extid（重试时跳过）
        if os.path.exists(self.ledger_path):
            with open(self.ledger_path) as f:
                for line in f:
                    try:
                        self.done.add(json.loads(line)["extid"])
                    except Exception:
                        continue
        if os.path.exists(self.dead_path):
            with open(self.dead_path) as f:
                for line in f:
                    try:
                        self.dead_extids.add(json.loads(line)["extid"])
                    except Exception:
                        continue
        self.finished: set = set()           # 已整行处理完（含死信终局）的任务行
        if os.path.exists(self.finished_path):
            with open(self.finished_path) as f:
                for line in f:
                    try:
                        self.finished.add(json.loads(line)["extid"])
                    except Exception:
                        continue
        self.tmdb_cache_path = os.path.join(rundir, "tmdb_path_cache.json")
        self.tmdb_cache = {}
        if os.path.exists(self.tmdb_cache_path):
            try:
                self.tmdb_cache = json.load(open(self.tmdb_cache_path))
            except Exception:
                pass
        self._lock = threading.Lock()
        self._ledger = open(self.ledger_path, "a", buffering=1)
        self._dead = open(self.dead_path, "a", buffering=1)
        self._finished = open(self.finished_path, "a", buffering=1)

    def emit(self, row: dict):
        with self._lock:
            self._ledger.write(json.dumps(row, ensure_ascii=False) + "\n")
            self.done.add(row["extid"])

    def mark_finished(self, extid: str):
        """任务行终局标记（成功收完或死信终结）；瞬态失败不得调用。"""
        with self._lock:
            self._finished.write(json.dumps({"extid": extid,
                                             "ts": time.time()}) + "\n")
            self.finished.add(extid)

    def emit_dead(self, extid: str, reason: str, url: str = ""):
        with self._lock:
            self._dead.write(json.dumps({"extid": extid, "reason": reason,
                                         "url": url, "ts": time.time()},
                                        ensure_ascii=False) + "\n")
            self.dead_extids.add(extid)


_STATE: WorkerState = None
_LANES = 4
_TOKEN = ""


class TransientFetchError(RuntimeError):
    """行级瞬态失败（5xx／429／超时／连接类）。

    抛出后整批回队列重试；已成功行由 done-set 跳过，不重复下载。
    """


def _is_transient_curl(r) -> bool:
    if getattr(r, "throttled", False):
        return True
    status = r.status or 0
    if status >= 500 or status in (408, 429):
        return True
    reason = str(getattr(r, "reason", "") or "").lower()
    return ("exhaust" in reason or "timeout" in reason
            or "conn" in reason or "reset" in reason)


_API_PACE_LOCK = threading.Lock()
_API_PACE_LAST = [0.0]
_API_PACE_INTERVAL = 1.2          # 每 worker API 调用最小间隔(2-3 lanes 共享预算)


def _api_json(url: str, headers: dict):
    """GET JSON。4xx(408/429 除外)→(code, None)；
    进程级节拍器压请求率(GBIF 突发限流的根治:稳态 ~0.83 req/s/worker);
    429/5xx 短退避重试 2 轮;网络类→Transient;耗尽→Transient。"""
    import random as _random
    for attempt in range(3):
        with _API_PACE_LOCK:
            wait = _API_PACE_INTERVAL - (time.monotonic() - _API_PACE_LAST[0])
            if wait > 0:
                time.sleep(wait)
            _API_PACE_LAST[0] = time.monotonic()
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.status, json.load(r)
        except urllib.error.HTTPError as exc:
            if exc.code in (408, 429) or exc.code >= 500:
                time.sleep(15 * (attempt + 1) + _random.random() * 10)
                continue
            if 400 <= exc.code < 500:
                return exc.code, None                  # 永久:404/410 等
            raise TransientFetchError(f"{url[:60]}: http {exc.code}") from exc
        except Exception as exc:
            raise TransientFetchError(f"{url[:60]}: {exc}") from exc
    raise TransientFetchError(f"{url[:60]}: 429/5xx 退避耗尽")


def tmdb_image_path(kind: str, tmdb_id: str) -> str:
    """两段式第一段：entity API → 主图 path。空图/404 返回 ""（行级死信）。"""
    if not _TOKEN:
        raise RuntimeError("TMDB token 缺失（系统故障，勿走死信）")
    key = f"{kind}:{tmdb_id}"
    with _STATE._lock:
        if key in _STATE.tmdb_cache:
            return _STATE.tmdb_cache[key]
    field = {"movie": "poster_path", "tv": "poster_path",
             "person": "profile_path"}[kind]
    code, obj = _api_json(TMDB_API.format(kind, tmdb_id),
                          {"Authorization": f"Bearer {_TOKEN}",
                           "User-Agent": UA})
    path = ""
    if code == 200 and isinstance(obj, dict):
        path = obj.get(field) or ""
    with _STATE._lock:
        _STATE.tmdb_cache[key] = path
        if len(_STATE.tmdb_cache) % 500 == 0:      # 崩溃最多重查 500 个
            json.dump(_STATE.tmdb_cache, open(_STATE.tmdb_cache_path, "w"))
    return path


def _store_image(row: dict, extid: str, url: str, license_: str,
                 nc_zone: bool) -> None:
    """下载一张图 → 内容寻址 blob → 账本行。瞬态失败上抛，永久失败死信。"""
    r = curl_fetch(url, ua=UA, cap_bytes=CAP, timeout=300,
                   retries=(0, 5, 15, 30),
                   tmp_path=f"/tmp/b4curl.{os.getpid()}.{threading.get_ident()}.{extid[-8:]}")
    if not r.ok:
        status = r.status or 0
        if getattr(r, "throttled", False) or status >= 500 or status in (408, 429):
            raise TransientFetchError(f"{extid}: http:{r.status or r.reason}"[:80])
        # 网络耗尽类(无状态码:死链/DNS/连接拒绝/重试梯子走完)→行级死信。
        # 旧版归瞬态→整批回队列→同一死链永远炸批(2026-09-22 gbif 滴灌实锤:0 done 死循环)。
        _STATE.emit_dead(extid, f"http:{r.status or r.reason}"[:60], url)
        return
    data = r.data
    if len(data) < MIN_BYTES:
        _STATE.emit_dead(extid, "too_small", url)
        return
    ext = sniff(data)
    if not ext:
        _STATE.emit_dead(extid, "not_image", url)
        return
    sha = hashlib.sha256(data).hexdigest()
    base = BLOB_NC_BASE if nc_zone else BLOB_BASE
    key = f"{base}/{sha[:2]}/{sha}.{ext}"
    etag = _STATE.io.put_bytes(key, data)      # 内容寻址 PUT 幂等，免 HEAD 咨询
    if etag is None:
        raise RuntimeError(f"COS 上传失败: {key}")   # 系统性故障→批失败重试
    out = {"src": row["src"], "extid": extid, "url": url,
           "sha256": sha, "ext": ext, "bytes": len(data),
           "license": license_, "license_zone": "nc" if nc_zone else "",
           "ts": time.time()}
    if "qids" in row:
        out["qids"] = row["qids"]
    if "qid" in row:
        out["qid"] = row["qid"]
    _STATE.emit(out)


def fetch_row(row: dict, ctx) -> None:
    extid = str(row["extid"])
    if extid in _STATE.finished:
        return
    src = row["src"]
    try:
        if src == "tmdb":
            kind = row["kind"]
            path = tmdb_image_path(kind, row["tmdb_id"])
            if not path:
                _STATE.emit_dead(extid, "tmdb:no_image")
            else:
                _store_image(row, extid, TMDB_IMG.format(path),
                             TMDB_LICENSE, nc_zone=True)
        elif src == "gbifdl":
            # download 路线:URL 已由 multimedia.txt 切好,免 API 直收
            _store_image(row, extid, row["url"], row.get("license", ""),
                         nc_zone=bool(row.get("nc", "")))
        elif src == "gbif":
            # P3151 是 iNaturalist taxon ID（金丝雀实测纠偏），其图由 b2-iNat 线
            # 覆盖；GBIF 桥只用 P846（taxonKey），kind 仅 taxon 一种。
            gbif_id = row["gbif_id"]
            code, obj = _api_json(GBIF_TAXON_MEDIA_API.format(gbif_id),
                                  {"User-Agent": UA})
            media = []
            if code == 200 and isinstance(obj, dict):
                media = [m for m in (obj.get("results") or [])
                         if m.get("identifier")]
            if not media:
                # species/media 常为空 → 回落带图 occurrence 搜索（主力路径）
                code, obj = _api_json(GBIF_OCC_SEARCH_API.format(gbif_id),
                                      {"User-Agent": UA})
                if code == 200 and isinstance(obj, dict):
                    for occ in (obj.get("results") or []):
                        for m in (occ.get("media") or []):
                            if m.get("identifier"):
                                media.append(m)
                        if len(media) >= TAXON_IMG_LIMIT:
                            break
            if not media:
                _STATE.emit_dead(extid, "gbif:no_media")
            else:
                _fetch_media_children(row, extid, media[:TAXON_IMG_LIMIT])
        else:
            _STATE.emit_dead(extid, f"bad_src:{src}")
    except TransientFetchError:
        raise                                            # 不标终局，批回队列重试
    _STATE.mark_finished(extid)


def _fetch_media_children(row: dict, extid: str, media: list) -> None:
    """gbif 多图行：逐媒体收图，done∪dead 的子行跳过（断点续跑）。"""
    for i, m in enumerate(media):
        child = f"{extid}#{i}"
        if child in _STATE.done or child in _STATE.dead_extids:
            continue
        _store_image(row, child, m["identifier"],
                     m.get("license") or "", nc_zone=False)


def b4_batch_op(rows: list, ctx) -> None:
    """queue_runner 批算子：行级失败入死信，系统性异常才上抛。"""
    todo = [r for r in rows if str(r["extid"]) not in _STATE.finished]
    with ThreadPoolExecutor(_LANES) as ex:
        list(ex.map(lambda r: fetch_row(r, ctx), todo))
    print(f"[b4:{ctx.worker}] 批 {ctx.handle.bid}: {len(rows)} 行 "
          f"(跳过已收 {len(rows) - len(todo)})", flush=True)


class _Ctx:
    pass


def main():
    global _STATE, _LANES, _TOKEN
    ap = argparse.ArgumentParser()
    ap.add_argument("--worker", required=True)
    ap.add_argument("--src", default="")            # tmdb | gbif（队列模式）
    ap.add_argument("--queue", default="")
    ap.add_argument("--lanes", type=int, default=4)
    ap.add_argument("--home", default=os.path.expanduser("~/wk_b4"))
    ap.add_argument("--tasks", default="")          # 金丝雀直跑模式
    args = ap.parse_args()

    _LANES = args.lanes
    import re as _re
    for cand in (os.path.join(args.home, ".tmdb_token"),
                 "/yzp/zhaozy/yangzepeng/0905/demiwtg/collect/batch5/.tmdb_token"):
        if os.path.exists(cand):
            text = open(cand, encoding="utf-8", errors="replace").read()
            m = _re.search(r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+",
                           text)
            _TOKEN = m.group(0) if m else ""    # 文件含标签/中文备注，只取 JWT
            break
    need_token = (args.src == "tmdb") or (
        args.tasks and any(json.loads(l).get("src") == "tmdb"
                           for l in open(args.tasks) if l.strip()))
    if not _TOKEN and need_token:
        print("[b4] 缺 TMDB token", file=sys.stderr)
        sys.exit(2)

    creds = COSCreds.discover(paths=(os.path.join(args.home, ".cos_creds"),
                                     "/yzp/zhaozy/yangzepeng/0905/demiwtg/collect/image_backfill/.cos_creds"))
    io_ = COSIO(creds, build_host(BUCKET, REGION))
    rundir = os.path.join(args.home, f"run_{args.worker}")
    _STATE = WorkerState(rundir, io_)
    print(f"[b4] worker={args.worker} lanes={args.lanes} "
          f"done-set={len(_STATE.done)} token={'y' if _TOKEN else 'n'}", flush=True)

    if args.tasks:                                  # 金丝雀：不经队列直跑
        rows = [json.loads(l) for l in open(args.tasks) if l.strip()]
        ctx = _Ctx()
        ctx.worker = args.worker
        ctx.handle = type("H", (), {"bid": "canary"})()
        with ThreadPoolExecutor(_LANES) as ex:
            list(ex.map(lambda r: fetch_row(r, ctx), rows))
        print(f"[b4] canary done: {len(rows)} 行 -> ledger="
              f"{len(_STATE.done)}", flush=True)
        return

    queue = args.queue or f"lhcos-data/demiwtg-data/queue-b4-{args.src or 'x'}"
    out = qr_run(b4_batch_op, queue=COSQueue(io_, queue), worker=args.worker,
                 workdir=args.home, on_failure_sleep=60.0,
                 log=lambda m: print(m, flush=True))
    print(f"[b4] {out}", flush=True)


if __name__ == "__main__":
    main()
