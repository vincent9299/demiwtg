#!/usr/bin/env python3
"""28 个子段代表 IP × 每段一个 worker 部署（避免段内互踩）。"""
import os
import subprocess
import time

HUB = "/yzp/zhaozy/yangzepeng/0905/demiwtg/state/curation/image_backfill_full_v1/hub"
FC = "/yzp/zhaozy/yangzepeng/0905/demiwtg-data/image_backfill/fleet_curl.py"
UAS = [l.strip() for l in open(os.path.join(HUB, "ua_pool.txt")) if l.strip()]
pool = [l.strip() for l in open(os.path.join(HUB, "proxies", "one_per_subnet.txt")) if l.strip()]
assert len(pool) == 28, len(pool)

def run(cmd, timeout=60):
    return subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)

ok = 0
for i, spec in enumerate(pool):
    ip, port, user, pwd = spec.split(":")
    h = f"r{i + 1}"          # r1..r20, 再折返 r8..；每机 1-2 个，段互不相同
    ua = UAS[i % len(UAS)]
    slice_f = os.path.join(HUB, "pending_all", f"ps_{i:02d}.jsonl")
    r = run(f"timeout 60 scp -q -o ConnectTimeout=12 {FC} {slice_f} {h}:~/wk_backfill/")
    if r.returncode != 0:
        print(f"w{i} scp FAIL {h}", flush=True)
        continue
    r = run(f"""timeout 40 ssh -o ConnectTimeout=12 {h} "cd ~/wk_backfill && setsid nohup python3 -u fleet_curl.py --candidates ps_{i:02d}.jsonl --out-dir run_ps{i:02d} --proxy 'http://{user}:{pwd}@{ip}:{port}' --ua '{ua}' > ps{i:02d}.log 2>&1 < /dev/null & echo launched" """.strip())
    print(f"w{i} -> {h} via {ip}: {r.stdout.strip() or r.stderr[:50]}", flush=True)
    ok += 1
    time.sleep(2)
print(f"deploy done: {ok}/28", flush=True)
