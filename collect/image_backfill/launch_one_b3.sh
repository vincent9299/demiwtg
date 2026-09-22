#!/bin/bash
# b3（SDC）单机启动器：~/wk_backfill/launch_one_b3.sh <identity> [rps] [lanes]
# 队列固定 queue-b3；身份沿用第 1 批 qw_env_<identity>。
set -u
W=${1:?identity}
RPS=${2:-0.9}
LANES=${3:-3}
cd "$HOME/wk_backfill" || exit 9
if pgrep -f "queue_worker.py --identity $W --queue" >/dev/null 2>&1; then
  echo "已在跑 identity=$W"
  exit 0
fi
setsid nohup /usr/bin/python3 -u queue_worker.py --identity "$W" \
  --queue lhcos-data/demiwtg-data/queue-b3 \
  --rps "$RPS" --lanes "$LANES" >> "qw_b3_${W}.log" 2>&1 < /dev/null &
sleep 3
echo "identity=$W 存活=$(pgrep -fc "queue_worker.py --identity $W --queue")"
tail -2 "qw_b3_${W}.log"
