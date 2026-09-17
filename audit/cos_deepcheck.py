#!/usr/bin/env python3
"""内容完整性抽样(2026-09-17): 匿名全量 GET 重算 sha256 对账本, +
PIL 全解码验截断。输入: 本地账本随机样本 N 行(仅 >6KB 且 ext 属图片类)。
输出: state/integrity_sample.json (统计) + 失败清单 TSV。
"""
import hashlib
import io
import json
import random
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

BUCKET = "lhcos-368f6-1256345599"
REGION = "ap-singapore"
HOST = f"{BUCKET}.cos.{REGION}.myqcloud.com"
ROOT = "lhcos-data/demiwtg-data/datasets/demiwtg/kb/"
IMG_EXT = {"jpg", "jpeg", "png", "gif", "tif", "tiff", "webp", "svg"}
PIL_EXT = {"jpg", "jpeg", "png", "gif", "tif", "tiff", "webp"}  # svg PIL 打不开
N_SHA = 400
N_PIL = 300


def fetch(rel):
    url = f"https://{HOST}/{urllib.request.quote(ROOT + rel)}"
    for attempt in range(5):
        try:
            with urllib.request.urlopen(url, timeout=120) as r:
                return r.read()
        except Exception as e:
            if attempt == 4:
                raise
            time.sleep(2 * (attempt + 1))


def main():
    led = sys.argv[1] if len(sys.argv) > 1 else \
        os.environ.get("KB_AUDIT_LEDGER",
                      "/home/ubuntu/demi/raw/state/qid_images.jsonl")
    rows = []
    with open(led, encoding="utf-8") as f:
        for line in f:
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if (r.get("page_bytes") or 0) > 6000 and r.get("ext") in IMG_EXT:
                rows.append(r)
    random.seed(4242)
    sample_sha = random.sample(rows, N_SHA)
    pil_rows = [r for r in rows if r.get("ext") in PIL_EXT]
    sample_pil = random.sample(pil_rows, min(N_PIL, len(pil_rows)))

    out = {"sha_checked": 0, "sha_mismatch": [], "sha_fetch_err": 0,
           "pil_checked": 0, "pil_broken": [], "pil_fetch_err": 0}

    def do_sha(r):
        data = fetch(r["path"])
        got = hashlib.sha256(data).hexdigest()
        return r, got == r["sha256"], len(data)

    with ThreadPoolExecutor(32) as ex:
        for r, ok, ln in ex.map(do_sha, sample_sha):
            out["sha_checked"] += 1
            if not ok:
                out["sha_mismatch"].append(
                    {"path": r["path"], "ledger_bytes": r["page_bytes"],
                     "got_bytes": ln, "qid": r["qid"]})

    def do_pil(r):
        try:
            data = fetch(r["path"])
            from PIL import Image
            im = Image.open(io.BytesIO(data))
            im.load()
            if im.size != (r.get("width"), r.get("height")):
                return r, "dims"
            return r, None
        except Exception as e:
            return r, f"{type(e).__name__}"

    with ThreadPoolExecutor(32) as ex:
        for r, why in ex.map(do_pil, sample_pil):
            out["pil_checked"] += 1
            if why:
                out["pil_broken"].append({"path": r["path"], "why": why,
                                          "qid": r["qid"],
                                          "ledger_dims":
                                          [r.get("width"), r.get("height")]})

    p = "/home/ubuntu/demi/raw/state/integrity_sample.json"
    with open(p, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(json.dumps({k: (v if not isinstance(v, list) else len(v))
                      for k, v in out.items()}, ensure_ascii=False))
    print("->", p)


if __name__ == "__main__":
    main()
