#!/bin/bash
# 全舰队重发下载看门狗(正确 I/N 分片格式 + 排除自身的清理)
# 用法: bash fleet_relaunch.sh 2>&1 | tee /tmp/fleet_relaunch.log
SSHOPTS="-o BatchMode=yes -o ConnectTimeout=15"
i=0
for h in r1 r2 r3 r4 r5 r6 r7 r8 r9 r10 r11 r12 r13 r14 r15 r16 r17 r18 r19 r20; do
  printf "== %s shard %s/20\n" "$h" "$i"
  timeout 40 ssh $SSHOPTS "$h" '
    for p in $(pgrep -f run_fetch.sh); do [ "$p" != "$$" ] && kill "$p" 2>/dev/null; done
    for p in $(pgrep -f sdc_fetch_fleet.py); do [ "$p" != "$$" ] && kill "$p" 2>/dev/null; done
    sleep 1
    mkdir -p ~/sdc_fetch ~/lake/meta
    (setsid bash -c "nohup bash /tmp/run_fetch.sh '"$i"'/20 >> ~/sdc_fetch/watchdog.log 2>&1 < /dev/null" &)
    sleep 2
    pgrep -f "run_fetch.sh '"$i"'/20" >/dev/null && echo "OK watchdog up shard='"$i"'/20" || echo "WARN not detected"
  ' 2>&1 | tail -1
  i=$((i+1))
done
echo "ALL-DONE"
