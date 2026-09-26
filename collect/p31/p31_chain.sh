#!/bin/bash
# 等 wd_pull 完成 → 起 16 路 workers → pigz 解压流分发 → 合并
cd /home/ubuntu
while ! grep -q ASSEMBLED wd_pull.log 2>/dev/null; do sleep 30; done
echo "PULL_DONE $(date -u +%T)"
rm -f p31_out_*.tsv /tmp/p31f*
setsid nohup python3 p31_extract.py workers > p31_workers.log 2>&1 < /dev/null &
sleep 3
echo "workers up, dispatching..."
pigz -dc --name latest-all.json.gz | python3 p31_extract.py dispatch 2>&1 | tee p31_dispatch.log
echo "DISPATCH_EXIT $(date -u +%T)"
sleep 5
cat p31_out_*.tsv > p31_all.tsv
wc -l p31_all.tsv
echo P31_EXTRACT_DONE
