#!/bin/bash
# B4 fleet 部署：vendor demiflow_collect + b4_op + 凭证 → r1-r20 ~/wk_b4/，错峰发射 worker。
# 幂等：launch_one_b4.sh 自带在跑检测；重复执行只补漏。
# 用法: bash deploy_b4_fleet.sh [n台] [tmdb_workers] [gbif_workers]
set -u
N=${1:-20}
NT=${2:-3}
NG=${3:-3}
B4=/yzp/zhaozy/yangzepeng/0905/demiwtg/collect/batch4
DF=/yzp/zhaozy/yangzepeng/0905/demiflow/demiflow/collect

V=/tmp/b4_vendor/demiflow_collect
rm -rf /tmp/b4_vendor && mkdir -p $V
echo '"""vendor 自 demiflow/collect（部署时拷贝，勿手改）。"""' > $V/__init__.py
cp $DF/cosio.py $DF/cosqueue.py $DF/queue_runner.py $DF/exec_curl.py $V/

i=0
for h in r1 r2 r3 r4 r5 r6 r7 r8 r9 r10 r11 r12 r13 r14 r15 r16 r17 r18 r19 r20; do
  i=$((i+1)); [ $i -gt $N ] && break
  echo "== $h"
  timeout 60 ssh -o ConnectTimeout=10 -o BatchMode=yes $h 'mkdir -p ~/wk_b4' 2>/dev/null
  scp -q -o ConnectTimeout=10 -r /tmp/b4_vendor/demiflow_collect $h:~/wk_b4/ 2>/dev/null
  scp -q -o ConnectTimeout=10 $B4/b4_op.py $B4/launch_one_b4.sh \
      /yzp/zhaozy/yangzepeng/0905/demiwtg/collect/image_backfill/.cos_creds \
      /yzp/zhaozy/yangzepeng/0905/demiwtg/collect/batch5/.tmdb_token \
      $h:~/wk_b4/ 2>/dev/null
  timeout 60 ssh -o ConnectTimeout=10 -o BatchMode=yes $h \
    "cd ~/wk_b4 && chmod 600 .cos_creds 2>/dev/null; chmod +x launch_one_b4.sh b4_op.py 2>/dev/null; true" 2>/dev/null
  # 发射 worker（t1..tN / g1..gN）
  for j in $(seq 1 $NT); do
    timeout 40 ssh -o ConnectTimeout=10 -o BatchMode=yes $h "bash ~/wk_b4/launch_one_b4.sh tmdb t$j 3" 2>/dev/null | tail -1
  done
  for j in $(seq 1 $NG); do
    timeout 40 ssh -o ConnectTimeout=10 -o BatchMode=yes $h "bash ~/wk_b4/launch_one_b4.sh gbif g$j 3" 2>/dev/null | tail -1
  done
  sleep 1.5
done
echo "B4 部署+发射完成（$N 台 × ${NT}tmdb+${NG}gbif）"
