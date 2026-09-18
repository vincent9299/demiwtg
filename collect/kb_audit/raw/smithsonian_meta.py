#!/usr/bin/env python3
# Smithsonian Open Access 元数据分片（EDAN txt），逐对象写 COS，断点可续
import os, time, boto3
from botocore import UNSIGNED
from botocore.config import Config
OUT = "/lhcos-data/demiwtg-data/datasets/raw/smithsonian/metadata"
os.makedirs(OUT, exist_ok=True)
s3 = boto3.client("s3", region_name="us-west-2", config=Config(signature_version=UNSIGNED, retries={"max_attempts": 8}))
pag = s3.get_paginator("list_objects_v2")
n = done = skipped = 0
for page in pag.paginate(Bucket="smithsonian-open-access", Prefix="metadata/"):
    for o in page.get("Contents", []):
        n += 1
        rel = o["Key"][len("metadata/"):]
        dst = os.path.join(OUT, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        if os.path.exists(dst) and os.path.getsize(dst) == o["Size"]:
            skipped += 1; continue
        for i in range(6):
            try:
                s3.download_file("smithsonian-open-access", o["Key"], dst)
                done += 1; break
            except Exception as e:
                print(f"[{time.strftime('%T')}] {o['Key']} retry#{i} {e}", flush=True); time.sleep(8)
        if n % 200 == 0: print(f"[{time.strftime('%T')}] {n} listed, {done} dl, {skipped} skip", flush=True)
print(f"ALL_DONE listed={n} downloaded={done} skipped={skipped}", flush=True)
