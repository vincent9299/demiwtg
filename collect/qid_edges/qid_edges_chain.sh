#!/bin/bash
# qid_edges 第三遍抽取:四谓词全量边 + 类标签;16 路 workers + pigz 解压分发 + 合并
cd /home/ubuntu
rm -f qid_edges_out_*.tsv qid_labels_out_*.tsv /tmp/qe*
setsid nohup python3 qid_edges_extract.py workers > qid_edges_workers.log 2>&1 < /dev/null &
sleep 3
echo "workers up, dispatching... $(date -u +%T)"
pigz -dc --name latest-all.json.gz | python3 qid_edges_extract.py dispatch 2>&1 | tee qid_edges_dispatch.log
echo "DISPATCH_EXIT $(date -u +%T)"
sleep 15
cat qid_edges_out_*.tsv > qid_edges.tsv
cat qid_labels_out_*.tsv > qid_class_labels.tsv
wc -l qid_edges.tsv qid_class_labels.tsv
echo QID_EDGES_DONE
