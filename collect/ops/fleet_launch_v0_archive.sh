#!/bin/bash
# ④ fleet 分片发射器(③ 时代原版存档, 2026-09-13 晋升自 kb_night/qid_build/fleet_launch.sh)
# 战术沿革: 该串行起跑版存在 ssh 悬挂与 gzip 竞态两坑(见 logs/night_watch),
# 生产应使用 ops/launch_shards.sh 参数化版
set -u
BASE=/home/ubuntu/demi/kb_night/qid_build
FAT=$BASE/qid_concepts.fat.jsonl.gz
KB=/lhcos-data/demiwtg-data/datasets/demiwtg/kb
SSH="ssh -o BatchMode=yes -o ConnectTimeout=15"
N=25

# 1) 等 ③ 增肥产物
while [ ! -s "$FAT" ]; do sleep 60; done
echo "[fleet] fat 就绪 $(date +%H:%M:%S),分发概念文件"

# 2) 分发(25 机统一 ~/imgbuf/;本机直接放)
RS=$(seq 1 20 | sed 's/^/r/')
ALL="$RS pipeline-a pipeline-b pipeline-c pipeline-d"
for h in $ALL; do
  ( scp -q -o BatchMode=yes "$FAT" "$h:imgbuf/qid_concepts.fat.jsonl.gz" \
    && echo "[fleet] $h 概念已达" ) &
  while [ $(jobs -r | wc -l) -ge 6 ]; do wait -n; done
done
wait
mkdir -p ~/imgbuf && cp "$FAT" ~/imgbuf/

launch_dl() {  # $1=host $2=shard
  local h=$1 i=$2
  if [ "$h" = "localhost" ]; then
    (cd ~ && mkdir -p imgbuf/run && nohup env \
      PYTHONPATH=/home/ubuntu/demi/demiwtg-data:/home/ubuntu/demi/demiflow \
      DEMIWTG_CONTACT=${DEMIWTG_CONTACT:-} \
      /home/ubuntu/demi/.venv/bin/python \
      /home/ubuntu/demi/demiwtg-data/flow_images.py \
      --concepts ~/imgbuf/qid_concepts.fat.jsonl.gz \
      --blobs-root "$KB/blobs" \
      --manifest ~/imgbuf/qid_images-local.jsonl \
      --shard $i/$N --concurrency 8 \
      > imgbuf/run/images.log 2>&1 < /dev/null &)
  else
    $SSH "$h" "cd ~ && mkdir -p imgbuf/run && nohup env \
      PYTHONPATH=~/demiwtg-data:~/demiwtg-flow \
      DEMIWTG_CONTACT=${DEMIWTG_CONTACT:-} \
      ~/venv/bin/python ~/demiwtg-data/flow_images.py \
      --concepts ~/imgbuf/qid_concepts.fat.jsonl.gz \
      --blobs-root '$KB/blobs' \
      --manifest ~/imgbuf/qid_images-local.jsonl \
      --shard $i/$N --concurrency 8 \
      > imgbuf/run/images.log 2>&1 < /dev/null &" </dev/null
  fi
  echo "[fleet] $h 分片 $i/$N 直写起跑"
}

# 3) 25 分片全部直写起跑
i=0
for h in $RS; do launch_dl $h $i; i=$((i+1)); done
for h in pipeline-a pipeline-b pipeline-c pipeline-d; do
  launch_dl $h $i; i=$((i+1))
done
launch_dl localhost $i
echo "[fleet] 全部就位 $(date +%H:%M:%S)(25 下载 IP 全直写,无中转)"
