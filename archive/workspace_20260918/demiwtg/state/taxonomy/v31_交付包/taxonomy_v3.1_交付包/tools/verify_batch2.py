# -*- coding: utf-8 -*-
"""批 2 落盘后独立复核：重读落盘文件，全面校验。"""
import csv
from collections import defaultdict

CN = '/Users/meng/qwenwork/taxonomy_merged_progress_instances.csv'
EN = '/Users/meng/qwenwork/taxonomy_tree_instances_en.csv'
GEN = '融合世界标签体系 / 通用分类标签'
KN = GEN + ' / 知识与学科'

cn = {}
with open(CN, encoding='utf-8-sig') as f:
    rd = csv.reader(f)
    next(rd)
    for r in rd:
        if r:
            assert r[0] not in cn, f'CN 重复: {r[0]}'
            cn[r[0]] = r[1] if len(r) > 1 else ''
en = {}
with open(EN, encoding='utf-8-sig') as f:
    rd = csv.reader(f)
    next(rd)
    for r in rd:
        if r:
            assert r[0] not in en, f'EN 重复: {r[0]}'
            en[r[0]] = (r[1], r[2] if len(r) > 2 else '')

assert list(cn) == list(en), '中英路径不一致'
print('行数:', len(cn), '| 中英一致: True')

cn_i = sum(len(v.split('|')) for v in cn.values() if v.strip())
en_i = sum(len(i.split('|')) for _, i in en.values() if i.strip())
print('CN 实例:', cn_i, '| EN 实例:', en_i)
assert cn_i == 346634 and en_i == 473942, '实例数异常'

# 触碰行中英段位一致（全量重查，避开88处固有斜杠段名用触碰集合）
bad = [k for k in cn
       if k.startswith(GEN + ' / 医学与健康') or k.startswith(KN)
       or k.startswith(GEN + ' / 地理与地点')
       if len(k.split(' / ')) != len(en[k][0].split(' / '))]
print('触碰区段位不齐:', bad)

# 源路径零残留：知识与学科域下已迁出的节点不应还在
moved_out = ['地区', '土地块', '地理学', '乡下', '市郊', '广场', '中央', '景观', '海底地形',
             '海滨度假胜地', '聚居地', '人口聚居区', '邻近地区', '周边地区', '陆块', '山峰/尖顶',
             '地平线', '地址', '品牌', '家庭', '室内设计', '临时结构', '交叉口', '饮水地点',
             '停泊位', '手柄', '织物边缘', '配件', '方向定位', '清新剂', '系列', '靠窗的座位',
             '废品场', '断片', '宝石', '恶臭', '芬芳', '黏稠糊状物', '固体', '剩余物',
             '电视节目类型', '天蝎座', '独角兽', '天使', '巫婆', '精灵', '幻象', '恶灵',
             '神话生物', '超自然主义', '上层阶级', '体制', '占有物', '反对', '势力范围',
             '经济获益', '支出', '周转资产', '奖励', '投资', '所购之物', '猬科', '负子蟾',
             '鹭群繁殖地', '鳄目', '家庭菜园', 'V形种植区', '宝藏', '奇异事物', '商品名',
             '时尚', '流浪者', '乐园', '路边', '隐藏/隐蔽处', '战场遗址', '遗骸', '幸运符',
             '不祥之物', '地域文化', '少数', '滑雪芭蕾', '请求', '指令', '逆转', '后部',
             '决定因素', '反射', '名称', '项目', '有机过程', '工程', '知识', '认知因素',
             '问题解决', '属', '恶地', '热带', '外部', '低气压', '月球', '沉积', '蓝天',
             '傍晚天空', '冰架', '露水', '半沙漠', '冰柱', '旋度', '死水区']
leak = []
for m in moved_out:
    src = KN + ' / ' + m
    hits = [k for k in cn if k == src or k.startswith(src + ' / ')]
    if hits:
        leak.append((m, hits[:2]))
print('源路径残留泄漏:', leak if leak else '无')

# 目标分支就位
spots = {
    GEN + ' / 医学与健康 / 医学专科': '医学专科开张',
    GEN + ' / 地理与地点 / 行政区划与城市': '地区迁入',
    GEN + ' / 政治、法律与社会制度 / 政治体制': '多源新建',
    GEN + ' / 材料与物质 / 气味': '多源新建',
    GEN + ' / 自然景观 / 土壤层': '地平线吸收',
    GEN + ' / 品牌与产品 / 收藏品与组合': '多源新建',
    GEN + ' / 体育与游戏 / 体育运动大项': '强制空根',
}
for s, label in spots.items():
    hit = [k for k in cn if k == s or k.startswith(s + ' / ')]
    inst = sum(len(cn[k].split('|')) for k in hit if cn[k].strip())
    ok = len(hit) > 0
    print(f'  {"✓" if ok else "✗"} {label}: {s.split(" / ")[3]} {len(hit)} 行/{inst} 实例')

# 知识与学科剩余结构
l2 = defaultdict(int)
for k, v in cn.items():
    if k.startswith(KN + ' / '):
        seg = k.split(' / ')
        l2[seg[3]] += len(v.split('|')) if v.strip() else 0
print('\n知识与学科剩余 L2 及实例:')
for name in sorted(l2):
    print(f'  {name}: {l2[name]}')

# 医学与健康实例
med = [k for k in cn if k.startswith(GEN + ' / 医学与健康')]
mi = sum(len(cn[k].split('|')) for k in med if cn[k].strip())
print('\n医学与健康域实例:', mi, '(应 424)')
assert mi == 424

# 知识/认知与知识过程改名核对
assert (KN + ' / 认知与知识过程') in cn, '认知与知识过程缺失'
assert (KN + ' / 工程学') in cn, '工程学缺失'
assert (KN + ' / 研究方法') in cn, '研究方法缺失'
assert (KN + ' / 认知科学') in cn, '认知科学缺失'
assert (KN + ' / 生物分类阶元') in cn, '生物分类阶元缺失'
print('keep 改名核对: 全部就位')

print('\n=== 落盘后独立复核通过 ===')
