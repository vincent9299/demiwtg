#!/bin/bash
set -e
I="$1"; cd ~/demi/raw/state
for f in poison_html_rows.shard$I.jsonl.gz poison_other_rows.shard$I.jsonl.gz stats.shard$I.json; do
  [ -s "$f" ] || { echo "MISSING_$f"; exit 1; }
  SZ=$(stat -c %s "$f")
  cat "$f" | python3 ~/stream_cos.py "lhcos-data/demiwtg-data/audit/2026-09-17/shards/$f" "$SZ"
done
echo "PUSHSHARD_${I}_OK"
