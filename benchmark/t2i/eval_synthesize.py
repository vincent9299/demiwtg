"""t2i（生成）赛道出题：按出题 prompt 调 LLM API 对样本出 T2I 题。

读 t2i/data/samples.jsonl（eval_sample.py 产物），每图出一道生成题
（出题 prompt = t2i/synthesize_prompt_gen.md）。

v4.1（2026-08-26 pilot）：API 入口由 Galaxy 直连切到 modelhub 网关
（MODELHUB_URL，默认 http://127.0.0.1:4001/v1），支持多模型轮转（--models
逗号分隔，按样本轮询），适配 reasoning 模型（max_tokens 拉 16384、
content 为 null 时回退 reasoning_content）。

v6.0（2026-08-30 条目制）：检查条目 = 类别 × 取值 × 层级（L1 显式 /
L2 单跳隐式 / L3 链式），隐式蕴含收编进对齐；无硬约束清单，知识由出题
模型自身供给（输入 = 实体名 + 分类路径，无图出题）；机审 = 层级配额、
链跳数一致、锚点卫生、九类枚举（audit_v60）。

产物：
    t2i/data/synth_gen/questions.jsonl            # 默认（兼容 v4 旧用法，单模型）
    t2i/data/synth_gen/questions_<model>.jsonl     # --models 多模型时按模型分文件
    t2i/data/synth_gen/raw/<sid>.json              # 每题原始 API 响应

用法：
    # 旧用法（Galaxy 单模型，向后兼容）
    GALAXY_API_KEY=sk-... python3 benchmark/t2i/eval_synthesize.py --limit 10

    # 新用法（modelhub 网关 + 多模型轮转）
    MODELHUB_KEY=EMPTY python3 benchmark/t2i/eval_synthesize.py \\
        --models xiaoyao/gpt-5.6-sol,xiaoyao/claude-fable-5,openrouter/google/gemini-3.7-flash \\
        --limit 10
"""

from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import os
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests

BENCH_ROOT = Path(__file__).resolve().parent.parent   # t2i/ -> benchmark/
sys.path.insert(0, str(BENCH_ROOT))

from t2i.eval_score import FACET_KEYS                 # noqa: E402  facet 词表权威源

SUB_DIR = Path(__file__).resolve().parent             # t2i/
EVAL_DIR = SUB_DIR / "data"
PROMPT_FILE = SUB_DIR / "synthesize_prompt_gen.md"
SAMPLES = EVAL_DIR / "samples.jsonl"

API_URL = "https://token.ai-galaxy.com/v1/chat/completions"
MODEL = "qwen3.7-plus"

MAX_TOKENS = 16384
TIMEOUT = (10, 600)              # 连接 10s，读 600s（推理模型输出慢）

# v4.1: modelhub 网关默认配置（出题切到网关，多模型轮转）
MODELHUB_URL = "http://127.0.0.1:4001/v1/chat/completions"
MODELHUB_KEY = "EMPTY"           # 网关无鉴权，key 任意非空
DEFAULT_MODELS = [               # v4.1 pilot 默认三模型
    "xiaoyao/gpt-5.6-sol",
    "xiaoyao/claude-fable-5",
    "openrouter/google/gemini-3.7-flash",
]
TEMPERATURE = 0.4                # 出题要一定多样性（v4.1 自 0.7 调低，schema 服从性优先）

# v5.6 五镜头（视觉知识检测线）：知识冲突在画面上只可能以五种方式暴露
LENS_NAMES = {"K1": "形态与构成", "K2": "表面与图样", "K3": "符号与文字",
              "K4": "数量与尺度", "K5": "状态与规律"}

# v6.0 条目制：检查条目 = 类别 × 取值 × 层级；九类枚举与层级见
# synthesize_prompt_gen_v6.0.md。默认层级配额 1:3:6
V60_CATEGORIES = {"主体在场", "形态结构", "颜色材质", "数量尺度",
                  "空间关系", "文字符号", "动作交互", "状态情境", "风格"}
V60_LEVELS = ("L1", "L2", "L3")
# v6.0 组合主类枚举（combo_type 输出字段 + 削峰统计桶；光学媒介为实测高频类）
V60_COMBO_TYPES = ("光学媒介", "接触交互", "容纳承载", "尺度并置",
                   "因果", "多实例", "文化语境", "光影投影", "装配咬合",
                   "运动瞬间", "光学传播", "生态互动")
# combo_type 缺失时的关键词兜底分类（削峰统计表防污染）
V60_SCENE_TYPES = ("实体密度", "细节密度", "交互链", "过程时刻", "环境作用",
                   "视点剖示", "规约场景", "多实例对比", "纵深层次", "光照时段",
                   "动态要素", "多人物编排")
V60_HOP_TYPES = ("过程-因果", "规约-标准", "发育-生长", "功能-结构",
                 "物理规律", "文化-规制", "关系（组合）", "分类-辨识",
                 "量-守恒", "序-时序", "原理-推演", "化学-反应",
                 "生态-互动")
V60_WEAK_POINTS = ("镜面倒影", "尺度并置", "剖面暴露", "精确计数", "文字字样",
                   "接触交互链", "长尾色材", "动态模糊", "朝向左右",
                   "部件数目", "光影一致性", "手部细节", "人体姿态", "物理现象", "化学产物",
                   "生态关系", "受力形变", "其他")
V60_COMBO_KEYWORDS = (
    ("光学媒介", ("镜像", "倒影", "反射", "镜面", "幕墙", "水面")),
    ("接触交互", ("接触", "递接", "握", "夹持", "扶持", "搭在")),
    ("容纳承载", ("容纳", "承载", "液面", "装载", "容器", "承接")),
    ("尺度并置", ("比例", "尺度", "巨型", "袖珍", "并置")),
    ("因果", ("因果", "导致", "泼洒", "结果与原因")),
    ("多实例", ("多实例", "逐阶段", "个体间", "各自处于")),
    ("文化语境", ("纹章", "规约", "仪式", "典故", "文化")),
)
# 29 域菜单（与 synthesize_prompt_gen_v6.0.md 知识挖掘节一致；域标注机审词表）
V60_PREMISE_TYPES = (
    "阶段/时刻", "年龄/生长阶段", "变体/子类型", "使用状态", "数量/编组",
    "环境场所", "视角/暴露", "事件/典故语境", "反事实假设", "时间/历史语境",
)   # 前提类别表（premise_types 统计枚举）
V60_DOMAINS_MENU = (
    "政治、法律与社会制度", "地理与地点", "宗教与信仰", "民族、语言与文化",
    "人造物体", "动物", "真菌与微生物", "植物", "人物与人体", "行为动作",
    "食物", "建筑与基础设施", "交通工具", "自然景观", "场景", "节日与符号",
    "属性与状态", "材料与物质", "时间数量与度量", "文字与信息图形", "声音",
    "文化艺术与媒介", "知识与学科", "组织机构与社会事件", "体育与游戏",
    "历史与时代", "品牌与产品", "数字与互联网文化", "医学与健康",
)   # 29 域菜单（跨域注入核验与批次域统计；非难度口径）
V60_KNOWLEDGE_KINDS = (
    "概念自身结构", "状态与阶段", "环境交互", "可组合关系", "规约与典故",
    "物理规律", "化学规律", "生物规律", "地理气象", "天文对应",
)   # 知识跨度统计枚举（第二节知识类别表；可自造四字类名，机审放行）
V60_DEFAULT_MIX = {"L1": 1, "L2": 3, "L3": 6}


