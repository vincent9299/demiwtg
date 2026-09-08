#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""gen_instance_kb.py — LLM 一次调用为一个概念生成 aliases + 知识文本草稿。

对 datasets/demiwtg/meta/concepts.json 中未富化概念（无 aliases 且 docs 层草稿
无条目），一次 LLM 调用产出：
    aliases（合并回 concepts.json 概念行，去重保序 ≤10）
    docs  （追加 state/collect/concepts_docs_draft.jsonl，kind=summary）

契约（AGENTS.md 1.5，2026-09-07 概念化迁移）：概念行四字段
name/aliases/carriers/taxonomy；desc/query/source 已退役——知识文本落 docs 层
草稿（草稿已有同名条目优先保留，不覆盖），检索词由采集运行时
（state/collect/query_terms_cache.json）维护，source 不再写行。

机制（共用 taxonomy/llm_common.py）：
  - OpenAI 兼容端点（LLM_BASE_URL），LLM_WEB_SEARCH=1 可联网核实（仅官方端点）
  - name 全局唯一（一个概念一条记录，见 AGENTS.md 1.5），一个概念只生成一次
  - 挂载上下文从 taxonomy.json 现算（mount_map）；行内 taxonomy 为快照，不回写
  - 断点续跑：缓存 state/taxonomy/.llm_kb_cache.jsonl，--overwrite 重生成
  - --only-empty 只补缺口、--refresh 全量重生成（docs 草稿条目始终优先保留）

用法：
  python3 taxonomy/gen_instance_kb.py --dry-run --limit 3 --branch "内容作品 IP"
  export LLM_API_KEY=sk-... LLM_BASE_URL=https://api.openai.com/v1 LLM_MODEL=gpt-4o-mini
  python3 taxonomy/gen_instance_kb.py --branch "内容作品 IP" --limit 50 --write
  python3 taxonomy/gen_instance_kb.py --only-empty --write      # 只补缺口
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent                # 仓库根（taxonomy/ 已提升根目录）
sys.path.insert(0, str(ROOT))

from taxonomy import llm_common as llm                       # noqa: E402
from taxonomy.mount_map import load_mount_map                # noqa: E402

META_PATH = ROOT / "datasets" / "demiwtg" / "meta" / "concepts.json"
TAXONOMY_PATH = ROOT / "datasets" / "demiwtg" / "meta" / "taxonomy.json"
CACHE_PATH = ROOT / "state" / "taxonomy" / ".llm_kb_cache.jsonl"
DOCS_DRAFT_PATH = ROOT / "state" / "collect" / "concepts_docs_draft.jsonl"
META_LOCK_PATH = META_PATH.parent / ".meta.lock"

SYSTEM_PROMPT = (
    "你是中文 IP 标签体系的知识库撰写助手。对每个给定的 IP 概念（作品 / 品牌 / 地标 / "
    "角色 / 美食 / 赛事 / 吉祥物等），产出客观、准确、不编造的内容，风格接近维基百科词条：\n"
    "必须有具体知识点（年代、国家、创作者、代表作、数据、荣誉），拒绝空话套话。\n"
    "若你对该概念不了解或信息可能过时，应使用可用的联网检索工具核实后再作答。\n"
    "只输出一个 JSON 对象，不要任何额外文字，格式：\n"
    '{"docs": "详细介绍(150-350字，说明它是什么、来源/创作者、核心内容或特征、'
    '影响力/知名度；须包含具体事实，禁止写"归入XX分类""可作为独立IP资产被识别与调用"'
    '一类空话)", '
    '"aliases": ["别名/英文名/简称", ...]（最多8个）}'
)


def load_docs_bodies() -> dict:
    """docs 层草稿 {name: body}（断点/判据/prompt 上下文三用）。"""
    out = {}
    if DOCS_DRAFT_PATH.exists():
        with DOCS_DRAFT_PATH.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                out[r["name"]] = r.get("body") or ""
    return out


def build_user_prompt(name, taxonomy_paths, it, docs_body):
    ctx = []
    if it.get("aliases"):
        ctx.append("已有别名：" + "、".join(str(x) for x in it["aliases"]))
    if docs_body:
        ctx.append("已有知识草稿：" + docs_body[:200])
    ctx_block = ("\n".join(ctx) + "\n") if ctx else ""
    return (
        f"概念名称：{name}\n"
        f"所属分类(instance of)：{taxonomy_paths}\n"
        f"{ctx_block}"
        "请生成该概念的 docs / aliases。"
    )


