#!/usr/bin/env python3
"""Split two IP top-level branches:
   - '作品与文化资产 IP' -> '内容作品 IP' + '艺术与文物 IP'
   - '赛事、潮玩与互动 IP' -> '赛事 IP' + '潮玩互动 IP' + '乐园节庆 IP'
"""
import re

PATH = "V2融合世界标签体系_清洗版.txt"
NODE_RE = re.compile(r'^((?:[│  ]{4})*)([├└]── (.*))$')

WORKS = ["动漫作品 IP", "影视作品 IP", "戏曲舞台作品 IP", "文学作品 IP",
         "游戏作品 IP", "音乐作品 IP"]
ARTS = ["名画", "建筑艺术 IP", "雕塑艺术品", "文物与馆藏 IP"]
EVENT = ["体育赛事 IP", "电子竞技赛事 IP"]
TOYS = ["桌游卡牌 IP", "潮玩手办 IP"]
FUN = ["主题乐园 IP", "展览节庆 IP", "综艺娱乐 IP"]


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


def split(ip_root, old_name, new_branches):
    old = find(ip_root, old_name)
    if old is None:
        raise SystemExit(f"not found: {old_name}")
    kids = list(old["children"])
    by_name = {k["name"]: k for k in kids}
    for names in new_branches.values():
        for n in names:
            if n not in by_name:
                raise SystemExit(f"missing child: {old_name} > {n}")
    for k in kids:
        k["parent"] = None
    created = []
    for new_name, names in new_branches.items():
        children = [by_name[n] for n in names]
        b = {"name": new_name, "children": children, "parent": ip_root, "depth": 1}
        for c in children:
            c["parent"] = b
        created.append(b)
    idx = ip_root["children"].index(old)
    ip_root["children"][idx:idx + 1] = created


def main():
    ip_root, lines, start = parse()

    split(ip_root, "作品与文化资产 IP",
          {"内容作品 IP": WORKS, "艺术与文物 IP": ARTS})
    split(ip_root, "赛事、潮玩与互动 IP",
          {"赛事 IP": EVENT, "潮玩互动 IP": TOYS, "乐园节庆 IP": FUN})

    lines[start:] = render(ip_root)
    with open(PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print("written")


if __name__ == "__main__":
    main()
