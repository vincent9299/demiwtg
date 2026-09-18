#!/usr/bin/env python3
# WIT 全量 TSV（wikipedia_kaggle_2021 = WIT v1），流式直写 COS
import json, os, time, urllib.request, urllib.error
OUT = "/lhcos-data/demiwtg-data/datasets/raw/wit"
os.makedirs(OUT, exist_ok=True)
API = "https://storage.googleapis.com/storage/v1/b/gresearch/o"
objs, token = [], None
while True:
    q = f"{API}?prefix=wit/&maxResults=1000&fields=nextPageToken,items(name,size)"
    if token: q += f"&pageToken={token}"
    with urllib.request.urlopen(q, timeout=60) as r:
        d = json.load(r)
    objs += [(i["name"], int(i["size"])) for i in d.get("items", [])]
    token = d.get("nextPageToken")
    if not token: break
targets = [(n, s) for n, s in objs if n.endswith(".tsv.gz")]
print(f"[{time.strftime('%T')}] tsv parts: {len(targets)}, total {sum(s for _,s in targets)/1e9:.1f} GB", flush=True)
for n, s in targets:
    fn = n.replace("wit/", "")
    dst = os.path.join(OUT, fn)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    if os.path.exists(dst) and os.path.getsize(dst) == s:
        print(f"skip {fn}", flush=True); continue
    url = f"https://storage.googleapis.com/gresearch/{n}"
    for i in range(8):
        try:
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=120) as r, open(dst, "wb") as f:
                while True:
                    b = r.read(1 << 22)
                    if not b: break
                    f.write(b)
            if os.path.getsize(dst) == s:
                print(f"[{time.strftime('%T')}] OK {fn}", flush=True); break
        except Exception as e:
            print(f"[{time.strftime('%T')}] {fn} retry#{i} {e}", flush=True); time.sleep(10)
print("ALL_DONE", flush=True)
