"""命令行入口。

用法：
  python3 scripts/multimodal/cli.py --config data/image_collect_config.json
  python3 scripts/multimodal/cli.py --config data/image_collect_config.json --metadata-only
  python3 scripts/multimodal/cli.py --config data/image_collect_config.json --jobs 城市吉祥物

存储布局（数据湖风格，见 docs/IP图片数据集_存储重设计方案.md）：
  dataset/
    blobs/              # 原始图片字节，内容寻址 <aa>/<sha256>.<ext>
    meta/
      images.jsonl      # 主清单（每张图一行，按 sha256 去重）
      tags.json         # tag↔图 关系索引
      runs/<run_id>/    # 本批次过程产物（candidates/success/failed/rejected/stats）
      runs/_latest      # -> 最新 <run_id>
    by_tag/             # 按标签的软链树（由 tags.json 派生）
"""

from __future__ import annotations

import argparse
import datetime
import os
import sys

# 支持两种运行方式：
#   python3 scripts/multimodal/cli.py ...
#   python3 -m scripts.multimodal.cli ...
if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    from scripts.multimodal.config import load_config
    from scripts.multimodal.pipeline import run
else:
    from .config import load_config
    from .pipeline import run


def main(argv=None):
    ap = argparse.ArgumentParser(description="多模态图片采集系统（M1: Wikimedia Commons）")
    ap.add_argument("--config", required=True, help="采集任务配置 JSON 路径")

    # 数据湖风格布局：--meta 为 dataset/meta 根；--run-id 命名本批次 runs/<run_id>；
    # --out 默认由 --meta + --run-id 推导，可显式覆盖。
    ap.add_argument("--meta", default="dataset/meta",
                    help="元数据根目录（默认 dataset/meta），主清单 images.jsonl/tags.json 写于此")
    ap.add_argument("--run-id", default=None,
                    help="本批次 ID（默认时间戳）；runs/<run-id> 存放本批过程产物")
    ap.add_argument("--out", default=None,
                    help="本批次 JSONL 产物目录（默认 <meta>/runs/<run-id>）")
    ap.add_argument("--images-dir", default="dataset/blobs",
                    help="图片内容寻址存储根目录（默认 dataset/blobs），授权/未授权源统一落盘于此")
    ap.add_argument("--metadata-only", action="store_true",
                    help="仅跑阶段一（检索+候选 JSONL），不下载")
    ap.add_argument("--jobs", default=None,
                    help="只处理指定标签（逗号分隔）；缺省处理全部")
    ap.add_argument("--source", default=None,
                    help="只处理指定来源（如 wikimedia）；缺省全部")
    args = ap.parse_args(argv)

    run_id = args.run_id or datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    meta_dir = args.meta
    out_dir = args.out or os.path.join(meta_dir, "runs", run_id)

    jobs = load_config(args.config)
    if args.source:
        jobs = [j for j in jobs if j.source == args.source]
    only_tags = set(t.strip() for t in args.jobs.split(",")) if args.jobs else None

    run(jobs, out_dir=out_dir, images_dir=args.images_dir,
        meta_dir=meta_dir, run_id=run_id,
        metadata_only=args.metadata_only, only_tags=only_tags)


if __name__ == "__main__":
    main()