# ---------------------------------------------------------------------------
# 通用：LLM 调用与解析
# ---------------------------------------------------------------------------
def call_api(api_key: str, api_url: str, model: str,
             system_prompt: str, user_message: list, tag: str,
             max_tokens: int = MAX_TOKENS) -> dict:
    payload = {
        "model": model,
        "stream": False,
        "temperature": TEMPERATURE,
        "max_tokens": max_tokens,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    retries = 5
    backoff = [30, 60, 120]   # 限流退避（30s 起，5 次重试；防上游 RPM 限流 529）
    for attempt in range(retries):
        try:
            resp = requests.post(api_url, json=payload, headers=headers,
                                 timeout=TIMEOUT)
            if resp.status_code == 429 or resp.status_code == 529:
                wait = backoff[min(attempt, len(backoff) - 1)]
                print(f"  [warn] {tag} HTTP {resp.status_code} 限流，"
                      f"{wait}s 后重试（{attempt+1}/{retries}）",
                      file=sys.stderr)
                time.sleep(wait)
                continue
            if resp.status_code != 200:
                raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:300]}")
            data = resp.json()
            # v4.1: reasoning 模型 content 可能为 null，burn 完 max_tokens 全在 thinking
            msg = (data.get("choices") or [{}])[0].get("message") or {}
            if msg.get("content") is None and msg.get("reasoning_content"):
                msg["content"] = msg["reasoning_content"]
                data["choices"][0]["message"] = msg
                print(f"  [note] {tag} reasoning-only 响应，已回退 reasoning_content",
                      file=sys.stderr)
            return data
        except Exception as e:  # noqa: BLE001
            if attempt >= retries - 1:
                raise
            wait = backoff[min(attempt, len(backoff) - 1)]
            print(f"  [warn] {tag} 调用失败（{e}），{wait}s 后重试",
                  file=sys.stderr)
            time.sleep(wait)
    raise AssertionError("unreachable")


def extract_json_array(content: str) -> list[dict]:
    """从模型输出中抠出 JSON 数组（容忍 ```json 围栏与前后杂文）。"""
    text = content.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    start, end = text.find("["), text.rfind("]")
    if start < 0 or end <= start:
        raise ValueError(f"输出中无 JSON 数组: {content[:200]!r}")
    return json.loads(text[start : end + 1])


