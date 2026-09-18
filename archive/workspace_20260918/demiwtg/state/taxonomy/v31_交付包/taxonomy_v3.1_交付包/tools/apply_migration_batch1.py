# -*- coding: utf-8 -*-
"""迁移批1 数据层执行：备份已完成（backup/迁移批1_前/）。
原则：前缀替换，实例清单原样随行，子树内部结构零变化；中文+EN 双底稿同映射。
"""
import csv
import json
import sys
from collections import OrderedDict

SRC = '/Users/meng/qwenwork'
CN_FILE = f'{SRC}/taxonomy_merged_progress_instances.csv'
EN_FILE = f'{SRC}/taxonomy_tree_instances_en.csv'
ROOT = '融合世界标签体系'
GEN = f'{ROOT} / 通用分类标签'
IP = f'{ROOT} / IP 分类标签'

DRY = '--apply' not in sys.argv

# 新域英文名（既有域英文名从 EN 底稿现值继承）
NEW_DOMAIN_EN = {
    '政治、法律与社会制度': 'Politics, Law and Social Institutions',
    '地理与地点': 'Geography and Places',
    '品牌与产品': 'Brands and Products',
    '宗教与信仰': 'Religion and Faith',
    '历史与时代': 'History and Eras',
    '民族、语言与文化': 'Ethnicity, Language and Culture',
}
NEW_L2_EN = {
    '经济制度与金融': 'Economic Systems and Finance',
    '虚构世界与角色': 'Fictional Worlds and Characters',
    '民族与族群': 'Ethnic Groups',
}

log = []


def read_cn():
    rows = OrderedDict()
    with open(CN_FILE, encoding='utf-8-sig') as f:
        rd = csv.reader(f)
        next(rd)
        for r in rd:
            if r:
                rows[r[0]] = r[1] if len(r) > 1 else ''
    return rows


def read_en():
    rows = OrderedDict()
    with open(EN_FILE, encoding='utf-8-sig') as f:
        rd = csv.reader(f)
        next(rd)
        for r in rd:
            if r and len(r) >= 2:
                rows[r[0]] = [r[1], r[2] if len(r) > 2 else '']
    return rows


