# -*- coding: utf-8 -*-
"""迁移批 2 执行器：知识与学科域清理（用户已整体通过草案）。
语义：前缀替换、实例原样随行、子树内部结构零变化、中英同映射。
结构规则：
  - (新建)分支单源：源根改名为分支名（S1），子树随行；
  - (新建)分支多源 / 体育运动大项：先建空分支根，各源挂为其子节点（S2）；
  - 并入既有分支：源挂为目标分支子节点（S2）；
  - 特殊：品牌实例并入既有根；地平线吸收进新建土壤层；地址吸收进 说话/交谈/演讲；
    生命科学拆分（医学→医学与健康/医学专科；有机过程吸收进生命科学）。
备份 → dry-run → --apply → 数量校验。
"""
import csv
import json
import sys
from collections import OrderedDict, defaultdict

SRC = '/Users/meng/qwenwork'
CN_FILE = f'{SRC}/taxonomy_merged_progress_instances.csv'
EN_FILE = f'{SRC}/taxonomy_tree_instances_en.csv'
ROOT = '融合世界标签体系'
ENROOT = 'Fused World Label System'
GEN = f'{ROOT} / 通用分类标签'
KN = GEN + ' / 知识与学科'
DRY = '--apply' not in sys.argv

log = []
touched = set()

# ---- keep 改名（知识与学科域内原地改名） ----
KEEP_RENAME = {
    '工程':   ('工程学', 'Engineering'),
    '知识':   ('认知与知识过程', 'Cognition and Knowledge Processes'),
    '认知因素': ('认知科学', 'Cognitive Science'),
    '问题解决': ('研究方法', 'Research Methods'),
    '属':     ('生物分类阶元', 'Taxonomic Genera'),
}
# 不改名但保留的 keep 节点
KEEP_SAME = ['化学', '物理学', '数学', '历史学', '哲学', '语言学', '天文学',
             '经济学', '文学', '设计元素', '教育理念与模式']

# ---- 强制空根再挂子的新建分支（宽类名，不做改名吸收） ----
FORCE_EMPTY = {'体育运动大项'}

