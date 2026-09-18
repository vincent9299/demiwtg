"""Fleet curl 版补图下载器（2026-09-17）。

背景：asyncio/httpx 长驻进程在这批腾讯 VM 上会静默失去全部连接并卡死
（事件循环只剩定时器空转）；curl 在同机始终可靠。故用 python 串行驱动
curl 子进程完成下载，python 只做账本/校验。

闸门与 fleet_dl.py 一致：原图 URL + SHA256 复验 + 20MB 上限 + 原子写 +
done/dead 双清单幂等续跑。超时/网络失败按行重试至 3 次（间隔 2/5/10s）。

用法：python3 fleet_curl.py --candidates wm_XX.jsonl --out-dir run_XX
"""

from __future__ import annotations

import argparse
import collections
import gzip
import hashlib
import json
import os
import re
import subprocess
import time
import zlib

API_UA = "demiflow-backfill/1.2 (image restoration; https://github.com/hollowreed42/demiflow-backfill)"
BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
# 诚实身份单一事实来源：hub/ua_pool.txt（80 条）；r 机由部署器随脚本同步同文件
UA_POOL_FILES = (
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "ua_pool.txt"),
    "/yzp/zhaozy/yangzepeng/0905/demiwtg/state/curation/image_backfill_full_v1/hub/ua_pool.txt",
)
# 中央分配表（assign_uas.py 生成）：每出口一条唯一 UA，key=proxy:<ip>/host:<rN>
UA_ASSIGN_FILES = (
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "ua_assign.tsv"),
    "/yzp/zhaozy/yangzepeng/0905/demiwtg/state/curation/image_backfill_full_v1/hub/ua_assign.tsv",
)


def load_ua_pool():
    for p in UA_POOL_FILES:
        try:
            uas = [l.strip() for l in open(p, encoding="utf-8") if l.strip()]
            if uas:
                return uas
        except OSError:
            continue
    return [API_UA]


def load_ua_assign():
    for p in UA_ASSIGN_FILES:
        try:
            m = {}
            with open(p, encoding="utf-8") as f:
                for line in f:
                    k, _, v = line.rstrip("\n").partition("\t")
                    if k and v:
                        m[k] = v
            if m:
                return m
        except OSError:
            continue
    return {}


def default_ua(proxy: str, out_dir: str) -> str:
    """按出口查分配表：--proxy 取 proxy:<ip>，直连取 ua.env（部署器按
    ssh 别名写入的本机唯一 UA，看门狗路径也走这里）→ host:<主机名> →
    crc 回退并提示同步。"""
    import socket
    assign = load_ua_assign()
    key = ""
    if proxy:
        m = re.search(r"@(\d+\.\d+\.\d+\.\d+):", proxy) or re.search(r"@(\[?[0-9a-fA-F:.]+\]?):", proxy)
        key = f"proxy:{m.group(1)}" if m else ""
    else:
        env_p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ua.env")
        try:
            for line in open(env_p, encoding="utf-8"):
                if line.startswith("UA=") and line[3:].strip():
                    return line[3:].strip()
        except OSError:
            pass
        key = f"host:{socket.gethostname().split('.')[0]}"
    ua = assign.get(key)
    if ua:
        return ua
    pool = load_ua_pool()
    if assign:
        print(f"[fleet-curl] 警告：分配表无 {key or '(未知出口)'}，crc 回退（可能与他机重复，"
              f"请同步 ua_assign.tsv/ua.env）", flush=True)
    return pool[zlib.crc32(out_dir.encode()) % len(pool)]
CURL_HEADERS = {
    "wikimedia": ["-A", API_UA, "-e", "https://github.com/hollowreed42/demiflow-backfill"],
    "wikimedia_zh": ["-A", API_UA, "-e", "https://github.com/hollowreed42/demiflow-backfill"],
    "baidu": ["-A", BROWSER_UA, "-H", "Referer: https://image.baidu.com/"],
    "huaban_api": ["-A", BROWSER_UA, "-H", "Referer: https://huaban.com/"],
    "pixiv": ["-A", BROWSER_UA, "-H", "Referer: https://www.pixiv.net/"],
}


