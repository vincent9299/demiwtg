"""重放已复核的固定批次判断，默认导出两批合并视觉版；不调用模型。"""
from pathlib import Path
from collections import Counter
import argparse
import csv
import hashlib
import json
import re

BASE = Path(__file__).resolve().parent
SOURCE = BASE / 'inputs' / 'original-table.txt'
SOURCE_SHA256 = '39c43ea8c2e5e3bf1c08f826d0183756a4a9e652cef545f4a973f470f5799d33'
rows = json.loads((BASE / 'source-concepts.json').read_text())
by_id = {r['id']: r for r in rows}
routes = {}


def assign(visual, knowledge, ids):
    """Each route is three organizational levels, not three taxonomic ranks."""
    v, k = visual.split('/'), knowledge.split('/')
    assert len(v) == len(k) == 3, (visual, knowledge)
    routes[visual] = k
    for rid in ids.split():
        assert rid in by_id, rid
        assert 'visual' not in by_id[rid], ('duplicate', rid)
        by_id[rid].update(visual=v, knowledge=k.copy(), tags=[], extra_entries=[], note='', review='', granularity='')


# People and anatomy. Depictions of anatomy are assigned separately from body structures.
assign('人物与人体/人物身份/音乐演奏者', '艺术与传播/音乐/演奏者', '1-004 2-010')
assign('人物与人体/人体结构/骨骼', '医学与健康/人体解剖/骨骼系统', '2-096 3-165')
assign('人物与人体/人体结构/脑与神经结构', '医学与健康/人体解剖/神经系统', '3-162 3-164 3-200')

# Animals. A biological subject remains primary when a name adds an age, action or habitat.
assign('动物/哺乳动物/猫科动物', '生命科学/动物学/哺乳动物', '1-014 2-168 2-209')
assign('动物/哺乳动物/灵长类动物', '生命科学/动物学/哺乳动物', '2-220 3-044 3-102')
assign('动物/哺乳动物/犬类', '生命科学/动物学/哺乳动物', '2-208')
assign('动物/哺乳动物/马类', '生命科学/动物学/哺乳动物', '3-059')
assign('动物/鸟类/鸡雉类', '生命科学/鸟类学/鸡雉类', '3-014 3-126 3-138 3-144 3-176 3-178 3-208')
assign('动物/鸟类/鹳类', '生命科学/鸟类学/鹳类', '3-072 3-101 3-113 3-193 3-203')
assign('动物/鸟类/鹤类', '生命科学/鸟类学/鹤类', '3-134 3-214')
assign('动物/鸟类/鹭类', '生命科学/鸟类学/鹭类', '3-013')
assign('动物/鸟类/鸭雁类', '生命科学/鸟类学/鸭雁类', '3-043 3-061 3-108 3-132 3-136 3-181 3-197')
assign('动物/鸟类/企鹅类', '生命科学/鸟类学/企鹅类', '3-040 3-074 3-095 3-140')
assign('动物/鸟类/翠鸟类', '生命科学/鸟类学/翠鸟类', '3-006 3-096 3-150')
assign('动物/鸟类/犀鸟与地犀鸟', '生命科学/鸟类学/犀鸟与地犀鸟', '3-027 3-045 3-198 3-213')
assign('动物/鸟类/啄木鸟与拟啄木鸟', '生命科学/鸟类学/啄木鸟与拟啄木鸟', '3-055 3-090 3-148 3-149')
assign('动物/鸟类/雀形目鸟类', '生命科学/鸟类学/雀形目鸟类', '3-019 3-046 3-077')
assign('动物/鸟类/鹦鹉类', '生命科学/鸟类学/鹦鹉类', '3-037')
assign('动物/鸟类/鸻鹬类', '生命科学/鸟类学/鸻鹬类', '3-005 3-022 3-092')
assign('动物/鸟类/鸬鹚类', '生命科学/鸟类学/鸬鹚类', '3-017')
assign('动物/鸟类/猛禽', '生命科学/鸟类学/猛禽', '3-179')
assign('动物/鸟类/鸸鹋与食火鸡', '生命科学/鸟类学/鸸鹋与食火鸡', '3-079 3-210')
assign('动物/爬行动物/鳄类', '生命科学/动物学/爬行动物', '3-002 3-080 3-093 3-103 3-137')
assign('动物/爬行动物/蛇类', '生命科学/动物学/爬行动物', '3-010')
assign('动物/爬行动物/蜥蜴类', '生命科学/动物学/爬行动物', '3-107 3-116 3-173')
assign('动物/鱼类/海水鱼类', '生命科学/鱼类学/海水鱼类', '2-014 2-135 3-152')
assign('动物/鱼类/淡水鱼类', '生命科学/鱼类学/淡水鱼类', '3-034 3-105 3-133 3-143 3-145 3-171')
assign('动物/节肢动物/甲虫类', '生命科学/昆虫学/甲虫类', '1-008 2-205 3-025 3-081')
assign('动物/节肢动物/蝶蛾及其幼虫', '生命科学/昆虫学/蝶蛾类', '3-051 3-098 3-153')
assign('动物/节肢动物/蛛形动物', '生命科学/动物学/蛛形动物', '2-216 3-003')
assign('动物/节肢动物/甲壳动物', '生命科学/动物学/甲壳动物', '3-131')
assign('动物/其他无脊椎动物/刺胞动物', '生命科学/动物学/刺胞动物', '2-161')
assign('动物/其他无脊椎动物/软体动物', '生命科学/动物学/软体动物', '3-085 3-091')
assign('动物/其他无脊椎动物/棘皮动物', '生命科学/动物学/棘皮动物', '3-073 3-174')
assign('动物/古生物及其遗存/恐龙及其遗存', '生命科学/古生物学/恐龙', '3-053 3-058 3-190')
assign('动物/古生物及其遗存/翼龙及其遗存', '生命科学/古生物学/翼龙', '3-038 3-155 3-158 3-201')
assign('动物/古生物及其遗存/史前哺乳动物', '生命科学/古生物学/史前哺乳动物', '3-112 3-128')
assign('动物/动物活动遗迹/巢穴', '生命科学/动物行为/筑巢与繁殖', '3-154')

# Plants: navigation by observable form and familiar cultivation form, not a formal taxonomic tree.
assign('植物/木本植物/果树', '农业与园艺/作物栽培/果树作物', '2-128 2-143 2-154 2-178 2-224 3-050')
assign('植物/木本植物/针叶树', '农业与园艺/林业/经济林木', '3-202')
assign('植物/草本与藤本植物/蔬菜与豆类植物', '农业与园艺/作物栽培/蔬菜与豆类作物', '1-001 1-020 1-023 2-147 2-175 2-177 2-184 2-185 2-218')
assign('植物/草本与藤本植物/禾谷植物', '农业与园艺/作物栽培/谷物作物', '3-035 3-064')
assign('植物/草本与藤本植物/观花与观叶草本', '农业与园艺/观赏园艺/草本花卉', '1-013 3-015 3-020 3-021 3-029 3-031 3-120')
assign('植物/草本与藤本植物/芳香与药用草本', '农业与园艺/特色植物/药用与芳香植物', '3-063 3-166 3-205 3-212')
assign('植物/草本与藤本植物/水生与湿地植物', '生命科学/植物学/水生与湿地植物', '2-145 2-212 3-030 3-070 3-122')
assign('植物/草本与藤本植物/食虫植物', '生命科学/植物学/食虫植物', '3-169')
assign('植物/多肉与肉质植物/叶多肉植物', '农业与园艺/观赏园艺/多肉植物', '1-003 3-012 3-160 3-172')
assign('植物/多肉与肉质植物/茎与地下器官肉质植物', '农业与园艺/观赏园艺/多肉与球根植物', '2-109 3-199')
assign('植物/植物部位/根与茎', '生命科学/植物形态/根与茎', '1-022 1-024 2-153 3-068')
assign('植物/植物部位/叶', '生命科学/植物形态/叶', '3-109')
assign('植物/植物部位/花与花序', '生命科学/植物形态/花与花序', '3-071 3-083 3-127 3-167 3-196')
assign('植物/植物部位/球果种子与附属组织', '生命科学/植物形态/球果种子与附属组织', '2-123 2-155 2-165 2-181 3-076')

