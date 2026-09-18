"""配图线编排(纯声明,2026-09-10):概念 P18 → Commons 原图 → COS 图池。

fleet 入口:每台机器独立跑本入口,任务切片靠 --shard i/N(概念流位置
步长切片,与 flow_kb 同款);各机账本本地落,收尾合并(幂等键 (qid,
sha256) 天然去重)。全量口径:按 QID 顺序系统扫,不设双语插队。

管线(全部 map_async 推模式):
  from_iter(iter_p18_tasks)   增肥概念 → 任务行(已收跳过,断点续跑)
  → CommonsFetchStage         API 元数据 + 字节下载(限速/重试/守门)
  → CommonsBlobSink           kb/blobs + qid_images-shard 账本

运行(各机,PYTHONPATH=<仓库根>):
  python3 flow_images.py --concepts qid_concepts.fat.jsonl \
      --blobs-root /lhcos-data/demiwtg-data/datasets/demiwtg/kb/blobs \
      --manifest qid_images-shard-i.jsonl --shard i/N
"""

from __future__ import annotations

import argparse
import itertools
import json
import os
import time

for _k in list(os.environ):
    if "proxy" in _k.lower():
        del os.environ[_k]

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="配图线:P18 → Commons 原图")
    p.add_argument("--concepts", required=True, help="增肥版概念文件(.jsonl[.gz])")
    p.add_argument("--blobs-root", required=True, help="COS kb/blobs 池根")
    p.add_argument("--manifest", required=True, help="本机账本 jsonl")
    p.add_argument("--shard", default="", metavar="I/N", help="fleet 分片")
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--concurrency", type=int, default=8,
                   help="下载并发(礼貌红线内,勿超 8)")
    p.add_argument("--log-every", type=int, default=2000)
    return p.parse_args()


def load_done(manifest: str) -> tuple[set, set]:
    """本机既有账本 → 已收 (qid,file) 跳过集 + (qid,sha) 幂等集。"""
    done_files: set = set()
    if os.path.exists(manifest):
        with open(manifest, encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                done_files.add((r["qid"], r["commons_file"]))
    return done_files, set()


def main() -> None:
    args = parse_args()
    from operators.commons import (CommonsBlobSink, CommonsFetchStage,
                                   iter_p18_tasks)
    from demiflow.standalone import local_data

    done, _ = load_done(args.manifest)
    factory = iter_p18_tasks(args.concepts, done)
    if args.shard:
        i, n = (int(x) for x in args.shard.split("/"))
        inner = factory
        factory = lambda: itertools.islice(inner(), i, None, n)  # noqa: E731

    fetch = CommonsFetchStage()
    fetch.concurrency = args.concurrency
    fetch.queue_depth = args.concurrency       # 字节载荷,队列即内存上界
    sink = CommonsBlobSink(blobs_root=args.blobs_root, manifest=args.manifest)
    src = local_data().from_iter(factory)
    if args.limit:
        src = src.limit(args.limit)
    t0 = time.time()
    stats = (src.map_async(fetch).map_async(sink)
             .run_stream(log_every=args.log_every))
    print(f"[images] 本机收图 {sink.sunk:,}(API 命中 {fetch.fetched:,});"
          f"引擎口径:{stats.summary()}")


if __name__ == "__main__":
    main()
