# 最新交接：V4知识库衔接并行的benchmark出题与训练数据流程（2026-09-18）

## 先读这个状态，不自动恢复夜间优化

用户最新要求：**“好，是这个思路，之前也有出题的pipeline和prompt，这么修改、怎么和知识pipeline/知识库衔接，你先了解下，整理下材料和文档，完整准备上下文，我们换一个窗口继续。”** 本窗口完成静态代码／prompt核查、真实结果字段检查、材料索引与文档整理。尚未实现下游适配、修改业务prompt或生成新题；没有模型／训练／新采集调用，没有重启服务、提交或推送。下方历史“不要结束／继续优化”均不再是当前任务。

本地主仓 `/yzp/zhaozy/yangzepeng/0905/demiwtg` 的HEAD为 `497a34516b89acb19151e038c1cecaa0364f179b`（main）；依赖 `/yzp/zhaozy/yangzepeng/0905/demiflow` 为 `b5e3f41f25d450a94c315f0d59048467de44e8d2`。**两仓均有大量既有未提交修改**，不能reset／清理／覆盖；下游调查这一段只改本交接、DESIGN及state中的上下文索引。不能只凭早期7b734fc交接推定当前代码。此前知识优化管理器已完成，后台预标注已恢复；本次不重新操作服务。

## 1. 用户已经确认的方向

- **知识pipeline → 多模态知识库 → 两个并行消费者：benchmark出题pipeline、训练数据pipeline。** 训练数据不必先成为benchmark题；可以共享构题与材料审核代码，但正式测试样本不能流入训练。先做golden开发案例是实施先后，不是串联架构。
- 知识库包含可靠文字和视觉资料。独立视角、部件、纹样、状态、阶段等图片即使没有对应文章段落，也可按支持范围收录；不能为保留图片编写无依据正文。身份／来源不明的图仍不放行。
- 文章配图和检索参考共享视觉材料，按用途选择；不另建重复图库。最终文章没有选中，不等于图片无参考价值；同照片去重，保留有信息差异的图片。当前图片偏少既有文章选图造成的缩减，也有真实材料缺口。
- 检索参考提供知识，编辑原图提供待改场景，两者不同；编辑原图可另选有来源的现成素材，不要求都出自知识库。目标图单独管理，用作答案示例／训练监督，不能回流为本题参考，测试目标及近重复不进入训练或检索。
- Caption可以检索和辅助生成候选，不能独立认证知识或图中事实，不能把目标caption答案直写成题面。应由核验知识与真实图片共同决定考点和场景。
- Benchmark不要求唯一像素目标；监督训练需要核验目标，编辑还需真实前后对应与保持要求。目标图可以先发现或后准备，不能按模型输出倒改答案。
- 用户认可回到V1的知识应用关系，V2／V3走偏经验用于纠正；V4已确认原则见DESIGN第20节，最新并行架构和图片角色见第86节。V4知识pipeline当前存在，不等于V4题库已经产出。
- 先做小规模golden开发案例是当前讨论方向；约10–12题是助手建议，**不是已经冻结的数量／请求预算**。用于调方法的留作开发，正式测试另隔离冻结。模型、具体样本、目标图准备方式和训练格式尚未决定。

## 2. 新窗口阅读顺序与材料入口

1. `AGENTS.md`：模块边界、原始数据与历史结果不可变、现役代码不依赖archive业务实现。
2. `curation/DESIGN.md` 第18–20节：V2／V3复盘、主评分分工、完整V4设计；第86节：本轮用户确认的并行关系与知识库图片范围。第20节的旧代码状态仅是当时记录，实际代码以本交接第3–5项为准。
3. 本交接第3–7项：旧代码入口、知识交付现状、训练接口及待实施计划。
4. `state/curation/v4/downstream_handoff_20260918/context_inventory.json`：本次读取入口、代码哈希、函数定位、现存样例和真实接口快照索引（无像素/base64、无大段模型原响应）。
5. 对照旧案例：V1 `curation/archive/v1/pilot_review_zh.ipynb`／`state/curation/knowledge_application_v1/pilot/cases.json`（12题）；V2 `state/curation/knowledge_application_v1/expansion20_v1/cases_review_zh.ipynb`／`cases.json`（20题）；V3 `state/curation/knowledge_application_v1/version3_20/cases_review_zh.ipynb`／`cases.json`（20题）。它们是开发历史，不冒充新golden或未见测试。
6. 当前知识图文：`state/curation/v4/article_pipeline_upgrade/final_knowledge_annotated_v53.ipynb`／同名`.html`，全部正文和图已实际展示，问题旁注明；主入口`curation/v4/knowledge_debug.ipynb`为最新只读结果。

典型复盘：V1腰果体现“概念＋条件→未写明结构”，穿山甲体现知识决定携幼位置；海鹦旧参考只支持繁殖外观、不支持冬羽，不能把V1都追认为通过。V2竖琴核心弦组太小且有遮挡，手势额外要求同步手影，绞胎表面图不能证明断面。V3手摇弦琴局部／全貌图分工可借鉴，驻波的必要知识到曲线转换仍保留；不继承生成底图和无关复杂负担。只读旧记录，不据此重写旧分。

## 3. 旧出题pipeline和prompt的真实情况

| 位置／入口 | 已核对的行为 | V4适配要点 |
|---|---|---|
| `benchmark/t2i/eval_synthesize.py`：`run`、`audit_v60`、`v60_shape_dispatch` | v6.0分支仅输入概念名、taxonomy、层级及动态配额；明确由出题模型自身供给知识，不输入知识库或参考像素。更早非v6.0分支才有caption／图片，不能混称现行全部caption出题。 | 改为版本化知识／来源／真实参考输入，允许材料不足；不沿用模型记忆作为正式知识依据。保留原始响应、断点、输入版本等通用能力。 |
| `benchmark/t2i/prompts/synthesize_prompt_gen_v6.0.md`（312行） | 主动设计丰富场景；L1也要求≥6前提、≥4跳、≥5结论；要求combo_type／premise_types、跨域、场景数及弱点乘积等。 | 保留不泄漏、范围闭合、可观察与合法变化原则，替换复杂度目标和字段；删除的维度不能在validator／dispatch中继续要求。 |
| T2I脚本默认路径 | `PROMPT_FILE`仍指`t2i/synthesize_prompt_gen_v6.0.md`，实际文件在`prompts/`；默认`t2i/data/samples.jsonl`也不存在。已用exists静态确认，未运行旧出题。 | 新入口显式传入／冻结输入和prompt；不能直接启动旧默认命令。当前未修旧路径。 |
| `benchmark/edit/eval_synthesize.py`：`load_inputs`、`build_jobs`、`build_text` | 读取旧focus200素材、生成prompt作caption、`state/collect/concepts_docs_draft.jsonl`作desc；概念与level继承旧T2I bench200；九类编辑、知识题比例默认20%。 | 用知识库供给和独立V4候选／原图素材替代；不继续继承旧200名单、层级配比或仅20%知识题设定。 |
| edit离线链：`cmd_emit_plan → cmd_render_question → cmd_ingest_question → cmd_validate` | 物化prompt和源图绑定，核验哈希，收录裸JSON，原响应不改写，禁止覆盖已完成qid；`audit_v61_edit`包含旧字段与计数门槛。 | 值得复用物化／收录／版本与溯源模式，不能照搬业务validator、配额和削峰规则。来源和ID由程序附回。 |
| `benchmark/edit/prompts/synthesize_prompt_edit_v6.1.md`（222行） | 原图像素优先，caption非证据；但要求九类菜单、固定level、R/P/N义务、scene/premise/hop/weak等大量字段和数量门槛。没有当前知识库图文证据输入契约。 | 保留目标定位、可见事实、单一主操作、合理保持及不能成题的纪律；引入有来源的知识和多图角色。V4排除extract／compose，其他类型按知识与原图选，不凑配额。 |
| `.../synthesize_prompt_edit_v6.1_simple.md`（176行） | 取消部分数量门槛，但仍沿用复杂JSON和枚举，suite固定basic，保留九类语义。 | **不是可以直接复用的V4知识出题prompt**；只可借鉴减少无关负担的部分。 |
| `benchmark/{t2i,edit}/reviews/question_dev.ipynb` | 混有旧抽样执行cell与旧批次查看，运行抽样可能覆盖样本文件；不是V4知识库消费入口。 | 下一版做独立、完整case展示，不执行旧notebook来“看看”，不动既有结果册。 |

应增加明确命名的V4入口／prompt／校验版本，不覆盖冻结的v6.0／v6.1以及历史题库。研究V4与旧prompt版本号不是同一套版本序列，命名需避免混淆。不是只换一段system prompt就完成适配。

保留旧正式主评分：T2I `benchmark/t2i/prompts/judge_prompt_gen_v6.0_V2.md`；edit `benchmark/edit/prompts/judge_prompt_edit_qib_v2.2.md`及`codex_score_prompt_edit_v2.md`。新知识诊断单独存，不把额外来源／另一条件输出偷偷传给旧主判官，不改旧分。历史正式题库`benchmark/t2i/bench200/questions.jsonl`、`benchmark/edit/bench200/questions.jsonl`原样保留。

## 4. 当前知识pipeline和知识库的实际衔接点

现役编排真源是`curation/v4/knowledge_debug.ipynb`内`run_pipeline`；`run_notebook_pipeline.py:load_pipeline`通过AST加载该函数，不是第二条独立业务链。材料清洗／身份 → 正文和图片相关性筛选 → 联合提取 → 一次`final_review` → `PublishArticle`。图片实际Qwen→Gemma独立判断同文同图，仅双keep保留；受共享GPU限制按阶段切换，不伪称同时驻留逐图直通。依赖使用本地demiflow及其未提交修改。

| 文件／字段 | 实际含义及消费注意事项 |
|---|---|
| `RUN/knowledge_base.jsonl` | 每概念一行：`concept, case_id, status, status_reason, identity, documents, images, knowledge, audit`。状态reviewed只是模型和程序完成，不是独立事实核验。 |
| `knowledge[].content.paragraphs`及`references` | 最终正文；references含来源、source_ids、paragraph_indices，可用于追溯。不要只截正文丢掉条件和原文上下文。 |
| `knowledge[].content.images`及`audit.selected_image_ids` | **仅最终文章采用的图**。selection-only模式下caption为空、集中配图区，不保证每图都有细粒度“支持哪条知识”的绑定；不能假定已完成任务级支持核验。 |
| 顶层`images`／`documents` | 来源追溯材料，包含未采用／不合格／待定，**不是可信参考库清单**。例如OK有146条原始图片记录，但本轮发布失败、最终选图0。 |
| `RUN/datasets/related_materials.jsonl` | 更完整的材料检查点。`material_pack.passages`保留正文、context_before/after、source_id及出处；`material_pack.images`为筛选keep候选，保留image_id、bytes.path/sha256、record/provenance、selection_review。 |
| `selection_review` | 含primary_review／independent_review、decision、visible_information、limitations等；可作索引线索与审核记录，模型描述本身不作为独立证据。 |
| `material_pack.pending_images / excluded_images / image_gaps` | 待定、排除与缺图等原因，不能因扩充视觉材料绕过其状态；顶层image_decisions可追溯判断。 |
| `RUN/datasets/paragraph_extract.jsonl` | 分组提取稿及source_catalog、article_image_ids；尚未最终核验，不直接当知识金标准。 |
| `RUN/datasets/final_review_requests.jsonl` | 只包含**各组提取稿实际采用**的原文与图片，不是上游全部合格候选。`PrepareFinalReview`从wanted_s/wanted_i汇集；不能从这里重建完整视觉库。 |

