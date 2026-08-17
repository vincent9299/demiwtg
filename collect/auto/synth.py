# -*- coding: utf-8 -*-
"""适配合成（六步闭环第 4 步，低频 LLM 环节）——探测前置的受益者。

输入 probed_ok 提案（含真实响应样本 probe_sample / sample_keys），产出完整
Tier A spec。确定性优先原则：
- search 段（url/params/query_transform/page_size）由提案确定性复制，LLM 不参与；
- network_scope.api_hosts 取提案 api_host；media_hosts 由样本中的内容 URL
  host 确定性提取（不信任 LLM 的 host 声明）；
- LLM 只负责 record 段的抽取映射（items 位置 / filters / fields 路径），
  且输出经【样本交叉校验】：路径引用的键必须在真实样本中存在，否则拒绝。

裁决失败时可带失败证据重合成（verify revise 后直接重跑 synth，上限 K 次），
对应三态闸门的 revise。
产物：state/collect/specs/<name>.v1.json（registry 载入为 llm_spec，
生命周期默认 candidate——能否晋升由 verify 闸门裁决）。

用法：python3 collect/cli.py synth [--name NAME] [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import urllib.parse

from . import auto_dir
from ..spec_adapter import _FIELD_KEYS, extract_field, load_spec_file

MAX_REVISES = 2
_QUERY_TRANSFORMS = ("none", "lower_underscore")

# record 段允许的 transform 闭集（与 spec_adapter 词表一致）
_TRANSFORMS = ("first", "int", "str", "mime_by_ext")

_SYSTEM = """你是 JSON API 抽取规则合成器。给定一次真实检索响应的样本条目与
全部可用键，输出抽取规则 JSON（只输出 JSON）：
{"items": "<条目容器路径，如 $ 或 $.results[*]>",
 "filters": [ {"path": "...", "in": [...] 或 "exists": true 或 "startswith": "..."} ],
 "fields": { ... } }
