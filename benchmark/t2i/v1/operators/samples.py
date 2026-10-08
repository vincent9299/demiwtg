"""Sample import, model fan-out and the v6.0 authoring input construction.

v6.0 authors without images: the model supplies its own knowledge from the
entity name plus taxonomy paths (see eval_synthesize.py v6.0 branch, kept as
the historical reference implementation). Sample images remain sampling
provenance only and never enter the authoring request.
"""

V60_LEVELS = ("L1", "L2", "L3")






def author_text(row, config):
    """v6.0 user text: entity, taxonomy paths, optional level/quota/adjustment lines."""
    quota_line = ''
    mix = config.get('mix')
    if mix:
        mix_txt = '、'.join(f'{level}×{mix[level]}' for level in V60_LEVELS if mix.get(level))
        quota_line = (f"\n本题条目配额：{mix_txt}（合计 {sum(mix.values())} 条）。")
    lvl_line = ''
    levels = config.get('target_levels')
    if levels:
        target = levels[row['seq'] % len(levels)]
        lvl_line = (f"\n【目标层级】{target}（量化门槛必须达到；确实达不到时"
                    "输出 cannot_construct 并在 notes 写明卡在哪个维度，"
                    "不得降级或硬凑）")
    adjustment = ''
    if row.get('adjustment'):
        # 历史削峰禁令/补峰征召文本在「请按出题指令」前注入；新批次按样本
        # 显式提供同格式文本（动态分道调度不随框架迁移，历史批次已冻结）。
        adjustment = str(row['adjustment']).strip() + '\n'
    return (
        f"样本编号：{row['sample_id']}\n"
        f"【题目编号（qid）】{row['sample_id']}\n"
        f"【概念名】{row['instance']}\n"
        f"【所属分类路径】\n"
        + "\n".join(row['mount_paths'] or ['（未挂载）'])
        + lvl_line + '\n'
        + adjustment
        + "请按出题指令出 1 道题，严格执行交题前自查清单，"
          "只输出一个严格 JSON 对象，不要任何其他文字。"
        + quota_line
    )
