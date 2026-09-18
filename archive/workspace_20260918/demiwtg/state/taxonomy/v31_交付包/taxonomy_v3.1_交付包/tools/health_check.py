#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""标签体系数据体检脚本（只读，不改任何文件）。

用法:
    python3 health_check.py <cn_csv> <en_csv> [--out <工单输出目录>]

检查项（实测基线见 SKILL.md）:
    1. 同父同名碰撞（树结构重复）           —— 期望 0
    2. 空中间分支（无实例无子节点）          —— 期望 0
    3. 叶内重复实例                          —— 期望 0
    4. 中英行数一致 + EN 首段禁含中文        —— 期望全过
    5. 跨叶重复实例 = 域内重复 + 跨域重复    —— 域内重复进裁决表
    6. 超大叶(>=60) / 异常小叶(<=3)          —— 超大叶进拆叶评审
    7. L2 深/平结构分解

工单输出（--out 指定目录，默认当前目录）:
    工单_域内重复实例.csv   —— 域内重复，平行分支合并候选
    工单_超大叶.csv         —— >=60 实例的叶子
    工单_同名段观察.csv     —— 不同路径下的同名节点（观察项）
"""
import csv
import os
import sys
import re
from collections import Counter, defaultdict

BIG_LEAF = 60      # 超大叶阈值
TINY_LEAF = 3      # 异常小叶阈值
HAS_CJK = re.compile(r'[\u4e00-\u9fff]')


def load_cn(path):
    rows = {}
    with open(path, encoding='utf-8-sig', newline='') as f:
        r = csv.reader(f)
        next(r)  # header
        for row in r:
            if row:
                rows[row[0]] = row[1] if len(row) > 1 else ''
    return rows


def load_en(path):
    rows = {}
    with open(path, encoding='utf-8-sig', newline='') as f:
        r = csv.reader(f)
        next(r)
        for row in r:
            if row:
                rows[row[0]] = row[1] if len(row) > 1 else ''
    return rows


def domain_of(p):
    parts = p.split(' / ')
    return parts[2] if len(parts) > 2 else '?'


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)
    cn_path, en_path = sys.argv[1], sys.argv[2]
    out_dir = sys.argv[sys.argv.index('--out') + 1] if '--out' in sys.argv else '.'
    os.makedirs(out_dir, exist_ok=True)

    cn = load_cn(cn_path)
    en = load_en(en_path)
    paths = set(cn)
    problems = []

    print(f'CN 行数: {len(cn)} | EN 行数: {len(en)}')
    if len(cn) != len(en):
        problems.append(f'中英行数不一致: {len(cn)} vs {len(en)}')
    if set(cn) != set(en):
        only_cn = set(cn) - set(en)
        only_en = set(en) - set(cn)
        problems.append(f'中英路径集不一致: 仅CN {len(only_cn)} / 仅EN {len(only_en)}')

    # 1. 同父同名碰撞
    parent_children = defaultdict(list)
    for p in paths:
        parts = p.split(' / ')
        if len(parts) >= 2:
            parent_children[' / '.join(parts[:-1])].append(parts[-1])
    collisions = {par: [n for n, c in Counter(kids).items() if c > 1]
                  for par, kids in parent_children.items()}
    collisions = {k: v for k, v in collisions.items() if v}
    print(f'[1] 同父同名碰撞: {len(collisions)} 个父节点 {"✅" if not collisions else "⚠️"}')
    if collisions:
        problems.append(f'同父同名碰撞 {len(collisions)} 处')

    # 2. 空中间分支
    has_child = set()
    for p in paths:
        parts = p.split(' / ')
        for i in range(1, len(parts)):
            has_child.add(' / '.join(parts[:i]))
    empty_mid = [p for p, v in cn.items() if not v and p not in has_child]
    print(f'[2] 空中间分支: {len(empty_mid)} 个 {"✅" if not empty_mid else "⚠️"}')
    if empty_mid:
        problems.append(f'空中间分支 {len(empty_mid)} 个')

    # 3. 叶内重复实例
    inleaf_dup = [p for p, v in cn.items() if v and len(v.split('|')) != len(set(v.split('|')))]
    print(f'[3] 叶内重复实例的叶子: {len(inleaf_dup)} 个 {"✅" if not inleaf_dup else "⚠️"}')
    if inleaf_dup:
        problems.append(f'叶内重复 {len(inleaf_dup)} 叶')

    # 4. EN 首段禁含中文
    bad_en = [p for p, ep in en.items() if ep and HAS_CJK.search(ep.split(' / ')[0])]
    print(f'[4] EN 首段含中文: {len(bad_en)} 行 {"✅" if not bad_en else "⚠️"}')
    if bad_en:
        problems.append(f'EN 首段含中文 {len(bad_en)} 行')

    # 5. 跨叶重复实例
    inst_loc = defaultdict(list)
    for p, v in cn.items():
        if not v:
            continue
        d = domain_of(p)
        for inst in set(v.split('|')):
            inst = inst.strip()
            if inst:
                inst_loc[inst].append((d, p))
    same_dom, cross_dom = [], []
    for inst, locs in inst_loc.items():
        if len(locs) < 2:
            continue
        doms = {d for d, _ in locs}
        (same_dom if len(doms) == 1 else cross_dom).append((inst, locs))
    print(f'[5] 跨叶重复实例: {len(same_dom) + len(cross_dom)} '
          f'= 域内 {len(same_dom)}（进裁决表）+ 跨域 {len(cross_dom)}（多归属，抽审）')

    # 6. 超大叶 / 小叶
    leaf_sizes = [(p, len(v.split('|'))) for p, v in cn.items() if v]
    big = sorted([(p, c) for p, c in leaf_sizes if c >= BIG_LEAF], key=lambda x: -x[1])
    tiny = [(p, c) for p, c in leaf_sizes if c <= TINY_LEAF]
    print(f'[6] 超大叶(>={BIG_LEAF}): {len(big)} 个 | 异常小叶(<={TINY_LEAF}): {len(tiny)} 个')

    # 7. L2 深/平分解（L2 = 第 4 段, count(' / ')==3）
    l2s = [p for p in paths if p.count(' / ') == 3]
    deep = sum(1 for l2 in l2s
               if any(q.startswith(l2 + ' / ') and q in has_child for q in paths))
    l2_inst = sum(1 for p in l2s if cn.get(p))
    print(f'[7] L2 总数: {len(l2s)} | 真深层: {deep} | 纯平: {len(l2s) - deep} | L2 直挂实例: {l2_inst}')

    # 工单
    with open(os.path.join(out_dir, '工单_域内重复实例.csv'), 'w',
              encoding='utf-8-sig', newline='') as f:
        w = csv.writer(f, lineterminator='\n')
        w.writerow(['实例', '域', '出现次数', '路径1', '路径2', '路径3'])
        for inst, locs in sorted(same_dom, key=lambda x: (x[1][0][0], x[0])):
            ps = [p for _, p in locs]
            w.writerow([inst, locs[0][0], len(locs)] + ps[:3] + [''] * max(0, 3 - len(ps[:3])))
    with open(os.path.join(out_dir, '工单_超大叶.csv'), 'w',
              encoding='utf-8-sig', newline='') as f:
        w = csv.writer(f, lineterminator='\n')
        w.writerow(['实例数', '域', '路径'])
        for p, c in big:
            w.writerow([c, domain_of(p), p])
    seg_names = Counter(p.split(' / ')[-1] for p in paths)
    dup_names = {n: c for n, c in seg_names.items() if c > 1}
    with open(os.path.join(out_dir, '工单_同名段观察.csv'), 'w',
              encoding='utf-8-sig', newline='') as f:
        w = csv.writer(f, lineterminator='\n')
        w.writerow(['段名', '出现次数'])
        for n, c in sorted(dup_names.items(), key=lambda x: -x[1]):
            w.writerow([n, c])

    print(f'\n工单已写入 {out_dir}: 工单_域内重复实例 / 工单_超大叶 / 工单_同名段观察')
    if problems:
        print('\n⚠️ 问题清单:')
        for x in problems:
            print('  -', x)
        sys.exit(2)
    print('\n✅ 结构类检查全部通过（重复实例/超大叶为评审工单，非结构错误）')


if __name__ == '__main__':
    main()
