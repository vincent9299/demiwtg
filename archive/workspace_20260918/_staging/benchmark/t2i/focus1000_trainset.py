#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""focus1000 高难度图问训练集构建（题面↔生成图配对）。

定位（2026-09-02 用户拍板）：V6.0 维度采样的复杂题面 + qwen-image-3.0-pro 生成图，
作为另一种更高难度的训练数据（更细节的图问对）；目标 1000 条起步，生图续跑后
重新执行本脚本即可增量刷新。

产物：data/focus1000/train_pairs.jsonl，每行一对：
  {pair_id, prompt_id, instance, variant, gen_prompt, key_visual_conclusions,
   dims:{level,combo_type,scene_types,premise_types,hop_types,knowledge_categories},
   image:{blob_path, gen_path, sha256, width, height, ratio}}
blob_path 指向数据湖内容寻址位置（merge 后的权威字节源），gen_path 保留 benchmark
侧原始文件位置。断点幂等：全量重建，行序按 prompt_id 稳定排序。

用法：python3 focus1000_trainset.py
"""
from __future__ import annotations

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = Path(__file__).resolve().parent / "data" / "focus1000"
PROMPTS_F = DATA_DIR / "gen_prompts.jsonl"
RESULTS_F = DATA_DIR / "gen_results.jsonl"
OUT_F = DATA_DIR / "train_pairs.jsonl"
TARGET = 1000


def main():
    prompts = {json.loads(l)["prompt_id"]: json.loads(l)
               for l in PROMPTS_F.read_text().splitlines() if l.strip()}
    pairs = {}
    for line in RESULTS_F.read_text().splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        if r.get("error") or r["prompt_id"] not in prompts:
            continue
        p = prompts[r["prompt_id"]]
        pairs[r["prompt_id"]] = {
            "pair_id": r["sha256"][:16],
            "prompt_id": r["prompt_id"],
            "instance": r["instance"],
            "variant": r["variant"],
            "gen_prompt": p["gen_prompt"],
            "key_visual_conclusions": p.get("key_visual_conclusions") or [],
            "dims": {"level": p.get("level"), "combo_type": p.get("combo_type"),
                     "scene_types": p.get("scene_types") or [],
                     "premise_types": p.get("premise_types") or [],
                     "hop_types": p.get("hop_types") or [],
                     "knowledge_categories": p.get("knowledge_categories") or []},
            "image": {"blob_path": f"blobs/{r['sha256'][:2]}/{r['sha256']}.png",
                      "gen_path": r["file"],
                      "sha256": r["sha256"],
                      "width": r.get("width"), "height": r.get("height"),
                      "ratio": r.get("ratio")},
        }
    rows = [pairs[k] for k in sorted(pairs)]
    OUT_F.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
                     encoding="utf-8")
    insts = {r["instance"] for r in rows}
    print(f"[trainset] 配对 {len(rows)} / 目标 {TARGET}（缺 {max(0, TARGET - len(rows))}）"
          f" | 覆盖实例 {len(insts)}/1000 | prompts 总池 {len(prompts)}")
    print(f"[trainset] 写出 {OUT_F}")


if __name__ == "__main__":
    main()
