#!/bin/bash
# DF20 全尺寸 107.8GB 流式直写 COS，断点续传+重试
URL="http://ptak.felk.cvut.cz/plants/DanishFungiDataset/DF20-train_val.tar.gz"
OUT="/lhcos-data/demiwtg-data/datasets/raw/df20/DF20-train_val.tar.gz"
EXPECT=115741214441
for i in $(seq 1 200); do
  curl -C - -o "$OUT" --connect-timeout 30 --speed-limit 10240 --speed-time 60 "$URL" && break
  echo "[$(date +%T)] retry #$i"; sleep 10
done
SZ=$(stat -c%s "$OUT" 2>/dev/null || echo 0)
if [ "$SZ" = "$EXPECT" ]; then echo "[$(date +%T)] DONE ok size=$SZ"; else echo "[$(date +%T)] SIZE MISMATCH got=$SZ want=$EXPECT"; fi