最关键的实现位置：`curation/v4/ops/article.py`的`source_text`、`PrepareArticleInput`、`PrepareFinalReview`、`make_reference`、`PublishArticle`；`ops/multimodal.py:SelectRelatedMaterials`；`ops/image_filter.py:ApplyConfirmedImageSelection`；`ops/material_routing.py`提供按图文相关性组织提取的能力，**不等于下游题目检索器已经存在**。

本次从真实检查点去重计数（“keep”仍为模型候选）：

| 概念 | 筛图keep候选 | 最终发布图 | 当前限制 |
|---|---:|---:|---|
| OK手势 | 28 | 0 | 最终稿因标题解析失败未发布，仍有signer误译；8张拟选图仅在未发布区展示 |
| 白花芍药 | 0 | 0 | 身份可靠图缺口，不能靠放宽选图补齐 |
| 高原 | 9 | 4 | 最终仍含不能确认高原身份的场景 |
| 高锰酸钾 | 15 | 4 | 正文仍有反应范围／主语问题 |
| 人口分布图 | 21 | 10 | 正文仍引入疾病调查史 |
| 基本流程图 | 23 | 0 | 联合提取列表引用解析失败，未进入final |
| 瓶式台球 | 0 | 0 | 材料不足 |

上述keep候选的`bytes.generation_origin`均为`not_verified`。这不是“已确认为生成图”，也不是“已经证明符合V4非生成输入要求”。下一步对拟采用资料核验其来源，不能直接批量宣布可以进golden。

最新真实run：`bench200_sample5_article_v37`、`article_holdout_population_v37`、`article_holdout_basic_flowchart_v28`（均在`state/curation/v4/`）。主册及带问题标注册含7概念、125段已发布正文、18张已发布图；没有隐藏数量。完整结果和未实施修复见紧接本交接之后的暂停快照。

## 5. 可借鉴的多图输入实现与训练侧缺口

- `curation/archive/shared/pipeline.py:check_case/freeze`：旧开发case已有知识、来源、reference_images、edit_source、application_links、knowledge_checks等对应；freeze构造无资料／文／图／图文条件。可参考来源绑定和角色，但它是旧开发四条件专用实现，甚至要求每case有参考图，不能原样套给允许纯文字材料的V4。
- `curation/archive/shared/bagel_runner.py:validate_jobs/make_inputs`与`gemini_runner.py:make_payload`：有序真实图片＋紧邻角色文本，区分edit_source与retrieval_reference；保存实际输入、哈希。原先是人工预选材料，不是已经接上真实检索。新代码不能直接import归档业务链，应在现役模块迁移所需能力并测试。
- 当前正式`benchmark/t2i/eval_t2i_gen.py`主要只读gen_prompt；`benchmark/edit/eval_edit_gen.py:prepare_request`只传编辑原图和edit_instruction。要做有资料条件须显式增加图文输入适配及实际请求记录；只新增JSON字段不会自动传给生成模型。
- BAGEL `bagel/Bagel/data/t2i_dataset.py:T2IIterableDataset`消费parquet的image及captions，构造文本无loss／目标图有loss序列；不是现成的知识RAG训练读取器。
- `bagel/Bagel/data/interleave_datasets/edit_dataset.py:UnifiedEditIterableDataset.parse_row`把image_list当编辑时序、instruction_list当步间指令，并随机选择区间。**不能直接把多张参考图加进image_list**，否则会被当编辑中间态甚至训练目标。
- `bagel/Bagel/data/interleave_datasets/interleave_t2i_dataset.py:InterleavedBaseIterableDataset`已有`_add_text/_add_image`以及`need_loss/need_vae/need_vit`构造能力，可用于后续明确区分参考／编辑输入和目标。`data/configs/example.yaml`仅为既有t2i_pretrain／unified_edit／vlm_sft示例；本次范围内没有发现连接当前知识库的专用训练导出／读取器，未做训练端到端验证。

## 6. 下一窗口建议的具体适配顺序（尚未执行）

1. **先定一个最小可读交付，并只读导出几个现存案例检查。** 两个下游共享同一知识版本和材料索引，包含正文与来源、独立视觉资料、身份／支持范围／状态／缺口。优先复用已存在字段，不让模型重写ID、URL或复杂结构。导出应覆盖文章之外的合格视觉资料，同时守住待定／排除与事实核验边界；不能仅把`knowledge_base.images`改名为references。
2. **将共同选材与各自构题分开。** Benchmark在自己的入口生成评测任务和判据；训练入口独立生成指令／选择原图／准备核验目标。两者复用V4原则和可共用函数，而非复制正式题。可以先人工选资料验证开发案例，再明确实现真实检索；清楚记录二者区别。
3. **一起修改prompt、输入渲染、程序校验和展示。** 新T2I／edit prompt以核验材料和真实像素为依据，保留简短条件→知识→可见要求；去掉combo_type／premise_types及旧数量门槛。原图定位、知识条件、可观察性、合法变化、保持要求和材料不足仍须检查。不要用填假值的方法蒙混旧validator。新prompt应为通用规则，概念身份是核验输入，具体回归错误留在测试记录。
4. **按V4政策重新选择开发材料。** 不能把旧200概念或已调参7例称未见广泛抽样，不强制一概念一题／29域配额。先制作可审阅完整case；golden数量、实际执行模型与目标图准备方式在具体工作中明确。不要立即重跑200或恢复旧无界prompt调参。
5. **训练侧接好监督角色后再谈训练。** 准备目标核验和编辑前后保持，再实现带知识条件的读取／导出；先验证输入参考不计作监督目标、目标不进入输入／检索、不同数据划分隔离。不能拿普通caption→image loader宣称完成ImageRAG训练。
6. **保持原主评分，知识诊断另加；保留所有失败。** 基础读取／导出／引用及角色校验可离线检查，模型运行按届时明确的小批安排；不改历史bench200、V1–V3及知识run。正式代码归benchmark／curation／bagel既有模块，不在根目录散建脚本；训练构建代码最终归属和存储格式尚待落实。

当前没有新pipeline命令可直接执行，也没有“新golden set已完成”。本次文档记录的是可核验的现状、已确认方向和下一步建议。旧主册已有脏修改和输出，下一窗口不能为了新展示覆盖它们。

## 7. 保留的工程状态与继续口令

知识canonical仍为`image-relevance-v5-chart-identity / article-joint-v16 / final-review-v53`，两仓代码有未提交改动。模型筛选通过、程序reviewed、助手语义复核与独立事实认证必须区分。具体concept例子仍在共享prompt中；人工身份说明有OK／玻璃棒／白花芍药三项，不能称完全通用。

未实施解析候选`state/curation/v4/article_pipeline_upgrade/joint_list_refs.patch`和无正文父标题兼容问题留存，不自动应用。此前247公共＋独立清洗4测试通过；本次只做文档和静态接口核查，不把旧测试算作下游适配已通过。原实验全部结束、最新结果已展示，夜间优化暂停保持。

新窗口可以直接说：

> 继续V4下游适配，先读 curation/KNOWLEDGE_PIPELINE_HANDOFF.md 顶部最新交接和 curation/DESIGN.md 第20、86节。知识库作为共同上游，benchmark出题和训练数据两条pipeline并行；参考图片是知识的一部分，可独立于文章配图，编辑原图与目标图另按角色管理。先核对现有接口，落实最小知识交付及新版出题输入／prompt／校验，再准备完整golden开发案例。知识优化仍暂停，不自动重跑200或恢复夜间调参。

---

# 暂停时快照：prompt通用性及下游用途讨论前的知识结果（2026-09-18）

**最新用户指令覆盖下方历史“继续优化／不要结束”的要求：先停在当前结果，讨论 prompt 是否通用，以及如何为 T2I／edit benchmark 和 ImageRAG 方式训练准备知识与图片。不要自动恢复调参、新增概念或 bench200。** 当前管理器 session17581 已 exit0，日志确认 `Completed; preannotation restored`；没有新实验在运行，后台预标注已恢复。未启动／读取预登记南非国旗，未启动200、未提交推送。

已完成五概念 `bench200_sample5_article_v37`、人口 `article_holdout_population_v37`、基本流程图 `article_holdout_basic_flowchart_v28`，质量记录及台账139条。仍未收敛：白花主要回归通过但无可靠图；OK最终稿仍有signer误译，且无正文父标题导致发布失败；高原仍选身份不能确认的雪山场景；化学仍有反应范围及主语问题；人口又搬入疾病调查史；基本流程图因联合提取列表引用解析失败，没有进入final。程序reviewed不代表语义正确，模型审查自述不作为核验结论。

主 notebook `curation/v4/knowledge_debug.ipynb` 已实际刷新为以上三run，完整7概念、18张已发布图及全部正文，0 cell错误、0新模型调用、知识hash不变。最新带问题标注的完整查看文件为 `state/curation/v4/article_pipeline_upgrade/final_knowledge_annotated_v53.ipynb` 及同名 `.html`，含125段已发布正文、18图、7处问题标注，OK未发布原始稿的8张拟选图单独明确标注。独立notebook也已执行无错误。此前 `final_knowledge_annotated_v52_v53.*` 为旧快照，保留作对照，不混称最新。

canonical仍为 image-relevance-v5-chart-identity / article-joint-v16 / final-review-v53。实际prompt是共享规则加具体回归例子：芍药物候期、青藏高原、人口图、手势词语翻译等；notebook另有OK手势／玻璃棒／白花芍药三项人工身份说明。因此不能宣称完全概念无关，也不能把已反复调参的案例视为未见概念泛化证据。建议后续把回归例子与生产通用规则分开，概念身份作为经核验的输入资料；本次仅讨论，未修改prompt。

下游讨论方向：知识文章配图不等于完整可用图片库；保留通过核验的图片候选供不同用途选择，分别考虑文章展示、编辑输入、检索参考、训练目标。不能因图片不足放宽身份要求，也不能把目标答案图或近重复图当检索参考。最终文章之外的材料输出、下游出题／训练流程均尚未因本次讨论新增或改造，不把建议写成已采纳架构。

未实施候选：`article_pipeline_upgrade/joint_list_refs.patch` 只暂存，六组实际原响应在内存候选解析中均通过，记录 `joint_list_refs_candidate_validation.json`；尚未应用。OK无正文父标题兼容问题也未修复。已实施代码和247公共＋独立清洗4测试结果见下方记录；暂停后未再改业务代码或启动模型。

---

# 历史：joint16 / final53 启动时记录，已由上方完成状态及暂停决定覆盖（2026-09-18）

继续用户授权的夜间优化，主要问题收敛后再跑预登记南非国旗完整双模型链；不要在此结束，不跑200、不提交推送。当前管理器session17581，`state/curation/v4/article_pipeline_upgrade/final_v53_manager.log`，拥有STOP并负责恢复。目标五v37/人口v37/基本v28，从最新筛图材料五v36/人口v36/基本v27重新跑joint及final。运行期间不要改canonical/业务代码/服务/STOP或另启模型客户端。此前89886管理器已exit0，Qwen健康、worker33878、STOPfalse。

