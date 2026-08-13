# -*- coding: utf-8 -*-
"""V3 构建器（迭代版）：在 V3 树/实例上叠加扩充模块的新增分支、叶子拆分与新实例。

用法：python3 scripts/v3/build_v3.py
输入：data/V3融合世界标签体系.txt + data/ip_instances_v3.json + scripts/v3/exp_*.py
输出：原地更新 V3 树与实例文件（幂等）。

模块接口：
  BRANCH_ADDITIONS = {parent_path: [(name, children), ...]}   # 新增分支（幂等）
  LEAF_SPLITS      = {leaf_path: {child_name: [instances]}}   # 叶子细化：原叶子变中间层，实例迁至子叶
  INSTANCES        = {leaf_path: [instances]}                  # 追加实例（去重合并）
路径均不含根名，形如 "IP 分类标签/地标 IP/..." 或 "地标 IP/..."（实例键）。
"""
import json, re, sys, os, importlib
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
MODULES = ['exp_landmarks', 'exp_geo', 'exp_culture', 'exp_people_brands', 'exp_events_tech',
           'exp_landmarks_city', 'exp_landmarks_city_deep', 'exp_landmarks_city_deep2', 'exp_deepen_brands', 'exp_deepen_brands2', 'exp_deepen_brands3', 'exp_deepen_brands4', 'exp_deepen_people', 'exp_deepen_people2', 'exp_deepen_people3', 'exp_deepen_misc', 'exp_round4_branches', 'exp_round4_fill_a', 'exp_round4_fill_b']
mods = []
for name in MODULES:
    try:
        mods.append(importlib.import_module(name))
    except ModuleNotFoundError:
        pass

V3_TREE = 'data/V3融合世界标签体系.txt'
V3_INST = 'data/ip_instances_v3.json'

