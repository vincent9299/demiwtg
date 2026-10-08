"""Replay reviewed decisions for the two fixed batches; no model or network calls.

This is a document exporter, not the repository's production taxonomy pipeline.
The manifest pins both source inputs and the semantic decision snapshot.
"""
from collections import Counter, defaultdict
from hashlib import sha256
from pathlib import Path
import html
import json
import re

BASE = Path(__file__).resolve().parent
ROOTS = [
    '动物', '植物', '真菌', '微观生物结构', '人物与人体', '交通工具',
    '器具与设备', '建筑与设施', '食物与食材', '服饰与造型',
    '艺术与装饰品', '图像与标识', '自然环境与物质', '活动与场景',
    '抽象与文化主题', '待明确主体',
]
SCOPE_STATUSES = {
    '不适用', '未核验到种', '未明确到种', '范围待确认',
    '主体待确认', '品种或栽培类型', '种下名称待核',
}
PLACEMENT_STATUSES = {'按当前导航约定归类', '需要上下文', '需要明确视觉载体'}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(path):
    return sha256(path.read_bytes()).hexdigest()


def read_batch(path, batch):
    chunks = re.split(r'(?m)^(?=[123]\t)', path.read_text(encoding='utf-8'))[1:]
    require(len(chunks) == 3, f'{batch}: 预期三个组')
    rows = []
    for group, chunk in enumerate(chunks, 1):
        columns = chunk.rstrip('\n').split('\t')
        require(len(columns) == 4 and columns[0] == str(group), f'{batch}: 表格结构变化')
        names = columns[3].splitlines()
        require(all(names), f'{batch}: 空概念名')
        for index, name in enumerate(names, 1):
            sid = f'{group}-{index:03d}'
            if batch != 'B1':
                sid = f'{batch}-{sid}'
            rows.append({'batch': batch, 'source_id': sid, 'group': group, 'concept': name})
    require(len({r['concept'] for r in rows}) == len(rows), f'{batch}: 批内出现重复名称')
    return rows


