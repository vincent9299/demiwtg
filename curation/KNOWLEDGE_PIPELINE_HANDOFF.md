# V2 工作交接：概念多模态知识整理（2026-09-17，当前入口）

> 下个窗口先读本节，再读 AGENTS.md 与 DESIGN 第21—24、69—74节。下方早期交接仅是历史记录，不能用旧默认配置覆盖当前实现。

## notebook运行检查补充（最新）

用户在长驻内核先后遇到BatchRelationshipReviews缺失、BatchImageSelection不接受neutral。磁盘代码正确，但仅刷新单个模块会混用旧类，已撤销热加载补丁。两个notebook初始化会拒绝算子文件晚于内核启动的会话；必须重启内核。玻璃棒每个后续算子cell还检查冻结的代码、demiflow依赖哈希、配置与RUN是否改变，发现变化立即停止。

当前默认RUN：完整pipeline为bench200_sample5_dual_v2（保留用户最新选样配置），玻璃棒为glass_operator_dual_image_v2。旧run不覆盖。不要建议在旧内核只重跑第32步继续；重新加载文件并重启内核。更改notebook后的新run不能假定兼容旧manifest。

本轮验证：141处算子构造参数与实际签名一致；干净内核用真实玻璃棒blocks的63张图执行第32步，得到16批，未调用模型；完整pipeline空输入执行至最终文件，玻璃棒所有代码cell也在独立干净内核用空输入执行通过。旧内核、冻结后改配置拒绝测试通过。验证记录state/curation/notebook_runtime_check_v1/，包括两个执行notebook和validation.json。57项针对性测试通过。以上不替代非空材料完整双模型链、GPU切换及知识正确性验证；本轮新增模型请求0。

## 用户目标与范围

继续本项目 V2 工作：原始datasets → 概念相关文档/图片 → 可追溯图文知识。这里的V2是用户对下一阶段工作的称呼，不等同于历史archive/v2；现役代码仍在curation/v4，不搬目录，不代表已实现V4出题。不自动启动出题或修改评分。

用户要原生demiflow数据流：具体文件显式读取；概念、文档、图片分别处理，必要时关联/聚合。小批只在入口采样过滤，下游与全量共用算子。notebook中直接看到算子和真实中间Dataset，不造StreamFlow/SQLite/隐藏业务调度器，不添加无用展示字段。

最终阅读形式：标题；连贯正文与图片；合并去重的参考来源。不展示审核状态列或独立图片支持列，但内部引用、判断、失败与暂缓信息仍保留。图片由模型按正文匹配和多样性取舍，没有强制5张上限，也不强制全保留。

## 代码与运行入口

- 项目：/yzp/zhaozy/yangzepeng/0905/demiwtg；公共Python：../env/bin/python。
- demiflow：同级../demiflow，独立GitHub仓库vincent9299/demiflow；不能只拉主仓而漏掉其通用接口实现。
- 完整编排：curation/v4/knowledge_debug.ipynb，run_pipeline里可直接看到Dataset链。CLI run_notebook_pipeline.py加载同一个函数和MODEL_CONFIG。
- 逐步调试：curation/v4/glass_operator_debug.ipynb；Jupyter落盘用await checkpoint_async，避免asyncio.run与内核事件循环冲突。
- 业务算子：curation/v4/ops/；提示词：ops/prompts/。state只保存请求、响应、冻结配置、产物，不存新业务代码。
- 新运行目录：state/curation/v4/knowledge_dual_image_v1、glass_operator_dual_image_v1。完整notebook默认只读当前RUN；新RUN无产物时明确提示缺失，不回退历史结果。需要执行时显式选execute模式。

## 当前正式流程

