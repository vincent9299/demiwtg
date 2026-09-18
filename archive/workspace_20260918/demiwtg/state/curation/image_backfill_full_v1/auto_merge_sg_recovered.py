#!/usr/bin/env python3
"""等 12 个 sgrec12 tar 分片传完 → 解包 → SHA256+Pillow 校验 → 原子归并进权威 blobs。"""
import hashlib, json, os, subprocess, sys, time

BASE = "/yzp/zhaozy/yangzepeng/0905/demiwtg/state/curation/image_backfill_full_v1"
PARTS = os.path.join(BASE, "sg_parts")
STAGE = os.path.join(BASE, "sg_merge_staging")
BLOBS = "/yzp/zhaozy/yangzepeng/0905/demiwtg/datasets/demiwtg/blobs"
SUFFIX = ["aa","ab","ac","ad","ae","af","ag","ah","ai","aj","ak","al"]
LOG = open(os.path.join(BASE, "auto_merge.log"), "a", buffering=1)
def log(m): print(time.strftime("[%F %T]"), m, file=LOG, flush=True)

# 远端期望大小
remote = {}
out = subprocess.run(["ssh","sg-master","stat -c '%s %n' /tmp/sgrec12_part_*"],
                     capture_output=True, text=True, timeout=60).stdout
for line in out.strip().splitlines():
    sz, name = line.split()
    remote[os.path.basename(name)] = int(sz)

# 1) 等待分片全部到齐且 rsync 退出
while True:
    ok = all(os.path.exists(os.path.join(PARTS,p)) and
             os.path.getsize(os.path.join(PARTS,p)) == remote.get(p,-1) for p in SUFFIX)
    if ok: break
    n = sum(1 for p in SUFFIX if os.path.exists(os.path.join(PARTS,p)))
    log(f"等待分片完成：{n}/12 个文件在位")
    time.sleep(300)

log("12 分片全部到齐，开始解包")
os.makedirs(STAGE, exist_ok=True)
r = subprocess.run(f"cat {' '.join(os.path.join(PARTS,'sgrec12_part_'+p) for p in SUFFIX)} | tar xf - -C {STAGE}",
                   shell=True)
if r.returncode != 0:
    log(f"tar 解包失败 rc={r.returncode}"); sys.exit(1)

# 2) 校验并归并（恢复目录布局 recovered/blobs/aa/sha.ext）
src_root = os.path.join(STAGE, "recovered", "blobs")
stats = {"merged":0, "skipped_existing":0, "sha_bad":0, "decode_bad":0, "no_ext_match":0}
records = []
from PIL import Image
for aa in os.listdir(src_root):
    d = os.path.join(src_root, aa)
    for fn in os.listdir(d):
        stem, _, ext = fn.rpartition(".")
        src = os.path.join(d, fn)
        rec = {"file": f"blobs/{aa}/{fn}", "size": os.path.getsize(src)}
        data = open(src, "rb").read()
        if hashlib.sha256(data).hexdigest() != stem:
            stats["sha_bad"] += 1; rec["result"] = "sha_mismatch"; records.append(rec); continue
        try:
            img = Image.open(os.path.join(src)); img.load()
        except Exception:
            stats["decode_bad"] += 1; rec["result"] = "decode_fail"; records.append(rec); continue
        dst_dir = os.path.join(BLOBS, aa)
        dst = os.path.join(dst_dir, fn)
        os.makedirs(dst_dir, exist_ok=True)
        if os.path.exists(dst):
            stats["skipped_existing"] += 1; rec["result"] = "exists_skip"
        else:
            tmp = dst + ".merging"
            with open(tmp, "wb") as f: f.write(data)
            os.replace(tmp, dst)
            stats["merged"] += 1; rec["result"] = "merged"
        records.append(rec)
        if len(records) % 2000 == 0: log(f"已处理 {len(records)}：{stats}")
log(f"完成：{stats}")
with open(os.path.join(BASE, "sg_recovered_imported.json"), "w") as f:
    json.dump({"stats": stats, "records": records}, f, ensure_ascii=False)
