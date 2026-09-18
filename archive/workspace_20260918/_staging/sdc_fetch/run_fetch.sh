#!/bin/bash
# SDC 下载看门狗:崩溃自动拉起(账本幂等),清单跑完自然退出
set -u
LIST=/lhcos-data/demiwtg-data/datasets/demiwtg/kb/sdc_fetch/fetch_list.tsv.gz
SHARD=$1
D=${2:-4}
R=${3:-3}
mkdir -p ~/sdc_fetch ~/lake/meta
cd ~/sdc_fetch
echo "[watchdog] start shard=$SHARD conc=$D rate=$R $(date)" >> watchdog.log
while true; do
  OUT=$(python3 /tmp/sdc_fetch_fleet.py --list "$LIST" --shard "$SHARD" \
        --state ~/sdc_fetch --dl-conc "$D" --dl-rate "$R" 2>&1 | tail -2)
  echo "[$(date +%m-%d\ %H:%M:%S)] $OUT" >> watchdog.log
  case "$OUT" in *DONE*) break;; esac
  sleep 30
done
echo "[watchdog] shard=$SHARD 完成 $(date)" >> watchdog.log
