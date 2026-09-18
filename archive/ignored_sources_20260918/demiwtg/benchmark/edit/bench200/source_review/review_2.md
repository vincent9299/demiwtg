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

逐张处理下面 19 张图片，将每图一个完整 JSON 对象按行写入 /yzp/zhaozy/yangzepeng/0905/demiwtg/benchmark/edit/bench200/source_review/review_2.jsonl。最终只回复该文件路径。
{"qid": "e042", "instance": "陕西省（安塞腰鼓）", "sha256": "b170743e8665d1c27fda6ab2d65835bab63eaf35adae75a2466978923b430616", "path": "/yzp/zhaozy/yangzepeng/0905/_staging/benchmark/t2i/data/images_v60_r11/60049_陕西省（安塞腰鼓）_b170743e.jpg"}
{"qid": "e048", "instance": "阿姆斯特丹马拉松", "sha256": "e9e19feb3a5505203142098a55eee571d83846c553cffd9e5d24a8bc0ac58bc5", "path": "/yzp/zhaozy/yangzepeng/0905/_staging/benchmark/t2i/data/images_v60_r11/60054_阿姆斯特丹马拉松_e9e19feb.jpg"}
{"qid": "e028", "instance": "织女星运载火箭", "sha256": "50b35e1b32a011936f5849924394c4e9199846c71c1e068676a527c0944c83f5", "path": "/yzp/zhaozy/yangzepeng/0905/_staging/benchmark/t2i/data/images_v60_r11/60034_织女星运载火箭_50b35e1b.jpg"}
{"qid": "e177", "instance": "米兰大教堂", "sha256": "0ea167b44b90575246ba048494e103d473f5b8bc8f5863b5b55908f14f8361a2", "path": "/yzp/zhaozy/yangzepeng/0905/_staging/benchmark/t2i/data/images_v60_r11/60194_米兰大教堂_0ea167b4.jpg"}
{"qid": "e093", "instance": "洪水", "sha256": "8ffc8edd4e089282ab7c6b04599aabf6441360fe67eaf846898e2dbf7583f263", "path": "/yzp/zhaozy/yangzepeng/0905/_staging/benchmark/t2i/data/images_v60_r11/60104_洪水_8ffc8edd.jpg"}
{"qid": "e145", "instance": "动物博物馆", "sha256": "c2ebb77505ae7b5b28062f79ade829cd22427d138fd38661545c62eec764ff8e", "path": "/yzp/zhaozy/yangzepeng/0905/_staging/benchmark/t2i/data/images_v60_r11/60153_动物博物馆_c2ebb775.jpg"}
{"qid": "e130", "instance": "舰载战斗机", "sha256": "b0be37480da16e9709e13f1c8fde3aceade1a75a4cae2c23c6d1c076eeeb41d6", "path": "/yzp/zhaozy/yangzepeng/0905/_staging/benchmark/t2i/data/images_v60_r11/60151_舰载战斗机_b0be3748.jpg"}
{"qid": "e100", "instance": "油焖大虾", "sha256": "955299d2011f7d8be6274eec3d96e747f13d7ad5d7602fbce3b357747fd87123", "path": "/yzp/zhaozy/yangzepeng/0905/_staging/benchmark/t2i/data/images_v60_r11/60130_油焖大虾_955299d2.webp"}
{"qid": "e075", "instance": "庆丰包子铺", "sha256": "560bf5372412b9d3375710931bfa3d07141833533bc777698daa7a306e328f72", "path": "/yzp/zhaozy/yangzepeng/0905/_staging/benchmark/t2i/data/images_v60_r11/60086_庆丰包子铺_560bf537.jpg"}
{"qid": "e143", "instance": "木寨岭隧道", "sha256": "8fbe89c3d4795b9103a0ee30459453e16791177f1bd40aab66cadfd81159883c", "path": "/yzp/zhaozy/yangzepeng/0905/_staging/benchmark/t2i/data/images_v60_r11/60185_木寨岭隧道_8fbe89c3.webp"}
{"qid": "e164", "instance": "海竿", "sha256": "9074a784453347f1c71958a3b44057936b7af93432bef11808ce3227a8083648", "path": "/yzp/zhaozy/yangzepeng/0905/_staging/benchmark/t2i/data/images_v60_r11/60178_海竿_9074a784.jpg"}
{"qid": "e191", "instance": "白及", "sha256": "814db1900eaf436afdac460e2b1f3791cb53df4297441511f788a7cd5d867e39", "path": "/yzp/zhaozy/yangzepeng/0905/_staging/benchmark/t2i/data/images_v60_r11/60173_白及_814db190.jpg"}
{"qid": "e140", "instance": "金鱼草", "sha256": "7a7a22a7c356f2878f844b53cfc03da1a5d7199f76651606debfba6327476b42", "path": "/yzp/zhaozy/yangzepeng/0905/_staging/benchmark/t2i/data/images_v60_r11/60148_金鱼草_7a7a22a7.webp"}
{"qid": "e084", "instance": "金银花", "sha256": "085ffe4cb02b8b520c492448c1dd4526191308098ef3244818a5dec2f21e5105", "path": "/yzp/zhaozy/yangzepeng/0905/_staging/benchmark/t2i/data/images_v60_r11/60096_金银花_085ffe4c.jpg"}
{"qid": "e148", "instance": "麻辣烫", "sha256": "df8dfd64833e7cc1176bc697ccda0a59cf4fdf8ea55aef5171f34cbefb0dced1", "path": "/yzp/zhaozy/yangzepeng/0905/_staging/benchmark/t2i/data/images_v60_r11/60190_麻辣烫_df8dfd64.jpg"}
{"qid": "e044", "instance": "企鹅", "sha256": "7c712f05c74e28d40a6aeb696be51b8602a7644a7667a874949d7b9946cc25c2", "path": "/yzp/zhaozy/yangzepeng/0905/_staging/benchmark/t2i/data/images_v60_r11/60050_企鹅_7c712f05.jpg"}
{"qid": "e099", "instance": "白族服饰", "sha256": "9c724fed34e7239b3b54945c3b0691fe1a54a90f79f4786f589b255d95bfead1", "path": "/yzp/zhaozy/yangzepeng/0905/_staging/benchmark/t2i/data/images_v60_r11/60107_白族服饰_9c724fed.jpg"}
{"qid": "e054", "instance": "炸鸡", "sha256": "539600117b6ad8f983de5cddab4fd38e171b2020b8d3f997538b2cdb7c39c495", "path": "/yzp/zhaozy/yangzepeng/0905/_staging/benchmark/t2i/data/images_v60_r11/60058_炸鸡_53960011.webp"}
{"qid": "e025", "instance": "乌兰布统草原", "sha256": "112a9e4b14f8f52007b10f827882554ba62b6dc11a5581adca9947bfaa2d743b", "path": "/yzp/zhaozy/yangzepeng/0905/_staging/benchmark/t2i/data/images_v60_r11/60026_乌兰布统草原_112a9e4b.jpg"}