"""带宽 / 下载速度压测（bench）——采集系统选型与容量规划的量尺。

面向场景：下载国内外大规模数据集（HuggingFace / LAION / COCO / ModelScope 等）
与爬虫式海量小图时的真实吞吐评估。所有目标字节落 state/collect/bench/<run_id>/
scratch/（跑完默认清理），不触碰数据湖（meta/ 与 blobs/）。

目标注册表（TARGETS，--targets 过滤，--list 打印）：
  overseas（国外）：hf_big（HF LFS 大文件）/ hf_laion_tar（LAION-2B URL 元数据 tar，
                    DataComp 同族的 tar/parquet 元数据场景）/ coco_zip（COCO 官方站）
  domestic（国内/镜像）：hfmirror_big / hfmirror_laion_tar（hf-mirror.com 镜像对比组）/
                    modelscope_big（ModelScope resolve 直链）/ tuna_iso（清华 TUNA，
                    国内带宽基准）
  small（爬虫式小图池）：wikimedia（Commons Special:FilePath 缩略图）/
                    inat（iNaturalist 静态图，best-effort）

压测任务（--tasks 多选，默认 single,ladder）：
  single   每目标单流下载：MB/s、TTFB、字节数
  ladder   同一目标按并发阶梯（--concurrency，默认 1,2,4,8,16）测聚合吞吐，
           自动标注饱和点（较上一级提速 <10%）；大文件用不重叠 Range 段
  chunked  单文件多线程 Range 分段并行（aria2 风格），测单文件极限速度；
           服务端不支持 Range 则回退单流并标记
  mirror   HF 官方站 vs hf-mirror.com 同参数对比，打印镜像相对损耗
  small    爬虫场景画像：小图 URL 池高并发下载，QPS + p50/p95/p99 延迟 + 失败分类

用法：
    python3 collect/bench.py --list
    python3 collect/bench.py --light                          # 省流量档（跨机对比推荐）
    python3 collect/bench.py --targets tuna_iso,hfmirror_big --tasks single,ladder \
        --max-bytes 536870912 --concurrency 1,2,4,8,16
    python3 collect/bench.py --tasks chunked --threads 8
    python3 collect/bench.py --tasks small --small-n 50 --concurrency 16
    python3 collect/bench.py --tasks mirror
    python3 collect/cli.py bench --targets all     # 子命令等价入口

流量预算（每大目标，含全部 5 任务）：
    默认档 ≈ 4GB（512MiB×8 轮）；--light 档 ≈ 80MB（16MiB×5 轮）。
"""

from __future__ import annotations

import os
import sys

# 命名冲突防护（与 bulk.py / stream.py 同契约，但必须先于 concurrent.futures 导入）：
# `python3 collect/bench.py` 时 sys.path[0]=collect/，collect/queue.py 会遮蔽标准库
# queue（concurrent.futures.thread 内部依赖 queue.SimpleQueue，一旦遮蔽会被模块级
# 缓存死）。脚本直跑时包上下文缺失，仅摘 sys.path 不够，用 importlib 按源码路径
# 把真 stdlib queue 装回 sys.modules。
_here = os.path.dirname(os.path.abspath(__file__))
_shadow = [p for p in sys.path if p and os.path.abspath(p) == _here]
for _p in _shadow:
    sys.path.remove(_p)
_shadowed = os.path.join(_here, "queue.py")
if "queue" not in sys.modules or \
        (getattr(sys.modules["queue"], "__file__", "") or "") == _shadowed:
    import importlib.util as _ilu
    _qpath = os.path.join(os.path.dirname(os.__file__), "queue.py")
    _qspec = _ilu.spec_from_file_location("queue", _qpath)
    _qmod = _ilu.module_from_spec(_qspec)
    _qspec.loader.exec_module(_qmod)
    sys.modules["queue"] = _qmod
sys.path[:] = _shadow + sys.path

import argparse
import datetime
import json
import re
import shutil
import signal
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from collect.util import DEFAULT_HEADERS
else:
    from .util import DEFAULT_HEADERS  # 顺带触发 util 的 CA 回退（沙箱 MITM 代理适配）

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

CHUNK = 1 << 20                      # 1MiB 流式块
VALID_TASKS = ("single", "ladder", "chunked", "mirror", "small")

# ---------------------------------------------------------------------------
# 目标注册表：kind=big 为大文件直链（single/ladder/chunked/mirror 可用）；
# kind=small 为小图 URL 池（single/ladder/small 可用）。mirror_of 声明镜像对。
# 新增压测目标在此登记；用户临时清单走 --urls-file（注入为 custom 大文件目标）。
# ---------------------------------------------------------------------------

