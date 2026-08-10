#!/usr/bin/env python3
"""Analyze residual suspicious names: print context path, subtree size, parent.

Usage:
    python3 scripts/analyze_residuals.py "清洗后_残留可疑命名.csv"  [--name 标签名]
"""
import re
import sys

NODE_RE = re.compile(r'^((?:[│  ]{4})*)([├└]── (.*))$')

path = "V2融合世界标签体系_清洗版.txt"
lines = open(path, encoding="utf-8").read().splitlines()

nodes = []  # (depth, name, lineno)
for i, line in enumerate(lines, start=1):
    m = NODE_RE.match(line)
    if not m:
        continue
    prefix, _, name = m.groups()
    nodes.append((len(prefix) // 4 + 1, name, i))

parent = {}
subtree_size = {}
subtree_leaves = {}
children_map = {}

stack = []
for depth, name, lineno in nodes:
    while stack and stack[-1][0] >= depth:
        stack.pop()
    pid = stack[-1][2] if stack else None
    parent[lineno] = pid
    children_map.setdefault(pid, []).append(lineno)
    stack.append((depth, name, lineno))

for lineno in sorted(parent, reverse=True):
    kids = children_map.get(lineno, [])
    if not kids:
        subtree_size[lineno] = 1
    else:
        subtree_size[lineno] = 1 + sum(subtree_size[k] for k in kids)

# path string for a lineno
def path_str(lineno):
    parts = []
    cur = lineno
    while cur is not None:
        _, name, ln = next(n for n in nodes if n[2] == cur)
        parts.append(name)
        cur = parent[cur]
    return "/".join(reversed(parts))

if __name__ == "__main__":
    import csv
    from collections import defaultdict
    if len(sys.argv) > 2 and sys.argv[2] == "--name":
        target = sys.argv[3]
        hits = [(ln, n) for d, n, ln in nodes if n == target]
        for ln, n in hits:
            print(f"{n}  |  line {ln}  |  size={subtree_size[ln]}  |  {path_str(ln)}")
        sys.exit(0)
    csv_path = sys.argv[1] if len(sys.argv) > 1 else "清洗后_残留可疑命名.csv"
    with open(csv_path, encoding="utf-8") as f:
        reader = csv.reader(f)
        next(reader)
        rows = [r for r in reader if len(r) >= 2]

    by_type = defaultdict(list)
    for typ, name, *rest in rows:
        by_type[typ].append(name)

    for typ, names in by_type.items():
        print(f"\n===== {typ}  ({len(names)}) =====")
        for name in names:
            hits = [(ln, n) for d, n, ln in nodes if n == name]
            for ln, n in hits:
                p = parent[ln]
                pname = next((n for d, n, l in nodes if l == p), "-") if p else "-"
                print(f"  {name:<12} L{ln:<6} size={subtree_size[ln]:<4} parent={pname:<10} | {path_str(ln)}")
