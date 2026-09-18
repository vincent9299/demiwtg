#!/bin/bash
# SDC ①-a 续跑2: 从 S5 起(S3/S4 产物已就绪,quota.fname.sorted 已排好)
set -euo pipefail
export LC_ALL=C
W=/tmp/sdc_manifest
S=$W/manifest_stages.py
KB=/lhcos-data/demiwtg-data/datasets/demiwtg/kb
KBOUT=$KB/sdc_fetch
cd "$W"

log() { echo "[$(date +%H:%M:%S)] $*"; }

log "S5 排除已有账本 [resume2]"
if [ ! -s "$W/have.sorted" ]; then
  python3 "$S" s5_have | sort -S1G -u > "$W/have.sorted"
fi
wc -l < "$W/have.sorted" > "$W/s5_have.count"
join -t$'\t' -v 1 -1 1 -2 1 "$W/quota.fname.sorted" "$W/have.sorted" \
  > "$W/new_edges.tsv"
log "S5 已有文件数: $(cat "$W/s5_have.count"); 限额后边数: $(wc -l < "$W/quota.fname.sorted"); 排除后边数: $(wc -l < "$W/new_edges.tsv")"

log "S6 等 image 表(b 产物)并预筛 join"
n=0
until [ -s "$KBOUT/image_bitmap.tsv.gz" ]; do
  n=$((n + 1)); [ $n -gt 240 ] && { log "等 b 超时 4h,退出"; exit 3; }
  sleep 60
done
zcat "$KBOUT/image_bitmap.tsv.gz" > "$W/image_bitmap.tsv"
join -t$'\t' -v 1 -1 1 -2 1 "$W/new_edges.tsv" "$W/image_bitmap.tsv" \
  | cut -f1 | uniq | wc -l > "$W/s6_drop.count"
join -t$'\t' -1 1 -2 1 -o '1.1,1.2,1.3,1.4,2.2' \
  "$W/new_edges.tsv" "$W/image_bitmap.tsv" > "$W/fetch_edges.tsv"
log "S6 预筛丢弃文件数(非BITMAP/超界/表缺失): $(cat "$W/s6_drop.count")"

log "S7 聚合出清单"
python3 "$S" s7_group "$W/fetch_edges.tsv" "$W/fetch_list.tsv" "$W/funnel.json"
gzip -c "$W/fetch_list.tsv" > "$KBOUT/fetch_list.tsv.gz"
cp "$W/funnel.json" "$KBOUT/"
log "DONE funnel: $(cat "$W/funnel.json")"
