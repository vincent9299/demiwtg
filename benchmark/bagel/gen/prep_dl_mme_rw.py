import os, time
os.environ["HF_HOME"] = "/yzp/zhaozy/yangzepeng/0905/demiwtg/benchmark/bagel/data/hf_home"
from huggingface_hub import snapshot_download
for attempt in range(100):
    try:
        p = snapshot_download(repo_id="yifanzhang114/MME-RealWorld-Base64",
                              repo_type="dataset", max_workers=2)
        print("MME-RealWorld-Base64 downloaded:", p, flush=True)
        break
    except Exception as e:
        print(f"attempt {attempt}: {type(e).__name__}, retry in 10s", flush=True)
        time.sleep(10)
