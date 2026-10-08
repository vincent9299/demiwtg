# Edit V2：概念核心编辑题

> **项目强制规范**：本 pipeline 的开发、修改、运行配置与评审必须先阅读并遵守 [《项目 Pipeline 强制规范》](../../../PIPELINE_SPEC.md)。本 README 仅补充本流程的具体约定，不替代或放宽项目规范。

正式入口为 `edit_v2_benchmark_pipeline.config(...) → run_pipeline(config)`。Notebook 和 CLI 共用同一入口。Codex 出题只读一份完整的 [agent_codex.yaml](prompts/agent_codex.yaml)：`demiflow_agent_v2` 内联模型、runtime、算子、固定入参、预算、任务和最终 schema；当前任务版本 `edit-v2-core-knowledge-13`。出题只保留这份 agent 配置，旧 `tasks.yaml` 与普通离线出题分支已删除。`review.yaml` 是后续单次评审的唯一完整 prompt pack。

## 10 概念端到端试跑（2026-10-04）

用户释放 GPU 后，本轮 `edit_e2e10_v1_20261004` 于北京时间 09:50 从正式 CLI 启动，10:14 结束：Codex 出题与按需构图 → 本地 Qwen-Image-2.1 编辑 → Malasci GPT-6-astra/xhigh 联合评审。出题并发 2，后续各 1，新增请求额度分别为 10。WeMM 使用 GPU 0/8002，Qwen 使用 GPU 1/8005，均由 demiflow 服务管理器启动，并在结束后停止；未接管其他服务。

输入采用已冻结 cohort@4 的 selection_rank 前 10 项；50 张正例、38 份文档及完整上下文均通过预检，实际文字编码与固定场景表检索已通过。旧独立图文来源对之前抽取的 10 个名字没有匹配材料，因此本轮显式改用审定名单，不改写旧来源。新增固定名单接入及既有出题/作答/评审接线的 4 项隔离回归通过。参数、服务声明、预检、进程回执和运行日志保存在工作区 `_demiflow/benchmark_edit_v2/edit_e2e10_v1_20261004/`；notebook 已指向本轮，执行开关保持 False 以免重复提交。

最终为 **6/10 完成出题、作答与评审，4/10 出题侧技术失败**，摘要 `summary__edit_e2e10_v1_20261004.lance@3` 如实保留 `status=incomplete / complete=False`。6 份作答均成功生成并评审：贴头辫 2 分、那达慕大会 2 分、泰坦尼克号遗址 5 分、六股辫 9 分、老虎栖息地 2 分、特洛伊战争传说 9 分；通过 2、部分通过 1、失败 3。仅可评分的 6 题均分 4.83，不能视为 10 题总体分数或稳定能力估计。

“摩天大楼上的午餐”“斗兽棋棋盘”“爱（罗伯特·印第安纳）”在原生图像工具读取种子图时遇到 `bwrap: Failed to make / slave: Permission denied`，没有交付合成原图；“奥西里斯”因 Codex 单条事件超过 4 MiB 限额失败。独立挂载隔离诊断也失败；没有关闭沙箱或修改宿主安全设置来绕过问题。超限事件正文未被记录，不能据此声称收到了生成图片。上述 4 项没有进入作答或计为零分，也没有自动重新提交。

出题 journal 共 10 个请求（9 个返回，1 个记录错误），评审 6 个请求均返回；6 个候选、作答、评审的 task_id 完全对应，15 个实际种子／作答图片对象通过 SHA 与完整解码检查，两份 prompt 的 hash 与启动回执一致。本批尚未验证成功的原图合成闭环；场景编码检索仅有独立真实调用预检，不能用它冒充作者实际使用了检索。完整逐题结果、固定版本、调用计数和图片核验在控制目录 `results.json`，环境诊断在 `environment_diagnostics.json`；`preview.ipynb` 是从正式调试 notebook 只读执行得到的题面、图片与评审预览，不会再次调用模型。

## 提示词与材料

Prompt 按任务、三类输入、质量标准、考点与原图联合设计、正式题面与判据、工具执行、原图验收、交付核对、输出契约和本轮输入组织。判断模块按“核心判断＋补充边界”展开，保留标准的完整解释与例子；输入章节直接沿用 T2I 的输入与作答条件正文，正例使用边界保留在该章节；作答条件与例子按 Edit 的原图＋指令调整。工具参数集中在 API 章节。添加、删除、修改、组合只是设计操作的方式，不按操作数量判断质量。固定规则在前，实际 API 参数、当轮材料及图片统一在后。`api_configuration` 占位符从本次已解析的算子配置直接填入实际表 URI/version、字段、编码模型、固定参数和动态参数 schema；不手抄第二份配置。LLM 只提供本轮变化的场景描述及上一步返回引用，无需猜表或编写 Dataset。

