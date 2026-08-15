#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gen_instance_llm.py — 用 LLM 为每个 IP 实例标签生成知识库字段：
    · detail  详细介绍（2-4 句，客观准确）
    · query   检索扩展词（query 词，含英文/简称/别名）
    · aliases 别名（含英文名）

设计要点
--------
1. OpenAI 兼容 Chat Completions 接口：把 LLM_BASE_URL 指向任意兼容端点
   （OpenAI / 通义(Qwen) / DeepSeek / 本地 Ollama 等）即可，不需要专用 SDK。
2. 联网检索：LLM_WEB_SEARCH=1 且端点为 OpenAI 官方时，走 Responses API 的
   web_search_preview，模型对不确定的实体自动联网核实；其余端点忽略该选项并给出警告。
3. 断点续跑：每成功一条追加到 data/.llm_kb_cache.jsonl，重启自动跳过已完成项；
   --overwrite 可强制重生成。
4. 安全：默认跳过 source=curated（人工精确）的实例以免覆盖优质内容；
   --include-curated 可纳入。
5. 合并：--write 时把缓存合并回 instances_meta.json（写 desc / query / 扩展 aliases）。

示例
----
  # 1) 试运行：不调 API，打印前 3 条的 prompt（校验逻辑，零成本）
  python scripts/gen_instance_llm.py --dry-run --limit 3 --branch "内容作品 IP"

  # 2) 真实生成（在你的机器上，已配好 LLM_API_KEY）
  export LLM_API_KEY=sk-...
  export LLM_BASE_URL=https://api.openai.com/v1
  export LLM_MODEL=gpt-4o-mini
  python scripts/gen_instance_llm.py --branch "内容作品 IP" --limit 50 --write

  # 3) 开启联网检索（仅 OpenAI 端点）
  LLM_WEB_SEARCH=1 python scripts/gen_instance_llm.py --branch "内容作品 IP" --limit 50 --write

  # 4) 全量（建议先从单分支试点，再铺开）
  python scripts/gen_instance_llm.py --write