_HF = "https://huggingface.co/datasets"
_HFM = "https://hf-mirror.com/datasets"

TARGETS = {
    # —— overseas ——
    "hf_big": {
        "region": "overseas", "kind": "big",
        "url": f"{_HF}/nyanko7/danbooru2023/resolve/main/metadata/posts.tar.gz",
        "size_hint_mb": 2870, "desc": "HF LFS 大文件（bulk.py 已验证可达）",
    },
    "hf_laion_tar": {
        "region": "overseas", "kind": "big",
        "url": f"{_HF}/laion/laion2B-en/resolve/main/data/laion2B-en-000000.tar.gz",
        "size_hint_mb": 305, "desc": "LAION-2B URL 元数据 tar（DataComp 同族场景）",
    },
    "coco_zip": {
        "region": "overseas", "kind": "big",
        "url": "https://images.cocodataset.org/zips/val2017.zip",
        "size_hint_mb": 1000, "desc": "COCO 官方站整包",
    },
    # —— domestic / mirror ——
    "hfmirror_big": {
        "region": "domestic", "kind": "big", "mirror_of": "hf_big",
        "url": f"{_HFM}/nyanko7/danbooru2023/resolve/main/metadata/posts.tar.gz",
        "size_hint_mb": 2870, "desc": "hf-mirror.com 镜像（hf_big 对比组）",
    },
    "hfmirror_laion_tar": {
        "region": "domestic", "kind": "big", "mirror_of": "hf_laion_tar",
        "url": f"{_HFM}/laion/laion2B-en/resolve/main/data/laion2B-en-000000.tar.gz",
        "size_hint_mb": 305, "desc": "hf-mirror.com 镜像（hf_laion_tar 对比组）",
    },
    "modelscope_big": {
        "region": "domestic", "kind": "big",
        "url": "https://www.modelscope.cn/models/Qwen/Qwen2.5-0.5B-Instruct/"
               "resolve/master/model.safetensors",
        "size_hint_mb": 988, "desc": "ModelScope resolve 直链（国内模型站代表）",
    },
    "tuna_iso": {
        "region": "domestic", "kind": "big",
        "url": "https://mirrors.tuna.tsinghua.edu.cn/archlinux/iso/latest/"
               "archlinux-x86_64.iso",
        "size_hint_mb": 1100, "desc": "清华 TUNA 镜像（国内带宽基准）",
    },
    # —— small（爬虫式小图池）——
    "wikimedia": {
        "region": "overseas", "kind": "small",
        "desc": "Commons Special:FilePath 缩略图（知名公有领域图，确定性 URL）",
        "urls": [
            "https://commons.wikimedia.org/wiki/Special:FilePath/"
            "Mona_Lisa.jpg?width=1024",
            "https://commons.wikimedia.org/wiki/Special:FilePath/"
            "Girl_with_a_Pearl_Earring.jpg?width=1024",
            "https://commons.wikimedia.org/wiki/Special:FilePath/"
            "Starry_Night_Over_the_Rhone.jpg?width=1024",
            "https://commons.wikimedia.org/wiki/Special:FilePath/"
            "Einstein_1921_by_F_Schmutzer_-_restoration.jpg?width=1024",
            "https://commons.wikimedia.org/wiki/Special:FilePath/"
            "Blue_Marble_2002.png?width=1024",
            "https://commons.wikimedia.org/wiki/Special:FilePath/"
            "Tsunami_by_hokusai_19th_century.jpg?width=1024",
        ],
    },
    "inat": {
        "region": "overseas", "kind": "small",
        "desc": "iNaturalist 静态图（运行时经官方 API 取真实 photo id 后拼直链，确定性可达）",
        "urls": [],   # 由 _fill_inat_pool 现场填充（空池时惰性触发，一次 API 调用）
    },
}

# small 任务的爬虫画像池：wikimedia/inat 小图 + COCO val2017 直链（硬编码常见有效 id；
# 个别失效 id 如实计入失败分类——这正是爬虫场景的真实面貌）。
COCO_SMALL_IDS = (
    "000000039769", "000000289059", "000000522418", "000000184613",
    "000000318219", "000000554625", "000000438017", "000000109976",
    "000000300341", "000000452784", "000000479126", "000000514376",
    "000000045070", "000000045472", "000000295231", "000000153664",
)
COCO_SMALL_URLS = [
    f"https://images.cocodataset.org/val2017/{pid}.jpg" for pid in COCO_SMALL_IDS
]

