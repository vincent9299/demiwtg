#!/bin/bash
# SDC ①-b 驱动: pipeline-b 流解 18 块 image 表 → 预筛表上 COS
set -euo pipefail
export LC_ALL=C
W=/tmp/sdc_extract
PARTS=/lhcos-data/demiwtg-data/datasets/raw/wikimedia
KBOUT=/lhcos-data/demiwtg-data/datasets/demiwtg/kb/sdc_fetch
mkdir -p "$W" "$KBOUT"

echo "[run_extract] start $(date)"
cat "$PARTS"/commonswiki-latest-image.sql.gz.part-* | gzip -dc \
  | python3 "$W/extract_image.py" > "$W/image_bitmap.tsv"

# dump 理论按主键序;若实测有乱序则补一道外排保证 join 语义
if sort -S1G -c "$W/image_bitmap.tsv" 2>/dev/null; then
  echo "[run_extract] 已按 fname 有序,免排序"
else
  echo "[run_extract] 检出乱序,外排修正"
  sort -S1G "$W/image_bitmap.tsv" > "$W/image_bitmap.sorted"
  mv "$W/image_bitmap.sorted" "$W/image_bitmap.tsv"
fi

gzip -c "$W/image_bitmap.tsv" > "$KBOUT/image_bitmap.tsv.gz"
wc -l < "$W/image_bitmap.tsv" > "$KBOUT/image_bitmap.count"
echo "[run_extract] DONE $(date) lines=$(cat "$KBOUT/image_bitmap.count")"
