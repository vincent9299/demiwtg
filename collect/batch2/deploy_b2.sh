#!/bin/bash
# 第 2 批 fleet 部署：vendor demiflow_collect + b2_op + 凭证 → 20 机错峰发射。
# 用法: bash deploy_b2.sh <src> [n台]     如: bash deploy_b2.sh oi 20
# 前置: 该源队列已切批（cut_lists.py），r 机第 1 批 queue_worker 已收尾或让出带宽。
set -u
SRC=${1:?用法: deploy_b2.sh <oi|met|si|inat> [n台]}
N=${2:-20}
B2=/yzp/zhaozy/yangzepeng/0905/demiwtg/collect/batch2
DF=/yzp/zhaozy/yangzepeng/0905/demiflow/demiflow/collect

# 湖侧临时 vendor 目录（纯 stdlib 五件套）
V=/tmp/b2_vendor/demiflow_collect
rm -rf /tmp/b2_vendor && mkdir -p $V
echo '"""vendor 自 demiflow/collect（部署时拷贝，勿手改）。"""' > $V/__init__.py
cp $DF/cosio.py $DF/cosqueue.py $DF/queue_runner.py $DF/exec_curl.py $V/

i=0
for h in r1 r2 r3 r4 r5 r6 r7 r8 r9 r10 r11 r12 r13 r14 r15 r16 r17 r18 r19 r20; do
  i=$((i+1)); [ $i -gt $N ] && break
  echo "== $h"
  timeout 60 ssh -o ConnectTimeout=10 -o BatchMode=yes $h 'mkdir -p ~/wk_b2' 2>/dev/null
  scp -q -o ConnectTimeout=10 -r /tmp/b2_vendor/demiflow_collect $h:~/wk_b2/ 2>/dev/null
  scp -q -o ConnectTimeout=10 $B2/b2_op.py \
      /yzp/zhaozy/yangzepeng/0905/demiwtg/collect/image_backfill/.cos_creds \
      $h:~/wk_b2/ 2>/dev/null
  timeout 60 ssh -o ConnectTimeout=10 -o BatchMode=yes $h \
    "cd ~/wk_b2 && mv -f .cos_creds creds.tmp 2>/dev/null; mv -f creds.tmp .cos_creds 2>/dev/null; chmod 600 .cos_creds demiflow_collect/*.py 2>/dev/null; true" 2>/dev/null
  sleep 1.5
done
echo "vendor 部署完成（$N 台）——发射见 launch_b2.sh"
