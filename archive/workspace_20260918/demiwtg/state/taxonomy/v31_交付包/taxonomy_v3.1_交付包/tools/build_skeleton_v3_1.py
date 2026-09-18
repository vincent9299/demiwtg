# -*- coding: utf-8 -*-
"""骨架 v3.1 = 评审版 v3（29 域，IP 已吸收） + 2026-08-23 九条人工裁定。
只动骨架工件，不动底层数据（数据层迁移已另行落盘）。
"""
import json
from collections import OrderedDict

SRC = '/Users/meng/qwenwork'
IN_JSON = f'{SRC}/世界目录_骨架全览_v3.json'
OUT_JSON = f'{SRC}/世界目录_骨架全览_v3.1.json'
OUT_MD = f'{SRC}/世界目录_骨架全览_v3.1.md'

GROUPS = OrderedDict([
    ('自然世界', ['动物', '植物', '真菌与微生物', '自然景观']),
    ('人与生活', ['人物与人体', '食物', '节日与符号', '医学与健康']),
    ('人造之物', ['人造物体', '交通工具', '材料与物质', '品牌与产品']),
    ('空间与场景', ['建筑与基础设施', '场景', '地理与地点']),
    ('行为与抽象', ['行为动作', '属性与状态', '时间数量与度量', '体育与游戏']),
    ('表达与媒介', ['文字与信息图形', '声音', '文化艺术与媒介', '数字与互联网文化']),
    ('社会与知识', ['组织机构与社会事件', '知识与学科', '政治、法律与社会制度',
                    '宗教与信仰', '民族、语言与文化', '历史与时代']),
])

# 低等生物六分支晋升后的精确计数（底稿实测：各为 1 叶，实例 20/18/17/15/14/14）
LOWER_ORGANISM_L2 = OrderedDict([
    ('微型水生无脊椎动物', {'src': '晋升自低等生物', 'leaves': 1, 'inst': 20}),
    ('扁形动物', {'src': '晋升自低等生物', 'leaves': 1, 'inst': 18}),
    ('海绵动物', {'src': '晋升自低等生物', 'leaves': 1, 'inst': 17}),
    ('线形动物', {'src': '晋升自低等生物', 'leaves': 1, 'inst': 15}),
    ('苔藓虫', {'src': '晋升自低等生物', 'leaves': 1, 'inst': 14}),
    ('轮形动物', {'src': '晋升自低等生物', 'leaves': 1, 'inst': 14}),
])


def resolve(node, note):
    node['mig_status'] = 'resolved'
    node['resolution'] = note
    return node


