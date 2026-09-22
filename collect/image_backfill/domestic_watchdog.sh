#!/bin/bash
# 国内源看门狗：静默死亡自动拉起，账本幂等；连续两轮无新增则收工
cd /yzp/zhaozy/yangzepeng/0905/demiwtg/collect/image_backfill
LOG=/yzp/zhaozy/yangzepeng/0905/demiwtg/collect/image_backfill/checkpoints
last=-1; stale=0
while true; do
  for s in 0 1; do
    PYTHONPATH=/yzp/zhaozy/yangzepeng/0905/demiflow:/yzp/zhaozy/yangzepeng/0905/demiwtg/collect/image_backfill \
      timeout 14400 /yzp/zhaozy/yangzepeng/0905/env/bin/python -u -m backfill \
      --candidates $LOG/candidates_domestic.jsonl.gz --shard $s/2 \
      --dataset $LOG/run --blob-root /yzp/zhaozy/yangzepeng/0905/demiwtg/datasets/demiwtg \
      --concurrency 24 --log-every 500 >> $LOG/domestic_shard${s}of2_a2.log 2>&1
  done
  cur=$(cat $LOG/run/meta/backfill-shard-0-of-2.jsonl $LOG/run/meta/backfill-shard-1-of-2.jsonl 2>/dev/null | wc -l)
  echo "[watchdog $(date +%H:%M)] done_total=$cur" >> $LOG/domestic_watchdog.log
  if [ "$cur" -le "$last" ]; then stale=$((stale+1)); else stale=0; fi
  [ $stale -ge 2 ] && echo "[watchdog] 收敛，退出" >> $LOG/domestic_watchdog.log && break
  last=$cur; sleep 10
done