v52完整结果：人口v36主要图文回归通过，保留1幅正确人口数量图，但审查把密度/数量图视为不匹配人口分布过严，召回有限。五v36白花科名/物候期/无图正确；OK方向/4图正确，但signer与checking in误译。高原最终仍选青藏面积最大（当前原文另称巴西最大），搬入实例物种数量和居民年代，2图正确；不明雪山图已被joint排除，不能说final放行。化学原始成稿仍有热水有助溶解/不宜热水相冲突、sodium bismuthate→硝酸铋和并列设备被合并；当前83原文及上下文均已读，水温冲突本轮确有，不沿用旧无温水池判断。KOH具体反应确有原文，可保留；tropical ulcers本轮确有，不误判。化学成稿拟选5图均实际看过，**实际发布为failed、无正文配图**，原因是审查写【资料24】【25】【28】省略类型导致unknown_review_marker，不能称拟稿已发布。

基本v27双模型各10批，23keep/15exclude/1pending，实际同文同图通过。6组joint中第4组为0原文4图片，却虚构资料1/2/3引用，整概念正确阻断，未调用final。`blocked_extraction_validation.json`记录，不用只接受成功稿的旧验证器硬套失败组。三个run质量记录/台账136条完成；五v36来源/像素/预算检查通过不代表语义通过。

已实施代码：final输入先全部采用原文、再完整提取稿作覆盖提示（不增加材料/像素）；joint渲染与解析仍逐行保持三父旧输入，code_validation_v53.json。材料复用守卫新增select_images的version/template/model/schema检查，旧article_selection_v2被正确拒绝。审查中紧邻【资料1】【2】可按明确首类型归一；不跨文字推断、不给正文补证据、不重新选图，未知编号仍失败。化学旧响应重解析通过但未改历史/未改语义未通过结论。247公共测试+独立env-cleaning4通过；首次误忽略test_cleaning.py导致缺jieba及测试检测旧标签的失败日志保留，已改正执行范围及保留明确待检查提取稿标签。两册代码编译通过。

canonical候选joint16/final53已冻结：joint合并重复规则，明确纯图片组不得引用资料，不照抄示例编号，保留原文对象/条件及配图约束；final原文在前，合并规则到1607字+130字结尾，处理跨对象排他排名、实例百科、技术译名、并列关系、最终正文相反操作要求。因joint变更，三组均重新提取，不称相同草稿final对照。仍medium/65536/full draft/selection-only/审查记录，联合low/16384。计划final_v53_plan.json，候选未评估。

主notebook正在刷新为v52实际已发布结果和明确失败状态（notebook_v52_display.log），不展示未发布拟稿为知识。最终必须再实际刷新最新所有概念、正文和图，无数量限制。刷新helper将通过措辞改为“主要图文回归通过”，不会暗示全部事实认证；known limitations可显示。fresh南非国旗尚未开始、未读数据，先把现有主要问题收敛。当前新增未看过的化学拟选图I86c6297c9b55已查看：产品标签拼图，与3/5不是同照片副本。

---

# 当前：图片筛选v5 + joint15/final52按原生链顺序复测（2026-09-18）

继续至主要问题相对收敛后新增预登记南非国旗全链；不能提前结束。管理器session89886，article_pipeline_upgrade/filter_v5_manager.log；新state工具run_filter_candidate.py顺序跑人口v36、五概念v36、基本流程图v27。没有外层STOP，原生image_review_service自行借用两GPU运行Gemma并恢复Qwen/预标注；运行中不要改变canonical/服务或另启模型客户端。计划filter_v5_plan.json冻结三prompt哈希，复用父人口v6/selection_v2/基本v2的清洗、identity及未改正文筛选，真正重跑Qwen与Gemma筛图、joint及final。

canonical image-relevance-v5-chart-identity / article-joint-v15 / final-review-v52。统计图必须从实际像素辨认变量名，轮廓/M数值不能证明人口变量；无法辨认则pending，不要求普通流程图也有变量名。joint15/final52进一步禁止根据中文网站补中国，将本品/制剂的分类泛化为整种物质；药片规格不明不提取按片配液操作。其余证据、简单文章、只选图号、一次final、medium/65536保持。

v51管理器已正常结束，三run exit0，Qwen健康/worker24612/STOPfalse，resource.json已存。完整最终响应、当前来源池及全部最终图片已核对。五v35白花科名/物候期和无图通过，OK方向及4图、高原机制/范围及3图主要通过；化学65原文中未提供KOH反应对应原文，最终正确删除草稿方程式，溶解性冲突及就医要求改善，但无依据添加“中国药品分类”仍失败。人口v35保留1830似乎、设计参数、限制与dasymetric缓解，但6图中I48039eed32c2无可辨变量名仍入选，因此未通过。基本v26主要通过，5图中大红字覆盖的I452只作一般示意，不能拿其遮挡细节解释。质量记录、来源/像素/预算校验和台账133条完成；不把审查记录自述当真实正确性证明。旧水温/KOH/地域审查更正仍有效。

主notebook已实际刷新v51五v35/人口v35/基本v26，7概念22图及全部正文/审查，0新调用/0错误/结果hash不变（notebook_v51_display.log）。当前运行结束后仍须再刷新，不能只改SAVED_RUN。46article/245公共/独立清洗4测试已通过；本轮仅prompt与state运行工具，无业务代码变化。fresh南非国旗尚未启动/读资料，bench200未启动，未提交推送。

后续还须补断点守卫：validate_material_reuse目前只比固定policy和正文prompt，未比select_images的实际prompt。checkpoint_guard_plan.json/evidence.json已记录，旧selection_v2模板/版本与当前不同却现有守卫未覆盖；当前人口v36匹配。等待管理器结束后再改业务代码及测试，不能在当前运行中改。人口v36双模型9+9批同文同图检查已通过，21keep/12exclude/3pending；I480两者均pending，城市航拍被Gemma排除。人口v36已完整结束：6joint+1final、21/12/3筛图；主要图文回归通过，最终1张正确人口数量图，完整12原文已核对，来源/像素/预算检查及quality已存、台账134。审查记录仍错误声称人口密度/数量图不匹配人口分布图，配图偏保守，不称图片召回充分；最终图文未错配。管理器已转五v36，目前Qwen筛图，随后基本v27。补守卫的patch已存article_pipeline_upgrade/checkpoint_guard.patch，须管理器完全结束后才apply并测试。

五v36后续进展：55次Qwen+35次Gemma均返回，实际同文同图及仅双keep检查通过，219图为52keep/89exclude/78pending。白花0keep；OK28、高原9、化学15keep。Qwen一批JSON局部格式坏，I3e3c352f1ae8/I3f80adadf416/I4b5bedc25afc按protocol_invalid待定，未补造/放行（image_protocol_observations.json）。新增高原I59755fec9d30为宽平顶崖壁，可作地貌候选；I5ad6cac47410仅湖泊帐篷雪山、I5b55594f41d9仅岩石山谷雪山，像素不能确认高原。三图已实际查看，记录new_pixels_v51_v52.json，须检查后续joint/final是否排除后两张。服务已恢复，材料embedding完成并进入joint，尚无最终响应。原两张mesa Ic2afea8cc00f/Ida645610c712被Gemma pending；不能把这当新增正确性证据，偏保守过滤仍有召回损失。

五v36已完成16组joint且程序均draft无validation/review_required问题，进入final：白花和OK、高原完整最终响应已读；化学仍pending。白花科名冲突/物候期/无图正确；OK方向和4图正确，但checking in→签到、signer→签名者不自然且有歧义，审查承诺精简13件争议却正文仍全列。高原未通过：当前资料2称巴西面积最大，5/9称青藏最大，最终选了后者；还搬入青藏物种数和最早人类活动年代。最终2图I59755fec9d30/I2101963418ab已看且无同图副本，不明雪山图和重复Ie899均未入选。

五v36最终来源池比v35：OK23/白花3完全相同；高原9→19（13新增全文已读）；化学65→83（31新增全文已读）。**必须读source_text渲染中的context_before/context_after，不只看text字段**：新增helper read_evidence.py；当前高原和化学所有供应上下文也已完整读。高原冻土两倍升温/40%暖冻土在资料15后文中，不能误判无证据。化学当前温/热水确实进入池（10/11热水、58温水、18/19凉开水）；46是确有KOH方程式的完整原文；不可沿用v51无暖水/无KOH的判断。OTC U4bf149aea81dc86c和分类U8d66bdc1352909ee已从本轮池省略。化学26=6.51水溶解度、72=6.4及微溶属性、68=含就医急救、69=无质量规格的按片配液。详细adopted_evidence_review_notes.json。当前59确写tropical ulcers，其他来源topical，不能一概认定热带为误译。

**仅暂存未实施的下一轮候选**：final_review_v53_candidate.yaml、final_source_first.patch、final_v53_pending_notes.json。拟将final输入原文置于待审稿前（joint输入应完全不变），保留完整稿作覆盖提示；排他排名需跨对象对比、删实例百科、上下文翻译（signer/checking in）、图标题不必与概念名逐字相同（人口数量/密度均可）。必须等当前89886管理器完成化学和基本v27并恢复资源后，再定最终53prompt/应用source-first和checkpoint_guard.patch、核对三父joint逐行不变、跑必要测试、冻结原生计划。从新筛选v36/v36/v27复用提取做final对照，不用旧v4筛图材料。未通过前不跑fresh南非国旗，仍未读其数据。不要在这里结束。

---

# 当前：joint14/final51重新提取并review正在运行（2026-09-17）

继续优化至主要问题相对收敛，然后新增南非国旗全链，不能在此结束。管理器session94126，article_pipeline_upgrade/final_v51_manager.log；拥有STOP final_v51并负责恢复。目标五v35/人口v35/基本v26，父材料v10/v10/v2，reuse_stage=materials，重新跑joint及final，不复用错误提取稿。运行中不改canonical/业务代码/服务/STOP或另启模型客户端。

canonical article-joint-v14 / final-review-v51。joint14从13最小修改，明确属性与对象、物候期≠花期、appears保留、方向、地域和操作必要要求；final51同步措辞边界。化学要求不同反应情境不归纳为仅由酸碱性决定的通用规则，可保留原文明示反应物的具体方程式，不补强弱等隐含条件。处方/非处方地域不能根据中文网站补中国；原文范围不明就省略分级结论。仍一次final、medium/65536/完整已采用原文和像素、只选图号、普通短审查记录。没有新增模型步骤或自适应effort功能。

v50包括xhigh诊断已全部结束、自动恢复Qwen/worker18522/STOPfalse。xhigh正常完成55975completion（medium15794，约3.54倍），实际messages完全同文同图，却仍保留缺反应情境的通用归纳；更多字数未解决主要问题，未采用。完整响应/80原文/2选图核对、来源/像素/预算检查及台账130条已记。其他原生v50结果见下方，仍不收敛。

**审查边界澄清**（scope_review_correction_v50.json，已附v47–50五概念quality及台账）：资料47明确有KOH参与的具体反应，不能断言不同具体反应绝不能并存；应修缺条件的通用归纳。资料69/70没有明示中国，之前把“补中国”表扬为范围改善是过度推断，撤回；不能要求模型猜法域。旧温水段不在joint13池的更正仍有效。原始响应/知识都不改，未通过结论因其他问题保持。

代码最近变更仍为v50审查格式兼容：明确##文章主题可作正文边界，明确列表/有界升序范围只展开为audit引用，不给正文补证据/不重新选图。46article/245公共测试通过；env-cleaning4之前通过。此次v51仅prompt修改，不重复测试未变代码。

主notebook已实际刷新为v50五v34/人口v34/基本v25，完整7概念19图、所有正文/记录，质量未通过明确标注，0新调用/0错误/hash不变。v51结果结束后须真实再刷新，不能把诊断混入正式结果。

