"""edit 赛道 v6.1 出题驱动：按 synthesize_prompt_edit_v6.1.md 协议对 focus200
源图出编辑题（每实例 1 题）。

输入（只读）：
    data/focus200/manifest.jsonl              553 行图池（main 371 + 双载体补 93
                                              + hard++++ 89，generator/batch 分桶）
    state/collect/focus_bench_v1.json         200 实例权威顺序
    state/collect/focus1000_instances.json    实体 desc（防幻觉锚定；103/200 有）
    datasets/demiwtg/meta/taxonomy.json       分类路径（mount_map 现算 → 主域）
    data/complexity_audit_synth.jsonl         初审素材（371 旧图；已知偏乐观，
                                              复核见 data/audit_doublecheck.report.json）
    data/focus200/supplement_prompts.jsonl    双载体补图已核实素材（93）
    data/focus200/quality_regen_v1/*          hard++++ 已核实素材（89）
    benchmark/t2i/data/focus1000/gen_prompts.jsonl 等  gen_prompt（题面 caption 角色）

批次设计（2026-09-03 v6.1）：
    默认逐实例继承正式 t2i bench_v1 的 level（同实例同档，整体
    L1=10% / L2=60% / L3=30%）；任一 paired level 缺失即失败。显式关闭对齐时
    才按同配比均衡补位。suite 在各 level 内独立分配，避免 knowledge 与 L3
    完全共线；9 类 edit_type 轮转。
    选图按层级偏好素材：L3 优先 hard++++/双载体补图，L1/L2 优先 main 旧图。

防审计虚报设计（复核结论落地）：
    素材分级注入（初审清单标注高虚报维度/载体类型 + "以图为准"防锚定声明）；
    输出契约 evidence_receipt（题面引用要素逐项凭图回执，source != image 拒收）；
    初审 count 不入题面、大 count 组不作序数定位锚；交叉核验容差按修正比放宽。

产物（data/synth_v61/）：
    questions.jsonl         题库（含 _meta 溯源字段）
    raw/<qid>.json          每题原始 API 响应（断点复用）
    cannot_construct.jsonl  不可出题分支收集（换类型/换图重试后仍卡住的）
    batch_stats_lane*.json  削峰道内入账统计
    run_report.json         批次汇总（含 xcheck 交叉核验复审名单）

用法：
    GALAXY_API_KEY=sk-... python3 benchmark/edit/eval_synthesize.py --dry-run
    GALAXY_API_KEY=sk-... python3 benchmark/edit/eval_synthesize.py --limit 20 \
        --peak-shave --lanes 2
    GALAXY_API_KEY=sk-... python3 benchmark/edit/eval_synthesize.py --peak-shave
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import math
import os
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests

REPO = Path(__file__).resolve().parent.parent.parent          # edit/ -> benchmark/ -> 仓库根
sys.path.insert(0, str(REPO / "data"))

from collect_v2.mount_map import load_mount_map               # noqa: E402

SUB_DIR = Path(__file__).resolve().parent                     # edit/
EVAL_DIR = SUB_DIR / "data"
FOCUS = EVAL_DIR / "focus200"
QRV = FOCUS / "quality_regen_v1"
T2I_F1000 = REPO / "benchmark" / "t2i" / "data" / "focus1000"
PROMPT_FILE = SUB_DIR / "synthesize_prompt_edit_v6.1.md"
MANIFEST = FOCUS / "manifest.jsonl"
BENCH_INSTANCES = REPO / "state" / "collect" / "focus_bench_v1.json"
FOCUS1000_INSTANCES = REPO / "state" / "collect" / "focus1000_instances.json"
TAXONOMY = REPO / "datasets" / "demiwtg" / "meta" / "taxonomy.json"
AUDIT_SYNTH = EVAL_DIR / "complexity_audit_synth.jsonl"
SUPP_PROMPTS = FOCUS / "supplement_prompts.jsonl"
T2I_QUESTIONS = REPO / "benchmark" / "t2i" / "data" / "bench_v1" / "questions.jsonl"

API_URL = "https://token.ai-galaxy.com/v1/chat/completions"
MODEL = "qwen3.7-plus"
TEMPERATURE = 0.4
MAX_TOKENS = 16384
TIMEOUT = (10, 600)

EDIT_TYPES = ("replace", "add", "remove", "adjust", "background",
              "action", "style", "extract", "compose")
LEVELS = ("L1", "L2", "L3")
LEVEL_RANK = {"L1": 0, "L2": 1, "L3": 2}
DEFAULT_MIX = {"L1": 10, "L2": 60, "L3": 30}     # 对齐正式 t2i bench_v1
DEFAULT_KNOWLEDGE_SHARE = 20

# 门槛计数只认表内固定行（协议门槛口径说明④）
SCENE_MENU = ("实体密度", "细节密度", "交互链", "过程时刻", "环境作用",
              "视点剖示", "规约场景", "多实例对比", "纵深层次", "光照时段",
              "动态要素", "多人物编排")
CONSEQUENCE_MENU = ("光影联动", "倒影同步", "接触受力", "计数联动", "材质互动",
                    "液流运动", "磨损老化", "生态反应", "状态指示", "气象温度",
                    "空间补全", "纹理透视")
SPECIAL_OBLIGATION_MENU = ("风格笔触", "风格色彩", "风格明暗", "风格材质",
                           "主体完整", "抠图边界", "白底纯净", "杂物清除",
                           "背景语义", "背景透视", "背景景深", "前景边界")
DOMAINS_MENU = ("政治、法律与社会制度", "地理与地点", "宗教与信仰", "民族、语言与文化",
                "人造物体", "动物", "真菌与微生物", "植物", "人物与人体", "行为动作",
                "食物", "建筑与基础设施", "交通工具", "自然景观", "场景", "节日与符号",
                "属性与状态", "材料与物质", "时间数量与度量", "文字与信息图形", "声音",
                "文化艺术与媒介", "知识与学科", "组织机构与社会事件", "体育与游戏",
                "历史与时代", "品牌与产品", "数字与互联网文化", "医学与健康")
# v6.1 类型无关的最低结构门槛：结果义务/保持/负向/场景/知识类别/弱项。
# style/extract/background 的“结果义务”按协议用风格特征、抠图边界、背景边界
# 等同类可视判定替代物理后果，避免为凑数虚构源图载体。
GATES = {"L1": (2, 2, 1, 1, 1, 1),
         "L2": (3, 3, 2, 2, 2, 2),
         "L3": (4, 4, 2, 3, 3, 3)}
TARGETING_MENU = ("唯一属性直指", "属性组合", "序数定位", "方位定位", "部位定位",
                  "关系定位", "全局范围")
PRESERVATION_MENU = ("无关区域保持", "计数布局保持", "主体身份保持", "姿态视角保持",
                     "全局光照保持", "风格媒介保持", "色调氛围保持", "物理属性保持")
PREMISE_MENU = ("定位前提", "改动规定前提", "保持范围前提", "环境场所前提",
                "时间阶段前提", "数量编组前提", "视角取景前提")
KNOWLEDGE_MENU = ("概念自身结构", "状态与阶段", "环境交互", "可组合关系",
                  "规约与典故", "物理规律", "化学规律", "生物规律", "地理气象",
                  "天文对应")
HOP_MENU = ("过程-因果", "规约-标准", "发育-生长", "功能-结构", "物理规律",
            "文化-规制", "关系（组合）", "分类-辨识", "量-守恒", "序-时序",
            "原理-推演", "化学-反应", "生态-互动")
WEAK_MENU = ("过度编辑", "改动遗漏", "后果缺失", "定位错改", "身份漂移", "计数漂移",
             "光影不一致", "倒影失同步", "接触悬浮", "字样崩坏", "边缘伪影",
             "材质失真", "姿态崩坏", "背景重绘", "构图漂移", "色调漂移")
RISKY_CARRIER_PAT = ("影子", "反光", "倒影", "映像")     # 复核实证高虚报载体类型


# ---------------------------------------------------------------------------
# 输入装载
# ---------------------------------------------------------------------------
def read_jsonl(path: Path) -> list[dict]:
    rows = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def load_inputs() -> dict:
    manifest = read_jsonl(MANIFEST)
    bench = json.loads(BENCH_INSTANCES.read_text(encoding="utf-8"))
    # desc：focus1000 优先，缺的回退 meta instances.json
    desc: dict[str, str] = {}
    for rec in json.loads(FOCUS1000_INSTANCES.read_text(encoding="utf-8"))["instances"]:
        if rec.get("desc"):
            desc[rec["name"]] = rec["desc"]
    meta_inst = REPO / "datasets" / "demiwtg" / "meta" / "instances.json"
    if meta_inst.exists():
        for rec in json.loads(meta_inst.read_text(encoding="utf-8"))["instances"]:
            desc.setdefault(rec["name"], rec.get("desc") or "")
    mounts = load_mount_map(str(TAXONOMY))
    # 初审素材（371 旧图，sha 键）
    audit = {r["sha256"]: r for r in read_jsonl(AUDIT_SYNTH) if r.get("sha256")}
    # hard++++ 已核实素材（sha 键；review 按 candidate_id 关联）
    qrv_rev: dict[str, dict] = {}
    for sh in "abc":
        rp = QRV / f"agent_{sh}" / "review_r2.jsonl"
        if rp.exists():
            qrv_rev.update({r["candidate_id"]: r for r in read_jsonl(rp)})
    qrv: dict[str, dict] = {}
    for r in read_jsonl(QRV / "accepted_results.jsonl"):
        rev = qrv_rev.get(r["candidate_id"], {})
        qrv[r["sha256"]] = {"row": r, "review": rev}
    # 双载体补图素材（prompt_id 键）
    supp = {r["prompt_id"]: r for r in read_jsonl(SUPP_PROMPTS)}
    # gen_prompt 作 caption（题面生图原文，协议 caption 角色：可能含幻觉须对图核实）
    gp: dict[str, str] = {}
    gen_results = T2I_F1000 / "gen_results.jsonl"
    gen_prompts = T2I_F1000 / "gen_prompts.jsonl"
    if gen_results.exists() and gen_prompts.exists():
        pid_by_sha = {r["sha256"]: r["prompt_id"] for r in read_jsonl(gen_results)
                      if r.get("sha256")}
        ptext = {r["prompt_id"]: r.get("gen_prompt") or ""
                 for r in read_jsonl(gen_prompts)}
        for sha, pid in pid_by_sha.items():
            gp[sha] = ptext.get(pid, "")
    for r in read_jsonl(QRV / "accepted_results.jsonl"):
        gp[r["sha256"]] = r.get("gen_prompt") or ""
    for r in read_jsonl(SUPP_PROMPTS):
        pass
    supp_res = FOCUS / "supplement_results.jsonl"
    if supp_res.exists():
        for r in read_jsonl(supp_res):
            pr = supp.get(r.get("prompt_id"))
            if pr:
                gp[r["sha256"]] = pr.get("gen_prompt") or ""
    # 正式 t2i 题库与本批次共享同一 200 实例；按实体名继承 level，形成真正的
    # paired 难度对照，而不是只在总体比例上碰巧一致。
    t2i_levels: dict[str, str] = {}
    if T2I_QUESTIONS.exists():
        for q in read_jsonl(T2I_QUESTIONS):
            name, lvl = str(q.get("_query_label") or ""), str(q.get("level") or "")
            if not name or lvl not in LEVELS:
                continue
            if name in t2i_levels and t2i_levels[name] != lvl:
                raise ValueError(f"t2i 同一实体 level 冲突：{name}: {t2i_levels[name]} / {lvl}")
            t2i_levels[name] = lvl
    return {"manifest": manifest, "bench": bench, "desc": desc, "mounts": mounts,
            "audit": audit, "qrv": qrv, "supp": supp, "gen_prompt": gp,
            "t2i_levels": t2i_levels}


def main_domain(instance: str, mounts: dict) -> str:
    for p in mounts.get(instance, []):
        segs = [s.strip() for s in p.split("/")]
        if len(segs) >= 2:
            return segs[1]
    return ""


# ---------------------------------------------------------------------------
# 素材块（分级注入 + 防锚定）
# ---------------------------------------------------------------------------
def material_lines(row: dict, audit: dict, qrv: dict, supp: dict) -> list[str]:
    sha = row["sha256"]
    lines: list[str] = []
    if sha in qrv:
        r, rev = qrv[sha]["row"], qrv[sha]["review"]
        acc = r.get("acceptance") or {}
        lines.append("已核实素材（hard++++ 批次，逐图复核过，可信度高于初审）：")
        lines.append(f"- 后果载体：{'、'.join(rev.get('carriers_verified') or row.get('carriers_required') or [])}")
        lines.append(f"- 参照物：{'、'.join(acc.get('unique_referents') or [])}")
        sc = acc.get("same_class_count") or {}
        obs = rev.get("observed_count")
        lines.append(f"- 同类组：目标 {sc.get('min', '?')}~{sc.get('max', '?')} 个完整可数实例"
                     + (f"，复核观测 {obs} 个" if obs else ""))
        lines.append(f"- 场景复杂度设计目标：{acc.get('complexity_dimensions', {}).get('target', '?')} 个独立维度")
    elif row.get("batch") == "dual_carrier_supplement":
        pr = supp.get(row.get("prompt_id") or "")
        if pr:
            cl = pr.get("checklist") or {}
            lines.append("已核实素材（双载体补生成批次，生成后核验过）：")
            lines.append(f"- 后果载体：{'、'.join(pr.get('carriers_required') or [])}")
            lines.append(f"- 参照物：{'、'.join(cl.get('referents') or [])}")
            if cl.get("visible_variants"):
                lines.append(f"- 同类变体：{'、'.join(cl['visible_variants'])}")
        else:
            # 历史 supplement_results 有 93 行，而逐图 prompt 仅保留了少量记录。
            # 空素材块会诱导模型把 batch 名当成“已核实”；明确降级为无素材。
            lines.append("（本补图没有可追溯的逐图核实记录；不得从批次名推断任何图面事实，全部自行看图）")
    elif sha in audit:
        a = audit[sha]
        ss = a.get("scene_sources") or {}
        dims = [f"{k}{'+' if v >= 2 else ''}" for k, v in ss.items() if v]
        groups = [f"{g.get('class')}×{g.get('count')}({g.get('role')})"
                  for g in a.get("same_class_groups") or []]
        lines.append("初审素材（模型审计产出，已知含 10%~40% 虚项，仅供定位参考）：")
        if dims:
            lines.append(f"- 场景维度（+号=自报强项，环境作用/过程时刻为高虚报维度，"
                         f"引用须给图面证据）：{'、'.join(dims)}")
        if groups:
            lines.append(f"- 同类组（count 与 role 常有误报，计数不得直接入题面）：{'、'.join(groups)}")
        if a.get("referents"):
            lines.append(f"- 参照物（复核实证最可靠字段）：{'、'.join(a['referents'])}")
        if a.get("consequence_carriers"):
            cs = a["consequence_carriers"]
            risky = [c for c in cs if any(p in c for p in RISKY_CARRIER_PAT)]
            tag = "（影子/反光/倒影类高虚报，须像素级证据才可引用）" if risky else ""
            lines.append(f"- 后果载体（约 11% 虚报{tag}）：{'、'.join(cs)}")
        if a.get("summary"):
            lines.append(f"- 初审摘要：{a['summary']}")
    else:
        lines.append("（本图无审计素材，全部要素自行对图发现）")
    return lines


MATERIAL_DISCLAIMER = (
    "【辅助信息纪律】原图像素是源场景唯一事实。实体 desc 可提供世界知识，caption、"
    "分类路径和素材清单只提供待核假设；它们都不能证明对象、数量、方位、文字、影子、"
    "倒影、接触点或环境在图中存在。任何冲突以图为准；看不清即视为不可用，不猜、不补。"
    "初审计数不得直接进题面（要计数须自己数图）；count>10 的组或密集人群不得作为"
    "「第 N 个」序数定位锚。"
)


# ---------------------------------------------------------------------------
# 批次构造
# ---------------------------------------------------------------------------
def level_slots(n: int, mix: dict) -> list[str]:
    """百分比配比 → 最大余数计数 → 均衡交错槽位（各层尽量均匀铺开）。"""
    total = sum(mix.values())
    want = {k: mix[k] * n / total for k in mix}
    counts = {k: int(math.floor(v)) for k, v in want.items()}
    rema = n - sum(counts.values())
    for k in sorted(want, key=lambda k: -(want[k] - math.floor(want[k])))[:rema]:
        counts[k] += 1
    slots, got = [], {k: 0 for k in counts}
    for _ in range(n):
        best = max(counts, key=lambda k: counts[k] * (len(slots) + 1) / n - got[k])
        slots.append(best)
        got[best] += 1
    return slots


def _largest_remainder_counts(group_sizes: dict[str, int], share: int) -> dict[str, int]:
    """按组分配全局 share%，使 suite 与 level 独立且总量确定。"""
    total_n = sum(group_sizes.values())
    target = round(total_n * share / 100)
    wants = {k: n * share / 100 for k, n in group_sizes.items()}
    out = {k: min(group_sizes[k], int(math.floor(v))) for k, v in wants.items()}
    left = target - sum(out.values())
    order = sorted(group_sizes, key=lambda k: (-(wants[k] - math.floor(wants[k])), k))
    for k in order:
        if left <= 0:
            break
        if out[k] < group_sizes[k]:
            out[k] += 1
            left -= 1
    return out


def suite_slots(levels: list[str], knowledge_share: int) -> list[str]:
    """在每个 level 内均匀铺开 knowledge，避免 suite=level 的统计混杂。"""
    sizes = {lvl: levels.count(lvl) for lvl in LEVELS}
    wants = _largest_remainder_counts(sizes, knowledge_share)
    knowledge_positions: dict[str, set[int]] = {}
    for lvl in LEVELS:
        n, k = sizes[lvl], wants[lvl]
        # k 个位置放在 k+1 个间隔之间；小样本也不会全部挤在批尾。
        pos = {max(0, min(n - 1, round((j + 1) * (n + 1) / (k + 1)) - 1))
               for j in range(k)} if n and k else set()
        # round 极端碰撞时确定性补齐。
        for cand in range(n):
            if len(pos) >= k:
                break
            pos.add(cand)
        knowledge_positions[lvl] = pos
    seen = {lvl: 0 for lvl in LEVELS}
    out = []
    for lvl in levels:
        idx = seen[lvl]
        out.append("knowledge" if idx in knowledge_positions[lvl] else "basic")
        seen[lvl] += 1
    return out


def image_candidates(rows: list[dict], level: str) -> list[dict]:
    prefs = ("quality_regen_v1", "dual_carrier_supplement", "main") if level == "L3" \
        else ("main", "dual_carrier_supplement", "quality_regen_v1")
    out: list[dict] = []
    for b in prefs:
        out.extend(r for r in rows if (r.get("batch") or "main") == b)
    return out


def build_jobs(inputs: dict, limit: int, mix: dict, *, align_t2i_levels: bool = True,
               knowledge_share: int = DEFAULT_KNOWLEDGE_SHARE) -> list[dict]:
    manifest = inputs["manifest"]
    by_inst: dict[str, list[dict]] = {}
    for r in manifest:
        by_inst.setdefault(r["instance"], []).append(r)
    bench = inputs["bench"][:limit] if limit else inputs["bench"]
    fallback_slots = level_slots(len(bench), mix)
    t2i_levels = inputs.get("t2i_levels") or {}
    if align_t2i_levels:
        missing = [name for name in bench if name not in t2i_levels]
        if missing:
            raise ValueError(f"--align-t2i-levels 开启但 {len(missing)} 个实体缺正式 t2i level："
                             + "、".join(missing[:5]))
        levels = [t2i_levels[name] for name in bench]
    else:
        levels = fallback_slots
    suites = suite_slots(levels, knowledge_share)
    jobs = []
    for i, name in enumerate(bench):
        lvl = levels[i]
        rows = by_inst.get(name) or []
        if not rows:
            raise ValueError(f"{name}: manifest 无图；不能静默缩短确定性 paired 批次")
        imgs = image_candidates(rows, lvl)
        jobs.append({
            "qid": f"e{i + 1:03d}", "seq": i, "instance": name,
            "level": lvl, "suite": suites[i],
            "edit_type": EDIT_TYPES[i % len(EDIT_TYPES)],
            "alt_type": EDIT_TYPES[(i + 3) % len(EDIT_TYPES)],
            "images": imgs,
            "main_domain": main_domain(name, inputs["mounts"]),
            "level_source": "t2i_bench_v1" if align_t2i_levels else "mix_slots",
        })
    return jobs


# ---------------------------------------------------------------------------
# user 消息
# ---------------------------------------------------------------------------
def build_text(job: dict, img_row: dict, edit_type: str, inputs: dict,
               adjustment: str) -> list:
    name = job["instance"]
    mounts = inputs["mounts"].get(name) or ["（未挂载）"]
    desc = inputs["desc"].get(name, "")
    cap = inputs["gen_prompt"].get(img_row["sha256"], "")
    if len(cap) > 1500:
        cap = cap[:1500] + "…（截断）"
    mat = "\n".join(material_lines(img_row, inputs["audit"], inputs["qrv"], inputs["supp"]))
    parts = [
        f"样本编号：{job['qid']}",
        f"【题目编号（qid）】{job['qid']}",
        "【先看图】请先独立检查随附原图，再读取以下文字辅助。原图像素是源场景"
        "唯一事实；文字不能把看不见或看不清的东西变成事实。",
        f"【实体名】{name}",
        f"【实体知识（只可定义概念/目标态，不可证明图中存在）】{desc or '（无 desc）'}",
        f"【caption（高风险待核假设，不可作图面证据）】{cap or '（无）'}",
        f"【批次主域】{job['main_domain'] or '（未知）'}"
        "（分类路径以 demiwtg 为根，主域是其后的第二段，不是字面首段）",
        "【分类路径】\n" + "\n".join(mounts[:3]),
        f"【目标编辑类型】{edit_type}（必须出该类型；与图内容不适配时走不可出题分支，不得硬出）",
        f"【套系】{job['suite']}"
        + ("（知识编辑套：题面给定原因/状态/规则目标，未明说的必要视觉结果由公认知识唯一约束）"
           if job["suite"] == "knowledge" else "（直接编辑套）"),
        f"【目标层级】{job['level']}（输出 level 必须与此完全一致，以保持与 T2I 的逐实例 paired 分布；"
        "图确实无法支撑时走不可出题分支）",
        "【素材清单】\n" + mat,
        MATERIAL_DISCLAIMER,
        "【证据回执再强调】题面和 reasoning 使用的每个源图目标、定位锚、干扰项、"
        "后果载体与具体保持对象，都须逐项进入 evidence_receipt；每项含 element、anchor、"
        "used_in、source=\"image\"、confidence=\"high\"。新增目标态与图外公认知识不冒充源图证据。",
        adjustment,
        "请按出题指令对这张原图出 1 道题，严格执行交题前自查清单，"
        "只输出一个严格 JSON 对象，不要任何其他文字。",
    ]
    return [{"type": "text", "text": "\n".join(p for p in parts if p)}]


# ---------------------------------------------------------------------------
# 离线工具：批次计划发射 / 机审校验 / 削峰调度（供 codex 等外部出题者使用，
# 不调 API：codex 用其内置模型按 plan 逐题生成，用 validate 验收、dispatch 取禁令）
# ---------------------------------------------------------------------------
ATTEMPT_PLANS = lambda job: [                      # noqa: E731  换位补额尝试序列
    (0, job["edit_type"]), (0, job["alt_type"]),
    (1, job["edit_type"]),
]


def cmd_emit_plan(args, inputs, jobs) -> None:
    out_dir = Path(args.out_dir) if args.out_dir else EVAL_DIR / "synth_v61"
    out_dir.mkdir(parents=True, exist_ok=True)
    plan_path = out_dir / "plan.jsonl"
    n_text = 0
    with plan_path.open("w", encoding="utf-8") as f:
        for job in jobs:
            texts = []
            for img_idx, etype in ATTEMPT_PLANS(job):
                if img_idx >= len(job["images"]):
                    continue
                t = build_text(job, job["images"][img_idx], etype, inputs,
                               "{ADJUSTMENT}")[0]["text"]
                im = job["images"][img_idx]
                texts.append({
                    "image_index": img_idx, "edit_type": etype, "text": t,
                    "expected_meta": {
                        "task": "edit", "_protocol": "edit-v6.1-image-first",
                        "_job_qid": job["qid"], "_instance": job["instance"],
                        "_sha256": im["sha256"], "_file": im["file"],
                        "_target_level": job["level"], "_suite": job["suite"],
                        "_edit_type": etype, "_generator": im["generator"],
                        "_batch": im.get("batch") or "main",
                        "_sample_image": f"focus200/{im['file']}",
                        "difficulty": job["level"],
                    },
                })
                n_text += 1
            row = {
                "qid": job["qid"], "seq": job["seq"], "instance": job["instance"],
                "level": job["level"], "suite": job["suite"],
                "protocol_version": "edit-v6.1-image-first",
                "level_source": job["level_source"],
                "edit_type": job["edit_type"], "alt_type": job["alt_type"],
                "main_domain": job["main_domain"],
                "images": [{"file": r["file"], "sha256": r["sha256"],
                            "batch": r.get("batch") or "main",
                            "generator": r["generator"]}
                           for r in job["images"][:2]],
                "texts": texts,
            }
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    mix_counts: dict[str, int] = {}
    for j in jobs:
        mix_counts[j["level"]] = mix_counts.get(j["level"], 0) + 1
    print(f"plan 发射：{len(jobs)} 题 / {n_text} 个尝试文本 -> {plan_path}\n"
          f"  层级 {mix_counts} · knowledge {sum(1 for j in jobs if j['suite'] == 'knowledge')}"
          f"（按 level 分层独立铺开）· level_source={jobs[0]['level_source']}"
          " · images 只留前 2 张候选")


def load_plan(plan_path: Path) -> dict[str, dict]:
    rows = read_jsonl(plan_path)
    out: dict[str, dict] = {}
    for r in rows:
        qid = str(r.get("qid") or "")
        if not qid:
            raise ValueError(f"plan 行缺 qid：{plan_path}")
        if qid in out:
            raise ValueError(f"plan qid 重复：{qid}")
        if r.get("protocol_version") != "edit-v6.1-image-first":
            raise ValueError(f"plan {qid} protocol_version 不符")
        if r.get("level") not in LEVELS or r.get("suite") not in {"basic", "knowledge"}:
            raise ValueError(f"plan {qid} level/suite 非法")
        if r.get("edit_type") not in EDIT_TYPES or r.get("alt_type") not in EDIT_TYPES:
            raise ValueError(f"plan {qid} edit_type/alt_type 非法")
        images, texts = r.get("images"), r.get("texts")
        if not isinstance(images, list) or not images or not isinstance(texts, list) or not texts:
            raise ValueError(f"plan {qid} 缺 images/texts")
        legal = set()
        for t in texts:
            idx, etype = t.get("image_index"), t.get("edit_type")
            if not isinstance(idx, int) or not 0 <= idx < len(images) or etype not in EDIT_TYPES:
                raise ValueError(f"plan {qid} 含非法 attempt")
            if (idx, etype) in legal:
                raise ValueError(f"plan {qid} attempt 重复：{idx}/{etype}")
            legal.add((idx, etype))
            if not str(t.get("text") or "").strip() or "{ADJUSTMENT}" not in t["text"]:
                raise ValueError(f"plan {qid} attempt 缺文本或调整标记")
        out[qid] = r
    seqs = [r.get("seq") for r in rows]
    if seqs != list(range(len(rows))):
        raise ValueError(f"plan seq 必须按 0..{len(rows) - 1} 连续有序")
    expected_qids = [f"e{i + 1:03d}" for i in range(len(rows))]
    if [r["qid"] for r in rows] != expected_qids:
        raise ValueError("plan qid 必须与有序 seq 一一对应")
    return out


def plan_as_job(p: dict) -> dict:
    return {"qid": p["qid"], "instance": p["instance"], "level": p["level"],
            "suite": p["suite"], "edit_type": p["edit_type"],
            "alt_type": p["alt_type"], "main_domain": p.get("main_domain")}


def cmd_dispatch(args) -> None:
    """按已完成题的实时分布计算下一题的削峰/补峰调整文本（stdout 取走即用）。"""
    plan = load_plan(Path(args.plan))
    p = plan.get(args.dispatch)
    if not p:
        sys.exit(f"qid {args.dispatch} 不在 plan 中")
    records = []
    seen_qids: set[str] = set()
    if args.questions and Path(args.questions).exists():
        for q in read_jsonl(Path(args.questions)):
            done_qid = str(q.get("_job_qid") or q.get("qid") or "")
            if done_qid in seen_qids:
                sys.exit(f"--questions qid 重复，削峰统计不可信：{done_qid}")
            seen_qids.add(done_qid)
            if str(q.get("status") or "constructed") != "constructed":
                continue
            records.append({
                "status": "constructed",
                "consequence_types": [str(x) for x in (q.get("consequence_types") or [])],
                "scene_types": [str(x) for x in (q.get("scene_types") or [])],
                "knowledge_domains": [str(x) for x in (q.get("knowledge_domains") or [])],
                "level": str(q.get("level") or ""),
            })
    done = [r for r in records if r["status"] == "constructed"]
    bans, avoid, sol_c, sol_s = shape_dispatch(done)
    lane_cons: dict[str, int] = {}
    for r in done:
        for c in r["consequence_types"]:
            lane_cons[c] = lane_cons.get(c, 0) + 1
            if r.get("level") in LEVELS:
                key = f"{r['level']}::{c}"
                lane_cons[key] = lane_cons.get(key, 0) + 1
    text = adjustment_text(bans, avoid, sol_c, sol_s, plan_as_job(p), lane_cons)
    print(text if text.strip() else "（无动态调整）")


def _plan_attempt(p: dict, q: dict, kind: str) -> tuple[int, dict, str] | None:
    """返回命中的 (image_index, image_row, edit_type)，并核验确定性尝试组合。"""
    sha = str(q.get("_sha256") or "")
    hits = [(i, im) for i, im in enumerate(p.get("images") or [])
            if str(im.get("sha256") or "") == sha]
    if len(hits) != 1:
        return None
    idx, im = hits[0]
    etype = str(q.get("_attempt_type") if kind == "cannot_construct"
                else q.get("edit_type") or "")
    legal = {(int(t["image_index"]), str(t["edit_type"])) for t in p.get("texts") or []}
    if (idx, etype) not in legal:
        return None
    return idx, im, etype


def _meta_errors(q: dict, p: dict, im: dict, etype: str,
                 kind: str) -> list[str]:
    errs = []
    expected = {
        "task": "edit", "_protocol": "edit-v6.1-image-first",
        "_job_qid": p["qid"], "_instance": p["instance"],
        "_sha256": im["sha256"], "_file": im["file"],
        "_target_level": p["level"], "_suite": p["suite"],
        "_generator": im["generator"], "_batch": im.get("batch") or "main",
        "_sample_image": f"focus200/{im['file']}",
    }
    if kind == "questions":
        expected["_edit_type"] = etype
        expected["difficulty"] = p["level"]
    else:
        expected["_attempt_type"] = etype
    for key, value in expected.items():
        if q.get(key) != value:
            errs.append(f"{key}={q.get(key)!r}，应为 {value!r}")
    return errs


def _adjustment_errors(q: dict) -> list[str]:
    if not isinstance(q.get("_adjustment"), str):
        return ["缺 _adjustment 字符串，无法审计削峰禁令"]
    match = re.search(r"不得使用的后果传播类型：([^；。]+)", q["_adjustment"])
    banned = {x.strip() for x in match.group(1).split("、")} if match else set()
    used_banned = sorted(banned & set(q.get("consequence_types") or []))
    return [f"违反 _adjustment 后果硬禁令：{used_banned}"] if used_banned else []


def cmd_validate(args) -> None:
    """离线机审；可用 --strict 将所有门槛 WARN 升为 REJECT。"""
    plan = load_plan(Path(args.plan))
    audit_by_sha = {r["sha256"]: r for r in read_jsonl(AUDIT_SYNTH)
                    if r.get("sha256")} if AUDIT_SYNTH.exists() else {}
    targets = []
    if args.validate:
        targets.append(("questions", Path(args.validate)))
    if args.validate_cc:
        targets.append(("cannot_construct", Path(args.validate_cc)))
    if not targets:
        sys.exit("--validate / --validate-cc 至少给一个")
    n_pass = n_warn = n_reject = 0
    report = {"reject": [], "warn": {}, "xcheck": [], "coverage": {}}
    terminal: dict[str, str] = {}

    def reject_one(kind: str, qid: str, why) -> None:
        nonlocal n_reject
        n_reject += 1
        report["reject"].append({"qid": qid, "kind": kind, "why": why})
        print(f"[{kind}] REJECT {qid}: {str(why)[:240]}")

    if args.expected_count and len(plan) != args.expected_count:
        reject_one("plan", "<batch>",
                   f"plan 行数 {len(plan)}，应为 --expected-count={args.expected_count}")

    for kind, path in targets:
        if not path.exists():
            reject_one(kind, "<file>", f"文件不存在：{path}")
            continue
        for q in read_jsonl(path):
            qid = str(q.get("_job_qid") or q.get("qid") or "")
            if qid in terminal:
                reject_one(kind, qid or "<empty>", f"qid 重复/跨 questions 与 cc 重叠（先见于 {terminal[qid]}）")
                continue
            terminal[qid] = kind
            p = plan.get(qid)
            if not p:
                reject_one(kind, qid or "<empty>", "不在 plan 中")
                continue
            attempt = _plan_attempt(p, q, kind)
            if attempt is None:
                reject_one(kind, qid, "图与 edit_type 不是 plan.texts 中的合法尝试组合")
                continue
            _, im, etype = attempt
            meta_errs = _meta_errors(q, p, im, etype, kind)
            if meta_errs:
                reject_one(kind, qid, meta_errs)
                continue
            if kind == "cannot_construct":
                errs = []
                required = {
                    "task", "qid", "status", "edit_instruction", "edit_type", "suite", "level",
                    "level_reason", "targeting_types", "consequence_types",
                    "special_obligation_types", "preservation_types", "premise_types",
                    "hop_types", "scene_types", "knowledge_categories", "knowledge_domains",
                    "weak_points", "product_checks", "evidence_audit", "evidence_receipt",
                    "reasoning", "notes", "cannot_reason_code",
                }
                missing_fields = sorted(required - set(q))
                if missing_fields:
                    errs.append(f"缺规定字段：{missing_fields}")
                if str(q.get("qid") or "") != qid:
                    errs.append("qid 回显不符")
                if str(q.get("status") or "") != "cannot_construct":
                    errs.append("status 必须为 cannot_construct")
                if not str(q.get("notes") or "").strip():
                    errs.append("notes 必填")
                if q.get("edit_type") != etype or q.get("suite") != p["suite"]:
                    errs.append("edit_type/suite 回显不符")
                cannot_codes = {"target_not_visible", "target_ambiguous", "type_incompatible",
                                "insufficient_evidence", "insufficient_level_support",
                                "knowledge_not_unique", "ban_conflict"}
                if q.get("cannot_reason_code") not in cannot_codes:
                    errs.append("cannot_reason_code 不在封闭枚举")
                for key in ("edit_instruction", "reasoning", "level"):
                    if str(q.get(key) or ""):
                        errs.append(f"{key} 在 cannot_construct 中必须为空字符串")
                for key in ("targeting_types", "consequence_types", "special_obligation_types",
                            "preservation_types", "premise_types", "hop_types", "scene_types",
                            "knowledge_categories", "knowledge_domains", "weak_points",
                            "product_checks", "evidence_receipt"):
                    if q.get(key) != []:
                        errs.append(f"{key} 在 cannot_construct 中必须为 []")
                if q.get("level_reason") != {}:
                    errs.append("level_reason 在 cannot_construct 中必须为 {}")
                empty_ea = {"visible_facts": [], "edit_targets": [], "distractors": [],
                            "uncertain_or_rejected": []}
                if q.get("evidence_audit") != empty_ea:
                    errs.append("evidence_audit 在 cannot_construct 中必须为规定空对象")
                if errs:
                    reject_one("cc", qid, errs)
                else:
                    n_pass += 1
                continue
            if str(q.get("status") or "constructed") == "cannot_construct":
                reject_one(kind, qid, "cannot_construct 不得混入 questions 文件")
                continue
            job = plan_as_job(p)
            job["edit_type"] = etype
            warns, reject = audit_v61_edit(q, job)
            adjustment_errs = _adjustment_errors(q)
            warns.extend(adjustment_errs)
            reject = reject or any("违反" in e for e in adjustment_errs)
            if args.strict and warns:
                reject = True
            if reject:
                reject_one(kind, qid, warns[:8])
                continue
            if warns:
                print(f"[questions] WARN  {qid}: {'; '.join(warns)[:240]}")
                n_warn += 1
                for w in warns:
                    key = w.split("：")[0][:40]
                    report["warn"][key] = report["warn"].get(key, 0) + 1
            else:
                n_pass += 1
            jac = xcheck_scene(q, audit_by_sha.get(q.get("_sha256")))
            if jac is not None and jac < 0.34:
                report["xcheck"].append({"qid": qid, "jaccard": round(jac, 2)})

    missing = sorted(set(plan) - set(terminal))
    if args.require_complete and missing:
        reject_one("coverage", "<batch>", f"缺 {len(missing)} 个终态：{missing[:20]}")
    report["coverage"] = {"plan": len(plan), "terminal": len(terminal),
                          "missing": missing, "duplicates_or_invalid": n_reject}
    print(f"\nvalidate 汇总：PASS {n_pass} · WARN {n_warn} · REJECT {n_reject}"
          + (f" · xcheck 复审 {len(report['xcheck'])} 题" if report["xcheck"] else "")
          + f" · 终态 {len(terminal)}/{len(plan)}")
    rep_path = (Path(args.out_dir) / "validation_report.json" if args.out_dir
                else Path("validation_report.json"))
    rep_path.parent.mkdir(parents=True, exist_ok=True)
    rep_path.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"报告 -> {rep_path}")
    if n_reject:
        sys.exit(1)


def _write_jsonl_atomic(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".part")
    tmp.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
                   encoding="utf-8")
    tmp.replace(path)


def cmd_assemble_drafts(args) -> None:
    """把并行草稿按 plan 顺序单点注入动态调整与隐藏元数据，并严格验收。"""
    plan = load_plan(Path(args.plan))
    draft_dir = Path(args.assemble_drafts)
    paths = sorted(draft_dir.glob("*.jsonl"))
    if not paths:
        sys.exit(f"--assemble-drafts 下没有 JSONL：{draft_dir}")
    by_qid: dict[str, dict] = {}
    source_by_qid: dict[str, Path] = {}
    for path in paths:
        for q in read_jsonl(path):
            qid = str(q.get("qid") or q.get("_job_qid") or "")
            if not qid:
                sys.exit(f"草稿 qid 缺失（{path}）")
            if qid in by_qid and not path.name.startswith("revision_"):
                sys.exit(f"草稿 qid 重复：{qid!r}（{source_by_qid[qid]} / {path}）")
            by_qid[qid] = q
            source_by_qid[qid] = path
    extra = sorted(set(by_qid) - set(plan))
    missing = sorted(set(plan) - set(by_qid))
    if extra or missing:
        sys.exit(f"草稿覆盖与 plan 不一致：extra={extra[:10]} missing={missing[:10]}")

    questions: list[dict] = []
    cannot: list[dict] = []
    records: list[dict] = []
    failures: list[str] = []
    for p in plan.values():
        q = dict(by_qid[p["qid"]])
        kind = "cannot_construct" if q.get("status") == "cannot_construct" else "questions"
        attempt = _plan_attempt(p, q, kind)
        if attempt is None:
            failures.append(f"{p['qid']}: 图/edit_type 不是 plan 合法 attempt")
            continue
        _, im, etype = attempt
        done = [r for r in records if r.get("status") == "constructed"]
        bans, avoid, sol_c, sol_s = shape_dispatch(done)
        lane_cons: dict[str, int] = {}
        for r in done:
            for c in r.get("consequence_types") or []:
                lane_cons[c] = lane_cons.get(c, 0) + 1
                if r.get("level") in LEVELS:
                    key = f"{r['level']}::{c}"
                    lane_cons[key] = lane_cons.get(key, 0) + 1
        adj = adjustment_text(bans, avoid, sol_c, sol_s, plan_as_job(p), lane_cons)
        common_meta = {
            "task": "edit", "_job_qid": p["qid"], "_instance": p["instance"],
            "_sha256": im["sha256"], "_file": im["file"],
            "_sample_image": f"focus200/{im['file']}",
            "_batch": im.get("batch") or "main", "_target_level": p["level"],
            "_suite": p["suite"], "_generator": im["generator"],
            "_protocol": "edit-v6.1-image-first", "_adjustment": adj,
        }
        q.update(common_meta)
        if kind == "questions":
            q.update({"difficulty": p["level"], "_edit_type": etype})
            job = plan_as_job(p)
            job["edit_type"] = etype
            warns, reject = audit_v61_edit(q, job)
            adj_errs = _adjustment_errors(q)
            warns.extend(adj_errs)
            reject = reject or any("违反" in e for e in adj_errs)
            if reject or warns:
                failures.append(f"{p['qid']}: " + "; ".join(warns[:8]))
                continue
            questions.append(q)
            records.append({
                "qid": p["qid"], "status": "constructed",
                "consequence_types": q.get("consequence_types") or [],
                "scene_types": q.get("scene_types") or [],
                "knowledge_domains": q.get("knowledge_domains") or [],
                "level": q.get("level"),
            })
        else:
            q["_attempt_type"] = etype
            cannot.append(q)
            records.append({"qid": p["qid"], "status": "cannot_construct"})
    if failures:
        print("\n".join(f"[assemble] REJECT {x}" for x in failures), file=sys.stderr)
        sys.exit(1)
    out_dir = Path(args.out_dir) if args.out_dir else draft_dir.parent
    _write_jsonl_atomic(out_dir / "questions.jsonl", questions)
    _write_jsonl_atomic(out_dir / "cannot_construct.jsonl", cannot)
    print(f"草稿汇编完成：questions {len(questions)} · cc {len(cannot)} -> {out_dir}")


def _file_of(q: dict) -> str:
    return str(q.get("_file") or q.get("_sha256") or "?")


def encode_image(img_path: Path, cache_dir: Path, cache_key: str,
                 max_side: int = 2048, quality: int = 92) -> str:
    """原图 → 内容键控的高质量 JPEG data-url（控制 payload 且保留审题细节）。"""
    cache_dir.mkdir(parents=True, exist_ok=True)
    cached = cache_dir / f"{cache_key[:16]}_{max_side}_q{quality}.jpg"
    if not cached.exists():
        from PIL import Image, ImageOps             # noqa: PLC0415
        with Image.open(img_path) as im:
            im = ImageOps.exif_transpose(im).convert("RGB")
            im.thumbnail((max_side, max_side))
            im.save(cached, "JPEG", quality=quality)
    return "data:image/jpeg;base64," + base64.b64encode(cached.read_bytes()).decode("ascii")


# ---------------------------------------------------------------------------
# API 调用与解析（沿 t2i 驱动配置）
# ---------------------------------------------------------------------------
def call_api(api_key: str, api_url: str, model: str, system_prompt: str,
             user_message: list, tag: str, max_tokens: int) -> dict:
    payload = {"model": model, "stream": False, "temperature": TEMPERATURE,
               "max_tokens": max_tokens,
               "messages": [{"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_message}]}
    headers = {"Authorization": f"Bearer {api_key}",
               "Content-Type": "application/json"}
    backoff = [30, 60, 120]
    for attempt in range(5):
        try:
            resp = requests.post(api_url, json=payload, headers=headers, timeout=TIMEOUT)
            if resp.status_code in (429, 529):
                wait = backoff[min(attempt, 2)]
                print(f"  [warn] {tag} HTTP {resp.status_code} 限流，{wait}s 后重试",
                      file=sys.stderr)
                time.sleep(wait)
                continue
            if resp.status_code != 200:
                raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:300]}")
            data = resp.json()
            msg = (data.get("choices") or [{}])[0].get("message") or {}
            if msg.get("content") is None and msg.get("reasoning_content"):
                msg["content"] = msg["reasoning_content"]
                data["choices"][0]["message"] = msg
            return data
        except Exception as e:                                  # noqa: BLE001
            if attempt >= 4:
                raise
            wait = backoff[min(attempt, 2)]
            print(f"  [warn] {tag} 调用失败（{e}），{wait}s 后重试", file=sys.stderr)
            time.sleep(wait)
    raise AssertionError("unreachable")


def _lenient_object(text: str, start: int) -> dict:
    seg = text[start:]
    for _ in range(10):
        try:
            return json.JSONDecoder().raw_decode(seg)[0]
        except json.JSONDecodeError as e:
            p = e.pos - 1
            while p >= 0 and seg[p] in " \t\r\n":
                p -= 1
            if p >= 0 and seg[p] == ",":
                seg = seg[:p] + seg[p + 1:]
                continue
            raise
    raise ValueError("尾逗号修复超过 10 次仍失败")


def extract_json_object(content: str) -> dict:
    text = re.sub(r"^```(?:json)?\s*", "", content.strip())
    text = re.sub(r"\s*```\s*$", "", text)
    start = text.find("{")
    if start < 0:
        raise ValueError(f"输出中无 JSON 对象: {content[:200]!r}")
    return _lenient_object(text, start)


# ---------------------------------------------------------------------------
# 机审（edit v6.1：固定词表/类型感知门槛/image-first receipt 硬契约）
# ---------------------------------------------------------------------------
def audit_v61_edit(q: dict, job: dict) -> tuple[list[str], bool]:
    """返回 (告警列表, 是否拒收)。告警不删（人工复审）；拒收 = 契约违背。"""
    if str(q.get("status") or "") == "cannot_construct":
        return ["cannot_construct 只能写入 --validate-cc 文件"], True
    warns, reject = [], False
    required_fields = {
        "task", "qid", "status", "edit_instruction", "edit_type", "suite", "level",
        "level_reason", "targeting_types", "consequence_types",
        "special_obligation_types", "preservation_types", "premise_types",
        "hop_types", "scene_types", "knowledge_categories", "knowledge_domains",
        "weak_points", "product_checks", "evidence_audit", "evidence_receipt",
        "reasoning", "notes", "cannot_reason_code",
    }
    missing_fields = sorted(required_fields - set(q))
    if missing_fields:
        warns.append(f"constructed 缺规定字段：{missing_fields}")
        reject = True
    if str(q.get("qid") or "") != job["qid"]:
        return ["qid 回显不符"], True
    if str(q.get("task") or "") != "edit":
        warns.append("task 必须为 edit")
        reject = True
    if str(q.get("status") or "") != "constructed":
        warns.append("questions 中 status 必须为 constructed")
        reject = True
    if str(q.get("edit_type") or "") != job["edit_type"]:
        warns.append(f"edit_type 回显不符：{q.get('edit_type')!r}")
        reject = True
    if str(q.get("suite") or "") != job["suite"]:
        warns.append(f"suite 回显不符：{q.get('suite')!r}")
        reject = True
    lvl = q.get("level")
    if lvl not in LEVELS:
        warns.append(f"level {lvl!r} 不在 L1/L2/L3")
        reject = True
    elif lvl != job["level"]:
        warns.append(f"level {lvl} 必须精确回显目标 {job['level']}（保持 T2I paired 分布）")
        reject = True
    if not str(q.get("edit_instruction") or "").strip():
        warns.append("缺 edit_instruction")
        reject = True
    rs = str(q.get("reasoning") or "")
    terminal_ids: dict[str, list[str]] = {"R": [], "P": [], "N": []}
    terminal_set: set[str] = set()
    max_arrows = 0
    if not rs.strip():
        warns.append("缺 reasoning")
    else:
        if "→" not in rs and "->" not in rs:
            warns.append("reasoning 无推导箭头（→）")
        kind_prefix = {"结论": "R", "保持": "P", "不得画": "N"}
        for lineno, line in enumerate((x for x in rs.splitlines() if x.strip()), 1):
            marks = re.findall(r"\[(结论|保持|不得画)\]\s*([RPN]\d+)", line)
            if len(marks) != 1:
                warns.append(f"reasoning 第 {lineno} 行必须恰含一个 R/P/N 终端")
                continue
            label, oid = marks[0]
            if oid[0] != kind_prefix[label]:
                warns.append(f"reasoning 第 {lineno} 行标签与编号前缀不符：{label}/{oid}")
            terminal_ids[oid[0]].append(oid)
            if oid in terminal_set:
                warns.append(f"reasoning 终端编号重复：{oid}")
            terminal_set.add(oid)
            max_arrows = max(max_arrows, line.count("→") + line.count("->"))
        for prefix, ids in terminal_ids.items():
            expected = [f"{prefix}{i + 1}" for i in range(len(ids))]
            if ids != expected:
                warns.append(f"reasoning {prefix} 编号必须从 1 连续且按序：{ids}")
        eff = lvl if lvl in LEVELS else job["level"]
        n_conc, n_keep, n_neg = (len(terminal_ids["R"]), len(terminal_ids["P"]),
                                 len(terminal_ids["N"]))
        g = GATES[eff]
        if n_conc < g[0]:
            warns.append(f"{eff} 门槛：[结论] {n_conc} < {g[0]}")
        if n_keep < g[1]:
            warns.append(f"{eff} 门槛：[保持] {n_keep} < {g[1]}")
        if n_neg < g[2]:
            warns.append(f"{eff} 门槛：[不得画] {n_neg} < {g[2]}")
        if n_neg and "混淆源" not in rs:
            warns.append("[不得画] 必须注明混淆源")
        kinds = {d.strip().split("；域·")[0]
                 for d in re.findall(r"（知识·([^）]+)）", rs)}
        if len(kinds) < g[4]:
            warns.append(f"{eff} 门槛：知识类别 {len(kinds)} < {g[4]}（按（知识·类别）去重）")
        bad_kinds = sorted(kinds - set(KNOWLEDGE_MENU))
        if bad_kinds:
            warns.append(f"reasoning 含固定词表外知识类别：{bad_kinds}")
    if not rs.strip():
        kinds = set()
    enum_fields = (
        ("targeting_types", "定位方式", TARGETING_MENU),
        ("consequence_types", "后果传播类型", CONSEQUENCE_MENU),
        ("special_obligation_types", "类型专用结果义务", SPECIAL_OBLIGATION_MENU),
        ("preservation_types", "保持类别", PRESERVATION_MENU),
        ("premise_types", "前提类别", PREMISE_MENU),
        ("hop_types", "跳类型", HOP_MENU),
        ("scene_types", "场景复杂度来源", SCENE_MENU),
        ("knowledge_categories", "知识类别", KNOWLEDGE_MENU),
        ("weak_points", "弱项", WEAK_MENU),
    )
    for f, name, menu in enum_fields:
        v = q.get(f)
        allow_empty = ((f == "consequence_types" and q.get("edit_type") in {
            "style", "extract", "background"
        }) or (f == "special_obligation_types" and q.get("edit_type") not in {
            "style", "extract", "background"
        }))
        if not isinstance(v, list) or (not v and not allow_empty) or any(
                not str(x).strip() for x in v):
            warns.append(f"{f} 应为{'可空' if allow_empty else '非空'}字符串数组（{name}多选）")
            continue
        if len(v) != len(set(map(str, v))):
            warns.append(f"{f} 含重复值")
        bad = [x for x in v if x not in menu]
        if bad:
            warns.append(f"{f} 含固定词表外值：{bad}")
            reject = True
    if q.get("edit_type") in {"style", "extract", "background"} \
            and not q.get("special_obligation_types"):
        warns.append(f"{q.get('edit_type')} 必须填写 special_obligation_types")
    if q.get("edit_type") not in {"style", "extract", "background"} \
            and q.get("special_obligation_types"):
        warns.append(f"{q.get('edit_type')} 不应填写 special_obligation_types")
    special_allowed = {
        "style": {"风格笔触", "风格色彩", "风格明暗", "风格材质"},
        "extract": {"主体完整", "抠图边界", "白底纯净", "杂物清除"},
        "background": {"背景语义", "背景透视", "背景景深", "前景边界"},
    }
    etype_special = special_allowed.get(str(q.get("edit_type") or ""))
    if etype_special is not None:
        wrong_special = sorted(set(q.get("special_obligation_types") or []) - etype_special)
        if wrong_special:
            warns.append(f"{q.get('edit_type')} 含其他类型的专用义务：{wrong_special}")
    if rs.strip() and set(q.get("knowledge_categories") or []) != kinds:
        warns.append("knowledge_categories 必须与 reasoning 的（知识·类别）去重集合完全一致")
    if lvl in LEVELS:
        min_target = {"L1": 1, "L2": 2, "L3": 3}[lvl]
        if q.get("edit_type") in {"style", "background"}:
            min_target = 1
        elif q.get("edit_type") == "extract":
            min_target = min(min_target, 2)
        if len(set(q.get("targeting_types") or [])) < min_target:
            warns.append(f"{lvl} 门槛：定位方式 {len(set(q.get('targeting_types') or []))} < {min_target}")
        min_hops = {"L1": 1, "L2": 2, "L3": 3}[lvl]
        if len(set(q.get("hop_types") or [])) < min_hops:
            warns.append(f"{lvl} 门槛：跳类型 {len(set(q.get('hop_types') or []))} < {min_hops}")
    st = [x for x in (q.get("scene_types") or []) if x in SCENE_MENU]
    if lvl in LEVELS and len(set(st)) < GATES[lvl][3]:
        warns.append(f"{lvl} 门槛：场景固定行 {len(set(st))} < {GATES[lvl][3]}（自造类名不计门槛）")
    kd = q.get("knowledge_domains")
    if not isinstance(kd, list) or not kd:
        warns.append("knowledge_domains 应为非空数组（29 域菜单名）")
    else:
        bad = [d for d in kd if d not in DOMAINS_MENU]
        if bad:
            warns.append(f"knowledge_domains 含菜单外域名（封闭枚举）: {bad}")
            reject = True
        if len(kd) != len(set(map(str, kd))):
            warns.append("knowledge_domains 含重复值")
    wps = q.get("weak_points")
    if isinstance(wps, list) and lvl in LEVELS and len(set(map(str, wps))) < GATES[lvl][5]:
        warns.append(f"{lvl} 门槛：弱项去重 {len(set(map(str, wps)))} < {GATES[lvl][5]}")
    ea = q.get("evidence_audit") or {}
    visible = ea.get("visible_facts")
    visible_ids: list[str] = []
    if not isinstance(visible, list) or not visible:
        warns.append("evidence_audit.visible_facts 缺失或为空")
    else:
        for i, fact in enumerate(visible):
            if not isinstance(fact, dict):
                warns.append(f"evidence_audit.visible_facts[{i}] 必须为对象")
                continue
            vid = str(fact.get("id") or "")
            if (not re.fullmatch(r"V[1-9]\d*", vid)
                    or not str(fact.get("fact") or "").strip()
                    or not str(fact.get("anchor") or "").strip()):
                warns.append(f"evidence_audit.visible_facts[{i}] id/fact/anchor 不合规")
            visible_ids.append(vid)
        if visible_ids != [f"V{i + 1}" for i in range(len(visible_ids))]:
            warns.append(f"visible_facts id 必须从 V1 连续唯一且按序：{visible_ids}")
    edit_targets = ea.get("edit_targets")
    if not isinstance(edit_targets, list) or not edit_targets:
        warns.append("evidence_audit.edit_targets 缺失或为空")
        edit_targets = []
    for f in ("distractors", "uncertain_or_rejected"):
        if not isinstance(ea.get(f), list):
            warns.append(f"evidence_audit.{f} 必须为数组（允许空）")
    distractors = ea.get("distractors") if isinstance(ea.get("distractors"), list) else []
    bad_refs = sorted((set(edit_targets) | set(distractors)) - set(visible_ids))
    if bad_refs:
        warns.append(f"edit_targets/distractors 引用不存在的可见事实：{bad_refs}")
        reject = True
    # evidence_receipt 硬契约：只接收高置信像素证据，并注明使用位置。
    rec = q.get("evidence_receipt")
    if not isinstance(rec, list) or not rec:
        warns.append("缺 evidence_receipt（题面引用要素须逐项凭图回执）")
        reject = True
    else:
        seen_elements: set[str] = set()
        receipt_by_vid: dict[str, list[dict]] = {}
        for i, r in enumerate(rec):
            if not isinstance(r, dict):
                warns.append(f"evidence_receipt[{i}] 必须为对象")
                reject = True
                continue
            if str(r.get("source") or "") != "image":
                warns.append(f"evidence_receipt[{i}].source != image（凭清单采信，拒收）")
                reject = True
            element = str(r.get("element") or "").strip()
            if not element or not str(r.get("anchor") or "").strip():
                warns.append(f"evidence_receipt[{i}] element/anchor 为空")
                reject = True
            if element in seen_elements:
                warns.append(f"evidence_receipt[{i}] element 重复：{element}")
            seen_elements.add(element)
            vid_match = re.match(r"^(V[1-9]\d*)\s*[:：]", element)
            if not vid_match or vid_match.group(1) not in visible_ids:
                warns.append(f"evidence_receipt[{i}].element 必须以已定义 Vn 开头")
                reject = True
            else:
                receipt_by_vid.setdefault(vid_match.group(1), []).append(r)
            used = r.get("used_in")
            if (not isinstance(used, list) or not used or any(
                    x != "edit_instruction" and not re.fullmatch(r"[RPN]\d+", str(x))
                    for x in used)):
                warns.append(f"evidence_receipt[{i}].used_in 只能含 edit_instruction 或 R/P/N 编号")
                reject = True
            elif any(x != "edit_instruction" and x not in terminal_set for x in used):
                warns.append(f"evidence_receipt[{i}].used_in 引用不存在终端："
                             f"{[x for x in used if x != 'edit_instruction' and x not in terminal_set]}")
                reject = True
            if r.get("confidence") != "high":
                warns.append(f"evidence_receipt[{i}].confidence 必须为 high；看不清的要素应弃用")
                reject = True
        reasoning_vids = set(re.findall(r"(?<![A-Za-z0-9_])V[1-9]\d*(?![A-Za-z0-9_])", rs))
        missing_visible = sorted(reasoning_vids - set(visible_ids))
        if missing_visible:
            warns.append(f"reasoning 引用未定义可见事实：{missing_visible}")
            reject = True
        missing_receipts = sorted(reasoning_vids - set(receipt_by_vid))
        if missing_receipts:
            warns.append(f"reasoning 可见事实缺 evidence_receipt：{missing_receipts}")
            reject = True
        uncovered_targets = sorted(v for v in edit_targets if not any(
            "edit_instruction" in (r.get("used_in") or []) for r in receipt_by_vid.get(v, [])))
        if uncovered_targets:
            warns.append(f"edit_targets 缺 edit_instruction 回执：{uncovered_targets}")
            reject = True
    lr = q.get("level_reason")
    lr_keys = ("result_obligations", "preservation_obligations", "negative_obligations",
               "scene_type_count", "knowledge_category_count", "longest_hops",
               "hop_type_count", "product_count", "meets_target")
    if not isinstance(lr, dict) or any(k not in lr for k in lr_keys):
        warns.append("level_reason 必须是含九个规定键的对象")
    elif lvl in LEVELS:
        actual = {
            "result_obligations": len(terminal_ids["R"]),
            "preservation_obligations": len(terminal_ids["P"]),
            "negative_obligations": len(terminal_ids["N"]),
            "scene_type_count": len(set(q.get("scene_types") or [])),
            "knowledge_category_count": len(set(q.get("knowledge_categories") or [])),
            "hop_type_count": len(set(q.get("hop_types") or [])),
            "product_count": len(q.get("product_checks") or []),
        }
        for key, value in actual.items():
            if lr.get(key) != value:
                warns.append(f"level_reason.{key}={lr.get(key)!r} 与实际 {value} 不一致")
        if lr.get("meets_target") is not True:
            warns.append("level_reason.meets_target 必须为 true")
        min_hops = {"L1": 1, "L2": 2, "L3": 3}[lvl]
        if not isinstance(lr.get("longest_hops"), int) or lr["longest_hops"] < min_hops:
            warns.append(f"{lvl} 门槛：level_reason.longest_hops < {min_hops}")
        elif lr["longest_hops"] != max_arrows:
            warns.append(f"level_reason.longest_hops={lr['longest_hops']} 与逐行最大箭头数 {max_arrows} 不一致")
    pcs = q.get("product_checks")
    if not isinstance(pcs, list):
        warns.append("product_checks 必须为数组")
    else:
        if lvl in {"L1", "L2"} and pcs:
            warns.append(f"{lvl} product_checks 必须为空")
        if lvl == "L3" and not pcs:
            warns.append("L3 至少需要 1 个 product_check")
        for i, pc in enumerate(pcs):
            oid = str(pc.get("obligation_id") or "") if isinstance(pc, dict) else ""
            factors = pc.get("factors") if isinstance(pc, dict) else None
            if (not re.fullmatch(r"[RN]\d+", oid) or oid not in terminal_set
                    or not isinstance(factors, list)
                    or len(set(map(str, factors))) != 2
                    or any(x not in WEAK_MENU for x in factors or [])
                    or any(x not in set(q.get("weak_points") or []) for x in factors or [])):
                warns.append(f"product_checks[{i}] obligation_id/factors 不合规")
    # level 是目标档而不是最低档：不能靠低标签承载一整套高档门槛。
    if lvl in {"L1", "L2"}:
        higher = "L2" if lvl == "L1" else "L3"
        hg = GATES[higher]
        reaches_higher = all((
            len(terminal_ids["R"]) >= hg[0],
            len(terminal_ids["P"]) >= hg[1],
            len(terminal_ids["N"]) >= hg[2],
            len(set(q.get("scene_types") or [])) >= hg[3],
            len(set(q.get("knowledge_categories") or [])) >= hg[4],
            max_arrows >= {"L2": 2, "L3": 3}[higher],
            len(set(q.get("hop_types") or [])) >= {"L2": 2, "L3": 3}[higher],
            len(set(q.get("weak_points") or [])) >= hg[5],
            higher != "L3" or bool(q.get("product_checks")),
        ))
        if reaches_higher:
            warns.append(f"{lvl} 同时满足全部 {higher} 门槛；应换更简单设计或升档")
    if "notes" in q and not isinstance(q["notes"], (str, type(None))):
        warns.append("notes 应为字符串")
    if q.get("cannot_reason_code") not in ("", None):
        warns.append("constructed 的 cannot_reason_code 必须为空")
    return warns, reject


def xcheck_scene(q: dict, audit_row: dict | None) -> float | None:
    """出题自报 scene_types vs 初审 scene_sources 的 Jaccard（<0.34 进复审）。"""
    if not audit_row:
        return None
    a = {d for d, v in (audit_row.get("scene_sources") or {}).items() if v}
    b = {x for x in (q.get("scene_types") or []) if x in SCENE_MENU}
    if not a or not b:
        return None
    return len(a & b) / len(a | b)


# ---------------------------------------------------------------------------
# 削峰/补峰（道内）
# ---------------------------------------------------------------------------
def shape_dispatch(records: list[dict], share: float = 0.2) -> tuple:
    done = len(records)
    if not done:
        return [], [], [], []
    cap = max(2, math.ceil(done * share))
    cons: dict[str, int] = {}
    for r in records:
        for c in r.get("consequence_types", []):
            cons[c] = cons.get(c, 0) + 1
    bans = [c for c, n in sorted(cons.items(), key=lambda x: -x[1]) if n >= cap][:2]
    dom: dict[str, int] = {}
    for r in records:
        for d in r.get("knowledge_domains", []):
            dom[d] = dom.get(d, 0) + 1
    avoid = [d for d, n in sorted(dom.items(), key=lambda x: -x[1]) if n > done * 0.5][:2]
    sol_c, sol_s = [], []
    if done >= 3:
        sol_c = [c for c in CONSEQUENCE_MENU if c not in cons][:2]
        seen_s = {t for r in records for t in r.get("scene_types", [])}
        sol_s = [t for t in SCENE_MENU if t not in seen_s][:2]
    return bans, avoid, sol_c, sol_s


def adjustment_text(bans: list, avoid: list, sol_c: list, sol_s: list,
                    job: dict, lane_cons: dict[str, int]) -> str:
    # 主域豁免：主域必然申报使用，撞上即结构性 cannot_construct
    if job.get("main_domain"):
        avoid = [d for d in avoid if d != job["main_domain"]]
    # L3 需要构造弱点乘积：某后果在既有 L3 中尚不足 2 次时暂不禁，达到 2 次后
    # 恢复常规削峰。统计键显式带 level，避免用全批次数假装完成 L3 覆盖。
    if job.get("level") == "L3":
        bans = [c for c in bans if lane_cons.get(f"L3::{c}", 0) >= 2]
    # 紧邻动作/放置往往至少需要接触或局部受光之一；两者同时禁会把大量合法题
    # 结构性推入 cannot。若同时入选，豁免当前较少见者（并列时豁免接触受力）。
    tight_pair = {"接触受力", "光影联动"}
    if tight_pair.issubset(set(bans)):
        exempt = min(("接触受力", "光影联动"), key=lambda c: lane_cons.get(c, 0))
        bans = [c for c in bans if c != exempt]
    parts = []
    if bans or avoid:
        seg = []
        if bans:
            seg.append("不得使用的后果传播类型：" + "、".join(bans))
        if avoid:
            seg.append("知识来源尽量避开的域：" + "、".join(avoid))
        parts.append("【削峰禁令】" + "；".join(seg)
                     + "。（后果类型禁令为硬约束，确实无法不破禁出题时输出 "
                       "cannot_construct，不得硬凑；域避让为软约束，无等价替代知识"
                       "来源时可不用）")
    if sol_c or sol_s:
        seg = []
        if sol_c:
            seg.append("优先使用的后果传播类型：" + "、".join(sol_c))
        if sol_s:
            seg.append("优先纳入的场景复杂度来源：" + "、".join(sol_s))
        parts.append("【补峰征召】" + "；".join(seg)
                     + "。（软约束：图与征召类无自然因果交汇时可选其他，在 notes "
                       "注明未应召原因）")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def run(args) -> None:
    system_prompt = PROMPT_FILE.read_text(encoding="utf-8")
    inputs = load_inputs()
    jobs = build_jobs(inputs, args.limit, args.mix,
                      align_t2i_levels=args.align_t2i_levels,
                      knowledge_share=args.knowledge_share)
    if not jobs:
        sys.exit("无出题任务（manifest/实例清单为空？）")

    if args.emit_plan:
        cmd_emit_plan(args, inputs, jobs)
        return

    mix_counts: dict[str, int] = {}
    for j in jobs:
        mix_counts[j["level"]] = mix_counts.get(j["level"], 0) + 1
    type_counts: dict[str, int] = {}
    for j in jobs:
        type_counts[j["edit_type"]] = type_counts.get(j["edit_type"], 0) + 1
    img_batch = {"L3": {}, "L1L2": {}}
    for j in jobs:
        b = j["images"][0].get("batch") or "main"
        key = "L3" if j["level"] == "L3" else "L1L2"
        img_batch[key][b] = img_batch[key].get(b, 0) + 1
    print(f"edit v6.1 image-first 出题批次：{len(jobs)} 题（每实例 1 题）\n"
          f"  层级：{mix_counts} · level_source={jobs[0]['level_source']}\n"
          f"  suite=knowledge {sum(1 for j in jobs if j['suite'] == 'knowledge')} 题（按 level 独立铺开）\n"
          f"  类型轮转：{type_counts}\n"
          f"  选图（L3 偏好 hard++++/双载体，L1/L2 偏好 main）：{img_batch}")
    if args.dry_run:
        for j in jobs[:10]:
            top = j["images"][0]
            print(f"    {j['qid']} {j['instance']:<14} {j['level']} {j['suite']:<9} "
                  f"{j['edit_type']:<10} <- {top.get('batch') or 'main'} {top['file'].split('/')[-1]}")
        print("  dry-run 结束（未调 API）")
        return

    api_key = args.api_key or os.environ.get("GALAXY_API_KEY")
    if not api_key:
        sys.exit("请通过环境变量 GALAXY_API_KEY 提供 API key")

    out_dir = Path(args.out_dir) if args.out_dir else EVAL_DIR / "synth_v61"
    raw_dir = out_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    cache_dir = out_dir / "img_cache"
    q_path = out_dir / "questions.jsonl"
    cc_path = out_dir / "cannot_construct.jsonl"

    done_qids = set()
    existing_records: dict[str, dict] = {}
    for path in (q_path, cc_path):
        if not path.exists():
            continue
        for q in read_jsonl(path):
            qid = str(q.get("_job_qid") or q.get("qid") or "")
            if not qid:
                continue
            done_qids.add(qid)
            if path == q_path and str(q.get("status") or "") == "constructed":
                existing_records[qid] = {
                    "qid": qid, "status": "constructed",
                    "consequence_types": [str(x) for x in q.get("consequence_types") or []],
                    "scene_types": [str(x) for x in q.get("scene_types") or []],
                    "knowledge_domains": [str(x) for x in q.get("knowledge_domains") or []],
                    "level": q.get("level"),
                }
    counters = {"total": 0, "cc": 0, "reject": 0, "error": 0}
    report = {"warns": {}, "xcheck": [], "levels": mix_counts}
    write_lock = threading.Lock()
    fout = q_path.open("a", encoding="utf-8")                  # noqa: SIM115
    fcc = cc_path.open("a", encoding="utf-8")                  # noqa: SIM115

    def raw_path(qid: str, img_row: dict, edit_type: str) -> Path:
        return raw_dir / f"{qid}_{edit_type}_{img_row['sha256'][:10]}.json"

    def attempt_job(job: dict, img_row: dict, edit_type: str, adj: str) -> str:
        """单次尝试。返回 constructed / cannot / reject / error。"""
        qid = job["qid"]
        if qid in done_qids:
            return "skip"
        text = build_text(job, img_row, edit_type, inputs, adj)
        img = FOCUS / img_row["file"]
        if not img.exists():
            print(f"  [error] {qid} 图缺失 {img}", file=sys.stderr)
            return "error"
        msg = [{"type": "image_url", "image_url": {
                   "url": encode_image(img, cache_dir, img_row["sha256"])}},
               text[0]]
        raw_p = raw_path(qid, img_row, edit_type)
        result = None
        if raw_p.exists():
            try:
                cand = json.loads(raw_p.read_text(encoding="utf-8"))
                if (cand["choices"][0]["message"].get("content") or "").strip():
                    result = cand
            except Exception:                                   # noqa: BLE001
                result = None
        t0 = time.time()
        if result is None:
            try:
                result = call_api(api_key, args.api_url, args.model, system_prompt,
                                  msg, qid, args.max_tokens)
            except Exception as e:                              # noqa: BLE001
                print(f"  [error] {qid}: {e}", file=sys.stderr)
                return "error"
            raw_p.write_text(json.dumps(result, ensure_ascii=False, indent=2),
                             encoding="utf-8")
        content = result["choices"][0]["message"]["content"] or ""
        if not content.strip():
            print(f"  [error] {qid} content 为空", file=sys.stderr)
            return "error"
        try:
            q = extract_json_object(content)
        except (ValueError, json.JSONDecodeError) as e:
            print(f"  [error] {qid} JSON 解析失败：{e}", file=sys.stderr)
            try:
                raw_p.unlink()
            except OSError:
                pass
            return "error"
        q["_adjustment"] = adj
        warns, reject = audit_v61_edit(q, job)
        adjustment_errs = _adjustment_errors(q)
        warns.extend(adjustment_errs)
        reject = reject or any("违反" in e for e in adjustment_errs)
        if args.strict and warns:
            reject = True
        with write_lock:
            for w in warns:
                report["warns"][w.split("：")[0][:24]] = report["warns"].get(w.split("：")[0][:24], 0) + 1
        if str(q.get("status") or "") == "cannot_construct":
            with write_lock:
                counters["cc"] += 1
            print(f"  [cannot] {qid} {job['instance']}：{str(q.get('notes'))[:100]}")
            return "cannot"
        if reject:
            with write_lock:
                counters["reject"] += 1
            print(f"  [reject] {qid}: {'; '.join(warns)[:160]}", file=sys.stderr)
            try:
                raw_p.unlink()          # 契约违背，弃 raw 重试
            except OSError:
                pass
            return "reject"
        if warns:
            print(f"  [audit] {qid}: {'; '.join(warns)[:160]}", file=sys.stderr)
        jac = xcheck_scene(q, inputs["audit"].get(img_row["sha256"]))
        if jac is not None and jac < 0.34:
            with write_lock:
                report["xcheck"].append({"qid": qid, "jaccard": round(jac, 2),
                                         "instance": job["instance"]})
        with write_lock:
            q.update({"task": "edit", "difficulty": q.get("level"),
                      "_job_qid": qid, "_instance": job["instance"],
                      "_sha256": img_row["sha256"], "_file": img_row["file"],
                      "_sample_image": f"focus200/{img_row['file']}",
                      "_batch": img_row.get("batch") or "main",
                      "_target_level": job["level"], "_edit_type": edit_type,
                      "_suite": job["suite"], "_generator": img_row["generator"],
                      "_protocol": "edit-v6.1-image-first", "_adjustment": adj})
            fout.write(json.dumps(q, ensure_ascii=False) + "\n")
            fout.flush()
            counters["total"] += 1
            done_qids.add(qid)
        usage = result.get("usage", {})
        print(f"  [done] {qid} {job['instance']} {q.get('level')} {edit_type} "
              f"{time.time() - t0:.0f}s tokens={usage.get('total_tokens', '?')}")
        return "constructed"

    def run_with_retries(job: dict, adj_fn) -> dict | None:
        """换位补额：①原类型+首选图 ②换类型+首选图 ③原类型+备选图。
        cannot_construct（类型不适配/禁令冲突）同样走换位重试；全部尝试耗尽才
        落 cannot_construct.jsonl（换位补额：勿硬凑）。"""
        plans = [(job["images"][0], job["edit_type"]),
                 (job["images"][0], job["alt_type"])]
        if len(job["images"]) > 1:
            plans.append((job["images"][1], job["edit_type"]))
        plans = plans[:args.max_attempts]
        last_cc = None
        for img_row, etype in plans:
            job["edit_type"] = etype          # 机审回显按当次类型
            st = attempt_job(job, img_row, etype, adj_fn(job))
            if st == "constructed":
                return {"qid": job["qid"], "edit_type": etype,
                        "consequence_types": [], "scene_types": [],
                        "knowledge_domains": [], "status": st}
            if st == "skip":
                return None
            if st == "cannot":
                last_cc = (img_row, etype)
                continue                       # 换类型/换图再试
            time.sleep(2)
        if last_cc is not None:
            img_row, etype = last_cc
            try:
                q = extract_json_object(json.loads(
                    raw_path(job["qid"], img_row, etype).read_text(encoding="utf-8"))
                    ["choices"][0]["message"]["content"] or "")
            except Exception:                                   # noqa: BLE001
                q = {}
            with write_lock:
                fcc.write(json.dumps({**q, "task": "edit",
                                      "_job_qid": job["qid"],
                                      "_attempt_type": etype,
                                      "_sha256": img_row["sha256"],
                                      "_file": img_row["file"],
                                      "_sample_image": f"focus200/{img_row['file']}",
                                      "_batch": img_row.get("batch") or "main",
                                      "_target_level": job["level"],
                                      "_suite": job["suite"],
                                      "_generator": img_row["generator"],
                                      "_instance": job["instance"],
                                      "_protocol": "edit-v6.1-image-first",
                                      "_adjustment": adj},
                                     ensure_ascii=False) + "\n")
                fcc.flush()
                done_qids.add(job["qid"])
            return {"qid": job["qid"], "edit_type": etype,
                    "consequence_types": [], "scene_types": [],
                    "knowledge_domains": [], "status": "cannot"}
        with write_lock:
            counters["error"] += 1
        print(f"  [park] {job['qid']} {job['instance']} 重试 {len(plans)} 次未成，待人工",
              file=sys.stderr)
        return None

    def record_of(job: dict) -> dict | None:
        try:
            q = next(q for q in reversed(read_jsonl(q_path))
                     if (q.get("_job_qid") or q.get("qid")) == job["qid"])
            keep = lambda f: [str(x).strip() for x in (q.get(f) or [])  # noqa: E731
                              if str(x).strip()] if isinstance(q.get(f), list) else []
            return {"qid": job["qid"], "status": str(q.get("status") or "constructed"),
                    "consequence_types": keep("consequence_types"),
                    "scene_types": keep("scene_types"),
                    "knowledge_domains": keep("knowledge_domains"),
                    "level": q.get("level")}
        except Exception:                                       # noqa: BLE001
            return None

    lanes = max(1, args.lanes) if args.peak_shave else 1
    lane_jobs: dict[int, list] = {}
    for i, job in enumerate(jobs):
        lane_jobs.setdefault(i % lanes, []).append(job)

    def run_lane(lane_id: int, ljobs: list):
        # 断点恢复时仍把已完成题计入本 lane 的削峰分布，不能“失忆”。
        records: list[dict] = [existing_records[j["qid"]] for j in ljobs
                               if j["qid"] in existing_records]
        for job in ljobs:
            def adj_fn(j, _records=records):
                bans, avoid, sol_c, sol_s = shape_dispatch(
                    [r for r in _records if r.get("status") == "constructed"])
                lane_cons: dict[str, int] = {}
                for r in _records:
                    if r.get("status") == "constructed":
                        for c in r["consequence_types"]:
                            lane_cons[c] = lane_cons.get(c, 0) + 1
                            if r.get("level") in LEVELS:
                                key = f"{r['level']}::{c}"
                                lane_cons[key] = lane_cons.get(key, 0) + 1
                return adjustment_text(bans, avoid, sol_c, sol_s, j, lane_cons)
            rec = run_with_retries(job, adj_fn)
            if rec:
                rec.update(record_of(job) or {"qid": job["qid"], "status": "unknown"})
                records.append(rec)
        if args.peak_shave:
            (out_dir / f"batch_stats_lane{lane_id}.json").write_text(
                json.dumps(records, ensure_ascii=False, indent=1), encoding="utf-8")
            print(f"  [lane{lane_id}] {len(records)} 题入账", flush=True)

    try:
        if args.peak_shave:
            with ThreadPoolExecutor(max_workers=len(lane_jobs)) as ex:
                list(ex.map(lambda kv: run_lane(*kv), lane_jobs.items()))
        else:
            run_lane(0, jobs)
    finally:
        fout.close()
        fcc.close()
        (out_dir / "run_report.json").write_text(
            json.dumps({"counters": counters, **report}, ensure_ascii=False, indent=1),
            encoding="utf-8")

    print(f"\n完成：{counters['total']} 题 · cannot_construct {counters['cc']} · "
          f"拒收重试 {counters['reject']} · 失败 {counters['error']} -> {q_path}")
    if report["xcheck"]:
        print(f"交叉核验复审名单（scene Jaccard<0.34）：{len(report['xcheck'])} 题，"
              f"见 run_report.json")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--limit", type=int, default=0, help="限实例数；0=全量 200")
    ap.add_argument("--mix", type=str, default="L1:10,L2:60,L3:30",
                    help="未对齐/缺正式题库时的层级配比（默认与 t2i 10/60/30 一致）")
    ap.add_argument("--align-t2i-levels", action=argparse.BooleanOptionalAction,
                    default=True, help="逐实例继承 t2i bench_v1 level（默认开启）")
    ap.add_argument("--knowledge-share", type=int, default=DEFAULT_KNOWLEDGE_SHARE,
                    help="各 level 内独立铺开的 knowledge 百分比（默认 20）")
    ap.add_argument("--peak-shave", action="store_true",
                    help="多串行道削峰：道内串行按实时分布下发禁令，道间并行")
    ap.add_argument("--lanes", type=int, default=4, help="削峰道数")
    ap.add_argument("--max-attempts", type=int, default=3,
                    help="cannot_construct/拒收后的换位补额尝试上限")
    ap.add_argument("--api-url", type=str, default=API_URL)
    ap.add_argument("--api-key", type=str, default="")
    ap.add_argument("--model", type=str, default=MODEL)
    ap.add_argument("--max-tokens", type=int, default=MAX_TOKENS)
    ap.add_argument("--out-dir", type=str, default="")
    ap.add_argument("--dry-run", action="store_true", help="只打印批次计划，不调 API")
    ap.add_argument("--emit-plan", action="store_true",
                    help="离线工具：发射确定性批次计划 plan.jsonl（含逐题尝试文本，"
                         "供 codex 等外部出题者消费），不调 API")
    ap.add_argument("--plan", type=str, default="",
                    help="validate/dispatch 用：plan.jsonl 路径")
    ap.add_argument("--validate", type=str, default="",
                    help="离线工具：机审 questions 文件（reject 级退出码 1）")
    ap.add_argument("--validate-cc", type=str, default="",
                    help="离线工具：校验 cannot_construct 文件（notes 必填）")
    ap.add_argument("--strict", action="store_true",
                    help="validate：把所有结构/门槛 WARN 升为 REJECT")
    ap.add_argument("--require-complete", action="store_true",
                    help="validate：要求 plan 的每个 qid 在 questions 或 cc 中恰有一个终态")
    ap.add_argument("--expected-count", type=int, default=0,
                    help="validate：额外钉死 plan 应有行数；全量验收时传 200")
    ap.add_argument("--assemble-drafts", type=str, default="",
                    help="离线工具：汇编目录内并行 JSONL 草稿，按 plan 顺序注入动态调整/元数据并严格验收")
    ap.add_argument("--dispatch", type=str, default="",
                    help="离线工具：输出下一题（qid）的削峰/补峰调整文本；"
                         "配合 --questions（已完成题库）与 --plan")
    ap.add_argument("--questions", type=str, default="",
                    help="dispatch 用：已完成 questions 文件路径（缺省=空批次）")
    args = ap.parse_args()

    if args.assemble_drafts:
        if not args.plan:
            sys.exit("--assemble-drafts 需要 --plan 指向 plan.jsonl")
        cmd_assemble_drafts(args)
        return
    if args.validate or args.validate_cc:
        if not args.plan:
            sys.exit("--validate 需要 --plan 指向 plan.jsonl")
        cmd_validate(args)
        return
    if args.dispatch:
        if not args.plan:
            sys.exit("--dispatch 需要 --plan 指向 plan.jsonl")
        cmd_dispatch(args)
        return

    mix: dict[str, int] = {}
    for part in args.mix.split(","):
        k, _, v = part.partition(":")
        k, v = k.strip().upper(), v.strip()
        if k not in LEVELS or not v.isdigit():
            sys.exit(f"--mix 形如 L1:10,L2:60,L3:30: {args.mix!r}")
        mix[k] = int(v)
    if sum(mix.values()) != 100:
        sys.exit(f"--mix 百分比之和应为 100: {mix}")
    if not 0 <= args.knowledge_share <= 100:
        sys.exit("--knowledge-share 必须在 0..100")
    args.mix = mix
    run(args)


if __name__ == "__main__":
    main()
