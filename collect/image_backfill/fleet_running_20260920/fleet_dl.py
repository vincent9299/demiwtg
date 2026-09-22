"""Fleet 单文件补图下载器（2026-09-16，r1-r20+pipeline-b/c/d 用）。

与采集 pipeline backfill.py 同闸门：防盗链头表（wikimedia 礼仪 UA）、
单图 20MB 上限、90s 硬超时、SHA256 复验唯一入库闸门、原子写（tmp+rename）、
done/dead 双清单幂等续跑。差异：429/5xx/超时做有限次退避重试（10s/20s/40s），
仍失败记死信；无 demiflow 引擎依赖，仅需 httpx。

行契约与 backfill.py 相同：{"c","u","s","e","src"}。
用法：python3 fleet_dl.py --candidates shard.jsonl --out-dir ./run
"""

from __future__ import annotations

import argparse
import asyncio
import gzip
import hashlib
import json
import os
import time

import httpx

API_UA = ("collect-v2/0.1 (research image collection; "
          "https://github.com/vincent9299/demiwtg-data) httpx/0.28")
BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
MAX_BYTES = 20 * 1024 * 1024
HARD_TIMEOUT = 90.0
RETRY_DELAYS = (3, 8, 15)

HEADER_TABLE = {
    "wikimedia": {"User-Agent": API_UA},
    "wikimedia_zh": {"User-Agent": API_UA},
    "baidu": {"User-Agent": BROWSER_UA, "Referer": "https://image.baidu.com/",
              "Accept": "image/avif,image/webp,image/*,*/*;q=0.8"},
    "huaban_api": {"User-Agent": BROWSER_UA, "Referer": "https://huaban.com/",
                   "Accept": "image/avif,image/webp,image/*,*/*;q=0.8"},
    "pixiv": {"User-Agent": BROWSER_UA, "Referer": "https://www.pixiv.net/",
              "Accept": "image/avif,image/webp,image/*,*/*;q=0.8"},
}


class Pacer:
    """全局请求起搏：请求发起间隔 >= 1/rps（令牌桶按 wikimedia 实测 1/s）。"""

    def __init__(self, rps: float):
        self.interval = 1.0 / rps
        self._next = 0.0
        self._lock = asyncio.Lock()

    async def wait_slot(self) -> None:
        async with self._lock:
            now = time.monotonic()
            start = max(now, self._next)
            self._next = start + self.interval
        delay = start - time.monotonic()
        if delay > 0:
            await asyncio.sleep(delay)


