#!/bin/bash
# 第 2 批 fleet 发射：每机 1 个 b2 worker（lanes=4 并发下载），setsid 常驻。
# 用法: bash launch_b2.sh <src> [n台] [lanes]    如: bash launch_b2.sh oi 20 4
# 幂等：已在跑（按 --src 匹配 b2_op）的机跳过。
set -u
SRC=${1:?用法: launch_b2.sh <oi|met|si|inat> [n台] [lanes]}
N=${2:-20}
LANES=${3:-4}
i=0
for h in r1 r2 r3 r4 r5 r6 r7 r8 r9 r10 r11 r12 r13 r14 r15 r16 r17 r18 r19 r20; do
  i=$((i+1)); [ $i -gt $N ] && break
  echo -n "$h: "
  timeout 40 ssh -o ConnectTimeout=10 -o BatchMode=yes $h \
    "if pgrep -f \"[b]2_op.py.*--src $SRC\" >/dev/null; then echo 已在跑; else cd ~/wk_b2 && setsid nohup /usr/bin/python3 -u b2_op.py --worker $h --src $SRC --lanes $LANES >> b2_$SRC.log 2>&1 < /dev/null & sleep 2; echo \"启动(\$(pgrep -c -f \"[b]2_op.py.*--src $SRC\") proc)\"; fi" 2>&1
  sleep 1.5
done
echo "发射完成。监控: 各机 ~/wk_b2/b2_$SRC.log；湖侧队列快照:"
python3 - <<'PY'
import sys
sys.path.insert(0, '/yzp/zhaozy/yangzepeng/0905/demiflow')
from demiflow.collect.cosio import COSCreds, COSIO, build_host
from demiflow.collect.cosqueue import COSQueue
import os
os.chdir('/yzp/zhaozy/yangzepeng/0905/demiwtg/collect/batch2')
io = COSIO(COSCreds.from_file('/yzp/zhaozy/yangzepeng/0905/demiwtg/collect/image_backfill/.cos_creds'),
           build_host('lhcos-368f6-1256345599', 'ap-singapore'))
q = COSQueue(io, f'lhcos-data/demiwtg-data/queue-b2-{sys.argv[1] if len(sys.argv)>1 else SRC}')
s = q.snapshot()
print(f"queue-b2-{SRC}: batches={len(s.batches)} done={len(s.done)} 在途={len(s.claimed-s.done)} todo={len(s.todo)}")
PY
