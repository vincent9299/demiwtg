"""v6.0 machine audit, ported verbatim from eval_synthesize.audit_v60.

只告警不删（保留人工复核），与历史约定一致；warns 进入行的
audit_warnings 字段，最终是否入库由导出与人工复核决定。
"""
import re

V60_LEVELS = ("L1", "L2", "L3")


def audit_v60(q: dict, mix: dict | None = None,
              gen_prompt_key: str = "gen_prompt",
              main_domain: str | None = None) -> list:
    """v6.0 结构机审（推理 DAG 文本制，2026-08-30 七维新版门槛）。

    与 eval_synthesize.audit_v60 逐条一致：题级层级枚举、层级依据、DAG
    文本非空且含推导箭头与 [结论] 节点；前提标注数、组合标注、[结论] 数、
    L3 必含 [不得画]；知识节点域标注与知识跨度去重计数。语义判据不在
    机审范围，走抽样复审。只告警不删。
    """
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


class AuditSynthesized:
    """Attach v6.0 machine-audit warnings; warnings never silently drop questions."""

    def __init__(self, config):
        self.mix = config.get('mix')

    def __call__(self, row):
        if row['status'] not in ('authored', 'cannot_construct', 'rejected'):
            return row
        question = row['question']
        warnings = audit_v60(question, self.mix, main_domain=row.get('main_domain'))
        return {**row, 'audit_warnings': warnings, 'status': 'audited' if row['status'] == 'authored' else row['status'] + '_audited'}
