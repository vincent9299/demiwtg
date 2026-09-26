#!/usr/bin/env python3
# wave2_c1.py — 2.2a: 主表剔除 13,649 HTML 残渣行 + 死信并入;2.2b: blob 验证+删除
# 只写 ~/merge_wave2/out2/ 与 COS 新 key;旧件已有备份(backup/20260923 + 日期件 20260924)。
import gzip, json, os, sys, time, hashlib
from concurrent.futures import ThreadPoolExecutor

HOME = "/home/ubuntu"
W2 = f"{HOME}/merge_wave2"
WH = f"{HOME}/wh_backfill"
OUT2 = f"{W2}/out2"
IN = f"{W2}/in"
sys.path.insert(0, f"{HOME}/demiflow_collect")
from cosio import COSCreds, COSIO, build_host
io = COSIO(COSCreds.discover(paths=(f"{HOME}/.cos_creds",)),
           build_host("lhcos-368f6-1256345599", "ap-singapore"))
B = "lhcos-data/demiwtg-data/datasets/demiwtg/kb/"

def iter_gz(p):
    with gzip.open(p, "rt", errors="replace") as f:
        for l in f: yield l

def md5(p):
    h = hashlib.md5()
    with open(p, "rb") as f:
        while True:
            b = f.read(1 << 24)
            if not b: break
            h.update(b)
    return h.hexdigest()

def log(*a): print(f"[{time.strftime('%H:%M:%S')}]", *a, flush=True)

def step1_rewrite():
    os.makedirs(OUT2, exist_ok=True)
    add = [json.loads(l) for l in open(f"{WH}/deadletter_additions.jsonl")]
    add_sha = {a["sha256"] for a in add}
    assert len(add) == 13649 and len(add_sha) == 13649, f"additions {len(add)}/{len(add_sha)}"
    # 主表剔行(输入 = wave2 out,md5=08be0f3a 已锚定 = 现 canonical)
    kept = removed = 0
    removed_rows = []
    with gzip.open(f"{W2}/out/images.v2.wave2.jsonl.gz", "rt", errors="replace") as fin, \
         gzip.open(f"{OUT2}/images.v2.20260924b.jsonl.gz", "wt", compresslevel=1) as out:
        for l in fin:
            assert l.startswith('{"sha256": "'), l[:30]
            sha = l[12:76]
            if sha in add_sha:
                removed += 1; removed_rows.append(sha)
                continue
            out.write(l); kept += 1
    log("main rewrite kept", kept, "removed", removed)
    assert kept == 18657248 - 13649 == 18643599, f"kept {kept}"
    # 死信并入
    n_dl = 0
    with gzip.open(f"{W2}/out/deadletter.v2.wave2.jsonl.gz", "rt", errors="replace") as fin, \
         gzip.open(f"{OUT2}/deadletter.v2.20260924b.jsonl.gz", "wt", compresslevel=1) as out:
        for l in fin:
            out.write(l); n_dl += 1
        for a in add:
            out.write(json.dumps(a, ensure_ascii=False) + "\n"); n_dl += 1
    log("deadletter rows", n_dl)
    assert n_dl == 8424478 + 13649 == 8438127, f"dl {n_dl}"
    json.dump({"kept": kept, "removed": removed, "deadletter": n_dl,
               "add_md5": md5(f"{OUT2}/images.v2.20260924b.jsonl.gz"),
               "dl_md5": md5(f"{OUT2}/deadletter.v2.20260924b.jsonl.gz"),
               "removed_sha_sample": removed_rows[:5]},
              open(f"{OUT2}/c1_report.json", "w"), indent=1)
    log("STEP1_DONE")

def step2_publish():
    rep = json.load(open(f"{OUT2}/c1_report.json"))
    for src, key in [(f"{OUT2}/images.v2.20260924b.jsonl.gz", "images.v2.jsonl.gz"),
                     (f"{OUT2}/images.v2.20260924b.jsonl.gz", "images.v2.20260924b.jsonl.gz"),
                     (f"{OUT2}/deadletter.v2.20260924b.jsonl.gz", "quarantine/deadletter.v2.jsonl.gz"),
                     (f"{OUT2}/deadletter.v2.20260924b.jsonl.gz", "quarantine.deadletter.v2.20260924b.jsonl.gz")]:
        assert io.put_multipart(B + key, src)
        sz = io.head(B + key)
        assert sz == os.path.getsize(src), f"size mismatch {key}"
        log("published", key, sz)
    p2 = f"{OUT2}/postcheck.jsonl.gz"
    assert io.download_to(B + "images.v2.jsonl.gz", p2)
    assert md5(p2) == rep["add_md5"], "post md5 mismatch"
    n = sum(1 for _ in iter_gz(p2)); os.remove(p2)
    assert n == 18643599
    log("STEP2_DONE rows", n, "md5", rep["add_md5"])

def _html_magic(b):
    t = b[:512].lstrip().lower()
    return t.startswith(b"<!doctype html") or (t.startswith(b"<html") and b"<body" in b[:2048].lower())

def step3_delete_blobs():
    add = [json.loads(l) for l in open(f"{WH}/deadletter_additions.jsonl")]
    logf = open(f"{OUT2}/blob_delete.log", "a")
    done = set()
    if os.path.exists(f"{OUT2}/blob_deleted.txt"):
        done = {l.strip() for l in open(f"{OUT2}/blob_deleted.txt") if l.strip()}
    stats = {"verified": 0, "not_html_or_big": 0, "missing": 0, "deleted": 0, "skipped_done": 0,
             "already_gone": 0}

    def handle(a):
        bp = a["blob_path"]
        if bp in done: stats["skipped_done"] += 1; return None
        try:
            sz = io.head(B + bp)
        except Exception:
            sz = None
        if sz is None:
            stats["missing"] += 1; return ("MISS", bp)
        if sz > 4096:
            stats["not_html_or_big"] += 1; return ("SKIPBIG", bp)
        try:
            b = io.get_bytes(B + bp)
        except Exception:
            stats["not_html_or_big"] += 1; return ("SKIPGET", bp)
        if not _html_magic(b):
            stats["not_html_or_big"] += 1; return ("SKIPMAGIC", bp)
        stats["verified"] += 1
        try:
            io.delete(B + bp)
            return ("DEL", bp)
        except Exception as e:
            return ("DELFAIL", bp + " " + str(e)[:60])

    t0 = time.time()
    with ThreadPoolExecutor(12) as ex:
        for i, r in enumerate(ex.map(handle, add), 1):
            if r is None: continue
            kind, info = r
            if kind == "DEL":
                stats["deleted"] += 1
                with open(f"{OUT2}/blob_deleted.txt", "a") as f: f.write(info + "\n")
                logf.write(info + "\n"); logf.flush()
            elif kind == "DELFAIL":
                logf.write(f"DELFAIL {info}\n"); logf.flush()
            if i % 2000 == 0:
                log("blob del", i, "/", len(add), dict(stats), f"{time.time()-t0:.0f}s")
    log("STEP3_DONE", json.dumps(stats))
    json.dump(stats, open(f"{OUT2}/blob_delete_stats.json", "w"))

if __name__ == "__main__":
    {"step1": step1_rewrite, "step2": step2_publish, "step3": step3_delete_blobs,
     "all": lambda: (step1_rewrite(), step2_publish(), step3_delete_blobs())}[sys.argv[1]]()
