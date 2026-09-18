#!/usr/bin/env python3
import gzip, json, subprocess, time
t0 = time.time()
concepts = set()
p = subprocess.Popen(["zcat", "/lhcos-data/demiwtg-data/datasets/demiwtg/kb/qid_concepts.jsonl.gz"], stdout=subprocess.PIPE, bufsize=1 << 22)
for line in p.stdout:
    try: concepts.add(line.decode().split('"')[3])
    except Exception: pass
p.wait()
print(f"[{time.strftime('%H:%M:%S')}] 概念集 {len(concepts):,} -- 扫xref中", flush=True)
ik2qid = {}
for line in gzip.open("/home/ubuntu/demi/raw/concept_xref.tsv.gz", "rt"):
    qid, prop, val = line.rstrip("\n").split("\t")
    if prop == "P235" and qid in concepts: ik2qid[val] = qid
print(f"[{time.strftime('%H:%M:%S')}] ik2qid {len(ik2qid):,} -- 扫CID-InChIKey 7.4GB 中", flush=True)
want_cids = set()
for l in open("/home/ubuntu/demi_raw_pubchem.jsonl"):
    want_cids.add(json.loads(l)["external_id"][3:])
cid2ik = {}
_n = 0
z = subprocess.Popen("cat /lhcos-data/demiwtg-data/datasets/raw/pubchem/CID-InChI-Key.gz.part-* | zcat", shell=True, stdout=subprocess.PIPE, bufsize=1 << 22)
for line in z.stdout:
    parts = line.decode(errors="replace").rstrip("\n").split("\t")
    if len(parts) >= 3 and parts[0] in want_cids: cid2ik[parts[0]] = parts[2]
    _n += 1
    if _n % 20000000 == 0: print(f"[{time.strftime('%H:%M:%S')}] ik流过 {_n/1e6:.0f}M", flush=True)
z.wait()
print(f"ik2qid={len(ik2qid):,} cid2ik={len(cid2ik):,} {time.time()-t0:.0f}s", flush=True)
n = dbl = 0
with open("/home/ubuntu/demi_raw_pubchem.jsonl") as f, open("/tmp/pubchem_reannot.jsonl", "w") as out:
    for line in f:
        r = json.loads(line); n += 1
        ik = cid2ik.get(r["external_id"][3:])
        if ik and ik2qid.get(ik) == r["qid"]:
            r["confidence"] = "double-bridge"; dbl += 1
        out.write(json.dumps(r, ensure_ascii=False) + "\n")
print(f"DONE 重标注 {n:,} 行, 双桥命中 {dbl:,}", flush=True)
