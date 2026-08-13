#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""由 IP 标签体系的叶子实例生成采集任务（jobs）。

读取 data/ip_instances.json 的全量叶子实例，结合 data/image_collect_config.json
的 defaults，输出一份可直接交给 scripts/multimodal/cli.py 的完整配置：

    python3 scripts/gen_jobs.py
    -> data/image_collect_config.instances.json

每个实例生成一个 job：
  - tag  = "<叶子分类路径> / <实例名>"  （保证跨类目唯一）
  - query = 英文别名（--alias 提供）否则留空，由 config.Job 回退为标签叶子名（中文）

英文查询词映射是提升 Commons 覆盖率的关键；当前仅用别名表覆盖部分实例，
其余回退中文。大规模前可补充完整别名表。
"""

import argparse
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from scripts.multimodal.config import DEFAULTS as MULTIMODAL_DEFAULTS  # noqa: E402


def _norm(name: str) -> str:
    """归一化实例名：去《》、去（...）/（...）注释、去首尾空白。"""
    s = name.strip()
    s = s.replace("《", "").replace("》", "")
    s = re.sub(r"（.*?）", "", s)
    s = re.sub(r"\(.*?\)", "", s)
    return s.strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--instances", default=os.path.join(ROOT, "data", "ip_instances.json"))
    ap.add_argument("--template", default=os.path.join(ROOT, "data", "image_collect_config.json"))
    ap.add_argument("--alias", default=None,
                    help="可选 JSON：{实例名: 英文查询词} 或 {完整tag: 英文查询词}")
    ap.add_argument("--out", default=os.path.join(ROOT, "data", "image_collect_config.instances.json"))
    ap.add_argument("--pilot", type=int, default=0,
                    help="只取前 N 个实例生成试点配置（0=全量）")
    args = ap.parse_args()

    inst = json.load(open(args.instances, encoding="utf-8"))
    tmpl = json.load(open(args.template, encoding="utf-8"))
    alias = json.load(open(args.alias, encoding="utf-8")) if args.alias else {}

    # 归一化别名索引：alias 键可能是实例名或标签（带《》/（）装饰）
    alias_norm = {_norm(k): v for k, v in alias.items()}

    # 收集 (path, it) 顺序，便于 --pilot 截取前 N 个实例
    pairs = []
    for path, items in inst.get("instances", {}).items():
        for it in items:
            pairs.append((path, it))
    if args.pilot and args.pilot > 0:
        pairs = pairs[:args.pilot]

    jobs = []
    seen = set()
    for path, it in pairs:
        tag = f"{path} / {it}"
        # 极端情况下 tag 仍重复时加后缀，确保唯一
        while tag in seen:
            tag = tag + " "
        seen.add(tag)
        job = {"tag": tag}
        q = alias_norm.get(_norm(it)) or alias_norm.get(_norm(tag))
        if q:
            job["query"] = q  # 英文 query（别名）
        # zh_query：中文叶子名（去《》（）装饰），供中文源（wikimedia_zh / baidu）使用
        zh = _norm(it)
        if zh:
            job["zh_query"] = zh
        jobs.append(job)

    out = {
        # 每实例选 target_count 张【不同原图】，按原始宽度档位分布（不再缩放）。
        "defaults": {
            **tmpl.get("defaults", {}),
            "target_count": 4,
            "sources": ["wikimedia", "wikimedia_zh"],
            "unauthorized_sources": list(MULTIMODAL_DEFAULTS["unauthorized_sources"]),
        },
        "jobs": jobs,
    }
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    with_alias = sum(1 for j in jobs if "query" in j)
    print(f"生成 {len(jobs)} 个 job -> {args.out}")
    print(f"  其中带英文 query 别名: {with_alias}，回退中文: {len(jobs) - with_alias}")
    print(f"  授权源: {out['defaults']['sources']}；未授权源: {out['defaults']['unauthorized_sources']}")


if __name__ == "__main__":
    main()
