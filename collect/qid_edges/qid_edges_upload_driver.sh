#!/bin/bash
# 等 qid_edges 抽取完成 → 完整性核对 → 上传 COS
cd /home/ubuntu
while ! grep -q QID_EDGES_DONE qid_edges_chain.log 2>/dev/null; do sleep 30; done
echo "extract done $(date -u +%T)"
sleep 15
WE=$(wc -l < qid_edges.tsv)
LE=$(wc -l < qid_class_labels.tsv)
DN=$(grep -h "^w[0-9]* DONE" qid_edges_workers.log | awk '{gsub(/,/,"",$3); sub(/edges=/,"",$3); s+=$3} END {print int(s)}')
DL=$(grep -h "^w[0-9]* DONE" qid_edges_workers.log | awk '{gsub(/,/,"",$4); sub(/labels=/,"",$4); s+=$4} END {print int(s)}')
echo "edges_file=$WE edges_done=$DN labels_file=$LE labels_done=$DL"
if [ -n "$DN" ] && [ "$DN" -gt 0 ] && [ $((DN>WE?DN-WE:WE-DN)) -gt $((WE/20)) ]; then
  echo "EDGES_MISMATCH>5% — 不上传,等人工"; exit 1
fi
for p in $(pgrep -f "qid_edges_extract.py workers"); do kill $p 2>/dev/null; done
sleep 2
python3 qid_edges_upload.py
echo "UPLOAD_DRIVER_EXIT $?"
