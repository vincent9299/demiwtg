# -*- coding: utf-8 -*-
"""源发现（六步闭环第 2 步，低频 LLM 环节；产物人工过目，零自动晋升）。

输入缺口簇（gap_report.json），LLM 基于世界知识提出候选源【提案】：
只有端点草案 + 探针查询词，不含抽取规则——完整 spec 由 P2 synth 基于
probe 拿到的真实响应样本合成（探测前置：不信任 LLM 对响应结构的猜想）。

防幻觉护栏（全部确定性代码）：
- name 必须 [a-z0-9_]+ 且不与注册表/已有提案重名；
- search_url_draft 必须 https 且 host 与声明的 api_host 一致；
- 生成源一律 authorized=false（授权证据裁决在验收环节，不在发现环节）；
- 字段白名单过滤：LLM 输出中未登记的键一律丢弃。

产物：state/collect/auto/proposals/<name>.json（status=proposed）。

用法：python3 collect/cli.py discover [--max-clusters N] [--dry-run]
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import re

from . import auto_dir
from ..registry import load_registry

_NAME_RE = re.compile(r"^[a-z0-9_]+$")
MAX_PROPOSALS_PER_CLUSTER = 2   # 护栏：每簇提案数上限（对齐每轮晋升<=2 的预算姿态）

# LLM 输出字段白名单（闭集；未登记键丢弃）
_SOURCE_KEYS = ("name", "source_kind", "capabilities", "lang", "api_host",
                "search_url_draft", "params_draft", "notes", "sample_queries")

_SYSTEM = """你是图片采集源发现助手。给定一个标签簇的采集缺口，提出最适合补齐的
图片检索 API 源。只输出 JSON：{"sources": [ ... ]}，每个源字段：
name（小写字母数字下划线的机器名）、source_kind（目录|数据集|领域社区|搜索引擎）、
capabilities（字符串数组）、lang（en|zh|both）、api_host（API 主机名）、
search_url_draft（https 检索端点 URL 草案）、
params_draft（查询参数对象，用 {query} 作为查询词占位符）、
notes（端点依据：API 文档/公开接口约定，不确定就明说）、
sample_queries（2-4 个适合探针测试的查询词，优先英文规范名）。
硬约束：只提公开免鉴权的 GET JSON API（需要签名/JS 渲染/登录的一律不提）；
最多 %(max)s 个；宁缺毋滥，不确定端点真实存在就不要提。"""


def _validate(src: dict, taken: set) -> "tuple[dict, str]":
    """白名单过滤 + 硬约束校验；返回 (提案, 拒绝原因或空串)。"""
    p = {k: src[k] for k in _SOURCE_KEYS if k in src}
    name = p.get("name") or ""
    if not _NAME_RE.match(name):
        return p, "name 非法: %r" % name
    if name in taken:
        return p, "name 已存在: %s" % name
    url = p.get("search_url_draft") or ""
    if not url.startswith("https://"):
        return p, "search_url_draft 非 https: %r" % url
    host = url.split("/", 3)[2].split(":")[0].lower()
    api_host = (p.get("api_host") or "").lower()
    if not api_host or not (host == api_host or host.endswith("." + api_host)):
        return p, "url host %s 与 api_host %r 不一致" % (host, api_host)
    p["authorized"] = False     # 生成源一律未授权（授权裁决在验收环节）
    qs = [q for q in (p.get("sample_queries") or []) if isinstance(q, str) and q.strip()]
    if not qs:
        return p, "缺少 sample_queries"
    p["sample_queries"] = qs[:4]
    return p, ""


def discover_cluster(client, cluster: dict, existing: list,
                     use_responses: bool, dry_run: bool) -> list:
    """对单个缺口簇调用一次 LLM，返回合法提案列表。"""
    from taxonomy.llm_common import generate

    sample = "\n".join(
        "- %s（有 %d 缺 %d，查询词 %r，别名 %s）" % (
            i["name"], i["have"], i["need"], i["query"], "/".join(i["aliases"]) or "-")
        for i in cluster["top_starved"])
    user = ("缺口簇：%s（缺 %d 个实例、共 %d 张图）\n最饥渴实例：\n%s\n\n"
            "已有来源（避免重复）：%s" % (
                cluster["cluster"], cluster["gap_instances"],
                cluster["total_need"], sample, ", ".join(existing)))
    system = _SYSTEM % {"max": MAX_PROPOSALS_PER_CLUSTER}
    if dry_run:
        print("=" * 72)
        print("[dry-run] cluster=%s\n-- system --\n%s\n-- user --\n%s" % (
            cluster["cluster"], system, user))
        return []
    out = generate(client, system, user, use_responses=use_responses) or {}
    return list(out.get("sources") or [])


def main(argv=None):
    ap = argparse.ArgumentParser(prog="collect discover",
                                 description="LLM 源发现（产提案，不晋升）")
    ap.add_argument("--meta", default="data/dataset/meta")
    ap.add_argument("--gap-report", default=None,
                    help="缺口报告路径（默认 state/collect/auto/gap_report.json）")
    ap.add_argument("--max-clusters", type=int, default=1,
                    help="本轮处理的缺口簇数（按缺口量降序，默认 1）")
    ap.add_argument("--dry-run", action="store_true", help="不调 API，仅打印 prompt")
    args = ap.parse_args(argv)

    adir = auto_dir(args.meta)
    gap_path = args.gap_report or os.path.join(adir, "gap_report.json")
    if not os.path.exists(gap_path):
        raise SystemExit("缺少缺口报告 %s；先运行: python3 collect/cli.py gap" % gap_path)
    with open(gap_path, encoding="utf-8") as f:
        report = json.load(f)
    clusters = report.get("clusters") or []
    if not clusters:
        print("无缺口簇，退出。")
        return

    from taxonomy import llm_common
    if not args.dry_run:
        llm_common.require_api_key()
        client = llm_common.make_client()
    else:
        client = None

    reg = load_registry(args.meta)
    prop_dir = os.path.join(adir, "proposals")
    os.makedirs(prop_dir, exist_ok=True)
    taken = set(reg.names()) | {fn[:-5] for fn in os.listdir(prop_dir)
                                if fn.endswith(".json")}
    cache = llm_common.JsonlCache(os.path.join(adir, "discover_cache.jsonl"))
    done = cache.done_keys()

    existing = reg.names()
    n_ok = n_bad = 0
    for cluster in clusters[:args.max_clusters]:
        key = "cluster:%s" % cluster["cluster"]
        if key in done:
            print("[skip] 簇 %s 已发现过（缓存续跑）" % cluster["cluster"])
            continue
        try:
            sources = discover_cluster(client, cluster, existing,
                                       llm_common.want_responses(), args.dry_run)
        except Exception as e:  # noqa: BLE001
            print("[warn] 簇 %s LLM 调用失败: %s" % (cluster["cluster"], e))
            cache.append(key, {"error": str(e)}, ok=False)
            continue
        if args.dry_run:
            continue
        kept = []
        for src in sources[:MAX_PROPOSALS_PER_CLUSTER * 2]:
            p, reason = _validate(src, taken)
            if reason:
                n_bad += 1
                print("[reject] %s" % reason)
                continue
            taken.add(p["name"])
            kept.append(p)
            path = os.path.join(prop_dir, p["name"] + ".json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"status": "proposed",
                           "cluster": cluster["cluster"],
                           "generated_at": datetime.datetime.now().isoformat(
                               timespec="seconds"),
                           "proposal": p}, f, ensure_ascii=False, indent=1)
            n_ok += 1
            print("[提案] %s -> %s" % (p["name"], path))
        cache.append(key, {"proposals": [p["name"] for p in kept],
                           "rejected": n_bad}, ok=True)

    if not args.dry_run:
        print("完成：提案 %d 个，拒绝 %d 个。下一步: python3 collect/cli.py probe"
              % (n_ok, n_bad))


if __name__ == "__main__":
    main()
