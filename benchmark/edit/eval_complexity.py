"""edit（编辑）赛道样本复杂度审计与选取（focus1000 池 -> 复杂图 top-K/实例）。

背景：edit v6.0 协议的难度七维中场景复杂度读原图——原图场景越密，定位消歧与
保持约束矩阵越大。本脚本对 focus1000 实例池（state/collect/focus1000_instances.json，
补图链保证每实例 ≥20 图）做两步：

1. audit：本地 vLLM（VL）全量审计池内合格图（edit 适配门：quality>=8 且
   identity 且 focus>=7 且短边>=512），按协议 scene_types 12 维打 0/1/2，
   外加 edit 特化字段（同类多实例数 / 后果传播载体 / 类型适配画像）；
   断点续跑（audit jsonl 已有 sha 跳过，error 行自动重试）。
2. select：每实例按（scene 维度数 -> 同类多实例 -> 载体数 -> quality）取
   top-K（默认 3），批次级分布报表（L2/L3 供给率、类型适配覆盖、低复杂实例名单）。

产物（评测数据，不入 git；旧轮历史账本已归档 archive/）：
    benchmark/edit/complexity_audit.jsonl     # 逐图审计（append，可续跑）
    benchmark/edit/selected_samples.jsonl     # 每实例 top-K 选取
    benchmark/edit/selected_report.json       # 批次校准报表

用法：
    python3 benchmark/edit/eval_complexity.py audit [--workers 12] [--limit 20]
    python3 benchmark/edit/eval_complexity.py select [--top-k 3]
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import re
import sys
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests

SUB_DIR = Path(__file__).resolve().parent                    # edit/
BENCH_ROOT = SUB_DIR.parent                                  # benchmark/
REPO_ROOT = BENCH_ROOT.parent                                # 仓库根
EVAL_DIR = SUB_DIR    # edit 无 data/ 层：focus200 与账本落子模块根
META_DIR = REPO_ROOT / "datasets" / "demiwtg" / "meta"
DATASET_DIR = REPO_ROOT / "datasets" / "demiwtg"

FOCUS_LIST = REPO_ROOT / "state" / "collect" / "focus1000_instances.json"
GEN_RESULTS = BENCH_ROOT / "t2i" / "data" / "focus1000" / "gen_results.jsonl"
GEN_JOBS_19 = EVAL_DIR / "focus200" / "missing19_jobs.jsonl"
FOCUS200_MANIFEST = EVAL_DIR / "focus200" / "manifest.jsonl"
AUDIT_OUT = EVAL_DIR / "complexity_audit.jsonl"
AUDIT_OUT_SYNTH = EVAL_DIR / "complexity_audit_synth.jsonl"
SELECT_OUT = EVAL_DIR / "selected_samples.jsonl"
REPORT_OUT = EVAL_DIR / "selected_report.json"

DEFAULT_ENDPOINT = "http://localhost:8000/v1/chat/completions"
DEFAULT_MODEL = "qwen3.8-27b"

MIN_EDGE_EDIT = 512
FOCUS_EDIT = 7

SCENE_DIMS = ["实体密度", "细节密度", "交互链", "过程时刻", "环境作用",
              "视点剖示", "规约场景", "多实例对比", "纵深层次", "光照时段",
              "动态要素", "多人物编排"]

CARRIER_ENUM = ["水面倒影", "镜面玻璃反光", "影子投影", "接触叠放",
                "液体容器", "仪表指示", "可动机构"]

AUDIT_PROMPT = """审计这张图片的场景复杂度。按 12 个维度打分（0=完全不成立，1=弱成立或单一，2=显著成立或多项）：
- 实体密度：画面点名级对象数量多、各自可辨
- 细节密度：单对象高部件数或高细节构成
- 交互链：对象间接触、遮挡、受力关系并存
- 过程时刻：事件进行中的瞬时状态
- 环境作用：同一环境对多个对象的差异化作用（雪、光、风、水汽）
- 视点剖示：视角或剖面让内部结构可见
- 规约场景：仪式、赛事、制度场景自带站位、持物、着装规制
- 多实例对比：同类多实例各处不同阶段或变体
- 纵深层次：前中背景多层各带内容与遮挡
- 光照时段：晨昏、人工光对全场景的统一光照作用
- 动态要素：运动物体与瞬时痕迹同框
- 多人物编排：3 人及以上各带角色与动作

