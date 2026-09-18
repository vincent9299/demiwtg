#!/bin/bash
# LoRA + FSDP + flash-attn speed benchmark for BAGEL, aligned with the
# official production fine-tuning recipe (TRAIN.md):
#   resolution:      max_latent_size=64  -> up to 1024px (yaml max_image_size=1024)
#   sequence length: expected_num_tokens=10240 / max_num_tokens=11520
#   lr:              2e-5, constant schedule
# Hardware assumption: single node, 2 GPUs (FULL_SHARD).
set -x
cd "$(dirname "$0")/.."

NGPU=${NGPU:-2}
TOTAL_STEPS=${TOTAL_STEPS:-30}
EXPECTED_TOKENS=${EXPECTED_TOKENS:-10240}
MAX_TOKENS=${MAX_TOKENS:-11520}
MAX_TOKENS_PER_SAMPLE=${MAX_TOKENS_PER_SAMPLE:-10240}
LORA_R=${LORA_R:-32}

export WANDB_MODE=offline
export TOKENIZERS_PARALLELISM=false
export NCCL_DEBUG=WARN
export PYTHONPATH="$(pwd):$PYTHONPATH"
# debug: serialize kernel launches so CUDA errors point at the exact call
# (off by default: it slows training down, only enable when debugging)
export CUDA_LAUNCH_BLOCKING=${CUDA_LAUNCH_BLOCKING:-0}

torchrun --nnodes=1 --node_rank=0 --nproc_per_node=$NGPU \
  --master_addr=127.0.0.1 --master_port=29511 \
  train/lora_pretrain_navit.py \
  --dataset_config_file ./data/configs/example.yaml \
  --model_path /tank/demiwtg/bagel/models/BAGEL-7B-MoT \
  --layer_module Qwen2MoTDecoderLayer \
  --max_latent_size 64 \
  --finetune_from_hf True \
  --log_every 1 \
  --lr 2e-5 \
  --num_workers 1 \
  --expected_num_tokens $EXPECTED_TOKENS \
  --max_num_tokens $MAX_TOKENS \
  --max_num_tokens_per_sample $MAX_TOKENS_PER_SAMPLE \
  --use_flex True \
  --sharding_strategy FULL_SHARD \
  --total_steps $TOTAL_STEPS \
  --save_every 100000 \
  --warmup_steps 100 \
  --results_dir /tank/demiwtg/bagel/results/lora_flash_speed \
  --checkpoint_dir /tank/demiwtg/bagel/results/lora_flash_speed/checkpoints \
  --wandb_project bagel-lora \
  --wandb_name lora_flash_speed \
  --wandb_offline True \
  --lora_r $LORA_R \
  --lora_alpha $LORA_R \
  --example_data_dir /tank/demiwtg/bagel/bagel_example \
  "$@"
