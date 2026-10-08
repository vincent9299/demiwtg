# AGENTS.md · 项目架构原则与数据约束

## 第一条：必须遵守项目 Pipeline 强制规范

**凡涉及 pipeline 的新建、修改、调试、编排、评审与验收，必须先阅读并遵守 [《项目 Pipeline 强制规范》](PIPELINE_SPEC.md)，再阅读目标 pipeline 的 README。不得跳过规范直接实现，不得以旧实现或已有测试通过代替规范验收。**

每个现役 pipeline 的 README 必须在标题后的首段强制引用该规范，新增 pipeline 必须纳入统一布局及引用检查。通用规范只在 `PIPELINE_SPEC.md` 维护；本文件保留项目业务、数据约束及历史决策，与规范冲突的旧条文不再作为 pipeline 实现依据。用户当前明确指令优先，具体授权仍按任务范围执行。

后续 review 发现的规范缺口、歧义、冲突或通用改进，集中登记到根目录 [PIPELINE_SPEC_TODO.md](PIPELINE_SPEC_TODO.md)，记录证据、关联条款、状态及完成标准；已有规范的实现偏差仍须修复，TODO 不替代现行强制要求。

### DemiForge 开发入口（2026-10-06，候选标准试点）

业务 pipeline 开发使用 [DemiForge 项目绑定](.demiforge/project.json) 和 `demiforge` skill；
项目根运行 `python3 .demiforge/forge list` / `context --id <任务名>` 续接已有任务，
新任务在修改前用 `start` 固定目标、可改路径、非目标和当前脏工作树基线。
接入说明见 [DemiForge 最小闭环](../Demiurge/docs/demiforge-quickstart.md)。当前仅登记 `subset`，
其他 pipeline 先审阅并补齐业务上下文、监测路径和隔离测试命令，不能直接声称已接入。
统一标准 `1.0.0-candidate.1` 为候选，**本文件第一条及 PIPELINE_SPEC.md 仍有效**；
不复制维护另一份业务规范。默认上下文只节选本文件入口与 QID 归属，相关历史决定仍须按任务追读。
只读平台 `demiflow` 不因业务任务获得修改授权；代码开发不授权真实模型、生产读写或共享环境变更。
检查、fixture 测试与署名语义审查都绑定本次实际文件版本；未覆盖项必须明确，不能把本地接受当作生产验收。

2026-09-29 修订原因：按用户要求集中维护强制规范，并把 fine_screening / zimage_probe 评审暴露的范围、完成状态、缓存、失败与配置问题提炼为通用验收要求。移出的旧条文见 [历史来源](docs/archive/pipeline_rules_from_agents_20260929.md)，不再维护并行的执行规范。

### QID 公共宽表与 subset 归属（2026-10-01，用户授权）

**来源图片监督扩展（2026-10-02，用户后续决策）：** 修改现有 qid_images pipeline，尽量接入可可靠关联的上游原始资料。用户已撤销早期 `source_annotations` 七分类设计，公共字段统一为 `raw_supervision`（原始监督信号）：一个按来源和原始记录名称组织的 JSON，保全标签、框、图注、P18、实际搜索词等已有内容，补齐可读标签名称。来源已有的 Source/分数/标注者等说明原样保留，不新增通用信号分类协议或人工／机器顶层分桶。固定来源和精确图片关联证据保留；消费方自行做视觉机审与综合机审。实现、实际运行范围和发布状态以 [qid_images README](preparation/qid_images/README.md) 及固定回执为准。当前公共表位于 `preparation/qid_images/datasets/qid_images.lance`。

**后续执行接口决策（2026-10-01）：** 用户已要求业务只通过 Dataset API 提交，DataFusion 完全由 demiflow 底层管理，并明确删除旧公开引擎会话入口，防止业务或 AI 再使用旁路 SQL。此决策覆盖下方早期“可选显式接口、默认未切换”的阶段性描述；现行边界见 PIPELINE_SPEC S11.7、Demiflow 的 `docs/datafusion.md` 和两条 QID pipeline README。历史公共表与已发布版本不因执行内核升级自动重写。

用户已确认公共宽表设计并授权完整实施：`preparation/qid_images` 按 SHA 汇总来源、QID 关系与技术特征，`preparation/qid_concepts` 按 fat ∪ 图片账本 QID 汇总名称、xref/桥、一跳关系、文档、分类与完整图片供给。公共目标为 `datasets/qid_images.lance`、`datasets/qid_concepts.lance`，使用显式 release 绑定固定版本供下游消费；名称体系 preparation 仍独立。两个 pipeline README 是本流程完整设计与验收记录的唯一维护入口；README 的通用职责已加入 PIPELINE_SPEC S01.8。

本条覆盖下方旧“qid_sub 是公共表”的归属约定：100k 的两张 `qid_sub_*` 表归 `demiwtg/subset/datasets/`，迁移保留所有 Lance 历史版本、采样字段和原图片 URI，旧引用通过 relocation manifest 解析，原冻结回执不改写。迁移与真实全量发布的完成状态须看两个新 README 和实际控制回执，不能仅凭本设计条目判断已经执行完毕。新公共准备不运行模型、不补下载、不修旧采样桶，也不以 100k 作为全集。

2026-10-01 全量交付已完成：公共 `qid_concepts@1` 10,648,274 个 QID、`qid_images@1` 18,437,838 个 SHA，固定 release 为 `qid_public_20261001_v1`；该名字是发布登记值，不是文件名，实际两表为工作区 `datasets/qid_concepts.lance`、`datasets/qid_images.lance`。30,114,261 条关系双向核对及消费验收通过。旧 100k 两表已整目录迁入 subset，所有文件摘要与历史版本保持，公共表对旧 QID/SHA/选中关系缺失均为 0。完成证据 `_demiflow/qid_public_20261001/postpublication.json`、`_demiflow/qid_subset_ownership_20261001/{coverage,result}.json`。正式发布当时使用原执行器；早期显式原生接口仅是验证阶段，如今已删除该公共入口。当前本地 Dataset 关系算子自动使用平台内部 DataFusion，Python 业务回调保留，源码未修改；详见工作区 DEMIFLOW_PLATFORM_TODO 的 DF-014 和两条 QID README 的最新验收记录。

### 视觉概念主线与 collect 标准化推进约定（2026-09-30）

**2026-10-04 文档主表粒度（用户确认）：** 重定向保留在已有公共 URL 索引的 `redirects` 映射中，文档主表只保留非重定向文档版本。通过 preparation/documents 正式入口退役存量重定向行，后续 metadata 与 QID 关联入口同步排除；正文失败身份、其余行全部字段、旧版本、对象和原索引保留。不把此调整等同于跨来源 URL 去重，具体固定版本和执行结果见该 pipeline README。

**2026-10-03 文档归属最新指令（覆盖此前暂停迁移）：** 用户确认导入代码继续迁至 `collect/wiki_documents`，文档对象统一放在 `preparation/datasets`。已完成同文件系统移动：对象为 `preparation/datasets/documents/objects`，公共索引为 `preparation/datasets/documents/library/index.sqlite`；旧资产目录保留兼容链接，历史登记 Lance 表与固定版本仍在原位，不复制资产、不另建库、不改 concepts 配置或失败回执。正式入口和新回执归 collect；公共文档主表归 preparation/documents。用户随后收紧范围：先交付主表，不读正文统计、不建公共结构树；embedding 暂停，既有向量按用户明确要求清理，保留正文与公共文档索引。迁移证据为 `_demiflow/document_embeddings_20261003/document_assets_move_20261003/result.json`。

**2026-10-03 独立 taxonomy 决策（用户授权）：** 用户要求以现有 3,000+ 原名的身份审定为来源，实现独立 `preparation/taxonomy`：使用已定大模型 agent，按需读取概念/节点定义、成员与动态正文证据，通过局部树操作、挂载、复核和反馈循环迭代。增量须保留并重验旧成员。3,315 是原名范围，不代表均已通过身份检查；具体固定输入、模型调用预算、工程验证、真实质量和 0.1 状态以 [taxonomy README](preparation/taxonomy/README.md) 与固定运行摘要为准，不改写原 P2 或公共表。

用户确认“新工作用到哪个模块，就先补齐该模块及必要依赖的规范；标准化后复用”。通用获取/执行/存储/恢复机制沉淀 demiflow，业务判断留在各 pipeline；不以全 collect 重写作为概念主线前置。当前优先搜索取证与概念审核，历史正文接入、图片下载、批量采集和旧脚本退役按实际使用节点推进。唯一计划记录见 [taxonomy-rebuild/EXECUTION_PLAN.md](taxonomy-rebuild/EXECUTION_PLAN.md)。2026-10-01 用户已确认讨论建议并授权完整实现 P1→P2、补齐 demiflow 通用能力和详细 review；输入必须可配置，3,315 名称不作为固定范围。完整依据见 [P1→P2 实施规范](taxonomy-rebuild/P1_P2_SPEC.md)。用户随后授权调用已定模型并结合真实下载收集性能；可执行有明确预算的小规模验证，不扩大为全批，生产公共表保持只读。

### Evaluation 根目录只保留分类导航（2026-10-03，用户要求）

用户指出根级算子与赛道目录混放；原根级流程实际用于 T2I/Edit 的有知识与无知识对照评测。该流程迁至 `evaluation/knowledge_comparison/` 后，用户明确要求删除，并确认已另存 prompt；因此整目录及活动导入、CLI 测试、源码清单和布局登记均清理，不恢复旧入口或兼容转发。用户另存的 prompt 保留。旧 Edit V1 证据读取及测试仍归 `evaluation/edit/v1/`，历史 rubric packet 的 JSON 解码由材料记录读取方直接处理。删除目录内没有 datasets，未改写历史业务数据，没有运行模型。

### T2I V2 出题内探测（2026-10-03，后续用户要求）

2026-10-04 出题输入进一步收敛（覆盖下方早期输入约定）：保留原始 definition 概念说明和材料来源/本地文档入口，原文由模型按需读取或联网检索。已核实事实、待核实事项、附加限定及此前任务方向不进入初始模型上下文，原审定记录保留溯源。prompt 明确材料清单不完整且不限定考点范围。旧批 definition 部分含形态或教学示例，当前未自动改写，详见 V2 README v16 记录。

同日输入进一步收敛：用户明确移除 references；出题只有概念资料、依据材料和单列正例图，不给模型有图/无图实验条件说明。新表文字依据与正例图分别存储，不保留混合 references_json；配对标记仅留作业务记录。prompt 删除已核实事实和未决事项的重复字段说明，实际资料及其适用条件仍保留。新协议使用新运行/目标，历史20概念结果不重写、不自动运行。

用户明确将 case_annotation 的作答与评审直接融入 V2，出一道题立即 Z-Image 作答、GPT-6 评审，不再启动逐行子 pipeline 或等待全批。image_review/joint/blind 的适用要求合并成一次按既有考点和判据的联合评审，不声称独立盲评。模型最终确认为 Malasci `gpt-6-astra`，`reasoning_effort=xhigh`；出题 Codex 于 2026-10-04 按用户要求也改为 xhigh。V2 agent 配置和普通 prompt 配置各一份，原出题正文/schema 保留；新 probe 的分类不覆盖旧人工标签。共享生成行算子由 V2 维护，case_annotation 显式导入，历史实验、数据和 prompt 保持。用户随后明确清理重复出题入口：V2 出题只走 agentmap_async，agent_codex.yaml 只维护 design_question，tasks.yaml 只维护 review_answer；删除普通单次出题分支及重复 GLM 正文，缺少 agent_config 在执行前报错，不自动切模型。原20概念仅出题批次已结束并暂停；此次实现和隔离验证不自动启动真实探测，见 V2 README。

### T2I 案例标注的唯一维护位置（2026-09-29，用户要求）

**2026-10-03 归属更新（用户授权）：** 案例生成、图片评审、三类标注及人工复核归 `evaluation/t2i/case_annotation/`；代码、notebook、prompt、测试、archive、数据、日志与运行锁均已迁移。已退役 `zimage_probe` 的数据仍有历史消费者，已合并到案例流程 datasets，全部 Lance 版本与调用日志字节保留，旧固定 URI 通过平台精确迁移映射解析。`benchmark/t2i/case_annotation/` 与 `benchmark/t2i/zimage_probe/` 两个旧目录均移除，不留兼容入口或软链。此条覆盖下段“原址 datasets 保留”的旧安排。回执见工作区 `_demiflow/t2i_case_ownership_20261003/result.json`；本次不重跑模型。

用户随后明确要求目录合并：生成、图片评审、三类标注、人工维护和历史主审复核统一归属 `benchmark/t2i/case_annotation/`，唯一正式入口为 `case_annotation_pipeline.config → run_pipeline`、唯一 notebook 为 `case_annotation_debug.ipynb`。`zimage_probe` 现役代码与 notebook 已退役并归档，原址 datasets 仅保留冻结历史引用，新运行不写该目录。一次 Codex 调用同时图评审与提供分类依据，属于联合判断而非独立盲标；旧人工标签和独立盲标证据保留。跨不同 pipeline 的 demiorch graph 编排作为后续 PS-003 TODO，本轮是内聚的单 pipeline。复核通过该模块正式入口写明确 schema 的 Lance，所有日常呈现在其 notebook；旧 HTML/CSV 呈现与导出脚本退役，历史证据在该模块 archive 保留。主审建议不自动覆盖人工 labels；单行编辑必须走正式提交入口的锁、版本与旧值检查。

用户随后明确“以最终复核结果为准”，并要求手标也重新判断：当前采用`adjudications__case_annotation_review_v2_20260929.lance@1`，三类25/225/214、暂缓5，22条手标统一复判，原未选择2条已纳入③。`selected`保留历史，`final_selected`表示最终纳入；旧人工表与v1保持。用户最新要求页面只看最终复核：notebook第2格保留最终三组Z-Image指标与弱点，第3格用1/2/3为行索引、二级分类list/三级分类list/概念list三个字段汇总；分类保留路径前缀、组内去重、完整展示，暂缓单列。GLM与盲标保留为底层证据，不再展示对比。①含本批出错筛选，不能用其低pass率直接作无偏能力排名；暂缓不回填旧类。

### 独立图片对象引用（2026-09-29，用户确认）

