#!/bin/bash
# P106/P171 补充抽取(QP_PREDS/QP_OUT 参数化),合并为 qid_edges_ext.tsv
cd /home/ubuntu
rm -f qid_edges_ext_out_*.tsv qid_edges_ext_labels_out_*.tsv /tmp/qe*
export QP_PREDS="P106,P171,P140,P131,P136" QP_OUT="qid_edges_ext"
setsid nohup python3 qid_edges_extract.py workers > qid_edges_ext_workers.log 2>&1 < /dev/null &
sleep 3
echo "workers up, dispatching... $(date -u +%T)"
# grep 预滤:C 速砍掉不含目标谓词的行(2026-09-24 实测瓶颈在 Python 调度器)
PREDS_RE=$(echo $QP_PREDS | tr "," "\n" | sed "s/^/P/" | paste -sd"|" -)
pigz -dc --name latest-all.json.gz | grep -aE "\"($PREDS_RE)":" | python3 qid_edges_extract.py dispatch 2>&1 | tee qid_edges_ext_dispatch.log
echo "DISPATCH_EXIT $(date -u +%T)"
sleep 15
cat qid_edges_ext_out_*.tsv > qid_edges_ext.tsv
wc -l qid_edges_ext.tsv
echo QID_EDGES_EXT_DONE
