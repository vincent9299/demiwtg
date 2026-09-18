#!/usr/bin/env python3
# 任务A：SDC 补挂（零下载）——depicts 边 × 现有账本 → qid_images_ext/sdc_attach
# 两输入流均按 M-id 升序（dump 天然序），归并 join，内存 O(概念集+have_files)
import gzip, json, subprocess, time, os, sys

have_files = {}   # 文件名 -> (sha256, license, size, path)
p = subprocess.Popen(["zcat", "/tmp/qid_images.jsonl.gz"],
                     stdout=subprocess.PIPE, bufsize=1 << 22)
for line in p.stdout:
    try:
        r = json.loads(line)
        have_files[r["commons_file"]] = (r["sha256"], r.get("license"), r.get("page_bytes"), r.get("path"))
    except Exception:
        pass
p.wait()
print(f"[{time.strftime('%H:%M:%S')}] 现有账本文件名: {len(have_files):,}", flush=True)

concepts = set()
p = subprocess.Popen(["zcat", "/tmp/qid_concepts.jsonl.gz"],
                     stdout=subprocess.PIPE, bufsize=1 << 22)
for line in p.stdout:
    try:
        concepts.add(line.decode().split('"')[3])
    except Exception:
        pass
p.wait()
print(f"[{time.strftime('%H:%M:%S')}] 概念集: {len(concepts):,}", flush=True)

def mnum(mid):
    return int(mid[1:])

# 归并: sdc_depicts 与 mid_to_file 都按 M-id 升序
m2f = gzip.open("/tmp/mid_to_file.tsv.gz", "rt")
dep = gzip.open("/tmp/sdc_depicts.tsv.gz", "rt")

out_gz = gzip.open(os.path.expanduser("~/qid_images_ext_sdc_attach.jsonl.gz"), "wt", compresslevel=1)
man = os.path.expanduser("~/lake/meta/image-shard-extsdc.jsonl")
os.makedirs(os.path.dirname(man), exist_ok=True)
man_f = open(man, "w", buffering=1 << 20)

n_dep = n_hit_concept = n_attached = 0
t0 = time.time()
cur_mid = None
cur_files = {}

def flush_edges(mid, fname):
    global n_attached
    if fname in have_files:
        sha, lic, size, path = have_files[fname]
        blob_path = path or f"blobs/{sha[:2]}/{sha}.jpg"
        for qid in cur_edges:
            row = {"qid": qid, "sha256": sha, "blob_path": blob_path, "source": "sdc",
                   "license": lic, "size_bytes": size,
                   "relation_type": "depicts_part", "external_id": mid,
                   "confidence": "sdc-p180", "orig_file": fname, "fused_at": time.time()}
            line = json.dumps(row, ensure_ascii=False)
            out_gz.write(line + "\n"); man_f.write(line + "\n")
            n_attached += 1

cur_edges = []
m_line = m2f.readline()
for d_line in dep:
    mid, qid, rank, quals = d_line.rstrip("\n").split("\t")
    n_dep += 1
    if qid not in concepts:
        continue
    n_hit_concept += 1
    if mid != cur_mid:
        # 推进 m2f 到该 mid
        while m_line:
            m_mid, m_fname = m_line.rstrip("\n").split("\t")
            if mnum(m_mid) < mnum(mid):
                m_line = m2f.readline(); continue
            break
        fname = m_fname if (m_line and m_mid == mid) else None
        cur_mid, cur_fname, cur_edges = mid, fname, [qid]
        flush_edges(mid, cur_fname)
    else:
        if not cur_edges or cur_edges[-1] != qid:
            cur_edges.append(qid)
        # 同 mid 的后续边也要写(文件已定位)
        if cur_fname:
            sha, lic, size, path = have_files.get(cur_fname, (None,)*4)
            if sha:
                row = {"qid": qid, "sha256": sha, "blob_path": path or f"blobs/{sha[:2]}/{sha}.jpg",
                       "source": "sdc", "license": lic, "size_bytes": size,
                       "relation_type": "depicts_part", "external_id": mid,
                       "confidence": "sdc-p180", "orig_file": cur_fname, "fused_at": time.time()}
                line = json.dumps(row, ensure_ascii=False)
                out_gz.write(line + "\n"); man_f.write(line + "\n")
                n_attached += 1
    if n_dep % 5000000 == 0:
        print(f"[{time.strftime('%H:%M:%S')}] dep {n_dep/1e6:.0f}M 命中概念 {n_hit_concept/1e6:.1f}M 补挂 {n_attached/1e6:.2f}M {time.time()-t0:.0f}s", flush=True)
out_gz.close(); man_f.close()
print(f"DONE dep={n_dep} hit_concept={n_hit_concept} attached={n_attached}", flush=True)