# Fungi: visible fruiting bodies and growth forms are separated from use as food/medicine.
assign('真菌/大型真菌/伞状真菌', '生命科学/真菌学/大型真菌', '3-041 3-135 3-211')
assign('真菌/大型真菌/齿状及珊瑚状真菌', '生命科学/真菌学/大型真菌', '3-078 3-097 3-161')
assign('真菌/大型真菌/鬼笔类真菌', '生命科学/真菌学/鬼笔类真菌', '3-065 3-110')
assign('真菌/大型真菌/胶质真菌', '生命科学/真菌学/大型真菌', '2-204')
assign('真菌/虫生真菌/虫草类真菌', '生命科学/真菌学/虫生真菌', '3-033 3-175')

# Manufactured objects: physical object families are the visual entry points.
assign('器具与设备/手工工具/扳手与套筒', '工程与技术/机械工具/紧固工具', '1-006 2-017 2-117')
assign('器具与设备/手工工具/锤斧与敲击工具', '工程与技术/机械工具/敲击与劈砍工具', '2-004 2-068 2-129 2-202')
assign('器具与设备/手工工具/挖掘工具', '农业与园艺/农林装备/园艺工具', '2-087 2-152')
assign('器具与设备/餐厨与日用器具/刀具', '食品与饮食/烹饪器具/刀具', '1-015 2-107 2-219 3-115')
assign('器具与设备/餐厨与日用器具/模具', '食品与饮食/烘焙/烘焙工具', '2-119')
assign('器具与设备/餐厨与日用器具/杯具', '食品与饮食/饮品文化/酒杯', '2-214')
assign('器具与设备/餐厨与日用器具/日用分配器', '生活与社会/日常生活/卫生用品', '2-044')
assign('器具与设备/餐厨与日用器具/炊煮器具', '食品与饮食/烹饪器具/炊具', '2-099')
assign('器具与设备/机械与作业设备/起重设备', '工程与技术/工程机械/起重与提升', '2-093 2-112 2-137 2-144 2-150')
assign('器具与设备/机械与作业设备/升降与顶升设备', '工程与技术/工程机械/升降与顶升', '2-078 2-120 2-188 3-099')
assign('器具与设备/机械与作业设备/喷雾设备', '农业与园艺/农林装备/植保设备', '2-111')
assign('器具与设备/机械与作业设备/动力设备', '工程与技术/机械工程/液压传动', '3-123')
assign('器具与设备/机械零部件/齿轮与传动件', '工程与技术/机械工程/机械传动', '2-222 3-177')
assign('器具与设备/机械零部件/密封件', '工程与技术/机械工程/密封技术', '2-101')
assign('器具与设备/机械零部件/管件', '工程与技术/机械工程/管道与连接件', '2-162')
assign('器具与设备/环境调节设备/风扇', '工程与技术/暖通与制冷/通风设备', '2-097')
assign('器具与设备/环境调节设备/空调', '工程与技术/暖通与制冷/空调系统', '2-159 2-160 3-062 3-089')
assign('器具与设备/医学与实验设备/血压测量设备', '医学与健康/临床检查/血压监测', '2-015 2-018 2-022 3-075')
assign('器具与设备/医学与实验设备/体温测量设备', '医学与健康/临床检查/体温监测', '2-051 2-057 3-049')
assign('器具与设备/医学与实验设备/X射线与CT设备', '医学与健康/医学影像/X射线与CT', '2-012 2-158 3-206')
assign('器具与设备/医学与实验设备/超声设备', '医学与健康/医学影像/超声诊断', '2-055 2-067 3-182')
assign('器具与设备/医学与实验设备/雾化治疗设备', '医学与健康/治疗技术/雾化治疗', '2-032')
assign('器具与设备/医学与实验设备/显微镜', '生命科学/实验技术/显微成像', '3-067 3-069 3-170')
assign('器具与设备/医学与实验设备/移液与分液设备', '生命科学/实验技术/液体处理', '2-079 3-186')
assign('器具与设备/康复辅助器具/假肢', '医学与健康/康复医学/假肢与矫形', '2-073 3-042')
assign('器具与设备/康复辅助器具/助行与训练器具', '医学与健康/康复医学/步行与运动康复', '2-020 2-108')
assign('器具与设备/摄影器材/胶片相机', '艺术与传播/摄影/摄影器材', '2-058 3-032 3-104')
assign('器具与设备/家具/座椅', '生活与社会/日常生活/家具', '2-082 2-113')
assign('器具与设备/体育器材/球桌', '体育与休闲/球类运动/台球与乒乓球', '2-005 2-007 2-049')
assign('器具与设备/体育器材/冰鞋', '体育与休闲/冰雪运动/冰上运动装备', '2-021 2-046')
assign('器具与设备/体育器材/力量训练器械', '体育与休闲/健身训练/力量训练', '2-059 2-083 2-091')
assign('器具与设备/体育器材/投掷与击剑器材', '体育与休闲/竞技体育/田径与击剑', '2-127 3-168')
assign('器具与设备/乐器/吹管乐器', '艺术与传播/音乐/吹管乐器', '2-053 2-085 3-117')
assign('器具与设备/乐器/打击乐器', '艺术与传播/音乐/打击乐器', '1-019 2-126')
assign('器具与设备/乐器/弹拨与拉弦乐器', '艺术与传播/音乐/弦乐器', '3-100 3-129 3-187')
assign('器具与设备/乐器/乐器部件', '艺术与传播/音乐/乐器构造', '2-086')
assign('器具与设备/武器与防护装备/枪械', '军事与防务/武器装备/轻武器', '2-034 3-001 3-026 3-111')
assign('器具与设备/武器与防护装备/弓弩与投掷武器', '军事与防务/武器装备/冷兵器', '2-037 2-077 3-056 3-151')
assign('器具与设备/武器与防护装备/护甲', '军事与防务/武器装备/个人防护装备', '3-060')

# Transport distinguishes whole vehicles from components, without manufacturing empty vehicle classes.
assign('交通工具/航空器/固定翼飞机', '交通与航天/航空/飞机机型', '3-004 3-007 3-009 3-028 3-086 3-087 3-184')
assign('交通工具/航空器/直升机', '交通与航天/航空/直升机机型', '3-142')
assign('交通工具/航天器/运载火箭', '交通与航天/航天/发射运载系统', '3-159 3-188 3-194')
assign('交通工具/水上交通工具/帆船', '交通与航天/航海/帆船', '3-008 3-024 3-052 3-054 3-088')
assign('交通工具/水上交通工具/货船', '交通与航天/航海/货物运输', '3-114')
assign('交通工具/水上交通工具/军舰与潜艇', '军事与防务/海军装备/军舰与潜艇', '3-066 3-094 3-185')
assign('交通工具/水上交通工具/龙舟', '体育与休闲/水上运动/龙舟', '2-225')
assign('交通工具/陆地车辆/装甲战斗车辆', '军事与防务/陆军装备/装甲车辆', '3-048 3-057 3-106')
assign('交通工具/交通工具部件/汽车部件', '交通与航天/汽车工程/制动系统', '2-072')
assign('交通工具/交通工具部件/机翼与机体部件', '交通与航天/航空工程/机体结构', '2-095 2-194 3-082 3-121 3-156 3-157')

