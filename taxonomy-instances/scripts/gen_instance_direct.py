#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gen_instance_direct.py — 由助手(模型自身)直接逐实例生成知识库字段，不依赖任何外部 API / 联网。

生成三项：
    · desc    详细介绍（模型自身知识，客观准确）
    · query   检索扩展词（含英文/简称，逗号分隔前的数组）
    · aliases 别名（含英文名）

策略
----
- DIRECT_KB：模型直接撰写的知名实体真实内容（覆盖各 IP 子分支头部实体）。
  命中即写入 curated 级真实 desc/query/aliases。
- 未命中实体：保留既有富文本（不回退、不编造），仅补齐 query 词（取别名首项或实例名），
  保证查看器对所有实例都能显示 query 词。
- 全程本地运行，零网络、零密钥。

用法
----
  python scripts/gen_instance_direct.py --dry-run --limit 3
  python scripts/gen_instance_direct.py --write
"""
import json
import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
META_PATH = ROOT / "data" / "instances_meta.json"

# ------------------------------------------------------------------ 模型直接撰写的知识库
# 每条: name -> {definition, intro, desc, query:[...], aliases:[...]}
# query / aliases 含英文与常用简称，用于检索扩展。
DIRECT_KB = {
    # ===== 内容作品 IP · 动漫 =====
    "哆啦A梦": {"definition": "日本国民级科幻搞笑漫画及动画系列", "intro": "藤子·F·不二雄创作的猫型机器人题材作品", "desc": "《哆啦A梦》是藤子·F·不二雄创作的科幻搞笑漫画，讲述来自未来的猫型机器人哆啦A梦帮助小学生野比大雄的故事；1979 年起多次动画化，成为全球最具影响力的长寿 IP 之一，衍生电影、游戏与海量周边。", "query": ["Doraemon", "机器猫", "小叮当", "铜锣卫门"], "aliases": ["Doraemon", "机器猫", "小叮当", "铜锣卫门"]},
    "名侦探柯南": {"definition": "日本推理题材漫画及动画系列", "intro": "青山刚昌创作的变身侦探题材作品", "desc": "《名侦探柯南》是青山刚昌创作的推理漫画，主角工藤新一被变小为小学生柯南，以推理破案；1996 年动画化后长盛不衰，以单元剧推理与主线悬念著称。", "query": ["Detective Conan", "Conan", "江户川柯南"], "aliases": ["Detective Conan", "江户川柯南"]},
    "火影忍者": {"definition": "日本忍者题材少年漫画及动画系列", "intro": "岸本齐史创作的忍者成长冒险作品", "desc": "《火影忍者》是岸本齐史创作的少年漫画，讲述漩涡鸣人成长为忍者领袖的历程；以忍术体系、羁绊主题与长篇剧情风靡全球，续作《博人传》延续世界观。", "query": ["Naruto", "NARUTO", "漩涡鸣人"], "aliases": ["Naruto", "NARUTO", "漩涡鸣人"]},
    "海贼王": {"definition": "日本航海冒险题材少年漫画及动画系列", "intro": "尾田荣一郎创作的寻宝冒险作品", "desc": "《海贼王》（ONE PIECE）是尾田荣一郎创作的冒险漫画，讲述蒙奇·D·路飞寻找大秘宝「ONE PIECE」成为海贼王的旅程；自 1997 年连载，是全球发行量最高的漫画之一。", "query": ["One Piece", "OP", "路飞"], "aliases": ["One Piece", "OP", "路飞"]},
    "龙珠": {"definition": "日本格斗冒险题材少年漫画及动画系列", "intro": "鸟山明创作的武术科幻作品", "desc": "《龙珠》是鸟山明创作的格斗冒险漫画，围绕孙悟空收集龙珠、修炼武术展开；开创赛亚人、元气弹等经典设定，深刻影响全球动作漫画与动画。", "query": ["Dragon Ball", "DB", "孙悟空", "卡卡罗特"], "aliases": ["Dragon Ball", "DB", "卡卡罗特"]},
    "樱桃小丸子": {"definition": "日本日常搞笑漫画及动画系列", "intro": "樱桃子创作的童年生活题材作品", "desc": "《樱桃小丸子》是樱桃子基于自身童年创作的日常动画，以小学三年级女孩丸子一家的琐碎生活展现昭和温情幽默，是日本国民级国民动画。", "query": ["Chibi Maruko Chan", "Maruko", "樱桃小丸子"], "aliases": ["Chibi Maruko Chan", "Maruko"]},
    "蜡笔小新": {"definition": "日本日常搞笑漫画及动画系列", "intro": "臼井仪人创作的幼儿题材作品", "desc": "《蜡笔小新》是臼井仪人创作的搞笑漫画，以五岁男孩野原新之助的无厘头言行解构成人世界，风格另类幽默，动画长播至今。", "query": ["Crayon Shin Chan", "Shin Chan", "野原新之助"], "aliases": ["Crayon Shin Chan", "Shin Chan"]},
    "千与千寻": {"definition": "宫崎骏执导的奇幻动画电影", "intro": "吉卜力工作室代表作", "desc": "《千与千寻》是宫崎骏执导、吉卜力制作的奇幻动画电影，讲述少女千寻误入神灵世界救回父母的冒险；获奥斯卡最佳动画长片与柏林金熊奖，是全球动画巅峰之作。", "query": ["Spirited Away", "Sen to Chihiro", "宫崎骏", "吉卜力"], "aliases": ["Spirited Away", "Sen to Chihiro", "吉卜力"]},
    "龙猫": {"definition": "宫崎骏执导的奇幻动画电影", "intro": "吉卜力工作室代表作", "desc": "《龙猫》是宫崎骏执导、吉卜力制作的奇幻动画，讲述两姐妹在乡间邂逅森林精灵龙猫的温情故事；龙猫形象成为吉卜力与宫崎骏的符号性标志。", "query": ["My Neighbor Totoro", "Totoro", "吉卜力", "宫崎骏"], "aliases": ["My Neighbor Totoro", "Totoro"]},
    "你的名字": {"definition": "新海诚执导的奇幻爱情动画电影", "intro": "2016 年现象级日本动画", "desc": "《你的名字。》是新海诚执导的奇幻爱情动画，男女主因梦境身体互换并追寻彼此；以精致作画与情感叙事成为 2016 年现象级作品，票房破纪录。", "query": ["Your Name", "Kimi no Na wa", "新海诚"], "aliases": ["Your Name", "Kimi no Na wa", "新海诚"]},
    "宝可梦": {"definition": "任天堂旗下跨媒体收集对战 IP", "intro": "精灵宝可梦全球品牌", "desc": "《宝可梦》（Pokémon）是任天堂、Game Freak 的跨媒体 IP，以收集与对战精灵「宝可梦」为核心，涵盖游戏、动画、卡牌与商品，皮卡丘为其全球符号。", "query": ["Pokemon", "Pokémon", "精灵宝可梦", "皮卡丘"], "aliases": ["Pokemon", "Pokémon", "精灵宝可梦", "皮卡丘"]},
    "数码宝贝": {"definition": "日本跨媒体虚拟生物题材 IP", "intro": "东映动画数码怪兽系列", "desc": "《数码宝贝》（Digimon）是万代与东映的跨媒体 IP，讲述少年与数码世界伙伴怪兽共同冒险成长；与宝可梦并称两大怪兽收集题材。", "query": ["Digimon", "数码暴龙"], "aliases": ["Digimon", "数码暴龙"]},
    "美少女战士": {"definition": "日本魔法少女题材漫画及动画", "intro": "武内直子创作的作品", "desc": "《美少女战士》是武内直子创作的魔法少女漫画，主角月野兔变身为水手月亮守护地球；开创战斗型魔法少女范式，影响深远。", "query": ["Sailor Moon", "美少女戦士", "月野兔"], "aliases": ["Sailor Moon", "月野兔"]},
    "葫芦娃": {"definition": "上海美术电影制片厂剪纸动画", "intro": "中国经典国产动画", "desc": "《葫芦娃》是上海美术电影制片厂 1986 年剪纸动画，七兄弟各怀绝技消灭蛇精蝎精；是中国动画经典，承载几代观众记忆。", "query": ["Calabash Brothers", "葫芦兄弟", "七兄弟"], "aliases": ["Calabash Brothers", "葫芦兄弟"]},
    "黑猫警长": {"definition": "上海美术电影制片厂动画系列", "intro": "中国经典国产动画", "desc": "《黑猫警长》是上海美术电影制片厂 1984 年动画，黑猫警长惩治犯罪守护森林；以『请看下集』式悬念成为国产动画经典。", "query": ["Black Cat Sheriff", "黑猫警长"], "aliases": ["Black Cat Sheriff"]},
    "大闹天宫": {"definition": "上海美术电影制片厂神话动画长片", "intro": "中国动画里程碑", "desc": "《大闹天宫》是上海美术电影制片厂 1961–1964 年动画长片，改编自《西游记》孙悟空大闹天宫；民族风格美术巅峰，国际屡获大奖。", "query": ["Havoc in Heaven", "孙悟空", "上美影"], "aliases": ["Havoc in Heaven", "上美影"]},
    "喜羊羊与灰太狼": {"definition": "中国原创儿童动画系列", "intro": "原创动力出品", "desc": "《喜羊羊与灰太狼》是原创动力出品的儿童动画，羊村与灰太狼的搞笑对抗；曾长期领跑国产少儿动画收视，有多部大电影。", "query": ["Pleasant Goat", "喜羊羊"], "aliases": ["Pleasant Goat"]},
    "熊出没": {"definition": "中国原创儿童动画电影及系列", "intro": "华强方特出品", "desc": "《熊出没》是华强方特出品的儿童动画与电影系列，熊大熊二守护森林对抗光头强；春节档动画电影常驻，国民级少儿 IP。", "query": ["Boonie Bears", "熊大", "熊二", "光头强"], "aliases": ["Boonie Bears", "熊大", "熊二", "光头强"]},
    "小猪佩奇": {"definition": "英国学前动画系列", "intro": "Astley Baker Davies 出品", "desc": "《小猪佩奇》是英国学前动画，讲述粉色小猪佩奇一家的日常生活；以简单画风与亲子温情全球走红，衍生海量周边。", "query": ["Peppa Pig", "粉红猪小妹"], "aliases": ["Peppa Pig", "粉红猪小妹"]},
    "猫和老鼠": {"definition": "美国经典幽默动画系列", "intro": "米高梅出品", "desc": "《猫和老鼠》（Tom and Jerry）是米高梅的经典无声对白幽默动画，猫鼠追逐闹剧跨越世代，两获奥斯卡最佳动画短片。", "query": ["Tom and Jerry", "Tom Jerry"], "aliases": ["Tom and Jerry", "Tom Jerry"]},
    "米老鼠": {"definition": "迪士尼标志性卡通角色", "intro": "华特·迪士尼创作", "desc": "米老鼠（Mickey Mouse）是华特·迪士尼 1928 年创造的卡通老鼠，迪士尼帝国的象征；伴生角色米妮、唐老鸭构成经典家族。", "query": ["Mickey Mouse", "米奇", "Disney"], "aliases": ["Mickey Mouse", "米奇", "Disney"]},
    "唐老鸭": {"definition": "迪士尼经典卡通角色", "intro": "迪士尼鸭子形象", "desc": "唐老鸭（Donald Duck）是迪士尼 1934 年创造的卡通鸭子，以暴躁脾气与独特嗓音著称，常与米老鼠、高飞同框。", "query": ["Donald Duck", "唐纳德鸭"], "aliases": ["Donald Duck", "唐纳德鸭"]},
    "孙悟空": {"definition": "中国神话小说《西游记》主角", "intro": "齐天大圣", "desc": "孙悟空是中国神魔小说《西游记》主角，号齐天大圣，随唐僧西天取经；七十二变、筋斗云等神通广为流传，是中华文化最具世界知名度的文学形象之一。", "query": ["Sun Wukong", "Monkey King", "齐天大圣", "美猴王"], "aliases": ["Sun Wukong", "Monkey King", "齐天大圣", "美猴王"]},
    "哪吒": {"definition": "中国神话人物", "intro": "莲花化身的少年神将", "desc": "哪吒是中国神话中的少年神将，莲花化身、脚踏风火轮，出自《封神演义》《西游记》；2019 年动画电影《哪吒之魔童降世》刷新国产动画票房。", "query": ["Nezha", "哪吒之魔童降世", "三坛海会大神"], "aliases": ["Nezha", "三坛海会大神"]},
    # ===== 内容作品 IP · 游戏 =====
    "超级马里奥": {"definition": "任天堂招牌平台跳跃游戏系列", "intro": "马里奥兄弟", "desc": "《超级马里奥》是任天堂的平台跳跃游戏系列，水管工马里奥拯救碧琪公主；与马力欧IP构成游戏史最具影响力的品牌之一。", "query": ["Super Mario", "Mario", "任天堂"], "aliases": ["Super Mario", "Mario", "任天堂"]},
    "塞尔达传说": {"definition": "任天堂动作冒险游戏系列", "intro": "开放世界标杆", "desc": "《塞尔达传说》是任天堂的动作冒险游戏系列，林克拯救塞尔达公主；2017 年《旷野之息》重定义开放世界游戏。", "query": ["The Legend of Zelda", "Zelda", "Link", "任天堂"], "aliases": ["The Legend of Zelda", "Zelda", "Link"]},
    "我的世界": {"definition": "沙盒建造游戏", "intro": "Mojang 出品", "desc": "《我的世界》（Minecraft）是 Mojang 的沙盒建造游戏，玩家以方块自由建造与生存；全球销量最高的游戏之一，教育版进入课堂。", "query": ["Minecraft", "MC", "我的世界"], "aliases": ["Minecraft", "MC"]},
    "王者荣耀": {"definition": "腾讯天美工作室 MOBA 手游", "intro": "国民级移动电竞", "desc": "《王者荣耀》是腾讯天美工作室的 5v5 MOBA 手游，基于中国历史与神话英雄；长期居中国移动游戏收入前列，并发展出职业联赛 KPL。", "query": ["Honor of Kings", "Arena of Valor", "KPL"], "aliases": ["Honor of Kings", "Arena of Valor", "KPL"]},
    "原神": {"definition": "米哈游开放世界 RPG", "intro": "全球发行", "desc": "《原神》是米哈游的开放世界动作 RPG，玩家穿越提瓦特大陆；以二次元美术与跨平台运营成为全球现象级手游。", "query": ["Genshin Impact", "Genshin", "米哈游"], "aliases": ["Genshin Impact", "Genshin", "米哈游"]},
    "英雄联盟": {"definition": "Riot 出品 MOBA 端游", "intro": "全球电竞标杆", "desc": "《英雄联盟》（League of Legends）是 Riot 的 5v5 MOBA 端游，以英雄多样与赛事生态著称；S 赛是全球观看量最高的电竞赛事之一。", "query": ["League of Legends", "LOL", "英雄联盟"], "aliases": ["League of Legends", "LOL"]},
    "魔兽世界": {"definition": "暴雪 MMORPG", "intro": "大型多人在线角色扮演", "desc": "《魔兽世界》（World of Warcraft）是暴雪的 MMORPG，以艾泽拉斯世界观与团队副本著称；长期定义大型多人在线游戏品类。", "query": ["World of Warcraft", "WOW", "魔兽"], "aliases": ["World of Warcraft", "WOW", "魔兽"]},
    "仙剑奇侠传": {"definition": "大宇资讯国产 RPG 系列", "intro": "中文 RPG 经典", "desc": "《仙剑奇侠传》是大宇资讯的国产角色扮演游戏，以李逍遥与赵灵儿的仙侠爱情故事开创中文 RPG 经典；衍生影视与多代续作。", "query": ["Chinese Paladin", "仙剑"], "aliases": ["Chinese Paladin", "仙剑"]},
    # ===== 内容作品 IP · 影视 =====
    "流浪地球": {"definition": "中国科幻电影系列", "intro": "刘慈欣小说改编", "desc": "《流浪地球》是郭帆执导、改编自刘慈欣小说的科幻电影，人类推动地球逃离太阳系；2019 年开启中国硬科幻电影时代，续集票房破纪录。", "query": ["The Wandering Earth", "刘慈欣", "郭帆"], "aliases": ["The Wandering Earth", "刘慈欣", "郭帆"]},
    "哪吒之魔童降世": {"definition": "2019 年中国动画电影", "intro": "光线彩条屋出品", "desc": "《哪吒之魔童降世》是光线彩条屋出品的动画电影，重构哪吒『我命由我不由天』的成长叙事；票房居国产动画前列。", "query": ["Ne Zha", "魔童降世"], "aliases": ["Ne Zha", "魔童降世"]},
    "阿凡达": {"definition": "詹姆斯·卡梅隆科幻电影", "intro": "影史票房标杆", "desc": "《阿凡达》（Avatar）是詹姆斯·卡梅隆执导的科幻电影，以潘多拉星球与 3D 技术革新影史；长期居全球票房前列。", "query": ["Avatar", "James Cameron", "卡梅隆"], "aliases": ["Avatar", "James Cameron", "卡梅隆"]},
    "复仇者联盟": {"definition": "漫威超级英雄电影系列", "intro": "MCU 集结", "desc": "《复仇者联盟》是漫威电影宇宙的超级英雄集结系列，钢铁侠、美队、雷神等联手；全球票房与粉丝文化现象级。", "query": ["Avengers", "Marvel", "MCU"], "aliases": ["Avengers", "Marvel", "MCU"]},
    "哈利波特": {"definition": "J.K.罗琳魔法文学及电影系列", "intro": "魔法世界 IP", "desc": "《哈利·波特》是 J.K.罗琳的魔法小说及电影系列，讲述巫师少年哈利在霍格沃茨的成长；衍生『魔法世界』主题乐园与游戏。", "query": ["Harry Potter", "HP", "J.K. Rowling"], "aliases": ["Harry Potter", "HP", "J.K. Rowling"]},
    "星球大战": {"definition": "乔治·卢卡斯太空歌剧 IP", "intro": "全球科幻经典", "desc": "《星球大战》（Star Wars）是乔治·卢卡斯创作的太空歌剧 IP，原力、绝地、天行者家族构成跨世代科幻宇宙；衍生剧集、游戏与周边庞大。", "query": ["Star Wars", "星球大战", "Luke Skywalker"], "aliases": ["Star Wars", "Luke Skywalker"]},
    # ===== 内容作品 IP · 文学 =====
    "西游记": {"definition": "中国古典神魔小说", "intro": "四大名著之一", "desc": "《西游记》是明代吴承恩的神魔小说，唐僧师徒西天取经；与孙悟空、猪八戒等形象深刻塑造华人文化想象，改编无数。", "query": ["Journey to the West", "吴承恩"], "aliases": ["Journey to the West", "吴承恩"]},
    "红楼梦": {"definition": "中国古典世情小说", "intro": "四大名著之一", "desc": "《红楼梦》是清代曹雪芹的世情小说，以贾府兴衰与宝黛爱情折射封建家族；中国古典小说巅峰，红学成为专门学问。", "query": ["Dream of the Red Chamber", "曹雪芹", "石头记"], "aliases": ["Dream of the Red Chamber", "曹雪芹", "石头记"]},
    "三国演义": {"definition": "中国古典历史演义小说", "intro": "四大名著之一", "desc": "《三国演义》是元末明初罗贯中的历史演义，演绎东汉末年至三国鼎立的群雄争战；桃园三结义、赤壁等情节家喻户晓。", "query": ["Romance of the Three Kingdoms", "罗贯中"], "aliases": ["Romance of the Three Kingdoms", "罗贯中"]},
    "水浒传": {"definition": "中国古典英雄传奇小说", "intro": "四大名著之一", "desc": "《水浒传》是元末明初施耐庵的英雄传奇，讲述梁山好汉聚义；108 将形象深入民间，改编戏曲影视极多。", "query": ["Water Margin", "施耐庵", "108将"], "aliases": ["Water Margin", "施耐庵", "108将"]},
    "三体": {"definition": "刘慈欣科幻小说三部曲", "intro": "雨果奖作品", "desc": "《三体》是刘慈欣的科幻三部曲，从文革背景延伸至宇宙文明存亡；获雨果奖，带动中国科幻走向世界，改编影视与动画。", "query": ["The Three Body Problem", "刘慈欣", "黑暗森林"], "aliases": ["The Three Body Problem", "刘慈欣", "黑暗森林"]},
    "盗墓笔记": {"definition": "南派三叔悬疑探险小说系列", "intro": "盗墓题材网络文学", "desc": "《盗墓笔记》是南派三叔的悬疑探险小说，吴邪、张起灵等人下墓解密；开启盗墓题材热潮，衍生剧集与电影。", "query": ["Daomu Biji", "南派三叔", "吴邪"], "aliases": ["Daomu Biji", "南派三叔", "吴邪"]},
    # ===== 品牌 IP =====
    "可口可乐": {"definition": "全球碳酸饮料品牌", "intro": "可口可乐公司旗舰", "desc": "可口可乐（Coca-Cola）是可口可乐公司的碳酸饮料品牌，1886 年创立于美国；以红色标识与圣诞营销成为全球最具价值品牌之一。", "query": ["Coca-Cola", "Coke", "可口可乐"], "aliases": ["Coca-Cola", "Coke"]},
    "麦当劳": {"definition": "全球快餐连锁品牌", "intro": "金拱门", "desc": "麦当劳（McDonald's）是全球快餐连锁，以巨无霸、麦乐鸡与金色拱门标识闻名；据点遍布百余国，本土化营销突出。", "query": ["McDonald's", "McD", "金拱门"], "aliases": ["McDonald's", "McD", "金拱门"]},
    "肯德基": {"definition": "全球炸鸡快餐连锁品牌", "intro": "百胜餐饮", "desc": "肯德基（KFC）是百胜旗下的炸鸡快餐连锁，以 Colonel Sanders 上校形象与炸鸡配方著称；在中国市场门店极广。", "query": ["KFC", "Kentucky Fried Chicken", "肯德基"], "aliases": ["KFC", "Kentucky Fried Chicken"]},
    "星巴克": {"definition": "全球咖啡连锁品牌", "intro": "第三空间", "desc": "星巴克（Starbucks）是美国咖啡连锁品牌，以绿色美人鱼标识与『第三空间』体验著称；推动全球咖啡连锁化。", "query": ["Starbucks", "星巴克"], "aliases": ["Starbucks"]},
    "耐克": {"definition": "全球运动品牌", "intro": "Just Do It", "desc": "耐克（Nike）是美国运动品牌，以勾形标识与『Just Do It』口号闻名；覆盖鞋服与运动科技，签约众多顶级运动员。", "query": ["Nike", "Just Do It", "勾"], "aliases": ["Nike", "Just Do It"]},
    "苹果": {"definition": "美国科技与消费电子品牌", "intro": "iPhone 制造商", "desc": "苹果（Apple）是美国科技品牌，以 iPhone、Mac、iOS 生态与设计驱动著称；全球市值最高的公司之一。", "query": ["Apple", "iPhone", "Apple Inc"], "aliases": ["Apple", "iPhone", "Apple Inc"]},
    "华为": {"definition": "中国信息与通信科技品牌", "intro": "5G 与终端", "desc": "华为（Huawei）是中国信息与通信科技品牌，业务覆盖运营商网络、消费电子与云；5G 与麒麟芯片具全球影响力。", "query": ["Huawei", "华为"], "aliases": ["Huawei"]},
    "小米": {"definition": "中国科技与智能硬件品牌", "intro": "性价比生态", "desc": "小米（Xiaomi）是中国科技品牌，以手机与 AIoT 生态链著称；『为发烧而生』与高性价比定位赢得大众市场。", "query": ["Xiaomi", "MI", "小米"], "aliases": ["Xiaomi", "MI"]},
    "迪士尼": {"definition": "美国娱乐与主题乐园品牌", "intro": "Disney", "desc": "迪士尼（Disney）是美国娱乐巨头，涵盖动画、电影、乐园与流媒体；米老鼠、公主系列与漫威/星战构成超级 IP 矩阵。", "query": ["Disney", "迪士尼", "Walt Disney"], "aliases": ["Disney", "Walt Disney"]},
    "乐高": {"definition": "丹麦拼插积木玩具品牌", "intro": "LEGO", "desc": "乐高（LEGO）是丹麦拼插积木玩具品牌，以标准化砖块激发创造；主题套装与乐园全球流行，跨界 IP 联名众多。", "query": ["LEGO", "乐高"], "aliases": ["LEGO"]},
    "三丽鸥": {"definition": "日本角色 IP 公司", "intro": "Hello Kitty 母公司", "desc": "三丽鸥（Sanrio）是日本角色 IP 公司，Hello Kitty、库洛米等可爱角色构成授权帝国；主打『治愈系』可爱经济。", "query": ["Sanrio", "Hello Kitty", "库洛米"], "aliases": ["Sanrio", "Hello Kitty", "库洛米"]},
    "任天堂": {"definition": "日本游戏公司", "intro": "马里奥/宝可梦平台", "desc": "任天堂（Nintendo）是日本游戏公司，出品马里奥、塞尔达、宝可梦等；以创意玩法与主机（Switch）影响游戏史。", "query": ["Nintendo", "任天堂", "Switch"], "aliases": ["Nintendo", "Switch"]},
    "茅台": {"definition": "中国白酒品牌", "intro": "贵州茅台酒", "desc": "茅台是贵州茅台酒厂出品的高端酱香型白酒，以独特酿造与金融属性著称；中国白酒价值标杆与礼品符号。", "query": ["Moutai", "贵州茅台", "Maotai"], "aliases": ["Moutai", "贵州茅台", "Maotai"]},
    "特斯拉": {"definition": "美国电动汽车品牌", "intro": "Elon Musk", "desc": "特斯拉（Tesla）是美国电动汽车品牌，以 Model S/3/Y 与自动驾驶技术推动电动化；CEO 埃隆·马斯克具全球话题度。", "query": ["Tesla", "TSLA", "马斯克"], "aliases": ["Tesla", "TSLA", "马斯克"]},
    "故宫": {"definition": "中国明清皇家宫殿与博物馆", "intro": "故宫博物院", "desc": "故宫（紫禁城）是明清两代皇家宫殿，现故宫博物院，世界现存最大木结构宫殿群；1987 年列入世界文化遗产。", "query": ["Forbidden City", "Palace Museum", "紫禁城"], "aliases": ["Forbidden City", "Palace Museum", "紫禁城"]},
    "长城": {"definition": "中国古代军事防御工程", "intro": "世界文化遗产", "desc": "长城是中国历代修建的军事防御工程，东起山海关西至嘉峪关；1987 年列入世界文化遗产，是中华民族符号。", "query": ["Great Wall", "万里长城"], "aliases": ["Great Wall", "万里长城"]},
    "埃菲尔铁塔": {"definition": "法国巴黎地标铁塔", "intro": "巴黎象征", "desc": "埃菲尔铁塔（Eiffel Tower）是 1889 年巴黎世博会建成的铁塔，高约 330 米；巴黎城市象征与全球最知名地标之一。", "query": ["Eiffel Tower", "Paris"], "aliases": ["Eiffel Tower", "Paris"]},
    "自由女神像": {"definition": "美国纽约地标雕像", "intro": "法国赠美礼物", "desc": "自由女神像（Statue of Liberty）是法国赠美的新古典主义雕像，矗立纽约港；象征自由与移民，世界文化遗产。", "query": ["Statue of Liberty", "Liberty", "New York"], "aliases": ["Statue of Liberty", "Liberty", "New York"]},
    "富士山": {"definition": "日本最高峰与活火山", "intro": "日本象征", "desc": "富士山（Mount Fuji）是日本最高峰（3776 米）与活火山，圆锥山体为日本象征；2013 年列入世界文化遗产。", "query": ["Mount Fuji", "Fuji", "富士山"], "aliases": ["Mount Fuji", "Fuji"]},
    "金字塔": {"definition": "古埃及陵墓建筑", "intro": "吉萨金字塔群", "desc": "金字塔是古埃及法老陵墓，以吉萨胡夫金字塔最著名；古代世界七大奇迹中唯一尚存者，世界文化遗产。", "query": ["Pyramid", "Giza", "埃及"], "aliases": ["Pyramid", "Giza", "埃及"]},
    "泰姬陵": {"definition": "印度莫卧儿陵墓建筑", "intro": "世界文化遗产", "desc": "泰姬陵（Taj Mahal）是莫卧儿皇帝沙贾汗为亡妃修建的大理石陵墓；白色穹顶对称美学代表，世界文化遗产。", "query": ["Taj Mahal", "印度"], "aliases": ["Taj Mahal", "印度"]},
    # ===== 美食 IP =====
    "北京烤鸭": {"definition": "北京传统名菜", "intro": "宫廷风味", "desc": "北京烤鸭是北京传统名菜，以果木明炉烤制、片皮蘸酱卷饼；全聚德、大董等老字号为代表，享誉世界。", "query": ["Peking Duck", "Beijing Duck", "烤鸭"], "aliases": ["Peking Duck", "Beijing Duck"]},
    "四川火锅": {"definition": "川渝麻辣火锅", "intro": "麻辣鲜香", "desc": "四川火锅是以牛油底料、花椒辣椒涮煮食材的川渝饮食；麻辣鲜香，社交属性强，连锁品牌遍布全国与海外。", "query": ["Sichuan Hotpot", "Hot Pot", "火锅"], "aliases": ["Sichuan Hotpot", "Hot Pot"]},
    "兰州拉面": {"definition": "甘肃清真牛肉拉面", "intro": "一清二白三红四绿", "desc": "兰州拉面（兰州牛肉面）是以手工拉面、清炖牛肉汤与辣油萝卜构成的清真面食；『一清二白三红四绿』，全国街巷常见。", "query": ["Lanzhou Lamian", "Lanzhou Beef Noodle", "牛肉面"], "aliases": ["Lanzhou Lamian", "Lanzhou Beef Noodle", "牛肉面"]},
    "小笼包": {"definition": "江南灌汤蒸包", "intro": "上海/南翔", "desc": "小笼包是江南灌汤蒸制面点，皮薄汁多，以南翔、上海为代表；蟹粉小笼为高端品类，海外中餐名片。", "query": ["Xiaolongbao", "Soup Dumpling", "南翔小笼"], "aliases": ["Xiaolongbao", "Soup Dumpling", "南翔小笼"]},
    "寿司": {"definition": "日本米饭料理", "intro": "Sushi", "desc": "寿司（Sushi）是以醋饭搭配鱼生等食材的日本料理；握寿司、卷物为代表，全球日料符号，职人文化深厚。", "query": ["Sushi", "日本料理"], "aliases": ["Sushi", "日本料理"]},
    "披萨": {"definition": "意大利面饼料理", "intro": "Pizza", "desc": "披萨（Pizza）是意大利起源、全球流行的面饼料理，番茄酱与芝士为底；那不勒斯披萨为世界文化遗产。", "query": ["Pizza", "意大利"], "aliases": ["Pizza", "意大利"]},
    # ===== 吉祥物 IP =====
    "冰墩墩": {"definition": "北京2022冬奥会吉祥物", "intro": "熊猫造型", "desc": "冰墩墩是北京 2022 冬奥会吉祥物，以熊猫形象裹冰晶外壳设计；赛事期间全球爆红，成为现象级特许商品。", "query": ["Bing Dwen Dwen", "冰墩墩", "北京冬奥"], "aliases": ["Bing Dwen Dwen", "北京冬奥"]},
    "雪容融": {"definition": "北京2022冬残奥会吉祥物", "intro": "红灯笼造型", "desc": "雪容融是北京 2022 冬残奥会吉祥物，以红灯笼为造型；与冰墩墩同期推出，传递温暖包容意象。", "query": ["Shuey Rhon Rhon", "雪容融"], "aliases": ["Shuey Rhon Rhon"]},
    "福娃": {"definition": "北京2008奥运会吉祥物", "intro": "五个娃娃", "desc": "福娃是北京 2008 奥运会吉祥物，贝贝、晶晶、欢欢、迎迎、妮妮五娃谐音『北京欢迎你』；中国奥运吉祥物经典。", "query": ["Fuwa", "北京奥运", "五福娃"], "aliases": ["Fuwa", "五福娃"]},
    "熊本熊": {"definition": "日本熊本县营业部长吉祥物", "intro": "Kumamon", "desc": "熊本熊（Kumamon）是日本熊本县吉祥物，黑色憨态熊形象；以『营业部长』人设与病毒式营销成为全球最成功地方吉祥物之一。", "query": ["Kumamon", "熊本熊"], "aliases": ["Kumamon", "熊本熊"]},
    "皮卡丘": {"definition": "宝可梦电系角色", "intro": "黄皮老鼠", "desc": "皮卡丘（Pikachu）是宝可梦中的电系角色，黄色鼠形；宝可梦全球符号，东京奥运曾担任日本代表团 mascot 联动。", "query": ["Pikachu", "宝可梦"], "aliases": ["Pikachu", "宝可梦"]},
    # ===== 非遗 IP =====
    "京剧": {"definition": "中国戏曲剧种", "intro": "国粹", "desc": "京剧是中国影响最大的戏曲剧种，融合唱念做打，2010 年列入人类非物质文化遗产；生旦净丑行当体系完备。", "query": ["Peking Opera", "Beijing Opera", "国粹"], "aliases": ["Peking Opera", "Beijing Opera", "国粹"]},
    "太极拳": {"definition": "中国传统武术与养生功法", "intro": "非遗", "desc": "太极拳是中国传统武术与养生功法，以柔克刚、慢练呼吸；2020 年列入人类非物质文化遗产代表作名录。", "query": ["Tai Chi", "Taijiquan", "太极"], "aliases": ["Tai Chi", "Taijiquan", "太极"]},
    "书法": {"definition": "中国汉字书写艺术", "intro": "非遗", "desc": "书法是中国汉字的书写艺术，篆隶楷行草五体演变；列为人类非物质文化遗产，是东方视觉艺术核心。", "query": ["Chinese Calligraphy", "Shufa", "墨宝"], "aliases": ["Chinese Calligraphy", "Shufa", "墨宝"]},
    "剪纸": {"definition": "中国民间镂空剪纸艺术", "intro": "非遗", "desc": "剪纸是中国民间以剪刀或刻刀在纸上镂空造型的艺术，常用于窗花节庆；列入人类非物质文化遗产。", "query": ["Chinese Paper Cutting", "Jianzhi"], "aliases": ["Chinese Paper Cutting", "Jianzhi"]},
    "二十四节气": {"definition": "中国农耕时间知识体系", "intro": "非遗", "desc": "二十四节气是中国先民观天象制定的农耕时间体系，2016 年列入人类非物质文化遗产；指导农事与节俗。", "query": ["24 Solar Terms", "节气"], "aliases": ["24 Solar Terms", "节气"]},
    # ===== 赛事 IP =====
    "奥运会": {"definition": "国际综合性体育盛会", "intro": "Olympics", "desc": "奥运会（Olympic Games）是国际奥委会主办的综合性体育盛会，分夏冬两季；五环标识与圣火仪式为全球体育最高符号。", "query": ["Olympic Games", "Olympics", "五环"], "aliases": ["Olympic Games", "Olympics", "五环"]},
    "世界杯": {"definition": "国际足联足球锦标赛", "intro": "FIFA World Cup", "desc": "世界杯（FIFA World Cup）是国际足联男子足球最高赛事，四年一届；全球观看量最大的单项体育赛事。", "query": ["FIFA World Cup", "World Cup", "足球"], "aliases": ["FIFA World Cup", "World Cup", "足球"]},
    "NBA": {"definition": "美国职业篮球联赛", "intro": "Basketball", "desc": "NBA（National Basketball Association）是美国职业篮球联赛，汇聚全球顶尖球员；篮球文化与球星 IP 全球输出。", "query": ["NBA", "Basketball", "篮球"], "aliases": ["NBA", "Basketball", "篮球"]},
    # ===== 艺术 IP =====
    "蒙娜丽莎": {"definition": "达·芬奇油画名作", "intro": "卢浮宫镇馆", "desc": "《蒙娜丽莎》是达·芬奇的肖像油画，以微妙微笑与晕涂法著称；卢浮宫镇馆之宝，全球最知名画作之一。", "query": ["Mona Lisa", "Leonardo da Vinci", "达芬奇"], "aliases": ["Mona Lisa", "Leonardo da Vinci", "达芬奇"]},
    "清明上河图": {"definition": "北宋风俗画长卷", "intro": "张择端", "desc": "《清明上河图》是北宋张择端绘制的风俗长卷，描绘汴京市井繁华；中国十大传世名画之一，故宫博物院珍藏。", "query": ["Along the River During the Qingming Festival", "张择端"], "aliases": ["Along the River During the Qingming Festival", "张择端"]},
    # ===== 音乐 IP =====
    "周杰伦": {"definition": "华语流行音乐歌手", "intro": "Jay Chou", "desc": "周杰伦是华语流行音乐歌手、制作人，融合中西方曲风与中国式意象；2000 年代起定义华语流行，IP 跨音乐、电影与品牌。", "query": ["Jay Chou", "周杰伦", "JVR"], "aliases": ["Jay Chou", "JVR"]},
    "贝多芬": {"definition": "德国古典主义作曲家", "intro": "Beethoven", "desc": "路德维希·范·贝多芬是德国作曲家，交响曲与钢琴奏鸣曲巨匠；《命运》《欢乐颂》家喻户晓，古典音乐符号。", "query": ["Beethoven", "Ludwig van Beethoven"], "aliases": ["Beethoven", "Ludwig van Beethoven"]},
    # ===== 真人 IP · 历史 =====
    "李白": {"definition": "唐代浪漫主义诗人", "intro": "诗仙", "desc": "李白是唐代浪漫主义诗人，号青莲居士，诗风豪放飘逸；『诗仙』与杜甫并称『李杜』，中华文化代表人物。", "query": ["Li Bai", "Poet Li", "诗仙"], "aliases": ["Li Bai", "Poet Li", "诗仙"]},
    "孔子": {"definition": "春秋时期思想家教育家", "intro": "儒家创始人", "desc": "孔子是春秋时期思想家、教育家，儒家学派创始人；以『仁』『礼』思想塑造东亚文明，被尊为至圣先师。", "query": ["Confucius", "Kong Zi", "儒家"], "aliases": ["Confucius", "Kong Zi", "儒家"]},
    "诸葛亮": {"definition": "三国时期蜀汉丞相", "intro": "智圣", "desc": "诸葛亮是三国蜀汉丞相，以『鞠躬尽瘁』与智慧谋略著称；木牛流马、空城计等典故深入民间文化。", "query": ["Zhuge Liang", "Kong Ming", "智圣"], "aliases": ["Zhuge Liang", "Kong Ming", "智圣"]},
    # ===== 神话 IP =====
    "白娘子": {"definition": "中国民间传说蛇仙", "intro": "白蛇传", "desc": "白娘子（白素贞）是中国民间传说《白蛇传》主角，千年蛇仙与许仙的人妖之恋；端午雄黄、水漫金山等情节家喻户晓。", "query": ["Lady White", "白素贞", "白蛇传"], "aliases": ["Lady White", "白素贞", "白蛇传"]},
    "牛郎织女": {"definition": "中国民间爱情传说", "intro": "七夕来源", "desc": "牛郎织女是中国民间传说，被银河分隔的恋人每年七夕相会；七夕节的由来，爱情忠贞意象。", "query": ["Cowherd and Weaver Girl", "七夕", "Qixi"], "aliases": ["Cowherd and Weaver Girl", "七夕", "Qixi"]},
    "嫦娥": {"definition": "中国神话月宫仙子", "intro": "奔月", "desc": "嫦娥是中国神话中吞药奔月的仙子，居广寒宫；中秋赏月与探月工程常以嫦娥为名（如嫦娥探月工程）。", "query": ["Chang'e", "Moon Goddess", "奔月"], "aliases": ["Chang'e", "Moon Goddess", "奔月"]},
    # ===== 科技 IP =====
    "北斗": {"definition": "中国卫星导航系统", "intro": "BDS", "desc": "北斗（BDS）是中国自主建设的全球卫星导航系统，提供定位授时与短报文；与美国 GPS、俄罗斯格洛纳斯并列。", "query": ["BeiDou", "BDS", "北斗导航"], "aliases": ["BeiDou", "BDS", "北斗导航"]},
    "神舟": {"definition": "中国载人航天飞船系列", "intro": "Crewed Spacecraft", "desc": "神舟飞船是中国载人航天系列，2003 年首次载杨利伟飞天；支撑空间站建造与航天员驻留。", "query": ["Shenzhou", "载人航天"], "aliases": ["Shenzhou", "载人航天"]},
    "复兴号": {"definition": "中国标准动车组", "intro": "高铁", "desc": "复兴号是中国标准动车组，运营时速 350 公里；代表中国高铁自主技术与『中国速度』国家名片。", "query": ["Fuxing Hao", "CR400", "中国高铁"], "aliases": ["Fuxing Hao", "CR400", "中国高铁"]},
    # ===== 城市 IP =====
    "北京": {"definition": "中国首都", "intro": "直辖市", "desc": "北京是中华人民共和国首都，直辖市与超大城市；故宫、长城、胡同文化汇聚，政治文化与历史中心。", "query": ["Beijing", "Peking", "首都"], "aliases": ["Beijing", "Peking", "首都"]},
    "上海": {"definition": "中国直辖市与经济中心", "intro": "魔都", "desc": "上海是中国直辖市与经济、金融中心，外滩与陆家嘴天际线闻名；国际化大都市与『魔都』符号。", "query": ["Shanghai", "魔都"], "aliases": ["Shanghai", "魔都"]},
    "巴黎": {"definition": "法国首都", "intro": "光之城", "desc": "巴黎是法国首都，以埃菲尔铁塔、卢浮宫与时尚文化闻名；『光之城』与浪漫之都符号。", "query": ["Paris", "France", "光之城"], "aliases": ["Paris", "France", "光之城"]},
    # ===== 节日 IP =====
    "春节": {"definition": "中国农历新年", "intro": "传统节日", "desc": "春节是中国农历新年，贴春联、守岁、拜年、红包构成核心习俗；全球华人最隆重的传统节日。", "query": ["Spring Festival", "Chinese New Year", "过年"], "aliases": ["Spring Festival", "Chinese New Year", "过年"]},
    "中秋节": {"definition": "中国农历团圆节日", "intro": "赏月吃月饼", "desc": "中秋节是中国传统节日，赏月、吃月饼、团圆为主题；嫦娥传说与丰收感恩意象。", "query": ["Mid Autumn Festival", "Moon Festival", "月饼"], "aliases": ["Mid Autumn Festival", "Moon Festival", "月饼"]},
}


def norm(name):
    return name.strip().strip("《》").strip()


def main():
    ap = argparse.ArgumentParser(description="模型直接生成实例知识库（无外部 API）")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args()

    doc = json.load(open(META_PATH, encoding="utf-8"))
    insts = doc.get("instances", [])
    idx = {norm(it["name"]): it for it in insts}

    hits = 0
    filled_query = 0
    plan = []
    for it in insts:
        k = norm(it["name"])
        if k in DIRECT_KB:
            rec = DIRECT_KB[k]
            plan.append(("KB", it["name"], rec["desc"][:30]))
            hits += 1
        else:
            plan.append(("q", it["name"], ""))
            filled_query += 1

    if args.dry_run:
        for typ, name, prev in plan[:max(args.limit, 5)]:
            print(f"[{typ}] {name} | {prev}")
        print(f"[dry-run] KB命中={hits}, 待补query={filled_query}, 总计={len(insts)}")
        return

    for it in insts:
        k = norm(it["name"])
        if k in DIRECT_KB:
            rec = DIRECT_KB[k]
            it["source"] = "curated"
            it["definition"] = rec.get("definition", it.get("definition"))
            it["intro"] = rec.get("intro", it.get("intro"))
            it["desc"] = rec["desc"]
            it["query"] = rec.get("query") or []
            existing = list(it.get("aliases") or [])
            for x in rec.get("aliases", []):
                if x not in existing:
                    existing.append(x)
            it["aliases"] = existing[:10]
        else:
            if not it.get("query"):
                a = it.get("aliases") or []
                it["query"] = [a[0]] if a else [it["name"]]

    doc["meta"] = dict(doc.get("meta", {}))
    doc["meta"]["source"] = (doc["meta"].get("source", "") +
                             " + gen_instance_direct.py(模型直接生成 KB 头部+全量补齐 query)")
    if args.write:
        json.dump(doc, open(META_PATH, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print(f"已写入：KB真实内容={hits} 条, 全量补齐 query 词。文件={META_PATH}")
    else:
        print(f"[预览] KB命中={hits}, 待补query={filled_query}; 加 --write 落盘。")


if __name__ == "__main__":
    main()