1. 原始概念/文档/图片文件显式读取，入口概念筛选或采样，关联材料。
2. 读取原文、正文清洗、段落质量过滤和可确定的结构修复，保留原文定位及原生图文关联；不使用临时clean_docs作为原始输入。
3. 概念身份检查及正文相关性筛选；图片字节检查、精确重复处理。
4. 图片按概念筛选：Qwen3.8初筛 → Gemma4-31B独立复核含keep图的完整原批次 → 合并判断。模型只收到中性概念身份和像素，没有上游接受理由/图片caption暗示。
5. 原生图文关联优先；文本embedding分组、图文相似度补充关联；按32K输入容量组装，能不分就不分，避免文字组×图片组全组合。
6. joint_paragraphs.yaml的v5提示词联合提炼，形成主题与连贯图文段落，保留来源和引用。
7. 引用/支持核验，按需触发一轮跨批重复、互补、冲突处理和局部修复；保留材料不确定性，不能模型多数票认证。
8. sink保存分层结果，notebook读最终图文。

demiflow通用接口提交：`b5e3f41`（records、local_relational、持久prompt journal及测试）。

## 最近实验与合并结论

- 提炼prompt六版对照：v5在这三例中覆盖与压缩较均衡，已设为默认；不是全局最优、不是知识质量验收。原结果state/curation/v4/prompt_coverage_v5_verified。
- 图片四模型：同114张图片、相同输入，Qwen3.8、Qwen3.6-A3B、Gemma26、Gemma31各29次，共116次调用。Qwen3.8误收温度计，Gemma31误收空心管，单模型没有全面胜者。
- 采用Qwen3.8初筛→Gemma31确认keep。初筛排除/待定不被单方复核救回；有效keep且复核keep才入选；分歧/失败暂缓。复核保留整个原4图批次，不重压批，以保持实验上下文。Gemma看不到Qwen回答。
- 正式新算子：ops/image_filter.py；image_filter_runtime.py管理提示配置与复用校验；local_review_service.py只管理资源，算子编排仍在notebook。
- 四模型原始运行：state/curation/v4/image_filter_models_v1；查看image_filter_models_debug.ipynb。
- 新算子真实旧响应回放：state/curation/v4/dual_image_pipeline_check_v1，114张=56入选/52排除/6暂缓；温度计pending、空心管exclude；29初筛批中28需Gemma复核；零新增模型调用。回放脚本replay_confirm_images.py。
- 26项针对性测试通过；两个notebook schema/所有代码cell编译/CLI加载及两套模型执行上下文构造通过。回放汇总在上述state运行目录。两个现役notebook现在仅保留最新正式pipeline，不再内嵌实验/历史对照。

## 尚未完成及下一步

**新双模型链尚未实跑完整级联/三概念最终知识，新的资源借用管理器未经历实服务完整周期。** 不能把旧响应回放说成端到端通过。

下一窗口优先：核对当前模型服务和预标注状态 → 用新run运行OK手势、玻璃棒、白花芍药三例 → 在notebook对照新旧完整正文和图片。可复用独立清洗checkpoint；不可直接复用旧单模型筛选后的materials绕过新筛选。image_filter_policy.json必须匹配才允许复用筛选后材料，默认REUSE_MATERIALS=None。修改代码/提示词/输入必须新版本，不覆盖旧成功或失败。

重点看：温度计是否在联合提炼之前拦住；空心管不放回；玻璃棒高质量原文是否遗漏、主题是否过碎；白花芍药是否把泛称白色芍药误当Paeonia sterniana。56张入选中仍有物种身份未认证花图，双模型一致不等于认证。初审是Codex暂定参考，不是专家金标准；不能据此声称零错误。

组合在这批要29+28=57次图片请求，近乎单模型两倍，不宣称提速。减少联合提炼分组与改善图片过滤是两个不同目标。暂不扩量，不引入新的NLP模型，不重新泛谈架构。

## 服务、权限与数据边界

原服务localhost:8000/v1，served model qwen3.8-27b；Gemma复核8001。运行前查实际进程，勿复用旧PID。用户已授权研究占用GPU时排空/暂停预标注、临时切换本地模型，并恢复原Qwen命令与预标注；只动明确匹配进程，不用付费网关。

