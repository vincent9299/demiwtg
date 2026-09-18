#!/bin/bash
# IP 恢复监视+复投：每小时探测全部代理，恢复者(3/3 通)立即带节拍投产
HUB=/yzp/zhaozy/yangzepeng/0905/demiwtg/state/curation/image_backfill_full_v1/hub
FC=/yzp/zhaozy/yangzepeng/0905/demiwtg/collect/image_backfill/fleet_curl.py
UAF=$HUB/ua_pool.txt
while true; do
  echo "[$(date +%H:%M)] 探测开始"
  ok_list=""
  idx=0
  while read spec; do
    ip=$(echo "$spec" | cut -d: -f1)
    rest=$(echo "$spec" | cut -d: -f2-)
    # 探测：3 连发 wikimedia
    oks=0
    for t in 1 2 3; do
      c=$(timeout 15 ssh -o ConnectTimeout=8 r3 "curl -sx 'http://$rest' -o /dev/null -w '%{http_code}' --max-time 12 -A 'demiflow-backfill/1.2 (image restoration; https://github.com/hollowreed42/demiflow-backfill)' -e 'https://github.com/hollowreed42/demiflow-backfill' 'https://upload.wikimedia.org/wikipedia/commons/5/5e/Guppy_Poecilia_reticulata.jpg'" 2>/dev/null)
      [ "$c" = "200" ] && oks=$((oks+1))
      sleep 1
    done
    if [ "$oks" -ge 3 ]; then ok_list="$ok_list $spec"; fi
    idx=$((idx+1))
  done < $HUB/proxies/master_final.txt
  n=$(echo $ok_list | wc -w)
  echo "[$(date +%H:%M)] 恢复 $n 个: $(echo $ok_list | tr ' ' '\n' | cut -d: -f1 | tr '\n' ',')"
  # 复投恢复者（每 IP 一个 worker，动态节拍起步 0.25，分片用 ps_ 轮转）
  w=0
  for spec in $ok_list; do
    ip=$(echo "$spec" | cut -d: -f1); rest=$(echo "$spec" | cut -d: -f2-)
    h=r$(( w % 20 + 1 ))
    n=$(printf "%02d" $(( w % 28 )))
    ua=$(awk -F'\t' -v k="proxy:$ip" '$1==k{print $2}' $HUB/ua_assign.tsv)
    [ -n "$ua" ] || ua=$(sed -n "$(( w + 1 ))p" $UAF)   # 缺表回退
    timeout 60 scp -q -o ConnectTimeout=12 $FC $(dirname $FC)/cos_util.py $(dirname $FC)/.cos_creds $HUB/ua_pool.txt $HUB/ua_assign.tsv $h:~/wk_backfill/ < /dev/null 2>/dev/null
    timeout 30 ssh -n -o ConnectTimeout=12 $h "chmod 600 ~/wk_backfill/.cos_creds; printf 'UA=%s\n' \"\$(awk -F'\t' -v k=host:$h '\$1==k{print \$2}' ~/wk_backfill/ua_assign.tsv)\" > ~/wk_backfill/ua.env" 2>/dev/null
    timeout 60 scp -q -o ConnectTimeout=12 $HUB/pending_all/ps_$n.jsonl $h:~/wk_backfill/pr.jsonl < /dev/null 2>/dev/null
    timeout 40 ssh -n -o ConnectTimeout=12 $h "cd ~/wk_backfill && setsid nohup python3 -u fleet_curl.py --candidates pr.jsonl --out-dir run_pr_$ip --proxy 'http://$rest' --ua '$ua' --rps-start 0.25 --cos-prefix lhcos-data/demiwtg-data/datasets/demiwtg/blobs > pr_$ip.log 2>&1 < /dev/null & echo ok" >/dev/null 2>&1
    w=$((w+1))
    sleep 2
  done
  echo "[$(date +%H:%M)] 本轮复投 $w 个 worker"
  sleep 3600
done
