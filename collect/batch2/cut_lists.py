#!/usr/bin/env python3
"""第 2 批候选清单切批上 COS 队列（对齐第 1 批布局，结果暂存 COS）。

源 tsv（湖侧 lists/ 已拉取）→ 统一任务行 → queue-b2-<src>/batches/b%06d.jsonl.gz
（2000 行/批）+ manifest.json。幂等：--skip-existing 时已存在的批跳过。

行构造：
- oi:   imageID \t mids \t url  ──mid_map 展开──> {src,extid,url,qids:[Q…]}
- si:   media \t object \t QID \t bridge \t license \t title \t url
        ──按 media 去重聚合──> {src,extid,url,license,qids:[Q…]}
- inat: QID \t photo_id \t license \t dims \t uuid \t url ──> {src,extid,qid,url,license}
- met:  objectID \t QID \t mount \t label ──> {src,extid,qid,url:""}（算子两段式解析）

用法：
  python3 cut_lists.py --src oi [--rows-per-batch 2000] [--canary 50] [--skip-existing]
"""
from __future__ import annotations

import argparse
import gzip
import json
import os
import sys
import time

sys.path.insert(0, '/yzp/zhaozy/yangzepeng/0905/demiflow')
from demiflow.collect.cosio import COSCreds, COSIO, build_host
from demiflow.collect.cosqueue import COSQueue

BUCKET, REGION = "lhcos-368f6-1256345599", "ap-singapore"
LISTS = os.path.dirname(os.path.abspath(__file__)) + "/lists"


def tsv_rows(path):
    with gzip.open(path, "rt", encoding="utf-8", errors="replace") as f:
        for line in f:
            parts = line.rstrip("\n").split("\t")
            if len(parts) >= 2:
                yield parts


def gen_oi(limit):
    # 第二列已是消歧后的 Q 号数字部分（891/891 命中 mid_map 值域实证）；
    # mid_map 仅审计留档，无需参与展开
    n = 0
    for parts in tsv_rows(f"{LISTS}/fetch_openimages.tsv.gz"):
        if len(parts) < 3:
            continue
        img, qnums, url = parts[0], parts[1], parts[2]
        # GCS 桶已收回公开读（403 Anonymous caller）；S3 镜像同构路径匿名可读
        url = url.replace("https://storage.googleapis.com/openimages/",
                          "https://open-images-dataset.s3.amazonaws.com/")
        qids = sorted({f"Q{x}" for x in qnums.split(",") if x})
        if not qids:
            continue
        yield {"src": "oi", "extid": img, "url": url, "qids": qids}
        n += 1
        if limit and n >= limit:
            return


def gen_si(limit):
    """474 万行按 media 聚合（同 media 多 QID 只下一次，账本带全 qids）。

    2026-09-20 用户裁定方案 C：URL 一律改取 `.jpg` 衍生版（同分辨率有损压缩，
    体积 ~1/10；tif 原图均值 87MB 全量 286TB 不可行，jpg 中位 19.4MP 足训高分辨率）。
    """
    agg, n = {}, 0
    for parts in tsv_rows(f"{LISTS}/fetch_smithsonian.tsv.gz"):
        if len(parts) < 7:
            continue
        media, _obj, qid, _bridge, lic, _title, url = parts[:7]
        if not url.lower().endswith(".jpg"):
            url = url.rsplit(".", 1)[0] + ".jpg"
        r = agg.get(media)
        if r is None:
            agg[media] = {"src": "si", "extid": media, "url": url,
                          "license": lic, "qids": [qid]}
            n += 1
            if limit and n >= limit:
                break
        elif qid not in r["qids"]:
            r["qids"].append(qid)
    yield from agg.values()


def gen_inat(limit):
    n = 0
    for parts in tsv_rows(f"{LISTS}/fetch_inat.tsv.gz"):
        if len(parts) < 6:
            continue
        qid, photo, lic, _dims, _uuid, url = parts[:6]
        yield {"src": "inat", "extid": photo, "qid": qid, "url": url,
               "license": lic}
        n += 1
        if limit and n >= limit:
            return


def gen_met(limit):
    n = 0
    for parts in tsv_rows(f"{LISTS}/fetch_met.tsv.gz"):
        if len(parts) < 2:
            continue
        oid, qid = parts[0], parts[1]
        yield {"src": "met", "extid": oid, "qid": qid, "url": ""}
        n += 1
        if limit and n >= limit:
            return


GENS = {"oi": gen_oi, "si": gen_si, "inat": gen_inat, "met": gen_met}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True, choices=list(GENS))
    ap.add_argument("--rows-per-batch", type=int, default=2000)
    ap.add_argument("--canary", type=int, default=0, help="只切前 N 行（金丝雀）")
    ap.add_argument("--skip-existing", action="store_true")
    args = ap.parse_args()

    creds = COSCreds.from_file(
        '/yzp/zhaozy/yangzepeng/0905/demiwtg/collect/image_backfill/.cos_creds')
    io = COSIO(creds, build_host(BUCKET, REGION))
    prefix = (f"lhcos-data/demiwtg-data/queue-b2-{args.src}-canary"
              if args.canary else f"lhcos-data/demiwtg-data/queue-b2-{args.src}")
    q = COSQueue(io, prefix)

    t0 = time.time()
    bids = q.produce(GENS[args.src](args.canary or 0),
                     rows_per_batch=args.rows_per_batch,
                     skip_existing=args.skip_existing)
    man = {"src": args.src, "batches": len(bids),
           "rows_per_batch": args.rows_per_batch,
           "canary": args.canary, "ts": time.time(),
           "elapsed_s": round(time.time() - t0, 1)}
    io.put_bytes(f"{prefix}/manifest.json",
                 json.dumps(man, ensure_ascii=False).encode())
    print(f"[cut] {args.src}{'(canary)' if args.canary else ''}: "
          f"{len(bids)} 批 × {args.rows_per_batch} 行 → {prefix} "
          f"({man['elapsed_s']}s)", flush=True)


if __name__ == "__main__":
    main()
