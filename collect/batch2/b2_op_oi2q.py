#!/usr/bin/env python3
"""第 2 批四源下载算子（业务层，消费 demiflow 平台机制）。

架构（对齐第 1 批第三代）：
- 队列/认领/完成/重试：demiflow cosqueue + queue_runner（成功才 complete）；
- 传输：demiflow exec_curl.curl_fetch（短命 curl、分类重试、字节数封顶）；
- 落盘：sha256 内容寻址 blob 直传 COS `datasets/demiwtg/kb/blobs/<s2>/<sha>.<ext>`；
- 账本：本机 run 目录 ledger.jsonl（done-set 断点续跑）+ dead.jsonl（行级死信，
  仅**永久性**失败：404/非图/过小等；**不上抛**——批只因系统性故障失败）。
- 瞬态失败（R5）：重试耗尽的 5xx/超时/连接类 curl 失败与 Met API 网络异常抛
  TransientFetchError 使**整批回队列**重试（done-set 跳过已收行），不再写入死信；
  恢复后最终产生成功记录。Met 只缓存成功应答（含 primaryImage 为空的真实无图）。

任务行（jsonl，按源）：
- oi: {"src","extid":imageID,"qids":[Q…],"url"}     （QID 由 mid_map 预展开）
- si: {"src","extid":media_id,"qids":[Q…],"url","license"}
- inat: {"src","extid":photo_id,"qid","url","license"}
- met: {"src","extid":objectID,"qid","url":""}      （url 由算子两段式 API 解析）

用法（r 机 / 湖侧金丝雀同款）：
  python3 b2_op.py --worker r1 --src oi [--queue queue-b2-oi] [--lanes 4]
部署目录（r 机）：~/wk_b2/{demiflow_collect/, b2_op.py, .cos_creds}
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

try:                                        # r 机 vendor 包优先，湖侧回退真包
    from demiflow_collect.cosio import COSCreds, COSIO, build_host
    from demiflow_collect.cosqueue import COSQueue
    from demiflow_collect.queue_runner import run as qr_run
    from demiflow_collect.exec_curl import curl_fetch
except ImportError:
    # 湖侧 PEP660 editable 装的 demiflow 对新模块是静态映射——显式指仓库
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
UA = ("concept-corpus-b2/1.0 (academic concept-image corpus; "
      "https://github.com/vincent9299/demiwtg-data)")
CAP = 64 << 20
_RPS = [1.5]
_PROXY = [""]
_rlock = threading.Lock()
_rnext = [0.0]
def throttle():
    while True:
        with _rlock:
            now = time.monotonic()
            if now >= _rnext[0]:
                _rnext[0] = max(now, _rnext[0]) + 1.0 / _RPS[0]
                return
            w = _rnext[0] - now
        time.sleep(min(w, 5.0))
def aimd(ok429):
    # 429 -> 速率减半(下限0.2);成功 20 次回升一档(上限初始值)
    with _rlock:
        if ok429:
            _RPS[0] = max(0.2, _RPS[0] / 2)
        else:
            _RPS[0] = min(_RPS[0] * 1.05 + 0.01, 1.5)
MIN_BYTES = 1024                            # 1KB 以下视为残页
MET_API = "https://collectionapi.metmuseum.org/public/collection/v1/objects/{}"

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
    """进程级单例：ledger done-set 断点续跑 + Met URL 缓存（跨批复用）。"""

    def __init__(self, rundir, io):
        self.io = io
        self.rundir = rundir
        os.makedirs(rundir, exist_ok=True)
        self.ledger_path = os.path.join(rundir, "ledger.jsonl")
        self.dead_path = os.path.join(rundir, "dead.jsonl")
        self.done: set = set()
        if os.path.exists(self.ledger_path):
            with open(self.ledger_path) as f:
                for line in f:
                    try:
                        self.done.add(json.loads(line)["extid"])
                    except Exception:
                        continue
        self.met_cache_path = os.path.join(rundir, "met_url_cache.json")
        self.met_cache = {}
        if os.path.exists(self.met_cache_path):
            try:
                self.met_cache = json.load(open(self.met_cache_path))
            except Exception:
                pass
        self._lock = threading.Lock()
        self._ledger = open(self.ledger_path, "a", buffering=1)
        self._dead = open(self.dead_path, "a", buffering=1)

    def met_url(self, oid: str) -> str:
        with self._lock:
            if oid in self.met_cache:
                return self.met_cache[oid]
        try:
            req = urllib.request.Request(MET_API.format(oid),
                                         headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=30) as r:
                img = (json.load(r) or {}).get("primaryImage") or ""
        except urllib.error.HTTPError as exc:
            if 400 <= exc.code < 500 and exc.code not in (408, 429):
                img = ""                            # 永久无对象：按空图终结
            else:
                raise TransientFetchError(f"met api {oid}: {exc.code}") from exc
        except Exception as exc:
            raise TransientFetchError(f"met api {oid}: {exc}") from exc
        with self._lock:
            self.met_cache[oid] = img
            if len(self.met_cache) % 500 == 0:      # 崩溃最多重查 500 个
                json.dump(self.met_cache, open(self.met_cache_path, "w"))
        return img

    def emit(self, row: dict):
        with self._lock:
            self._ledger.write(json.dumps(row, ensure_ascii=False) + "\n")
            self.done.add(row["extid"])

    def emit_dead(self, extid: str, reason: str, url: str = ""):
        with self._lock:
            self._dead.write(json.dumps({"extid": extid, "reason": reason,
                                         "url": url, "ts": time.time()},
                                        ensure_ascii=False) + "\n")


_STATE: WorkerState = None
_LANES = 4                                        # main() 依 --lanes 覆盖


class TransientFetchError(RuntimeError):
    """行级瞬态失败（重试耗尽的 5xx／超时／连接类，或 Met API 网络异常）。

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