# ---------- parse V3 tree ----------
lines = open(V3_TREE, encoding='utf-8').read().splitlines()
nodes = []  # (depth, name)
for ln in lines:
    if not ln.strip():
        continue
    m = re.match(r'^((?:(?:│   |    ))*)(?:├── |└── )(.*)$', ln)
    if m:
        nodes.append((len(m.group(1)) // 4 + 1, m.group(2).strip()))
    else:
        nodes.append((0, ln.strip()))

children = defaultdict(list)
parent = {}
stack = []
for idx, (d, n) in enumerate(nodes):
    while stack and nodes[stack[-1]][0] >= d:
        stack.pop()
    parent[idx] = stack[-1] if stack else -1
    if stack:
        children[stack[-1]].append(idx)
    stack.append(idx)

ROOT_NAME = nodes[0][1]
def path_of(idx):
    p = []
    cur = idx
    while cur != -1:
        p.append(nodes[cur][1]); cur = parent.get(cur, -1)
    return '/'.join(reversed(p))

def norm(p):
    return p[len(ROOT_NAME) + 1:] if p.startswith(ROOT_NAME + '/') else p

def rebuild_index():
    return {norm(path_of(i)): i for i in range(len(nodes))}

by_path = rebuild_index()

# ---------- apply branch additions ----------
def add_subtree(pidx, subtree, added):
    name, kids = subtree
    if name in [nodes[c][1] for c in children[pidx]]:
        exist = [c for c in children[pidx] if nodes[c][1] == name][0]
    else:
        nodes.append((nodes[pidx][0] + 1, name))
        new_idx = len(nodes) - 1
        parent[new_idx] = pidx
        children[pidx].append(new_idx)
        added.append(norm(path_of(new_idx)))
        exist = new_idx
    for k in kids:
        add_subtree(exist, k, added)

total_added = []
for mod in mods:
    for parent_path, subs in getattr(mod, 'BRANCH_ADDITIONS', {}).items():
        if parent_path not in by_path:
            print('!! 父路径不存在:', parent_path); continue
        pidx = by_path[parent_path]
        for s in subs:
            add_subtree(pidx, s, total_added)
    by_path = rebuild_index()

# ---------- apply leaf splits ----------
splits_done = 0
for mod in mods:
    for leaf_path, city_map in getattr(mod, 'LEAF_SPLITS', {}).items():
        full = 'IP 分类标签/' + leaf_path
        if full not in by_path:
            print('!! 拆分目标叶子不存在:', leaf_path); continue
        pidx = by_path[full]
        if children.get(pidx):
            pass  # 已拆分过，继续补子叶（幂等）
        for cname, _ in city_map.items():
            if cname not in [nodes[c][1] for c in children.get(pidx, [])]:
                nodes.append((nodes[pidx][0] + 1, cname))
                ni = len(nodes) - 1
                parent[ni] = pidx
                children[pidx].append(ni)
                total_added.append(norm(path_of(ni)))
        splits_done += 1
    by_path = rebuild_index()

# ---------- merge instances ----------
v3 = json.load(open(V3_INST, encoding='utf-8'))
inst = {k.replace(' / ', '/'): list(v) for k, v in v3['instances'].items()}
old_total = sum(len(v) for v in inst.values())

ip_root_idx = by_path['IP 分类标签']
leaf_rel = {}
def collect_leaves(idx):
    if not children.get(idx):
        leaf_rel[norm(path_of(idx)).replace('IP 分类标签/', '', 1)] = True
    for c in children.get(idx, []):
        collect_leaves(c)
collect_leaves(ip_root_idx)

# split: 原叶子实例清空，子叶实例注入
new_inst_count = 0
for mod in mods:
    for leaf_path, city_map in getattr(mod, 'LEAF_SPLITS', {}).items():
        if leaf_path in inst and children.get(by_path.get('IP 分类标签/' + leaf_path, -1)):
            del inst[leaf_path]
        for cname, vals in city_map.items():
            child_key = leaf_path + '/' + cname
            bucket = inst.setdefault(child_key, [])
            for v in vals:
                if v not in bucket:
                    bucket.append(v); new_inst_count += 1

split_parents = set()
for mod in mods:
    split_parents.update(getattr(mod, 'LEAF_SPLITS', {}).keys())
missing_leaves = []
for mod in mods:
    for leaf_path, vals in getattr(mod, 'INSTANCES', {}).items():
        if leaf_path not in leaf_rel:
            if leaf_path not in split_parents:
                missing_leaves.append(leaf_path)
            continue
        bucket = inst.setdefault(leaf_path, [])
        for v in vals:
            if v not in bucket:
                bucket.append(v); new_inst_count += 1

if missing_leaves:
    print('!! 实例指向的叶子不存在 (%d):' % len(missing_leaves))
    for m in missing_leaves: print('   ', m)

# ---------- write V3 tree ----------
out = [ROOT_NAME]
def emit(idx, prefix):
    for i, c in enumerate(children.get(idx, [])):
        last = (i == len(children[idx]) - 1)
        out.append(prefix + ('└── ' if last else '├── ') + nodes[c][1])
        emit(c, prefix + ('    ' if last else '│   '))
emit(0, '')
open(V3_TREE, 'w', encoding='utf-8').write('\n'.join(out) + '\n')

# ---------- write V3 instances ----------
meta = {
    "source_tree": "data/V3融合世界标签体系.txt",
    "generated": "2026-08-13",
    "base": "V3 迭代：叶子细化拆分 %d 处，新增分支节点 %d 个，本轮新增实例 %d 条" % (
        splits_done, len(total_added), new_inst_count),
    "path_separator": " / ",
    "note": "覆盖全优先，不做跨分支去重；同一实体允许多视角挂载。地标已细化至城市级。",
}
json.dump({"meta": meta, "instances": {k.replace('/', ' / '): inst[k] for k in sorted(inst, key=lambda x: x.split('/')[0])}},
          open(V3_INST, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)

# ---------- stats ----------
print('V3 树节点总数: %d (本轮新增分支节点 %d, 其中拆分细叶 %d 处)' % (len(nodes), len(total_added), splits_done))
ip_nodes = [i for i in range(len(nodes)) if norm(path_of(i)).startswith('IP 分类标签')]
ip_leaves = [i for i in ip_nodes if not children.get(i)]
print('IP 段节点: %d, 叶子: %d' % (len(ip_nodes), len(ip_leaves)))
new_total = sum(len(v) for v in inst.values())
print('实例: %d -> %d (新增 %d)' % (old_total, new_total, new_inst_count))
few = [(k, len(v)) for k, v in inst.items() if len(v) < 8]
print('实例数 <8 的叶子: %d' % len(few))
