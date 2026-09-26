#!/bin/bash
# dump 落地后一键抽取 P27/P17/P577/P170(全 item 值, 复用 qid_edges_extract 16-fifo 架构)
# 产物: recut_v6/qid_edges_ext2.tsv (qid \t pred \t QID)
set -e
EXTRACT=/yzp/zhaozy/yangzepeng/0905/demiwtg/collect/qid_edges/qid_edges_extract.py
DUMP=/yzp/zhaozy/yangzepeng/0905/datasets/wd_full/latest-all.json.gz
OUTDIR=/yzp/zhaozy/yangzepeng/0905/demiwtg/collect/rerun_v2_20260925/recut_v6
cd "$OUTDIR"
rm -f qid_edges_ext2_out_*.tsv qid_edges_ext2_labels_out_*.tsv /tmp/qe*
export QP_PREDS="P27,P17,P577,P170" QP_OUT="qid_edges_ext2"
setsid nohup python3 "$EXTRACT" workers > qid_edges_ext2_workers.log 2>&1 < /dev/null &
sleep 3
echo "workers up, dispatching... $(date -u +%T)"
PREDS_RE="P27|P17|P577|P170"
pigz -dc "$DUMP" | grep -aE "\"($PREDS_RE)\":" | python3 "$EXTRACT" dispatch 2>&1 | tee qid_edges_ext2_dispatch.log
sleep 15
cat qid_edges_ext2_out_*.tsv > qid_edges_ext2.tsv
wc -l qid_edges_ext2.tsv
echo QID_EDGES_EXT2_DONE