def subprocess_probe_size(url: str):
    """Range 0-0 探对象总长（content-range 尾段）；失败返回 None。"""
    import subprocess as _sp
    try:
        r = _sp.run(["curl", "-sL", "-r", "0-0", "-D", "-", "-o", "/dev/null",
                     "-m", "15", "-A", UA, url],
                    capture_output=True, text=True, timeout=25)
        for h in r.stdout.splitlines():
            k, _, v = h.partition(":")
            if k.strip().lower() == "content-range" and "/" in v:
                tot = v.strip().rsplit("/", 1)[-1]
                if tot.isdigit():
                    return int(tot)
    except Exception:
        return None
    return None


def fetch_row(row: dict, ctx) -> None:
    extid = str(row["extid"])
    url = row.get("url") or ""
    if not url and row.get("src") == "met":
        url = _STATE.met_url(extid)
        if not url:
            _STATE.emit_dead(extid, "met:no_primary_image")
            return
    # SI 巨扫 jpg 单流跨洋 ~100KB/s：120s 正好卡线 → 300s 放行慢而合法的行
    if row.get("src") == "si":
        # SI 巨扫 jpg（>25MB）单流跨洋 300s 拉不完 → 先 Range 探总长，瞬时分类进死信
        # （20 分钟的重试循环换成 0.3s 探针；这些行走 pending_slow 慢道终局）
        pr = subprocess_probe_size(url)
        if pr is not None and pr > 8 << 20:
            _STATE.emit_dead(extid, "jpg_over_8mb", url)
            return
    is_si = row.get("src") == "si"
    is_fl = "flickr" in url
    r = None
    if is_fl:
        for _att in range(3):
            throttle()
            r = curl_fetch(url, ua=UA, cap_bytes=CAP, timeout=45, retries=(0,), proxy=_PROXY[0],
                           tmp_path=f"/tmp/b2curl.{os.getpid()}.{threading.get_ident()}.{extid[-6:]}")
            if getattr(r, "status", None) == 429:
                aimd(True)
                if _att >= 1:
                    break
                time.sleep(8)
                continue
            aimd(False)
            if r.ok or getattr(r, "status", None) in (404, 410):
                break
            if _att >= 2:
                break
            time.sleep(3)
    else:
        r = curl_fetch(url, ua=UA, cap_bytes=CAP, proxy=_PROXY[0],
                       timeout=600 if is_si else 300,
                       retries=(0,) if is_si else (0, 5, 15, 30),
                       tmp_path=f"/tmp/b2curl.{os.getpid()}.{threading.get_ident()}.{extid[-6:]}")
    if not r.ok:
        if _is_transient_curl(r) and not is_si and not is_fl:
            # R5：瞬态失败不入死信，整批回队列；恢复后 done-set 之外重收。
            # SI 例外：跨洋单流仅 30-80KB/s，重试必耗尽成活锁——单次尝试 +
            # 600s 封顶 + 失败即死信（si_stream:*），批必定走完，终局统一补。
            raise TransientFetchError(
                f"{extid}: http:{r.status or r.reason}"[:80])
        reason = (f"si_stream:{r.status or r.reason}" if is_si and _is_transient_curl(r)
                  else (str(r.reason) if r.status == 200
                        else f"http:{r.status or r.reason}"))[:60]
        if is_fl and getattr(r, "status", None) == 429:
            reason = "flickr:429_cool"
        elif is_fl and _is_transient_curl(r):
            reason = f"flickr:{r.status or r.reason}"[:60]
        _STATE.emit_dead(extid, reason, url)
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
    key = f"{BLOB_BASE}/{sha[:2]}/{sha}.{ext}"
    etag = _STATE.io.put_bytes(key, data)      # 内容寻址 PUT 幂等，免 HEAD 咨询
    if etag is None:
        raise RuntimeError(f"COS 上传失败: {key}")   # 系统性故障→批失败重试
    out = {"src": row["src"], "extid": extid, "url": url,
           "sha256": sha, "ext": ext, "bytes": len(data),
           "license": row.get("license", ""), "ts": time.time()}
    if "qids" in row:
        out["qids"] = row["qids"]
    if "qid" in row:
        out["qid"] = row["qid"]
    _STATE.emit(out)


