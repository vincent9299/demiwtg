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

逐张处理下面 20 张图片，将每图一个完整 JSON 对象按行写入 /yzp/zhaozy/yangzepeng/0905/demiwtg/benchmark/edit/bench200/source_review/review_0.jsonl。最终只回复该文件路径。
{"qid": "e119", "instance": "斯图加特公开赛", "sha256": "02584320255d578d79a56049596d9f8832e6bd2652f1d09042001451753e1e4c", "path": "/yzp/zhaozy/yangzepeng/0905/_staging/benchmark/t2i/data/images_v60_r11/60127_斯图加特公开赛_02584320.jpg"}
{"qid": "e077", "instance": "马拉松", "sha256": "3bec3a80ebbbb6f9c79388b734de48ee022ab42694abe36ae81c4b19af8c4471", "path": "/yzp/zhaozy/yangzepeng/0905/_staging/benchmark/t2i/data/images_v60_r11/60095_马拉松_3bec3a80.jpg"}
{"qid": "e123", "instance": "TED演讲", "sha256": "d80ea31871e9ca6705e05fec22aed22293395f34e87b5c79bdc21065869aa7a1", "path": "/yzp/zhaozy/yangzepeng/0905/_staging/benchmark/t2i/data/images_v60_r11/60160_TED演讲_d80ea318.jpg"}
{"qid": "e124", "instance": "桑给巴尔群岛", "sha256": "36daab987259e50be1983c900a2c4021709653e05f42b777cf74fcad90ed6248", "path": "/yzp/zhaozy/yangzepeng/0905/_staging/benchmark/t2i/data/images_v60_r11/60134_桑给巴尔群岛_36daab98.jpg"}
{"qid": "e118", "instance": "商业广场", "sha256": "acc480a4c5801a9e177bcbeea0525f122e4463916ab9cb2ee3835fe8b389fb2e", "path": "/yzp/zhaozy/yangzepeng/0905/_staging/benchmark/t2i/data/images_v60_r11/60129_商业广场_acc480a4.jpg"}
{"qid": "e094", "instance": "铜陵长江大桥", "sha256": "c4c5e9a1574d3c8e65655009eab5fe000b13c79933e09e3e5105be2e13df2428", "path": "/yzp/zhaozy/yangzepeng/0905/_staging/benchmark/t2i/data/images_v60_r11/60102_铜陵长江大桥_c4c5e9a1.webp"}
{"qid": "e097", "instance": "蒂芙尼", "sha256": "3138ae23b2c9d78f184902986de647b07a641bc2a214927c23070e5b6c17dc73", "path": "/yzp/zhaozy/yangzepeng/0905/_staging/benchmark/t2i/data/images_v60_r11/60108_蒂芙尼_3138ae23.jpg"}
{"qid": "e153", "instance": "剑舞", "sha256": "af6aa3bc8cfca3a70898d2b4d3879589dd9027b8630b2e23c3759cb748f2cef2", "path": "/yzp/zhaozy/yangzepeng/0905/_staging/benchmark/t2i/data/images_v60_r11/60164_剑舞_af6aa3bc.png"}
{"qid": "e192", "instance": "百得胜", "sha256": "d82710e2bdd7446b973e422018b595abebca362a81cbeaba5a26f6cfe9aefd9c", "path": "/yzp/zhaozy/yangzepeng/0905/_staging/benchmark/t2i/data/images_v60_r11/60090_百得胜_d82710e2.jpg"}
{"qid": "e032", "instance": "金庙（阿姆利则）", "sha256": "861454998ef30d1a9282a0ed953a4b4c0b9e0d553776da02308798e5f96378db", "path": "/yzp/zhaozy/yangzepeng/0905/_staging/benchmark/t2i/data/images_v60_r11/60032_金庙（阿姆利则）_86145499.jpg"}
{"qid": "e166", "instance": "穿山甲", "sha256": "4cd0128f7793df7d6aecf26733492f3567f8e873cc500d27dc4af57c8013b9e9", "path": "/yzp/zhaozy/yangzepeng/0905/_staging/benchmark/t2i/data/images_v60_r11/60177_穿山甲_4cd0128f.jpg"}
{"qid": "e095", "instance": "水利工程", "sha256": "67fd15420773ead2a3c9eaca8251588098a973d6e74863b82aa452a3fc9fccae", "path": "/yzp/zhaozy/yangzepeng/0905/_staging/benchmark/t2i/data/images_v60_r11/60111_水利工程_67fd1542.webp"}
{"qid": "e053", "instance": "菲律宾酸汤", "sha256": "4980253a1f10031403bfaea1af00f34a25ffd6b1c2d69fa28b0426775b35a714", "path": "/yzp/zhaozy/yangzepeng/0905/_staging/benchmark/t2i/data/images_v60_r11/60057_菲律宾酸汤_4980253a.jpg"}
{"qid": "e147", "instance": "莜面栲栳栳", "sha256": "0da069d0962f3ef0748d14d92f17c3fcd7a41dca9332b04a6471e223f21df993", "path": "/yzp/zhaozy/yangzepeng/0905/_staging/benchmark/t2i/data/images_v60_r11/60159_莜面栲栳栳_0da069d0.jpg"}
{"qid": "e194", "instance": "再力花", "sha256": "509eab6ae1bd54d9e46d1e8fe0c5bb404e5dbd5d410fd008c9275c7514fa68b4", "path": "/yzp/zhaozy/yangzepeng/0905/_staging/benchmark/t2i/data/images_v60_r11/60161_再力花_509eab6a.jpg"}
{"qid": "e111", "instance": "资生堂", "sha256": "4fb3b55937d06da97480c3a30e05c505d5f6b6914ec6e31d35512caaafb64935", "path": "/yzp/zhaozy/yangzepeng/0905/_staging/benchmark/t2i/data/images_v60_r11/60126_资生堂_4fb3b559.jpg"}
{"qid": "e152", "instance": "七星瓢虫", "sha256": "44b725064d05251f0290b60d3dd9b45ead5954dc076f5dfa09227fba7200ea3a", "path": "/yzp/zhaozy/yangzepeng/0905/_staging/benchmark/t2i/data/images_v60_r11/60195_七星瓢虫_44b72506.jpg"}
{"qid": "e041", "instance": "金蟾", "sha256": "5eefde084d97928abbfabbcbd653f520c89daf52a2412eeeb1f5b0a2338ac185", "path": "/yzp/zhaozy/yangzepeng/0905/_staging/benchmark/t2i/data/images_v60_r11/60043_金蟾_5eefde08.jpg"}
{"qid": "e196", "instance": "聚甲醛", "sha256": "01bb5a8649284ec75b4f8fc64e47bbc2fdcf17a9bbe7f18db7a149bbacb79143", "path": "/yzp/zhaozy/yangzepeng/0905/_staging/benchmark/t2i/data/images_v60_r11/60115_聚甲醛_01bb5a86.jpg"}
{"qid": "e086", "instance": "红蝉", "sha256": "293bfdb26f6a012ee7cfd97c3f04225a2d5a90c5a8af563bbf832fde1e58988d", "path": "/yzp/zhaozy/yangzepeng/0905/_staging/benchmark/t2i/data/images_v60_r11/60105_红蝉_293bfdb2.jpg"}