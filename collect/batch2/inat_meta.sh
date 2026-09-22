#!/bin/bash
# iNat 元数据快照（含 taxa/photos/observations 关联钥匙）流式直写 COS
URL="https://inaturalist-open-data.s3.amazonaws.com/metadata/inaturalist-open-data-20260827.tar.gz"
OUT="/lhcos-data/demiwtg-data/datasets/raw/inat/inaturalist-open-data-20260827.tar.gz"
EXPECT=$(curl -sI "$URL" | grep -i content-length | tr -dc '0-9')
for i in $(seq 1 200); do
  curl -C - -o "$OUT" --connect-timeout 30 --speed-limit 10240 --speed-time 60 "$URL" && break
  echo "[$(date +%T)] retry #$i"; sleep 10
done
SZ=$(stat -c%s "$OUT" 2>/dev/null || echo 0)
[ "$SZ" = "$EXPECT" ] && echo "[$(date +%T)] DONE ok $SZ" || echo "[$(date +%T)] MISMATCH got=$SZ want=$EXPECT"
