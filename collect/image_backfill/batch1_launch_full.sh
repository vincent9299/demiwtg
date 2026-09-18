#!/bin/bash
# 第 1 批顶配启动器：61 代理 worker + 20 直连 worker = 81 出口
# 用法: bash batch1_launch_full.sh <tasks_cos_key> [rps_start]
# 每机先拉一次任务清单，再错峰启动该机的 worker（61 代理前 61 个，后 20 个直连）
set -u
KEY=$1; RPS=${2:-0.25}
STG=/yzp/zhaozy/yangzepeng/0905/demiwtg/collect/image_backfill
HUB=/yzp/zhaozy/yangzepeng/0905/demiwtg/state/curation/image_backfill_full_v1/hub
NW=81
TAG=$(basename "$KEY" .jsonl); TAG=${TAG%.jsonl.gz}
# 1) 全机部署依赖 + 每机拉一次清单（20×1GB，分钟级）
for i in $(seq 1 20); do
  h=r$i
  timeout 120 scp -q -o ConnectTimeout=15 $STG/kb_backfill.py $STG/fleet_curl.py $STG/cos_util.py $STG/.cos_creds $HUB/ua_pool.txt $HUB/ua_assign.tsv $h:~/wk_backfill/ 2>/dev/null
  timeout 30 ssh -o ConnectTimeout=15 $h "chmod 600 ~/wk_backfill/.cos_creds; printf 'UA=%s\n' \"\$(awk -F'\t' -v k=host:$h '\$1==k{print \$2}' ~/wk_backfill/ua_assign.tsv)\" > ~/wk_backfill/ua.env"
  timeout 30 ssh -o ConnectTimeout=15 $h "cd ~/wk_backfill && test -s tasks_$TAG.jsonl.gz 2>/dev/null && echo tasks exist || python3 -c \"
import cos_util, urllib.request
k='$KEY'
sid,sk=cos_util.creds()
a=cos_util._sig('GET','/'+k,{},sid,sk)
r=urllib.request.Request('https://'+cos_util.HOST+'/'+k,headers={'authorization':a})
open('tasks_$TAG'+('.jsonl.gz' if k.endswith('.gz') else '.jsonl'),'wb').write(urllib.request.urlopen(r,timeout=900).read())
print('tasks ok')\"" || echo "$h 清单拉取失败"
  sleep 2
done
# 2) 81 worker 错峰启动（w0-60 代理，w61-80 直连）
i=0
while read -u 3 spec; do
  [ $i -ge 61 ] && break
  h=r$(( i % 20 + 1 )); w=$i
  ip=${spec%%:*}; rest=$(echo "$spec" | cut -d: -f2-)
  ua=$(awk -F'\t' -v k="proxy:$ip" '$1==k{print $2}' $HUB/ua_assign.tsv)
  timeout 40 ssh -o ConnectTimeout=15 $h "cd ~/wk_backfill && setsid nohup python3 -u kb_backfill.py --tasks tasks_$TAG.jsonl* --shard $w/$NW --manifest run_kb_${TAG}_w$w/manifest.jsonl --proxy 'http://$rest' --ua '$ua' --rps-start $RPS --hard-cap-mb 64 > kb_${TAG}_w$w.log 2>&1 < /dev/null & echo w$w-at-$h-px-$ip" < /dev/null
  i=$((i+1)); sleep 2
done 3< $HUB/proxies/master_final.txt
for w in $(seq 61 80); do
  h=r$(( (w-61) + 1 ))
  timeout 40 ssh -o ConnectTimeout=15 $h "cd ~/wk_backfill && setsid nohup python3 -u kb_backfill.py --tasks tasks_$TAG.jsonl* --shard $w/$NW --manifest run_kb_${TAG}_w$w/manifest.jsonl --rps-start $RPS --hard-cap-mb 64 > kb_${TAG}_w$w.log 2>&1 < /dev/null & echo w$w-at-$h-direct" < /dev/null
  sleep 2
done
echo "顶配部署指令已全部发出（$NW worker）"