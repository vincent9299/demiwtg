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

逐张处理下面 12 张图片，将每图一个完整 JSON 对象按行写入 /yzp/zhaozy/yangzepeng/0905/demiwtg/benchmark/edit/bench200/source_review_simple/review_1.jsonl。最终只回复该文件路径。
{"qid": "e169", "instance": "白花芍药", "sha256": "3d9bb3f5c05fefd3aecf587f5a9b05231838aac39d74c37af32550bb4391f2d9", "path": "/yzp/zhaozy/yangzepeng/0905/_staging/benchmark/t2i/data/images_v60_r11/60183_白花芍药_3d9bb3f5.png"}
{"qid": "e039", "instance": "天津站", "sha256": "5f5173e74dc64f6757d08c13dfafb1f7f53ab0554ed34c8611250d2b85690ecf", "path": "/yzp/zhaozy/yangzepeng/0905/_staging/benchmark/t2i/data/images_v60_r11/60045_天津站_5f5173e7.png"}
{"qid": "e158", "instance": "瓶式台球", "sha256": "20bd548282b40ade36aba819069d6ce7f3d8acc97832bfbbf345e306659e0190", "path": "/yzp/zhaozy/yangzepeng/0905/_staging/benchmark/t2i/data/images_v60_r11/60200_瓶式台球_20bd5482.jpg"}
{"qid": "e058", "instance": "汉谟拉比法典石柱", "sha256": "fc74f15f1eb77d9df54b5e16cff1dcf51a3e4b9a00e719b7a5c7006486e9a61c", "path": "/yzp/zhaozy/yangzepeng/0905/_staging/benchmark/t2i/data/images_v60_r11/60064_汉谟拉比法典石柱_fc74f15f.jpg"}
{"qid": "e108", "instance": "窗花", "sha256": "19e0f7a59299a8e1e7ebb5961dbedf0d6eb5685c9fd1b99da700afce59615b42", "path": "/yzp/zhaozy/yangzepeng/0905/_staging/benchmark/t2i/data/images_v60_r11/60118_窗花_19e0f7a5.jpg"}
{"qid": "e102", "instance": "豆花米线", "sha256": "8b02c6ee123e815044a7bb4797c7f6b2476c0ce5d62a03fce48e044378acdadc", "path": "/yzp/zhaozy/yangzepeng/0905/_staging/benchmark/t2i/data/images_v60_r11/60112_豆花米线_8b02c6ee.png"}
{"qid": "e193", "instance": "双皮奶", "sha256": "0e4d34482c0075e29ebcf9015dfe94b5a12246e9681981ee3ecc56e0460d53ef", "path": "/yzp/zhaozy/yangzepeng/0905/_staging/benchmark/t2i/data/images_v60_r11/60146_双皮奶_0e4d3448.png"}
{"qid": "e146", "instance": "灯影牛肉", "sha256": "470152afdcd235d41751e77974d7415d69f8054e622b7d85941436b60fc98eb6", "path": "/yzp/zhaozy/yangzepeng/0905/_staging/benchmark/t2i/data/images_v60_r11/60171_灯影牛肉_470152af.png"}
{"qid": "e122", "instance": "绢丝", "sha256": "6a7cf0c2ee71adfc81b1a59760b42f5d70d34dcfe8b12e5f056678308601bd00", "path": "/yzp/zhaozy/yangzepeng/0905/_staging/benchmark/t2i/data/images_v60_r11/60133_绢丝_6a7cf0c2.jpg"}
{"qid": "e106", "instance": "法拉利", "sha256": "09adddc7306757df42675a0c52c8a1e616f6c4c114e319e5b82e87ce4fcb0664", "path": "/yzp/zhaozy/yangzepeng/0905/_staging/benchmark/t2i/data/images_v60_r11/60135_法拉利_09adddc7.png"}
{"qid": "e179", "instance": "伏特加", "sha256": "dfaa1d5d6f78accb5a334c9e417b521437fef4fef6e064b2df588b2ca06bb652", "path": "/yzp/zhaozy/yangzepeng/0905/_staging/benchmark/t2i/data/images_v60_r11/60199_伏特加_dfaa1d5d.jpg"}
{"qid": "e170", "instance": "随笔", "sha256": "0e4300f808e0242462cf86e0e47b3154f53693fee7040a67115e56960e9ad31d", "path": "/yzp/zhaozy/yangzepeng/0905/_staging/benchmark/t2i/data/images_v60_r11/60184_随笔_0e4300f8.jpg"}