# Buildings: in the visual edition named sites join generic structures of the same kind.
assign('建筑与设施/桥梁/悬索桥', '建筑与土木/桥梁工程/悬索桥', '1-025 2-054 2-066 2-122 2-146 2-201')
assign('建筑与设施/桥梁/拱桥', '建筑与土木/桥梁工程/拱桥', '2-011 2-048 2-052 2-080 2-106 3-192')
assign('建筑与设施/桥梁/桁架桥', '建筑与土木/桥梁工程/桁架桥', '2-196 2-200 3-180')
assign('建筑与设施/桥梁/梁桥', '建筑与土木/桥梁工程/梁桥', '2-139')
assign('建筑与设施/建筑结构/拱与扶壁', '建筑与土木/建筑构造/拱与扶壁', '2-027 2-038 2-115')
assign('建筑与设施/建筑结构/梁与拉索', '建筑与土木/结构工程/承重构件', '2-118 3-141')
assign('建筑与设施/宗教建筑/教堂', '历史与文化/宗教文化/基督宗教建筑', '2-006 2-060 2-090 2-094')
assign('建筑与设施/宗教建筑/清真寺', '历史与文化/宗教文化/伊斯兰建筑', '2-019 2-023')
assign('建筑与设施/宗教建筑/寺庙与神庙', '历史与文化/宗教文化/寺庙与神庙', '2-003 2-040 2-103 2-167')
assign('建筑与设施/建筑与建筑群/宫殿', '历史与文化/建筑遗产/宫殿', '2-116 2-211')
assign('建筑与设施/建筑与建筑群/塔', '历史与文化/建筑遗产/塔', '2-029 2-069 2-193')
assign('建筑与设施/建筑与建筑群/陵墓', '历史与文化/建筑遗产/陵墓', '2-030 2-062 2-104')
assign('建筑与设施/建筑与建筑群/园林', '历史与文化/建筑遗产/园林', '2-198')
assign('建筑与设施/建筑与建筑群/仓储建筑群', '历史与文化/建筑遗产/工业与商业建筑', '2-047')
assign('建筑与设施/遗址/建筑与聚落遗址', '历史与文化/考古遗址/建筑与聚落遗址', '2-088 2-089 2-197 2-213')
assign('建筑与设施/交通与水工设施/道路', '交通与航天/道路交通/道路网络', '2-009')
assign('建筑与设施/交通与水工设施/防波堤', '建筑与土木/水工工程/港口工程', '2-138')

# Clothes and hairstyle are separated from a person's identity.
assign('服饰与造型/服装/传统与民族服装', '历史与文化/服饰文化/传统与民族服饰', '2-064 2-140 2-190 2-221')
assign('服饰与造型/服装/学位礼服', '生活与社会/教育/学位与毕业仪式', '2-100 3-139')
assign('服饰与造型/服装/表演服装', '艺术与传播/表演艺术/舞台服装', '2-110')
assign('服饰与造型/发型/盘发与发髻', '生活与社会/美容造型/发型', '2-008 2-045')
assign('服饰与造型/发型/编发', '生活与社会/美容造型/发型', '2-166 2-179 2-195')
assign('服饰与造型/佩戴饰品/冠饰', '历史与文化/考古与文物/礼仪与权力象征', '2-156')

# Food categories concern preparation and form; biological ingredient links are facets.
assign('食物与食材/糕点与甜品/月饼', '食品与饮食/糕点与甜品/月饼', '1-021 2-031 2-063 2-121 2-133 2-186')
assign('食物与食材/糕点与甜品/酥点与起酥糕点', '食品与饮食/烘焙/酥点与起酥糕点', '2-013 2-035 2-174 2-187')
assign('食物与食材/糕点与甜品/泡芙', '食品与饮食/烘焙/泡芙', '2-041 2-206')
assign('食物与食材/米面食品/汤圆', '食品与饮食/米面食品/汤圆', '2-131 2-176')
assign('食物与食材/米面食品/糕团', '食品与饮食/米面食品/糕团', '2-163 2-173')
assign('食物与食材/米面食品/烧饼', '食品与饮食/米面食品/烧饼', '2-164 2-189')
assign('食物与食材/米面食品/面条与意面', '食品与饮食/米面食品/面条与意面', '2-056 2-098 2-169')
assign('食物与食材/米面食品/披萨', '食品与饮食/米面食品/披萨', '2-192')
assign('食物与食材/菜肴/酿蔬菜与叶包菜肴', '食品与饮食/烹饪技法/包裹与填馅', '2-016 2-081 2-148 2-183 2-191')
assign('食物与食材/菜肴/肉冻与冷肉菜肴', '食品与饮食/烹饪技法/肉冻与冷肉制作', '2-039 2-042 3-016')
assign('食物与食材/菜肴/蛋类组合菜肴', '食品与饮食/菜肴/班尼迪克蛋与相关菜肴', '2-092 2-130 2-141 2-182')
assign('食物与食材/菜肴/刺身', '食品与饮食/烹饪技法/刺身', '2-199 3-189 3-209')
assign('食物与食材/植物性食材/坚果仁与种仁', '食品与饮食/食材/坚果与种仁', '1-009 2-203')

# Natural entities are separate from diagrams, iconic locations and source-country labels.
assign('自然环境与物质/地形与地貌/喀斯特地貌', '地球与环境/地质地貌/喀斯特', '2-024 2-025 2-026 2-105')
assign('自然环境与物质/地形与地貌/海底地貌', '地球与环境/海洋科学/海底地貌', '2-134')
assign('自然环境与物质/水文与海洋现象/瀑布', '地球与环境/地质地貌/钙华地貌', '3-191')
assign('自然环境与物质/水文与海洋现象/水面波浪', '地球与环境/流体与海洋现象/船行波', '2-172')
assign('自然环境与物质/水文与海洋现象/海底冷泉', '地球与环境/海洋科学/冷泉生态', '3-124')
assign('自然环境与物质/天气现象/热带气旋', '地球与环境/气象学/热带气旋', '2-084 3-084')
assign('自然环境与物质/矿物与宝石/玛瑙', '地球与环境/矿物与宝石/玛瑙', '2-001 2-002')
assign('自然环境与物质/矿物与宝石/切割钻石', '工程与技术/宝石加工/钻石切工', '2-070 2-215')

# Signs, diagrams and artwork are visual representations, not their represented entities.
assign('图像与标识/公共标志/警告标志', '生活与社会/公共安全/警告标志', '1-010 1-011 2-132')
assign('图像与标识/公共标志/禁止标志', '生活与社会/公共规则/禁止标志', '1-017 1-018 2-033 2-157')
assign('图像与标识/公共标志/消防标志', '生活与社会/公共安全/消防标志', '2-136')
assign('图像与标识/道路标线/路面标记', '交通与航天/道路交通/交通标线', '1-016 2-028 2-043 2-071 2-171')
assign('图像与标识/科学与信息图示/分类与关系图', '数学与自然科学/数学与逻辑/分类与集合表示', '1-007 1-012')
assign('图像与标识/科学与信息图示/化学结构图与模型', '数学与自然科学/化学/分子结构表示', '2-076 2-149 3-047 3-183')
assign('图像与标识/科学与信息图示/生物分子结构图', '生命科学/分子生物学/生物大分子结构', '3-011 3-036')
assign('图像与标识/科学与信息图示/人体解剖图', '医学与健康/人体解剖/解剖图谱', '3-018 3-039')
assign('图像与标识/科学与信息图示/细胞与组织图', '生命科学/细胞与组织/结构图示', '3-119 3-130 3-146')
assign('微观生物结构/细胞结构/细胞与细胞骨架', '生命科学/细胞与组织/细胞结构', '3-118 3-147')
assign('图像与标识/艺术图像/绘画作品', '艺术与传播/视觉艺术/绘画', '2-050')
assign('图像与标识/艺术图像/摄影作品', '艺术与传播/摄影/摄影作品', '2-065 2-170')
assign('图像与标识/文化图像/生肖形象', '历史与文化/民俗文化/生肖', '1-002')
assign('图像与标识/文化图像/虚构生物形象', '艺术与传播/影视与虚构作品/虚构生物', '3-195')

