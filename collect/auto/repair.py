# -*- coding: utf-8 -*-
"""修复回路（degraded 触发，低频 LLM 环节）——复用 probe→synth→verify 链。

设计立场：站点改版导致的存量源退化，修复价值大于发现新源。
- 生成源（llm_spec）：回绕闭环重走——重探（拿站点新响应样本）→ 带 degrade
  证据重合成 spec → 重验收；achieved 回 probation 重新观察，失败保持 degraded
  （机会递减，用尽后由 govern 依账本证据退休）。
- 手写源（入 git 的代码/手写 spec）：只打印降级证据与修复指引——LLM 不改
  入 git 的代码产物；人工修复后 govern --reactivate 给证据恢复。

预算：每轮 --max-repair（默认 2）；单源重探 <=3 请求 + 验收 <=4 请求。

用法：python3 collect/cli.py repair [--name NAME] [--dry-run]
"""

from __future__ import annotations

import argparse
import datetime
import json
import os

from . import auto_dir
from ..registry import load_registry

MAX_REPAIRS = 2               # 单源修复轮次上限；用尽后 govern 退休（自动 known_dead）
MAX_REPAIR_PER_ROUND = 2


def find_proposal(adir: str, name: str) -> "tuple[dict | None, str]":
    """定位提案文件；返回 (提案 dict 或 None, 路径)。"""
    path = os.path.join(adir, "proposals", "%s.json" % name)
    if not os.path.exists(path):
        return None, path
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f), path
    except (OSError, json.JSONDecodeError):
        return None, path


def repair_one(name: str, meta_dir: str, adir: str, reg) -> bool:
    """修复单个 degraded 生成源；返回是否完成一轮修复动作（不论成败）。"""
    from . import probe as _probe, synth as _synth, verify as _verify

    prop, path = find_proposal(adir, name)
    if prop is None:
        print("[repair] %s: 提案缺失（%s），无法修复；govern 将依证据退休"
              % (name, path))
        return False
    attempts = int(prop.get("repair_attempts", 0))
    if attempts >= MAX_REPAIRS:
        print("[repair] %s: 修复机会已用尽（%d/%d），等待 govern 退休裁决"
              % (name, attempts, MAX_REPAIRS))
        return False
    prop["repair_attempts"] = attempts + 1
    prop["synth_revises"] = 0            # 新一轮修复给新的 revise 预算
    prop["status_lifecycle"] = "repairing"

    # 1) 重探：拿站点新响应样本（degrade 可能就是响应结构变了）
    ev = _probe.probe_proposal(prop)
    report = os.path.join(adir, "probe_report.jsonl")
    with open(report, "a", encoding="utf-8") as f:
        f.write(json.dumps(ev, ensure_ascii=False) + "\n")
    prop["last_probe"] = ev
    if ev["verdict"] != "ok":
        prop["status"] = "probed_fail"
        _save(path, prop)
        print("[repair] %s: 重探 %s（%s），保持 degraded，下轮再试"
              % (name, ev["verdict"], ev.get("detail") or "-"))
        return True

    # 2) 重合成：degrade 证据（last_verify/健康账本原因）作 revise 提示
    from taxonomy import llm_common
    if not llm_common.API_KEY:
        prop["status"] = "probed_ok"
        _save(path, prop)
        print("[repair] %s: 重探 ok 但缺 LLM_API_KEY，重合成跳过（保持 degraded）"
              % name)
        return True
    client = llm_common.make_client()
    spec, errs = _synth.synth_proposal(prop, client, False,
                                       llm_common.want_responses())
    if spec is None:
        prop["last_synth_errors"] = errs
        _save(path, prop)
        print("[repair] %s: 重合成失败: %s（保持 degraded）"
              % (name, "; ".join(errs)[:200]))
        return True
    specs_dir = os.path.dirname(adir) + "/specs"
    os.makedirs(specs_dir, exist_ok=True)
    out = os.path.join(specs_dir, "%s.v1.json" % name)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(spec, f, ensure_ascii=False, indent=1)
    prop["status"] = "synth_done"

    # 3) 重验收：achieved 回 probation 重新观察
    queries = list((prop.get("proposal") or {}).get("sample_queries") or [])
    verdict, verrs = _verify.verify_spec(name, meta_dir, queries, reg)
    now = datetime.datetime.now().isoformat(timespec="seconds")
    prop["last_verify"] = {"ts": now, "verdict": verdict, "errors": verrs}
    if verdict == "achieved":
        reg.set_lifecycle(name, "probation",
                          reason="repair achieved（第 %d 轮修复）: %s" % (
                              prop["repair_attempts"], "; ".join(verrs)[:200]))
        prop["status_lifecycle"] = "achieved"
        print("[repair] %s: 修复成功，回 probation 重新观察" % name)
    else:
        prop["status_lifecycle"] = "revise" if verdict == "revise" else "blocked"
        print("[repair] %s: 重验收 %s: %s（保持 degraded）"
              % (name, verdict, "; ".join(verrs)[:200]))
    _save(path, prop)
    return True


def _save(path: str, prop: dict) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(prop, f, ensure_ascii=False, indent=1)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="collect repair",
                                 description="degraded 源修复回路（重探→重合成→重验收）")
    ap.add_argument("--meta", default="data/dataset/meta")
    ap.add_argument("--name", default=None, help="只修复指定源")
    ap.add_argument("--max-repair", type=int, default=MAX_REPAIR_PER_ROUND,
                    help="本轮修复源数量上限（护栏，默认 2）")
    ap.add_argument("--dry-run", action="store_true",
                    help="只列出待修复源与修复机会，不执行")
    args = ap.parse_args(argv)

    reg = load_registry(args.meta)
    adir = auto_dir(args.meta)
    degraded = [n for n in reg.names()
                if reg.card(n).lifecycle == "degraded"
                and (not args.name or n == args.name)]
    if not degraded:
        print("没有 degraded 源，无需修复。")
        return
    for n in degraded:
        c = reg.card(n)
        prop, _ = find_proposal(adir, n)
        att = (prop or {}).get("repair_attempts", 0)
        kind = "生成源" if c.provenance.startswith("llm") else "手写源"
        print("待修复: %s（%s, provenance=%s, 已用修复机会 %d/%d%s）" % (
            n, kind, c.provenance, att, MAX_REPAIRS,
            "" if prop is not None or c.provenance == "manual"
            else "，提案缺失"))
    if args.dry_run:
        return
    n = 0
    for name in degraded:
        if reg.card(name).provenance == "manual":
            print("[repair] %s: 手写源 degraded——人工检查代码/spec 是否站点改版，"
                  "修复后运行: python3 collect/cli.py govern --reactivate %s "
                  "--reason '...'" % (name, name))
            continue
        if n >= args.max_repair:
            print("[repair] 本轮修复名额已满（%d），其余下轮" % args.max_repair)
            break
        if repair_one(name, args.meta, adir, reg):
            n += 1
    print("完成：本轮修复 %d 个源。" % n)


if __name__ == "__main__":
    main()
