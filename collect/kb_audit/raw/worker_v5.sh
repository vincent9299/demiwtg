#!/bin/bash
# v5: 支持 8 列 OFFSET 的直传 worker（队列: NAME URL COSDIR TOTAL PARTIDX _ [UA] [OFFSET]）
Q=$1
CHUNK=1073741824
while IFS=$'\t' read -r NAME URL COSDIR TOTAL A B UA OFFSET; do
  [ -z "$NAME" ] && continue
  S=${OFFSET:-$(( A*CHUNK ))}
  [ $S -ge $TOTAL ] && continue
  E=$(( S+CHUNK-1 )); [ $E -ge $TOTAL ] && E=$(( TOTAL-1 ))
  EXP=$(( E-S+1 ))
  DST="$COSDIR/$NAME.part-$(printf %05d $A)"
  CUR=$(stat -c%s "$DST" 2>/dev/null || echo 0)
  [ "$CUR" = "$EXP" ] && continue
  KEY="${DST#/lhcos-data/}"
  OK=0
  for t in 1 2 3; do
    curl -s --limit-rate 12M -r $S-$E ${UA:+-A "$UA"} --connect-timeout 30 --speed-limit 20480 --speed-time 120 "$URL" | \
      python3 ~/stream_cos.py "$KEY" "$EXP" && { OK=1; break; }
    echo "[$(date +%T)] RETRY $NAME#$A rt$t"
  done
  [ $OK = 1 ] && echo "[$(date +%T)] OK $NAME#$A" || echo "[$(date +%T)] FAIL $NAME#$A"
done < "$Q"
echo "[$(date +%T)] V5_ALL_DONE"
