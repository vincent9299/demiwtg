#!/usr/bin/env python3
"""重新生成清洗残留 CSV（反映清洗版当前状态）。

输出：
- 清洗后_剩余同名标签.csv   标签名出现>=2次的节点
- 清洗后_残留可疑命名.csv   量词/数字节点 + 散落实体类目的 …的 形容词

用法: python3 scripts/regenerate_residual_csvs.py
"""
import re
from collections import Counter, defaultdict

NODE_RE = re.compile(r'^((?:[│  ]{4})*)([├└]── (.*))$')

SRC = "V2融合世界标签体系_清洗版.txt"
lines = open(SRC, encoding="utf-8").read().splitlines()

rec = {}
for i, line in enumerate(lines, start=1):
    m = NODE_RE.match(line)
    if not m:
        continue
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

def path_str(ln):
    parts = []
    cur = ln
    while cur is not None:
        parts.append(rec[cur][1])
        cur = parent_of(cur)
    return "/".join(reversed(parts))

# ---- 1) 剩余同名 ----
by_name = defaultdict(list)
for ln, (d, n) in rec.items():
    by_name[n].append(ln)

rows = []
for n, lns in sorted(by_name.items(), key=lambda kv: (-len(kv[1]), kv[0])):
    if len(lns) < 2:
        continue
    locs = " || ".join(f"{ln}:{path_str(ln)}" for ln in lns)
    rows.append((n, len(lns), locs))

with open("清洗后_剩余同名标签.csv", "w", encoding="utf-8") as f:
    f.write("标签名,出现次数,位置(行号:路径)\n")
    for n, c, locs in rows:
        f.write(f"{n},{c},{locs}\n")
print(f"剩余同名标签: {len(rows)} 个")

# ---- 2) 残留可疑命名 ----
sus = []  # (类型, 名称, 行号)
for ln, (d, n) in rec.items():
    p = path_str(ln)
    # 量词/数字：以量词开头且处于实体类目（属性与状态之外）
    if n in {"二十二", "七十八", "一件", "一双", "一对", "一个星期"}:
        continue  # 已裁决处理
    if re.match(r'^(一[把块双对条件个个餐套阵支枝根片面颗粒][的]?)', n):
        if not p.startswith("通用分类标签/属性与状态"):
            sus.append(("量词/数字节点", n, ln))
    # 形容词：以 的 结尾且散落在实体类目（属性与状态/时间数量与度量之外）
    if n.endswith("的") and len(n) <= 6:
        if not (p.startswith("通用分类标签/属性与状态")
                or p.startswith("通用分类标签/时间数量与度量")):
            sus.append(("形容词条目", n, ln))

# 去重（同名字段合并行号）
from collections import OrderedDict
merged = OrderedDict()
for typ, n, ln in sus:
    merged.setdefault((typ, n), []).append(ln)

with open("清洗后_残留可疑命名.csv", "w", encoding="utf-8") as f:
    f.write("类型,标签名,出现行号\n")
    for (typ, n), lns in merged.items():
        f.write(f"{typ},{n},{';'.join(map(str, lns))}\n")
print(f"残留可疑命名: {len(merged)} 个")
