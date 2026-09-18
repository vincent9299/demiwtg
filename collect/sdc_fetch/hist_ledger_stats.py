#!/usr/bin/env python3
"""验证历史 882 万图的真实获取层级与体积分布(qid_images 账本) + 模拟本批清单方案成本。"""
import gzip
import json
from collections import Counter

# ① 历史账本: tier 分布 / 各 tier 均值 / 总字节
tier_n = Counter()
tier_bytes = Counter()
n = 0
with gzip.open("/lhcos-data/demiwtg-data/datasets/demiwtg/kb/qid_images.jsonl.gz",
               "rt", encoding="utf-8") as f:
    for line in f:
        n += 1
        try:
            r = json.loads(line)
        except Exception:
            continue
        t = r.get("tier") or "unknown"
        tier_n[t] += 1
        tier_bytes[t] += r.get("page_bytes") or 0

print(f"rows={n:,}")
for t in sorted(tier_n, key=tier_n.get, reverse=True):
    tb = tier_bytes[t]
    print(f"  tier={t:10s} n={tier_n[t]:>10,} avg={tb/max(tier_n[t],1)/1024:>8.0f}KB "
          f"total={tb/1e9:>7.1f}GB")

# ② 本批清单 img_size 分布与方案模拟
import statistics
sizes = []
with gzip.open("/lhcos-data/demiwtg-data/datasets/demiwtg/kb/sdc_fetch/fetch_list.tsv.gz",
               "rt", encoding="utf-8", errors="surrogateescape") as f:
    for line in f:
        parts = line.rstrip("\n").split("\t")
        if len(parts) >= 4 and parts[3].isdigit():
            sizes.append(int(parts[3]))

sizes.sort()
n = len(sizes)
tot = sum(sizes)
print(f"\nfetch_list files={n:,} total={tot/1e12:.2f}TB avg={tot/n/1e6:.2f}MB")
print(f"  分位: p50={sizes[n//2]/1e6:.2f}MB p90={sizes[int(n*0.9)]/1e6:.2f}MB "
      f"p99={sizes[int(n*0.99)]/1e6:.2f}MB max={sizes[-1]/1e6:.1f}MB")

THUMB = 250 * 1024          # 1200px 缩略按 ~250KB 估
guard = 10 << 20
b_n = sum(1 for s in sizes if s > guard)
b_orig = sum(s for s in sizes if s <= guard)
plan_b = b_orig + b_n * THUMB
print(f"  >10MB 文件: {b_n:,} ({b_n/n*100:.1f}%) 占原字节 {sum(s for s in sizes if s > guard)/1e12:.2f}TB")
print(f"  方案B(历史规则,>10MB转缩略): ~{plan_b/1e12:.2f}TB")
guard2 = 1 << 20
c_n = sum(1 for s in sizes if s > guard2)
c_orig = sum(s for s in sizes if s <= guard2)
print(f"  >1MB 文件: {c_n:,} ({c_n/n*100:.1f}%)")
print(f"  方案C(>1MB转缩略): ~{(c_orig + c_n*THUMB)/1e12:.2f}TB")
