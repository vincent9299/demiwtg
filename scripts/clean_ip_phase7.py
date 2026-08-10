#!/usr/bin/env python3
"""Phase 7: fix remaining machine-translation semantic errors (2026-08-10).

Operations (rename only; 1 promote):
  1. 葡萄 (武器/导弹) -> 葡萄弹, promoted to last child of 武器
  2. 狗鱼 (武器) -> 长矛  (pike 兵器)
  3. 吸盘 (食物/甜食/糖制食品) -> 棒棒糖  (sucker)
  4. 俱乐部 (体育器材/高尔夫器材) -> 高尔夫球杆
  5. 俱乐部 (器具/木条) -> 棍棒
  6. 不可言说之物 (衣服) -> 贴身衣物  (undergarments)
  7. 桌子 (知识与学科/列阵) -> 表格  (table=数据表)

Left as-is (user decision): 吻 (Kisses 巧克力), 列阵 (array->数组).

Usage: python3 scripts/clean_ip_phase7.py
"""
import re

SRC = "V2融合世界标签体系_清洗版.txt"
BAK = "V2融合世界标签体系_清洗版.txt.bak5"
NODE_RE = re.compile(r'^((?:[│  ]{4})*)([├└]── (.*))$')

RENAME = [
    (("通用分类标签", "人造物体", "武器", "导弹", "葡萄"), "葡萄弹", ("通用分类标签", "人造物体", "武器")),
    (("通用分类标签", "人造物体", "武器", "狗鱼"), "长矛", None),
    (("通用分类标签", "食物", "甜食", "糖制食品", "吸盘"), "棒棒糖", None),
    (("通用分类标签", "人造物体", "体育器材", "高尔夫器材", "俱乐部"), "高尔夫球杆", None),
    (("通用分类标签", "人造物体", "器具", "木条", "俱乐部"), "棍棒", None),
    (("通用分类标签", "人造物体", "商品", "消费品", "可穿戴设备", "衣服", "不可言说之物"), "贴身衣物", None),
    (("通用分类标签", "知识与学科", "列阵", "桌子"), "表格", None),
]


def main():
    with open(SRC, encoding="utf-8") as f:
        lines = f.read().splitlines()

    # build tree
    rec = {}
    for i, line in enumerate(lines, start=1):
        m = NODE_RE.match(line)
        if m:
            prefix, _, name = m.groups()
            rec[i] = (len(prefix) // 4 + 1, name)

    def parent_of(ln):
        d = rec[ln][0]
        if d == 1:
            return None
        for j in range(ln - 1, 0, -1):
            if j in rec and rec[j][0] == d - 1:
                return j
        return None

    def path_of(ln):
        parts = []
        cur = ln
        while cur is not None:
            d, n = rec[cur]
            parts.append(n)
            cur = parent_of(cur)
        return tuple(reversed(parts))

    def lineno_by_path(path):
        target_depth = len(path)
        hits = []
        for ln, (d, n) in rec.items():
            if d == target_depth and n == path[-1] and path_of(ln) == path:
                hits.append(ln)
        if len(hits) != 1:
            print(f"!! {'/'.join(path)}: 命中 {len(hits)} -> {hits}")
            raise SystemExit(1)
        return hits[0]

    moves = {}
    for path, new_name, promote_to in RENAME:
        ln = lineno_by_path(path)
        moves[ln] = (new_name, promote_to)

    promote_ln = next(ln for ln in moves if moves[ln][1] is not None)
    promote_parent = lineno_by_path(moves[promote_ln][1])
    new_depth = rec[promote_parent][0] + 1
    new_indent = "│   " * (new_depth - 1)

    weapon_kids = [ln for ln in rec if rec[ln][0] == rec[promote_parent][0] + 1
                   and parent_of(ln) == promote_parent]
    last_weapon_kid = max(weapon_kids)

    missle_kids = [ln for ln in rec if parent_of(ln) == parent_of(promote_ln)]

    # apply renames + drop 葡萄 line
    new = []
    for i, line in enumerate(lines, start=1):
        if i == promote_ln:
            continue
        if i in moves:
            new_name, _ = moves[i]
            m = NODE_RE.match(line)
            new.append(m.group(1) + m.group(2)[:4] + new_name)
        else:
            new.append(line)

    # fix markers: previous sibling of 葡萄 becomes └── (if any)
    prev_kid = max((k for k in missle_kids if k < promote_ln), default=None)
    if prev_kid is not None:
        m = NODE_RE.match(new[prev_kid - 1])
        new[prev_kid - 1] = m.group(1) + "└── " + m.group(3)

    # old last weapon kid becomes ├── ; insert 葡萄弹 as └── right after weapon subtree
    m = NODE_RE.match(new[last_weapon_kid - 1])
    new[last_weapon_kid - 1] = m.group(1) + "├── " + m.group(3)
    weapon_end = last_weapon_kid - 1  # 0-based last line of weapon subtree
    for j in range(last_weapon_kid, len(new) + 1):
        mm = NODE_RE.match(new[j - 1])
        if mm and (len(mm.group(1)) // 4 + 1) <= rec[promote_parent][0]:
            break
        weapon_end = j
    new.insert(weapon_end, new_indent + "└── 葡萄弹")

    with open(BAK, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    with open(SRC, "w", encoding="utf-8") as f:
        f.write("\n".join(new) + "\n")
    print(f"改名 {len(moves)} 处，迁移 1 处（葡萄弹 → 武器 末位子节点）")
    print(f"备份: {BAK}")


if __name__ == "__main__":
    main()
