"""增量消费分类器：实体标签（instances.json 实例名）vs 已下载状态（meta/images.jsonl）。

标签即实体名（不含路径，见 AGENTS.md 1.5）：全路径精确匹配退化为名字匹配；
历史兼容保留 (父,叶) 组合兜底（旧格式路径 tag 的中间分支改名不误重下），
实体名 tag 无父段，兜底自然失效。

三种消费模式（classify 的 mode 参数）：
  delta        只保留 state 中完全不存在的标签（新增实体专用）；
  replay       全量重放：已达标（>= min_images）的标签跳过，未达标/新标签补采；
  replay-rules 同 replay（实体名不随路径改名，身份规则退化为精确匹配）。
"""

from __future__ import annotations

import json
import os
from typing import Dict, List, Optional, Tuple


def split_tag(tag: str) -> Tuple[str, str]:
    """返回 (直接父分支名, 叶子实例名)。实体名 tag（单段，不含路径）父名为空串。"""
    parts = [p for p in tag.split(" / ") if p]
    if len(parts) >= 2:
        return parts[-2], parts[-1]
    return "", parts[-1] if parts else ""


def build_state_indexes(tags_map: Dict[str, list]) -> Tuple[Dict[str, int], Dict[Tuple[str, str], int]]:
    """从 tags_map（tag -> [条目]）构建两级索引：全路径->张数、(父,叶)->张数。"""
    exact: Dict[str, int] = {t: len(v) for t, v in tags_map.items()}
    fuzzy: Dict[Tuple[str, str], int] = {}
    for t, v in tags_map.items():
        key = split_tag(t)
        fuzzy[key] = fuzzy.get(key, 0) + len(v)
    return exact, fuzzy


def classify(jobs: list, tags_map: Dict[str, list], mode: str,
             min_images: int) -> Tuple[list, Dict[str, int], Dict[str, list]]:
    """把 jobs 按消费模式分类。

    返回 (kept_jobs, existing_counts, report)：
      kept_jobs        本轮需要检索/下载的 jobs；
      existing_counts  kept 中每个标签的已有图数（用于 stage2 topup 目标计算）；
      report           {skip_exact, skip_fuzzy, skip_delta_fuzzy, topup, new} 各标签列表。
    """
    if mode not in ("delta", "replay", "replay-rules"):
        raise ValueError(f"未知 consume_mode: {mode!r}（可选 delta/replay/replay-rules）")
    exact, fuzzy = build_state_indexes(tags_map)
    kept: list = []
    existing_counts: Dict[str, int] = {}
    report: Dict[str, list] = {
        "skip_exact": [], "skip_fuzzy": [], "skip_delta_fuzzy": [],
        "topup": [], "new": [],
    }
    for j in jobs:
        tag = j.tag
        n = exact.get(tag, 0)
        if n:
            if mode == "delta" or n >= min_images:
                # delta 只采新标签：已存在即跳过（不论是否达标）；
                # replay/replay-rules：已达标才跳过。
                report["skip_exact"].append(tag)
                continue
            kept.append(j)
            existing_counts[tag] = n
            report["topup"].append(tag)
            continue
        fn = fuzzy.get(split_tag(tag), 0)
        if mode == "delta":
            if fn:
                report["skip_delta_fuzzy"].append(tag)
            else:
                kept.append(j)
                report["new"].append(tag)
            continue
        # replay / replay-rules：replay 模式不做模糊跳过（保持旧行为：只看精确路径），
        # replay-rules 模式模糊命中且达标即跳过、未达标按 topup 补采。
        if mode == "replay-rules" and fn:
            if fn >= min_images:
                report["skip_fuzzy"].append(tag)
                continue
            kept.append(j)
            existing_counts[tag] = fn  # 旧路径的图计入基数，topup 只补缺口
            report["topup"].append(tag)
            continue
        kept.append(j)
        report["new"].append(tag)
    return kept, existing_counts, report


def summarize(report: Dict[str, list]) -> str:
    return (f"跳过(精确命中) {len(report['skip_exact'])} / "
            f"跳过(组合达标) {len(report['skip_fuzzy'])} / "
            f"跳过(delta组合命中) {len(report['skip_delta_fuzzy'])} / "
            f"补采 {len(report['topup'])} / 新标签 {len(report['new'])}")



