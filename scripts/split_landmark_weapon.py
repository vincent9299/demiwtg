#!/usr/bin/env python3
"""Split '地标与武器 IP' into three top-level branches under IP 分类标签:
   - 地标 IP       (历史文物地标 IP, 风景名胜地标)
   - 武器 IP       (武器)
   - 著名载具 IP   (著名交通载具 IP)
"""
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
        prefix, _, name = m.groups()
        nodes.append({"depth": len(prefix) // 4, "name": name, "children": []})
    stack = []
    for n in nodes:
        while stack and stack[-1]["depth"] >= n["depth"]:
            stack.pop()
        n["parent"] = stack[-1] if stack else None
        if stack:
            stack[-1]["children"].append(n)
        stack.append(n)
    return nodes[0], lines, start


def find(node, name):
    if node["name"] == name:
        return node
    for c in node["children"]:
        r = find(c, name)
        if r:
            return r
    return None


def render(node, prefix="", last=True):
    branch = "└── " if last else "├── "
    line = prefix + branch + node["name"]
    child_prefix = prefix + ("    " if last else "│   ")
    lines = [line]
    for i, c in enumerate(node["children"]):
        lines.extend(render(c, child_prefix, i == len(node["children"]) - 1))
    return lines


def main():
    ip_root, lines, start = parse()

    old = find(ip_root, "地标与武器 IP")
    if old is None:
        raise SystemExit("地标与武器 IP not found")

    # detach all children, rename them into new top-level branches
    kids = list(old["children"])
    landmark = next(c for c in kids if c["name"] == "历史文物地标 IP")
    scenery = next(c for c in kids if c["name"] == "风景名胜地标")
    weapon = next(c for c in kids if c["name"] == "武器")
    vehicle = next(c for c in kids if c["name"] == "著名交通载具 IP")

    for k in kids:
        k["parent"] = None

    def new_branch(name, children):
        return {"name": name, "children": children, "parent": ip_root, "depth": 1}

    b_landmark = new_branch("地标 IP", [landmark, scenery])
    b_weapon = new_branch("武器 IP", [weapon])
    b_vehicle = new_branch("著名载具 IP", [vehicle])
    for b in (b_landmark, b_weapon, b_vehicle):
        for c in b["children"]:
            c["parent"] = b

    # replace old branch in ip_root children, keeping position
    idx = ip_root["children"].index(old)
    ip_root["children"][idx:idx + 1] = [b_landmark, b_weapon, b_vehicle]

    new_lines = render(ip_root)
    lines[start:] = new_lines
    with open(PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"written. old ip lines {len(lines[start:]) - len(new_lines)}")


if __name__ == "__main__":
    main()