def load_and_validate(base=BASE):
    base = Path(base)
    manifest = json.loads((base / 'combined-manifest.json').read_text(encoding='utf-8'))
    require(manifest['version'] == 'visual-combined-v1', '不支持的版本')
    require([b['batch'] for b in manifest['batches']] == ['B1', 'B2'], '批次顺序变化')
    sources = []
    for batch in manifest['batches']:
        path = base / batch['path']
        require(digest(path) == batch['sha256'], f"{batch['batch']}: 原始输入哈希变化")
        records = read_batch(path, batch['batch'])
        counts = Counter(r['group'] for r in records)
        require([counts[g] for g in (1, 2, 3)] == batch['group_counts'], '组别计数不一致')
        sources.extend(records)
    decisions_path = base / manifest['decision_file']
    require(digest(decisions_path) == manifest['decision_sha256'], '分类判断快照哈希变化；需复核并更新版本清单')
    rows = json.loads(decisions_path.read_text(encoding='utf-8'))
    expected = defaultdict(list)
    for source in sources:
        expected[source['concept']].append({k: source[k] for k in ('batch', 'source_id', 'group')})
    require(len(sources) == manifest['expected_occurrences'], '来源记录数不一致')
    require(len(rows) == len(expected) == manifest['expected_unique_concepts'], '概念数量不一致')
    require([r['concept'] for r in rows] == list(expected), '概念原文或首次出现顺序不一致')
    require(len({r['id'] for r in rows}) == len(rows), '概念 ID 重复')
    for row in rows:
        name = row['concept']
        require(row['sources'] == expected[name], f'{name}: 来源、组别或来源顺序不一致')
        require(row['id'] == expected[name][0]['source_id'], f'{name}: 主 ID 不一致')
        path = row['path']
        require(isinstance(path, list) and len(path) == 3, f'{name}: 路径不是三层')
        require(all(isinstance(p, str) and p.strip() == p and p and '/' not in p for p in path), f'{name}: 非法类目')
        require(path[0] in ROOTS, f'{name}: 未知一级类目')
        require(row['basis'] and row['review_method'], f'{name}: 缺少判断记录')
        require(row['placement_status'] in PLACEMENT_STATUSES, f'{name}: 未知归属状态')
        audit = row['name_audit']
        require(audit['status'] in SCOPE_STATUSES, f'{name}: 未知名称粒度状态')
        require(not audit['reference'] or audit['reference'].startswith('https://'), f'{name}: 非法证据链接')
        if path[0] == '待明确主体':
            require(row['placement_status'] == '需要上下文' and row['note'], f'{name}: 未决项缺少说明')
    overlaps = [r for r in rows if len(r['sources']) > 1]
    require(len(overlaps) == manifest['exact_label_overlap'], '跨批次同名计数不一致')
    old = json.loads((base / 'concept-records.json').read_text(encoding='utf-8'))
    old_by_id = {r['id']: r for r in old}
    require(len(old_by_id) == 464, '首批历史快照缺失')
    moved = [r for r in rows if r['id'] in old_by_id and r['path'] != old_by_id[r['id']]['visual']]
    stats = {
        'version': manifest['version'], 'occurrences': len(sources), 'concepts': len(rows),
        'exact_label_overlap': len(overlaps), 'new_labels': len(rows) - len(old),
        'same_label_different_group': sum(len({s['group'] for s in r['sources']}) > 1 for r in overlaps),
        'level_1': len({r['path'][0] for r in rows}),
        'level_2': len({tuple(r['path'][:2]) for r in rows}),
        'level_3': len({tuple(r['path']) for r in rows}),
        'root_counts': {root: sum(r['path'][0] == root for r in rows) for root in ROOTS},
        'placement_counts': dict(Counter(r['placement_status'] for r in rows)),
        'name_scope_counts': dict(Counter(r['name_audit']['status'] for r in rows)),
        'old_paths_changed': len(moved), 'preservation_checks_passed': True,
        'semantic_accuracy_measured': False, 'independent_semantic_review': False,
        'all_species_verified': False, 'pipeline_model_api_calls': 0,
        'source_sha256': {b['batch']: b['sha256'] for b in manifest['batches']},
        'decision_sha256': manifest['decision_sha256'],
    }
    return manifest, rows, stats, moved


def md(value):
    text = html.escape(str(value), quote=False)
    return re.sub(r'([\\`*_|\[\]])', r'\\\1', text).replace('\n', '<br>')


def provenance(row):
    return '；'.join(f"{s['batch']}:{s['source_id']}（组{s['group']}）" for s in row['sources'])


def markers(row):
    marks = []
    scope = row['name_audit']
    if scope['status'] == '未明确到种':
        marks.append('未到种·' + scope['rank'])
    elif scope['status'] != '不适用':
        marks.append(scope['status'])
    if row['placement_status'] != '按当前导航约定归类':
        marks.append(row['placement_status'])
    if row['concept_form'] not in ('对象或概念名称', '生物名称'):
        marks.append(row['concept_form'])
    return ' '.join(f'【{md(m)}】' for m in marks)


