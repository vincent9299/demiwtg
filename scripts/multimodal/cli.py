"""命令行入口。

用法：
  python3 scripts/multimodal/cli.py --config data/image_collect_config.json
  python3 scripts/multimodal/cli.py --config data/image_collect_config.json --metadata-only
  python3 scripts/multimodal/cli.py --config data/image_collect_config.json --jobs 城市吉祥物
"""

from __future__ import annotations

import argparse
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
    ap.add_argument("--out", default="data/multimodal",
                    help="JSONL 清单产物目录（默认 data/multimodal）")
    ap.add_argument("--images-dir", default="images",
                    help="图片内容寻址存储根目录（默认 images）")
    ap.add_argument("--metadata-only", action="store_true",
                    help="仅跑阶段一（检索+候选 JSONL），不下载")
    ap.add_argument("--jobs", default=None,
                    help="只处理指定标签（逗号分隔）；缺省处理全部")
    ap.add_argument("--source", default=None,
                    help="只处理指定来源（如 wikimedia）；缺省全部")
    args = ap.parse_args(argv)

    jobs = load_config(args.config)
    if args.source:
        jobs = [j for j in jobs if j.source == args.source]
    only_tags = set(t.strip() for t in args.jobs.split(",")) if args.jobs else None

    run(jobs, out_dir=args.out, images_dir=args.images_dir,
        metadata_only=args.metadata_only, only_tags=only_tags)


if __name__ == "__main__":
    main()
