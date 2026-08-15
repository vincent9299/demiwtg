# -*- coding: utf-8 -*-
"""gen_instance_kb.py — 为 IP 分支中「缺少实例级富文本」的实例生成知识库。

背景（2026-08-15，第七轮）：
  之前只有「虚构角色 IP」分支的实例有富文本（gen_role_intros.py，字典+模板），
  其余 20 个子分支（内容作品 IP / 品牌 IP / 地标 IP / 美食 IP … 共 55,781 条）
  实例级 KB 全为 0，导致查看器只能把实例回退显示「分类标签」的内容（错误且雷同）。

  本脚本由「模型（WorkBuddy 代理）」生成实例知识，不用外部 LLM API、不纯模板：
    - 我能可靠写出的知名实体 → 写入 MODEL_KB（真实 definition/intro/desc/aliases），source=curated；
    - 长尾无可靠信息的实体 → 用「实例级、不编造事实」的接地模板（只套 名称+所属分类 框架），
      source=templated，保证全量覆盖且不再回退分类内容。

  实例与分类标签是两类知识对象（instance of vs subclass of），本脚本只补实例侧。

用法：
  python3 scripts/gen_instance_kb.py                 # 仅统计待生成数 + 抽样
  python3 scripts/gen_instance_kb.py --write        # 写回 data/instances_meta.json
  python3 scripts/gen_instance_kb.py --branch "内容作品 IP" --write   # 只跑某子分支（试点）
"""
import json, os, sys, datetime

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
META = os.path.join(REPO, "data", "instances_meta.json")
SEP = " / "

doc = json.load(open(META, encoding="utf-8"))
insts = doc.get("instances", [])


def norm(s):
    return (s or "").replace("《", "").replace("》", "").strip()


