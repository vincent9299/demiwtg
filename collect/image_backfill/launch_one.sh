#!/bin/bash
# 用法: launch_one.sh <w> <nw> <proxy_spec|-> <ua|->
cd ~/wk_backfill
w=$1; nw=$2; proxy=$3; ua=$4
PX=""; [ "$proxy" != "-" ] && PX="--proxy http://$proxy"
UA=""; [ "$ua" != "-" ] && UA="--ua $ua"
setsid nohup python3 -u kb_backfill.py \
  --tasks tasks_poison_html_rows.jsonl.gz --shard $w/$nw \
  --manifest run_kb_poison_html_rows_w$w/manifest.jsonl \
  $PX $UA --rps-start 0.25 --hard-cap-mb 64 \
  > kb_poison_html_rows_w$w.log 2>&1 < /dev/null &
exit 0
