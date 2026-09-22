#!/bin/bash
# cn1 消费 daemon 常驻守护: 崩溃自动重启, 30s 退避。
# 用法(cn1): nohup ./run_pan_consume.sh >> ~/pan123-relay/supervisor.log 2>&1 &
# 读回抽样默认 2%(daemon 内置), 试点期可 export READBACK_PCT=100 覆盖后重启。
set -u
cd "$(dirname "$0")"
while true; do
    echo "[$(date '+%m-%d %H:%M:%S')] 拉起 cos123_relay"
    python3 cos123_relay.py
    rc=$?
    echo "[$(date '+%m-%d %H:%M:%S')] 退出码 $rc, 30s 后重启"
    sleep 30
done