def render_tree(rows, stats, manifest):
    lines = [
        '# 最终版视觉分类树：两批合并 v1', '',
        f"版本日期：{manifest['date']}。本版合并首批 464 条与第二批 2,975 条，保留 **3,439 条来源记录、3,315 个不同原始名称**；124 个跨批次完全同名项在树中显示一次，保留两条来源。", '',
        '**“最终版”表示本次两批合并交付已固定，不表示所有名称已经科学定种或通过图像验证。** 主体无法确定的概念保留在末尾待明确区，抽象主题另列；它们都计入总数，没有隐去。', '',
        f"导航共有 {stats['level_1']} 个一级、{stats['level_2']} 个二级、{stats['level_3']} 个三级分组。三个导航层级不对应固定的纲、目、科；末层概念原名不改。", '',
        '阅读标记：`未到种·…` 为类群或宽泛通名；`范围待确认` 需要原定义；`品种或栽培类型` 与科目不同；`未核验到种` 不代表概念错误，只表示本轮没有完成物种级审定。年龄、性别、部位、行为单独描述，不冒充新的物种。', '',
        '每行保留来源 ID 和原组别。B1 是首批，B2 是新批；组别不解释为难度或稀有度。完全同名仅作显示层聚合，不证明跨批含义完全相同。同义词、译名和带作者的版本均不合并。', '',
        '归类以可见主体为主：实体、部件、图示、活动、环境分开；动物加动作仍以明确的动物为主体。文物与艺术作品优先按作品或文物对象归类；具名桥梁未核结构时统一放在“具名桥梁”。', '',
        '[处理过程与质量检查](09-合并与质量检查.md) · [未到种、歧义与待明确清单](10-需确认与物种粒度.md)', '',
        '## 一级目录', '', '| 一级分组 | 概念数 |', '| --- | ---: |',
    ]
    for index, (root, count) in enumerate(stats['root_counts'].items(), 1):
        lines.append(f'| [{root}](#group-{index:02d}) | {count} |')
    tree = {}
    for index, root in enumerate(ROOTS, 1):
        children = defaultdict(lambda: defaultdict(list))
        for row in rows:
            if row['path'][0] == root:
                children[row['path'][1]][row['path'][2]].append(row)
        if not children:
            continue
        tree[root] = {}
        lines += ['', f'<a id="group-{index:02d}"></a>', '', f'## {root}（{stats["root_counts"][root]}）', '']
        if root == '待明确主体':
            lines += ['以下是保全未决名称的工作区，尚未给出确定主体归属。', '']
        elif root == '抽象与文化主题':
            lines += ['以下概念需要补充视觉载体，不能直接当作单一对象识别类。', '']
        for l2 in sorted(children):
            tree[root][l2] = {}
            lines += [f'### {l2}', '']
            for l3 in sorted(children[l2]):
                items = children[l2][l3]
                tree[root][l2][l3] = [r['id'] for r in items]
                lines += [f'#### {l3}（{len(items)}）', '']
                for row in items:
                    marks = markers(row)
                    note = f" — {md(row['note'])}" if row['note'] else ''
                    lines.append(f"- **{md(row['concept'])}** · `{provenance(row)}` {marks}{note}".rstrip())
                lines.append('')
    ids = [rid for l1 in tree.values() for l2 in l1.values() for leaf in l2.values() for rid in leaf]
    require(Counter(ids) == Counter(r['id'] for r in rows), '渲染树覆盖不一致')
    require(sum(line.startswith('- **') for line in lines) == len(rows), 'Markdown 概念行数量不一致')
    return '\n'.join(lines).rstrip() + '\n', tree


