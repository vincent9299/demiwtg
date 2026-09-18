#!/bin/bash
# Open Images 标注（关联钥匙：MID + bbox），train 全量 + val/test
B="https://storage.googleapis.com/openimages"
D="/lhcos-data/demiwtg-data/datasets/raw/openimages"
mkdir -p "$D"
for f in "v7/oidv7-train-annotations-human-imagelabels.csv" \
         "v6/oidv6-train-annotations-bbox.csv" \
         "v6/oidv6-class-descriptions.csv" \
         "v7/oidv7-validation-annotations-human-imagelabels.csv" \
         "v7/oidv7-test-annotations-human-imagelabels.csv" \
         "v6/oidv6-validation-annotations-bbox.csv" \
         "v6/oidv6-test-annotations-bbox.csv"; do
  O="$D/$(basename $f)"
  E=$(curl -sI "$B/$f" | grep -i content-length | tr -dc '0-9')
  [ -z "$E" ] && { echo "[$(date +%T)] 404? $f"; continue; }
  for i in 1 2 3 4 5; do curl -C - -o "$O" --connect-timeout 30 "$B/$f" && break; sleep 10; done
  SZ=$(stat -c%s "$O" 2>/dev/null || echo 0)
  echo "[$(date +%T)] $(basename $f) got=$SZ want=$E $([ "$SZ" = "$E" ] && echo OK || echo FAIL)"
done
echo ALL_DONE
