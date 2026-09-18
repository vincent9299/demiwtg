#!/bin/bash
set -e
I="$1"; cd ~
[ -s sniffout_topup_$I.tsv ] || { echo "NO_LOCAL_$I"; exit 1; }
SZ=$(stat -c %s sniffout_topup_$I.tsv)
cat sniffout_topup_$I.tsv | python3 stream_cos.py "lhcos-data/demiwtg-data/audit/2026-09-17/topup/sniffout_topup_$I.tsv" "$SZ"
echo "PUSHOUT_${I}_OK ($SZ)"
