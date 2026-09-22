#!/usr/bin/env python3
"""在 r9 上执行：从 COS 拉取 7 片切片 + seed_v6，校验 md5，建 7 个 manifest。幂等可重跑。"""
import sys, hashlib, os, gzip, shutil

sys.path.insert(0, "/home/ubuntu/wk_backfill")
import cos_util

FILES = [
    ("s008.jsonl.gz", "4504af570a5d4150ff15a5d561fe2962"),
    ("s028.jsonl.gz", "a133a927d7b0af070cc21a0dace9bc8b"),
    ("s048.jsonl.gz", "b90afbd8ccaeec582b1e6b9742466fec"),
    ("s068.jsonl.gz", "4446a4aa26b20fdfa63b74446b5d2665"),
    ("s088.jsonl.gz", "4bae60a01daa2a45d8362b3853203876"),
    ("s108.jsonl.gz", "e34ada85792e4c90e437da78e9e99513"),
    ("s130.jsonl.gz", "646ce41272496be4c4281de25ae6a34e"),
    ("seed_v6.jsonl.gz", "558685610c567118565b30eec2e69c9d"),
]
WD = "/home/ubuntu/wk_backfill"

for name, want in FILES:
    dest = os.path.join(WD, name)
    if os.path.exists(dest):
        with open(dest, "rb") as f:
            if hashlib.md5(f.read()).hexdigest() == want:
                print("skip", name, flush=True)
                continue
    for attempt in range(10):
        st, h, body = cos_util._call("GET", "tmp/r9relief/" + name)
        if st == 200 and hashlib.md5(body).hexdigest() == want:
            open(dest, "wb").write(body)
            print("got", name, len(body), flush=True)
            break
        print("retry", name, "st=", st, "att=", attempt, flush=True)
    else:
        print("FAILED", name, flush=True)
        sys.exit(1)

with gzip.open(os.path.join(WD, "seed_v6.jsonl.gz"), "rb") as f:
    with open(os.path.join(WD, "seed_v6.jsonl"), "wb") as o:
        shutil.copyfileobj(f, o)
for w in (8, 28, 48, 68, 88, 108, 130):
    d = os.path.join(WD, "run_kb_w%d" % w)
    os.makedirs(d, exist_ok=True)
    shutil.copyfile(os.path.join(WD, "seed_v6.jsonl"), os.path.join(d, "manifest.jsonl"))
    print("manifest w%d ok" % w, flush=True)
print("PULL_DONE", flush=True)
