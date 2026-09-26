#!/usr/bin/env python3
"""重跑选定分类体系 · Step1: 扫描最新 canonical 账本, 产出每 QID 图计数与全局统计.

输入: sample_1m_transfer/assets/wh_backfill/images.v2.jsonl.gz (2026-09-25 wh 补账版,
      一图一行, qids 为列表; 该文件是当前唯一权威口径, 旧跑数基于 09-23 版 16.0M 行).
输出:
  /dev/shm/rerun_edges/rerun_edges_<pid>.txt  每 (qid,图) 边行 "qid\tshort\tsize_bytes",
                                              供 Step2 聚合; 放共享内存避免落盘
  本目录 scan_stats_v2.json                    全局行级统计(行数/短边行/来源/tier 等)

口径: short = min(width,height) >= 1024 (与采样 v3 一致);
      宽高缺失的行计 short=0 但仍入 n_all; parse 失败行单独计数不中断.
"""
import collections
import json
import os
import subprocess
import sys
import time
from multiprocessing import Pool

SRC = sys.argv[1] if len(sys.argv) > 1 else "/yzp/zhaozy/yangzepeng/0905/demiwtg/collect/sample_1m_transfer/assets/wh_backfill/images.v2.jsonl.gz"
SHM = sys.argv[2] if len(sys.argv) > 2 else "/dev/shm/rerun_edges"
TAG = sys.argv[3] if len(sys.argv) > 3 else ""
OUT = "/yzp/zhaozy/yangzepeng/0905/demiwtg/collect/rerun_v2_20260925"
CHUNK = 32 << 20          # 主进程按 32MB 文本块分发, 保证行完整
NPROC = 48

_fh = None                 # worker 内按 pid 命名的边文件句柄, 进程存活期间复用


def _wfh():
    """惰性打开本进程的边文件(按 pid 命名, 避免多进程写同一文件)."""
    global _fh
    if _fh is None:
        os.makedirs(SHM, exist_ok=True)
        _fh = open(f"{SHM}/rerun_edges_{os.getpid()}.txt", "w", buffering=1 << 22)
    return _fh


def process(chunk):
    """解析一个完整文本块: 逐行 json.loads, 累计小块统计并把 qid 边写入本进程文件."""
    st = {"rows": 0, "rows_short": 0, "rows_nowh": 0, "rows_emptyq": 0,
          "edges": 0, "edges_short": 0, "bad": 0, "bytes_short": 0,
          "src": collections.Counter(), "tier": collections.Counter(),
          "fix": collections.Counter()}
    buf = []
    ap = buf.append
    for line in chunk.splitlines():
        if not line:
            continue
        st["rows"] += 1
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            st["bad"] += 1
            continue
        w, h = r.get("width"), r.get("height")
        if not w or not h:
            short = 0
            st["rows_nowh"] += 1
        else:
            short = 1 if min(w, h) >= 1024 else 0
            st["rows_short"] += short
        qids = r.get("qids") or []
        if not qids:
            st["rows_emptyq"] += 1
        sz = r.get("size_bytes") or 0
        if short:
            st["edges_short"] += len(qids)
            st["bytes_short"] += sz
        st["edges"] += len(qids)
        st["src"][r.get("source") or "?"] += 1
        st["tier"][r.get("tier") or "?"] += 1
        st["fix"][r.get("fix_state") or "?"] += 1
        for q in qids:
            ap(f"{q}\t{short}\t{sz}\n")
    fh = _wfh()
    fh.writelines(buf)      # 一次性落本进程边文件
    fh.flush()              # 必须显式刷: pool terminate 不保证走关闭钩子, 缓冲会丢
    st["src"] = dict(st["src"])
    st["tier"] = dict(st["tier"])
    st["fix"] = dict(st["fix"])
    return st


def main():
    t0 = time.time()
    os.makedirs(OUT, exist_ok=True)
    os.makedirs(SHM, exist_ok=True)
    for f in os.listdir(SHM):  # 清上次残留, 防重复计数
        os.remove(os.path.join(SHM, f))

    # pigz 多核解压, 主进程切完整行块, 分发给进程池
    proc = subprocess.Popen(["pigz", "-dc", SRC], stdout=subprocess.PIPE,
                            bufsize=1 << 24)

    def gen():
        rem = b""
        while True:
            blk = proc.stdout.read(CHUNK)
            if not blk:
                break
            if rem:
                blk = rem + blk
            i = blk.rfind(b"\n")
            if i < 0:
                rem = blk
                continue
            rem, piece = blk[i + 1:], blk[:i]
            yield piece
        if rem.strip():
            yield rem

    total = {"rows": 0, "rows_short": 0, "rows_nowh": 0, "rows_emptyq": 0,
             "edges": 0, "edges_short": 0, "bad": 0, "bytes_short": 0,
             "src": collections.Counter(), "tier": collections.Counter(),
             "fix": collections.Counter()}
    n_chunks = 0
    with Pool(NPROC) as pool:
        for st in pool.imap_unordered(process, gen(), chunksize=1):
            n_chunks += 1
            for k in ("rows", "rows_short", "rows_nowh", "rows_emptyq",
                      "edges", "edges_short", "bad", "bytes_short"):
                total[k] += st[k]
            total["src"].update(st["src"])
            total["tier"].update(st["tier"])
            total["fix"].update(st["fix"])
            if n_chunks % 50 == 0:
                print(f"  ..{n_chunks} chunks rows={total['rows']:,} "
                      f"{time.time()-t0:.0f}s", flush=True)
    proc.stdout.close()
    proc.wait()

    total["src"] = dict(total["src"])
    total["tier"] = dict(total["tier"])
    total["fix"] = dict(total["fix"])
    import gzip
    with open(f"{OUT}/scan_stats_v2{TAG}.json", "w") as f:
        json.dump(total, f, indent=1)
    print(f"[done] chunks={n_chunks} rows={total['rows']:,} "
          f"edges={total['edges']:,} short_rows={total['rows_short']:,} "
          f"elapsed={time.time()-t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
