# -*- coding: utf-8 -*-
"""运行治理（六步闭环第 6 步，纯确定性代码，无 LLM）——生命周期由数字证据驱动。

读 source_health.json（跨 run 累积）+ 注册表，执行数字规则迁移：
- 晋升 probation → active：runs>=3、检索>=5 次、失败率<10%、无弱下载信号；
- 降级 active/probation → degraded：检索>=20 次且失败率>=50%，或命中下载
  弱源判据（pipeline._weak_sources_from_health：尝试>=30、0 成功、确定性失败主导）；
- 退休 degraded 生成源 → retired：修复机会用尽或提案丢失无法修复
  ——retired 集即账本自动产生的 known_dead（usable_names 自动剔除，无需配置维护）；
- 手写源 degraded 不自动退休（入 git 的代码产物由人审核），修复后用
  --reactivate 人工给证据恢复 active。

护栏：每轮晋升 <=2（与 verify --max-promote 对称）；证据不足（runs<3）不迁移。

用法：python3 collect/cli.py govern [--dry-run]
      python3 collect/cli.py govern --reactivate NAME --reason "修复说明"
"""

from __future__ import annotations

import argparse

from . import auto_dir
from ..pipeline import _load_health, _weak_sources_from_health
from ..registry import load_registry

MIN_RUNS = 3                  # 证据量下限：单 run 抖动不触发迁移
PROMOTE_MIN_SEARCHES = 5
PROMOTE_MAX_FAIL_RATE = 0.10
DEGRADE_MIN_SEARCHES = 20
DEGRADE_FAIL_RATE = 0.50
MAX_PROMOTE_PER_ROUND = 2


def _stats(health: dict, name: str) -> "tuple[int, float]":
    e = health.get(name) or {}
    searches = e.get("search_ok", 0) + e.get("search_fail", 0)
    rate = e.get("search_fail", 0) / searches if searches else 0.0
    return searches, rate


def govern(meta_dir: str, dry_run: bool = False,
           max_promote: int = MAX_PROMOTE_PER_ROUND) -> list:
    """执行一轮治理；返回迁移列表 [(name, from, to, reason)]。"""
    from .repair import MAX_REPAIRS, find_proposal   # 复用修复侧的提案定位

    health = _load_health(meta_dir)
    weak_dl = _weak_sources_from_health(health)
    reg = load_registry(meta_dir)
    adir = auto_dir(meta_dir)
    moves: list = []
    promoted = 0

    for name in reg.names():
        card = reg.card(name)
        lc = card.lifecycle
        if lc in ("retired", "candidate"):
            continue                      # candidate 归 verify 闸门管；retired 终态
        e = health.get(name) or {}
        if lc == "degraded":
            if not card.provenance.startswith("llm"):
                print("[degraded] %s 为手写源：修复代码/spec 后 "
                      "govern --reactivate %s 恢复" % (name, name))
                continue
            prop, _ = find_proposal(adir, name)
            attempts = (prop or {}).get("repair_attempts", 0)
            if prop is None:
                reason = "degraded 且提案丢失（无法修复），账本证据退休"
            elif attempts >= MAX_REPAIRS:
                reason = "degraded 且修复 %d 次未果（上限 %d），账本证据退休" % (
                    attempts, MAX_REPAIRS)
            else:
                continue                  # 修复机会未尽，留给 repair
            if len([m for m in moves if m[2] == "retired"]) >= 5:
                continue                  # 单轮退休护栏（防账本异常批量误杀）
            moves.append((name, lc, "retired", reason))
            continue
        # active / probation：证据不足不动
        if e.get("runs", 0) < MIN_RUNS:
            continue
        searches, fail_rate = _stats(health, name)
        if lc == "probation" and promoted < max_promote \
                and searches >= PROMOTE_MIN_SEARCHES \
                and fail_rate < PROMOTE_MAX_FAIL_RATE and name not in weak_dl:
            moves.append((name, lc, "active",
                          "probation 观察达标：检索 %d 次失败率 %.0f%%，runs=%d" % (
                              searches, fail_rate * 100, e["runs"])))
            promoted += 1
        elif (searches >= DEGRADE_MIN_SEARCHES and fail_rate >= DEGRADE_FAIL_RATE) \
                or name in weak_dl:
            why = ("下载弱源判据（尝试>=30 且 0 成功）" if name in weak_dl
                   else "检索 %d 次失败率 %.0f%%" % (searches, fail_rate * 100))
            moves.append((name, lc, "degraded", why))

    for name, frm, to, reason in moves:
        if dry_run:
            print("[dry-run] %s: %s -> %s（%s）" % (name, frm, to, reason))
        else:
            reg.set_lifecycle(name, to, reason="govern: " + reason)
            print("[govern] %s: %s -> %s（%s）" % (name, frm, to, reason))
    if not moves:
        print("治理完成：无生命周期迁移（证据不足或全部健康）。")
    else:
        retired = [m[0] for m in moves if m[2] == "retired"]
        print("治理完成：迁移 %d 项%s" % (
            len(moves), "；退休（自动 known_dead）: %s" % retired if retired else ""))
    return moves


def main(argv=None):
    ap = argparse.ArgumentParser(prog="collect govern",
                                 description="运行治理：健康账本数字证据驱动生命周期迁移")
    ap.add_argument("--meta", default="data/dataset/meta")
    ap.add_argument("--dry-run", action="store_true",
                    help="只打印迁移裁决，不写覆盖层")
    ap.add_argument("--max-promote", type=int, default=MAX_PROMOTE_PER_ROUND,
                    help="本轮 probation→active 晋升上限（护栏，默认 2）")
    ap.add_argument("--reactivate", default=None, metavar="NAME",
                    help="人工修复后恢复指定源至 active（需 --reason）")
    ap.add_argument("--reason", default="", help="reactivate 的修复说明（进覆盖层证据）")
    args = ap.parse_args(argv)

    if args.reactivate:
        if not args.reason:
            raise SystemExit("--reactivate 必须附 --reason（修复证据进覆盖层）")
        reg = load_registry(args.meta)
        card = reg.card(args.reactivate)
        if card.lifecycle not in ("degraded", "retired"):
            raise SystemExit("%s 当前为 %s，无需 reactivate" % (
                args.reactivate, card.lifecycle))
        old = card.lifecycle
        reg.set_lifecycle(args.reactivate, "active",
                          reason="人工修复重启: " + args.reason)
        print("[reactivate] %s: %s -> active（%s）" % (
            args.reactivate, old, args.reason))
        return
    govern(args.meta, dry_run=args.dry_run, max_promote=args.max_promote)


if __name__ == "__main__":
    main()
