#!/bin/bash
# 61 出口代理池部署：48 个健康出口 × 每 worker 独立分片 + 自有项目 UA 轮换
# 在本机调度中枢运行：bash deploy61.sh
set -u
HUB=/yzp/zhaozy/yangzepeng/0905/demiwtg/state/curation/image_backfill_full_v1/hub
PXS=$HUB/proxies
SLICES=$HUB/pending_all
FC=/yzp/zhaozy/yangzepeng/0905/demiwtg/collect/image_backfill/fleet_curl.py
# UA 按出口查中央分配表（hub/ua_assign.tsv，每出口唯一）
# 48 个健康出口（42 新批 OK + 5 老批 + 1 静态）
grep -E "wk=200" $PXS/check_all.txt | awk '{print $1}' > $PXS/ok42.txt
cat > $PXS/ok_extra5.txt <<'EOF'
23.142.108.123:443:AsXabmHjKLnV:zVPTbjxOB2
149.119.185.219:443:AsXabmHjKLnV:zVPTbjxOB2
66.17.67.82:443:AsXabmHjKLnV:zVPTbjxOB2
50.3.64.106:443:AsXabmHjKLnV:zVPTbjxOB2
66.17.67.97:443:AsXabmHjKLnV:zVPTbjxOB2
216.132.205.99:443:FhxUHUWYDkJi:Gnxrn5sQmY
EOF
sed 's/$/:443:tbAMQCkwCHJq:fiXcBpP8ty/' $PXS/ok42.txt > $PXS/pool48.txt
cat $PXS/ok_extra5.txt >> $PXS/pool48.txt
n=$(wc -l < $PXS/pool48.txt)
echo "pool size: $n"
i=0
while read spec; do
  h=r$(( i % 20 + 1 ))
  w=$i
  ua=$(awk -F'\t' -v k="proxy:${spec%%:*}" '$1==k{print $2}' $HUB/ua_assign.tsv)
  [ -n "$ua" ] || ua=$(sed -n "$(( i + 1 ))p" $HUB/ua_pool.txt)   # 缺表回退
  slice=$(printf "px_%02d.jsonl" $w)
  [ -f $SLICES/$slice ] || { echo "no slice $slice"; continue; }
  timeout 60 scp -q -o ConnectTimeout=12 $FC $(dirname $FC)/cos_util.py $(dirname $FC)/.cos_creds $HUB/ua_pool.txt $HUB/ua_assign.tsv $h:~/wk_backfill/ < /dev/null 2>/dev/null || true
  timeout 30 ssh -n -o ConnectTimeout=12 $h "chmod 600 ~/wk_backfill/.cos_creds; printf 'UA=%s\n' \"\$(awk -F'\t' -v k=host:$h '\$1==k{print \$2}' ~/wk_backfill/ua_assign.tsv)\" > ~/wk_backfill/ua.env" 2>/dev/null || true
  timeout 60 scp -q -o ConnectTimeout=12 $SLICES/$slice $h:~/wk_backfill/px_${w}.jsonl < /dev/null 2>/dev/null || { echo "slice scp fail $h"; i=$((i+1)); continue; }
  timeout 40 ssh -n -o ConnectTimeout=12 $h "pgrep -f \"[p]x_${w}.jsonl\" >/dev/null || (cd ~/wk_backfill && setsid nohup python3 -u fleet_curl.py --candidates px_${w}.jsonl --out-dir run_px${w} --proxy 'http://${spec#*:443:}@${spec%%:*}:443' --ua '$ua' --cos-prefix lhcos-data/demiwtg-data/datasets/demiwtg/blobs > px${w}.log 2>&1 < /dev/null &) ; echo w${w}->${h%%:*}" 2>/dev/null
  i=$((i+1))
  sleep 2
done < $PXS/pool48.txt
echo "deploy done: $i workers"
