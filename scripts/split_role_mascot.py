#!/usr/bin/env python3
"""Split '虚构角色与形象 IP' into two top-level branches under IP 分类标签:
   - 虚构角色 IP      (9 角色类二级节点)
   - 吉祥物与形象 IP  (4 吉祥物/形象类二级节点)
"""
import re

PATH = "V2融合世界标签体系_清洗版.txt"
NODE_RE = re.compile(r'^((?:[│  ]{4})*)([├└]── (.*))$')

ROLE = ["儿童绘本角色", "动漫角色", "影视角色", "戏曲舞台剧角色",
        "文学小说角色", "游戏角色", "神话角色", "超级英雄与漫画角色",
        "志怪鬼怪形象"]
MASCOT = ["品牌吉祥物", "城市文旅吉祥物", "图腾瑞兽", "萌宠具象形象 IP"]


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

    old = find(ip_root, "虚构角色与形象 IP")
    if old is None:
        raise SystemExit("虚构角色与形象 IP not found")

    kids = list(old["children"])
    by_name = {k["name"]: k for k in kids}
    for n in ROLE + MASCOT:
        if n not in by_name:
            raise SystemExit(f"missing child: {n}")
    for k in kids:
        k["parent"] = None

    def new_branch(name, child_names):
        children = [by_name[n] for n in child_names]
        b = {"name": name, "children": children, "parent": ip_root, "depth": 1}
        for c in children:
            c["parent"] = b
        return b

    b_role = new_branch("虚构角色 IP", ROLE)
    b_mascot = new_branch("吉祥物与形象 IP", MASCOT)

    idx = ip_root["children"].index(old)
    ip_root["children"][idx:idx + 1] = [b_role, b_mascot]

    lines[start:] = render(ip_root)
    with open(PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print("written")


if __name__ == "__main__":
    main()
