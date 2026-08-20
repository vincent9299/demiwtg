#!/usr/bin/env python3
"""IP 识别探针：以 danbooru 角色 tag 为真值，测 VLM 能认出多少。

背景：bulk_danbooru2023 的图自带社区人工标注的 tag_string_character（真值），
而打标流水线的 VLM 是「确认式」（给候选实体判断像不像），从没测过它在无候选
时的自由识别能力。本探针补这个数：抽样 danbooru 图 → VLM 自由识别主体角色 →
与真值 tag 对比，产出识别率画像（按 score 分层 / 体系已知角色 vs 长尾角色）。

判定两级：
1. 规则归一化匹配（VLM 答案 vs GT tag 全名/去作品限定形态，双向子串）；
2. 规则判不了的（如 VLM 答中文名、答作品名）交纯文本 LLM 裁判。

产物（运行时状态，可从 posts_meta.jsonl + images.jsonl 重算）：
    state/probe_identity/sample.jsonl     探针样本集（固定 seed，可复现）
    state/probe_identity/results.jsonl    逐图结果（断点续跑，按 pid 去重）

用法：
    python3 curation/probe_identity.py                 # 抽 400 张跑探针
    python3 curation/probe_identity.py --sample 100    # 小规模试跑
    python3 curation/probe_identity.py --report        # 只重算汇总报告
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import os
import random
import re
import sys
import time
from collections import defaultdict
from pathlib import Path

try:
    import httpx
except ImportError:
    sys.exit("缺少 httpx：python3 -m pip install httpx")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from curation.annotate_vlm import (  # noqa: E402
    DEFAULT_ENDPOINT, DEFAULT_MODEL, encode_image, load_manifest)

IDENTIFY_PROMPT = (
    "你是动漫角色识别专家。这张图来自动漫插画数据集。请识别画面中最突出/"
    "最主体的人物角色是谁。\n"
    "严格按 JSON 输出：{\"name\": \"角色名\"}\n"
    "要求：\n"
    "- 给出该角色的名字（中文、英文、罗马音均可，优先你最确定的叫法）；"
    "能认出所属作品时可写「角色名（作品名）」\n"
    "- 认不出具体角色（无名原创角色/认不准）时输出 {\"name\": \"\"}\n"
    "- 只输出 JSON，不要其他内容")

JUDGE_PROMPT = (
    "判断「模型给出的角色名」与「标注的角色 tag」是否指同一个角色。"
    "tag 形如 first_name 或 first_name_(series)，括号内是所属作品。\n"
    "别名/译名/昵称/罗马音差异算同一角色；只认出作品或画师不算；"
    "不同角色即使同作品也不算。\n"
    "严格按 JSON 输出：{{\"same\": true 或 false}}\n"
    "标注 tag：{tag}（作品：{copyright}）\n"
    "模型给出的角色名：{answer}")

# score 分层抽样配比（代表作模式门是 top1>=10，最低分即 10）
STRATA = ((10, 30, 0.50), (30, 100, 0.35), (100, 10**9, 0.15))


def _norm(s: str) -> str:
    """归一化：小写、下划线/连字符转空格、压空白。"""
    return re.sub(r"\s+", " ", re.sub(r"[_\-]+", " ", (s or "")).strip().lower())


def _bare(tag: str) -> str:
    """去 booru 作品限定后缀：anya_(spy_x_family) → anya。"""
    return _norm(re.sub(r"\s*\([^)]*\)", "", tag))


def rule_match(answer: str, gt_tag: str) -> bool | None:
    """规则匹配：True/False 明确；None = 拿不准（交裁判）。"""
    a = _norm(answer)
    if not a:
        return False
    full, bare = _norm(gt_tag), _bare(gt_tag)
    for cand in {full, bare}:
        if not cand:
            continue
        if a == cand or cand in a:
            return True
    # 反向：答案是不含空格的专名（如中文名），GT 里不可能含中文 → 拿不准
    # 答案首词恰为 bare 也算（如 "Asuna SAO"）
    if " " in a and a.split()[0] == bare:
        return True
    return None


def load_probe_sample(meta_dir: Path, sample_n: int, seed: int,
                      out_path: Path) -> list:
    """从 posts_meta × images.jsonl 抽探针样本（首位角色为真值，score 分层）。"""
    root = meta_dir.parent.parent.parent   # data/dataset/meta 向上三级 = 仓库根
    posts_path = root / "state/collect/bulk/danbooru2023/posts_meta.jsonl"
    by_pid = {}
    for rec in load_manifest(meta_dir):
        if rec.get("source") != "bulk_danbooru2023":
            continue
        aid = (rec.get("asset_ids") or {}).get("bulk_danbooru2023", "")
        if aid.startswith("danbooru2023-"):
            by_pid[int(aid.split("-", 1)[1])] = rec
    rows = []
    with open(posts_path, encoding="utf-8") as f:
        for line in f:
            r = json.loads(line)
            chars = (r.get("tag_string_character") or "").split()
            if not chars or r["id"] not in by_pid:
                continue
            rows.append({
                "pid": r["id"], "sha256": by_pid[r["id"]]["sha256"],
                "path": by_pid[r["id"]]["path"],
                "score": r.get("score") or 0,
                "gt_character": chars[0],            # 首位角色 = 代表作归属口径
                "gt_copyright": (r.get("tag_string_copyright") or "").split()[:2],
                "n_chars": len(chars),
            })
    rng = random.Random(seed)
    picked = []
    for lo, hi, ratio in STRATA:
        bucket = [r for r in rows if lo <= r["score"] < hi]
        rng.shuffle(bucket)
        picked.extend(bucket[:max(1, int(sample_n * ratio))])
    tmp = str(out_path) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        for r in picked:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    os.replace(tmp, out_path)
    print(f"[probe] 样本 {len(picked)} 条（总候选 {len(rows)}）→ {out_path}",
          flush=True)
    return picked


def load_taxonomy_names(root: Path) -> set:
    """体系已有实例名+别名（归一化）：用于区分「已知角色 vs 长尾角色」。"""
    names = set()
    p = root / "data/taxonomy/instances.json"
    for it in json.load(open(p, encoding="utf-8"))["instances"]:
        names.add(_norm(it.get("name", "")))
        for a in it.get("aliases") or []:
            names.add(_norm(a))
    names.discard("")
    return names


async def run_probe(args) -> None:
    root = Path(__file__).resolve().parent.parent
    meta_dir = Path(args.meta).resolve()
    ds_root = meta_dir.parent              # data/dataset
    state_dir = root / "state/probe_identity"
    state_dir.mkdir(parents=True, exist_ok=True)
    sample_path = state_dir / "sample.jsonl"
    results_path = state_dir / "results.jsonl"

    if args.resample or not sample_path.exists():
        samples = load_probe_sample(meta_dir, args.sample, args.seed, sample_path)
    else:
        samples = [json.loads(l) for l in open(sample_path, encoding="utf-8")]
        print(f"[probe] 复用既有样本 {len(samples)} 条（--resample 可重抽）",
              flush=True)

    done = set()
    if results_path.exists():
        with open(results_path, encoding="utf-8") as f:
            for line in f:
                try:
                    done.add(json.loads(line)["pid"])
                except (json.JSONDecodeError, KeyError):
                    continue
    todo = [s for s in samples if s["pid"] not in done]
    print(f"[probe] 样本 {len(samples)} / 已完成 {len(done)} / 待跑 {len(todo)}",
          flush=True)
    if not todo and not args.report:
        return

    sem = asyncio.Semaphore(args.concurrency)
    outf = open(results_path, "a", encoding="utf-8")
    counter = {"done": 0}
    t0 = time.time()

    async def identify(client, s):
        b64 = await asyncio.to_thread(
            encode_image, ds_root, {"path": s["path"]}, args.max_edge)
        if not b64:
            return {"pid": s["pid"], "ok": False, "error": "encode_fail"}
        payload = {
            "model": args.model, "max_tokens": 120, "temperature": 0.0,
            "chat_template_kwargs": {"enable_thinking": False},
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "user", "content": [
                    {"type": "image_url",
                     "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                    {"type": "text", "text": IDENTIFY_PROMPT},
                ]},
            ],
        }
        for attempt in range(1, args.retries + 1):
            try:
                r = await client.post(args.endpoint, json=payload, timeout=300)
                r.raise_for_status()
                content = r.json()["choices"][0]["message"]["content"]
                m = re.search(r"\{.*\}", content, re.S)
                name = json.loads(m.group(0)).get("name", "") if m else ""
                return {"pid": s["pid"], "ok": True,
                        "vlm_name": str(name).strip()}
            except Exception as e:  # noqa: BLE001
                if attempt == args.retries:
                    return {"pid": s["pid"], "ok": False,
                            "error": f"{type(e).__name__}: {e}"[:200]}
                await asyncio.sleep(min(2 ** attempt, 10))

    async def judge(client, tag, copyright_tags, answer):
        payload = {
            "model": args.model, "max_tokens": 40, "temperature": 0.0,
            "chat_template_kwargs": {"enable_thinking": False},
            "response_format": {"type": "json_object"},
            "messages": [{"role": "user", "content": JUDGE_PROMPT.format(
                tag=tag, copyright=" ".join(copyright_tags) or "无",
                answer=answer)}],
        }
        try:
            r = await client.post(args.endpoint, json=payload, timeout=60)
            r.raise_for_status()
            content = r.json()["choices"][0]["message"]["content"]
            m = re.search(r"\{.*\}", content, re.S)
            return bool(json.loads(m.group(0)).get("same")) if m else False
        except Exception:  # noqa: BLE001
            return False                      # 裁判失败从严：不算认出

    async def worker(s):
        async with sem:
            res = await identify(client, s)
            if not res.get("ok"):
                rec = {**s, "vlm_name": "", "matched": False,
                       "match_by": "error", "error": res.get("error")}
            else:
                name = res["vlm_name"]
                rm = rule_match(name, s["gt_character"]) if name else False
                if rm is None:
                    rm = await judge(client, s["gt_character"],
                                     s["gt_copyright"], name)
                    by = "judge"
                else:
                    by = "rule"
                rec = {**s, "vlm_name": name, "matched": bool(rm),
                       "match_by": by if name else "empty"}
            outf.write(json.dumps(rec, ensure_ascii=False) + "\n")
            outf.flush()
            counter["done"] += 1
            if counter["done"] % 20 == 0 or counter["done"] == len(todo):
                dt = time.time() - t0
                print(f"[probe] {counter['done']}/{len(todo)} "
                      f"({counter['done']/dt:.2f} img/s)", flush=True)

    limits = httpx.Limits(max_connections=args.concurrency * 2,
                          max_keepalive_connections=args.concurrency)
    async with httpx.AsyncClient(limits=limits) as client_:
        client = client_
        await asyncio.gather(*(worker(s) for s in todo))
    outf.close()
    print(f"[probe] 完成，耗时 {time.time()-t0:.0f}s", flush=True)


def report(args) -> None:
    root = Path(__file__).resolve().parent.parent
    state_dir = root / "state/probe_identity"
    results_path = state_dir / "results.jsonl"
    if not results_path.exists():
        sys.exit("[probe] 无 results.jsonl，先跑探针")
    recs = [json.loads(l) for l in open(results_path, encoding="utf-8")]
    sample_path = state_dir / "sample.jsonl"
    if sample_path.exists():
        # 只统计当前样本集内的结果（resample 后旧记录自动出局）
        pids = {json.loads(l)["pid"] for l in open(sample_path, encoding="utf-8")}
        recs = [r for r in recs if r["pid"] in pids]
    known_names = load_taxonomy_names(root)

    def bucket(r):
        sc = r["score"]
        return "10-29" if sc < 30 else ("30-99" if sc < 100 else "≥100")

    print("=" * 66)
    print(f"IP 识别探针报告：样本 {len(recs)} 张（真值 = danbooru 首位角色 tag）")
    print("=" * 66)

    def _stat(sub, label):
        n = len(sub)
        if not n:
            return
        named = [r for r in sub if r.get("vlm_name")]
        hit = [r for r in sub if r.get("matched")]
        wrong = [r for r in named if not r.get("matched")]
        print(f"\n【{label}】n={n}")
        print(f"  给出名字   : {len(named):4d} ({100*len(named)/n:5.1f}%)")
        print(f"  认对（识别率）: {len(hit):4d} ({100*len(hit)/n:5.1f}%)")
        print(f"  认错/答非所问: {len(wrong):4d} ({100*len(wrong)/n:5.1f}%)")
        print(f"  弃答（认不出）: {n-len(named):4d} ({100*(n-len(named))/n:5.1f}%)")

    _stat(recs, "总体")
    for b in ("10-29", "30-99", "≥100"):
        _stat([r for r in recs if bucket(r) == b], f"score {b}")
    _stat([r for r in recs if _bare(r["gt_character"]) in known_names
           or _norm(r["gt_character"]) in known_names], "GT 角色在体系内（已知角色）")
    _stat([r for r in recs if _bare(r["gt_character"]) not in known_names
           and _norm(r["gt_character"]) not in known_names], "GT 角色不在体系（长尾角色）")
    _stat([r for r in recs if r["n_chars"] == 1], "单角色图")
    _stat([r for r in recs if r["n_chars"] > 1], "多角色图（真值=首位）")

    hit = [r for r in recs if r.get("matched")]
    print(f"\n判定方式：规则命中 {sum(1 for r in hit if r['match_by']=='rule')} / "
          f"LLM 裁判 {sum(1 for r in hit if r['match_by']=='judge')}")
    errs = [r for r in recs if r.get("error")]
    if errs:
        print(f"异常样本 {len(errs)} 张（encode_fail/请求失败），未计入识别")
    print("\n认对样例（前 10）：")
    for r in hit[:10]:
        print(f"  GT={r['gt_character']:<30} VLM={r['vlm_name']}")
    wrong = [r for r in recs if r.get("vlm_name") and not r.get("matched")]
    print("\n认错样例（前 10）：")
    for r in wrong[:10]:
        print(f"  GT={r['gt_character']:<30} VLM={r['vlm_name']}")


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="IP 识别探针：danbooru 角色 tag 真值 vs VLM 自由识别")
    ap.add_argument("--meta", default="data/dataset/meta")
    ap.add_argument("--endpoint", default=DEFAULT_ENDPOINT)
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--sample", type=int, default=400, help="抽样张数（默认 400）")
    ap.add_argument("--seed", type=int, default=42, help="抽样随机种子")
    ap.add_argument("--concurrency", type=int, default=8)
    ap.add_argument("--max-edge", type=int, default=1024)
    ap.add_argument("--retries", type=int, default=3)
    ap.add_argument("--resample", action="store_true", help="强制重抽样本")
    ap.add_argument("--report", action="store_true", help="只重算汇总报告")
    args = ap.parse_args(argv)

    if args.report:
        report(args)
        return
    asyncio.run(run_probe(args))
    report(args)


if __name__ == "__main__":
    main()
