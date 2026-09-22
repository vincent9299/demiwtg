#!/usr/bin/env python3
# PubChem 融合：CID-SMILES × 双桥(P662+P235) × 概念集 -> RDKit 离线渲染2D结构图 -> blob+账本
import gzip, json, subprocess, os, sys, time, hashlib, io, threading, queue as _q

sys.path.insert(0, "/root/../home/ubuntu/demi/raw")
from rdkit import Chem, RDLogger
from rdkit.Chem.Draw import MolToImage
RDLogger.DisableLog("rdApp.*")

# 复用 fuse_df20 的 COS 组件(keep-alive 上传)
_exec = open("/home/ubuntu/demi/raw/fuse_df20.py").read().split("def main()")[0]
exec(_exec)

COS = "/lhcos-data/demiwtg-data/datasets/raw/pubchem"

t0 = time.time()
# ① 概念集
concepts = set()
p = subprocess.Popen(["zcat", "/lhcos-data/demiwtg-data/datasets/demiwtg/kb/qid_concepts.jsonl.gz"], stdout=subprocess.PIPE, bufsize=1 << 22)
for line in p.stdout:
    try: concepts.add(line.decode().split('"')[3])
    except Exception: pass
p.wait()
# ② 双桥: P662(CID->qid) + P235(InChIKey->qid)
cid2qid = {}; ik2qid = {}
for line in gzip.open("/home/ubuntu/demi/raw/concept_xref.tsv.gz", "rt"):
    qid, prop, val = line.rstrip("\n").split("\t")
    if prop == "P662" and qid in concepts: cid2qid[val] = qid
    elif prop == "P235" and qid in concepts: ik2qid[val] = qid
print(f"[{time.strftime('%H:%M:%S')}] 概念{len(concepts):,} P662命中{len(cid2qid):,} P235命中{len(ik2qid):,} {time.time()-t0:.0f}s", flush=True)
# ③ CID -> InChIKey 交叉表(仅概念内CID)
cid2ik = {}
z = subprocess.Popen(f"cat {COS}/CID-InChI-Key.gz.part-* | zcat", shell=True, stdout=subprocess.PIPE, bufsize=1 << 22)
for line in z.stdout:
    parts = line.decode(errors="replace").rstrip("\n").split("\t")
    if len(parts) >= 2 and parts[0] in cid2qid:
        cid2ik[parts[0]] = parts[1]
z.wait()
print(f"[{time.strftime('%H:%M:%S')}] 概念内CID有InChIKey: {len(cid2ik):,} {time.time()-t0:.0f}s", flush=True)

ledger_local = "/tmp/pubchem_manifest.jsonl"
ext_path = "/home/ubuntu/demi_raw_pubchem.jsonl"  # master 本地暂存,完成后gzip上 COS
os.makedirs(os.path.expanduser("~/lake/meta"), exist_ok=True)
man_f = open(os.path.expanduser("~/lake/meta/image-shard-extpubchem.jsonl"), "w", buffering=1 << 20)
ext_f = open(ext_path, "w", buffering=1 << 20)

n_seen = n_match = n_rendered = n_fail = n_dbl = 0
z = subprocess.Popen(f"cat {COS}/CID-SMILES.gz.part-* | zcat", shell=True, stdout=subprocess.PIPE, bufsize=1 << 22)
for line in z.stdout:
    n_seen += 1
    parts = line.decode(errors="replace").rstrip("\n").split("\t")
    if len(parts) < 2: continue
    cid, smi = parts[0], parts[1]
    qid = cid2qid.get(cid)
    if not qid: continue
    n_match += 1
    ik = cid2ik.get(cid)
    dbl = ik and ik2qid.get(ik) == qid
    try:
        mol = Chem.MolFromSmiles(smi, sanitize=True)
        if mol is None: n_fail += 1; continue
        img = MolToImage(mol, size=(512, 512), kekulize=True)
        buf = io.BytesIO(); img.save(buf, format="PNG")
        body = buf.getvalue()
        n_rendered += 1
        if dbl: n_dbl += 1
        sha = hashlib.sha256(body).hexdigest()
        key = f"lhcos-data/demiwtg-data/datasets/demiwtg/blobs/{sha[:2]}/{sha}.png"
        if head_len(key) != len(body):
            upload(key, body)
        row = {"qid": qid, "sha256": sha, "blob_path": f"blobs/{sha[:2]}/{sha}.png",
               "source": "pubchem", "license": "PD", "size_bytes": len(body),
               "relation_type": "definitional", "external_id": f"CID{cid}",
               "confidence": "double-bridge" if dbl else "p662-single",
               "note": "2D structural formula (symbolic, not photograph)",
               "orig_path": f"CID{cid}.png", "fused_at": time.time()}
        line_out = json.dumps(row, ensure_ascii=False)
        ext_f.write(line_out + "\n"); man_f.write(line_out + "\n")
    except Exception:
        n_fail += 1
    if n_seen % 20000000 == 0:
        print(f"[{time.strftime('%H:%M:%S')}] SMILES过{n_seen/1e6:.0f}M 匹配{n_match/1e3:.0f}K 渲染{n_rendered/1e3:.0f}K(双桥{n_dbl/1e3:.0f}K) 失败{n_fail} {time.time()-t0:.0f}s", flush=True)
z.wait()
ext_f.close(); man_f.close()
print(f"DONE seen={n_seen} match={n_match} rendered={n_rendered} dbl={n_dbl} fail={n_fail} {time.time()-t0:.0f}s", flush=True)
