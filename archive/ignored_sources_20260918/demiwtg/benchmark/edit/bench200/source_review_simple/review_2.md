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
本轮允许简单场景与简单编辑：单一清晰主体、简单背景、没有复杂后果载体均不构成拒收理由。只需能提出一个明确可执行、可核验的改色/增加/删除/替换/提取等操作，并有具体保持内容。不要为通过而虚报复杂度；复杂度低可以正常接收。

逐张处理下面 12 张图片，将每图一个完整 JSON 对象按行写入 /yzp/zhaozy/yangzepeng/0905/demiwtg/benchmark/edit/bench200/source_review_simple/review_2.jsonl。最终只回复该文件路径。
{"qid": "e080", "instance": "戛纳金棕榈奖", "sha256": "5367f5f74941b5fb39dd0c823d272bd884264bd8436f6e4ea15aecb448d3f5de", "path": "/yzp/zhaozy/yangzepeng/0905/_staging/benchmark/t2i/data/images_v60_r11/60091_戛纳金棕榈奖_5367f5f7.jpg"}
{"qid": "e055", "instance": "外婆家", "sha256": "e6b9f72546b2c6d2f6877af9f41c0f0f6cec5d8b602f14722deb1dc97f249d73", "path": "/yzp/zhaozy/yangzepeng/0905/_staging/benchmark/t2i/data/images_v60_r11/60065_外婆家_e6b9f725.jpg"}
{"qid": "e149", "instance": "小红书", "sha256": "6a04697ff823962fe29ef9b093f1b65cacfd3d470d469371a86551f692e2046f", "path": "/yzp/zhaozy/yangzepeng/0905/_staging/benchmark/t2i/data/images_v60_r11/60157_小红书_6a04697f.jpg"}
{"qid": "e052", "instance": "龙井茶", "sha256": "480f930fa74997a3ecd51b838ccfe63adddee23ceb6cc51c166cc45284c55e4c", "path": "/yzp/zhaozy/yangzepeng/0905/_staging/benchmark/t2i/data/images_v60_r11/60059_龙井茶_480f930f.png"}
{"qid": "e085", "instance": "电感器", "sha256": "2eb203e265a1f03e35fff07b4142765cb2fa00926a1b624ba8c0e38f7c6471ca", "path": "/yzp/zhaozy/yangzepeng/0905/_staging/benchmark/t2i/data/images_v60_r11/60092_电感器_2eb203e2.png"}
{"qid": "e136", "instance": "摩托罗拉", "sha256": "e7d6b6a3071af7ee900e734c3ff64e0b6226d66e98f95aa64f71cdc1c567046d", "path": "/yzp/zhaozy/yangzepeng/0905/_staging/benchmark/t2i/data/images_v60_r11/60143_摩托罗拉_e7d6b6a3.jpg"}
{"qid": "e063", "instance": "中华鲟", "sha256": "294ad0125ef6d89657a9f6d25cb1a7a6de5db2d5a2b912a6172986e2a1ada3cd", "path": "/yzp/zhaozy/yangzepeng/0905/_staging/benchmark/t2i/data/images_v60_r11/60069_中华鲟_294ad012.jpg"}
{"qid": "e174", "instance": "城市经济学", "sha256": "25c2a8d989bd659871b4fb652f552a6df45f35ccae7d5d4375e75a5b3009bb0e", "path": "/yzp/zhaozy/yangzepeng/0905/_staging/benchmark/t2i/data/images_v60_r11/60189_城市经济学_25c2a8d9.jpg"}
{"qid": "e183", "instance": "邮储银行", "sha256": "4a9a6681dbb7224a0938a820f9b1544fa9e0970ea95d6232d44b6e2afa0360fa", "path": "/yzp/zhaozy/yangzepeng/0905/_staging/benchmark/t2i/data/images_v60_r11/60073_邮储银行_4a9a6681.jpg"}
{"qid": "e138", "instance": "法哲学", "sha256": "b64f9c20f766da5044d8e3225c725c4d2e58c4166fa884ab2f3f7d66164122f9", "path": "/yzp/zhaozy/yangzepeng/0905/_staging/benchmark/t2i/data/images_v60_r11/60180_法哲学_b64f9c20.jpg"}
{"qid": "e168", "instance": "高锰酸钾", "sha256": "e4923063336f1612f7c8a1a9f331071e23723c0b99420a3a8b6b49e6261f29d3", "path": "/yzp/zhaozy/yangzepeng/0905/_staging/benchmark/t2i/data/images_v60_r11/60196_高锰酸钾_e4923063.jpg"}
{"qid": "e076", "instance": "锂", "sha256": "7e59e16809126532f9fac0201a6908522332daeb9bb8ed250a359bee65c805d4", "path": "/yzp/zhaozy/yangzepeng/0905/_staging/benchmark/t2i/data/images_v60_r11/60082_锂_7e59e168.jpg"}