#!/usr/bin/env python3
# wave2_c1fix.py — 恢复 9 行被 wh 误分类为 HTML 残渣的 UTF-16 SVG 真图
# canonical: 18,643,599 + 9 = 18,643,608;deadletter: 8,438,127 - 9 = 8,438,118
import gzip, json, os, sys, hashlib, time
HOME = "/home/ubuntu"
W2 = f"{HOME}/merge_wave2"
WH = f"{HOME}/wh_backfill"
OUT2 = f"{W2}/out2"
sys.path.insert(0, f"{HOME}/demiflow_collect")
from cosio import COSCreds, COSIO, build_host
io = COSIO(COSCreds.discover(paths=(f"{HOME}/.cos_creds",)),
           build_host("lhcos-368f6-1256345599", "ap-singapore"))
B = "lhcos-data/demiwtg-data/datasets/demiwtg/kb/"

def md5(p):
    h = hashlib.md5()
    with open(p, "rb") as f:
        while True:
            b = f.read(1 << 24)
            if not b: break
            h.update(b)
    return h.hexdigest()

def log(*a): print(f"[{time.strftime('%H:%M:%S')}]", *a, flush=True)

add = [json.loads(l) for l in open(f"{WH}/deadletter_additions.jsonl")]
deleted = {l.strip() for l in open(f"{OUT2}/blob_deleted.txt") if l.strip()}
restore = {a["sha256"] for a in add if a["blob_path"] not in deleted}
drop = {a["sha256"] for a in add} - restore
assert len(restore) == 9 and len(drop) == 13640, (len(restore), len(drop))
json.dump(sorted(restore), open(f"{OUT2}/svg_restore_shas.json", "w"), indent=1)
log("restore 9 shas; drop", len(drop))

kept = 0
with gzip.open(f"{W2}/out/images.v2.wave2.jsonl.gz", "rt", errors="replace") as fin, \
     gzip.open(f"{OUT2}/images.v2.20260924c.jsonl.gz", "wt", compresslevel=1) as out:
    for l in fin:
        assert l.startswith('{"sha256": "'), l[:30]
        if l[12:76] in drop: continue
        out.write(l); kept += 1
assert kept == 18643608, kept
log("canonical rows", kept)

n = 0
with gzip.open(f"{W2}/out/deadletter.v2.wave2.jsonl.gz", "rt", errors="replace") as fin, \
     gzip.open(f"{OUT2}/deadletter.v2.20260924c.jsonl.gz", "wt", compresslevel=1) as out:
    for l in fin:
        out.write(l); n += 1
    for a in add:
        if a["sha256"] in restore: continue
        out.write(json.dumps(a, ensure_ascii=False) + "\n"); n += 1
assert n == 8438118, n
log("deadletter rows", n)

for src, key in [(f"{OUT2}/images.v2.20260924c.jsonl.gz", "images.v2.jsonl.gz"),
                 (f"{OUT2}/images.v2.20260924c.jsonl.gz", "images.v2.20260924c.jsonl.gz"),
                 (f"{OUT2}/deadletter.v2.20260924c.jsonl.gz", "quarantine/deadletter.v2.jsonl.gz"),
                 (f"{OUT2}/deadletter.v2.20260924c.jsonl.gz", "quarantine.deadletter.v2.20260924c.jsonl.gz")]:
    assert io.put_multipart(B + key, src)
    sz = io.head(B + key)
    assert sz == os.path.getsize(src), key
    log("published", key, sz)

p2 = f"{OUT2}/postcheck2.jsonl.gz"
assert io.download_to(B + "images.v2.jsonl.gz", p2)
m = md5(p2)
cnt = sum(1 for _ in gzip.open(p2, "rt", errors="replace"))
os.remove(p2)
assert cnt == 18643608
json.dump({"rows": cnt, "md5": m, "restored": sorted(restore),
           "note": "9 UTF-16 SVG rows restored; deadletter 8,438,118; deleted blobs 13,640"},
          open(f"{OUT2}/c1fix_report.json", "w"), indent=1)
log("FIX_DONE rows", cnt, "md5", m)
