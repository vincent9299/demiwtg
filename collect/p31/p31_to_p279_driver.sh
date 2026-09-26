#!/bin/bash
# 等 P31 链完成 → 完整性核对 → 自动起 P279 链
cd /home/ubuntu
while ! grep -q P31_EXTRACT_DONE p31_chain_run2.log 2>/dev/null; do sleep 30; done
echo "P31 done at $(date -u +%T)"
sleep 15
WC=$(wc -l < p31_all.tsv)
DN=$(grep -h "^w[0-9]* DONE" p31_workers.log | awk '{gsub(/,/,"",$3); s+=$3} END {print int(s)}')
echo "p31_all=$WC workerDONE=$DN"
if [ -n "$DN" ] && [ "$DN" -gt 0 ] && [ $((DN>WC?DN-WC:WC-DN)) -gt $((WC/20)) ]; then
  echo "MISMATCH>5% — 不自动起 P279,等人工"; exit 1
fi
# 杀残留 p31 workers(按 PID)
for p in $(pgrep -f "p31_extract.py workers"); do kill $p 2>/dev/null; done
sleep 2
bash p279_chain.sh > p279_chain.log 2>&1
echo "P279_CHAIN_EXIT $?"
