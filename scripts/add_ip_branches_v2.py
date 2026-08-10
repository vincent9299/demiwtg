#!/usr/bin/env python3
"""Insert 3 new IP top-level branches (B1 城市与地域, B2 组织机构, B3 电竞)
into the cleaned taxonomy tree, reordering the 18 existing branches to 21.

Only the `IP 分类标签` children block (lines after the `IP 分类标签` header)
is replaced; the rest of the file stays byte-identical.

Preview by default (prints unified diff); pass --write to apply.
"""
import re
import sys

PATH = "V2融合世界标签体系_清洗版.txt"
NODE_RE = re.compile(r'^((?:[│  ]{4})*)([├└]── (.*))$')


def parse_block(lines):
    """Parse a block of tree lines into a list of top-level node dicts."""
    nodes = []
    stack = []  # (depth, node)
    for line in lines:
        m = NODE_RE.match(line)
        if not m:
            continue
        prefix, _, name = m.groups()
        depth = len(prefix) // 4
        node = {"name": name, "children": []}
        while stack and stack[-1][0] >= depth:
            stack.pop()
        if stack:
            stack[-1][1]["children"].append(node)
        else:
            nodes.append(node)
        stack.append((depth, node))
    return nodes


def branch(name, *subs):
    node = {"name": name, "children": []}
    for s in subs:
        if isinstance(s, tuple):
            sname, ssubs = s
            node["children"].append(
                {"name": sname, "children": [{"name": x, "children": []} for x in ssubs]})
        else:
            node["children"].append({"name": s, "children": []})
    return node


def render(nodes, prefix):
    """prefix = string of 4-char groups (space or │) leading up to this level."""
    out = []
    for i, n in enumerate(nodes):
        last = (i == len(nodes) - 1)
        conn = "└── " if last else "├── "
        out.append(prefix + conn + n["name"])
        child_prefix = prefix + ("    " if last else "│   ")
        if n["children"]:
            out.extend(render(n["children"], child_prefix))
    return out


def build_new_branches():
    b1 = branch("城市与地域 IP",
                ("城市品牌 IP", ["城市形象 IP", "城市标语口号 IP", "城市文旅 IP"]),
                ("地域文化 IP", ["地域民俗 IP", "地域物产 IP", "方言文化 IP"]),
                ("国家文化符号 IP", ["国家形象 IP", "国家象征 IP"]),
                ("城市与地域类别—传播范围",
                 ["城市与地域类别（全国传播）", "城市与地域类别（区域传播）",
                  "城市与地域类别（国际传播）"]))
    b2 = branch("组织机构 IP",
                ("国际组织 IP", ["国际治理组织 IP", "国际专业组织 IP", "国际体育组织 IP"]),
                ("公益组织 IP", ["国际公益组织 IP", "基金会 IP", "非政府组织（NGO）IP"]),
                ("行业协会 IP", ["标准与行业组织 IP", "专业学会 IP"]),
                ("体育组织 IP", ["体育联盟 IP", "体育俱乐部 IP", "赛事运营组织 IP"]),
                ("文化机构 IP", ["博物馆 IP", "美术馆 IP", "图书馆 IP"]),
                ("组织机构类别—组织范围",
                 ["组织机构类别（全国级）", "组织机构类别（国际级）",
                  "组织机构类别（地方级）"]))
    b3 = branch("电竞 IP",
                ("电竞赛事 IP", ["国际电竞赛事 IP", "职业联赛 IP", "杯赛 IP"]),
                ("电竞战队 IP", ["职业战队 IP", "俱乐部 IP"]),
                ("电竞明星 IP", ["职业选手 IP", "电竞主播 IP"]),
                ("电竞内容 IP", ["电竞直播 IP", "电竞综艺 IP"]),
                ("电竞类别—传播范围",
                 ["电竞类别（全国传播）", "电竞类别（区域传播）",
                  "电竞类别（国际传播）"]))
    return {"城市与地域 IP": b1, "组织机构 IP": b2, "电竞 IP": b3}


# Final top-level order (21 branches)
ORDER = [
    "内容作品 IP", "艺术与文物 IP", "非遗与传统手工艺 IP", "品牌 IP",
    "组织机构 IP", "美食 IP", "地标 IP", "城市与地域 IP",
    "历史与文化遗产 IP", "自然生态与动物 IP", "武器 IP", "著名载具 IP",
    "真人与人物 IP", "教育与科普 IP", "虚构角色 IP", "吉祥物与形象 IP",
    "赛事 IP", "电竞 IP", "潮玩互动 IP", "乐园节庆 IP", "科技与数字 IP",
]


def main():
    lines = open(PATH, encoding="utf-8").read().splitlines()
    # locate IP 分类标签 header line
    header = next(i for i, l in enumerate(lines) if l.strip().endswith("IP 分类标签"))
    # children block = lines after header up to EOF (IP is last top section)
    block = parse_block(lines[header + 1:])
    old_by_name = {n["name"]: n for n in block}
    new_by_name = build_new_branches()

    missing = [n for n in ORDER if n not in old_by_name and n not in new_by_name]
    if missing:
        raise SystemExit(f"ORDER references unknown branch: {missing}")
    dup = [n for n in old_by_name if n in new_by_name]
    if dup:
        raise SystemExit(f"collision between existing and new: {dup}")

    ordered = [new_by_name.get(n) or old_by_name[n] for n in ORDER]
    new_block = render(ordered, "    ")  # base prefix = 4 spaces (under IP header)

    before = lines[:header + 1]
    after_lines = lines[header + 1:]
    # compute diff for preview
    import difflib
    diff = difflib.unified_diff(after_lines, new_block, lineterm="",
                                fromfile="before", tofile="after")
    if "--write" not in sys.argv:
        sys.stdout.write("\n".join(diff) + "\n")
        print(f"\n[preview] {len(block)} existing branches -> {len(ordered)} branches "
              f"(+3: 城市与地域/组织机构/电竞). Re-run with --write to apply.")
        return

    lines[header + 1:] = new_block
    with open(PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"written. {len(ordered)} top-level IP branches (was {len(block)}).")


if __name__ == "__main__":
    main()
