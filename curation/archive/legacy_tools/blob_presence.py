#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""blob_presence.py — blobs 实存索引（图片字节消费者的共用行过滤件，2026-09-08）。

背景：meta/images.jsonl 已恢复全量 preloss 清单（2,849,013 行），其中大量行
的 blob 在图片丢失后尚未还原（待集群按 URL 补采）。凡是**读图片字节**的消费
者（viewer imgs、评测抽样、VLM 补标、focus1000 caption）必须按 blob 实存过滤
行；只做排序/统计的消费者（如 search_kb 目标排序）不必过滤。

实现：blobs 文件名即内容 sha（AGENTS 2.1），扫一层目录取 64 位十六进制词干
建集合即可，不逐文件哈希（落盘时已保证）。

用法：
    from curation.blob_presence import blob_shas
    have = blob_shas()                # {sha, ...}
    rows = [r for r in rows if r["sha256"] in have]
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BLOBS = ROOT / "datasets" / "demiwtg" / "blobs"


def blob_shas(blobs_root: Path | str | None = None) -> set[str]:
    """blobs 现存 sha 集合（扫 <aa>/ 目录文件名词干，~ms 级）。"""
    root = Path(blobs_root) if blobs_root else BLOBS
    out: set[str] = set()
    if not root.exists():
        return out
    for sub in root.iterdir():
        if not sub.is_dir():
            continue
        for f in sub.iterdir():
            stem = f.name.partition(".")[0]
            if len(stem) == 64:
                out.add(stem)
    return out