def main():
    cn = read_cn()
    en = read_en()
    assert list(cn.keys()) == list(en.keys()), '中英文底稿路径不一致，中止'
    assert len(cn) == len(set(cn)), '中文底稿存在重复路径，中止'

    # 既有域英文名（从 EN 底稿提取）
    dom_en = {}
    for p, (ep, _) in en.items():
        cparts = p.split(' / ')
        eparts = ep.split(' / ')
        if len(cparts) >= 3 and cparts[1] == '通用分类标签' and len(eparts) >= 3:
            dom_en.setdefault(cparts[2], eparts[2])
    dom_en.update(NEW_DOMAIN_EN)

    stats = {'moved_rows': 0, 'deleted_rows': 0, 'created_rows': 0}

    def inst_total():
        return sum(len(v.split('|')) for v in cn.values() if v.strip())

    def step(label):
        print(f'  [{label}] 实例总数 = {inst_total()}')
    touched = set()  # 本次迁移触碰的目标路径（用于段位抽查）

    def subtree_prefix(src_root, dst_root):
        """整树前缀替换（含 src_root 自身行）"""
        keys = [k for k in cn if k == src_root or k.startswith(src_root + ' / ')]
        assert keys, f'源子树不存在: {src_root}'
        for k in keys:
            newk = dst_root + k[len(src_root):]
            assert newk not in cn, f'目标路径已存在: {newk}'
            touched.add(newk)
            cn[newk] = cn.pop(k)
            epath, einsts = en.pop(k)
            # EN 路径同映射：段位一一对应，替换前缀段
            cparts = k.split(' / ')
            eparts = epath.split(' / ')
            assert len(cparts) == len(eparts), f'EN 段位不齐: {k}'
            dparts = dst_root.split(' / ')
            src_n = len(src_root.split(' / '))

            def seg_en(i, seg):
                if seg in dom_en:
                    return dom_en[seg]
                if seg in NEW_L2_EN:
                    return NEW_L2_EN[seg]
                if seg == ROOT:
                    return 'Fused World Label System'
                if seg == '通用分类标签':
                    return 'General Classification Tags'
                if seg in cparts:  # 名称未变的段，取源路径同段英文
                    return eparts[cparts.index(seg)]
                raise AssertionError(f'无法翻译段: {seg} (位置{i})')

            new_eparts = [seg_en(i, seg) for i, seg in enumerate(dparts)]
            new_eparts += eparts[src_n:]
            en[newk] = [' / '.join(new_eparts), einsts]
            stats['moved_rows'] += 1
        log.append(f'  迁移 {len(keys)} 行: {src_root} → {dst_root}')

    def ensure_node(path):
        """确保中间节点行存在（中文+EN），不存在则建空行"""
        if path in cn:
            return
        cn[path] = ''
        cparts = path.split(' / ')
        eparts = []
        for seg in cparts:
            if seg in dom_en:
                eparts.append(dom_en[seg])
            elif seg in NEW_L2_EN:
                eparts.append(NEW_L2_EN[seg])
            elif seg == ROOT:
                eparts.append('Fused World Label System')
            elif seg == '通用分类标签':
                eparts.append('General Classification Tags')
            else:
                raise AssertionError(f'无法翻译的新节点段: {seg}')
        en[path] = [' / '.join(eparts), '']
        stats['created_rows'] += 1
        log.append(f'  新建节点行: {path}')

    def delete_leaf(path):
        assert path in cn, f'待删行不存在: {path}'
        subs = [k for k in cn if k.startswith(path + ' / ')]
        assert not subs, f'{path} 有子行，不能按叶子删: {subs}'
        del cn[path]
        en.pop(path)
        stats['deleted_rows'] += 1
        log.append(f'  删除行: {path}')

    print('== 1. 51 条 auto：域级前缀替换 ==')
    step('auto前')
    mig = json.load(open(f'{SRC}/迁移批1_映射表.json', encoding='utf-8'))
    auto = [x for x in mig['items'] if x['status'] == 'auto']
    assert len(auto) == 51, f'auto 条数异常: {len(auto)}'
    for x in auto:
        subtree_prefix(f'{GEN} / {x["from"]} / {x["name"]}',
                       f'{GEN} / {x["to"]} / {x["name"]}')
    step('auto后')

    print('== 2. 斯芬克斯 / 美人鱼 → 文化艺术与媒介 / 虚构世界与角色 ==')
    ensure_node(f'{GEN} / 文化艺术与媒介 / 虚构世界与角色')
    subtree_prefix(f'{GEN} / 人物与人体 / 斯芬克斯',
                   f'{GEN} / 文化艺术与媒介 / 虚构世界与角色 / 斯芬克斯')
    subtree_prefix(f'{GEN} / 动物 / 美人鱼',
                   f'{GEN} / 文化艺术与媒介 / 虚构世界与角色 / 美人鱼')

    print('== 3. 怪兽：删叶 + 无归宿实例并入民间传说怪物（中英各自去重） ==')
    mm = f'{IP} / 虚构角色 IP / 志怪鬼怪形象 / 民间传说怪物'
    gj = f'{GEN} / 动物 / 怪兽'

    def merge_into_mm(cn_list, mm_list):
        """把怪兽叶实例中『在民间传说怪物无归宿』的项并入；返回(补入项,跳过项)"""
        mmset = set(mm_list)
        add, skip = [], []
        for x in cn_list:
            x = x.strip()
            if not x:
                continue
            if x in mmset:
                skip.append((x, 'exact'))
            elif any(x.lower() in m.lower() for m in mmset):
                skip.append((x, 'qualified'))
            else:
                add.append(x)
        return add, skip

    # 中文侧
    add_cn, skip_cn = merge_into_mm(cn[gj].split('|'), cn[mm].split('|'))
    assert '鲲鹏' in add_cn, f'中文侧应补鲲鹏，实际 {add_cn}'
    cn[mm] = '|'.join([v for v in cn[mm].split('|') if v] + add_cn)
    # 英文侧
    en_mm = en[mm][1]
    en_gj = en[gj][1]
    add_en, skip_en = merge_into_mm(en_gj.split('|'), en_mm.split('|'))
    en[mm][1] = '|'.join([v for v in en_mm.split('|') if v] + add_en)
    delete_leaf(gj)
    log.append(f'  怪兽并入民间传说怪物：中文补{len(add_cn)}({add_cn})、英文补{len(add_en)}({add_en})')

    print('== 4. 怪物卡车：撤销迁移（不操作） ==')
    log.append('  怪物卡车留交通工具域，零操作')

    print('== 5. 商业竞争：整树迁行为动作（修正：非空叶，含价格竞争14实例） ==')
    subtree_prefix(f'{GEN} / 交通工具 / 商业竞争',
                   f'{GEN} / 行为动作 / 商业竞争')

    print('== 6. 金融资本 → 政治法律制度 / 经济制度与金融 ==')
    ensure_node(f'{GEN} / 政治、法律与社会制度 / 经济制度与金融')
    subtree_prefix(f'{GEN} / 交通工具 / 金融资本',
                   f'{GEN} / 政治、法律与社会制度 / 经济制度与金融 / 金融资本')

    print('== 7. 虎：实例并入 动物/…/大型猫科动物/老虎，删源叶 ==')
    tiger = f'{GEN} / 动物 / 哺乳动物 / 猫科动物 / 大型猫科动物 / 老虎'
    hu = f'{GEN} / 人物与人体 / 虎'
    assert cn[tiger] == '' and cn[hu]
    cn[tiger] = cn[hu]
    assert en[tiger][1] == '' and en[hu][1]
    en[tiger][1] = en[hu][1]
    delete_leaf(hu)
    log.append('  虎 12 实例并入「老虎」叶（中/英），源叶删除')

    print('== 8. 低等生物：删壳，六子分支升为动物域 L2 ==')
    shell = f'{GEN} / 动物 / 低等生物'
    kids = [k for k in cn if k.startswith(shell + ' / ') and k.count(' / ') == 4]
    assert len(kids) == 6, f'低等生物一级子分支数异常: {kids}'
    for kid in kids:
        name = kid.split(' / ')[-1]
        target = f'{GEN} / 动物 / {name}'
        assert target not in cn, f'晋升碰撞: {target}'
        subtree_prefix(kid, target)
    delete_leaf(shell)

    print('== 9. 东亚人贬称 → 东亚人群：改名迁 民族与族群 ==')
    ensure_node(f'{GEN} / 民族、语言与文化 / 民族与族群')
    src = f'{GEN} / 知识与学科 / 东亚人贬称'
    dst = f'{GEN} / 民族、语言与文化 / 民族与族群 / 东亚人群'
    assert src in cn and dst not in cn
    cn[dst] = cn.pop(src)
    epath, einsts = en.pop(src)
    # 重建英文路径：前缀用新域/新L2，叶子段改名
    en[dst] = [' / '.join([
        'Fused World Label System', 'General Classification Tags',
        NEW_DOMAIN_EN['民族、语言与文化'], NEW_L2_EN['民族与族群'],
        'East Asian Peoples']), einsts]
    stats['moved_rows'] += 1
    log.append(f'  {src} → {dst}（改名+迁移，中/英）')

    # 校验
    print('== 校验 ==')
    assert len(cn) == len(set(cn)), '迁移后出现重复路径'
    assert list(cn.keys()) == list(en.keys()), '中英文底稿迁移后路径不一致'
    # 原始数据有 88 处「斜杠段名」多义词记法（如 帝王蟹/蟹 ↔ King Crab / Crab），
    # 中英段位天然不齐，属固有形态不在本批处理；只抽查本次触碰的行
    bad = [p for p in touched
           if len(p.split(' / ')) != len(en[p][0].split(' / '))]
    assert not bad, f'迁移触碰行 EN 段位不齐: {bad[:5]}'
    inst_after = sum(len(v.split('|')) for v in cn.values() if v.strip())
    inst_after_en = sum(len(ei.split('|')) for _, ei in en.values() if ei.strip())
    # 中英「怪兽」叶基数本就不同步（中17/英25），净变化按各语言独立校验：
    #   中文：删叶-17、补鲲鹏+1 → -16
    #   英文：删叶-25、补无归宿6项+1 → -19
    cn_delta = 346650 - inst_after
    en_delta = 473961 - inst_after_en
    assert cn_delta == 16, f'CN 实例净变化异常: {cn_delta} (应为16)'
    assert en_delta == 19, f'EN 实例净变化异常: {en_delta} (应为19)'
    print(f'行数: 21400 → {len(cn)} | CN 实例: 346650 → {inst_after} (-16) | EN 实例: 473961 → {inst_after_en} (-19)')
    print(f'移行 {stats["moved_rows"]} 行，删 {stats["deleted_rows"]} 行，新建 {stats["created_rows"]} 行')

    for line in log:
        print(line)

    if DRY:
        print('\n[DRY-RUN] 未写盘。加 --apply 执行。')
        return

    with open(CN_FILE, 'w', encoding='utf-8-sig', newline='') as f:
        w = csv.writer(f, lineterminator='\n')
        w.writerow(['node_path', 'instance清单'])
        for k, v in cn.items():
            w.writerow([k, v])
    with open(EN_FILE, 'w', encoding='utf-8-sig', newline='') as f:
        w = csv.writer(f, lineterminator='\n')
        w.writerow(['node_path', 'node_path_en', 'instance清单(英文)'])
        for k, (ep, ei) in en.items():
            w.writerow([k, ep, ei])
    print('\n已写盘：', CN_FILE, EN_FILE)


if __name__ == '__main__':
    main()