fields 的键只能取：%s。
取值形态三种：路径字符串（$.a.b，可用 " | " 回退链）；{"template": "...{键名}..."}；
{"const": "常量"}。可加变换：{"path": "...", "transform": "%s"}。
规则：content_url 必填且必须指向真实图片 URL 键；路径引用的键必须来自样本，
禁止臆造；license 缺失用 {"const": "未知(未授权来源,非CC)"}；filters 宁少勿错。""" % (
    "/".join(_FIELD_KEYS), "/".join(_TRANSFORMS))


def _media_hosts_from_sample(sample: dict) -> list:
    """从样本条目中提取内容 URL 的 host（确定性；图片扩展名启发式）。"""
    hosts = set()
    exts = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tif", ".tiff")

    def walk(v):
        if isinstance(v, str):
            if v.startswith("https://") and any(
                    v.lower().split("?")[0].endswith(e) for e in exts):
                hosts.add(urllib.parse.urlparse(v).netloc.lower())
        elif isinstance(v, dict):
            for x in v.values():
                walk(x)
        elif isinstance(v, list):
            for x in v[:5]:
                walk(x)

    walk(sample)
    return sorted(hosts)


def _sample_of(prop: dict) -> "tuple[dict, list]":
    """取探测样本条目与键表（probe 已落盘）。"""
    ev = prop.get("last_probe") or {}
    raw = ev.get("probe_sample")
    if not raw:
        return None, []
    try:
        return json.loads(raw), list(ev.get("sample_keys") or [])
    except (json.JSONDecodeError, TypeError):
        return None, list(ev.get("sample_keys") or [])


def _cross_check(sample: dict, record: dict) -> list:
    """用真实样本演练抽取：返回错误列表（空=通过）。"""
    errs = []
    fields = record.get("fields") or {}
    if not fields.get("content_url"):
        errs.append("fields.content_url 缺失")
    for k in fields:
        if k not in _FIELD_KEYS:
            errs.append("未知字段键 %s" % k)
    try:
        out = {k: extract_field(sample, fs) for k, fs in fields.items()}
        if not out.get("content_url"):
            errs.append("样本上抽不出 content_url（路径与真实结构不符）")
        elif not str(out["content_url"]).startswith("https://"):
            errs.append("抽出的 content_url 非 https")
    except Exception as e:  # noqa: BLE001
        errs.append("抽取演练异常: %s" % e)
    return errs


def build_spec(prop: dict, record: dict, query_transform: str) -> dict:
    """提案 + 抽取规则 → 完整 Tier A spec（确定性骨架 + LLM record 段）。"""
    p = prop["proposal"]
    host = (p.get("api_host") or "").lower()
    sample, _ = _sample_of(prop)
    media = _media_hosts_from_sample(sample) if isinstance(sample, dict) else []
    return {
        "spec_version": 1,
        "name": p["name"],
        "kind": "directed",
        "source_kind": p.get("source_kind", ""),
        "authorized": False,           # 生成源一律未授权（授权裁决另有通道）
        "lang": p.get("lang", "en"),
        "capabilities": list(p.get("capabilities") or []),
        "network_scope": {"api_hosts": [host],
                          "media_hosts": media or [host]},
        "limits": {"min_interval_sec": 1.0},
        "search": {
            "url": p["search_url_draft"],
            "params": dict(p.get("params_draft") or {"q": "{query}"}),
            "query_transform": query_transform
            if query_transform in _QUERY_TRANSFORMS else "none",
            "page_size": {"param": "", "factor": 6, "floor": 10, "cap": 20},
        },
        "record": {
            "items": record.get("items", "$"),
            "max_records": 20,
            "filters": list(record.get("filters") or []),
            "fields": dict(record.get("fields") or {}),
        },
    }


def synth_proposal(prop: dict, client, dry_run: bool,
                   use_responses: bool) -> "tuple[dict, list]":
    """单提案合成：返回 (spec 或 None, 错误列表)。"""
    from taxonomy.llm_common import generate

    sample, keys = _sample_of(prop)
    if sample is None:
        return None, ["无探测样本（probe_sample 缺失/损坏），先重探"]
    revise_hint = ""
    last = prop.get("last_verify") or {}
    if last.get("errors"):
        revise_hint = "\n上一版失败证据（必须规避）：%s" % "; ".join(
            last["errors"][:5])
    user = ("真实响应样本条目（截断）：\n%s\n\n全部可用键：%s%s" % (
        json.dumps(sample, ensure_ascii=False)[:1200], keys, revise_hint))
    if dry_run:
        print("=" * 72)
        print("[dry-run] name=%s\n-- system --\n%s\n-- user --\n%s" % (
            prop["proposal"]["name"], _SYSTEM, user))
        return None, []
    out = generate(client, _SYSTEM, user, use_responses=use_responses) or {}
    record = {k: out[k] for k in ("items", "filters", "fields") if k in out}
    errs = _cross_check(sample, record)
    if errs:
        return None, errs
    qt = "lower_underscore" if re.search(r"\btags?\b", json.dumps(
        prop["proposal"].get("params_draft") or {})) else "none"
    return build_spec(prop, record, qt), []


def main(argv=None):
    ap = argparse.ArgumentParser(prog="collect synth",
                                 description="探测样本 → 完整 Tier A spec")
    ap.add_argument("--meta", default="data/dataset/meta")
    ap.add_argument("--name", default=None, help="只合成指定提案")
    ap.add_argument("--dry-run", action="store_true", help="不调 API，仅打印 prompt")
    args = ap.parse_args(argv)

    adir = auto_dir(args.meta)
    prop_dir = os.path.join(adir, "proposals")
    if not os.path.isdir(prop_dir):
        raise SystemExit("无提案目录；先运行 discover → probe")

    from taxonomy import llm_common
    if not args.dry_run:
        llm_common.require_api_key()
        client = llm_common.make_client()
    else:
        client = None

    specs_dir = os.path.join(os.path.dirname(adir), "specs")  # state/collect/specs
    os.makedirs(specs_dir, exist_ok=True)
    n_ok = n_fail = 0
    for fn in sorted(os.listdir(prop_dir)):
        if not fn.endswith(".json"):
            continue
        path = os.path.join(prop_dir, fn)
        with open(path, encoding="utf-8") as f:
            prop = json.load(f)
        p = prop.get("proposal") or {}
        if args.name and p.get("name") != args.name:
            continue
        if prop.get("status") not in ("probed_ok", "synth_done"):
            continue
        if prop.get("status_lifecycle") in ("achieved", "blocked"):
            print("[skip] %s 已由 verify 裁决（%s）" % (
                fn[:-5], prop["status_lifecycle"]))
            continue
        revises = prop.get("synth_revises", 0)
        if revises > MAX_REVISES:
            print("[blocked] %s revise 超上限 %d 次" % (fn[:-5], MAX_REVISES))
            prop["status_lifecycle"] = "blocked"
            with open(path, "w", encoding="utf-8") as f:
                json.dump(prop, f, ensure_ascii=False, indent=1)
            continue
        spec, errs = synth_proposal(prop, client, args.dry_run,
                                    llm_common.want_responses())
        if args.dry_run:
            continue
        if spec is None:
            n_fail += 1
            prop["last_synth_errors"] = errs
            prop["synth_revises"] = revises + 1
            print("[fail] %s: %s" % (fn[:-5], "; ".join(errs)))
        else:
            n_ok += 1
            out = os.path.join(specs_dir, "%s.v1.json" % spec["name"])
            with open(out, "w", encoding="utf-8") as f:
                json.dump(spec, f, ensure_ascii=False, indent=1)
            prop["status"] = "synth_done"
            load_spec_file(out)   # 结构自检（失败会抛错，不落晋升链）
            print("[spec] %s -> %s（生命周期 candidate，待 verify 闸门）"
                  % (spec["name"], out))
        with open(path, "w", encoding="utf-8") as f:
            json.dump(prop, f, ensure_ascii=False, indent=1)

    if not args.dry_run:
        print("完成：合成 %d，失败 %d。下一步: python3 collect/cli.py verify"
              % (n_ok, n_fail))


if __name__ == "__main__":
    main()