完整复核checkpoint命中不启动Gemma；外部已管理的Gemma服务不由pipeline停止。借用失败必须确认原服务恢复，不能仅删STOP掩盖失败。本次合并轮没有切换服务，收尾只读确认8000健康、STOP不存在。

原始datasets/blobs只读；state、sync、日志、权重不提交GitHub。notebook提交代码和说明。两个现役notebook清空过时输出，修改前含实验/历史输出的完整副本在state/curation/notebook_formal_only_20260917/；其他notebook的执行输出保留在本地原文件；从GitHub新克隆没有本机state与结果图，需另行同步数据或重新运行。历史代码归档、兼容符号链接保留；不要把归档重命名看成误删。

最近验证命令：
```bash
../env/bin/python -m pytest -q curation/v4/test_confirm_images.py curation/v4/test_image_filter_comparison.py curation/v4/test_model_comparison.py curation/v4/test_multimodal.py
../env/bin/python -m curation.archive.manage verify
```

---

# 以下为早期交接与逐轮记录（以顶部最新状态为准）

# 概念知识整理 pipeline 交接（2026-09-16）

## 当前意图

先读 AGENTS.md、curation/DESIGN.md，重点第21—24节长期原则和第60—66节最新实现。上游目标是从datasets采集原始资料得到可追溯的概念多模态知识库，不限适合出题的知识。当前不做V4出题、不改评分、不扩量。用户只看notebook，不再生成独立HTML预览。

用户已要求把玻璃棒清洗实验接入主链：本轮已完成接入、更新两个notebook、做清洗段验证；未重跑模型。下一步先与用户看新清洗中间Dataset，再决定是否执行下游。不能把本轮说成整条知识pipeline验收。

## 从哪里看和运行

项目：`/yzp/zhaozy/yangzepeng/0905/demiwtg`。
公共Python：`/yzp/zhaozy/yangzepeng/0905/env/bin/python`。
清洗对照环境：`/yzp/zhaozy/yangzepeng/0905/env-cleaning/bin/python`（额外jieba、datasketch、resiliparse，仅对照需要）。

- `curation/v4/cleaning_debug.ipynb`：已执行，完整清洗结果。第2节为5份最终正文，后续为DOM字段、未解决碎片、修改前后和排除原因。读取cleaning_glass_v6，保持输出可见。
- `curation/v4/glass_operator_debug.ipynb`：逐算子调试，玻璃棒。新RUN `state/curation/v4/glass_operator_debug_v4`；第9格CleanDocument，第9b格FilterDocumentBlocks，然后图片、身份、相关性、分组、图文提炼等原链。新版尚未执行；旧输出已清空，避免误导。
- `curation/v4/knowledge_debug.ipynb`：完整编排在可见的run_pipeline函数。新RUN `state/curation/v4/knowledge_cleaning_v2`，默认MODE仍view_saved（只读已有下游结果）；execute才执行新链。历史最终结果展示不是新清洗版本运行结果。业务算子在ops/，提示词在ops/prompts/。
- 两个修改前notebook含输出的完整快照：`state/curation/cleaning_pipeline_integration_v1/{knowledge_debug,glass_operator_debug}.ipynb`。
- CLI `curation/v4/run_notebook_pipeline.py`加载notebook真实run_pipeline，没有第二套编排。其历史export分支仍会调用HTML发布器；用户不需要HTML，后续不要直接调用此export入口生成预览，应在notebook查看。此次未触发该入口。

主链继续使用demiflow原生read_records、Dataset join/group/map_cached/map_prompt_async/checkpoint。不要换SQLite、隐藏调度器或重造StreamFlow。demiflow源码在同级demiflow；已安装版本支持checkpoint_async，Jupyter中用await避免asyncio.run冲突。

## 本次接入内容

`ReadDocument → CleanDocument → FilterDocumentBlocks → 文档/图片关联 → 原有身份与后续链`

