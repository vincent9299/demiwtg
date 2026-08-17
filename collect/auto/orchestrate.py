# -*- coding: utf-8 -*-
"""编排层（L4，纯确定性调度，自身零 LLM 调用）——一轮缺口驱动闭环。

单轮顺序：
  govern（生命周期记账：晋升/降级/退休）→ repair（degraded 修复）→
  gap（缺口分析）→ discover（仅在【无未决提案且存在缺口】时）→
  probe → synth → verify → 轮末摘要。

设计边界：
- 编排只做确定性串联与护栏传递（--max-clusters/--max-promote/--max-repair），
  每个环节的智能与裁决仍归各模块（Agent 仅 discover/synth/repair 三处低频点）；
- LLM 步骤（discover/synth）缺 LLM_API_KEY 时整段跳过，治理/验收链不受影响；
- 不触发采集本身：probation/active 源由常规采集 run 经 usable_names 自动参与，
  缺口数字驱动下一轮发现（cron/常驻进程可周期调用本子命令）。

用法：python3 collect/cli.py orchestrate [--max-clusters 1] [--max-promote 2]
      [--skip-discovery] [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import os
from collections import Counter

from . import auto_dir, discover, gap, govern, probe, repair, synth, verify
from ..registry import load_registry


def _pending_proposals(adir: str) -> list:
    """未走完晋升链的提案（未裁决 achieved/blocked）；有则先消化存量再发现新源。"""
    out = []
    prop_dir = os.path.join(adir, "proposals")
    if not os.path.isdir(prop_dir):
        return out
    for fn in sorted(os.listdir(prop_dir)):
        if not fn.endswith(".json"):
            continue
        try:
            with open(os.path.join(prop_dir, fn), encoding="utf-8") as f:
                p = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue
        if p.get("status_lifecycle") not in ("achieved", "blocked"):
            out.append(fn[:-5])
    return out


def _gap_total(adir: str) -> int:
    try:
        with open(os.path.join(adir, "gap_report.json"), encoding="utf-8") as f:
            return int(json.load(f).get("total_need") or 0)
    except (OSError, json.JSONDecodeError, ValueError):
        return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="collect orchestrate",
                                 description="一轮缺口驱动闭环（确定性调度各环节）")
    ap.add_argument("--meta", default="data/dataset/meta")
    ap.add_argument("--taxonomy", default="data/taxonomy/instances.json")
    ap.add_argument("--max-clusters", type=int, default=1,
                    help="discover 每轮簇数上限（护栏，默认 1）")
    ap.add_argument("--max-promote", type=int, default=2,
                    help="verify/govern 每轮晋升上限（护栏，默认 2）")
    ap.add_argument("--max-repair", type=int, default=2,
                    help="repair 每轮修复源数上限（护栏，默认 2）")
    ap.add_argument("--skip-discovery", action="store_true",
                    help="只跑治理/修复/验收链，不做缺口发现")
    ap.add_argument("--dry-run", action="store_true",
                    help="全链 dry-run（govern 只裁决不写；LLM 步骤只打印 prompt）")
    args = ap.parse_args(argv)

    dr = ["--dry-run"] if args.dry_run else []
    adir = auto_dir(args.meta)
    from taxonomy import llm_common
    has_key = bool(llm_common.API_KEY)

    print("=" * 72)
    print("[1/7] govern：健康账本 → 生命周期迁移")
    govern.main(["--meta", args.meta, "--max-promote", str(args.max_promote)] + dr)

    print("-" * 72)
    print("[2/7] repair：degraded 源修复回路")
    repair.main(["--meta", args.meta, "--max-repair", str(args.max_repair)] + dr)

    if args.skip_discovery:
        print("-" * 72)
        print("[3-6/7] --skip-discovery：跳过缺口发现链")
    else:
        print("-" * 72)
        print("[3/7] gap：缺口分析")
        gap.main(["--meta", args.meta, "--taxonomy", args.taxonomy])

        pending = _pending_proposals(adir)
        need = _gap_total(adir)
        print("-" * 72)
        if pending:
            print("[4/7] discover：跳过——存量提案未走完晋升链: %s" % pending)
        elif need <= 0:
            print("[4/7] discover：跳过——无缺口（total_need=0）")
        elif not has_key and not args.dry_run:
            print("[4/7] discover：跳过——缺 LLM_API_KEY")
        else:
            print("[4/7] discover：缺口 %d 张，发现新源（簇上限 %d）"
                  % (need, args.max_clusters))
            discover.main(["--meta", args.meta, "--gap-report",
                           os.path.join(adir, "gap_report.json"),
                           "--max-clusters", str(args.max_clusters)] + dr)

        print("-" * 72)
        print("[5/7] probe：提案探测")
        probe.main(["--meta", args.meta])

        print("-" * 72)
        if not has_key and not args.dry_run:
            print("[6/7] synth：跳过——缺 LLM_API_KEY")
        else:
            print("[6/7] synth：探测样本 → 完整 spec")
            synth.main(["--meta", args.meta] + dr)

    print("-" * 72)
    print("[7/7] verify：三级闸门验收")
    verify.main(["--meta", args.meta, "--max-promote", str(args.max_promote)])

    # 轮末摘要：注册表生命周期分布 + 未决提案
    reg = load_registry(args.meta)
    dist = Counter(reg.card(n).lifecycle for n in reg.names())
    print("=" * 72)
    print("轮末摘要：源 %d 个（%s）；未决提案 %s；缺口 %d 张" % (
        len(reg.names()),
        ", ".join("%s=%d" % kv for kv in sorted(dist.items())),
        _pending_proposals(adir) or "无", _gap_total(adir)))
    print("提示：probation/active 源在下一次常规采集 run 自动参与；"
          "周期调用本子命令即无人值守闭环。")


if __name__ == "__main__":
    main()