MIRROR_PAIRS = [("hf_big", "hfmirror_big"),
                ("hf_laion_tar", "hfmirror_laion_tar")]


# ---------------------------------------------------------------------------
# 下载原语（标准库 urllib；压测姿态：不限速、默认不重试、失败如实计数）
# ---------------------------------------------------------------------------

def _with_deadline(fn, hard_sec: float):
    """整体下载硬超时（同 util._run_with_deadline 思路）：open+read 全程计入，
    慢滴答不会无限挂起；到点放弃挂死连接（daemon 线程由对端关闭回收）。"""
    result: list = [None]
    exc: list = [None]
    done = threading.Event()

    def _worker():
        try:
            result[0] = fn()
        except BaseException as e:  # noqa: BLE001
            exc[0] = e
        finally:
            done.set()

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    if not done.wait(max(hard_sec, 1.0)):
        raise TimeoutError(f"下载超过硬超时 {hard_sec:.0f}s（已放弃挂死连接）")
    if exc[0] is not None:
        raise exc[0]
    return result[0]


def _classify(e: Exception) -> str:
    if isinstance(e, TimeoutError):
        return "timeout"
    if isinstance(e, urllib.error.HTTPError):
        if 400 <= e.code < 500:
            return "http_4xx"
        if e.code >= 500:
            return "http_5xx"
        return f"http_{e.code}"
    if isinstance(e, urllib.error.URLError):
        return "conn"
    return "other"


def _open(url: str, timeout: int, offset=None, length=None):
    """发起请求（默认 opener 自动跟随重定向；Special:FilePath 依赖此行为）。
    offset/length 给出时附加 Range 头，由调用方按 206/200 分支处理。"""
    h = dict(DEFAULT_HEADERS)
    h["Accept-Encoding"] = "identity"   # 压测要真实字节数，禁压缩
    if offset is not None:
        end = str(offset + length - 1) if length else ""
        h["Range"] = f"bytes={offset}-{end}"
    req = urllib.request.Request(url, headers=h)
    return urllib.request.urlopen(req, timeout=timeout)


def stream_download(name: str, url: str, fname: str, sink_dir, *,
                    offset=None, limit=None, timeout=60, retries=0) -> dict:
    """流式下载单个任务单元到 sink_dir/fname（sink_dir=None 时弃字节只计速）。
    返回指标记录；失败不抛异常，ok=False + error 分类，交由上层统计。"""
    rec = {"target": name, "url": url, "ok": False, "status": None,
           "bytes": 0, "ttfb_ms": None, "elapsed_ms": None, "mbps": None,
           "range": None, "error": None}
    path = os.path.join(sink_dir, fname) if sink_dir else None

    def _do():
        t0 = time.monotonic()
        resp = _open(url, timeout, offset, limit)
        rec["status"] = resp.status
        if offset is not None:
            rec["range"] = resp.headers.get("Content-Range") or f"req {offset}+"
        f = open(path, "wb") if path else None
        got = 0
        ttfb = None
        try:
            while True:
                want = CHUNK if limit is None else min(CHUNK, limit - got)
                if want <= 0:
                    break
                chunk = resp.read(want)
                if not chunk:
                    break
                if ttfb is None:
                    ttfb = (time.monotonic() - t0) * 1000
                if f:
                    f.write(chunk)
                got += len(chunk)
        finally:
            resp.close()
            if f:
                f.close()
        el = (time.monotonic() - t0) * 1000
        rec.update(ok=True, bytes=got,
                   ttfb_ms=round(ttfb if ttfb is not None else el, 1),
                   elapsed_ms=round(el, 1),
                   mbps=round(got / 1048576 / (el / 1000), 2) if el > 0 else None)
        return rec

    last = None
    for attempt in range(retries + 1):
        try:
            return _with_deadline(_do, max(45.0, float(timeout) * 3))
        except Exception as e:  # noqa: BLE001
            last = e
            if attempt < retries:
                time.sleep(min(2 ** (attempt + 1), 15))
    rec["error"] = _classify(last)
    return rec


def probe_range(url: str, timeout: int) -> tuple[bool, int]:
    """探测服务端 Range 支持：bytes=0-0 试探；206=支持并解析总长，416 也算支持。
    返回 (supports_range, total_size_or_None)。全程硬超时，黑洞主机不会挂死。"""

    def _do():
        try:
            resp = _open(url, timeout, offset=0, length=1)
            status = resp.status
            cr = resp.headers.get("Content-Range") or ""
            try:
                resp.read()
            finally:
                resp.close()
            m = re.search(r"/(\d+)\s*$", cr)
            return status == 206, (int(m.group(1)) if m else None)
        except urllib.error.HTTPError as e:
            if e.code == 416:
                return True, None
            return False, None

    try:
        return _with_deadline(_do, max(45.0, float(timeout) * 3))
    except Exception:  # noqa: BLE001
        return False, None


