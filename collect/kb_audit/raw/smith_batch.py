#!/usr/bin/env python3
# Smithsonian 批量：~300片/批 -> 本地tar.gz -> cp到COS -> 清理；断点续传靠 progress 文件
import os, time, subprocess, tarfile, boto3
from concurrent.futures import ThreadPoolExecutor
from botocore import UNSIGNED
from botocore.config import Config

LOCAL = os.path.expanduser("~/smith_local")
COS = "/lhcos-data/demiwtg-data/datasets/raw/smithsonian"
PROG = os.path.expanduser("~/smith_progress.txt")
BATCH = 300
os.makedirs(LOCAL, exist_ok=True)
s3 = boto3.client("s3", region_name="us-west-2", config=Config(signature_version=UNSIGNED, retries={"max_attempts": 8}))

keys, tok = [], None
while True:
    kw = dict(Bucket="smithsonian-open-access", Prefix="metadata/", MaxKeys=1000)
    if tok: kw["ContinuationToken"] = tok
    r = s3.list_objects_v2(**kw)
    keys += [(o["Key"], o["Size"]) for o in r.get("Contents", [])]
    if not r.get("IsTruncated"): break
    tok = r.get("NextContinuationToken")
print(f"total {len(keys)} shards {sum(s for _,s in keys)/1e9:.2f}GB", flush=True)

done_batches = set()
if os.path.exists(PROG):
    done_batches = {int(x) for x in open(PROG).read().split() if x.isdigit()}

nb = (len(keys) + BATCH - 1) // BATCH
def dl_one(args):
    k, sz = args
    dst = os.path.join(LOCAL, k.replace("/", "_"))
    if os.path.exists(dst) and os.path.getsize(dst) == sz: return True
    try:
        s3.download_file("smithsonian-open-access", k, dst)
        return True
    except Exception:
        return False

for b in range(nb):
    if b in done_batches: continue
    part = keys[b*BATCH:(b+1)*BATCH]
    with ThreadPoolExecutor(8) as ex:
        results = list(ex.map(dl_one, part))
    ok = sum(results)
    if ok < len(part):
        print(f"[{time.strftime('%T')}] batch {b}: {ok}/{len(part)} dl, retry next round", flush=True)
        continue
    tp = os.path.join(LOCAL, f"batch_{b:04d}.tar.gz")
    with tarfile.open(tp, "w:gz", compresslevel=1) as tf:
        for k, sz in part:
            tf.add(os.path.join(LOCAL, k.replace("/", "_")), arcname=k.replace("/", "_"))
    exp = os.path.getsize(tp)
    cp = f"{COS}/smithsonian_batches/batch_{b:04d}.tar.gz"
    os.makedirs(os.path.dirname(cp), exist_ok=True)
    for t in range(4):
        subprocess.run(["cp", tp, cp + ".tmp"], check=False)
        os.rename(cp + ".tmp", cp)
        try:
            if os.path.getsize(cp) == exp: break
        except Exception: pass
        time.sleep(3)
    with open(PROG, "a") as f: f.write(f"{b}\n")
    for k, sz in part: os.remove(os.path.join(LOCAL, k.replace("/", "_")))
    os.remove(tp)
    print(f"[{time.strftime('%T')}] batch {b+1}/{nb} shipped ({len(part)} shards)", flush=True)
print("ALL_DONE", flush=True)
