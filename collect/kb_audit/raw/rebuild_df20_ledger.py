#!/usr/bin/env python3
# 从真相源 ~/qid_images_ext_df20.jsonl.gz 确定性重建: 去重后的 ext.gz + 投喂清单
import json, gzip, os
seen = set()
ext_path = os.path.expanduser("~/qid_images_ext_df20.jsonl.gz")
man_path = os.path.expanduser("~/lake/meta/image-shard-extdf20.jsonl")
rows = []
for l in gzip.open(ext_path, "rt"):
    r = json.loads(l)
    k = (r["qid"], r["sha256"])
    if k in seen: continue
    seen.add(k)
    rows.append(r)
print(f"去重后 {len(rows):,} 行")
tmp = ext_path + ".new"
with gzip.open(tmp, "wt", compresslevel=6) as f:
    for r in rows:
        f.write(json.dumps(r, ensure_ascii=False) + "\n")
os.replace(tmp, ext_path)
with open(man_path, "w") as f:
    for r in rows:
        f.write(json.dumps(r, ensure_ascii=False) + "\n")
print("ext.gz 与投喂清单均已重建")
