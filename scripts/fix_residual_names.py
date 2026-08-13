#!/usr/bin/env python3
"""清洗残留裁决 · 可疑命名批量修复（2026-08-10 第三批次）

对 `清洗后_残留可疑命名.csv` 的 71 项执行裁决：
- 量词/数字节点 16 → 改名 14 / 删除 1 / 保留 1
- 形容词散落 55 → 改名词 26 / 删除 27（含 4 项补漏）
- 特例 2 → 机动的→机动车；汽车的删除；防水（台布下）→防水材质，外套下保留

用法: python3 scripts/fix_residual_names.py
"""
import re

SRC = "data/V2融合世界标签体系_清洗版.txt"
BAK = "data/V2融合世界标签体系_清洗版.txt.bak3"

# (名称, 父节点) → 新名称；父节点用于消歧（可爱的/防水的 出现多次）
RENAME_SPEC = [
    # 量词/数字节点
    ("一把夹钳", "人造物体", "夹钳"),
    ("一块布", "台布", "布料"),
    ("一把剪刀", "复式杠杆", "剪子"),
    ("一把手钳", "复式杠杆", "手钳"),
    ("一块地板", "板", "地板材料"),
    ("一把钳子", "手工工具", "手用钳子"),
    ("一件", "游戏器材", "棋子"),
    ("二十二", "手枪", ".22口径"),
    ("一双", "聚会", "夫妇"),
    ("一条面包", "面包", "整条面包"),
    ("七十八", "圆盘", "78转唱片"),
    ("一对", "二", "成对"),
    ("一个星期", "一段时间", "星期"),
    ("一块土地", "知识与学科", "土地块"),
    # 形容词 → 名词
    ("专科院校的", "教育家", "学术人员"),
    ("中空的", "凹陷地", "中空地形"),
    ("乡村风格的", "平民", "乡下居民"),
    ("具龙骨突的", "鸟类", "龙骨鸟类"),
    ("印欧语系的", "自然语言", "印欧语系"),
    ("日耳曼的", "印欧语系的", "日耳曼语族"),
    ("国家的", "人物与人体", "国民"),
    ("地理学的", "知识与学科", "地理学"),
    ("家用的", "仆人", "家仆"),
    ("寒冷的", "辣椒", "辣味辣椒"),
    ("尼格罗人种的", "人物与人体", "尼格罗人种"),
    ("干燥的", "改革者", "禁酒主义者"),
    ("无家可归的", "不幸的人", "无家可归者"),
    ("无线的", "电信", "无线"),
    ("无肩带的", "女服", "无肩带女服"),
    ("有保护能力的", "化合物", "防护性化合物"),
    ("有光泽的", "杂志", "光面杂志"),
    ("有胎盘的", "哺乳动物", "有胎盘哺乳动物"),
    ("机动的", "交通工具", "机动车"),
    ("武装的", "人物与人体", "武装人员"),
    ("浪漫的", "理想主义者", "浪漫主义者"),
    ("特别的", "一道菜", "特色菜"),
    ("脆的", "糖制食品", "脆糖"),
    ("苦味的", "艾尔啤酒", "苦啤酒"),
    ("赤道的", "望远镜", "赤道仪"),
    ("政治的", "文化艺术与媒介", "政治"),
    ("防水的", "台布", "防水材质"),
    # 新发现（regenerate_residual_csvs.py 检出，2026-08-10 追加）
    ("一套衣服", "衣服", "套装"),
    ("一餐", "食物", "餐食"),
    ("一餐", "食品", "餐食"),
    ("一餐", "关头", "共餐"),
    ("一套", "收藏品集合", "套件"),
]

# (名称, 父节点) 待删除
DELETE_SPEC = [
    ("一块地", "一块土地"),
    ("僵硬的", "自然景观"),
    ("古代的", "老人"),
    ("善于交际的", "聚会"),
    ("基础的", "商品"),
    ("多雨的", "自然景观"),
    ("室外的", "场景"),
    ("常绿的", "植物"),
    ("干的", "材料与物质"),
    ("弯曲的", "捐赠基金"),
    ("忧郁的", "小鸡"),
    ("扁平的", "上层鳉鱼"),
    ("晴朗的", "自然景观"),
    ("暴风雨的", "自然景观"),
    ("有纹理的", "文字与信息图形"),
    ("有限的", "公共运输"),
    ("死亡的", "人物与人体"),
    ("破损的", "交通工具"),
    ("自然的", "成功人士"),
    ("花的", "植物"),
    ("金属质的", "台布"),
    ("锋利的", "材料与物质"),
    ("黑白杂色的", "台布"),
    ("灰白的", "知识与学科"),
    ("可爱的", "摄影模特"),
    ("新娘的", "婚礼"),
    ("现代的", "人物与人体"),
    ("汽车的", "交通工具"),
]

def main():
    with open(SRC, encoding="utf-8") as f:
        lines = f.read().splitlines()

    NODE_RE = re.compile(r'^((?:[│  ]{4})*)([├└]── (.*))$')
    rec = {}
    for i, line in enumerate(lines, start=1):
        m = NODE_RE.match(line)
        if not m:
            continue
        prefix, _, name = m.groups()
        rec[i] = (len(prefix) // 4 + 1, name)

    def parent_name(ln):
        d = rec[ln][0]
        if d == 1:
            return None
        for j in range(ln - 1, 0, -1):
            if j in rec and rec[j][0] == d - 1:
                return rec[j][1]
        return None

    def resolve(spec, desc):
        """(name, parent) -> lineno; must be exactly 1 per (name, parent) pair."""
        out = {}
        for name, pname in spec:
            hits = [ln for ln, (d, n) in rec.items() if n == name and parent_name(ln) == pname]
            if len(hits) != 1:
                print(f"!! {desc} {name!r}(父 {pname!r}): 命中 {len(hits)} 处 -> {hits}")
                raise SystemExit(1)
            out[(name, pname)] = hits[0]
        return out

    rename_lines = resolve([(n, p) for n, p, _ in RENAME_SPEC], "改名")
    delete_lines = resolve(DELETE_SPEC, "删除")
    assert not (set(rename_lines.values()) & set(delete_lines.values())), "改名/删除行重叠"

    renames = {rename_lines[(n, p)]: new for n, p, new in RENAME_SPEC}
    deletes = set(delete_lines.values())

    # apply
    new = []
    for i, line in enumerate(lines, start=1):
        if i in deletes:
            continue
        new_name = renames.get(i)
        if new_name:
            m = NODE_RE.match(line)
            if m:
                marker = m.group(2)[:4]
                line = m.group(1) + marker + new_name
            else:
                print(f"!! L{i} 改名失败: {line!r}")
                raise SystemExit(1)
        new.append(line)

    with open(BAK, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    with open(SRC, "w", encoding="utf-8") as f:
        f.write("\n".join(new) + "\n")

    print(f"改名 {len(renames)} 处，删除 {len(deletes)} 行，行数 {len(lines)} → {len(new)}")
    print(f"备份: {BAK}")

if __name__ == "__main__":
    main()
