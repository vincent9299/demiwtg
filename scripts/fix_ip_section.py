#!/usr/bin/env python3
"""Fix issues in the 'IP 分类标签' section of the cleaned taxonomy tree.

Fixes:
  1. Tail structure: move '跨品类品牌类别' under '品牌 IP', make
     '新兴物种类别' a top-level branch of IP; repair tree connectors.
  2. Delete machine-translation residues / misplaced nodes.
  3. Merge cross-branch duplicate concepts.

Usage:
    python3 scripts/fix_ip_section.py [--write]
"""
import argparse
import difflib
import re
import sys

PATH = "V2融合世界标签体系_清洗版.txt"
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
    """Return first node whose full name-path == names."""
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
    """Render the IP tree in the file's box-drawing format."""
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

    top_brand = next(c for c in ip_root["children"] if c["name"] == "品牌 IP")
    top_event = next(c for c in ip_root["children"] if c["name"] == "赛事、潮玩与互动 IP")

    new_species = find(ip_root, ["IP 分类标签", "赛事、潮玩与互动 IP", "新兴物种类别"])
    cross_brand = find(ip_root, ["IP 分类标签", "赛事、潮玩与互动 IP", "跨品类品牌类别"])
    if new_species is None or cross_brand is None:
        raise SystemExit("tail nodes not found")

    detach(cross_brand)
    attach(top_brand, cross_brand)
    detach(new_species)
    attach(ip_root, new_species)
    ip_root["children"] = [c for c in ip_root["children"]
                           if c is not new_species] + [new_species]

    to_delete = [
        # translation residues / misplaced
        ["IP 分类标签", "真人与人物 IP", "体育明星", "足球运动员", "线路工"],
        ["IP 分类标签", "真人与人物 IP", "体育明星", "足球运动员", "背部"],
        ["IP 分类标签", "真人与人物 IP", "体育明星", "足球运动员", "足球守门员"],
        ["IP 分类标签", "真人与人物 IP", "历史古人", "政治家", "果皮"],
        ["IP 分类标签", "真人与人物 IP", "历史古人", "艺术家", "前拉斐尔派成员", "狩猎"],
        ["IP 分类标签", "真人与人物 IP", "历史古人", "艺术家", "画家", "鱼苗"],
        ["IP 分类标签", "真人与人物 IP", "政治与社会人物", "政府官员", "外交官", "松鸦"],
        ["IP 分类标签", "真人与人物 IP", "政治与社会人物", "政府官员", "推事", "首席大法官", "松鸦"],
        ["IP 分类标签", "真人与人物 IP", "政治与社会人物", "政府官员", "推事", "首席大法官", "汉堡包"],
        ["IP 分类标签", "真人与人物 IP", "科学文化人物", "作家", "灰色"],
        ["IP 分类标签", "真人与人物 IP", "科学文化人物", "作家", "诗人", "武术性的"],
        ["IP 分类标签", "真人与人物 IP", "科学文化人物", "作家", "起重机"],
        ["IP 分类标签", "真人与人物 IP", "科学文化人物", "科学家", "生物学家", "分类学家", "劈裂器"],
        ["IP 分类标签", "真人与人物 IP", "科学文化人物", "科学家", "生物学家", "分类学家", "装卸工"],
        ["IP 分类标签", "真人与人物 IP", "科学文化人物", "科学家", "语言学家", "语音学家", "甜食"],
        ["IP 分类标签", "真人与人物 IP", "音乐艺人", "作曲家", "套索"],
        ["IP 分类标签", "真人与人物 IP", "音乐艺人", "作曲家", "理发师"],
        ["IP 分类标签", "真人与人物 IP", "音乐艺人", "歌手", "多米诺骨牌"],
        ["IP 分类标签", "地标与武器 IP", "武器", "军火", "一次拍摄"],
        ["IP 分类标签", "地标与武器 IP", "武器", "军火", "壳"],
        # cross-branch duplicate merges
        ["IP 分类标签", "作品与文化资产 IP", "音乐作品 IP", "音乐剧 IP"],
        ["IP 分类标签", "作品与文化资产 IP", "文物与馆藏 IP", "陶瓷器", "瓷器"],
        ["IP 分类标签", "品牌 IP", "互联网与软件品牌", "电商平台品牌"],
        ["IP 分类标签", "品牌 IP", "体育运动品牌", "水上运动品牌"],
        ["IP 分类标签", "真人与人物 IP", "科学文化人物", "艺术家"],
        ["IP 分类标签", "真人与人物 IP", "网络内容创作者", "虚拟主播"],
        ["IP 分类标签", "真人与人物 IP", "科学文化人物", "科学家", "心理学家", "心理语言学家"],
    ]
    deleted = []
    for path in to_delete:
        n = find(ip_root, path)
        if n is None:
            print(f"[warn] not found: {'>'.join(path)}", file=sys.stderr)
            continue
        detach(n)
        deleted.append(">".join(path[1:]))

    new_lines = render(ip_root)
    old_lines = lines[start:]
    if new_lines == old_lines:
        print("no change")
        return

    d = list(difflib.unified_diff(old_lines, new_lines, "IP段(旧)", "IP段(新)", lineterm=""))
    if args.write:
        lines[start:] = new_lines
        with open(PATH, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        print(f"written. deleted {len(deleted)} nodes; ip lines {len(old_lines)} -> {len(new_lines)}")
    else:
        print("\n".join(d))
        print(f"\n[preview only] {len(d)} diff lines, {len(deleted)} deletions; run with --write to apply")


if __name__ == "__main__":
    main()
