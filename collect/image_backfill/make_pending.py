#!/usr/bin/env python3
"""在 fleet 机上生成本分片未完成行清单 pending_<n>.jsonl。用法: python3 make_pending.py <n>"""
import json, sys

n = sys.argv[1]
os = __import__("os")
os.chdir(__import__("os").path.expanduser("~/wk_backfill"))
done = {json.loads(l)["sha256"] for l in open(f"run_{n}/meta/done.jsonl")}
dead = {json.loads(l)["s"] for l in open(f"run_{n}/meta/dead.jsonl")}
out = [l for l in open(f"wm_{n}.jsonl") if json.loads(l)["s"] not in done | dead]
open(f"pending_{n}.jsonl", "w").writelines(out)
print("pending", len(out))
