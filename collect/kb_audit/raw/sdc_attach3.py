#!/usr/bin/env python3
# 任务A v3：全部查表走 sqlite，不赌任何输入的排序
import gzip, json, sqlite3, subprocess, time, os

t0 = time.time()
concepts = set()
p = subprocess.Popen(["zcat", "/tmp/qid_concepts.jsonl.gz"], stdout=subprocess.PIPE, bufsize=1 << 22)
for line in p.stdout:
    try: concepts.add(line.decode().split('"')[3])
    except Exception: pass
p.wait()
print(f"[{time.strftime('%H:%M:%S')}] 概念集 {len(concepts):,} {time.time()-t0:.0f}s", flush=True)

db = "/tmp/attach.db"
if os.path.exists(db): os.remove(db)
con = sqlite3.connect(db)
con.executescript("""
PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF;
CREATE TABLE hf(fname TEXT PRIMARY KEY, sha TEXT, ext TEXT, lic TEXT, size INT) WITHOUT ROWID;
CREATE TABLE m2f(mid TEXT PRIMARY KEY, fname TEXT) WITHOUT ROWID;
""")

buf = []
p = subprocess.Popen(["zcat", "/tmp/qid_images.jsonl.gz"], stdout=subprocess.PIPE, bufsize=1 << 22)
def flush_hf():
    con.executemany("INSERT OR IGNORE INTO hf VALUES(?,?,?,?,?)", buf); buf.clear()
for line in p.stdout:
    try:
        r = json.loads(line)
        buf.append((r["commons_file"].replace("_", " "), r["sha256"], r.get("ext") or "jpg", r.get("license"), r.get("page_bytes")))
        if len(buf) >= 50000: flush_hf()
    except Exception: pass
flush_hf(); con.commit()
print(f"[{time.strftime('%H:%M:%S')}] hf入sqlite {time.time()-t0:.0f}s", flush=True)

buf = []
with gzip.open("/tmp/mid_to_file.tsv.gz", "rt") as f:
    def flush_m2f():
        con.executemany("INSERT OR IGNORE INTO m2f VALUES(?,?)", buf); buf.clear()
    for line in f:
        a, b = line.rstrip("\n").split("\t")
        buf.append((a, b.replace("_", " ")))
        if len(buf) >= 100000: flush_m2f()
    flush_m2f()
con.commit()
print(f"[{time.strftime('%H:%M:%S')}] m2f入sqlite {time.time()-t0:.0f}s", flush=True)

out_gz = gzip.open(os.path.expanduser("~/qid_images_ext_sdc_attach.jsonl.gz"), "wt", compresslevel=1)
man = os.path.expanduser("~/lake/meta/image-shard-extsdc.jsonl")
os.makedirs(os.path.dirname(man), exist_ok=True)
man_f = open(man, "w", buffering=1 << 20)

n_dep = n_concept_hit = n_file_hit = n_attached = 0
fname_cache = {}
def mid2fname(mid):
    f = fname_cache.get(mid, 0)
    if f != 0: return f or None
    row = con.execute("SELECT fname FROM m2f WHERE mid=?", (mid,)).fetchone()
    f = row[0] if row else ""
    if len(fname_cache) > 3000000: fname_cache.clear()
    fname_cache[mid] = f
    return f or None

lookup_buf = []
seen_pairs = set()
def do_lookup(batch):
    global n_attached
    fnames = list(set(f for _, _, f in batch))
    for i in range(0, len(fnames), 900):
        chunk = fnames[i:i+900]
        qmarks = ",".join("?" * len(chunk))
        for row in con.execute(f"SELECT fname, sha, ext, lic, size FROM hf WHERE fname IN ({qmarks})", chunk):
            hf_lookup[row[0]] = row[1:]
    for qid, mid, fname in batch:
        info = hf_lookup.get(fname)
        if not info: continue
        sha, ext, lic, size = info
        k = (qid, sha)
        if k in seen_pairs: continue
        seen_pairs.add(k)
        row_out = {"qid": qid, "sha256": sha, "blob_path": f"blobs/{sha[:2]}/{sha}.{ext}",
                   "source": "sdc", "license": lic, "size_bytes": size,
                   "relation_type": "depicts_part", "external_id": mid,
                   "confidence": "sdc-p180", "orig_file": fname.replace(" ", "_"),
                   "fused_at": time.time()}
        line = json.dumps(row_out, ensure_ascii=False)
        out_gz.write(line + "\n"); man_f.write(line + "\n")
        n_attached += 1

hf_lookup = {}
with gzip.open("/tmp/sdc_depicts.tsv.gz", "rt") as dep:
    for d_line in dep:
        mid, qid, rank, quals = d_line.rstrip("\n").split("\t")
        n_dep += 1
        if qid not in concepts: continue
        n_concept_hit += 1
        fname = mid2fname(mid)
        if not fname: continue
        n_file_hit += 1
        hf_lookup.clear()
        lookup_buf.append((qid, mid, fname))
        if len(lookup_buf) >= 20000:
            do_lookup(lookup_buf); lookup_buf.clear()
        if n_dep % 5000000 == 0:
            print(f"[{time.strftime('%H:%M:%S')}] dep {n_dep/1e6:.0f}M 概念命中 {n_concept_hit/1e6:.1f}M 有文件 {n_file_hit/1e6:.1f}M 补挂 {n_attached/1e6:.2f}M {time.time()-t0:.0f}s", flush=True)
do_lookup(lookup_buf)
out_gz.close(); man_f.close(); con.close()
print(f"DONE dep={n_dep} concept_hit={n_concept_hit} file_hit={n_file_hit} attached={n_attached} {time.time()-t0:.0f}s", flush=True)