# ---------------------------------------------------------------------------
# MODEL_KB：模型手写的真实实例知识。键为实例原始名（含《》与否皆按数据实际）。
# 值 = (definition, intro, desc, [aliases])
# 只收录我能可靠写出准确内容的知名实体；长尾交给 templated() 兜底。
# ---------------------------------------------------------------------------
MODEL_KB = {
    # ---- 内容作品 IP / 动漫作品 / 儿童动画 ----
    "《小猪佩奇》": ("英国学前幼儿动画《小猪佩奇》（Peppa Pig）中的主角粉红小猪家庭故事，围绕佩奇一家的日常生活展开。", "《小猪佩奇》是面向学龄前儿童的英国动画系列。", "《小猪佩奇》自 2004 年开播，以简洁的画风、重复的语言节奏与家庭温情叙事风靡全球，衍生出图书、玩具与主题乐园，是当代低幼动画最具商业延展性的 IP 之一。", ["Peppa Pig", "小猪佩奇家族"]),
    "《哆啦A梦》": ("日本国民级漫画/动画《哆啦A梦》中以道具帮助大雄的猫型机器人角色所在的作品 IP。", "《哆啦A梦》是藤子·F·不二雄创造的国民级动漫作品。", "《哆啦A梦》讲述来自 22 世纪的猫型机器人用四次元口袋的道具帮助小学生大雄成长的故事，是全球最具影响力的动漫 IP 之一，亦承担日本动漫文化大使角色。", ["Doraemon", "机器猫"]),
    "《熊出没》": ("中国合家欢动画《熊出没》系列，围绕熊大熊二与光头强在森林中的冲突与友情展开。", "《熊出没》是华强方特出品的国产动画 IP。", "《熊出没》以「护林 vs 伐木」的轻喜剧主线陪伴数亿中国家庭观众，长青于电视、电影与主题乐园，是国产合家欢动画的顶流 IP。", ["Boonie Bears", "熊出没大电影"]),
    "《喜羊羊与灰太狼》": ("国产动画《喜羊羊与灰太狼》，讲述羊村众羊以智慧智斗灰太狼的故事。", "《喜羊羊与灰太狼》是原创国产动画 IP。", "《喜羊羊与灰太狼》以低龄向的智斗喜剧构建庞大影视与周边矩阵，是 2000 年代后中国最具国民度的儿童动画 IP 之一。", ["Pleasant Goat and Big Big Wolf"]),
    "《海绵宝宝》": ("美国动画《海绵宝宝》（SpongeBob SquarePants）中住在比奇堡海底的黄色海绵主角所在的作品 IP。", "《海绵宝宝》是尼克国际儿童频道的现象级动画。", "《海绵宝宝》以荒诞幽默与怪诞美学风靡全球，是 21 世纪最具代表性的儿童动画作品之一，衍生剧集、电影与海量周边。", ["SpongeBob SquarePants", "SpongeBob"]),
    "《米老鼠》": ("迪士尼创始卡通《米老鼠》（Mickey Mouse）中以圆耳小老鼠为主角的作品 IP。", "《米老鼠》是迪士尼动画王国的图腾作品。", "《米老鼠》自 1928 年《威利号汽船》诞生，奠定迪士尼动画帝国，是全球最知名的商业卡通 IP 与欢乐符号。", ["Mickey Mouse"]),
    "《猫和老鼠》": ("美国经典喜剧动画《猫和老鼠》（Tom and Jerry）中猫鼠追逐打闹的无对白短片系列。", "《猫和老鼠》是米高梅出品的经典动画。", "《猫和老鼠》以纯粹的物理喜剧与配乐叙事跨越世代，是全球最长青的动画短剧 IP 之一，反复被重制与授权。", ["Tom and Jerry"]),
    "《蜡笔小新》": ("日本漫画/动画《蜡笔小新》中 5 岁男孩野原新之助的搞怪日常作品 IP。", "《蜡笔小新》是臼井仪人漫画的国民级儿童作品。", "《蜡笔小新》以超越年龄的搞笑视角与无厘头语言风靡亚洲，是成人向幽默与童真结合的经典动画 IP。", ["Crayon Shin-chan", "Shin-chan"]),
    "《樱桃小丸子》": ("日本动画《樱桃小丸子》中以小学三年级女孩樱桃子为原型的家庭日常作品 IP。", "《樱桃小丸子》是樱桃子自传式漫画的动画化。", "《樱桃小丸子》以平凡日常的幽默与温情成为国民级动画作品，映射普通家庭的真实烟火气。", ["Chibi Maruko-chan"]),
    "《名侦探柯南》": ("日本推理动画《名侦探柯南》中身体变小的高中生侦探工藤新一（柯南）探案的作品 IP。", "《名侦探柯南》是青山刚昌推理漫画的动画化。", "《名侦探柯南》以「真相只有一个」的单元推理长盛不衰，是推理动漫长寿 IP 的核心符号，衍生剧场版与游戏。", ["Detective Conan", "Case Closed"]),
    "《火影忍者》": ("日本热血动漫《火影忍者》（NARUTO）中漩涡鸣人从吊车尾逆袭为救世英雄的作品 IP。", "《火影忍者》是岸本齐史漫画的动画化。", "《火影忍者》以「永不放弃」的信念与忍者世界构建全球粉丝群，是 2000 年代最具影响力的动漫 IP 之一，衍生剧场版与游戏。", ["NARUTO", "Naruto"]),
    "《海贼王》": ("日本少年动漫《海贼王》（ONE PIECE）中蒙奇·D·路飞立志成为海贼王的作品 IP。", "《海贼王》是尾田荣一郎漫画的动画化。", "《海贼王》以伟大航路冒险与羁绊叙事成为销量最高的漫画之一，是当代少年 Jump 最具代表性的热血动漫 IP。", ["ONE PIECE", "One Piece"]),
    "《千与千寻》": ("宫崎骏执导动画电影《千与千寻》中少女千寻误入神隐世界的奇幻作品 IP。", "《千与千寻》是吉卜力工作室的动画电影。", "《千与千寻》以深邃的隐喻与东方奇幻美学斩获奥斯卡最佳动画长片，是宫崎骏最具世界声誉的动画电影 IP。", ["Spirited Away", "千と千尋の神隠し"]),
    # ---- 内容作品 IP / 影视作品 / 电影 ----
    "《哈利·波特》": ("J.K.罗琳魔法小说改编电影《哈利·波特》系列，讲述大难不死的男孩哈利在霍格沃茨的冒险。", "《哈利·波特》是当代最畅销奇幻文学/影视 IP。", "《哈利·波特》以魔法世界与成长主题成为全球最具商业价值的文学/影视跨媒 IP，衍生剧集、主题乐园与游戏宇宙。", ["Harry Potter"]),
    "《星球大战》": ("乔治·卢卡斯创立的科幻电影《星球大战》（Star Wars）宇宙系列。", "《星球大战》是好莱坞科幻电影鼻祖级 IP。", "《星球大战》以原力、绝地武士与银河史诗构建跨越数十年的影视/动画/游戏/衍生品帝国，是科幻 IP 的标杆。", ["Star Wars"]),
    "《复仇者联盟》": ("漫威电影宇宙（MCU）集结超级英雄的《复仇者联盟》系列电影 IP。", "《复仇者联盟》是漫威电影宇宙的群像电影。", "《复仇者联盟》以钢铁侠、美队、雷神等英雄集结放大漫威电影宇宙的商业体量，是全球票房最高的超级英雄电影 IP 之一。", ["The Avengers", "Avengers"]),
    "《泰坦尼克号》": ("詹姆斯·卡梅隆执导爱情灾难电影《泰坦尼克号》（Titanic）。", "《泰坦尼克号》是影史最具影响力的爱情电影之一。", "《泰坦尼克号》以沉船背景下的跨阶层爱情与视觉奇观成为影史票房里程碑，是经典电影 IP 的代表作。", ["Titanic"]),
    "《阿凡达》": ("詹姆斯·卡梅隆执导科幻电影《阿凡达》（Avatar），以潘多拉星球与纳威人为主线。", "《阿凡达》是开启 3D 电影时代的科幻巨制。", "《阿凡达》以逼真 CGI 与外星生态叙事刷新全球票房纪录，并衍生主题乐园与续作宇宙，是科幻电影 IP 的里程碑。", ["Avatar"]),
    # ---- 内容作品 IP / 文学小说 ----
    "《红楼梦》": ("中国古典四大名著之一《红楼梦》，以贾宝玉、林黛玉等人的悲剧命运描绘封建家族兴衰。", "《红楼梦》是中国古典文学的巅峰之作。", "《红楼梦》以宝黛钗爱情悲剧与贾府盛衰折射社会全景，是中国文学最具思想与艺术价值的经典 IP，衍生戏曲、影视与红学。", ["Dream of the Red Chamber", "石头记"]),
    "《西游记》": ("中国古典神魔小说《西游记》，讲述唐僧师徒西天取经的故事。", "《西游记》是中国古典四大名著之一。", "《西游记》以孙悟空等取经团队的奇幻冒险成为国民级神话 IP，反复被改编为动画、影视、游戏与舞台作品。", ["Journey to the West"]),
    "《三国演义》": ("中国古典历史小说《三国演义》，演绎魏蜀吴三国的权谋争雄。", "《三国演义》是中国古典四大名著之一。", "《三国演义》以群雄逐鹿与智谋叙事成为历史演义的巅峰 IP，深刻影响戏曲、影视、游戏与商业文化。", ["Romance of the Three Kingdoms"]),
    "《水浒传》": ("中国古典小说《水浒传》，讲述 108 位好汉聚义梁山的故事。", "《水浒传》是中国古典四大名著之一。", "《水浒传》以侠义聚义与招安悲剧构建草莽英雄图谱，是武侠与历史演义的重要母题 IP。", ["Water Margin", "All Men Are Brothers"]),
    "《福尔摩斯》": ("柯南·道尔侦探小说《福尔摩斯探案集》中推理天才夏洛克·福尔摩斯的作品 IP。", "《福尔摩斯》是侦探文学鼻祖级 IP。", "《福尔摩斯》以演绎法与观察力破解奇案，烟斗与猎鹿帽成为推理文化图腾，是全球被改编最多的文学 IP 之一。", ["Sherlock Holmes"]),
    # ---- 内容作品 IP / 游戏作品 ----
    "《超级马里奥》": ("任天堂平台跳跃游戏《超级马里奥》（Super Mario）系列，水管工马里奥闯关救公主。", "《超级马里奥》是任天堂的招牌游戏 IP。", "《超级马里奥》自 1981 年登场，是全球最知名的游戏作品 IP，代表平台跳跃黄金时代，亦是任天堂企业符号。", ["Super Mario", "Mario"]),
    "《塞尔达传说》": ("任天堂动作冒险游戏《塞尔达传说》（The Legend of Zelda）系列，林克对抗盖侬拯救王国。", "《塞尔达传说》是任天堂的开放世界游戏 IP。", "《塞尔达传说》以探索、解谜与史诗叙事成为动作冒险游戏的标杆 IP，旷野之息等作品屡获年度游戏。", ["The Legend of Zelda", "Zelda"]),
    "《原神》": ("米哈游开放世界 RPG《原神》（Genshin Impact），旅行者在提瓦特大陆寻找亲兄妹。", "《原神》是米哈游出品的开放世界游戏 IP。", "《原神》以跨平台开放世界与角色抽卡运营成为全球现象级游戏 IP，角色（如派蒙、钟离）具备强二创与商业延展性。", ["Genshin Impact"]),
    "《王者荣耀》": ("腾讯 MOBA 手游《王者荣耀》，将历史/神话人物重塑为竞技英雄。", "《王者荣耀》是国民级移动电竞 IP。", "《王者荣耀》以低门槛 MOBA 与社交竞技成为中国最赚钱的手游 IP 之一，并联动非遗、文旅与电竞赛事。", ["Honor of Kings"]),
    # ---- 品牌 IP ----
    "可口可乐": ("全球碳酸饮料品牌可口可乐（Coca-Cola），红色飘带与经典瓶身闻名。", "可口可乐是全球最具价值的饮料品牌 IP。", "可口可乐以秘方叙事、红色视觉与节日营销构建跨越百年的品牌 IP，授权覆盖服装、收藏与主题零售。", ["Coca-Cola", "Coke"]),
    "苹果": ("科技公司品牌苹果（Apple），以 iPhone、Mac 等产品著称的消费电子 IP。", "苹果是全球最具价值的科技品牌 IP。", "苹果以极简设计与生态闭环成为科技生活方式符号，品牌授权与联名高度克制却极具溢价。", ["Apple", "iPhone"]),
    "迪士尼": ("娱乐传媒品牌迪士尼（Disney），涵盖动画、乐园与影视的综合性 IP 帝国。", "迪士尼是全球最大的娱乐品牌 IP 之一。", "迪士尼以米老鼠起家，构建动画、电影、乐园与流媒体的超级 IP 矩阵，是授权变现最成熟的品牌 IP。", ["Disney", "The Walt Disney Company"]),
    "耐克": ("运动品牌耐克（Nike），以勾形标志与「Just Do It」著称。", "耐克是全球领先的运动品牌 IP。", "耐克以运动员代言、限量鞋款与潮流联名成为运动文化符号，授权与跨界合作极为活跃。", ["Nike"]),
    "乐高": ("拼插玩具品牌乐高（LEGO），以彩色积木构建一切。", "乐高是全球最具影响力的玩具品牌 IP。", "乐高以模块化积木与 IP 联名套装（星球大战、漫威等）成为跨龄玩具 IP，并衍生电影与主题乐园。", ["LEGO"]),
    "故宫": ("明清皇家宫殿与文化品牌故宫（故宫博物院），以文物与宫廷文化为核心的文旅 IP。", "故宫是中国最具代表性的文化遗产品牌 IP。", "故宫以「故宫文创」「故宫猫」等年轻化运营将六百年宫廷文化转化为爆款文旅与授权 IP。", ["Forbidden City", "Palace Museum"]),
    "泡泡玛特": ("潮玩品牌泡泡玛特（POP MART），以盲盒玩偶（Molly、Labubu 等）引爆收藏经济。", "泡泡玛特是潮玩盲盒头部品牌 IP。", "泡泡玛特以盲盒机制与艺术家联名构建潮玩 IP 矩阵，是「收集式情绪消费」的现象级品牌。", ["POP MART"]),
    # ---- 地标 IP ----
    "埃菲尔铁塔": ("法国巴黎地标埃菲尔铁塔（Eiffel Tower），1889 年世博会钢铁纪念碑。", "埃菲尔铁塔是巴黎的城市地标 IP。", "埃菲尔铁塔以浪漫符号与夜间灯光秀成为法国文旅名片，授权覆盖纪念品、影视与城市营销。", ["Eiffel Tower"]),
    "长城": ("中国古代军事防御工程长城，横亘北疆的世界文化遗产地标。", "长城是中华文明的象征性地标 IP。", "长城以雄伟体魄与历史厚度成为国家文化符号，授权与文旅联动极广，是中外认知度最高的中国地标。", ["Great Wall"]),
    "故宫博物院": ("故宫博物院（见「故宫」），明清皇宫与顶级博物馆文旅 IP。", "故宫博物院是中国第一文旅地标 IP。", "故宫博物院以宫殿建筑与百万文物支撑研学、文创与数字展览，是文旅 IP 化的典范。", ["Palace Museum"]),
    "自由女神像": ("美国纽约地标自由女神像（Statue of Liberty），法国赠美的启蒙象征。", "自由女神像是美国的象征性地标 IP。", "自由女神像以火炬与移民叙事成为美国自由符号，授权广泛用于文旅纪念与城市品牌。", ["Statue of Liberty"]),
    "巴黎": ("法国首都巴黎（Paris），以艺术、时尚与地标著称的城市文旅 IP。", "巴黎是全球最具魅力的城市 IP 之一。", "巴黎以埃菲尔铁塔、卢浮宫与时尚产业构成强文旅 IP，授权与城市营销价值极高。", ["Paris"]),
    # ---- 美食 IP ----
    "北京烤鸭": ("北京传统名菜北京烤鸭，以果木烤制、片皮蘸酱为特色。", "北京烤鸭是中华美食的代表性 IP。", "北京烤鸭以全聚德等老字号与宴飨礼仪成为中餐出海的旗舰美食 IP，授权与预制菜延展活跃。", ["Peking Duck", "Beijing Roast Duck"]),
    "茅台": ("贵州仁怀产酱香型白酒茅台，中国高端白酒代表品牌。", "茅台是中国高端白酒的标杆品牌 IP。", "茅台以产区稀缺性与社交货币属性成为白酒 IP 顶点，品牌授权与文旅（茅台镇）联动深厚。", ["Moutai", "Kweichow Moutai"]),
    "寿司": ("日本传统料理寿司（Sushi），以醋饭与生鱼为核心的料理 IP。", "寿司是日本料理的全球化名片 IP。", "寿司以极致刀工与食材哲学成为日料代表 IP，授权门店与预制食品遍布全球。", ["Sushi"]),
    # ---- 吉祥物与形象 IP ----
    "冰墩墩": ("北京 2022 冬奥会吉祥物冰墩墩，冰糖外壳熊猫形象。", "冰墩墩是冬奥顶流吉祥物 IP。", "冰墩墩以「丑萌」熊猫引爆抢购与二创，是大型赛事吉祥物商业化最成功的案例之一。", ["Bing Dwen Dwen"]),
    "Hello Kitty": ("三丽鸥无嘴白猫女孩形象 Hello Kitty，全球授权商品之王。", "Hello Kitty 是三丽鸥最成功的吉祥物 IP。", "Hello Kitty 以极简无嘴设计适配一切授权商品，是「角色经济」与可爱文化的标志性 IP。", ["Hello Kitty"]),
    "Line Friends": ("라인프렌즈（Line Friends），聊天软件 Line 的布朗熊、可妮兔等表情包形象 IP。", "Line Friends 是表情包衍生的潮玩 IP。", "Line Friends 以表情包角色延展线下门店、潮玩与联名，是社媒形象 IP 化的代表。", ["Line Friends", "布朗熊"]),
    # ---- 非遗与传统手工艺 IP ----
    "京剧": ("中国戏曲剧种京剧，以脸谱、唱腔与程式化表演著称的非遗 IP。", "京剧是中国戏曲的代表性非遗 IP。", "京剧以生旦净丑与脸谱符号成为非遗传播与国潮联名的核心素材，承载传统舞台艺术 IP 化。", ["Peking Opera", "Jingju"]),
    "苏绣": ("江苏苏州传统刺绣苏绣，以精细针法闻名的国家级非遗 IP。", "苏绣是中国四大名绣之首的非遗 IP。", "苏绣以双面绣与文人审美成为高端工艺礼品与文旅授权 IP，推动传统手工艺当代转化。", ["Su Embroidery"]),
    # ---- 赛事 IP ----
    "奥运会": ("国际综合性体育赛事奥林匹克运动会（Olympics），以五环与圣火为符号。", "奥运会是全球最具影响力的体育赛事 IP。", "奥运会以国家荣誉与城市营销构建超大型赛事 IP，授权、赞助与吉祥物经济极为成熟。", ["Olympics", "Olympic Games"]),
    "世界杯": ("国际足联世界杯（FIFA World Cup），全球关注度最高的足球赛事 IP。", "世界杯是足坛第一赛事 IP。", "世界杯以四年一度的全民狂欢与东道主文旅红利成为商业价值最高的单项赛事 IP。", ["FIFA World Cup"]),
    # ---- 艺术与文物 IP ----
    "蒙娜丽莎": ("达·芬奇名画《蒙娜丽莎》（Mona Lisa），卢浮宫镇馆之宝。", "《蒙娜丽莎》是全球最知名的 artworks IP。", "《蒙娜丽莎》以神秘微笑成为艺术 IP 顶流，授权与二次创作横跨时尚、文创与数字艺术。", ["Mona Lisa"]),
    "敦煌壁画": ("甘肃敦煌莫高窟壁画与彩塑群，丝路佛教艺术宝库。", "敦煌壁画是中华艺术的非遗级 IP。", "敦煌壁画以飞天、藻井等视觉符号成为国潮文创与数字展览的富矿 IP，授权与研学价值极高。", ["Dunhuang Murals"]),
}