"""
import os
import sys
import json
import time
import argparse
import threading
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

ROOT = Path(__file__).resolve().parent.parent          # taxonomy-instances/
META_PATH = ROOT / "data" / "instances_meta.json"
CACHE_PATH = ROOT / "data" / ".llm_kb_cache.jsonl"

# ---- 配置（来自环境变量，适配“你的 LLM”端点）----
API_KEY = os.environ.get("LLM_API_KEY", "")
BASE_URL = os.environ.get("LLM_BASE_URL", "https://api.openai.com/v1").rstrip("/")
MODEL = os.environ.get("LLM_MODEL", "gpt-4o-mini")
WEB_SEARCH = os.environ.get("LLM_WEB_SEARCH") == "1"
TIMEOUT = float(os.environ.get("LLM_TIMEOUT", "60"))
TEMPERATURE = float(os.environ.get("LLM_TEMPERATURE", "0.3"))

_dry_run = False
_cache_lock = threading.Lock()
_seen_keys = set()


def log(*a):
    print(*a, flush=True)


# ---------------------------------------------------------------- 选择实例
def load_targets(args):
    doc = json.load(open(META_PATH, encoding="utf-8"))
    insts = doc.get("instances", [])
    out = []
    for it in insts:
        name = it.get("name", "")
        cat = it.get("category", "")
        if not name:
            continue
        if args.branch and args.branch not in cat:
            continue
        if args.only_empty and (it.get("desc") or it.get("query") or it.get("aliases")):
            continue
        if (not args.include_curated) and it.get("source") == "curated":
            continue
        out.append((name, cat))
    return out


def load_done_keys():
    keys = set()
    if CACHE_PATH.exists():
        for line in CACHE_PATH.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                if rec.get("ok"):
                    keys.add(rec["key"])
            except Exception:
                pass
    return keys


# ---------------------------------------------------------------- prompt
SYSTEM_PROMPT = (
    "你是中文 IP 标签体系的知识库撰写助手。对每个给定的 IP 实例（作品 / 品牌 / 地标 / "
    "角色 / 美食 / 赛事 / 吉祥物等），产出客观、准确、不编造的内容。\n"
    "若你对该实体不了解或信息可能过时，应使用可用的联网检索工具核实后再作答。\n"
    "只输出一个 JSON 对象，不要任何额外文字，格式：\n"
    '{"detail": "详细介绍(2-4句，说明它是什么、来源、影响力)", '
    '"query": "检索扩展词，逗号或顿号分隔，最多6个，优先英文与常用简称", '
    '"aliases": ["别名/英文名/简称", ...]（最多8个）}'
)


def build_user_prompt(name, cat, it):
    ctx = []
    if it.get("definition"):
        ctx.append("已有定义：" + it["definition"])
    if it.get("intro"):
        ctx.append("已有简介：" + it["intro"])
    if it.get("aliases"):
        ctx.append("已有别名：" + "、".join(it["aliases"]))
    ctx_block = ("\n".join(ctx) + "\n") if ctx else ""
    return (
        f"实例名称：{name}\n"
        f"所属分类(instance of)：{cat}\n"
        f"{ctx_block}"
        "请生成该实例的 detail / query / aliases。"
    )


# ---------------------------------------------------------------- LLM 调用
def make_client():
    # 懒加载：仅真实调用时才需要 openai 包，dry-run 不需要
    from openai import OpenAI
    return OpenAI(api_key=API_KEY, base_url=BASE_URL, timeout=TIMEOUT)


def _extract_json(text):
    text = text.strip()
    try:
        return json.loads(text)
    except Exception:
        pass
    s = text.find("{")
    e = text.rfind("}")
    if s != -1 and e != -1 and e > s:
        try:
            return json.loads(text[s:e + 1])
        except Exception:
            pass
    return None


def call_chat(client, name, cat, it):
    user = build_user_prompt(name, cat, it)
    try:
        resp = client.chat.completions.create(
            model=MODEL,
            temperature=TEMPERATURE,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user},
            ],
            response_format={"type": "json_object"},
        )
        return _extract_json(resp.choices[0].message.content or "")
    except Exception as e:
        # 部分兼容端点不支持 response_format，重试一次不加
        if "response_format" in str(e) or "json_object" in str(e):
            resp = client.chat.completions.create(
                model=MODEL,
                temperature=TEMPERATURE,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user},
                ],
            )
            return _extract_json(resp.choices[0].message.content or "")
        raise


def call_responses(client, name, cat, it):
    """OpenAI Responses API + web_search_preview（联网检索）。"""
    user = build_user_prompt(name, cat, it)
    resp = client.responses.create(
        model=MODEL,
        temperature=TEMPERATURE,
        tools=[{"type": "web_search_preview"}],
        input=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user},
        ],
    )
    return _extract_json(getattr(resp, "output_text", "") or "")


def generate_one(client, name, cat, it, use_responses):
    last = None
    for attempt in range(4):
        try:
            if use_responses:
                try:
                    return call_responses(client, name, cat, it)
                except Exception:
                    # Responses/联网不可用则回退 chat
                    return call_chat(client, name, cat, it)
            return call_chat(client, name, cat, it)
        except Exception as e:
            last = e
            wait = 2 ** attempt
            log(f"  [retry] {name} 第{attempt+1}次失败: {e}（{wait}s 后重试）")
            time.sleep(wait)
    raise last


# ---------------------------------------------------------------- 合并
def merge_record(it, rec):
    changed = False
    detail = (rec.get("detail") or "").strip()
    if detail:
        it["desc"] = detail
        changed = True
    q = rec.get("query") or ""
    if isinstance(q, str):
        q = [x.strip() for x in q.replace("、", ",").split(",") if x.strip()]
    if isinstance(q, list):
        q = [str(x).strip() for x in q if str(x).strip()]
    q = q[:6]
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
        it["source"] = "templated"
    return changed


def append_cache(key, rec, ok):
    line = json.dumps({"key": key, "ok": ok, "rec": rec},
                      ensure_ascii=False)
    with _cache_lock:
        with open(CACHE_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")


# ---------------------------------------------------------------- 主流程
def main():
    global _dry_run
    ap = argparse.ArgumentParser(description="用 LLM 为每个 IP 实例生成 detail/query/aliases")
    ap.add_argument("--branch", default="", help="只处理 category 含该子串的实例（如 '内容作品 IP'）")
    ap.add_argument("--only-empty", action="store_true", help="只处理尚无 desc/query/aliases 的实例")
    ap.add_argument("--include-curated", action="store_true", help="连 curated（人工精确）实例也重生成")
    ap.add_argument("--limit", type=int, default=0, help="最多处理 N 条（试点用）")
    ap.add_argument("--workers", type=int, default=4, help="并发线程数")
    ap.add_argument("--delay", type=float, default=0.0, help="每条提交后的间隔秒（限流）")
    ap.add_argument("--overwrite", action="store_true", help="重生成已缓存的实例")
    ap.add_argument("--dry-run", action="store_true", help="不调 API，仅打印 prompt")
    ap.add_argument("--write", action="store_true", help="把缓存合并回 instances_meta.json")
    args = ap.parse_args()
    _dry_run = args.dry_run

    targets = load_targets(args)
    done = set() if args.overwrite else load_done_keys()
    targets = [(n, c) for (n, c) in targets if (n + "\u0001" + c) not in done]
    if args.limit:
        targets = targets[:args.limit]

    log(f"目标实例：{len(targets)} 条"
        + (f"（branch={args.branch!r}" if args.branch else "")
        + (", only-empty" if args.only_empty else "")
        + (", include-curated" if args.include_curated else ", 跳过curated")
        + ("）"))

    if args.dry_run:
        doc = json.load(open(META_PATH, encoding="utf-8"))
        inst_by_key = {(it["name"] + "\u0001" + it["category"]): it
                       for it in doc.get("instances", [])}
        for name, cat in targets[: max(args.limit, 3)]:
            it = inst_by_key.get(name + "\u0001" + cat, {})
            log("=" * 60)
            log(build_user_prompt(name, cat, it))
        log("=" * 60)
        log("[dry-run] 未调用 API，结束。")
        return

    if not API_KEY:
        log("错误：未设置 LLM_API_KEY 环境变量。真实生成前请先 export LLM_API_KEY=... ")
        sys.exit(2)

    use_responses = WEB_SEARCH and BASE_URL.rstrip("/") in (
        "https://api.openai.com/v1", "https://api.openai.com")
    if WEB_SEARCH and not use_responses:
        log("[warn] LLM_WEB_SEARCH=1 仅 OpenAI 官方端点支持联网检索；"
            "当前端点不支持，已忽略（仅用模型自身知识）。")

    client = make_client()

    def work(name, cat):
        doc = json.load(open(META_PATH, encoding="utf-8"))
        it = next((x for x in doc.get("instances", [])
                   if x["name"] == name and x["category"] == cat), {})
        rec = generate_one(client, name, cat, it, use_responses)
        ok = bool(rec and rec.get("detail"))
        key = name + "\u0001" + cat
        append_cache(key, rec or {}, ok)
        return name, cat, ok, (rec or {}).get("detail", "")[:40] if rec else ""

    ok_n = 0
    fail_n = 0
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as ex:
        futs = [ex.submit(work, n, c) for n, c in targets]
        for i, fut in enumerate(as_completed(futs), 1):
            try:
                name, cat, ok, prev = fut.result()
                if ok:
                    ok_n += 1
                else:
                    fail_n += 1
                log(f"[{i}/{len(targets)}] {'OK ' if ok else 'FAIL'} {name}  | {prev}")
            except Exception as e:
                fail_n += 1
                log(f"[{i}/{len(targets)}] ERROR {e}")
            if args.delay:
                time.sleep(args.delay)

    log(f"生成完成：成功 {ok_n} / 失败 {fail_n}；缓存于 {CACHE_PATH}")

    if args.write:
        apply_cache()
    else:
        log("（未加 --write，缓存未合并。需要时再运行 --write）")


def apply_cache():
    if not CACHE_PATH.exists():
        log("无缓存文件，跳过合并。")
        return
    recs = {}
    for line in CACHE_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except Exception:
            continue
        if r.get("ok") and r.get("rec"):
            recs[r["key"]] = r["rec"]
    doc = json.load(open(META_PATH, encoding="utf-8"))
    n = 0
    for it in doc.get("instances", []):
        key = it["name"] + "\u0001" + it["category"]
        if key in recs and merge_record(it, recs[key]):
            n += 1
    doc["meta"] = dict(doc.get("meta", {}))
    doc["meta"]["source"] = (
        doc["meta"].get("source", "") +
        " + gen_instance_llm.py(LLM 生成 detail/query/aliases)"
    )
    json.dump(doc, open(META_PATH, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    log(f"已合并 {n} 条到 {META_PATH}")


if __name__ == "__main__":
    main()
