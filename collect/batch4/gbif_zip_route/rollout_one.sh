#!/bin/bash
# rollout_one.sh IDXSPEC DELAY — 杀本机 px4/旧px5 树,启动 px5 v2(setsid 脱离)
set -u
SPEC=$1; DELAY=$2
BP=$(pgrep -f '^bash /tmp/url_slice_px4\.sh' || true)
[ -n "$BP" ] && kill $BP && echo "killed px4: $BP"
BP2=$(pgrep -f '^bash /tmp/px5_pull\.sh' || true)
[ -n "$BP2" ] && kill $BP2 && echo "killed px5-old: $BP2"
sleep 1
pkill -f '^curl -sfL .*0000975-260921141020460' || true
rm -f ~/osm_slice_gbif/*.bin
cd ~/osm_slice_gbif || exit 9
setsid nohup bash /tmp/px5_pull.sh \
  "https://occurrence-download.gbif.org/occurrence/download/request/0000975-260921141020460.zip" \
  gbif "$SPEC" 164456211593 "$DELAY" >> px5.log 2>&1 < /dev/null &
sleep 2
pgrep -f '^bash /tmp/px5_pull\.sh' >/dev/null && echo "px5 alive" || { echo "PX5_START_FAIL"; exit 8; }
tail -1 px5.log
