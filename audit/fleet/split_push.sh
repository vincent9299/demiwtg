#!/bin/bash
set -e
cd ~/demi/raw/state
rm -f chunk_*
split -n l/8 -d missing.tsv chunk_
i=0
for f in chunk_0*; do
  SZ=$(stat -c %s "$f")
  cat "$f" | python3 ~/stream_cos.py "lhcos-data/demiwtg-data/audit/2026-09-17/topup/chunk_$i.tsv" "$SZ"
  i=$((i+1))
done
echo "CHUNKS_PUSHED ($i)"
