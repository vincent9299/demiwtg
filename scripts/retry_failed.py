#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""重试下载失败的候选（dataset/meta/runs/_latest/downloads_failed.jsonl）。

与全量重跑不同，本脚本只针对已失败的候选，复用其 content_url 重新下载落盘，
写入同一 dataset/blobs 图库（SHA-256 内容寻址，不重复占盘）。
适用于：瞬时 SSL/超时失败，或修复下载器后（如 EXIF 方向校正）需补跑的"误杀"项。

用法：
  python3 scripts/retry_failed.py
"""

import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from multimodal.config import load_taxonomy
from multimodal.models import read_jsonl
from multimodal.downloader import download_and_store
from multimodal.sources import get_adapter
from multimodal.util import RateLimiter


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--taxonomy", default=os.path.join(ROOT, "data", "ip_instances.json"))
    ap.add_argument("--aliases", default=None,
                    help="英文别名表；缺省 data/ip_query_aliases.json")
    ap.add_argument("--failed", default=os.path.join(ROOT, "dataset", "meta", "runs", "_latest", "downloads_failed.jsonl"))
    ap.add_argument("--images-dir", default=os.path.join(ROOT, "dataset", "blobs"))
    ap.add_argument("--out", default=os.path.join(ROOT, "dataset", "meta", "runs", "_retry"))
    args = ap.parse_args()

    jobs, _label = load_taxonomy(args.taxonomy, args.aliases)
    eff = {j.tag: j.effective for j in jobs}
    allowed = get_adapter("wikimedia").allowed_suffixes
    cands = read_jsonl(args.failed)
    print(f"待重试候选: {len(cands)}")

    rate = RateLimiter()
    succ, fail = [], []
    for c in cands:
        e = eff.get(c.tag)
        if not e:
            c.status = "failed"
            c.fail_reason = "找不到对应任务配置"
            fail.append(c)
            continue
        ok, results = download_and_store(c, e, allowed, args.images_dir, rate)
        if ok:
            succ.extend(results)
        else:
            fail.append(c)

    os.makedirs(args.out, exist_ok=True)
    with open(os.path.join(args.out, "downloads_success.jsonl"), "w", encoding="utf-8") as f:
        for s in succ:
            f.write(json.dumps(s.to_dict(), ensure_ascii=False) + "\n")
    with open(os.path.join(args.out, "downloads_failed.jsonl"), "w", encoding="utf-8") as f:
        for c in fail:
            f.write(json.dumps(c.to_dict(), ensure_ascii=False) + "\n")

    print(f"重试成功文件: {len(succ)}")
    print(f"仍失败: {len(fail)}")
    for c in fail[:10]:
        print("   FAIL", c.tag, "|", c.fail_reason)


if __name__ == "__main__":
    main()