模型的材料输入统一为 `concept_material`（概念资料）、`evidence_materials`（依据材料）和单列的 `positive_examples`（正例图），不再发送 `references`。输入说明对齐 T2I `t2i-v2-independent-materials-16`：概念说明确定对象和必要范围，说明中的具体特征、实例或任务建议不限定考点；依据材料仅覆盖此前涉及的部分知识，可结合可靠知识、补读和联网检索独立出题。概念资料提供 concept 与完整 taxonomy；使用固定 cohort 时同时提供审定的规范名称、definition 和原名，独立材料入口未提供概念说明时不自动编造。独立文章正文按 T2I 使用 A 编号；固定文档在模型输入中只提供材料编号、来源 URL、本地绝对路径 local_path 和 SHA256，Codex 按需自行读取。原文不进入初始上下文，不发送 document_ref、bindings 或 eligible 等旧算子入参。正例图来自已 published 且 keep 的概念图片关系，按 image_number 绑定实际图片。不单列已核实事实或未核实事项清单。

初始图片只有概念正例，场景候选在提出检索需求后取得；已删除预置 source_pool/source_candidates 的配置、CLI 和 notebook 接线。`execution_context` 只保留执行开关及构造次数预算。依据材料编号不参与图片选择；初始正例按 image_number 选择，检索图按实际 attached 回执绑定。冻结表的内部 `references_json` 保留原有字节引用和来源记录，出题前拆分为上述模型输入，不复制进提示词。本轮试作答只接原图与 instruction。

每个概念使用独立 Codex 上下文，结合实际图片确定核心考点、题目、可选原图构造指令和验收条件。支持按需检索，实际使用的知识和来源写入 `test_points.basis`；`point` 说明考察内容，新增 `criterion` 单独说明前后变化、可见证据与允许变化。469 个 T2I 通过概念是候选概念，具体编辑题是否成立仍需重新判断。

出题与原图构造的共同原则是考察模型对概念核心内容的识别、理解和知识运用。第 3.2 节直接沿用 T2I 六方面的完整说明：特征与结构、属性与状态、功能与机制、过程与变化、关系与组织、规则与约定。它们用于选择和组织有价值的考点，一题可聚焦一个，也可自然结合多个，不要求逐项覆盖。先确定核心内容、待考能力和可见的正确／错误区别，再联合设计原图、正式指令与判据，最后选择操作方式；不能从任意图片改动反推知识考点。

正式任务按增、删、改（各种调整）及必要组合设计。增题考新增对象、部件或关系的核心正确性，既可考形态身份，也可考补全结构或功能关系；添加完整对象时优先按所需场景检索，补全部件时可加工有必要上下文的正例。删题考依据核心内容识别应删与应留对象；改题考修正核心表现或从一个合法状态转变到另一个状态；组合题考多项操作能否共同实现同一核心目标。原图来源与构造都由考点决定，删改优先用正例，检索需求在看到返回图片后核实。操作类型和六方面均未新增必填分类字段。

第 4.2 节为每类分别说明重点能力、原图选择与合成、题面与判据：增侧重知识提取、补全及情境应用；删侧重概念辨别、条件判断与选择性保留；改侧重诊断纠错、条件变化下的知识运用及协调调整；组合侧重综合运用、约束协调与整体一致性。能力可以交叉，不作互斥分类。合成提示按类型检查缺失处的上下文与空间、干扰项的真实区别与无关异常、修改起点的可识别性与合法性，以及多处初始条件的一致与可解性；实际原图须验收通过后交付。

删除题也可考察核心特征的识别能力：在正例场景中加入易混淆对象，要求依据概念知识删除不符合条件者并保留合法实例。作者自检确认区分特征实际可见且干扰项不是合法变体；评审同时核对应删、应留内容，不把自然共存的干扰项强解释为结构冲突。在线提示词已去除 offline 条件说明。

API 章节补充当前17万素材库的召回背景：在原候选范围内按四级分类／短路径实例的 keep/hold 召回，SHA 去重，原图短边严格大于1024；172,297个输入中172,292张成功编码进入当前索引。没有按后续逐图适用等级重新筛选，也没有预置场景关键词清单。筛选条件只帮助理解召回范围，不证明具体场景一定有图或适合本题。来源见 [场景素材库说明](../../../curation/edit_scene_images/README.md)及[原批次固定范围](../../../curation/archive/edit_scene_images_annotations_20261003/archive/GLM_HANDOFF_add_pool_v5_20260927.md)。

