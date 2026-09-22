"""Read-only notebook views: Markdown, plain tables and native image outputs.

No HTML, model calls, Dataset orchestration, or writes live in this module.
Datasets passed to the views must be materialized checkpoint readers.
"""
from curation.preparation.records import run_state, run_manifest
from collections import Counter
from pathlib import Path


import yaml
from IPython.display import Image, Markdown, display

from curation.preparation.delivery import eligible
from curation.preparation.materials import pixels
from curation.preparation.review_notebooks import display_json
from curation.preparation.records import digest, read, rows, read_record

STATUS = {
    'selected': '材料已选好', 'constructed': '已有题目草稿',
    'valid_task': '结构与引用合法', 'accepted_task': '任务审核通过',
    'target_attached': '已关联待验目标', 'training_ready': '训练目标审核通过',
    'needs_target': '缺少监督目标，不能完成', 'needs_materials': '材料不足',
    'pending_construct': '等待作者响应', 'pending_review_task': '等待任务审核',
    'pending_review_target': '等待目标审核', 'invalid_task': '程序校验未通过',
    'rejected_task': '任务审核未通过', 'rejected_target': '目标审核未通过',
}
CHECKS = {
    'grounding': '判据是否有资料支持', 'knowledge_necessary': '是否需要应用知识推导视觉结果',
    'observable': '结果是否可观察', 'nonleaking': '题面／参考是否未泄漏答案',
    'reasonable_preservation': '编辑保持要求是否合理',
    'instruction_satisfied': '目标是否完成题面', 'all_criteria_satisfied': '目标是否满足全部判据',
    'image_quality_usable': '目标画质是否可用', 'edit_correspondence': '原图／目标是否对应',
    'preservation': '未要求修改的内容是否保持',
}


def md(text):
    display(Markdown(text))


def table(headers, records):
    def cell(value):
        return str(value if value is not None else '—').replace('|', r'\|').replace('\n', '；')
    lines = ['| ' + ' | '.join(map(cell, headers)) + ' |', '| ' + ' | '.join(['---'] * len(headers)) + ' |']
    lines += ['| ' + ' | '.join(map(cell, row)) + ' |' for row in records]
    md('\n'.join(lines))


def case(dataset, case_id):
    values = dataset.take_all() if hasattr(dataset, 'take_all') else list(dataset)
    found = next((r for r in values if r.get('task_id') == case_id), None)
    if found is None:
        raise ValueError(f'没有案例 {case_id!r}；可选：{[r.get("task_id") for r in values]}')
    return found


def snapshot_ref(run, name):
    """Resolve a saved checkpoint without rebuilding its historical graph."""
    record = run_state(Path(run))['stages'].get(name)
    if not record:
        raise ValueError(f'本run尚无 {name} checkpoint；选择已完成run，或先执行上一步。')
    return record['dataset_ref']


def check_step_order(files, previous):
    """Fail early on stale/out-of-order step execution; never schedule operators."""
    last = next(reversed(files.stages), None) if files.stages else None
    if last != previous:
        raise RuntimeError(f'需要先完成 {previous or "初始化"}；当前最后一步为 {last}。'
                           '重新运行初始化，再从上往下执行；已有checkpoint会复用。')
    from importlib import import_module
    graph_version = import_module("curation." + files.branch + ".runtime").graph_version
    from curation.preparation.records import code_version
    if files.manifest['implementation'] != code_version() or files.manifest['graph'] != graph_version(files.branch):
        raise RuntimeError('代码或完整算子链已变化；请重启内核并使用新run。')


def show_plan(plan):
    table(['字段', '实际输入', '含义'], [
        ('task_id', plan['task_id'], '贯穿全流程的案例编号'),
        ('concept', plan['concept'], '这道题涉及的概念'),
        ('task_type', plan['task_type'], 't2i为文生图；edit为修改给定原图'),
        ('split', plan['split'], 'development为开发；train为训练；test须单独冻结'),
        ('rule_family', plan.get('rule_family'), '用于划分隔离的规则家族'),
    ])
    md('**构题意图（intent）**\n\n' + plan.get('intent', '未提供'))
    selection = plan.get('selection', {})
    md('**选材方式**：`' + selection.get('method', '未提供') + '`。' + selection.get('reason', ''))
    if selection.get('item_ids'):
        md('指定材料：' + '、'.join(f'`{i}`' for i in selection['item_ids']))