用户选择“全部现役图片链路和对应通用能力”，随后明确要求“彻底迁移、切换、导出，不存在 Lance 表里”：本项目图片统一存为独立对象，表中只交付稳定 URI 与内容 SHA256，不再新增表内图片 Blob。具体规范见 [S11](PIPELINE_SPEC.md#s11--存储与平台边界)。本条覆盖下文历史记录中新增 BlobRef/source_refs 像素引用与 LanceBlobStore 交付的约定；历史判断和模型输出保留。生产迁移进度与审计位置见 [改造记录](docs/image_objects_20260929.md)，以实际导出、切换和退役回执为准。

生产执行记录：2,133,062 个对象（923.265 GiB）已导出并核 SHA，75 张业务表原址切换；旧图片载荷及切换备份已退役。当前 collect images@8、公共 images@16、articles@8，源库原有 36,911 条无图片记录仍为空；历史 data 字段仅保留外部文件描述以支持冻结证据，新 head 不含 data。11 份 notebook 的来源引用已更新，既有输出/判断不变。全量 annotation 已用原 SQLite 日志恢复；历史调用日志按原证据规则保留。实际回执在工作区 `_demiflow/image_object_cutover_20260929/`，不作为日常读图依赖。

### QID 下载图片 URI 挂接（2026-09-29，用户要求）

- `collect/datasets/qid_images_v2.lance@11` 新增可空 `image_uri`；490,577 条来源记录对应 490,575 个已下载唯一 SHA，其余 17,947,294 行为 null。公共 `datasets/qid_sub_100k_bucket_v1_images.lance@5` 全部补齐 URI，状态为 `available/object_uri`；concepts、选样、许可、变体及历史快照保持。
- 普通文件继续位于 `collect/download/blobs/<sha[:2]>/<sha>.<ext>`，两表直接共享绝对 file URI；该目录是持久资产，不能清缓存或移动后不迁移 URI。全量重新核 SHA/大小，544,150,878,170 字节，0 缺失/不符，未复制图片。回执 `_demiflow/qid_image_uris_20260929/published.json` complete；详情见 [交接文档](collect/download/HANDOFF_subsets_pipeline_数据交接.md)。
- subset pipeline 继承可选源 URI、按实际引用统计完整/部分/待下载，保留已补图覆盖保护；新抽样用 @11、新 run/目标名、`legacy_pool_source=None`。Notebook 原 @10 抽样配置/输出保留，默认不重跑，最后一格独立看 @5。维护脚本只为本次回填，现役读图不依赖维护脚本或队列。

### 共用准备与任务 curation 的用途边界（2026-10-02，用户授权）

**2026-10-03 旧流程退役（用户最新指令）：** 用户确认已有新版，要求删除 `curation/legacy_concept_images` 并去掉依赖，旧 articles 也不再使用，当前文档准备归 `preparation/documents`。旧概念配图目录及 articles 生产入口已删除，不保留审核转发入口；历史 notebook 输出和原有审核档案归档，Lance 表、固定版本与调用日志保留。训练、出题、评测及维护工具需要的图片读写/记录契约归 `preparation/images/catalog/operators/records.py`；articles 下仅保留共享历史材料契约与测试，不登记为现役 pipeline。此条覆盖下方旧消费者须保留概念配图入口的安排。

**2026-10-03 Edit 原图后续决策：** 用户要求清理 `curation/edit_scene_images` 的旧代码和 README，复制 `preparation/image_embeddings` 标准实现，固定使用旧场景候选 `image_inputs__scene_pool_add_v5_l4_keep_hold_tp2_v1.lance@13` 的 172,297 张图片，作为第一版编辑可检索原图。新版只生产 WeMM 图片向量，不再执行旧分类或 add 区域标注。旧表和调用日志保留；历史 reviews、交接材料和 Notebook 输出移到 `curation/archive/edit_scene_images_annotations_20261003`。本次代码替换不代表全量编码或出题检索已完成，状态以新版 README 和运行摘要为准。

用户明确只有 P1–P4 与共用概念正例图属于此处的共用概念层，后确认其归 preparation；P1–P4 本轮保持判断规则，仅调整共享资产默认路径与搬迁解析，共用正例图为 `preparation/concept_positive_images`。任务流程按产物用途命名：`curation/edit_scene_images`、`curation/t2i_training_samples`、`curation/edit_training_pairs`；旧文章／训练依赖的概念配图协议为 `curation/legacy_concept_images`，不是新主线的公共必经步骤。Edit 场景原图须完整迁移，包括数据与日志。公共中性 annotation 已停止并退役，源码在 `curation/archive/image_annotation_20261002`，原结果和调用日志保留；不再生产通用 caption/richness。主说明见 [preparation](preparation/README.md) 与 [curation](curation/README.md)。公共准备表已迁至各生产 pipeline 的 datasets，registry 已迁至工作区 `_demiflow/registry/`。用户要求撤销顶层公共 datasets；概念底库应归 collect/datasets/concepts.lance（去掉 master 文件名，不新增无加工逻辑的 preparation pipeline），文档库应归 preparation/wiki_documents/datasets，已按用户要求等待原 P1/P2 进程退出、运行锁释放且核查没有文档句柄后，完成这两项迁移及默认路径切换；根 datasets 已移除。原 P1/P2 冻结配置与结果保留，退出不表示业务审核成功或完成。实际状态见 [归属说明](docs/asset_ownership_20261002.md)。本条覆盖下文旧启动／归属状态。

### 独立图片中性 annotation（2026-10-01）

**最新运行状态（2026-10-02 10:40 北京时间）：用户要求暂停标注，`neutral_images_v2_20261001` 主进程及所属 vLLM 已停止，双卡释放。run、原始及续跑 journal、已有文件保留；本轮未 export，公共 images 仍为 @16。不得依据下方旧授权或 GPU 临时让位安排自动恢复标注、发布或清扫，等待用户再次要求继续。暂停回执见 `preparation/images/annotation/runs/neutral_images_v2_20261001/pause.json`、`process.json`。**

用户明确图片与文章为独立 pipeline。本次 annotation 已移除概念评分节点、旧 all/image/concept 参数和全库 Python 索引；只维护 descriptions/image_scores。唯一 config → run_pipeline 入口显式区分 prepare/annotate/export，默认 annotate；原生只读 journal replay、固定引用与逐 SHA 完成检查见 [现役 README](preparation/images/annotation/README.md)。下文 2026-09-28 的双节点/四列内容是历史实现记录，不再描述现役 annotation。原 full_label run 与 journal 保留且暂停；旧 launcher 包含已退役参数，不能直接恢复。用户进一步明确本次只改图片标注：articles/review 之间的代码耦合不属于本轮范围，不是 annotation 改造的前置条件；annotation 与二者没有直接代码调用，仅以公共 descriptions 字段供消费者读取。文章、review 及其他会话 QID/概念审定/image_audit 未修改。

用户随后授权继续标注，并明确这是正式运行，必须合并公共表。新 run `neutral_images_v2_20261001` 沿用 images@16 和旧输入@7 的 ready 名单 1,306,045 张；首次复制旧 journal 后继续使用同一副本，保留原始文件及此次新增响应。现配置 through=export、publish_policy=valid_rows，处理结束后自动合并成功的 descriptions/image_scores，失败名单保留，不执行概念评分或自动清扫；CLI/notebook 共用该 run 的 config.json。切换发布方式不改变模型请求身份，已经保存的同请求响应直接复用，输入和阶段表可重建。重复 SHA 复核与业务写入重试循环已删除，状态计数改走 Dataset 聚合，只读回放缺响应使用平台独立错误类型。当前进程按 process.json 核查，完成以 summary 和公共提交版本为准。

### 公共图片 annotation pipeline 第一版（2026-09-28，本会话交付）

- `preparation/images/annotation/` 建成标准 pipeline：唯一入口 `image_annotation_pipeline.py` 的 `config → run_pipeline`，四格 `image_annotation_debug.ipynb`，operators/prompts/tests/README 齐备并纳入统一布局检查。融合历史基础图片标注与 collect_v2 实体评分为两个模型节点：`describe_image` 一图一请求（只有实际图片，1536/JPEG90，产出 DESCRIPTION+richness），`score_concept` 一图一概念一请求（名称/别名/可选知识正文，产出 match_status/kb_match/identity/focus）；`quality=round(0.4kb+0.4focus+0.2richness,1)` 由代码派生，公式 ID `kb_focus_richness_0.4_0.4_0.2_v1` 固定，模型不输出 quality。
- 公共 `datasets/images.lance` 新增两个 nullable 顶层列 `image_scores`/`concept_scores`（类型在 catalog/operators/schema.py 唯一声明；平台 add_lance_columns 只支持新增顶层列）。annotation 独占四列 `descriptions/concept_matches/image_scores/concept_scores`：descriptions 与 image_scores 同 annotation_id，concept_matches 与 concept_scores 同 annotation_id；按该 ID 幂等合并，同 ID 不同业务内容报冲突，run_id/provenance 差异不算冲突。`annotation_parts='all'|'image'|'concept'` 控制更新的列集合。审核/发布/目录列与未知未来列原样保留；缺目录 SHA 在 merge 前显式报错交回 catalog（anti join 预检，inner join 会静默滤掉缺行）。
- 标注 ID 与复用：图片级 ID=SHA+实际输入摘要+语义 config_id（prompt 版本/pack hash/模型/预处理/生成参数；并发与 run 名不影响）。复用两层：run 内原生 SQLite journal 复用同请求完整响应；跨 run 按公共表同 config_id 成对记录复用（换 run/收紧范围零新请求）；知识正文或 richness 绑定变化产生新 ID，新旧评分并存不覆盖。`skip_annotated_images=True` 把已有 done 描述的图整体排除出本轮范围（用户要求"打了标签的不需要再打"；范围规则，不为旧图伪造新评分）；`exclude_concept_count_ge` 按固定来源全库去重概念数排除（复现 469 时显式设 3，不写成公共默认）。
- 阶段表在本模块 `datasets/`（image_inputs/image_results/concept_results/patch/summary，typed，仅 overwrite 本 run 表），运行文件在本模块 `runs/`；像素按行 `source_refs` 绑定的 `demiwtg/collect/datasets/images.lance` 固定版本读取并核 SHA，base64 只进请求不落表；范围计数基于公共目录行内去重非空概念集合，先过滤后关联像素。验收测试为隔离湖+模拟响应（tests/ 22 项含复用零付费、部分模式、并发冲突重试、失败无假状态、ReuseImageAnnotations 兼容）。

### 469 原图范围过滤与审核复用（2026-09-28，用户授权）

- 用户要求排除全库关联去重概念数>=3的图片，并确认可停止旧后处理、切换新代码；新运行仍由用户手动启动。已核实旧v3模型响应完成、未写公共表，精确停止旧469写入者，保留阶段/响应；不影响其他Edit/GPU任务。
- review notebook改为 `t2i_dual_keep469_visual_v4_scope3_20260928`，`reviewed_run`固定v3、`max_calls=0`、threshold=3。固定raw images@5先过滤再展开，排除行只读SHA和concepts；下游只带来源引用/关联计数/必要元数据，不再复制sources。旧26.29GiB visual_inputs不读取，旧image_requests仅提取概念/图片SHA/编号/模型输入身份，复用已提交image_relevance，模型和服务节点完全跳过。
- 复用保持旧case_id/image_id/原批次结论，重验保留图片字节；缺失/变化/缺审核绑定不得发布。原图版本、概念身份、模型和prompt契约必须一致，范围只可收紧。排除记录单独保留，过滤图片的旧pending不污染剩余图片状态。全部469概念保留，无图与全过滤分开。
- 公共目标仍为datasets/images.lance，最终按SHA部分列merge；阶段只写本轮数据。范围外行/其他生产者列保留；本轮观察来源用固定引用，不重新内联原始sources。原图与原模型判断不改写。第三格查看本轮精简结果和分页缩略图，第四格观察后处理与旧调用日志；均不启动模型。
- demiflow通用修复：物化缓存按编码字节分块并逐行溢写恢复；外排宽行载荷只写一次、归并携带偏移；read_lance显式提供batch_size/batch_readahead/fragment_readahead。内存预算只约束对应缓存，不能宣称整个进程RSS有硬上限；业务概念过滤和发布策略不下沉平台。


### T2I 两阶段筛选模块更名（2026-09-28，用户要求）

- `benchmark/t2i/sampling` 更名为 `benchmark/t2i/coarse_screening`（粗筛），`benchmark/t2i/screening` 更名为 `benchmark/t2i/fine_screening`（精筛）。目录、入口文件（`t2i_coarse_screening_pipeline.py` / `t2i_fine_screening_pipeline.py`）、debug notebook、运行锁目录（工作区根 `_demiflow/benchmark_t2i_*`）、跨模块 import、测试、notebook 与 README/交接手册同步更新；lance 表名与 run 名不变，旧路径不再保留转发。
- 表内存储引用同步迁移：summary/records/results/samples/concepts/reviews 等 35 张表里保存的 uri 字符串（selection、call_json、review_source_uri、payload 等）含旧路径，已按新路径生成新 lance 版本（旧版本保留可审计）；固定版本号引用不受影响，coarse/fine notebook 的只读查看与断点恢复链路已验证可打开全部引用表。
- 术语统一：分类初筛阶段称粗筛（coarse_screening），双模型逐概念判断阶段称精筛（fine_screening）。v2/Edit 出题的 `screening_source` 参数、prompt 名（`t2i-concept-screening-4`）与表内字段名保持不变。

### Edit V2 标准 pipeline 第一版（2026-09-28，本会话）

- 用户要求按标准 pipeline 清理旧实现，使用 Codex 出题，并提出同一次 Codex 执行内生图、检查、交付。沿用联合设计种子/核心考点/原图构造/题目的既定思路。
- 已重写为 config → run_pipeline，固定公共图文/可选场景池直接 Dataset 关联，每概念独立 Codex 请求，typed inputs/designs/candidates/summary；原生日志复用。同目标 append 按 task_id 去重。
- 新 prompt `edit-v2-codex-source-2` 保留基线知识标准，增加作者原图检查记录；只有实际 edit_source 存在且作者检查通过才交付 unreviewed 候选。无图/材料不足/待合成/检查失败/技术失败区分；作者检查不等于独立审核。
- 移除 Edit V2 的 preparation RunFiles/冻结材料/旧发布适配/JSON 阶段封装/local Qwen 路径，删除未使用 prompting/transforms；测试归 v2/tests，无转发层。V1、历史数据及仍在运行的 source_images 独立流程保留。
- **平台扩展已获授权并接通**：用户明确“好，接通一下吧”。现有 demiflow codex_exec 增加显式 image_generation、artifact_store 与文件数/总字节数限额。启用时在独立临时 workspace-write 中执行，给作者输入图片路径映射及产物目录；结束清理前按实际文件写 LanceBlobStore，并随原生调用保存固定引用。续跑校验并复用图片，不再生成；存储/缓存图片失败不自动付费重试。文件交付归平台，出题/像素/作者检查规则仍归 Edit。
- generate_source=True 只支持 Codex；False 显式关闭原生生图，仍可保留 needs_source_synthesis 方案。默认文件限额8个/64MiB为接入层硬限制；max_generation_attempts=2为作者指令预算，不声称可强制工具调用次数。无图片/非图片/未绑定文件/复制种子冒充构造图不得导出；作者检查失败的实际图可留作诊断。
- Notebook 默认 Codex/gpt-6-astra/xhigh/live，固定 fine_screening@1、articles@4、images@9、5概念，GENERATE_SOURCE=True、RUN_PIPELINE=False，运行名 edit_codex_seed_pilot_v2_20260928。原生图像后端具体型号和账号实际可用性不凭配置推造；开发只跑隔离模拟及回归，真实出题和生图由用户手工试跑。


### T2I 两轮分类结果与 high 名单分布（2026-09-28，用户要求对齐 fine_screening 最后一格）

- coarse_screening notebook第二格仍独立只读；现在同时读取L2_RUN_ID二级轮与L3_RUN_ID三级轮的摘要及固定reviews/memberships。各轮展示high/medium/low判断项数、真实分类数、补位项数、档内去重概念数，技术状态单列。此条覆盖此前仅三级快速查看及SHOW_L3_CONCEPT_COUNTS可选计数的约定。
- 每轮high唯一概念集合按完整taxonomy的全部归属展示二级/三级分布、概念数、占本批比例和完整concept_list，分类内去重、跨分类可重复；不限制为模型判high的路径，不用单一leaf归属，不把补位标签造进taxonomy。缺层级明确显示未标注。
- 两轮结果分别保留round_views['l2'/'l3']及l2/l3别名；分类名单完整、不截断。第一轮high超11万，逐概念taxonomy大表默认仅保留DataFrame，SHOW_FULL_HIGH_TABLE=True才重复渲染完整大表；各分类内名单不受影响。只读成员元数据，不扫图片/主概念库，不调用模型、不改demiflow；原执行分支、第一格及保存输出保持。

### T2I 三级分类结果独立快速查看（2026-09-28，用户反馈第一格慢）

- coarse_screening notebook第二格默认可独立只读执行，直接接三级轮summary及其固定reviews，不依赖第一格CONFIG/state、不重建上一轮范围。默认只展示high完整明细及三档分类/补位数；L3_VIEW_PRIORITY=None查看全部判断，l3_details保留全集。
- SHOW_L3_CONCEPT_COUNTS默认False，跳过成员表关联与去重；设True才补档内概念数。只读不扫描图片、全量概念池、不调用模型。第一格全库概览和已有输出保持；只有RUN_L3_PIPELINE=True的执行分支才要求第一格已准备CONFIG及完整state，仍用原正式入口。

### 图片吞吐优化与469手工运行（2026-09-28，用户最新要求）

- 用户要求先优化demiflow与业务pipeline，明确469仍由用户手工运行；开发只做隔离测试和只读配置验证，不启动模型。用户随后确认：能复用准备结果则一并完成此前目录拆分；v2已退出且模型调用为0，允许现在切换。
- 通用阻塞算子使用demiflow `map_async(..., execution='thread', concurrency=..., queue_depth=...)`，平台管理本级线程池、背压及退出排空，业务不再包装to_thread。默认inline不变；线程模式拒绝异步函数和不可强杀的hard_timeout，调用方保证线程安全与I/O有限超时。SQLite日志、请求身份及不确定调用语义保持。
- Edit沿用原32读图并发/16队列及请求载荷，仅替换执行机制。469准备并发8/队列8；Qwen客户端128/服务64、Gemma64/32，输入队列16/8；二者依次使用GPU0+1、TP2/DP1，显存0.92、上下文32768、batched_tokens16384、enforce_eager=False、加载900秒。4图/批、1536像素最大边、JPEG90、输出8192及审核prompt保持。
- 新配置run=`t2i_dual_keep469_visual_v3_tp2_20260928`，旧v2在资源冲突时没有发出模型请求；通过prepared_run复用旧v2两个固定准备表并重验原图SHA，旧阶段与错误保留。notebook四格为配置、手工执行、全部469概念及缩略图/两模型明细表、只读GPU/调用日志观察。第3格支持概念单选/多选/全选、自适应表宽与图片分页，保留无图/待审核概念，未提交不算零通过；只读查看不重跑审核。窗口新请求与缓存复用吞吐分开。参数是实测起点，不预称双卡已打满。

### 公共图片子 pipeline 与平台部分列写入（2026-09-28，用户最新确认）

- 本条覆盖下方“同一图片目标只留一个入口”、469 notebook 第7—9格、旧v1运行名及 borrow 服务约定。图片拆为 `preparation/images/catalog` 和 `preparation/images/review`，各自管理 `config → run_pipeline`、notebook、算子、提示词、测试与 README；共享 schema 和实际复用算子须明确生产者归属，不保留旧 `images_pipeline --flow` 转发。
- 用户授权扩展 demiflow 的通用按主键部分列 merge 与显式增列；业务直接用 `write_lance(mode='merge', on=..., update_columns=..., when_not_matched=..., expected_version=...)` 和 `data.add_lance_columns`。平台不含图片字段、选样或业务去重；目标字段归属在项目约定，未来公共字段由独立生产者更新。
- catalog 写基础目录/尺寸；review 写 concept_assessments 和派生集合，保留 descriptions/concept_matches/技术字段及未来其他生产者列。来源与概念关联取并集；整列值的业务合并在项目内完成，公共数据 merge 一次提交，DDL 单独版本。所有本轮结果阶段完成后才写公共目标。
- 469 是公共 preparation 本次更新范围（固定 fine_screening@1 的全部有效keep，collect images@5）；下游再决定选样。材料优先200代码已移入 benchmark/t2i/v2，旧表/CSV不动。
- 手工审核 notebook 为 `images/review/image_review_debug.ipynb` 三格：配置、运行、只读查看。run=`t2i_dual_keep469_visual_v2_20260928`，完整taxonomy、无图状态、全范围审核及>=5张通过图统计保留。旧notebook原输出分别保留到catalog/review新路径。
- 两个模型节点用 demiflow 原生 VLLMService，Qwen3.8→Gemma31 顺序使用GPU0+1/TP2；只启动/释放本节点进程，不接管外部服务。新审核使用SQLite调用日志；历史及文章调用方不批量迁移。开发仅隔离模拟与只读核对，用户手动执行469，不能代停现有服务或启动生产模型。

### T2I 全部 469 个通过概念先审核（2026-09-28，用户最新要求）

- 用户改为全部 469 个双模型有效 keep 概念先跑 preparation，审核后再选样；覆盖下方先挑 200、至少 3 张候选图的本轮约定。历史覆盖表、200 个名单和 CSV 原样保留，不再用作本轮审核范围。
- preparation/images notebook 第 7 格配置、第 8 格手工运行、第 9 格只读查看；固定 fine_screening high_l3_categories_uniform1000@1 和 collect images@5，run=t2i_dual_keep469_visual_v1_20260928。概念名与完整 taxonomy 提供身份上下文，不传筛选理由；无图/无可读图也保留概念结果但不请求模型。
- 保持现有 Qwen3.8 初审、Gemma31 独立复审和 borrow 服务策略；批大小 4、并发 4/2、每模型节点预算 5000、输出 8192、timeout600、进度每20条。开发仅隔离模拟与只读配置验证，模型由用户手动启动，不代操作服务。
- 本轮阶段限定 469 个概念及其图片；公共 images 采用 merge，提交的新版本保留全表，不是本轮子集。查看本轮通过图数与至少5张的概念数，区分无图、不可读、待定/失败和已通过。目录补齐与视觉审核是独立操作，列更新本身不要求绑定；本次未把出题扩大或启动。

### T2I 200 个材料优先概念准备审核（2026-09-28，用户确认）

- 用户选择先从双模型通过的概念里选 200 个准备 preparation 审核，尚不执行模型审核或 200 次出题。现役 images_pipeline 增加 reference_selection_config/run_reference_selection；仅数据统计与名单落表，业务不进入 demiflow。
- 固定 fine_screening high_l3_categories_uniform1000@1、images@9、articles@4，先已有审核图文，再至少 3 张目录标记可用的非已知生成候选图；seed=20260928，各优先档按三级分类前缀轮转，完整 taxonomy 保留。数量不足报错，不用 hold/failed 或缺材料概念补足。
- preparation/images notebook 最后一格配置并查看 reference_coverage/reference_concepts 两表，run=t2i_dual_keep_reference200_v1_20260928；独立视觉审核配置接入名单及 collect images@5，调用保持注释。候选图数量不是已发布参考数量，不修改公共表或历史 notebook 输出。原 5-case 出题配置保持。

### T2I 正式出题输出判据与具体任务检查（2026-09-28，用户确认）

- 仅 T2I V2 的每项 `test_points` 改为必填 `point/basis/criterion`：考点、支撑考点及判据的事实与适用条件、最终任务中的可观察正确/错误标准。覆盖下方 T2I 不输出判据的旧约定；不修改 Edit 协议。判据可写出答案，不作为生成模型的作答指令，不新增分值、权重或评测流程，候选仍为 unreviewed。
- prompt `t2i-v2-independent-materials-6` 保留原五条质量门槛，最终检查集中到“知识可靠、任务需要、图像可判”，联合核对最终题面、考点、依据、判据；保留核心区分价值、必要展示条件、无隐藏要求、合法变化、证据不足与空题出口，不输出检查过程。
- 同步 YAML 响应定义、Arrow 落表和 notebook 的考点/依据/判据展示。新版 notebook run 为 `dual_keep_codex5_criteria_v3_20260928`，沿用 5 概念抽样、完整 taxonomy、固定图文来源与独立 Codex 配置。旧表及 notebook 输出保留；旧表缺判据时明确显示未输出，不补造标准。开发只做隔离模拟测试，由用户手动试跑。

### T2I 第二轮五条规则增强（2026-09-27，用户确认）

- fine_screening prompt升为t2i-concept-screening-4：原五条标题、解释及严格门槛完整保留，每条内分别补清核心知识如何落在任务中、同条件下值得测的错误、条件对核心的作用、事实/任务要求/可见证据的区别、范围一致性。另补三项具体任务检查，落实到candidate_point/visual_check，不增加响应字段，不要求提前写完整题面。
- 第一轮仍按分类及少量样例判断优先级，五条核心标准一致，不把第二轮的具体任务检查变成第一轮逐概念出题要求。无材料仍为正常输入，不确定的关键知识不能编造。仅改项目prompt/说明，不改demiflow语义；notebook与GLM交接已同步v4，旧v3用量只作历史参照。

### T2I 真实三级 high 均匀抽样与夜间交接（2026-09-27，用户确认）

- 用户特别强调保持demiflow平台语义干净：本轮GLM夜间护航不授权平台改造，不能把分类/抽样/双模型AND/业务失败策略或本轮模型、路径、run特例混入平台，也不能改缓存、请求身份、不确定请求、锁或存储语义来绕过恢复限制。项目业务留在pipeline/operators/notebook；通用能力缺口登记共同根DEMIFLOW_PLATFORM_TODO.md，需改平台才能继续时报告具体阻塞，不能以持续护航为由临时污染框架。
- 本条覆盖上一版混合519项试跑：本轮仅199个真实三级 high 分类，320个 high 概念补位项留待后续。固定 mixed v2 memberships@1/reviews@1，以 category_kind=category 在去重与配额前排除补位；26,122个唯一概念，抽样实际在第二轮正式 pipeline 开始处执行。
- 类间均分、类内按完整路径轮转、跨类概念去重；小类不足取满后分配余量，总量不足报错。当前 seed=20260927、sample_size=1000 已隔离 prepare 验证：194类各5个、5类各6个，无重复和补位。第一轮不抽样、不改上游失败状态；无图文/分类理由，每概念两模型各一次，全部有效 keep 才通过。
- Notebook 新 run=high_l3_categories_uniform1000_v1_20260927，RUN_PIPELINE=True、MODE=modelhub，用户交给GLM后台执行并持续夜间护航；本次开发不代启生产模型。源notebook是唯一参数来源，后台直接执行同一本，不复制隐藏参数。保留既有输出。
- 每模型并发4、输出16384、timeout600、progress_every20；追加本run进度日志供后台查看，无完成行不产生定时心跳。交接手册在 benchmark/t2i/fine_screening/archive/GLM_HANDOFF_high_l3_uniform1000_20260927.md。相同完整响应可复用；失败响应/未完成占位不能靠同名重跑自动修复，不能删日志或盲换run全量重算。

### T2I 接三级有效 high 做双模型试跑（2026-09-27，用户要求）

- 三级混合轮调用已结束，但 summary 仍为 waiting_for_reviews：3,184 项有效、101 failed、235 invalid_response，未生成完整 concepts。有效 high 是 199 个真实分类 + 320 个概念补位项，对应 26,442 个唯一概念；不能把“调用结束”写成完整交付。
- fine_screening 增加成对固定 category_member_source/category_review_source：只接 reviewed/high 分类及其成员，再按 concept 关联既有全量概念池的 name/taxonomy，不沿用旧二级分类优先级。抽样、模型、writer 仍在正式入口；未成功分类留原状态，不改 coarse_screening 的完成门槛。来源对要求显式 sample_size，固定引用保存在 selection。
- Notebook 来源固定到 mixed v2 的 memberships@1/reviews@1，名称/taxonomy 仍来自 all_master_category_pool@1；新 run=high_l3_dual_pilot1000_v1_20260927。建议先试1,000，MODE=prepare、RUN_PIPELINE=False，不自动提交模型；None 仍表示未定规模。每次请求一个概念，两模型独立判断、全部有效 keep 才通过，无图文/分类理由。
- Notebook 增加累计 token/耗时情景表。GLM v3 234 个正常响应均值约3,121输入+4,193输出tokens、92.5秒；DeepSeek尚无本项目实测，双模型情景仅暂按同量级估算，不表示已核实配额/价格。既往246个带usage响应有12次在8192截断，因此试跑上限改16,384，不等于实际每次用满。规模、并发和后续扩量依据首批实际 usage/质量再定。

### Edit 原图视觉全量接线与 GLM 护航（2026-09-27，用户确认四级 keep+hold / TP=2）

- 用户确认来源为最新四级4B目标：concept_inputs__scene_pool_category_l4_v1@1、category_results__scene_pool_category_l4_v1@1，回连其原候选 image_checks__scene_pool_category_l3_v1@1；不能从四级keep-only图片目标恢复hold。keep14,317+hold12,857共27,174项，原生Dataset只读核对为82,331概念、172,297张唯一图片。
- 正式config增加 category_result_source 固定目标复用及 category_decisions 召回档位；匹配当前分组键/类型/成员数、完整状态后跳过文本模型，max_text_calls=0。两个图片限额支持None；本轮全部通过尺寸的候选进视觉，不沿用500图/每概念4图。分类决定原样保存，来源字段不进入视觉请求。
- Notebook本轮 run=scene_pool_add_v5_l4_keep_hold_tp2_v1，action=run、through=pool；用户交由GLM执行护航，本次开发只配线、隔离测试和只读范围核对，不代为启动生产模型。交接文件在 benchmark/edit/source_images/archive/GLM_HANDOFF_add_pool_v5_20260927.md。
- 用户明确要求一份模型分两卡：Qwen3.8-27B、GPU0+1、TP=2/DP=1；demiflow原生服务管理负责加载/就绪/释放，业务只配置。当前请求并发128、max_num_seqs128、batched_tokens16384、显存0.92、上下文16384、输出6144、thinking=False、视觉预算约2048 tokens、timeout600。此条覆盖此前DP=2及8192视觉输出的本轮约定；实际吞吐由GLM运行验证，不能预称已打满。
- 运行参数先改notebook再提交同款；已有完整响应可复用，不等同阶段断点恢复，未完成请求不自动重试。新协议写独立run，历史表/公共数据不改。正常任务完成与技术失败分别计数，验收读固定结果版本和complete，不凭锁释放判断成功。

### Edit 共用原图以 add 用途标注和分级（2026-09-27，用户认可 review 后要求落实）

- 视觉 prompt 当前为 `edit-source-image-5`，按“整图 → 区域 → 新增候选 → 关系要求 → 用途等级”说明每个字段。caption 与场景/视角描述属于整图；区域描述可见事实、位置、空间与占用、锚点、physical_conditions 和限制；新增候选包含 object_types、fit_reason、relations(type/evidence/requirement)、observability。位置和对象类型须有承载、功能、结构、环境或关系依据，不能仅凭“有空位”给所有图附通用对象清单。
- 允许有依据的复合关系和物理条件，不强求复杂、多区域或多标签；普通光影/位置要求不能自动当作概念核心考点。不得推断隐藏内部、精确物理数值或静态图无法证明的动态结果。add 保留现有对象身份/位置，允许新增引起的必要阴影、反射和遮挡；不能靠删改已有对象腾位置。
- `add_suitability=high/medium/low/unsuitable/uncertain`。前三档是已确认可用的不同用途等级，均对应 keep；后两项对应 reject/hold。high 看具体适配依据和可观察约束，medium 看明确的基本承载，low 表示已确认但限制显著、用途窄；不确定不得伪装成 low，不按关系复杂度/标签数量/审美或预期模型成功率打分。
- 用户进一步要求整体移除干扰信息：视觉模型只接固定任务说明和图片，不传 source_concepts/source_groups、上游筛选理由或文件元数据；删除 source_match/source_match_reason 来源审计输出。来源字段仍沿业务数据流用于召回、关联和追溯，但不构造视觉 prompt_payload、不进入视觉字符预算。本条覆盖此前来源匹配审计与 keep 必须 source_match=match 的约定。
- Prompt 保留直接影响视觉判断的新增条件、区域与对象适配、可见依据/约束/可检查性、等级和检索标签。上游来源、后续出题分工、人工审核、工程处理等说明留在文档，不要求模型同时处理主任务外的流程。同步删除不再需要的输入绑定、响应 schema、校验与图卡字段，不能只删提示词里的提醒而仍发送干扰信息。
- 模型仅输出等级与原始标注，不输出 decision 或 retrieval_tags。业务 check_image 按等级确定性派生 keep/reject/hold，从 scene_groups、区域 anchor_tags/physical_conditions、候选 relations.type/object_types 按出现顺序去重汇总五类检索标签，不截断。原始响应不改写，派生字段保存在 annotation/交付表供筛选与展示。此条覆盖此前要求模型输出映射及重复标签的约定；结构合格不冒称像素和物理依据已经人工验证。
- 同步业务 Arrow schema、响应校验、等级进度/摘要、Notebook 图卡和等级过滤。新版视觉表不与旧 schema 混写，执行视觉阶段前检查已有输出，冲突时要求新 run/target；旧表不迁移/补算评分。当前4B分类 prompt、参数、run 和 image_checks 边界保持，新视觉输出预算在 Notebook 配为8192 tokens，开发不自动提交视觉模型实验。

### T2I high 内接续三级分类判断（2026-09-27，用户要求）

- coarse_screening notebook 第二格复用同一个 config/run_pipeline。用户最新要求将 1,477 个 high 直挂概念与 2,043 个真实三级分类一同分组判断，共 3,520 项。正式入口增加可选 short_path_policy='concept'，不新增 pipeline；每批40项，真实分类最多5样例、概念项只有自身1个样例，模型/推理/预算/并发参数继承第一格 CONFIG。
- 必须接第一轮完整交付的 state。按同一 state 的 reviews 取 high 二级前缀，taxonomy_depth=3；范围外路径不带入。有任一 high 路径达到三级则仅归真实分类；都不足时每概念补一项，最长短路径作上下文，同长度按字典序。category_kind=category/concept 沿 memberships/inputs/reviews/category_priorities 留存，原 taxonomy 不加补位标签；真实分类与同名概念不混并。无 high 范围时停止，不能传空前缀退回全库。此条覆盖此前短路径暂不送审的约定。
- 五条核心门槛和响应 schema 共用；只有 concept 策略附加 short_path_concepts.txt 单概念适用说明，不要求概念项满足“类内多个概念带来不同考点”。默认 category 策略保持原 prompt/模型载荷/分批摘要，可复用第一轮固定 reviews。混合轮新 run 为 high_l3_with_concepts_batch40_v2_20260927，不误用旧三级轮缓存。
- 默认 RUN_L3_PIPELINE=False，只读范围和已保存结果；用户手工切换执行。prepare 零模型请求，可核对实际批数；modelhub 才调用 GLM。结果三档全部落新池，固定来源为 L3_SCREENING_SOURCE；下游逐概念双模型粗筛仍单独决定规模。保留第一格已有输出和参数，开发不自动提交生产模型实验。

### Edit 原图接续四级分类与实例细筛（2026-09-27，用户要求）

- 原图 pipeline 新增互斥的接续输入 `concept_input_source + checked_image_source`，均显式固定 URI/version；与公共 `concept_source + image_source` 二选一。接续只读前轮 image_checks 中 ready 图片及其 source_concepts，原生 Dataset 去重后关联完整概念快照；不重跑三级粗筛，不回扫公共图片或补回被前轮排除的图片。
- 本轮 notebook 为 `scene_pool_category_l4_v1`，来源是 `scene_pool_category_l3_v1` 的 concept_inputs@5、image_checks@1；183,013 张图关联 88,878 个概念。`taxonomy_depth=4, short_path_policy='concept'`：有四级路径时按前四层归类；所有路径均不足时，每概念一个实例末端，最长短路径作上下文、同长度按字典序，不虚构中间层。实际 4,174 个真实分类 + 27,162 个实例 = 31,336 单元，category_kind 留表区分两者。
- 沿用原 screen_source_category prompt、版本与 schema，载荷仍仅 domain/category；实例名称放入 category 末端，不增加成员样例。该规则覆盖此前“文本永远不含概念名称”的表述，仅限短路径实例补位。每单元一次4B调用，任一分类 keep 则召回该概念；回连图片只保留本轮 keep 概念/分组，并按 SHA 合并，固定 Blob、尺寸与尺寸来源沿行保留。
- 新持久边界 `through='image_checks'` 在全量回连图片后返回，尚未应用 max_images/max_images_per_concept、未调用视觉模型。Notebook 本轮默认该边界，4B 双卡DP=2、并发128、max_tokens=1024、thinking=False、新请求预算40,000；所有参数均由 config 传入，公开入口和 CLI 同步。
- 用户明确本轮手工启动/监控，开发只做隔离响应回放和正式来源只读范围核对，不自动提交31,336次调用。Notebook 默认 monitor，手工改 run 后执行统一入口；预览默认本轮最新摘要、各结果表仍按摘要固定版本读取。每阶段打印输入/输出、数量、状态、复用和耗时；保存本次准备/执行摘要，未完成/失败不沿用旧成功。完整请求响应可复用，未完成请求仍不会隐式重试。

### T2I 第一轮全量落表、第二轮决定规模（2026-09-27，用户最新纠正）

- 本条覆盖此前“第一轮抽 5,000 个 high 候选”的约定。第一轮只做全库分类初筛，发布每唯一概念一行的 concepts 全集，high/medium/low 均保留；完整 taxonomy 和 category_priorities 不丢。顶层 priority 是最高所属档，只用于选择范围，不代表逐概念通过。
- 第一轮入口/CLI/notebook 删除 sample_size、sample_seed、max_per_category、exclude_sources 及 quotas/samples/提交去重实现；完整池采用 overwrite 快照，版本由 Lance 保留。新 run 为 all_master_category_pool_v1_20260927，复用已完成 reviews@1，不重新调用分类模型。
- 第二轮 notebook 决定实验规模、种子、单类上限及历史排除，实际 high 抽样数据流在 fine_screening 正式入口。SAMPLE_SIZE=None 表示尚未决定规模，不生成可执行 CONFIG；必须先填正整数才能运行，不预设 5,000，也不把 None 当作全 high 执行。可用 prepare 只落本轮样本/配额/输入，不创建模型请求。
- 第二轮只在 high 内尽量覆盖分类和完整路径，多分类概念去重，数量不足时报错，不从其他档补足。GLM Flash 与 DeepSeek Flash 独立读 name/taxonomy、无图文和分类理由，全部有效且全部 keep 才通过；开发不自动提交第二轮生产模型实验。
- 第一轮 notebook 继续展示 2,754 类的全集资源概览：概念档内按名称、图片档内按 SHA 去重，跨档可重复，不能混成池顶层最高档归属的互斥计数。图片是关联储备，不表示出题材料审核通过。保留历史表和 notebook 已保存输出。
- 全集已用 notebook 同一 CONFIG 实际落表为 concepts__all_master_category_pool_v1_20260927.lance@1：385,292 个唯一概念，high 110,568 个；完整复用分类结果，零新增模型请求。第二轮源固定到该版本，SAMPLE_SIZE 仍为 None，未提交第二轮实验。

### 平台改进事项统一登记（2026-09-27，用户要求）

- 共同工作区根目录的 [DEMIFLOW_PLATFORM_TODO.md](../DEMIFLOW_PLATFORM_TODO.md) 专门维护 demiflow 平台待办。DF-001 记录彻底移除 LanceRecordStore 的范围，包含模型日志、其他 pipeline 及历史引用迁移；已完成的 coarse_screening 业务状态改造单独标明。
- 用户当前明确先推进项目中的 T2I 工作，平台事项后续逐项优化；登记待办不自动触发全平台改造。

### Sampling 运行状态使用显式业务表（2026-09-27，用户讨论后执行）

- coarse_screening 的业务运行摘要和 append 提交记录由本 pipeline 定义 SUMMARY/SAMPLE_COMMITS schema，通过标准 data.read_lance/write_lance 存取 summary__run 与 sample_commits__run；正式入口和 notebook 不再导入 LanceRecordStore。启动先写 preparing 且结果引用为空，阶段完成才登记固定 URI/version，失败不冒充旧成功。
- 本模块 4 个既有运行的最后状态已迁为 summary@1，旧 records/调用日志/结果表保持原版本；notebook 的参数、用户删减的输出及 high/medium/low 分布展示保持。业务表跨阶段仍固定版本读取，只有运行摘要用于查询当前进度。
- 用户进一步询问能否连模型日志一起删除 LanceRecordStore，以及其他 pipeline 的依赖。已查到精筛（fine_screening）、T2I/Edit 出题、原图准备、训练、preparation 和评测的直接/间接使用，另有平台日志/运行/维护与历史迁移工具。当前仅 coarse_screening 业务状态已完成替换，模型日志内部和其他流程尚未迁移，不能声称该平台模块已删除。

### 概念挂载合并进主表（2026-09-27，用户要求，已执行）

- 用户明确要求合并并删除 `datasets/concept_taxonomy.lance`。概念与挂载统一由 `datasets/master_concepts.lance` 保存；本条覆盖下方独立 memberships 权威表、四表发布和保留四张主数据表的旧约定，不重建关系表。
- 主表@3 为 385,292 行。原 name/aliases/carriers/taxonomy 与来源字段逐值保持，taxonomy 仍为 JSON 路径列表；新增 `taxonomy_metadata: list<struct>`，按原路径顺序保存全部 446,775 条关系的原 taxonomy_node_key、ordinal、source_ref、source_record_key、migrated_at_us。元数据内节点键保留 `demiwtg / ` 前缀，概念键由行内 name 提供。今后挂载变化在同一主表版本内同步更新 taxonomy 与对应元数据。
- 默认采集发布为 `master_data_merged_20260927`（master@3、nodes@1、edges@1）。旧 `master_data_current_20260921` 四表发布、关系表登记和旧映射已退役；历史登记快照与审计内容不改写。主表@1/@2 保留，已有 notebook 的固定@2与保存输出保持。
- 新 schema 属于 `collect/schemas.py`；一次性入口为 `tools/lake_migration/merge_concept_taxonomy.py`，控制计划和逐字段核验回执在共同根 `_demiflow/concept_taxonomy_merge_20260927/`。删除前完整回读原字段与新增元数据，不把旧关系表 URI 映射为不同 schema 的主表。

### Preparation 按目标表拆分（2026-09-27，用户要求）

- 现役入口按两张公共目标分为 `preparation/articles/articles_pipeline.py` 与 `preparation/images/images_pipeline.py`，各自 notebook 为 `articles_debug.ipynb`、`images_debug.ipynb`。本条覆盖下方文章/视觉统一在 preparation_pipeline 与 preparation_debug 的旧约定，不保留旧入口转发或软链接。
- 文章入口只写公共 `datasets/articles.lance`；配图仍参与文章审核，直接复用图片入口的 `run_image_review` 子图并保存运行依据，不再自动提交公共图片表，也不保留 `visual_target_uri/visual_write_mode`。图片入口统一负责目录补尺寸、视觉导出与响应重放，目标为 `datasets/images.lance`。
- 两个 preparation 入口共用父目录的 `operators/`、`prompts/`、`tests/`，不复制算子和提示词。仅允许文章入口导入图片入口的 `run_image_review`；算子仍不得导入 pipeline。两条入口均纳入统一布局检查。
- 本次为代码与入口整理；阶段、日志、历史运行仍平铺 `preparation/datasets/`，公共表位置、固定引用及 notebook 已保存输出和用户运行开关保持。同步 CLI、调用方和源码登记，测试使用隔离数据根，不启动生产或模型调用。


### T2I 分类初筛与分层抽样（2026-09-27，用户要求）

- 在 `benchmark/t2i/coarse_screening/`（原 sampling）新增标准 pipeline，唯一入口 `t2i_coarse_screening_pipeline.config(...) → run_pipeline(config)`，单格 notebook 配置实验参数，登记统一布局检查。主线显式读取 master 固定版本、展开分类、聚合跨叶样例、分类模型请求、关联优先级、分层配额和候选 writer；行/分组计算放 `operators/`，不新增平台 API。
- 分类 high/medium/low 仅分配抽样投入，概念仍须后续严格 keep/hold 筛选。分类模型初始采用已接入的 `glm/glm-5.3`，具体模型和抽样权重在 notebook；不将“较强模型”当作本任务效果已验证的事实。不提供文章/图片，不因 low 永久删除整个分类。
- 分类 prompt 完整提供下游概念筛选的五条重要规则作为质量上下文，并写明分类层面的用法：评估样例是否支持类内经常存在符合全部规则且有不同核心价值的方向，不逐样例出题/输出 keep/hold，不靠一个优秀例外抬高整个分类。修改规则时同步核对上下游 prompt；分类理由仍不传给下游概念筛选。
- 默认全库按二级分类、每类最多 5 个跨完整路径样例；同大域按分类名排序，每次最多 40 类共享完整五条规则，超过字符预算提前拆批。分类响应为 category/priority/sample_fit/reason 数组，漏项/重复/额外分类使整批无效；请求预算和 usage 按批计数，明细仍每类一行。固定分类样例种子和独立抽样种子；大域保底、分类/大域上限及探索比例由调用方显式配置，分类结果未全部有效时不交付新样本。
- 多路径概念保留完整 taxonomy，只选择一个配额归属避免重复；按优先级再稳定哈希选归属，不宣称完成义项消歧。候选 name/taxonomy 与现有 fine_screening 主表协议一致，输出 sampled 不表示筛选通过；分类理由不传给后续概念筛选。
- 可显式复用固定版本 reviews，并从固定历史样本按 concept anti join 排除；绑定完整批次输入/prompt/schema/模型（包括同批其他分类），相同才复用。分类摘要、请求批次、判断、配额和候选表平铺本模块 datasets，公共主表只读。Python 默认 prepare；Notebook 保留用户手动设置的运行开关与已有输出，开发不自动启动生产模型。输入 token 按实际批次估计，服务 usage 去掉同批分类重复计数。
- 首轮 all_master_batch40_v3_20260927 的 86 批有 74 批在 8,192 输出上限处截断，绝大部分预算用于推理；用户明确选择保持 max。Notebook 新运行 all_master_batch40_v3_retry_max_20260927 显式 max、输出上限 65,536（含推理）、超时 1,800 秒。retry_source 固定旧 reviews@1，仅复用整批全 reviewed 的 12 批，其余 74 批重试；与全量复用 review_source 互斥。prepare 显示 retry_plan，不发请求。旧表和日志只读；新旧结果保留真实调用引用。模型参数改变必须考虑原生日志的持久请求预算和失败响应缓存，不能仅改上限后同名重跑；不因技术截断放宽五条规则。
- retry_max 轮的 74 个新请求均完整结束，合并后 84 批成功、2 批因漏分类/改分类名而无效。当前 notebook 改为 all_master_batch40_v3_retry2_max_20260927，retry_source 固定 retry_max 轮 reviews@1；复用 84 批、仅重试 2 批。参数与输入不变，旧输出保留，仍由用户手动运行。
- 用户随后明确授权在 VS Code 无法连接时，将 retry2 轮按 notebook 相同 CONFIG 后台提交；此授权覆盖上述本轮手工执行约定。后台仍调用唯一正式 run_pipeline，成功后自动完成本轮 5,000 个候选抽样，不自动启动下游粗筛或出题。

### T2I 粗筛双模型与 Codex 正式出题（2026-09-27，用户要求）

- 粗筛位于 preparation 与正式出题之间，主表使用公共 `datasets/master_concepts.lance` 的概念和完整 taxonomy；当前不提供文章或图片描述。筛选规则优先概念核心、有价值的核心区分潜力和可靠判分，边界案例暂缓，不设通过率目标。
- 实验模型名单放 fine_screening notebook：`glm/glm-5.3-flash` 与 `galaxy/deepseek-v4.1-flash` 独立读相同输入；全部有效且全部 keep 才保留。结果每概念一行，顶层 decision 存合并结论，model_results 保留每模型原始六字段、执行状态和调用引用，不拼造共同考点。技术失败不得改成 hold；预算按每个模型节点计。
- 用户要求正式 pipeline 可直接调用本机 Codex，无需另开桌面对话或手工转交。T2I V2 的可选 codex 模式保持原生 prompt 节点、日志、校验和 writer，通过非交互 CLI 执行；不是 Python 直接调用当前会话的 collaboration 工具。
- 正式出题每概念新建执行上下文，不续接当前讨论或其他概念历史；只传正式出题请求和该概念材料，不传粗筛理由与候选考点。基础指令仍存在，不宣称完全空白上下文。模型和推理参数显式配置，保留真实执行身份，不把 Codex 结果记为 GLM。
- 正式出题与粗筛共用五条质量门槛，针对最终题目重新检查核心联系、核心区分潜力、外围生成负担、可靠公平判分和自然代表范围。质量不够可直接返回 `question: null` 与具体原因，不能因粗筛通过而凑题；有效空题为业务不足，不进入候选表。
- 无材料是正常输入，允许充分使用可靠已有知识；用户计划后续用 GPT-6 出题，必要时由出题执行者检索核验关键事实，不强制逐题检索。Codex 配置 `codex_web_search` 默认 live，可选 cached/disabled，进入请求缓存身份及日志；HTTP 模式不自动获得工具。检索事实和实际来源链接写入已有 basis，不虚构输入材料编号，也不自动作为作答模型材料。此处允许作者在同一次出题执行中核验事实，不新增独立检索 pipeline 或审题阶段；具体模型仍由调用方显式设置。
- Notebook 保持用户手工执行，保留已有输出；新粗筛协议使用新运行名，历史表不回写。开发仅隔离测试和只读检查模型/CLI 能力，不自动启动生产模型批次。

### Subset 先选实体再选图、进度与续跑（2026-09-27，用户授权）

- 用户报告 `pilot100k_bucket_v1_staged_v3` 在 images 的数量/字节校验失败。定位为源表两个 SHA 各重复一行；完整所选 SHA 集合、去重字节数及前序阶段均一致。最终 join 后增加同键 reduce，按相同准入规则保留一条完整原记录（非补回变体优先、稳定消歧），大小冲突明确失败；不改抽样、不下载。透明进度回调改传绑定方法，避免 callable 深拷贝让计数始终为0。
- 此修复对该失败运行做一次限定旧指纹、完整来源/阶段校验的恢复迁移，原回执及新旧代码差异保存在 subset/datasets 的 `progress_before_image_dedup__...json` 与 `repair__..._image_duplicates.json`；已完成阶段原版本保持，images/summary 不登记完成。此条仅覆盖本次已审计修复的同名续跑，一般代码/配置改变仍拒绝复用。Notebook 参数不变，仍由用户重启 kernel 后手工运行。
- 用户要求优化 subset 使用 demiflow 的编排，提供 notebook 运行、进度与 resume。本条覆盖旧版先全库选图再选实体的执行顺序及当前后台运行约定；抽样配额/种子/来源/选图语义不变。
- 冷启动按标准 Dataset 链：窄 QID—SHA 关系 → 精确容量 → 关联分类 → 配额 → 选 QID/深度队列 → 仅入选实体选图 → 预算 → 公共 concepts/images。普通实体只保留基础图前缀；简单计数求和用 aggregate。demiflow 小右表 inner/semi 先过滤左侧再排序，保持原稳定顺序，无新平台 API。
- 阶段表、JSON 回执与日志平铺 subset/datasets；writer/reader 仍在正式 pipeline 可见。operators/runfiles 只管理业务运行记录、透明行计数和进度，不执行 Dataset、不隐藏表读写。回执绑定配置/代码/来源以及提交版本/时间/schema/行数；未登记阶段不自动视为完成。resume 是阶段粒度，不能承诺排序中间恢复。
- Notebook 当前 run 为 `pilot100k_bucket_v1_staged_v3`，显式 resume=True、30秒日志、512MiB join 块预算。新版由用户手工执行同步 run_pipeline；首次需重启旧 kernel，不采用中断 await 后线程继续写表的模式。保持保存输出，不伪造生产完成状态。
- 旧 framework_v2 后台已停止。用户已完成的全量池 `qid_pool__pilot100k_bucket_v1@1`（4,998,328 QID）及 `quotas__pilot100k_bucket_v1@1`（20,938行）通过固定哈希的 `reuse_pool__pilot100k_bucket_v1.json` 回执复用；读取前检查与当前全部采样参数及原表身份一致。这是已完成全量阶段的恢复，不是旧小候选名单准入。
- 完整 resume 返回原登记结果，不覆盖用户补图后的新版本；需要继续发布时仍保留补图保护。配置/代码改变须使用新 run。公共数据仍只有 concepts/images 两表，图片下载由用户执行。

### demiflow 通用关联优化与 subset 重试（2026-09-27，用户授权）

- 用户明确要求先优化 demiflow 的通用关联与重复排序，再按原参数重试；不得在 subset 另写绕过框架的处理流程。该授权覆盖此前本次不修改平台的开发限制，现有 Dataset API 和采样语义保持。
- 框架内实现有界小右表索引、重复键内存组/热键落盘、排序块批量 IO、直接同键 join→group 顺序复用；按现有 local workers 上限以 spawn 进程处理大排序块与中间归并。任意 Python reducer 不假设可分解，不自动并行化业务回调。
- 原 retry1 在候选池@1完成后停止并保留对照。Notebook 当前 run 为 `pilot100k_bucket_v1_framework_v2`，源版本、分类快照、全部采样参数和公共目标名不变；新后台 CONFIG 仍从 notebook 导出，唯一配置差异为 run。状态中记录框架实现 SHA256，成功前不把新任务标为完成。
- 用户允许必要时以 Rust 实现实际用到的 demiflow 内核；先依据优化后性能剖析限定热点，不因此扩大业务范围或把分类、配额、抽样规则移入原生内核。当前生产重试仍使用已验证的 Python/多进程实现。

### QID subset 独立 pipeline（2026-09-27，用户要求）

新增理由：用户要求将大域配额抽样落成 `demiwtg/subset/` 标准 pipeline，从 collect/datasets 读源，向公共 datasets 交付 `qid_sub_*` 新数据集；图片由用户从 COS 补充。

- 正式入口 `subset/subset_pipeline.py`，实验参数在 `subset/subset_debug.ipynb`；遵循下方 T2I V2 标杆和标准 Dataset 编排，加入统一布局检查。不新增平台 API。
- 用户要求 notebook 参数始终与实际提交的后台任务一致：后台配置从 notebook CONFIG 导出，参数变更先同步 notebook；不得只在临时启动脚本里覆盖。Notebook 保留本轮只读配置对比、状态查看和成功结果加载入口，后台同样调用正式 run_pipeline；不伪造 notebook 执行输出。
- 图片与概念读 collect 固定 Lance URI/version；原有分类 TSV 用明确 SHA256 固定，标准 reader 逐行解释。原始采集表只读，不依赖临时抽样缓存。当前完整图片账本为 `qid_images_v2@10` 共18,437,871行，fat仍为@8；旧17,756,205行测算不是当前源容量，运行时重新计算。
- 配额/候选/运行汇总平铺于 `demiwtg/subset/datasets/`；一个 pipeline 交付公共 `datasets/qid_sub_*_concepts.lance` 主表和 `qid_sub_*_images.lance` 图片表。从 images 统计容量并抽取 QID/图片，按 QID 关联 fat 完整原记录写主表，再从固定主表选中的 SHA 派生去重图片表。主表有序 `image_sha256s` 和 `base_images` 保留选图关系、顺序及基础/增量边界，图表 `selected_qids` 为所选关联反向索引；不另写公共关联表，不按 QID 带回全部来源图片。此处命名子集是用户明确请求的数据资产，不是复制 preparation 样本运行历史。
- 用户要求先原样搬原字段、不关联 xref：输出 schema 继承固定原表全部字段/类型，再加采样和待补图字段；原始 `qids`、`size_bytes`、`cos_loc`、变体信息、fat页面ID/P18/P373/溯源均逐值保持，不用选中关联覆盖原始 qids，不把 size_bytes 改名。新增字段撞名明确失败。概念原记录缺失时保留所选 QID，其他原列为空，不补外部标签、不执行原文档的全库概念合并任务。
- subset concepts 还须携带既有采样分类：`main_class/class_id` 来自 `recut_v6/qid_cut_map_final.tsv` 的 main_class/cat_id，`bucket` 来自 `cls_final_4951_浏览版.tsv`，`class_label` 优先读固定 `category_source`（`recut_v6/cut_categories_final.tsv`，含可读标签）；未配置时用桶表原label。`sampling_group/domain` 保留现有派生意义。三份 TSV 读取前后核对 SHA256，分类显示名不改变配额，不将这些字段误称为 fat/images 原列或旧 taxonomy 已完成对齐。
- 按图片原表说明用 `cos_loc.domain/key` 定位后续下载，禁止根据 blob_path/SHA 重建实际 COS key；默认 `require_cos_loc=True`，无位置行在统计容量前跳过并计数，不发网络请求。图下载由用户另行执行；subset不调用COS或出题模型。
- 身份消歧、语义错桶检查和任务适用性判断暂不作为 subset 前置阶段；先抽样、补图、试用下游，根据实际反馈再修正。保留现有元数据/配额/预算检查，候选入库不表示任务审核通过。
- 用户后续确认改为固定 `bucket_targets`：总量 10 万，商品 10,000、人类 9,500、组织机构 4,500，其余 28 桶配额见 notebook。`domain` 是人为汇总字段，不参与重新分配；桶内原始类代表保底，之后按 ≥5/4/3/2/1 图档优先、同档采样组均衡。`core_target=80,000` 为争取目标，当前接受的 `core_minimum=73,577` 为硬下限；不足就失败，不能跨桶挪名额或静默减类。完整来源容量重新计算，候选图片仍仅经过元数据检查。
- 深度队列按主子集桶配额加权，字节上限与单桶图片关联上限均由调用方配置；基础预算不可满足时明确失败。
- 图片 Blob 留空且明确 metadata_only/pending_cos；保留固定元数据来源和 COS 路径，不伪造可读 BlobRef。用户于2026-09-27明确授权按 notebook 的已定抽样逻辑后台运行全量元数据抽样，仍不下载图片。已有补图目标禁止被元数据重跑覆盖。
- 首次启动时上游桶浏览 TSV 已改变50个准入类的桶归属，旧哈希校验失败且未写子集。为遵守用户“按之前定下的逻辑”，本轮 bucket_source 固定 `subset/datasets/bucket_assignment__pilot100k_bucket_v1.tsv`（SHA256 `68cfd3e93e97104c3f733d4474973c67ec425ef84768c7771fd6d2fa04572d1e`）。仅从历史清单恢复完整4779组→桶映射，无冲突，覆盖4927准入类/5,067,011原映射实体；其余24类继续暂缓。逐项依据见同目录 `bucket_assignment_audit__pilot100k_bucket_v1.json`，未改 collect 原件。实际QID/图片候选仍从全量collect@10重算，绝不使用历史候选QID名单限制范围；配额及其他配置未变。

### 三层数据架构（2026-09-27，用户确认）

本条覆盖下方历史记录中“preparation 公共结果放本模块 datasets”及“qid 表放公共 datasets”的布局。路径均相对 `project.resolve_root()` 返回的共同工作区；生产者所在模块不决定公共交付物的存储位置。

| 层 | 存储位置 | 数据与职责 |
| --- | --- | --- |
| 原始采集层 | `demiwtg/collect/datasets/` | 原始图文 `images/documents.lance`、`qid_images_v2/qid_edges/qid_concepts_fat/qid_concept_xref.lance` 等采集事实与来源；不混入应用标注或出题结果 |
| 公共样本层 | `datasets/` | preparation 生产的目标样本表，以及概念主表、taxonomy 等跨 pipeline 共用材料；图片基础信息、描述、审核状态和固定来源引用在此交付 |
| Pipeline 应用层 | `demiwtg/<pipeline>/datasets/` | 各流程的输入快照、必要中间结果、调用记录、原图候选池、题目、训练条目和评测结果 |

- **数据流与入口可见**：采集写原始层 → preparation 读固定采集版本、清洗/补尺寸/按需标注 → 写公共样本层 → benchmark/训练/评测读显式公共 URI/version → 写各自应用层。图片字节沿公共样本交付的固定 source_refs/BlobRef 读取，下游不绕过交付关系重选默认原始表。具体配置放 notebook，正式入口直接绑定 reader/writer。
- 公共样本的存储层不代表审核通过；未标注、未审核和不可用状态保持显式，下游按业务条件选择。全量图片目录只按基础列 merge，既有标注和审核列不重算。
- **preparation 现役公共目标为 `datasets/images.lance` 与 `datasets/articles.lance`。** 起初一并迁出的 `knowledge_base__bench200_production_20260920_v8.lance`、`metadata__test__configured_answering.lance` 已按用户后续要求删除，其现役登记和旧位置映射同步清理；不恢复或重建。公共样本层不新增按运行复制的样本目标表。
- 目标样本支持按主键更新所负责的列。当前图片目录补齐已通过窄 schema 按 SHA 更新基础列，未提交的标注/审核/发布列保持，新增 SHA 的这些列为空；普通文章/视觉 writer 仍是完整业务结果按主键 merge，不把它描述成任意选列更新接口。按列更新与是否执行某个计算/模型阶段是两回事，跳过标注必须不执行标注节点。
- 业务表在各自 `datasets/` 下平铺；run/阶段/题目编号作为表名后缀，不新增 runs/history 数据子目录。全局登记与仍被引用的公共证据继续留在公共目录，`_demiflow/` 仅放锁、精确位置映射和迁移回执；这些辅助设施不是第四层业务数据。
- 迁移采用同文件系统整目录移动，连同每表控制目录保留全部行、版本、索引和字节。旧冻结引用通过共同根 `_demiflow/lance_locations.json` 精确映射到新位置，不改历史引用和 notebook 已保存输出；新代码、默认值、测试及 notebook 配置使用新物理路径，不建旧路径软链接。
- 现役布局、迁移入口及验证说明见 [三层数据布局](tools/lake_migration/FLAT_DATASETS.md)。本次目录迁移不等于执行全量图片补齐；该流程仍由用户手工运行。

### Preparation 全量图片目录与按列补尺寸（2026-09-27，用户确认）

- 用户要求将采集全量图片纳入 preparation 并补齐尺寸，由用户手工执行；本次不启动生产全量任务、不调用标注模型。入口 `preparation_pipeline.image_catalog_config(...) → run_image_catalog(config)`，配置及手工调用位于 `preparation/image_catalog_debug.ipynb`。
- 全部采集 SHA 进入目录，独立于文章/视觉审核概念名单。先复用已有实测宽高；仍缺尺寸且有字节时按固定来源读取图头补齐，不放大，不用来源声明尺寸代填。`dimension_read_mode='header'` 是补尺寸默认模式，来源记 `blob_header`，不重复全图 SHA/结构校验；`full` 保留完整校验，来源记 `blob`。尺寸 ready 不表示通过图片内容校验。小图/生成图/无字节记录不从目录删除，缺字节与读取失败保留独立状态。
- **只更新基础列，不搞标注。** `source_refs/concepts/width/height/availability/byte_size/generation_origin/dimension_source/dimension_status/dimension_error` 通过窄 schema 按 SHA merge；未提供的描述、匹配、审核、published 列保持原值。新 SHA 的标注列为空，采集概念关联不自动升级为审核或发布关系。
- reader 固定采集与旧目标版本，主线可见 SHA join、补尺寸批算子、阶段 writer 与部分列 merge；最终更新前检查目标版本，保留旧快照。缺字段只扩列；不回写原始采集表、不重算旧标注、不覆盖整张审核表。
- 默认 notebook 为全量 `max_images=None`；批量和 I/O 并发可改。2026-09-27 首次全量已完成并恢复失败的末尾合并：公共 images@9 共 2,164,671 行，其中 ready 2,127,754、missing_bytes 36,911、read_error 6；旧 900,883 行的标注保持。下游按显式 URI/version 使用，历史 @5 仍是结果子集，不能把 @5 说成全量。
- 性能要求：缺尺寸图每批一次 SHA 定位/Blob 句柄提取，全流程共享 I/O 并发池；跨本地批处理 worker 汇总总进度，不能把单 worker 计数标成全局进度。Pillow 超大尺寸 warning 汇总为计数，不刷屏；仅在受锁保护的图片打开作用域处理 warning，不关闭全局像素硬上限。正在运行的 notebook 不自动热替换、中断或重启，代码更新用于后续显式调用。
- 已完成阶段表可由 `catalog_source={uri, version}` 显式复用，仅核对来源绑定、唯一 SHA、行数后重试按列合并，不重复读图。`prepared_source` 仍需固定当前目标版本；失败回滚会生成新版本，不能继续用旧 head。`merge_memory_bytes` 显式控制合并内存池，默认 8 GiB，仅在本次合并期间设置 `LANCE_MEM_POOL_SIZE`，完成/失败均恢复原环境。阶段状态汇总用 Lance 列式聚合，不为几个状态值展开、排序两百万条 Python 行。

### Edit 共用原图准备（2026-09-27，用户确认；三级分类试跑更新）

- 正式入口 `benchmark/edit/source_images/source_image_pipeline.py` 按标准 config(...) → run_pipeline(config) 编排主表分类、4B 剪枝、原图关联、SHA 去重、尺寸过滤、视觉标注和 Lance writer；素材准备的两个 prompt 不改变 Edit V2 单题约定。
- **按全库概念 taxonomy 前三层归类，第一层为大域，4B 只看 domain/category。** 默认 taxonomy_depth=3，例如动物 / 鸟类 / 猛禽 / 鹰归入动物 / 鸟类 / 猛禽；不足三层保留已有路径，空分类留组。同概念多个深层路径落在同类时去重。当前 master@2 为 385,292 概念、7,917 个三级分类、29 大域；二级为 2,754 类。本条覆盖此前二级默认配置及 16,692 个末端路径加代表概念的方案。
- 分类模型不接收概念名称、别名、代表样例或成员数量；计数仅落表核对，删除 examples_per_leaf/example_seed 参数。keep 后展开分类内全部成员，多分类概念合并保留的分类与分组，不再逐概念 4B 调用。类别不变时成员变化可复用同一分类请求。
- 分类是宽松第一轮剪枝：存在稳定的一批场景/空间/承载面来源即可 keep，不因混有无用成员就整类排除；明确无关 reject，无法确定 hold，hold 在结果表待复查而不进入当前图片批次。具体错绑、质量和可编辑区域由视觉模型按像素判断；分类 keep 不代表概念或图片审核通过。
- notebook 配 taxonomy_prefixes=[]、concepts=None、max_concepts=None，无手写白名单或 200 概念截断。prefixes 是可选实验范围，concepts 精确名单覆盖它；taxonomy_depth、尺寸/限量、模型/并发/预算均在 notebook。文本预算 8,000；每概念最多 4 图，首轮 image_limit=500 同时绑定 max_images/max_image_calls。用户查看 keep 比例和阶段耗时后，可手工改为 5000 扩池；保持同一 run 和 overwrite 复用相同请求并交付扩池后的完整结果，不保证最终池数量或场景配额。
- 来源显式固定公共 master_concepts@2 与 images@9（2,164,671 行）。新增无标注行通过 concepts 参与候选召回，197 个历史发布概念不限制范围；published_only=True 才检查 published/keep。沿 source_refs 使用固定 Blob，不查 collect latest 或重绑，不回写公共表。
- category_inputs/category_results 每分类一行；按 category join 主表分组后写 concept_candidates，图片按 concept join、SHA 合并及过滤。候选模式只读公共基础列，dimension_status 为空的旧行才提取历史实测尺寸；已知 read_error/missing_bytes 保留错误不重读，其余缺尺寸才按固定 Blob 补齐。min_short_side 必填，notebook 严格 >1024，小图在数量限额前排除，结果写 image_checks，不放大原图。
- 明确生成标记排除，未知来源不伪称已验真；缺引用报错，技术失败不变成业务 reject。视觉记录实际可见区域、视角、空间、限制和检索提示；同 SHA 只调用一次，hold/reject 不入池，机器 keep 输出仍 unreviewed。
- through=categories/image_inputs/pool 控制分类、图片候选及最终池边界；相同模型请求按原生日志复用。notebook 三级运行名 scene_pool_category_l3_v1，历史表和已保存输出不回写。手工 notebook 用 local（4B 文本 + 27B 视觉），Python 默认 offline；开发仅隔离验证，不自动启动完整分类/视觉生产批次，不停止无关服务。
- 试跑摘要返回 image_decisions，以及 category_elapsed_s/image_elapsed_s；notebook 的视觉 keep 比例仅以 annotated 数量为分母，技术失败/pending 单列。阶段时间包含响应复用和读写、视觉还含读图，不称为纯推理时间或用缓存重跑推算新请求吞吐。
- **后台运行授权与参数一致性（2026-09-27，用户要求）**：用户因 VS Code 断连明确授权本条 pipeline 加速并后台执行，覆盖此前仅手工启动约定；范围仍按 notebook 的全量三级分类和首轮 image_limit=500。每次提交前先同步源 notebook 的配置，后台用标准 nbconvert 直接执行它、指定项目 demiwtg 内核，保存执行副本与启动回执，不能另写一套隐藏的参数或业务流程。当前文本/视觉并发 64/16，4B 服务启用 CUDA graphs 和 max_num_seqs=64；保持模型请求内容、来源版本和 run 身份，已有完整响应复用。log_path 同时输出后台可追踪的进度文件。
- 本次性能证据指向调用日志：4,285 个单行分片及反复打开最新版本使一次查找约 470 ms。已用官方 Lance API 合并/索引，保留全部旧版本；现有 LanceRecordStore 内部复用可失效的表句柄，并在原 writer 锁内合并小分片/维护索引，未新增公共 API、业务规则或延迟持久化。固定 RecordRef 与外部提交可见性须回归验证。恢复已停止任务时，仅在旧任务退出并持有运行锁/写锁后，将 98 条无完整响应的本地请求从最新日志头重新排队；完整响应不动，原调用日志 @4288 和控制目录 recovery_20260927.json 保留恢复前证据。正常运行仍不隐式重试不确定请求。
- 用户允许选择快速本地小模型及必要下载，视觉可用 Qwen3.8-27B / Gemma。下载必须使用公司代理 http://10.127.48.4:3128，大小写 HTTP(S)_PROXY 一致；本地模型 HTTP 保持直连。
- **模型加载接入入口（2026-09-27，用户要求）**：`text_service/vision_service` 可通过 config/notebook 配置本地权重、GPU、DP/TP、显存比例、上下文/批量预算及启动/退出超时；None 默认沿用外部服务，offline 不加载。模型阶段用小型生命周期函数启动 vLLM、等待就绪，并在退出（含异常）时释放本次进程组；未知占用端口明确失败，不自动接管其他服务。数据读写及原生模型节点仍在 pipeline 主线，不扩 demiflow API。Notebook 当前 GPU=[0,1]、DP=2、TP=1，各阶段两卡各一份同模型；先双卡 4B 分类、释放后再双卡 27B 标注，总并发 128/32，首轮仍为 500 图。vLLM 原生内部负载分配保持单端点和已有请求身份；每次后台提交仍直接执行同一 notebook。覆盖此前模型只能外部启动的约定。
- 后续实跑发现旧分片整理目标过小，遗留小尾片使维护逐次触发；记录存储改为累计 32 个单行追加片再整理到较大目标，新增交错满片/尾片回归，防止吞吐随运行下降。切换双卡前停止旧任务，持运行锁/写锁保留完整响应与全部历史版本，仅将无响应请求重新排队，另存 dual_gpu 恢复回执；不改变正常不确定请求的处理语义。
- **大范围图片关联（2026-09-27，用户再次强调平台边界）**：7,917 类完成后召回 196,966 个概念，旧业务将名单拼成约 3.65 MB 的 reader filter，触发平台 64 KiB 限制。关联改为 demiflow 的 `read_lance → flat_map(concept×SHA) → join(selected concepts) → reduce_by_key(SHA)`，概念名单不取到驱动端拼 SQL 或做集合匹配；匹配后才解释历史尺寸和固定图片引用。reader 只下推可用性等固定长度条件。不绕过 demiflow，也不为本次错误放宽过滤器限制；后续实测瓶颈可在平台原生算子中优化。模型请求身份、已有分类响应和图片筛选规则保持。

### 原图 Notebook 监控与重复提交（2026-09-27，用户要求）

- 用户希望从 notebook 观察已运行任务，避免重复执行。原图 notebook 默认 `notebook_action='monitor'`，只用 demiflow `run_is_active` 观察原生锁，限量读取进度日志尾部并刷新，不读图片、不调用模型、不改业务表；监控间隔、观察时长及尾部大小在配置格。
- `notebook_action='run'` 才请求执行，同 run 已有写入者则转为只读监控；真正写入仍由 `run_pipeline` 原生锁保证唯一。后台任务不因 notebook 监控断开而停止，监控停止或锁释放不能冒称成功。最近落盘的运行摘要可能来自旧执行，显示时须明确。
- **预览不依赖内存 result（2026-09-27，用户反馈修复）**：监控窗口到期不代表后台完成。两个预览格每次重新读取持久摘要及已提交阶段表，显示 URI/版本/行数，未提交或空表明确提示；图片优先 pool，无 pool 时展示 image_inputs 并标注候选未入池。可在导入/配置后独立执行，不能因摘要尚未写出而静默跳过全部输出。
- **正式结果预览（2026-09-27，用户明确范围）**：查看 7,917 全量分类链路时，预览固定 `scene_pool_category_l3_v1 / records@1` 及其引用版本，区别 183,013 张尺寸通过、每概念限额后 123,603 张、实际视觉500张和 keep255张。预览可选择 pool/image_checks/image_results 并分页展开模型理由/区域/限制；不将未经视觉判断的全量候选称为可用原图。只读抽看与明细置于本模块 reviews，不改机器结果或新增模型调用。
- 本次增加监控不等于完成阶段级 resume；已有完整模型响应继续按请求复用，关联/去重等阶段仍会重建。后续后台新提交前须在源 notebook 显式设为 run 并保存，不在后台隐藏覆盖。已启动进程沿用其启动时的 notebook。

### Preparation 清洗、T2I 独立图文消费（2026-09-26，用户确认）

本条覆盖下方 T2I 的文章去重/冲突检查、配图匹配、引用/依赖检查、缺失材料拒绝出题及必需配图推导规则。

- preparation 负责交付清洗：文章预检与依据/引用完整性检查、图片来源规范化及已知生成图排除、公开可用状态一致性。用户明确选择排除必须查看配图才能理解的段落；明确图号和指图段落在上游排除，保留审计记录，不将这类清洗下放给 T2I。
- T2I 默认信任 preparation：文章按 reviewed 取正文，图片按 published/keep 取独立材料。不解析内部审核 JSON、来源或文章配图，不检查段落引用/配图依赖，不做同概念文章去重或冲突报错。不向模型传递上游来源引用；材料编号仍用于当前输入及考点依据。
- 图、文分别按概念聚合，再组合成模型输入；不构造图文匹配或“独立图覆盖配图”的优先层。当前保留图片数上限；取消配图后不推导必需图，后续有联合字段时再扩展优先策略。
- 无材料时照常出题：空参考列表，prompt 说明没有参考材料，不产生 invalid_materials、缺失概念或补齐证据的特殊业务分支。
- 保留编号、图片编码、上下文预算，以及实际读取时的 SHA/解码检查。正文不截断，超预算跳过；图片只在预算通过后读取一次，读取失败直接抛错。
- 同步改 preparation 出口、T2I、notebook、提示词、测试和说明；历史数据及 notebook 已保存输出不回写，开发不调用正式模型。

### Preparation 交付边界与 T2I V2 配置直用（2026-09-26，用户纠正）

修订理由：用户指出 preparation 应屏蔽原始存储复杂度，出题不应绕过交付引用再指定 collect 表；源码/配置冻结和 request→manifest→拆回参数属于过度设计。本条覆盖下方 T2I V2 的运行冻结、同名运行拒绝配置变更及复用旧输入/完成状态要求。

- 下游从显式配置的 preparation 表读取材料及其图片引用。图片表已有 `source_refs`（来源 URI、Lance 版本），文章配图若已携带 `blob_ref` 则直接使用；只有 SHA 的文章配图按 SHA 关联配置的 preparation 图片表。不能在出题侧导入 collect 的 `IMAGES_URI`、读取原始表 latest 或自行重绑图片版本。缺少交付引用时明确报错，不回退到默认原图表。
- T2I V2 入口统一为 `run_pipeline(config)`；`config(...)` 汇集 run、article_source、visual_source、target_uri、write_mode、概念和模型参数；concurrency、queue_depth、temperature 等执行/采样参数同样从 config 绑定，不在节点写死。reader/writer 直接使用 config 中对应字段，不再打包成 request/manifest 后逐项拆回。不扫描源码和 prompt 建运行快照，不因源码、参数、来源或输出变化要求更换运行名。
- 每次执行按当前参数重新读取材料、写输入及设计表；不得按旧 complete 状态提前返回或读取旧输入代替本次来源。输入中明确指定的 Lance 版本和图片交付引用仍按值读取，它们不是额外的运行冻结机制。
- 保留原生模型请求日志及相同请求的响应复用。overwrite 每次写本次结果；append 保留同运行、同目标、同批内容的提交记录，避免重复追加。运行记录不再保存源码或配置 manifest。
- 来源、关系和读写条件在数据流主线可见；不为配置校验、字段包装或别名解析增加转调层。仅有名称为“元信息”的函数却打开业务表，也属于隐藏数据依赖。


### T2I / Edit Benchmark V2 单题约定（2026-09-26，用户确认）

修订理由：用户要求两条 V2 都围绕概念核心内容一次出一道题；Edit 由同一次调用从已提供图片中选一张原图，不再先设计意图、搜图、定稿和审题。本条覆盖旧 V2 多题及独立 Edit 保留旧协议的约定。

- 每概念一次请求，返回单个 `question`；无法出题时为 `question: null` 和具体 `reason`。删除 `tasks_per_concept` / `tasks_per_unit` 题数参数，不保留旧 candidates 列表兼容。允许一道题含多个紧密相关考点，不以覆盖全部知识为目标。
- 两边均使用一个 `design_question` prompt。T2I 的题目字段为 `instruction`、`test_points[{point,basis}]`；Edit 增加 `source_image`，按本次实际图片的 1 起始编号绑定原图。题面包含必要锚点、展示和保持要求，不再额外输出 criteria、知识空缺或搜索计划。
- **空题属于业务逻辑**：业务响应算子区分有题/空题，检查字段及不足原因，并检查 Edit 原图编号。复用 YAML 的字段定义和已有标准校验能力，不因对象/null 两种业务结果扩展 demiflow。
- Edit 当前从已交付图文中的图片选图，缺图/不适合留原因；删除重复选图、定稿、独立审题及构题后再检索。两边输出均为 `unreviewed` 待审题。
- **Edit 操作围绕核心内容选择（用户进一步纠正）**：先选概念核心考点，再用增、删、改及必要组合实现。风格、动作、背景等修改统一归入“改”，不再把 style 单列为选题类别；组合表示围绕同一核心结构、关系或规则联合使用增删改，不沿用旧 compose 的类型枚举与操作数配额，不拼接独立考点。仅改风格或背景时遵守相应内容保持范围。当前不采用以抠图/分割/白底提取为主要目标的题目，因为其主要考察定位、分割及边缘处理，与概念核心内容的联系通常有限。仍然每概念一题，不增加类型标签输出。
- 同步响应、表结构、CLI、notebook、测试和说明；新协议使用新运行名及目标表，历史题目与 notebook 已保存输出不改写。开发只做隔离模拟验证，不调用正式模型。


### T2I 直接使用 preparation 结果表（2026-09-25，用户明确纠正）

修订理由：用户要求去掉输入别名，不增加概念。T2I 出题和训练入口直接写 preparation 结果表路径与固定版本，不要求使用者理解发布登记。用户随后要求 review 并清理重复包装和无效转换。

- notebook 与配置使用 `uri`、`version` 指定文章/图片结果表；按审核状态与概念读取，不再要求提供发布名、别名或 release_id。
- 当前输入在运行记录及读取链路中保持 `uri`、`version`，不为内部兼容扩成另一套引用再转回路径；不为此读取表头、扫描行数或查询登记。一次请求的图文材料只组装一次，不保留没有业务作用的纯转调包装。
- 历史运行及其固定引用保持原读取语义；当前入口、注释和说明不沿用旧发布别名。

### T2I Benchmark V2 精简出题（2026-09-25，用户明确要求）

修订理由：用户要求根据新的出题共识，用标准 demiflow API 精简 V2、清理不用的代码，并参照 curation/t2i/debug.ipynb 建立调试入口。本条覆盖旧 V2 审题、构题后重新检索及四格 debug 模板约定。

- V2 当前只做发布材料绑定、候选出题、结构校验和落表。审题规则另行讨论，输出明确为待审候选；不自动打题、评测或宣称形成正式基准。
- 模型每题业务输出只保留 `instruction`、`test_points[{point, basis}]`；正确性及可观察性检查用于约束出题，不单独输出 criteria 或 requirement。删去旧知识空缺分析、重复调用封装和未使用阶段，不为旧协议保留兼容层。此输出变更只适用于 T2I V2；V1 和独立 Edit 的题目协议不变。
- debug 参照训练侧保留一个 code cell，显式参数、调用正式 run_pipeline、按实际表路径与固定版本读取、直接预览题目与图文材料；手动执行才请求模型。开发验证只使用隔离数据和模拟响应。
- 使用标准 map_prompt_async/run_stream、原生请求日志及 Lance writer；材料编号、图片 Blob 版本和运行配置固定，续跑不重检索材料。

### T2I 历史基准收敛（2026-09-24，用户明确要求）

修订理由：用户要求只保留旧 T2I V1 的固定 200 题和 800 张模型输出，将其数据归入 V1 datasets，并删除混合 20 题、后续 5 题及其他 T2I 历史实验。用户确认保留当前 V2 pipeline、公共知识/图片材料、独立 Edit 基准。此条覆盖下方相关实验继续保留的旧约定。

- `benchmark/t2i/v1/datasets/bench200_artifacts.lance` 和 `bench200_blobs.lance` 保存完整旧基准证据：题库、源图、四模型输出、评分及构题溯源；原始内容字节与证据名称不改。公共索引继续提供跨赛道历史查找，T2I 的全部字节落在 V1 datasets。
- 删除混合 `concepts10_questions20_20260918` 题表、`aligned_evaluation_20260919_v5`、两例开源模型对照和历史参考恢复数据/查看册，以及 focus1000 退役实验的专属证据；删除无引用 Blob、旧公共索引/Blob 副本及失效登记/位置映射。不删除独立 Edit、BAGEL、训练数据和公共材料。
- 当前 V1 是旧 v6.0 构题流程，不是后续小实验的源码快照。后续候选构题、原判据冻结和公开题面检索逻辑保留在现役 V2/evaluation；不为清理混入 V1 或重写版本历史。
- 清理脚本、清单、校验和回执放共同工作区 `_demiflow/t2i_cleanup_20260924/`，不保留被删除实验的数据备份，不调用模型、不提交 Git。


### 顶层共享 datasets 清理（2026-09-24，用户明确要求）

修订理由：用户要求 `/yzp/zhaozy/yangzepeng/0905/datasets` 只保留最新、实体、公共表，清除临时 run 级表。本条覆盖下方该目录继续保留所有分类历史与维护迁移表的旧约定，仅适用于本次指定的顶层目录。

- 保留 4 张现役主数据实体表、2 张全局登记表、最新公共证据索引及其 2 张 Blob 表、当前正式发布引用的 2 张审计表，共 11 张。
- 删除 19 张已完成的临时维护/进度表、被完整替代的旧证据索引及旧分类历史表；同步清除对应的现役目录登记和失效路径映射。不按名称中的日期删除仍被公共发布或评测引用的表。
- 保留表的行、历史版本和固定引用不改写，各 pipeline 自己的 datasets/ 不在本次清理范围。清理计划、文件盘点与完成回执位于工作区 `_demiflow/datasets_cleanup_20260924/`；不再往共享 datasets/ 新建一次性清理运行表。

### T2I debug 单格生成与预览（2026-09-24，用户明确要求）

修订理由：用户要求第一格改为从正式发布源筛选两个概念、调用 GLM 生成训练数据、写新 Lance 表并按条目预览，同时删除第二格。本条覆盖下方 T2I 两格历史对照约定。

- `curation/t2i/debug.ipynb` 仅一个 code cell，复用正式 `run_pipeline()` 完成材料准备、构题、校验、审核和导出；不把未审核候选或历史模型对照当作训练条目。
- 显式配置发布源、概念、模型与运行名；模型为 `glm/glm-5.3-flash`。Lance writer 留在正式 pipeline，notebook 显示新训练表的实际路径和固定版本，再用标准 `read_lance()` 读取。
- 每行展示一个样本，区分完整训练输入与监督目标，图片可点击预览；删除独立第二格，不新增查看器或调度包装。手动执行 cell 才调用模型，维护验证使用隔离数据和模拟响应。

### 非 curation 历史材料归档（2026-09-23，用户明确要求）

修订理由：用户要求当前 curation 以外的历史查看册和审核记录统一收进各自的 archive/，覆盖下方这些模块继续保留 reviews/ 的约定。

- benchmark、evaluation（含 BAGEL）的历史查看册、审核记录直接归所属模块的 archive/，不再套 reviews/；空 reviews/ 删除。正式 pipeline、operators、prompts、debug 和依赖声明保持现役。
- 本次不整理 curation/，保留其当前代码、查看册和运行目录。历史文件仅移动，notebook 输出、模型结论及固定 Lance 证据 ID 原样保留；不把证据 ID 当成本地路径改写。
- 同步文档链接、Git 忽略规则和目录检查；archive/ 仅存历史材料，不成为新执行入口。保留两仓未提交改动，不提交、不调用模型、不改湖内数据。

### 直接使用标准 API 与 Edit 首版试跑（2026-09-23，用户明确要求）

修订理由：用户指出 `files.lance_checkpoint` 等封装隐藏实际读写、增加阅读成本，要求完全使用标准 API；并授权实现 Edit、调用本地模型试跑两个 case。

- pipeline 直接使用 demiflow Dataset 的标准 `read_lance`、`map`、`map_prompt_async`、`write_lance` 等 API，以及 Lance/PyArrow/Diffusers 官方 API。不得新增、扩展或包装通用读写、checkpoint、调度、模型调用 API；确需扩展时必须先向用户说明具体缺口并确认。业务算子只实现材料、构题、合成、审核等业务转换。不要用新的框架、继承层或 helper 隐藏执行链。
- 题目、失败记录、图对、审核及训练条目明确写入 Lance 表，pipeline 中能直接看到表路径和 writer。空表可直接使用 Lance 官方 writer 与显式 Arrow schema；不为此扩展 demiflow。历史表和冻结引用不改写。
- Edit 学习方向参考 T2I，额外独立记录编辑类型；已有图作原图还是监督目标由 VLM 根据监督信号决定。VLM 默认本地 `qwen3.8-27b`，合成默认本地 `Qwen-Image-2.1`，记录模型调用耗时并在 notebook 展示。
- 用户随后要求一卡一个模型：允许把本地 Qwen3.8 服务改为 GPU0 单卡，为 GPU1 的 Qwen-Image-2.1 留出显存；保持服务模型名和端口，记录上下文限制与启动参数。
- 本轮允许两个 case 的真实构题、合成、审核和试跑结果落湖；不启动训练或批量生产，不提交。Edit debug 参照 T2I：短小的看题/看图 cell 与可修改模型、算子的实际编排 cell；保存真实试跑输出。


### T2I debug 两格编排（2026-09-23，用户最新明确要求）

修订理由：用户要求 T2I notebook 第一格看指定题目，并随后明确要求同格输出图片；第二格给出可替换模型/算子的实际编排，其他部分复用。这覆盖下面“所有 notebook 三格空模板”和“T2I 调用命令必须注释”的旧限制，仅用于本次明确要求的 T2I 调试入口。

- curation/t2i/debug.ipynb 仅两个 code cell：第一格按 RUN/TASK_ID 只读看题，并直接输出构题材料图、实际作答参考图、监督目标图；第二格直接写 Dataset 算子链，MODEL 和 STAGE 可改，复用冻结输入、响应 schema 和原生 Lance 日志。
- 第二格模型使用用户在网关列表核对后选定的 `glm/glm-5.3-flash`，由用户手动运行才发请求；构题和审核分别重放原输入，审核默认仍针对原题。调试日志与原运行隔离，不改历史结论或自动导出训练数据。不加执行开关、辅助模块或新的 pipeline。
- 编写期间只读取网关模型列表、做模拟接口测试，不替用户实际调用模型。网关未列出的指定模型不得默默替换。

### 工作目录收敛执行（2026-09-23，用户确认执行清单）

修订理由：用户确认执行已核对的清理方案，保留当前主线和有效成果，退役 focus1000 与旧试跑。这次明确授权覆盖下方旧“历史目录原位保留”和“不写生产湖”的维护限制，仅限本次保全与清理。

- 采集目录及工作区、仓库内所有 `_staging` 不修改；不操作模型服务、训练、正式新数据生产、环境或权重，不提交。
- 保全两套 V1 200题、源图、模型输出、冻结评分/协议/判官证据、必要 pilot 溯源与当前设计查看证据；按固定 Lance 引用读写，原记录字节及分数不改。
- focus1000 退出独立 pipeline，不再补跑。已有生成图及提示词/生成记录/59评分/评测排除关系保留；图片保持生成来源和评测约束，不自动成为训练或已审核参考材料。
- 工作目录清掉退役实验、重复图和可重建导出。先保全必要数据、逐 Blob SHA 核验、迁移消费者和结果复核，再依据显式清单删除；不新建 backup/archive 中转目录或旧路径软链接。
- 主线为 preparation、curation/t2i 与 edit、benchmark 两赛道、evaluation。V1 留固定基准/重现能力，V2 迭代；BAGEL 作为模型接入及按需官方回归工具。

### 基准构建与模型评测分工（2026-09-22，用户最新明确要求）

修订理由：用户要求 benchmark 负责出题、evaluation 负责评测，将 BAGEL 官方评测和 V1 评测代码移出 benchmark。专业名称采用 benchmark construction / evaluation，顶层目录仍叫 benchmark/、evaluation/。本节覆盖下方 BAGEL 保持原位及 V1 混合源码的旧约定。

- benchmark/{t2i,edit}/{v1,v2}/ 负责基准构建：题目、输入材料、任务判据与题库检查。evaluation/{t2i,edit}/v1/ 负责旧版模型作答、判分、冻结核验和结果展示；当前 evaluation/native pipeline 保持现有入口。
- benchmark/bagel/ 整体迁至 evaluation/bagel/；原 evaluation/bagel.py 改为 evaluation/bagel/adapter.py。根目录 bagel/ 官方模型代码不移动。benchmark/vlm/、focus1000/ 本轮保持原位。
- 历史 V1 混合批次保留于 benchmark/{t2i,edit}/v1/，不改写题目、图像、模型输出、评分或冻结 manifest。评测源码显式读取该位置；eval_codex_score.py 与冻结模板保持原字节。notebook 分拆保留已有执行输出。
- 当前 pipeline 的源码快照排除迁入的 V1 评测与 BAGEL vendor/数据目录；纳入实际使用的 BAGEL adapter。保持现有 Lance 运行身份、两仓未提交改动，不提交、不重启服务、不执行正式构题/模型作答/判分/训练。

### Benchmark 分赛道版本与顶层模块（2026-09-22，用户最新明确要求）

修订理由：用户要求合并两个 benchmark，将现役构题按 T2I/Edit 拆成 V2，旧 benchmark 同样按赛道归入 V1，并把 preparation、evaluation 移出 curation。本节覆盖下方旧目录和版本保留约定。

- 顶层模块为 preparation/、benchmark/、evaluation/；curation/ 保留 t2i/ 和 edit/。
- benchmark/t2i/v1/、benchmark/edit/v1/ 保留原顶层旧 benchmark；benchmark/t2i/v2/、benchmark/edit/v2/ 为从原 curation/benchmark 拆出的独立构题 pipeline，各自拥有 ops/、prompts/、runtime.py、pipeline.py 和 notebook。不同赛道不互相导入业务算子或 prompt。
- benchmark/bagel/、vlm/、focus1000/ 等未按 T2I/Edit 拆分的目录保持原位；只修复必要的文件引用。V1 的旧源码、历史题目、图片、评分与冻结 prompts 保留，不因旧版删除约定而移除。
- 迁移源码与入口时更新全部活动调用方，不建立旧 Python 包别名或软链接。历史 run 定位保留原 Lance 身份；preparation/evaluation 的新顶层定位仍指向原命名空间，新 benchmark V2 两赛道各有命名空间，不迁移或改写历史数据。
- 保留两仓已有未提交改动，不提交、不重启服务、不进行正式数据生产、模型作答、评分或训练。

### Curation 按 pipeline 收缩（2026-09-21，用户最新明确要求）

修订理由：用户确认当前没有知识库，文章整理和基础标注均为基础材料准备；线上 RAG 将来单独设计。用户要求 curation 只保留几条 pipeline，删除旧目录与无用调用链。本节覆盖下文旧目录、taxonomy、归档和历史文件保留要求。

- 当前目录为 `curation/preparation`、`benchmark`、`t2i`、`edit`、`evaluation`；不保留 `pipeline_v2` 包装、`downstream` 公共业务层或旧入口别名。
- 训练拆分理由（2026-09-22，用户明确要求）：现有候选设计主要面向 T2I，迁入 `t2i/ops`；T2I 与 Edit 各自维护 `ops/`、`prompts/`、测试和 `stepbystep.ipynb`，不互相导入业务实现。Edit 合成契约与 prompt 继续讨论，不把旧混合分支当作已实现的编辑 pipeline；已有编辑图对报告原样归入 `edit/reviews`。
- 文本整理、基础图片标注与视觉材料审核归 `preparation`，配置在 `preparation/configs`；产物称基础材料或语料。RAG 的索引、线上召回另做 pipeline，本轮不预建。
- 删除 curation 内旧 SQLite/JSONL 数据、taxonomy、实验、layout、归档与历史 review。保留仓库外数据湖；现有材料契约中的 knowledge 字段暂保留，随逐算子讨论再定，不改历史发布。
- 采集与材料输入读取固定发布中的概念表，不要求分类树或挂载表。运行定位符为 `curation/<pipeline>/runs/<run>`，实际业务数据写 Lance。
- 保留两仓已有改动，不重启服务、不提交；讨论期间不生产正式训练数据、不启动训练。用户明确说改之前先讨论。

### 原始材料与策展结果分离（2026-09-21，用户纠正后的最新约定）

修订理由：用户只要求合并 derived 与 releases；此前把 raw 一起合并属于错误扩展，现纠正。下方历史统一实体表约定不得再用于当前写入。

- 采集唯一写端为 `datasets/raw/images.lance`、`datasets/raw/documents.lance`；仅保存原始字节、来源、分辨率、采集关联概念等事实，不包含策展标注和审核列。
- 策展唯一结果表为 `datasets/curated/images.lance`、`datasets/curated/articles.lance`。图片描述、概念匹配、视觉审核按 SHA 聚合为结构化列表，发布状态在对应审核项；文章草稿与审核文章合表。
- 原始图片与策展图片是两张真实 Lance 表，各自独立版本。策展写入必须绑定固定 raw DatasetRef，SHA 必须存在于该版本；`source_refs[]` 保留处理所用的源版本。只读原始字节，不回写源表。采集更新不会自动改变策展结果或既有发布。
- 标注提示词与参数放 `curation/config/`，由 Git 管理；历史实际配置可保存在运行溯源，不建独立协议业务表。
- 发布是固定策展 DatasetRef 加显式 release_id 选择，不复制发布表。知识/独立视觉读取 raw；标注复用读取固定 curated 图片表；出题、训练、评测读取明确发布。
- 通用存储、索引、锁、checkpoint 和引用由 demiflow 提供；schema、来源绑定、标注及发布选择由 demiwtg 定义。master、runs、registry 的职责不变。
- 更正设计与验收见 `curation/layout/entity_tables_20260921.md`、`curation/layout/raw_curated_separation_20260921.md`。核验完成后才退役错误合并表，不改历史模型结论。

### 执行规则（2026-09-21，实体路径以上方最新约定为准）

本轮修订理由：用户要求真实合成一张 Lance 表并自查代码边界。图片、文档分别为 `raw/images.lance`、`raw/documents.lance`；不在业务层路由物理分表。平台回执与锁统一放每张表同级 `_demiflow/<表名>/`；此目录不是业务存储。旧历史条目中的 sharding、state、JSONL 写端与归档要求不再适用。

本次修改理由：用户要求架构边界清晰、取消兼容垫片、Lance 为唯一业务存储，并统一 Python 环境；只保留最新代码；用户随后要求删除归档代码，taxonomy 并入策展，并将现役源码全部纳入 Git 跟踪。

- 唯一活动策展代码为 `curation/pipeline_v2/`，保留知识、出题、训练数据、评测四框架；独立视觉是知识框架可单跑的子图。CLI 加载 notebook 中同一条 demiflow Dataset 链，不另写调度器。
- V0/V1、退役入口及源码恢复副本已按用户最新指令删除；不保留归档代码或兼容别名。历史实验数据、样本、评分与迁移记录保留，不能因源码清理一并删除。
- `curation/taxonomy/` 是策展内部的概念、分类和挂载主数据模块，不是第五条 pipeline。采集从该模块读取固定 master release；湖内 `master/` 位置不变。旧 JSON 主数据写入脚本已删除。
- 两个仓库的现役源码、测试、notebook、配置和说明纳入各自 Git 跟踪；环境、密钥、模型、湖与运行输出不入库。未经指示不提交。
- **平台在 demiflow，业务在 demiwtg**：Dataset、Lance 引用/登记/发布/checkpoint、Blob、记录表、调用预算与重放属于平台；字段 schema、材料关联、审核、隔离、出题/训练/评测规则属于业务。`data_access/` 和文件/Lance 回退适配器已删除。
- **Lance 是唯一业务存储**：输入、阶段、运行元数据、请求/响应、评测图像与发布引用进入数据湖。跨阶段传 Dataset/DatasetRef；图片按内容 SHA 或固定 BlobRef 读取，不根据旧文件是否存在切换存储。JSON 配置、源码 notebook、可重建查看导出、进程锁/日志与平台提交回执不充当另一套业务数据。
- 数据根经 `project.py`，默认工作区 `datasets/`，可用 `DEMIWTG_DATASETS_ROOT` 指定。`master/` 是概念/分类/挂载主数据；`raw/` 是原始材料，`curated/` 是策展实体结果；`runs/` 执行；`registry/` 引用/发布账本。运行定位符仍用 `curation/experiments/<用途>/<run>`，实际数据写 `datasets/runs/pipeline/...`。
- `viewer/` 已按用户要求删除。用户随后纠正：`sync/` 保留，其当前冷备中继代码已恢复并纳入 Git；它不等于旧 lake_sync/merge_meta 文件总账，后者及旧测试已删除。
- 唯一共用 Python 为工作区 `env/bin/python`。`env-cleaning`、`env-lance` 已合并退出；不创建环境软链接或历史入口。Bagel 专用环境不属于本次三环境合并范围。
- 不重启既有模型/采集服务，不改历史题目/输出/评分，不替用户提交已有改动。测试必须隔离数据根，禁止复制或写入生产湖。

下面保留旧架构决策原文用于理解历史；与本节冲突时以本节为准。

### 当前目录约定（2026-09-20，用户明确要求；覆盖后文旧布局条款）

调整理由：用户要求重要文件放在对应代码模块下，子目录名称说明用途；取消顶层 state，不用软链接；保留 V0/V1 冻结参照、V2 最新实现、最终成果、给用户看的关键节点及必要溯源，删除非关键过程文件。下面是当前有效布局，后文旧路径仅说明历史，不得据此重建 state 或兼容软链接。

- 统一导览为 `curation/README.md`。`curation/pipeline_v2/` 是唯一活动实现，四条同级流程仍为知识、出题、训练数据、评测，沿用既有 demiflow 框架。`legacy/` 仅保存历史回归所需的实现和样例，不是活动版本。
- 版本调整理由（2026-09-20，用户明确授权）：V1 已产生小批训练数据，本次独立视觉材料发布与候选目标参与构题改变了方法和协议，知识、出题、训练数据、评测统一进入 V2。`curation/pipeline_v1/` 原样冻结；273 个文件的字节清单为 `curation/layout/version_snapshots/v1_before_v2.json`。旧 run 与数据保留原版本，不改名为 V2。
- `curation/pipeline_v0/` 保存北京时间 2026-09-19 18:23 基线；原始 272 个文件字节不变，原始清单和元数据在其 `snapshot_metadata/`。V0 用于历史对照，不将其旧导入重定向到 V1。
- `curation/knowledge_base/` 保存最终知识交付：111 个机器审核通过概念，不是人工 golden。`curation/image_annotations/` 保存约 7.5 GB 的图片预标注数据库、协议和续跑信息，不能当缓存删除。`curation/training_data/` 保存最终小批样本和审核溯源。
- `curation/reviews/` 保存给用户看的关键节点，README 按讨论顺序列出题目、评分、对照和迭代；13 本关键查看册保留原始字节。`research_notes.md` 保存历史讨论全文，当前方法以 `curation/pipeline_memory.md` 为准。
- `curation/experiments/` 保存关键实验完整记录，`upstream_evidence/` 保存被其引用的上游证据，`early_benchmarks/` 保存早期题目和评分。实验 run ID 不是 pipeline 版本。新结果也写在这个目录，不能写入顶层 state。
- 原 state 中的采集材料／断点已归属 `collect/records/` 与 `collect/image_backfill/checkpoints/`；标签迁移交付／恢复资料在 `taxonomy/migration_records/`。`curation/runtime/model_service/` 只存本地模型服务锁。原始 datasets、模型和环境不属于本次清理删除范围。
- 顶层 state 和 archive 均已移除。跨模块唯一源码恢复副本放在 `tools/recovery_snapshots/`；已按 SHA-256 删除与当前文件、V0 或其他备份完全相同的副本，恢复时须同时查 duplicate_removals.json。
- 文件整理只使用真实目录。`curation/layout/relocations.json` 和当前版本 `pipeline_v2/paths.py` 用于读取冻结记录中的旧路径；模型输出、题目、判据、评分与训练样本不因移动而改写。删除清单、保留原因和完整性结果在 `curation/layout/`。
- 本次明确授权代码模块内按用途存放大体积成果，覆盖旧“所有运行成果必须进 state”的约定。原始／派生大数据仍不入 Git；源码、当前说明和必要维护元数据正常维护。可以添加说明目录用途的 README，但不再把临时过程报告散落在模块根目录。

### 平台与业务边界（2026-09-21，用户要求彻底移除 data_access）

调整理由：此前兼容层让通用能力与业务定义混在一个顶层模块，且迁移后消费者存在断点。用户明确要求删除 `data_access/`，平台在 demiflow，业务在 demiwtg；本条覆盖后文历史记录中对 data_access 的引用。

- `data_access/` 已移除，不保留兼容包或导入别名。固定版本引用、登记、发布、Lance checkpoint、Blob 读取/校验/缓存和原子导出直接使用 `demiflow.lance`；通用不可变文件、锁、调用日志及重放也由 demiflow 实现。demiflow 不导入 demiwtg 或持有项目 schema/表路径。
- 新增 `project.py` 仅存项目数据根配置；这是本次明确登记的顶层配置代码，不是新的访问层。`DEMIWTG_DATASETS_ROOT` 保留；默认根为工作区 `datasets/`。
- `collect/assets.py` 绑定单张 `raw/images.lance`，`collect/materials.py` 解释采集来源，`collect/material_schema.py` 定义材料表；`curation/taxonomy/master_data.py` 处理主数据发布选择和兼容树，`curation/taxonomy/schemas.py` 定义分类表；`curation/schemas.py` 定义知识、标注及阶段表。通用能力不得在这些模块重复实现。
- 一次性入湖/迁移程序归 `tools/lake_migration/`，只处理项目特定来源和映射。活动 pipeline 不依赖迁移工具，直接向平台提交显式业务 schema。
- 主数据必须使用完整固定发布；缺失/无效发布明确失败，不能静默回退各表 head。memberships 是挂载关系真相，树中 instances 由其投影。
- 测试必须使用临时数据根，禁止通过默认配置写入或复制生产湖。V0/V1 与历史源码证据保持字节不变；历史证据中的旧模块名不代表活动入口。

### datasets 根迁至仓库顶层（2026-09-20，用户指令）

依据：用户明确要求“datasets 转移到项目顶层目录，所有数据都放到这个目录里，分层管理”。方案 §1 的可配置数据根即此部署形态。

- 数据根迁至 `/yzp/zhaozy/yangzepeng/0905/datasets`（仓库外、工作区顶层）；`demiwtg/` 数据集内按 registry／raw／derived／runs／releases 五层分层（MECE：账本／来源事实／派生资产／执行产物／冻结交付；2026-09-20 用户指出初版七层不 MECE 后修订），既有 blobs／corpus／kb／meta／pages 作为 legacy 层平移进去，只读保全。
- 所有代码经 `project.dataset_root()`（环境变量 `DEMIWTG_DATASETS_ROOT` 可覆盖）解析数据根，不保存硬编码位置；2026-09-20 已完成 pipeline_v2 各入口、legacy 四入口、image_preannotate、common.blob_shas、downstream/runtime、taxonomy 工具、benchmark/edit、viewer、collect 本地写端常量（relay_b2／import_blobs／merge_meta）的适配，回归 398／4 与基线一致。
- 冻结记录中的旧路径经 `curation/layout/relocations.json`（新增 `datasets/demiwtg` → 新根绝对映射）＋`pipeline_v2/paths.resolve_artifact` 解析，不改写历史 JSON；V1 冻结代码不带该扩展，重放 V1 run 时数据集路径需显式传入。
- `datasets/demiwtg/meta/{taxonomy,concepts}.json` 随迁移离开 Git 追踪（工作区显示删除）；其权威转入数据根，taxonomy 的 Git 审阅形态后续按总方案 §11 由 Lance 发布生成确定性 diff 到代码模块。
- 无软链接；仓库内不残留 datasets 目录。物理搬移于 2026-09-20 16:01 完成（同文件系统 rename，13ms；前置：运行中的 visual_pipeline 退出＋blobs 静默＋无写端句柄）。搬移后验证：images.jsonl 可读、blob_shas 经新根精确计数 2,127,682、AssetReader 逐字节 SHA 校验通过、分层骨架 catalog／raw／reference／intermediate／annotations／evidence／releases 就位。

### 主数据入湖与 master/ 层（2026-09-21，用户确认，已执行）

依据：用户确认 taxonomy、concepts 是驱动采集与 pipeline 的基础主数据，按业务角色纠正目录归属。方案 [curation/layout/master_data_reorganization_plan_20260921.md](curation/layout/master_data_reorganization_plan_20260921.md)，执行记录 [curation/layout/master_data_migration_report_20260921.md](curation/layout/master_data_migration_report_20260921.md)。

- 湖内新增 `master/` 层：`concepts/v1`（385,292）、`taxonomy/v1/{nodes,edges}`（21,409/21,408）、`memberships/v1/concept_taxonomy`（446,775，挂载关系**权威表**，自旧 nodes.instances 原文提取，零重复/零悬空）。历史 taxonomy v31 CSV 包归 `raw/taxonomy_sources/v31_20260824/`（64,221 行，来源保全）。
- 发布 `master_data_v1_20260921`（release_kind=master_data）：四表固定版本组合，登记前全量对账（内容摘要逐字段等价、树/外键/挂载重建检查 16 项全过）。消费经 `taxonomy.master_data`（`resolve_master_release`/`open_master_table`/`mount_map`/`taxonomy_tree_compat`）按完整 release 读取；默认 release 是项目配置（`DEMIWTG_MASTER_RELEASE` > `DEFAULT_MASTER_RELEASE`），禁止四表各自取 latest 拼装。
- 旧 `derived/concepts/v1`、`derived/taxonomy/v1`、`releases/taxonomy/v31_20260824` 为 legacy 只读（冻结引用保留，不删除、不移动、不改登记）；`nodes.instances` 与 `concepts.taxonomy` 降级为兼容投影/只读快照，新增/移除挂载只写 memberships 新版本后发新 release。
- 迁移 run 记录与旧→新映射：`datasets/runs/master_migration/master_data_reorg_20260921/`（baseline/manifest/reconciliation/anomalies 四件）。

## 1. 项目分区

```
demiwtg/
├── viewer/                     # 【代码】查看器闭环：tag_tree_explorer.html + build_viewer.py + build/ 产物（gitignore；英文平行页已随 2026-09-06 统一版退役删除）
├── benchmark/                  # 【代码】评测基准：按三大题型拆成 vlm/、t2i/、edit/ 三子模块（抽样-出题-判分流水线 + reviews/ 下 question_dev/results_review notebooks）；评测数据不入 git：t2i=bench200/+archive/+data/，edit=无 data/ 层（批次目录、archive/、活素材全落子模块根，见架构决策 2026-09-05）；bagel/=第 4 场景（BAGEL-7B-MoT 官方基准评测：README/results_review.ipynb/gen+vlm 脚本入库，data/ 与 vendored 官方仓不入库）
├── taxonomy/                   # 【代码】标签体系维护与富化（audit_nodes / mount_map / gen_taxonomy_kb / gen_instance_kb / upgrade_v31；2026-09-05 还原盘起提升根目录）
├── curation/                   # 【代码】V4策展编排（v4/，demiflow底座）＋公共预标注工具；历史代码按archive/pre_v1、v1、v2、v3、shared、legacy_tools归档
├── bagel/                      # 【代码依赖】Bagel 官方模型包（Bagel/ 训练/推理代码 + 自研 run_wkbench runner；2026-09-05 起入主仓；2026-09-21 降级为常规保留依赖、非项目重点——BAGEL-7B-MoT 权重 28G 迁至工作区 models/、原址留软链、全部引用无感；eval/vlm/data 重物仍 gitignore；见架构决策 2026-09-05 / 2026-09-21）
├── modelhub/                   # 【子项目】LLM 网关（LiteLLM）+ 静态出口代理（mihomo）：本地 vLLM/Galaxy 直连、OpenRouter 走静态住宅 IP；独立仓库，整体不入主仓（见架构决策 2026-08-25）
├── .venv/                      # 【环境】项目公共 Python 环境（conda py3.10，torch 2.6+cu124；原 bagel/env，2026-08-24 提升为公共并由 env/ 改名；不入 git）
├── datasets/                   # 【纯数据】数据集根目录（一数据集一目录；原 data/datasets/，2026-08-24 升为顶层）
│   ├── demiwtg/                #   自建数据集 demiwtg（硬约束见第 2 节）
│   │   ├── blobs/              #     图片原始字节区（内容寻址，不可变，不入 git）
│   │   └── meta/               #     真相区：images.jsonl（统一权威主清单，2026-09-08 由 instance_images.jsonl 更名）+ taxonomy 两件套（taxonomy.json/concepts.json 入 git；2026-09-06 起中英统一，英文平行件与 alias_western 已退役；2026-09-07 instances.json 概念化为 concepts.json）
│   └── .../                    #   开源数据集落盘区（danbooru2024/coco2017 等，不入 git）
├── state/                      # 运行时状态，按模块归属分子目录（不入 git）
│   ├── collect/                #   datasets/（下载过程脚本，只读归档）+ v1 遗留运行时状态（死信/health/runs，只读归档）+ concepts_docs_draft.jsonl（docs 层摘要草稿）+ query_terms_cache.json（检索词运行时缓存）+ docs_clean/（历史清洗产物的兼容链接，已归档；新编排禁用）
│   ├── dataset_index/          #   COCO 标注缓存
│   ├── taxonomy/               #   taxonomy 模块 LLM 断点缓存与审计报告
│   ├── curation/               #   curation 历史分析残留（标签树 CSV、watermark 实验产物等）
│   └── .lancedb/               #   Lance 查询索引
├── logs/                       # 运行日志（不入 git）
├── AGENTS.md                   # 唯一权威约束/说明文档
└── README.md                   # 极简指针，只指向本文档
```

- `datasets/` 下**只是数据存储**：任何代码、页面、生成产物都不许放进去。
- 代码只允许放在顶层 `taxonomy/`、`curation/` 与 `viewer/`、`benchmark/`（2026-09-05 还原盘起模块提升根目录）。
- 仓库顶层禁止新增散落的脚本或数据目录（`datasets/`、`state/`、`logs/` 是明确登记过的例外；`bagel/` 为登记的子项目例外（2026-09-05 起入主仓；2026-09-21 降级为常规保留依赖、非项目重点，权重已迁工作区 models/、原址软链），内部布局自治，不受本仓模块/数据边界规则约束，评测数据等重物仍不入 git；`modelhub/` 为登记的独立子项目例外（LLM 网关 + 静态代理），内部布局自治，同不受约束；`.venv/` 为登记的公共环境例外，只放环境不放代码）。
- 常规文档为 `AGENTS.md`（约束）与 `README.md`（指针）。**明确例外（2026-09-10，用户要求固化研究方向）：[`curation/pipeline_memory.md`](curation/pipeline_memory.md) 为 curation 模块知识核心集的长期设计约定。** 新增此例外的理由是防止后续策展、审核与出题偏离用户已确认的研究目标；不是恢复历史过程文档。其他历史过程文档（docs/、子目录 README）仍不恢复，过程记录看 git 历史。
- **Pipeline文档合并（2026-09-18，用户明确要求）**：为统一设计、实施状态和交接，将原DESIGN.md、KNOWLEDGE_PIPELINE_HANDOFF.md、IMAGE_BACKFILL_HANDOFF.md合并为`curation/pipeline_memory.md`，保留设计章节编号及完整历史记录；原文件只留跳转，后续统一维护新文档。模型prompt源码和notebook仍独立，仓库级约束继续由本文件管理。
- **相关工作必读**：修改 curation 模块知识核心集的提取、筛选、审核及策展校准流程前，先阅读 `curation/pipeline_memory.md`。其中的研究设计约束适用于这些工作；存储和布局遵循本文件。当前代码并未全部符合设计约定，不得以现有实现反向替代设计标准。
- **图片预标注入口（2026-09-10）**：`curation/image_preannotate.py`；用户已授权本地 8000 Qwen 小批验证后全量处理 images.jsonl 清单关联图片。协议与任务边界见 `curation/pipeline_memory.md` 第 11 节，状态与结果在 `state/curation/image_preannotation_v1/`，不回写权威清单或人工标签。

### Curation归档与V4编排（2026-09-14，用户授权的架构调整）

理由：用户要求历史实验按V1／V2／V3归档，V4独立，并在curation内使用已安装的demiflow编排。以下分工替代旧“curation根目录pipeline.py为现役入口”的说明；历史记录与评分不覆盖。

- `curation/v4/`为新流程；入口`/yzp/zhaozy/yangzepeng/0905/env/bin/python -m curation.v4.flow`支持inventory／prepare／review材料准备；`-m curation.v4.pipeline`支持小批本地模型候选提取及逐阶段停靠、检查、续跑。业务算子、来源适配、材料契约均在此，不能依赖archive中的旧实验实现。用户本次明确要求先小批实现pipeline、逐过程审查，故接入现有本地Qwen，不启动正式出题或改评分，暂停notebook工作。机器候选不能自动转为已核验知识。
- `curation/archive/pre_v1/`为首轮12题之前的核心集、校准与知识probe；`v1/`为首轮及非物体／场景补充；`v2/`为expansion20；`v3/`为version3_20；`shared/`为跨版本旧运行器、展示、诊断和未采纳评分草案；`legacy_tools/`保存此前_archived内容。不能把早期core_pilot_v3误当第三版20题。
- `curation/pipeline_memory.md`继续是长期约定；`common.py`、`blob_presence.py`及图片预标注／守护／启动脚本继续服务当前任务，未当实验退役。现有服务不重启、不换模型；公共工具不依赖归档实验。
- `curation/knowledge_application_v1`与`curation/_archived`为旧路径兼容符号链接；`curation.__path__`保留旧core／pipeline等导入兼容，不能将这些兼容入口误当V4实现。归档代码仅调整路径和可核验的冻结哈希兼容，不修改冻结题目、输出、评分或请求；原始源码快照与移动映射由`python -m curation.archive.manage verify`核验。
- 运行数据仍在`state/curation/`；历史实验数据目录与case notebook原位置不变。V4试运行放`state/curation/v4/`。其中内部ID登记为新结构的试运行注册表，不修改现有concepts.json主键、权威taxonomy或新旧清单；全库身份合并／拆分与正式迁移另行实现。
- 模型配置、输入、版本和状态按pipeline_memory.md 设计第21节执行。demiflow并发与落盘成功不等于知识已核验；当前入口验证不宣称全库覆盖、COS取图接通或V4题目已完成。

### V4 notebook代码位置（2026-09-19，用户明确要求）

活动notebook及其生成／展示代码放在`curation/v4/`代码区，运行数据仍放`state/curation/v4/`。最终题目集中展示在`questions20_review.ipynb`，评测整体／逐步链为`evaluation_debug.ipynb`与`evaluation_stepbystep.ipynb`；实现放既有`evaluation/native/`分区，避免影响独立知识生产源码冻结。历史冻结的源码快照及revision notebook属于运行证据，保留原样，不作为新源码入口。理由是用户要求代码可审阅、题目可集中阅读。用户要求接入judge后，以上两册已扩展为判据核验／冻结→作答→标准`map_prompt_async`判分→配对消融的整体评测链；CLI默认准备零模型调用，已有答案可`--judge-only`续判。随后用户明确要求由“答题”改名“评测”并实跑20题、judge用子代理，因此统一上述入口，旧版册保留为`evaluation_legacy_debug.ipynb`且内容不改；公共CLI仍兼容旧manifest。实现见pipeline_memory.md第102节，后续真实运行记录按本文当前状态维护。

### 知识整理端到端入口约定（2026-09-14，用户明确更新）

理由：旧小批使用了历史临时清洗文件，无法检验从采集材料到知识库的完整链路。当前概念知识整理的入口限定为datasets中的采集原始材料及必要元数据；整体目标输出为干净、可追溯、带审核状态的知识库，详见curation/pipeline_memory.md第25节。页面保存格式不一定是HTML，采集正文与派生文本必须区别。

- 不将历史clean_docs、拼接摘要或旧候选当新编排输入；解析、规则清洗、过滤、去重等需要的能力重新编排为现役版本化算子，可复制改造旧代码，但不运行依赖archive的业务链。继续复用demiflow执行底座。
- 历史4件清洗产物已移至state/curation/archive/legacy_processing/docs_clean_20260908/，原state/collect/docs_clean路径仅保留历史兼容链接，校验清单为同级docs_clean_20260908.sha256。旧脚本已归档不再迁移。旧实验可读，不修改冻结输入输出或评分。
- 现役入口已移除clean_docs并拒绝用含其的旧材料包启动新提取；inspect/status仍可查看历史结果。新清洗和最终知识库验收尚未实现，不宣称已端到端完成。运行结果仍放state/curation，代码在curation，不回写datasets原始材料或迁移权威概念名契约。

### 逐算子调试入口（2026-09-14，用户最新要求）

用户已重新授权notebook，覆盖此前暂停决定。入口curation/v4/knowledge_debug.ipynb逐cell经demiflow运行真实业务算子；数据展示支持limit及固定种子抽样。源码与运行输出分别在curation和state/curation。新增流程内CleanMaterials（cleaning.py、knowledge_stages.py），在identity前从采集材料生成可追溯清洗版本，不读取历史clean_docs。清洗初版与审核限制见pipeline_memory.md 设计第26节；默认notebook执行至清洗，模型步骤显式配置后逐cell调试，当前不宣称知识库端到端质量验收。此条更新上一节“清洗尚未实现”的状态，原始数据和历史实验仍不修改。

### 采集记录数据流取代概念查询入口（2026-09-14，用户要求）

现役入口改为`python -m curation.v4.record_flow`及`curation/v4/knowledge_debug.ipynb`。按采集文件逐条处理，顺序记录概念页面对应、附加已有概念关联，再通过可选ID／固定种子采样过滤；后续读取、清洗、材料关联和知识算子不接收入口ID列表。小批与完整文件处理共用实现，关闭过滤／采样／读入上限即可使用同一主线。记录与关联落盘state/curation，demiflow执行流式算子，不将全量材料装入列表。细节及当前语义覆盖缺口见pipeline_memory.md 设计第27节；不宣称全库或跨窗口知识已核验。

旧flow.py／pipeline.py查询批次入口保留历史兼容、status／inspect及共享实现，不能继续称为现役端到端主线。旧notebook保留在其历史run中的knowledge_debug_query_snapshot.ipynb；原始datasets、历史结果、评分和运行服务不改动。

### 概念驱动知识整理，资料层共享执行（2026-09-14，用户再次明确）

理由：用户确认业务主线为“概念→原始资料→多模态知识”，此前将避免重复扫描误解为以材料行决定业务入口。现役入口更新为`python -m curation.v4.concept_flow`，notebook仍为`curation/v4/knowledge_debug.ipynb`。此条覆盖上一节将record_flow作为业务主线的说明；其流式读取、磁盘索引、共享清洗、断点复用保留为底层能力。

概念过滤／采样在资料关联之前执行；未入选概念不因共享资料自动进入任务，无资料概念保留缺口，未知／歧义材料单独保留待识别。资料和知识分别保存并关联，概念结果汇总不能替代跨材料语义整合。具体当前实现、标识及限制见pipeline_memory.md 设计第28节。仅本流程冻结且解析器与原始文件版本一致的原始读取结果可复用，历史clean_docs和旧清洗／知识输出不作为这轮原始输入；原始datasets、评分、历史输出不改写。

### 分类型Dataset与显式关联（2026-09-14，用户明确要求）

理由：通用kind/record封装遮蔽概念、文档、图片的字段与关联过程。现役notebook和命令行转为`curation/v4/dataset_flow.py`，业务schema在`curation/v4/datasets.py`：分别保存概念、文档、图片、关联、全文、清洗、图片检查及知识表，字段直接可见；同schema分片可以进入同类Dataset。demiflow负责惰性Dataset与算子执行，已安装版本没有通用join API，因此使用显式SQL关联并将结果流交给demiflow，不伪称调用了不存在的API。concept_flow/record_flow保留原始读取及旧知识算子的内部兼容能力，不再作为业务表契约。

原始完整字段在独立来源追溯存储中保留，不能把来源额外字段作为事实默默丢弃，也不让统一record封装贯穿业务算子。文档与图片分别关联，避免笛卡尔积；输入输出字段、关联键、未匹配行在notebook可看。知识输出仍是机器候选，身份及跨窗口整合限制见pipeline_memory.md 设计第29节。原始datasets、已有实验及评分不改写。

### 三条Dataset扩列、按需汇集（2026-09-14，用户采纳第三种方案）

理由：用户认为第29节逐处理步拆表过散，明确选择概念、文档、图片分别处理／扩列，必要时才嵌套联合处理。现役交互入口仍为knowledge_debug.ipynb，使用column_flow.ColumnFlow。文档链实际经demiflow map_async连续读取、保存读取列、清洗、保存清洗列；图片链独立扩展字节检查列；概念选择和覆盖计数扩在concepts。关联索引、来源追溯和断点为底层实现，不作为必须逐表查看的业务主线。详情见pipeline_memory.md 设计第30节。

读取／清洗／图片检查的旧分步表名在新run中仅作兼容视图，不保存独立步骤输出。新run保留原始字段和完整扩列结果，不覆盖历史run。仅在需要身份匹配或多材料推导时按概念汇集，继续保留联合核验与知识审核边界，不提前把所有资料嵌入概念。

### demiflow 原生算子主线，取消 SQLite（2026-09-14，用户明确要求）

理由：用户要求使用 demiflow Dataset 算子串联，不再用 SQLite／SQL 实现资料关联和处理。现役入口更新为 `curation/v4/stream_flow.py:StreamFlow`（命令行 `python -m curation.v4.stream_flow`）与 `knowledge_debug.ipynb`，覆盖前述 column_flow／dataset_flow 主线决定。直接从原始 datasets 读入，概念、文档、图片分别扩列，必要时分批汇集。通用 join、reduce_by_key、group_batches、map_cached、checkpoint 已扩展到兄弟 demiflow 仓库并安装到公共环境，当前 local backend 采用可落盘排序和文件缓存，不调用数据库。具体语义和限制见 pipeline_memory.md 设计第31节。

旧 SQLite 编排代码与运行保留历史兼容，不作为新主线输入。原始数据、blobs、旧实验、服务和评分不变。知识阶段沿用共享算子并保存文件，默认不重新发模型请求；本次执行方式修正不等于身份、冲突、图像支持及最终知识库质量已经验收。

## 1.5 标签体系数据契约（两类独立资产，定死；2026-09-21 起真相在湖内 master/，meta/ 两件套已随退役删除）

> **真相位置迁移（2026-09-21，已执行）**：本节数据模型与词汇纪律不变，载体从 `datasets/demiwtg/meta/{taxonomy,concepts}.json` 换为湖内主数据四表（`master/concepts`、`master/taxonomy/{nodes,edges}`、`master/memberships`，发布 `master_data_v1_20260921`）。挂载关系真相从「树节点 instances 名单」移至 **memberships 表**；树/概念旧形态经 `data_access.master_data.taxonomy_tree_compat` 等兼容投影只读生成，不恢复 meta JSON 为可写真源。下方文件形态描述保留为历史契约参考。

整个标签体系**只存在两类资产**——树（taxonomy.json）与概念（concepts.json），代码、数据字段、文档一律使用这两个词，禁止再引入其他分类术语（category、leaf、root 已废除；instance/实体 一词由 concept/概念 取代，2026-09-07）。数据模型以本节为准（原 schema/tag_taxonomy.schema.json 已删除：无校验消费者，勿恢复）。

**两类资产彻底解耦（架构决策 2026-08-19；2026-09-07 概念化迁移沿用）**：概念是资产，taxonomy 是视角。概念的生灭与富知识完全不依赖树；树只是展示/导航视图，同一套概念未来可被多套树视角引用。挂载关系的**真相**写在树一侧（节点 instances 名单）；concepts.json 行内的 `taxonomy` 字段是**快照**（消歧与源路由用，树变更后跑 `curation/migrate_concepts.py --refresh-taxonomy` 刷新）。原 build_unified.py（树推导实例表的重建器）已删除：它维护的正是被废除的耦合。

**概念不区分 class/individual（SKOS 语义：统一主键空间）**。新概念准入规则：可指称（收「词」不收「算式」，成员拼装键禁止入库）、可复现（跨题复现频次 ≥K）、有供给（通用图源可搜、VLM 可认）。

### taxonomy.json —— 树（展示视角）

```
{ "schema_version": "...", "meta": {...}, "tree": <node> }

node = {
  name: str                    # 节点显示名
  path: str                    # 完整路径，' / ' 分隔，从根『demiwtg』起算（前缀精简：域直挂根，无中间层）
  depth: int                   # 根为 0
  children?: [node]            # 子树；末端节点省略
  instances?: [str]            # 挂在本节点下的概念名列表（对 concepts.json 的引用，挂载关系的唯一真相落点）
  knowledge_intro?/aliases?/representative_cases?/related_tags?: [KB 字段，可选；knowledge_intro 为 150-350 字维基百科词条风格]
}
```

### concepts.json —— 概念（独立权威源，统一主键空间）

```
{ "schema_version": "...", "meta": {...}, "concepts": [concept] }

concept = {
  name: str                    # 概念主键（全局唯一；正名纪律：定了一次不再动，变化由 aliases 吸收）
  aliases: [str]               # 别名/英文名（身份字段：判重与英文源路由；无别名为 []）
  carriers: str                # 载体："image+text" | "text"（链路由：图像采集线据此跳过 text-only 概念）
  taxonomy: [str]              # 挂载路径快照（' / ' 分隔，从域起算，树遍历序；真相在树，仅供消歧与源路由，不承载知识）
}
```

- **概念独立于树**：未挂载任何树节点的概念是合法状态（taxonomy=[]，待认领池）；增删树节点不造成概念的创建或删除。
- **`name` 全局唯一是硬约束**：一个概念一条记录；多处挂载表现为多个树节点的 instances 名单同时含该名字（行内 taxonomy 快照同步多路径）。
- **退役字段（2026-09-07 概念化迁移，历史 schema 溯 git）**：`desc`（52,980 条）→ `state/collect/concepts_docs_draft.jsonl`（docs 层草稿：{name, kind: summary, body}；被消费后另批转正）；`query`（52,980 条）→ `state/collect/query_terms_cache.json`（{name: [检索词]}，采集 planner 冷启动先验——检索词从静态资产改为运行时状态）；`source` → 行内退役（迁移时分布存 concepts.json meta.source_stats：derived 330,842 / llm 54,262 / curated 158）。
- **英文平行两件套已退役（架构决策 2026-09-06）**：EN 实体对齐后成为中文概念的 aliases 或独立概念，英文知识以别名形态存活；四个平行文件物理移出 meta/ 归档 state/taxonomy/retired_meta/，search_kb --lang en 与 viewer --lang en 入口拒绝退役提示。
- 图片打标只存**概念名**（标签不含路径）——体系演化（改路径/重生成树）不需要迁移图数据。看图入口（viewer 的 build/imgs.js）由 meta/images.jsonl 的 instances 字段现场聚合（字段名 instances 沿用 demiwtg-data 采集链契约不改，语义=概念名）、相对路径指到 blobs 原图（相对 viewer/ 的 ../datasets/demiwtg/blobs/...），不再建软链树。
- 数据字段定义即契约，改字段 = 改本节 + 同步全部消费代码。

## 2. datasets/demiwtg/ 硬约束（定死，逐条执行）

### 2.1 blobs/ —— 原始字节区（不可变）

```
datasets/demiwtg/blobs/<aa>/<sha256>.<ext>   # aa = sha256 前两位；sha256 = 文件内容哈希
```

- 图片**只增不删、不重命名、不改动**。
- 新增图片必须：先算内容 sha256，再按 `blobs/<aa>/<sha256>.<ext>` 落盘；已存在同名文件则直接跳过（内容寻址天然去重）。
- 文件名中的哈希**必须是文件内容的 sha256**，禁止沿用下载器给的不可信文件名。
- 删除任何旧图片目录之前，必须逐文件验证其内容已存在于 blobs（sha256 比对），否则先并入 blobs 再删。

### 2.2 meta/ —— 真相区（只放真相，别的什么都不放）

**允许的文件（穷举，不允许出现清单之外的东西）：**

| 文件 | 角色 |
|---|---|
| `images.jsonl` | **统一权威主清单**（2026-09-06 起统一，时名 instance_images.jsonl；2026-09-08 更名复用简名 images.jsonl）：去重键 (sha256, instance) 一行一对，逐实例炸开；含 EN 并入行（实例名已归一为中文正名，new 实体保留 EN 名）与原 v1 images.jsonl 并入行（identity/focus/quality=null 待 annotate_backfill 补标）；VLM 补标、质量门、viewer imgs.js 均以它为单一来源（v1 同名退役件在 state/taxonomy/retired_meta/，同名不冲突，勿混淆） |
| `taxonomy.json` | 标签体系树（展示视角，权威源，入 git；含并入的 EN 新实体挂载） |
| `concepts.json` | 概念资产库（统一主键空间权威源，入 git；四字段契约见 1.5；2026-09-07 由 instances.json 概念化迁移而来，历史 schema 溯 git） |
| `.meta.lock` | 跨进程写锁（运行时瞬态） |

**禁止出现在 meta/ 下的东西：**

- ❌ 审计日志（只写不读的账本一律不建；先有读取代码才允许写入）
- ❌ 备份文件（*.bak-*、*.bak-sync 之类）
- ❌ 派生索引（LanceDB、实例名→图反向索引等；需要时由消费者从 images.jsonl 现场聚合）
- ❌ 运行时状态（死信队列 sqlite、健康账本、done flags、COCO 缓存）

**判据（新增任何文件前先回答）：**

1. 有消费者吗？——**必须先有读取它的代码，才允许写入它**。
2. 是真相还是派生？——派生的东西不进 meta。
3. 删掉它会丢数据吗？——丢了数据才是真相；能重建的不进 meta。

### 2.3 运行时状态在顶层 state/（不属于数据湖，按模块归属分子目录）

`state/collect/`：下载过程脚本（datasets/download_all.sh、character_resume.sh、hf_mirror_hf.py，只读归档；HF 数据集落盘区已迁至 `datasets/`）；v1 遗留状态（死信队列 `.dlq_*.sqlite3`、`source_health.json`、`runs/<run_id>/`、`source_registry.jsonl`）只读归档不再写入；`state/dataset_index/`：COCO 缓存；`state/.lancedb/`：Lance 查询索引；`state/taxonomy/`：taxonomy 模块 LLM 断点缓存与审计报告；`state/curation/`：curation 历史分析残留（标签树 CSV、watermark 实验产物等；评测数据已迁入 benchmark/，见架构决策 2026-08-24）。代码约定：仓库根由 `--meta`（默认 `datasets/demiwtg/meta`）向上三级推导（datasets/demiwtg/meta → 仓库根）。永远不进 meta/、不进 datasets/、不进 git。

### 2.4 一致性规则

- `images.jsonl` 是唯一真相（前身链：metadata.jsonl（2026-09-06 更名）→ instance_images.jsonl → images.jsonl（2026-09-08 更名复用简名，用户拍板，见 2026-09-08 决策块））；**不建任何派生索引文件**（历史上先后废除的派生件：instance_images.json（旧实例→图反向索引，2026-08-21 废）与 v1 images.jsonl（2026-09-06 退役，其名 2026-09-08 起被现清单复用）；双份存储有一致性漂移风险；需要实例名→图关系时由消费者从 images.jsonl 现场聚合，如 viewer/build_viewer.py）。
- `images.jsonl` 的 instances 字段只应是当前体系的概念名（字段名沿用 demiwtg-data 采集链契约）；体系演化后残留的死名打标从 images.jsonl 剥离（无隔离区）。
- 一张图的 instances 变更（改名/隔离）改的是 images.jsonl，**图字节不动**。
- 新元数据字段设计时必须先问"哪个消费者读它"；答案为空就不加。

## 3. 代码模块职责


> **知识核心集试点补充（2026-09-09）**：当前默认 `state/curation/core_pilot_v3`，只调用本机8000上的 `qwen3.8-27b`（仍兼容8001；4001付费网关硬拒绝）。新版图片协议独立判断 T2I 与编辑：完整T2I考点要求 full 覆盖且无未支持部分；编辑允许结构和状态改变，要求可见锚点、充分初始条件与知识依赖，不要求源图已符合目标知识。图片可视化自报值不是删除闸门。`fork --reuse-docs` 仅在相同文本协议下复用 calibration 知识候选；`compare` 对同知识/同图片比较协议结果，变化不等于准确率改善；`run --repair-invalid` 显式限额重试并保留原输出与反馈。人工可纠正图片结果，原模型结果不覆盖；更改知识陈述仍需新批次重核关联图片。`comparison.json`、`review_hints.json` 与 `report.json` 的材料缺口由审核入口消费；助手提示不算人工标签。图片来源只区分已声明生成与未核实，不把网络来源推断为真实照片。



> **架构决策（2026-09-09，小规模知识核心集）**：用户授权将旧 curation 归档后建设新流程。现役入口为 `python3 -m curation.pipeline`，模块为 `core.py`（存储/校验/人工准入）、`prompts.py`（六类知识内容与模型协议）、`pipeline.py`（选材/物化/推理/收录/统计/导出）、`review.py` + `review.ipynb`（人工审核），历史 `_archived/` 不作为新流程依赖。知识内容与29域分离；概念、树和湖文件只读，不给 concepts.json 恢复 desc 等退役字段。运行产物在 `state/curation/<run>/`，由流水线和审核 notebook 消费：manifest 冻结概念/来源片段/图片候选，tasks 绑定输入哈希，results 保存校验通过的模型候选，reviews 保存人工决定，report/core 为可重建统计与导出。先有来源支持与条件核验、再看图片证据；模型候选不自动成为核心集，fact 与 evidence 均人工接受且对应任务可用才导出。旧门通过/拒绝/未标注三组保留；六类知识多标签不作难度计数。calibration/holdout 按概念隔离，后者需人工校准后 freeze 才能推理/收录；不宣称未见 benchmark 泛化。命令顺序：prepare → render docs → run docs（显式 --limit）→ render evidence → run evidence → notebook/review → report/export。**用户明确限定仅使用本地 Qwen3.8-27B；4001 属付费网关，任何付费接口使用前必须另获明确确认。** 当前 runner 硬限制本机8000/8001、模型 qwen3.8-27b，禁代理及重定向；不启动/停止用户模型服务。事实或提示词需修改时使用新 run，旧模型结果不覆盖。


> **简单实拍补充（2026-09-07，用户拍板）**：已完成的 23 题原样计入总量，剩余题适量增加简单实拍，避免过度降低整体难度。当前选图为 55 张照片候选与 145 张生成图，其中 19 张采用独立 `synthesize_prompt_edit_v6.1_simple.md`；原 v6.1 标准协议不变。简单配置沿用原脚本的证据、图题绑定与收录校验，取消多跳及高义务数量门槛；`construction_profile=simple`、`difficulty=simple` 显式区分，`level` 只保留 T2I 逐实例参考层级，不宣称为编辑实测难度。新增清单合并为 bench200/source_review/combined_sources.jsonl 供 emit-plan 消费；最终比例按成功题实际来源统计。

> **选图补充（2026-09-07，用户拍板）**：edit bench200 改为采集照片候选与生成图共同覆盖同一 200 实例，一实例最终一图一题，已完成合格题保留。用户明确指定可复用 `_staging/benchmark/t2i/data/` 的历史采集源图副本；不改湖、不重抽实例。`eval_complexity.py prepare-mixed/select-mixed` 以历史样本质量快照和成功复杂度记录初筛，再由显式 `gpt-5.6-sol/high` 子代理逐图补核身份、实拍外观与编辑适配；缺 identity 不从 kb_match 推断。复核通过的图片按 SHA256 原字节复制到 edit/focus200/collected/，清单在 bench200/source_review/selected_sources.jsonl，由 eval_synthesize --source-manifest 消费；原生成图清单不改。照片优先；生成图沿原层级素材偏好，并复用 rank_key 在同批次内择复杂图，不能混用照片 10 分制质量与生成图百分制评分。候选失败沿原三步尝试序列补位，最终来源以 questions 的实际绑定为准，统计分来源并按 level 分层。

> **架构决策（2026-09-07）**：edit 正式出题批次落 `benchmark/edit/bench200/`（运行数据不入 git），沿用 v6.1 出题协议与 `eval_synthesize.py` 的选图、类型尝试序列、削峰及严格机审。实例顺序和难度来自 `benchmark/t2i/bench200/questions.jsonl`；pilot 20 题及其计划原样保留并计入 200 题总配额，只补其余 180 个实例。出题不用 OpenRouter/API，使用显式指定 `gpt-5.6-sol` / `high` 的全新子代理；每题只给物化 md 与绑定源图，不继承调度对话；必须完整读取 md 到文件末尾，不能截取前 240 行而漏掉末尾批次调整。后续 render 从同一校验函数显式附带原有定位方式数量及机械格式自查，修复原 v6.1 表格漏列定位门槛的问题；不修改冻结模板与任何既有验收门槛，旧渲染/题目保留。离线流程为 emit-plan → render-question → 子代理裸 JSON → ingest-question → validate；输入哈希与模型配置保存在批次 dispatch 供收录校验消费。正式判分配置由用户定为修订 QIB v2.2 + `gpt-6-astra` / `medium`，覆盖下文旧 sol 定案；此次先完成出题。

> **架构决策（2026-09-08）**：权威主清单更名 + 图片部分还原（用户拍板两则）。① `meta/instance_images.jsonl` 更名 **`images.jsonl`**（复用简名，实质推翻 2026-09-06「避开 images.jsonl 旧名防混淆」的命名决策——v1 同名退役件仍在 state/taxonomy/retired_meta/，同名不冲突但文档口径以本条为准）；主仓消费端同步：viewer/build_viewer+HTML、benchmark eval_sample×3/eval_complexity/focus1000_caption/focus1000_merge_lake、curation annotate_backfill/focus_sample/search_kb + dataset_analysis.ipynb（共 12 文件）；meta_unify.py 加退役守卫（历史一次性脚本，照 upgrade_v31_en 先例，字符串留作历史记录）。② 图片还原（目标：减少后续下载量）：Sep 5 tar 备份（目录 `/yzp/zhaozy/yangzepeng/0905/1/`，19×4GiB 分片 = 123云盘 DIR"1" 的全部内容）**流在 80GiB 处截断——原上传只到 part_as，缺 at+ 分片**（`.1.part_ad`/`.1.part_ao` 经 md5 证实为同内容重复下载件；云端无更多分片；`demiwtg_all.tar.gz` 10.8GB 为纯代码+结果备份，零 blobs）。已执行：tar 定向抽取 `datasets/demiwtg/blobs`（路径过滤绝不触碰 meta/，防 Sep-5 旧真相覆盖 concepts/taxonomy）还原 150,633 blobs/68GB（归档序前 19 个 sha 前缀目录）；`curation/harvest_blobs.py` 从本地评测样本副本（benchmark/_staging/bagel）按内容寻址收割 +1,453（preloss sha 过滤，生成图不进湖）；`curation/replay_manifest.py` 按 blobs 实存回放 preloss 清单（抽样 64/64 文件名==内容 sha）→ images.jsonl 194,449 行 / 152,081 blobs（含 1,117 无清单行孤儿 blob，blobs 不可变留置）/ 134,941 概念有图 / 质量门合格口径 15,191 概念；viewer 重建（imgs.js 26.3MB，30,565 概念有图；standalone 同步）。preloss 其余 ~196.6 万 sha 的 blob 自该备份不可恢复；云盘残余可选项：results_archive.tar.gz 9GB + taxonomy_sample_cases.zip 937MB（已删评测目录的归档，样本图约 2GB 为湖副本，可选补拉收割）。③ 同日晚用户拍板**恢复全量 preloss 清单**（推翻早间"清单只回放 blob 实存子集"的回放口径）：meta/images.jsonl 直拷归档件恢复 2,849,013 行——缺 blob 行含下载链接（99% 行带 content_url/landing_url），是集群补采的工作面而非悬空行；curation/replay_manifest.py --apply 封死（重跑会截断回 19.4 万子集），干跑保留为 blob 覆盖报表。**图片字节消费者按 blob 实存过滤行**（共用件 curation/blob_presence.py）：t2i/edit eval_sample 池过滤、eval_complexity build_pools、annotate_backfill scan_pending、focus1000_caption load_targets（viewer build_imgs_js 原有逐行 exists 守卫不变，重建验证 imgs.js 输出与子集期一致 26.3MB/30,565 概念）；仅排序/统计类消费者（search_kb 目标排序）不过滤。集群补采双产物（curation/export_cluster_coverage.py + export_refetch_min.py → state/collect/demiwtg_data_sync/）：lake_coverage_for_cluster.jsonl（blob 实存 194,449 行，集群 schema concepts 键——合并后 --skip-covered/配额/去重按湖内覆盖工作）+ **refetch_min.jsonl.gz**（补采工作清单主件：缺 blob 带 URL 行 195.8 万（同 sha 多概念合并），极简五键 {c:[概念], u:url, s:sha256, e:ext, src:源}，gzip 157MB=全字段版 2.4GB 的 7%——用户拍板"只留下载必要字段+概念加一列 url"减传输带宽；无 URL 10,319 行走常规检索线）；集群侧工具 refetch_missing.py（双格式自动识别+gzip 魔数解压；按源防盗链头下载 + sha256 复验唯一入库闸门 + 原子落 blob + norm_rec 补全 24 字段回写行 + 断点/死信轮；湖侧全量清单在册，回灌后按 (sha,concept) join 还原 license/author/打标元数据零丢失）与 merge_lake_coverage.py 已备 demiwtg-data 本地仓（ahead 待推送）。

> **架构决策（2026-09-07）**：instances.json → concepts.json 概念化迁移（用户拍板：改名 + 契约瘦身为四字段；2026-09-05/06「归一化概念体系」方法论讨论的真相层落地，采集交接件 concepts_batch_200.json 已先行新契约）。定案：① 概念行 = {name, aliases, carriers, taxonomy}，顶层 schema_version+meta+concepts；`curation/migrate_concepts.py` 一次性迁移 385,262 行（干跑→--apply，幂等防重跑；--refresh-taxonomy 供树变更后刷新快照），carriers 存量默认 image+text，taxonomy 快照从域起算、树遍历序（全量有挂载；多挂分布 1 处 348,122 / 2 处 25,574 / ≥3 处 11,566，存量按树实况全量保留，多挂≤3 为新概念策展纪律）。挂载真相仍在树——2026-08-19「挂载关系不持久化」就快照维度修订：快照只作消歧与源路由、不承载知识，禁手改、刷新走脚本。② 退役字段去向：desc → state/collect/concepts_docs_draft.jsonl（docs 层草稿 {name, kind: summary, body}，策展精修 105 条优先保留不覆盖，终态 52,980 条；docs 正式层待消费后另批转正）；query → state/collect/query_terms_cache.json（52,980 名，planner 冷启动先验——检索词从静态资产改为运行时状态）；source 行内退役（分布存 meta.source_stats：derived 330,842 / llm 54,262 / curated 158）。③ 消费端同步：viewer（build_viewer 读 concepts.json + docs 草稿 join 进概念行 docs 字段、sidecar 更名 concepts.js、缓存号 v5、HTML fetch 回退/查表键 window.__CONCEPTS__/stats 改「概念」、source 标签删除、standalone 同步；重建产物 concepts.js 115.8MB，imgs.js 空为图片全量丢失后预期态）；curation/focus_sample（mini 表改 concepts 键，四字段整条拷贝，产物名 focus*_concepts.json）；curation/search_kb（targets 改 concepts 键、--only-empty 判据改 docs 草稿名单、english_alias/all_aliases 的 query 半边改读采集缓存、source==curated 跳过删除）；taxonomy/gen_instance_kb 重写为概念富化器（aliases 回写 concepts.json + docs 追加草稿，不再生成 query/source，写盘持 .meta.lock 原子替换）；curation/annotate_backfill（kb 查表改由 concepts + docs 草稿现场构建，脱离 op_annotate.load_instance_kb）；benchmark edit eval_synthesize / dual_carrier_supplement 与 focus1000 caption/genimg/eval_complexity（desc 改读 docs 草稿，mini 表键兼容 concepts/instances 两代）；三册 curation notebook 路径键同步；taxonomy.json meta.description 自指更新；mount_map/README/.gitignore 例外链（!concepts.json，85MB < 原 98MB）。④ 同批修复 data/ 时代 ROOT 深度残留（focus_sample/search_kb/annotate_backfill/gen_instance_kb 原推导到仓库根上一层）与 import 面（顶层 taxonomy.* 直连；data/ shim 仅保留兼容存量 collect_v2.* 调用方）；§1 树形图同步顶层布局。⑤ 历史一次性脚本（en_entity_merge/meta_unify/upgrade_v31/upgrade_v31_en）不改造：读端对已删文件自然 FileNotFoundError 自守卫，历史溯 git。⑥ instance_images.jsonl 的 instances 字段名与 demiwtg-data 采集链契约不动（外部仓 --concepts 批任务模式已由集群侧上线对齐四字段契约；概念模式打标 kb 的 docs sidecar 补丁+bench283 种子 105 条已备 state/collect/demiwtg_data_sync/——本机无 GitHub 推送凭据，待 SG 授权机推送，2026-09-08）；30 个 candidate 新概念键已随人审合入（2026-09-08：+30 新键建议挂载写树生效、385,262→385,292，快照与树零失配校验通过；253 个既有概念批次与真相零差异无需同步；草稿留档 state/collect/concepts_batch_200.json）。

| 模块 | 职责 | 入口 |
|---|---|---|
| `taxonomy/` | 标签体系维护：树审计（audit_nodes 死叶子审查）、挂载聚合（mount_map，只读现算不落盘）、富化（gen_taxonomy_kb 节点 KB / gen_instance_kb 概念富化——aliases 回写 concepts.json、知识文本追加 docs 草稿，各一次 LLM 调用） | 各脚本 `--write` |
| `curation/` | 数据策展与检索接地：search_kb 概念知识检索接地管线（search_kb_sources 直供源扩充 / search_kb_supervise 全量跑监督；--lang en 赛道已随统一版退役）、annotate_backfill 补标驱动（kb_match=None 行 VLM 打标回写）、migrate_concepts（instances→concepts 迁移器与 taxonomy 快照刷新）、harvest_blobs（本地残留图副本按内容寻址回灌 blobs）、clean_docs_pages（集群 docs 页清洗出净版：原始 pages/ 只读，净版落 state/collect/docs_clean/，判定序 short_raw→low_density→nav_listing→listing_page→html_noise→gibberish→duplicate→keep）、en_entity_merge EN/ZH 实体合并（tier0/bulk/escalate/apply/orphans）、meta_unify meta 收口（images 退役并入 / en 归一并入）、focus_sample 重点补图池抽样、质量分析 notebook（download_quality / search_kb_quality / docs_analysis=docs 页质量分级与清洗呈现 / lake_sync_details=回湖同步明细抽样：两代 schema 盘点、打标完整度、缩略图墙、误绑探针）、数据集分析 notebook（dataset_analysis.ipynb，参数写在 cell 内部，直接运行：① danbooru2024 字段下钻；② demiwtg 权威清单分布与过滤；③ taxonomy 视角节点量级与抽样，只读） | 各脚本 `--help`；notebook 直接运行 |
| `viewer/` | 查看器闭环：页面 tag_tree_explorer.html + 构建脚本 build_viewer.py（读 taxonomy/concepts/docs 草稿/主清单四源，docs join 进概念行；imgs.js 只收录 VLM 打标行、每概念 top-50、caption 截断 100 字）+ 产物 build/（sidecar taxonomy.js/concepts.js/imgs.js 与 standalone 单文件，gitignore；英文平行页已随统一版退役删除）；HTML 与 build/ 同址是 file:// 双击可用的硬要求 | `viewer/build_viewer.py` |
| `benchmark/` | 评测基准：按三大题型拆成三子模块（见架构决策 2026-08-24 三子模块拆分）。**t2i/**（生成）与 **edit/**（编辑）各带完整四件套：抽样（eval_sample.py 分层配额，--filter 一条 duckdb SQL WHERE；edit 版默认叠加编辑适配门）、出题（eval_synthesize.py，Galaxy API；t2i 版含 facet 词表审计、edit 版 9 类 edit_type 轮转 + 每第 5 题知识编辑套）、判分（eval_score.py 调本地 vLLM judge，score/dump 子命令；t2i 版 FACETS 权威源 + φ 映射聚合，edit 版 EDIT_DIMS 三维钳制）、gen_results_review.py（生成审阅 notebook）；**vlm/**（理解）暂不拆代码，只放 notebook。每子模块两个 notebook（现在 reviews/ 下）：question_dev.ipynb（抽样+分布+题库审阅，for 题目构造）、results_review.ipynb（打分/评估结果分析）。评测数据布局见架构决策 2026-09-05（t2i：bench200/ 现行 + archive/ 历史 + data/ 默认落点；edit：无 data/ 层，批次目录 synth_v*/、活图池 focus200/、归档 archive/ 全落子模块根）；样本图/题库/判分产物均不入 git（.gitignore 登记）；出题/判分协议 md 在 prompts/、随代码入 git；编辑评分契约 edit/edit_score_prompts.json（ImgEdit 官方原文，随代码入 git） | 各脚本 `--help`；各子模块 `reviews/question_dev.ipynb` / `reviews/results_review.ipynb` |

> **架构决策（2026-09-21）**：BAGEL-7B-MoT 权重迁出 + bagel 子项目降级（用户拍板："bagel 可能不是项目重点，可以不作为一个子项目"）。① `bagel/Bagel/models/BAGEL-7B-MoT`（28G）同盘 rename 迁至工作区 `/yzp/zhaozy/yangzepeng/0905/models/BAGEL-7B-MoT`（与 Qwen/gemma/Z-Image 等同一模型仓），原址留相对软链 `BAGEL-7B-MoT -> ../../../../models/BAGEL-7B-MoT`；既有全部引用零改动无感——curation v0/v1/v2 的 evaluation/worker.py 与 bagel.py、run_wkbench.py 默认路径、benchmark/bagel 绝对路径，以及 `bagel/models`、`bagel/Bagel/models/BAGEL-7B-MoT`、`benchmark/bagel/data/models` 三条软链均实测解析到位；git 工作区零变化（`bagel/Bagel/models/` 本就 gitignore）。② 降级定性：bagel 由"被测/被训核心模型全链路子项目"降为常规保留依赖——代码仍在库、路径不动、pipeline 引用不变，顶层豁免（内部布局自治、重物不入 git）物理维持。③ 只迁权重、不动代码布局的依据（入库代码核验）：官方源码树仅 `inferencer.py` 晚于整体拷入且与上游 diff 逐字节一致（时间戳差异非内容改动），自研增量仅 `scripts/run_wkbench.py`（329 行评测 runner）——"纯官方 copy"不成立，故代码留在库内。
>
> **架构决策（2026-09-05）**：bagel/ 子项目入主仓 + benchmark/bagel/ 第 4 场景入 git（用户拍板，推翻 2026-08-23「独立 git 仓库 + 主仓整体排除」方案——该子仓 .git 已随旧机迁移不复存在）。定案：① 主仓 .gitignore 撤销未锚定 `bagel/` 整体排除，bagel/ 以普通目录随代码入库；重物仅定向排除：`bagel/Bagel/models/`（BAGEL-7B-MoT 权重 ~28G）与 `bagel/Bagel/eval/vlm/data/`（VLM 评测下载数据 mmbench ~50M）；上游 Bagel 自带 .gitignore（wandb/results/eval_results/notebooks/tests 等）在子树内继续生效；`bagel/models -> Bagel/models` 兼容软链随库入库；子项目内部布局自治不变。② 未锚定规则撤销的连带效应：`benchmark/bagel/`（第 4 场景：以 BAGEL-7B-MoT 为被测模型的标准基准评测，2026-09-05 物理整合，详见其 README 与 results_review.ipynb）此前被整体遮蔽未入 git，本次入库——仅代码与文档入 git（README、results_review.ipynb、gen/+vlm/ 脚本），data/（~1.2G 题库/出图/运行缓存）与 vendored 官方 git 仓（gen/qib_official、vlm/VLMEvalKit，可再克隆）不入主仓。③ t2i 线评测数据排除边界同步登记：archive/（仅 MANIFEST.md 入库）、bench200/（仅 README.md 入库）、focus1000/data/ 不入 git（题库/出图/判分/溯源留本地，口径见各目录文档）；edit/ 边界以下条「edit 子模块目录对齐 t2i 并废除 data/ 层」为准。④ /data/ 登记 .gitignore 不再入库：现存为 collect_v2 退役残件（根目录 taxonomy/、curation/ 现行版本的迁移前旧副本/重复件），留档本地不删。
>
> **架构决策（2026-09-06）**：edit 判分协议角色分离（用户拍板：判官提示词不得混入 codex 任务书内容——原 v2 合并文本会把「不得读取 caption/reasoning/level/suite/模型名/另一候选」等编排语义喂给判官，构成判定噪音与锚定风险）。定案：① 新增 `edit/prompts/judge_prompt_edit_qib_v2.md` = 判官唯一权威源（TEMPLATE 块 + 9 个 `<!--TYPE:x-->` 分型块；只含 rubric 三档定义、分型核对重点、落档硬判据、判分流程、特殊情况与裸输出 JSON；φ 映射与 d2/d3≤d1 钳制规则**刻意不向判官展示**——换算与钳制是管线确定性计算，防钳制语义反向锚定判官原始落档，此点与 ImgEdit 官方把钳制句写进判官 prompt 的做法有意分歧）。② 原 `codex_score_prompt_edit_v2.md` 重写为编排协议（任务书契约）：判定主体与盲评隔离的物质保障、prepare→render→逐字判定流程、分数行字段契约（身份/哈希字段从 manifest 逐字照抄，mapped=φ(tier) 与 official_* 机械换算）、有效性政策（model_failure 计 0 / invalid_question 成对剔除 / 基础设施重试）、aggregate/compare 命令、冻结后审计规则——其内容永不进入判官输入。③ `eval_codex_score.py` 新增 `render` 子命令（解析模板标记块，按题渲染 eNNN.txt + index.jsonl 登记 prompt_sha256/instruction_sha256，--manifest 时附盲评图片绑定），与 t2i 的 judge_prompt_gen_v6.0_V2.md 惯例对齐；新增 `ingest` 子命令（判官裸输出 raw/<qid>.txt → 机械补齐身份/哈希/换算字段并逐题校验维度契约 → part_*.jsonl；--format json=QIB 裸 JSON / imgedit=官方 Brief reasoning+分数行，MODEL_FAILURE 标记按各自口径记失败；part 文件禁止手写），管线成 prepare→render→派发→ingest→aggregate/compare 五段。判官调用方式定为**每题一个全新子代理上下文**（输入只有单题物化 prompt + BEFORE/AFTER 两图，零附加）——上下文隔离由结构保证，编排者只调度不亲判；任务书因此收缩为派发规则+命令清单。④ pilot 已冻结两轮（Gemini/Bagel QIB）判定用旧合并文本，`synth_v61_pilot/scores_qib/prompts/` 为新协议复建渲染（审计对照）；此后任何新判分轮必须 prepare→render 后逐字判，未物化产物无效。⑤ v1（1–5，冻结）与官方 rubric 实验臂（本就逐字物化）不受影响。⑥ edit 赛道判官模型钉定 `gpt-5.6-sol`（用户拍板：与 pilot 已冻结两轮一致，新轮次含实验臂一律沿用，跨协议对比不混入判官差异；曾短暂考虑换 gpt-6-astra，否决），任务书 `--judge` 固定写 `gpt-5.6-sol-built-in-imgedit-official`。⑦ 对照臂文档对称化与 prompt 世代归档：官方 rubric 臂增两份 `prompts/judge_prompt_edit_imgedit_official.md`（判官原文载体：ImgEdit 官方九类 rubric 逐字内置为 OFFICIAL_TYPE 九块，只 `<edit_prompt>` 单处替换；render 与契约 `edit_score_prompts.json` 逐字节一致性校验，块或契约任一侧被改写即拒跑（已负测试）；官方钳制句与 ≤20 词纪律原样保留——对照臂忠实性优先，与 QIB 臂"钳制不进判官输入"的防锚定策略有意分歧；判官文档不含任何编排内容）与 `prompts/codex_score_prompt_edit_imgedit_official.md`（通用编排协议，与 QIB 协议结构平行；1–5 与 QIB 百分制禁线性互换，臂产物永不进主口径结论）；`render` 支持官方臂 md/json 与 QIB md 三种模板（pilot 官方臂 20 题以 md 模板重渲染 sha256 20/20 复现）；退役 prompt 归档 archive/prompts_v1_v60/（出题协议初版+v6.0、判分协议 v1）与 archive/audit_doublecheck_prompt.md（复核轮已结、结论已落地 eval_synthesize，留作模板），archive/MANIFEST.md 登记；reviews/ 两册退役审阅 notebook 移 archive/notebooks_retired/（results_review.ipynb 的冒烟分析对象本机已不存在、audit_review.ipynb 属复核轮），question_dev.ipynb 按惯例留位且路径文案同步新布局。⑧ 两份判官 prompt 经 gpt-6-astra 全文评审（报告存档 `reviews/judge_prompts_review_gpt-6-astra_20260906.md`：文档 A 23 条 + 文档 B 机制 7 条 + 官方文本观察项 18 条）。处置：无副作用修复即时落地——QIB md 补九类三维名称附录快照（A01：拆分时维度表只留在代码 EDIT_DIMS 的权威源闭合回归）、官方 md 头部来源登记（B-M01：GitHub 出处 + 契约 sha256 基线 f9468dc8…，并如实声明双副本校验的已知边界）、render 加载期闭锁（B-M03：块唯一性/九类完整性/占位符唯一校验，重复块负测试通过）；两臂渲染 sha 修复前后 20/20 一致，判官所见零变化，pilot 冻结分数不受影响。判据类修改（A02 Excel 门槛机会偏差、A04 通用硬判据误罚 style/background/extract 合法重绘、A09 异常态与 JSON 契约闭合等）一律不在冻结期动，汇入 200 题正式批次前的 QIB v2.1 修订；官方 rubric 原文一字不动，18 条观察项（钳制非独立、钳制句锚定、style 无参考图、compose 部分成功奖励差、"两臂比较不只换分制"等）作为三臂结果解读的必读注记。⑨ QIB 判官 prompt 去内部键名（用户拍板 2026-09-06）：判官输入/输出全面改用维度名与"维度一/二/三"（d1/d2/d3 降为纯存储层位置键，仅存在于 EDIT_DIMS、分数行 schema 与 aggregate 校验中），TEMPLATE 增设显式「输入/输出」节，判官上下文不跳转；ingest 校验改为按序匹配维度名并自动补 key（兼容带 key 输出，错维度名负测试拒收）；pilot 复建渲染已按新模板刷新（sha 变化，冻结两轮分数不受影响），官方臂渲染经回归验证 20/20 不变。⑩ QIB 判准 v2.1 落定（用户拍板：v2 不再开新轮次）：新增现役判官模板 `prompts/judge_prompt_edit_qib_v2.1.md`——逐档硬判据全部下沉到九个 TYPE 块（每型 =「类型前提」+ 三维度各自的 0/1/2 判据，按该维在该类型下的真实语义写），通用节只留类型无关骨架（角色/输入/三档定义/三条通用判定原则/流程/特殊情况/输出契约）；吸收 astra 评审修正项：A02（Excel 改为"全部精确达成 + 任务内可核验的精确执行"，废除"超常规"相对参照，简单题同有可达档 2）、A03（0/1 边界 = 硬约束违反 vs 连续量偏差；记 0 必须有可指认证据，存疑不记 0）、A04（style/background/extract 的全图重绘属授权操作，不再被通用"大范围重绘 Fail"条款误伤）、A05（独立归因：任务失败不等于其他维度自动失败）、A06（编辑归因：只判新引入缺陷，源图既有缺陷不扣分、未授权修复不奖励）、A07（辨识限度条款）、A09（invalid/judge_unscorable 的 detail 必填、model_failure 的 reason 规则）、A21（extract 可见部分不得补全、天然浅色轮廓不算白边、相对布局改变属偏差）、A22（background 前景投影归属规则：投在背景区域的阴影/倒影按新光源重建、归 background 区域，协调性入 Physical Consistency 判）、A23（compose 先拆两个子操作逐个核对，细节偏差不等于少做一项）。render 默认模板切至 v2.1，编排协议角色表与步骤 2 同步；v2 原文保留（pilot 冻结两轮的判准，`scores_qib/prompts/` 即其渲染，审计对照），pilot 分数不追溯重算；**v2.1 与 v2 的分数不可直接互比**（档位语义与判据均变），跨批次对比必须显式标注判准版本；200 题正式批次首轮起用 v2.1。⑪ 判官文档纯净化（用户拍板：判官文件不写渲染机制——判官拿到的已是渲染成品，维护者注对判官是噪音、对人是错位置）：两份现役判官 md（judge_prompt_edit_qib_v2.1.md、judge_prompt_edit_imgedit_official.md）删除头部维护者引语与 v2.1 文末附录，只含标题 + 模板块；机制信息归位编排协议——九类三维名称快照表 + EDIT_DIMS 权威源声明 + "判官文件不写维护者注"规则入 codex_score_prompt_edit_v2.md 附录，官方臂来源登记（arXiv/GitHub 出处 + 契约 sha256 + 校验边界 + 钳制句与 20 词要求属官方口径逐字保留）入 codex_score_prompt_edit_imgedit_official.md；清理后三臂渲染回归：v2 冻结模板与官方臂 sha 20/20 不变、v2.1 渲染 20/20 零占位符；v2 历史模板头部原样保留（审计定位，不改写历史文件）。⑫ 判官 prompt 精简终态（用户拍板，推翻⑪中"v2 头部保留"处置：分型判据 = 全部判定文本，通用节即噪音；对齐 ImgEdit 官方的精简形态）：v2.1 TEMPLATE 收敛为「角色→输入→本题判据（分型块）→输出→题面」——判分规则元规则节、三条通用判定原则、判分流程、特殊情况节、{{DIMS}} 维度清单节全部删除；validity 四态语义与 observations 四组定义折叠进输出节（字段定义所在处）；"存疑不记 0、拿不准 1/2 给 1、同题同标准、任务失败不连坐"等跨类型纪律随通用节移出判官文本——分型判据的逐维锚点承载档位边界，未来校准如需恢复纪律条款，以分型判据形态写回；v2 冻结模板同步瘦身为"标题+模板块"（删头部维护者注与附录快照；TEMPLATE/TYPE 一字未动，渲染 sha 回归 20/20 不变）；编排协议附录快照表删除（EDIT_DIMS 代码表为维度名唯一权威源，任何文档不维护快照副本，规则并入职责边界段）；TYPE 分型块改为 ImgEdit 官方同款布局（维度名单独行 + "0/1/2 + 两空格 + 判据"纯文本行，无 markdown 修饰，"类型前提"取消、其语义并入对应档位判据），v2.1 单题判官 prompt 6313→3922 bytes，三臂渲染回归全绿。随后用户手排 v2.1 定版布局（一、输入 / 二、打分规则（九类判据逐节陈列）/ 三、输出 / 四、题面 四部分编号结构）并删除 v2 模板文件；机械修复恢复可执行：补 TEMPLATE-END、恢复四、题面与"两张图"收尾句、九个 TYPE 块移出 TEMPLATE 至模板块外（渲染只注入本题类型判据，判官不见其他八类，杜绝跨型锚点污染与提示词膨胀）、`## 类型` 标题归一到标记外；v2 删除后的审计凭 `scores_qib/prompts/` 渲染产物与 index sha 登记保存（协议角色表同步）；v2.1 终版 = 四段编号结构（一输入 / 二打分规则＝{{TYPE_NOTES}} 注入本题类型判据 / 三输出 / 四题面＝{{EDIT_TYPE}}+{{INSTRUCTION}}），九个 TYPE 块在模板块外作素材区（渲染只注入本题类型，判官不见其余八类，杜绝跨型锚点污染），中途试验过"判据内嵌模板、渲染剪裁"方案经用户定夺回退为占位注入式；单题判官 prompt ~3.8KB，QIB 渲染 20/20、官方臂 sha 回归 20/20 绿。⑬ 文档去重收口（用户质疑两份任务书冗余后定案）：官方臂独立编排协议 `codex_score_prompt_edit_imgedit_official.md` 删除，其独有内容（变体参数对照表、口径禁令、来源与完整性登记）并入主协议 `codex_score_prompt_edit_v2.md` 的「官方 rubric 对照臂」一节——编排协议全仓只此一份，两臂走同一六步流程仅参数不同；批次执行仍由各批次 TASK 承担（冷启动执行者需要实例化路径的具体工作指令，通用协议带占位符不可直接执行）。文档终态：判官文本 ×2（QIB v2.1 / 官方原文载体）+ 编排协议 ×1（含对照臂变体节）+ 批次任务书 ×1（TASK_official_rubric_codex.md）+ 契约 edit_score_prompts.json + 管线 eval_codex_score.py；官方臂渲染回归 sha 20/20 不变。
>
> **架构决策（2026-09-05）**：edit 子模块目录对齐 t2i 并废除 data/ 层（用户拍板），同日登记 ImgEdit 官方 rubric 实验臂。定案：① edit/ 新布局：prompts/（出题协议 synthesize_prompt_edit*.md + 判分协议 codex_score_prompt_edit_v{1,2}.md + audit_doublecheck_prompt.md，随代码入 git）、reviews/（四册 notebook：question_dev / results_review / results_review_v61 / audit_review）、archive/（synth_v60 世代批次 + complexity_audit 账本 + audit_doublecheck 复核产物，MANIFEST.md 入 git）、批次目录与活素材落子模块根（synth_v61_pilot/、focus200/、complexity_audit_synth.jsonl、judge_prompts/ 物化区，均 gitignore）；data/ 层撤销（t2i 维持既有布局不动）。② 排除集机制统一升级：三份 eval_sample*.py 的 DEFAULT_EXCLUDES 改为三赛道整目录递归扫描 samples*.jsonl（原 data/+archive/+bench200 定向桶列表在 edit 去 data/ 后会漏扫顶层落点），任何布局演化下历史样本永不回流；t2i/archive/MANIFEST.md 排除集条款同步改写。③ 全部脚本目录常量随迁（EVAL_DIR/OUT_DIR/DATA→子模块根，audit_doublecheck 默认读物→archive/，gen_results_review 产物→reviews/），py_compile 全过。④ 12 份盲评 manifest/identity（scores_qib×4、scores_official×4、scores/codex_blind×2 等）的绝对路径全部改写至新址并逐一验证文件存在，旧机器 /tank 前缀残件一并清理。⑤ results_review_v61.ipynb 迁 reviews/ 重建重跑（demiwtg 内核，零报错，report 对账一致）。⑥ ImgEdit 官方 rubric 实验臂（判分文本物化）：官方 prompts.json 逐字渲染 20 题 judge prompt 落 synth_v61_pilot/scores_official/prompts/{eNNN.txt,index.jsonl}（sha256 登记，判官不得回读源 rubric），判官=codex 内置模型盲评（TASK_official_rubric_codex.md），产物只落 scores_official/，与 QIB 主口径物理隔离、永不混入正式结论；动机=量化 rubric 文本本身的协议效应，并保留与 ImgEdit-Bench 官方口径对话通道。
>
> **架构决策（2026-09-06）**：meta 真相区统一收口（用户拍板：EN 并入中文湖成统一版、images.jsonl 收官退役、VLM 补标后置另跑）。EN/ZH 实体合并由 `curation/en_entity_merge.py` 五层执行：tier0 确定性配对（别名/西文串≡EN 名限同节点，38,701 对，0 调用）→ bulk 逐节点 LLM 对齐（本地 vLLM Qwen3.8-27B @8001，16,527 节点全完成、341,302 对、0 API 调用）→ escalate 红旗节点复核（四模型轮转 glm/glm-5.3-flash + qianwen/qwen3.7-plus + galaxy/qwen3.6-flash + galaxy/glm-5.3，关思考防思维链截断，368/375 有效）→ apply 写库（matched/variant→中文实体 aliases 326,272 名、new→新实体 88,249 个 source=derived 入库挂树 99,013 处、tier0 回填 1,330 对；instances 296,010→384,259）→ orphans 兜底（对齐未覆盖的 EN 独有节点实体 1,003 个全量入库挂树）。配套 `curation/meta_unify.py`：metadata_en.jsonl 73,962 行归一并入（EN 实例名按对齐映射归一中文正名，(sha,instance) 去重）+ images.jsonl 322,332 行炸开并入（v1 采集字段 tiers/credit/source_rank 等照 migrate.py 先例丢弃，identity/focus/quality=null 待补标）。收口：images.jsonl/metadata_en.jsonl/taxonomy_en.json/instances_en.json 四文件物理移出 meta/ 归档 state/taxonomy/retired_meta/（改前另有 backup_pre_en_merge、backup_pre_orphan_ingest）；2.2 白名单收敛为 metadata.jsonl+三件套+.meta.lock，metadata.jsonl 升统一权威主清单；消费端同步（viewer/build_viewer.py 改读 metadata.jsonl、imgs.js 精选口径=打标行+top50+caption 截断、缓存号 v4、--lang en 与 tag_tree_explorer_en.html 删除；search_kb --lang en 拒绝退役；upgrade_v31_en.py 加防误跑守卫；eval_sample×2/annotate_backfill 注释更新；.gitignore 英文件例外链移除）。终态校验：metadata.jsonl 2,849,013 行 / 2,116,511 sha / 304,190 实例全部在册、name 唯一性通过、并入零新增重复键（存量遗留 261 个重复键为老采集链时代产物待后续清理）。追加定案（同日）：alias_western.json 退役归档 retired_meta/——49,197 个非空西文串 100% 已含于 instances.aliases（数据零独有），246,813 个 null 的负缓存语义（op_seed 防重问）由用户后续改 demiwtg-data 仓库 op_seed 读端承接；en_entity_merge tier0 读端已容错缺文件；meta/ 白名单收敛为 metadata.jsonl + taxonomy 两件套 + .meta.lock。追加定案（同日晚）：metadata.jsonl 更名 **instance_images.jsonl**（用户拍板：语义=实例×图片观测对，避开已退役的 images.jsonl 旧名防止契约混淆；本条目前文中的 metadata.jsonl 均指此文件）；主仓 11 个引用文件同步改名（bagel 的 geneval 自带同名文件不动）；instances.json 维持实体+富知识一体（拆分否决：desc/query 是实体 1:1 正典记录而非模态样本，aliases 是结构性匹配字段；体积增长留观，每实体一份 desc 的规模问题届时再议）。
>
> **架构决策（2026-08-25）**：新开 `modelhub/` 独立子项目（用户拍板：可单独 push GitHub、其他机器 pull 直接复用；静态代理全套并入）。定位：本地 LLM 统一接入层——LiteLLM 网关（127.0.0.1:4000，OpenAI 兼容）路由三条线：本地 vLLM（qwen3.8-27b，no_proxy 直连）、Galaxy 专线（qwen3.7-plus，no_proxy 直连）、OpenRouter 通配（进程级代理注入 → mihomo 按域名分流走静态住宅 IP 出口）；静态代理模块即原 `/root/gpu-static-proxy`（mihomo v1.19.30 双层链式：10808 隧道换源 IP → 静态 IP 节点 216.132.205.99；modelhub 只维护「AI API 域名走 STATIC 双层静态出口」一层规则，其余流量 modelhub 视角直连、继承宿主策略、不感知不维护）整体迁入 `modelhub/static_proxy/`（二进制与 GeoIP 库随迁，旧目录作废可删）。定案：① 照 bagel 先例：独立 git 仓库、主仓 .gitignore 整体排除、内部自治（自带 README，不受主仓「文档只两份」约束）；② 机密零入库：.env（API keys）与 static_proxy/config.yaml（节点凭据）只进 gitignore，仓内只有 *.example 模板；mihomo 二进制不入库（fetch_mihomo.sh 下载/旧机拷贝）；③ Python 环境独立：modelhub/.venv（litellm[proxy]==1.98.0 锁定），不碰主仓 .venv（不动主仓 openai/httpx/pydantic）；④ 消费端零改动启用：网关为 OpenAI 兼容端点，既有脚本换 LLM_BASE_URL=http://127.0.0.1:4000/v1 即接入，逐步迁移；上游端点与 key 全部 .env 可配置，启动时 gateway/gen_local_models.py 自动发现各 *_API_BASE 端点的模型并注册进 /v1/models（openrouter/* 通配 litellm 原生展开；Cline 按 OpenAI Compatible 配置网关地址即自动带出模型列表）；⑤ 端口登记（均仅本机监听）：4000 网关 / 7891 mihomo mixed / 9091 mihomo API / 1053 mihomo DNS。
>
> **架构决策（2026-08-24）**：英文版标签体系入库（用户拍板三则：扩白名单同居 meta/、实例名轻量清洗、查看器 --lang en 独立页面）。「融合世界标签体系 v3.1」交付包英文底稿（taxonomy_tree_instances_en.csv：21,406 行，中文路径/英文路径/英文实例清单三列）由 `taxonomy/upgrade_v31_en.py` 建成一套完全独立的英文平行数据（干跑→--apply，照中文版惯例；不动中文三件套与 alias_western.json）。定案：① taxonomy_en.json / instances_en.json 同居 meta/，2.2 白名单扩两行 + .gitignore 例外链放行入 git；② 前缀归一为中文 norm_path 的英文同款（剥根 Fused World Label System + General Classification Tags，换根 demiwtg），底稿缺行的 4 个骨架域与中文同源隐式补齐；③ 底稿实测 119 组翻译撞车（不同中文节点译成同一英文路径，如 炊具/锅具 → Cookware，名单重合度中位数仅 0.02）用户拍板自然合并（名单取并集），另 2 个译名折叠展开隐式节点（中文段『帝王蟹/蟹』译成 King Crab / Crab 两段）→ 英文树 21,291 节点 = 中文树 21,409 - 撞车合并 120 + 折叠展开 2，域级对齐（撞车与折叠清单落 state/taxonomy/en_merge_report.json 供后续精译修复）；④ 英文实例名轻量清洗（按词：全小写/全大写词转首字母大写，全大写缩写与混排词保留），清洗后同名大小写变体合并（name 唯一主键），413,329 → 382,341 全量 source=derived 占位入库（不做富知识、不映射中文知识），清洗合并明细落 state/taxonomy/en_clean_report.json；⑤ viewer 复用：build_viewer.py 加 --lang en，读英文两件套写 viewer/build_en/ sidecar（imgs.js 注入 null——英文实例与 images.jsonl 打标零交集，英文版无图是预期），页面 tag_tree_explorer_en.html 由主页面现场替换生成（标题 demiwtg (EN)、sidecar ?v=1 独立缓存号、fetch 回退改英文两件套），页面入 git、build_en/ 产物不入。结构对齐验证以中文路径列为桥：归一中文路径 21,405 ⊆ 中文树，差集恰为 4 骨架域。纯新增零覆写，故无入库前备份。图数据（images.jsonl/metadata.jsonl）零改动。
>
> **架构决策（2026-08-24）**：`data/datasets/` 整体升为项目根目录 `datasets/`（用户拍板：自建与开源数据集同居一处；data/ 保留原名只住代码）。理由：`data` 作顶层名太泛，且目录里早已住着 collect_v2/taxonomy 两套代码名实不符；拆开后 `datasets/` 语义精准、`data/` 收敛为数据构建代码区。定案：① 同盘 rename 零拷贝（~1.1TB：自建 demiwtg 631G + 23 个开源数据集；24 目录全量在位，blobs/meta 完好）；② 路径改址全链路：data/collect_v2 与 data/taxonomy 的 REPO_ROOT/ROOT 推导补一层（此前代码被搬入 data/ 后已暗指 data/ 而非仓库根，state//logs/ 类路径实已失效，本次一并修复）+ 数据集路径常量去 `"data"` 段，import 实链路断言验证；benchmark t2i/edit 的 eval_sample 默认路径同步；viewer 页面/构建脚本改址并重建 build/ 产物（20,100 实体）；③ .gitignore 例外链改 `datasets/*` 逐级放行三件套；④ 历史决策块旧路径为当时快照不回改；⑤ curation 模块在搬移中被误删的 dataset_analysis.ipynb 已由快照恢复归位 data/curation/（20-cell 终态：基线 16 格逐字对齐 8/22 快照 + 全部补丁/内联写命令按时序重放 + 拆分后删尾四格；抢救过程产物在 state/curation/_nb_recovery/，可清理），路径与 import 已按 data/ 新位置与顶层 datasets/ 调整。本会话未动 .qoder 交接文档与 logs/ 归档脚本（历史快照）。
>
> **架构决策（2026-08-24）**：benchmark 按三大题型拆成三子模块（用户拍板）：`vlm/`（理解）、`t2i/`（生成）、`edit/`（编辑），推翻上一条「评测数据在 benchmark/ 根三目录」布局。定案：① t2i/edit 各带完整四件套（eval_sample/eval_synthesize/eval_score 由原跨赛道脚本拆分，通用工具各留一份子模块自闭环，不建 common）；vlm 赛道协议未拍板暂不拆代码，只放 notebook；② 旧评测数据三目录（eval_v1/eval_v2/taxonomy_sample_cases，合计 ~2GB）用户拍板删除重抽，重抽时指定目录分落三子模块 `data/`（不入 git，.gitignore 登记；仅保留契约文件：出题 prompt 两册与 edit_score_prompts.json 随子模块入 git）；③ notebook 两册变一册：每子模块 question_dev.ipynb（合并原 eval_analysis + eval_review：抽样委托 + 分布分析 + 题库审阅，for 题目构造）与 results_review.ipynb（原 wkbench_review.ipynb 改名+按赛道裁剪：打分/评估结果分析，由各自 gen_results_review.py 生成）；④ 抽样排除集改三子模块 data/samples.jsonl 互斥（跨赛道防重复出题）；⑤ bagel 侧 `run_wkbench.py` 同步改址：DEFAULT_QUESTIONS 改指 t2i 赛道新题库（`benchmark/t2i/data/synth_gen/questions.jsonl`），样本图根目录随 --questions 位置自动推导（题库目录与其上级两级候选，适配各赛道 data/imgs/ 布局）。判分/出题协议本身零改动（FACETS 权威源、φ 映射、三维钳制、9 类轮转配额原样随迁）。
>
> **架构决策（2026-08-24）**：公共环境 `env/` 改名 `.venv/`（用户拍板；最初提议 `.env`，因与 dotenv 密钥文件约定撞名——密钥扫描/搜索排除类工具对 `.env` 有特殊处理——改用 Python 环境惯例隐藏名）。同盘 rename 零拷贝；bin/ 内 100 处旧路径（97 shebang + config 脚本）批量重写，conda-meta/lib 零引用无需动（内部亦无绝对路径软链）；bagel 侧 4 处脚本引用与 Jupyter kernel demiwtg 同步改址；.gitignore 登记改 `.venv/`。历史决策块中的 env/ 旧路径为当时快照，不回改。

> **架构决策（2026-08-24）**：`bagel/env` 提升为项目公共环境 `/tank/demiwtg/env`，`.venv-notebook` 退役（用户拍板）。理由：主仓侧实验（水印检测 pilot 等）需要 torch 推理栈，为它单独建环境浪费且易漂移；环境本体与 bagel 代码仓无关，提升后 bagel 脚本与主仓实验共用一套。同盘 rename 零拷贝；bin/ 85 个 shebang + bagel 内 4 处脚本引用 + conda-meta 批量重写（二进制内嵌串与 conda history 旧路径不影响运行，沿用 2026-08-23 迁移先例）。`.venv-notebook` 唯一消费者是 Jupyter kernel demiwtg，改指 env/ 并补装分析栈（duckdb/ipykernel；pandas 用 env 既有 2.3.3），dataset_analysis.ipynb 全 12 cell 复跑零错误后删除。`models/.venv-vllm` 维持 vLLM 部署专用不动。env/ 不入 git（.gitignore 登记）。

> **架构决策（2026-08-24）**：标签体系升级 v3.1（用户拍板三则：死名保留回挂、新增实例分批入库、英文底稿仅扩名单）。外部交付的「融合世界标签体系 v3.1」终版底稿（29 域 / 21,406 路径 / 29.6 万实例，三批迁移终版：域级前缀替换 + 22 个 IP 域吸收并入通用域 + 知识与学科清理）由 `taxonomy/upgrade_v31.py` 一次性升级进仓（干跑→裁定确认→--apply，幂等：每批实例入库后可重跑刷新树名单）。定案：① taxonomy.json 按底稿重建（21,410 节点 = 底稿 21,406 + 4 个底稿缺失域骨架隐式补齐；schema 1.1.0），节点 instances 名单只留在册名（∩ instances.json，跨节点多挂 9,075 名属契约允许），「IP 分类标签」分支随吸收自然消失；② 失配节点 KB 用名单指纹法抢救（前缀替换名单随行 ⇒ 直接名单/子树名单精确匹配 + Jaccard≥0.85 唯一命中，命中 1,483/1,491，报告落 state/taxonomy/v31_kb_recovery_report.json，未命中 8 条可 gen_taxonomy_kb 重生成）；③ 95 个死名实例（粗伞名，如主战坦克/黄道十二宫，涉及 1,010 次打标）不剥离：保留实例与图，按原挂载路径回挂存活节点（零歧义，无退化）；④ 新增 237,781 实例本次不写 instances.json，按域分组候选清单落 state/taxonomy/v31_pending_instances.json，分批富化入库（用户逐批确认）；⑤ alias_western.json 仅扩名单（+522 条 null 占位，英文实例中英不对齐不做别名填充）。图数据（images.jsonl/metadata.jsonl）零改动。交付包归档 state/taxonomy/v31_交付包/（不入 git）；升级前三件套+metadata.jsonl 物理备份在 state/taxonomy/backup_pre_v31/。
>
> **架构决策（2026-08-24）**：v3.1 新增 237,781 实例占位全量入库（用户拍板，推翻上一条定案④的分批富化后才入库）。理由：树名单只留在册名导致 viewer 只见 5.8 万实例，用户要求全量可见。定案：① 237,781 个待入库名以 source=derived 占位（仅 name，与存量 derived 同构）一次性写入 instances.json（58,229 → 296,010），富知识（desc/aliases/query）留待后续按域分批 LLM 富化；② 重跑 upgrade_v31.py --apply 幂等刷新树名单（丢弃引用 0、死名回挂 95 与 KB 保留 1,493 不变；v31_pending_instances.json 清零）；③ alias_western.json 按既定「仅扩名单」策略 +237,781 null 占位；④ viewer 重建（taxonomy.js 11.1 MB / instances.js 72.9 MB，树名单唯一名 296,010 = 在册数，名单⊆在册校验过）。入库前物理备份在 state/taxonomy/backup_pre_placeholder_ingest/。
>
> **架构决策（2026-08-24）**：标签树路径前缀精简（用户拍板）：根『融合世界标签体系』→ `demiwtg`，废除一级分支『通用分类标签』，29 域直挂根（路径口径 `demiwtg / 域 / 二级 / ...`；历史双树期 IP 路径同样归一）。定案落在 `data/taxonomy/upgrade_v31.py`（norm_path 归一函数：底稿路径与旧树路径统一归一，幂等可重跑不回退旧结构）；底稿根行与中间层行同归 demiwtg 空名单按序合并（合并 1 行，节点 21,409 不变）。重跑 --apply 重建：KB 保留 1,493 / 死名回挂 95 / 实挂引用 346,729 / 唯一名 296,010 全不变；KB 指纹抢救失配 0（上轮已全部归位，本轮路径全精确匹配）。消费端同步：① benchmark t2i/edit eval_sample.py 的 branch_of 注释与层级口径（域现在是 segs[1]，抽样分层粒度随之为 域×二级）；② viewer 页标题改 demiwtg、废除已失效的 IP/通用双树过滤器下拉框、sidecar 缓存号升 ?v=3（build_viewer.py SIDECAR_MARK 同步）；③ curation dataset_analysis.ipynb 注释旧路径示例。图数据零改动；改前备份 state/taxonomy/backup_pre_prefix_simplify/。
>
> **架构决策（2026-08-24）**：评测数据由 state/curation/ 迁入 benchmark/（用户拍板）：eval_v1/（360M）、eval_v2/（893M）、taxonomy_sample_cases/（775M，无代码消费者，人工审阅用）三目录同盘 rename 零拷贝。理由：这些是评测**结果数据**而非运行时状态，与出题/判分代码同模块闭环（沿用 bagel/env 提升的同盘迁移先例）。配套：① 全部引用同步改址——eval_sample/eval_synthesize/eval_score 的默认路径常量改自脚本自推（`Path(__file__).parent`）、三个审阅/分析 notebook、gen_wkbench_review.py、bagel 侧 run_wkbench.py 的题库与图根绝对路径；② .gitignore 登记三目录不入 git（代码仍入库）；③ judge prompt 物化目录随 EVAL_DIR 落在 eval_v2/judge_prompts（仍不入 git）。历史决策块中的旧路径为当时快照，不回改。
>
> **架构决策（2026-08-23）**：覆盖口径带质量门（用户拍板）：op_coverage.load_coverage 新增 min_quality/require_identity 两参（默认 8.0/开，即 notebook 默认过滤同款口径），只数合格行；「有图但全不合格」的实例按 0 图对待继续采，缺 quality 字段的存量迁移行按不合格计。chain 新增 --min-quality/--require-identity（BooleanOptionalAction）。重点下载零合格图实例用 `--skip-covered 1`（当前实测范围 5,001 个）；两门全关退化为旧口径（回归验证与无门时计数完全一致）。分区排序（0 图排队首）自动沿用合格计数。
>
> **架构决策（2026-08-23）**：wkbench 首跑冒烟修复（runner 侧补丁，不动官方模型代码）：edit 任务将输入图过 fp32 VAE 编码，在 bf16 autocast 区内 conv_in 直接炸 dtype 不匹配，且编码产物经 NaiveCache 进 vae2llm 时 accelerate dispatch 钩子挂不上。修法两条，都在 run_wkbench.py：① vae_model.encode 包一层（内部禁 autocast + 显式对齐 VAE 设备，因实例属性影子掉 dispatch 钩子的设备搬运）；② forward_cache_update_vae 包一层 shim，在 vae2llm 自身 device/dtype 上做投影（与 generate_t2i.py 的 decode_image 设备修复对称）。vlm/t2i/edit 三任务单卡冒烟全过后才起双卡分片全量。
>
> **架构决策（2026-08-23）**：bagel 项目由 `/tank/bagel` 整体迁入本仓 `bagel/`，成为登记子项目（用户拍板，顶层目录禁令的登记例外）。同盘 rename 零拷贝；定位：Bagel（BAGEL-7B-MoT 统一模型）的训练/推理/评测全链路（权重 28G、hf_home 78G、LMUData 33G、conda env 5.1G 等重物随迁）。配套定案：① 子项目自成一个独立 git 仓库（迁入时新建，原目录系 rsync 落地无历史），主仓 .gitignore 将 `bagel/` 整体排除，子项目内部 .gitignore 只放行代码与配置、重物全部不入仓；② 全部硬编码路径 `/tank/bagel` 已批量改写为 `/tank/demiwtg/bagel`（101 文本文件 + conda env 的 75 个 shebang + pip/conda-meta 元数据，复查零残留），`Bagel/bagel` 自指符号链接改为相对链接，conda 环境迁移后验证通过（torch 2.6.0+cu124，CUDA 可用）；③ 与主仓的接口不变：评测题库读 `state/curation/eval_v1/`，评测 runner 为 `bagel/Bagel/scripts/run_wkbench.py`；④ `bagel/HANDOVER.md` 为子项目内部的下载/环境交接文档（子项目自治，不受主仓“文档只两份”约束）。
>
> **架构决策（2026-08-23）**：新开 `benchmark/` 顶层模块（用户指定，顶层目录禁令的登记例外），用于多模态世界知识+推理评测基准建设（目标：定位 Bagel 类统一模型的短板）。分工定案：出题 prompt（`state/curation/eval_v1/synthesize_prompt.md`）与合成脚本（`curation/eval_synthesize.py`，qwen3.7-plus API）归属 curation，题库与评测样本落 `state/curation/eval_v1/`（不入 git）；benchmark/ 只放审阅/评测入口。首批预合成 10 样本 × 3 任务（vlm/edit/t2i）= 30 题已产出，出题 prompt 核心机制：证据审计防 OCR 捷径、caption 防幻觉传染、probe_dims 短板探针维度 + expected_failure_modes 失败模式预测、JSON 机读输出。bagel 侧评测 runner 落在 bagel 子项目 `bagel/Bagel/scripts/run_wkbench.py`（单卡 accelerate-dispatch 加载 BAGEL-7B-MoT，三任务推理：vlm 走 think+understanding 出文本、edit 走官方 imgedit 口径 cfg 出图、t2i 沿用 generate_t2i.py 参数；按 qid 断点续跑、支持 --shard 分片与 LORA_PATH 注入，产出 responses_shard*.jsonl + imgs/）。

> **架构决策（2026-08-23）**：eval_v2 生成（T2I）+编辑双赛道评测定案，出题与判分代码归 benchmark/，运行时产物落 state/curation/eval_v2/（不入 git）。题目构成：生成赛道由本仓数据分层抽样驱动（新增 eval_sample.py——质量门 quality≥8.0 且 identity=true 读 metadata.jsonl 权威清单，口径修正：质量字段不在 images.jsonl；(L1,L2) 分支配额 ∝ sqrt 且最大余数法恰分 n；排除集剔 eval_v1+eval_v2 既有样本 sha 防背答案；每实例限张），首批实测 500 张（279 张 edit_ok），图片索引自 1099 续编兼容前序产物；编辑赛道按 ImgEdit-Bench 套系构成（9 类 edit_type 轮转配额 + 每第 5 题强制知识编辑套，改动方向须由图外知识唯一决定）。判分：生成赛道照 Qwen-Image-Bench 协议——双线判分（知识线 implicit_checks 权重加和、通用线按题面 facet_tags 激活 22 个裁剪 facet，各 0.5 合成，主体缺失/主题跑偏封顶 30），刻度 {0,1,2,NA} 经 φ 非线性映射 0/60/100 后自底向上聚合，facet 词表单一权威源在 eval_score.py FACETS；编辑赛道原文采用 ImgEdit 官方 prompts.json 九类三维 5 分制 rubric（benchmark/edit_score_prompts.json 作为评分契约代码入 git，按 edit_type 路由，二三维不得高于一维的硬约束在判分侧强制钳制）。配套：eval_synthesize.py 迁入 benchmark/ 并重构（--task all 维持 v1 语义不变，gen/edit 为 v2 专项批次）；出题 prompt v2 两册（synthesize_prompt_{gen,edit}.md）与 judge 模板物化（judge_prompts/）均不入 git；judge 用本地 Qwen3.8-27B vLLM（localhost:8000，thinking + 确定性解码，解析失败计 0 并入异常率），出题走 Galaxy API（qwen3.7-plus，需 GALAXY_API_KEY）。冒烟已过：edit 判分端到端（原图冒充产出正确判「无变化」给 1 分）、t2i 判分（gate 封顶语义正确、0 解析失败）、抽样分层配额与排除集生效。

> **架构决策（2026-08-22）**：下载侧恢复连接复用（显式推翻 2026-08-21 keepalive=0 定案，用户拍板）。新增下载专用客户端（infra.get_download_client，双池直连/代理，Limits 128/64），只挂 dl: 档流量，检索侧维持禁复用不动。三层防线：① 病根已除（stream yield-in-retry 已修，全链零任务取消源，结构性不再产生半读连接进池）；② op_download 每请求硬超时 90s（asyncio.wait_for），超时取消任务经 stream 的 finally 关响应，未读完的连接销毁不入池，永久阻塞降级成丢一张图；③ read=30s 读超时与 supervise 12 分钟自愈兜底不动。理由：下载打 CDN，每图一次全新 TCP+TLS 握手已成实测主瓶颈（py-spy 实锤堵在 do_handshake），实测 6.8 张/s 对闸门理论上限 105 张/s。回退条件：任何停摆/风控迹象 → DOWNLOAD_LIMITS 的 max_keepalive_connections 改回 0 一行回退，重启即可。
>
> **架构决策（2026-08-22）**：覆盖过滤后实例队列稳定分区：0 图实例排队首、有存量（1~N-1 张）的难啃实例沉底（实现在 chain.py 启动期，只改顺序不改集合）。理由：重试区实测撞车 89%、实例速率不到干净区一半，先吃干净区把进度跑出来，难啃实例最后兜底；用户曾疑「降 --skip-covered 阈值能缩小重试区」，实测分布推翻：重试区主体是 36,205 个 0 图新实例（阈值降到 1 也跳不掉），1~7 张存量实例仅 2,811 个，降阈值只会放弃这批已投过资的实例。

> **架构决策（2026-08-22）**：打标前撞车快查回归，推翻 2026-08-21「职责清晰优先于微优化」定稿。Sink 新增无锁只读快查 contains()，chain 的 annotate_worker 打标前先查 (sha, instance) 索引，命中即跳过打标。理由：--skip-covered 8 重试区实测撞车占下载量 89%，原「微优化」场景已变主导成本——不前置则 ~9 成 VLM 槽位烧在重复图上；咨询语义不变契约：索引漏查（跨进程新行）时照常打标，权威判定仍在 sink 锁内，最坏多打一次标永不双写。

> **架构决策（2026-08-21）**：存量迁移链开工：新增 op_backfill（补标算子，与全量打标同 prompt 同口径，只重打 kb_match 并补 identity/focus，richness/caption 沿用存量）与 migrate.py（记录驱动：images.jsonl 炸开为每实例×图一条记录 → 读 blob 复验 sha256 → 补标 → 补 queries/query_langs → 追加 metadata.jsonl）；原算子（op_annotate/op_sink/chain）零改动。配套定案：① 迁移后一图多行合法（去重键 (sha256, instance)，对 sink 的 sha 撞车跳过契约定向豁免，仅本入口）；② danbooru 处置：双源（danbooru/bulk_danbooru2023，共 16,102 行）整体从迁移链剔除、不写 metadata.jsonl，由用户另走开源数据集元数据链路单独处理（migrate.py EXCLUDE_SOURCES；含早前确定性认领写回的 7,324 行，认领成果随 images.jsonl 留存供其链路复用）；③ 迁移收官后 metadata.jsonl 升为唯一权威主清单、删 images.jsonl（届时同步改 2.2/2.4 与 viewer 读端）。

> **架构决策（2026-08-21）**：删除 curation/filter_vlm.py（VLM 图片质量过滤 run/report）。理由：质量/身份类打分字段（kb_match/richness/identity/caption/focus/quality）已由 collect_v2 的 op_annotate 随采集链路内联产出，独立质检流水线无运行进程；state/filter_vlm/ 目录不存在、无任何结果残留，删前核实全仓无引用。curation/ 脚本化流水线至此全部移除，只留数据分析 notebook。

> **架构决策（2026-08-21）**：`data/dataset/` 更名 `demiwtg` 并入 `data/datasets/`（同盘 rename 零拷贝）；`data/taxonomy/` 三件套（taxonomy.json/instances.json/alias_western.json）迁入 `data/datasets/demiwtg/meta/`，`data/taxonomy/` 目录撤销。理由：自建数据本身就是一个数据集，与开源数据集统一收编到 data/datasets/ 下，消除 data/dataset 与 data/datasets 双轨；标签体系三件套是该数据集的标注模式资产，与 images.jsonl 同居，meta/ 成为 demiwtg 的唯一真相区。配套变更：仓库根由 `--meta` 向上推导由三级改四级；gen_taxonomy_kb/gen_instance_kb 断点缓存改放 state/taxonomy/（运行时缓存不许进 meta/ 白名单，llm_common.JsonlCache 自动建目录）；三件套继续入 git（.gitignore 逐级例外），blobs 与 jsonl 大文件维持不入 git。

> **架构决策（2026-08-21）**：删除 curation/annotate_vlm.py（VLM 知识打标 run/stream/apply）、curation/emerge.py（taxonomy 涌现缺口分析）、curation/util.py（meta_lock 随唯一消费者一并失去意义），及四个一次性脚本 fix_abs_paths.py（绝对路径迁移，实测 images.jsonl 已 0 条残留）/ backfill_provenance.py（溯源回填，数据源 runs/ 批次产物已清）/ probe_identity.py 与 probe_retrieval.py（数据依赖 bulk posts_meta 与 v1 DLQ 均已删）；同期清理 state/annotate_vlm/、state/emerge/ 运行时目录。理由：打标职责已由 collect_v2 的 op_annotate 接管（打标随采集链路内联），独立打标流水线无运行进程、无消费者；emerge 依赖 annotate_vlm 打标产物且产物全部可从 images.jsonl 重算；删前逐一核实无残留 import。curation/ 此后只留 filter_vlm。
>
> **架构决策（2026-08-21）**：删除 collect v1 采集系统（整模块 40 文件）与 curation/retry_failed.py（v1 死信重试器，随 v1 失去意义）；采集职责由 collect_v2 接管。理由：v1 已被 v2 三层架构完全替代且无运行进程；残留耦合仅 curation 两处引用 v1 的 meta_lock 文件锁工具，已原样迁入 curation/util.py（annotate_vlm/backfill_provenance 改 import 该处）。同时清理 COS 迁移过程文件（remote_pull.sh、cos_pull/）：186/186 分片迁移已于 2026-08-21 收官并验证（blobs 496G/323164 文件，内容寻址抽检通过），过程脚本不再需要。
>
> **架构决策（2026-08-21）**：HF 数据集落盘区由 state/collect/datasets/ 迁至 data/datasets/（23 个数据集目录整体搬移，同盘 rename 零拷贝；下载过程脚本留 state/collect/datasets/ 只读归档）。理由：开源数据集是长期数据资产而非运行时状态，data/ 才是数据根（.gitignore 早有 data/datasets/ 预登记）；state/ 回归纯运行时语义。同期清理：coco2017 标注 zip 为 0 字节下载失败残留，已删。解压教训：多线程共享单个 ZipFile 并发解压会因共享文件指针竞态产生大量伪 CRC 错误（首轮误判 38% 文件损坏，串行 testzip 复验全部 zip 实际完好），必须每线程独立打开 ZipFile。
>
> **架构决策（2026-08-19）**：标签体系解耦——instances.json 升为独立权威源（实体资产，生灭与富知识不依赖树），taxonomy.json 降为展示视角（树 + 挂载引用）；废除 instances.taxonomy_paths 字段（schema 2.0，实例表 56,789 条一次性迁移零丢失）并删除 build_unified.py。理由：树决定实例生死的反向控制是唯一残留耦合，斩断后数据处理链路（采集/打标/涌现）全部只读实例表；树可自由重生成/多视角并存而不伤资产。需要挂载关系的消费者（collect gap 聚簇、gen_instance_kb 与 emerge 的 prompt 上下文）改由 taxonomy/mount_map.py 从树现算。
>
> **架构决策（2026-08-17）**：broader/ 模块（Open-BROADER 上下位关系模型）迁出本仓库，回归独立项目 `/root/data/projects/open_broader/`（代码、55G 训练语料、训练产物、历史日志整体搬移，脚本内绝对路径已批量改写至新家）。理由：上下位判断本质依赖世界知识，通用大模型（Qwen3.8-27B 批审计 + 现成 embedding 检索）已可覆盖 taxonomy 树审计场景，且训练语料正确性存疑、课题短期难推进，故冻结训练、语料与 checkpoint 原地归档。本决策推翻 2026-08-16 的并入决策；未来如复活，先做大模型 vs BROADER 的 head-to-head 评测再立项。

- 跨模块 import 一律 `from <包>.<文件> import ...`：`taxonomy/`、`curation/`、`viewer/`、`benchmark/` 位于仓库根（消费者先 `sys.path.insert(0, REPO_ROOT)`）。
- 路径常量一律从脚本自身向上推导到仓库根（顶层模块脚本推导两层），不依赖 cwd 之外的魔法。
- 新增脚本必须先归属到一个模块；归不进去的说明职责边界有问题。

## 4. 数据与代码的边界

- `datasets/`、`state/`、`logs/` 是本地数据/运行时产物，**不入 git**（.gitignore 强制；例外：datasets/demiwtg/meta 下 taxonomy 两件套）。
- 入库的只有：代码（taxonomy、curation、viewer、benchmark，含 viewer 页面 HTML）、约束文档（AGENTS.md、README.md）、以及 `datasets/demiwtg/meta/` 下的权威 JSON（taxonomy.json/concepts.json）。
- 大 JSON（images.jsonl、blobs）永远不进 git；需要备份走独立通道。
- 生成产物（`viewer/build/`）不入 git，数据改动后重跑 build_viewer.py。

## 5. 关键命令

```bash
# 标签体系富化（LLM 各一次调用；需 LLM_API_KEY 等环境变量；dry-run 零成本预览）
python3 taxonomy/gen_taxonomy_kb.py --only-empty --write       # 节点 KB（knowledge_intro 等 4 字段）
python3 taxonomy/gen_instance_kb.py --only-empty --write   # 概念富化（aliases→concepts.json；知识文本→docs 层草稿）

# 概念体系维护（2026-09-07 概念化迁移器；instances.json 已退役，勿恢复）
python3 curation/migrate_concepts.py --refresh-taxonomy    # 树变更后刷新 concepts.json 挂载快照

# viewer 产物重建（数据改动后）
python3 viewer/build_viewer.py

# 数据策展与检索接地（curation/，各脚本 --help；notebook 直接运行）

# 图片采集链（已独立为 demiwtg-data 仓库：flow.py + operators/ + smokes/，另含 source_health/source_plan 源策略）
# 见 https://github.com/vincent9299/demiwtg-data

# modelhub LLM 网关 + 静态代理（独立子项目；详见 modelhub/README.md）
bash modelhub/start.sh && bash modelhub/smoke.sh   # 启动+冒烟；停止: bash modelhub/stop.sh [--all]
```

## 6. 禁止事项速查

- ❌ 在 `meta/` 里建除 2.2 清单外的任何文件
- ❌ 手改 blobs/ 下的文件（包括"顺手修一下坏图"——正确做法是重新采集）
- ❌ 删除图片目录前不做 blobs 内容比对
- ❌ 新增只写不读的"审计/日志"文件
- ❌ 在 `taxonomy/`、`curation/`、`viewer/`、`benchmark/` 之外新增脚本（`bagel/`、`modelhub/` 子项目内部自治，不受此限）
- ❌ 往 datasets/ 里放代码、页面或生成产物（viewer 页面与产物在 viewer/ 内闭环）
- ❌ 恢复历史过程文档（docs/、子目录 README）
- ❌ 在数据/代码里使用 category、leaf、root 作为分类概念（instance/实体 一词亦已由 concept/概念 取代，2026-09-07）
- ❌ 在 concepts.json 里为同一 name 写多条记录（一个概念一条；多处挂载表现为 memberships 多行 + 兼容投影多路径）
- ❌ 手改 concepts 行内 taxonomy 快照或 nodes.instances（挂载真相在 master/memberships；新增/移除挂载写 memberships 新版本后发新 master release；`curation/migrate_concepts.py --refresh-taxonomy` 为 meta 时代历史工具，勿再使用）
- ❌ 恢复 meta/taxonomy.json 或 meta/concepts.json 为可写真源（2026-09-21 起真相在湖内 master/；历史工具读端对已删文件自然失败自守卫）
- ❌ 恢复 instances.json 或 desc/query/source 行级字段（已退役：desc→docs 层草稿、query→采集运行时缓存、source→meta.source_stats；历史溯 git）
- ❌ 把 `datasets/`（demiwtg/meta 权威 JSON 例外）、`state/`、`logs/` 或 `modelhub/` 提交进主仓
- ❌ 把 `bagel/` 与 `benchmark/bagel/` 的重物（模型权重、评测数据、vendored 官方 git 仓）提交进主仓

## 7. 网络与下载约定（2026-08-20 新增：环境里残留已宕机代理 100.89.199.67:7890，pip/curl 会被拖死，故将代理策略定死）

### 7.1 采集集群 SSH 互信互通（2026-09-08 建成；2026-09-17 重构：舰队重编号 + 全公网直连 + 废除 sg-master 跳板）

**架构决策（2026-09-17，用户拍板）**：湖机（本机）接管 master 指挥位；全部 25 台腾讯 SG 机改**公网 IP 直连**（经公司代理 CONNECT），不再经 sg-master 跳板；机器重新编号 r1~r20 / p1~p5，sg-master 降级为普通节点 **p5**（kb 审计嗅探收割 + raw/ 工具箱备份完成后可退役）。机器间 10.3.x.x 内网互通、22022-29 反向隧道、各机 cosfs **不受本配置影响**（原样保留）。CN 组 pipeline-e~i 已于 2026-09-10 释放。

**舰队 25 机**（别名=公网 IP + pconn.py 代理直连；同表在湖机 `~/.ssh/config` 头部）：

| 新名 | 旧名 | 公网 | 内网 | 密钥 |
|---|---|---|---|---|
| p1~p4 | pipeline-a~d | 43.160.215.28 / 43.160.238.29 / 43.160.201.131 / 43.160.240.239 | 10.3.4.14 / 10.3.4.16 / 10.3.0.17 / 10.3.8.9 | lighthouse_key |
| p5 | sg-master（VM-0-14） | 43.160.250.196 | 10.3.0.14 | cluster_key |
| r1~r20 | （09-16 起新机，编号不变） | 见 `~/.ssh/config` | 10.3.x.x | lighthouse_key |

```bash
ssh p1   # 任意别名直连，无跳板；湖机→集群唯一通路=公司代理 10.127.48.4:3128
ssh r7
```

**红线**：公司代理惩罚突发 CONNECT——避免同时对全舰队并发建连（巡检/发射错峰，sleep 间隔）；旧名（sg-master/pipeline-*）已从 ssh config 删除，历史文档中出现的旧名按上表换算。

**集群 → 湖机**（唯一反向隧道在 VM；湖机是无公网 k8s pod 只能反向打通）：

- 隧道（2026-09-14 传输线起扩为 8 条）：`/root/tunnel_keepalive.sh`（常驻保姆，30s 错峰重建 + 4h 超龄换血）——**p5(VM-0-14):22022-22029 → 湖机 sshd(127.0.0.1:2222)**，经 pconn.py 代理反向打通；传输线（882 万图 82% 暂停中）续传依赖它，**勿动**。旧单隧道脚本 `/root/.ssh/lake_tunnel.sh` 进程仍在但已被 keepalive 实质取代。
- **全部 7 机均已配 `Host lake` 别名**：VM 直连 `localhost:22022`；a/b/c/d/e/f 一律 `ProxyJump`（a–d 跳 ubuntu@43.160.250.196；e/f 跳各自 `Host sg`），密钥统一 lighthouse_key（pub 已入湖机 authorized_keys，标注 "demiwtg 采集集群"；a–d/f 的私钥已分发，e 原有）。

```bash
ssh lake             # 集群任一机上执行 → 登录湖机 root
rsync -e ssh data/ lake:/yzp/zhaozy/yangzepeng/0905/demiwtg/...   # 数据回湖
```

- **隧道重启**（湖机 pod 重启后须重拉）：`setsid nohup /root/.ssh/lake_tunnel.sh >> /root/.ssh/lake_tunnel.log 2>&1 &`
- **带宽**：SG 组→湖机流量经湖机↔VM 单隧道（跨境），实测 ~10 MB/s 下行 / ~19 MB/s 上行；CN 组→湖机同样经 VM 隧道绕行（CN→SG→湖机）。传清单/代码秒级，传图片级大件按此估算（百万图回湖约数天，建议分批/先 blob 后清单）。
- 坑位记录：pipeline 首连需 `StrictHostKeyChecking accept-new`；ssh 内 heredoc 会丢（复杂配置本地写好整文件 scp，demiwtg-data HANDOVER 坑 #8）；腾讯云 hostname 按内网尾号命名（pipeline-e 与 SG 的 c 撞名 VM-0-17-ubuntu，非路由错乱）；a/b/c/d/f 原不持 lighthouse_key 私钥（已分发）；ssh -o 选项经 shell 变量展开会打碎含空格的 ProxyCommand（隧道脚本用函数直写命令，勿用 $OPTS 变量拼）。
- 采集集群全量机器清单（七机二维架构/分片）见 demiwtg-data 仓 HANDOVER.md。

### 7.2 增量回湖管线 lake_sync（2026-09-08 起，仓库根常驻 daemon）

**〔2026-09-20 退役〕** daemon 末轮 2026-09-10，此后未再运行；仓库根部署足迹（lake_sync.py 运行副本、merge_meta.py、.qwen.bak、sync_daemon.log、SYNC_HANDOFF.md 旧版、`sync/` 状态目录 1.2GB）已全部清理，真源代码保留在 `collect/lake_sync.py`（含 cn 组完整配置留档）与 git 历史。退役理由与边界见文末决策块「lake_sync 回湖 daemon 退役」。以下为运行期记录，路径已失效，不回改。

`lake_sync.py`（仓库根的常驻 daemon，每小时一轮）把集群侧新采集的图片与 docs 知识正文增量拉回湖：

- **模型**：清单增量镜像（每节点/文件记字节偏移，tail 只取完整行）→ 缺集现算（**湖侧实存 = 已同步**，无独立传输账本）→ tar 流拉取（按 sha 前缀 aa 分摊到组内多节点口）→ 逐文件校验后原子发布 → 源端回执清理（先写组桶 `meta/synced_shas.jsonl` 记账再删 COS，24h 宽限 + 每轮限量；**pages 暂不清理源端**）。
- **两类资产两套寻址**：blobs（图片，**内容寻址**，闸门 = sha256(内容)==文件名）；pages（docs 知识正文，**URL 寻址** `page_sha=sha256(url)`，闸门 = **版本化**——2026-09-09 起 docs 行带 `content_sha/page_bytes`（采集端 operators/page.py 记账），湖侧强复验内容哈希；旧行缺省走宽松门（sha256(url)==page_sha + 非空 + 文件名自洽）。同一 URL 重抓内容会变，强门只对新数据成立。）
- **状态**：`sync/`（state.json 偏移 / verified.jsonl / verified_pages.jsonl / deleted.jsonl / sync.log 轮摘要 jsonl / manifests/<node>/ 镜像清单）。
- **节点拓扑**（2026-09-17 重编号）：SG 组 p5（原 sg-master）+ p1~p4（原 pipeline-a~d，共享新加坡桶）；CN 组 pipeline-e~i 已于 2026-09-10 释放（湖侧运行副本 `demiwtg/lake_sync.py` 已摘除 cn 组；断点 state.json/镜像目录 manifests/ 已随重编号改名迁移）。**r1~r20 尚未入 NODE_GROUP**——SDC 投喂清单（各 r 机 `~/lake/meta/image-shard-extsdcfetch.jsonl`）待接入，接入时须把 r 机加进 NODE_GROUP（其 blob 在 SG 桶 datasets/demiwtg/blobs，与 kb/blobs 旧池不同树）。
- **首战成果**：pages 一轮补齐 10,049 页 / 224MB（bench283 知识页覆盖 **283/283**，平均 35.4 页/概念；authority=serp 9,132 / wiki 888）。
- **运维**：① 改脚本后须重启 daemon 才生效（Python 已载入的模块不会热更）；启动 `setsid nohup python3 lake_sync.py > sync_daemon.log 2>&1 &`；停 `pkill -f '[l]ake_sync.py'`（注意坑：pkill 模式串若原样出现在自己命令行里会自杀，用 `[l]` 括号法）。② 新节点重装导致主机密钥变更 → `ssh-keygen -R <ip>` 后 accept-new 重建（2026-09-08 pipeline-h/i 即此因被拒，已修）。③ 湖侧 `images.jsonl` 尚未合并镜像清单（新采集图在 `sync/manifests/` 可见，湖侧抽样/覆盖统计待合并后才反映）。
- **pages 清洗（2026-09-08）**：同步回的原始页是爬虫直出、约 80% 字节是站点外壳，故 `curation/clean_docs_pages.py` 出净版另存 `state/collect/docs_clean/`（原始 `datasets/demiwtg/pages/` 只读不动）。全量 10,020 页 → **净版 4,326 页（43%）**、150.9M 字压到 31.0M 字；分源可用率悬殊（wiki 96% / serp 34%）；拒页两大头 gibberish 2,280（反爬诱饵）+ short_raw 2,052（JS 壳站空壳）合占拒页 75%，均属采集端可避免的浪费。净版含 `concept_docs.jsonl`（{name, kind:"passages", body} 与湖侧 docs 层同构，bench283 覆盖 272/283，建议与现存 summary 摘要层**并存不覆盖**）。分析与呈现：`curation/docs_analysis.ipynb`（demiwtg kernel，含质量分级/原始 vs 净版样例/域名诊断/单概念钻取）。已记录的两类内容缺陷：SERP 概念误绑（如 Stargate SG-1 页被绑到「新加坡」）与近似重复页（en.m/en 双站同文，精确 sha 去重抓不到）。
- **已知缺陷与待办（2026-09-09 明细抽样发现，证据见 `curation/lake_sync_details.ipynb`）**：① **`needed_blobs` 只取 `blob_path`**，而集群两代清单路径键不同——`backfill-shard-*`（7 字段极简、无打标）用 `blob_path`，`image-shard-*`（24 字段、含 width/height/fetched_at）用 `path`，故后者被静默跳过（实测 image-shard 63,883 行中 99% 未回湖）。一行修法：`rel = r.get("blob_path") or r.get("path")`；概念键亦须兼容 `concepts`/`instances`（老 image-shard 用 instances）。② **VLM 打标字段全 null**：镜像里 quality/identity/kb_match/richness/caption 非空率 0.0（连 image-shard 行也是），故湖侧质量门对新采图完全失效（合格行 0）；需集群侧确认打标阶段是否落盘。③ `backfill-shard` 行无 `fetched_at`/`width`/`height`（溯源与尺寸门失效）。④ **概念误绑真实存在**：SERP/fandom 关键词撞车，探针「南丁格尔」14 行中 6 行是 Elder Scrolls 的 Nightingale Armor/Hall、鬼灭之刃与 Terra Battle 角色页。⑤ notebook 性能陷阱：PVC 上逐文件 `stat` 求体积会把整本拖到 ~9 分钟（实测 user CPU 仅 33s），改为「只列目录计数 + 用清单 size_bytes 估算」后全本 **63s**；同理逐行 `os.path.exists` 判回湖应改为一次列目录建 sha 集合。
- **2026-09-09 修正版部署**（repo demiwtg-data 为唯一真源，本文件副本由其覆盖）：① pages 缺集**跨组全局去重**（同 URL 两队都抓过只拉一份，修首轮 29 页双拉）；② blob/pages **两组并行拉取** + State 审计写锁 + tar 超时收紧 3600→900s + **逐批进度打印**（blob批/pages批 行，含耗时与 ok/bad/fail——此前 21h 无输出无诊断即此缺口）；③ 「镜像→真 meta」例行化 `merge_meta.py`（lake_sync 每轮末尾自动调用，已取代 merge_docs.py）：docs 全量重合并 → `meta/docs.jsonl` 10,023 行（283 概念，键 (page_sha, concepts)）；images **增量追加** → `meta/images.jsonl`（键 (sha256,instances)、偏移状态 sync/merge_state.json、不重写 285 万行大账；首轮 63,883 镜像行追加 50,882 新行，35 秒）。结构对齐：内容真源 {blobs,pages}/、清单 meta/（湖 meta/ 另有 concepts.json/taxonomy.json 既有真源）。backfill/dead 镜像不进账（复原操作/死信留档）。消费端只读 meta/，不碰 sync/manifests/。daemon pid 以 pgrep 为准，日志 sync_daemon.log（逐批）+ sync/sync.log（轮摘要）。

- 国内下载**不走代理**，优先找国内源（如 pypi 用 `pypi.tuna.tsinghua.edu.cn`；注意部分域名 DNS 只返回 IPv6 记录而本机无 IPv6，需确认 A 记录可达）。
- 确需访问外网（pypi.org、download.pytorch.org、GitHub 等）时才用代理：

```bash
export http_proxy=http://192.168.10.109:10808
export https_proxy=http://192.168.10.109:10808
# 或
export ALL_PROXY=socks5h://192.168.10.109:10808
export no_proxy="localhost,127.0.0.1,192.168.10.0/24,modelscope.cn,modelscope.org.cn,.modelscope.cn"
```

- 执行任何下载前，先 `env | grep -i proxy` 检查残留：发现已宕机的旧代理（100.89.199.67:7890）必须先 unset 或按上述配置覆盖。
- **外网链路直连优先**（2026-08-22 拍板）：外网源能直连通就直连，只有实测直连不通的才走代理，减少代理流量；代理源名单按实测增删（collect_v2 落点在 `infra._PROXY_SOURCES` 白名单制：2026-08-22 实测 mal/bing_images/yandex_images 直连可通走直连，wikimedia(_zh)/anilist/pixiv/deviantart 直连超时留代理池）。

> **图片全量守护补充（2026-09-10，用户授权选模型和失败拉起）**：本轮沿用已验证的本地 Qwen3.8-27B，候选模型未下载完整，不宣称横向实测胜出。`curation/run_image_pipeline.sh` / `curation/image_supervisor.py` 可接管并恢复当前本地 8000 服务及图片标注进程；该明确授权覆盖此前“不启动/停止用户模型服务”的限制，仅限本任务精确匹配的服务。状态、日志、断点仍在 `state/curation/image_preannotation_v1/`，详见 pipeline_memory.md 设计第 11 节。新增此条是记录本次运行管理授权，不扩展到付费接口或其他任务服务。

> **GPU 让位补充（2026-09-10，用户明确授权）**：图片预标注是利用空闲 GPU 的后台材料整理任务。当前研究实验需要资源时，助手可自行暂停该标注及其本地 Qwen 服务，保留断点和结果，实验结束后恢复原服务与标注；不再为同一让位操作重复询问。具体编排见 `curation/pipeline_memory.md` 第 12 节与 `curation/rag_diagnostic_session.py`。不授权删除标注、不混入其他模型、不影响其他无关任务、不使用付费接口。

### 知识pipeline统一原生算子接口（2026-09-15，用户要求）

用户要求迁移模型调用，并将包括读数据在内的通用能力尽量下沉到demiflow。现役knowledge_debug.ipynb直接使用read_datasource(ReadSource)、Dataset.union、join、reduce_by_key、group_batches、map_cached、map_prompt_async、checkpoint和read_json。ReadSource实现原生Datasource/ReadTask，通用文件解码/gzip/JSON数组流式读取在demiflow，采集schema解释/来源范围审计在curation。知识actor仅做准备、业务校验和构造候选；逐行缓存与阶段落盘均由demiflow执行，不再在新知识链调用旧Stage缓存或LocalModel。提示词、完整请求响应、持久预算、不确定调用阻塞和版本冻结继续保留。当前默认新run为knowledge_native_prompt_v5，状态和限制见pipeline_memory.md 设计第38节；兼容CLI不代表新主线，原始数据、旧run、评分不改写。

### Source直接使用原生读取（2026-09-15，用户纠正）

现役notebook不再使用ReadSource包装：具体文件直接data.read_records → checkpoint保留原始解码行 → filter/map转换业务字段。ConceptFromRecord、DocumentFromRecord、ImageFromRecord不读文件；通用扫描状态、坏行、gzip、JSON解析在demiflow。ReadSource仅供历史兼容。此条覆盖前述现役read_datasource(ReadSource)入口，文件快照冻结、原始材料只读、知识审核及版本边界不变，详见pipeline_memory.md 设计第40节。

### 知识忠实性审核四模型对照（2026-09-15，用户明确授权）

理由：用户要求试用本地四个模型，覆盖此前知识整理只准调用Qwen3.8的模型范围限制，限定为本次小批对照。候选为Qwen3.8-27B、Qwen3.6-35B-A3B、gemma-4-26B-A4B-it、gemma-4-31B-it；通过prompt_config.py中显式local_model_comparison选项，仍只允许本机8000/8001直连，禁止付费网关。该选项不改变公共预标注的原Qwen协议与默认模型。

比较仍使用demiflow PrepareFidelity → map_prompt_async → ApplyFidelity，不重跑提取或开始出题；已有错误回归与基于未参与调参原始文章的受控对照分别记录。每模型两次调用，输入、提示词、参数和全部响应先冻结后比较，不按结果调参重试。按已有GPU让位授权等待标注落盘、暂停其服务、顺序加载模型，结束后恢复原Qwen命令与标注断点。细节与实测结果写入pipeline_memory.md 设计第46节及state/curation/v4/fidelity_four_models_v1，不把模型同意或格式合格视为人工事实核验。


### 工作区保全与 collect 合并（2026-09-18，用户明确要求）

理由：用户要求尽量保留未入库代码，并将 demiwtg-data、kb_audit 统一纳入主仓 collect，旧 data/collect_v2 归档。此条覆盖上文关于采集独立仓库、禁止历史文档归档及新增顶层模块的旧限制。

- `collect/` 是原 demiwtg-data 的现役采集模块，`collect/kb_audit/` 保存原工作区审计工具，`collect/archive/collect_v2/` 保存旧兼容层。其源码、配置模板、交接文档纳入主仓；凭据、环境、数据与运行状态不入库。
- 工作区原 `demiwtg-data`、`kb_audit` 及本仓 `data/collect_v2` 保留相对符号链接，以兼容运行中的脚本；后者只是兼容入口，不放新代码。远端集群路径和 COS 对象键不因本地整合改名。
- `archive/workspace_20260918/` 与 `archive/ignored_sources_20260918/` 保存 state、_staging 和工作区根目录遗漏的源码、文档、配置及有限的小型结果，按原路径保留来源。MANIFEST.json 记录纳入／排除及内容哈希；这是历史快照，不作为新业务入口。notebook 快照去执行输出与内嵌附件；原始文件保留不变。
- `tools/` 是仓库保全工具；恢复或备份相关文档允许保留。原始 datasets/state、模型、Python 环境和依赖缓存继续不入 Git；归档源代码不等于备份完整实验数据。
- collect 不保留嵌套 .git；原采集仓库 Git 历史保存在主仓 archive/demiwtg-data-20260918 分支和本地 backup_audit Git bundle。


### 移除采集旧入口（2026-09-18，用户后续要求）

工作区 `demiwtg-data` 兼容链接已移除，唯一现役目录为 `demiwtg/collect`。现役本地脚本的绝对路径已同步，不再重建旧入口；覆盖上一节对此链接的保留约定。Git 历史归档、历史源码快照及远端 COS/集群路径保留，不因本地入口更名改写。


### 移除 data/collect_v2 兼容软链（2026-09-20，用户要求）

本仓 `data/collect_v2` 相对软链（→ collect/archive/collect_v2）已删除，`data/` 目录随之撤销，覆盖 2026-09-18「本仓 data/collect_v2 保留相对符号链接」的保留约定。存量调用方同步改直连顶层包：taxonomy/audit_nodes、taxonomy/gen_taxonomy_kb（`taxonomy.llm_common`）与 benchmark t2i/edit eval_sample、t2i eval_sample_domain_uniform（`taxonomy.mount_map`），py_compile 与导入解析均验证通过；其中两份 taxonomy 脚本的 collect_v2 导入自 2026-09-05 布局提升起即解析失败（sys.path 指向仓库根而非 data/），本次一并修复。collect_v2.* import 面只剩 `collect/archive/collect_v2/` 归档件（完整源码快照在同级 `collect_v2_staging/`），仅供历史查阅。.gitignore 的 `/data/` 规则保留防复建；历史决策块中 data/ 相关表述为当时快照，不回改。


### 清理 kb_audit 历史工作区（2026-09-20，用户要求）

`collect/kb_audit/`（2026-09-18 自湖机工作区保全的 kb 图池审计工具及 raw/ 历史脚本，共 72 文件）已删除，工作区 `kb_audit -> demiwtg/collect/kb_audit` 兼容软链一并移除。清理依据：kb 图池审计已于 2026-09-17 定案（毒行 7,904,315 / 真图 957,039，清单交付 COS `audit/2026-09-17/`），毒行重收与缩略升级由 `collect/image_backfill/` 现役工具链（kb_orchestrate/kb_backfill）承担，基础审计工具在 `collect/audit/` 另有更新副本；删除前 72 文件全部在 git 跟踪中，历史可恢复，另有 COS `audit/kb_images_20260917/` 双备份。覆盖 2026-09-18「collect/kb_audit/ 保存原工作区审计工具」的保全约定；`tools/WORKSPACE_BACKUP.md` 已同步。历史决策块中 kb_audit 表述为当时快照，不回改。


### lake_sync 回湖 daemon 退役（2026-09-20，用户确认）

lake_sync 线整体退役，仓库根 daemon 足迹已清理：`lake_sync.py`（运行副本，cn 组已摘版）、`merge_meta.py`（与 collect/ 副本逐字节一致）、`lake_sync.py.qwen.bak`、`sync_daemon.log`、`SYNC_HANDOFF.md`（9-09 pages 线前旧版，collect/ 有更新版）与 `sync/` 状态目录（1.2GB：state/merge_state 断点、manifests 镜像、verified/deleted 回执、9-13 图片丢失事故调查件 lost_blobs/lost_ledger/gz-restore-parked）。退役依据：daemon 自 2026-09-10 末轮后未再运行；数据源 p1-p5 已于 2026-09-17 退役且数据保全 COS（node-backup）；三大批次执行线走 COS 直传/直拉不经 lake_sync；r1-r20 SDC 线已定为 parts 收集→去重合并→COS 交付，本条覆盖 §7.2「r 机待接入 NODE_GROUP」的设想。根 `blobs/` 数据池不动；`curation/lake_sync_details.ipynb` 为历史分析快照，读端指向已删 sync/ 属预期；.gitignore 的 /sync/、/sync_daemon.log 规则保留防复建。真源代码 `collect/lake_sync.py` + git 历史，重启需按新节点重配 NODE_GROUP 与断点。同批清理根 `blobs/` 残迹（28KB：9-13 lost_blobs 调查日误落的 3 个 COS NoSuchKey 错误响应 XML，非图片、真湖无对应 sha；真湖 `datasets/demiwtg/blobs/` 与 `kb/` 不动）。


### Pipeline V0／V1命名（2026-09-19，用户确认分界）

理由：用户明确V0应为北京时间9月19日18:23、思路变化前的首次完整备份，V1只保留思路变化后的最新实现。`curation/archive/pipeline_v0/`为该272文件基线，`curation/pipeline_v0/`链接其中的标准pipeline代码；`curation/pipeline_v1/`链接现有`curation/v4/`最新活动实现，版本映射为`curation/pipeline_versions.json`，维护版本只列V0、V1。此前误标为V0的22:34备份已移至`curation/archive/pipeline_history/20260919_2234_checkpoint/`，仅保留历史证据。知识、出题、训练数据、评测四个同级pipeline共用这一版本分界，保持demiflow框架与标准入口。旧目录名仅作兼容链接，保留`curation.v4` import、notebook字节和冻结run标识，避免破坏活动知识生产及实验校验。更早实验V1/V2/V3以及run内源码快照属于历史归档，不是额外活动pipeline版本。

用户于2026-09-20明确更正为四个pipeline；训练数据独立入口为`training_pipeline.py`、`training_debug.ipynb`及`training_stepbystep.ipynb`，V0备份和V1均已包含。版本登记的正式键为`training_data`，`training_branch`仅为旧名称兼容映射，不再将训练数据表述为三条pipeline之外的附属分支。