出题者可按提供的本地绝对路径使用 Codex 原生 shell／文件工具读取依据文档，也可用已开启的原生网络搜索继续查证；实际知识、来源与适用条件写入 basis。当前 agent 不注册 `read_documents`，也不绑定文档回调资源列。引用编号、搜索命中和未读取的链接不冒充已经核实的材料。

第 6.3 节允许在当前原生工具支持时搜图或查看网页配图，用于核对概念实例、关键部位、不同状态及易混淆对象，并形成原图设计需求。模型应实际看图、查阅来源页面，在 basis 中说明可见支持范围；网络图片不自动视为审核正例。现有原图绑定仍只接初始正例和检索 API 的实际附图回执，网页图片 URL 不能直接替代图片编号。本次只补充提示词，没有新增图片搜索开关、网页图片导入接口或真实搜图能力验证。

## 当前执行范围

正式在线节点为 `Dataset.agentmap_async`，后端为 Codex app-server。`config(agent_config=...)` 必须显式提供 agent 配置，缺失时在执行前报错；模型、工具与执行预算全部由该文件确定，节点只提供行映射、调度、journal 和可进一步收紧的 `max_calls`。模型、推理强度、工具和上下文预算统一在 YAML 声明；Python／CLI 的 mode、model、max_context_chars 旧参数已移除，不保留离线回退入口。当前配置是 `gpt-6-astra / high`，开启原生搜索、shell、view_image 和 image_generation。

每概念一个隔离会话，Codex 自己管理推理和工具循环。demiflow 负责通用 API 调度、范围/预算检查、实际图片回传、日志、结果校验和落盘。配置解析后保存内容 hash；执行前若文件已变更会明确要求重新构造 config，避免使用混合配置。

### 已开放的 API

| API | 模型提供 | 配置预置 | 原生返回 |
| --- | --- | --- | --- |
| `map_embeddings` | `text`，最多 4096 字符 | 完整 EmbeddingModel、向量对象目录、超时 | `embedding_ref / encoder_id / dimensions / call` |
| `search_vectors` | 上一步的 `query_ref`，可选 `top_k` | 固定表、版本、向量字段、返回字段、编码身份和搜索参数 | `candidates`（含 `_distance`）、source、encoder_id；平台另附 images 回执和实际图片 |

两个检索 API 分别复用 `EmbeddingActor` 和 `VectorSearch`，不创建行内 Dataset，不在 agentmap 写特定算子分支。向量保存在受限对象中，LLM 原样传引用，无需抄写 4096 维浮点数组。API 不自动部署模型。

当前 scene 表为 `curation/edit_scene_images/datasets/edit_scene_images__a170b8529adc30d8.lance@9`，完整绝对 URI 在 agent YAML 中。返回列固定为 `sha256/image_uri`，向量列 `embedding`；WeMM-Embedding-9B 4096 维完整编码身份为 `a170b8529adc30d8f58c849012c71d6eb67a4af5e6e2b07817e7522470f04244`。执行前核对查询向量、配置和表 metadata 的身份，不能只以维度相同判为兼容。

编码 endpoint 预置 `http://127.0.0.1:8002/v1`；2026-10-03 本轮只读检查时连接被拒绝，尚未启动该服务或真实出题。注册与隔离测试通过不表示线上服务已可用。

场景图通过声明 `result_images: {items_field: candidates, uri_field: image_uri, sha256_field: sha256}` 回传。稳定编号为 `sha256:<完整SHA>`，`status=attached` 才表示该图真实送入模型上下文；本地对象另提供 `local_path` 供原生图像工具引用。每行最多附 8 张、单图 8 MiB/2000 万像素、总原始字节 32 MiB；每次 top_k 最多 4，最多 12 次算子回调尝试。初始图与原生图像工具的内部图片不计入这一回调附图预算。

初始图仍用 `seed_image` 整数编号，检索图用 `seed_image_id`，另一字段为 null。业务只允许绑定本会话实际 attached 的图片，按回执取得 ObjectRef 并再次验证字节；未附图或虚构编号不能出候选。原图合成仍由 Codex 原生工具执行，最终产物单独用 `source_artifact` 绑定。

通用 `demiflow.operator_llm.codex_agent` 提供输入图片路径映射和隔离的可写产物目录。Codex 将最终检查的图片复制到该目录并返回单层文件名；接入层在临时目录清理前将实际文件写入共享 `objects/<SHA前两位>/<SHA>`，在原生调用记录中保存 `name/byte_size/object_ref`（`uri + sha256`）。业务只接受本次调用已保存的图片，并验证 SHA、图片解码、图片选择及逐项检查记录。不接受模型随意返回的路径，也不把原种子字节冒充加工结果。

