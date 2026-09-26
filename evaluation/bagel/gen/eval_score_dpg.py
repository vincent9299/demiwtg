"""DPG-Bench official scoring (mPLUG VQA), single-GPU single-process port.

Replicates TencentQQGYLab/ELLA dpg_bench/compute_dpg_bench.py logic:
per-image yes/no VQA over dependency-aware propositions, child questions
zeroed when any parent is answered 'no'. Adds a stub to bypass modelscope's
OFA/fairseq import chain (only mPLUG is used).

Usage: python score_dpg_mplug.py --image_dir <dir> --resolution 512
"""
import argparse, os

# NOTE: must run before any modelscope import - satisfies the OFA/fairseq
# import chain with dummy modules (only mPLUG is actually used for scoring).
import _fairseq_stub  # noqa: F401

import pandas as pd
import torch
from PIL import Image
from tqdm import tqdm
from modelscope.pipelines import pipeline
from modelscope.utils.constant import Tasks

parser = argparse.ArgumentParser()
parser.add_argument("--image_dir", type=str, required=True)
parser.add_argument("--resolution", type=int, default=512)
parser.add_argument("--csv", type=str, default=os.path.join(os.environ["BAGEL_EVAL_WORKDIR"], 'dpg_bench/dpg_bench.csv'))
parser.add_argument("--device", type=str, default="cuda:0")
args = parser.parse_args()

# ---------------------------------------------------------------- questions
question_dict = {}
data = pd.read_csv(args.csv)
for _, line in data.iterrows():
    cid = str(line.item_id)
    qid = int(line.proposition_id)
    deps = [int(d.strip()) for d in line.dependency.split(",")]
    if cid not in question_dict:
        question_dict[cid] = dict(qid2tuple={}, qid2dependency={}, qid2question={})
    question_dict[cid]["qid2tuple"][qid] = line.tuple
    question_dict[cid]["qid2dependency"][qid] = deps
    question_dict[cid]["qid2question"][qid] = line.question_natural_language

# ---------------------------------------------------------------- model
# resolve the local snapshot from MODELSCOPE_CACHE: modelscope-hub 0.2.0's
# repo-id resolution is broken ('HubConfig' has no '_logged_out'), and the
# model is already downloaded, so go straight to the cached snapshot
_ms_cache = os.environ.get("MODELSCOPE_CACHE", os.path.join(os.environ["BAGEL_EVAL_WORKDIR"], 'modelscope'))
_mplug_dir = os.path.join(
    _ms_cache, "models", "damo--mplug_visual-question-answering_coco_large_en",
    "snapshots", "master")
if not os.path.isdir(_mplug_dir):
    raise FileNotFoundError(f"mPLUG snapshot not found: {_mplug_dir}")
vqa_pipe = pipeline(Tasks.visual_question_answering,
                    model=_mplug_dir,
                    device=args.device)

# ---------------------------------------------------------------- scoring
res_path = os.path.join(args.image_dir, "dpg_results.txt")
det_path = res_path.replace(".txt", "_detail.txt")
open(res_path, "w").close()
open(det_path, "w").close()

scores_all = []
cat2scores = {}
files = sorted(f for f in os.listdir(args.image_dir) if f.endswith(".png"))
for fn in tqdm(files):
    key = os.path.splitext(fn)[0]
    value = question_dict.get(key, None)
    if value is None:
        continue
    try:
        img = Image.open(os.path.join(args.image_dir, fn)).convert("RGB")
    except Exception as e:
        print("skip", fn, e)
        continue
    qid2question = value["qid2question"]
    qid2dependency = value["qid2dependency"]
    qid2tuple = value["qid2tuple"]
    qid2scores = {}
    for qid, question in qid2question.items():
        ans = vqa_pipe({"image": img, "question": question})["text"]
        qid2scores[qid] = float(ans == "yes")
        with open(det_path, "a") as f:
            f.write(f"{fn}, {question}, {ans}\n")
    for qid, parents in qid2dependency.items():
        if any(qid2scores[p] == 0 for p in parents if p != 0):
            qid2scores[qid] = 0
    score = sum(qid2scores.values()) / len(qid2scores)
    scores_all.append(score)
    for qid in qid2tuple.keys():
        cat = qid2tuple[qid].split("(")[0].strip()
        cat2scores.setdefault(cat, []).append(qid2scores[qid])
    with open(res_path, "a") as f:
        f.write(f"{fn}, {score}\n")

import numpy as np
out = [f"Model dir: {args.image_dir}", f"n_images: {len(scores_all)}"]
l1 = {}
for cat, vals in cat2scores.items():
    l1.setdefault(cat.split("-")[0].strip(), []).extend(vals)
out.append("L1 category scores:")
for k, v in sorted(l1.items()):
    out.append(f"\t{k}: {np.mean(v)*100:.2f}")
out.append("L2 category scores:")
for cat, vals in sorted(cat2scores.items()):
    out.append(f"\t{cat}: {np.mean(vals)*100:.2f}")
out.append(f"DPG-Bench score: {np.mean(scores_all)*100:.2f}")
report = "\n".join(out)
with open(res_path, "a") as f:
    f.write(report + "\n")
print(report)
