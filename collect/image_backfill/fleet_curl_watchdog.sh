#!/bin/bash
# fleet curl 看门狗：python 卡死/退出自动拉起（账本幂等），DONE 自然退出
# 用法: bash fleet_curl_watchdog.sh <n>  （n=00..22 分片号）
set -u
n=$1
cd ~/wk_backfill
while true; do
  OUT=$(python3 -u fleet_curl.py --candidates wm_${n}.jsonl --out-dir run_${n} 2>&1 | tail -3)
  echo "[$(date +%m-%d\ %H:%M:%S)] $OUT" >> run_${n}.watchdog.log
  case "$OUT" in *DONE*) break;; esac
  sleep 5
done
echo "[watchdog] shard $n 完成 $(date)" >> run_${n}.watchdog.log
