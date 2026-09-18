#!/usr/bin/env python3
# PlantNet-300K 融合: 本地 zip -> sha256 blob 入池 -> qid_images_ext/plantnet.jsonl
# zip 结构: plantnet_300K/plantnet300K_species_id_2_name.json + images/<species_id>/xxx.jpg
import zipfile, json, gzip, subprocess, os, sys, time, hashlib, threading, queue as _q

sys.path.insert(0, "/tmp")
PN_ZIP = os.path.expanduser("~/plantnet_300K.zip")

# --- 复用 fuse_df20 的 COS 直传组件(同文件已含线程本地连接) ---
exec(open("/tmp/fuse_df20.py").read().split("def main()")[0])

def main():
    t0 = time.time()
    # ① 桥: P225+P3151
    by_name, by_inat = {}, {}
    z = subprocess.Popen(["zcat", "/tmp/concept_xref_p225846.tsv.gz"], stdout=subprocess.PIPE, bufsize=1<<22)
    for line in z.stdout:
        qid, prop, val = line.decode().rstrip("\n").split("\t")
        if prop == "P225": by_name[val] = qid
        else: by_inat[val] = qid
    z.wait()
    concepts = set()
    p2 = subprocess.Popen(["zcat", "/tmp/qid_concepts.jsonl.gz"], stdout=subprocess.PIPE, bufsize=1<<22)
    for line in p2.stdout:
        try: concepts.add(line.decode().split('"')[3])
        except Exception: pass
    p2.wait()
    print(f"[{time.strftime('%H:%M:%S')}] 桥 P225={len(by_name):,} | 概念集={len(concepts):,}", flush=True)

    zf = zipfile.ZipFile(PN_ZIP)
    names = zf.namelist()
    # ② 物种映射 json
    map_json = [n for n in names if n.endswith("species_id_2_name.json")]
    sid2name = {}
    if map_json:
        sid2name = json.loads(zf.read(map_json[0]))
        print(f"[{time.strftime('%H:%M:%S')}] 物种映射: {len(sid2name):,} 种", flush=True)
    # ③ 概念集内物种 -> qid
    want = {}
    for sid, name in sid2name.items():
        toks = str(name).split()
        key = " ".join(toks[:2]) if len(toks) > 2 else str(name)
        qid = by_name.get(key) or by_name.get(str(name))
        if qid and qid in concepts:
            want[str(sid)] = (qid, str(name))
    print(f"[{time.strftime('%H:%M:%S')}] 概念集内物种: {len(want):,}", flush=True)
    # ④ 遍历图片
    imgs = [n for n in names if n.lower().endswith((".jpg", ".jpeg", ".png"))]
    print(f"图片条目: {len(imgs):,}", flush=True)
    ledger_local = os.path.expanduser("~/lake/meta/image-shard-extplantnet.jsonl")
    os.makedirs(os.path.dirname(ledger_local), exist_ok=True)
    ext_out = gzip.open(os.path.expanduser("~/qid_images_ext_plantnet.jsonl.gz"), "wt", compresslevel=1)
    tq = _q.Queue(maxsize=64)
    lock = threading.Lock()
    n = [0, 0, 0, 0]  # seen, matched, uploaded, dedup
    def worker():
        while True:
            item = tq.get()
            if item is None: return
            body, sha, ext, qid, nm, base = item
            try:
                key = f"lhcos-data/demiwtg-data/datasets/demiwtg/blobs/{sha[:2]}/{sha}.{ext}"
                if head_len(key) == len(body): n[3] += 1
                else: upload(key, body); n[2] += 1
                row = {"qid": qid, "sha256": sha, "blob_path": f"blobs/{sha[:2]}/{sha}.{ext}",
                       "source": "plantnet", "license": "CC BY 4.0", "size_bytes": len(body),
                       "relation_type": "definitional", "external_id": nm,
                       "confidence": "double-bridge", "orig_path": base, "fused_at": time.time()}
                line = json.dumps(row, ensure_ascii=False)
                with lock:
                    ext_out.write(line + "\n")
                    with open(ledger_local, "a") as lf: lf.write(line + "\n")
            except Exception as e:
                print(f"ERR {base}: {e}", flush=True)
            tq.task_done()
    threads = [threading.Thread(target=worker, daemon=True) for _ in range(4)]
    for t in threads: t.start()
    seen_pairs = set()
    for name in imgs:
        n[0] += 1
        parts = name.split("/")
        sid = None
        for p in parts:
            if p in want: sid = p; break
        if sid is None:
            continue
        qid, nm = want[sid]
        body = zf.read(name)
        sha = hashlib.sha256(body).hexdigest()
        k2 = (qid, sha)
        if k2 in seen_pairs: continue
        seen_pairs.add(k2)
        ext = os.path.splitext(name)[1].lower().lstrip(".") or "jpg"
        n[1] += 1
        tq.put((body, sha, ext, qid, nm, os.path.basename(name)))
        if n[0] % 50000 == 0:
            tq.join()
            print(f"[{time.strftime('%H:%M:%S')}] 过{n[0]/1e3:.0f}K 挂{n[1]/1e3:.0f}K 传{n[2]/1e3:.0f}K {time.time()-t0:.0f}s", flush=True)
    tq.join()
    ext_out.close()
    print(f"DONE seen={n[0]} matched={n[1]} uploaded={n[2]} dedup={n[3]}", flush=True)

if __name__ == "__main__":
    main()
