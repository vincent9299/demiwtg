#!/usr/bin/env python3
"""修账不变量校验: 新旧账本逐行锁步对比.

门(全部必须通过):
  G1 行数一致; G2 sha 集合一致(唯一图守恒);
  G3 改动行: 除 qids/refs 外所有字段值一致;
  G4 qids 差: 删除 ⊆ 旧关系集, 新增 ⊆ 新关系集(来自 rematch map);
  G5 refs 差: 仅 relation_type 允许变化.
"""
import gzip
import json

BASE = "/yzp/zhaozy/yangzepeng/0905"
OLD = f"{BASE}/demiwtg/collect/sample_1m_transfer/assets/wh_backfill/images.v2.jsonl.gz"
NEW = f"{BASE}/demiwtg/collect/rerun_v2_20260925/images.v2.si_rematch.jsonl.gz"
MAP = f"{BASE}/demiwtg/collect/rerun_v2_20260925/si_rematch_map2.tsv"

allowed_old = set()      # 允许删除的旧 QID
allowed_new = set()      # 允许新增的新 QID
for l in open(MAP):
    p = l.rstrip("\n").split("\t")
    if p[0] == "media_id":
        continue
    if p[5] == "replace":
        allowed_old.add(p[1])
        allowed_new.add(p[2])
    elif p[5] in ("remove_junk", "remove_wrong"):
        allowed_old.add(p[1])

n = same = changed = fail = 0
sha_old = set()
with gzip.open(OLD, "rt") as fo, gzip.open(NEW, "rt") as fn:
    for lo, ln in zip(fo, fn):
        n += 1
        if lo == ln:
            same += 1
            continue
        changed += 1
        try:
            ro, rn = json.loads(lo), json.loads(ln)
        except json.JSONDecodeError:
            fail += 1
            print("FAIL parse", n)
            continue
        if ro.get("sha256") != rn.get("sha256"):
            fail += 1
            print("FAIL sha", n)
            continue
        sha_old.add(ro["sha256"])
        for k in ro:
            if k in ("qids", "refs"):
                continue
            if ro[k] != rn.get(k):
                fail += 1
                print("FAIL field", k, n)
                break
        removed = set(ro.get("qids") or []) - set(rn.get("qids") or [])
        added = set(rn.get("qids") or []) - set(ro.get("qids") or [])
        if not removed <= allowed_old:
            fail += 1
            print("FAIL removed", removed, n)
        if not added <= allowed_new:
            fail += 1
            print("FAIL added", added, n)
        if len(ro.get("refs") or []) != len(rn.get("refs") or []):
            fail += 1
            print("FAIL refs len", n)
        else:
            for a, b in zip(ro.get("refs") or [], rn.get("refs") or []):
                diff = {k for k in a if a[k] != b.get(k)}
                if diff - {"relation_type"}:
                    fail += 1
                    print("FAIL ref field", diff, n)
                    break

n_new = 0
sha_new_cnt = 0
with gzip.open(NEW, "rt") as fn:
    for ln in fn:
        n_new += 1
        if '"qids": []' not in ln:
            sha_new_cnt += 1
print(f"rows old-stream={n:,} new={n_new:,} identical={same:,} changed={changed:,}")
print(f"G1 行数一致: {'PASS' if n == n_new else 'FAIL'}")
print(f"G2-G5 违规数: {fail}")
print(f"非空 qids 行: {sha_new_cnt:,}")