下一步等v51联合提取和最终输出，按新source_catalog重查来源池增减、新原文及全部最终图文，不能沿用旧编号/把被排除材料算作final看得到。记录quality、运行/像素/预算检查、台账、资源恢复。主要问题收敛后南非国旗完整Qwen+Gemma链；尚未启动/未读资料，200未启动、未提交推送。

---

# 当前：final-v50复测及化学xhigh对照正在运行（2026-09-17）

继续优化，不能在此结束。管理器session73190，article_pipeline_upgrade/final_v50_manager.log，拥有STOP final_v50并负责恢复。三个原生run：五v34/人口v34/基本v25（父提取v31/v31/v22不变）；额外同prompt/原文/像素的article_chemical_xhigh_v50，只测高锰酸钾，xhigh/65536/1800秒。正式仍medium/65536，诊断结果不能冒充正式接入。运行中不改canonical/业务代码/服务/STOP或另启模型客户端。

canonical article-joint-v13 / final-review-v50。v50保留相关方法对目标有用的参数/限制/缓解，缺少总定义不补写；原文属性名不自行细化，泛称不能套到物种；先按主题比较所有原文，不以常识补隐含条件。原文/像素池不变，一次final_review不增加模型步骤。

v49全部完成且自动恢复Qwen/worker10646/STOPfalse。基本v24主要通过；五v33 OK方向修正，白花科名冲突省略但花期5月依据不足（原文物候期，另一段泛称芍药）；高原正文主要通过但漏## 最终知识被阻断；化学反应结论并列/急救就医遗漏仍在，且【资料26/30】格式被阻断；人口v33删除有用设计参数与限制、把方法定义换成目标定义、1830 appears丢失，另因【资料1–6】阻断。完整原始响应/实际拟选图/当前原文已核对，quality和程序检查、台账126条已记。不得把程序失败的原始成稿说成已发布知识。

程序现在兼容审查记录后直接接##主题的清楚边界；审查中明确编号列表（含斜杠、裸列表）和【资料1–6】等有界升序范围逐个展开验证，只用于audit，不用于正文证据或选图。未知/倒序/越界/缺正文边界/无引用/截断仍失败。完整原响应不改；review_format_reparse_v50.json证明旧3类格式可解析，未重写历史结果或判语义通过。46项article及245项公共测试通过；env-cleaning4此前通过。三父joint逐行解释不变，两册编译及本轮diff检查通过。

主notebook已实际刷新v49五v33/人口v33/基本v24全部7概念7图，全部已发布正文和失败状态，0新调用/0错误/hash不变。v50完成后须再真实刷新全部最终结果/审查记录。inspect_final_text.py RUN --concept NAME只打印最终响应和usage，不打印思考或base64。

v50原生三个run已全部返回并完成完整图文/原文审查、程序检查与台账129条；管理器仍等化学xhigh，未恢复前勿另启客户端。基本v25主要通过；人口v34方法范围和核心覆盖恢复、4图变量清楚，但1830布点方法的appears仍丢失；五v34 OK方向保持且删重复争议事件，高原机制/实例范围保持，白花仍将物候期/泛称芍药写成白花花期5月，化学就医要求已恢复但碱性不同产物并列和OTC地域遗漏仍在。五v34全4final均reviewed、瓶式台球insufficient_materials，6/0/3/4张配图全部已看且无已知同图副本。

实际medium与xhigh化学请求的messages逐字/像素完全相同（actual_input_comparison.json），模型/temperature0/65536一致，仅effort不同。下一步等article_chemical_xhigh_v50完整响应、审查、程序验证/记账并确认资源恢复。若xhigh确实改善，要先作正式配置原生复测，不能混用诊断成稿。可考虑在新prompt下复测全局xhigh；旧v46白花无限思考发生于无审查记录的旧prompt，不能直接推定当前同样失败。按输入规模自动分配effort只是考虑，尚未实现，避免无证据工程扩展。另可修joint中的原文限定错误（物候期/花期、appears、方向与地域），这些错误已在草稿中，final反复沿用；已仅在state保存article_joint_v14_candidate.yaml，尚未采用/测试；从joint13最小修改，强调对象/属性名（物候期≠花期）、不确定词、方向、地域与必要处理条件。尚无final51或下一轮计划；先等当前xhigh结果。化学80段的完整source_text和body各80个精确不同项，不能靠精确去重减半。主要回归相对收敛后跑预登记南非国旗完整Qwen+Gemma链，尚未开始/未读资料；200未开始、未提交推送。水温旧误判见下条纠正，不能再计作joint13最终冲突。

---

# 当前：v48已完成，v49正在复测（2026-09-17）

继续到主要问题相对收敛，再加预登记的南非国旗完整链。v48五v32/人口v32/基本v23均完成，资源自动恢复Qwen/worker62314/STOPfalse。人口与基本最终知识主要回归通过；五概念仍有白花同页分类冲突自行解释、OK掌心方向翻译反转、化学碱性产物并列及就医条件遗漏。全部最终文字/审查记录/所选实际图已核对，程序来源/像素/预算检查通过，台账123条。审查记录是模型自述，不作为正确性证明。

**审查纠正**：joint13的化学80段最终原文池不含旧温水段U7193cbdebdb0da95，因此v47及v48的凉开水不能判作忽略给定水温冲突。v47 quality与台账已更正，原判断备份在bench200_sample5_article_v31/review_correction.json；原始模型结果不改。就医条件仍在本轮资料66，遗漏判断有效。下方历史水温结论须按此纠正。

v49候选/计划已冻结：final_review_v49.yaml、final_v49_plan.json；管理器session69815已启动，拥有STOP final_v49，等待后台在途任务退出后自动开始。保持joint13、完整稿/采用原文/像素、medium/65536、一次final。加强同页冲突、方向否定、操作必要要求；审查仅简记文字问题、不生成图片外观/来源猜测。程序规范审查中明确列出的裸资料/图号和逗号编号，未知编号仍拒绝，不推断范围，不改原响应，不因审查提图选图。44项article测试通过；公共环境243项非清洗测试通过，清洗对照误在公共环境运行时3项因缺jieba失败，已在env-cleaning中4项全通过（日志均保存）。主notebook实际刷新v48完成，7概念16图、所有正文/记录，0新增调用/0错误/hash不变。

下一步等run_final_candidate.py的final_v49_plan.json完成（五v33/人口v33/基本v24，复用v31/v31/v22提取）。运行期间不改canonical/服务/STOP或另启模型客户端。主要问题通过后南非国旗全链，尚未开始/未读材料；200未开始、未提交推送。

---

# 当前：final-v48显式简短审查记录正在实测（2026-09-17）

**继续优化，不要在此结束。** 管理器session4642，article_pipeline_upgrade/final_v48_manager.log，目标五v32/人口v32/基本v23；复用刚完成五v31/人口v31/基本v22的joint13提取，六次final调用。拥有STOP final_v48并负责恢复；不要改canonical/业务代码/服务/STOP，或另启模型客户端。

canonical article-joint-v13/final-review-v48；两册medium/65536/完整待审稿/只选图号，并加final_review_notes_required=True。仍一个final_review调用：先“## 审查记录”用普通文字写实质问题和删改决定，再“## 最终知识”写落实决定的知识。程序将审查记录与正文分开；审查引用只进audit.review_references，不会重新选入图片。知识每段自己的引用/完整性要求不变。最终prompt重整为约束与简单两部分，明确冲突不能选边/拼区间、比较范围和不确定性、操作必要限制/就医要求、地图实际读到变量名。无JSON/复杂字段/新模型步骤。

代码：ApplyArticle新增仅final启用review_notes_required，split_review_record严格边界、非空记录、输入编号、未提前结束；PublishArticle单独audit.review_notes及程序映射review_references；current_results完整显示审查记录并明确非知识正文/待审材料编号；article_trial与两册调用同步。原joint解释逐行验证三父run均不变。公共240项通过（tests_review_record_public.log，34.25s）；又补1条审查提图不复活图片的测试，全article42项通过（tests_review_record_images.log）。此前dep106/清洗4未受影响。两册编译/本轮diff检查通过。新候选未评估。

v47medium全部结束恢复；完整结果：基本v22主要回归通过；人口v31不明变量图I480/最早范围/appears丢失；五v31白花科名选边、化学6.4与6.51拼区间/碱性产物双说/药用水温选边/急救就医要求丢失未通过。OK及高原主要改善保持，新增图与原文全读；化学4图均此前已核对。quality、程序检查、台账120条均保存。

主notebook已实际刷新到v47五v31/人口v31/基本v22：全部7概念21图和全部正文，0新调用/0错误/hash不变、失败如实标明。源码已v48记录模式，当前保存的v47输出自然无审查记录；v48完成后须再真实刷新。

下一步等v48完整记录+正文，核对记录提出的删改是否在正文执行、全部实际配图/源文范围/冲突/覆盖/完成。失败继续有依据修。主要旧例相对收敛后再跑预登记南非国旗完整Qwen+Gemma链，尚未开始；200未开始、未提交推送。下方历史。

---

# 最新进度：v47/medium仍在等化学最终响应（2026-09-17）

当前管理器仍session72196，final_v47_medium_manager.log；五v31的18组joint已完成，OK/白花/高原final已返回，化学待完成。不要改canonical/服务/STOP或另启模型客户端。基本v22主要回归通过（4原文/2图）；人口v31未通过：无变量名的世界地图I48039eed32c2被选入，最早district-based dot map被扩成最早人口分布图之一，1830方法appears限定丢失。其点值/设计权衡/密度范围与半透明缓解保留；dasymetric原文这次joint未采用，记录独立覆盖变化。两run的quality、程序检查、台账已记，119条。

五v31部分审查在final_v47_medium_partial_review.json：OK完整正文和5图通过主要问题，final修正joint的ASL掌心方向；白花正常完成但科名冲突选边，未通过；高原保住有用机制和实例范围，3图已核对且无已知照片副本，篇幅偏长是精简观察。五v31所有新增原文已完整读过，new_source_audit.json记录：化学新增资料74水溶解度6.4/6.51、甲醇丙酮微溶/best相冲突；资料56为纯化标准溶液操作，与药用配制不同，要保留具体条件。化学尚未评价，五v31尚未写最终quality/ledger。

下一步等化学完整输出、核对所有图文并完成管理器恢复后记录。现仍不收敛，南非国旗尚未开始。考虑下一版让同一次final_review先用普通文字写简短核对结论、再写最终知识，以免直接沿用草稿/自选冲突一方；这是待评估想法，尚未改prompt/解析/模型步骤，须先看化学实际结果。另需明确地图读到变量名，单位与轮廓不够；最早/排名保留原文比较对象、不确定性。没有v48候选文件或代码，canonical仍joint13/final47。主notebook实际输出仍五v28/人口v29/基本v20全部7概念12图，最终须真实刷新。

---

# 当前纠正：v47使用medium，重新提取仍在运行（2026-09-17）

**继续工作，不要在此结束。** 用户要求相对收敛后再加新概念。canonical article-joint-v13/final-review-v47；两册final_review_effort=medium、final_max_output_tokens=65536、final_draft_outline_only=False、final_image_selection_only=True。联合提取low/16384，其他限制未变。

当前管理器session72196，article_pipeline_upgrade/run_final_candidate.py final_v47_medium_plan.json，日志final_v47_medium_manager.log，目标五v31/人口v31/基本v22，从父v10/v10/v2已筛材料重跑joint+final。管理器先用真实本地Qwen模板验证配置，再拥有STOP final_v47_medium，负责恢复。不要改canonical、切服务、删STOP或另启模型客户端。