def load_targets(args, mounts, docs_bodies):
    """概念列表：契约保证 name 全局唯一；挂载路径由 mounts（树现算）提供。"""
    doc = json.load(open(META_PATH, encoding="utf-8"))
    out = []
    for it in doc.get("concepts", []):
        name = it.get("name", "")
        if not name:
            continue
        paths = mounts.get(name, [])
        if args.branch and not any(args.branch in p for p in paths):
            continue
        if args.only_empty and (it.get("aliases") or name in docs_bodies):
            continue
        out.append(name)
    return out, doc


def merge_aliases(it, rec) -> bool:
    """LLM aliases 合并进概念行（去重保序，≤10）；返回是否有变化。"""
    al = rec.get("aliases") or []
    if isinstance(al, str):
        al = [al]
    existing = list(it.get("aliases") or [])
    for x in al:
        x = str(x).strip()
        if x and x not in existing:
            existing.append(x)
    existing = existing[:10]
    if existing and existing != list(it.get("aliases") or []):
        it["aliases"] = existing
        return True
    return False


def main():
    ap = argparse.ArgumentParser(description="LLM 一次调用为一个概念生成 aliases + 知识文本草稿")
    ap.add_argument("--only-empty", action="store_true",
                    help="只处理尚无 aliases 且 docs 草稿无条目的概念")
    ap.add_argument("--refresh", action="store_true",
                    help="连已富化概念也重生成（docs 草稿已有条目仍优先保留不覆盖）")
    llm.add_common_args(ap)
    args = ap.parse_args()

    mounts = load_mount_map(TAXONOMY_PATH)
    docs_bodies = load_docs_bodies()
    targets, doc = load_targets(args, mounts, docs_bodies)
    by_name = {it["name"]: it for it in doc.get("concepts", [])}
    cache = llm.JsonlCache(CACHE_PATH)
    done = set() if args.overwrite else cache.done_keys()
    targets = [n for n in targets if n not in done]
    if args.limit:
        targets = targets[:args.limit]

    print(f"目标概念：{len(targets)} 条（按概念名去重）"
          + (f"（branch={args.branch!r}" if args.branch else "")
          + (", only-empty" if args.only_empty else "")
          + (", refresh" if args.refresh else "") + "）", flush=True)

    if args.dry_run:
        for name in targets[: max(args.limit, 3)]:
            it = by_name.get(name, {})
            paths = "、".join(mounts.get(name, []))
            print("=" * 60)
            print(build_user_prompt(name, paths, it, docs_bodies.get(name, "")))
        print("=" * 60)
        print("[dry-run] 未调用 API，结束。")
        return

    llm.require_api_key()
    use_responses = llm.want_responses()
    client = llm.make_client()

    def work(name):
        it = by_name.get(name, {})
        paths = "、".join(mounts.get(name, []))
        user = build_user_prompt(name, paths, it, docs_bodies.get(name, ""))
        rec = llm.generate(client, SYSTEM_PROMPT, user, use_responses)
        ok = bool(rec and (rec.get("docs") or rec.get("aliases")))
        cache.append(name, rec or {}, ok)
        return name, ok, (rec or {}).get("docs", "")[:40] if rec else ""

    ok_n = fail_n = 0
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as ex:
        futs = [ex.submit(work, n) for n in targets]
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
    draft_names = set(load_docs_bodies())
    docs_out = []
    n_alias = 0
    for it in doc.get("concepts", []):
        rec = recs.get(it["name"])
        if not rec:
            continue
        if merge_aliases(it, rec):
            n_alias += 1
        body = (rec.get("docs") or "").strip()
        if body and it["name"] not in draft_names:
            docs_out.append({"name": it["name"], "kind": "summary", "body": body})
    doc["meta"] = dict(doc.get("meta", {}))
    doc["meta"]["source"] = (
        doc["meta"].get("source", "") + " + gen_instance_kb.py(LLM 概念富化)")
    DOCS_DRAFT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(META_LOCK_PATH, "a") as lf:
        fcntl.flock(lf, fcntl.LOCK_EX)
        try:
            tmp = META_PATH.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=1),
                           encoding="utf-8")
            os.replace(tmp, META_PATH)
            if docs_out:
                with DOCS_DRAFT_PATH.open("a", encoding="utf-8") as f:
                    fcntl.flock(f.fileno(), fcntl.LOCK_EX)
                    try:
                        for r in docs_out:
                            f.write(json.dumps(r, ensure_ascii=False) + "\n")
                    finally:
                        fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        finally:
            fcntl.flock(lf, fcntl.LOCK_UN)
    print(f"已合并：aliases {n_alias} 条 -> {META_PATH}；"
          f"docs {len(docs_out)} 条 -> {DOCS_DRAFT_PATH}")


if __name__ == "__main__":
    main()
