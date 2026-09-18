#!/usr/bin/env python3
# Smithsonian 元数据：下到本节点盘 → 分批 cp 到 COS（避免 cosfs 小文件元数据打爆）
import os, time, glob, subprocess, boto3
from botocore import UNSIGNED
from botocore.config import Config
LOCAL = os.path.expanduser("~/smith_local")
COS = "/lhcos-data/demiwtg-data/datasets/raw/smithsonian"
os.makedirs(LOCAL, exist_ok=True)
s3 = boto3.client("s3", region_name="us-west-2", config=Config(signature_version=UNSIGNED, retries={"max_attempts": 8}))
keys = []
tok = None
while True:
    kw = dict(Bucket="smithsonian-open-access", Prefix="metadata/", MaxKeys=1000)
    if tok: kw["ContinuationToken"] = tok
    r = s3.list_objects_v2(**kw)
    keys += [(o["Key"], o["Size"]) for o in r.get("Contents", [])]
    if not r.get("IsTruncated"): break
    tok = r.get("NextContinuationToken")
print(f"total {len(keys)} objects {sum(s for _,s in keys)/1e9:.2f}GB", flush=True)
done = 0
for k, sz in keys:
    rel = k[len("metadata/"):].replace("/", "_")
    lp = os.path.join(LOCAL, rel)
    cp = os.path.join(COS, "metadata_" + rel)
    if os.path.exists(cp) and os.path.getsize(cp) == sz: done += 1; continue
    if not (os.path.exists(lp) and os.path.getsize(lp) == sz):
        try: s3.download_file("smithsonian-open-access", k, lp)
        except Exception as e: print(f"dl fail {k}: {e}", flush=True); continue
    try:
        subprocess.run(["cp", lp, cp], check=True, timeout=300)
        os.remove(lp); done += 1
        if done % 100 == 0: print(f"[{time.strftime('%T')}] {done}/{len(keys)}", flush=True)
    except Exception as e: print(f"cp fail {k}: {e}", flush=True)
print(f"ALL_DONE {done}/{len(keys)}", flush=True)
