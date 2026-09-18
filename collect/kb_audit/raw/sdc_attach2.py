#!/usr/bin/env python3
# 任务A v2（sqlite 版，防 OOM）：depicts × mid_to_file 归并 → sqlite 查 blob → 补挂账本
import gzip, json, sqlite3, subprocess, time, os

t0 = time.time()
# ① 概念集（唯一大内存件，~800MB）
concepts = set()
p = subprocess.Popen(["zcat", "/tmp/qid_concepts.jsonl.gz"], stdout=subprocess.PIPE, bufsize=1 << 22)
for line in p.stdout:
    try: concepts.add(line.decode().split('"')[3])
    except Exception: pass
p.wait()
print(f"[{time.strftime('%H:%M:%S')}] 概念集 {len(concepts):,} {time.time()-t0:.0f}s", flush=True)

# ② 现有账本 -> sqlite
db = "/tmp/have_files.db"
if os.path.exists(db): os.remove(db)
con = sqlite3.connect(db)
con.executescript("PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF; CREATE TABLE hf(fname TEXT PRIMARY KEY, sha TEXT, ext TEXT, lic TEXT, size INT) WITHOUT ROWID;")
p = subprocess.Popen(["zcat", "/tmp/qid_images.jsonl.gz"], stdout=subprocess.PIPE, bufsize=1 << 22)
buf = []
def flush():
    con.executemany("INSERT OR IGNORE INTO hf VALUES(?,?,?,?,?)", buf)
    buf.clear()
for line in p.stdout:
    try:
        r = json.loads(line)
        buf.append((r["commons_file"].replace("_"," "), r["sha256"], r.get("ext") or "jpg", r.get("license"), r.get("page_bytes")))
        if len(buf) >= 50000: flush()
    except Exception: pass
flush(); con.commit()
print(f"[{time.strftime('%H:%M:%S')}] 账本入sqlite {time.time()-t0:.0f}s", flush=True)

# ③ mid_to_file 入 sqlite（不赌排序）
con.executescript("CREATE TABLE IF NOT EXISTS m2f(mid TEXT PRIMARY KEY, fname TEXT) WITHOUT ROWID;")
buf2 = []
import gzip as _g
with _g.open("/tmp/mid_to_file.tsv.gz", "rt") as f:
    def flush2():
        con.executemany("INSERT OR IGNORE INTO m2f VALUES(?,?)", buf2)
        buf2.clear()
    for line in f:
        a, b = line.rstrip("\n").split("\t")
        buf2.append((a, b))
        if len(buf2) >= 100000: flush2()
    flush2()
con.commit()
print(f"[{time.strftime('%H:%M:%S')}] m2f入sqlite {time.time()-t0:.0f}s", flush=True)

# ④ join
out_gz = gzip.open(os.path.expanduser("~/qid_images_ext_sdc_attach.jsonl.gz"), "wt", compresslevel=1)
man = os.path.expanduser("~/lake/meta/image-shard-extsdc.jsonl")
os.makedirs(os.path.dirname(man), exist_ok=True)
man_f = open(man, "w", buffering=1 << 20)

n_dep = n_concept_hit = n_file_hit = n_attached = 0
fname_cache = {}
def mid2fname(mid):
    f = fname_cache.get(mid)
    if f is None:
        row = con.execute("SELECT fname FROM m2f WHERE mid=?", (mid,)).fetchone()
        f = row[0] if row else ""
        if len(fname_cache) > 2000000: fname_cache.clear()
        fname_cache[mid] = f
    return f or None

for d_line in dep:
    mid, qid, rank, quals = d_line.rstrip("\n").split("\t")
    n_dep += 1
    if qid not in concepts: continue
    n_concept_hit += 1
    fname = mid2fname(mid)
    if not fname: continue
    n_file_hit += 1
    lookup_buf.append((qid, mid, fname))
    if len(lookup_buf) >= 20000:
        do_lookup(lookup_buf); lookup_buf.clear()
    if n_dep % 5000000 == 0:
        print(f"[{time.strftime('%H:%M:%S')}] dep {n_dep/1e6:.0f}M 概念命中 {n_concept_hit/1e6:.1f}M 有文件 {n_file_hit/1e6:.1f}M 补挂 {n_attached/1e6:.2f}M {time.time()-t0:.0f}s", flush=True)
do_lookup(lookup_buf); lookup_buf.clear()
    if n_dep % 5000000 == 0:
        print(f"[{time.strftime('%H:%M:%S')}] dep {n_dep/1e6:.0f}M 概念命中 {n_concept_hit/1e6:.1f}M 有文件 {n_file_hit/1e6:.1f}M 补挂 {n_attached/1e6:.2f}M {time.time()-t0:.0f}s", flush=True)
do_lookup(lookup_buf)
out_gz.close(); man_f.close(); con.close()
print(f"DONE dep={n_dep} concept_hit={n_concept_hit} file_hit={n_file_hit} attached={n_attached} {time.time()-t0:.0f}s", flush=True)
