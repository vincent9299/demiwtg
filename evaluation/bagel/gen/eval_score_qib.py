"""Render BAGEL QIB scores vs official 18-model leaderboard (CN-prompt track).

Usage: python qib_leaderboard.py --bench <judge_input_bench_scores.json>
"""
import argparse
import json

# Official Q-Judger leaderboard, Chinese-prompt track
# (dataset card README, Qwen/Qwen-Image-Bench)
OFFICIAL = [
    # model, Quality, Aesthetics, Alignment, RW-Fidelity, Creative, Overall
    ("GPT Image 2", 58.65, 67.53, 65.85, 57.38, 75.23, 64.69),
    ("Nano Banana 2.0", 54.77, 61.08, 62.40, 54.28, 67.05, 59.82),
    ("GPT Image 1.5", 55.14, 60.88, 61.72, 53.95, 66.35, 59.65),
    ("Nano Banana Pro", 55.67, 60.26, 61.25, 54.07, 66.23, 59.45),
    ("Qwen Image 2.0 Pro", 54.39, 58.67, 59.28, 51.83, 64.94, 57.84),
    ("Seedream 5.0", 52.55, 58.40, 58.90, 51.92, 65.29, 57.22),
    ("Seedream 4.5", 54.41, 58.72, 57.31, 51.69, 60.64, 56.78),
    ("Seedream 4.0", 54.01, 58.81, 56.64, 51.05, 58.15, 56.21),
    ("FLUX 2 Max", 53.64, 56.85, 57.35, 49.35, 56.50, 55.33),
    ("FLUX 2 Pro", 52.30, 56.94, 57.01, 47.29, 56.18, 54.57),
    ("GPT Image 1", 52.34, 55.09, 56.28, 48.14, 55.78, 54.07),
    ("Qwen Image 2512", 51.76, 54.74, 52.72, 47.00, 50.19, 52.06),
    ("Imagen 4.0 Ultra", 50.90, 54.25, 54.02, 45.59, 51.14, 51.99),
    ("HunyuanImage 3.0", 50.35, 53.57, 52.00, 44.31, 49.12, 50.81),
    ("Imagen 4.0", 50.16, 52.68, 51.64, 44.84, 47.94, 50.29),
    ("Qwen Image", 48.44, 52.25, 50.72, 43.16, 47.30, 49.23),
    ("Kling Image 2.1", 49.11, 50.15, 49.18, 44.74, 44.67, 48.26),
    ("GLM Image", 49.26, 50.64, 47.90, 44.69, 45.23, 48.19),
]

L1_KEYS = {
    "Quality": "Quality",
    "Aesthetics": "Aesthetics",
    "Alignment": "Alignment",
    "Real-world Fidelity": "RW-Fid",
    "Creative Generation": "Creative",
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bench", required=True)
    ap.add_argument("--name", default="BAGEL-7B-MoT (ours)")
    args = ap.parse_args()

    bench = json.load(open(args.bench))
    l1 = {L1_KEYS[k]: v for k, v in bench["level1"].items()}

    row = {k: (round(v, 2) if v is not None else None) for k, v in l1.items()}
    row["Overall"] = round(bench["total"], 2) if bench["total"] is not None else None
    cols = ["Quality", "Aesthetics", "Alignment", "RW-Fid", "Creative", "Overall"]

    entries = [(args.name, row)] + [(m, dict(zip(cols, sc))) for m, *sc in OFFICIAL]
    entries.sort(key=lambda e: e[1].get("Overall") or -1, reverse=True)

    print("| Rank | Model | " + " | ".join(cols) + " |")
    print("|---|---|" + "---|" * len(cols))
    for i, (name, r) in enumerate(entries, 1):
        cells = [f"**{r.get(c)}**" if (i == 1 and r.get(c) is not None) else str(r.get(c, "-"))
                 for c in cols]
        print(f"| {i} | {name} | " + " | ".join(cells) + " |")


if __name__ == "__main__":
    main()
