# -*- coding: utf-8 -*-
"""V3 构建器：在 V2 树基础上叠加各扩充模块的新增分支与实例，产出 V3 树与实例文件。

用法：python3 scripts/v3/build_v3.py
输入：data/V2融合世界标签体系_清洗版.txt + data/ip_instances.json + scripts/v3/exp_*.py
输出：data/V3融合世界标签体系.txt + data/ip_instances_v3.json
"""
import json, re, sys, os
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import exp_landmarks, exp_geo, exp_culture, exp_people_brands, exp_events_tech

V2_TREE = 'data/V2融合世界标签体系_清洗版.txt'
V2_INST = 'data/ip_instances.json'
V3_TREE = 'data/V3融合世界标签体系.txt'
V3_INST = 'data/ip_instances_v3.json'

# ---------- parse V2 tree ----------
lines = open(V2_TREE, encoding='utf-8').read().splitlines()
nodes = []  # (depth, name)
for ln in lines:
    if not ln.strip():
        continue
    m = re.match(r'^((?:(?:│   |    ))*)(?:├── |└── )(.*)$', ln)
    if m:
        nodes.append((len(m.group(1)) // 4 + 1, m.group(2).strip()))
    else:
        nodes.append((0, ln.strip()))  # root line

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

def path_of(idx):
    p = []
    cur = idx
    while cur != -1:
        p.append(nodes[cur][1]); cur = parent.get(cur, -1)
    return '/'.join(reversed(p))

ROOT_NAME = nodes[0][1]
def norm(p):
    return p[len(ROOT_NAME) + 1:] if p.startswith(ROOT_NAME + '/') else p

by_path = {norm(path_of(i)): i for i in range(len(nodes))}

# ---------- apply branch additions ----------
def add_subtree(pidx, subtree, added):
    name, kids = subtree
    if name in [nodes[c][1] for c in children[pidx]]:
        # 已存在则递归进入（幂等）
        exist = [c for c in children[pidx] if nodes[c][1] == name][0]
    else:
        nodes.append((nodes[pidx][0] + 1, name))
        new_idx = len(nodes) - 1
        parent[new_idx] = pidx
        children[pidx].append(new_idx)
        added.append(path_of(new_idx))
        exist = new_idx
    for k in kids:
        add_subtree(exist, k, added)

total_added = []
modules = [exp_landmarks, exp_geo, exp_culture, exp_people_brands, exp_events_tech]
for mod in modules:
    for parent_path, subs in mod.BRANCH_ADDITIONS.items():
        if parent_path not in by_path:
            print('!! 父路径不存在:', parent_path); continue
        pidx = by_path[parent_path]
        for s in subs:
            add_subtree(pidx, s, total_added)
        # rebuild by_path for new nodes
    by_path = {norm(path_of(i)): i for i in range(len(nodes))}

# ---------- merge instances ----------
v2 = json.load(open(V2_INST, encoding='utf-8'))
inst = {k.replace(' / ', '/'): list(v) for k, v in v2['instances'].items()}

ip_root_idx = by_path['IP 分类标签']
leaf_rel = {}
def collect_leaves(idx):
    if not children.get(idx):
        leaf_rel[norm(path_of(idx)).replace('IP 分类标签/', '', 1)] = True
    for c in children.get(idx, []):
        collect_leaves(c)
collect_leaves(ip_root_idx)

new_inst_count = 0
missing_leaves = []
for mod in modules:
    for leaf_path, vals in mod.INSTANCES.items():
        if leaf_path not in leaf_rel:
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
out = ['V3融合世界标签体系']
def emit(idx, prefix, is_last):
    for i, c in enumerate(children.get(idx, [])):
        last = (i == len(children[idx]) - 1)
        out.append(prefix + ('└── ' if last else '├── ') + nodes[c][1])
        emit(c, prefix + ('    ' if last else '│   '), last)
emit(0, '', True)
open(V3_TREE, 'w', encoding='utf-8').write('\n'.join(out) + '\n')

# ---------- write V3 instances ----------
out_inst = {k: inst[k] for k in sorted(inst, key=lambda x: x.split('/')[0])}
meta = {
    "source_tree": "data/V3融合世界标签体系.txt",
    "generated": "2026-08-13",
    "base": "V2清洗版 (710 叶 / 2813 实例) + scripts/v3/exp_*.py 扩充",
    "scope": "V3 全量扩充：新增二级/三级分支 %d 个，新增实例 %d 条，叶子实例总数 %d" % (
        len(total_added), new_inst_count, sum(len(v) for v in out_inst.values())),
    "path_separator": " / ",
    "note": "本轮以覆盖全为目标，不做跨分支去重；同一实体允许多视角挂载。",
}
json.dump({"meta": meta, "instances": {k.replace('/', ' / '): v for k, v in out_inst.items()}},
          open(V3_INST, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)

# ---------- stats ----------
print('V3 树节点总数: %d (V2: %d, 新增分支节点 %d)' % (len(nodes), len(lines), len(total_added)))
ip_nodes = [i for i in range(len(nodes)) if norm(path_of(i)).startswith('IP 分类标签')]
ip_leaves = [i for i in ip_nodes if not children.get(i)]
print('IP 段节点: %d, 叶子: %d' % (len(ip_nodes), len(ip_leaves)))
print('新增分支节点 %d 个:' % len(total_added))
for a in total_added: print('   +', a)
print('实例: V2 %d 条 -> V3 %d 条 (新增 %d)' % (
    sum(len(v) for v in v2['instances'].values()),
    sum(len(v) for v in out_inst.values()), new_inst_count))
few = [(k, len(v)) for k, v in out_inst.items() if len(v) < 3]
print('实例数 <3 的叶子: %d' % len(few))
