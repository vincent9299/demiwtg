"""命令行入口。

用法：
  python3 scripts/multimodal/cli.py --taxonomy data/instances_meta.json
  python3 scripts/multimodal/cli.py --taxonomy data/instances_meta.json --consume-mode replay-rules
  python3 scripts/multimodal/cli.py --taxonomy data/instances_meta.json \
      --sources wikimedia,wikimedia_zh,inaturalist,coco,hf_coco   # 启用可选数据集源
  python3 scripts/multimodal/cli.py --taxonomy data/instances_meta.json --metadata-only
  python3 scripts/multimodal/cli.py --taxonomy data/instances_meta.json --jobs 城市吉祥物

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
    from scripts.multimodal.config import EffectiveConfig, load_config, load_taxonomy
    from scripts.multimodal.pipeline import run
    import scripts.multimodal.sources  # noqa: F401  触发适配器注册
    from scripts.multimodal.sources.base import _REGISTRY as _SOURCE_REGISTRY
else:
    from .config import EffectiveConfig, load_config, load_taxonomy
    from .pipeline import run
    from . import sources as _sources_pkg  # noqa: F401  触发适配器注册
    from .sources.base import _REGISTRY as _SOURCE_REGISTRY


def main(argv=None):
    ap = argparse.ArgumentParser(description="多模态图片采集系统（M1: Wikimedia Commons）")
    ap.add_argument("--config", help="采集任务配置 JSON 路径（覆盖/临时任务用；"
                                      "常规采集建议用 --taxonomy 直读标签体系）")
    ap.add_argument("--taxonomy", help="统一标签体系实例元文件（如 data/instances_meta.json）："
                                       "直接以标签体系为采集输入，实时派生全量 jobs")
    ap.add_argument("--aliases", default=None,
                    help="（已废弃）别名已并入 instances_meta.json 的 instance.aliases；保留仅为兼容")

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
    ap.add_argument("--sources", default=None,
                    help="覆盖授权源列表（逗号分隔，整体替换）。默认 wikimedia,"
                         "wikimedia_zh,inaturalist；可选源如 coco/hf_coco/hf_laion/openverse")
    ap.add_argument("--unauthorized-sources", default=None,
                    help="覆盖未授权源列表（逗号分隔，整体替换）；传 none 表示全部关闭。"
                         "缺省使用内置 18 个中文源列表")
    ap.add_argument("--max-per-source", default=None, type=int,
                    help="每源每标签最多下载张数（覆盖默认；1=各活源各采 1 张最相关图）")
    ap.add_argument("--consume-mode", default="replay",
                    choices=["delta", "replay", "replay-rules"],
                    help="增量消费模式：delta=只采新标签；replay=全量重放（达标跳过，默认）；"
                         "replay-rules=重放+两级身份匹配跳过（分支改名不误重下）+topup 补采缺口")
    args = ap.parse_args(argv)

    run_id = args.run_id or datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    meta_dir = args.meta
    out_dir = args.out or os.path.join(meta_dir, "runs", run_id)

    if bool(args.config) == bool(args.taxonomy):
        ap.error("--config 与 --taxonomy 必须且只能提供一个")
    if args.taxonomy:
        jobs, taxonomy_name = load_taxonomy(args.taxonomy, args.aliases)
    else:
        jobs = load_config(args.config)
        taxonomy_name = os.path.basename(args.config)

    def _parse_sources(val: str) -> list:
        if val.strip().lower() == "none":
            return []
        names = [s.strip() for s in val.split(",") if s.strip()]
        bad = [n for n in names if n not in _SOURCE_REGISTRY]
        if bad:
            ap.error("未知来源 %s；已注册来源：%s" % (bad, sorted(_SOURCE_REGISTRY)))
        return names

    if (args.sources is not None or args.unauthorized_sources is not None
            or args.max_per_source is not None):
        src = _parse_sources(args.sources) if args.sources is not None else None
        unauth = (_parse_sources(args.unauthorized_sources)
                  if args.unauthorized_sources is not None else None)
        for j in jobs:
            if src is not None:
                j.defaults["sources"] = src
            if unauth is not None:
                j.defaults["unauthorized_sources"] = unauth
            if args.max_per_source is not None:
                j.defaults["max_per_source"] = args.max_per_source
            j.effective = EffectiveConfig.resolve(j.defaults, j.overrides)

    if args.source:
        jobs = [j for j in jobs if j.source == args.source]
    only_tags = set(t.strip() for t in args.jobs.split(",")) if args.jobs else None

    run(jobs, out_dir=out_dir, images_dir=args.images_dir,
        meta_dir=meta_dir, run_id=run_id,
        metadata_only=args.metadata_only, only_tags=only_tags,
        consume_mode=args.consume_mode,
        taxonomy_name=taxonomy_name)


if __name__ == "__main__":
    main()
