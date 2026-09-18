#!/bin/bash
# SDC ①-a 收尾: S6 预筛 join + S7 聚合(绕过 cosfs 负缓存,直接执行)
set -euo pipefail
export LC_ALL=C
W=/tmp/sdc_manifest
S=$W/manifest_stages.py
KBOUT=/lhcos-data/demiwtg-data/datasets/demiwtg/kb/sdc_fetch
cd "$W"

log() { echo "[$(date +%H:%M:%S)] $*"; }

log "S6 zcat image 表"
zcat "$KBOUT/image_bitmap.tsv.gz" > "$W/image_bitmap.tsv"

log "S6 预筛 join"
join -t$'\t' -v 1 -1 1 -2 1 "$W/new_edges.tsv" "$W/image_bitmap.tsv" \
  | cut -f1 | uniq | wc -l > "$W/s6_drop.count"
join -t$'\t' -1 1 -2 1 -o '1.1,1.2,1.3,1.4,2.2' \
  "$W/new_edges.tsv" "$W/image_bitmap.tsv" > "$W/fetch_edges.tsv"
log "S6 预筛丢弃文件数: $(cat "$W/s6_drop.count")"

log "S7 聚合出清单"
python3 "$S" s7_group "$W/fetch_edges.tsv" "$W/fetch_list.tsv" "$W/funnel.json"
gzip -c "$W/fetch_list.tsv" > "$KBOUT/fetch_list.tsv.gz"
cp "$W/funnel.json" "$KBOUT/"
log "DONE funnel: $(cat "$W/funnel.json")"