此前误设high后查本地模板发现只支持xhigh/medium/low，已精确终止原v47三个客户端和管理器，finally恢复资源；旧五v30/人口v30/基本v21记配置错误早停，非模型内容失败，不恢复旧run。session35368 exit130为主动SIGINT。configuration_stop.json与final_v47_config_rejection.json有记录。台账117条。下方high描述属于已取消配置。

v46五v29早停原因是白花xhigh耗尽65536仍未成稿、基本v20软件范围回归；人口v29通过。v45五v28高锰酸钾32768截断被阻断，不能当成功。主notebook实际显示最近已导出五v28/人口v29/基本v20，全7概念12图所有正文、0新调用/0错误/hash不变；下一次完成必须再次刷新。新南非国旗全链尚未启动，200未启动。

接下来：等当前重提取及review完整结果，检查所有正文/实际图片与源文支持、范围/冲突/覆盖/完整性，失败继续针对性修。主要回归收敛后启动预登记南非国旗完整双模型链；用新结果最终刷新notebook、验证和更新交接。state检查器已改递归统计joint_extraction/knowledge/calls，记录质量用record_quality.py（先写人工审查quality_review.json，不自动决定内容通过）。公共236/依赖106/清洗4此前已通过；本轮只有prompt/配置/state工具，未改业务代码，未提交推送。

---

# 最新：joint-v13 / final-v47 从已筛材料重新提取（2026-09-17）

管理器session35368，article_pipeline_upgrade/final_v47_manager.log；三目标五v30/人口v30/基本v21，以父v10/v10/v2的已筛材料为入口，重跑joint和final。拥有STOP final_v47，结束负责恢复。运行中不要改canonical、切服务或另启模型客户端。

v46已提前停止自己的五概念客户端：基本v20漏Visio/Lucidchart名称（未通过）；人口v29主要回归通过；五v29 OK动作归属问题消失，但白花xhigh耗尽65536且没有最终正文，随后高原/化学未评价。early_stop.json及质量记录完整，五v29无完整导出，不恢复此取消run；不能将未完成两概念判为语义失败。原管理器session7368 exit1是有意终止客户端导致，已恢复Qwen/worker45968/STOPfalse。

canonical现article-joint-v13/final-review-v47。联合提取逐段保留软件、模板、地点、物种及动作归属，final将对象范围规则放到最前及末尾，普通词自然中文。原文/像素证据边界不变，仍一次final_review。最终high（xhigh小输入仍无法完成）、65536输出额度、131072输入上限、完整待审稿、只选图号；联合提取仍low/16384/32768/4图软目标。因joint变更，这轮真正重新提取，未伪装复用旧稿。

主notebook已实际刷新：五v28 + 人口v29 + 基本v20，完整7概念12图及全部正文，0新调用/0错误/结果hash不变，质量状态明确。五v29未导出，未冒充最终结果显示；五v28高锰酸钾为程序failed。源码已high，新结果完成后仍需再次实际刷新。

公共236/依赖106/独立清洗4此前通过；本轮仅prompt/配置及state管理器支持材料复用，两册代码编译通过。台账114条。主要回归收敛后再跑预登记南非国旗完整Qwen+Gemma链，尚未启动，200未启动。未提交推送。下方历史。

---

# 最新：v46动作归属与输出余量复测（2026-09-17）

canonical article-joint-v12/final-review-v46，完整待审稿+xhigh+只选图号保留，final_max_output_tokens提升65536。管理器session7368，final_v46_manager.log，五v29/人口v29/基本v20，拥有STOP final_v46并负责恢复；运行中不要改canonical或启动其他模型客户端。

v45已全部结束且资源恢复。人口v28、基本v19主要问题通过，白花身份/无图、高原机制范围也改善；五v28未通过：OK将合影中成员的动作写到官员身上；化学输出32768耗尽被程序阻断，0知识0图，不能评价完整语义。v46按原句核对谁做了什么、重复事件提炼共同知识，不设实体黑名单；仅增加输出余量，不截断。台账111条，quality记录完整。公共236/依赖106/独立清洗4此前通过。

仍先使主要回归收敛，再跑预登记“南非国旗”全链，尚未开始，200未开始。主notebook实际保存输出仍v44五v27/人口v27/基本v18全7概念20图；必须最终实际刷新全部正文/图片及质量状态。未提交/推送。下方历史。

---

# 最新：v45完整待审稿 + Qwen xhigh 正在复测（2026-09-17）

canonical article-joint-v12/final-review-v45。两册final_draft_outline_only=False、final_review_effort=xhigh、final_image_selection_only=True；最终仍Qwen，Gemma仍独立筛图。run_final_candidate.py final_v45_plan.json 已启动（session58684），日志final_v45_manager.log，目标五v28/人口v28/基本v19，复用提取父v10/v10/v2。管理器验证配置后拥有STOP final_v45，结束自动恢复；不要改canonical、切服务、手删STOP或另启模型客户端。

为什么调整：v44主题清单对高原实际只有“高原”泛标题+14个资料号，无法保住有用机制；现恢复完整待审提取正文和全部已采用原文/像素，仍删旧纯视觉生成描述。v45还要求原文未给中文写法的专名直接保留原词，避免跨模型反复自信误译。一次final_review，不新增模型步骤。新候选尚未评价，200未启动。

Gemma第二次对照（关闭思考）三run全部未通过；第三次开启思考只完成五概念样本，仍因化学冲突/误译、高原机制遗漏、OK重复照片等不合格。已精确终止本次人口客户端，1请求0响应，标为stopped_unassessed；基本v3未启动。不是程序自然失败或新概念验收失败。原管理器已恢复Qwen/worker36805/STOPfalse，resource_after.json及early_stop.json完整。台账108条。Gemma未获采用，未实现正式Gemma预检。

最终解析新增对【资料1, 2】明确列表的程序展开；每个编号照常验证，不推断范围/缺失编号，冻结joint解析不变。44项针对性通过，公共236项已通过（tests_final_comma_public.log）。两册代码均可编译。Gemma只读/tokenize探针和已完成4次调用输入计数一致，但不追认正式preflight。

主knowledge_debug.ipynb实际保存输出仍是v44五v27/人口v27/基本v18全部7概念20图、所有正文，0新调用/0cell错误/hash不变，明确标明未通过。源码已v45，展示顶部仍准确说明旧v44输入；新结果收敛后必须再次实际刷新全部结果。两册旧源码备份在article_pipeline_upgrade/before_final_v45_notebooks。

下一步等v45全文、逐张实际图片、已知冲突/范围/有效覆盖检查及资源恢复；失败继续有依据地调整。主要问题收敛后跑预登记南非国旗完整双模型链，尚未启动也未读其资料；有新问题继续。未提交、未推送。下方历史。

---

# 最新：Gemma思考模式同输入对照正在运行（2026-09-17）

canonical仍joint-v12/final-v44，正式最终模型仍Qwen。Gemma关闭思考模式v2三run全部完成且恢复资源，6次调用未通过：白花科名选边、高原重复图片/机制遗漏、人口误删相关方法知识且无知识声明和配图区混用、基本模板用途泛化。全文/发布真图/映射及输入等价检查、台账106条已保存。

当前管理器article_pipeline_upgrade/run_gemma_article_comparison_v3.py（session5553），日志gemma_article_comparison_v3.log。仅开启Gemma自身enable_thinking，并用本地vLLM gemma4 reasoning parser分离思考与正文；同v44提示词、原文/像素/主题清单。目标article_gemma_review_sample5_v3、population_v3、basic_flowchart_v3，结束恢复Qwen与预标注。不要改canonical/切服务/删STOP/另启模型客户端。

article_trial现允许Gemma思考，移除Qwen专属reasoning_effort；本地服务parser选项仅对照显式启用，正式图片筛选默认不变。13项相关测试通过，公共234项测试已通过（tests_gemma_thinking_public.log）。Gemma仍仅诊断，尚无正式token预算预检；任何采纳必须补正式集成并实跑。主notebook已再次实际保存v44：五v27/人口v27/基本v18，全部7概念20图和所有正文，0新调用/0错误/hash不变，明确标明未通过。

旧问题收敛后才运行预登记南非国旗完整链；尚未开始，没有读取新例资料。200未跑、未提交推送。下方历史。

---

# 最新：v44未收敛，正在做Gemma最终review同输入对照（2026-09-17）

canonical保持article-joint-v12/final-review-v44，正式最终模型仍Qwen。v44三run已完成并恢复Qwen/预标注。五v27化学碱性产物矛盾、水温选边及硫酸铋钠误译回归，高原漏实例机制；人口v27漏设计权衡；基本v18主要回归通过。全文/实际图片/引用与容量检查、quality_review及台账103条已保存。

正在运行article_pipeline_upgrade/run_gemma_article_comparison_v2.py（session4007），日志gemma_article_comparison_v2.log。它合法借卡、运行相同v44主题清单/原文/图片输入的Gemma31最终review对照（sample5/population/basic_flowchart v2），最后恢复原Qwen及预标注。不要另启模型客户端、改canonical、切服务或手删STOP。对照预登记及服务事件位于state/curation/v4/article_gemma_review_comparison_v2/；仅诊断，Gemma尚无正式token预算预检，服务器拒绝溢出，不截断。

主knowledge_debug.ipynb刚已实际刷新为v43五v26/人口v26/基本v17全部7概念9图、所有正文、0新调用、0cell错误、结果hash不变。顶部明确未通过；不再是v32。源码是v44流程，最终还须更新为本轮实际结果。两册保留final_image_selection_only和final_draft_outline_only开关。

先核对Gemma全部结果是否改善，不预先换正式模型；若采纳必须补模型配置、正式token预算与原生编排的实跑。已有主要问题相对收敛后再启动预登记南非国旗完整双模型链（尚未启动/未读资料）。200未启动，未提交推送。下方均为历史记录。

---

# 最新：v44缩短最终prompt，仍在收敛（2026-09-17）

canonical article-joint-v12/final-review-v44。run_final_candidate管理器正在运行，日志final_v44_manager.log，目标五概念v27、人口v27、基本v18，拥有STOP final_v44，结束自动恢复。不要另开模型试验/改canonical/手删STOP。

v43全部完成并恢复。基本v17主要回归通过；人口v26搬回非目标黄热病/霍乱史，未通过；五v26的OK独立段漏引用（程序阻断）、高原实例范围泛化及机制遗漏、化学误译不清晰设备列表，仍未通过。化学碱性产物和水温冲突这次省略了，属改善而非整批通过。台账100条。

v44继续主题清单+完整已采用原文/真实像素的输入，用约1044字prompt按目标相关性、原文依据、跨来源冲突、有效覆盖四条组织；不再逐条比对草稿句子。最终仍仅选图号不生成图注，不加模型步骤。输入调整代码233项测试通过。两册源码已说明新输入，主notebook保存输出仍v32七概念16图，最终必须真实刷新。

下一步等v44全部结束、恢复后核对全文/原文/图片。主要回归相对收敛后再启动预登记南非国旗完整双模型链（尚未启动/未读资料），有新问题继续。200未启动，未提交推送。

---

# 最新：v43改为主题清单+原始证据的最终输入对照（2026-09-17）

当前canonical joint-v12/final-review-v43，run_final_candidate管理器运行中（final_v43_manager.log），目标五概念v26、人口v26、基本v17，拥有STOP final_v43并自动恢复。不要另启模型试验、改canonical或手删STOP。

v42全部完成并恢复；人口v25和基本v16通过主要回归；五v25未通过：化学碱性产物矛盾回归，水温冲突仍选边。台账97条，失败原样保存。不能称收敛，南非国旗/200均未启动。

