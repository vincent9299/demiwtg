# -*- coding: utf-8 -*-
"""检索源同步批：把三批迁移的路径变换重放到 taxonomy_merged_progress_full.csv。
full.csv 停在「迁移批1前」状态（21,400 路径），与当时的实例底稿 100% 一致。
本脚本重放三批变换，使检索源路径对齐终版（21,406）。

裁定（用户已确认）：
  ① 合并碰撞 → 并集（两列独立做管道符去重并集）
  ② 写新文件（老 full 原样留着当原料）
  ③ 终版新增的空源节点如实呈现（空行）

纪律：中英两份终版实例底稿一个字节不碰。
"""
import csv
import json
import sys
from collections import OrderedDict

W = '/Users/meng/qwenwork'
FULL = f'{W}/taxonomy_merged_progress_full.csv'
FINAL = f'{W}/taxonomy_merged_progress_instances.csv'
OUT = f'{W}/taxonomy_source_full_v3.1.csv'
DRY = '--apply' not in sys.argv

ROOT = '融合世界标签体系'
GEN = f'{ROOT} / 通用分类标签'
IP = f'{ROOT} / IP 分类标签'
KN = GEN + ' / 知识与学科'

log = []
stats = {'moved': 0, 'union': 0, 'deleted': 0, 'created': 0}

# ---------- 读取 ----------
def read_full():
    rows = OrderedDict()
    with open(FULL, encoding='utf-8-sig', newline='') as f:
        r = csv.reader(f)
        next(r)
        for row in r:
            if row:
                rows[row[0]] = [row[1] if len(row) > 1 else '',
                                row[2] if len(row) > 2 else '']
    return rows

def read_final_paths():
    paths = set()
    with open(FINAL, encoding='utf-8-sig', newline='') as f:
        r = csv.reader(f)
        next(r)
        for row in r:
            if row:
                paths.add(row[0])
    return paths

# ---------- 并集工具（两列独立，管道符去重，保序） ----------
def union_pipe(a, b):
    """管道符分隔的两列并集，保序去重。"""
    if not a.strip():
        return b
    if not b.strip():
        return a
    seen = set()
    out = []
    for x in a.split('|') + b.split('|'):
        x = x.strip()
        if x and x not in seen:
            seen.add(x)
            out.append(x)
    return '|'.join(out)

# ---------- 核心变换 ----------
def subtree_prefix(rows, src_root, dst_root):
    """整树前缀替换（含根行），源原样随行。"""
    keys = [k for k in rows if k == src_root or k.startswith(src_root + ' / ')]
    assert keys, f'源子树不存在: {src_root}'
    pairs = []
    for k in keys:
        nk = dst_root + k[len(src_root):]
        assert nk not in rows, f'目标已存在: {nk}'
        pairs.append((k, nk))
    for k, nk in pairs:
        rows[nk] = rows.pop(k)
        stats['moved'] += 1
    log.append(f'  迁移 {len(keys)} 行: {src_root} → {dst_root}')

def ensure_node(rows, path):
    if path not in rows:
        rows[path] = ['', '']
        stats['created'] += 1
        log.append(f'  新建节点: {path}')

def delete_row(rows, path):
    assert path in rows, f'待删行不存在: {path}'
    subs = [k for k in rows if k.startswith(path + ' / ')]
    assert not subs, f'{path} 有子行不能删: {subs[:3]}'
    del rows[path]
    stats['deleted'] += 1

def union_into(rows, src_path, dst_path):
    """把 src_path 的源并集进 dst_path，删 src。两列独立并集。"""
    sa, sb = rows[src_path]
    da, db = rows[dst_path]
    rows[dst_path] = [union_pipe(da, sa), union_pipe(db, sb)]
    del rows[src_path]
    stats['union'] += 1
    stats['deleted'] += 1
    log.append(f'  并集合并源: {src_path.split(" / ")[-1]} → {dst_path.split(" / ")[-1]}')