def leaf_and_parent(cat):
    segs = [p.strip() for p in (cat or "").split(SEP) if p.strip()]
    if "IP 分类标签" in segs:
        i = segs.index("IP 分类标签")
        segs = segs[i + 1:]
    leaf = segs[-1] if segs else "IP"
    parent = segs[-2] if len(segs) >= 2 else (segs[0] if segs else "IP")
    top = segs[0] if segs else "IP"
    return leaf, parent, top


def templated(cat, name, aliases):
    leaf, parent, top = leaf_and_parent(cat)
    definition = f"{name}是融合世界标签体系「{top}」大类下、「{parent}」体系中的「{leaf}」类 IP 实例，作为具体可识别的 IP 资产存在。"
    intro = f"{name}是「{leaf}」类别中的一个具体 IP 实例。"
    desc = f"{name}归入「{leaf}」（上级分类：{parent}），可在内容创作、授权衍生与跨媒介运营中作为独立 IP 资产被识别与调用。"
    if aliases:
        desc += f"常见别名/英文名：{'、'.join(aliases)}。"
    return definition, intro, desc


def apply(branch_filter=None):
    n_authored = n_tmpl = n_skip = 0
    for it in insts:
        cat = it.get("category", "")
        if "IP 分类标签" not in cat:
            n_skip += 1
            continue
        if branch_filter and branch_filter not in cat:
            continue
        # 已有富文本（如虚构角色 IP 的 curated/templated）→ 保留
        if it.get("source") in ("curated", "templated") and (it.get("definition") or it.get("intro")):
            n_skip += 1
            continue
        name = it.get("name")
        existing_alias = it.get("aliases") or []
        if name in MODEL_KB:
            d, intro, desc, al = MODEL_KB[name]
            # MODEL_KB 别名优先；无则保留已有别名
            final_al = list(al) if al else list(existing_alias)
            it["definition"] = d
            it["intro"] = intro
            it["desc"] = desc
            it["aliases"] = final_al
            it["source"] = "curated"
            n_authored += 1
        else:
            d, intro, desc = templated(cat, name, existing_alias)
            it["definition"] = d
            it["intro"] = intro
            it["desc"] = desc
            # 不覆盖已有别名
            if existing_alias and "aliases" not in it:
                it["aliases"] = existing_alias
            it["source"] = "templated"
            n_tmpl += 1
    return n_authored, n_tmpl, n_skip


branch = None
for a in sys.argv:
    if a.startswith("--branch"):
        branch = a.split("=", 1)[1] if "=" in a else sys.argv[sys.argv.index(a) + 1]
        break

n_authored, n_tmpl, n_skip = apply(branch)
scope = branch or "全部 IP 分支"
print(f"生成范围: {scope}")
print(f"  模型手写(精确/curated) = {n_authored}")
print(f"  接地模板(归类/templated) = {n_tmpl}")
print(f"  跳过(已有富文本/非IP)   = {n_skip}")

if "--write" not in sys.argv:
    print("（未加 --write，仅预览。加 --write 写回 data/instances_meta.json）")
else:
    doc["meta"]["source"] = ("data/taxonomy.json（实例名） + 本脚本模型生成实例 KB"
                             "（MODEL_KB 手写知名实体 + 长尾接地模板；与 gen_role_intros 互补）")
    doc["meta"]["generated_at"] = datetime.datetime.now().isoformat(timespec="seconds")
    enriched = sum(1 for i in insts if i.get("source") in ("curated", "templated"))
    doc["meta"]["stats"] = {"instances": len(insts), "instances_enriched": enriched}
    with open(META, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=1)
    print("已写回:", META, f"（富化实例总数={enriched}）")