# ---- 新建分支英文名 ----
NEW_L2_EN = {
    '行政区划与城市': 'Administrative Divisions and Cities',
    '土地与地块': 'Land Parcels and Plots',
    '地理学': 'Geography',
    '乡下': 'Rural Areas',
    '市郊': 'Suburbs',
    '山峰与尖顶': 'Peaks and Summits',
    '海底地形': 'Seabed Terrain',
    '广场': 'Plazas and Squares',
    '首都与中心': 'Capitals and Centers',
    '聚居地': 'Settlements',
    '城市风光': 'Urban Views',
    '海滨度假地': 'Seaside Resorts',
    '陆块与岛屿': 'Landmasses and Islands',
    '土壤层': 'Soil Layers',
    '电视节目类型': 'TV Program Genres',
    '星座与占星文化': 'Constellations and Astrology Culture',
    '社会阶层': 'Social Classes',
    '政治体制': 'Political Systems',
    '财产与资产': 'Property and Assets',
    '室内设计': 'Interior Design',
    '临时结构': 'Temporary Structures',
    '饮水设施': 'Drinking Water Facilities',
    '手柄': 'Handles',
    '织物边缘': 'Fabric Edges',
    '配件': 'Fittings and Parts',
    '导航工具': 'Navigation Tools',
    '清新剂': 'Air Fresheners',
    '链条': 'Chains',
    '碎片': 'Fragments and Shards',
    '宝石': 'Gemstones',
    '气味': 'Odors and Scents',
    '黏稠糊状物': 'Viscous Pastes',
    '固体': 'Solids',
    '灰烬': 'Ashes',
    '收藏品与组合': 'Collectibles and Assortments',
    '商品名': 'Product Names',
    '路边场景': 'Roadside Scenes',
    '隐蔽处': 'Hiding Spots',
    '遗骸与化石': 'Remains and Fossils',
    '民俗象征': 'Folk Symbols',
    '地域文化': 'Regional Cultures',
    '民族文化': 'Ethnic Cultures',
    '体育运动大项': 'Major Sports Disciplines',
    '请求动作': 'Request Actions',
    '购物交易动作': 'Shopping and Transaction Actions',
    '指令': 'Directives and Commands',
    '逆转': 'Reversals',
    '后部': 'Rear Parts',
    '决定因素': 'Determining Factors',
    '反射': 'Reflections',
    '名称': 'Names and Naming',
    '项目': 'Projects',
    '医学专科': 'Medical Specialties',
}
NEW_DOMAIN_EN = {'医学与健康': 'Medicine and Health'}


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
    assert list(cn) == list(en), '中英底稿路径不一致，中止'
    n0 = len(cn)
    cn_i0 = sum(len(v.split('|')) for v in cn.values() if v.strip())
    en_i0 = sum(len(i.split('|')) for _, i in en.values() if i.strip())
    assert cn_i0 == 346634 and en_i0 == 473942, f'基线不符: {cn_i0}/{en_i0}'

    draft = json.load(open(f'{SRC}/迁移批2_映射表_草案.json', encoding='utf-8'))
    items = {it['name']: it for it in draft['items']}

    # 既有域英文名
    dom_en = {}
    for p, (ep, _) in en.items():
        cp = p.split(' / ')
        epa = ep.split(' / ')
        if len(cp) >= 3 and cp[1] == '通用分类标签' and len(epa) >= 3:
            dom_en.setdefault(cp[2], epa[2])
    dom_en.update(NEW_DOMAIN_EN)

    def subtree(root):
        return [k for k in cn if k == root or k.startswith(root + ' / ')]

    def put(nk, inst, nep, ei):
        assert nk not in cn, f'碰撞: {nk}'
        cn[nk] = inst
        en[nk] = [nep, ei]
        touched.add(nk)

    moved = []

    # == 0. 新建医学与健康域根行 ==
    med = GEN + ' / 医学与健康'
    assert med not in cn
    cn[med] = ''
    en[med] = [f'{ENROOT} / General Classification Tags / {NEW_DOMAIN_EN["医学与健康"]}', '']
    touched.add(med)
    log.append('新建域根行: 医学与健康')

    # == 1. keep 改名 ==
    for old, (new, enew) in KEEP_RENAME.items():
        src = KN + ' / ' + old
        keys = subtree(src)
        assert keys, f'缺失 {src}'
        pairs = []
        for k in keys:
            nk = KN + ' / ' + new + k[len(src):]
            inst = cn.pop(k)
            ep, ei = en.pop(k)
            epa = ep.split(' / ')
            assert len(epa) == len(k.split(' / ')), f'段位不齐 {k}'
            nep = ' / '.join(epa[:3] + [enew] + epa[4:])
            pairs.append((nk, inst, nep, ei))
        for nk, inst, nep, ei in pairs:
            put(nk, inst, nep, ei)
        moved.append({'name': old, 'op': 'rename_keep', 'to': f'知识与学科 / {new}',
                      'rows': len(keys)})
        log.append(f'改名: {old} → {new}（{len(keys)} 行）')

    # == 2. split: 生命科学/医学 → 医学与健康/医学专科 ==
    src = KN + ' / 生命科学 / 医学'
    dst = med + ' / 医学专科'
    keys = subtree(src)
    pairs = []
    for k in keys:
        nk = dst + k[len(src):]
        inst = cn.pop(k)
        ep, ei = en.pop(k)
        epa = ep.split(' / ')
        nep = ' / '.join([ENROOT, 'General Classification Tags',
                          NEW_DOMAIN_EN['医学与健康'], NEW_L2_EN['医学专科']] + epa[5:])
        pairs.append((nk, inst, nep, ei))
    for nk, inst, nep, ei in pairs:
        put(nk, inst, nep, ei)
    moved.append({'name': '生命科学/医学', 'op': 'split_move',
                  'to': '医学与健康 / 医学专科', 'rows': len(keys)})
    log.append(f'拆分迁移: 生命科学/医学 → 医学与健康/医学专科（{len(keys)} 行）')

    # == 3. split: 有机过程吸收进生命科学 ==
    src = KN + ' / 有机过程'
    dst = KN + ' / 生命科学'
    keys = subtree(src)
    assert cn[src] == '', '有机过程根行应无实例'
    pairs = []
    for k in keys[1:]:
        nk = dst + k[len(src):]
        inst = cn.pop(k)
        ep, ei = en.pop(k)
        epa = ep.split(' / ')
        nep = ' / '.join(epa[:3] + ['Life Sciences'] + epa[4:])
        pairs.append((nk, inst, nep, ei))
    del cn[src]
    en.pop(src)
    touched.add(src)
    for nk, inst, nep, ei in pairs:
        put(nk, inst, nep, ei)
    moved.append({'name': '有机过程', 'op': 'absorb_into', 'to': '知识与学科 / 生命科学',
                  'rows': len(keys) - 1})
    log.append(f'吸收: 有机过程 → 生命科学（迁 {len(keys) - 1} 行，壳行删除）')

    # == 4. 特殊：品牌实例并入既有分支根 ==
    src = KN + ' / 品牌'
    dst = GEN + ' / 品牌与产品 / 品牌'
    assert src in cn and dst in cn
    si = [x for x in cn[src].split('|') if x.strip()] if cn[src].strip() else []
    di = [x for x in cn[dst].split('|') if x.strip()] if cn[dst].strip() else []
    seen = set(di)
    add = [x for x in si if x not in seen]
    cn[dst] = '|'.join(di + add)
    sep, sei = en[dst]
    sei_l = [x for x in sei.split('|') if x.strip()] if sei.strip() else []
    ssi = [x for x in en[src][1].split('|') if x.strip()] if en[src][1].strip() else []
    seen_e = set(sei_l)
    en[dst] = [sep, '|'.join(sei_l + [x for x in ssi if x not in seen_e])]
    del cn[src]
    en.pop(src)
    touched.add(dst)
    moved.append({'name': '品牌', 'op': 'merge_inst', 'to': '品牌与产品 / 品牌',
                  'added_cn': len(add)})
    log.append(f'实例并入: 品牌 → 品牌与产品/品牌（新增 {len(add)} 实例）')

    # == 5. 特殊：地平线吸收进新建土壤层（同名子节点） ==
    src = KN + ' / 地平线'
    dst = GEN + ' / 自然景观 / 土壤层'
    child = src + ' / 土壤层'
    assert src in cn and child in cn and dst not in cn and cn[src] == ''
    cn[dst] = cn.pop(child)
    en[dst] = [f'{ENROOT} / General Classification Tags / {dom_en["自然景观"]} / '
               f'{NEW_L2_EN["土壤层"]}', en.pop(child)[1]]
    del cn[src]
    en.pop(src)
    touched.add(dst)
    moved.append({'name': '地平线', 'op': 'absorb_new', 'to': '自然景观 / 土壤层', 'rows': 1})
    log.append('吸收: 地平线 → 自然景观/土壤层（同名子节点直接为根）')

    # == 6. 特殊：地址吸收进既有 说话/交谈/演讲 ==
    src = KN + ' / 地址'
    dst = GEN + ' / 行为动作 / 说话/交谈/演讲'
    assert src in cn and dst in cn and cn[src] == ''
    keys = subtree(src)
    pairs = []
    for k in keys[1:]:
        nk = dst + k[len(src):]
        inst = cn.pop(k)
        ep, ei = en.pop(k)
        epa = ep.split(' / ')
        dep = en[dst][0].split(' / ')
        nep = ' / '.join(dep + epa[4:])
        pairs.append((nk, inst, nep, ei))
    del cn[src]
    en.pop(src)
    touched.add(src)
    for nk, inst, nep, ei in pairs:
        put(nk, inst, nep, ei)
    moved.append({'name': '地址', 'op': 'absorb_into', 'to': '行为动作 / 说话/交谈/演讲',
                  'rows': len(keys) - 1})
    log.append(f'吸收: 地址 → 说话/交谈/演讲（迁 {len(keys) - 1} 行）')

    # == 7. 常规迁移：按 (目标域, 分支) 分组处理 ==
    SPECIAL = {'品牌', '地平线', '地址', '生命科学'}
    groups = defaultdict(list)
    for name, it in items.items():
        if it['op'] != 'move' or name in SPECIAL:
            continue
        b = it['to_branch'][:-4] if it['to_branch'].endswith('(新建)') else it['to_branch']
        groups[(it['to_domain'], b, it['to_branch'].endswith('(新建)'))].append(name)

    for (dom, b, is_new), names in sorted(groups.items()):
        dst = GEN + ' / ' + dom + ' / ' + b
        if is_new:
            assert dst not in cn, f'标新建但已存在 {dst}'
        else:
            assert dst in cn, f'标并入但不存在 {dst}'
        # 单源且非强制空根 → S1 改名；否则先建空根，各源挂子节点（S2）
        s1 = is_new and len(names) == 1 and b not in FORCE_EMPTY
        if not s1 and dst not in cn:
            cn[dst] = ''
            en[dst] = [f'{ENROOT} / General Classification Tags / {dom_en[dom]} / '
                       f'{NEW_L2_EN[b]}', '']
            touched.add(dst)
        for name in sorted(names):
            src = KN + ' / ' + name
            keys = subtree(src)
            assert keys, f'缺失 {src}'
            pairs = []
            if s1:
                for k in keys:
                    nk = dst + k[len(src):]
                    inst = cn.pop(k)
                    ep, ei = en.pop(k)
                    epa = ep.split(' / ')
                    assert len(epa) == len(k.split(' / ')), f'段位不齐 {k}'
                    nep = ' / '.join([ENROOT, 'General Classification Tags',
                                      dom_en[dom], NEW_L2_EN[b]] + epa[4:])
                    pairs.append((nk, inst, nep, ei))
            else:
                dep = en[dst][0].split(' / ')
                for k in keys:
                    nk = dst + ' / ' + name + k[len(src):]
                    inst = cn.pop(k)
                    ep, ei = en.pop(k)
                    epa = ep.split(' / ')
                    nep = ' / '.join(dep + epa[3:])
                    pairs.append((nk, inst, nep, ei))
            for nk, inst, nep, ei in pairs:
                put(nk, inst, nep, ei)
            moved.append({'name': name, 'op': 'move_s1' if s1 else 'move_s2',
                          'to': f'{dom} / {b}', 'rows': len(keys)})
            log.append(f'迁移: {name} → {dom}/{b}（{len(keys)} 行，'
                       f'{"S1改名" if s1 else "S2挂子"}）')

    # == 8. 校验 ==
    assert list(cn) == list(en), '中英路径不一致'
    assert len(cn) == len(set(cn)), '重复路径'
    cn_i1 = sum(len(v.split('|')) for v in cn.values() if v.strip())
    en_i1 = sum(len(i.split('|')) for _, i in en.values() if i.strip())
    assert cn_i1 == cn_i0, f'CN 实例变化 {cn_i0}→{cn_i1}'
    assert en_i1 == en_i0, f'EN 实例变化 {en_i0}→{en_i1}'
    # EN 首段必须全为英文根（防新建行误用中文根名）
    bad_en = [k for k in en if en[k][0].startswith('融合')]
    assert not bad_en, f'EN 首段未翻译: {bad_en[:3]}'
    # 触碰行中英段位一致（跳过已删除的源路径）
    for k in touched:
        if k not in cn:
            continue
        assert len(k.split(' / ')) == len(en[k][0].split(' / ')), f'段位不齐 {k}'
    # 知识与学科残留检查
    manual = {it['name'] for it in items.values() if it['op'] == 'manual'}
    keep_new = {v[0] for v in KEEP_RENAME.values()}
    expect = keep_new | set(KEEP_SAME) | manual | {'学科与知识品牌', '教育与科普', '生命科学'}
    resid = {p.split(' / ')[3] for p in cn if p.startswith(KN + ' / ')}
    assert resid == expect, f'残留不符: 多={resid - expect} 少={expect - resid}'

    print(f'行数: {n0} → {len(cn)} | CN 实例 {cn_i1}（不变） | EN 实例 {en_i1}（不变）')
    print(f'操作 {len(moved)} 项；知识与学科剩 {len(resid)} 个 L2')
    for line in log:
        print(' ', line)

    json.dump({'batch': '迁移批2', 'domain': '知识与学科',
               'principle': '前缀替换、实例随行、结构零变化、中英同映射；含5条改名、生命科学拆分、3处吸收合并',
               'ops': moved},
              open(f'{SRC}/迁移批2_映射表.json', 'w', encoding='utf-8'),
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
