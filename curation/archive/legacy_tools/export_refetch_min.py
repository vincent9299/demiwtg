#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""export_refetch_min.py — 极简补采清单导出（传输带宽优化，2026-09-08）。

背景：全量 preloss 清单（meta/images.jsonl 285 万行）中 264 万行缺 blob 但带
下载链接；全字段 worklist 2.4GB 传输太重。本脚本产出极简清单（gzip）：

行结构（短键省带宽；"在 concept 基础上加一列 url"的落地）：
  c    [概念名]      该图打标的概念（同 sha 多行合并）
  u    str           下载直链（原 content_url）
  s    str           sha256——内容寻址校验与 blob 落盘名（错内容进不了湖）
  e    str           扩展名（blob 文件名用）
  src  str           采集源（按源防盗链头表键：baidu/pixiv/huaban 需 Referer）

行过滤：缺 blob（排除集群已可跳过的实存行）且 content_url 非空（无 URL 行
走常规检索线，不进清单）。质量门不过滤——补图工作面保持完整，取舍留给集群。

湖侧原行元数据（license/author/打标等）不随清单传输：湖 images.jsonl 全量
在册，集群回灌后按 (sha, concept) join 还原，零丢失。

产物：state/collect/demiwtg_data_sync/refetch_min.jsonl.gz

用法：python3 curation/export_refetch_min.py
"""
from __future__ import annotations

import gzip
import json
from pathlib import Path

from blob_presence import blob_shas

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "datasets" / "demiwtg" / "meta" / "images.jsonl"
OUT = ROOT / "state" / "collect" / "demiwtg_data_sync" / "refetch_min.jsonl.gz"


def main() -> None:
    have = blob_shas()
    print(f"[blobs] 实存 {len(have):,} sha（这些行不进清单——集群无需重下）")

    # 按 sha 去重合并（一图多概念 → 一行，c 数组）
    merged: dict[str, dict] = {}
    n_skip_have = n_skip_nourl = 0
    with MANIFEST.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            sha = r.get("sha256") or ""
            if not sha:
                continue
            if sha in have:
                n_skip_have += 1
                continue
            url = r.get("content_url")
            if not url:
                n_skip_nourl += 1
                continue
            rec = merged.get(sha)
            if rec is None:
                merged[sha] = {"c": [i for i in (r.get("instances") or []) if i],
                               "u": url, "s": sha,
                               "e": r.get("ext") or "jpg",
                               "src": r.get("source") or ""}
            else:
                for i in r.get("instances") or []:
                    if i and i not in rec["c"]:
                        rec["c"].append(i)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    n_empty_c = 0
    with gzip.open(OUT, "wt", encoding="utf-8", compresslevel=6) as out:
        for rec in merged.values():
            if not rec["c"]:
                n_empty_c += 1
                continue           # 无概念名的行无法回写清单，不进清单
            out.write(json.dumps(rec, ensure_ascii=False, separators=(",", ":")) + "\n")

    size = OUT.stat().st_size
    print(f"[export] {len(merged) - n_empty_c:,} 行 → {OUT.name}"
          f"（gzip {size/1e6:.0f} MB；全字段版 2,426 MB）")
    print(f"[export] 跳过：blob 实存 {n_skip_have:,} 行 / 无 URL {n_skip_nourl:,} 行"
          f" / 无概念名 {n_empty_c:,} 行")


if __name__ == "__main__":
    main()
