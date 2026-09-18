#!/bin/bash
# 部署 Qwen-Image-Edit-2511 为 OpenAI 兼容图像编辑服务（默认 GPU1、端口 8002）
# 契约对齐 benchmark/edit/eval_edit_gen.py（chat/completions 内联 base64 图）
# 用法: bash start_qwen_image_edit.sh [PORT] [MODEL_PATH] [SERVED_NAME]
source /yzp/zhaozy/yangzepeng/0905/activate.sh

PORT=${1:-8002}
MODEL_PATH=${2:-/yzp/zhaozy/yangzepeng/0905/models/Qwen-Image-Edit-2511}
SERVED_NAME=${3:-Qwen-Image-Edit-2511}

exec env CUDA_VISIBLE_DEVICES=${QIMG_CUDA:-1} \
  python /yzp/zhaozy/yangzepeng/0905/serve_qwen_image.py \
    --model-path "$MODEL_PATH" \
    --served-model-name "$SERVED_NAME" \
    --port "$PORT" \
    --steps 40 --true-cfg 4.0 --guidance 1.0 --negative-prompt " "