def show_inputs(plans, items, case_id):
    values = plans.take_all()
    md(f'共 **{len(values)} 个计划**；材料池 **{len(items)} 条**，首版审核放行 **{sum(eligible(i) for i in items)} 条**。'
       '放行计数不代表最终知识发布接口已修复。')
    table(['案例', '概念', '题型', '划分'], [(r['task_id'], r['concept'], r['task_type'], r['split']) for r in values])
    show_plan(case(values, case_id))


def show_image(asset, label):
    md('**' + label + '**')
    raw, _, _ = pixels(asset)
    display(Image(data=raw, width=720))
    md(f'[原图文件]({asset["path"]}) · sha256：`{asset["sha256"]}`')


def show_materials(materials):
    if not materials:
        md('本步没有知识材料。')
    for n, item in enumerate(materials, 1):
        review = item.get('review', {})
        md(f'**资料 {n} · {item["concept"]} · {"文字知识" if item["kind"] == "text" else "知识参考图"}** '
           f'（`{item["item_id"]}`）')
        if item['kind'] == 'text':
            md(item['text'])
        else:
            show_image(item['asset'], '参考图：提供知识，不是待编辑原图或监督目标')
            metadata = item.get('image_metadata') or item['asset'].get('image_metadata')
            if metadata:
                md('**图片索引（机器标签）**\n\n' + display_json(metadata))
        md('**支持范围**：' + str(review.get('support_scope', review.get('scope', '未标明'))))
        md('**限制**：' + str(review.get('limitations') or '未额外记录；仍限于已审核范围'))
        sources = item.get('sources', [])
        if sources:
            table(['来源', '原文长度', '前后文'], [
                (source.get('url') or source.get('title') or source.get('source_id', '来源'),
                 len(source.get('text', '')), '有' if source.get('context_before') or source.get('context_after') else '未提供')
                for source in sources])
            md('候选设计读取已发布正文和来源标识；独立审核还读取引用片段及上下文。各模型阶段的准确输入见原始请求。')
        elif item.get('asset', {}).get('source'):
            source = item['asset']['source']
            md('**图片来源**：' + str(source.get('landing_url') or source.get('content_url') or source.get('url') or source))


def show_draft(row):
    draft = row.get('draft') or {}
    if not draft:
        md('尚无题目草稿。'); return
    md('**给作答模型的题面（instruction）**\n\n' + draft.get('instruction', draft.get('reason', '未生成题面')))
    for key, label in [('condition', '题面固定的情境'), ('knowledge_application', '知识怎样决定可见结果')]:
        if draft.get(key): md('**' + label + '**\n\n' + draft[key])
    if draft.get('edit_type'):
        md(f'**编辑类型**：`{draft["edit_type"]}`；**编辑位置**：{draft.get("anchor", "")}')
        md('**应保持**\n\n' + '\n'.join('- ' + x for x in draft.get('preserve', [])))
    show_criteria(row.get('criteria', draft.get('criteria', [])))


def show_criteria(criteria):
    table(['判据', '可见要求', '看哪个部位', '允许怎样变化', '依据资料号'], [
        (n, r['requirement'], r['observable_region'], r['allowed_variation'], r['evidence'])
        for n, r in enumerate(criteria, 1)])


def show_review(row, name):
    result = row.get(name)
    if not result:
        md('尚无本步审核结果。'); return
    table(['审核项', '结论'], [(CHECKS.get(k, k), '通过' if v is True else '未通过') for k, v in result.get('checks', {}).items()])
    md('**审核理由（完整原文）**\n\n' + result.get('reason', '未提供'))
    provenance = row.get(name + '_provenance', {})
    md('审核者：`' + str(provenance.get('model', provenance.get('reviewer', '未记录'))) + '`；此记录不等于人工golden。')


