import os
"""Extract prompts from Qwen-Image-Bench HF jsonl -> qib_prompts.jsonl"""
import json

SRC = os.path.join(os.environ["BAGEL_EVAL_WORKDIR"], 'prompts/qwen_image_bench_hf_v0518.jsonl')
DST = os.path.join(os.environ["BAGEL_EVAL_WORKDIR"], 'prompts/qib_prompts.jsonl')

n = 0
with open(SRC) as f, open(DST, "w") as out:
    for line in f:
        line = line.strip()
        if not line:
            continue
        r = json.loads(line)
        pid = str(r.get("ID", r.get("id", n)))
        prompt = (r.get("prompt_en") or r.get("prompt_cn") or r.get("prompt") or "").strip()
        if not prompt:
            continue
        out.write(json.dumps(
            {"id": f"qib_{pid}", "prompt": prompt,
             "prompt_cn": r.get("prompt_cn", "")}, ensure_ascii=False) + "\n")
        n += 1
print(f"extracted {n} prompts -> {DST}")
