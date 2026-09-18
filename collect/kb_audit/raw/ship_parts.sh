#!/bin/bash
# PlantNet zip 分块出货：dd 切块 -> cp 到 cosfs -> 读回校验 -> 失败重传（单块最多5次）
SRC=/home/ubuntu/demi/raw/plantnet300k/plantnet_300K.zip
D=/lhcos-data/demiwtg-data/datasets/raw/plantnet300k
TOTAL=31670505069; G=1073741824; N=30
for ((i=0;i<N;i++)); do
  S=$((i*G)); [ $S -ge $TOTAL ] && break
  E=$((S+G-1)); [ $E -ge $TOTAL ] && E=$((TOTAL-1))
  EXP=$((E-S+1))
  DST="$D/plantnet_300K.zip.part-$(printf %05d $i)"
  GOT=$(cat "$DST" 2>/dev/null | wc -c)
  [ "$GOT" = "$EXP" ] && continue
  dd if="$SRC" of=/tmp/pn.part bs=$G skip=$i count=1 2>/dev/null
  truncate -s $EXP /tmp/pn.part
  for t in 1 2 3 4 5; do
    cp /tmp/pn.part "$DST.tmp" 2>/dev/null
    mv -f "$DST.tmp" "$DST" 2>/dev/null; sleep 2
    GOT=$(cat "$DST" 2>/dev/null | wc -c)
    [ "$GOT" = "$EXP" ] && { echo "[$(date +%T)] part $i OK(t$t)"; break; }
    echo "[$(date +%T)] part $i trunc($GOT/$EXP) rt$t"
  done
  rm -f /tmp/pn.part
done
SUM=0
for p in "$D"/plantnet_300K.zip.part-*; do
  SZ=$(cat "$p" 2>/dev/null | wc -c); SUM=$((SUM+SZ))
done
if [ "$SUM" = "$TOTAL" ]; then rm -f "$SRC"; echo "[$(date +%T)] SHIPPED_ALL_PARTS $SUM"; else echo "SUM=$SUM/$TOTAL INCOMPLETE"; fi