def render_audit(rows, stats):
    lines = ['# 未到种、歧义与待明确清单', '',
        '科、目、纲、属是不同分类等级；“科目”不是一个统一等级。品种、亚种、部位、年龄与行为需要另外描述。此清单记录已识别的问题，不声称穷尽全部潜在名称错误。', '',
        '无证据的生物名称统一保留“未核验到种”，没有批量判为具体物种。沿用首批部分依据；新增条目有来源则列链接，没有来源的判断明确标为本轮名称判断，后续仍可修订。', '',
        '| 名称粒度状态（互斥计数） | 数量 |', '| --- | ---: |']
    for key, count in stats['name_scope_counts'].items():
        lines.append(f'| {key} | {count} |')
    sections = [
        ('已识别为未明确到种', lambda r: r['name_audit']['status'] == '未明确到种'),
        ('生物名称范围待确认', lambda r: r['name_audit']['status'] in ('范围待确认', '种下名称待核')),
        ('缺少主体或归属需要上下文', lambda r: r['placement_status'] == '需要上下文'),
        ('需要明确视觉载体', lambda r: r['placement_status'] == '需要明确视觉载体'),
    ]
    for title, predicate in sections:
        selected = [r for r in rows if predicate(r)]
        lines += ['', f'## {title}（{len(selected)}）', '',
                  '| 原名与来源 | 当前路径 | 说明 | 依据 |', '| --- | --- | --- | --- |']
        for row in selected:
            audit = row['name_audit']
            evidence = f"[来源]({audit['reference']})" if audit['reference'] else md(audit['evidence_type'])
            notes = list(dict.fromkeys(x for x in (row['note'], audit['explanation']) if x))
            lines.append(f"| {md(row['concept'])}<br>{md(provenance(row))} | {md(' / '.join(row['path']))} | {md('；'.join(notes))} | {evidence} |")
    lines += ['', '## 品种、部位与状态怎么处理', '',
        '例如家犬品种与毛色仍放在家犬导航；“普通翠鸟雄鸟”保留生物主体与性别修饰；“冠花贝母种球”放在植物地下器官；“白背亚种”缺少主体，不能凭旧树补成某种鸟。原名均不改。', '',
        '完整的品种、部位和状态标记直接显示在主树，避免再复制一份概念清单；这些形式标记也未做完整语言学标注。首批历史审核见 06、07 文件，它们仅涵盖首批，不能当作本版全量审核统计。', '',
        '本清单的各章节允许交叉：同一名称可以同时有粒度疑问和归属疑问，章节数不能直接相加。顶部状态表按每条概念唯一状态计数。', '']
    return '\n'.join(lines)


