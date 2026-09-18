#!/usr/bin/env python3
# Smithsonian 解析: 46 批 tar -> 记录级 TSV(id, ULAN, AAT, TGN, 图URL数, license)
import tarfile, gzip, json, os, time, sys

OUT = gzip.open(os.path.expanduser("~/smith_records.tsv.gz"), "wt", compresslevel=1)
D = "/lhcos-data/demiwtg-data/datasets/raw/smithsonian/smithsonian_batches"
n_files = n_rec = n_with_id = n_with_img = 0
t0 = time.time()
for b in range(46):
    path = f"{D}/batch_{b:04d}.tar.gz"
    if not os.path.exists(path):
        print(f"WARN 缺批 {b:04d}", flush=True); continue
    try:
        tf = tarfile.open(path, "r:gz")
        for m in tf:
            if not m.isfile(): continue
            n_files += 1
            try:
                d = json.loads(tf.extractfile(m).read().decode("utf-8", errors="replace"))
            except Exception:
                continue
            n_rec += 1
            isd = d.get("indexedStructured") or {}
            ulan = (isd.get("contributorULAN") or isd.get("creatorULAN") or [])
            aat = (isd.get("objectAAT") or isd.get("associationAAT") or [])
            tgn = (isd.get("placeTGN") or [])
            ids = d.get("id") or ""
            has_id = bool(ulan or aat or tgn)
            if has_id: n_with_id += 1
            media = d.get("media") or (d.get("content") or {}).get("media") or []
            if media:
                n_with_img += 1
                has_img = "1"
            else:
                has_img = ""
            if has_id or media:
                OUT.write("\t".join([
                    ids,
                    ",".join(str(x) for x in ulan[:5]),
                    ",".join(str(x) for x in aat[:5]),
                    ",".join(str(x) for x in tgn[:5]),
                    has_img,
                    "1" if has_id else "",
                    d.get("title") or "",
                ]).replace("\t", " ").replace("\n", " ") + "\n")
    except Exception as e:
        print(f"ERR batch {b:04d}: {e}", flush=True)
    if b % 5 == 0:
        OUT.flush()
        print(f"[{time.strftime('%H:%M:%S')}] 批{b+1}/46 文件{n_files/1e3:.0f}K 记录{n_rec/1e6:.2f}M 有ID{n_with_id/1e3:.0f}K 有图{n_with_img/1e3:.0f}K {time.time()-t0:.0f}s", flush=True)
OUT.close()
print(f"DONE files={n_files} records={n_rec} with_id={n_with_id} with_img={n_with_img}", flush=True)
