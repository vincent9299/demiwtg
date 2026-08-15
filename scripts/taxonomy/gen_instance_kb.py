#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""gen_instance_kb.py — LLM 一次调用为一个实例生成完整知识字段。

对 taxonomy-instances/data/instances.json 中缺富描述的实例，一次 LLM 调用产出：
    definition / intro / desc / query / aliases

写入实例对应字段，并把 source 置为 "llm"（curated=人工精写默认跳过；templated 为
历史模板值，重生成时覆盖为 llm）。

机制（共用 llm_common.py）：
  - OpenAI 兼容端点（LLM_BASE_URL），LLM_WEB_SEARCH=1 可联网核实（仅官方端点）
  - 断点续跑：缓存 taxonomy-instances/data/.llm_kb_cache.jsonl，--overwrite 重生成
  - 默认跳过 source=curated 与已富化实例；--only-empty 只补缺口、--refresh 全量重生成

用法：
  python3 scripts/taxonomy/gen_instance_kb.py --dry-run --limit 3 --branch "内容作品 IP"
  export LLM_API_KEY=sk-... LLM_BASE_URL=https://api.openai.com/v1 LLM_MODEL=gpt-4o-mini
  python3 scripts/taxonomy/gen_instance_kb.py --branch "内容作品 IP" --limit 50 --write
  python3 scripts/taxonomy/gen_instance_kb.py --only-empty --write      # 只补缺口
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from scripts.taxonomy import llm_common as llm

ROOT = Path(__file__).resolve().parent.parent.parent    # 仓库根
META_PATH = ROOT / "taxonomy-instances" / "data" / "instances.json"
CACHE_PATH = ROOT / "taxonomy-instances" / "data" / ".llm_kb_cache.jsonl"

SYSTEM_PROMPT = (
    "你是中文 IP 标签体系的知识库撰写助手。对每个给定的 IP 实例（作品 / 品牌 / 地标 / "
    "角色 / 美食 / 赛事 / 吉祥物等），产出客观、准确、不编造的内容。\n"
    "若你对该实体不了解或信息可能过时，应使用可用的联网检索工具核实后再作答。\n"
    "只输出一个 JSON 对象，不要任何额外文字，格式：\n"
    '{"definition": "定义(一句话，它是什么)", '
    '"intro": "一句话简介", '
    '"desc": "详细介绍(2-4句，说明它是什么、来源、影响力)", '
    '"query": "检索扩展词，逗号或顿号分隔，最多6个，优先英文与常用简称", '
    '"aliases": ["别名/英文名/简称", ...]（最多8个）}'
)


def build_user_prompt(name, taxonomy_path, it):
    ctx = []
    for label, key in (("已有定义", "definition"), ("已有简介", "intro"),
                       ("已有别名", "aliases")):
        v = it.get(key)
        if isinstance(v, list):
            v = "、".join(str(x) for x in v)
        if v:
            ctx.append(f"{label}：{v}")
    ctx_block = ("\n".join(ctx) + "\n") if ctx else ""
    return (
        f"实例名称：{name}\n"
        f"所属分类(instance of)：{taxonomy_path}\n"
        f"{ctx_block}"
        "请生成该实例的 definition / intro / desc / query / aliases。"
    )


def load_targets(args):
    doc = json.load(open(META_PATH, encoding="utf-8"))
    insts = doc.get("instances", [])
    out = []
    for it in insts:
        name = it.get("name", "")
        tpath = it.get("taxonomy_path", "")
        if not name:
            continue
        if args.branch and args.branch not in tpath:
            continue
        if (not args.refresh) and it.get("source") == "curated":
            continue
        if args.only_empty and (it.get("desc") or it.get("query") or it.get("aliases")):
            continue
        out.append((name, tpath))
    return out