产物目录只接受普通文件，拒绝符号链接、硬链接、子目录和特殊文件；`max_artifact_files=8`、`max_artifact_bytes=67108864` 是默认硬限额（每会话文件数/总字节数），在 agent YAML 的 `options.codex_agent` 调整。失败图片可保存供诊断，只有作者检查通过的题目进入候选表。文件保存失败属于技术失败，不冒充概念不适合出题。

是否实际合成由 agent YAML 的 `options.codex_agent.image_generation` 决定；本份配置为 true。设 false 且移除 artifact_store/产物限额时可只设计，待合成方案落为 `needs_source_synthesis`。Notebook 保持 `RUN_PIPELINE=False`。未点击 notebook 执行开关时不启动流程；不再通过独立 offline 模式准备另一份出题请求。
`max_generation_attempts=2` 是作者需遵守的图像工具尝试预算，不是后端强制次数上限。超时适用于整个 Codex 执行；同一执行可含多轮工具调用。主推理模型由 agent YAML 的 `model.name` 指定；原生图像工具的具体生成模型由 Codex 服务决定，本接口不伪称可独立指定或已核实其型号。

2026-10-03 本次提示词整理时，只读查询实际 `4001/v1/models`，未列出 `gpt-image-2.5-sun` 或 `malasci/gpt-image-2.5-sunburst`。原图构造继续使用现有 Codex 原生图像工具；固定调用上述型号尚需可用接口及准确注册名，未虚配为已接入。


Codex 原生 `shell_tool/view_image` 已开放，生成由 agent YAML 的 `image_generation` 控制；未开启产物交付时使用 read-only sandbox，开启时使用临时 cwd 的 workspace-write sandbox。生成图由原生图像／查看工具送回 Codex 上下文，平台保存最终选中的实际文件；不是只把 URI 当作模型已看见图片。平台不另维护 Codex 内部图片历史或强制原生工具的图片数上限。

`max_input_bytes` 默认 64 MiB，限制含图片编码的初始请求及单条发出消息；`max_scratch_bytes` 默认 256 MiB，限制临时空间的采样阈值及单文件上限。其他 app-server 默认限制见 [平台文档](../../../../demiflow/docs/operator_environment.md)：单条接收事件 4 MiB、累计 16 MiB、4096 个事件、stderr 64 KiB、进程树 RSS 采样阈值 2 GiB。超限明确失败。并发会叠加会话资源预算；这些阈值不等于 Codex 内部 token 或费用的硬上限。

## 输入与边界

所有来源必须显式固定 `{'uri': ..., 'version': ...}`，不读隐式 latest。

2026-10-04 的 10 概念端到端试跑使用 `cohort_source`，只读 T2I 已冻结的审定名单 `cohort__ready_positive300_common100_priority_20261004.lance@4`，按已有 `selection_rank` 取前 10 项。该来源与 concepts、screening_source、独立图文来源及 document_resources 互斥，不调用 T2I 的执行入口。规范名称和原始 definition 进入概念资料；文档按固定引用校验并只提供路径、来源和 E/D 块编号，正文按需读取；正例使用名单已经选定的图片和图审来源。原名称、概念/审定 ID、名单及审核版本保留溯源。taxonomy 原样保留，抽样分类不冒充概念分类。每概念至多 64 份文档、1,024 个证据引用，每份固定文档读取至多 8 MiB；模型完整文字预算仍为 60,000 字符。

| 配置 | 读取条件及用途 |
| --- | --- |
| `concepts` | 显式唯一概念列表；不推造 taxonomy |
| `screening_source` + `sample_size` | 与 concepts 互斥；只取 screened/keep，去重合并全部 taxonomy，按 sample_seed/name 哈希抽样 |
| `cohort_source` + `sample_size` | 固定已审定名单及正例快照，按 selection_rank 取前 N 项（N≤4096），复用定义、文档和图片；不重做上游审核 |
| `article_source` | 可选公共文章表，仅 reviewed 正文；不带上游判断 |
| `visual_source` | 可选公共图片表，仅已 published 且 keep 的概念关系，按交付 image_uri 和 sha256 读取独立对象 |
| `document_resources` | 可选 concept → 材料编号 → `{document_ref: {uri, sha256}, url}`；uri 必须为本地 file URI。固定引用冻结到 inputs，出题时解析为本地绝对路径，正文由 Codex 自行读取 |

图文在主线聚合、关联和编号。初始正例图按概念/SHA 去重、SHA 稳定排序，再以 `max_reference_images` 截取。实际图片号从 1 编号，独立于文字/图片混排的材料号。正文超 `max_context_chars` 时不截断、不请求模型。启用 search_vectors 时允许没有初始图片的概念进入 agent，按需检索种子；未启用检索时无图仍为 `needs_seed_images`。只为入选、预算合格的图片读取字节并校验 SHA/完整解码；业务读图最多 32 MiB、2400 万像素。