def render_report(manifest, rows, stats, moved, base=BASE):
    lines = ['# 两批合并：处理过程与质量检查', '',
        '## pipeline 有没有模型决策', '',
        '**分类设计有模型参与；当前脚本执行没有模型调用。** 上层树、候选归属和本轮复核由当前 Codex 会话中的助手完成，然后保存到 `combined-decisions.json`。`build.py --combined` 只校验固定输入和判断快照，再导出 Markdown / JSON；它不会向 Codex 或任何模型 API 发请求，也不会联网重新判断。', '',
        '原有 `build.py` 和 `audit_visual_leaves.py` 同样是固定判断回放，不是能够自动理解新概念的分类器。不能只替换输入文件就宣称完成新批次分类。', '',
        'Codex 是工作的代理环境，具体模型是另一个配置项；[官方配置说明](https://developers.openai.com/codex/config-basic/)单独定义 `model`。本项目没有配置用于分类调用的模型，本交付也未获得可审计的当前会话精确部署型号或 token 账单，因此不把文档中的示例型号当成实际调用记录。脚本回放不耗模型 token，本轮会话分析会耗 token，不能据此声称整个工作零成本。', '',
        '## 合并范围与保全', '',
        '| 指标 | 数量 |', '| --- | ---: |',
        '| 首批：组 1 / 2 / 3 | 25 / 225 / 214 |',
        '| 新批：组 1 / 2 / 3 | 151 / 1204 / 1620 |',
        '| 两批来源记录 | 3439 |', '| 跨批完全同名 | 124 |',
        '| 本版不同原始名称 | 3315 |', '| 第二批新增名称 | 2851 |',
        f"| 跨批同名但原组别不同 | {stats['same_label_different_group']} |",
        f"| 一级 / 二级 / 三级导航节点 | {stats['level_1']} / {stats['level_2']} / {stats['level_3']} |",
        f"| 首批概念导航路径调整 | {stats['old_paths_changed']} |", '',
        '完全同名项只在视图层合并显示，来源 ID、批次、组别全部保留；这不确认两批意图一定相同。近义词、翻译、拼写差异不合并，例如 Mooncake 与月饼、带作者与不带作者的作品名仍各自存在。', '',
        '输入是按组汇总的二级列表、三级列表和概念列表，没有每个概念的旧路径关系，因此不按列表位置猜配对，也不把弱模型生成的旧分支当作正确答案。首批已整理的视觉方案作为可修改候选，并在同类概念复核时调整；它不是独立审定的标准答案。', '',
        '## 本轮实际步骤', '',
        '1. 复制并冻结两份原始文本，记录 SHA-256；从概念列按原顺序取词，保留来源组别与 ID。',
        '2. 按完整名称建立跨批显示索引，只聚合完全相同的字符串；不改名、不补造概念。',
        '3. 以视觉主体重建候选分组。对艺术作品、遗址、部件和片段名称先做明确判断，再用同类名称规则和字符串匹配辅助批量放置。规则只产候选，不充当科学证据。',
        '4. 将候选按分类展开，阅读类别中的概念列表，修正字面匹配冲突和首批、新批归属不一致的情况。生成与复核均来自同一 Codex 会话，不冒称独立模型复审或专家审定。',
        '5. 对影响归属的部分难例查阅来源；把名称粒度、概念形式、归属状态分开记录。主体不足时进入待明确区，抽象主题保留视觉载体问题。',
        '6. 固定判断快照，再用程序检查原名集合、来源、ID、组别、路径和导出覆盖；生成最终 Markdown。重跑只回放已固定判断。', '',
        '## 复核中处理的具体边界', '',
        '| 概念或情况 | 本版处理及依据 |', '| --- | --- |',
        '| 作册般鼋 | 动物形青铜文物；不当作真实动物，也不猜作容器。[国家博物馆](https://www.chnmuseum.cn/zp/zpml/csp/202112/t20211221_253289.shtml) |',
        '| 葡萄牙战舰 | 刺胞动物名称；不按“战舰”归船。[Smithsonian](https://www.si.edu/object/nmnheducation_10009560) |',
        '| 鼠妇 | 放入甲壳动物，避免按“虫”归昆虫。[自然历史博物馆](https://www.nhm.ac.uk/take-part/outdoor-activities-for-kids/what-lives-under-there) |',
        '| V-22 鱼鹰倾转旋翼机 | 单列倾转旋翼机；不混入直升机。[波音](https://www.boeing.com/defense/military-rotorcraft/v-22-osprey) |',
        '| 蛇头菌 | 调整到鬼笔类真菌，具体俗名含义仍待确认。[生态环境部名录](https://www.mee.gov.cn/xxgk2018/xxgk/xxgk01/201805/W020180926382630924936.pdf) |',
        '| 犀牛（丢勒）、鲁昂大教堂（莫奈） | 按作品名称归绘画，不按字面归动物或建筑。 |',
        '| 普通仰泳虫、圃鹀麦田、芦鹀食种子 | 保留明确的动物主体，不因“仰泳”“麦田”“种子”分别归运动、农田和植物部位。 |',
        '| 冠花贝母种球、内齿轮拉刀、Schooner Rig | 分别按植物地下器官、加工刀具、帆装结构处理。 |',
        '| 蓝金色、白背亚种、Axe木棒 | 保留原名与问题，不猜主体、不偷偷修成新概念。 |', '',
        '## 程序检查与语义质量分开看', '',
        '| 检查 | 本版结果 |', '| --- | --- |',
        '| 两份原始输入哈希 | 通过；与固定批次一致 |',
        '| 原概念名与首次出现顺序 | 通过；没有改名、漏项或新增名称 |',
        '| 每个来源 ID、批次、组别 | 通过；3439 条来源关系逐条一致 |',
        '| 每个显示概念在主树出现一次 | 通过；3315 条 |',
        '| 路径格式、状态字段、已固定判断哈希 | 通过 |',
        '| 交付时 9 项保全与失败保护测试 | 已通过；重复生成内容一致，错误输入阻止写出 |',
        '| 首批原始输入及历史产物 | 24 份既有保全文件内容未变；仅修改入口、脚本及规范等 4 份文件 |',
        '| 独立专家或独立标注集验证 | 尚未完成 |',
        '| 全部生物名称科学定种 | 尚未完成 |',
        '| 图像能否稳定区分类别 | 没有接入图片，本轮未测量 |',
        '| 整体分类准确率 | 未测量，不给出虚构百分比 |', '',
        f"当前有 {stats['placement_counts'].get('需要上下文', 0)} 条需要上下文（其中 {stats['root_counts']['待明确主体']} 条缺少确定主体），另有 {stats['placement_counts'].get('需要明确视觉载体', 0)} 条需要明确视觉载体。名称粒度问题另算，详见 [审核清单](10-需确认与物种粒度.md)。", '',
        '**可保证的是记录保全、可追溯和同一快照可重放；不能把这些等同于分类语义全部正确。** 本版可用于浏览、讨论与后续标注，不应未经独立抽检直接作为 40 万数据的金标准。', '',
        '## 固定的执行规范', '',
        '执行链为：原始输入冻结 → 解析与保全 → 主体及树设计 → 候选分类 → 按类复核与难例查证 → 保存判断和未决状态 → 机械校验 → 导出 Markdown。改变名称范围或引入新批次必须重新经过判断与复核阶段，不能跳过。', '',
        '当前执行 `../../env/bin/python build.py --combined`（默认也是合并版）。旧批次回放使用 `--visual-only`，只更新旧交付。两者都不更新 CSV，也不写正式主数据表。', '',
        '后续若建设 40 万规模的真实模型 pipeline，应按 [业务规范第 9 节](TAXONOMY_REBUILD_SPEC.md#9-不可靠旧树下的-40-万概念重建) 和 [项目强制规范](../PIPELINE_SPEC.md) 实现统一入口；模型配置、prompt 版本、输入输出、token 与费用、拒绝归类状态、独立评测和抽检结果都要落盘。当前未实现这部分，未启动 40 万批处理，也未选择或调用一个外部分类模型。', '',
        '## 输入与判断快照', '',
    ]
    for batch in manifest['batches']:
        lines += [f"- `{batch['batch']}`：`{batch['path']}`；SHA-256 `{batch['sha256']}`。"]
    lines += [f"- 判断快照：`{manifest['decision_file']}`；SHA-256 `{manifest['decision_sha256']}`。", '',
        '## 首批路径变化记录', '', '| ID / 原名 | 首批视觉路径 | 合并版路径 |', '| --- | --- | --- |']
    old = {r['id']: r for r in json.loads((Path(base) / 'concept-records.json').read_text(encoding='utf-8'))}
    for row in moved:
        lines.append(f"| {row['id']} / {md(row['concept'])} | {md(' / '.join(old[row['id']]['visual']))} | {md(' / '.join(row['path']))} |")
    lines += ['', '## 跨批完全同名的显示聚合记录', '', '| 原名 | 两批来源 |', '| --- | --- |']
    for row in rows:
        if len(row['sources']) > 1:
            lines.append(f"| {md(row['concept'])} | {md(provenance(row))} |")
    return '\n'.join(lines) + '\n'


def build_combined(base=BASE):
    base = Path(base)
    manifest, rows, stats, moved = load_and_validate(base)
    tree_md, tree = render_tree(rows, stats, manifest)
    outputs = {
        '08-最终版视觉分类树.md': tree_md,
        '09-合并与质量检查.md': render_report(manifest, rows, stats, moved, base),
        '10-需确认与物种粒度.md': render_audit(rows, stats),
        '08-最终版视觉分类树.json': json.dumps(tree, ensure_ascii=False, indent=2) + '\n',
        'combined-validation.json': json.dumps(stats, ensure_ascii=False, indent=2) + '\n',
    }
    # Do not write any output until all inputs, decisions and rendered coverage pass.
    for name, content in outputs.items():
        temporary = base / (name + '.tmp')
        temporary.write_text(content, encoding='utf-8')
        temporary.replace(base / name)
    return stats
