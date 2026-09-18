#!/usr/bin/env python3
"""合并 8 片 join2 分片产物 → 与单机版一致的终局清单+终报(2026-09-17)。"""
import glob
import gzip
import json
from collections import defaultdict

ST = "/home/ubuntu/demi/raw/state/"

shards = sorted(glob.glob(ST + "stats.shard*.json"),
                key=lambda p: int(p.split("shard")[1].split(".")[0]))
assert len(shards) >= 8, f"expect >=8 shards, got {len(shards)}"
NS = len(shards)
S = [json.load(open(p, encoding="utf-8")) for p in shards]

# 全账本口径取 shard0（每片相同）
s0 = S[0]
agg = defaultdict(int)
for s in S:
    for k, v in s["small_class_counts"].items():
        agg[k] += v
ok_small = sum(c for k, c in agg.items()
               if k not in ("html", "empty", "missing", "err"))

err_cnt = defaultdict(int)
tier_html = defaultdict(int)
decile_html = defaultdict(int)
day_html = defaultdict(int)
ext_html = defaultdict(int)
for s in S:
    for k, v in s["html_err_codes"].items():
        err_cnt[k] += v
    for k, v in s["html_by_tier"].items():
        tier_html[k if k != "None" else "None"] += v
    for k, v in s["html_by_ledger_decile"].items():
        decile_html[k] += v
    for k, v in s["html_by_day_utc"].items():
        day_html[k] += v
    for k, v in s["html_by_ext"].items():
        ext_html[k] += v

# 大样本上界验证（若已跑）
large = {}
try:
    with open(ST + "sniff_large.tsv", encoding="utf-8") as f:
        for line in f:
            rel, cls, err = line.rstrip("\n").split("\t")
            large[rel] = (cls, err)
except FileNotFoundError:
    pass
large_html = sum(1 for cls, _ in large.values() if cls == "html")

day_rows = s0["day_rows_total"]
stats = {
    "ledger_rows": s0["ledger_rows"], "badjson": s0["badjson"],
    "sniffed_unique_blobs": sum(s["sniffed_unique_blobs"] for s in S),
    "small_class_counts": dict(agg),
    "html_rows": sum(s["html_rows"] for s in S),
    "html_bytes": sum(s["html_bytes"] for s in S),
    "html_unique_shas": sum(s["html_unique_shas"] for s in S),
    "html_err_codes": dict(sorted(err_cnt.items(), key=lambda x: -x[1])),
    "html_by_tier": dict(tier_html),
    "html_by_ext": dict(sorted(ext_html.items(), key=lambda x: -x[1])[:10]),
    "html_by_ledger_decile": dict(sorted(decile_html.items())),
    "html_by_day_utc": {k: f"{day_html.get(k, 0)}/{day_rows[k]}"
                        for k in sorted(day_rows)},
    "other_rows": sum(s["other_rows"] for s in S),
    "other_bytes": sum(s["other_bytes"] for s in S),
    "redownload_tasks_unique_qid_file":
        sum(s["redownload_tasks_unique_qid_file"] for s in S),
    "large_sample": {"checked": len(large), "html": large_html},
    "note_small_threshold_bytes": 6000,
    "note": "tasks/unique_shas 为各片直和(同任务跨片重复计), 去重口径见终报说明",
}
with open(ST + "audit_final_stats.json", "w", encoding="utf-8") as f:
    json.dump(stats, f, ensure_ascii=False, indent=1)

for name in ("poison_html_rows", "poison_other_rows"):
    with gzip.open(f"{ST}{name}.jsonl.gz", "wt", encoding="utf-8") as out:
        n = 0
        for i in range(NS):
            with gzip.open(f"{ST}{name}.shard{i}.jsonl.gz", "rt",
                           encoding="utf-8") as f:
                for line in f:
                    out.write(line)
                    n += 1
    print(f"merged {name}: {n:,} rows")

lines = ["# kb 图池审计·终报(2026-09-17, r 舰队分布式 join2)", ""]
for k, v in stats.items():
    lines.append(f"- **{k}**: {v}")
lines += ["", ">6KB 档为抽样验证(非全量);重收清单: "
          "poison_html_rows.jsonl.gz / poison_other_rows.jsonl.gz / "
          "thumb1200_rows.jsonl.gz"]
with open(ST + "audit_report.md", "w", encoding="utf-8") as f:
    f.write("\n".join(lines) + "\n")
print(json.dumps(stats, ensure_ascii=False, indent=1))
