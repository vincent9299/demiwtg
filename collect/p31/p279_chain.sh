#!/bin/bash
# P279+labels 第二遍抽取:dump 已装配好,无等待;16 路 workers + pigz 解压分发 + 合并
cd /home/ubuntu
rm -f p279_out_*.tsv /tmp/p279f*
setsid nohup python3 p279_labels_extract.py workers > p279_workers.log 2>&1 < /dev/null &
sleep 3
echo "workers up, dispatching... $(date -u +%T)"
pigz -dc --name latest-all.json.gz | python3 p279_labels_extract.py dispatch 2>&1 | tee p279_dispatch.log
echo "DISPATCH_EXIT $(date -u +%T)"
sleep 10
cat p279_out_*.tsv > p279_all.tsv
wc -l p279_all.tsv
echo P279_EXTRACT_DONE