- CleanDocument：原文解析、正文与图片关联抽取；保留原文与块定位。
- 新 `ops/filter_document_blocks.py:FilterDocumentBlocks`：复用QualityBranches的块质量规则（关闭实验metrics）和RepairSourceBlocks；排除交易/验证码/导航等块、同章节精确去重、修复引用URL和明确导航尾巴，恢复误作链接目录的连贯正文。
- 输出更新clean_text、clean_blocks、clean_counts、clean_status、clean_version、knowledge_eligibility；clean_filter保存document_reason、交易页信号、repairs、unresolved_structure。clean_start/end重新计算；raw_start/end与raw_text不改。source_media保留。
- 规则仍是小批校准的启发式，非“保证不误删”、非FineWeb2完整复现。英文阈值、C4标点删行、MinHash整篇删除等反例不进入生产主链。没有新增小模型。
- 跨文档近似去重不自动整篇删除；原有下游语义整合保留。未把所有对照分支叠加成生产过滤。
- `ReadDOMFields`仍是独立HTML快照检查。明确DOM键值可恢复，旧纯文本字段不猜配对；另抓的新页面不伪装成历史原文，也没有隐式网络请求。

## 实测和验证

新接入验证：`state/curation/cleaning_pipeline_integration_v1/`，manifest.json、documents.jsonl、validation.json。

核对datasets中的原文件后，用公共Python运行原生map_cached(CleanDocument)→map_cached(FilterDocumentBlocks)→checkpoint。14文档、5份非空正文、5629字符，逐篇与cleaning_glass_v6的final_text一致；所有原文/清洗后块位置校验通过，零模型调用。10项测试通过；两个notebook schema、全部cell编译及CLI函数加载通过。没有执行两个完整模型notebook。

测试：
```bash
cd /yzp/zhaozy/yangzepeng/0905/demiwtg
../env/bin/python -m pytest -q curation/v4/test_filter_document_blocks.py curation/v4/test_structure_repair.py
../env-cleaning/bin/python -m pytest -q curation/v4/test_cleaning_trials.py
```

本轮不是全仓测试；旧test_explicit_pipeline中“与旧StreamFlow输出逐字段一致”的断言代表旧行为，新链多了清洗规则和字段，后续修改这类测试应更新预期而不是删去新算子来满足旧等价要求。

## 清洗实验保留的真实问题

`state/curation/cleaning_glass_v1...v6/`均保留。v5曾错误恢复参考文献块，v6加参考章节约束后修正；不得覆盖失败结果。

v6恢复3段带较多链接的有效正文；11次修改；14个内容锚点保留。360 HTML可明确提取5个字段对，独立展示。百度HTML抓取403，7段字段碎片仍无法可靠配对，保留并标注。HTML来自新抓取快照，不能拿它替换历史正文后声称纯清洗提升。

清洗效果尚未跨概念泛化验证，未逐条独立事实核验。原来的DOM候选有18对，但仅5对来自保留文档；不能把所有DOM字段当合格知识。

## 下游历史问题，未被本轮解决

- `state/curation/v4/glass_operator_debug_v3`：此前126代码格执行通过，24次Qwen调用、111446 token，但最终仅2个纯文字主题、0图，质量不合格。
- 局部整合引用不存在的t1_b3/t1_b4、图像观察映射不合法，校验记录invalid_used_mapping/unknown_image_observation，原块因此local_integration_failed。没有放宽校验或伪造引用来修复。
- 玻璃棒原始维基正文丰富，最终过短且割裂，不能只归因清洗；需要继续看分组、联合提炼、跨批整合是否丢失独立章节和互补信息。
- 曾把温度计当玻璃棒、具体植物身份不清等视觉错误；模型同意不等于独立核验。四模型诊断及主题修复看DESIGN第62节。
- 最终展示期望：标题；正文/图片交错；所有参考来源集中去重。无硬性5图上限，由模型保留相关且多样的图。内部保留审核和溯源，最终不展示复杂状态列。

## 图片补下载与环境

