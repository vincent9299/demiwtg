#!/bin/bash
# Sequentially evaluate BAGEL-7B-MoT on all mapped benchmarks.
# Env: LORA_PATH (empty -> base model), WORK_DIR, CUDA_VISIBLE_DEVICES
set -u
# keep dataset cache & HF downloads on the big /root/data volume
export LMUData=/tank/demiwtg/bagel/LMUData
export HF_HOME=/tank/demiwtg/bagel/hf_home
# project conda env: torch 2.6.0+cu124 + vlmeval (editable). The old .venv
# (py3.12, torch cu130) is incompatible with the CUDA 12.9 driver.
PYTHON=${PYTHON:-/tank/demiwtg/.venv/bin/python}
WORK_DIR=${WORK_DIR:-/tank/demiwtg/bagel/eval_outputs/base}
LOG_DIR=$WORK_DIR/logs
mkdir -p "$LOG_DIR"
cd /tank/demiwtg/bagel/VLMEvalKit

DATASETS=(
  MMBench_DEV_EN_V11
  MMBench_DEV_CN_V11
  MME
  MME-RealWorld
  MME-RealWorld-CN
  SEEDBench_IMG
  SEEDBench2_Plus
  CV-Bench-2D
  CV-Bench-3D
  RealWorldQA
  MathVista_MINI
  WeMath
  MathVision_MINI
  OCRBench
  AI2D_TEST
  AI2D_TEST_NO_MASK
  DocVQA_VAL
  ChartQA_TEST
  InfoVQA_VAL
  CountBenchQA
)

TAG=base
[ -n "${LORA_PATH:-}" ] && TAG=lora
# optional subset: DATASETS_OVERRIDE="MME-RealWorld DocVQA_VAL" bash run_eval_suite.sh
if [ -n "${DATASETS_OVERRIDE:-}" ]; then
  read -r -a DATASETS <<< "$DATASETS_OVERRIDE"
fi
echo "[suite] tag=$TAG work_dir=$WORK_DIR datasets=${#DATASETS[@]}"

for ds in "${DATASETS[@]}"; do
  echo "[suite] ===== $ds START $(date +%H:%M:%S)"
  "$PYTHON" /tank/demiwtg/bagel/Bagel/scripts/run_bagel_eval.py \
    --data "$ds" --model BAGEL-7B-MoT \
    --work-dir "$WORK_DIR" >> "$LOG_DIR/$ds.log" 2>&1
  echo "[suite] ===== $ds END rc=$? $(date +%H:%M:%S)"
done
echo "[suite] ALL DONE"