def curl_header_args(src: str, ua_override: str = "") -> list[str]:
    if ua_override:
        # 身份自洽：UA 与 Referer 指向同一项目（wikimedia 类身份页）；
        # baidu/huaban/pixiv 的防盗链 Referer 优先于身份页
        m = re.search(r"https?://[^\s)]+", ua_override)
        ref = m.group(0) if m else "https://github.com/hollowreed42/demiflow-backfill"
        hdr = CURL_HEADERS.get(src)
        if hdr:
            base = ["-A", ua_override] + (hdr[2:] if len(hdr) > 2 else [])
            if src in ("wikimedia", "wikimedia_zh") and "-e" in base:
                base[base.index("-e") + 1] = ref
            return base
        return ["-A", ua_override, "-e", ref]
    return CURL_HEADERS.get(src, ["-A", BROWSER_UA])
MAX_BYTES = 20 * 1024 * 1024
CURL_TIMEOUT = 75
RETRY_DELAYS = (0, 5, 15, 30)


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


def ledger_shas(path: str, key: str) -> set:
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


class RateGovernor:
    """AIMD 动态节拍：成功稳步提速，一见限速立刻减半，窗口内限速占比
    超阈值则熔断冷却。所有变化记 meta/rate.log 供中枢观测。"""

    def __init__(self, out_dir, start, lo, hi, window=60, trip=0.05,
                 cooldown=600.0, ai_every=25, ai_step=0.02):
        self.log = os.path.join(out_dir, "meta", "rate.log")
        self.rps = start
        self.lo, self.hi = lo, hi
        self.window = window
        self.trip = trip
        self.cooldown = cooldown
        self.ai_every, self.ai_step = ai_every, ai_step
        self.hist = collections.deque(maxlen=window)   # True=本次被限速
        self.ok_streak = 0
        self.trips = 0
        self._emit("start")

    def _emit(self, event, extra=""):
        with open(self.log, "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": round(time.time(), 1), "event": event,
                                "rps": round(self.rps, 3), "extra": extra}) + "\n")

    def on_ok(self):
        self.hist.append(False)
        self.ok_streak += 1
        if self.ok_streak >= self.ai_every and self.rps < self.hi:
            self.rps = min(self.hi, self.rps + self.ai_step)
            self.ok_streak = 0
            self._emit("ai")

    def on_throttle(self):
        self.hist.append(True)
        self.ok_streak = 0
        self.rps = max(self.lo, self.rps / 2.0)
        self._emit("md")
        # 熔断需窗口过半样本 + 占比超阈：孤立 429 只减半不熔断，
        # 密集成串才判定为风暴
        if (len(self.hist) >= self.window // 2
                and sum(self.hist) / len(self.hist) > self.trip):
            self.trips += 1
            wait = min(self.cooldown * (2 ** (self.trips - 1)), 3600.0)
            self._emit("trip", f"wait={wait:.0f}s trips={self.trips}")
            time.sleep(wait)
            self.hist.clear()
            self.rps = self.lo

    def pace(self):
        time.sleep(1.0 / self.rps)


