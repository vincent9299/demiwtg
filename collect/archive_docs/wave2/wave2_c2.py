#!/usr/bin/env python3
# wave2_c2.py — 2.2c 毒 blob 大扫除(可续跑守护)
# 候选 = 0922 库存中 ≤4096B 的对象 − canonical(20260924c) sha 集合
# 删除条件(双保险): 整对象 GET + 严格 HTML 魔数(<!doctype html 或 <html>+<body)
# 红线: 只删清单内、只删 HTML;真图/SVG/XML 一律不碰。停止开关: ~/merge_wave2/STOP_2C
import gzip, json, os, sys, time
from concurrent.futures import ThreadPoolExecutor
HOME = "/home/ubuntu"
W2 = f"{HOME}/merge_wave2"
OUT2 = f"{W2}/out2"
sys.path.insert(0, f"{HOME}/demiflow_collect")
from cosio import COSCreds, COSIO, build_host
io = COSIO(COSCreds.discover(paths=(f"{HOME}/.cos_creds",)),
           build_host("lhcos-368f6-1256345599", "ap-singapore"))

def log(*a): print(f"[{time.strftime('%H:%M:%S')}]", *a, flush=True)

def html_magic(b):
    if not b: return False
    t = b[:512].lstrip().lower()
    return t.startswith(b"<!doctype html") or (t.startswith(b"<html") and b"<body" in b[:2048].lower())

def main():
    # 1) canonical sha 集(行首固定前缀切片,免 json 解析)
    canon = set()
    with gzip.open(f"{OUT2}/images.v2.20260924c.jsonl.gz", "rt", errors="replace") as f:
        for l in f:
            canon.add(l[12:76])
    log("canonical shas", len(canon))
    # 2) 候选
    cands = []
    with open(f"{W2}/all_small.tsv") as f:
        for l in f:
            key, sz = l.rstrip("\n").split("\t")
            base = key.rsplit("/", 1)[-1]
            sha = base.split(".")[0]
            if len(sha) == 64 and sha not in canon:
                cands.append((key, int(sz)))
    log("candidates", len(cands))
    json.dump({"canonical": len(canon), "candidates": len(cands)},
              open(f"{OUT2}/c2_pre.json", "w"))
    # 3) 续跑集
    done = set()
    if os.path.exists(f"{OUT2}/c2_deleted.txt"):
        done = {l.strip() for l in open(f"{OUT2}/c2_deleted.txt") if l.strip()}
    stats = {"deleted": 0, "not_html": 0, "gone": 0, "error": 0, "skipped_done": 0}
    dlog = open(f"{OUT2}/c2_deleted.txt", "a")
    elog = open(f"{OUT2}/c2_errors.txt", "a")
    STOP = f"{W2}/STOP_2C"

    def handle(item):
        key, sz = item
        if key in done: stats["skipped_done"] += 1; return
        try:
            b = io.get_bytes(key)
        except Exception as e:
            stats["error"] += 1; elog.write(f"GET {key} {str(e)[:60]}\n"); return
        if b is None:
            stats["gone"] += 1; return
        if len(b) > 4096 or not html_magic(b):
            stats["not_html"] += 1; return
        try:
            io.delete(key)
            stats["deleted"] += 1
            dlog.write(key + "\n")
        except Exception as e:
            stats["error"] += 1; elog.write(f"DEL {key} {str(e)[:60]}\n")

    t0 = time.time(); n = 0
    with ThreadPoolExecutor(16) as ex:
        for r in ex.map(handle, cands):
            n += 1
            if os.path.exists(STOP):
                log("STOP requested at", n); break
            if n % 50000 == 0:
                dlog.flush()
                log(n, "/", len(cands), dict(stats), f"{(time.time()-t0)/60:.1f}min")
    dlog.flush(); elog.close()
    json.dump(stats, open(f"{OUT2}/c2_stats.json", "w"))
    log("C2_DONE", json.dumps(stats))

if __name__ == "__main__":
    main()
