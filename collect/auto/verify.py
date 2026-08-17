# -*- coding: utf-8 -*-
"""验收闸门（六步闭环第 5 步，纯确定性代码，无 LLM）——三级闸门 + 三态裁决。

对 synth 产出的生成 spec（state/collect/specs/，llm_spec/candidate）执行：
1. build  —— 结构校验：load_spec_file + 字段键/transform 闭集 + scope 合法；
2. smoke  —— 实跑检索：真实网络请求探针查询词，断言候选数/content_url/
             host 在 scope 内/字段齐全（预算硬上限 4 请求）；
3. quality—— 候选质量：>=3 条候选，且 >=50% 声明宽高齐全、content_url 像图片。

三态裁决（状态迁移由本节数字证据驱动，无 LLM 参与）：
- achieved → 晋升 probation（写覆盖层事件；probation 源参与采集、留证据观察）
- revise   → 失败证据回写提案（供 synth --revise 重合成；超限自动 blocked）
- blocked  → 生命周期置 retired（覆盖层事件），spec 文件保留作尸检

护栏：每轮晋升 <=2（--max-promote）；生成源 probation 期一律未授权。

用法：python3 collect/cli.py verify [--name NAME] [--max-promote N]
"""

from __future__ import annotations

import argparse
import datetime
import json
import os

from . import auto_dir
from ..config import Job
from ..http_scope import RuntimeLimits, UsageMeter
from ..registry import load_registry
from ..spec_adapter import GenericSpecAdapter, _FIELD_KEYS, load_spec_file

SMOKE_LIMITS = RuntimeLimits(timeout_sec=20, max_retries=1,
                             min_interval_sec=1.0, max_requests_per_run=4,
                             deadline_sec=90)
_TRANSFORMS_OK = ("first", "int", "str", "mime_by_ext")


# ---------------------------------------------------------------------------
# 三级闸门（各自返回错误列表；空 = 通过）
# ---------------------------------------------------------------------------
def gate_build(spec: dict) -> list:
    errs = []
    try:
        load_spec_file_content(spec)
    except ValueError as e:
        errs.append("build: %s" % e)
    rec = spec.get("record") or {}
    for k, fs in (rec.get("fields") or {}).items():
        if k not in _FIELD_KEYS:
            errs.append("build: 未知字段键 %s" % k)
        if isinstance(fs, dict):
            t = fs.get("transform")
            if t and t not in _TRANSFORMS_OK:
                errs.append("build: 未知 transform %r（字段 %s）" % (t, k))
    scope = spec.get("network_scope") or {}
    if not scope.get("api_hosts"):
        errs.append("build: network_scope.api_hosts 为空")
    return errs


def load_spec_file_content(spec: dict) -> None:
    """结构校验（不读文件版）：name/api_hosts/content_url 必备。"""
    if not spec.get("name"):
        raise ValueError("spec 缺少 name")
    if not (spec.get("network_scope") or {}).get("api_hosts"):
        raise ValueError("spec 必须声明 network_scope.api_hosts")
    if not (spec.get("record") or {}).get("fields", {}).get("content_url"):
        raise ValueError("spec 必须声明 record.fields.content_url")


def gate_smoke(adapter: GenericSpecAdapter, queries: list) -> "tuple[list, list]":
    """实跑检索（探针词按序尝试，有候选即停）；返回 (错误列表, 候选列表)。"""
    errs, cands = [], []
    for q in (queries or ["test"])[:2]:
        job = Job(instance="__verify__", query=q)
        try:
            raws = adapter.search(job)
        except Exception as e:  # noqa: BLE001
            return ["smoke: 检索异常 %s: %s" % (type(e).__name__, e)], []
        if not raws:
            errs = ["smoke: 检索返回 0 候选（探针词 %r）" % q]
            continue
        errs = []
        for raw in raws:
            try:
                c = adapter.to_candidate(raw, job)
            except Exception as e:  # noqa: BLE001
                errs.append("smoke: to_candidate 异常: %s" % e)
                continue
            if not c.content_url or not c.content_url.startswith("https://"):
                errs.append("smoke: content_url 缺失或非 https")
                continue
            if not (adapter.scope.allows_media(c.content_url)
                    or adapter.scope.allows_api(c.content_url)):
                errs.append("smoke: content_url host 不在 network_scope 内: %s"
                            % c.content_url)
                continue
            cands.append(c)
        if cands:
            return [], cands
    if not cands and not errs:
        errs.append("smoke: 无有效候选")
    return errs, cands


