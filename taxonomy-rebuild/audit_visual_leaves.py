"""Audit name granularity without renaming or splitting any original concept."""
from pathlib import Path
from collections import Counter
import json

BASE = Path(__file__).resolve().parent


def export_audit(rows, base=BASE):
    """按传入的本轮概念记录重放既有名称审核；不联网、不新增物种鉴定。"""
    BASE = Path(base)
    by_id = {r['id']: r for r in rows}
    audit = []


    def add(rid, status, rank, interpretation, note, source=''):
        r = by_id[rid]
        audit.append([rid,r['group'],r['concept'],' / '.join(r['visual']),status,rank,interpretation,note,source])


    add('1-008','明确高阶类群','科','天牛科 Cerambycidae','不是单一物种。','https://www.ncbi.nlm.nih.gov/Taxonomy/Browser/wwwtax.cgi?id=34667&mode=Info')
    add('2-205','明确高阶类群','科','锹甲科 Lucanidae','不是单一物种。','https://www.ncbi.nlm.nih.gov/Taxonomy/Browser/wwwtax.cgi?id=41105&mode=info')
    add('3-081','明确高阶类群','科','叩甲科 Elateridae','不是单一物种。','https://www.ncbi.nlm.nih.gov/Taxonomy/Browser/wwwtax.cgi?id=30009&mode=Info')
    add('3-098','明确高阶类群','科','弄蝶科 Hesperiidae','不是单一物种。','https://www.ncbi.nlm.nih.gov/Taxonomy/Browser/wwwtax.cgi?id=40093&mode=Info')
    add('2-216','明确高阶类群','目','避日目 Solifugae','驼蛛是这类蛛形动物的通名。','https://ncbi.nlm.nih.gov/Taxonomy/Browser/wwwtax.cgi?id=41356&mode=Info')
    add('2-161','明确高阶类群','纲','立方水母纲 Cubozoa','不是某一种水母。','https://www.ncbi.nlm.nih.gov/Taxonomy/Browser/wwwtax.cgi?id=6137&mode=Info')
    add('3-131','明确高阶类群','高阶类群（原名为纲）','甲壳动物','“甲壳纲”是原标签；现代体系中的等级处理不一，不在此固定为纲。')

    genera = [
        ('2-135','Hippocampus','海马属',8156547),
        ('3-160','Conophytum','肉锥花属',7329128),
        ('3-038','Rhamphorhynchus','喙嘴翼龙属',None),
        ('3-112','Elasmotherium','板齿犀属',4830618),
        ('3-190','Kentrosaurus','钉状龙属',4823123),
        ('3-201','Quetzalcoatlus','风神翼龙属',4818447),
        ('3-155','Pterodactylus','翼手龙属',7236977),
        ('3-128','Chalicotherium','爪兽属',4830306),
    ]
    for rid, latin, chinese, key in genera:
        note = '按名称形式是属名，没有明确种名；不根据某属包含多少现行种来偷换名称等级。'
        if rid in ['3-155','3-128']:
            note = '通常对应此属，也可能被宽泛地使用；两种解释都不足以明确物种。'
        source = f'https://www.gbif.org/species/{key}' if key else 'https://www.nhm.ac.uk/our-science/services/collections/palaeontology/pterosaurs.html'
        add(rid,'属级名称','属',chinese+' '+latin,note,source)

    for rid in ['3-053','3-058']:
        add(rid,'类群＋标本或遗存','属＋标本','三角龙属 Triceratops','Mount／化石描述标本形式，未说明三角龙属中的物种。','https://www.nhm.ac.uk/discover/dino-directory/triceratops.html')
    add('3-158','类群＋标本或遗存','翼龙类群＋骨架','翼龙类 Pterosauria','骨架是呈现形式，翼龙本身是较大类群。','https://www.nhm.ac.uk/discover/the-truth-about-pterosaurs.html')

    add('2-014','宽泛通名，未明确到种','多物种类群','深海鮟鱇类','包含多个物种，不宜用该通名代替唯一学名。','https://sanctuaries.noaa.gov/news/2025/sanctuary-symbioses.html')
    add('3-010','宽泛通名，未明确到种','多物种通名','菱背响尾蛇类','未区分东部和西部等具体物种。','https://pubmed.ncbi.nlm.nih.gov/38891681/')
    add('3-040','宽泛通名，未明确到种','多物种通名','跳岩企鹅类','未区分北方、南方等具体物种。','https://www.bas.ac.uk/news/risks-to-penguin-populations-analysed/')

    uncertain = [
        ('2-145','王莲属统称或特定种名','园艺资料常用作 Victoria 属统称；若原定义明确某一种，则可作为物种标签。','https://scbg.cas.cn/hx/201012/t20101227_6734283.html'),
        ('2-109','园艺通名','弹簧草可能对应不同园艺植物，需原学名或原定义。',''),
        ('3-020','英文俗名','Indian Turnip 缺少原始学名，不直接判定为科目类群。',''),
        ('3-022','可作种名的英文通名','RSPB 的 Lapwing 指 Vanellus vanellus；前版标为必然多物种过于绝对。','https://www.rspb.org.uk/birds-and-wildlife/lapwing'),
        ('3-034','可作种名的英文通名','Pike 可作狗鱼类泛称，也常作具体物种简称，需明确原始含义。',''),
        ('3-043','可作种名的英文通名','RSPB 的 Shelduck 指 Tadorna tadorna，不应直接算作科目。','https://www.rspb.org.uk/birds-and-wildlife/shelduck/'),
        ('3-107','地区性名称','需确认是否特指某种斐济鬣蜥；不能直接判定为正式的科、目或属标签。',''),
        ('3-116','属级通称或种名简称','可能泛指 Ctenosaura 类，也可能是具体物种名的简称。',''),
        ('3-135','中文俗名','白毒伞须和物种学名对应；不是可以直接认定的科目名称。',''),
        ('3-161','属名简称或种名简称','存在肉齿菌属的用法，也有文献用肉齿菌指具体种，需原定义。','https://www.nmns.edu.tw/collect/catalog/detail/?id=1742414'),
    ]
    for rid, meaning, note, source in uncertain:
        add(rid,'名称有歧义，待确认','待确认',meaning,note,source)

    headers=['概念ID','原组别','概念原文','视觉版路径','核查结论','名称等级','对应类群或含义','说明','参考依据']
    audit_records = [dict(zip(headers, row)) for row in audit]
    assert len({a[0] for a in audit}) == len(audit), 'Duplicate audited concept ID'
    assert all(by_id[a[0]]['concept'] == a[2] for a in audit)
    (BASE/'leaf-audit.json').write_text(json.dumps(audit_records,ensure_ascii=False,indent=2)+'\n')
    lines = ['# 视觉版末层类群与歧义清单', '',
             '保留原概念名称，仅标注名称粒度。共21条未明确到具体物种，另有10条名称含义待确认。', '',
             '[返回带标注的视觉版分类树](01-视觉版分类树.md)', '',
             '未列入本清单不等于已完成物种核验。品种、部位和年龄或行为描述，不直接算作“科目”。', '']
    for heading, uncertain_group in [('未到具体物种',False),('名称含义待确认',True)]:
        lines += ['## '+heading, '']
        for a in audit_records:
            if (a['核查结论']=='名称有歧义，待确认') != uncertain_group:
                continue
            lines += [f"### {a['概念原文']}〔{a['概念ID']}〕", '',
                      f"- **层级或形式：**{a['名称等级']}；{a['对应类群或含义']}。",
                      f"- **视觉版路径：**{a['视觉版路径']}",
                      f"- **说明：**{a['说明']}"]
            if a['参考依据']:
                lines.append(f"- [参考依据]({a['参考依据']})")
            lines.append('')
    (BASE/'06-视觉版末层类群与歧义清单.md').write_text('\n'.join(lines)+'\n')

    # Retain the distinction between "not a species name" and "too broad a taxon".
    other=[]
    core_ids={a[0] for a in audit}
    for r in rows:
        if r['visual'][0] not in ['动物','植物','真菌'] or r['id'] in core_ids:
            continue
        grain=r['granularity']
        if any(token in grain for token in ['品种','变种','作物类型','部位','遗迹','＋']):
            other.append([r['id'],r['group'],r['concept'],grain,'不因其为品种、部位或状态组合而判成科目；来源物种是否明确是另一问题。'])
    other_lines = ['# 品种部位与状态概念', '',
                   '这些概念包含品种、部位或状态信息，不因其不是纯物种名称而判成粗粒度的科目。来源物种是否明确，需要另行判断。', '',
                   '| 原概念 | ID | 概念形式 |', '|---|---|---|']
    for rid, group, concept, grain, principle in other:
        other_lines.append(f'| {concept} | {rid} | {grain} |')
    (BASE/'07-品种部位与状态概念.md').write_text('\n'.join(other_lines)+'\n')

    summary={
        'scope':'视觉版中动物、植物、真菌三个一级类目，共166条；按名称核查，不是图像鉴定或完整物种名录修订。',
        'active_edition':'visual',
        'counts':dict(Counter(a[4] for a in audit)),
        'named_records_in_audit':len(audit),
        'higher_rank_or_broad_names':sum(a[4]!='名称有歧义，待确认' for a in audit),
        'ambiguous_names':sum(a[4]=='名称有歧义，待确认' for a in audit),
        'excluded_composite_forms':len(other),
        'unchanged_concept_names':True,
        'caveat':'未列入问题清单不等于完成物种级科学核验；品种和部位不等于粗粒度科目。',
    }
    (BASE/'leaf-audit-summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
    return summary


if __name__ == '__main__':
    # 独立调用仅刷新审核清单；完整交付统一运行 build.py。
    rows = json.loads((BASE / 'concept-records.json').read_text())
    print(json.dumps(export_audit(rows), ensure_ascii=False, indent=2))
