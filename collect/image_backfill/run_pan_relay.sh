#!/bin/bash
# 123pan 中继常驻守护(sg 机用): 崩溃自动重启, 30s 退避。
# 用法: nohup ./run_pan_relay.sh <shard-N/M> >> ~/pan123-relay/supervisor.log 2>&1 &
# 例:  nohup ./run_pan_relay.sh 0/2 >> ~/pan123-relay/supervisor.log 2>&1 &
set -u
cd "$(dirname "$0")"
SHARD="${1:-0/1}"
while true; do
    echo "[$(date '+%m-%d %H:%M:%S')] 拉起 cos_relay_push --shard $SHARD"
    python3 cos_relay_push.py --shard "$SHARD"
    rc=$?
    echo "[$(date '+%m-%d %H:%M:%S')] 退出码 $rc, 30s 后重启"
    sleep 30
done