def run_parallel(items: list[dict], concurrency: int, sink_dir,
                 timeout: int, retries: int) -> tuple[list[dict], float]:
    """并发执行下载任务单元（items: {name,url,fname,offset,limit}）。
    返回 (记录列表, 墙钟秒)。并发=1 时同样走池（代码路径统一）。"""
    t0 = time.monotonic()
    recs: list[dict] = []
    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as ex:
        futs = [
            ex.submit(stream_download, it["name"], it["url"], it["fname"],
                      sink_dir, offset=it.get("offset"), limit=it.get("limit"),
                      timeout=timeout, retries=retries)
            for it in items
        ]
        for i, fu in enumerate(futs):
            r = fu.result()
            tag = "ok " if r["ok"] else "ERR"
            print(f"    [{tag}] {r['target']} #{i} "
                  f"{r['bytes'] / 1048576:.1f}MiB "
                  f"{(r['mbps'] if r['mbps'] is not None else 0):.2f}MB/s"
                  f"{(' err=' + r['error']) if r['error'] else ''}", flush=True)
            recs.append(r)
    return recs, time.monotonic() - t0


def _agg(recs: list[dict], wall: float) -> dict:
    ok = [r for r in recs if r["ok"]]
    total = sum(r["bytes"] for r in ok)
    return {
        "units": len(recs), "ok": len(ok),
        "bytes": total, "wall_sec": round(wall, 2),
        "agg_mbps": round(total / 1048576 / wall, 2) if wall > 0 else None,
        "success_rate": round(len(ok) / len(recs), 3) if recs else None,
    }


# ---------------------------------------------------------------------------
# 五种压测任务
# ---------------------------------------------------------------------------

def _big_units(tname: str, url: str, n: int, max_bytes: int,
               range_ok: bool) -> list[dict]:
    """把 [0, max_bytes) 切成 n 个不重叠任务单元；服务端不支持 Range 时
    各单元退化为"从头读 seg 字节"（200 流式截断，聚合吞吐仍可比）。"""
    seg = max_bytes // n
    units = []
    for i in range(n):
        off = i * seg
        lim = max_bytes - off if i == n - 1 else seg
        units.append({
            "name": tname, "url": url, "fname": f"{tname}_{i}.part",
            "offset": off if range_ok else None,
            "limit": lim,
        })
    return units


def _ensure_pool(name: str, t: dict, timeout: int) -> bool:
    """小图目标 URL 池就绪检查：inat 空池时惰性填充；就绪返回 True。"""
    if t["kind"] != "small":
        return True
    if not t["urls"] and name == "inat":
        _fill_inat_pool(timeout)
    return bool(t["urls"])


def task_single(sel: dict, opts, scratch) -> dict:
    """每目标单流下载：大文件读到 max_bytes，小图池逐张串行。"""
    rows = []
    for name, t in sel.items():
        if t["kind"] == "big":
            print(f"[single] {name}: {t['url']}", flush=True)
            range_ok, _ = probe_range(t["url"], opts.timeout)
            recs, wall = run_parallel(
                _big_units(name, t["url"], 1, opts.max_bytes, range_ok),
                1, scratch, opts.timeout, opts.retries)
            a = _agg(recs, wall)
            r = recs[0]
            rows.append({"target": name, "region": t["region"],
                         "mbps": a["agg_mbps"], "ttfb_ms": r["ttfb_ms"],
                         "bytes": a["bytes"], "elapsed_ms": r["elapsed_ms"],
                         "ok": r["ok"], "error": r["error"]})
        else:
            if not _ensure_pool(name, t, opts.timeout):
                print(f"[single] {name}: URL 池为空，跳过", flush=True)
                continue
            print(f"[single] {name}: {len(t['urls'])} 张小图串行", flush=True)
            items = [{"name": name, "url": u, "fname": f"{name}_{i}.part"}
                     for i, u in enumerate(t["urls"])]
            recs, wall = run_parallel(items, 1, scratch, opts.timeout, opts.retries)
            a = _agg(recs, wall)
            rows.append({"target": name, "region": t["region"],
                         "mbps": a["agg_mbps"], "ttfb_ms": None,
                         "bytes": a["bytes"], "elapsed_ms": round(wall * 1000, 1),
                         "ok": a["success_rate"] == 1.0, "error": None,
                         "success_rate": a["success_rate"]})
    return {"rows": rows}