def gate_quality(cands: list) -> list:
    errs = []
    if len(cands) < 3:
        errs.append("quality: 有效候选 %d < 3" % len(cands))
        return errs
    n_dim = sum(1 for c in cands if c.declared_width and c.declared_height)
    if n_dim < len(cands) * 0.5:
        errs.append("quality: 声明宽高齐全率 %d/%d < 50%%" % (n_dim, len(cands)))
    img_exts = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tif", ".tiff")
    n_img = sum(1 for c in cands
                if any(c.content_url.lower().split("?")[0].endswith(e)
                       for e in img_exts) or (c.declared_mime or "").startswith("image/"))
    if n_img < len(cands) * 0.5:
        errs.append("quality: 图片特征率 %d/%d < 50%%" % (n_img, len(cands)))
    return errs


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def verify_spec(name: str, meta_dir: str, queries: list,
                reg) -> "tuple[str, list]":
    """三级闸门串行；返回 (achieved|revise|blocked, 错误列表)。"""
    spec_path = os.path.join(
        os.path.dirname(auto_dir(meta_dir)), "specs", "%s.v1.json" % name)
    if not os.path.exists(spec_path):
        return "blocked", ["spec 文件不存在: %s" % spec_path]
    try:
        with open(spec_path, encoding="utf-8") as f:
            spec = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        return "blocked", ["spec 无法解析: %s" % e]

    errs = gate_build(spec)
    if errs:
        return "revise", errs

    meter = UsageMeter()
    adapter = GenericSpecAdapter(spec, meter=meter)
    adapter.limits = SMOKE_LIMITS   # 闸门预算覆盖 spec 默认
    from ..http_scope import ScopedHttp
    adapter.http = ScopedHttp(adapter.scope, SMOKE_LIMITS, meter)
    errs, cands = gate_smoke(adapter, queries)
    if errs:
        return "revise", errs
    errs = gate_quality(cands)
    if errs:
        return "revise", errs
    return "achieved", ["smoke 候选 %d 条，请求 %d 次" % (
        len(cands), meter.requests)]


def _settle(prop_path: str, prop: dict, verdict: str, errs: list,
            reg, max_promote: int, promoted: list) -> None:
    """三态裁决落地：晋升 probation / 回写 revise 证据 / 置 retired。"""
    name = prop["proposal"]["name"]
    now = datetime.datetime.now().isoformat(timespec="seconds")
    prop["last_verify"] = {"ts": now, "verdict": verdict, "errors": errs}
    if verdict == "achieved":
        if len(promoted) >= max_promote:
            prop["last_verify"]["verdict"] = "revise"
            prop["last_verify"]["errors"] = errs + [
                "本轮晋升名额已满（%d），下轮重试" % max_promote]
        else:
            reg.set_lifecycle(name, "probation",
                              reason="verify achieved: " + "; ".join(errs)[:300])
            prop["status_lifecycle"] = "achieved"
            promoted.append(name)
            print("[achieved] %s 晋升 probation（可参与采集，留证据观察）" % name)
    elif verdict == "revise":
        prop["status_lifecycle"] = "revise"
        # 递增计数：synth 侧超限后转 blocked，防 synth↔verify 无限循环
        prop["synth_revises"] = int(prop.get("synth_revises", 0)) + 1
        print("[revise] %s: %s（重跑 synth 带证据重合成，已用 %d 次机会）"
              % (name, "; ".join(errs)[:200], prop["synth_revises"]))
    else:
        # 仅当源已进注册表才写 retired 事件（build 即失败且 spec 无法被
        # registry 加载时，源不存在于卡集，提案文件本身已记 blocked）
        if name in reg.names():
            reg.set_lifecycle(name, "retired",
                              reason="verify blocked: " + "; ".join(errs)[:300])
        prop["status_lifecycle"] = "blocked"
        print("[blocked] %s: %s" % (name, "; ".join(errs)[:200]))
    with open(prop_path, "w", encoding="utf-8") as f:
        json.dump(prop, f, ensure_ascii=False, indent=1)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="collect verify",
                                 description="三级闸门验收（build/smoke/quality）")
    ap.add_argument("--meta", default="data/dataset/meta")
    ap.add_argument("--name", default=None, help="只验收指定提案")
    ap.add_argument("--max-promote", type=int, default=2,
                    help="本轮晋升 probation 上限（护栏，默认 2）")
    args = ap.parse_args(argv)

    adir = auto_dir(args.meta)
    prop_dir = os.path.join(adir, "proposals")
    if not os.path.isdir(prop_dir):
        raise SystemExit("无提案目录；先运行 discover → probe → synth")
    reg = load_registry(args.meta)
    promoted: list = []
    n = 0
    for fn in sorted(os.listdir(prop_dir)):
        if not fn.endswith(".json"):
            continue
        path = os.path.join(prop_dir, fn)
        with open(path, encoding="utf-8") as f:
            prop = json.load(f)
        p = prop.get("proposal") or {}
        name = p.get("name")
        if args.name and name != args.name:
            continue
        if prop.get("status") != "synth_done":
            continue
        if prop.get("status_lifecycle") in ("achieved", "blocked"):
            print("[skip] %s 已裁决（%s）" % (name, prop["status_lifecycle"]))
            continue
        n += 1
        print("[verify] %s ..." % name, flush=True)
        verdict, errs = verify_spec(name, args.meta,
                                    list(p.get("sample_queries") or []), reg)
        _settle(path, prop, verdict, errs, reg, args.max_promote, promoted)

    if n == 0:
        print("没有待验收的提案（status=synth_done）。")
    else:
        print("完成：验收 %d 个，本轮晋升 %d（probation）。" % (n, len(promoted)))


if __name__ == "__main__":
    main()
