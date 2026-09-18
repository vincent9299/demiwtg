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

逐张处理下面 12 张图片，将每图一个完整 JSON 对象按行写入 /yzp/zhaozy/yangzepeng/0905/demiwtg/benchmark/edit/bench200/source_review_simple/review_0.jsonl。最终只回复该文件路径。
{"qid": "e129", "instance": "哈利法塔", "sha256": "282b650847021e2e917aa79acf75589752a6ab66dedd5549088fb58e176e9bde", "path": "/yzp/zhaozy/yangzepeng/0905/_staging/benchmark/t2i/data/images_v60_r11/60165_哈利法塔_282b6508.png"}
{"qid": "e141", "instance": "蜻蜓", "sha256": "8e6f0b92fbed9ec55b8082c492acb395420ddae57bff67c9f1b1b44e127d9642", "path": "/yzp/zhaozy/yangzepeng/0905/_staging/benchmark/t2i/data/images_v60_r11/60166_蜻蜓_8e6f0b92.jpg"}
{"qid": "e072", "instance": "棉子糖", "sha256": "8236e81f873aa5f08433309c9d043111b19c568c9dc8fc17e5b8fdf4c46969f2", "path": "/yzp/zhaozy/yangzepeng/0905/_staging/benchmark/t2i/data/images_v60_r11/60079_棉子糖_8236e81f.jpg"}
{"qid": "e057", "instance": "特斯拉", "sha256": "02bcf28e0cb4034dcc5e4fad0941dae66f8c947d98f3211b18ffb0e070e580ad", "path": "/yzp/zhaozy/yangzepeng/0905/_staging/benchmark/t2i/data/images_v60_r11/60062_特斯拉_02bcf28e.jpg"}
{"qid": "e096", "instance": "芭蕾舞盘发", "sha256": "8ee87f5b92ee5f3ba88577f033a3d12f1c1347d28d1f7118155869b73df24e9d", "path": "/yzp/zhaozy/yangzepeng/0905/_staging/benchmark/t2i/data/images_v60_r11/60125_芭蕾舞盘发_8ee87f5b.jpg"}
{"qid": "e199", "instance": "Cyberdyne HAL 外骨骼", "sha256": "df50fb190c9ba4363772bb62ef8ee973de29c5c2612de463738361856499563d", "path": "/yzp/zhaozy/yangzepeng/0905/_staging/benchmark/t2i/data/images_v60_r11/60202_Cyberdyne_HAL_外骨骼_df50fb19.jpg"}
{"qid": "e049", "instance": "爱尔博", "sha256": "1de885a3aec0981d5923c4eabdd9c0a69bae9443b790c88517242f9dbc67f1b2", "path": "/yzp/zhaozy/yangzepeng/0905/_staging/benchmark/t2i/data/images_v60_r11/60053_爱尔博_1de885a3.jpg"}
{"qid": "e104", "instance": "花笼", "sha256": "f67d9a88d4009189a1158ec1afd5540a1a8cc042d5f5c36e7d1ef66f810d6a1d", "path": "/yzp/zhaozy/yangzepeng/0905/_staging/benchmark/t2i/data/images_v60_r11/60114_花笼_f67d9a88.webp"}
{"qid": "e186", "instance": "空心砖", "sha256": "0c496da44249bc43e6e42254b533faa1ea37698b313ae9dd7cb382e4a6050edf", "path": "/yzp/zhaozy/yangzepeng/0905/_staging/benchmark/t2i/data/images_v60_r11/60197_空心砖_0c496da4.jpg"}
{"qid": "e026", "instance": "加拿大元", "sha256": "f56f00085c38e0fdca408defe01bf02d7148d821468a372682d432ae9aa3a8dd", "path": "/yzp/zhaozy/yangzepeng/0905/_staging/benchmark/t2i/data/images_v60_r11/60027_加拿大元_f56f0008.jpg"}
{"qid": "e159", "instance": "萧邦", "sha256": "7ca9420075b65652844660f02711323c38baefd0dfd795a9246514e2a6e99f0e", "path": "/yzp/zhaozy/yangzepeng/0905/_staging/benchmark/t2i/data/images_v60_r11/60169_萧邦_7ca94200.gif"}
{"qid": "e157", "instance": "一字螺丝刀", "sha256": "be7dabba4abbc25dc877a9a45e5f56342988722666ed4a456c5f35378a83f69e", "path": "/yzp/zhaozy/yangzepeng/0905/_staging/benchmark/t2i/data/images_v60_r11/60167_一字螺丝刀_be7dabba.png"}