def _lenient_object(text: str, start: int) -> dict:
    """raw_decode + 尾逗号容错：模型常犯 "…",\s*} 尾逗号错，逐次剪除重试。"""
    seg = text[start:]
    for _ in range(10):
        try:
            obj, _ = json.JSONDecoder().raw_decode(seg)
            return obj
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
    """v5.3：从输出中抠出第一个完整 JSON 对象（单题输出；raw_decode 抗多 JSON/示例污染）。"""
    text = content.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```\s*$", "", text)
    start = text.find("{")
    if start < 0:
        raise ValueError(f"输出中无 JSON 对象: {content[:200]!r}")
    return _lenient_object(text, start)


def encode_image(img_path: Path) -> str:
    mime = mimetypes.guess_type(img_path.name)[0] or "image/jpeg"
    b64 = base64.b64encode(img_path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{b64}"


# ---------------------------------------------------------------------------
# 出题批次
# ---------------------------------------------------------------------------
def load_samples(path: Path = SAMPLES) -> list:
    if not path.exists():
        sys.exit(f"样本清单不存在：{path}\n"
                 f"请先运行 benchmark/t2i/eval_sample.py")
    rows = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            rows.append(json.loads(line))
    return rows


def load_constraint_map(path: Path) -> dict:
    """v5.3：合并硬约束清单 jsonl → {实例名: 记录}（记录含编号约束列表）。"""
    m = {}
    with path.open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                rec = json.loads(line)
                m[rec["instance"]] = rec
    return m


PROBE_CASES = SUB_DIR / "data" / "density_probe" / "probe_cases_v4.jsonl"


def load_probe_cases(path: Path) -> dict:
    """v5.3：出题输入的 desc/paths 与探针输入对齐 → {概念名: case}。"""
    m = {}
    if path.exists():
        with path.open(encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    c = json.loads(line)
                    m[c["name"]] = c
    return m


# v5.3 判分项词表（与 synthesize_prompt_gen_v5.3.md 附录一致；判分侧权威源待
# eval_score.py v5.3 重建后切过去）
# v5.4 词表（2026-08-28 结构清理：删 resolution/emotional_expression/noise；
# 拆 noise -> detail_richness + artifacts；anatomical_fidelity 挪质量组；style_control 挪对齐组）
ALIGN_FACETS = {"subject_prominence", "quantity", "facial_expression",
                "material_properties", "color", "shape", "size",
                "contact_interaction", "noncontact_interaction",
                "fullbody_action", "spatial_2d", "spatial_3d",
                "composition_relationship", "difference_similarity",
                "containment", "real_world_scene", "virtual_scene",
                "style_control"}
QUALITY_FACETS = {"physical_logic", "material_texture", "detail_richness",
                  "artifacts", "edge_clarity", "naturalness",
                  "anatomical_fidelity"}
AESTHETIC_FACETS = {"composition", "color_harmony", "lighting_atmosphere"}


def audit_v53(q: dict, constraints: list, exact10: bool = False,
              v56: bool = False, v57: bool = False) -> list:
    """v5.3 结构审计（§6.10 机审增强的第一层）：只告警不删，保留人工复核。

    constraints = 该实例的合并清单（提供编号上界与 scope 元数据）；
    exact10=True 对应 v5.5/v5.6（保真检查恰好 10 条；v5.5.3：alignment_checks
    出题端废除，输出即告警）；False 对应 v5.3（5~7 条 + 对齐检查 2~4 条）；
    v56=True 追加镜头审计（每条检查标镜头 ∈ K1~K5；清单镜头 ≥3 种时检查
    须覆盖 ≥3 种镜头且单镜头 ≤4 条——防单镜头聚集）；
    v57=True 对应线索制（条数 5~16 变长、无 rubric 档位、镜头覆盖 ≥3 种
    但不再限单镜头条数——线索是判分坐标不是得分项）。"""
    n_constraints = len(constraints)
    warns = []
    if not q.get("gen_prompt"):
        warns.append("缺 gen_prompt")
    if q.get("invocation") not in ("L2", "L3"):
        warns.append(f"invocation {q.get('invocation')!r} 不在 L2/L3")
    fc = q.get("fidelity_checks") or []
    if exact10:
        if len(fc) != 10:
            warns.append(f"fidelity_checks 数 {len(fc)} 应恰好 10 条（v5.5）")
    elif v57:
        if not 5 <= len(fc) <= 16:
            warns.append(f"fidelity_checks 数 {len(fc)} 不在 5~16（v5.7 变长）")
    elif not 5 <= len(fc) <= 7:
        warns.append(f"fidelity_checks 数 {len(fc)} 不在 5~7")
    for i, c in enumerate(fc):
        an = c.get("anchor") or {}
        refs = an.get("constraint_refs") or []
        if an.get("type") == "derivation":
            if not an.get("derivation_chain"):
                warns.append(f"fidelity[{i}] 推导型缺 derivation_chain")
            if not refs:
                warns.append(f"fidelity[{i}] 推导型也应有起点编号 constraint_refs")
        elif not refs:
            warns.append(f"fidelity[{i}] 缺 constraint_refs")
        for r in refs:
            rs = str(r).strip().strip("[]（）()").lstrip("Cc").strip()
            if not rs.isdigit() or not 1 <= int(rs) <= n_constraints:
                warns.append(f"fidelity[{i}] 锚点 {r!r} 不在清单编号 1~{n_constraints}")
                break
        if not v57:                      # v5.7 线索制无档位描述
            rub = c.get("rubric") or {}
            for k in ("tier_0", "tier_1", "tier_2"):
                if not rub.get(k):
                    warns.append(f"fidelity[{i}] rubric 缺 {k}")
                    break
        if not c.get("visibility_requirement"):
            warns.append(f"fidelity[{i}] 缺 visibility_requirement")
        if q.get("invocation") == "L2" and an.get("type") == "derivation":
            warns.append(f"fidelity[{i}] L2 题不得有推导链")
    # v5.5 scope 闭环：锚定了变体约束（清单 scope 非空）的检查，题面必须点名
    # 该子变体，且 variants_used 登记同名
    scope_map = {i: str(c.get("scope")).strip()
                 for i, c in enumerate(constraints, 1)
                 if c.get("scope")}
    if scope_map:
        gp = q.get("gen_prompt") or ""
        for i, c in enumerate(fc):
            refs = (c.get("anchor") or {}).get("constraint_refs") or []
            need = {scope_map[int(str(r).strip().strip("[]（）()").lstrip("Cc").strip())]
                    for r in refs
                    if str(r).strip().strip("[]（）()").lstrip("Cc").strip().isdigit()
                    and int(str(r).strip().strip("[]（）()").lstrip("Cc").strip()) in scope_map}
            for sc in need:
                if sc not in gp:
                    warns.append(f"fidelity[{i}] 锚定 scope={sc!r} 的变体约束，"
                                 f"题面未点名该子变体")
                if sc not in str(c.get("variants_used") or ""):
                    warns.append(f"fidelity[{i}] 锚定 scope={sc!r} 的变体约束，"
                                 f"variants_used 未登记")
    ac = q.get("alignment_checks") or []
    if (exact10 and v56) or v57:
        # 镜头闭环：每条检查/线索必须标注镜头；清单镜头 ≥3 种时须覆盖 ≥3 种
        # （清单镜头不足 3 种的稀疏概念不做覆盖要求）。
        # v5.6 另限单镜头 ≤4 条（逐条判分防聚集）；v5.7 线索制不限单镜头条数。
        lens_vals = set(LENS_NAMES)
        ls = [str(c.get("lens") or "").strip() for c in fc]
        for i, l in enumerate(ls):
            if l not in lens_vals:
                warns.append(f"fidelity[{i}] 镜头 {l!r} 不在 K1~K5")
        supply = {str(c.get("lens") or "").strip() for c in constraints
                  if str(c.get("lens") or "").strip() in lens_vals}
        cnt = {}
        for l in ls:
            if l in lens_vals:
                cnt[l] = cnt.get(l, 0) + 1
        if len(supply) >= 3:
            if len(cnt) < 3:
                warns.append(f"镜头分布不足：清单含 {len(supply)} 种镜头，"
                             f"检查只覆盖 {len(cnt)} 种（应 ≥3 种）")
            over = {k: v for k, v in cnt.items() if v > 4}
            if v56 and over:
                warns.append(f"镜头分布过聚：{over}（单镜头应 ≤4 条）")
    if exact10:
        # v5.5.3（2026-08-29 拍板）：alignment_checks 出题端废除——判官基于
        # 题面×生成图直判 Alignment 18 项全量（无规定记 N/A），与质量/美感同制
        if ac:
            warns.append("alignment_checks 已废除（v5.5.3），不应输出")
    else:
        if not 2 <= len(ac) <= 4:
            warns.append(f"alignment_checks 数 {len(ac)} 不在 2~4")
        for i, c in enumerate(ac):
            if c.get("facet") not in ALIGN_FACETS:
                warns.append(f"alignment[{i}] 未知判分项 {c.get('facet')!r}")
            if not all((c.get("rubric") or {}).get(k)
                       for k in ("tier_0", "tier_1", "tier_2")):
                warns.append(f"alignment[{i}] rubric 缺档")
    # v5.4.4：quality_facets/aesthetic_facets 已废除（判分侧全量判 + 判官定 N/A），
    # 字段残留只提示不告警
    lc = q.get("leak_check") or {}
    rows = lc.get("对照表") or []
    if len(rows) < n_constraints:
        warns.append(f"leak_check 对照表 {len(rows)} 行 < 清单 {n_constraints} 条")
    bad = [r for r in rows
           if str(r.get("题面中是否出现", "")).strip() not in ("无", "")]
    if bad:
        warns.append(f"leak_check 存在泄漏: {bad}")
    if "无" not in str(lc.get("结论", "")):
        warns.append(f"leak_check 结论异常: {lc.get('结论')!r}")
    for f in ("gate_spec", "evidence_audit", "expected_failure_modes",
              "probe_dims", "critical"):
        if f in q:
            warns.append(f"含已废字段 {f}")
    return warns


def audit_v60(q: dict, mix: dict | None = None,
              gen_prompt_key: str = "gen_prompt",
              main_domain: str | None = None) -> list:
    """v6.0 结构机审（推理 DAG 文本制，2026-08-30 七维新版门槛）：题级层级枚举、
    层级依据、DAG 文本非空且含推导箭头与 [结论] 节点；量化门槛机审项 =
    前提标注数（L1/L2 ≥3、L3 ≥8）、组合标注（L1 起 ≥1 无条件）、[结论] 数
    （L1 ≥4 / L2 ≥5 / L3 ≥6）、L3 必含 [不得画]；知识节点域标注
    （知识·域 ∈ 29 域菜单）与知识跨度去重计数（L1/L2/L3 ≥2/3/4 域）。
    反刻板、强约束、长尾、牵扯、场景复杂度来源计数等语义判据不在机审范围，
    走抽样复审。

    只告警不删（保留人工复核），与 audit_v53 同约定。"""
    warns = []
    gp = str(q.get(gen_prompt_key) or "")
    if str(q.get("status") or "") == "cannot_construct":
        if not str(q.get("notes") or "").strip():
            warns.append("cannot_construct 须在 notes 写明卡住的维度")
        return warns
    if not gp:
        warns.append("缺 gen_prompt")
    lvl = q.get("level")
    if lvl not in V60_LEVELS:
        warns.append(f"level {lvl!r} 不在 L1/L2/L3")
    if not str(q.get("level_reason") or "").strip():
        warns.append("缺 level_reason")
    rs = str(q.get("reasoning") or "")
    if not rs.strip():
        warns.append("缺 reasoning")
    else:
        if "→" not in rs and "->" not in rs:
            warns.append("reasoning 无推导箭头（→）")
        if "[结论]" not in rs:
            warns.append("reasoning 无 [结论] 节点")
        if "（前提）" not in rs:
            warns.append("reasoning 无（前提）标注（题面锁定的前提须在根节点显式标注）")
        # 前提/组合/结论计数按标注数（量化门槛机审；重复标注会重复计数，从宽）
        n_pre, n_comb = rs.count("（前提）"), rs.count("（组合）")
        n_conc, n_neg = rs.count("[结论]"), rs.count("[不得画]")
        min_pre = {"L1": 6, "L2": 8, "L3": 8}.get(lvl)
        if min_pre and n_pre < min_pre:
            warns.append(f"{lvl} 门槛：前提标注 {n_pre} < {min_pre}")
        min_conc = {"L1": 5, "L2": 6, "L3": 6}.get(lvl)
        if min_conc and n_conc < min_conc:
            warns.append(f"{lvl} 门槛：[结论] {n_conc} < {min_conc}（[不得画] 不计入）")
        if lvl == "L3" and n_neg < 1:
            warns.append("L3 门槛：至少一个 [不得画] 负向结论")
        if "[不得画]" in rs and "混淆源" not in rs:
            warns.append("[不得画] 必须注明混淆源（钉在混淆点，禁止任意缺席）")
        # 知识节点域标注与跨度计数（七维机审）
        if "（知识）" in rs:
            warns.append("存在旧记号（知识）：应标注所属域（知识·域）")
        kinds = {d.strip().split("；域·")[0]
                 for d in re.findall(r"（知识·([^）]+)）", rs)}
        min_span = {"L1": 3, "L2": 4, "L3": 4}.get(lvl)
        if min_span and len(kinds) < min_span:
            warns.append(f"{lvl} 门槛：知识跨度 {len(kinds)} 类 < {min_span}"
                         f"（按（知识·类别）去重计数，枚举见第二节知识类别表，"
                         f"可自造四字类名；主概念固有知识计 1 类）")
    if "notes" in q and not isinstance(q["notes"], (str, type(None))):
        warns.append("notes 应为字符串")
    # 统计字段结构校验：枚举外自造类名放行（自扩展政策，入批次统计回路）
    ct = str(q.get("combo_type") or "").strip()
    if not ct:
        warns.append("combo_type 为空（第二节组合类型表选一，可自造简短类名）")
    for f, name in (("scene_types", "场景复杂度来源"), ("hop_types", "跳类型")):
        v = q.get(f)
        if not isinstance(v, list) or not v or any(not str(x).strip() for x in v):
            warns.append(f"{f} 应为非空字符串数组（{name}多选，可自造简短类名）")
    wps = q.get("weak_points")
    if not isinstance(wps, list) or not wps:
        warns.append("weak_points 应为非空数组（第二节弱项枚举多选、去重）")
    else:
        min_wp = {"L1": 1, "L2": 2, "L3": 3}.get(lvl, 0)
        if len(set(map(str, wps))) < min_wp:
            warns.append(f"{lvl} 门槛：弱项 {len(set(map(str, wps)))} < {min_wp}（去重）")
    pt = q.get("premise_types")
    if not isinstance(pt, list) or not pt:
        warns.append("premise_types 应为非空数组（第二节前提类别表多选）")
    else:
        min_pt = {"L1": 3, "L2": 4, "L3": 4}.get(lvl, 0)
        if len({x for x in pt}) < min_pt:
            warns.append(f"{lvl} 门槛：前提类别 {len(set(pt))} < {min_pt}")
    ht = q.get("hop_types")
    min_ht = {"L1": 2, "L2": 2, "L3": 3}.get(lvl, 0)
    if isinstance(ht, list) and len(set(ht)) < min_ht:
        warns.append(f"{lvl} 门槛：跳类型 {len(set(ht))} < {min_ht}")
    kd = q.get("knowledge_domains")
    if not isinstance(kd, list) or not kd:
        warns.append("knowledge_domains 应为非空数组（29 域菜单名，含主概念域）")
    elif main_domain:
        cross = [d for d in kd if d != main_domain]
        min_cross = {"L1": 1, "L2": 2, "L3": 2}.get(lvl, 0)
        if len(cross) < min_cross:
            warns.append(f"{lvl} 跨域注入 {len(cross)} < {min_cross}"
                         f"（主域 {main_domain} 之外的知识域，长尾世界知识来源）")
    return warns


def v60_classify_combo(q: dict) -> str:
    """削峰统计用：combo_type 字段优先，缺失时关键词兜底，再不行记 '未分类'。"""
    ct = str(q.get("combo_type") or "").strip()
    if ct in V60_COMBO_TYPES:
        return ct
    text = f"{q.get('reasoning') or ''}{q.get('gen_prompt') or ''}"
    for name, kws in V60_COMBO_KEYWORDS:
        if any(k in text for k in kws):
            return name
    return "未分类"


def v60_shape_dispatch(records: list, share: float = 0.2) -> tuple:
    """双向动态调整：削峰（组合主类占比超 share → 禁用 ≤2 类；域 >50% → 避开
    ≤2 域）+ 补峰（done≥3 后，批次未出现的组合主类/场景来源 → 优先征召各 ≤2，
    软约束自然性优先）。返回 (禁组合, 避域, 征召组合, 征召场景)。"""
    import math  # noqa: PLC0415
    done = len(records)
    if not done:
        return [], [], [], []
    cap = max(2, math.ceil(done * share))   # 只出现一次不禁（防小批量过度削峰）
    combo_counts: dict[str, int] = {}
    for r in records:
        combo_counts[r["combo_type"]] = combo_counts.get(r["combo_type"], 0) + 1
    bans = [ct for ct, n in sorted(combo_counts.items(), key=lambda x: -x[1])
            if n >= cap][:2]
    kind_counts: dict[str, int] = {}
    for r in records:
        for d in r.get("kinds", []):
            kind_counts[d] = kind_counts.get(d, 0) + 1
    dom_counts: dict[str, int] = {}
    for r in records:
        for d in r.get("domains", []):
            dom_counts[d] = dom_counts.get(d, 0) + 1
    avoid = [d for d, n in sorted({**kind_counts, **dom_counts}.items(),
                                   key=lambda x: -x[1])
             if n > done * 0.5][:2]
    sol_combos = sol_scenes = []
    if done >= 3:
        sol_combos = [ct for ct in V60_COMBO_TYPES
                      if ct not in combo_counts][:2]
        scene_seen = {t for r in records for t in r.get("scene_types", [])}
        sol_scenes = [t for t in V60_SCENE_TYPES
                      if t not in scene_seen][:2]
    return bans, avoid, sol_combos, sol_scenes


def _v60_stat_fields(q: dict) -> tuple:
    """输出统计字段清洗：越界值丢弃，结构异常置空。"""
    def _keep(v):
        return [str(x).strip() for x in v if str(x).strip()] if isinstance(v, list) else []
    st = _keep(q.get("scene_types"))
    ht = _keep(q.get("hop_types"))
    kd = _keep(q.get("knowledge_domains"))
    pt = _keep(q.get("premise_types"))
    wps = _keep(q.get("weak_points")) or ["其他"]
    return st, ht, wps, kd, pt


def audit(questions: list) -> list:
    """v4 字段/权重/词表审计，返回合格题（不合格的只告警不删——保留人工复核）。"""
    kept = []
    for q in questions:
        warns = []
        checks = q.get("implicit_checks") or []
        if not checks:
            warns.append("缺 implicit_checks")
        elif len(checks) < 2 or len(checks) > 4:
            warns.append(f"implicit_checks 数 {len(checks)} 不在 2~4")
        total = sum(c.get("weight", 0) for c in checks)
        if checks and abs(total - 1.0) > 0.05:
            warns.append(f"implicit_checks 权重和 {total:.2f} != 1.0")
        # v4: 每条 check 必须含 rubric + acceptable_variants
        for i, c in enumerate(checks):
            rubric = c.get("rubric") or {}
            for k in ("0", "1", "2"):
                if not rubric.get(k):
                    warns.append(f"check[{i}] rubric 缺 {k} 档描述")
                    break
            if "acceptable_variants" not in c:
                warns.append(f"check[{i}] 缺 acceptable_variants 字段（可为空数组）")
        tags = q.get("facet_tags") or []
        bad = [t for t in tags if t not in FACET_KEYS]
        if bad:
            warns.append(f"facet_tags 含未知词: {bad}")
        # v4: 3~5
        if not 3 <= len(tags) <= 5:
            warns.append(f"facet_tags 数量 {len(tags)} 不在 3~5")
        # v4: 配比（Alignment ≤2 + Quality ≤1 + Aesthetics ≤2）
        # FACETS 已导入但 audit 函数内未直接导入，用规则独立判定
        from t2i.eval_score import FACETS  # noqa: PLC0415  内联 import 避免循环
        pillar_of = {k: p for k, _, p, _ in FACETS}
        n_align = sum(1 for t in tags if pillar_of.get(t) == "Alignment")
        n_qual = sum(1 for t in tags if pillar_of.get(t) == "Quality")
        n_aest = sum(1 for t in tags if pillar_of.get(t) == "Aesthetics")
        if n_align > 2:
            warns.append(f"facet_tags Alignment 数 {n_align} > 2")
        if n_qual > 1:
            warns.append(f"facet_tags Quality 数 {n_qual} > 1")
        if n_aest > 2:
            warns.append(f"facet_tags Aesthetics 数 {n_aest} > 2")
        # v4: gate_spec 必填
        gs = q.get("gate_spec") or {}
        if not gs.get("required_subjects"):
            warns.append("缺 gate_spec.required_subjects")
        if not gs.get("theme_definition"):
            warns.append("缺 gate_spec.theme_definition")
        # v4: evidence_audit 必含 trigger_check + counterexample_test
        ea = q.get("evidence_audit") or {}
        if not ea.get("trigger_check"):
            warns.append("缺 evidence_audit.trigger_check")
        if not ea.get("counterexample_test"):
            warns.append("缺 evidence_audit.counterexample_test")
        if not q.get("gen_prompt"):
            warns.append("缺 gen_prompt")
        # v4 必含字段（probe_dims/needs_verification 已废）
        for f in ("knowledge_dim", "difficulty",
                  "evidence_audit", "expected_failure_modes",
                  "implicit_checks", "facet_tags", "gate_spec"):
            if not q.get(f):
                warns.append(f"缺字段 {f}")
        # v4 禁用字段
        if "probe_dims" in q:
            warns.append("含已废字段 probe_dims")
        if "needs_verification" in q:
            warns.append("含已废字段 needs_verification")
        if warns:
            print(f"  [audit] {q.get('qid', '?')}: {'; '.join(warns)}",
                  file=sys.stderr)
        kept.append(q)
    return kept


def run(api_url: str, api_key: str, models: list, limit: int,
        prompt_file: Path, quota: str, schema: str = "v5.2",
        constraints_file: str = "", invocation: str = "L2,L3",
        workers: int = 4, max_tokens: int = MAX_TOKENS,
        samples_file: Path = SAMPLES, out_dir: Path = None,
        min_constraints: int = 4, mix: dict = None,
        target_levels: list = None, peak_shave: bool = False,
        lanes: int = 3) -> None:
    system_prompt = prompt_file.read_text(encoding="utf-8")
    v53 = schema in ("v5.3", "v5.5", "v5.6", "v5.7")
    v55 = schema in ("v5.5", "v5.6")
    v56 = schema == "v5.6"
    v57 = schema == "v5.7"
    v60 = schema == "v6.0"
    # v6.0：mix=None 表示不设配额（条数按需）；给定则机审强制层级配额
    cmap: dict = {}
    cases: dict = {}
    inv_slots: list = []
    if v53:
        if not constraints_file:
            sys.exit("v5.3 需要 --constraints 合并硬约束清单")
        cmap = load_constraint_map(Path(constraints_file))
        cases = load_probe_cases(PROBE_CASES)
        parts = [s.strip() for s in invocation.split(",") if s.strip()]
        if any(":" in p for p in parts):
            # 带配比形式（如 L3:7,L2:3）-> 最大余数交错展开为槽位序列，
            # 使各调用方式在批次内均匀分布（L3:7,L2:3 即 L3 占 70%）
            counts = {}
            for part in parts:
                k, v = part.split(":")
                counts[k.strip()] = int(v)
            if set(counts) - {"L2", "L3"}:
                sys.exit(f"--invocation 配比键只允许 L2/L3: {invocation!r}")
            total = sum(counts.values())
            inv_slots = []
            for _ in range(total):
                done = len(inv_slots)
                best, best_deficit = None, None
                for k, want in counts.items():
                    got = sum(1 for s in inv_slots if s == k)
                    deficit = want * (done + 1) / total - got
                    if best_deficit is None or deficit > best_deficit:
                        best, best_deficit = k, deficit
                inv_slots.append(best)
        else:
            inv_slots = parts
        if quota:
            print("  [warn] v5.3 已废难度档，--quota 忽略", file=sys.stderr)
    else:
        system_prompt = system_prompt.replace("{每图题数}", "1")
    # 难度配额（如 L1:2,L2:4,L3:4）→ 按样本槽位轮替下发指定难度
    diff_slots = []
    if quota and not v53:
        counts = {}
        for part in quota.split(","):
            k, v = part.split(":")
            counts[k.strip()] = int(v)
        remaining = dict(counts)
        while any(remaining.values()):
            for k in counts:
                if remaining[k] > 0:
                    diff_slots.append(k)
                    remaining[k] -= 1
    samples = load_samples(samples_file)
    if v53:
        before = len(samples)
        samples = [r for r in samples if r["instance"] in cmap]
        if len(samples) < before:
            print(f"  [note] v5.3: 只保留有约束清单的 {len(samples)} 个样本"
                  f"（滤掉 {before - len(samples)} 个）", flush=True)
        seen: set = set()
        deduped = []
        for r in samples:
            if r["instance"] in seen:
                print(f"  [dedup] {r['sample_id']} {r['instance']}: "
                      f"同实例已有样本，跳过（每实例限一题）",
                      file=sys.stderr)
                continue
            seen.add(r["instance"])
            deduped.append(r)
        if len(deduped) < len(samples):
            print(f"  [note] v5.3 实例去重: {len(samples)} -> {len(deduped)}",
                  flush=True)
        samples = deduped
    if limit:
        samples = samples[:limit]
    print(f"t2i 出题批次：{len(samples)} 个样本 × {len(models)} 模型 = "
          f"{len(samples) * len(models)} 次调用", flush=True)

    out_dir = out_dir or (EVAL_DIR / "synth_gen")   # 版本化批次传独立目录
    raw_dir = out_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    # 单模型 -> 旧路径 questions.jsonl（向后兼容）
    # 多模型 -> 按模型分文件 questions_<safe_name>.jsonl，同模型共享一份
    # v5.3 -> 一律 questions_v53_<模型短名>.jsonl
    if v60:
            out_files = [(m, out_dir / f"questions_v60_{m.split('/')[-1]}.jsonl")
                     for m in models]
    elif v56:
            out_files = [(m, out_dir / f"questions_v56_{m.split('/')[-1]}.jsonl")
                     for m in models]
    elif v57:
            out_files = [(m, out_dir / f"questions_v57_{m.split('/')[-1]}.jsonl")
                     for m in models]
    elif v55:
            out_files = [(m, out_dir / f"questions_v55_{m.split('/')[-1]}.jsonl")
                     for m in models]
    elif v53:
        out_files = [(m, out_dir / f"questions_v53_{m.split('/')[-1]}.jsonl")
                     for m in models]
    elif len(models) == 1:
        out_files = [(models[0], out_dir / "questions.jsonl")]
    else:
        out_files = []
        seen = set()
        for m in models:
            safe = m.replace("/", "_").replace(".", "_")
            path = out_dir / f"questions_{safe}.jsonl"
            if path in seen:
                continue
            seen.add(path)
            out_files.append((m, path))

    n_total = n_skip = n_reject = 0
    counters = {"total": 0, "skip": 0, "reject": 0}
    write_lock = threading.Lock()
    # 断点续跑：按 (样本号, 模型) 跳过已产出（模型自带 qid 不可靠，不用 qid 对账）；
    # 文件以 append 打开
    done_pairs = set()
    for _, p in out_files:
        if p.exists():
            for line in p.open(encoding="utf-8"):
                if line.strip():
                    q = json.loads(line)
                    sid0 = (q.get("_job_sample")
                            or str(q.get("sample_id", "")).split("_")[0])
                    done_pairs.add((sid0, q.get("_generator_model")))
    fout_handles = {p: open(p, "a", encoding="utf-8")  # noqa: SIM115
                    for _, p in out_files}

    jobs = []   # (qid, model, rec, crec, msg)
    job_seq = {}   # sample_id -> 样本序号（削峰分级豁免查目标层级用）
    for i, rec in enumerate(samples, 1):
        sid = rec["sample_id"]
        label = rec["instance"]
        img = EVAL_DIR / rec["image"]
        if not img.exists():
            print(f"  [warn] {sid} 图片缺失，跳过", file=sys.stderr)
            continue
        tax = " | ".join(rec.get("mount_paths") or []) or "（未挂载）"
        diff = diff_slots[i - 1] if i - 1 < len(diff_slots) else None
        crec = cmap.get(label) if v53 else None
        if v60:
            # v6.0：概念名 + 分类路径 + 目标层级，知识由出题模型自身供给
            quota_line = ""
            if mix:
                mix_txt = "、".join(f"{lvl}×{mix[lvl]}" for lvl in V60_LEVELS)
                quota_line = (f"\n本题条目配额：{mix_txt}"
                              f"（合计 {sum(mix.values())} 条）。")
            lvl_line = ""
            if target_levels:
                tl = target_levels[(i - 1) % len(target_levels)]
                lvl_line = (f"\n【目标层级】{tl}（量化门槛必须达到；确实达不到时"
                            "输出 cannot_construct 并在 notes 写明卡在哪个维度，"
                            "不得降级或硬凑）")
            text = (
                f"样本编号：{sid}\n"
                f"【题目编号（qid）】{sid}\n"
                f"【概念名】{label}\n"
                f"【所属分类路径】\n"
                + "\n".join(rec.get("mount_paths") or ["（未挂载）"])
                + lvl_line
                + "\n请按出题指令出 1 道题，严格执行交题前自查清单，"
                  "只输出一个严格 JSON 对象，不要任何其他文字。"
                + quota_line
            )
        if v53 and not crec:
            print(f"  [warn] {sid} {label}: 无合并约束清单，跳过"
                  f"（v5.3 唯一知识来源，不得裸出题）", file=sys.stderr)
            continue
        if v53 and len(crec["constraints"]) < min_constraints:
            print(f"  [warn] {sid} {label}: 清单仅 "
                  f"{len(crec['constraints'])} 条 < {min_constraints}，"
                  f"结构性撑不起锚定检查，跳过", file=sys.stderr)
            continue
        if v53:
            case = cases.get(label) or {}
            inv = inv_slots[(i - 1) % len(inv_slots)]
            cl_lines = []
            for c in crec["constraints"]:
                pol = ("must_have（合法画法必须呈现）"
                       if c["polarity"] == "must_have"
                       else "must_not_have（合法画法不得出现）")
                scope = c.get("scope")
                if scope is None:      # 旧批清单（v5.5 前 variants 字段）
                    var = " / ".join(map(str, c.get("variants") or [])) or "无"
                    scope_line = f"        变体: {var}"
                else:
                    scope_txt = ("通用（对所有合法画法成立）" if not scope
                                 else f"仅子变体: {scope}")
                    scope_line = f"        适用范围: {scope_txt}"
                basis = ("知识库描述" if c.get("source") == "desc"
                         else "领域常识")
                lens = str(c.get("lens") or "").strip()
                lens_line = (f"        镜头: {lens} {LENS_NAMES.get(lens, '')}"
                             if lens in LENS_NAMES else "")
                lines = [f"[{c['id']}] {c['constraint']}",
                         f"        极性: {pol}"]
                if lens_line:
                    lines.append(lens_line)
                lines.append(scope_line)
                lines.append(f"        依据: {basis}")
                cl_lines.append("\n".join(lines))
            text = (
                f"样本编号：{sid}\n"
                f"【概念名】{label}\n"
                f"【所属分类路径】\n"
                + "\n".join(case.get("paths")
                            or rec.get("mount_paths") or [])
                + f"\n【知识库描述】{case.get('desc', '')}\n"
                f"【已核硬约束清单】\n" + "\n".join(cl_lines) + "\n"
                f"【本题调用方式】{'L2 组合' if inv == 'L2' else 'L3 推导'}\n"
                f"请按以上输入出 1 道题，严格执行交题前自查清单，"
                f"只输出一个严格 JSON 对象，不要任何其他文字。\n"
                f"注意：已核硬约束清单经过预审核、供应充分；"
                f"输出中不存在拒题选项，无论调用方式都必须交题。"
            )
        elif not v60:
            text = (
                f"样本编号：{sid}\n"
                f"sample_id：{rec['image'].split('/')[-1]}\n"
                f"query_label：{label}\n"
                f"caption：{rec.get('caption', '')}\n"
                f"taxonomy：{tax}\n\n"
                f"请出 1 道生成题，严格执行证据审计。"
                f"只输出 JSON 数组，不要任何其他文字。"
            )
            if diff:
                text += (
                    f"\n本批次难度配额：{quota}。本题指定难度：{diff}"
                    f"（样本不适配该难度时按难度分级一节回退或拒题，"
                    f"并在 notes 说明）。"
                )
        # v5.3+：无图出题（知识唯一来源=清单；样本图只是抽样依据，不进出题
        # 输入——图细节曾需专门条款防泄漏，删除输入端即根除）。v5.2 老协议
        # 证据审计仍用图。v6.0 同为无图出题（知识由出题模型自身供给）。
        if v53 or v60:
            msg = [{"type": "text", "text": text}]
        else:
            msg = [{"type": "text", "text": text},
                   {"type": "image_url",
                    "image_url": {"url": encode_image(img)}}]
        for model in models:
            # 唯一 sample_id 对应模型独立题号（防同 sha 撞 qid）
            qid = (f"{sid}_{model.split('/')[-1]}" if v53 or v60
                   else f"{sid}_{model.replace('/', '_')}")
            jobs.append((qid, model, rec, crec, msg))
            job_seq[sid] = i - 1

    eff_workers = min(lanes, max(1, len(jobs))) if (v60 and peak_shave) else workers
    print(f"任务池：{len(jobs)} 个 (样本×模型)，已完成 {len(done_pairs)} 个"
          f"，并发 {eff_workers}"
          + (f"（削峰多串行道：{lanes} 道×道内串行）" if v60 and peak_shave else ""),
          flush=True)

    def run_job(job):
        qid, model, rec, crec, msg = job
        sid, label = rec["sample_id"], rec["instance"]
        if (sid, model) in done_pairs:
            print(f"  [resume] {qid} 已存在，跳过", flush=True)
            return
        raw_p = raw_dir / f"{qid}.json"
        result = None
        if raw_p.exists():
            try:
                cand = json.loads(raw_p.read_text(encoding="utf-8"))
                _content = cand["choices"][0]["message"]["content"] or ""
                if _content.strip():
                    result = cand
                    print(f"  [resume] {qid} 复用 raw 响应", flush=True)
            except Exception:  # noqa: BLE001  raw 损坏则重新调
                result = None
        t0 = time.time()
        if result is None:
            print(f"[start] {qid} → {model}", flush=True)
            try:
                result = call_api(api_key, api_url, model, system_prompt,
                                  msg, qid, max_tokens=max_tokens)
            except Exception as e:  # noqa: BLE001
                print(f"  [error] {qid}: {e}", file=sys.stderr)
                return
            raw_p.write_text(json.dumps(result, ensure_ascii=False, indent=2),
                             encoding="utf-8")
        content = result["choices"][0]["message"]["content"] or ""
        if not content.strip():
            print(f"  [error] {qid} content 为空（reasoning_only？），跳过",
                  file=sys.stderr)
            return
        try:
            if v53 or v60:
                questions = [extract_json_object(content)]
            else:
                questions = extract_json_array(content)
        except (ValueError, json.JSONDecodeError) as e:
            print(f"  [error] {qid} JSON 解析失败：{e}", file=sys.stderr)
            try:
                raw_p.unlink()          # 坏响应不留存，避免续跑复用死循环
            except OSError:
                pass
            return
        if ((v53 or v60) and all(not q.get("gen_prompt")
                                  and str(q.get("status") or "") != "cannot_construct"
                                  for q in questions)):
            # reasoning 回退/截断产生的垃圾对象：不入题库，弃 raw 下轮重出
            print(f"  [error] {qid} 输出无 gen_prompt（reasoning 未落成/"
                  f"截断），弃 raw 待重出", file=sys.stderr)
            try:
                raw_p.unlink()
            except OSError:
                pass
            return
        rejects = [q for q in questions if q.get("status") == "reject"]
        for q in rejects:
            print(f"  [reject] {qid}: {q.get('reject_code', '?')} "
                  f"{str(q.get('reason', ''))[:80]}", file=sys.stderr)
        questions = [q for q in questions if q.get("status") != "reject"]
        if v53:
            for q in questions:
                warns = audit_v53(q, crec["constraints"], exact10=v55,
                                  v56=v56, v57=v57)
                if warns:
                    print(f"  [audit] {qid}: {'; '.join(warns)}",
                          file=sys.stderr)
        elif v60:
            for q in questions:
                warns = audit_v60(q, mix, main_domain=rec.get("l1"))
                if warns:
                    print(f"  [audit] {qid}: {'; '.join(warns)}",
                          file=sys.stderr)
        else:
            questions = audit(questions)
        target_path = next(p for m, p in out_files if m == model)
        with write_lock:
            counters["reject"] += len(rejects)
            fout = fout_handles[target_path]
            for q in questions:
                if q.get("skip"):
                    counters["skip"] += 1
                    continue
                q.setdefault("suite", "basic")
                q.setdefault("task", "t2i")   # bagel run_wkbench 按 task 过滤（v5.2 旧约有、v5.3 重构丢失）
                q["_sample_image"] = rec["image"]
                q["_query_label"] = label
                q["_generator_model"] = model
                q["_job_sample"] = sid
                q["_job_qid"] = qid
                if not q.get("qid"):
                    q["qid"] = qid
                fout.write(json.dumps(q, ensure_ascii=False) + "\n")
                fout.flush()
                counters["total"] += 1
        usage = result.get("usage", {})
        print(f"  [done] {qid}: {len(questions)} 题, "
              f"{time.time() - t0:.0f}s, "
              f"tokens={usage.get('total_tokens', '?')} "
              f"(reasoning={usage.get('completion_tokens_details', {}).get('reasoning_tokens', 0)})")

    try:
        if v60 and peak_shave:
            # 多串行道削峰：道内串行（每题禁令来自本道已完成题的实时分布），
            # 道间并行互不共享状态；统计表落 out_dir/batch_stats_lane*.json。
            # 不带旗 = 原一把全提交路径，逐字节不变。
            lane_jobs: dict[int, list] = {}
            for i, job in enumerate(jobs):
                lane_jobs.setdefault(i % lanes, []).append(job)

            def _record_of(qid: str) -> dict | None:
                """从 raw 响应入账（resume 复用同样计入）；失败不阻塞流水线。"""
                try:
                    raw_p2 = raw_dir / f"{qid}.json"
                    cand = json.loads(raw_p2.read_text(encoding="utf-8"))
                    q = extract_json_object(
                        cand["choices"][0]["message"]["content"] or "")
                    st, ht, wps, kd, pt = _v60_stat_fields(q)
                    return {"qid": qid,
                            "combo_type": v60_classify_combo(q),
                            "scene_types": st, "hop_types": ht, "weak_points": wps,
                            "domains": kd, "premise_types": pt,
                            "kinds": sorted({d.strip().split("；域·")[0]
                                             for d in re.findall(
                                                 r"（知识·([^）]+)）", str(q.get("reasoning") or ""))}),
                            "node_domains": sorted({d.strip().split("；域·")[1]
                                                     for d in re.findall(
                                                         r"（知识·([^）]+)）", str(q.get("reasoning") or ""))
                                                     if "；域·" in d})}
                except Exception:  # noqa: BLE001
                    return None

            def run_lane(lane_id: int, ljobs: list):
                records = []
                for job in ljobs:
                    qid, model, rec, crec, msg = job
                    bans, avoid, sol_c, sol_s = v60_shape_dispatch(records)
                    # 主域豁免：避让名单剔除本题概念的主概念域（分类路径 l1）——
                    # 主域必须申报且必然使用，撞上即结构性 cannot_construct
                    # （r11 实测 21 例：钢化玻璃撞材料与物质、语用学撞知识与学科等）
                    if rec.get("l1"):
                        avoid = [d for d in avoid if d != rec.get("l1")]
                    # 分级豁免：目标层级 L3 的题豁免镜面类禁令（弱点乘积素材），
                    # 但本道镜面计数 ≥2 时不豁免（防整批回退镜面堆叠）
                    tgt_lvl = (target_levels or [None]) * 10
                    idx_lvl = tgt_lvl[(job_seq.get(rec["sample_id"], 0)) % len(tgt_lvl)] if job_seq else None
                    if idx_lvl == "L3":
                        n_mirror = sum(1 for r in records
                                       if r["combo_type"] in ("光学媒介", "光学传播"))
                        if n_mirror < 2:
                            bans = [b for b in bans
                                    if b not in ("光学媒介", "光学传播")]
                    adj = ""
                    if bans or avoid:
                        parts = []
                        if bans:
                            parts.append("不得使用的组合主类：" + "、".join(bans))
                        if avoid:
                            parts.append("知识跨度尽量避开的类别/域：" + "、".join(avoid))
                        adj += ("【削峰禁令】" + "；".join(parts)
                                + "。（组合主类禁令为硬约束，确实无法不破禁出题时输出 "
                                  "cannot_construct，不得硬凑；类别/域避让为软约束——"
                                  "仅当概念存在等价替代知识来源时避开，主概念域及其"
                                  "核心知识类别不受此限）\n")
                    if sol_c or sol_s:
                        parts = []
                        if sol_c:
                            parts.append("优先使用的组合主类：" + "、".join(sol_c))
                        if sol_s:
                            parts.append("优先纳入的场景复杂度来源：" + "、".join(sol_s))
                        adj += ("【补峰征召】" + "；".join(parts)
                                + "。（软约束：概念与征召类无自然视觉交汇时可选其他，"
                                  "在 notes 注明未应召原因）\n")
                    ban_line = adj
                    msg2 = [{"type": "text",
                             "text": msg[0]["text"].replace(
                                 "\n请按出题指令出 1 道题",
                                 "\n" + ban_line + "请按出题指令出 1 道题", 1)}]
                    run_job((qid, model, rec, crec, msg2))
                    rec_obj = _record_of(qid)
                    if rec_obj:
                        records.append(rec_obj)
                (out_dir / f"batch_stats_lane{lane_id}.json").write_text(
                    json.dumps(records, ensure_ascii=False, indent=1),
                    encoding="utf-8")
                print(f"  [lane{lane_id}] {len(records)} 题入账 -> "
                      f"batch_stats_lane{lane_id}.json", flush=True)

            with ThreadPoolExecutor(max_workers=max(1, len(lane_jobs))) as ex:
                list(ex.map(lambda kv: run_lane(*kv), lane_jobs.items()))
        else:
            with ThreadPoolExecutor(max_workers=workers) as ex:
                list(ex.map(run_job, jobs))
    finally:
        for fh in fout_handles.values():
            fh.close()

    out_paths = ", ".join(str(p) for _, p in out_files)
    print(f"\n完成：{counters['total']} 题（{counters['skip']} 题类型不适配被 "
          f"skip，{counters['reject']} 题被模型拒题）-> {out_paths}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit", type=int, default=0,
                    help="限样本数；0=全量")
    ap.add_argument("--models", type=str, default="",
                    help="v4.1: 逗号分隔多模型轮转，留空走 Galaxy 单模型旧用法")
    ap.add_argument("--api-url", type=str, default="",
                    help="v4.1: 自定义 API URL，默认 modelhub 网关 4001")
    ap.add_argument("--api-key", type=str, default="",
                    help="v4.1: 自定义 API KEY，默认从 MODELHUB_KEY/GALAXY_API_KEY 取")
    ap.add_argument("--prompt", type=str, default="",
                    help="出题 prompt 文件；默认 synthesize_prompt_gen.md")
    ap.add_argument("--quota", type=str, default="",
                    help="难度批次配额（如 L1:2,L2:4,L3:4），按样本槽位下发")
    ap.add_argument("--schema",
                    choices=["v5.2", "v5.3", "v5.5", "v5.6", "v5.7", "v6.0"],
                    default="v5.2",
                    help="出题协议版本；v5.3 = 四维判分+硬约束清单锚定；"
                         "v5.5 = v5.3 + 无图出题 + 恰好 10 条 + QIB 刻度 + 粒度一致规则 + 全量判分；"
                         "v6.0 = 条目制（类别×取值×层级，隐式蕴含收编进对齐，"
                         "无清单、知识由出题模型自身供给）")
    ap.add_argument("--mix", type=str, default="",
                    help="v6.0：条目层级配额（如 L1:1,L2:3,L3:6，机审强制）；"
                         "留空 = 不设配额，条数按题目难度需要（默认）")
    ap.add_argument("--target-levels", type=str, default="",
                    help="v6.0：目标层级轮转序列（如 'L3,L3,L2' 按样本轮转下发；"
                         "留空 = 出题模型自选层级（默认））")
    ap.add_argument("--constraints", type=str, default="",
                    help="v5.3：合并硬约束清单 jsonl（按实例名，探针产物）")
    ap.add_argument("--invocation", type=str, default="L3:7,L2:3",
                    help="v5.3/v5.5：调用方式槽位（L2/L3），按样本轮转；"
                         "支持配比形式 L3:7,L2:3（默认，L3 占 70%%）")
    ap.add_argument("--peak-shave", action="store_true",
                    help="v6.0 多串行道削峰派发：道内串行、道间并行，每题按本道已完成题的组合分布下发禁用主类/避开域（默认关=一把全提交）")
    ap.add_argument("--lanes", type=int, default=3,
                    help="削峰道数（每道建议 ≥5 题）")
    ap.add_argument("--workers", type=int, default=4,
                    help="并发调用数（默认 4；串行旧行为传 1）")
    ap.add_argument("--max-tokens", type=int, default=MAX_TOKENS,
                    help="生成长度预算（reasoning 与答案共享；reasoning 长输出"
                         "模型建议 32768，默认 16384）")
    ap.add_argument("--samples", type=str, default="",
                    help="样本清单 jsonl（eval_sample.py 产物）；"
                         "默认 data/samples.jsonl，版本化批次传独立清单")
    ap.add_argument("--out-dir", type=str, default="",
                    help="题库输出目录（含 raw/）；默认 data/synth_gen，"
                         "版本化批次传独立目录")
    ap.add_argument("--min-constraints", type=int, default=4,
                    help="v5.3/v5.5：合并清单条数下限，低于则不进出题"
                         "（3 条清单撑不起 10 条锚定检查，实测结构性失败；"
                         "4 条为迄今成功下限）")
    args = ap.parse_args()

    if args.models:
        # v4.1: 多模型走 modelhub 网关
        models = [m.strip() for m in args.models.split(",") if m.strip()]
        api_url = args.api_url or MODELHUB_URL
        api_key = args.api_key or os.environ.get("MODELHUB_KEY") or MODELHUB_KEY
    else:
        # 旧用法：Galaxy 单模型
        models = [MODEL]
        api_url = API_URL
        api_key = args.api_key or os.environ.get("GALAXY_API_KEY")
        if not api_key:
            sys.exit("请通过环境变量 GALAXY_API_KEY 提供 API key")

    prompt_file = Path(args.prompt) if args.prompt else PROMPT_FILE
    if args.schema == "v5.3" and not args.prompt:
        prompt_file = SUB_DIR / "synthesize_prompt_gen_v5.3.md"
    elif args.schema == "v5.5" and not args.prompt:
        prompt_file = SUB_DIR / "synthesize_prompt_gen_v5.5.md"
    elif args.schema == "v5.6" and not args.prompt:
        prompt_file = SUB_DIR / "synthesize_prompt_gen_v5.6.md"
    elif args.schema == "v5.7" and not args.prompt:
        prompt_file = SUB_DIR / "synthesize_prompt_gen_v5.7.md"
    elif args.schema == "v6.0" and not args.prompt:
        prompt_file = SUB_DIR / "synthesize_prompt_gen_v6.0.md"

    mix = None
    if args.schema == "v6.0" and args.mix:
        mix = {}
        for part in args.mix.split(","):
            k, _, v = part.partition(":")
            k, v = k.strip(), v.strip()
            if k not in V60_LEVELS or not v.isdigit():
                sys.exit(f"--mix 键只允许 L1/L2/L3 且值为整数: {args.mix!r}")
            mix[k] = int(v)
    target_levels = None
    if args.schema == "v6.0" and args.target_levels:
        target_levels = [x.strip().upper() for x in args.target_levels.split(",") if x.strip()]
        bad = [x for x in target_levels if x not in V60_LEVELS]
        if bad:
            sys.exit(f"--target-levels 只允许 L1/L2/L3: {bad}")
    run(api_url, api_key, models, args.limit, prompt_file, args.quota,
        schema=args.schema, constraints_file=args.constraints,
        invocation=args.invocation, workers=args.workers,
        max_tokens=args.max_tokens,
        samples_file=Path(args.samples) if args.samples else SAMPLES,
        out_dir=Path(args.out_dir) if args.out_dir else None,
        min_constraints=args.min_constraints, mix=mix,
        target_levels=target_levels,
        peak_shave=args.peak_shave, lanes=args.lanes)


if __name__ == "__main__":
    main()