概念正例仍与本概念绑定，场景向量表经 `search_vectors` 消费。审定资料与正例通过固定 cohort 快照接入，保留其 preparation 来源；旧独立材料入口未提供 document_resources 时不生成文档入口。沿用固定对象引用保存来源身份，行准备阶段按平台迁移记录解析本地位置、分块核验 SHA，不把正文展开进初始模型上下文。缺文件或摘要不符按技术错误中止；若旧记录带 eligible=false，则不提供给模型。
该 pipeline 只读上游数据，不执行 preparation、标注或审核，不修改公共图文/原图池。

## 输出与执行语义

所有表平铺在 `benchmark/edit/v2/datasets/`，运行名无后缀；候选表可显式指定其他目标。输入/设计按 concept 唯一，候选按 task_id 唯一，summary 按 run 唯一。阶段表与 summary 覆盖写；候选支持 overwrite/append，append 在目标锁内按 task_id 去重。

本次 typed question/candidates 新增可空 `seed_image_id`。旧响应仍可按初始图片号读取；旧表不自动改 schema，请使用新运行名/目标，或另做明确迁移。Notebook 当前指向 `edit_e2e10_v1_20261004`，未写旧运行结果。

| 表 | 内容 |
| --- | --- |
| `inputs__run.lance` | 每概念一行，固定图片引用、材料号、taxonomy 和输入状态 |
| `designs__run.lance` | 每概念一行，typed question、seed_asset、edit_source、状态、reasoning、原生 call_json |
| `candidates__run.lance` | 每道可交付题一行，实际 edit_source + instruction，附内部审计字段；状态 unreviewed |
| `generations__run.lance` | 每题一次本地 Qwen 编辑的状态、实际原图与作答图 ObjectRef、模型/部署/请求身份；失败也保留行 |
| `reviews__run.lance` | 每题一次前后图联合评审，逐考点证据、总体结论、评分（无法评分为 -1）及分类依据；保留生成阶段固定引用 |
| `summary__run.lance` | 已提交的固定版本、状态计数、本次配置；不以处理行数冒充写入完成 |
| `calls__run.sqlite` / `probe_calls__run.sqlite` | 出题／评审原生 journal；保存请求、响应或错误及预留，Codex 响应另含 app-server 事件与实际产物引用，供复用和定位技术问题 |
| 工作区 `objects/`（文件目录） | 按 SHA 共享实际文件，包含失败题目已交付的诊断图片；引用保存在调用记录和业务表 |

`candidate` 仅表示题目结构有效、实际原图已绑定、作者按每项 source_image_checks 自检通过。它不表示独立审核通过。`source_image_check` 的 observations 是作者看图报告，当前不声称能够机械验证语义或证明每次都调用过查看工具。

缺知识/无成立方案为 `insufficient`；合成待执行、原图检查失败、无图、预算不足、无响应和技术失败分别留状态。`through=author` 时，所有概念均为 candidate/insufficient 才 `complete=True`。`through=probe` 还要求每道候选题都有成功作答和有效评审，并检查三个阶段的 task_id 完全对应。评审得出 fail 仍是完成的判断；技术失败、预算不足及待返回均使整轮未完成。候选表不因探测通过而改为正式审定。

作答模型只应收到 **edit_source 的图片字节和 instruction**。seed_asset、source_image_edit、source_image_checks、作者检查、知识材料及 test_points 都是内部信息，不自动作为作答参考。正确答案不必复原种子图。

`max_calls` 限制新 Codex 会话次数，默认本批概念数与 agent max_requests 的较小值；一个会话内可能发生多轮模型/原生工具交互。agent 的 `options.timeout_s` 是整个会话的时限，`concurrency/queue_depth` 控制概念并发。Codex 不使用 HTTP temperature/max_tokens。相同完整请求复用原生响应及固定图片引用，不依赖临时目录，也不重新生图；缓存图片缺失/损坏时报告交付错误，不自动重新调用。未完成的预留请求不会自动重试。改参数后的请求可能不同，不能仅凭同名运行保证复用。旧 `codex_exec` 与新 app-server 的请求身份不同，不跨 transport 复用；试跑应使用新运行名，历史结果表和图片仍可读取。可选 `model_revision` 是运行环境变化时的缓存身份标签，不替代 `model` 或承诺服务端冻结版本。

进度日志在阶段开始、每 progress_every 个结果及异常、落表完成时输出。它由完成行触发，不是等待模型期间的独立心跳。取得运行锁后先将 summary 置为 running 并清空完成引用；失败写 failed 与已提交阶段引用，不能把旧成功冒充本次完成。硬中断可能停留在 running，不表示后台仍存活。

