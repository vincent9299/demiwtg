#!/usr/bin/env python3
"""盲区补嗅准备: 找出 cand_small 中从未被嗅探覆盖的 rel。

内存策略: 8 片嗅探结果的 rel → 64位 blake2b 指纹 → array('q') 排序(58MB),
流式扫 cand_small(792.5万行), 指纹不在数组中的 rel → missing.tsv。
输出: state/missing.tsv + 覆盖统计。
"""
import array
import bisect
import hashlib
import os
import subprocess
import sys

ST = "/home/ubuntu/demi/raw/state/"
B = ("https://lhcos-368f6-1256345599.cos.ap-singapore.myqcloud.com/"
     "lhcos-data/demiwtg-data/node-backup/2026-09-17/p5/demi/raw/state/")


def fetch(key, dst):
    if os.path.exists(dst) and os.path.getsize(dst) > 0:
        return
    subprocess.run(["curl", "-s", "--retry", "3", "-o", dst, B + key],
                   check=True)


def fp(rel: str) -> int:
    return int.from_bytes(
        hashlib.blake2b(rel.encode(), digest_size=8).digest(),
        "little", signed=True)


fetch("cand_small.tsv", ST + "cand_small.tsv")
for i in range(8):
    fetch(f"sniffout_0{i}.tsv", ST + f"sniffout_0{i}.tsv")

fps = array.array("q")
n_lines = 0
for i in range(8):
    with open(ST + f"sniffout_0{i}.tsv", encoding="utf-8") as f:
        for line in f:
            rel = line.split("\t", 1)[0]
            fps.append(fp(rel))
            n_lines += 1
fps = array.array("q", sorted(fps))
print(f"sniff lines={n_lines:,} fps={len(fps):,}", flush=True)

n_miss = n_tot = 0
with open(ST + "cand_small.tsv", encoding="utf-8") as f, \
        open(ST + "missing.tsv", "w", encoding="utf-8") as out:
    for line in f:
        rel = line.split("\t", 1)[0]
        if not rel or rel.endswith("/"):
            continue
        n_tot += 1
        v = fp(rel)
        j = bisect.bisect_left(fps, v)
        if not (j < len(fps) and fps[j] == v):
            out.write(line if line.endswith("\n") else line + "\n")
            n_miss += 1
print(f"cand_total={n_tot:,} missing={n_miss:,} -> {ST}missing.tsv",
      flush=True)