v43输入调整已向用户说明：最终仅接收各组提取主题清单和关联资料号，不再传生成断言；完整提取稿保存在checkpoint。最后一步变成审查已采用材料并直接写知识，不能声称仍逐句修订旧稿。来源和像素池完整不变（outline_input_equivalence.json逐项对照，化学81原文/15图等）；不让任何排除/未引用材料复活。最终仍只选裸图号，不生成图注。两册final_draft_outline_only=True，article_trial按spec同步，PrepareFinalReview选项默认False支持旧诊断；joint解析不变。42项针对性通过，公共测试进行中（tests_final_outline_public.log）。

下一步等v43完成，核对全文及已知矛盾/身份/范围/覆盖/配图；相对收敛后再跑预登记南非国旗完整Qwen+Gemma链。最终刷新主notebook所有真实结果/图片（现输出仍v32七概念16图），更新DESIGN和台账/服务恢复证据。未提交推送。

---

# 最新：final-v42正在复测，尚未收敛（2026-09-17）

当前canonical **article-joint-v12 / final-review-v42**。管理器session66352（final_v42_after_format.log）已启动五概念v25、人口v25、基本流程图v16。它拥有STOP final_v42，结束自动恢复；不要另启试验、切服务、修改canonical或手删STOP。父提取仍v10/v10/v2，真实只重跑最后review。

v41格式回归三run已完成并恢复。人口v24、基本v15完整正文/原文/真实配图通过主要回归；五概念v24与v23化学正文相同，温水/凉开水冲突仍选一方，未通过。实验台账94条。v42明确比较所有给定原文和操作温度/浓度/时长，不只回查草稿已选尾注。收敛标准在convergence_criteria_v42.json，原始资料可靠性未独立认证，不能把忠实引用当作全部事实正确。

最终仍只选裸图号，不生成caption/视觉正文，程序原样附图；这是明确任务取舍。最终Markdown列表统一尾注解析已修，33项针对性+231项公共测试通过；demiflow106及独立清洗4此前通过。两册3/57个代码cell均编译通过，两仓本轮diff检查通过。joint冻结解释未改变。

**下一步**：等v42全部完成并资源恢复，逐项检查全部最终正文与实际图片（尤其温水/凉开水、白花科名、术语、范围、人口覆盖）；合格后用预登记“南非国旗”跑完整链（尚未开始，未读其资料/图片），如有问题继续修。主notebook保存输出仍v32七概念16图，最终必须运行refresh_main_notebook.py实际刷新全部最新结果/图片与明确质量状态。200未开始，未提交推送。

---

# 当前夜间调度：v41保留只选图号，恢复正文规则与知识覆盖（2026-09-17）

当前canonical article-joint-v12/final-review-v41。管理器session52287（run_final_candidate.py final_v41_plan.json），目标五概念v23、人口v23、基本流程图v14，复用父run v10/v10/v2提取。日志final_v41_manager.log，资源final_v41_resource.json。STOP所有权final_v41，结束自动恢复，不要中途改canonical、手删STOP或切服务。

v40三run完成并恢复（worker2600/Qwen健康/STOPfalse），严格只选图号模式有效：所有图片由程序原样恢复，未生成图注。但文字prompt简化过度，未通过：化学又出现“硫酸铋钠”错误译名；人口漏设计权衡、密度范围限制及dasymetric缓解方法；流程图适用场景漏Lucidchart名。88条台账/质量记录、引用/像素/预算检查均保存。

v41继续只选图片编号，**恢复v39完整的原文身份、译名及冲突规则**，并逐提取主题检查独立知识覆盖，只删错/无关/重复，不漏原理、条件、限制、机制与解决方法。每段软件功能/步骤/模板适用性明确软件名。配图须独立观看也不误认目标，不靠另写纠正说明保留近似对象或其他版本。仍是一次最终review，没有额外模型步骤。

v40起的明确取舍已向用户commentary说明：正文知识只来自原文，视觉信息保留在实际图片，不再生成图注或纯视觉文字；这不是声称视觉/OCR错误已经修好。ApplyArticle(final=True,image_selection_only=True)严格拒绝模型追加视觉文字或图像挂到正文；程序产生图N标签和空caption。两册MODEL_CONFIG final_image_selection_only=True，article_trial根据spec同步。joint解析不变，冻结提取仍逐行等价校验。

代码完整229项测试已通过（tests_v40_public.log），demiflow106及独立清洗4此前通过。主notebook源码已新流程但输出仍v32七概念/16图未通过；最终必须实际刷新全显示。刷新helper已添加空图注解释（仅全部run使用新模式时）。旧主要回归通过后再启动预登记南非国旗完整双模型链，仍未启动，不得称独立验证完成。200未启动，未提交推送。下方历史。

---

# 夜间优化正在继续（2026-09-17，先读本节）

用户明确授权持续优化：旧问题收敛后再加1—2个新概念，不收敛继续。当前没有验收通过，200未启动。canonical为joint-v12/final-v31，最终仍Qwen，Gemma独立筛图。

正在运行final_v31管理器（session2162，article_pipeline_upgrade/run_final_candidate.py + final_v31_plan.json）：五概念v13（复用v10提取）、人口v13（父v10）、基本流程图v4（父v2），只重跑最后review。它拥有final_v31预标注STOP，等待在途落盘后运行，结束自动恢复；不要手动删STOP或切模型。日志final_v31_manager.log，资源结果final_v31_resource.json。公共测试session45412，tests_final_parser.log。

v30三个run均完成，round4资源已恢复，resource_after_v30.json记录Qwen健康/worker20776/STOPfalse；内容全部未通过：OK仅引图仍写常以；化学识别碱性产物冲突却保留两说、5g规格漏掉；人口重复图+无依据段落被程序拦截；基本流程图仍误读遮挡循环及串联D/E为两分支，另一图矩形误称平行四边形。基本v2还曾泛化软件模板能力并被删除资料标记破坏句子，v3改善软件范围。基本流程图现在已参与调参，不再是独立例。quality_review与实验台账58条均保留失败。

v31将反复追加的最终prompt整理为七条规则约1709字，保留已知身份/范围/术语/冲突/图文/条件/规格要求，补软件范围和遮挡路径；输出仅主题、逐段引用，程序按引用附图，免重复配图行。新候选尚在实测，不宣称改善。ops/article.py新增仅最终解析的引用句子处理，资料编号作主语/介词宾语时保留“所引资料”，普通尾注仍去掉；最终###子标题可正常作章节。joint解析保持不变，须通过冻结提取等价检查。针对性25项已通过，公共测试进行中。

下一步：读三个v31全结果与真图并验收；不合格继续迭代。旧回归通过后选未看过的bench200概念做完整双模型链，提前冻结标准（候选可选南非国旗/注意塌方标志，尚未启动、未读其结果）。最后主notebook要实际执行保存所有最终图文和明确状态；当前仍只显示v10六概念23图，未通过标签。所有原数据/旧输出只读，不提交推送。下方旧“进行中”均为历史记录。

---

# 最新运行状态（2026-09-17，自动调度在运行）

**不要另启模型试验或手动切服务。** 已排好的两步调度都还未结束：

1. `run_basic_flowchart_after_regressions.py`，session91705：v11回归已结束；round3预标注已按所有权恢复。正在跑`article_holdout_basic_flowchart_v2`完整链，复用v1 identity，当前候选joint-v12/final-v29。Qwen筛图10次及正文2次已完成，目前借卡管理器等预标注在途请求落盘，然后切Gemma。STOP现在归本次借卡管理器持有，不是round3，不可手删。日志holdout_basic_flowchart_v2.log。
2. `run_final_v30_after_holdout.py`，session77004：正在等待上述完整run结束并资源恢复。**候选final-review-v30已在看基本流程图最终结果前冻结**，哈希及标准见article_pipeline_upgrade/final_v30_predeclared.json。管理器之后自动提升v30、创建自己的round4预标注暂停，只重跑最后review三组：五概念v12（父v10）、人口v12（父v10）、基本流程图v3（父v2）；最后自动恢复预标注，写resource_after_v30.json。不要修改state中的v30候选，否则哈希检查拒跑。

v11五概念/人口均完成，程序映射/容量通过但内容未通过：高原图注成因已修；人口的数量图标题和2023年无误，但没有数值色阶图例仍当颜色表示数量；OK仅引图段落仍写“常以”；高锰酸钾在未细分的同样“碱性”条件下仍给出MnO2/K2MnO4两种结论。v29处理图像概括/颜色编码；v30补逐条有引用但彼此矛盾的具体核对例。包装抄用法标签是较低优先级观察，不单独作为流程阻断理由。所有输出原样保存，未手改。

主notebook已执行并保存两个v10实际结果全部6概念/23图/全文，顶部明确未通过人工检查；未展示v11/新例。最终要换成实际最新三run，重新执行验证所有正文/图无展示上限。程序reviewed不等于人工通过。实验台账54条。

当前代码两册joint_image_target=4（软目标，原生紧邻可超但token不超），无最终图数上限。公共测试220项、清洗4、demiflow103通过；两仓diff --check和两册顶层await编译通过。Gemma最终review对照未采用，最终仍Qwen，Gemma独立筛图。200未启动，未提交推送。

下一步：跟踪上述两管理器；v2结束后核对服务恢复。等待v30三个run完成，逐项读全最终正文/真实图片、引用与容量检查，保留失败；然后更新quality_review、台账、DESIGN85、真实notebook展示和资源恢复证据。基本流程图v30模板提前冻结且未根据其结果调参，可以作为未参与调参的新例检查；人口属于回归例。

---

# 最新资源与对照调度（2026-09-17，务必先读）

正式候选仍joint-v11/review-v26，最终review仍Qwen。人口分布图v9已完成2调用：采用3张简单、变量明确的图，排除了不明变量和多年份复杂图；但nomograph被误译“等值线图”，尚未通过。五概念v9（session41659）七组提取全部合法，正在4次最终review。高原这次恢复19份原文中的隆升/季风等机制（v8仅9份）；高锰酸钾最终输入63份原文5图，OK23/7、白花3/0、高原19/3，全部输入预算通过。

**已经启动自动等待的Gemma最终review对照管理器：session10203**，脚本article_pipeline_upgrade/run_gemma_article_comparison.py，日志gemma_article_comparison_v1.log。它会等待五概念v9完整结束、所有run_notebook_pipeline/article_trial进程退出，然后自动恢复round2预标注STOP所有权，接着合法借卡启动Gemma31（131072 context、图片上限取实际最大），对同一批冻结提取输入执行一次final_review对照：article_gemma_review_population_v1、article_gemma_review_sample5_v1。当前还在等Qwen，**不要另开Qwen试验、不要手动切服务或删除STOP**。管理器最后恢复原Qwen和预标注，记录resource_after。

新增article_trial --review-model gemma-4-31b-it只允许reuse-extraction/export，无Qwen思考参数，端点本地8001；实验容量由服务器拒绝超限，不请求截断，尚未实现Gemma正式token预算验证。local_review_service新增可选local_review_context_tokens，默认仍32768，正式图片筛选不变。这个对照尚未产生结果，不宣称Gemma更好或已切正式review模型。预定标准在article_gemma_review_comparison_v1/predeclared_review_criteria.json（借卡前写入）。

下一步：先读五概念v9最终全文/配图，保留失败；再读同输入Gemma结果，比较身份/图文/原文忠实性/完整性与成本。若采用Gemma，须补正式模型选择、实际token预算和notebook编排后的实跑验证，不能把仅诊断接口当完成。若不采用，说明证据再继续有限修复。基本流程图仍未完整跑，人口已参与调参；最终主notebook仍需更新执行保存，不启动200。其余状态见下。