## 出题中的试作答与一次联合评审

`config(through='probe', probe={...})` 将后续环节放在同一条 Dataset 链上：

```text
agentmap(Codex) → 原图与题目校验 → designs / candidates
               → map_cached(本地 Qwen 编辑) → generations
               → map_prompt_async(GPT 一次联合评审) → reviews
```

四个阶段均通过平台 `save_lance(max_batch=1, queue_depth=1)` 保存再向下游交付，`run_stream` 统一管理 writer、锁、取消和收尾。候选一旦提交即可进入作答；不需要等待本批全部出题结束。摘要在收尾时记录各阶段实际提交版本；中断时已提交表保留，`complete` 不会先于结果发布。`through=author` 保留原先整段出题后提交的方式。探测只支持本 run 的 overwrite 快照，不允许 append 混入其他批次。

作答使用本地 **Qwen-Image-2.1**（本地模型说明中的视觉生成部分为 7B），默认端点 `http://127.0.0.1:8005/v1`、40 步、1024×1024、并发1。`revision` 必填，用作实际部署身份标签；修改权重、服务实现或推理设置时必须改标签，标签自身不证明权重被冻结。`run_seed` 与实际原图 SHA、正式指令共同派生种子。模型名、端点、部署、尺寸、步数、种子和输入都进入复用身份；调整评审 prompt、评审推理强度或并发不会改变作答缓存。

本地服务由工作区 `models/serve_z_image.py --backend qwen-image-2.1` 维护，新增 JSON `POST /v1/images/edits` 接口，向本地 `QwenImage21Pipeline` 明确传入 `image=`。已有文生图接口继续保留。编辑入口只接实际内联图片，回传输入图 SHA；平台检查 SHA 回执、模型、步数、种子、输出尺寸并完整解码图片后才保存独立对象。老版本服务只支持 `/images/generations`，需由服务管理方部署更新后才能运行；业务不会启动服务、下载权重、占用 GPU 或杀掉其他任务。

作答请求只有实际 `edit_source` 像素、原样 `instruction` 和推理参数。`test_points` 等审计信息随数据行保留，但不发送给作答端点。通用 HTTP、字节限额、图片校验及独立对象交付在 `demiflow.image_edit` 维护；业务行函数只组织本题及状态，不创建子 Dataset 或实现模型循环。

评审是普通 `map_prompt_async`，模型在 `prompts/review.yaml` 固定为 **malasci/gpt-6-astra**，通过 `request_options.reasoning_effort='xhigh'` 指定推理强度，不把强度拼入模型名。评审收到 Image 1 原图、Image 2 作答图，以及 concept/taxonomy/instruction/test_points；不接收出题推理、种子图、合成指令、作者自检或作答模型身份。一次请求同时完成判据适用性检查、逐考点评审、总体结论和概念核对门槛分类，不再拆成 image/joint/blind 三次调用，也不称为独立盲评。

评审 prompt 当前为 `edit-v2-probe-joint-review-7`，直接复制 T2I `t2i-v2-probe-joint-review-1` 的正文、模型与响应 schema。只调整 Edit 必需的输入说明：原图与编辑指令共同定义任务，Image 1 为原图、Image 2 为作答图，证据对照前后变化及必要保持条件，原图中的预设问题不直接算作作答失败。沿用 T2I 的输入范围、逐项判据、总体评分与概念分类，不再重复出题的六方面、四类操作或原图合成流程。`point/basis/criterion` 仍用于逐项评审；不独立扩写新的概念考题或隐藏要求。新响应的无评分值与 T2I 统一为 -1，业务校验与 notebook 同步；历史 null 无评分仍只读展示为“无法评分”，不重写旧结果。

逐项结论为 pass/fail/inconclusive/invalid_criterion；索引须按原序覆盖全部考点。总体为 pass（7–10）、partial（4–6）、fail（0–3）或 inconclusive（score=-1，与 T2I 一致；不参与均分或失败率）。遮挡、义项未明、依据不足和无效判据不能冒充已确认作答错误；无法访问图片属于技术问题，不打零分。类别由业务代码派生：①日常知识、中文大众熟悉且本次 fail/partial；③需要专业知识核对；其余有足够依据的归②；义项或概念视觉依据不足时留空。一次结果只能说明该图的表现，不能证明模型内部知识或稳定能力。

探测配置另外包括 `max_generation_calls/max_review_calls`（分别默认本批概念数）、`timeout_s/review_timeout_s=600`、两阶段并发/队列默认1、`review_max_context_chars=60000`、`review_max_output_tokens=8192`。图片单张原始字节上限32MiB、解码上限2400万像素；评审固定两图，另检查完整固定正文和动态文本预算。HTTP 回包在解码前有编码字节上限；这些限制约束载荷与在途数量，不等于 Python、PIL 或 GPU 峰值内存的硬保证。尺寸为256..4096之间32的倍数，超限或坏图明确失败，不自动缩图或改题。

