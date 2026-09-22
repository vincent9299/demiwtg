#!/bin/bash
# B4 单机启动器（r 机）：~/wk_b4/launch_one_b4.sh <src> <identity> [lanes]
# src = tmdb | gbif；队列固定 queue-b4-<src>。
set -u
SRC=${1:?tmdb|gbif}
W=${2:?identity}
LANES=${3:-3}
cd "$HOME/wk_b4" || exit 9
if pgrep -f "b4_op.py --worker $W --src $SRC" >/dev/null 2>&1; then
  echo "已在跑 $SRC/$W"
  exit 0
fi
setsid nohup /usr/bin/python3 -u b4_op.py --worker "$W" --src "$SRC" \
  --queue "lhcos-data/demiwtg-data/queue-b4-$SRC" \
  --lanes "$LANES" --home "$HOME/wk_b4" >> "qw_b4_${SRC}_${W}.log" 2>&1 < /dev/null &
sleep 3
echo "$SRC/$W 存活=$(pgrep -fc "b4_op.py --worker $W --src $SRC")"
tail -2 "qw_b4_${SRC}_${W}.log"
