#!/bin/bash
# 单 worker 恢复 + 429 死信清理。用法: bash fleet_single_restore.sh <n>
set -u
n=$1
cd ~/wk_backfill
# 杀掉所有该分片的 fleet_curl 进程与看门狗（含 e/o 双 worker 与重复看门狗）
pkill -f "fleet_curl_watchdog.sh ${n}\$" 2>/dev/null
pkill -f "fleet_curl.py --candidates wm_${n}" 2>/dev/null
sleep 1
# 清理可重试类死信（429/5xx/网络类），确定性失败（404/sha_mismatch/capped）保留
python3 - "$n" <<'PYEOF'
import os, sys
n = sys.argv[1]
p = f"run_{n}/meta/dead.jsonl"
if os.path.exists(p):
    keep = [l for l in open(p) if not any(r in l for r in
            ("http:429", "http:5", "net:", "curl:", "subproc_timeout"))]
    open(p, "w").writelines(keep)
    print(f"dead kept {len(keep)}")
PYEOF
setsid nohup bash fleet_curl_watchdog.sh "$n" > "curl${n}.log" 2>&1 < /dev/null &
echo "single worker restored for shard $n"
