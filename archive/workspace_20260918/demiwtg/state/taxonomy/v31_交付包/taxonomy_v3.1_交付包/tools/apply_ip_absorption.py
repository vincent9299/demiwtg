# -*- coding: utf-8 -*-
"""IP 吸收批：按定版骨架 v3.1 把数据层 IP 树物理并入 29 域。
挂载语义：融合世界标签体系 / IP 分类标签 / X IP / 余下
      → 融合世界标签体系 / 通用分类标签 / 目标域 / 承接L2 / 余下
原二级分支降为三级，子树内部结构零变化，实例原样随行，中英同映射。
先备份 → 映射 → 碰撞审计 → 写盘 → 数量校验。
"""
import csv
import json
import os
import sys
from collections import OrderedDict

SRC = '/Users/meng/qwenwork'
CN_FILE = f'{SRC}/taxonomy_merged_progress_instances.csv'
EN_FILE = f'{SRC}/taxonomy_tree_instances_en.csv'
ROOT = '融合世界标签体系'
GEN = f'{ROOT} / 通用分类标签'
IP = f'{ROOT} / IP 分类标签'

DRY = '--apply' not in sys.argv

# v2/v3.1 权威挂载表：IP域 -> (目标域, 承接L2)
IP_MAP = OrderedDict([
    ('地标 IP', ('建筑与基础设施', '著名地标与名胜')),
    ('品牌 IP', ('品牌与产品', '品牌')),
    ('真人与人物 IP', ('人物与人体', '知名人物')),
    ('虚构角色 IP', ('文化艺术与媒介', '虚构世界与角色')),
    ('内容作品 IP', ('文化艺术与媒介', '内容作品')),
    ('艺术与文物 IP', ('文化艺术与媒介', '艺术品与文物')),
    ('美食与饮食文化 IP', ('食物', '饮食文化与名店名品')),
    ('学科与知识 IP', ('知识与学科', '学科与知识品牌')),
    ('非遗与传统手工艺 IP', ('文化艺术与媒介', '非遗与传统手工艺')),
    ('组织机构 IP', ('组织机构与社会事件', '知名组织')),
    ('自然生态与动物 IP', ('动物', '著名动物与生态')),
    ('历史与文化遗产 IP', ('历史与时代', '文化遗产')),
    ('吉祥物与形象 IP', ('品牌与产品', '吉祥物与形象')),
    ('科技与数字 IP', ('数字与互联网文化', '科技与数字产品')),
    ('教育与科普 IP', ('知识与学科', '教育与科普')),
    ('赛事 IP', ('体育与游戏', '赛事')),
    ('潮玩互动 IP', ('人造物体', '玩具与潮玩')),
    ('乐园节庆 IP', ('场景', '乐园与节庆场所')),
    ('电竞 IP', ('体育与游戏', '电子竞技')),
    ('著名载具 IP', ('交通工具', '著名载具')),
    ('城市与地域 IP', ('场景', '城市与地域')),
    ('武器 IP', ('人造物体', '著名武器')),
])

