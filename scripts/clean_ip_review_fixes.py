#!/usr/bin/env python3
"""Review-fix pass on the IP section (见对话 review 结论).

Applies fixes for review items #3, #4, #5, #6, #7.
(#1 老字号 left as-is per user; #2 facet-spec left for user decision; #8 is doc-only.)

Operations
  #3. Delete contradictory brand-scope facet subtrees under 跨品类品牌类别:
      无国界/全球/国际 品牌类别 each carry 传播范围(全国/区域/国际) -> self-contradictory.
      Remove the three `—传播范围` facet nodes (9 nodes).
  #4. Prune clearly machine-translation-derived deep nodes in 真人与人物 IP:
      政府官员: 推事(+首席大法官), 教会职员(+长者+长老会长老), 获任命者, 裁判, 裁判员
      历史古人>政治家: 煽动家(+英国工党成员)
      科学文化人物>教育家: 指导, 示范者, 舞蹈教师, 阅读教师, 男校长
      科学文化人物>科学家>...>经济专家: 微观经济专家(+计量经济学家)
  #5. Delete over-granular leaf 武器 IP>军火>发射器>巴祖卡 (single model, inconsistent depth).
  #6. Rename facet 人工智能主体—生存状态 -> 人工智能主体—运行状态.
  #7. Merge 新兴物种 IP (only 基因编辑物种) under 科技与数字 IP>前沿科技>生物科技,
      then drop the now-empty 新兴物种 IP branch.

Usage:
    python3 scripts/clean_ip_review_fixes.py [--write]
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

    # ---- #3. contradictory brand-scope facets ----
    for scope in ("无国界", "全球", "国际"):
        p = ["IP 分类标签", "品牌 IP", "跨品类品牌类别",
             f"{scope}品牌类别", f"{scope}品牌类别—传播范围"]
        n = find(ip_root, p)
        if n is None:
            print(f"[warn] #3 not found: {'>'.join(p)}", file=sys.stderr)
            continue
        cnt = sum(1 for _ in walk_all(n))
        detach(n)
        log.append(f"#3: deleted {p[-1]} ({cnt} nodes)")

    # ---- #4. prune MT-derived deep nodes ----
    del_paths = [
        # 政府官员
        ["IP 分类标签", "真人与人物 IP", "政治与社会人物", "政府官员", "推事"],
        ["IP 分类标签", "真人与人物 IP", "政治与社会人物", "政府官员", "教会职员"],
        ["IP 分类标签", "真人与人物 IP", "政治与社会人物", "政府官员", "获任命者"],
        ["IP 分类标签", "真人与人物 IP", "政治与社会人物", "政府官员", "裁判"],
        ["IP 分类标签", "真人与人物 IP", "政治与社会人物", "政府官员", "裁判员"],
        # 历史古人 > 政治家
        ["IP 分类标签", "真人与人物 IP", "历史古人", "政治家", "煽动家"],
        # 科学文化人物 > 教育家 > 教师
        ["IP 分类标签", "真人与人物 IP", "科学文化人物", "教育家", "教师", "指导"],
        ["IP 分类标签", "真人与人物 IP", "科学文化人物", "教育家", "教师", "示范者"],
        ["IP 分类标签", "真人与人物 IP", "科学文化人物", "教育家", "教师", "舞蹈教师"],
        ["IP 分类标签", "真人与人物 IP", "科学文化人物", "教育家", "教师", "阅读教师"],
        ["IP 分类标签", "真人与人物 IP", "科学文化人物", "教育家", "男校长"],
    ]
    for p in del_paths:
        n = find(ip_root, p)
        if n is None:
            print(f"[warn] #4 not found: {'>'.join(p)}", file=sys.stderr)
            continue
        cnt = sum(1 for _ in walk_all(n))
        detach(n)
        log.append(f"#4: deleted {'>'.join(p[1:])} ({cnt} nodes)")

    # ---- #5. over-granular 巴祖卡 ----
    p = ["IP 分类标签", "武器 IP", "军火", "发射器", "巴祖卡"]
    n = find(ip_root, p)
    if n is None:
        print(f"[warn] #5 not found: {'>'.join(p)}", file=sys.stderr)
    else:
        detach(n)
        log.append(f"#5: deleted {'>'.join(p[1:])} (1 node)")

    # ---- #6. rename AI facet ----
    p = ["IP 分类标签", "科技与数字 IP", "人工智能", "人工智能主体", "人工智能主体—生存状态"]
    n = find(ip_root, p)
    if n is None:
        print(f"[warn] #6 not found: {'>'.join(p)}", file=sys.stderr)
    else:
        n["name"] = "人工智能主体—运行状态"
        log.append(f"#6: renamed {'>'.join(p[1:])} -> 人工智能主体—运行状态")

    # ---- #7. merge 新兴物种 IP into 生物科技 ----
    gene = find(ip_root, ["IP 分类标签", "新兴物种 IP", "基因编辑物种"])
    bio = find(ip_root, ["IP 分类标签", "科技与数字 IP", "前沿科技", "生物科技"])
    emerging = find(ip_root, ["IP 分类标签", "新兴物种 IP"])
    if gene is None or bio is None or emerging is None:
        print("[warn] #7 missing node for merge", file=sys.stderr)
    else:
        cnt = sum(1 for _ in walk_all(gene))
        detach(gene)
        attach(bio, gene)
        detach(emerging)  # now empty
        log.append(f"#7: moved 基因编辑物种 (+{cnt-1} descendants) under 前沿科技>生物科技; "
                   f"dropped empty 新兴物种 IP branch")

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
        print(f"\n[preview only] run with --write to apply")


if __name__ == "__main__":
    main()
