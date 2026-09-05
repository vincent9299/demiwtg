#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""en_entity_merge.py — EN/ZH 实体合并执行器（2026-09-02 用户拍板：推翻中英独立，EN 并入中文湖）。

分层：
  tier0      确定性匹配：alias_western/aliases/query 西文串 ≡ EN 名（限同节点）→ 直写对
  calibrate  85 节点 golden 校准：本地 vLLM 27B vs qwen3.7-plus 抽样结果（计数口径）
  bulk       本地 vLLM 逐节点对齐：pairs + 未匹配 EN 三分类(new/variant)
  escalate   不确定节点转 galaxy deepseek-v4-flash 复核（量小）
  apply      写库：matched/variant→aliases；new→新 instance(source=derived)+挂中文树；
             改前物理备份 state/taxonomy/backup_pre_en_merge/；报告 state/taxonomy/en_merge_v2_report.json
  report     汇总统计

产物（增量追加、断点续跑）落 state/taxonomy/en_merge/：
  tier0_pairs.jsonl / node_align.jsonl / node_align_flash.jsonl / done 标记内嵌于文件本身

用法：
  python3 data/collect_v2/en_entity_merge.py tier0
  python3 data/collect_v2/en_entity_merge.py calibrate
  python3 data/collect_v2/en_entity_merge.py bulk [--workers 64]
  python3 data/collect_v2/en_entity_merge.py escalate [--max-nodes 2000]
  python3 data/collect_v2/en_entity_merge.py apply [--dry-run]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import random
import re
import sys
import time
import unicodedata
from collections import defaultdict
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
META = ROOT / "datasets/demiwtg/meta"
CSV = ROOT / "state/taxonomy/v31_交付包/taxonomy_v3.1_交付包/data/taxonomy_tree_instances_en.csv"
OUT = ROOT / "state/taxonomy/en_merge"
GOLDEN = Path("/tank/tmp/kilo/en_zh_align/per_node.jsonl")
SEP = " / "
VLLM_BASE = "http://127.0.0.1:8000/v1"
VLLM_MODEL = "qwen3.8-27b"
FLASH_BASE = "http://127.0.0.1:4001/v1"
FLASH_MODEL = "galaxy/deepseek-v4-flash-0731"
FLASH_KEY = ""  # 从 modelhub/.env GLM_API_KEY_1 读
RANDOM_SEED = 20260902

SYS = (
    "你是双语实体对齐专家。给定同一概念树节点下、由不同来源独立生成的中文实例名单与英文实例名单，完成两件事："
    "1) 为每个中文实体在英文名单中找同一真实世界实体的对应条目（互为翻译或同实体异名：学名/俗名、全称/简称、"
    "含冠词差异算同；仅同类别不同实体不算；泛称与特称不算；拿不准不配）。每个中文实体至多配一个英文条目，宁缺毋滥。"
    "2) 对所有未匹配的英文名分类：type=new（该节点下此前不存在的独立新实体）或 type=variant"
    "（它只是某已配对英文名的别名变体/写法差异，需给 variant_of_en=该英文名序号）。"
    "只输出严格 JSON，不要任何其他文字。"
)


def nrm(s: str) -> str:
    s = unicodedata.normalize("NFKD", str(s))
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^0-9a-zA-Z]+", " ", s).strip().lower()


def norm_path(p: str, old_l1: set) -> str:
    segs = p.split(SEP)
    segs = segs[2:] if len(segs) > 1 and segs[1] in old_l1 else segs[1:]
    return SEP.join(["demiwtg"] + [s for s in segs if s])


def load_env_pair():
    global FLASH_KEY
    for line in (ROOT / "modelhub/.env").read_text().splitlines():
        if line.startswith("GLM_API_KEY_1="):
            FLASH_KEY = line.split("=", 1)[1].strip().strip('"').strip("'")
    return FLASH_KEY