def show_prompt(name, branch="training"):
    if branch not in {"training", "benchmark"}:
        raise ValueError("Unknown authoring pipeline")
    PROMPTS = Path(__file__).parent.parent / branch / "prompts"
    path = PROMPTS / (('select_edit_source' if name == 'select_edit_source_external' else name) + '.md')
    md(f'### 本算子当前使用的业务prompt：{name}\n\n[源文件]({path})\n\n```text\n{path.read_text()}\n```')
    spec = yaml.safe_load((PROMPTS / 'tasks.yaml').read_text())['prompts'][name]
    md('**标准模板**：业务输出放入 `result`；模板插入实际任务、资料及有序图片。\n\n'
       '```jinja\n' + spec['template'] + '```\n\n'
       f'[完整模板和返回schema]({PROMPTS / "tasks.yaml"})。下方旧案例的实际请求保留原格式，未被当前prompt覆盖。')


def show_step_input(stage, dataset, case_id):
    row = case(dataset, case_id)
    md('### 本步输入')
    if stage == 'select':
        show_plan(row)
    elif stage == 'construct':
        md('**意图**：' + row['plan']['intent'])
        md(f'任务类型：`{row["plan"]["task_type"]}`；消费者：`{row["branch"]}`。')
        show_materials(row['materials'])
        if row.get('edit_source'): show_image(row['edit_source'], '编辑原图：模型需要修改这张图')
        md('构题模型不接收监督目标。')
    elif stage in {'validate', 'review', 'retrieve'}:
        md('**已有题面**\n\n' + row.get('draft', {}).get('instruction', '尚未构题；本步会跳过'))
        if stage == 'review': md('审核同时读取上面的构题资料、原图和完整判据；重新发起独立审核请求。')
        if stage == 'retrieve': md('上面题面就是全部检索查询；隐藏判据和intent不参与排序。')
    elif stage in {'targets', 'review_targets'}:
        md('**待验任务**\n\n' + row.get('draft', {}).get('instruction', '尚未构题'))
        asset = row.get('target') or row['plan'].get('target')
        if asset: show_image(asset, '待验监督目标：只用于目标关联／审核')
        else: md('**未提供目标图。** 本案例不能导出为完成的训练样本。')
    else:
        md('任务状态：`' + row.get('status', '—') + '`；审核、检索及目标状态随本行传入。')


def show_step_output(stage, before, after, case_id):
    old, row = case(before, case_id), case(after, case_id)
    md('### 本步输出')
    current = row.get('status', '—')
    md(f'**{STATUS.get(current, current)}** · `{old.get("status", "plan")} → {current}`')
    added = sorted(set(row) - set(old))
    if added: md('本步新增字段：' + '、'.join('`' + key + '`' for key in added) + '。')
    if row.get('issues'): md('**保留的问题**\n\n' + '\n'.join('- ' + x for x in row['issues']))
    if stage == 'select':
        show_materials(row['materials'])
        if row.get('edit_source'): show_image(row['edit_source'], '已核验的编辑原图（与参考图分开）')
    elif stage == 'construct':
        show_draft(row)
    elif stage == 'validate':
        table(['判据编号', '资料号', '绑定后的稳定知识ID'], [
            (n, r['evidence'], ', '.join(r['knowledge_ids'])) for n, r in enumerate(row.get('criteria', []), 1)])
        md('这一步验证字段与引用合法性；来源是否真正支持判据，由下一步审核判断。')
    elif stage == 'review': show_review(row, 'review_task')
    elif stage == 'retrieve':
        used = {m['item_id'] for m in row.get('materials', [])}
        found = {m['item_id'] for m in row.get('answer_materials', [])}
        lookup = {m['item_id']: m for m in row.get('materials', []) + row.get('answer_materials', [])}
        table(['材料', '类型', '构题使用', '作答检索得到'], [
            (k, lookup[k]['kind'], '是' if k in used else '否', '是' if k in found else '否') for k in sorted(used | found)])
        md('**检索支持缺口**：' + ('有' if row.get('retrieval_gap') else '无已记录的ID缺口；仍需判断语义是否充分'))
        show_materials(row.get('answer_materials', []))
    elif stage == 'targets':
        md('关联结果：' + ('已绑定目标，继续做视觉审核。' if row.get('target') else '尚未绑定合格目标，保留在未完成集合。'))
    elif stage == 'review_targets': show_review(row, 'review_target')
    elif stage == 'export': show_export(row)


