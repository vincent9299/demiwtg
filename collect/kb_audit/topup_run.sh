#!/bin/bash
set -e
I="$1"
B="https://lhcos-368f6-1256345599.cos.ap-singapore.myqcloud.com/lhcos-data/demiwtg-data/audit/2026-09-17/topup"
cd ~
[ -s topup_chunk_$I.tsv ] || curl -s --retry 3 -o topup_chunk_$I.tsv "$B/chunk_$I.tsv"
echo "chunk lines: $(wc -l < topup_chunk_$I.tsv)"
python3 cos_sniff.py topup_chunk_$I.tsv sniffout_topup_$I.tsv 24
SZ=$(stat -c %s sniffout_topup_$I.tsv)
cat sniffout_topup_$I.tsv | python3 stream_cos.py "$B/sniffout_topup_$I.tsv" "$SZ"
echo "TOPUP_${I}_OK"
