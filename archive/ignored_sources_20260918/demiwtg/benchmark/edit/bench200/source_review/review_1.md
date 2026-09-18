审计这张图片的场景复杂度。按 12 个维度打分（0=完全不成立，1=弱成立或单一，2=显著成立或多项）：
- 实体密度：画面点名级对象数量多、各自可辨
- 细节密度：单对象高部件数或高细节构成
- 交互链：对象间接触、遮挡、受力关系并存
- 过程时刻：事件进行中的瞬时状态
- 环境作用：同一环境对多个对象的差异化作用（雪、光、风、水汽）
- 视点剖示：视角或剖面让内部结构可见
- 规约场景：仪式、赛事、制度场景自带站位、持物、着装规制
- 多实例对比：同类多实例各处不同阶段或变体
- 纵深层次：前中背景多层各带内容与遮挡
- 光照时段：晨昏、人工光对全场景的统一光照作用
- 动态要素：运动物体与瞬时痕迹同框
- 多人物编排：3 人及以上各带角色与动作

再输出以下字段：
- same_class_groups：列出图中全部显著同类多实例组（个数 ≥2 才算一组），每组：{"class": 类名（如"红灯笼"）, "count": 个数, "role": "is_subject"（该图主体实体自身的同类多实例）或 "unrelated"（与主体无关的对象组）, "variants": "identical"（同款复制）或 "varied"（不同状态/阶段/变体）, "arrangement": "row"（排成行或列）/"cluster"（聚堆）/"scattered"（散布）}；无则空数组
- referents：可作为定位参照的显著独立对象（用于"离 X 最近的那只"类指认），列 2~4 个（无则空数组）
- consequence_carriers：图中在场的编辑后果传播载体，从下列枚举多选：["水面倒影", "镜面玻璃反光", "影子投影", "接触叠放", "液体容器", "仪表指示", "可动机构"]（无则空数组）
- suitability：{"has_person": 布尔, "person_count": 整数, "has_animal": 布尔, "subject_separable": 0-2（主体轮廓清晰、与背景可分离）, "background_content": 0-2（背景有独立可辨内容）}
- summary：一句话场景概述，不超过 40 字

只输出一个严格 JSON 对象（无围栏无解释），键名一字不差：
{"scene_sources": {"实体密度": 0, "细节密度": 0, "交互链": 0, "过程时刻": 0, "环境作用": 0, "视点剖示": 0, "规约场景": 0, "多实例对比": 0, "纵深层次": 0, "光照时段": 0, "动态要素": 0, "多人物编排": 0}, "same_class_groups": [{"class": "", "count": 2, "role": "unrelated", "variants": "identical", "arrangement": "row"}], "referents": [], "consequence_carriers": [], "suitability": {"has_person": false, "person_count": 0, "has_animal": false, "subject_separable": 1, "background_content": 1}, "summary": ""}

补充本轮源图适配核验：每个 JSON 对象额外包含 qid、sha256（照抄输入）、source_kind（photo_candidate/non_photo/uncertain）、identity（true/false/null，仅当可见主体与输入实例相符时为true；无法确定具体实体则null）、usable_for_edit（布尔）及review_reason（说明实际可编辑对象、可保持内容或拒收理由）。photo_candidate仅表示外观符合单张实拍且未见明显拼接/渲染线索，不代表已验证相机来源。逐张用view_image看图；场景维度只认像素事实，不能凭实体知识补场景。水印、字幕、拼接、多视图、难以定位的目标均需在review_reason说明。不能为复杂度凑数；图像主体难辨、明显非单张实拍或无法支持明确编辑时usable_for_edit=false。只读取本md及列出的图片，不读取历史评分、其他文件或对话。