def merge_record(it, rec):
    changed = False
    for fld in ("definition", "intro", "desc"):
        v = (rec.get(fld) or "").strip()
        if v and v != it.get(fld):
            it[fld] = v
            changed = True
    q = rec.get("query") or ""
    if isinstance(q, str):
        q = [x.strip() for x in q.replace("、", ",").split(",") if x.strip()]
    if isinstance(q, list):
        q = [str(x).strip() for x in q if str(x).strip()][:6]
    if q:
        it["query"] = q
        changed = True
    al = rec.get("aliases") or []
    if isinstance(al, str):
        al = [al]
    existing = list(it.get("aliases") or [])
    for x in al:
        x = str(x).strip()
        if x and x not in existing:
            existing.append(x)
    existing = existing[:10]
    if existing:
        it["aliases"] = existing
        changed = True
    if changed and it.get("source") in ("derived", "templated"):
        it["source"] = "llm"
    return changed


def main():
    ap = argparse.ArgumentParser(description="LLM 一次调用为一个实例生成知识字段")
    ap.add_argument("--only-empty", action="store_true",
                    help="只处理尚无 desc/query/aliases 的实例")
    ap.add_argument("--refresh", action="store_true",
                    help="连 curated（人工精写）实例也重生成")
    llm.add_common_args(ap)
    args = ap.parse_args()

    targets = load_targets(args)
    cache = llm.JsonlCache(CACHE_PATH)
    done = set() if args.overwrite else cache.done_keys()
    targets = [(n, c) for (n, c) in targets if (n + "\u0001" + c) not in done]
    if args.limit:
        targets = targets[:args.limit]

    print(f"目标实例：{len(targets)} 条"
          + (f"（branch={args.branch!r}" if args.branch else "")
          + (", only-empty" if args.only_empty else "")
          + (", refresh" if args.refresh else ", 跳过curated") + "）", flush=True)

    if args.dry_run:
        doc = json.load(open(META_PATH, encoding="utf-8"))
        by_key = {(it["name"] + "\u0001" + it["taxonomy_path"]): it
                  for it in doc.get("instances", [])}
        for name, tpath in targets[: max(args.limit, 3)]:
            it = by_key.get(name + "\u0001" + tpath, {})
            print("=" * 60)
            print(build_user_prompt(name, tpath, it))
        print("=" * 60)
        print("[dry-run] 未调用 API，结束。")
        return

    llm.require_api_key()
    use_responses = llm.want_responses()
    client = llm.make_client()

    def work(name, tpath):
        doc = json.load(open(META_PATH, encoding="utf-8"))
        it = next((x for x in doc.get("instances", [])
                   if x["name"] == name and x["taxonomy_path"] == tpath), {})
        user = build_user_prompt(name, tpath, it)
        rec = llm.generate(client, SYSTEM_PROMPT, user, use_responses)
        ok = bool(rec and (rec.get("desc") or rec.get("definition")))
        cache.append(name + "\u0001" + tpath, rec or {}, ok)
        return name, ok, (rec or {}).get("desc", "")[:40] if rec else ""

    ok_n = fail_n = 0
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as ex:
        futs = [ex.submit(work, n, c) for n, c in targets]
        for i, fut in enumerate(as_completed(futs), 1):
            try:
                name, ok, prev = fut.result()
                ok_n += ok
                fail_n += (not ok)
                print(f"[{i}/{len(targets)}] {'OK ' if ok else 'FAIL'} {name} | {prev}",
                      flush=True)
            except Exception as e:
                fail_n += 1
                print(f"[{i}/{len(targets)}] ERROR {e}", flush=True)
            if args.delay:
                time.sleep(args.delay)

    print(f"生成完成：成功 {ok_n} / 失败 {fail_n}；缓存于 {CACHE_PATH}")

    if args.write:
        apply_cache(cache)
    else:
        print("（未加 --write，缓存未合并。需要时再运行 --write）")


def apply_cache(cache):
    recs = cache.records()
    if not recs:
        print("无有效缓存，跳过合并。")
        return
    doc = json.load(open(META_PATH, encoding="utf-8"))
    n = 0
    for it in doc.get("instances", []):
        key = it["name"] + "\u0001" + it["taxonomy_path"]
        if key in recs and merge_record(it, recs[key]):
            n += 1
    doc["meta"] = dict(doc.get("meta", {}))
    doc["meta"]["source"] = (
        doc["meta"].get("source", "") + " + gen_instance_kb.py(LLM 实例知识)")
    json.dump(doc, open(META_PATH, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print(f"已合并 {n} 条到 {META_PATH}")


if __name__ == "__main__":
    main()