def task_ladder(sel: dict, opts, scratch) -> dict:
    """并发阶梯扫描：每级并发下载合计 max_bytes（大文件 Range 分段 / 小图池取件），
    聚合吞吐 < 上一级 1.1 倍即标注饱和点。"""
    out = {}
    levels = sorted(set(opts.levels))
    for name, t in sel.items():
        if t["kind"] == "small" and not _ensure_pool(name, t, opts.timeout):
            print(f"[ladder] {name}: URL 池为空，跳过", flush=True)
            continue
        print(f"[ladder] {name}: 并发阶梯 {levels}", flush=True)
        range_ok = None
        if t["kind"] == "big":
            range_ok, _ = probe_range(t["url"], opts.timeout)
        curve = []
        prev = None
        for c in levels:
            if t["kind"] == "big":
                units = _big_units(name, t["url"], c, opts.max_bytes, bool(range_ok))
            else:
                urls = t["urls"]
                units = [{"name": name, "url": urls[i % len(urls)],
                          "fname": f"{name}_c{c}_{i}.part"} for i in range(c)]
            print(f"  并发 {c} ...", flush=True)
            recs, wall = run_parallel(units, c, scratch, opts.timeout, opts.retries)
            a = _agg(recs, wall)
            a["concurrency"] = c
            a["saturated"] = bool(prev is not None and a["agg_mbps"] is not None
                                  and a["agg_mbps"] < prev * 1.1)
            curve.append(a)
            prev = a["agg_mbps"] if a["agg_mbps"] is not None else prev
        sat = next((lv["concurrency"] for lv in curve if lv["saturated"]), None)
        out[name] = {"region": t["region"], "kind": t["kind"],
                     "range_supported": range_ok, "curve": curve,
                     "saturation_at": sat}
    return out


def task_chunked(sel: dict, opts, scratch) -> dict:
    """单文件 --threads 线程 Range 分段并行（aria2 风格）；不支持 Range 回退单流。"""
    rows = []
    for name, t in sel.items():
        if t["kind"] != "big":
            continue
        print(f"[chunked] {name}: {opts.threads} 线程分段", flush=True)
        range_ok, total = probe_range(t["url"], opts.timeout)
        limit = opts.max_bytes
        if total:
            limit = min(limit, total)
        if range_ok:
            units = _big_units(name, t["url"], opts.threads, limit, True)
        else:
            print(f"  [warn] {name} 不支持 Range，回退单流", flush=True)
            units = _big_units(name, t["url"], 1, limit, False)
        recs, wall = run_parallel(units, opts.threads if range_ok else 1,
                                  scratch, opts.timeout, opts.retries)
        a = _agg(recs, wall)
        rows.append({"target": name, "region": t["region"],
                     "threads": opts.threads if range_ok else 1,
                     "range_supported": range_ok, "total_size": total,
                     **a})
    return {"rows": rows}


def task_mirror(sel: dict, opts, scratch) -> dict:
    """HF 官方站 vs hf-mirror.com 同参数单流对比；损耗以镜像为基准。"""
    rows = []
    for orig, mirror in MIRROR_PAIRS:
        if orig not in sel or mirror not in sel:
            print(f"[mirror] 跳过 {orig} vs {mirror}（未同时选中）", flush=True)
            continue
        print(f"[mirror] {orig} vs {mirror}", flush=True)
        pair = {}
        for name in (orig, mirror):
            t = TARGETS[name]
            range_ok, _ = probe_range(t["url"], opts.timeout)
            recs, wall = run_parallel(
                _big_units(name, t["url"], 1, opts.max_bytes, range_ok),
                1, scratch, opts.timeout, opts.retries)
            pair[name] = _agg(recs, wall)["agg_mbps"]
        o, m = pair[orig], pair[mirror]
        loss = round((1 - o / m) * 100, 1) if (o and m) else None
        rows.append({"orig": orig, "mirror": mirror,
                     "orig_mbps": o, "mirror_mbps": m,
                     "loss_vs_mirror_pct": loss})
    return {"rows": rows}


def _fill_inat_pool(timeout: int):
    """inat 小图池惰性填充：一次 observations API 调用取带照片的观测，
    拼 static.inaturalist.org/photos/<id>/medium.jpg 直链（确定性可达）。
    失败不致命：池保持空，该目标在统计中自然缺席。"""
    urls = TARGETS["inat"]["urls"]
    if urls:
        return
    try:
        resp = _open("https://api.inaturalist.org/v1/observations"
                     "?photos=true&per_page=30&order=desc&order_by=id", timeout)
        doc = json.loads(resp.read().decode("utf-8", "replace"))
        resp.close()
        for ob in doc.get("results") or []:
            for ph in ob.get("photos") or []:
                u = (ph.get("url") or "").replace("square", "medium")
                if u.startswith("https://"):
                    urls.append(u)
        print(f"[small] inat 池填充 {len(urls)} 条（API 现场取件）", flush=True)
    except Exception as e:  # noqa: BLE001
        print(f"[warn] inat 池填充失败（不影响其它目标）: {e}", flush=True)


