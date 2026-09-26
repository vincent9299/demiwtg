"""Structural audit of v6.1 constructed questions, from the protocol's own contract.

The historical strict validator is not in the tree (only its reports). This
audit re-derives the mechanical checks from the v6.1 protocol text itself:
echo fields (edit_type/suite/level/qid), closed enumerations (§6), the
cannot_construct contract (§9) and required field presence (§8). It flags
violations; it never silently drops a question.
"""

TARGETING_TYPES = ("唯一属性直指", "属性组合", "序数定位", "方位定位", "部位定位", "关系定位", "全局范围")
CONSEQUENCE_TYPES = ("光影联动", "倒影同步", "接触受力", "计数联动", "材质互动", "液流运动",
                     "磨损老化", "生态反应", "状态指示", "气象温度", "空间补全", "纹理透视")
SPECIAL_OBLIGATION_TYPES = ("风格笔触", "风格色彩", "风格明暗", "风格材质", "主体完整", "抠图边界",
                            "白底纯净", "杂物清除", "背景语义", "背景透视", "背景景深", "前景边界")
PRESERVATION_TYPES = ("无关区域保持", "计数布局保持", "主体身份保持", "姿态视角保持", "全局光照保持",
                      "风格媒介保持", "色调氛围保持", "物理属性保持")
PREMISE_TYPES = ("定位前提", "改动规定前提", "保持范围前提", "环境场所前提", "时间阶段前提",
                 "数量编组前提", "视角取景前提")
KNOWLEDGE_CATEGORIES = ("概念自身结构", "状态与阶段", "环境交互", "可组合关系", "规约与典故",
                        "物理规律", "化学规律", "生物规律", "地理气象", "天文对应")
HOP_TYPES = ("过程-因果", "规约-标准", "发育-生长", "功能-结构", "物理规律", "文化-规制",
             "关系（组合）", "分类-辨识", "量-守恒", "序-时序", "原理-推演", "化学-反应", "生态-互动")
SCENE_TYPES = ("实体密度", "细节密度", "交互链", "过程时刻", "环境作用", "视点剖示", "规约场景",
               "多实例对比", "纵深层次", "光照时段", "动态要素", "多人物编排")
WEAK_POINTS = ("过度编辑", "改动遗漏", "后果缺失", "定位错改", "身份漂移", "计数漂移", "光影不一致",
               "倒影失同步", "接触悬浮", "字样崩坏", "边缘伪影", "材质失真", "姿态崩坏", "背景重绘",
               "构图漂移", "色调漂移")
KNOWLEDGE_DOMAINS = ("政治、法律与社会制度", "地理与地点", "宗教与信仰", "民族、语言与文化", "人造物体",
                     "动物", "真菌与微生物", "植物", "人物与人体", "行为动作", "食物", "建筑与基础设施",
                     "交通工具", "自然景观", "场景", "节日与符号", "属性与状态", "材料与物质",
                     "时间数量与度量", "文字与信息图形", "声音", "文化艺术与媒介", "知识与学科",
                     "组织机构与社会事件", "体育与游戏", "历史与时代", "品牌与产品", "数字与互联网文化",
                     "医学与健康")
CANNOT_REASON_CODES = ("target_not_visible", "target_ambiguous", "type_incompatible",
                       "insufficient_evidence", "insufficient_level_support",
                       "knowledge_not_unique", "ban_conflict")
ENUM_FIELDS = {'targeting_types': TARGETING_TYPES, 'consequence_types': CONSEQUENCE_TYPES,
               'special_obligation_types': SPECIAL_OBLIGATION_TYPES,
               'preservation_types': PRESERVATION_TYPES, 'premise_types': PREMISE_TYPES,
               'knowledge_categories': KNOWLEDGE_CATEGORIES, 'hop_types': HOP_TYPES,
               'scene_types': SCENE_TYPES, 'weak_points': WEAK_POINTS,
               'knowledge_domains': KNOWLEDGE_DOMAINS}
REQUIRED_FIELDS = ('task', 'qid', 'status', 'edit_instruction', 'edit_type', 'suite', 'level',
                   'level_reason', 'targeting_types', 'consequence_types',
                   'special_obligation_types', 'preservation_types', 'premise_types',
                   'hop_types', 'scene_types', 'knowledge_categories', 'knowledge_domains',
                   'weak_points', 'product_checks', 'evidence_audit', 'evidence_receipt',
                   'reasoning', 'notes', 'cannot_reason_code')


def audit_construct(row):
    """Return audit warnings for a constructed/cannot_construct question row."""
    question = row.get('question')
    if question is None:
        return []
    warns = []
    if str(question.get('status') or '') == 'cannot_construct':
        code = question.get('cannot_reason_code')
        if code not in CANNOT_REASON_CODES:
            warns.append(f"cannot_reason_code {code!r} 不在封闭枚举")
        if str(question.get('edit_instruction') or ''):
            warns.append("cannot_construct 不应携带 edit_instruction")
        if str(question.get('level') or ''):
            warns.append("cannot_construct 的 level 应为空")
        return warns
    for field in REQUIRED_FIELDS:
        if field not in question:
            warns.append(f"缺字段 {field}")
    if question.get('edit_type') != row.get('target_edit_type'):
        warns.append(f"edit_type {question.get('edit_type')!r} != 指定 {row.get('target_edit_type')!r}")
    if question.get('suite') != row.get('suite'):
        warns.append(f"suite {question.get('suite')!r} != 输入 {row.get('suite')!r}")
    if question.get('level') != row.get('target_level'):
        warns.append(f"level {question.get('level')!r} != 输入 {row.get('target_level')!r}")
    if question.get('cannot_reason_code'):
        warns.append("constructed 不应携带 cannot_reason_code")
    for field, allowed in ENUM_FIELDS.items():
        values = question.get(field)
        if not isinstance(values, list):
            warns.append(f"{field} 应为数组")
            continue
        outside = [v for v in values if v not in allowed]
        if outside:
            warns.append(f"{field} 含枚举外值 {outside[:3]}")
    products = question.get('product_checks')
    if row.get('target_level') in ('L1', 'L2') and products:
        warns.append("L1/L2 的 product_checks 必须为空")
    receipt_sources = {r.get('source') for r in (question.get('evidence_receipt') or [])
                       if isinstance(r, dict)}
    if receipt_sources - {'image'}:
        warns.append(f"evidence_receipt 出现非 image 来源 {receipt_sources}")
    return warns


class AuditConstruct:
    """Attach structural warnings; hard echo violations become explicit statuses."""

    def __call__(self, row):
        if row['status'] not in ('authored', 'cannot_construct'):
            return row
        warns = audit_construct(row)
        question = row['question']
        echo_violation = (row['status'] == 'authored' and warns and any(
            '!=' in w for w in warns if 'edit_type' in w or 'suite' in w or 'level' in w))
        status = 'invalid_construct' if echo_violation else (
            'audited' if row['status'] == 'authored' else 'cannot_construct_audited')
        return {**row, 'audit_warnings': warns, 'status': status}