再输出以下字段：
- same_class_groups：列出图中全部显著同类多实例组（个数 ≥2 才算一组），每组：{"class": 类名（如"红灯笼"）, "count": 个数, "role": "is_subject"（该图主体实体自身的同类多实例）或 "unrelated"（与主体无关的对象组）, "variants": "identical"（同款复制）或 "varied"（不同状态/阶段/变体）, "arrangement": "row"（排成行或列）/"cluster"（聚堆）/"scattered"（散布）}；无则空数组
- referents：可作为定位参照的显著独立对象（用于"离 X 最近的那只"类指认），列 2~4 个（无则空数组）
- consequence_carriers：图中在场的编辑后果传播载体，从下列枚举多选：["水面倒影", "镜面玻璃反光", "影子投影", "接触叠放", "液体容器", "仪表指示", "可动机构"]（无则空数组）
- suitability：{"has_person": 布尔, "person_count": 整数, "has_animal": 布尔, "subject_separable": 0-2（主体轮廓清晰、与背景可分离）, "background_content": 0-2（背景有独立可辨内容）}
- summary：一句话场景概述，不超过 40 字

只输出一个严格 JSON 对象（无围栏无解释），键名一字不差：
{"scene_sources": {"实体密度": 0, "细节密度": 0, "交互链": 0, "过程时刻": 0, "环境作用": 0, "视点剖示": 0, "规约场景": 0, "多实例对比": 0, "纵深层次": 0, "光照时段": 0, "动态要素": 0, "多人物编排": 0}, "same_class_groups": [{"class": "", "count": 2, "role": "unrelated", "variants": "identical", "arrangement": "row"}], "referents": [], "consequence_carriers": [], "suitability": {"has_person": false, "person_count": 0, "has_animal": false, "subject_separable": 1, "background_content": 1}, "summary": ""}"""


# ---------------------------------------------------------------------------
# 池聚合（metadata.jsonl 现算，不落派生索引）
# ---------------------------------------------------------------------------
def build_synth_pool() -> dict[str, dict]:
    """合成源池：双清单合流 → instance -> {sha256: 池行}。

    - gen_results.jsonl（qwen-image 主体批，t2i 侧产物原件）
    - focus200/missing19_jobs.jsonl（GPT-Image-2 补齐批，edit 侧）
    图不进湖（metadata.jsonl 是采集清单）；sha256 沿用生成时算好的值。
    """
    manifests = [(FOCUS200_MANIFEST, "manifest"), (GEN_RESULTS, "qwen-image"),
                 (GEN_JOBS_19, "gpt-image-2")]
    pools: dict[str, dict] = defaultdict(dict)
    seen = set()
    for mf, gen in manifests:
        if not mf.exists():
            continue
        with mf.open(encoding="utf-8") as f:
            for line in f:
                row = json.loads(line)
                rel = row.get("file") or ""
                if not rel or row.get("status") not in (None, "generated", "ok"):
                    continue
                p = rel if Path(rel).is_absolute() else mf.parent / rel
                if row["sha256"] in seen or not p.exists():
                    continue
                seen.add(row["sha256"])
                pools[row["instance"]][row["sha256"]] = {
                    "sha256": row["sha256"],
                    "ext": p.suffix.lstrip(".") or "png",
                    "path": str(p), "width": row.get("width"),
                    "height": row.get("height"),
                    "quality": None, "focus": None, "richness": None,
                    "generator": row.get("generator") or gen,
                }
    return dict(pools)


def load_focus_instances() -> set:
    if not FOCUS_LIST.exists():
        sys.exit(f"focus1000 清单不存在：{FOCUS_LIST}")
    data = json.loads(FOCUS_LIST.read_text(encoding="utf-8"))
    return {it["name"] for it in data["instances"]}


def pass_edit_gate(row: dict) -> bool:
    if not row.get("identity"):
        return False
    if (row.get("quality") or 0) < 8:
        return False
    if (row.get("focus") or 0) < FOCUS_EDIT:
        return False
    if min(row.get("width") or 0, row.get("height") or 0) < MIN_EDGE_EDIT:
        return False
    return True


def pass_size_gate(row: dict) -> bool:
    return min(row.get("width") or 0, row.get("height") or 0) >= MIN_EDGE_EDIT


def build_pools(focus: set, gate: str = "edit") -> dict[str, dict]:
    """instance -> {sha256: 池行（含 path/尺寸/质量字段）}；流式单遍扫描。

    gate=edit：完整 edit 适配门（quality/identity/focus/短边）——打标齐备后用；
    gate=size：只短边门——补标未完成时的乱序审计口径，select 时再 join 打标。
    """
    gate_fn = pass_edit_gate if gate == "edit" else pass_size_gate
    pools: dict[str, dict] = defaultdict(dict)
    seen = set()
    with (META_DIR / "metadata.jsonl").open(encoding="utf-8") as f:
        for line in f:
            row = json.loads(line)
            hit = focus.intersection(row.get("instances") or [])
            if not hit or row["sha256"] in seen or not gate_fn(row):
                continue
            seen.add(row["sha256"])
            slim = {
                "sha256": row["sha256"], "ext": row["ext"],
                "path": row.get("path"), "width": row["width"],
                "height": row["height"],
                "quality": row.get("quality"), "focus": row.get("focus"),
                "richness": row.get("richness"),
            }
            for name in hit:
                pools[name][row["sha256"]] = slim
    return dict(pools)


# ---------------------------------------------------------------------------
# VLM 审计
# ---------------------------------------------------------------------------
def encode_image(img_path: Path, max_edge: int) -> str:
    from PIL import Image
    img = Image.open(img_path)
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    w, h = img.size
    if max(w, h) > max_edge:
        k = max_edge / max(w, h)
        img = img.resize((max(1, round(w * k)), max(1, round(h * k))),
                         Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def call_vlm(args, content: list) -> str:
    payload = {
        "model": args.model,
        "stream": False,
        "temperature": 0.0,
        "seed": 42,
        "max_tokens": 1024,
        "messages": [
            {"role": "system", "content": "你是图像场景复杂度审计员，只输出 JSON。"},
            {"role": "user", "content": content},
        ],
    }
    if args.think:
        payload["chat_template_kwargs"] = {"enable_thinking": True}
    else:
        payload["chat_template_kwargs"] = {"enable_thinking": False}
    for attempt in range(3):
        try:
            resp = requests.post(args.endpoint, json=payload,
                                 headers={"Content-Type": "application/json"},
                                 timeout=(10, 300))
            if resp.status_code != 200:
                raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:200]}")
            return resp.json()["choices"][0]["message"]["content"]
        except Exception as e:  # noqa: BLE001
            if attempt == 2:
                raise
            time.sleep(2 * (attempt + 1))
    raise AssertionError("unreachable")


GROUP_ROLES = {"is_subject", "unrelated"}
GROUP_VARIANTS = {"identical", "varied"}


def parse_audit(content: str) -> dict:
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", content.strip())
    best = None
    for m in re.finditer(r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", text, re.S):
        try:
            cand = json.loads(m.group(0))
        except json.JSONDecodeError:
            continue
        if isinstance(cand, dict):
            best = cand
            if "scene_sources" in cand:
                break
    if best is None:
        raise ValueError(f"无 JSON 对象: {content[:150]!r}")
    d = best
    src = d.get("scene_sources") or {}
    clean = {k: max(0, min(2, int(src.get(k, 0)))) for k in SCENE_DIMS}
    d["scene_sources"] = clean
    d["scene_count"] = sum(1 for v in clean.values() if v >= 1)
    d["scene_strong"] = sum(1 for v in clean.values() if v >= 2)
    groups = []
    for g in (d.get("same_class_groups") or []):
        try:
            groups.append({"class": str(g.get("class") or "")[:20],
                           "count": max(2, int(g.get("count") or 2)),
                           "role": g.get("role") if g.get("role") in GROUP_ROLES else "unrelated",
                           "variants": g.get("variants") if g.get("variants") in GROUP_VARIANTS else "identical",
                           "arrangement": str(g.get("arrangement") or "row")[:12]})
        except (TypeError, ValueError):
            continue
    d["same_class_groups"] = groups
    d["referents"] = [str(x)[:20] for x in (d.get("referents") or [])][:6]
    if "same_class_count" not in d or d.get("same_class_count") is None:
        d["same_class_count"] = max((g["count"] for g in groups), default=0)
    else:
        d["same_class_count"] = max(0, int(d["same_class_count"]))
    d["consequence_carriers"] = [c for c in (d.get("consequence_carriers") or [])
                                 if c in CARRIER_ENUM]
    return d


def audit_one(args, inst: str, slim: dict) -> dict:
    img_path = Path(slim["path"])
    if not img_path.is_absolute():
        img_path = DATASET_DIR / img_path
    if not img_path.exists():
        return {"sha256": slim["sha256"], "instance": inst, "error": "blob 缺失"}
    content = [{"type": "text", "text": AUDIT_PROMPT},
               {"type": "image_url",
                "image_url": {"url": encode_image(img_path, args.max_edge)}}]
    try:
        out = parse_audit(call_vlm(args, content))
    except Exception as e:  # noqa: BLE001
        return {"sha256": slim["sha256"], "instance": inst, "error": str(e)[:200]}
    out.update({"sha256": slim["sha256"], "instance": inst,
                "path": slim["path"], "width": slim["width"],
                "height": slim["height"], "quality": slim["quality"],
                "focus": slim["focus"], "richness": slim["richness"],
                "generator": slim.get("generator")})
    return out


def load_done_shas(out_file: Path) -> set:
    done = set()
    if out_file.exists():
        with out_file.open(encoding="utf-8") as f:
            for line in f:
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not row.get("error"):
                    done.add(row["sha256"])
    return done


def run_audit(args) -> None:
    out_file = AUDIT_OUT_SYNTH if args.source == "synth" else AUDIT_OUT
    if args.source == "synth":
        pools = build_synth_pool()
        print(f"合成源池（{GEN_RESULTS.name}）...", flush=True)
    else:
        focus = load_focus_instances()
        print(f"focus 实例 {len(focus)} 个，聚合池中（gate={args.gate}）...",
              flush=True)
        pools = build_pools(focus, gate=args.gate)
    todo = [(inst, slim)
            for inst, pool in sorted(pools.items())
            for sha, slim in sorted(pool.items())]
    done = load_done_shas(out_file)
    tasks = [(i, s) for i, s in todo if s["sha256"] not in done]
    if args.limit:
        tasks = tasks[: args.limit]
    n_pool = len({s["sha256"] for _, s in todo})
    print(f"池内合格图 {n_pool}（{len(pools)} 实例），已完成 {len(done)}，"
          f"本轮待审 {len(tasks)}", flush=True)
    if not tasks:
        return

    EVAL_DIR.mkdir(parents=True, exist_ok=True)
    t0, n_ok, n_err = time.time(), 0, 0
    lock_print = {"last": 0.0}

    def progress(i: int):
        now = time.time()
        if now - lock_print["last"] >= 5 or i == len(tasks):
            lock_print["last"] = now
            rate = (i + 1) / max(now - t0, 1e-6)
            eta = (len(tasks) - i - 1) / max(rate, 1e-6) / 60
            print(f"[{i + 1}/{len(tasks)}] {rate:.2f} img/s, ETA {eta:.0f} min, "
                  f"err {n_err}", flush=True)

    with out_file.open("a", encoding="utf-8") as fout, \
            ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(audit_one, args, inst, slim): (inst, slim)
                for inst, slim in tasks}
        for i, fut in enumerate(as_completed(futs)):
            try:
                row = fut.result()
            except Exception as e:  # noqa: BLE001
                inst, slim = futs[fut]
                row = {"sha256": slim["sha256"], "instance": inst,
                       "error": str(e)[:200]}
            fout.write(json.dumps(row, ensure_ascii=False) + "\n")
            if row.get("error"):
                n_err += 1
            else:
                n_ok += 1
            fout.flush()
            progress(i)
    print(f"\n完成：ok {n_ok}, err {n_err}（error 行下次运行自动重试） -> {out_file}",
          flush=True)


# ---------------------------------------------------------------------------
# 选取与批次校准
# ---------------------------------------------------------------------------
def rank_key(row: dict):
    same = row.get("same_class_count") or 0
    distractor = 2 if same >= 3 else (1 if same >= 2 else 0)
    return (row.get("scene_count", 0), distractor,
            len(row.get("consequence_carriers") or []),
            row.get("quality") or 0,
            min(row.get("width") or 0, row.get("height") or 0))


def run_select(args) -> None:
    by_inst: dict[str, list] = defaultdict(list)
    with AUDIT_OUT.open(encoding="utf-8") as f:
        for line in f:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not row.get("error"):
                by_inst[row["instance"]].append(row)

    n_below, picked_rows = 0, []
    weak_instances = []
    for inst in sorted(by_inst):
        rows = sorted(by_inst[inst], key=rank_key, reverse=True)
        usable = [r for r in rows if r.get("scene_count", 0) >= 2]
        if not usable:
            usable = rows[:1]
            weak_instances.append(inst)
            n_below += 1
        for rank, r in enumerate(usable[: args.top_k], 1):
            out = {"instance": inst, "rank": rank,
                   "sha256": r["sha256"], "path": r["path"],
                   "scene_count": r["scene_count"],
                   "scene_strong": r.get("scene_strong", 0),
                   "scene_sources": r["scene_sources"],
                   "same_class_count": r.get("same_class_count", 0),
                   "consequence_carriers": r.get("consequence_carriers", []),
                   "suitability": r.get("suitability", {}),
                   "quality": r.get("quality"), "width": r["width"],
                   "height": r["height"]}
            picked_rows.append(out)

    with SELECT_OUT.open("w", encoding="utf-8") as f:
        for r in picked_rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    top1 = [r for r in picked_rows if r["rank"] == 1]
    hist = defaultdict(int)
    for r in top1:
        hist[r["scene_count"]] += 1
    suit = defaultdict(int)
    for r in picked_rows:
        s = r.get("suitability") or {}
        if s.get("has_person"):
            suit["has_person"] += 1
        if s.get("has_animal"):
            suit["has_animal"] += 1
        if (s.get("subject_separable") or 0) >= 2:
            suit["separable"] += 1
        if (s.get("background_content") or 0) >= 2:
            suit["bg_content"] += 1
    carrier_hist = defaultdict(int)
    for r in picked_rows:
        for c in r["consequence_carriers"]:
            carrier_hist[c] += 1

    def pct(n, d):
        return round(100 * n / d, 1) if d else None

    n_inst = len(top1)
    rep = {
        "n_instances": n_inst,
        "n_selected": len(picked_rows),
        "topk": args.top_k,
        "top1_scene_hist": dict(sorted(hist.items())),
        "top1_scene_ge3_pct": pct(sum(v for k, v in hist.items() if k >= 3), n_inst),
        "top1_scene_ge4_pct": pct(sum(v for k, v in hist.items() if k >= 4), n_inst),
        "scene_below_floor_instances": {"n": n_below, "sample": weak_instances[:30]},
        "suitability_coverage": {k: f"{v}/{len(picked_rows)} "
                                     f"({pct(v, len(picked_rows))}%)"
                                 for k, v in sorted(suit.items())},
        "carrier_coverage": dict(sorted(carrier_hist.items(),
                                        key=lambda x: -x[1])),
    }
    REPORT_OUT.write_text(json.dumps(rep, ensure_ascii=False, indent=2),
                          encoding="utf-8")
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    print(f"\n选取 -> {SELECT_OUT}\n报表 -> {REPORT_OUT}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="mode")
    p_audit = sub.add_parser("audit", help="VLM 全量复杂度审计（断点续跑）")
    p_audit.add_argument("--workers", type=int, default=12)
    p_audit.add_argument("--limit", type=int, default=0)
    p_audit.add_argument("--gate", choices=["edit", "size"], default="edit",
                         help="湖段池门槛：edit=完整适配门（打标齐备后）；"
                              "size=只短边门（补标前乱序审计）；synth 源忽略")
    p_audit.add_argument("--source", choices=["lake", "synth"], default="lake",
                         help="lake=真实照片段（metadata 池聚合）；"
                              "synth=qwen-image 生成图（gen_results.jsonl 外部清单）")
    p_sel = sub.add_parser("select", help="每实例 top-K 选取与批次校准")
    p_sel.add_argument("--top-k", type=int, default=3)
    for x in (ap, p_audit, p_sel):
        x.add_argument("--endpoint", default=DEFAULT_ENDPOINT)
        x.add_argument("--model", default=DEFAULT_MODEL)
        x.add_argument("--max-edge", type=int, default=1024)
        x.add_argument("--think", action="store_true")
    args = ap.parse_args()
    if args.mode == "audit":
        run_audit(args)
    elif args.mode == "select":
        run_select(args)
    else:
        ap.error("请指定子命令：audit 或 select")


if __name__ == "__main__":
    main()
