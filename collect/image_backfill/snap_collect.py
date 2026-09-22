#!/usr/bin/env python3
"""在 r 机上跑：扫描全部 run_kb_w*/manifest.jsonl，
输出去重成功行(全字段, 取 fetched_at 最新) + 暂时死信表 + 永久死信统计。
跳过 seed 最小行（无 sha256 字段的跳过标记）。"""
import json, glob, os, sys
from collections import Counter

PERM = {"http:404", "not_image", "over_cap"}
seen = {}
dead_perm = Counter()
dead_retry = {}

for f in sorted(glob.glob(os.path.expanduser("~/wk_backfill/run_kb_w*/manifest.jsonl"))):
    for line in open(f, encoding="utf-8", errors="replace"):
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except Exception:
            continue
        qid = r.get("qid"); cf = r.get("commons_file")
        if not qid or not cf or "sha256" not in r:   # seed 最小行跳过
            continue
        k = (qid, cf)
        m = r.get("miss")
        if m is None:
            ts = r.get("fetched_at") or 0
            if k not in seen or ts > seen[k][0]:
                seen[k] = (ts, line)
        elif m in PERM:
            dead_perm[m] += 1
        else:
            dead_retry[k] = m    # 暂时死信只留最近原因（覆盖即可）

with open("/tmp/snap_success.jsonl", "w") as o:
    for ts, line in seen.values():
        o.write(line + "\n")
with open("/tmp/snap_dead_retry.jsonl", "w") as o:
    for (qid, cf), m in dead_retry.items():
        o.write(json.dumps({"qid": qid, "commons_file": cf, "miss": m}) + "\n")
print(json.dumps({"success_unique": len(seen),
                  "dead_perm": dict(dead_perm),
                  "dead_retry": len(dead_retry)}))
