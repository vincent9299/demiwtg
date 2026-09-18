#!/bin/bash
# ④ 分片发射器(参数化版, 2026-09-14 定稿)
# 战术要点(两夜实战沉淀, 沿革见 logs/night_watch_2026-09-12_14.log):
#   1. 起跑用 timeout 45 包裹: nohup-over-ssh 的会话悬挂是常态, 远端进程
#      nohup 后不受 ssh 客户端死活影响;
#   2. 幂等: 各机账本 ~/imgbuf/qid_images-local.jsonl 是续跑依据,
#      重启零重复(平台 resume.scan_counts 同款语义);
#   3. pkill 图案永远用 [x]xx 方括号形态, 防止与自身命令串同文自杀。
# 用法:
#   launch_shards.sh start <tasks_file> [log_name]   # 全 fleet 起跑
#   launch_shards.sh start-host <host> <i> <tasks_file> [log_name]
HOSTS="r1 r2 r3 r4 r5 r6 r7 r8 r9 r10 r11 r12 r13 r14 r15 r16 r17 r18 r19 r20 pipeline-a pipeline-b pipeline-c pipeline-d"
N=25
KB=/lhcos-data/demiwtg-data/datasets/demiwtg/kb

shard_of() {
  case $1 in
    localhost) echo 24 ;;
    pipeline-a) echo 20 ;; pipeline-b) echo 21 ;;
    pipeline-c) echo 22 ;; pipeline-d) echo 23 ;;
    r*) echo $((${1#r} - 1)) ;;
  esac
}

launch_one() {  # $1=host $2=idx $3=tasks $4=log
  local h=$1 i=$2 tasks=$3 log=${4:-images}
  if [ "$h" = localhost ]; then
    cd ~ && nohup env PYTHONPATH=/home/ubuntu/demi/demiwtg-data:/home/ubuntu/demi/demiflow \
      /home/ubuntu/demi/.venv/bin/python /home/ubuntu/demi/demiwtg-data/flow_images_batch.py \
      --concepts "$tasks" --blobs-root "$KB/blobs" \
      --manifest ~/imgbuf/qid_images-local.jsonl --shard $i/$N \
      > imgbuf/run/$log.log 2>&1 < /dev/null &
  else
    timeout 45 ssh -o BatchMode=yes -o ConnectTimeout=15 "$h" "cd ~ && nohup env \
      PYTHONPATH=\$HOME/demiwtg-data:\$HOME/demiwtg-flow \
      ~/venv/bin/python ~/demiwtg-data/flow_images_batch.py \
      --concepts '$tasks' --blobs-root '$KB/blobs' \
      --manifest ~/imgbuf/qid_images-local.jsonl --shard $i/$N \
      > imgbuf/run/$log.log 2>&1 < /dev/null &" </dev/null >/dev/null 2>&1
  fi
  echo "launched $h shard $i/$N"
}

case ${1:-} in
  start)
    TASKS=$2; LOG=${3:-images}
    i=0
    for h in $HOSTS; do launch_one "$h" "$i" "$TASKS" "$LOG"; i=$((i+1)); done
    launch_one localhost 24 "$TASKS" "$LOG"
    ;;
  start-host)
    launch_one "$2" "$(shard_of "$2")" "$3" "${4:-images}"
    ;;
  *)
    echo "usage: $0 start <tasks_file> [log] | start-host <host> <tasks> [log]" >&2
    exit 1
    ;;
esac
