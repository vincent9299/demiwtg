#!/bin/bash
# v4 直传版：curl 区间下载 | stream_cos.py 流式 multipart 上传 COS，零本地盘
# 队列格式同 v3: NAME URL COSDIR TOTAL A B [UA]
Q=$1
CHUNK=1073741824
while IFS=$'\t' read -r NAME URL COSDIR TOTAL A B UA; do
  [ -z "$NAME" ] && continue
  for ((i=A; i<=B; i++)); do
    S=$(( i*CHUNK )); [ $S -ge $TOTAL ] && break
    E=$(( S+CHUNK-1 )); [ $E -ge $TOTAL ] && E=$(( TOTAL-1 ))
    EXP=$(( E-S+1 ))
    DST="$COSDIR/$NAME.part-$(printf %05d $i)"
    CUR=$(stat -c%s "$DST" 2>/dev/null || echo 0)
    [ "$CUR" = "$EXP" ] && continue
    KEY="${DST#/lhcos-data/}"
    OK=0
    for t in 1 2 3; do
      curl -s --limit-rate 12M -r $S-$E ${UA:+-A "$UA"} --connect-timeout 30 --speed-limit 20480 --speed-time 120 "$URL" | \
        python3 ~/stream_cos.py "$KEY" "$EXP" && { OK=1; break; }
      echo "[$(date +%T)] RETRY $NAME#$i rt$t"
    done
    [ $OK = 1 ] && echo "[$(date +%T)] OK $NAME#$i" || echo "[$(date +%T)] FAIL $NAME#$i"
  done
done < "$Q"
echo "[$(date +%T)] V4_ALL_DONE $Q"