---

# 最新进行中：v11/v26完整回归，仍未收尾（2026-09-17）

当前正式候选 **article-joint-v11 / final-review-v26**，不是已验收版本。正在跑：
- `bench200_sample5_article_v9`，session41659，复用五概念v8入选材料，重跑提取+review；日志article_pipeline_upgrade/sample5_article_v9.log。
- `article_holdout_population_v9`，session8392，复用人口v8入选材料，重跑提取+review；日志article_pipeline_upgrade/holdout_population_v9.log。

五概念v8完成11调用，7组提取全部合法，4次review。程序映射/容量验证通过；内容仍未通过：高锰酸钾二维图又被描述为四面体构型，静态紫色液体被说成溶解产物，抄入批准文号；高原少提取了部分能解释形成/气候机制的相关背景。白花芍药无不明图和冲突科名。最终4+0+0+3+7=14图，尚未作为通过结果展示。

人口v8完成2调用，范围区分和原文不确定措辞改善，但错误保留无变量标题世界紫图、漏清晰年份。仅重跑final_review：v23删紫图但漏/混年份；v24精简prompt补年份但又放行紫图，在白花/高原也重现科名冲突、补Baulig日本国籍、由图推断侵蚀。v25无总括定义、删紫图、深圳2024年恢复，但俄语世界图城市层错标2025（实际2023）。**这些版本全部未通过，不得冒称优化完成。** 台账已46条，quality_review保存失败。

v11/v26新的取舍：相关背景不是全部排除，保留确实解释目标机制的实例；配图按实际说明内容去重，换地区不自动算新知识。只介绍画法时选变量/时期关系简单清晰的一图，多图层/多变量/多时期只有正文需解释独立信息才采用。图注不能借正文给二维图补三维构型或给静态画面补过程。保持普通文章输出，不增加结构化字段。

**资源仍由本任务round2暂停预标注，STOP存在**，所有权在article_pipeline_upgrade/qwen_experiment_pause_round2.json；Qwen8000健康未停，不能在v9两客户端结束前切Gemma。其他v8、v23、v24、v25运行均完成。完成所有Qwen实验后，验证无worker/supervisor/旧launcher，删除本任务拥有的STOP并用local_review_service.spawn恢复run_image_pipeline.sh，再允许基本流程图借Gemma。

下一步：读取v9全部最终正文/图片并对已知问题验收。若仍失败，要诚实保留，不只凭格式通过；可考虑不同review模型作有界对照（**尚未实现/运行，当前review仍Qwen**），避免无止境堆规则。`基本流程图`v1仅gather/identity完成，v2只有预定标准，未看过全链结果；它仍是独立新例，需完整跑双模型链并审查。人口已调参，不再是独立例。

主notebook仍实际展示五概念v6的17图（RUN指向已存在v7，收尾须改为未来空目录）。最终必须用实际通过结果刷新主册并执行保存全部最终图文，含新例，无展示上限；不能只改配置或保留旧输出当新结果。218项公共测试+独立清洗4+demiflow103通过。原数据/旧run只读、200未启动、未提交推送。此前各“下一步”“待实施”仅历史状态。

---

# 最新进行中：范围传递修复已落地，正式入口复测 v8（2026-09-17）

当前候选 article-joint-v10 / final-review-v22；完整链为 identity → select_blocks / select_images(Qwen→Gemma) → joint_paragraphs → final_review → 程序校验发布。**本轮尚未收尾，200未启动。**

- v9/v21提升后重跑的五概念v7未通过：高锰酸钾一组HTTP stop但漏【完成】，程序正确阻止整概念发布；7提取+3review。人口分布图v7也未通过：把人口分布图等同点分布图，最终0图。相同HTTP参数/输入的定向试验与正式运行输出不同，不能宣称温度0保证稳定。
- 找到范围丢失：identity正确判 Dot distribution map 为 related_context，但联合提取只收到目标名字。现在PrepareRoutingMaterials从原始概念记录取别名，从已筛正文的text/related_context取原文标题；只传简短身份边界，不传理由/quote/caption，不作事实证据。显式物种定义优先于宽泛别名。跨组最终review合并范围，并只保留实际引用原文对应的背景标题。token预算包含此输入；旧routing元数据不复用，已筛材料及其embedding可复用。
- 范围诊断：只加原始英文别名无效；明确手写定义能改善但非通用方案；自动背景边界试验article_population_background_scope改正概念等同并保留3图，仍有原文不确定措辞丢失，因此v10/v22追加忠实保留appears/seems/may及最后结束标记提醒。普通文章与简短编号保持不变。
- 正在运行：bench200_sample5_article_v8（session74359，parent五概念v2材料）；article_holdout_population_v8（session18240，parent人口v6材料）；均走正式notebook函数，重新提取+review。日志在article_pipeline_upgrade。不要在这些Qwen请求结束前切Gemma。
- 37项范围/路由/文章重点测试通过；全公共测试已218项通过（36.72秒）。独立清洗4+demiflow103此前通过，之后依赖与清洗未改。
- **预标注仍由本任务round2暂停，STOP存在**，所有权article_pipeline_upgrade/qwen_experiment_pause_round2.json；Qwen8000未停。结束所有Qwen实验后按local_review_service恢复STOP/launcher/worker，再跑基本流程图独立验证，让管理器借Gemma，最后确认原资源恢复。
- 基本流程图v1仅gather/identity完成；v2只有预定标准，无完整结果。人口分布图已调参，是回归例，不是独立新例。主notebook当前仍实际展示五概念v6全部17图；最后必须更新为实际通过的最新五概念+人口+基本流程图结果，并重新执行保存全部最终图文，不能只改RUN。

下一步：检查v8完整正文/实际图片、已知回归、严格引用与token预算；有失败保留记录再修。通过后恢复round2预标注，跑基本流程图v2完整双模型链并独立验收。最终更新实验台账、DESIGN85、真实notebook输出、资源恢复；不冒称全量或独立事实认证。

最新补充：人口v8已完成2调用并通过程序映射/容量检查，但内容仍未通过：无变量标题I48039eed32c2世界紫图被当人口图，清晰年份被省略。article_review_v23_population（session78034）从v8提取只重跑最终review，候选YAML在article_pipeline_upgrade/final_review_v23.yaml，尚未提升正式模板（仍v10/v22），以免当前五概念v8途中预算模板改变。五概念v8 session74359仍运行。诊断和v7失败已加入台账，共40条。

---

# 最新补充：人口分布图继续迭代，等待v21/v9验证（2026-09-17）

`article_holdout_population_v6`已完成23次调用，但未通过：模糊图例被补成确定区间，独立霍乱制图史进入人口分布图。**人工判断纠正：** 放大原图图例确认I050e884185b0写的是“人口数量”，不是“人口密度”；此前关于密度/数量混淆的结论是人工误读，不能当作模型缺陷。可确认图上6个颜色档；v20复测写成7档是明确错误。报告已修正，保留纠正记录。

`article_review_v20_population`已完成：删除独立霍乱史，但补猜图例区间，未通过。`article_joint_v8_population`已完成：仍有独立方法史，并把填纹理分区图配到点值原理，未通过。

当前定向试验：`article_review_v21_population`（session62586，旧提取稿上只做最终review，仍运行）；`article_joint_v9_population`（session85549，已完成1次提取，格式通过，相关方法范围和图文形式显著改善）。新规则：图只用于表现方式时，不转录图例统计数字；保留明确变量/图形/时期；产品关键规格仍保留；填色/填纹理不冒充每点代表若干人的点图。方法史只留直接以目标为对象的实例。正式模板仍v7/v19，候选v9/v21尚未提升。

未参与调参的“基本流程图”`article_holdout_basic_flowchart_v1`已完成gather/identity，身份resolved；未来完整run v2已有预设验收标准。人口分布图已参与调参，后续不能再称独立新例。Qwen8000及预标注已恢复，STOP不存在；没有Gemma占卡，不能在当前Qwen试验未结束时切模型。

下一步：v21定向结果通过后，提升候选v9/v21，五概念新run `bench200_sample5_article_v7`从v2已筛材料重跑提取+review；人口分布图新run v7从v6材料重跑提取+review。验证无回退后，用基本流程图v1 identity checkpoint跑v2到export并检查全部最终图文。最后主notebook同时展示通过的新run，更新台账/文档和资源证据。当前主册仍展示五概念v6全部17图。

---

# 最新进行中：文章 pipeline 已实施，五概念回归通过，正在验证新例（2026-09-17）

用户本轮要求：列问题/改法、直接调整pipeline、用checkpoint循环调策略及prompt。实现与问题表见DESIGN第85节。实际模型链为`identity → select_blocks / select_images(Qwen → Gemma) → joint_paragraphs → final_review`，之后程序校验并保存。旧verify/repair/relationship/merge模型链不执行；模型只写普通文章和简单资料/图号，网址、图片地址、内部结构由程序处理。下方“待实施”均为历史状态。

当前正式候选 **article-joint-v7 / final-review-v19**，较短思考；提取输出16384/900秒、最终review输出32768/1200秒，输入预算32768/131072。`bench200_sample5_article_v6`已完成：从v2完整7组提取checkpoint只重跑4次review，563.24秒、106335 token。本轮已追踪的身份/配图/原文忠实性/完整性问题通过回归。最终：OK手势16主题5图，瓶式台球0/0（明确材料不足），白花芍药5/0，高原4/1，高锰酸钾18/11。

- 白花芍药无不明卡片/白芍切片，省略原文冲突科名；该物种块根药用有原文，不应机械删除。
- 高原仅一张地形图，无村落/标牌/农田/牦牛错配和近重复图；保留有明确实例范围的形成与气候机制。Baulig无新增国籍，Paleocene–Eocene译名修正。
- 高锰酸钾将错误“硫酸铋钠”改为原文`sodium bismuthate (NaBiO3)`；二维结构、图形符号、静态过程和标准编号误读修正，包装形式与关键规格保留。
- v1—v5正式文章结果均曾有明确不足，未冒充验收。中等思考未解决高原错配，v13/v14压缩丢知识，未采用。实验台账现30条，详细失败与结果在`article_pipeline_upgrade/experiments.json`。
- 本轮不是独立事实认证或全球最优证明；部分正文仍夹杂非必要英语词，旧来源时效性未全面核实。

主`curation/v4/knowledge_debug.ipynb`已在干净demiwtg内核执行并保存v6全部5概念17图：0cell错误、0新增模型调用、全部正文可见、knowledge哈希不变。SAVED_RUN=v6，RUN为未来v7。玻璃棒册也使用同一新流程。新例完成后还需把它一起加入主notebook实际输出。

**正在运行的新例：** `article_holdout_population_v6`，session 42289，日志`article_pipeline_upgrade/holdout_population_v6.log`。复用仅到identity的`article_holdout_population_v5`，重新跑筛选→Qwen/Gemma→提取→review→export；没有重新扫原始清单或重复identity。已提前写`predeclared_review_criteria.json`，按人口地理分布范围、年份、图例、单位及图文一致性验收，不应搬入完整点图历史。此前v1—v5只到gather/identity，不算完成的新例验证。此完整run尚未验收，不可宣称已经全链通过。

**资源：** 为五概念实验创建的预标注STOP已按所有权记录恢复，Qwen健康、预标注worker重新启动，见`article_pipeline_upgrade/qwen_experiment_pause.json`。新例管理器随后合法借卡，当前切Gemma；待其结束确认`original_resources_restored`、Qwen8000健康、预标注worker存在、STOP消失。不要手动启动第二个Qwen或删除借卡管理器持有的STOP。