每题最多一次新增编辑请求、一次新增评审请求，均不自动重试。完整相同候选的编辑返回值由平台 `map_cached` 复用，包括已确认的失败状态；从未提交的 budget_exhausted/pending_generation 通过平台 cache_when 保留为可续跑行，不缓存为完成结果。评审复用原生 journal。遇到服务失败，应先核查端点与对应调用记录，再用明确的新探测运行重新请求；不通过普通重复执行隐藏重试。进程在编辑返回值保存前退出时可能重做该项，不承诺恰好一次外部执行。图片存储失败中止流程，不归为模型低分。

本次新增 criterion、阶段引用及探测字段。入口拒绝覆盖旧 schema 的 designs/candidates/summary；新协议使用新运行名和目标，不自动迁移历史题目或判断。

## Notebook 与清理

[调试 notebook](edit_v2_benchmark_debug.ipynb) 集中配置固定来源、小批抽样和唯一 agent 文件路径，默认 `RUN_PIPELINE=False`，由用户手工执行。最后一格可在新 kernel 中独立运行，只读固定结果并生成 `runs/<run>/case_browser.html`；不依赖生产格内存、不调用模型、不补做检索或修改业务表。展示层在 `operators/case_viewer.py` 与同名 HTML 维护，notebook 只负责调用和显示。

浏览方式参考 T2I evaluation：单页搜索、结果筛选、翻页、概念跳转及图片放大。每个概念依次呈现正式题面和全部考点／依据／判据、冻结概念资料与正例图、检索与原图构造、最终原图和作答对照、逐项评审。最终原图始终单列显示，即使字节与种子图相同；合成失败没有最终原图时明确留空，不用种子图替代。原图验收逐项呈现条件、作者判断和可见证据。

场景检索从该次调用的 observations 读取查询参数、返回候选、稳定图片编号及实际附图回执；显示所有有固定对象的候选，并区分 `attached`、预算未附图、不可读取和已选种子。构造产物只展示调用实际保存的 artifacts，标明被最终题目绑定的文件。原生网络检索从已完成工具事件呈现查询、来源和摘要；只有远端缩略图 URL 时提供显式联网加载按钮，不自动下载或声称该图已进入模型上下文。没有检索记录时直说未记录调用，正例图不冒充检索候选。调用记录缺失或失败时保留错误与可读取的部分记录，不重放工具。

出题与评审的完整输入读取历史 request_ref，图片以原始 data URI 摘要核对后显示，不能用当前模板重新渲染代替。旧 Qwen 作答显示该次保存的 prompt、参数和经 source_sha256 匹配的实际原图。原图 URI、SHA 和调用定位放在详情中。所有展示文字保留全文；最多 300 个概念、8,192 个唯一图片对象，文字与编码缩略图各限 64 MiB；逐图读取最多 32 MiB／2,400 万像素，缩略图最长边 768。超总预算拒绝发布部分内容；单图读取失败明确显示原因。原生 journal 按单次引用逐条读取，单条载荷还受原运行保存时的请求／事件预算约束。运行中缺少最终阶段引用时可查看各表已提交的独立固定快照，并在元数据中注明尚未统一收尾。

2026-10-04 查看层验证：本轮 10 概念、56 个实际图片对象全部展示可读，6 份原图／作答与评审对应，4 个技术失败可独立筛选；真实运行的场景检索次数为 0。另以隔离样例验证已附图／未附图候选、合成原图、失败不替换原图、固定版本读取、文字转义、浏览交互及超预算拒绝，3 项测试通过；目录规范检查通过。已从新 kernel 单独执行查看格并保存真实输出，没有新增模型调用。此前控制目录的 `preview.ipynb` 保留为旧版列表展示快照，当前交互浏览见正式 notebook 与上述 HTML。

已移除 V2 旧的 preparation RunFiles 继承、冻结材料与源码、历史发布格式适配、通用 JSON 阶段表、local Qwen 接线及未使用 transforms/prompting 模块。测试迁到 `v2/tests`，无旧路径转发。V1 仍有实际调用，历史表、冻结结果、独立 source_images 流程和其他任务的改动保留。

```bash
python -m benchmark.edit.v2.edit_v2_benchmark_pipeline --help
pytest benchmark/edit/v2/tests -q
```

