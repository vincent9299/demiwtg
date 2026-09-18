#!/bin/bash
set -e
ST=~/demi/raw/state
B="https://lhcos-368f6-1256345599.cos.ap-singapore.myqcloud.com/lhcos-data/demiwtg-data/audit/2026-09-17"
cd $ST
for i in 0 1 2 3 4 5 6 7; do
  [ -s sniffout_topup_$i.tsv ] || curl -sf --retry 3 -o sniffout_topup_$i.tsv "$B/topup/sniffout_topup_$i.tsv"
done
awk -F"\t" "NF==3" sniffout_topup_0*.tsv > sniffout_08.tsv
echo "shard8 rows: $(wc -l < sniffout_08.tsv)"
cd ~
python3 audit_join2_shard.py 8
[ -s $ST/sniff_large.tsv ] || curl -s --retry 3 -o $ST/sniff_large.tsv "$B/sniff_large.tsv"
python3 merge_audit.py 2>&1 | tail -4
cd $ST
for f in poison_html_rows.jsonl.gz poison_other_rows.jsonl.gz audit_final_stats.json audit_report.md; do
  SZ=$(stat -c %s $f)
  cat $f | python3 ~/stream_cos.py "lhcos-data/demiwtg-data/audit/2026-09-17/$f" "$SZ"
done
echo FINISH_ALL_OK