def _small_pool(sel: dict, custom_urls: list, timeout: int) -> list[tuple[str, str]]:
    if sel.get("inat"):
        _fill_inat_pool(timeout)
    pool = []
    for name, t in sel.items():
        if t["kind"] == "small":
            pool += [(name, u) for u in t["urls"]]
    # COCO val2017 直链固定并入池（不依赖选中的目标，两边机器一致）
    pool += [("coco_small", u) for u in COCO_SMALL_URLS]
    pool += [("custom", u) for u in custom_urls]
    return pool


def _percentile(sorted_vals: list, p: float):
    if not sorted_vals:
        return None
    k = min(len(sorted_vals) - 1, int(round(p * (len(sorted_vals) - 1))))
    return sorted_vals[k]


def task_small(sel: dict, opts, scratch, custom_urls: list) -> dict:
    """爬虫场景画像：小图池取 --small-n 件，以阶梯最大并发下载；
    统计 QPS / p50/p95/p99 完成延迟 / 失败分类。"""
    pool = _small_pool(sel, custom_urls, opts.timeout)
    if not pool:
        print("[small] 无可用小图池（选中小图目标或提供 --urls-file）", flush=True)
        return {}
    n = opts.small_n
    picks = [pool[i % len(pool)] for i in range(n)]
    conc = max(opts.levels)
    print(f"[small] {n} 张小图 @ 并发 {conc}（池 {len(pool)} 条）", flush=True)
    items = [{"name": nm, "url": u, "fname": f"small_{i}.part"}
             for i, (nm, u) in enumerate(picks)]
    recs, wall = run_parallel(items, conc, scratch, opts.timeout, opts.retries)
    ok = [r for r in recs if r["ok"]]
    lat = sorted(r["elapsed_ms"] for r in ok)
    fails: dict = {}
    for r in recs:
        if not r["ok"]:
            fails[r["error"]] = fails.get(r["error"], 0) + 1
    return {
        "n": n, "concurrency": conc, "wall_sec": round(wall, 2),
        "ok": len(ok), "qps": round(len(ok) / wall, 2) if wall > 0 else None,
        "latency_ms": {
            "p50": _percentile(lat, 0.50), "p95": _percentile(lat, 0.95),
            "p99": _percentile(lat, 0.99),
        },
        "failures": fails, "records": recs,
    }


# ---------------------------------------------------------------------------
# 报告（终端 Markdown 表 + report.json）
# ---------------------------------------------------------------------------

def _fmt(x, suffix=""):
    return "-" if x is None else f"{x}{suffix}"


def print_report(results: dict):
    if "single" in results:
        print("\n## single（单流测速）")
        print("| 目标 | 区域 | MB/s | TTFB(ms) | 字节 | 耗时(ms) | 结果 |")
        print("|---|---|---|---|---|---|---|")
        for r in results["single"]["rows"]:
            print(f"| {r['target']} | {r['region']} | {_fmt(r['mbps'])} "
                  f"| {_fmt(r['ttfb_ms'])} | {r['bytes']} | {_fmt(r['elapsed_ms'])} "
                  f"| {'ok' if r['ok'] else r['error']} |")
    if "ladder" in results:
        print("\n## ladder（并发阶梯，聚合吞吐 MB/s）")
        for name, lv in results["ladder"].items():
            curve = " -> ".join(
                f"{c['concurrency']}:{_fmt(c['agg_mbps'])}" for c in lv["curve"])
            print(f"- {name}: {curve}"
                  + (f"（饱和点≈{lv['saturation_at']} 并发）" if lv["saturation_at"]
                     else ""))
    if "chunked" in results:
        print("\n## chunked（单文件分段并行）")
        print("| 目标 | 线程 | Range | 聚合 MB/s | 字节 | 墙钟(s) |")
        print("|---|---|---|---|---|---|")
        for r in results["chunked"]["rows"]:
            print(f"| {r['target']} | {r['threads']} "
                  f"| {'是' if r['range_supported'] else '否'} "
                  f"| {_fmt(r['agg_mbps'])} | {r['bytes']} | {r['wall_sec']} |")
    if "mirror" in results:
        print("\n## mirror（镜像对比）")
        print("| 官方站 | MB/s | 镜像 | MB/s | 官方站相对损耗 |")
        print("|---|---|---|---|---|")
        for r in results["mirror"]["rows"]:
            print(f"| {r['orig']} | {_fmt(r['orig_mbps'])} | {r['mirror']} "
                  f"| {_fmt(r['mirror_mbps'])} | {_fmt(r['loss_vs_mirror_pct'], '%')} |")
    if "small" in results and results["small"]:
        s = results["small"]
        lat = s["latency_ms"]
        print("\n## small（爬虫小图画像）")
        print(f"- 件数 {s['n']} @ 并发 {s['concurrency']}：成功 {s['ok']}/"
              f"{s['n']}，QPS {_fmt(s['qps'])}，墙钟 {s['wall_sec']}s")
        print(f"- 完成延迟(ms)：p50 {_fmt(lat['p50'])} / p95 {_fmt(lat['p95'])} "
              f"/ p99 {_fmt(lat['p99'])}")
        if s["failures"]:
            print(f"- 失败分类：{s['failures']}")