# Decorative objects and material artworks.
assign('艺术与装饰品/工艺品/剪纸', '历史与文化/传统工艺/剪纸', '2-036 2-074 2-075')
assign('艺术与装饰品/工艺品/结绳工艺品', '历史与文化/传统工艺/中国结', '2-061')
assign('艺术与装饰品/工艺品/灯彩', '历史与文化/民俗文化/节庆灯彩', '2-223')
assign('艺术与装饰品/雕塑与塑像/人物塑像', '艺术与传播/视觉艺术/雕塑', '2-151 2-207')
assign('艺术与装饰品/花艺/花束', '农业与园艺/观赏园艺/花艺', '2-125')
assign('艺术与装饰品/文物集合/出土文物集合', '历史与文化/考古与文物/出土文物', '3-204')

# Scenes and actions are only primary when there is no more specific object subject.
assign('活动与场景/互动动作/击掌', '生活与社会/人际交往/手势与互动', '1-005')
assign('活动与场景/体育活动/水上运动', '体育与休闲/水上运动/风筝冲浪', '2-210')
assign('活动与场景/体育活动/滑雪', '体育与休闲/冰雪运动/滑雪', '2-217')
assign('活动与场景/医疗操作/手术', '医学与健康/手术治疗/手术操作', '3-023 3-163 3-207')
assign('活动与场景/民俗活动/祭灶', '历史与文化/民俗文化/节日习俗', '2-102')
assign('活动与场景/农业与园艺场景/农田与种植', '农业与园艺/农业生产/作物种植', '2-114 2-142')
assign('活动与场景/农业与园艺场景/温室', '农业与园艺/设施园艺/温室', '2-124')
assign('活动与场景/水域与植被场景/荷塘', '农业与园艺/景观园艺/水景植物配置', '2-180')
assign('抽象概念/能源/潮汐能', '工程与技术/能源工程/海洋可再生能源', '3-125')


def override(rid, knowledge=None, tags=(), entries=(), note=None, review=None, granularity=None):
    r = by_id[rid]
    if knowledge:
        parts = knowledge.split('/')
        assert len(parts) == 3
        r['knowledge'] = parts
    r['tags'].extend(tags)
    r['extra_entries'].extend(e.split('/') for e in entries)
    if note is not None:
        r['note'] = note
    if review is not None:
        r['review'] = review
    if granularity is not None:
        r['granularity'] = granularity


# The knowledge edition chooses a domain owner and adds independent retrieval entrances.
override('2-099', '历史与文化/考古与文物/青铜器', tags=['青铜器', '炊具'], entries=['食品与饮食/烹饪器具/炊具'], note='以器物类型做视觉归类，以文物身份做知识归类。')
override('2-151', '历史与文化/宗教文化/佛教造像', tags=['陶瓷', '观音'], entries=['艺术与传播/视觉艺术/雕塑'])
override('2-156', entries=['生活与社会/服饰饰品/冠饰'], tags=['皇冠', '历史文物'])
override('1-002', tags=['生肖', '文化符号'], note='文化符号集合，不按真实动物物种分类。')
override('3-195', tags=['虚构生物', '影视'], note='影视虚构形象，不进入真实翼龙类群。')

for rid in '3-004 3-009 3-086 3-087 3-142 3-184'.split():
    override(rid, '军事与防务/空军装备/军用航空器', entries=['交通与航天/航空/飞机机型'], tags=['军用'])
override('3-142', note='知识库按军事用途主归类；航空结构入口应为直升机。')
by_id['3-142']['extra_entries'] = [['交通与航天', '航空', '直升机机型']]
for rid in '3-007 3-028'.split():
    override(rid, '交通与航天/航空运输/货运航空器', entries=['交通与航天/航空/飞机机型'], tags=['货运'])
override('3-114', entries=['食品与饮食/食品供应链/冷链运输'], tags=['冷藏运输'])
override('2-225', entries=['历史与文化/民俗文化/端午习俗'], tags=['龙舟'])

named_bridges = {'2-011':'中国桥梁', '2-048':'海外桥梁', '2-054':'中国桥梁', '2-080':'中国桥梁', '2-106':'海外桥梁', '2-139':'海外桥梁'}
for rid, region in named_bridges.items():
    old = '/'.join(by_id[rid]['knowledge'])
    override(rid, '建筑与土木/桥梁案例/' + region, entries=[old], tags=['具名桥梁'], granularity='具名实体')

for rid in '1-021 2-031 2-063 2-121 2-133 2-186'.split():
    override(rid, entries=['历史与文化/饮食民俗/中秋饮食'], tags=['月饼'])
for rid in '2-131 2-176'.split():
    override(rid, entries=['历史与文化/饮食民俗/节日食品'], tags=['汤圆'])
for rid in '2-199 3-189 3-209'.split():
    override(rid, entries=['食品与饮食/食材/鱼类食材'], tags=['鱼类食材'], note='已加工菜肴，不按活体动物主分类。')
override('1-009', tags=['种仁', '食材'])
override('2-203', entries=['生命科学/植物形态/种子'], tags=['银杏', '食材'], note='按常见食材语境归类；保留“银杏果”原名，不把俗称解释为植物学果实。')
override('2-204', entries=['食品与饮食/食材/食用菌'], tags=['食用菌'], note='原名未指定干制或烹调状态，视觉主归真菌。')
override('3-097', entries=['食品与饮食/食材/食用菌'], tags=['食用菌'])

override('1-022', '农业与园艺/作物与产品/蔬菜产品', entries=['生命科学/植物形态/根与茎'], tags=['茭白', '膨大茎'], note='“茭白”通常指食用膨大茎；不将其当作独立植物物种名。')
override('1-024', '农业与园艺/作物与产品/蔬菜产品', entries=['生命科学/植物形态/花与花序'], tags=['蒜', '花薹'], note='按可见长茎形态归类；植物学上涉及花薹，不是独立物种。')
override('2-153', '农业与园艺/作物与产品/蔬菜产品', entries=['生命科学/植物形态/根与茎'], tags=['石刁柏', '嫩茎'])
override('3-109', tags=['萝卜', '叶'], entries=['农业与园艺/作物栽培/蔬菜与豆类作物'])
override('3-167', tags=['芥蓝', '花'], entries=['农业与园艺/作物栽培/蔬菜与豆类作物'])
override('3-083', tags=['可可', '花'], entries=['农业与园艺/作物栽培/经济作物'])
override('3-127', tags=['烟草', '花序'], entries=['农业与园艺/作物栽培/经济作物'])
override('3-068', tags=['兜状荷包牡丹', '根'], entries=['农业与园艺/观赏园艺/草本花卉'])
override('3-071', tags=['冠花贝母', '花'], entries=['农业与园艺/观赏园艺/草本花卉'])
override('3-196', tags=['雨久花', '花序'], entries=['生命科学/植物学/水生与湿地植物'])
for rid in '2-155 2-165 3-076'.split():
    override(rid, tags=['假种皮'], note='植物部位概念，物种信息应与“假种皮”这一部位分开保存。')
