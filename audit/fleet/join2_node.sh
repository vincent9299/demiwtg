#!/bin/bash
set -e
I="$1"
ST=~/demi/raw/state
B="https://lhcos-368f6-1256345599.cos.ap-singapore.myqcloud.com/lhcos-data"
mkdir -p $ST; cd $ST
[ -s sniffout_0$I.tsv ] || curl -s --retry 3 -o sniffout_0$I.tsv "$B/demiwtg-data/node-backup/2026-09-17/p5/demi/raw/state/sniffout_0$I.tsv"
[ -s qid_images.jsonl ] || { curl -s --retry 3 "$B/demiwtg-data/datasets/demiwtg/kb/qid_images.jsonl.gz" | gzip -dc > qid_images.jsonl; }
N=$(wc -l < qid_images.jsonl)
[ "$N" -ge 8861000 ] || { echo "LEDGER_SHORT $N"; exit 1; }
python3 ~/audit_join2_shard.py "$I"
echo "JOIN2_SHARD_${I}_OK"