def _load_urls_file(path: str) -> list[str]:
    """自定义 URL 清单：纯文本一行一 URL，或 JSONL（取 url 字段）。"""
    urls = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("{"):
                try:
                    doc = json.loads(line)
                    if doc.get("url"):
                        urls.append(doc["url"])
                        continue
                except json.JSONDecodeError:
                    pass
            if line.startswith("http"):
                urls.append(line)
    return urls


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv=None):
    argv = sys.argv[1:] if argv is None else list(argv)
    ap = argparse.ArgumentParser(
        prog="bench",
        description="带宽/下载速度压测：国内外大规模数据集与爬虫场景"
                    "（字节落 scratch 跑完即清，不触碰数据湖）")
    ap.add_argument("--list", action="store_true",
                    help="打印目标注册表后退出，不压测")
    ap.add_argument("--targets", default="all",
                    help="目标名逗号分隔（如 tuna_iso,hfmirror_big）；all=全部（默认）")
    ap.add_argument("--urls-file", default=None,
                    help="自定义 URL 清单（纯文本或 JSONL 含 url 字段）：注入 custom "
                         "大文件目标，并补充 small 任务 URL 池")
    ap.add_argument("--tasks", default="single,ladder",
                    help=f"任务逗号分隔，可选 {','.join(VALID_TASKS)}（默认 single,ladder）")
    ap.add_argument("--light", action="store_true",
                    help="省流量档：16MiB 限额 + 3 级阶梯(1,4,16) + 20 张小图，"
                         "每目标总流量从 ~4GB 降到 ~80MB（跨机对比推荐；"
                         "显式给出的 --max-bytes/--concurrency/--small-n 优先）")
    ap.add_argument("--max-bytes", type=int, default=None,
                    help="每目标压测字节上限（默认 512MiB；--light 时 16MiB；"
                         "Range 限量，防烧磁盘）")
    ap.add_argument("--concurrency", default=None,
                    help="ladder 并发阶梯逗号分隔（默认 1,2,4,8,16；--light 时 1,4,16）；"
                         "small 任务取其中最大值")
    ap.add_argument("--threads", type=int, default=8,
                    help="chunked 任务的单文件分段线程数（默认 8）")
    ap.add_argument("--small-n", type=int, default=None,
                    help="small 任务下载的小图件数（默认 50；--light 时 20）")
    ap.add_argument("--timeout", type=int, default=60,
                    help="单次下载 socket 超时秒数（硬超时为其 3 倍，默认 60）")
    ap.add_argument("--retries", type=int, default=0,
                    help="失败重试次数（默认 0：失败如实计入失败率）")
    ap.add_argument("--keep", action="store_true",
                    help="保留 scratch 下载字节（默认跑完即删）")
    ap.add_argument("--run-id", default=None,
                    help="本次压测 ID（默认时间戳）；产物在 state/collect/bench/<run-id>")
    args = ap.parse_args(argv)

    if args.list:
        print(f"{'名称':<22}{'区域':<10}{'类型':<7}{'提示大小':<10}说明")
        for n, t in TARGETS.items():
            hint = (f"{t['size_hint_mb']}MB" if "size_hint_mb" in t
                    else (f"{len(t['urls'])}urls" if t.get("urls") else "惰性填充"))
            print(f"{n:<22}{t['region']:<10}{t['kind']:<7}{hint:<10}{t['desc']}")
        print("\n镜像对比组：" + "; ".join(f"{a} vs {b}" for a, b in MIRROR_PAIRS))
        return

    tasks = [t.strip() for t in args.tasks.split(",") if t.strip()]
    bad = [t for t in tasks if t not in VALID_TASKS]
    if bad:
        ap.error(f"未知任务 {bad}；可选 {VALID_TASKS}")
    # 档位解析：显式参数 > --light 档 > 默认档
    max_bytes = (args.max_bytes if args.max_bytes is not None
                 else (16 * 1024 * 1024 if args.light else 512 * 1024 * 1024))
    conc_str = (args.concurrency if args.concurrency is not None
                else ("1,4,16" if args.light else "1,2,4,8,16"))
    small_n = (args.small_n if args.small_n is not None
               else (20 if args.light else 50))
    try:
        levels = sorted({int(x) for x in conc_str.split(",") if x.strip()})
    except ValueError:
        ap.error("--concurrency 需为逗号分隔整数，如 1,2,4,8,16")
    if not levels:
        ap.error("--concurrency 至少一个值")

    # 目标选择
    custom_urls = _load_urls_file(args.urls_file) if args.urls_file else []
    targets = dict(TARGETS)
    for i, u in enumerate(custom_urls):
        targets[f"custom_{i}"] = {"region": "custom", "kind": "big", "url": u,
                                  "desc": "来自 --urls-file"}
    if args.targets.strip().lower() != "all":
        names = [s.strip() for s in args.targets.split(",") if s.strip()]
        unknown = [n for n in names if n not in targets]
        if unknown:
            ap.error(f"未知目标 {unknown}；可用：{sorted(targets)}")
        sel = {n: targets[n] for n in names}
    else:
        sel = targets
    # ladder/chunked 只对 big 目标有意义；single/small 自会按 kind 过滤
    if not any(t["kind"] == "big" for t in sel.values()) and \
            set(tasks) - {"small"}:
        print("[warn] 选中目标中没有大文件目标，big 类任务将无输出", flush=True)

    # 运行目录（AGENTS.md 2.3：运行时状态在顶层 state/，不入 git）
    run_id = args.run_id or datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = os.path.join(REPO_ROOT, "state", "collect", "bench", run_id)
    scratch = os.path.join(run_dir, "scratch")
    os.makedirs(scratch, exist_ok=True)

    opts = argparse.Namespace(max_bytes=max_bytes, levels=levels,
                              threads=args.threads, small_n=small_n,
                              timeout=args.timeout, retries=args.retries)
    print(f"[bench] run={run_id} 目标={list(sel)} 任务={tasks} "
          f"max_bytes={max_bytes} 阶梯={levels} light={args.light}", flush=True)

    results: dict = {}
    started = datetime.datetime.now().isoformat(timespec="seconds")
    t0 = time.monotonic()

    def _raise_intr(signum, frame):  # noqa: ARG001
        raise KeyboardInterrupt(f"收到信号 {signum}，提前收尾")

    # 外部 kill（timeout 命令等）发 SIGTERM 时也走收尾路径，部分报告不丢
    signal.signal(signal.SIGTERM, _raise_intr)
    try:
        if "single" in tasks:
            results["single"] = task_single(sel, opts, scratch)
        if "ladder" in tasks:
            results["ladder"] = task_ladder(sel, opts, scratch)
        if "chunked" in tasks:
            results["chunked"] = task_chunked(sel, opts, scratch)
        if "mirror" in tasks:
            results["mirror"] = task_mirror(sel, opts, scratch)
        if "small" in tasks:
            results["small"] = task_small(sel, opts, scratch, custom_urls)
    except KeyboardInterrupt as e:
        print(f"\n[bench] 中断（{e}），已完成任务的结果仍写入报告", flush=True)
    finally:
        report = {
            "run_id": run_id, "started_at": started,
            "wall_sec": round(time.monotonic() - t0, 1),
            "args": {"targets": list(sel), "tasks": tasks,
                     "max_bytes": max_bytes, "levels": levels,
                     "threads": args.threads, "small_n": small_n,
                     "timeout": args.timeout, "retries": args.retries,
                     "light": args.light},
            "results": results,
        }
        with open(os.path.join(run_dir, "report.json"), "w",
                  encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=1)
        print_report(results)
        print(f"\n[bench] 报告: {os.path.join(run_dir, 'report.json')}", flush=True)
        if args.keep:
            print(f"[bench] scratch 保留于 {scratch}", flush=True)
        else:
            shutil.rmtree(scratch, ignore_errors=True)
            print("[bench] scratch 已清理", flush=True)


if __name__ == "__main__":
    main()