断点支持filter_inputs（可只到identity）、额外复用text_selection、复用materials、仅复用extraction。全部检查对应prompt/配置/范围/哈希。文章算子变更后复用提取，须重新构造输入及解析旧响应并逐行确认完全等价；实际v2全部7组等价。未知引用/输出截断/失败组/超容量明确阻止发布；纯图片说明可作为正文，不要求重复输出；图号及来源由程序准确恢复。

测试：curation/v4公共环境215项、清洗独立环境4项、demiflow103项；随后重点38项通过。覆盖257文档边界、零新调用恢复、身份checkpoint不重扫、引用越界/失败阻止发布等。v6另有`article_validation.json`及`quality_review.json`。bench200仍有92概念原始关联超过256，尚未真实全量；原始数据与旧run只读，未启动200，未提交或推送。

收尾：等待新例完整输出，阅读全部正文和真实最终图片，按预设标准判定；必要时记录失败再调整。验证程序可用`article_pipeline_upgrade/validate_article_run.py`；展示脚本`refresh_main_notebook.py bench200_sample5_article_v6 article_holdout_population_v6`（只在两个run完成后）。更新台账、DESIGN第85节、本顶部及资源恢复证据。

---

# 最新完成：bench200 五概念正式双模型端到端试跑（2026-09-17）

**待写prompt／已确认决策8（2026-09-17）：** 用户采纳按“是否实质帮助理解目标概念”收紧材料相关性。select_blocks逐段筛选，不以提及名称或属于同类就保留；joint_paragraphs围绕目标提炼，实例说明作用明确、保留对象/地域/时期/条件，不能由实例泛化整个类别；最后一次review检查各主题和实例必要性、标题范围及重复，避免检索材料多的实例占据文章。青藏高原可作为高原实例，不一律排除，也不整篇搬入。配图按实际说明作用与具体对象一致性判断。完整待写prompt要求与回归例见DESIGN第84节；与前述简化方案一起实施，尚未修改prompt或重跑。

**待实施／已确认决策7（2026-09-17）：** 来源标题、网址及图片地址由程序按简单资料/图号附回，不让模型复制。模型仅声明实际引用/选用关系，程序检查编号并准确恢复来源，不自动列全部输入作有效引用、不猜地址。详见DESIGN第83节；尚未实施。

**待实施／已确认决策6（2026-09-17）：** 模型和最终正文使用图1/图2等可读图号，不写内部图片长ID；程序保存映射，跨组编号、删图/去重/重排后同步正文指图并检查悬空引用。回归为OK手势视觉媒介主题。详见DESIGN第82节，与输入/文章输出简化一起改，不新增模型调用；尚未实施。

**待写prompt／已确认决策5（2026-09-17）：** 用户强调配图一致性、不能配错。正文讲具体地点/物种/对象时，配图必须有依据对应，不能用同类图片代替；缺证不配图，有依据正文可保留。最后review核对正文、真实像素及来源，不只检查caption。回归为海镜村正文配无地点对应依据的Ic8d0763aa2e9。详见DESIGN第81节，与第77节合并落实，不新增模型阶段；尚未实施。

**待写prompt／已确认决策4（2026-09-17）：** 最后一次review统一处理同概念重复事实和近重复图片：共同内容只写一次，保留不同条件/例子/来源；直接看真实图片，不按caption或文件ID差异认定信息不同，不设固定图片张数。回归为高原两个“定义与分类”主题及地貌主题第1/4张近重复图。见DESIGN第80节；不新增模型阶段，尚未实施。

**待实施／已确认决策3（2026-09-17）：** 精简joint_paragraphs输入，让模型集中提取有依据的图文知识：概念身份范围＋编号原文（必要上下文）＋编号真实图片；去掉image_selection内上游模型描述、筛选理由、keep及双模型审核记录，完整记录留给程序。用户明确同意：正文资料支持它明确讲述的知识，图片支持实际可见的内容，模型生成caption不充当证据。来源原有图注按带出处文字资料处理，不能与生成caption混淆；不把图片中的文字或单图情况直接扩写成事实或普遍规律。第77节身份要求及第78节最后一次review方案继续有效。详见DESIGN第79节，仍待统一修改。

**待实施／已确认决策2（2026-09-17）：** 保留joint_paragraphs，后面改为一次review并直接写最终图文，再由程序检查保存；替代当前verify_paragraphs、repair_topics、review_relationships、merge_paragraphs、verify_merged_paragraphs等后续多次处理。输入各组提取结果＋实际引用原文（必要上下文）/真实图片＋明确身份范围，不重新输入上游排除材料或全部原始材料。输出普通文章与简单来源/插图标记，程序生成内部结构并恢复图注及limitations。需保证容量不截断、失败不丢记录、不把未审核提取稿当最终结果；引用和遗漏仍须检查，一次自审不等于独立核验。完整范围、失败处理与对照验证见DESIGN第78节。用户要求先记下，讨论完再改；尚未实施。商品宣传一项目前不优先修改。

**待改prompt／已确认决策1（2026-09-17）：** 用户明确要求保证正确性：介绍卡片内照片身份未确认，不能作为辅助配图保留。标题/介绍文字不能替代照片身份核验；泛称不能替代明确物种范围；limitations及双模型一致不能作为放行理由。未确认者暂缓最终配图，保留原材料与不确定性记录。回归图为白花芍药Ic24cab2b3f23。先记录，待逐项讨论完再统一改prompt；本轮未实施、未重跑、未改旧结果。详见DESIGN第77节。

**最新展示纠正：** 用户要在主notebook直接看pipeline最终结果。`curation/v4/knowledge_debug.ipynb`的view_saved通过显式SAVED_RUN绑定已完成的bench200_sample5_dual_v1，并保存执行后的最终图文；RUN仍留作未来v2执行目录。默认展示全部最终knowledge字段（含limitations和完整引用），材料/audit通过audit=True可选查看，避免77MB过程记录遮住最终结果。此前仅更新state审查册、主册仍指向无结果v2导致用户看不到，现已修正。

## 最新展示约定（2026-09-17，用户要求）

用户要求notebook不再限制展示，先完整查看pipeline结果再讨论缺陷。`current_results.py`默认`limit=None`，展示最终图文、全部图片字段（含limitations、region）、完整引用及其他主题字段；概念身份、材料、audit等结果行剩余字段原样完整展开，明确标为非最终正文。无字段截断或参考来源去重，不改pipeline判断。两本正式notebook已接入；只刷新展示模块，不热加载业务算子。

`bench200_sample5_dual_v1/knowledge_debug_review.ipynb`已在干净内核view_saved重新执行并保存，5概念、47主题、27图，全部图片/引用/附带字段与knowledge_base逐项核对，0cell错误、0新增模型调用、结果文件哈希不变。全字段版本约77MB，原展示备份在run/display_before_full_fields；验证在display_full_fields_validation.json。此前“不展示审核状态”等阅读简化约定由本次明确要求覆盖。白花芍药最终JSON本来保留limitations，旧展示隐藏了它；不能把展示遗漏说成pipeline没有记录限定。

用户要求先从bench200抽样端到端试跑，再考虑200概念正式批次。本轮已完成 `state/curation/v4/bench200_sample5_dual_v1`，入口为启动时冻结的 `knowledge_debug.ipynb:run_pipeline`（AST与7b734fc一致），CLI `--through export --source-scope collected`；未复用旧单模型材料。业务Python/YAML与冻结快照一致。

**看真实结果：** `state/curation/v4/bench200_sample5_dual_v1/knowledge_debug_review.ipynb` 已在干净demiwtg内核以view_saved执行，保存全部最终图文，零新增调用、零cell错误。本册是运行时冻结快照，不是第二套业务入口。主notebook在另一窗口加入旧内核检查并将默认RUN设为未来`bench200_sample5_dual_v2`；保留该配置，未将v1结果冒称v2结果。

- 权威范围：`benchmark/t2i/bench200/questions.jsonl`，200题对应200个不同概念，全部精确匹配现有概念名。`bench200_selection.json`冻结原清单哈希、全部200名与抽样过程；种子20260917随机3例为瓶式台球／高原／高锰酸钾，另加OK手势／白花芍药作定向回归。
- 原始关联71文档、588图片记录，269张字节可用；瓶式台球身份歧义后无入选材料。其余219张实际Qwen初筛55批、Gemma独立复核49批；92 keep／89 exclude／38 pending，其中16张Qwen keep降为pending。49批完整消息（含像素）与对应Qwen逐字节一致。
- 9组联合提炼（OK2、白花1、高原3、高锰酸钾3），所有入选正文/图片均覆盖且在32768输入预算内；7批关系判断、6次局部整合、17次按需主题修复。合计211次本地调用（Qwen162、Gemma49）、1,961,454 token、90.45分钟，211响应均HTTP200/stop，无缺失响应。

|概念|最终主题|正文字符|图片|
|---|---:|---:|---:|
|OK手势|9|2020|7|
|瓶式台球|0|0|0|
|白花芍药|3|309|1|
|高原|15|2375|13|
|高锰酸钾|20|4079|6|

**工程跑通，质量不适合直接正式扩到200：**详见run/quality_review.json。瓶式台球材料缺口被保留；白花芍药初筛和草稿中的泛称物候/药用说法，后续核验已暂缓，最终无药材图，仅有Ic24cab2b3f23介绍卡片，但照片具体物种仍未认证。高原定义/气候主题重复，照片近重复，图片海拔标注被扩写为无地域限定规律；高锰酸钾把商品文案当知识，且原已保留的包装规格主题在局部整合漏type后消失，未回退原文。部分图片参考来源为空或编码串；OK手势正文泄露内部图片ID。已阅读全部47主题8783字符及27张最终图联系表，另看重点原图与中间图，不是专家逐项事实认证。

用户追问为何白花芍药出现以前没有的药材图：`input_comparison.json`已定位。旧v5父材料27图可用→5图进入提炼→最终2图；本轮63图可用→23图进入提炼→最终1图。I9acc796980ca旧记录byte_status=not_local，本轮verified_bytes；新增可用图36张。因此旧最终结果与新草稿不构成同输入对照。**药材图仅在本轮草稿出现，最终被挡住**，不要拿中间缺陷冒称最终发布错误。

新资源借用管理器完成首次实服务闭环：排空预标注→Gemma31复核→原命令恢复Qwen8000和预标注。`image_review_service/events.jsonl`有`original_resources_restored`，收尾`resource_after.json`再次验证模型正确、worker存在、STOP不存在。

全量盘点`bench200_inventory.json`：8309文档关联、47398图片关联，不能称可用证据数。92概念总材料数超过入口GROUP_SIZE=256，最多858；此5例未覆盖该入口多材料批边界。200全量未启动，不改题目/评分。

用户问是否逐批Qwen→Gemma流式直通：当前为**阶段内部流式、阶段之间完整checkpoint屏障**，全部Qwen初筛后才切Gemma，再恢复Qwen提炼；两模型当前均TP2共用同一双卡。没有改为同时驻留或逐批切模型。

下一步优先修身份/图片文字与广告主张到事实的范围控制、整合格式失败时的有效原文回退、重复与来源展示；随后用未参与调试的新例及超过256材料的概念验证，再分批扩大。不得覆盖本run或为了消除错误重放全部成功请求。运行证据还包括validation.json、routing_validation.json、result_summary.json、timing.json、review/图表。

---

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
