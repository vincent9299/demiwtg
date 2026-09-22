#!/usr/bin/env python3
"""cn1 侧：从广州 COS 内网读对象，tar 流写 stdout（不落盘）。stdin 传 job JSON。"""
import sys, json, tarfile, io
sys.path.insert(0, "/tmp/costest")
import cos_util

cos_util.HOST = "lhcos-cee54-1256345599.cos-internal.ap-guangzhou.myqcloud.com"
PREFIX = "lhcos-data/demiwtg-data/datasets/demiwtg/blobs"
jobs = json.load(sys.stdin)
tar = tarfile.open(fileobj=sys.stdout.buffer, mode="w|", format=tarfile.USTAR_FORMAT)
ok = miss = 0
for j in jobs:
    sha, ext = j["sha"], j["ext"]
    key = f"{PREFIX}/{sha[:2]}/{sha}.{ext}"
    st, h, b = cos_util._call("GET", key)
    if st != 200:
        miss += 1
        continue
    ti = tarfile.TarInfo(f"{sha[:2]}/{sha}.{ext}")
    ti.size = len(b)
    tar.addfile(ti, io.BytesIO(b))
    ok += 1
tar.close()
sys.stderr.write(json.dumps({"streamed": ok, "miss": miss}) + "\n")
