#!/usr/bin/env python3
"""Add 6 new top-level IP branches to the cleaned taxonomy tree.

Inserted in semantic order among existing IP top-level branches:
  P1 非遗与传统手工艺 IP   (after 艺术与文物 IP)
  P2 美食 IP               (after 品牌 IP)
  P6 历史与文化遗产 IP     (after 地标 IP)
  P3 自然生态与动物 IP     (after 历史与文化遗产 IP)
  P4 教育与科普 IP         (after 真人与人物 IP)
  P5 科技与数字 IP         (after 乐园节庆 IP, before 新兴物种类别)
"""
import re

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


def render(node, prefix="", last=True):
    branch = "└── " if last else "├── "
    line = prefix + branch + node["name"]
    child_prefix = prefix + ("    " if last else "│   ")
    lines = [line]
    for i, c in enumerate(node["children"]):
        lines.extend(render(c, child_prefix, i == len(node["children"]) - 1))
    return lines


def branch(name, *subs):
    """Build a node from a name and list of (subname, [subsubs]) tuples."""
    node = {"name": name, "children": []}
    for s in subs:
        if isinstance(s, tuple):
            sname, ssubs = s
            child = {"name": sname, "children": [{"name": x, "children": []} for x in ssubs]}
        else:
            child = {"name": s, "children": []}
        node["children"].append(child)
    return node


def build_new_branches():
    return [
        branch("非遗与传统手工艺 IP",
               ("传统技艺 IP", ["传统编织技艺", "传统印染技艺", "传统雕刻技艺",
                                 "传统造纸技艺", "传统酿造技艺", "传统金属工艺"]),
               ("民俗文化 IP", ["传统节庆民俗", "婚丧礼俗", "民间信仰民俗", "生活习俗 IP"]),
               ("曲艺与传统戏剧 IP", ["相声评书 IP", "地方曲艺 IP", "传统戏曲 IP"]),
               ("传统医药文化 IP", ["中医文化 IP", "针灸推拿文化", "药膳文化 IP"]),
               ("传统服饰文化 IP", ["汉服文化 IP", "民族服饰 IP", "传统配饰文化"]),
               ("非遗类别—传播范围", ["非遗类别（全国传播）", "非遗类别（区域传播）",
                                      "非遗类别（国际传播）"])),
        branch("美食 IP",
               ("地方菜系 IP", ["川菜文化 IP", "粤菜文化 IP", "鲁菜文化 IP",
                                 "淮扬菜文化 IP", "火锅文化 IP", "茶点文化 IP"]),
               ("地方小吃 IP", ["地域名小吃 IP", "市井美食 IP"]),
               ("美食地标 IP", ["美食街区 IP", "老字号 IP", "美食夜市 IP"]),
               ("饮食文化 IP", ["茶文化 IP", "酒文化 IP", "咖啡文化 IP", "节令饮食 IP"]),
               ("美食节事 IP", ["美食节 IP", "饮食文化节 IP"]),
               ("美食类别—呈现载体", ["美食类别（互动数字内容）", "美食类别（视听内容）",
                                      "美食类别（静态图像）"])),
        branch("历史与文化遗产 IP",
               ("朝代与历史时期 IP", ["先秦文化 IP", "汉唐文化 IP", "宋元文化 IP",
                                       "明清文化 IP"]),
               ("历史事件 IP", ["重大历史事件 IP", "文明交流事件 IP"]),
               ("文化带与线路 IP", ["丝绸之路 IP", "大运河文化 IP", "茶马古道 IP",
                                     "长征文化线路 IP"]),
               ("考古文明 IP", ["史前文明 IP", "古城遗址 IP", "陵寝文化 IP",
                                 "出土文物文化 IP"]),
               ("历史文化类别—传播范围", ["历史文化类别（全国传播）", "历史文化类别（区域传播）",
                                          "历史文化类别（国际传播）"])),
        branch("自然生态与动物 IP",
               ("珍稀动物 IP", ["国宝动物 IP", "海洋生物 IP", "濒危物种 IP",
                                 "明星动物个体 IP"]),
               ("自然景观 IP", ["国家公园 IP", "自然保护区 IP", "地质奇观 IP", "森林生态 IP"]),
               ("湿地与水生态 IP", ["湖泊生态 IP", "湿地 IP", "江河水系 IP"]),
               ("野生动物保护 IP", ["保护地 IP", "生态科普 IP"]),
               ("自然生态类别—传播范围", ["自然生态类别（全国传播）", "自然生态类别（区域传播）",
                                           "自然生态类别（国际传播）"])),
        branch("教育与科普 IP",
               ("科普内容 IP", ["科普视频 IP", "科普图书 IP", "科学实验 IP", "科普场馆 IP"]),
               ("教育机构 IP", ["高等教育机构 IP", "基础教育机构 IP", "职业教育机构 IP",
                                 "国际教育机构 IP"]),
               ("研学体验 IP", ["研学营地 IP", "科技馆体验 IP", "博物馆研学 IP"]),
               ("教育内容品牌", ["在线教育品牌", "素质教育品牌", "教育出版品牌"]),
               ("教育科普类别—呈现载体", ["教育科普类别（互动数字内容）", "教育科普类别（视听内容）",
                                           "教育科普类别（静态图像）"])),
        branch("科技与数字 IP",
               ("航天科技 IP", ["航天工程 IP", "深空探测 IP", "运载火箭 IP", "航天员 IP"]),
               ("互联网产品 IP", ["国民应用 IP", "数字平台 IP", "开源生态 IP"]),
               ("人工智能 IP", ["AI 助手 IP", "大模型 IP", "智能硬件 IP"]),
               ("前沿科技 IP", ["生物科技 IP", "量子科技 IP", "新能源汽车科技 IP", "机器人 IP"]),
               ("科技数字类别—传播范围", ["科技数字类别（全国传播）", "科技数字类别（区域传播）",
                                           "科技数字类别（国际传播）"])),
    ]


def main():
    ip_root, lines, start = parse()

    names = [c["name"] for c in ip_root["children"]]
    anchor = {"艺术与文物 IP": 0, "品牌 IP": 1, "地标 IP": 2,
              "真人与人物 IP": 3, "乐园节庆 IP": 4}
    for n in names:
        for a in list(anchor):
            if a == n:
                del anchor[a]

    new_roots = build_new_branches()
    new_by_name = {b["name"]: b for b in new_roots}
    old_by_name = {c["name"]: c for c in ip_root["children"]}

    ORDER = [
        "内容作品 IP",
        "艺术与文物 IP",
        "非遗与传统手工艺 IP",
        "品牌 IP",
        "美食 IP",
        "地标 IP",
        "历史与文化遗产 IP",
        "自然生态与动物 IP",
        "武器 IP",
        "著名载具 IP",
        "真人与人物 IP",
        "教育与科普 IP",
        "虚构角色 IP",
        "吉祥物与形象 IP",
        "赛事 IP",
        "潮玩互动 IP",
        "乐园节庆 IP",
        "科技与数字 IP",
        "新兴物种类别",
    ]
    ip_root["children"] = []
    for name in ORDER:
        node = new_by_name.get(name) or old_by_name.get(name)
        if node is None:
            raise SystemExit(f"missing node in order: {name}")
        node["parent"] = ip_root
        ip_root["children"].append(node)

    lines[start:] = render(ip_root)
    with open(PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print("written. new top-level order:")
    for i, c in enumerate(ip_root["children"]):
        print(f"  {i + 1}. {c['name']}")


if __name__ == "__main__":
    main()
