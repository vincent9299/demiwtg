#!/bin/bash
# 本地两段 truthy 头部(0-18G)切成 COS part-10000 起编号（避开尾款 0-23）
D=/lhcos-data/demiwtg-data/datasets/raw/wikimedia
G=1073741824
seg() {
  local SRC=$1 BASE_IDX=$2 LEN=$3
  local N=$(( (LEN+G-1)/G ))
  for ((i=0;i<N;i++)); do
    local S=$((i*G)); local E=$((S+G-1)); [ $E -ge $LEN ] && E=$((LEN-1))
    local EXP=$((E-S+1))
    local DST="$D/latest-truthy.nt.bz2.part-$(printf %05d $((BASE_IDX+i)))"
    local CUR=$(stat -c%s "$DST" 2>/dev/null || echo 0)
    [ "$CUR" = "$EXP" ] && continue
    dd if="$SRC" of=/tmp/th.part bs=$G skip=$i count=1 2>/dev/null
    truncate -s $EXP /tmp/th.part
    for t in 1 2 3; do cp /tmp/th.part "$DST.tmp" && mv -f "$DST.tmp" "$DST"; [ "$(stat -c%s "$DST")" = "$EXP" ] && break; done
    rm -f /tmp/th.part; echo "head part $((BASE_IDX+i)) ok"
  done
}
seg /home/ubuntu/demi/kb_night/qid_build/truthy/seg_0000000000000000 10000 8999999999
seg /home/ubuntu/demi/kb_night/qid_build/truthy/seg_0900000000000000 11000 8999992320
echo HEADS_SHIPPED
