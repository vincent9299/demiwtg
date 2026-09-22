#!/bin/bash
# 美国代理池恢复监视：每15分钟探5个样本，>3个活即自动重建全 fleet
# 用法: setsid nohup bash px_recovery_watch.sh > /tmp/px_watch.log 2>&1 &
while true; do
  A=$(timeout 60 ssh -o BatchMode=yes m2 'ok=0; while read px; do rest=$(echo $px|cut -d: -f2-); a=$(curl -s --max-time 5 -x "http://$rest" http://ifconfig.me 2>/dev/null | head -c 5); [ -n "$a" ] && ok=$((ok+1)); done < /tmp/px5.txt; echo $ok' 2>/dev/null)
  echo "[$(date +%H:%M)] 美国代理存活样本: ${A:-0}/5"
  if [ "${A:-0}" -ge 3 ]; then
    echo "[$(date +%H:%M)] 恢复！自动重建全 fleet"
    python3 /yzp/zhaozy/yangzepeng/0905/demiwtg/collect/image_backfill/rebuild_fleet.py >> /tmp/px_watch.log 2>&1
    break
  fi
  sleep 900
done