def show_export(row):
    md('**可导出完成样本**：' + ('是' if row.get('export_ready') else '否'))
    if row.get('benchmark_question'):
        md('**导出的公开题面**\n\n' + row['benchmark_question']['instruction'])
        md('同时保留判据与知识诊断；当前主评分尚未使用逐项K协议。')
    if row.get('training_sample'):
        table(['顺序', '类型', '角色', '内容', '计算训练loss'], [
            (n, part['type'], part['role'], part.get('text') or part.get('path'), '是' if part['loss'] else '否')
            for n, part in enumerate(row['training_sample']['sequence'], 1)])
        md('参考／原图只作输入；只有监督目标计算loss。当前输出仍是中立格式，未适配实际BAGEL训练读取器。')


def show_summary(run):
    state = run_state(Path(run))
    records = []
    for name in state['stages']:
        values = list(rows(snapshot_ref(run, name)))
        records.append((name, len(values), '；'.join(f'{k}: {v}' for k, v in Counter(r.get('status', r.get('publication_status', r.get('kind', 'record'))) for r in values).items())))
    table(['checkpoint', '记录数', '实际状态'], records)
    md(f'运行目录：`{run}`。上表只读该目录保存的结果，不触发模型调用。')


def show_cases(run):
    state = run_state(Path(run))
    last = 'export' if 'export' in state['stages'] else next(reversed(state['stages']))
    for row in rows(snapshot_ref(run, last)):
        if row.get('status') == 'not_sampled':
            continue
        md(f'## {row.get("plan", {}).get("concept", row.get("concept"))} · {row.get("plan", {}).get("task_type", "尚未选题")} · {row.get("task_id", "") }')
        md('状态：**' + STATUS.get(row['status'], row['status']) + '**。')
        if row.get('issues'): md('\n'.join('- ' + str(x) for x in row['issues']))
        show_discovery(row)
        for field in ('edit_source_search', 'external_source_search', 'edit_source_selection'):
            if row.get(field): md('**' + field + '**\n\n' + display_json(row[field]))
        for n, asset in enumerate(row.get('local_source_candidates', []) + row.get('source_candidates', []), 1):
            show_image(asset, f'检索候选{n}，是否采纳见原图审核')
        show_draft(row)
        md('### 构题依据')
        show_materials(row.get('materials', []))
        if row.get('edit_source'): show_image(row['edit_source'], '编辑原图')
        md('### ' + ('与题目共同选定的训练参考' if row.get('training_input_binding') else '按题面检索到的作答参考'))
        show_materials(row.get('answer_materials', []))
        show_review(row, 'review_task')
        if row.get('target'): show_image(row['target'], '监督目标（不是参考）')
        if row.get('branch') == 'training': show_review(row, 'review_target')
        show_export(row)


def show_audit(dataset, case_id):
    """Full, untruncated fields and requests in a deliberately separate cell."""
    row = case(dataset, case_id)
    md('## 完整原始记录\n\n' + display_json(row))
    for stage in ('design_candidates', 'select_edit_source_external', 'discover', 'select_focus', 'construct', 'review_task', 'review_target'):
        binding = row.get(stage + '_binding')
        if binding:
            md('## 当时实际请求：' + stage + '\n\n' + display_json(read_record(binding['request_ref'])))


def show_discovery(row):
    if row.get('candidate_design'):
        md('**一次候选设计的模型输出（含未采用／不足原因）**\n\n' + display_json(row['candidate_design']))
    found = row.get('discovery', {})
    if found:
        md('**自主发现的知识候选**')
        for n, candidate in enumerate(found.get('opportunities', []), 1):
            md(f'**候选{n}：{candidate["claim"]}**')
            table(['字段', '模型给出的依据'], [(label, candidate.get(key)) for key, label in [
                ('evidence', '来源资料号'), ('condition', '适用条件'), ('visible_result', '可见后果'),
                ('knowledge_gap', '待补足或落实的指导信息'), ('challenge', '知识挑战'),
                ('image_contribution', '配图贡献'), ('limitations', '支持限制')]])
        for value in found.get('excluded', []): md('未采用内容：' + str(value))
        if found.get('reason'): md(found['reason'])
    selection = row.get('focus_selection', {})
    if selection:
        md('**考点取舍（包含未采用原因）**')
        table(['决定', '候选号', '类型', '原因'],
              [('选择', v['opportunity'], v['task_type'], v['reason']) for v in selection.get('selected', [])]
              + [('不选', v['opportunity'], '', v['reason']) for v in selection.get('rejected', [])])
        if selection.get('reason'): md(selection['reason'])
    if row.get('focus'):
        md('**本条任务的考点**\n\n' + row['focus'].get('knowledge_application', row['focus'].get('application', '')))
        md('**待补足或落实的指导信息**\n\n' + row['focus'].get('knowledge_gap', row['focus'].get('knowledge_missing_without_reference', '')))