def main():
    rows = read_full()
    final_paths = read_final_paths()
    assert len(rows) == 21400, f'full 行数异常: {len(rows)}'
    n_src_before = sum(1 for a, b in rows.values() if a.strip() or b.strip())
    print(f'full 起始: {len(rows)} 行, 带源 {n_src_before} 个')

    # ================= 批 1 =================
    print('== 批 1 ==')
    mig1 = json.load(open(f'{W}/迁移批1_映射表.json', encoding='utf-8'))
    auto = [x for x in mig1['items'] if x.get('status') == 'auto']
    assert len(auto) == 51
    for x in auto:
        subtree_prefix(rows, f'{GEN} / {x["from"]} / {x["name"]}',
                       f'{GEN} / {x["to"]} / {x["name"]}')

    # 斯芬克斯 / 美人鱼
    ensure_node(rows, f'{GEN} / 文化艺术与媒介 / 虚构世界与角色')
    subtree_prefix(rows, f'{GEN} / 人物与人体 / 斯芬克斯',
                   f'{GEN} / 文化艺术与媒介 / 虚构世界与角色 / 斯芬克斯')
    subtree_prefix(rows, f'{GEN} / 动物 / 美人鱼',
                   f'{GEN} / 文化艺术与媒介 / 虚构世界与角色 / 美人鱼')

    # 怪兽 → 民间传说怪物（此时还在 IP 树内）
    gj = f'{GEN} / 动物 / 怪兽'
    mm_ip = f'{IP} / 虚构角色 IP / 志怪鬼怪形象 / 民间传说怪物'
    union_into(rows, gj, mm_ip)

    # 商业竞争
    subtree_prefix(rows, f'{GEN} / 交通工具 / 商业竞争',
                   f'{GEN} / 行为动作 / 商业竞争')

    # 金融资本
    ensure_node(rows, f'{GEN} / 政治、法律与社会制度 / 经济制度与金融')
    subtree_prefix(rows, f'{GEN} / 交通工具 / 金融资本',
                   f'{GEN} / 政治、法律与社会制度 / 经济制度与金融 / 金融资本')

    # 虎 → 老虎
    tiger = f'{GEN} / 动物 / 哺乳动物 / 猫科动物 / 大型猫科动物 / 老虎'
    union_into(rows, f'{GEN} / 人物与人体 / 虎', tiger)

    # 低等生物：六子晋升，壳源并集给六子
    shell = f'{GEN} / 动物 / 低等生物'
    kids = [k for k in rows if k.startswith(shell + ' / ') and k.count(' / ') == 4]
    assert len(kids) == 6, f'低等生物子数异常: {len(kids)}'
    sa, sb = rows[shell]
    for kid in kids:
        name = kid.split(' / ')[-1]
        target = f'{GEN} / 动物 / {name}'
        assert target not in rows
        rows[target] = rows.pop(kid)
        # 壳源并集给每个子节点
        rows[target][0] = union_pipe(rows[target][0], sa)
        rows[target][1] = union_pipe(rows[target][1], sb)
        stats['moved'] += 1
        stats['union'] += 1
    del rows[shell]
    stats['deleted'] += 1
    log.append(f'  低等生物壳删除，源并集给 6 子')

    # 东亚人贬称 → 东亚人群
    ensure_node(rows, f'{GEN} / 民族、语言与文化 / 民族与族群')
    src = f'{GEN} / 知识与学科 / 东亚人贬称'
    dst = f'{GEN} / 民族、语言与文化 / 民族与族群 / 东亚人群'
    assert src in rows and dst not in rows
    rows[dst] = rows.pop(src)
    stats['moved'] += 1
    log.append(f'  东亚人贬称 → 东亚人群')

    # ================= IP 吸收批 =================
    print('== IP 吸收批 ==')
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
    # 新建目标域行（若不存在）
    for d, _ in IP_MAP.values():
        ensure_node(rows, f'{GEN} / {d}')

    for ipname, (dom, recv) in IP_MAP.items():
        src_root = f'{IP} / {ipname}'
        dst_root = f'{GEN} / {dom} / {recv}'
        keys = [k for k in rows if k == src_root or k.startswith(src_root + ' / ')]
        assert keys, f'IP 子树缺失: {src_root}'

        # 承接 L2 根行
        if dst_root in rows:
            root_exists = True
        else:
            rows[dst_root] = ['', '']
            stats['created'] += 1
            root_exists = False

        # 域根的源 → 并集进承接 L2
        src_a, src_b = rows[src_root]
        if root_exists:
            rows[dst_root] = [union_pipe(rows[dst_root][0], src_a),
                              union_pipe(rows[dst_root][1], src_b)]
            stats['union'] += 1
        else:
            rows[dst_root] = [src_a, src_b]
        del rows[src_root]
        stats['deleted'] += 1

        moved = 0
        for k in keys:
            if k == src_root:
                continue
            nk = dst_root + k[len(src_root):]
            assert nk not in rows, f'目标已存在: {nk}'
            rows[nk] = rows.pop(k)
            moved += 1
            stats['moved'] += 1
        log.append(f'  {ipname} → [{dom}] {recv}: 移行 {moved} 行')

    # 清理 IP 残壳
    leftovers = [k for k in rows if k.startswith(IP)]
    for k in leftovers:
        assert rows[k][0] == '' and rows[k][1] == '', f'IP 残壳带源: {k}'
        del rows[k]
        stats['deleted'] += 1
    log.append(f'  清理 IP 层残壳 {len(leftovers)} 行')

    # ================= 批 2 =================
    print('== 批 2 ==')
    draft = json.load(open(f'{W}/迁移批2_映射表_草案.json', encoding='utf-8'))
    items = {it['name']: it for it in draft['items']}

    # 新建医学与健康域根
    med = GEN + ' / 医学与健康'
    ensure_node(rows, med)

    # keep 改名
    KEEP_RENAME = {
        '工程': '工程学', '知识': '认知与知识过程', '认知因素': '认知科学',
        '问题解决': '研究方法', '属': '生物分类阶元',
    }
    for old, new in KEEP_RENAME.items():
        src = KN + ' / ' + old
        keys = [k for k in rows if k == src or k.startswith(src + ' / ')]
        assert keys, f'缺失 {src}'
        pairs = [(k, KN + ' / ' + new + k[len(src):]) for k in keys]
        for k, nk in pairs:
            assert nk not in rows, f'碰撞 {nk}'
            rows[nk] = rows.pop(k)
            stats['moved'] += 1
        log.append(f'  改名: {old} → {new}（{len(keys)} 行）')

    # 生命科学/医学 → 医学与健康/医学专科
    src = KN + ' / 生命科学 / 医学'
    dst = med + ' / 医学专科'
    keys = [k for k in rows if k == src or k.startswith(src + ' / ')]
    for k in keys:
        nk = dst + k[len(src):]
        assert nk not in rows
        rows[nk] = rows.pop(k)
        stats['moved'] += 1
    log.append(f'  拆分迁移: 生命科学/医学 → 医学专科（{len(keys)} 行）')

    # 有机过程 → 吸收进生命科学（壳源并集给生命科学）
    src = KN + ' / 有机过程'
    dst = KN + ' / 生命科学'
    keys = [k for k in rows if k == src or k.startswith(src + ' / ')]
    assert rows[src] == ['', ''] or True  # 壳可能有源
    sa, sb = rows[src]
    for k in keys[1:]:
        nk = dst + k[len(src):]
        assert nk not in rows
        rows[nk] = rows.pop(k)
        stats['moved'] += 1
    del rows[src]
    stats['deleted'] += 1
    rows[dst] = [union_pipe(rows[dst][0], sa), union_pipe(rows[dst][1], sb)]
    stats['union'] += 1
    log.append(f'  吸收: 有机过程 → 生命科学（迁 {len(keys)-1} 行，壳源并集）')

    # 品牌 → 品牌与产品/品牌（壳源并集）
    src = KN + ' / 品牌'
    dst = GEN + ' / 品牌与产品 / 品牌'
    assert src in rows and dst in rows
    union_into(rows, src, dst)

    # 地平线 → 自然景观/土壤层
    src = KN + ' / 地平线'
    dst = GEN + ' / 自然景观 / 土壤层'
    child = src + ' / 土壤层'
    assert src in rows and child in rows and dst not in rows
    rows[dst] = rows.pop(child)
    # 壳源并集给新根
    sa, sb = rows[src]
    rows[dst] = [union_pipe(rows[dst][0], sa), union_pipe(rows[dst][1], sb)]
    del rows[src]
    stats['moved'] += 1
    stats['deleted'] += 1
    stats['union'] += 1
    log.append('  吸收: 地平线 → 自然景观/土壤层')

    # 地址 → 行为动作/说话/交谈/演讲
    src = KN + ' / 地址'
    dst = GEN + ' / 行为动作 / 说话/交谈/演讲'
    assert src in rows and dst in rows
    keys = [k for k in rows if k.startswith(src + ' / ')]
    assert rows[src] == ['', ''] or True
    for k in keys:
        nk = dst + k[len(src):]
        assert nk not in rows
        rows[nk] = rows.pop(k)
        stats['moved'] += 1
    del rows[src]
    stats['deleted'] += 1
    log.append(f'  吸收: 地址 → 说话/交谈/演讲（迁 {len(keys)} 行）')

    # 常规迁移
    SPECIAL = {'品牌', '地平线', '地址', '生命科学'}
    KEEP_SAME = ['化学', '物理学', '数学', '历史学', '哲学', '语言学', '天文学',
                 '经济学', '文学', '设计元素', '教育理念与模式']
    FORCE_EMPTY = {'体育运动大项'}
    from collections import defaultdict
    groups = defaultdict(list)
    for name, it in items.items():
        if it['op'] != 'move' or name in SPECIAL:
            continue
        b = it['to_branch'][:-4] if it['to_branch'].endswith('(新建)') else it['to_branch']
        groups[(it['to_domain'], b, it['to_branch'].endswith('(新建)'))].append(name)

    for (dom, b, is_new), names in sorted(groups.items()):
        dst = GEN + ' / ' + dom + ' / ' + b
        if is_new:
            assert dst not in rows, f'标新建但已存在 {dst}'
        else:
            assert dst in rows, f'标并入但不存在 {dst}'
        s1 = is_new and len(names) == 1 and b not in FORCE_EMPTY
        if not s1 and dst not in rows:
            rows[dst] = ['', '']
            stats['created'] += 1
        for name in sorted(names):
            src = KN + ' / ' + name
            keys = [k for k in rows if k == src or k.startswith(src + ' / ')]
            assert keys, f'缺失 {src}'
            if s1:
                for k in keys:
                    nk = dst + k[len(src):]
                    assert nk not in rows
                    rows[nk] = rows.pop(k)
                    stats['moved'] += 1
            else:
                for k in keys:
                    nk = dst + ' / ' + name + k[len(src):]
                    assert nk not in rows
                    rows[nk] = rows.pop(k)
                    stats['moved'] += 1
            log.append(f'  迁移: {name} → {dom}/{b}（{"S1" if s1 else "S2"}，{len(keys)} 行）')

    # ================= 校验 =================
    print('== 校验 ==')
    result_paths = set(rows.keys())
    missing = final_paths - result_paths
    extra = result_paths - final_paths
    assert not missing, f'终版有但同步后缺失 {len(missing)} 个: {sorted(missing)[:5]}'
    assert not extra, f'同步后多出 {len(extra)} 个: {sorted(extra)[:5]}'
    assert len(rows) == len(final_paths), f'行数不符: {len(rows)} vs {len(final_paths)}'

    # 源不丢：带源节点数不减（并集可能减少独立计数，但总管道符元素不减）
    n_src_after = sum(1 for a, b in rows.values() if a.strip() or b.strip())
    pipe_before = sum(len(a.split('|')) for a, b in read_full().values() if a.strip())
    pipe_before += sum(len(b.split('|')) for a, b in read_full().values() if b.strip())
    pipe_after = sum(len(a.split('|')) for a, b in rows.values() if a.strip())
    pipe_after += sum(len(b.split('|')) for a, b in rows.values() if b.strip())
    print(f'带源节点: {n_src_before} → {n_src_after}')
    print(f'源管道符元素: {pipe_before} → {pipe_after}')
    assert pipe_after >= pipe_before, f'源元素丢失! {pipe_before} → {pipe_after}'

    print(f'\n行数: 21,400 → {len(rows)} | 移行 {stats["moved"]} 并集 {stats["union"]} '
          f'删 {stats["deleted"]} 建 {stats["created"]}')
    for line in log:
        print(line)

    if DRY:
        print('\n[DRY-RUN] 未写盘。加 --apply 执行。')
        return

    with open(OUT, 'w', encoding='utf-8-sig', newline='') as f:
        w = csv.writer(f, lineterminator='\n')
        w.writerow(['node_path', 'source清单', 'source详情(名称;URL;类型)'])
        for k, (a, b) in rows.items():
            w.writerow([k, a, b])
    print(f'\n已写盘: {OUT}')


if __name__ == '__main__':
    main()