def load_rosters(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    out = {}

    def walk(n):
        out[n["path"]] = list(n.get("instances") or [])
        for c in n.get("children") or []:
            walk(c)

    walk(data["tree"])
    return out


def load_bridge():
    import csv
    en2zh = {}
    with open(CSV, encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            z, e = (row.get("node_path") or "").strip(), (row.get("node_path_en") or "").strip()
            if z and e:
                en2zh[norm_path(e, {"General Classification Tags", "IP Classification Tags"})] = \
                    norm_path(z, {"通用分类标签", "IP 分类标签"})
    return en2zh


def build_nodes(min_list: int = 1):
    """配对节点工作集：[(zh_path, zh_names(sorted unique), en_names)]"""
    en2zh = load_bridge()
    zh_r = load_rosters(META / "taxonomy.json")
    en_r = load_rosters(META / "taxonomy_en.json")
    nodes = []
    for ep, elist in en_r.items():
        zp = en2zh.get(ep)
        zlist = zh_r.get(zp) if zp else None
        ez, zz = sorted(set(elist)), sorted(set(zlist)) if zlist else []
        if zz and ez and len(zz) >= min_list and len(ez) >= min_list:
            nodes.append((zp, zz, ez))
    return nodes


def append_jsonl(path: Path, rows: list):
    with path.open("a", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def load_jsonl(path: Path) -> list:
    out = []
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return out


def make_prompt(zp: str, zh_names: list, en_names: list) -> str:
    msg = (f"节点：{zp}\n中文名单（序号. 名称）：\n"
           + "\n".join(f"{i}. {n}" for i, n in enumerate(zh_names, 1))
           + "\n英文名单（序号. 名称）：\n"
           + "\n".join(f"{i}. {n}" for i, n in enumerate(en_names, 1))
           + '\n\n输出 JSON：{"pairs": [[<中文序号>, <英文序号>], ...], '
             '"en_unmatched": [{"en": <英文序号>, "type": "new"|"variant", '
             '"variant_of_en": <英文序号或null>}], "confidence": "high"|"low"}')
    return msg


def parse_align(text: str) -> dict | None:
    # thinking 模型可能带思维链前缀：优先 ```json``` 块，其次从最后一个 "pairs" 回溯找 '{' 起点的平衡 JSON
    m = re.search(r"```json\s*(\{.*?\})\s*```", text, re.S)
    if m:
        try:
            d = json.loads(m.group(1))
        except json.JSONDecodeError:
            d = None
        if d is not None:
            return _clean_align(d)
    for anchor in ('"pairs"', '{'):
        starts = [mm.start() for mm in re.finditer(re.escape(anchor), text)]
        for s in reversed(starts):
            b = text.rfind("{", 0, s + 1)
            if b < 0:
                continue
            depth, in_str, esc = 0, False, False
            for i in range(b, len(text)):
                c = text[i]
                if in_str:
                    if esc:
                        esc = False
                    elif c == "\\":
                        esc = True
                    elif c == '"':
                        in_str = False
                elif c == '"':
                    in_str = True
                elif c == "{":
                    depth += 1
                elif c == "}":
                    depth -= 1
                    if depth == 0:
                        try:
                            return _clean_align(json.loads(text[b:i + 1]))
                        except json.JSONDecodeError:
                            break
        if anchor == '"pairs"':
            continue
    return None


def _clean_align(d: dict) -> dict:
    pairs, unm = [], []
    for p in d.get("pairs") or []:
        if isinstance(p, list) and len(p) == 2 and all(isinstance(x, int) for x in p):
            pairs.append(p)
    for u in d.get("en_unmatched") or []:
        if isinstance(u, dict) and isinstance(u.get("en"), int):
            unm.append({"en": u["en"], "type": u.get("type"),
                        "variant_of_en": u.get("variant_of_en")})
    if not isinstance(d.get("confidence"), str):
        d["confidence"] = "low"
    d["pairs"], d["en_unmatched"] = pairs, unm
    return d


# ---------------- tier 0 ----------------

def cmd_tier0(_args):
    OUT.mkdir(parents=True, exist_ok=True)
    aw = json.loads((META / "alias_western.json").read_text(encoding="utf-8"))
    insts = json.loads((META / "instances.json").read_text(encoding="utf-8"))["instances"]
    nodes = build_nodes(min_list=1)
    # EN 名 → 出现的 zh 节点集合
    en_norm = defaultdict(set)
    for zp, zz, ez in nodes:
        for e in ez:
            en_norm[nrm(e)].add(zp)
    zh2zhnodes = defaultdict(set)
    zh_rosters = load_rosters(META / "taxonomy.json")
    for zp, lst in zh_rosters.items():
        for nm in lst:
            zh2zhnodes[nm].add(zp)
    pairs, n_cand = [], 0
    for rec in insts:
        name = rec["name"]
        raw = aw.get(name)
        cands = set()
        for s in ([raw] if isinstance(raw, str) else list(raw or [])) \
                + list(rec.get("aliases") or []) + list(rec.get("query") or []):
            n = nrm(s)
            if n and re.search(r"[a-z]", n):
                cands.add(n)
        if not cands:
            continue
        n_cand += 1
        for c in cands:
            if c in en_norm and (en_norm[c] & zh2zhnodes.get(name, set())):
                # 同节点精确命中 → 反查 EN 原名（取第一个同节点的）
                tgt = None
                for zp, zz, ez in nodes:
                    if zp in (en_norm[c] & zh2zhnodes.get(name, set())):
                        for e in ez:
                            if nrm(e) == c:
                                tgt = e
                                break
                    if tgt:
                        break
                if tgt:
                    pairs.append({"zh": name, "en": tgt})
                break
    append_jsonl(OUT / "tier0_pairs.jsonl", pairs)
    print(f"tier0: 有西文候选 {n_cand:,} 实体，同节点精确命中 {len(pairs):,} 对 → tier0_pairs.jsonl")


# ---------------- LLM 调用 ----------------

async def call_node(client, base, model, zp, zz, ez, key=None):
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    body = {"model": model, "temperature": 0, "max_tokens": 3072,
            "chat_template_kwargs": {"enable_thinking": False},
            "messages": [{"role": "system", "content": SYS},
                         {"role": "user", "content": make_prompt(zp, zz, ez)}]}
    for attempt in range(3):
        try:
            r = await client.post(f"{base}/chat/completions", headers=headers, json=body, timeout=240)
            r.raise_for_status()
            text = r.json()["choices"][0]["message"]["content"]
            d = parse_align(text)
            if d is not None:
                return d
        except Exception:
            pass
        await asyncio.sleep(2 * (attempt + 1))
    return None


async def run_bulk(nodes, out_path, base, model, key, workers, limit=None):
    done = {r["node"] for r in load_jsonl(out_path) if r.get("confidence") not in (None, "err")}
    todo = [(zp, zz, ez) for zp, zz, ez in nodes if zp not in done]
    if limit:
        todo = todo[:limit]
    sem = asyncio.Semaphore(workers)
    cnt = [0]
    sink = out_path.open("a", encoding="utf-8")

    async with httpx.AsyncClient() as client:
        async def one(zp, zz, ez):
            async with sem:
                t0 = time.time()
                d = await call_node(client, base, model, zp, zz, ez, key)
                rec = {"node": zp, "zh_n": len(zz), "en_n": len(ez),
                       "pairs": (d or {}).get("pairs", []),
                       "en_unmatched": (d or {}).get("en_unmatched", []),
                       "confidence": (d or {}).get("confidence", "err"),
                       "model": model, "secs": round(time.time() - t0, 1),
                       "zh_names": zz, "en_names": ez}
                sink.write(json.dumps(rec, ensure_ascii=False) + "\n")
                cnt[0] += 1
                if cnt[0] % 200 == 0:
                    sink.flush()
                    print(f"  {cnt[0]}/{len(todo)}（失败累计见 confidence=err）", flush=True)

        await asyncio.gather(*[one(*t) for t in todo])
    sink.close()
    fails = sum(1 for r in load_jsonl(out_path) if r.get("confidence") == "err")
    print(f"bulk 完成 {len(todo)} 节点（累计解析失败 {fails}）→ {out_path.name}")


def _backend(args_backend: str):
    """vllm=本地 27B（GPU 被占时不可用）；flash=galaxy deepseek-v4-flash 走网关 4001。"""
    if args_backend == "flash":
        load_env_pair()
        return FLASH_BASE, FLASH_MODEL, FLASH_KEY
    return VLLM_BASE, VLLM_MODEL, None


def cmd_bulk(args):
    OUT.mkdir(parents=True, exist_ok=True)
    nodes = build_nodes(min_list=1)
    base, model, key = _backend(args.backend)
    print(f"backend={model}")
    asyncio.run(run_bulk(nodes, OUT / "node_align.jsonl", base, model,
                         key, args.workers))


def cmd_calibrate(args):
    gold = {r["node"]: r for r in load_jsonl(GOLDEN)}
    nodes = [n for n in build_nodes(min_list=3) if n[0] in gold]
    base, model, key = _backend(args.backend)
    print(f"backend={model}")
    asyncio.run(run_bulk(nodes, OUT / f"calibrate_{args.backend}.jsonl", base, model,
                         key, 16, limit=len(nodes)))
    rows = {r["node"]: r for r in load_jsonl(OUT / "calibrate_27b.jsonl") if r["confidence"] != "err"}
    same = near = 0
    diffs = []
    for zp, r in rows.items():
        g = gold[zp]
        d = abs(len({p[0] for p in r["pairs"]}) - g["zh_matched"])
        if d == 0:
            same += 1
        if d <= 1:
            near += 1
        diffs.append(d)
    n = len(rows)
    if n:
        import statistics
        print(f"校准：{n} 节点 | zh_matched 计数完全一致 {same/n:.0%} | 差≤1 {near/n:.0%} | "
              f"平均差 {statistics.mean(diffs):.2f}")
    print("判定线：完全一致 ≥70% 或 差≤1 ≥85% → 放量；否则提高 escalate 比例")


def cmd_escalate(args):
    load_env_pair()
    rows = load_jsonl(OUT / "node_align.jsonl")
    tier0 = {p["zh"] for p in load_jsonl(OUT / "tier0_pairs.jsonl")}
    tier0_by_node = defaultdict(set)
    zh_rosters = load_rosters(META / "taxonomy.json")
    nodes_map = {zp: (zz, ez) for zp, zz, ez in build_nodes(min_list=1)}
    # tier0 对所在节点（用于矛盾检测）
    t0pairs = load_jsonl(OUT / "tier0_pairs.jsonl")
    for p in t0pairs:
        for zp in zh_rosters:
            pass  # 同节点矛盾检测走 bulk 结果内的 zh 名册
    suspects = []
    for r in rows:
        if r.get("confidence") == "err":
            suspects.append(r["node"])
            continue
        zmatch = {p[0] for p in r["pairs"]}
        zh_in_t0 = {i + 1 for i, nm in enumerate(r.get("zh_names", []))
                    if nm in tier0}
        if zh_in_t0 - zmatch:                       # tier0 命中但模型漏配
            suspects.append(r["node"])
            continue
        cov = len(zmatch) / max(min(r["zh_n"], r["en_n"]), 1)
        if cov < 0.5 and min(r["zh_n"], r["en_n"]) >= 3:
            suspects.append(r["node"])
            continue
        if r.get("confidence") == "low":
            suspects.append(r["node"])
    suspects = list(dict.fromkeys(suspects))[: args.max_nodes]
    print(f"escalate：{len(suspects)} 个不确定节点 → flash 复核")
    todo = [(zp, *nodes_map[zp]) for zp in suspects if zp in nodes_map]
    asyncio.run(run_bulk(todo, OUT / "node_align_flash.jsonl", FLASH_BASE, FLASH_MODEL,
                         FLASH_KEY, 8))


def merge_ops():
    """汇总 bulk+flash+tier0 → 最终操作集。flash 结果优先。"""
    rows = {r["node"]: r for r in load_jsonl(OUT / "node_align.jsonl") if r.get("confidence") != "err"}
    flash = {r["node"]: r for r in load_jsonl(OUT / "node_align_flash.jsonl") if r.get("confidence") != "err"}
    rows.update(flash)
    t0 = {(p["zh"], p["en"]) for p in load_jsonl(OUT / "tier0_pairs.jsonl")}
    alias_ops = defaultdict(set)   # zh entity -> EN 名集合
    new_entities = []              # (node, en_name)
    t0_used = set()
    for zp, r in rows.items():
        zz, ez = r.get("zh_names", []), r.get("en_names", [])
        paired_en = {}
        for zi, ei in r["pairs"]:
            if 1 <= zi <= len(zz) and 1 <= ei <= len(ez):
                alias_ops[zz[zi - 1]].add(ez[ei - 1])
                paired_en[ei] = zz[zi - 1]
        # tier0 同节点补充（模型漏配的确定性对）
        zset = set(zz)
        for zh, en in t0:
            if zh in zset and en in set(ez) and en not in {ez[k - 1] for k in paired_en}:
                alias_ops[zh].add(en)
                paired_en[ez.index(en) + 1] = zh
                t0_used.add((zh, en))
        for u in r.get("en_unmatched", []):
            ei = u.get("en")
            if not isinstance(ei, int) or not (1 <= ei <= len(ez)):
                continue
            if ei in paired_en:
                continue
            if u.get("type") == "variant" and isinstance(u.get("variant_of_en"), int):
                tgt = paired_en.get(u["variant_of_en"])
                if tgt:
                    alias_ops[tgt].add(ez[ei - 1])
                    continue
            new_entities.append((zp, ez[ei - 1]))
    return alias_ops, new_entities, t0_used


def cmd_report(_args):
    alias_ops, new_entities, t0_used = merge_ops()
    zh_insts = json.loads((META / "instances.json").read_text(encoding="utf-8"))["instances"]
    existing = {i["name"] for i in zh_insts}
    coll = [e for _, e in new_entities if e in existing]
    print(f"alias 写入实体 {len(alias_ops):,}（EN 名共 {sum(len(v) for v in alias_ops.values()):,}）")
    print(f"new 实体 {len(new_entities):,}（与在册撞名 {len(coll)}）| tier0 补配 {len(t0_used):,}")
    # 未被 bulk 覆盖的 zh 实体（留空不写）
    covered = set(alias_ops)
    print(f"zh 在册 {len(existing):,}，覆盖 {len(covered & existing):,}（{(len(covered & existing)/len(existing)):.1%}）")


def cmd_apply(args):
    import shutil
    alias_ops, new_entities, t0_used = merge_ops()
    insts_doc = json.loads((META / "instances.json").read_text(encoding="utf-8"))
    tax_doc = json.loads((META / "taxonomy.json").read_text(encoding="utf-8"))
    insts = insts_doc["instances"]
    by_name = {i["name"]: i for i in insts}
    existing = set(by_name)
    # 撞名 new 实体：跳过新建，视作该在册实体的 alias（同名本就是最强对齐）
    n_skip = 0
    node_new = defaultdict(list)
    for zp, en in new_entities:
        if en in existing:
            alias_ops[en].add(en) if False else None
            n_skip += 1
            continue
        node_new[zp].append(en)
    # 1) aliases 写入
    n_alias_ins = n_alias_new = 0
    for zh, ens in alias_ops.items():
        rec = by_name.get(zh)
        if rec is None:
            continue
        cur = set(rec.get("aliases") or [])
        low = {a.lower() for a in cur}
        add = [e for e in sorted(ens) if e.lower() not in low]
        if add:
            rec["aliases"] = (rec.get("aliases") or []) + add
            n_alias_ins += len(add)
            n_alias_new += 1
    # 2) new 实体入库 + 挂树
    n_new = 0
    for zp, ens in node_new.items():
        for en in ens:
            if en in by_name:
                continue
            rec = {"name": en, "source": "derived"}
            insts.append(rec)
            by_name[en] = rec
            n_new += 1
    mounted = 0
    nodes_by_path = {}

    def walk(n):
        nodes_by_path[n["path"]] = n
        for c in n.get("children") or []:
            walk(c)

    walk(tax_doc["tree"])
    for zp, ens in node_new.items():
        node = nodes_by_path.get(zp)
        if node is None:
            continue
        lst = node.setdefault("instances", [])
        for en in ens:
            if en not in lst:
                lst.append(en)
                mounted += 1
    report = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "alias_entities": len(alias_ops),
        "alias_names_added": n_alias_ins,
        "entities_with_new_alias": n_alias_new,
        "new_entities": n_new,
        "new_mounted": mounted,
        "new_skipped_name_collision": n_skip,
        "tier0_backfill_pairs": len(t0_used),
        "instances_total_after": len(insts),
    }
    print(json.dumps(report, ensure_ascii=False, indent=1))
    if args.dry_run:
        print("（dry-run，未写库）")
        return
    bak = ROOT / "state/taxonomy/backup_pre_en_merge"
    bak.mkdir(parents=True, exist_ok=True)
    for f in ("instances.json", "taxonomy.json"):
        if not (bak / f).exists():
            shutil.copy2(META / f, bak / f)
    (META / "instances.json").write_text(
        json.dumps(insts_doc, ensure_ascii=False, indent=1), encoding="utf-8")
    (META / "taxonomy.json").write_text(
        json.dumps(tax_doc, ensure_ascii=False, indent=1), encoding="utf-8")
    (ROOT / "state/taxonomy/en_merge_v2_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    # 校验
    names = [i["name"] for i in json.loads((META / "instances.json").read_text(encoding="utf-8"))["instances"]]
    assert len(names) == len(set(names)), "name 唯一性被破坏"
    print("写库完成 + name 唯一性校验通过；备份在", bak)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("tier0")
    bp = sub.add_parser("bulk"); bp.add_argument("--workers", type=int, default=64)
    bp.add_argument("--backend", choices=("vllm", "flash"), default="vllm")
    cp = sub.add_parser("calibrate"); cp.add_argument("--backend", choices=("vllm", "flash"), default="vllm")
    ep = sub.add_parser("escalate"); ep.add_argument("--max-nodes", type=int, default=2000)
    sub.add_parser("report")
    app = sub.add_parser("apply"); app.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    dict(tier0=cmd_tier0, calibrate=cmd_calibrate, bulk=cmd_bulk,
         escalate=cmd_escalate, report=cmd_report, apply=cmd_apply)[args.cmd](args)


if __name__ == "__main__":
    main()
