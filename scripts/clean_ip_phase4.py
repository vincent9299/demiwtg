#!/usr/bin/env python3
"""Phase 4 IP section cleanup: review items #6, #7, #10.

Operations:
  #10. Delete misplaced leaf nodes:
       - 体育明星/篮球运动员/前锋, 篮球运动员/得分后卫
       - 体育明星/足球运动员/守门员
       - 科学文化人物/科学家/英雄
  #6.  Delete obviously-wrong facet wrappers:
       - 影像品牌 / 电脑品牌 (under 数码 3C 品牌) only have facet children
       - 箱包品牌 (under 箱包配饰品牌) only has facet children
       - 酒类品牌 / 饮料品牌 (under 零食饮料小吃品牌) only have facet children
       - 工程师 (under 科学文化人物) has unique 呈现范围/活动情境 facets
         not shared by any other person branch — delete them, keep 黑客
  #7.  Strip ' IP' suffix from all non-top-level nodes (194 nodes).

Usage:
    python3 scripts/clean_ip_phase4.py [--write]
"""
import argparse
import difflib
import re
import sys

PATH = "data/V2融合世界标签体系_清洗版.txt"
NODE_RE = re.compile(r'^((?:[│  ]{4})*)([├└]── (.*))$')


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


def walk_all(node):
    yield node
    for c in node["children"]:
        yield from walk_all(c)


def detach(node):
    par = node["parent"]
    par["children"].remove(node)
    node["parent"] = None


def attach(parent, node):
    node["parent"] = parent
    parent["children"].append(node)


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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args()

    ip_root, lines, start = parse()
    log = []

    # ---- #10. Delete misplaced leaf nodes ----
    del_paths = [
        ["IP 分类标签", "真人与人物 IP", "体育明星", "篮球运动员", "前锋"],
        ["IP 分类标签", "真人与人物 IP", "体育明星", "篮球运动员", "得分后卫"],
        ["IP 分类标签", "真人与人物 IP", "体育明星", "足球运动员", "守门员"],
        ["IP 分类标签", "真人与人物 IP", "科学文化人物", "科学家", "英雄"],
    ]
    for p in del_paths:
        n = find(ip_root, p)
        if n is None:
            print(f"[warn] #10 not found: {'>'.join(p)}", file=sys.stderr)
            continue
        detach(n)
        log.append(f"#10: deleted {'>'.join(p[1:])}")

    # ---- #6. Delete obviously-wrong facet wrappers ----
    # Leaf-style brand nodes that only have facet-template children.
    # Detach the facet wrappers, leaving the brand node as a pure leaf.
    leaf_brand_facets = [
        (["IP 分类标签", "品牌 IP", "数码 3C 品牌", "影像品牌"], None),
        (["IP 分类标签", "品牌 IP", "数码 3C 品牌", "电脑品牌"], None),
        (["IP 分类标签", "品牌 IP", "箱包配饰品牌", "箱包品牌"], None),
        (["IP 分类标签", "品牌 IP", "零食饮料小吃品牌", "酒类品牌"], None),
        (["IP 分类标签", "品牌 IP", "零食饮料小吃品牌", "饮料品牌"], None),
    ]
    for parent_path, _ in leaf_brand_facets:
        parent = find(ip_root, parent_path)
        if parent is None:
            print(f"[warn] #6 parent not found: {'>'.join(parent_path)}", file=sys.stderr)
            continue
        # detach ALL children (they are all facet templates)
        kids = list(parent["children"])
        for kid in kids:
            p = ">".join(path_of(kid)[1:])
            n = sum(1 for _ in walk_all(kid))
            detach(kid)
            log.append(f"#6: deleted facet {p} ({n} nodes)")

    # Engineer unique facets: delete 呈现范围 + 活动情境, keep 黑客
    engineer_path = ["IP 分类标签", "真人与人物 IP", "科学文化人物", "工程师"]
    engineer = find(ip_root, engineer_path)
    if engineer is None:
        print("[warn] #6 工程师 not found", file=sys.stderr)
    else:
        for facet_name in ("工程师—呈现范围", "工程师—活动情境"):
            kid = next((c for c in engineer["children"] if c["name"] == facet_name), None)
            if kid is None:
                print(f"[warn] #6 {facet_name} not under 工程师", file=sys.stderr)
                continue
            p = ">".join(path_of(kid)[1:])
            n = sum(1 for _ in walk_all(kid))
            detach(kid)
            log.append(f"#6: deleted unique facet {p} ({n} nodes; kept 黑客)")

    # ---- #7. Strip ' IP' suffix from all non-top-level nodes ----
    renamed = 0
    for n in walk_all(ip_root):
        if n is ip_root:
            continue
        if n["depth"] == 1:  # top-level branch (one of 19)
            continue
        if n["name"].endswith(" IP"):
            n["name"] = n["name"][:-3]
            renamed += 1
    log.append(f"#7: stripped ' IP' suffix from {renamed} non-top-level nodes")

    # ---- render & diff ----
    new_lines = render(ip_root)
    old_lines = lines[start:]
    print("# operations:")
    for entry in log:
        print(f"  - {entry}")

    if new_lines == old_lines:
        print("no tree change")
        return

    d = list(difflib.unified_diff(old_lines, new_lines, "IP段(旧)", "IP段(新)", lineterm=""))
    if args.write:
        lines[start:] = new_lines
        with open(PATH, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        print(f"\nwritten. ip lines {len(old_lines)} -> {len(new_lines)}")
    else:
        print("\n" + "\n".join(d))
        print(f"\n[preview only] {len(d)} diff lines; run with --write to apply")


if __name__ == "__main__":
    main()
