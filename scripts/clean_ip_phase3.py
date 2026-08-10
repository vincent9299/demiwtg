#!/usr/bin/env python3
"""Phase 3 IP section cleanup: structural fixes per review.

Operations:
  A. Delete `历史古人/艺术家` subtree (MT residue, 27 grandchildren).
  B. Dedupe `海洋生物IP类别` (delete the copy under 新兴物种类别;
     keep `海洋生物 IP` under 自然生态与动物 IP).
  C. Move `虚拟偶像` from `真人与人物 IP` to `虚构角色 IP`
     (appended after `志怪鬼怪形象`).
  D. Split `新兴物种类别` by content:
     - `人工智能主体` → `科技与数字 IP/人工智能 IP`
     - `再生人` / `幽灵` / `神` → `虚构角色 IP/志怪鬼怪形象`
     - `星系体` / `宇宙级别存在` → delete
     - rename `新兴物种类别` → `新兴物种 IP` (only `基因编辑物种` left)
  E. Flatten parent-child same-name wrappers:
     - `武器 IP/武器` → children promoted to `武器 IP`
     - `著名载具 IP/著名交通载具 IP` → children promoted to `著名载具 IP`

Usage:
    python3 scripts/clean_ip_phase3.py [--write]
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

    # ---- A. Delete 历史古人/艺术家 subtree (MT residue) ----
    artist = find(ip_root, ["IP 分类标签", "真人与人物 IP", "历史古人", "艺术家"])
    if artist is None:
        print("[warn] 历史古人/艺术家 not found", file=sys.stderr)
    else:
        n = sum(1 for _ in walk_all(artist))
        detach(artist)
        log.append(f"A: deleted 历史古人/艺术家 subtree ({n} nodes)")

    # ---- B. Dedupe 海洋生物IP类别 ----
    sea_dup = find(ip_root, ["IP 分类标签", "新兴物种类别", "海洋生物IP类别"])
    if sea_dup is None:
        print("[warn] 新兴物种类别/海洋生物IP类别 not found", file=sys.stderr)
    else:
        n = sum(1 for _ in walk_all(sea_dup))
        detach(sea_dup)
        log.append(f"B: deleted 新兴物种类别/海洋生物IP类别 ({n} nodes; kept 海洋生物 IP under 自然生态)")

    # ---- C. Move 虚拟偶像 → 虚构角色 IP (after 志怪鬼怪形象) ----
    vvirtual = find(ip_root, ["IP 分类标签", "真人与人物 IP", "虚拟偶像"])
    fiction = find(ip_root, ["IP 分类标签", "虚构角色 IP"])
    if vvirtual is None or fiction is None:
        print("[warn] 虚拟偶像 or 虚构角色 IP not found", file=sys.stderr)
    else:
        detach(vvirtual)
        attach(fiction, vvirtual)  # appended as last child
        log.append("C: moved 虚拟偶像 真人与人物 IP → 虚构角色 IP (last child)")

    # ---- D. Split 新兴物种类别 by content ----
    emerging = find(ip_root, ["IP 分类标签", "新兴物种类别"])
    if emerging is None:
        print("[warn] 新兴物种类别 not found", file=sys.stderr)
    else:
        # D1. 人工智能主体 → 科技与数字 IP/人工智能 IP
        ai_subj = find(ip_root, ["IP 分类标签", "新兴物种类别", "人工智能主体"])
        ai_parent = find(ip_root, ["IP 分类标签", "科技与数字 IP", "人工智能 IP"])
        if ai_subj and ai_parent:
            detach(ai_subj)
            attach(ai_parent, ai_subj)
            log.append("D1: moved 人工智能主体 → 科技与数字 IP/人工智能 IP")
        else:
            print("[warn] 人工智能主体 or 人工智能 IP not found", file=sys.stderr)

        # D2. 再生人 / 幽灵 / 神 → 虚构角色 IP/志怪鬼怪形象
        monster = find(ip_root, ["IP 分类标签", "虚构角色 IP", "志怪鬼怪形象"])
        if monster is None:
            print("[warn] 志怪鬼怪形象 not found", file=sys.stderr)
        else:
            for nm in ("再生人", "幽灵", "神"):
                node = find(ip_root, ["IP 分类标签", "新兴物种类别", nm])
                if node is None:
                    print(f"[warn] {nm} not found in 新兴物种类别", file=sys.stderr)
                    continue
                detach(node)
                attach(monster, node)
                log.append(f"D2: moved {nm} → 虚构角色 IP/志怪鬼怪形象")

        # D3. 星系体 / 宇宙级别存在 → delete
        for nm in ("星系体", "宇宙级别存在"):
            node = find(ip_root, ["IP 分类标签", "新兴物种类别", nm])
            if node is None:
                print(f"[warn] {nm} not found", file=sys.stderr)
                continue
            n = sum(1 for _ in walk_all(node))
            detach(node)
            log.append(f"D3: deleted 新兴物种类别/{nm} ({n} nodes)")

        # D4. rename 新兴物种类别 → 新兴物种 IP
        emerging["name"] = "新兴物种 IP"
        log.append("D4: renamed 新兴物种类别 → 新兴物种 IP")

    # ---- E. Flatten parent-child same-name wrappers ----
    # E1. 武器 IP/武器: promote children of 武器 to 武器 IP
    weapon_ip = find(ip_root, ["IP 分类标签", "武器 IP"])
    if weapon_ip is None:
        print("[warn] 武器 IP not found", file=sys.stderr)
    else:
        # find the single child named 武器
        weapon_wrap = next((c for c in weapon_ip["children"] if c["name"] == "武器"), None)
        if weapon_wrap is None:
            print("[warn] 武器 IP/武器 wrapper not found", file=sys.stderr)
        else:
            # promote weapon_wrap's children to be children of weapon_ip (in place)
            idx = weapon_ip["children"].index(weapon_wrap)
            grandchildren = list(weapon_wrap["children"])
            for gc in grandchildren:
                gc["parent"] = weapon_ip
            weapon_ip["children"] = (
                weapon_ip["children"][:idx] + grandchildren + weapon_ip["children"][idx + 1:]
            )
            weapon_wrap["children"] = []
            weapon_wrap["parent"] = None
            log.append("E1: flattened 武器 IP/武器 (children promoted)")

    # E2. 著名载具 IP/著名交通载具 IP: promote children
    vehicle_ip = find(ip_root, ["IP 分类标签", "著名载具 IP"])
    if vehicle_ip is None:
        print("[warn] 著名载具 IP not found", file=sys.stderr)
    else:
        vehicle_wrap = next(
            (c for c in vehicle_ip["children"] if c["name"] == "著名交通载具 IP"), None
        )
        if vehicle_wrap is None:
            print("[warn] 著名载具 IP/著名交通载具 IP wrapper not found", file=sys.stderr)
        else:
            idx = vehicle_ip["children"].index(vehicle_wrap)
            grandchildren = list(vehicle_wrap["children"])
            for gc in grandchildren:
                gc["parent"] = vehicle_ip
            vehicle_ip["children"] = (
                vehicle_ip["children"][:idx] + grandchildren + vehicle_ip["children"][idx + 1:]
            )
            vehicle_wrap["children"] = []
            vehicle_wrap["parent"] = None
            log.append("E2: flattened 著名载具 IP/著名交通载具 IP (children promoted)")

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
