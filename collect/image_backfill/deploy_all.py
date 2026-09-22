#!/usr/bin/env python3
"""61 出口全量部署：读 proxies/master_final.txt + 体检排除表，
生成分片并逐台部署 worker（错峰 2s），幂等（已跑的跳过）。"""
import glob
import json
import os
import subprocess
import sys
import time

HUB = "/yzp/zhaozy/yangzepeng/0905/demiwtg/collect/image_backfill/checkpoints/hub"
PENDING = os.path.join(HUB, "pending_all")
FC = "/yzp/zhaozy/yangzepeng/0905/demiwtg/collect/image_backfill/fleet_curl.py"
UAS = [l.strip() for l in open(os.path.join(HUB, "ua_pool.txt")) if l.strip()]
# 中央分配表：每出口唯一 UA（assign_uas.py 生成）
UA_ASSIGN = {}
for _l in open(os.path.join(HUB, "ua_assign.tsv"), encoding="utf-8"):
    _k, _, _v = _l.rstrip("\n").partition("\t")
    if _k and _v:
        UA_ASSIGN[_k] = _v

def run(cmd, timeout=60):
    return subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)

# 1) 代理池（全部 61 个，格式 ip:port:user:pass）
pool = [l.strip() for l in open(os.path.join(HUB, "proxies", "one_per_subnet.txt")) if l.strip()]
print(f"pool: {len(pool)}", flush=True)

# 2) 分片：pending 并集 -> 61 份
rows = {}
for f in glob.glob(os.path.join(PENDING, "r_*.jsonl")):
    for l in open(f):
        try:
            r = json.loads(l)
        except Exception:
            continue
        rows.setdefault(r["s"], l)
items = list(rows.values())
k = len(pool)
print(f"pending unique: {len(items)}", flush=True)
slice_files = []
for i in range(k):
    p = os.path.join(PENDING, f"ps_{i:02d}.jsonl")
    with open(p, "w") as o:
        for j, l in enumerate(items):
            if j % k == i:
                o.write(l if l.endswith("\n") else l + "\n")
    slice_files.append(p)

# 3) 部署（顺序错峰；scp 分片+脚本，远程 pgrep 幂等启动）
COS_UTIL = os.path.join(os.path.dirname(FC), "cos_util.py")
CREDS = os.path.join(os.path.dirname(FC), ".cos_creds")
COS_PREFIX = "lhcos-data/demiwtg-data/datasets/demiwtg/blobs"
SKIP_HOSTS = set(x for x in os.environ.get("SKIP_HOSTS", "").split(",") if x)
ALLOWED = [f"r{k}" for k in range(1, 21) if f"r{k}" not in SKIP_HOSTS]
ok = 0
for i, (spec, slice_f) in enumerate(zip(pool, slice_files)):
    ip, port, user, pwd = spec.split(":")
    h = f"r{i % 20 + 1}"
    if h in SKIP_HOSTS:
        # 被排除机的 worker 重映射到可用机（UA 仍按代理 IP 分配，与宿主机无关）
        h = ALLOWED[i % len(ALLOWED)]
        print(f"w{i} 原定 {f'r{(i) % 20 + 1}'} 被排除，重映射到 {h}", flush=True)
    ua = UA_ASSIGN.get(f"proxy:{ip}") or UAS[(i * 7) % len(UAS)]   # 出口唯一 UA，缺表回退轮换
    host_ua = UA_ASSIGN.get(f"host:{h}", "")                        # 该机直连身份（ua.env/看门狗用）
    proxy = f"http://{user}:{pwd}@{ip}:{port}"
    if run(f'''timeout 25 ssh -o ConnectTimeout=12 {h} "pgrep -f \\"[p]s_{i:02d}\\\\.jsonl\\"" >/dev/null 2>&1''').returncode == 0:
        print(f"w{i} already running on {h}", flush=True)
        ok += 1
        continue
    r1 = run(f"timeout 60 scp -q -o ConnectTimeout=12 {FC} {COS_UTIL} {CREDS} {HUB}/ua_pool.txt {HUB}/ua_assign.tsv {slice_f} {h}:~/wk_backfill/")
    if r1.returncode != 0:
        print(f"w{i} scp FAIL {h}: {r1.stderr[:80]}", flush=True)
        continue
    run(f"timeout 30 ssh -o ConnectTimeout=12 {h} 'chmod 600 ~/wk_backfill/.cos_creds; "
        f"printf \"UA=%s\\n\" \"{host_ua}\" > ~/wk_backfill/ua.env'")
    r2 = run(f"""timeout 40 ssh -o ConnectTimeout=12 {h} "cd ~/wk_backfill && setsid nohup python3 -u fleet_curl.py --candidates ps_{i:02d}.jsonl --out-dir run_ps{i:02d} --proxy '{proxy}' --ua '{ua}' --rps-start 0.25 --cos-prefix {COS_PREFIX} > pz{i:02d}.log 2>&1 < /dev/null & echo launched" """.strip())
    print(f"w{i} -> {h} via {ip}: {r2.stdout.strip() or r2.stderr[:60]}", flush=True)
    if "launched" in r2.stdout or r2.returncode == 0:
        ok += 1
    time.sleep(2)
print(f"deploy done: {ok}/{k}", flush=True)