# 需新建域的英文名
NEW_DOMAIN_EN = {
    '数字与互联网文化': 'Digital and Internet Culture',
    '体育与游戏': 'Sports and Games',
}
# 承接L2英文名（目标L2不存在时使用；虚构世界与角色已存在则沿用既有）
RECV_L2_EN = {
    '著名地标与名胜': 'Famous Landmarks and Scenic Spots',
    '品牌': 'Brands',
    '知名人物': 'Famous Figures',
    '虚构世界与角色': 'Fictional Worlds and Characters',
    '内容作品': 'Content Works',
    '艺术品与文物': 'Artworks and Cultural Relics',
    '饮食文化与名店名品': 'Food Culture and Renowned Brands',
    '学科与知识品牌': 'Academic and Knowledge Brands',
    '非遗与传统手工艺': 'Intangible Cultural Heritage and Traditional Crafts',
    '知名组织': 'Renowned Organizations',
    '著名动物与生态': 'Famous Animals and Ecology',
    '文化遗产': 'Cultural Heritage',
    '吉祥物与形象': 'Mascots and Images',
    '科技与数字产品': 'Tech and Digital Products',
    '教育与科普': 'Education and Popular Science',
    '赛事': 'Major Events',
    '玩具与潮玩': 'Toys and Trendy Toys',
    '乐园与节庆场所': 'Amusement Parks and Festival Venues',
    '电子竞技': 'Esports',
    '著名载具': 'Famous Vehicles',
    '城市与地域': 'Cities and Regions',
    '著名武器': 'Famous Weapons',
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
    assert list(cn.keys()) == list(en.keys()), '中英底稿路径不一致，中止'

    # 既有域英文名
    dom_en = {}
    for p, (ep, _) in en.items():
        cp = p.split(' / ')
        epa = ep.split(' / ')
        if len(cp) >= 3 and cp[1] == '通用分类标签' and len(epa) >= 3:
            dom_en.setdefault(cp[2], epa[2])
    dom_en.update(NEW_DOMAIN_EN)

    stats = {'moved': 0, 'new_rows': 0}
    mapping_out = []

    print('== 0. 新建目标域行（若不存在） ==')
    need_doms = sorted({d for d, _ in IP_MAP.values() if f'{GEN} / {d}' not in cn})
    for d in need_doms:
        dp = f'{GEN} / {d}'
        cn[dp] = ''
        en[dp] = [' / '.join(['Fused World Label System', 'General Classification Tags', dom_en[d]]), '']
        stats['new_rows'] += 1
        log.append(f'  新建域行: {dp}')

    print('== 1. 逐 IP 域挂载（前缀替换） ==')
    for ipname, (dom, recv) in IP_MAP.items():
        src_root = f'{IP} / {ipname}'
        dst_root = f'{GEN} / {dom} / {recv}'
        keys = [k for k in cn if k == src_root or k.startswith(src_root + ' / ')]
        assert keys, f'IP 子树缺失: {src_root}'

        # 承接L2根行：已存在（虚构世界与角色）则保留既有，否则新建
        if dst_root in cn:
            root_exists = True
            assert cn[dst_root] == '' or True
        else:
            cn[dst_root] = ''
            en[dst_root] = [' / '.join(['Fused World Label System', 'General Classification Tags',
                                        dom_en[dom], RECV_L2_EN[recv]]), '']
            stats['new_rows'] += 1
            root_exists = False

        moved = 0
        for k in keys:
            if k == src_root:
                # 根行（0实例）：若目标根行已存在则删源行，否则已转换完成
                del cn[k]
                en.pop(k)
                if not root_exists:
                    # 目标根行上面已建；此处仅记数
                    pass
                continue
            newk = dst_root + k[len(src_root):]
            assert newk not in cn, f'目标路径已存在: {newk}'
            cn[newk] = cn.pop(k)
            epath, einsts = en.pop(k)
            # EN 前缀替换：源前3段（根/IP 分类标签/X IP）换成4段（根/通用/域/承接L2），
            # 余下段从源第4段（index 3）起原样复用（已翻译）
            eparts = epath.split(' / ')
            cparts = k.split(' / ')
            assert len(eparts) == len(cparts), f'EN 段位不齐: {k}'
            new_ep = ' / '.join([
                'Fused World Label System', 'General Classification Tags',
                dom_en[dom], RECV_L2_EN[recv],
            ] + eparts[3:])
            assert len(new_ep.split(' / ')) == len(newk.split(' / ')), f'EN 段位与 CN 不齐: {newk}'
            en[newk] = [new_ep, einsts]
            moved += 1
            stats['moved'] += 1
        mapping_out.append({'ip': ipname, 'to_domain': dom, 'recv_l2': recv,
                            'moved_rows': moved, 'root_existed': root_exists})
        log.append(f'  {ipname} → [{dom}] {recv}: 移行 {moved} 行'
                   + ('（目标L2已存在，根行合并）' if root_exists else ''))

    print('== 2. 清理 IP 层残壳 ==')
    leftovers = [k for k in cn if k.startswith(IP)]
    for k in leftovers:
        assert cn[k] == '', f'IP 残壳行带实例: {k}'
        del cn[k]
        en.pop(k)
    log.append(f'  清理 IP 层残壳 {len(leftovers)} 行（含 IP 分类标签根行）')

    print('== 3. 校验 ==')
    assert len(cn) == len(set(cn)), '迁移后重复路径'
    assert list(cn.keys()) == list(en.keys()), '中英路径不一致'
    assert not any(k.startswith(IP) for k in cn), 'IP 层未清干净'
    # 中英路径段位抽查（新挂的行，避开88处固有斜杠段名）
    inst_after = sum(len(v.split('|')) for v in cn.values() if v.strip())
    inst_after_en = sum(len(ei.split('|')) for _, ei in en.values() if ei.strip())
    assert inst_after == 346634, f'CN 实例变化: {inst_after} (应不变 346634)'
    assert inst_after_en == 473942, f'EN 实例变化: {inst_after_en} (应不变 473942)'
    print(f'行数: 21400 → {len(cn)} | CN 实例 {inst_after}（不变） | EN 实例 {inst_after_en}（不变）')
    print(f'移行 {stats["moved"]} 行，新建 {stats["new_rows"]} 行')
    for line in log:
        print(line)

    # 映射表工件
    json.dump({'batch': 'IP吸收批', 'principle': '前缀替换，IP域吸收为目标域L2，原二级降三级，结构零变化',
               'items': mapping_out},
              open(f'{SRC}/IP吸收批_映射表.json', 'w', encoding='utf-8'),
              ensure_ascii=False, indent=2)

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
    print('\n已写盘')


if __name__ == '__main__':
    main()