override('2-181', tags=['荷花', '种子'])
override('2-123', tags=['球果'])

override('2-114', tags=['抱子甘蓝', '农田'], entries=['农业与园艺/作物栽培/蔬菜与豆类作物'])
override('2-142', tags=['烟草', '种植'], entries=['农业与园艺/作物栽培/经济作物'])
override('2-124', tags=['极乐鸟植物', '温室'], entries=['农业与园艺/观赏园艺/草本花卉'], note='按温室场景主归类；植物名称作为主题。')
override('2-125', tags=['极乐鸟植物', '花束'], entries=['农业与园艺/观赏园艺/草本花卉'])
override('2-180', tags=['荷花', '池塘'], entries=['生命科学/植物学/水生与湿地植物'])
override('1-014', tags=['狮', '雌性', '无鬃'], granularity='生物名称＋性别与外观')
override('2-168', tags=['美洲豹', '雨林'], entries=['地球与环境/生态系统/热带雨林'], granularity='生物名称＋生境', review='“美洲豹雨林”可能以动物或雨林场景为主体；目前按美洲豹为主体。')
for rid, organism in [('2-209','雪豹'), ('3-210','鸸鹋'), ('3-214','黑颈鹤')]:
    override(rid, tags=[organism, '幼体'], entries=['生命科学/发育生物学/幼体与生长'], granularity='生物名称＋年龄阶段')
override('3-148', tags=['绒啄木鸟', '觅食'], entries=['生命科学/动物行为/觅食行为'], granularity='生物名称＋行为')
override('3-179', tags=['角雕', '展翅'], entries=['生命科学/动物行为/运动行为'], granularity='生物名称＋行为')
override('3-051', tags=['蛾类幼虫', '进食'], entries=['生命科学/动物行为/觅食行为'], granularity='生物名称＋阶段与行为', review='需核对英文俗名对应的物种；不把“幼虫”另建成与昆虫并列的动物大类。')
override('3-154', tags=['翠鸟类', '巢穴'], granularity='动物活动遗迹')
override('3-061', '农业与园艺/动物饲养/家禽品种', entries=['生命科学/鸟类学/鸭雁类'], tags=['家鹅', '白色'], granularity='家养品种／品系＋颜色')
override('3-077', '农业与园艺/动物饲养/观赏鸟品种', entries=['生命科学/鸟类学/雀形目鸟类'], tags=['金丝雀', '卷羽品种'], granularity='家养品种')
override('2-208', '农业与园艺/动物饲养/犬品种', entries=['生命科学/动物学/哺乳动物'], tags=['犬', '雪纳瑞'], granularity='家养品种')
override('3-059', '农业与园艺/动物饲养/马品种', entries=['生命科学/动物学/哺乳动物'], tags=['马', '埃克斯穆尔'], granularity='家养品种', review='原名含“丘陵”修饰，需确认是品种描述还是场景描述。')

override('3-011', '生命科学/遗传学/DNA结构', entries=['生命科学/分子生物学/生物大分子结构'], tags=['DNA', '结构图'])
override('2-149', tags=['甲烷', '结构式'])
override('3-183', tags=['路易斯结构', '结构式'])
override('3-018', entries=['艺术与传播/信息设计/科学插图'], tags=['人体解剖', '图示'])
override('3-039', tags=['胸廓', '解剖图'], review='暂按解剖图归类；若素材为真实骨骼标本，应改用人体结构入口。')
override('3-118', granularity='微观生物结构', review='名称未明确显微照片、示意图或模型；当前归入微观结构，不强行认定为图示。')
override('3-147', granularity='微观生物结构', review='名称未明确显微图像或示意图；不能仅凭名称确定呈现形式。')
override('3-036', granularity='结构概念／图示', review='原名未指定具体蛋白质或呈现形式；当前保留在分子结构图示入口。')
override('3-125', granularity='抽象概念', review='潮汐能可由海面、设备或示意图表现；没有唯一可见对象，需图像级呈现标签。')

for rid, label in {'3-023':'眼科手术', '3-163':'脊柱手术', '3-207':'骨科手术'}.items():
    override(rid, '医学与健康/手术治疗/' + label)
override('3-067', entries=['地球与环境/地质分析/偏光显微观察'], tags=['偏光显微镜'], note='仪器可跨生命科学与地质材料领域使用；主归实验成像，增加地质入口。')
override('3-069', tags=['共聚焦显微镜'])
override('3-170', tags=['荧光显微镜'])
override('2-158', tags=['C臂', 'X射线', '移动式'])
override('3-182', tags=['超声', '骨密度'])
override('3-206', tags=['X射线', '骨密度'])

# Name granularity is orthogonal to the tree. These labels do not claim a complete taxonomy audit.
for rid in '1-003 3-003 3-012 3-013 3-021 3-025 3-029 3-030 3-031 3-033 3-041 3-050'.split():
    override(rid, granularity='种级学名（名称粒度）')
override('1-001', granularity='种下变种名（名称粒度）', note='保留 var. 原名；未据此修改为当前接受名。')
override('3-015', granularity='栽培品种名', tags=['Rubra'], note='单引号部分为栽培品种名称；它不是一个新增物种。')
override('3-120', granularity='栽培类型／外观描述', tags=['橘红色'], review='需确认是正式栽培品种名，还是对冠花贝母花色的描述。')
override('3-064', granularity='作物类型', tags=['大麦', '二棱'], note='描述大麦的穗型／栽培类型，不当作新增物种。')
for rid, detail in {
    '1-008':'科级类群：天牛科', '2-205':'科级类群：锹甲科', '3-081':'科级类群：叩甲科',
    '3-098':'科级类群：弄蝶科', '2-135':'属级类群：海马属', '3-160':'属级类群：肉锥花属',
    '3-038':'属级类群：喙嘴翼龙属', '3-112':'属级类群：板齿犀属', '3-190':'属级类群：钉状龙属',
    '3-201':'属级类群：风神翼龙属', '2-161':'纲级类群：立方水母类',
    '2-216':'目级类群：避日目', '3-131':'高阶类群：甲壳动物',
    '2-014':'多物种类群通名', '2-145':'属级通名：王莲类', '3-022':'多物种类群通名',
    '3-034':'属级或多物种通名', '3-040':'包含多个物种的通名', '3-043':'通名，需学名定种',
    '3-010':'包含多个物种的通名', '3-128':'类群通名，范围需核对',
}.items():
    override(rid, granularity=detail, note='原名保留；上层分类细化不等于该概念已经精确到物种。')
override('3-131', note='保留“甲壳纲”原词；树使用中性的“甲壳动物”，不固定有争议或历史沿用的分类等级。')
for rid in '3-053 3-058 3-158'.split():
    override(rid, granularity='古生物类群＋标本或遗存', tags=['标本或遗存'])
override('3-155', granularity='属名或泛称（待确认）', review='“翼手龙”可能指翼手龙属，也可能被宽泛地用于翼龙；不强行落到种。')