def parse_retry_after(hdr_path):
    """从 curl -D 落盘的响应头里取 Retry-After 秒数；无则 0。"""
    try:
        with open(hdr_path, "r", encoding="latin-1") as f:
            for line in f:
                k, _, v = line.partition(":")
                if k.strip().lower() == "retry-after":
                    return max(0.0, float(v.strip() or 0))
    except (OSError, ValueError):
        pass
    return 0.0


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--candidates", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--blob-root", default="",
                    help="共享 blob 根（缺省=out-dir/blobs）；同机多 worker 共用")
    ap.add_argument("--proxy", default="", help="http 代理（http://user:pass@host:port）")
    ap.add_argument("--ua", default="", help="覆盖默认 UA（用自有项目地址池的变体）")
    ap.add_argument("--rps", type=float, default=0.0,
                    help="兼容旧参数：等效 --rps-start 且同时抬为上限（0=用动态默认）")
    ap.add_argument("--rps-start", type=float, default=0.25, help="起步速率")
    ap.add_argument("--rps-min", type=float, default=0.08, help="降速下限")
    ap.add_argument("--rps-max", type=float, default=0.45, help="提速上限")
    ap.add_argument("--window", type=int, default=60, help="限速判定窗口（行）")
    ap.add_argument("--trip-rate", type=float, default=0.05, help="窗口内限速占比阈值")
    ap.add_argument("--cooldown", type=float, default=600.0, help="熔断基础冷却秒")
    ap.add_argument("--cos-prefix", default="",
                    help="COS 直传前缀（如 lhcos-data/demiwtg-data/datasets/demiwtg/blobs）；"
                         "空=只写本地 blob（旧行为）")
    ap.add_argument("--cos-keep-local", action="store_true",
                    help="COS 上传成功后保留本地 blob（默认删除腾盘）")
    args = ap.parse_args()
    if not args.ua:
        # 出口绑定唯一诚实 UA：代理→proxy:<ip>，直连→host:<主机名>
        args.ua = default_ua(args.proxy, args.out_dir)
        print(f"[fleet-curl] UA（按出口分配表）: {args.ua}", flush=True)

    meta = os.path.join(args.out_dir, "meta")
    blobs = os.path.join(args.blob_root, "blobs") if args.blob_root else os.path.join(args.out_dir, "blobs")
    os.makedirs(meta, exist_ok=True)
    os.makedirs(blobs, exist_ok=True)
    done_path = os.path.join(meta, "done.jsonl")
    dead_path = os.path.join(meta, "dead.jsonl")
    skip = ledger_shas(done_path, "sha256") | ledger_shas(dead_path, "s")
    rows = [r for r in load_rows(args.candidates) if r["s"] not in skip]
    n = len(rows)
    print(f"[fleet-curl] {n} 待下（已跳过 {len(skip)}）", flush=True)

    cos = None
    if args.cos_prefix:
        import cos_util
        cos = cos_util
        try:
            cos.creds()                       # 起步即验凭据，缺了立刻报清楚
        except RuntimeError as e:
            print(f"[fleet-curl] COS 不可用：{e}；退出（不静默降级为本地模式）", flush=True)
            raise SystemExit(2)
        print(f"[fleet-curl] COS 直传开启 prefix={args.cos_prefix} "
              f"keep_local={args.cos_keep_local}", flush=True)

    if not n:
        print("[fleet-curl] DONE 无待办", flush=True)
        return

    t0 = time.time()
    done = dead = 0
    if args.rps > 0:
        args.rps_start = args.rps
        args.rps_max = max(args.rps_max, args.rps)      # --rps 语义=起步即上限
    gov = RateGovernor(args.out_dir, args.rps_start, args.rps_min,
                       args.rps_max, args.window, args.trip_rate, args.cooldown)
    for i, row in enumerate(rows):
        s, ext, src, url = row["s"], row["e"], row["src"], row["u"]
        rel = os.path.join(blobs, s[:2], f"{s}.{ext}")
        if os.path.exists(rel):
            continue
        cos_key = cos.blob_key(args.cos_prefix, s, ext) if cos else ""
        if cos and cos.head(cos_key) >= 0:
            # COS 已有该对象（本地曾 purge 的幂等恢复）：只补账本
            with open(done_path, "a", encoding="utf-8") as f:
                f.write(json.dumps({
                    "concepts": row["c"], "source": src, "content_url": url,
                    "sha256": s, "ext": ext,
                    "blob_path": f"blobs/{s[:2]}/{s}.{ext}",
                    "cos_key": cos_key, "cos_only": 1}, ensure_ascii=False) + "\n")
            done += 1
            continue
        os.makedirs(os.path.dirname(rel), exist_ok=True)
        tmp = f"{rel}.tmp{os.getpid()}"
        hdr = tmp + ".hdr"
        reason = None
        throttled = False
        for delay in RETRY_DELAYS:
            if delay:
                time.sleep(delay)
            try:
                cmd = (["curl", "-sSLk", "--max-time", str(CURL_TIMEOUT),
                        "--max-filesize", str(MAX_BYTES), "-D", hdr]
                       + curl_header_args(src, args.ua)
                       + ["-w", "%{http_code}", "-o", tmp, url])
                if args.proxy:
                    cmd[1:1] = ["-x", args.proxy]
                r = subprocess.run(cmd,
                    timeout=CURL_TIMEOUT + 10, capture_output=True, text=True)
            except subprocess.TimeoutExpired:
                reason = "subproc_timeout"; continue
            if r.returncode != 0:
                reason = f"curl:{r.returncode}"; continue
            status = (r.stdout or "").strip()
            if status == "429" or status.startswith("5"):
                throttled = True
                ra = parse_retry_after(hdr)
                if ra > 0:
                    time.sleep(ra)                # 尊重服务端指示的恢复时间
                reason = f"http:{status}"; continue          # 退避后重试
            if status and status != "200":
                reason = f"http:{status}"; break              # 确定性失败
            if not os.path.exists(tmp):
                reason = "curl:no_output"; continue
            sz = os.path.getsize(tmp)
            if sz == 0 or sz > MAX_BYTES:
                reason = "capped"; break
            h = hashlib.sha256()
            with open(tmp, "rb") as f:
                for chunk in iter(lambda: f.read(1 << 20), b""):
                    h.update(chunk)
            if h.hexdigest() != s:
                reason = "sha_mismatch"; break
            os.replace(tmp, rel)
            rec = {
                "concepts": row["c"], "source": src, "content_url": url,
                "sha256": s, "ext": ext,
                "blob_path": f"blobs/{s[:2]}/{s}.{ext}",
                "size_bytes": sz}
            if cos:
                # SHA 已过闸，直传 COS；ETag(=md5) 校验字节一致
                blob = open(rel, "rb").read()
                etag = cos.put(cos_key, blob)
                if etag is not None and etag == hashlib.md5(blob).hexdigest():
                    rec["cos_key"] = cos_key
                    if not args.cos_keep_local:
                        os.unlink(rel)        # 腾盘：r 机不留副本
                        rec["blob_path"] = ""
                else:
                    # 上传失败/校验不符：本地保留，账本标记，存量直传阶段补
                    rec["cos_fail"] = 1
                    print(f"[fleet-curl] COS 上传未确认 sha={s[:12]}，本地保留",
                          flush=True)
            with open(done_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            done += 1
            reason = None
            break
        if os.path.exists(hdr):
            os.unlink(hdr)
        if tmp and os.path.exists(tmp):
            os.unlink(tmp)
        if throttled:
            gov.on_throttle()                   # 减半 + 窗口占比超阈熔断冷却
        elif reason is None:
            gov.on_ok()
        gov.pace()                              # 动态节拍：1/当前rps
        if reason:
            with open(dead_path, "a", encoding="utf-8") as f:
                f.write(json.dumps({"s": s, "u": url, "src": src,
                                    "reason": reason},
                                   ensure_ascii=False) + "\n")
            dead += 1
        if (i + 1) % 50 == 0:
            rate = (i + 1) / max(time.time() - t0, 1e-9)
            print(f"[进度] {i+1}/{n}（{rate:.2f} 行/s）done={done} dead={dead}",
                  flush=True)
    print(f"[fleet-curl] DONE 落盘={done} 死信={dead} "
          f"耗时={(time.time()-t0)/60:.1f} 分钟", flush=True)


if __name__ == "__main__":
    main()
