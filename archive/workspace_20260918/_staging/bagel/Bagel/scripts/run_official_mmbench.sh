#!/bin/bash
# A/B 对照实验：用官方 Bagel/eval/vlm 代码跑 MMBench DEV EN V11
# 目的：排除 VLMEvalKit 调用层差异（预处理/dispatch/prompt），验证官方协议下的真实得分
# 官方代码零改动，仅新增数据文件 eval/vlm/data/mmbench/*.tsv
set -x

export PATH=/tank/demiwtg/.venv/bin:$PATH
export CUDA_VISIBLE_DEVICES=0
# GPU 0 被另一租户 vLLM 占 43GB，只剩 ~36GB：禁缓存池减少碎片，给整卡加载留空间
export PYTORCH_NO_CUDA_MEMORY_CACHING=1
export PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True

# evaluate.sh 需要的分布式环境变量（单机单卡）
export ARNOLD_WORKER_NUM=1
export ARNOLD_ID=0
export ARNOLD_WORKER_0_HOST=127.0.0.1
export MASTER_PORT=29512
export GPUS=1

cd /tank/demiwtg/bagel/Bagel

bash eval/vlm/evaluate.sh mmbench-dev-en \
    --model-path /tank/demiwtg/bagel/models/BAGEL-7B-MoT \
    --out-dir /tank/demiwtg/bagel/eval_outputs/base/official_mmbench_en \
    --batch-size 1 --num-workers 1

echo "[official-ab] MMBench done rc=$?"
