import os
import time
from huggingface_hub import hf_hub_download
for attempt in range(50):
    try:
        p = hf_hub_download(repo_id="Qwen/Qwen-Image-Bench",
                            filename="qwen_image_bench_hf_v0518.jsonl",
                            repo_type="dataset",
                            local_dir=os.path.join(os.environ["BAGEL_EVAL_WORKDIR"], 'prompts'))
        print("downloaded:", p, flush=True)
        break
    except Exception as e:
        print(f"attempt {attempt} failed: {type(e).__name__}, retrying...", flush=True)
        time.sleep(5)
