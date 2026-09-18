#!/bin/bash
# fleet 启动脚本：在 SG 上运行，逐台启动并验证（避免并行 ssh 挂起）
# 用法: bash fleet_launch.sh <i>  （i=0..22, 0-19=>r1-r20, 20=>pipeline-b, 21=>pipeline-c, 22=>pipeline-d）
i=$1
if [ "$i" -lt 20 ]; then
  h=r$((i+1)); py="python3"
else
  case $i in 20) h=pipeline-b;; 21) h=pipeline-c;; 22) h=pipeline-d;; esac
  py="./venv/bin/python"
fi
n=$(printf "%02d" $i)
HUB=/yzp/zhaozy/yangzepeng/0905/demiwtg/state/curation/image_backfill_full_v1/hub
FC=/yzp/zhaozy/yangzepeng/0905/demiwtg/collect/image_backfill
# r 机直连出口的唯一 UA：按 ssh 别名查分配表（远程真实主机名可能≠rN，故显式传参）
ua=$(awk -F'\t' -v k="host:$h" '$1==k{print $2}' $HUB/ua_assign.tsv)
[ -n "$ua" ] || ua="demiflow-backfill/1.2 (image restoration; https://github.com/hollowreed42/demiflow-backfill)"
timeout 60 scp -q -o ConnectTimeout=12 $FC/fleet_dl.py $HUB/ua_pool.txt $HUB/ua_assign.tsv "$h":~/wk_backfill/ 2>/dev/null
# ua.env 供看门狗直跑 fleet_curl 时取本机唯一身份
timeout 30 ssh -o ConnectTimeout=12 "$h" "printf 'UA=%s\n' '$ua' > ~/wk_backfill/ua.env" 2>/dev/null
ssh -o ConnectTimeout=8 -o BatchMode=yes "$h" "
  cd ~/wk_backfill &&
  setsid nohup $py -u fleet_dl.py --candidates wm_${n}.jsonl --out-dir run_${n} \
    --concurrency 1 --rps 0.15 --log-every 100 --ua '$ua' > run_${n}.log 2>&1 < /dev/null &
  sleep 3
  pid=\$(pgrep -f \"fleet_dl.py --candidates wm_${n}\" | head -1)
  if [ -n \"\$pid\" ]; then
    alive=\$(ps -o stat= -p \$pid)
    conns=\$(ss -tn 2>/dev/null | grep -c :443)
    echo \"$h pid=\$pid stat=\$alive conns=\$conns log=\$(tail -n 1 ~/wk_backfill/run_${n}.log)\"
  else
    echo \"$h FAILED: \$(tail -n 2 ~/wk_backfill/run_${n}.log)\"
  fi
"
