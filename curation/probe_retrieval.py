#!/usr/bin/env python3
"""检索-实例归属准确率探针：以 danbooru 角色 tag 为真值核溯源归属。

背景：流式采集的 danbooru 图按实例扩展词检索（magician/DMM/Speedo 之类），
tag 式检索词极少，溯源实例与图内容大概率两回事。本探针用采集时刻落盘的
danbooru 社区角色 tag（DLQ payload 的 evidence.characters，即 API 返回的
tag_string_character）作真值，核 images.jsonl 当前 instances 归属是否一致。

判定两级（每个「图 × 实例归属」一对）：
1. characters 为空 → no_char_tag（角色 tag 切面无信息，不调 LLM）；
2. 规则归一化匹配（tag vs 实例名/别名）命中 → match（by=rule）；
3. 其余交纯文本 LLM 裁判，四分类 verdict + reason（reason 落盘便于审计）。

产物（运行时状态，可从 images.jsonl + DLQ 重算）：
    state/probe_retrieval/pairs.jsonl    判定对清单（固定顺序可复现）
    state/probe_retrieval/results.jsonl  逐对结果（断点续跑，按 key 去重）

用法：
    python3 curation/probe_retrieval.py                 # 全量判定对
    python3 curation/probe_retrieval.py --sample 100    # 小规模冒烟
    python3 curation/probe_retrieval.py --report        # 只重算汇总报告
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import re
import sqlite3
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

try:
    import httpx
except ImportError:
    sys.exit("缺少 httpx：python3 -m pip install httpx")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from curation.annotate_vlm import (  # noqa: E402
    DEFAULT_ENDPOINT, DEFAULT_MODEL, load_instance_kb, load_manifest)

JUDGE_PROMPT = (
    "判断「图片角色 tag」与「实例归属」是否一致。图片来自 danbooru 动漫图站，"
    "角色 tag 是社区人工标注的图中出现角色（形如 name 或 name_(series)，"
    "括号内是所属作品），可视为真值。\n"
    "实例：{name}\n"
    "实例背景：{kb}\n"
    "该图检索词：{query}\n"
    "该图角色 tag：{chars}\n"
    "判定标准：\n"
    "- match：tag 中任一角色就是该实例本身，或属于该实例对应的作品/IP"
    "（tag 括号内作品后缀是强佐证）；实例是公司/品牌/组织时，tag 任一角色"
    "出自其旗下任一作品也算 match\n"
    "- mismatch：角色明确属于其他 IP，与该实例无关；含实例是品牌/地标/物件"
    "而图中角色与其毫无关联的归因错误\n"
    "- unknown：角色或实例信息太冷门，无法判断\n"
    '严格按 JSON 输出：{{"verdict": "match" 或 "mismatch" 或 "unknown", '
    '"reason": "一句话理由"}}，只输出 JSON')

SOURCE = "danbooru"          # images.jsonl 中流式 danbooru 图的 source 值
QUEUE_DB = "state/collect/.dlq_full_r5.sqlite3"
DESC_CHARS = 120             # 裁判 prompt 里实例 desc 截断长度

# 检索词形态分层（对应流式采集 tag 式检索词稀少的病根）
_CJK = re.compile(r"[\u4e00-\u9fff]")
_TAGISH = re.compile(r"[_(]")


def _norm(s: str) -> str:
    """归一化：小写、下划线/连字符转空格、压空白。"""
    return re.sub(r"\s+", " ", re.sub(r"[_\-]+", " ", (s or "")).strip().lower())


def _bare(tag: str) -> str:
    """去 booru 作品限定后缀：anya_(spy_x_family) → anya。"""
    return _norm(re.sub(r"\s*\([^)]*\)", "", tag))


def query_kind(q: str) -> str:
    q = q or ""
    if _CJK.search(q):
        return "中文词"
    if _TAGISH.search(q):
        return "tag式"
    return "普通英文"


def rule_match(chars: list[str], names_norm: set) -> bool:
    """规则快路径：character tag（全名/去括号形态）与实例名或别名归一化命中。"""
    for tag in chars:
        for cand in (_norm(tag), _bare(tag)):
            if not cand:
                continue
            for nm in names_norm:
                if cand == nm or cand in nm or nm in cand:
                    return True
    return False


def load_dlq_characters(root: Path) -> dict:
    """DLQ payload → {asset_id: characters 串}（只读打开，流式进程可能持写锁）。"""
    db = root / QUEUE_DB
    if not db.exists():
        sys.exit(f"[probe] 找不到采集队列 {db}，无法取角色 tag 真值")
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            "SELECT json_extract(payload,'$.asset_id'), "
            "json_extract(payload,'$.evidence.characters') "
            "FROM items WHERE source=? AND status='done'", (SOURCE,))
        out = {}
        for aid, chars in rows:
            if aid:
                out[str(aid)] = chars or ""
        return out
    finally:
        conn.close()


def build_pairs(root: Path, meta_dir: Path, sample_n: int, seed: int,
                out_path: Path) -> list:
    """images.jsonl（source=danbooru）× DLQ 角色 tag → 逐归属判定对。"""
    dlq = load_dlq_characters(root)
    recs = []
    uncovered = 0
    for rec in load_manifest(meta_dir):
        if rec.get("source") != SOURCE:
            continue
        aid = (rec.get("asset_ids") or {}).get(SOURCE, "")
        if aid not in dlq:
            uncovered += 1                 # DLQ 未覆盖（约 10 张），跳过
            continue
        recs.append((rec, dlq[aid]))
    if uncovered:
        print(f"[probe] DLQ 未覆盖 {uncovered} 张（跳过）", flush=True)
    if sample_n:
        rng = random.Random(seed)
        rng.shuffle(recs)
        recs = recs[:sample_n]

    pairs = []
    for rec, chars in recs:
        queries = rec.get("queries") or {}
        for inst in rec.get("instances") or []:
            pairs.append({
                "sha256": rec["sha256"],
                "instance": inst,
                "kb_match": rec.get("kb_match") or 0,
                "query": queries.get(inst, ""),
                "characters": chars,
            })
    tmp = str(out_path) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        for p in pairs:
            f.write(json.dumps(p, ensure_ascii=False) + "\n")
    os.replace(tmp, out_path)
    print(f"[probe] 判定对 {len(pairs)} 条（{len(recs)} 张图）→ {out_path}",
          flush=True)
    return pairs


async def run_probe(args) -> None:
    root = Path(__file__).resolve().parent.parent
    meta_dir = Path(args.meta).resolve()
    state_dir = root / "state/probe_retrieval"
    state_dir.mkdir(parents=True, exist_ok=True)
    pairs_path = state_dir / "pairs.jsonl"
    results_path = state_dir / "results.jsonl"

    if args.resample or not pairs_path.exists():
        pairs = build_pairs(root, meta_dir, args.sample, args.seed, pairs_path)
    else:
        pairs = [json.loads(l) for l in open(pairs_path, encoding="utf-8")]
        print(f"[probe] 复用既有判定对 {len(pairs)} 条（--resample 可重建）",
              flush=True)

    def _key(p):
        return f'{p["sha256"]}|{p["instance"]}'

    done = set()
    if results_path.exists():
        with open(results_path, encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                    done.add(f'{r["sha256"]}|{r["instance"]}')
                except (json.JSONDecodeError, KeyError):
                    continue
    todo = [p for p in pairs if _key(p) not in done]
    print(f"[probe] 判定对 {len(pairs)} / 已完成 {len(done)} / 待跑 {len(todo)}",
          flush=True)
    if not todo:
        return

    kb = load_instance_kb(root)
    # 实例名 + 别名归一化集合（规则快路径用）
    names_norm = defaultdict(set)
    for inst, rec in kb.items():
        s = names_norm[inst]
        s.add(_norm(inst))
        for a in rec["aliases"]:
            if _norm(a):
                s.add(_norm(a))

    sem = asyncio.Semaphore(args.concurrency)
    memo = {}                       # (instance, chars 排序串) → (verdict, reason)
    outf = open(results_path, "a", encoding="utf-8")
    counter = {"done": 0}
    t0 = time.time()

    async def judge(client, p):
        rec = kb.get(p["instance"]) or {"desc": "", "aliases": []}
        kb_txt = "；".join(rec["aliases"][:6])
        if rec["desc"]:
            kb_txt = (kb_txt + "；" if kb_txt else "") + rec["desc"][:DESC_CHARS]
        payload = {
            "model": args.model, "max_tokens": 256, "temperature": 0.0,
            "chat_template_kwargs": {"enable_thinking": False},
            "response_format": {"type": "json_object"},
            "messages": [{"role": "user", "content": JUDGE_PROMPT.format(
                name=p["instance"], kb=kb_txt or "无",
                query=p["query"] or "无", chars=p["characters"])}],
        }
        for attempt in range(1, args.retries + 1):
            try:
                r = await client.post(args.endpoint, json=payload, timeout=60)
                r.raise_for_status()
                content = r.json()["choices"][0]["message"]["content"]
                m = re.search(r"\{.*\}", content, re.S)
                if not m:
                    raise ValueError("no_json")
                obj = json.loads(m.group(0))
                v = str(obj.get("verdict") or "").strip().lower()
                if v not in ("match", "mismatch", "unknown"):
                    raise ValueError(f"bad_verdict:{v}")
                return v, str(obj.get("reason") or "")[:200]
            except Exception:  # noqa: BLE001
                if attempt == args.retries:
                    return "judge_fail", ""
                await asyncio.sleep(min(2 ** attempt, 10))

    async def worker(p):
        async with sem:
            chars = p["characters"].split()
            if not chars:
                verdict, by, reason = "no_char_tag", "empty", ""
            elif rule_match(chars, names_norm.get(p["instance"],
                                                  {_norm(p["instance"])})):
                verdict, by, reason = "match", "rule", ""
            else:
                mk = (p["instance"], " ".join(sorted(chars)))
                if mk in memo:
                    verdict, reason = memo[mk]
                else:
                    verdict, reason = await judge(client, p)
                    if verdict != "judge_fail":
                        memo[mk] = (verdict, reason)
                by = "judge"
            rec = {**p, "verdict": verdict, "by": by, "reason": reason}
            outf.write(json.dumps(rec, ensure_ascii=False) + "\n")
            outf.flush()
            counter["done"] += 1
            if counter["done"] % 100 == 0 or counter["done"] == len(todo):
                dt = time.time() - t0
                print(f"[probe] {counter['done']}/{len(todo)} "
                      f"({counter['done']/dt:.1f} pair/s)", flush=True)

    limits = httpx.Limits(max_connections=args.concurrency * 2,
                          max_keepalive_connections=args.concurrency)
    async with httpx.AsyncClient(limits=limits) as client_:
        client = client_
        await asyncio.gather(*(worker(p) for p in todo))
    outf.close()
    print(f"[probe] 完成，耗时 {time.time()-t0:.0f}s", flush=True)


def report(args) -> None:
    root = Path(__file__).resolve().parent.parent
    state_dir = root / "state/probe_retrieval"
    results_path = state_dir / "results.jsonl"
    if not results_path.exists():
        sys.exit("[probe] 无 results.jsonl，先跑探针")
    recs = [json.loads(l) for l in open(results_path, encoding="utf-8")]
    pairs_path = state_dir / "pairs.jsonl"
    if pairs_path.exists():
        # 只统计当前判定对内的结果（resample 后旧记录自动出局）
        keys = set()
        for l in open(pairs_path, encoding="utf-8"):
            p = json.loads(l)
            keys.add(f'{p["sha256"]}|{p["instance"]}')
        recs = [r for r in recs
                if f'{r["sha256"]}|{r["instance"]}' in keys]

    print("=" * 66)
    print(f"检索-实例归属验证报告：{len(recs)} 个归属对"
          f"（真值 = 采集时刻 danbooru 角色 tag）")
    print("=" * 66)

    dist = Counter(r["verdict"] for r in recs)
    n = len(recs)
    print("\n【总体分布（归属对级）】")
    for v in ("match", "mismatch", "unknown", "no_char_tag", "judge_fail"):
        c = dist.get(v, 0)
        print(f"  {v:<12}: {c:6d} ({100*c/n:5.1f}%)")
    judged = [r for r in recs if r["verdict"] in ("match", "mismatch")]
    if judged:
        acc = 100 * sum(1 for r in judged if r["verdict"] == "match") / len(judged)
        print(f"  角色 tag 可判定对内 match 率: {acc:.1f}% (n={len(judged)})")

    # 图级口径：一张图至少一个归属 match / 全错
    per_img = defaultdict(list)
    for r in recs:
        per_img[r["sha256"]].append(r["verdict"])
    n_img = len(per_img)
    img_match = sum(1 for vs in per_img.values() if "match" in vs)
    img_wrong = sum(1 for vs in per_img.values()
                    if "mismatch" in vs and "match" not in vs)
    img_na = sum(1 for vs in per_img.values()
                 if set(vs) <= {"no_char_tag", "unknown", "judge_fail"})
    print(f"\n【图级口径】共 {n_img} 张")
    print(f"  至少一个归属 match : {img_match:6d} ({100*img_match/n_img:5.1f}%)")
    print(f"  有归属被证伪且无 match: {img_wrong:6d} ({100*img_wrong/n_img:5.1f}%)")
    print(f"  角色 tag 切面无结论 : {img_na:6d} ({100*img_na/n_img:5.1f}%)")

    def _stat(sub, label):
        jd = [r for r in sub if r["verdict"] in ("match", "mismatch")]
        if not jd:
            na = sum(1 for r in sub if r["verdict"] == "no_char_tag")
            print(f"\n【{label}】n={len(sub)}（可判定 0，角色 tag 空 {na}）")
            return
        m = sum(1 for r in jd if r["verdict"] == "match")
        na = sum(1 for r in sub if r["verdict"] == "no_char_tag")
        print(f"\n【{label}】n={len(sub)}，可判定 {len(jd)}，"
              f"match 率 {100*m/len(jd):5.1f}%（角色 tag 空 {na}）")

    for lo, hi, label in ((7, 10**9, "kb_match ≥7（VLM 核实过）"),
                          (4, 7, "kb_match 4-6（部分匹配）"),
                          (0, 4, "kb_match ≤3（溯源标基本未核实）")):
        _stat([r for r in recs if lo <= r["kb_match"] < hi], label)
    for k in ("tag式", "普通英文", "中文词"):
        _stat([r for r in recs if query_kind(r["query"]) == k], f"检索词形态：{k}")

    mm = [r for r in recs if r["verdict"] == "mismatch"]
    top_inst = Counter(r["instance"] for r in mm).most_common(15)
    print(f"\n【mismatch 最多的实例 top 15】（共 {len(mm)} 条 mismatch）")
    for name, c in top_inst:
        tot = sum(1 for r in judged if r["instance"] == name)
        print(f"  {name:<24} {c:4d}/{tot}")
    top_chars = Counter(t for r in mm for t in r["characters"].split())
    print("\n【mismatch 图中高频角色 tag top 20】（树外新角色候选线索）")
    for tag, c in top_chars.most_common(20):
        print(f"  {tag:<40} {c}")

    def _show(sub, label, k=10):
        print(f"\n{label}（前 {k}）：")
        for r in sub[:k]:
            print(f"  [{r['instance']}] q={r['query']!r} chars="
                  f"{r['characters'][:60]!r} | {r['reason'][:60]}")

    _show([r for r in recs if r["verdict"] == "match" and r["by"] == "judge"],
          "match 样例（裁判判同）")
    _show(mm, "mismatch 样例")
    jf = [r for r in recs if r["verdict"] == "judge_fail"]
    if jf:
        print(f"\n裁判失败 {len(jf)} 条（请求异常），未计入")


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="检索-实例归属准确率探针：danbooru 角色 tag 真值核溯源")
    ap.add_argument("--meta", default="data/dataset/meta")
    ap.add_argument("--endpoint", default=DEFAULT_ENDPOINT)
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--sample", type=int, default=0,
                    help="只抽 N 张图冒烟（0=全量）")
    ap.add_argument("--seed", type=int, default=42, help="抽样随机种子")
    ap.add_argument("--concurrency", type=int, default=16)
    ap.add_argument("--retries", type=int, default=3)
    ap.add_argument("--resample", action="store_true", help="强制重建判定对")
    ap.add_argument("--report", action="store_true", help="只重算汇总报告")
    args = ap.parse_args(argv)

    if args.report:
        report(args)
        return
    asyncio.run(run_probe(args))
    report(args)


if __name__ == "__main__":
    main()
