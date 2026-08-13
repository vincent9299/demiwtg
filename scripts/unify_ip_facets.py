#!/usr/bin/env python3
"""Complete 品牌 IP facet coverage under the two-profile governed scheme.

Decision (review #2, 2026-08-10): the IP section keeps TWO facet profiles,
governed by node archetype:
  Profile A (—传播范围 + —呈现载体): content/cultural/individual-person IPs.
  Profile B (—组织范围 + —运行方式): 品牌 IP and team/group nodes in 真人与人物 IP.
This script only fixes the uneven coverage inside 品牌 IP: 10 of its 23
subcategories lack Profile-B facets. We add —组织范围 + —运行方式 to those,
matching their siblings. The meta branch 跨品类品牌类别 is left untouched.

Usage:
    python3 scripts/unify_ip_facets.py [--write]
"""
import argparse
import difflib
import re
import sys

PATH = "data/V2融合世界标签体系_清洗版.txt"
NODE_RE = re.compile(r'^((?:[│  ]{4})*)([├└]── (.*))$')

ORG_VALS = ["全国级", "国际级", "地方级"]
RUN_VALS = ["公共管理运行", "公益运行", "商业运行"]


def parse():
    lines = open(PATH, encoding="utf-8").read().splitlines()
    start = next(i for i, l in enumerate(lines) if l.strip().endswith("IP 分类标签"))
    nodes = []
    for i, line in enumerate(lines[start:], start=start):
        m = NODE_RE.match(line)
        if not m:
            raise SystemExit(f"parse error line {i + 1}: {line!r}")
        prefix, _, name = m.groups()
        nodes.append({"depth": len(prefix) // 4, "name": name, "line": i + 1,
                      "children": []})
    stack = []
    for n in nodes:
        while stack and stack[-1]["depth"] >= n["depth"]:
            stack.pop()
        n["parent"] = stack[-1] if stack else None
        if stack:
            stack[-1]["children"].append(n)
        stack.append(n)
    return nodes[0], lines, start


def path_of(node):
    p = []
    while node:
        p.append(node["name"])
        node = node.get("parent")
    return list(reversed(p))


def find(root, names):
    def walk(n):
        if path_of(n) == names:
            return n
        for c in n["children"]:
            r = walk(c)
            if r:
                return r
        return None
    return walk(root)


def render(ip_root):
    def render_node(node, prefix, last):
        branch = "└── " if last else "├── "
        line = prefix + branch + node["name"]
        child_prefix = prefix + ("    " if last else "│   ")
        lines = [line]
        for i, c in enumerate(node["children"]):
            lines.extend(render_node(c, child_prefix, i == len(node["children"]) - 1))
        return lines
    return render_node(ip_root, "", True)


def make_facet(base, dim, vals):
    node = {"name": f"{base}—{dim}", "children": [], "depth": 0, "parent": None}
    for v in vals:
        node["children"].append({"name": f"{base}（{v}）", "children": [],
                                 "depth": 0, "parent": None})
    return node


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args()

    ip_root, lines, start = parse()
    brand_ip = find(ip_root, ["IP 分类标签", "品牌 IP"])
    if brand_ip is None:
        raise SystemExit("品牌 IP not found")

    log = []
    for sub in list(brand_ip["children"]):
        if sub["name"] == "跨品类品牌类别":
            continue
        has_org = any(c["name"].endswith("—组织范围") for c in sub["children"])
        has_run = any(c["name"].endswith("—运行方式") for c in sub["children"])
        if has_org and has_run:
            continue
        base = sub["name"] + "类别"
        if not has_org:
            sub["children"].append(make_facet(base, "组织范围", ORG_VALS))
            log.append(f"added 组织范围 to {sub['name']}")
        if not has_run:
            sub["children"].append(make_facet(base, "运行方式", RUN_VALS))
            log.append(f"added 运行方式 to {sub['name']}")

    new_lines = render(ip_root)
    old_lines = lines[start:]
    print("# operations:")
    for e in log:
        print(f"  - {e}")
    if new_lines == old_lines:
        print("no tree change")
        return
    d = list(difflib.unified_diff(old_lines, new_lines, "IP段(旧)", "IP段(新)", lineterm=""))
    if args.write:
        lines[start:] = new_lines
        with open(PATH, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        print(f"\nwritten. ip lines {len(old_lines)} -> {len(new_lines)}, "
              f"+{len(new_lines) - len(old_lines)} nodes")
    else:
        print("\n" + "\n".join(d))
        print(f"\n[preview only] run with --write to apply")


if __name__ == "__main__":
    main()
