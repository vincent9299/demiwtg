"""CLIPScore for generated images (rule-based, local, no API).

Usage: python clip_score_eval.py --image_dir <dir> --benchmark dpg|qib
Loads prompts, computes CLIP ViT-B/32 similarity per image, writes
<dir>/clipscore_results.json with mean + per-id scores.
"""
import argparse, glob, json, os

import clip
import torch
from PIL import Image

parser = argparse.ArgumentParser()
parser.add_argument("--image_dir", type=str, required=True)
parser.add_argument("--benchmark", type=str, default="dpg", choices=["dpg", "qib"])
parser.add_argument("--batch", type=int, default=64)
args = parser.parse_args()

# -------- prompts
prompts = {}
if args.benchmark == "dpg":
    d = "/tank/demiwtg/bagel/gen_eval/ELLA/dpg_bench/prompts"
    for f in os.listdir(d):
        if f.endswith(".txt"):
            prompts[os.path.splitext(f)[0]] = open(os.path.join(d, f)).read().strip()
else:
    with open("/tank/demiwtg/bagel/gen_eval/prompts/qib_prompts.jsonl") as f:
        for line in f:
            r = json.loads(line)
            prompts[r["id"]] = r["prompt"]

device = "cuda" if torch.cuda.is_available() else "cpu"
model, preprocess = clip.load("ViT-B/32", device=device)
model.eval()

images = sorted(glob.glob(os.path.join(args.image_dir, "*.png")))
per_id = {}
scores = []
texts_buf, ids_buf, imgs_buf = [], [], []

def flush():
    if not texts_buf:
        return
    with torch.no_grad():
        tf = clip.tokenize(texts_buf, truncate=True).to(device)
        it = torch.stack([preprocess(i) for i in imgs_buf]).to(device)
        fi = model.encode_image(it)
        ft = model.encode_text(tf)
        fi = fi / fi.norm(dim=-1, keepdim=True)
        ft = ft / ft.norm(dim=-1, keepdim=True)
        sim = (fi * ft).sum(dim=-1) * 100.0
    for pid, s in zip(ids_buf, sim.tolist()):
        per_id[pid] = round(s, 4)
        scores.append(s)
    texts_buf.clear(); ids_buf.clear(); imgs_buf.clear()

for path in images:
    pid = os.path.splitext(os.path.basename(path))[0]
    if pid not in prompts:
        continue
    try:
        img = Image.open(path).convert("RGB")
    except Exception as e:
        print(f"skip {pid}: {e}")
        continue
    texts_buf.append(prompts[pid]); ids_buf.append(pid); imgs_buf.append(img)
    if len(texts_buf) >= args.batch:
        flush()
flush()

mean = float(torch.tensor(scores).mean()) if scores else 0.0
result = {"benchmark": args.benchmark, "image_dir": args.image_dir,
          "n_images": len(scores), "clipscore_mean": round(mean, 4),
          "per_id": per_id}
out_path = os.path.join(args.image_dir, "clipscore_results.json")
with open(out_path, "w") as f:
    json.dump(result, f, indent=1)
print(f"CLIPScore: n={len(scores)} mean={mean:.4f} -> {out_path}")
