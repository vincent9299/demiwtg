#!/bin/bash
# Overfit sanity test: train LoRA on 2 unique VLM samples for 300 steps.
# If the gradient path (flash attn -> backward -> optimizer -> LoRA update)
# is correct, CE loss should collapse toward ~0.
set -x
cd "$(dirname "$0")/.."

NGPU=${NGPU:-1}
TOTAL_STEPS=${TOTAL_STEPS:-300}
EXPECTED_TOKENS=${EXPECTED_TOKENS:-2048}
MAX_TOKENS=${MAX_TOKENS:-4096}
MAX_TOKENS_PER_SAMPLE=${MAX_TOKENS_PER_SAMPLE:-4096}
LORA_R=${LORA_R:-32}

export WANDB_MODE=offline
export TOKENIZERS_PARALLELISM=false
export NCCL_DEBUG=WARN
export PYTHONPATH="$(pwd):$PYTHONPATH"
export CUDA_LAUNCH_BLOCKING=${CUDA_LAUNCH_BLOCKING:-0}

torchrun --nnodes=1 --node_rank=0 --nproc_per_node=$NGPU \
  --master_addr=127.0.0.1 --master_port=29513 \
  train/lora_pretrain_navit.py \
  --dataset_config_file ./data/configs/overfit_t2i.yaml \
  --model_path /tank/demiwtg/bagel/models/BAGEL-7B-MoT \
  --layer_module Qwen2MoTDecoderLayer \
  --max_latent_size 64 \
  --finetune_from_hf True \
  --log_every 5 \
  --lr 1e-4 \
  --num_workers 1 \
  --expected_num_tokens $EXPECTED_TOKENS \
  --max_num_tokens $MAX_TOKENS \
  --max_num_tokens_per_sample $MAX_TOKENS_PER_SAMPLE \
  --use_flex True \
  --sharding_strategy FULL_SHARD \
  --total_steps $TOTAL_STEPS \
  --save_every 100000 \
  --warmup_steps 20 \
  --results_dir /tank/demiwtg/bagel/results/lora_overfit_t2i \
  --checkpoint_dir /tank/demiwtg/bagel/results/lora_overfit_t2i/checkpoints \
  --wandb_project bagel-lora \
  --wandb_name lora_overfit_t2i \
  --wandb_offline True \
  --lora_r $LORA_R \
  --lora_alpha $LORA_R \
  --example_data_dir /tank/demiwtg/bagel/bagel_example_overfit \
  "$@"
