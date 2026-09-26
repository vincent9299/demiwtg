#!/bin/bash
# stop_gbif_one.sh — 停本机全部 GBIF 进程(px5 拉片树 + gbif 滴灌 worker),不动 b3x/tmdb
set -u
echo "== $(hostname) $(date -u +%T)"
# 1) px5 拉片器:先杀父 bash,再清孤儿 curl
BP=$(pgrep -f '^bash /tmp/px5_pull\.sh' || true)
[ -n "$BP" ] && { kill $BP; echo "killed px5 bash: $BP"; }
sleep 1
pkill -f '^curl -sfL .*0000975-260921141020460' 2>/dev/null && echo "killed orphan curls" || true
# px5 的 HEAD/上传 python 是瞬态的,出现也会随父死;残留清理:
pkill -f 'url_slice_px' 2>/dev/null || true
rm -f ~/osm_slice_gbif/*.bin 2>/dev/null
# 2) gbif 滴灌 worker(--src gbif;绝不匹配 b3x 的 queue_worker.py)
WP=$(pgrep -f 'b4_op\.py --worker .* --src gbif ' || true)
[ -n "$WP" ] && { kill $WP; echo "killed gbif drip workers: $WP"; }
sleep 2
# 复核
P5=$(pgrep -cf '^bash /tmp/px5_pull' || true)
WG=$(pgrep -cf 'b4_op\.py --worker .* --src gbif ' || true)
B3=$(pgrep -cf 'queue_worker\.py --identity' || true)
echo "left: px5=$P5 gbif-worker=$WG b3x-workers=$B3"