def b2_batch_op(rows: list, ctx) -> None:
    """queue_runner 批算子：行级失败入死信，系统性异常才上抛。"""
    todo = [r for r in rows if str(r["extid"]) not in _STATE.done]
    with ThreadPoolExecutor(_LANES) as ex:
        list(ex.map(lambda r: fetch_row(r, ctx), todo))
    print(f"[b2:{ctx.worker}] 批 {ctx.handle.bid}: {len(rows)} 行 "
          f"(跳过已收 {len(rows) - len(todo)})", flush=True)


class _Ctx:
    pass


def main():
    global _STATE
    ap = argparse.ArgumentParser()
    ap.add_argument("--worker", required=True)
    ap.add_argument("--src", default="")
    ap.add_argument("--queue", default="")
    ap.add_argument("--lanes", type=int, default=4)
    ap.add_argument("--rps", type=float, default=1.5)
    ap.add_argument("--proxy", default="")
    ap.add_argument("--home", default=os.path.expanduser("~/wk_b2"))
    args = ap.parse_args()

    queue = args.queue or f"lhcos-data/demiwtg-data/queue-b2-{args.src or 'x'}"
    global _LANES
    _LANES = args.lanes
    _RPS[0] = args.rps
    _PROXY[0] = args.proxy.strip()
    creds = COSCreds.discover(paths=(os.path.join(args.home, ".cos_creds"),))
    io_ = COSIO(creds, build_host(BUCKET, REGION))
    rundir = os.path.join(args.home, f"run_{args.worker}")
    _STATE = WorkerState(rundir, io_)

    print(f"[b2] worker={args.worker} queue={queue} lanes={args.lanes} "
          f"done-set={len(_STATE.done)}", flush=True)
    out = qr_run(b2_batch_op, queue=COSQueue(io_, queue), worker=args.worker,
                 workdir=args.home, on_failure_sleep=60.0,
                 log=lambda m: print(m, flush=True))
    print(f"[b2] {out}", flush=True)


if __name__ == "__main__":
    main()
