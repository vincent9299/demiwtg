#!/bin/bash
# px5_pull.sh URL PREFIX IDXSPEC TOTAL [START_DELAY] — GBIF 全局 512MB 网格选择性拉片器 v2
# 跳过 verbatim 区;COS 已有片免睡快进;429 温和化:当前片无限重试(sleep 递增封顶 600s,
# 绝不换片施压);每片轮换身份;片后 sleep 2700±300 抖动。IDXSPEC 如 "0-9,150-157,264-272"。
set -u
URL=$1; PREFIX=$2; IDXSPEC=$3; TOTAL=$4
DELAY0=${5:-0}
PSZ=$((512*1024*1024))
D=~/osm_slice_$PREFIX; mkdir -p $D; cd $D || exit 1
ENVS=$(ls /tmp/qw_env_* 2>/dev/null)
[ -z "$ENVS" ] && { echo "no-env"; exit 1; }
NENV=$(echo "$ENVS" | wc -l)

IDXS=()
for part in ${IDXSPEC//,/ }; do
  if [[ $part == *-* ]]; then
    a=${part%-*}; b=${part#*-}
    for ((i=a;i<=b;i++)); do IDXS+=($i); done
  else
    IDXS+=($part)
  fi
done
echo "px5 start $(date -u +%FT%T): ${#IDXS[@]} pieces spec=$IDXSPEC delay0=$DELAY0"
[ "$DELAY0" -gt 0 ] && sleep "$DELAY0"

ESEQ=0   # 每片轮换身份的全局计数
for PASS in 1 2 3; do
  REMAIN=0
  for IDX in "${IDXS[@]}"; do
    OFF=$(( IDX * PSZ )); [ $OFF -ge $TOTAL ] && continue
    END=$(( OFF + PSZ - 1 )); [ $END -ge $TOTAL ] && END=$((TOTAL-1))
    WANT=$(( END - OFF + 1 )); F=$(printf "g%04d.bin" $IDX)
    export CHK_KEY="lhcos-data/demiwtg-data/kb/osm_parts/$PREFIX/$F"
    HAVE=$(python3 - <<'PY'
import os, sys
sys.path.insert(0, "/home/ubuntu/wk_b2")
from demiflow_collect.cosio import COSCreds, COSIO, build_host
io = COSIO(COSCreds.discover(paths=("/home/ubuntu/wk_b2/.cos_creds",)),
           build_host("lhcos-368f6-1256345599", "ap-singapore"))
print("YES" if io.head(os.environ["CHK_KEY"]) is not None else "NO")
PY
)
    [ "$HAVE" = "YES" ] && continue
    REMAIN=$((REMAIN+1))
    try=0
    while true; do
      try=$((try+1))
      ESEQ=$((ESEQ+1))
      QW=$(echo "$ENVS" | sed -n "$(( (ESEQ-1) % NENV + 1 ))p")
      KBP_PROXY=$(grep -oP '(?<=^KBP_PROXY=).*' "$QW" | head -1)
      rm -f "$F"
      if [ -n "$KBP_PROXY" ]; then
        CODE=$(curl -sfL -x "$KBP_PROXY" --connect-timeout 15 \
          --speed-limit 20480 --speed-time 120 -m 10800 -r "$OFF-$END" \
          -o "$F" -w '%{http_code}' "$URL" 2>/dev/null || echo x)
      else
        CODE=$(curl -sfL --connect-timeout 15 \
          --speed-limit 20480 --speed-time 120 -m 10800 -r "$OFF-$END" \
          -o "$F" -w '%{http_code}' "$URL" 2>/dev/null || echo x)
      fi
      SZ=$(stat -c%s "$F" 2>/dev/null || echo 0)
      [ "$SZ" -eq "$WANT" ] && break
      echo "$(date -u +%T) try$try code=$CODE sz=$SZ want=$WANT env=$(basename $QW) $F" >> retry.log
      sleep $(( try*30 > 600 ? 600 : try*30 ))
    done
    export SLICE_F=$F
    python3 - <<'PY' >> up.log 2>&1 || echo "UPFAIL $F" >> fail.log
import os, sys
sys.path.insert(0, "/home/ubuntu/wk_b2")
from demiflow_collect.cosio import COSCreds, COSIO, build_host
io = COSIO(COSCreds.discover(paths=("/home/ubuntu/wk_b2/.cos_creds",)),
           build_host("lhcos-368f6-1256345599", "ap-singapore"))
f = os.environ["SLICE_F"]
print(f, io.put_multipart("lhcos-data/demiwtg-data/kb/osm_parts/gbif/" + f,
                          os.path.join(os.path.expanduser("~"), "osm_slice_gbif", f)))
PY
    rm -f "$F"
    echo "$(date -u +%T) pass$PASS done $F" >> progress
    sleep $(( 2700 + RANDOM % 600 - 300 ))
  done
  [ $REMAIN -eq 0 ] && break
  echo "pass $PASS end remain=$REMAIN" >> progress
  sleep 300
done
echo "URLSLICE_DONE $PREFIX $IDXSPEC $(date -u +%FT%T)" > progress