def show_records(dataset, stage, case_id=None, *, audit=False):
    """Read an already checkpointed Dataset; show the semantic record, not code."""
    values = dataset.take_all() if hasattr(dataset, 'take_all') else list(dataset)
    md(f'**{stage}：{len(values)} 条记录**')
    table(['记录', '概念', '状态', '原因'], [(r.get('task_id', r.get('item_id', '—')), r.get('concept'),
        r.get('status', r.get('publication_status', r.get('kind'))),
        r.get('issues', r.get('delivery_issues', r.get('sampling_reason', '')))) for r in values])
    if not values:
        return
    active = [r for r in values if r.get('status') not in {'not_sampled', 'needs_materials'}]
    row = next((r for r in values if r.get('task_id') == case_id or r.get('unit_id') == case_id), None) if case_id else None
    row = row or (active or values)[0]
    md('**下方完整展示记录**：`' + str(row.get('task_id', row.get('concept'))) + '`。更换CASE_ID可查看其他记录。')
    show_discovery(row)
    if stage == 'candidates' and row.get('training_input_binding'):
        md('**随题目选定并绑定的训练输入（后续不重新检索替换）**\n\n'
           + display_json(row['training_input_binding']))
    if stage in {'knowledge', 'training_materials', 'attempt_materials', 'design', 'candidates', 'delivery', 'windows', 'sample', 'discover', 'focus', 'select'}:
        show_materials(row.get('materials', []))
    if stage in {'training_materials', 'attempt_materials', 'design', 'candidates'}:
        if row.get('target_reference_options'):
            md('**各候选目标可用的原始资料编号（文本可选；不是已选作答输入）**\n\n'
               + display_json(row['target_reference_options']))
        for n, asset in enumerate(row.get('design_targets', []), 1):
            show_image(asset, f'候选监督目标 {n}：供构题审阅，不是事实证据或作答参考')
        if row.get('design_target_search'):
            md(display_json(row['design_target_search']))
    if stage in {'edit_candidates', 'edit_search', 'edit_source', 'edit_external_search', 'edit_external_source'}:
        for n, asset in enumerate(row.get('source_candidates', []), 1):
            show_image(asset, f'编辑原图候选{n}：尚未批准，不能充当知识配图')
        md(display_json({k: row[k] for k in ('edit_source_search', 'external_source_search') if k in row}))
        if row.get('edit_source_selection'): md(display_json(row['edit_source_selection']))
    if row.get('edit_source'): show_image(row['edit_source'], '选定的编辑原图')
    if row.get('draft'): show_draft(row)
    if stage == 'review': show_review(row, 'review_task')
    if stage in {'retrieve', 'bind_inputs'}:
        show_materials(row.get('answer_materials', []))
        md(display_json(row.get('training_input_binding', {}) if stage == 'bind_inputs' else row.get('answer_retrieval', {})))
    if stage in {'targets', 'review_targets'}:
        md(display_json(row.get('target_search', {})))
        if row.get('target'): show_image(row['target'], '监督目标候选：不回流到出题输入')
        show_review(row, 'review_target')
    if stage in {'export', 'ready', 'incomplete'}: show_export(row)
    if audit:
        md(display_json(row))
        for name in ('design_candidates', 'select_edit_source_external', 'discover', 'select_focus', 'select_edit_source', 'construct', 'review_task', 'review_target'):
            binding = row.get(name + '_binding')
            if binding: md('**实际请求：' + name + '**\n\n' + display_json(read_record(binding['request_ref'])))