# Ambiguity is recorded without renaming, deleting or silently splitting source concepts.
for rid, reason in {
    '2-004':'“Axe”与“木棒”的含义不一致，暂归锤斧与敲击工具。',
    '2-162':'“管子”可能是管件，也可能是中国吹管乐器；暂按管件，需原图或原始定义确认。',
    '2-170':'“胜利之吻”可能指摄影作品或据此制作的雕塑；暂按摄影作品。',
    '2-109':'“弹簧草”在园艺语境可指不同植物；保留通名，不指定物种。',
    '3-020':'英文俗名 Indian Turnip 需用学名确认，当前仅作草本植物导航。',
    '3-107':'“斐济鬣蜥”不足以保证唯一物种，需学名或原图辅助。',
    '3-116':'“栉尾鬣蜥”可能是属级泛称或具体种的简称，需补学名确认。',
    '3-135':'“白毒伞”俗名可能对应多种白色鹅膏，不能仅凭中文名定种。',
    '3-161':'“肉齿菌”名称范围需核对；暂归齿状及珊瑚状真菌。',
    '3-204':'“马王堆出土文物”是跨器物类型的集合，无法在不改概念的前提下归为单一器形。',
}.items():
    override(rid, review=reason)
override('2-115', granularity='建筑构件', note='原概念字符串自身含斜杠，但整体作为一个概念保留，不把它拆成四个节点。')
override('2-049', review='按 Table Tennis 球台语境暂归球桌；保留原文 Tennis Table。')
override('2-147', tags=['瑞士甜菜'], note='视觉与农业用途按蔬菜归类，不沿用原表可能出现的十字花科关联。')
override('2-139', review='暂按伦敦现桥及悬臂箱梁结构归类；需确认素材不是旧桥。Historic England 的档案图注与名录存在结构表述差别。', note='箱梁依据见 references.json；知识库保留具名桥梁入口。')
override('2-146', review='“玻璃吊桥”以悬索桥暂归类；若“吊桥”仅是景区俗称，需由原图核对承重结构。')

# Follow-up audit: a short common name can also be a standard species name.
override('2-145', granularity='属级通名或种名（需确认原定义）', review='王莲常作 Victoria 属植物统称，也有资料用作特定种的中文名；需确认原概念的学名。', note='不能仅凭名称断言一定是属级标签。')
override('3-022', granularity='英文通名（可指具体种）', review='Lapwing 在 RSPB 名录中指 Vanellus vanellus，也可泛指麦鸡类；需原始定义定范围。', note='修正前版“一定是多物种类群”的判定。')
override('3-043', granularity='英文通名（可指具体种）', review='Shelduck 在 RSPB 名录中指 Tadorna tadorna，也可作麻鸭类通名；需原始定义定范围。', note='不直接计入明确科目或属级类群。')
override('3-034', granularity='英文通名（物种范围需确认）', review='Pike 可泛指狗鱼类，也常是具体狗鱼物种的简称；没有原始定义时不直接判为属级。', note='补充学名即可保持原概念名称不变。')
override('2-212', granularity='种级中文名（按植物志）', note='《中国植物志》香蒲对应 Typha orientalis；不可因为还有香蒲属就把本条自动判为属级。')

# Explicit alias relationships are navigational only; the source records remain separate.
alias_groups = [
    {'relation':'中英文同义', 'ids':['1-001','1-020'], 'note':'芥蓝；不合并原记录，不处理学名接受状态。'},
    {'relation':'中英文同义', 'ids':['2-031','2-121'], 'note':'Mooncake／月饼。'},
    {'relation':'同义表达', 'ids':['2-082','2-113'], 'note':'可折叠座椅／折叠椅。'},
    {'relation':'近义或上下位', 'ids':['2-028','2-043'], 'note':'车道标线与道路标记的范围不一定完全相等，不能自动合并。'},
    {'relation':'近义或上下位', 'ids':['2-027','2-038'], 'note':'Lancet arch 是更具体的尖拱形式，不标记为完全同义。'},
    {'relation':'上位与具体构型', 'ids':['2-008','2-045'], 'note':'Bun updo 与 sock bun 保留各自范围。'},
    {'relation':'同一测量任务，规格可能不同', 'ids':['2-015','2-018','2-022'], 'note':'三种血压计标签并非必然等价。'},
]


def complete_metadata():
    for r in rows:
        assert 'visual' in r, r
        if not r['granularity']:
            if r['visual'][0] in ['动物', '植物', '真菌']:
                r['granularity'] = '生物名称'
                if r['visual'][1] == '植物部位':
                    r['granularity'] = '植物部位'
            elif r['visual'][0] == '活动与场景':
                r['granularity'] = '动作或场景'
            elif r['visual'][0] == '图像与标识':
                r['granularity'] = '图像、标志或结构表示'
            else:
                r['granularity'] = '对象类型或专名'
        r['tags'] = list(dict.fromkeys(r['tags']))
        r['extra_entries'] = [list(p) for p in dict.fromkeys(tuple(p) for p in r['extra_entries'])]
        assert all(len(p) == 3 for p in [r['visual'], r['knowledge']] + r['extra_entries'])
        assert r['knowledge'] not in r['extra_entries'], r['id']


def path_text(parts):
    return ' / '.join(parts)


