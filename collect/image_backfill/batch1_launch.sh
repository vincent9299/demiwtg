#!/bin/bash
# 第 1 批重收启动器（kb_backfill 链路，2026-09-18）
# 用法: bash batch1_launch.sh <tasks_cos_key> <n_machines> [rps_start]
#   例: bash batch1_launch.sh lhcos-data/demiwtg-data/_staging_batch1/canary_poison_10k.jsonl 1 0.25
#       bash batch1_launch.sh lhcos-data/demiwtg-data/audit/2026-09-17/poison_html_rows.jsonl.gz 8
# 红线：须用户放行后才可运行；WM 任务全局串行（勿与 fleet_curl/SDC 并发）
set -u
KEY=$1; N=${2:-1}; RPS=${3:-0.25}
STG=/yzp/zhaozy/yangzepeng/0905/demiwtg-data/image_backfill
HUB=/yzp/zhaozy/yangzepeng/0905/demiwtg/state/curation/image_backfill_full_v1/hub
TAG=$(basename "$KEY" .jsonl); TAG=${TAG%.jsonl.gz}
for ((i=0; i<N; i++)); do
  h=r$((i+1))
  timeout 90 scp -q -o ConnectTimeout=15 $STG/kb_backfill.py $STG/fleet_curl.py $STG/cos_util.py $STG/.cos_creds $HUB/ua_pool.txt $HUB/ua_assign.tsv $h:~/wk_backfill/ 2>/dev/null
  timeout 30 ssh -o ConnectTimeout=15 $h "chmod 600 ~/wk_backfill/.cos_creds; printf 'UA=%s\n' \"\$(awk -F'\t' -v k=host:$h '\$1==k{print \$2}' ~/wk_backfill/ua_assign.tsv)\" > ~/wk_backfill/ua.env"
  # 任务清单：机器侧签名拉取（cos_creds 已就位）
  timeout 30 ssh -o ConnectTimeout=15 $h "cd ~/wk_backfill && python3 -c \"
import cos_util, urllib.request
k='$KEY'
sid,sk=cos_util.creds()
a=cos_util._sig('GET','/'+k,{},sid,sk)
r=urllib.request.Request(f'https://{cos_util.HOST}/{k}',headers={'authorization':a})
open('tasks_$TAG'+('.jsonl.gz' if k.endswith('.gz') else '.jsonl'),'wb').write(urllib.request.urlopen(r,timeout=600).read())
print('tasks ok')\""
  timeout 40 ssh -o ConnectTimeout=15 $h "cd ~/wk_backfill && setsid nohup python3 -u kb_backfill.py \
    --tasks tasks_$TAG.jsonl* --shard $i/$N \
    --manifest run_kb_${TAG}_s$i/manifest.jsonl \
    --rps-start $RPS --hard-cap-mb 64 \
    > kb_${TAG}_s$i.log 2>&1 < /dev/null & echo s$i@$h launched"
  sleep 3
done
echo "已发 $N 机（$TAG，rps=$RPS）。日志: ~/wk_backfill/kb_${TAG}_s*.log"