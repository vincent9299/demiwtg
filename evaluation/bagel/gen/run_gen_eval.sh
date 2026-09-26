#!/bin/bash
: "${BAGEL_EVAL_WORKDIR:?Set an explicit external regression exchange directory; see README.md}"
# Generation evaluation pipeline for BAGEL-7B-MoT on DPG-Bench / Qwen-Image-Bench.
# Usage:
#   bash run_gen_eval.sh base dpg          # base model, DPG-Bench only
#   bash run_gen_eval.sh base all          # base model, both benchmarks
#   bash run_gen_eval.sh lora dpg          # + t2i LoRA adapter
#   LIMIT=32 bash run_gen_eval.sh base dpg # smoke test with 32 prompts
set -uo pipefail

# mplug 评分模型缓存（目录规整后位置）
export MODELSCOPE_CACHE=${BAGEL_EVAL_WORKDIR}/modelscope
export HF_HOME=${BAGEL_EVAL_WORKDIR}/hf_home
# project conda env (torch 2.6.0+cu124); .venv was removed
PYTHON=${PYTHON:-/yzp/zhaozy/yangzepeng/0905/env-bagel/bin/python}

MODEL_TAG=${1:-base}      # base | lora
BENCH=${2:-dpg}           # dpg | qib | all
LIMIT=${LIMIT:-0}
GPUS=${GPUS:-"0 1"}       # GPUs for sharded generation, e.g. GPUS=1 for single GPU
GEN_ROOT=${BAGEL_EVAL_WORKDIR}/images
SCRIPTS=/yzp/zhaozy/yangzepeng/0905/demiwtg/evaluation/bagel/gen
T2I_LORA=/yzp/zhaozy/yangzepeng/0905/demiwtg/bagel/results/lora_overfit_t2i/checkpoints/final/lora.safetensors

if [ "$MODEL_TAG" = "lora" ]; then export LORA_PATH="$T2I_LORA"; else unset LORA_PATH; fi

run_gen() {
  local bench=$1 out_dir=$2
  mkdir -p "$out_dir"
  local gpus=($GPUS)
  local n=${#gpus[@]}
  echo "[gen-pipe] generating $bench ($MODEL_TAG) -> $out_dir limit=$LIMIT gpus='$GPUS'"
  local pids=() i
  for i in "${!gpus[@]}"; do
    CUDA_VISIBLE_DEVICES=${gpus[$i]} "$PYTHON" "$SCRIPTS/eval_gen.py" \
      --benchmark "$bench" --out_dir "$out_dir" --image_size 512 \
      --limit "$LIMIT" --shard "$i" --num_shards "$n" \
      > "$out_dir/gen_shard$i.log" 2>&1 &
    pids+=($!)
  done
  local rcs=() p
  for p in "${pids[@]}"; do wait "$p"; rcs+=($?); done
  echo "[gen-pipe] $bench generation done rcs=(${rcs[*]})"
  # CLIPScore (local rule-based)
  "$PYTHON" "$SCRIPTS/eval_score_clip.py" --image_dir "$out_dir" --benchmark "$bench" \
    > "$out_dir/clip_eval.log" 2>&1
  cat "$out_dir/clip_eval.log" | tail -1
}

score_dpg() {
  local out_dir=$1
  local gpus=($GPUS)
  echo "[gen-pipe] official DPG scoring (mPLUG VQA) on $out_dir"
  CUDA_VISIBLE_DEVICES=${gpus[0]} "$PYTHON" "$SCRIPTS/eval_score_dpg.py" \
    --image_dir "$out_dir" --resolution 512 \
    > "$out_dir/dpg_score.log" 2>&1
  tail -6 "$out_dir/dpg_results.txt" 2>/dev/null
}

for bench in $( [ "$BENCH" = "all" ] && echo "dpg qib" || echo "$BENCH" ); do
  out_dir="$GEN_ROOT/${MODEL_TAG}_${bench}"
  run_gen "$bench" "$out_dir"
  [ "$bench" = "dpg" ] && score_dpg "$out_dir"
done
echo "[gen-pipe] ALL DONE tag=$MODEL_TAG bench=$BENCH"
