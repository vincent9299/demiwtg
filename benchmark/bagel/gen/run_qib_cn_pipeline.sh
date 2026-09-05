#!/bin/bash
# QIB official-protocol end-to-end pipeline (Chinese-prompt track).
# Stage 1: regenerate 1000 images with prompt_cn (dual-GPU sharded, resumable)
# Stage 2: build judge_input.jsonl (ID/prompt/image_path)
# Stage 3: official Q-Judger inference (ms-swift TransformersEngine, single GPU)
# Stage 4: leaderboard comparison vs official 18-model results (CN-prompt track)
set -uo pipefail

ROOT=/yzp/zhaozy/yangzepeng/0905/demiwtg/bagel
SCRIPTS=/yzp/zhaozy/yangzepeng/0905/demiwtg/benchmark/bagel/gen
OFFICIAL=/yzp/zhaozy/yangzepeng/0905/demiwtg/benchmark/bagel/gen/qib_official
IMG_DIR=/yzp/zhaozy/yangzepeng/0905/demiwtg/benchmark/bagel/data/images/base_qib_cn
JUDGE_MODEL=$ROOT/models/QIB-Judger
QPY=$ROOT/qib_env/bin/python
PY=/yzp/zhaozy/yangzepeng/0905/env-bagel/bin/python
export QIB_PROMPT_FILE=/yzp/zhaozy/yangzepeng/0905/demiwtg/benchmark/bagel/data/prompts/qib_prompts_cn.jsonl
export MODELSCOPE_CACHE=/yzp/zhaozy/yangzepeng/0905/demiwtg/benchmark/bagel/data/modelscope
export HF_HOME=/yzp/zhaozy/yangzepeng/0905/demiwtg/benchmark/bagel/data/hf_home

mkdir -p "$IMG_DIR"

# ---------------- Stage 1: generation (dual-GPU, resumable by existing pngs)
if [ "$(ls "$IMG_DIR"/*.png 2>/dev/null | wc -l)" -lt 1000 ]; then
  echo "[qib-cn-pipe] stage1: dual-GPU generation -> $IMG_DIR"
  pids=()
  for i in 0 1; do
    CUDA_VISIBLE_DEVICES=$i $PY $SCRIPTS/eval_gen.py \
      --benchmark qib --out_dir "$IMG_DIR" --image_size 512 \
      --shard $i --num_shards 2 \
      > "$IMG_DIR/gen_shard$i.log" 2>&1 &
    pids+=($!)
  done
  for p in "${pids[@]}"; do wait "$p" || echo "[qib-cn-pipe] WARN gen pid=$p rc=$?"; done
else
  echo "[qib-cn-pipe] stage1: 1000 pngs already present, skip"
fi
n_imgs=$(ls "$IMG_DIR"/*.png 2>/dev/null | wc -l)
echo "[qib-cn-pipe] stage1 done: $n_imgs/1000 images"
if [ "$n_imgs" -lt 1000 ]; then
  echo "[qib-cn-pipe] FATAL: generation incomplete ($n_imgs < 1000), aborting before judge"
  exit 1
fi

# ---------------- Stage 2: judge input
JUDGE_INPUT=$IMG_DIR/judge_input.jsonl
$PY - <<'PYEOF'
import json, os
img_dir = "/yzp/zhaozy/yangzepeng/0905/demiwtg/benchmark/bagel/data/images/base_qib_cn"
src = "/yzp/zhaozy/yangzepeng/0905/demiwtg/benchmark/bagel/data/prompts/qib_prompts_cn.jsonl"
out = os.path.join(img_dir, "judge_input.jsonl")
rows = []
with open(src) as f:
    for line in f:
        r = json.loads(line)
        pid = int(r["id"].split("_", 1)[1])
        path = os.path.join(img_dir, f"{r['id']}.png")
        if not os.path.exists(path):
            continue
        rows.append({"ID": pid, "prompt": r["prompt"], "image_path": path})
with open(out, "w") as f:
    for r in rows:
        f.write(json.dumps(r, ensure_ascii=False) + "\n")
print(f"judge_input: {len(rows)} rows -> {out}")
PYEOF
[ -s "$JUDGE_INPUT" ] || { echo "[qib-cn-pipe] FATAL: empty judge input"; exit 1; }

# ---------------- Stage 3: official Q-Judger inference (single GPU)
# wait for judge weights to be fully downloaded (>=55G safetensors + index)
echo "[qib-cn-pipe] stage3: waiting for judge weights in $JUDGE_MODEL"
for i in $(seq 1 240); do
  sz=$(du -sm "$JUDGE_MODEL" 2>/dev/null | cut -f1)
  if [ -n "$sz" ] && [ "$sz" -ge 55000 ] && [ -f "$JUDGE_MODEL/model.safetensors.index.json" ] \
     && ! ls "$JUDGE_MODEL" | grep -q '\.incomplete$'; then
    echo "[qib-cn-pipe] judge weights ready (${sz}MB)"; break
  fi
  [ $((i % 6)) -eq 0 ] && echo "[qib-cn-pipe] still waiting weights: ${sz:-0}MB ($i min)"
  sleep 60
done
echo "[qib-cn-pipe] stage3: Q-Judger inference on GPU0"
cd "$OFFICIAL"
CUDA_VISIBLE_DEVICES=0 $QPY judge.py \
  --input "$JUDGE_INPUT" \
  --model "$JUDGE_MODEL" \
  --max-batch-size 24 --max-new-tokens 4096 \
  2>&1 | tee "$IMG_DIR/judge_run.log"
rc=$?
echo "[qib-cn-pipe] stage3 done rc=$rc"

# ---------------- Stage 4: leaderboard comparison
BENCH_JSON=${JUDGE_INPUT%.jsonl}_bench_scores.json
if [ -f "$BENCH_JSON" ]; then
  $PY $SCRIPTS/eval_score_qib.py --bench "$BENCH_JSON" | tee "$IMG_DIR/leaderboard.md"
else
  echo "[qib-cn-pipe] WARN: bench scores not found at $BENCH_JSON"
fi
echo "[qib-cn-pipe] ALL DONE"