开发验证只用隔离数据与模拟 app-server（以及共享文件传输的旧 CLI 回归），覆盖真实 PNG 交付、临时目录清理后复用、原图自检失败、文件缺失/损坏/越界、限额、超时与存储失败。新增隔离测试覆盖真实 HTTP 编码响应 → Lance 检索 → app-server 实际附图 → 无初始图出题 → 稳定编号交付，以及精确回放不重复编码。尚未启动真实出题、生图或上游标注任务；原生工具的账号可用性与生成质量由手工试跑验证。

2026-10-03 本次探测改造的隔离验证覆盖：实际原图进入 Qwen `image=`、生成端无判据泄漏、评审双图顺序与 xhigh 参数、完整结果复用、坏图/错误源图回执/响应超限、全不足时空阶段无下游请求、磁盘写入失败中止，以及第二个作者尚未结束时第一题已开始作答。另检查两种实际输入渲染后的固定前缀一致，输入中的模板符号不二次解析。模型质量与真实吞吐仍未测试。

验收结果：Edit 原有37项回归与新增19项探测测试全部通过（新增测试分批覆盖）；平台缓存相关39项、已有本地文生图接口8项以及 Edit 单独布局检查通过。全仓布局检查另在 `preparation/concepts/configs` 遇到目录清单不匹配，属于本轮未改动模块。只读健康检查时，8002 编码服务与8005 Qwen服务均连接被拒绝；本轮没有部署或重启服务，也没有执行真实模型请求。

后续提示词与输入整理验证：材料拆分、文档原生入参、场景与正例实际图片编号、正式入口调用及固定前缀相关6项通过；随后出题／评审渲染、真实附图顺序与评审响应契约相关10项通过（两组有重叠）。两份出题 JSON 示例均通过各自响应 schema，模板章节引用及配置可解析。只做隔离验证和网关目录读取，没有发起真实出题、生图或评审。

本次对齐 T2I v16 的输入说明后，14 项隔离测试分批通过，覆盖三类材料渲染、A/D 与图片编号、初始非正例图片拒绝、文档回调、评审契约与双图顺序，以及第一题在第二个作者完成前进入作答的流式衔接。两份出题配置与 JSON 示例、评审配置、Notebook 代码语法和差异空白检查通过。未请求真实模型。

2026-10-04 v10 改为本地文档读取，并将输入通用正文直接复制自 T2I v16，仅改 Edit 作答条件与例子；在线 prompt 移除 offline 分支说明。删除题扩展为核心特征识别与易混淆对象区分，评审更新为 v4。12 项隔离回归通过，覆盖模拟 Codex 进程按含中文／空格的绝对路径读取实际文档、初始上下文不展开正文、只注册编码与检索两个 API、真实检索附图与图片绑定、固定前缀及评审契约。两份输入通用正文与 T2I 逐字比对一致，各自两份 JSON 示例通过 schema 校验，Python／Notebook 语法及差异空白检查通过；没有运行真实模型。

2026-10-04 v11 将六方面及识别、理解、知识运用作为原图构造的共同原则，重写四类操作、原图验收和交付核对，评审同步为 v5。10 项既有隔离回归通过，覆盖完整提示词渲染、固定前缀、材料与实际图片绑定、评审双图顺序及响应契约。两份出题提示词的输入通用正文与六方面说明均与 T2I v16 逐字核对，各两份输出示例通过 schema，章节编号与配置通过检查；正文分别为 14,900／11,765 字符，评审为 7,328 字符，实际请求仍按完整动态输入检查预算。本次只调整提示词与说明，未运行真实模型。

同日 v12 按用户要求将每种操作的重点能力、原图选择与合成注意事项、题面与判据分别展开，评审同步为 v6。最终在线配置与离线配置的第 4.2–4.4 节一致，六方面仍逐字沿用 T2I；两份配置及各两份输出示例通过校验，4 项固定前缀／完整材料渲染／实际图片与评审双图绑定回归通过。最终出题正文分别为 15,889／12,754 字符；没有调用真实模型。

同日按用户要求删除重复的 `prompts/tasks.yaml` 及离线出题分支，出题只接完整的 `agent_codex.yaml`，移除 Python／CLI 的旧 mode、model、max_context_chars 参数。评审 v7 直接复制 T2I 当前联合评审，只调整 Edit 的原图、编辑指令与前后变化说明；模型和响应 schema 与 T2I 一致，无法评分统一为 -1，校验与 notebook 同步。53 项 Edit V2 隔离回归分批全部通过，另有 1 项正式 CLI 入口检查通过；覆盖零预算后续跑、实际原图构造与交付、检索附图、文档读取、作答和双图评审、缓存复用及逐条接续处理。两份现役配置、Python／Notebook 语法及差异空白检查通过，评审正文为 2,755 字符。未运行真实模型或写入生产结果。
