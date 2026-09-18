#!/usr/bin/env python3
"""SDC fetch ①-a: 清单生成各阶段(跑在 r1,3GB 内存安全设计)。

阶段与产物(/tmp/sdc_manifest/,均 LC_ALL=C 字节序):
  s0_filter  sdc_depicts × 概念集 → hit.tsv (mid\tqid\trank)
  s2_filter  mid_to_file × hit_mids(bisect) → m2f_hit.tsv (mid\tfname)
  s4_quota   按 qid 分组、fname 升序取 ≤30、去重 → quota.tsv (fname\tmid\tqid\trank)
  s5_have    qid_images 的 commons_file(空格→下划线) → stdout
  s7_group   预筛后按 fname 分组 → fetch_list.tsv + funnel 统计
配 GNU sort/join 由 run_manifest.sh 编排,本文件只做必须 Python 的环节。
"""
import gzip
import json
import sys
import time
from bisect import bisect_left

RAW = "/lhcos-data/demiwtg-data/datasets/raw/wikimedia"
KB = "/lhcos-data/demiwtg-data/datasets/demiwtg/kb"


def opengz(path):
    return gzip.open(path, "rt", encoding="utf-8", errors="surrogateescape")


def s0_filter(out_path):
    concepts = set()
    with opengz(f"{KB}/qid_concepts.jsonl.gz") as f:
        for line in f:
            try:
                concepts.add(json.loads(line)["qid"])
            except Exception:
                continue
    print(f"[s0] concepts={len(concepts):,}", flush=True)
    n_tot = n_hit = 0
    t0 = time.time()
    with opengz(f"{RAW}/sdc_depicts.tsv.gz") as f, \
            open(out_path, "w", encoding="utf-8",
                 errors="surrogateescape") as out:
        for line in f:
            n_tot += 1
            parts = line.rstrip("\n").split("\t", 3)
            if len(parts) >= 2 and parts[1] in concepts:
                out.write(f"{parts[0]}\t{parts[1]}\t{parts[2] if len(parts) > 2 else 'normal'}\n")
                n_hit += 1
            if n_tot % 10_000_000 == 0:
                el = time.time() - t0
                print(f"[s0] {n_tot:,} edges hit={n_hit:,} "
                      f"({n_tot/el:,.0f}/s)", flush=True)
    print(f"[s0] DONE edges={n_tot:,} hit_edges={n_hit:,} "
          f"concepts={len(concepts):,}", flush=True)


def s2_filter(mids_path, out_path):
    mids = []
    with open(mids_path, encoding="utf-8") as f:
        for line in f:
            mids.append(line.rstrip("\n"))
    print(f"[s2] hit_mids={len(mids):,}", flush=True)
    n_tot = n_out = 0
    t0 = time.time()
    with opengz(f"{RAW}/mid_to_file.tsv.gz") as f, \
            open(out_path, "w", encoding="utf-8",
                 errors="surrogateescape") as out:
        for line in f:
            n_tot += 1
            mid, _, fname = line.rstrip("\n").partition("\t")
            i = bisect_left(mids, mid)
            if i < len(mids) and mids[i] == mid:
                out.write(f"{mid}\t{fname}\n")
                n_out += 1
            if n_tot % 20_000_000 == 0:
                el = time.time() - t0
                print(f"[s2] {n_tot:,} rows out={n_out:,} "
                      f"({n_tot/el:,.0f}/s)", flush=True)
    print(f"[s2] DONE rows={n_tot:,} out={n_out:,}", flush=True)


RANK_PRIO = {"preferred": 0, "normal": 1, "deprecated": 2}


