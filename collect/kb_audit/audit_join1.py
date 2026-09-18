#!/usr/bin/env python3
"""审计联合第一步(2026-09-17): 本地账本 × COS inventory 对账。

产出(raw/state/):
- cand_small.tsv        唯一 blob ≤6KB(错误页嗅探候选, 全量)
- cand_large_sample.tsv 大于 6KB 的随机 2 万样本(HTML 上界验证)
- rows_missing.jsonl.gz 账本有、库存无(文件丢失行)
- rows_sizemiss.tsv.gz  page_bytes ≠ 库存 size(截断/改动行)
- thumb1200_rows.jsonl.gz 缩略图行(重收清单之二)
- join1_stats.json      各项计数
"""
import gzip
import json
import random
from collections import defaultdict

INV = "/home/ubuntu/demi/raw/state/blobs_inventory.tsv"
LED = "/home/ubuntu/demi/raw/state/qid_images.jsonl"
ST = "/home/ubuntu/demi/raw/state/"

SMALL_MAX = 6000
LARGE_SAMPLE = 20000

inv = {}
markers = 0
with open(INV, encoding="utf-8") as f:
    for line in f:
        rel, sz = line.rstrip("\n").rsplit("\t", 1)
        if rel.endswith("/"):
            markers += 1
            continue
        inv[rel] = int(sz)
print(f"inventory: {len(inv):,} 对象 (+{markers} 目录标记)")

referenced = set()
rows = miss_rows = sizemiss = thumb_rows = badjson = 0
bytes_ledger = 0
small = {}
large_pool = []
per_ext = defaultdict(lambda: [0, 0])
tier_cnt = defaultdict(int)
no_dims = 0
zero_bytes = 0

f_miss = gzip.open(ST + "rows_missing.jsonl.gz", "wt", encoding="utf-8")
f_sm = gzip.open(ST + "rows_sizemiss.tsv.gz", "wt", encoding="utf-8")
f_thumb = gzip.open(ST + "thumb1200_rows.jsonl.gz", "wt", encoding="utf-8")

with open(LED, encoding="utf-8") as f:
    for idx, line in enumerate(f):
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            badjson += 1
            continue
        rows += 1
        rel = r["path"]
        referenced.add(rel)
        pb = r.get("page_bytes") or 0
        bytes_ledger += pb
        tier_cnt[r.get("tier")] += 1
        ext = r.get("ext") or "?"
        per_ext[ext][0] += 1
        per_ext[ext][1] += pb
        if not r.get("width") or not r.get("height"):
            no_dims += 1
        if pb == 0:
            zero_bytes += 1
        sz = inv.get(rel)
        if sz is None:
            miss_rows += 1
            f_miss.write(line)
        elif sz != pb:
            sizemiss += 1
            f_sm.write(f"{idx}\t{rel}\t{pb}\t{sz}\n")
        elif sz <= SMALL_MAX:
            small.setdefault(rel, sz)
        else:
            large_pool.append((rel, sz))
        if r.get("tier") == "thumb1200":
            thumb_rows += 1
            f_thumb.write(line)

for fh in (f_miss, f_sm, f_thumb):
    fh.close()

random.seed(20260917)
large_sample = random.sample(large_pool, min(LARGE_SAMPLE, len(large_pool)))
with open(ST + "cand_small.tsv", "w", encoding="utf-8") as f:
    for rel, sz in small.items():
        f.write(f"{rel}\t{sz}\n")
with open(ST + "cand_large_sample.tsv", "w", encoding="utf-8") as f:
    for rel, sz in large_sample:
        f.write(f"{rel}\t{sz}\n")

orphans = len(inv) - len(referenced & inv.keys())
orphan_bytes = sum(sz for rel, sz in inv.items() if rel not in referenced)
stats = {
    "inventory_objects": len(inv), "dir_markers": markers,
    "ledger_rows": rows, "badjson": badjson,
    "tier": dict(tier_cnt),
    "ledger_bytes_total": bytes_ledger,
    "missing_blob_rows": miss_rows,
    "size_mismatch_rows": sizemiss,
    "small_candidates_unique": len(small),
    "large_sample_size": len(large_sample),
    "large_total_unique": len(large_pool),
    "thumb1200_rows": thumb_rows,
    "orphan_objects": orphans, "orphan_bytes": orphan_bytes,
    "rows_no_dims": no_dims, "rows_zero_bytes": zero_bytes,
    "per_ext": {k: [v[0], v[1]] for k, v in
                sorted(per_ext.items(), key=lambda x: -x[1][1])[:15]},
}
with open(ST + "join1_stats.json", "w", encoding="utf-8") as f:
    json.dump(stats, f, ensure_ascii=False, indent=1)
print(json.dumps(stats, ensure_ascii=False, indent=1))