def load_rows(path: str) -> list[dict]:
    opener = gzip.open if path.endswith(".gz") else open
    rows = []
    with opener(path, "rt", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return rows


def load_ledger_shas(path: str, key: str) -> set:
    shas: set = set()
    if not os.path.exists(path):
        return shas
    with open(path, encoding="utf-8") as f:
        for line in f:
            try:
                shas.add(json.loads(line)[key])
            except (json.JSONDecodeError, KeyError):
                continue
    return shas


async def fetch_one(client: httpx.AsyncClient, row: dict, blob_root: str,
                    done_f, dead_f, lock: asyncio.Lock,
                    counters: dict, pacer: "Pacer") -> str:
    """单次尝试。返回 "ok"/"skip"/"done_dead"/"retry"（retry 由外层退避后重进）。"""
    s, ext, src, url = row["s"], row["e"], row["src"], row["u"]
    rel = os.path.join(blob_root, s[:2], f"{s}.{ext}")
    if os.path.exists(rel):                     # 幂等：他机已下/blob 实存
        async with lock:
            counters["skip"] += 1
        return "skip"
    headers = HEADER_TABLE.get(src, {"User-Agent": BROWSER_UA})
    reason = None
    for _attempt in range(1):                   # 单次尝试；break=记死信
        await pacer.wait_slot()
        try:
            async with client.stream("GET", url, headers=headers,
                                     timeout=httpx.Timeout(30, read=30)) as resp:
                if resp.status_code == 200:
                    h = hashlib.sha256()
                    chunks, size = [], 0
                    t0 = time.monotonic()
                    async for chunk in resp.aiter_bytes(65536):
                        size += len(chunk)
                        if size > MAX_BYTES:
                            reason = "capped"; break
                        if time.monotonic() - t0 > HARD_TIMEOUT:
                            reason = "hard_timeout"; break
                        h.update(chunk)
                        chunks.append(chunk)
                    if reason:
                        break
                    if h.hexdigest() != s:
                        reason = "sha_mismatch"; break
                    os.makedirs(os.path.dirname(rel), exist_ok=True)
                    tmp = f"{rel}.tmp{os.getpid()}"
                    with open(tmp, "wb") as f:
                        for c in chunks:
                            f.write(c)
                    os.replace(tmp, rel)
                    async with lock:
                        done_f.write(json.dumps({
                            "concepts": row["c"], "source": src,
                            "content_url": url, "sha256": s, "ext": ext,
                            "blob_path": f"blobs/{s[:2]}/{s}.{ext}",
                            "size_bytes": size}, ensure_ascii=False) + "\n")
                        done_f.flush()
                        counters["done"] += 1
                    return "ok"
                elif resp.status_code == 429 or resp.status_code >= 500:
                    return "retry"
                else:
                    reason = f"http:{resp.status_code}"   # 确定性失败
        except (httpx.TimeoutException, httpx.TransportError):
            return "retry"
        except Exception as exc:                          # noqa: BLE001
            reason = f"net:{type(exc).__name__}"
    async with lock:
        dead_f.write(json.dumps({"s": s, "u": url, "src": src,
                                 "reason": reason},
                                ensure_ascii=False) + "\n")
        dead_f.flush()
        counters["dead"] += 1
    return "done_dead"


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--candidates", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--concurrency", type=int, default=12)
    ap.add_argument("--rps", type=float, default=1.0)
    ap.add_argument("--log-every", type=int, default=100)
    args = ap.parse_args()

    os.makedirs(os.path.join(args.out_dir, "meta"), exist_ok=True)
    os.makedirs(os.path.join(args.out_dir, "blobs"), exist_ok=True)
    done_path = os.path.join(args.out_dir, "meta", "done.jsonl")
    dead_path = os.path.join(args.out_dir, "meta", "dead.jsonl")
    skip = load_ledger_shas(done_path, "sha256") | load_ledger_shas(dead_path, "s")
    rows = [r for r in load_rows(args.candidates) if r["s"] not in skip]
    print(f"[fleet] {len(rows)} 待下（已跳过 {len(skip)}）", flush=True)

    counters = {"done": 0, "dead": 0, "skip": 0}
    sem = asyncio.Semaphore(args.concurrency)
    pacer = Pacer(args.rps)
    lock = asyncio.Lock()
    limits = httpx.Limits(max_connections=args.concurrency,
                          max_keepalive_connections=args.concurrency)
    t0 = time.time()
    seen = 0

    with open(done_path, "a", encoding="utf-8") as done_f, \
         open(dead_path, "a", encoding="utf-8") as dead_f:
        async with httpx.AsyncClient(limits=limits,
                                     timeout=httpx.Timeout(30, read=30),
                                     follow_redirects=True) as client:
            async def guarded(row):
                blob_root = os.path.join(args.out_dir, "blobs")
                for delay in (0,) + RETRY_DELAYS:
                    if delay:
                        await asyncio.sleep(delay)      # 退避不占并发额度
                    async with sem:
                        try:
                            outcome = await asyncio.wait_for(
                                fetch_one(client, row, blob_root,
                                          done_f, dead_f, lock,
                                          counters, pacer),
                                timeout=105)
                        except asyncio.TimeoutError:
                            outcome = "retry"          # 慢滴连接兜底，防占死槽位
                    if outcome != "retry":
                        break
                nonlocal seen
                n = counters["done"] + counters["dead"] + counters["skip"]
                if n >= seen + args.log_every:
                    seen = n
                    rate = n / max(time.time() - t0, 1e-9)
                    print(f"[进度] {n}/{len(rows)}（{rate:.2f} 行/s） "
                          f"落盘={counters['done']} 死信={counters['dead']} "
                          f"跳过={counters['skip']}", flush=True)

            await asyncio.gather(*(guarded(r) for r in rows))
    print(f"[fleet] 完成：落盘 {counters['done']}、死信 {counters['dead']}、"
          f"跳过 {counters['skip']}、耗时 {(time.time()-t0)/60:.1f} 分钟", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
