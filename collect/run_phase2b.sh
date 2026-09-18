#!/bin/bash
# ③ 增肥接力驱动器(2026-09-13 晋升自夜航工作区 kb_night/qid_build/run_phase2b_v2.sh)
# 编排仓库内 flow_wikidata.py 的 filter→enrich 两段 + gzip + 发运(训练机 meta + COS kb/),
# 与 demiflow 机制配合:策略在此、算子在 operators/。段文件(truthy 分片)路径按需改。
# 原版实战: 2026-09-10/11 夜全链跑通(223万P18任务, 05:33 全部完成)。
set -u
BASE=/home/ubuntu/demi/kb_night/qid_build
REPO=/home/ubuntu/demi/demiwtg-data
PY=/home/ubuntu/demi/.venv/bin/python
CONCEPTS=$BASE/qid_concepts.jsonl
SSH_OPTS="-o BatchMode=yes -o ConnectTimeout=20"

CONCAT="cat $BASE/truthy/seg_0000000000000000; \
ssh $SSH_OPTS pipeline-b 'cat ~/truthy_chunk/tail0'; \
cat $BASE/truthy/seg_0900000000000000; \
ssh $SSH_OPTS pipeline-c 'cat ~/truthy_chunk/tail1'; \
ssh $SSH_OPTS pipeline-a 'cat ~/truthy_chunk/seg_18000000000'; \
ssh $SSH_OPTS pipeline-b 'cat ~/truthy_chunk/seg_26000000000'; \
ssh $SSH_OPTS pipeline-c 'cat ~/truthy_chunk/seg_34000000000'"

echo "[relay2] 过滤启动 $(date +%H:%M:%S)"
cd "$REPO" && PYTHONPATH="$REPO" "$PY" flow_wikidata.py filter \
  --qids "$CONCEPTS" --out "$BASE/props.jsonl" \
  --concat-cmd "$CONCAT" --concurrency 2 --log-every 1000000 \
  || { echo "[relay2] 过滤失败"; exit 2; }

echo "[relay2] 增肥启动 $(date +%H:%M:%S)"
cd "$REPO" && PYTHONPATH="$REPO" "$PY" flow_wikidata.py enrich \
  --concepts "$CONCEPTS" --props "$BASE/props.jsonl" \
  --out-enriched "$BASE/qid_concepts.fat.jsonl" \
  --out-graph "$BASE/qid_graph.jsonl" \
  || { echo "[relay2] 增肥失败"; exit 3; }

echo "[relay2] 压缩发运 $(date +%H:%M:%S)"
gzip -f "$BASE/qid_concepts.fat.jsonl" "$BASE/qid_graph.jsonl"
ssh $SSH_OPTS lake 'cat > /yzp/zhaozy/yangzepeng/0905/demiwtg/datasets/demiwtg/meta/qid_concepts.fat.jsonl.gz' \
  < "$BASE/qid_concepts.fat.jsonl.gz" || echo "[relay2] 训练机推送失败(人工补)"
ssh $SSH_OPTS lake 'cat > /yzp/zhaozy/yangzepeng/0905/demiwtg/datasets/demiwtg/meta/qid_graph.jsonl.gz' \
  < "$BASE/qid_graph.jsonl.gz" || echo "[relay2] 图推送失败(人工补)"
cp "$BASE/qid_concepts.fat.jsonl.gz" "$BASE/qid_graph.jsonl.gz" \
   /lhcos-data/demiwtg-data/datasets/demiwtg/kb/ 2>/dev/null \
   || echo "[relay2] COS 备份失败(人工补)"
echo "[relay2] 全部完成 $(date +%H:%M:%S)"
