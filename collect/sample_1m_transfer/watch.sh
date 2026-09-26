#!/bin/bash
# 单次值守:睡 560s,对比上次状态算速率,卡死告警
D=/yzp/zhaozy/yangzepeng/0905/demiwtg/collect/sample_1m_transfer
ST=$D/watch.state
sleep 560
now=$(date "+%m-%d %H:%M")
ldone=$(wc -l < $D/done.sha 2>/dev/null || echo 0)
lfail=$(wc -l < $D/fail.sha 2>/dev/null || echo 0)
pgrep -fx "python3 pull_driver.py" > /dev/null && palive=Y || palive=N
prev=$(cat $ST 2>/dev/null)
if [ -n "$prev" ]; then
  pld=$(echo "$prev" | awk "{print \$1}"); pts=$(echo "$prev" | awk "{print \$2}")
  dp=$((ldone - pld)); dt=$(( $(date +%s) - pts ))
  [ $dt -gt 0 ] && rpm=$((dp * 60 / dt)) || rpm=0
else
  rpm="?"
fi
echo "$ldone $(date +%s)" > $ST
statline=$(tail -1 $D/escort.status)
alert=""
[ "$palive" = "N" ] && alert="!!PULL_DEAD"
[ "$rpm" != "?" ] && [ "$rpm" -lt 20 ] && [ "$palive" = "Y" ] && alert="$alert !!PULL_STALLED(${rpm}/min)"
echo "$now done=$ldone rate=${rpm}/min alive=$palive fail=$lfail $alert"
[ -n "$alert" ] && echo "$ALERT_DETAIL: $statline"
