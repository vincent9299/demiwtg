#!/usr/bin/env python3
"""审计联合第二步·分片版(2026-09-17, r 舰队分布式): 单片嗅探结果 × 全账本。

与单机 audit_join2.py 数学等价: 每片只载入 sniffout_0{N}.tsv 的 map(约1M项,
~200MB, 2GB 小机可容), 流式过全账本, 只处理 rel 命中本片的行。
8 片在各节点并行跑完 → merge_audit.py 合并产出与单机版一致的清单+终报。

用法: python3 audit_join2_shard.py <shard 0-7>
产出(raw/state/): poison_html_rows.shard{N}.jsonl.gz / poison_other_rows.shard{N}.jsonl.gz
                 / stats.shard{N}.json
"""
import gzip
import json
import datetime
import sys
from collections import defaultdict

ST = "/home/ubuntu/demi/raw/state/"
LED = ST + "qid_images.jsonl"

shard = int(sys.argv[1])
sniff = {}
VALS = {}  # (cls,err) 元组驻留, 防止每行新建字符串元组
with open(f"{ST}sniffout_0{shard}.tsv", encoding="utf-8") as f:
    for line in f:
        rel, cls, err = line.rstrip("\n").split("\t")
        v = VALS.setdefault((cls, err), (cls, err))
        sniff[rel] = v
print(f"[shard{shard}] 嗅探载入: {len(sniff):,} 唯一 blob", flush=True)

rows = badjson = 0
html_rows = other_rows = 0
html_bytes = other_bytes = 0
tasks_poison = set()
cls_cnt = defaultdict(int)
err_cnt = defaultdict(int)
tier_html = defaultdict(int)
decile_html = defaultdict(int)
day_html = defaultdict(int)
day_rows = defaultdict(int)
uniq_html_sha = set()
ext_html = defaultdict(int)

f_h = gzip.open(f"{ST}poison_html_rows.shard{shard}.jsonl.gz", "wt", encoding="utf-8")
f_o = gzip.open(f"{ST}poison_other_rows.shard{shard}.jsonl.gz", "wt", encoding="utf-8")

with open(LED, encoding="utf-8") as f:
    for idx, line in enumerate(f):
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            badjson += 1
            continue
        rows += 1
        day = datetime.datetime.fromtimestamp(
            r.get("fetched_at") or 0,
            datetime.timezone.utc).strftime("%m-%d")
        day_rows[day] += 1
        got = sniff.get(r["path"])
        if got is None:
            continue
        cls, err = got
        cls_cnt[cls] += 1
        if cls == "html":
            html_rows += 1
            html_bytes += r.get("page_bytes") or 0
            tasks_poison.add((r["qid"], r["commons_file"]))
            uniq_html_sha.add(r["sha256"])
            tier_html[r.get("tier")] += 1
            decile_html[min(idx * 10 // 8861354, 9)] += 1
            day_html[day] += 1
            ext_html[r.get("ext")] += 1
            if err:
                err_cnt[err] += 1
            f_h.write(json.dumps({**r, "err_code": err},
                                 ensure_ascii=False) + "\n")
        elif cls in ("empty", "missing", "err"):
            other_rows += 1
            other_bytes += r.get("page_bytes") or 0
            tasks_poison.add((r["qid"], r["commons_file"]))
            f_o.write(json.dumps({**r, "sniff": cls},
                                 ensure_ascii=False) + "\n")

f_h.close()
f_o.close()
stats = {
    "shard": shard,
    # 以下三个是全账本口径(每片都相同), 合并时取 shard0
    "ledger_rows": rows, "badjson": badjson, "day_rows_total": dict(day_rows),
    # 以下按片累加
    "sniffed_unique_blobs": len(sniff),
    "small_class_counts": dict(cls_cnt),
    "html_rows": html_rows, "html_bytes": html_bytes,
    "html_unique_shas": len(uniq_html_sha),
    "html_err_codes": dict(sorted(err_cnt.items(), key=lambda x: -x[1])),
    "html_by_tier": dict(tier_html),
    "html_by_ext": dict(sorted(ext_html.items(), key=lambda x: -x[1])[:10]),
    "html_by_ledger_decile": {str(k): v for k, v in sorted(decile_html.items())},
    "html_by_day_utc": dict(day_html),
    "other_rows": other_rows, "other_bytes": other_bytes,
    "redownload_tasks_unique_qid_file": len(tasks_poison),
}
with open(f"{ST}stats.shard{shard}.json", "w", encoding="utf-8") as f:
    json.dump(stats, f, ensure_ascii=False, indent=1)
print(f"[shard{shard}] DONE html={html_rows:,} other={other_rows:,} "
      f"tasks={len(tasks_poison):,}", flush=True)