def s4_quota(in_path, out_path, quota):
    """输入按 (qid,fname) 排序的 mid\tqid\trank\tfname,输出 fname\tmid\tqid\trank。

    同 (qid,fname) 排序后相邻:同组多行(不同 rank/不同 mid)只保留
    rank 优先级最高的一行;每 qid 按 fname 升序截前 quota 个。
    """
    n_in = n_out = 0
    qids_seen = 0
    cur_qid = None
    cur_n = 0
    cur_key = None       # (fname) 当前去重键
    best = None          # (prio, mid, rank) 当前组最优
    t0 = time.time()

    def emit():
        return f"{cur_key}\t{best[1]}\t{cur_qid}\t{best[2]}\n"

    with open(in_path, encoding="utf-8", errors="surrogateescape") as f, \
            open(out_path, "w", encoding="utf-8", errors="surrogateescape") as out:
        for line in f:
            mid, qid, rank, fname = line.rstrip("\n").split("\t")
            n_in += 1
            if qid != cur_qid or fname != cur_key:
                if best is not None and cur_n <= quota:
                    out.write(emit())
                    n_out += 1
                if qid != cur_qid:
                    cur_qid = qid
                    cur_n = 0
                    qids_seen += 1
                cur_key = fname
                best = (RANK_PRIO.get(rank, 1), mid, rank)
                cur_n += 1
            else:
                p = RANK_PRIO.get(rank, 1)
                if p < best[0]:
                    best = (p, mid, rank)
            if n_in % 10_000_000 == 0:
                el = time.time() - t0
                print(f"[s4] {n_in:,} in out={n_out:,} qids={qids_seen:,} "
                      f"({n_in/el:,.0f}/s)", flush=True)
        if best is not None and cur_n <= quota:
            out.write(emit())
            n_out += 1
    print(f"[s4] DONE in={n_in:,} out={n_out:,} qids={qids_seen:,} "
          f"quota={quota}", flush=True)


def s5_have():
    n = 0
    out = sys.stdout
    with opengz(f"{KB}/qid_images.jsonl.gz") as f:
        for line in f:
            try:
                fn = json.loads(line)["commons_file"]
            except Exception:
                continue
            out.write(fn.replace(" ", "_") + "\n")
            n += 1
    print(f"[s5] have_rows={n:,}", file=sys.stderr, flush=True)


def s7_group(in_path, out_path, stats_path):
    """输入按 fname 排序的 fname\tmid\tqid\trank\tsize,聚合 qid:rank 列表。"""
    n_edges = n_files = 0
    bytes_sum = 0
    qids = set()
    t0 = time.time()
    out = open(out_path, "w", encoding="utf-8", errors="surrogateescape")
    with open(in_path, encoding="utf-8", errors="surrogateescape") as f:
        cur = None   # [fname, mid, size, [(qid,rank)...]]
        for line in f:
            fname, mid, qid, rank, size = line.rstrip("\n").split("\t")
            n_edges += 1
            if cur is None or fname != cur[0]:
                if cur is not None:
                    qstr = ",".join(f"{q}:{r}" for q, r in cur[3])
                    out.write(f"{cur[0]}\t{cur[1]}\t{qstr}\t{cur[2]}\n")
                    n_files += 1
                cur = [fname, mid, size, []]
            cur[3].append((qid, rank))
            qids.add(qid)
            bytes_sum += int(size)
            if n_edges % 5_000_000 == 0:
                el = time.time() - t0
                print(f"[s7] {n_edges:,} edges files={n_files:,} "
                      f"({n_edges/el:,.0f}/s)", flush=True)
        if cur is not None:
            qstr = ",".join(f"{q}:{r}" for q, r in cur[3])
            out.write(f"{cur[0]}\t{cur[1]}\t{qstr}\t{cur[2]}\n")
            n_files += 1
    out.close()
    stats = {"files": n_files, "edges": n_edges, "qids": len(qids),
             "gb": round(bytes_sum / 1e9, 1)}
    with open(stats_path, "w") as f:
        json.dump(stats, f, indent=1)
    print(f"[s7] DONE {stats}", flush=True)


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "s0_filter":
        s0_filter(sys.argv[2])
    elif cmd == "s2_filter":
        s2_filter(sys.argv[2], sys.argv[3])
    elif cmd == "s4_quota":
        s4_quota(sys.argv[2], sys.argv[3], int(sys.argv[4]))
    elif cmd == "s5_have":
        s5_have()
    elif cmd == "s7_group":
        s7_group(sys.argv[2], sys.argv[3], sys.argv[4])
    else:
        sys.exit(f"unknown stage {cmd}")
