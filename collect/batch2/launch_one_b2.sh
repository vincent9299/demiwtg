#!/bin/bash
# r 机本地启动器（部署到 ~/wk_b2/launch_one_b2.sh，参数：<src> [lanes] [worker_id]）
# 零内联引号：ssh 只传 argv，彻底避开转义断裂坑（09-20 实战再犯）。
set -u
SRC=${1:?src}
LANES=${2:-4}
W=${3:-$(hostname -s)}
cd "$HOME/wk_b2" || exit 9
if pgrep -f "b2_op.py --worker "$W" --src $SRC" >/dev/null 2>&1; then
  echo "已在跑: $(pgrep -fc "b2_op.py --worker "$W" --src $SRC") proc"
  exit 0
fi
setsid nohup /usr/bin/python3 -u b2_op.py --worker "$W" --src "$SRC" \
  --lanes "$LANES" >> "b2_${SRC}.log" 2>&1 < /dev/null &
sleep 3
echo "worker=$W src=$SRC 存活=$(pgrep -fc "b2_op.py --worker "$W" --src $SRC")"
tail -2 "b2_${SRC}.log"