逐张处理下面 20 张图片，将每图一个完整 JSON 对象按行写入 /yzp/zhaozy/yangzepeng/0905/demiwtg/benchmark/edit/bench200/source_review/review_1.jsonl。最终只回复该文件路径。
{"qid": "e103", "instance": "奔牛节", "sha256": "bac6f9366962fc8650125371019950834d267c8bfa76f6be0cc6fc5ead773d8d", "path": "/yzp/zhaozy/yangzepeng/0905/_staging/benchmark/t2i/data/images_v60_r11/60113_奔牛节_bac6f936.jpg"}
{"qid": "e187", "instance": "华阴老腔", "sha256": "033f3c67f2ede5b4f3d78e14b4f00240132f3accab260106f5c86db155da5aab", "path": "/yzp/zhaozy/yangzepeng/0905/_staging/benchmark/t2i/data/images_v60_r11/60083_华阴老腔_033f3c67.jpg"}
{"qid": "e139", "instance": "技巧运动", "sha256": "a1c66bd7bb77e4ff7f55cff7a498f1b7f3870159b9ff5ee1a927172f28961322", "path": "/yzp/zhaozy/yangzepeng/0905/_staging/benchmark/t2i/data/images_v60_r11/60147_技巧运动_a1c66bd7.jpg"}
{"qid": "e151", "instance": "瓦伦西亚斗牛场", "sha256": "4d8689d8edc5c4952e3ca94e7cb52392f1e9d79e81762785a8a583c640f316a9", "path": "/yzp/zhaozy/yangzepeng/0905/_staging/benchmark/t2i/data/images_v60_r11/60176_瓦伦西亚斗牛场_4d8689d8.jpg"}
{"qid": "e200", "instance": "肖肖尼瀑布", "sha256": "1882ef66f40c0776752e1f83620e4f4cb05aac02c6155c97ef1aae0cbf1cdd2b", "path": "/yzp/zhaozy/yangzepeng/0905/_staging/benchmark/t2i/data/images_v60_r11/60201_肖肖尼瀑布_1882ef66.jpg"}
{"qid": "e040", "instance": "沃尔沃中国公开赛", "sha256": "1034b17aa60ff2f493554f0cb712c10540339dd6b557f802eff19c1d5a1811e2", "path": "/yzp/zhaozy/yangzepeng/0905/_staging/benchmark/t2i/data/images_v60_r11/60046_沃尔沃中国公开赛_1034b17a.png"}
{"qid": "e107", "instance": "挑战者号航天飞机", "sha256": "ebd9db8585ffbc5a397be5b0859d488cdfb58200c031beb2fa9b28a0664045a1", "path": "/yzp/zhaozy/yangzepeng/0905/_staging/benchmark/t2i/data/images_v60_r11/60117_挑战者号航天飞机_ebd9db85.jpg"}
{"qid": "e165", "instance": "洞穴潜水", "sha256": "529c2b0a516c90724c9c782ed8b33785badcfda4c17ffd3673ef0f5f2cfcb773", "path": "/yzp/zhaozy/yangzepeng/0905/_staging/benchmark/t2i/data/images_v60_r11/60191_洞穴潜水_529c2b0a.jpg"}
{"qid": "e083", "instance": "鱼露", "sha256": "6600d37202fe7a74368d3ecf541f38456268dd95915ec07964b27303a20d8547", "path": "/yzp/zhaozy/yangzepeng/0905/_staging/benchmark/t2i/data/images_v60_r11/60094_鱼露_6600d372.jpg"}
{"qid": "e033", "instance": "个园", "sha256": "689012cd2c70efd888aa5e1c6e57f8fa6952dad230dd6b1eeb942f60cb712f92", "path": "/yzp/zhaozy/yangzepeng/0905/_staging/benchmark/t2i/data/images_v60_r11/60039_个园_689012cd.jpg"}
{"qid": "e182", "instance": "昆曲", "sha256": "4643337d791f0dc2f8888ff5e75cc920c93244cb814050f67f5c0296fcc48ff3", "path": "/yzp/zhaozy/yangzepeng/0905/_staging/benchmark/t2i/data/images_v60_r11/60037_昆曲_4643337d.webp"}
{"qid": "e074", "instance": "巴金故居", "sha256": "4e6709350b677b91fba854007b5a883df8b82d007f9b79638a55d69df153afb2", "path": "/yzp/zhaozy/yangzepeng/0905/_staging/benchmark/t2i/data/images_v60_r11/60084_巴金故居_4e670935.jpg"}
{"qid": "e181", "instance": "东湖生态旅游景区", "sha256": "a5254cfb1d8251a23fc7630a8a3ea8817f3e31d66ab7e6114c21a037a49415ca", "path": "/yzp/zhaozy/yangzepeng/0905/_staging/benchmark/t2i/data/images_v60_r11/60021_东湖生态旅游景区_a5254cfb.jpg"}
{"qid": "e185", "instance": "拉格比公学", "sha256": "803d29e0076dd3aedf531e6a5ec670fd3172e10993658795f38deec373da08f7", "path": "/yzp/zhaozy/yangzepeng/0905/_staging/benchmark/t2i/data/images_v60_r11/60055_拉格比公学_803d29e0.jpg"}
{"qid": "e034", "instance": "高原", "sha256": "4719c990d969a32c3513ca730dfc038bb6a8cf693374a1423ee79a09d65a4033", "path": "/yzp/zhaozy/yangzepeng/0905/_staging/benchmark/t2i/data/images_v60_r11/60035_高原_4719c990.jpg"}
{"qid": "e064", "instance": "石花洞", "sha256": "3a4dc274baa306cdc63d8b1ef4625d701e4d9e3e11584944a3248ff028a49654", "path": "/yzp/zhaozy/yangzepeng/0905/_staging/benchmark/t2i/data/images_v60_r11/60068_石花洞_3a4dc274.jpg"}
{"qid": "e065", "instance": "哈巴雪山", "sha256": "10b12602fe0c71757b27f0dc8f3acb1e25a9d9613a603cc8ce443e282b037935", "path": "/yzp/zhaozy/yangzepeng/0905/_staging/benchmark/t2i/data/images_v60_r11/60075_哈巴雪山_10b12602.jpg"}
{"qid": "e079", "instance": "扇贝", "sha256": "93d4eef0dbed760f9c342c721cf65a5c22dbe5ea009031745faf91369d4c985c", "path": "/yzp/zhaozy/yangzepeng/0905/_staging/benchmark/t2i/data/images_v60_r11/60093_扇贝_93d4eef0.png"}
{"qid": "e132", "instance": "祈祷手势", "sha256": "c703991a24d378e268432bb2e2ae7e4aabc31bd47185de0b6737b8e34649a9f4", "path": "/yzp/zhaozy/yangzepeng/0905/_staging/benchmark/t2i/data/images_v60_r11/60142_祈祷手势_c703991a.jpg"}
{"qid": "e066", "instance": "墨西哥卷饼（Burrito）", "sha256": "de10196d000a60e2b1c1063cacefaf325bbb1a9e6078c51501ae50bca4953e9f", "path": "/yzp/zhaozy/yangzepeng/0905/_staging/benchmark/t2i/data/images_v60_r11/60076_墨西哥卷饼（Burrito）_de10196d.jpg"}