玻璃棒73条图片来自legacy images.jsonl，不是QID。此前6张本地、67缺失；已通过demiwtg-data下载并SHA/解码校验补57张，现在63张本地、10张失败（9 quark、1 giphy，SHA不符）。数据清单未改，原图未替换成不匹配字节。历史知识结果不会因补下载自动更新。

详见 `curation/IMAGE_BACKFILL_HANDOFF.md` 和 `state/curation/glass_image_backfill_v1/`。全库补图交给其他会话，别重复启动。模型服务/预标注状态必须实时查看，不沿用历史PID；本轮未调用或重启模型。

## 新会话建议第一步

读取上述文档和两个notebook的当前源代码，先展示第9b步真实输出，确认用户满意后再继续玻璃棒后续算子调试。新输入/代码/模型配置需新RUN，不覆盖旧结果，不盲目复用旧processed_documents。不要重跑所有成功调用来得到“成功”报告，更不要直接扩概念或开始出题。

## 最新进展：三概念端到端已跑完（覆盖前述“未重跑”状态）

用户随后明确要求用最新合并pipeline端到端跑3概念，已完成。运行目录 `state/curation/v4/cleaning_e2e_three_v1`；`knowledge_debug.ipynb`第3节已执行并保存全部最终图文，直接读JSONL、不生成HTML。默认只查看。`glass_operator_debug.ipynb`仍是独立逐算子调试入口，未在本轮逐格执行。

Qwen3.8-27B：182次调用、687265 token，全部正常结束，无缺响应。26原始文档、322图片记录，本地109图可用；23组联合提炼。最终玻璃棒8主题/1209字符/15图，OK手势15主题/3487字符/6图，白花芍药2主题/277字符/1图。运行与质量详情见DESIGN第67节，以及RUN/result_summary.json、quality_review.json。

**不合格，不能直接扩量或出题：**
- 温度计图I6790beeb9453再次被误当玻璃棒，人工像素确认。
- 玻璃棒折射实验重复，形态主题保留11张相似棒状图。
- 白花芍药分类形态段落因quote_not_in_input暂缓，模型判supported不能覆盖定位失败；需定位具体引文问题及避免整段信息损失。最终花图物种未确认。
- 两轮整合后仍4对残留关系（OK手势/玻璃棒各2）。72次关系判断成本较大；第一轮62对全部判需整合，需评估是否过度合并互补但独立内容。
- 全量转换/join/全库未关联审计耗约26分钟才进入模型，完整约68分钟。下一轮评估将全库审计移出小批主线；本轮未跳步。

执行时notebook快照在RUN/knowledge_debug_source_snapshot.ipynb，已保存输入/代码/提示词/完整响应。后续先与用户看真实结果，选择定位问题；不要在旧RUN覆盖提示词或自动重跑全部成功调用。人工本轮读全部最终文字，仅打开3张重点图，未宣称全部事实和图片核验。

## 最新进展：已精简主链（2026-09-17）

用户随后要求精简pipeline。详情以DESIGN第68节为准。两个notebook已更新：读取后用SelectSourceRecords下推同一概念选择，默认不做全库未关联审计；关系候选用BatchRelationshipReviews/ApplyRelationshipReviews及review_relationships.yaml批量判断；主线只有一轮按需整合，无固定第二轮、无末轮只记录问题的模型检查。引用定位、首次像素核验、实际改写后核验、失效主题按需修复仍保留。QID页面映射不提前裁掉，避免掩盖歧义。

默认新RUN：knowledge_slim_v1、glass_operator_slim_v1。完整notebook第3节仍显示上轮结果，明确不是精简后结果；不要覆盖旧run。原notebook快照在state/curation/pipeline_slim_v1/。

已验证：slim_upstream_check_v1从原始数据到gather约100.42秒，原26文档/322图片ID集合、清洗正文均与上轮一致。上轮62关系对规划为8批，容量未截断。17项测试通过，两个notebook编译/独立初始化通过。slim_relationship_probe_v1实际1次Qwen调用完整回答8对，协议均有效，但仍全部判需整合，不能声称语义质量提高。新全链未跑；没有擅自重跑182次。用户后续可在新RUN试验或继续review。

