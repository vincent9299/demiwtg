#!/bin/bash
# 全舰队停止:先杀看门狗(防自动重启)再杀下载器;逐台报告残留进程数
SSHOPTS="-o BatchMode=yes -o ConnectTimeout=15"
for h in r1 r2 r3 r4 r5 r6 r7 r8 r9 r10 r11 r12 r13 r14 r15 r16 r17 r18 r19 r20; do
  printf "== %s\n" "$h"
  timeout 40 ssh $SSHOPTS "$h" '
    for p in $(pgrep -f run_fetch.sh); do [ "$p" != "$$" ] && kill "$p" 2>/dev/null; done
    sleep 1
    for p in $(pgrep -f sdc_fetch_fleet.py); do [ "$p" != "$$" ] && kill "$p" 2>/dev/null; done
    sleep 2
    for p in $(pgrep -f sdc_fetch_fleet.py); do [ "$p" != "$$" ] && kill -9 "$p" 2>/dev/null; done
    LEFT=$(pgrep -f "run_fetch.sh|sdc_fetch_fleet.py" | grep -v $$ | wc -l)
    echo "residual_procs=$LEFT  ledger=$(wc -l < ~/sdc_fetch/ledger.jsonl 2>/dev/null || echo 0)"
  ' 2>&1 | tail -1
done
echo "STOP-SWEEP-DONE"