def main():
    skel = json.load(open(IN_JSON, encoding='utf-8'), object_pairs_hook=OrderedDict)
    pending = skel.pop('_待处置', OrderedDict())
    assert set(pending) == {'东亚人贬称', '低等生物'}, f'待处置清单异常: {list(pending)}'

    # 1+2. 斯芬克斯 / 美人鱼：确认落 文化艺术与媒介，挂入虚构世界与角色分支下
    for name, note in [
        ('斯芬克斯', '裁定：迁文化艺术与媒介/虚构世界与角色（神话形象文化再现）'),
        ('美人鱼', '裁定：迁文化艺术与媒介/虚构世界与角色（实例无一生物实体）'),
    ]:
        resolve(skel['文化艺术与媒介'][name], note)['under_branch'] = '虚构世界与角色'

    # 3. 怪兽：撤销独立迁入，并入 虚构世界与角色/志怪鬼怪形象/民间传说怪物（补鲲鹏1）
    gj = skel['文化艺术与媒介'].pop('怪兽')
    assert gj['inst'] == 17
    fw = skel['文化艺术与媒介']['虚构世界与角色']
    fw['inst'] += 1  # 鲲鹏补入（其余16实例与民间传说怪物重合，不重复计）
    fw.setdefault('sub', OrderedDict())['志怪鬼怪形象']['inst'] += 1  # 民间传说怪物在其下一层
    fw['note_monster'] = '怪兽叶已并入志怪鬼怪形象/民间传说怪物：13实例重合不重复加，龙/麒麟/九尾狐有带限定同名项，仅鲲鹏+1'

    # 4. 怪物卡车：撤销迁移，回归交通工具
    mt = skel['人造物体'].pop('怪物卡车')
    resolve(mt, '裁定：实例全为特技改装真车，撤销迁移，留交通工具域')
    mt['src'] = '原有'
    mt.pop('mig_cat', None)
    skel['交通工具']['怪物卡车'] = mt

    # 5. 商业竞争：留行为动作（整树含价格竞争14实例，非空叶）
    resolve(skel['行为动作']['商业竞争'], '裁定：非空叶（含价格竞争14实例），整树留行为动作')

    # 6. 金融资本：确认落 政治法律制度/经济制度与金融
    resolve(skel['政治、法律与社会制度']['金融资本'], '裁定：迁经济制度与金融分支下')
    skel['政治、法律与社会制度']['金融资本']['under_branch'] = '经济制度与金融'

    # 7. 虎：撤销独立L2，并入 哺乳动物/猫科动物/大型猫科动物/老虎
    hu = skel['动物'].pop('虎')
    assert hu['inst'] == 12
    skel['动物']['哺乳动物']['note_tiger'] = '人物与人体「虎」叶12实例已并入猫科/大型猫科动物/老虎叶（不另立L2）'

    # 8. 低等生物：废壳，六分支晋升为动物域L2
    skel['动物'].update(LOWER_ORGANISM_L2)

    # 9. 东亚人贬称 → 东亚人群：迁 民族与族群 下
    dp = pending['东亚人贬称']
    node = {'src': '迁入（原东亚人贬称改名）', 'leaves': dp['leaves'], 'inst': dp['inst'],
            'resolution': '裁定：实例为20个国别人群，内容不敏感坏在分支名，改名「东亚人群」迁民族与族群，不删除'}
    skel['民族、语言与文化']['民族与族群']['sub_东亚人群'] = node
    skel['民族、语言与文化']['民族与族群']['leaves'] += node['leaves']
    skel['民族、语言与文化']['民族与族群']['inst'] += node['inst']

    # 按 GROUPS 重排
    ordered = OrderedDict()
    for g, doms in GROUPS.items():
        for d in doms:
            ordered[d] = skel[d]
    for d in skel:
        if d not in ordered:
            ordered[d] = skel[d]

    json.dump(ordered, open(OUT_JSON, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)

    # MD
    md = ['# 世界目录骨架全览 v3.1（29 域 · 九条人工裁定后）', '',
          '> 基于评审版 v3（三模型背靠背 + 裁决表）叠加 2026-08-23 九条人工裁定。',
          '> IP 已按「吸收IP」融合进各领域；底层数据迁移已落盘（见迁移批1）。', '']
    md += ['## 第一层：29 个领域', '']
    n = 0
    tot_l = tot_i = 0
    for g, doms in GROUPS.items():
        md.append(f'### {g}')
        for d in doms:
            n += 1
            br = ordered[d]
            lv = sum(v.get('leaves', 0) for v in br.values())
            ins = sum(v.get('inst', 0) for v in br.values())
            tot_l += lv; tot_i += ins
            md.append(f'{n:02d}. **{d}** — {len(br)} 个二级分支，{lv} 叶，{ins} 实例')
        md.append('')
    md += [f'**合计：{tot_l} 叶 / {tot_i} 实例**（待处置清零，全部裁定入域）', '',
           '## 裁定摘要', '',
           '- 斯芬克斯、美人鱼 → 文化艺术与媒介／虚构世界与角色',
           '- 怪兽：撤独立叶，仅鲲鹏 1 实例补入民间传说怪物',
           '- 怪物卡车：撤销迁移，回交通工具（特技改装真车）',
           '- 商业竞争：整树（含价格竞争）留行为动作',
           '- 金融资本 → 政治法律制度／经济制度与金融',
           '- 虎：并入动物／哺乳动物／猫科动物／大型猫科动物／老虎',
           '- 低等生物：废壳，六门类分支晋升动物域 L2（+98 实例）',
           '- 东亚人贬称 → 改名「东亚人群」迁民族、语言与文化／民族与族群（+20 实例）', '']
    open(OUT_MD, 'w', encoding='utf-8').write('\n'.join(md))

    print('域数:', len(ordered))
    print(f'合计: {tot_l} 叶 / {tot_i} 实例')
    print('写出:', OUT_JSON, OUT_MD)


if __name__ == '__main__':
    main()