可配置global_material_audit=True开启全库审计（同时关闭下推）；关系容量relationship_batch_pairs=8、relationship_batch_chars=24000。大文件仍需流式扫描，当前没有永久索引；性能数字不是隔离基准，不承诺固定倍数加速。

## 最新进展：精简版已重跑，发现实际内容损失（2026-09-17）

覆盖前述“新全链未跑”：`state/curation/v4/slim_e2e_three_v1` 已完成，124调用/611047 token/约41分钟，旧版182调用/687265 token/约68分钟。原始及清洗、相关性入选正文相同，背景补图新增5张可用，因此不是严格消融实验。

**不合格：**玻璃棒材质尺寸、核心化学用途原已提炼并通过核验，后续两项局部整合映射失败，ApplyLocalIntegration 把原块整体暂缓，最终丢失；应优先定向修复失败回退，不能将改写失败当作原文不可靠，也不能把冲突内容直接放行。白花芍药花期果期首次提炼遗漏；分类形态引文失败是旧问题。OK手势更长但仍重复且丢部分细节。温度计最终未发布不代表识图修复，上游仍误认。

详见DESIGN第69节与RUN/content_review.json、comparison_summary.json、input_comparison.json。已更新并实际执行 knowledge_debug.ipynb 第3节、glass_operator_debug.ipynb 开头的只读对照cell：完整新旧正文/实际图片并排，保存输出，无额外模型调用、无独立HTML。后续逐算子cells未执行；未来调试默认新RUN knowledge_slim_v2 / glass_operator_slim_v2。本次任务只重跑、对照、更新notebook，尚未改写失败回退逻辑或再跑第二轮模型修复。不要将其说成已无损精简。

## 最新：32K容量优先装组已接入，尚未重跑生成

DESIGN第70节。两个notebook新执行使用RouteByTokenBudget，单概念能放入32768输入token（含图片、提示词/引用/协议及256余量）就一次提炼；单组跳过跨组整合，输出另留16384。新RUN为knowledge_token32k_v1/glass_operator_token32k_v1。旧展示不覆盖。

只做冻结材料装组验证：OK手势23.3K/16图、玻璃棒25.8K/28图、白花芍药5.9K/5图，都1组，23→3请求；无新生成调用。结果token32k_planning_v1。超大不可分单元显式报错，更细切分未实现。不能把装得下当质量通过。

用户追问温度计误认是否压缩造成：尚无同图同prompt的隔离对照，不能归因。现有发送最长边1536/JPEG90；如Gemma/Qwen收到同一字节而仅Gemma认出，说明仍有可辨线索，模型能力/提示词也可能造成差异。后续若验证需固定输入和prompt，对当前图/高清图与两模型对照；本轮未自动换服务或启动该实验。

## 最新：32K三概念复用材料重跑已完成

DESIGN第71节。新成功run `token32k_reuse_three_v2`，复用slim_e2e_three_v1完成的清洗/身份/相关性/向量checkpoint，material_reuse.json保存hash及父版本；不复用提炼/核验。v1因tokenizer在线程首次导入失败而停止，零模型调用，已保留；算子改为初始化时加载，v2完成。

约4分17秒、8调用、123036 token（只计复用后段）。联合3次、核验4次、修复1次；没有跨组关系/整合。最终OK手势4主题551字符4图，玻璃棒2主题330字符0图，白花芍药1主题131字符1图。两个notebook已保存新旧实际图文对照。

不合格：OK手势整体提炼严重遗漏；玻璃棒核心功能恢复但材质引文失败、三图漏region，温度计仍被误认；白花芍药花期果期恢复但形态引文仍失败、配图物种仍不确定。不要说32K整体提炼已经解决质量，也别把零图当识图正确。新CLI参数 --reuse-materials 指向父run，notebook run_pipeline同名参数；新代码必须新RUN。旧run输出只读。

## 最新：6版prompt实测完成，默认采用v5候选（仍未验收）

