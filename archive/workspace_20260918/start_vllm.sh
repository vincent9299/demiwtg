#!/bin/bash
# 启动 vLLM 服务（使用项目 conda 环境）
source /yzp/zhaozy/yangzepeng/0905/activate.sh

MODEL=${1:-/yzp/zhaozy/yangzepeng/0905/models/Qwen2.5-0.5B-Instruct}
NAME=${2:-qwen2.5-0.5b}
PORT=${3:-8000}

exec vllm serve "$MODEL" \
  --served-model-name "$NAME" \
  --host 0.0.0.0 \
  --port "$PORT" \
  --gpu-memory-utilization 0.9
