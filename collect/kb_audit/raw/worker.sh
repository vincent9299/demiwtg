#!/bin/bash
# 分块下载 worker：args = 队列文件(每行: NAME URL COSDIR TOTAL_BYTES IDX_START IDX_END [UA])
# 1GiB 分块；块已存在且尺寸正确则跳过；低盘自动等待；8 次重试
Q=$1
CHUNK=1073741824
while IFS=$'\t' read -r NAME URL COSDIR TOTAL A B UA; do
  [ -z "$NAME" ] && continue
  mkdir -p "$COSDIR" /tmp/wk 2>/dev/null
  for ((i=A; i<=B; i++)); do
    S=$(( i*CHUNK )); [ $S -ge $TOTAL ] && break
    E=$(( S+CHUNK-1 )); [ $E -ge $TOTAL ] && E=$(( TOTAL-1 ))
    EXP=$(( E-S+1 ))
    DST="$COSDIR/$NAME.part-$(printf %05d $i)"
    CUR=$(stat -c%s "$DST" 2>/dev/null || echo 0)
    [ "$CUR" = "$EXP" ] && continue
    while true; do
      FREE=$(df --output=avail -k / | tail -1)
      [ $FREE -ge 6000000 ] && break
      echo "[$(date +%T)] lowdisk ${FREE}kB wait $NAME#$i"; sleep 60
    done
    P="/tmp/wk/$NAME.$$.part"
    OK=0
    for t in 1 2 3 4 5 6 7 8; do
      curl -s --limit-rate 12M -r $S-$E ${UA:+-A "$UA"} -o "$P" --connect-timeout 30 --speed-limit 20480 --speed-time 120 "$URL"
      [ "$(stat -c%s "$P" 2>/dev/null || echo 0)" = "$EXP" ] && { OK=1; break; }
      sleep 8
    done
    if [ $OK = 1 ]; then
      UP=0
      for t in 1 2 3; do
        cp "$P" "$DST.tmp" 2>/dev/null
        mv -f "$DST.tmp" "$DST" 2>/dev/null; sleep 1
        GOT=$(cat "$DST" 2>/dev/null | wc -c)
        if [ "$GOT" = "$EXP" ]; then UP=1; break; fi
        echo "[$(date +%T)] TRUNC $NAME#$i ($GOT/$EXP) up-retry$t"
      done
      [ $UP = 1 ] && echo "[$(date +%T)] OK $NAME#$i" || echo "[$(date +%T)] UPFAIL $NAME#$i"
    else
      echo "[$(date +%T)] FAIL $NAME#$i"
    fi
    rm -f "$P"
  done
done < "$Q"
echo "[$(date +%T)] WORKER_ALL_DONE $Q"