见DESIGN第72节。固定三份32K联合图文输入，6版各3调用，v4/v5/v6复用提炼结果继续核验，总35新调用。结果与比较在state/curation/v4/prompt_optimization_v1、prompt_coverage_v1...v6及三个_verified目录。当前默认ops/prompts/joint_paragraphs.yaml等于实验joint_coverage_v5.yaml；原版在experiments/joint_original_32k.yaml。实际版本号随prompt pack保存。

v5核验后OK手势13主题2279字符3图、玻璃棒4主题1119字符4图、白花芍药3主题400字符2图。两份notebook有实际图文和六版比较，默认只读。旧32K版为4/2/1主题，551/330/131字符。v1/v2截断，v3有多余version协议失败，失败记录不覆盖。

仍不合格：温度计误认和核验漏拦；芍药泛称物候套到目标物种；花图物种未定；Markdown引用复制失败。v5只是本批较均衡，不是全局最优，也未进行独立全面事实审核。

用户最新提出Gemma图片过滤/双模型：已建议先对照筛选前的同一批可用原图，统计误收/误删，再决定替换图片筛选；联合提炼仍Qwen。双模型不投票认证，分歧待复核。本轮没有切换服务或启动Gemma实验，下一轮按用户进一步指示继续。

## 最新补充：图片按概念筛选四模型实验（2026-09-17）

先看DESIGN第73节及curation/v4/image_filter_models_debug.ipynb。运行state/curation/v4/image_filter_models_v1，114张筛选前可用图、四模型116次调用/380977 token，输入/提示词/实际图像消息一致，全部历史保留。

没有单模型全面胜出：Gemma31B拦住温度计但误收空心管；Qwen3.8相反。当前组合候选Qwen3.8初筛→Gemma31B复核keep，分歧pending；离线回放明显无关误收0/44、初审相关保留45/47（未保留的两图事后复查也存在歧义），按原批次预计57请求而非单模型29。只复核pending不能拦住自信错误。仍共同保留10张未认证物种花图，不能称细粒度身份质量通过。组合是离线回放，未实跑主链、未更换主链模型、未重提炼知识。

ApplyImageSelection仅修复明确空/null补充limitations导致的假性协议失败，缺字段等仍阻塞。原响应重放parser_v2，零新增模型调用；19项测试通过。原始初审47/44/23冻结不改，边界复查与敏感性统计单独保存。

资源恢复情况查看session/events.jsonl最后finished_resources_restored及实际8000服务/预标注进程，不沿用历史PID。不要重新跑四个成功模型或拿旧主notebook中的知识结果冒充新筛选后的知识。

## 2026-09-17：实验结论已接入正式pipeline

现役入口仍为`curation/v4/knowledge_debug.ipynb`（直接可见run_pipeline），拆步入口`curation/v4/glass_operator_debug.ipynb`。默认中性身份输入Qwen3.8图片初筛→Gemma31独立复核keep所在完整原批→分歧暂缓；其后32K容量、联合提炼v5、按需一轮整合延续此前配置。最终图文三部分不变。

新增业务算子在`ops/image_filter.py`；配置/复用校验在`image_filter_runtime.py`；`local_review_service.py`仅负责复核期间借用GPU、恢复原Qwen及预标注。CLI读取notebook的MODEL_CONFIG。默认新run：knowledge_dual_image_v1、glass_operator_dual_image_v1。旧筛选后materials不能绕过新组合；复用必须有匹配policy。清洗checkpoint仍可独立复用。

实际验证文件：`state/curation/v4/dual_image_pipeline_check_v1/validation.json`，由`replay_confirm_images.py`经原生demiflow重放旧真实响应生成：114图=56入选/52排除/6暂缓，温度计暂缓、空心管排除，29+28批，零新模型调用。两个notebook已保存这份汇总。原最终知识仍是历史v5结果，未冒充新链产物。新级联服务完整周期和三概念最终知识尚未重跑；两模型一致仍不能认证具体花卉物种。