def write_csv(name, headers, records):
    # UTF-8 with BOM allows direct import in common Chinese spreadsheet applications.
    with (BASE / name).open('w', encoding='utf-8-sig', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(headers)
        writer.writerows(records)


def build_tree(kind):
    tree = {}
    for r in rows:
        a, b, c = r[kind]
        tree.setdefault(a, {}).setdefault(b, {}).setdefault(c, []).append(r['id'])
    return tree


def export_tree(kind, stem, title):
    tree = build_tree(kind)
    (BASE / (stem + '.json')).write_text(json.dumps(tree, ensure_ascii=False, indent=2) + '\n')
    audit_file = BASE / 'leaf-audit.json'
    audit = {a['概念ID']: a for a in json.loads(audit_file.read_text())} if kind == 'visual' and audit_file.exists() else {}
    lines = [f'# {title}', '', '当前采用视觉版。每条原概念只在主树中出现一次；保留原名及概念ID。' if kind == 'visual' else '每条原概念只在主树中出现一次；保留原名及概念ID。', '',
             '以下是三层导航类目，其下挂载原始概念。类目深度不等于生物分类等级。', '']
    if kind == 'visual':
        lines += ['按主体对象、部件或场景导航；这里的“视觉”指视觉概念标注，不保证仅凭外观就能辨认型号或物种。', '']
        if audit:
            pending = sum(a['核查结论']=='名称有歧义，待确认' for a in audit.values())
            lines += ['## 末层标注说明', '',
                      f"- **【未到种·层级】**：{len(audit)-pending}条，名称仍停留在科、目、纲、属或宽泛类群，包括类群加标本形式。",
                      f'- **【待确认】**：{pending}条，名称可能是类群通称，也可能指具体物种，需要原定义或学名确认。',
                      '- **归属待核对**：原有的分类或呈现形式疑问，不等同于未到物种。',
                      '- 品种、部位、幼体和行为描述不自动算作科目；未标注也不代表已完成物种核验。', '',
                      '[集中查看这31条概念及依据](06-视觉版末层类群与歧义清单.md)', '']
    else:
        lines += ['按知识领域和检索主题导航；扩展入口保存在 CSV 与 concept-records.json 中，不在此重复计数。', '']
    for a, btree in tree.items():
        count = sum(len(ids) for ct in btree.values() for ids in ct.values())
        lines += [f'## {a}（{count}）', '']
        for b, ctree in btree.items():
            lines += [f'### {b}', '']
            for c, ids in ctree.items():
                lines += [f'- **{c}**（{len(ids)}）']
                for rid in ids:
                    r = by_id[rid]
                    # Block markup is avoided inside original labels, which remain literal text.
                    label = r['concept'].replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
                    suffix = ' · 归属待核对' if r['review'] else ''
                    if rid in audit:
                        a = audit[rid]
                        if a['核查结论'] == '名称有歧义，待确认':
                            suffix = f" · **【待确认】** {a['说明']}"
                        else:
                            rank = a['名称等级']
                            suffix = f" · **【未到种·{rank}】** {a['对应类群或含义']}。{a['说明']}"
                        if a['参考依据']:
                            suffix += f" [依据]({a['参考依据']})"
                    lines.append(f"  - {label}〔{rid}〕" + suffix)
            lines.append('')
    (BASE / (stem + '.md')).write_text('\n'.join(lines) + '\n')
    return tree


def validate_source():
    # 这是固定批次回放；新批次不能沿用本文件中按 ID 编写的归属判断。
    if hashlib.sha256(SOURCE.read_bytes()).hexdigest() != SOURCE_SHA256:
        raise ValueError('Original input changed; this replay requires the frozen 464-concept batch.')
    original = []
    for chunk in re.split(r'(?m)^(?=[123]\t)', SOURCE.read_text())[1:]:
        cols = chunk.rstrip('\n').split('\t')
        assert len(cols) == 4
        for i, name in enumerate(cols[3].splitlines(), 1):
            original.append((f'{cols[0]}-{i:03d}', int(cols[0]), name))
    actual = [(r['id'], r['group'], r['concept']) for r in rows]
    assert actual == original, 'Source concept labels, groups, count or order changed.'
    assert len(rows) == len(by_id) == 464
    assert Counter(r['group'] for r in rows) == {1:25, 2:225, 3:214}
    for kind in ['visual', 'knowledge']:
        tree = build_tree(kind)
        ids = [rid for b in tree.values() for c in b.values() for group in c.values() for rid in group]
        assert Counter(ids) == Counter(by_id.keys()), (kind, 'missing or duplicated leaves')
    return {
        'source_sha256': hashlib.sha256(SOURCE.read_bytes()).hexdigest(),
        'concept_count': len(rows), 'unique_original_labels': len({r['concept'] for r in rows}),
        'groups': dict(Counter(r['group'] for r in rows)),
        'original_labels_exactly_preserved': True, 'original_order_preserved': True,
        'one_primary_path_per_concept_per_edition': True,
        'review_count': sum(bool(r['review']) for r in rows),
        'knowledge_extra_entry_count': sum(len(r['extra_entries']) for r in rows),
        'knowledge_concepts_with_extra_entries': sum(bool(r['extra_entries']) for r in rows),
        'counts': {kind: {
            'level_1': len({r[kind][0] for r in rows}),
            'level_2': len({tuple(r[kind][:2]) for r in rows}),
            'level_3': len({tuple(r[kind]) for r in rows}),
        } for kind in ['visual', 'knowledge']},
    }


def export_files(visual_only=False, csv_exports=True):
    stats = validate_source()
    for kind, stem, title in [('visual','01-视觉版分类树','视觉版分类树'), ('knowledge','02-知识库版分类树','知识库版分类树')]:
        if visual_only and kind != 'visual':
            continue
        export_tree(kind, stem, title)
        headers = ['概念ID','原组别','概念原文','一级分类','二级分类','三级分类','概念粒度说明','主题与属性标签','知识库扩展入口','归属待核对','备注']
        if csv_exports:
            write_csv(stem + '.csv', headers, [
                [r['id'],r['group'],r['concept'],*r[kind],r['granularity'],'；'.join(r['tags']),
                 '；'.join(path_text(p) for p in r['extra_entries']) if kind == 'knowledge' else '',r['review'],r['note']]
                for r in rows])
    if visual_only:
        (BASE / 'concept-records.json').write_text(json.dumps(rows, ensure_ascii=False, indent=2) + '\n')
        (BASE / 'validation.json').write_text(json.dumps(stats, ensure_ascii=False, indent=2) + '\n')
        return stats
    write_csv('03-两版逐条对照.csv', ['概念ID','原组别','概念原文','视觉版路径','知识库版主路径','知识库扩展入口','主题与属性标签','概念粒度说明','归属待核对','备注'], [
        [r['id'],r['group'],r['concept'],path_text(r['visual']),path_text(r['knowledge']),
         '；'.join(path_text(p) for p in r['extra_entries']),'；'.join(r['tags']),r['granularity'],r['review'],r['note']] for r in rows])
    write_csv('04-归属待核对.csv', ['概念ID','概念原文','视觉版暂定路径','知识库版暂定路径','核对原因'], [
        [r['id'],r['concept'],path_text(r['visual']),path_text(r['knowledge']),r['review']] for r in rows if r['review']])
    write_csv('05-同义与相关概念.csv', ['关系','概念ID','原概念','说明'], [
        [a['relation'],'；'.join(a['ids']),'；'.join(by_id[rid]['concept'] for rid in a['ids']),a['note']] for a in alias_groups])
    (BASE / 'concept-records.json').write_text(json.dumps(rows, ensure_ascii=False, indent=2) + '\n')
    (BASE / 'validation.json').write_text(json.dumps(stats, ensure_ascii=False, indent=2) + '\n')
    return stats


def export_overview(stats):
    v, k = stats['counts']['visual'], stats['counts']['knowledge']
    lines = [
        '# 同一概念清单，两套分类树', '',
        '已按原始清单建立两版可评审草案：视觉版用于图片概念标注，知识库版用于按领域和主题检索。', '',
        f"原始概念共 {len(rows)} 条：第1组25条、第2组225条、第3组214条。原文、大小写、标点、顺序及组别全部保留。没有合并近义词，没有补造新的概念，也没有把类群名称擅自替换成物种名。", '',
        '## 先看差别', '',
        '| 比较点 | 视觉版 | 知识库版 |',
        '|---|---|---|',
        '| 主问题 | 图中标注的主体是什么？ | 查这个概念时，会从哪个领域进入？ |',
        '| 聚合依据 | 对象类型、器形、结构、部件、动作或场景 | 学科、用途、文化主题和检索任务 |',
        '| 相邻概念 | 同一类对象或相似结构 | 同一领域内彼此有关的对象、人物、操作和知识 |',
        '| 一个概念的入口 | 一个主路径，属性另存 | 一个主路径，可添加跨主题检索入口 |',
        '| 典型收益 | 标注边界和统计口径相对稳定 | 医学、音乐、民俗等专题下的内容更完整 |',
        '| 实际限制 | 不保证每个物种、型号仅凭一张图可区分 | 主题可能交叉，需要明确主归属与辅助入口 |', '',
        '**两版的差别在分类依据，不以树的层数或类目数量作为优劣标准。** 动物等基础类目本来就可以相近；文物、器材、场景、图示和文化概念更容易体现差别。', '',
        '## 同一条概念在两版中的位置', '',
        '| 原概念 | 视觉版主路径 | 知识库版主路径 | 差别 |',
        '|---|---|---|---|',
    ]
    examples = {
        '2-099':'可见器物类型 → 文物身份；知识库还关联炊具。',
        '2-158':'设备对象 → 医学影像领域。',
        '3-018':'图示类型 → 人体解剖知识。',
        '3-023':'手术场景 → 眼科治疗主题。',
        '2-010':'人的职业身份 → 音乐领域。',
        '2-151':'人物塑像 → 佛教文化。',
        '3-004':'固定翼飞机 → 军事装备用途。',
        '2-208':'犬类动物 → 家养品种管理。',
        '2-048':'结构类型 → 具名桥梁案例；知识库还关联拱桥。',
        '3-214':'主树都保留鹤类；知识库增加幼体与生长入口。',
        '1-008':'两版都保留甲虫类；“天牛”仍是科级概念。',
        '3-160':'两版都保留多肉植物入口；“肉锥花”仍是属级概念。',
    }
    for rid, gap in examples.items():
        r = by_id[rid]
        lines.append(f"| {r['concept']} | {path_text(r['visual'])} | {path_text(r['knowledge'])} | {gap} |")
    lines += [
        '', '“妇好三联甗”的两种归属基于其青铜文物身份及蒸煮用途，见[中国国家博物馆藏品说明](https://www.chnmuseum.cn/zp/zpml/kgfjp/202107/t20210728_250870.shtml)。悉尼港大桥的拱桥分类见[新南威尔士州政府资料](https://www.nsw.gov.au/visiting-and-exploring-nsw/locations-and-attractions/sydney-harbour-bridge)。其他抽查依据保存在 references.json。', '',
        '## 树与原概念的关系', '',
        '树固定为“一级 / 二级 / 三级类目”，再挂载概念。三个类目层级是产品导航深度，不对应固定的纲、目、科、属、种。动物与植物枝条采用实用导航分组；这两版都不是完整的分类学数据库。', '',
        '- `概念ID`：本次按原组别和原行序生成；两版共用，不因路径改变而改变。',
        '- `概念原文`：不可变字段，保持用户原始清单。',
        '- `原组别`：保留1、2、3，独立于分类树；不推断它们一定等于难度或稀有程度。',
        '- `视觉版路径`与`知识库版主路径`：可独立调整。',
        '- `主题与属性标签`：保存幼体、动作、生境、材质、型号特征等；目前先补充影响归属的标签，并非完整自动标注。',
        '- `知识库扩展入口`：指向同一个概念ID，不创建新概念或新副本。',
        '- `概念粒度说明`：区分类群、种级学名、品种、部位、行为和场景；“生物名称”不代表已经核验到种。',
        '- `归属待核对`：注明原名有歧义、缺少图像上下文或资料表述不一致的情况。', '',
        '## 归类规则与边界', '',
        '1. 对象主体优先：雪豹幼崽归雪豹所在动物类目，幼体是属性；绒啄木鸟觅食保留鸟类主归属，觅食作为行为关联。',
        '2. 场景本身是目标时，归场景：荷塘、温室、农田不强行归为单株植物。美洲豹雨林暂以动物为主体，并保留核对标记。',
        '3. 具名实体和通用器形可共用视觉父类：具名桥梁按结构归类；知识库允许从案例或历史主题进入。',
        '4. 区分整体、部件和表示形式：机翼不等于飞机，花序不等于植物整体，植物细胞图不等于植物细胞。微观实体与图示分别设置入口。',
        '5. 已加工食品按食品归类：金枪鱼刺身进入菜肴；鱼类食材作为知识库关联。植物名没有加工提示时以植物解释，明确的茎、叶、花、种子按部位解释。',
        '6. 家养品种、栽培品种及科属通名照原文保留；仅重建上层树不能使它们全部成为“具体物种”。',
        '7. 中国生肖形象、阿凡达伊卡兰翼龙进入文化或虚构形象，避免与真实动物混用。',
        '8. 视觉版也使用常见语义和功能类别，例如蔬菜植物、医学设备；它是标注导航树，不是只按颜色、轮廓聚类的纯外观树。',
        '9. 植物导航按优先顺序处理交叉：明确部位 → 多肉与肉质类型 → 木本 → 水生湿地草本 → 其余草本与藤本。药用、观赏、食用可另加标签。此规则是本清单的导航约定，不是生物分类等级。',
        '10. 科目或属级概念和具体物种可以同在概念集合中，但它们的粒度需要单独筛选，不能用“第三级”代替“已到种”。', '',
        '## 两版一级目录', '',
        '| 视觉版一级目录 | 概念数 | 知识库版一级目录 | 概念数 |',
        '|---|---:|---|---:|',
    ]
    vc = list(Counter(r['visual'][0] for r in rows).items())
    kc = list(Counter(r['knowledge'][0] for r in rows).items())
    for i in range(max(len(vc), len(kc))):
        va, vn = vc[i] if i < len(vc) else ('', '')
        ka, kn = kc[i] if i < len(kc) else ('', '')
        lines.append(f'| {va} | {vn} | {ka} | {kn} |')
    lines += [
        '', '此表左右独立列举，不表示行间一一映射。', '',
        f"视觉版有 {v['level_1']} 个一级类目、{v['level_2']} 个二级类目、{v['level_3']} 个三级类目；知识库版为 {k['level_1']} / {k['level_2']} / {k['level_3']}。这些是当前清单覆盖的类目，未为不存在的概念创建空分支。", '',
        '当前清单较小，部分三级类目仅有一条概念。是否合并应依据未来数据规模和使用习惯；这里先保留有明确扩展方向的类目，不人为凑齐数量。', '',
        '## 文件入口', '',
        '- [视觉版完整分类树](01-视觉版分类树.md) / [视觉版逐条CSV](01-视觉版分类树.csv)',
        '- [知识库版完整分类树](02-知识库版分类树.md) / [知识库版逐条CSV](02-知识库版分类树.csv)',
        '- [两版464条概念对照表](03-两版逐条对照.csv)',
        '- [归属待核对清单](04-归属待核对.csv)',
        '- [同义与相关概念](05-同义与相关概念.csv)',
        '- `concept-records.json`：共用概念记录、两版路径、属性与检索入口。',
        '- `01-视觉版分类树.json`、`02-知识库版分类树.json`：树节点末端保存概念ID。',
        '- `source-concepts.json`：原始概念快照；`validation.json`：原文保留及覆盖检查。',
        '- `references.json`：部分需要背景知识的归属依据；未声称逐项完成物种、品种和专名核验。',
        '- `build.py`：重建上述数据文件和本说明；分类修改集中在此文件。', '',
        '## 核验与范围', '',
        f"已将两版导出表和原附件逐条比对：464条概念的名称、顺序、组别一致，两棵主树各出现每个ID一次。知识库有 {stats['knowledge_concepts_with_extra_entries']} 条概念设置了扩展入口。", '',
        f"有 {stats['review_count']} 条概念保留归属核对标记，例如“管子”“胜利之吻”和“美洲豹雨林”。这不阻止其进入草案；当前路径明确标为暂定。", '',
        '原附件的二级、三级分类是按组汇总的列表，没有提供每条概念对应的旧路径。因此本方案可追溯到原组别和原概念，但不伪造逐条“旧路径 → 新路径”映射。', '',
        '原文件与现有项目数据均未修改；本目录为新建方案。两版同义关系也只作关联，例如 Mooncake 与月饼仍是两个原始记录。', '',
        '## 采用建议', '',
        '如果主要任务是图片采集、标注和按类别评测，可用视觉版作为主目录，知识库版作为领域检索视图；底层只维护一份概念记录。若主要管理课程、资料和专题内容，则以知识库版作为默认入口。', '',
        '真正需要选定的是默认浏览入口与维护规则。概念ID保持共享后，不必在两套重复概念库之间二选一。', '',
    ]
    (BASE / '00-两版差异与使用说明.md').write_text('\n'.join(lines))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    editions = parser.add_mutually_exclusive_group()
    editions.add_argument('--combined', action='store_true', help='重放两批合并视觉版（默认）；不调用模型。')
    editions.add_argument('--visual-only', action='store_true', help='重放首批 464 条历史视觉版，不生成 CSV。')
    args = parser.parse_args()
    if not args.visual_only:
        from combined_view import build_combined
        print(json.dumps(build_combined(BASE), ensure_ascii=False, indent=2))
        raise SystemExit(0)
    complete_metadata()
    # 先验证输入，再从本轮记录重建审核标注，最后渲染主树，避免读取上轮标注。
    validate_source()
    from audit_visual_leaves import export_audit
    export_audit(rows, BASE)
    stats = export_files(visual_only=True, csv_exports=False)
    print(json.dumps(stats, ensure_ascii=False, indent=2))
