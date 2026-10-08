# P1→P2 实际修改清单：按完整业务模块与 Dataset API 归账

更新：2026-10-01。本文件描述已经写入代码的状态，不再是接口提案。用户所说的 pipeline 指完整模块／文件夹，包含入口、operators、prompts、schema、配置、notebook、测试和 README；不把一个入口函数或一个辅助函数另算一条 pipeline。

本清单同时覆盖此前 P1→P2 工作已经引入的业务变化，以及本轮原生 API、层次边界和缺陷修复。共享工作区中存在其他任务的修改，因此不把整个仓库的 Git diff 都归到本次。运行范围可配置；没有执行 3,315 全批，也没有写公共概念或图片表。

## 1. 按业务 pipeline 模块

### 1.1 preparation/concepts：视觉概念核验、分类挂载及显式采纳

完整模块：[preparation/concepts](../preparation/concepts/README.md)。入口为 `concepts_pipeline.config → run_pipeline`。`audit`、`place`、`tree`、`adopt_tree`、`adopt` 是同一模块的明确 operation，不是五个彼此独立的 pipeline。

| 模块内职责 | 涉及的输出表与字段 | 修改前 → 现在 | 业务与平台的分界 |
| --- | --- | --- | --- |
| 输入登记与原名身份 | inputs，以及后续透传的 `source_record_id`、`concept_id`、`original_name`、`aliases`、`raw_taxonomy`、`old_taxonomy`、`source`、`clues`、`status/reason` | 从旧树名称整理发展为可配置固定 Lance 来源与字段映射。原名决定原记录身份；QID、旧文档定位等进入带来源的线索，不自动把旧材料当事实。 | 选择源表、字段、范围及身份算法是业务；固定版本读取使用平台原有 `read_lance`。 |
| P1 提出待核内容 | plans：`plan.candidate_senses`、`claims`、`queries`、`plan_call` | 从对输入名称直接整理，改为提出候选解释、原名对应、范围和核心内容的核验项。可以用待核断言，不强迫制造疑问句；旧分类与已有材料是线索。 | prompt、响应 schema、核验项数量、原名查询保留规则在本模块；模型执行、日志、计数和错误类别由 `map_prompt_async` 承担。 |
| 首轮搜索请求及问题关联 | searches：`evidence[].query_id/query/claim_ids/round/status/reason/candidates/attempts` | 此前业务 `Search` 自己并发调用客户端；现在纯函数生成有界请求，原生 `search_web` 执行，纯函数把中性 bindings 还原成 claim_ids。 | 平台不认识概念、原名或核验问题含义。业务不再写搜索协程、HTTP 或候选响应适配。 |
| 候选来源信息 | `evidence[].candidates[].url/title/snippet/engines/result_kind` | 原来只保留 URL、标题和摘要；现在另存引擎来源及普通结果／infobox 链接类型。 | 平台交付事实上的返回渠道；业务与 P2 判断资料用途和可信度，不按名称命中分数硬删候选。 |
| 文档请求及轮次记录 | fetched：`documents[].url/final_url/content_type/retrieved_at/document_ref/raw_ref/query_ids/claim_ids/round/last_requested_round/status/reason/attempts` | 此前业务 `Fetch` 控制并发、候选遍历、去重和额度；现在业务声明候选组及额度，`fetch_documents` 执行。旧 URL 再次被请求时合并关联，保留首次 `round`，另记本次 `last_requested_round`。 | `round`、claim_ids、一次补查额度属于业务；网络、对象保存、成功额度执行及复用机制属于平台。 |
| 阅读需求与证据交付 | evidence：`readings`、`selected`、`material_tokens`；正文仅在流中 `_materials` 存在 | 此前业务 `Read` 打开对象、定位章节、排序、选块、统计未读范围、反复压入 prompt；现在只声明问题、适用文档、保留证据、指定位置和额度，调用 `read_documents`。 | “希望核验什么”“哪些旧证据不能删”由业务声明；具体如何读取、计量和适配由平台执行。 |
| P2 的完整业务载荷 | 模型调用日志；阶段表保存正文引用与位置 | 本轮保留一个纯 `assessment_inputs` 构造函数，让平台在选材时计算实际 P2 载荷。最终模型节点使用同一业务构造逻辑。 | 平台可以调用纯载荷构造函数，不能在里面添加概念政策，也不能偷偷调用模型。 |
| P2 审定 | assessment_first / assessment_second / assessments：`assessment`、`assessment_id`、`assessment_call`、`rounds` | 输出身份、名称关系、概念层级、核心事实、视觉出题可用性、逐问题结果及补查请求；审定必须引用实际已读块。第二次 P2 不得要求第三轮。 | 全部语义判断、引用业务校验、等义改名和范围改变判断都在本模块。 |
| 一次补查与技术状态 | supplement_searches / supplement_fetched / supplement_evidence；`supplement_execution`、`rounds`、`status/reason` | 同一原生搜索／获取／阅读能力用于补查；只补读时没有 HTTP。命中已有 URL 也保全新问题关联、允许读取未读内容。第二轮技术失败保留第一轮审定。 | 业务决定是否补查、已知 URL 范围、最多一轮、各类额度及失败后的业务状态；平台只执行请求和数字限额。 |
| P3 分类挂载（此前已新增） | placements：`node_id`、`taxonomy`、`tree_source`、`tree_version`、`assessment_id`、`status/reason/tree_issue/candidate_ids/call_json` | 用明确审定与固定树版本挂载；身份未明、范围变化及树本身不足分别保留。 | 分类政策和候选节点解释是业务。本轮平台修复没有另改挂载规则。 |
| P4 树提案与树采纳（此前已新增） | tree_proposals、trees：节点定义、包含／排除范围、例子、父节点、版本及 review_note | 基于有界输入提出新树或修订，再以明确提案 ID 和 review_note 采纳。 | 属于本业务模块。既有实现仍是有界样本提案，不声称已经完成大规模持续建树。 |
| 概念结果采纳（此前已新增） | adopted：身份、原名、审定引用、核心事实、任务方向、可选挂载与审核信息 | 操作者明确选择审定记录，限制范围变化自动继承，保留原名与图片关联所需身份。 | “可采纳什么”属于业务；表提交和固定版本属于平台。 |
| 阶段表及运行摘要 | 各阶段表；summary 的 `phase/complete/input_count/counts_json/config_json/metrics_json/outputs` | 此前业务手工初始化 writer、批调用、关闭 HTTP、装队列及解释 usage；现在图上声明 `save_lance`，运行后消费 `run_stream` 的固定版本及技术观测。 | 业务仍决定表名、schema、主键、提交位置、完成分母及补查比例；不再实现提交生命周期或技术指标解析。 |
| 预算摘要 | `metrics_json.budget_ceiling` | 此前总写 3N；现在分开完整结构理论上限、实际启用节点、显式调用额度对应的输入／输出上限。 | 数值与 operation 对应关系属于业务；真实请求数、usage、缓存复用及累计日志计量属于平台。 |
| 错误映射 | `status/reason` | 此前业务匹配平台异常类名；现在消费稳定的技术 category，再映射为 pending、context_budget、call_budget、model_error 等业务状态。 | 平台分类技术原因，业务决定该原因在本流程里的含义。旧日志的类名兼容由平台处理。 |
| 模块的其他文件 | 配置、四格 notebook、README、prompts、测试、Markdown 导出 | 这些都归本模块。README 和 notebook 按原生 API 与服务档案更新；测试覆盖完整正式入口，不另造生产执行入口。 | 本轮没有为了修平台边界重写 P1/P2 语义 prompt。Markdown 是固定结果的只读导出，不是另一套结果真相。 |

P2 `assessment` 的完整主要字段为：`identity_status`、`canonical_name`、`definition`、`concept_kind`、`qualifiers`、`name_relation`、`taxon_rank`、`identity_evidence_ids`、`core_facts`、`task_status`、`task_sketch`、`gaps`、`reason`、`questions`、`supplement_requests`。科、属、种、品种、阶段、器具类别、型号、角色等都由业务协议解释，平台没有写死这些分类。

**本轮完成后的越界检查：**在该模块的运行代码中，没有再发现需要下沉的 HTTP 调用、搜索 provider、并发任务组、正文解析、对象正文读取、章节定位、选块／计量算法、usage 解析、队列观测或阶段 writer 生命周期。`audit.py` 保留的是业务纯函数；它没有被整文件搬到平台。配置中存在并发、超时、引擎和额度数字是正确的调用方声明，不等于业务实现了底层机制。

### 1.2 benchmark/t2i/v2：T2I 正式出题

完整模块：[benchmark/t2i/v2](../benchmark/t2i/v2/README.md)。此处是此前 P1→P2 工作已经做过的下游衔接，本轮没有借平台修复再调整出题门槛。

| 模块内改动 | 输出字段 | 修改前 → 现在 | 边界结论 |
| --- | --- | --- | --- |
| 新增固定审定输入 `concept_audit_source` | 输入来源配置、选样记录 | 原概念列表／screening_source 两类来源，扩展为三选一；要求固定版本和显式 sample_size。 | 来源选择、采纳状态、身份明确、名称等义、task ready 的条件属于该出题模块。 |
| 透传审定上下文 | inputs、designs、candidates 的 `concept_record` | 原来没有该列；现在保存来源身份、原名、assessment_id、adopted_source、规范名、定义、对象类型、限定、core_facts、task_sketch。 | 业务上下文，不能移进平台模型运行时。 |
| 旧图片关联及规范名称 | 行级 `concept`、模型输入 concept、references_json、taxonomy | 行级 concept 保持 original_name 以关联旧资料；模型使用 canonical_name 和审定记录；taxonomy 可来自采纳结果，也可尚未挂载。 | 不能把改名当成图片相关性已审核。 |
| prompt 与题目身份 | prompt 输入；`task_id` | prompt 解释审定范围与冲突处理；有 concept_record 时将其加入 task_id 摘要，旧入口保留原 ID 算法。 | 都是出题业务行为。task_id 变化已明确列出，不再遗漏。 |
| 模块内 README、schema、测试 | 对应输入／中间／候选契约 | 随接口接入同步说明和验证；没有新增另一套 P5 执行器。 | 未发现由本次审定接入新增的、仍错放于此模块的通用下载或服务实现。 |

同文件里其他会话的对象迁移、材料选择或执行改动不据整份 Git diff 归入本工作，也不据此宣布整个 T2I 模块已经完成全面平台审计。

### 1.3 collect 与早期 taxonomy-rebuild 产物

| 模块／产物 | 实际改动与字段影响 | 是否有未下沉内容 |
| --- | --- | --- |
| 旧 collect 模块 | 分析既有实现并维护 TODO；本轮没有热切旧生产采集、改图片下载或重写其结果表。原 `wikisearch` 的通用引擎实现已在平台维护一份独立适配，用于新消费者。 | 旧 collect 仍有旧部署、引擎及名称筛选实现，尚未逐消费者迁移。它们是明确延期的历史链路，不是新概念 pipeline 的运行依赖。别名漏传 bug 继续留在 COLLECT-001，不能标成旧 collect 已修复。 |
| taxonomy-rebuild 早期 `build.py`、`combined_view.py`、`audit_visual_leaves.py`、MD／PDF 与回放工具 | 这是本聊天早期的分类交付及历史工具，本轮没有把它们伪装成新的事实核验执行器。新树尚未批量生成。 | 归概念业务的历史产物；后续正式新树用审核结果与 P3/P4，不依赖历史判断回放来证明事实。 |
| taxonomy-rebuild 规范、设计、清单、review、性能及执行计划 | 归概念 pipeline 的设计／验收材料；虽然物理位置保留在原目录，也有明确业务归属。 | 不构成第三种生产架构层。 |
| preparation/articles、images、QID、训练和评测 | 没有作为本次业务修改对象；公共数据保持只读。 | 已知全仓测试中其他模块的问题单列，不借本轮改动顺带重写。 |

## 2. 按 demiflow Dataset API

下面的接口均已落地。参数展示本次新增或相关的实际签名；完整运行说明见 [平台文档](../../demiflow/docs/web_evidence.md)。内部类／函数只用于说明“这个 API 如何完成职责”，不要求业务逐个调用。

| Dataset API | 修改性质与接口 | 内部能力模块 | 修改前 → 现在 | 是否混入业务 |
| --- | --- | --- | --- | --- |
| `search_web` | 新增。`search_web(*, requests, output, session, max_candidates=5, request_concurrency=2, concurrency=8, queue_depth=None, when=None, label='search_web')` | Typed plan、查询组调度、WebSession、SearXNG provider、HTTP／日志、SearxNGService、受管 supervisor 与共享请求限额 | 原来只有客户端，业务写 Search actor；现在 API 完整执行查询列表，交付候选／技术回执。支持 results 与 infobox，畸形记录有回执，配置与适配版本进入缓存身份。 | 无概念名、科属种、问题类型、来源权威度或名称硬筛规则。 |
| `fetch_documents` | 新增。`fetch_documents(*, requests, output, session, known=None, per_request=2, max_attempts=None, max_new_documents=None, request_concurrency=2, url_concurrency=2, concurrency=8, queue_depth=None, when=None, label='fetch_documents')` | 候选组与去重、额度执行、WebClient 传输、独立对象、正文解析、隔离执行、获取与解析两层日志 | 原来候选执行在业务；现在业务提供有序 URL 组和额度，平台执行并保存 raw/document 引用。复用仍合并所有关联；解析器变更复用原始快照。 | 无“这是补查第二轮”“只允许两篇”等写死业务规则；数字由调用方传入。 |
| `read_documents` | 新增。`read_documents(*, request, output, context, document_concurrency=2, timeout_s=30, max_bytes=8388608, concurrency=4, queue_depth=None, when=None, label='read_documents')` | 独立对象校验、章节定位、跨文档选块、关联去重、未读目录、TokenBudget／PromptContext、进程隔离 | 原来业务负责所有读取与上下文缩减；现在原生节点返回实际材料、位置、未读范围与技术状态。完整输入先检查全额度和零新增材料，再至多六次缩放新增额度；保留旧证据和强制上下文。 | 平台只知道问题文本和不透明 bindings；P2 payload 内容由业务纯函数构造，平台不审定概念。 |
| `map_prompt_async` | 扩展既有接口。相关参数：`when`、`call_output`、`error_output`、`request_gate`、`token_budget`、`options={'sqlite_journal':{'path':..., 'max_requests':N}}`，以及原有 concurrency／queue_depth／max_requests | 原生 prompt runtime/client、SQLite journal、稳定错误类别、token 档案、调用观测、nullable schema | 从原有原生调用能力扩展为条件调用、完整输入准入、共享调用门控、持久累计额度、规范调用／错误回执。观测在模型节点产生，不依赖最终业务行。 | 模型名、prompt、schema、通过条件与补查要求均由业务传入。 |
| `run_stream` | 扩展既有执行行为；入口仍为 `run_stream(*, on_progress=None, on_drain=None, log_every=0, cancellation=None, queue_factory=None, stall_timeout=None)` | StreamResources、队列／延迟、模型与日志统计、actor／共享资源生命周期、兼容池清理登记 | 原来业务拼装关闭与技术指标；现在统一启动／收尾，返回 `stats.outputs` 和 `stats.metrics`，失败时 on_drain 也有已提交版本。旧 collect 私有全局变量不再由 Dataset 直接清理。 | 平台没有概念 complete 计算、补查率或 3N 业务预算公式。 |
| `save_lance` | 新增非终结提交节点。`save_lance(uri, *, schema, key, stage, max_batch=32, flush_interval=5., queue_depth=64)` | Typed batch plan、StreamLanceSink／Writer、单目标锁、Lance expected_version、唯一键检查、不确定提交核对 | 原来业务创建 writer、初始化并用 batch_map 调用；现在声明即完整交给平台管理。确认提交后放行，空输入也有明确快照，尾批落盘，旧版本可读。 | 表名、schema、主键、业务写入范围和提交位置仍在业务图。 |
| `map_prompt` 等共享模型契约 | 没有因此新增另一套模型 API；既有同步／异步共用的响应 schema 等获得通用修复 | 消息构造、nullable 响应校验、原生日志及错误回执 | 具体类型可与 null 组合且非空值继续严格校验；HTTP 错误保留真实状态，不能全部误认成模型 JSON 错误。 | 无概念业务 schema 固化到平台。 |
| `map`、`map_async`、`batch_map`、`read_lance`、`write_lance` | 本主线继续复用，不把“使用了”算成“新增 API”。本轮 typed web／save 节点复用既有 async／batch 执行器。 | 原有 Dataset 执行与读写 | 不改变它们为任意 helper 的别名；尤其终结式 write_lance 与流中 save_lance 的交付语义不同。 | 工作区其他任务的 thread、join、Lance 迁移等修改不归到本次。 |

### 2.1 底层能力必须能够归到哪一个 Dataset API

| 原先孤立列出的名称 | 现在的归属和实际作用 |
| --- | --- |
| `WebClient.search`、SearXNG 适配、`wikisearch`、SearxNGService | `search_web` 的内部实现与资源声明。业务不再负责解析搜索响应或复制引擎。 |
| `WebClient.fetch`、parse_document、store_document | `fetch_documents` 内部依次获取、整理、独立保存。不是三个需要业务编排的节点。 |
| `read_document` | `read_documents` 内部打开、验证一份已保存文档。它不会搜索、下载或调用 P2。 |
| `PromptContext` | `read_documents` 的配置对象：prompt、TokenBudget、业务纯载荷构造函数。与 `map_prompt_async` 使用同一真实消息计数契约。 |
| `TextTokenCounter`、`TokenBudget` | `read_documents` 的完整上下文适配，以及 `map_prompt_async` 最终请求准入。不是新业务阶段。 |
| `RequestGate` | 模型／搜索／获取节点的共享并发、启动间隔及故障停止机制。run_stream 管本次执行观察口径。 |
| `ObservedQueue`、`LatencySummary`、`StreamStats.timing_summary()` | `run_stream` 的队列与耗时观测；各模型／网络节点贡献自己的技术调用数据。 |
| `StreamLanceWriter` | `save_lance` 的小批写入、唯一键、版本保护及不确定提交核对实现。业务不再直接 initialize／reference。 |
| `journal_totals` | `run_stream` 自动汇总原生模型节点日志，区分本次节点观察与全日志累计；业务不再写 SQL 或解析 usage。 |
| `run_isolated(timeout_s=...)` | `fetch_documents` 解析和 `read_documents` 读取／选块的超时与进程清理机制；复用已有隔离能力。 |
| 服务 start／status／stop 与共享使用租约 | `search_web` 服务资源所依赖的平台控制面。服务运维命令有独立生命周期，不应伪装成每概念执行一次的 Dataset 行算子。 |
| 文档 Arrow 组件 | search／fetch／read 的共享结果协议；业务在其上添加 round、claim_ids 等字段并声明自己的阶段 schema。 |
| 旧 collect 网络／模型池清理登记 | `run_stream` 的兼容资源关闭。只有使用旧平台模块时登记；新节点走显式资源归属。 |

**平台侧反向越界检查：**本次涉及的平台运行模块没有导入 preparation、benchmark 或 demiwtg，没有接收概念 pipeline 的完整 cfg，没有固定物种层级、视觉出题门槛、名称等义、一次补查、分类树或采纳条件。旧 collect 的语义权威度／名称评分也没有被整个搬进平台。没有需要反向迁回业务的新增概念政策。

额外部署缺口：真实 P2 错误排查发现，现有 LiteLLM 网关默认重试两次。`map_prompt_async` 的日志累计额度只约束发往网关的请求，不能约束网关内部尝试。该缺口属于模型 API 的服务契约，不是概念业务规则；已登记 DF-013，未在本次排查中热改共享网关，不能宣称完整服务链的无隐藏重试和费用上界已经验收。详见 [review 第 5.4 节](P1_P2_BOUNDARY_IMPLEMENTATION_REVIEW.md#54-两次-p2-http-408-的实际来源后续只读排查)。

### 2.2 服务边界与使用限制

受管 SearXNG 服务复用 demiflow 原有 HTTP supervisor。平台维护配置和关键词适配；业务声明安装好的 Python／SearXNG 路径、版本、引擎、语言和代理。不会隐式安装、修改已有运行时或引入另一个平台。

新服务的档案摘要包含声明的运行时版本、引擎、路由、配置修订及关键词引擎代码。共享复用校验启动配置；使用期间持有租约，单个流程结束不停止共享服务，显式 stop 在还有使用者时拒绝；同机跨 run 请求槽控制总并发。全缓存命中不启动服务。

外部自管服务必须给出 `search_profile` 配置版本；仅交一个 URL 已不满足概念取证前置条件。外部管理员未登记的配置变化无法被平台凭空识别，因此受管档案更适合可复现运行。这是外部服务契约的限制，不是将配置读取重新留给业务代码实现。

本轮真实验证把既有 SearXNG 源码路径作为显式运行时依赖，使用新平台配置和新平台引擎文件；没有修改或借用旧业务的启停脚本，也没有切换旧生产任务。

## 3. 其他修改及归属

| 看起来是“其他”的内容 | 实际归属 | 是否构成第三类生产改动 |
| --- | --- | --- |
| taxonomy-rebuild 清单、实施规范、review、性能、执行计划 | preparation/concepts 的设计、迁移与验收材料 | 否。物理目录保留历史位置，逻辑归属明确。 |
| `DEMIFLOW_PLATFORM_TODO.md` DF-012、平台 README、web_evidence 文档 | 上述 Dataset API 的实现／验收说明 | 否。不是额外业务逻辑。 |
| collect TODO 的历史别名 bug、旧消费者迁移状态 | 旧 collect 模块的后续维护计划 | 否。未把历史链路未迁移冒称已完成。 |
| `_demiflow/concepts_platform_boundary_20261001` | 两类边界的变更快照、隔离测试和有界真实验证产物 | 否。不是日常生产执行入口，不写公共数据。 |
| 本次新增／修改测试 | 概念模块、Dataset API、服务与执行器对应回归 | 否。下载假服务、计数器和测试样例都不是业务运行机制。 |

没有第三套无法归属的生产执行逻辑。遗留的是已明确延期的旧 collect 消费者迁移与后续分类树质量工作，不是新 P1→P2 主图里尚未下沉的算子。

验证结果、失败项及真实案例限制见 [本轮实现与边界复核](P1_P2_BOUNDARY_IMPLEMENTATION_REVIEW.md)。


## 4. 2026-10-01 HTTP 流式传输补齐：本轮增量归属

本节只列这次从非流式 HTTP 切换为可配置 SSE 的修改；前面的原生取证 API 清单仍适用。pipeline 指完整模块目录，不只 Python 入口。没有新增业务 pipeline。

### 4.1 按业务 pipeline

| pipeline 模块 | 涉及输出 | 从什么改成什么 | 边界检查 |
| --- | --- | --- | --- |
| `preparation/concepts/`（正式名 `concepts_pipeline`） | `plan_call`、`assessment_call` 及 rounds 中的 call 承载平台新增技术元数据；summary 的 config_json/metrics_json 同步新增配置和观测。概念/审定/树的 Arrow schema 未改变。 | 过去只配置总超时和普通 JSON POST；现在显式选择 stream、gateway 和分阶段超时，由原 Dataset 模型节点执行。默认启用 SSE/usage，禁用原始事件日志；config、README、notebook 和 HTTP 测试同步。 | P1/P2 prompt、身份判断、视觉任务、补查规则仍在业务且未修改；模块没有 HTTPX/SSE/连接管理或重试循环。没有待下沉的本轮平台机制。 |

其他业务 pipeline 未切换流式。旧 collect、模型网关部署代码和共享网关进程未修改。其他会话已有改动不归入本轮。

### 4.2 按 demiflow Dataset API

| Dataset API 与实际接口 | 所属底层模块 | 从什么改成什么 | 边界检查 |
| --- | --- | --- | --- |
| `map_prompt_async(..., concurrency=4, options={'stream':True, 'gateway':'litellm', 'timeout_s':600, 'connect_timeout_s':10, 'read_timeout_s':120, 'write_timeout_s':30, 'pool_timeout_s':30, 'stream_include_usage':True}, call_output=..., error_output=...)` | `data/dataset.py` → `operator_llm/runtime.py/client.py`，新增内部 `http_options.py/http_stream.py` | 原来仅一次性 JSON 响应、池上限使用 HTTPX 默认；现在节点共享有界池、每请求独立 SSE 组装、协议完整性校验、取消/断流清理、显式网关零重试/无 fallback 适配。 | 平台没有 malasci 地址/模型、P1/P2 prompt、科属判断或补查政策。流片段不成为 Dataset 行。 |
| 同一 `map_prompt_async` 的 `options.sqlite_journal`、`call_output/error_output` | `operator_llm/sqlite_journal.py/journal.py/errors.py/call_ref.py` | 完整响应仍先保存后交付；现在失败日志还保存部分正文、最后事件、已知 usage、原错误及 timings。表只带小回执和 error_ref。取消撞上响应提交时不会再写第二个错误记录；同键未完成请求禁止盲目重发。 | 不把不完整响应转成业务 hold，不为重试自动扩预算，不新建业务存储机制。 |
| `run_stream(...)` 返回的 `stats.metrics` | `execution/stream_resources.py` 调用模型 trace 和 `call_ref.journal_totals` | 新增首字节/首事件/首正文/相邻数据间隔直方图；失败流已知 usage 计入全日志汇总；重放不会重复计入本轮新增 usage。 | 技术指标归平台；业务完成与补查比例仍由概念模块计算。 |
| `map_prompt(..., options=...)` | 复用同一配置验证与 HTTP 客户端 | 显式 options 路径可以使用相同 SSE 完整性与日志契约；保留原同步执行，未重写同步调度或声称跨调用共享异步池。 | 不新增另一套模型 API。 |

`stream_log_dir/max_response_bytes/max_event_bytes/max_stream_events/max_connections/max_keepalive_connections` 均是模型 Dataset API 的 options 能力，不是需要业务串联的新算子。完整默认值见 [平台说明](../../demiflow/docs/prompt_http.md)。底层函数只是这几个 API 的实现模块。

### 4.3 其他修改归属

平台说明、平台测试、DF-013 的状态归 4.2；概念 README/notebook、实施规范、API 清单、review 和性能记录归 4.1。工作区 `_demiflow/concepts_stream_20261001/` 只保存本次测试配置、固定结果引用和回执，没有成为生产运行依赖。本轮没有第三类运行模块、旁路入口或独立连接平台。

第 2 节记载的“仍待处理网关重试”是此前诊断状态；本轮已增加请求级 adapter 并在正式入口取得 0 retries / 0 fallbacks 的回执。没有热改共享网关，也不将两次成功观测扩大为供应商端费用保证。
## 2026-10-01 多概念验证增量

详细逐项表与行为前后对比见 [质量验证记录第 2、5、7–9 节](P1_P2_多概念质量验证_20261001.md)。本次业务修改全部属于 `preparation/concepts` 模块：P1/P2 提示词、P2 请求材料组织与短引用映射、配置与实际并发日志、固定版本 Markdown 和该模块 notebook。

平台修改分别从以下 Dataset API 进入：`search_web` 的会话启动节奏及响应内错误计数；`fetch_documents` 的正文解析；`read_documents` 的跨文档阅读和完整请求适配；`map_prompt_async` 与 `run_stream` 的服务故障有界收尾。后者由原生客户端、PromptActor 的收尾时限和流执行器配合，不包含概念语义、轮次规则或业务重试。`save_lance` 沿用已有交付协议。没有新增其他业务 pipeline，没有把同类能力另写进业务目录。

## 2026-10-01 公共文档复用增量

本轮只接通可配置公共文档库，XNG 内化在另一工作区完成，循环代理后续处理。以下按模块与 Dataset API 分组，底层名称都列在它所支持的公开接口下。

### 按业务 pipeline 模块

| 模块与正式入口 | 输出字段 | 原行为 → 当前行为 | 业务保留的边界 |
| --- | --- | --- | --- |
| `preparation/concepts/`；`concepts_pipeline.config → run_pipeline → run_audit` | 原有各阶段 `documents[*]` 增加可空的 `acquisition`；正文仍只用 `document_ref/raw_ref`。summary 原生技术指标增加公共库命中、未命中与登记次数。 | 原来只配置对象目录，URL 获取日志局限于当前 run；现在同时配置公共索引、对象目录、复用/刷新及年龄条件，并传入原生 `fetch_documents` 的资源。跨 run 命中时直接将同一正文引用交给原有阅读与 P2。 | 只选择路径、取证范围、阅读预算和概念语义；仍每概念一行、最多一轮补查。没有实现索引查询、HTTP、锁、复制对象或 SHA 校验。 |

同模块改动还包括 `operators/settings.py` 的配置入口、四格 notebook 的配置展示、`tests/test_stream_audit.py` 的正式入口验证和 README。P1/P2 提示词与语义校验没有被本轮公共复用改造修改；同时进行的提示词修订属于另一项工作，不能算成本轮存储改造的效果。

没有新建文档迁移业务 pipeline，也没有把旧 collect 的 wikitext 解析混入概念业务。公共登记的通用接口已提供，具体历史格式转换留给对应资料准备流程的标准化。

### 按 demiflow Dataset API

| Dataset 接口及本次变化 | 所支持的底层模块 | 原行为 → 当前行为 | 边界与限制 |
| --- | --- | --- | --- |
| `fetch_documents(requests=..., output=..., session=WebSession(..., document_library=DocumentLibrary(...)), ...)`；Dataset 方法参数不变，扩展资源配置 | `collect/session.py` 声明验证；`collect/web.py` 获取调度与每 run 日志；新增 `collect/document_library.py` 的来源索引、进程锁、对象校验与登记；复用 `objects.LocalObjectStore` 和既有解析器 | 原来只有当前 run 的获取缓存；现在先尊重当前 run 固定回执，再在公共库查询成功快照，未命中才下载解析并登记。对象目录相同本身不能避免网络，必须使用同一公共索引。 | 平台不决定概念相关性、可信度、科属种或一次补查政策。当前是共享 POSIX 文件系统的 SQLite/文件锁，不宣称分布式对象存储锁。原始/最终 URL 和明确导入别名参与查找，不做名称模糊匹配。 |
| 新增 `register_documents(request=..., output=..., library=..., max_bytes=2097152, max_document_bytes=8388608, concurrency=4, queue_depth=None, when=None, label='register_documents')` | `data/dataset.py` → `data/plan.RegisterDocumentsOp` → `collect/document_library.RegisterDocuments` → 同一 `DocumentLibrary/LocalObjectStore` | 原来已有对象只能通过各 run 的日志定位；现在可从固定 Dataset 的文档引用校验、复制并登记公共资料，保留原始获取时间、处理版本、来源标识和原始内容哈希。 | 每输入行一份规范文档，回执用通用 `DOCUMENT_RESULT`；输入坏文档保留 `invalid_document`，存储故障停止。只接收规范文档引用，尚不转换原始 wiki dump。 |
| `read_documents(...)`：公开接口与执行代码不变 | 已有 `collect/documents.read_document` 除阅读算子外，也支持公共查库/登记的规范对象校验；`collect/reading.py` 保持 | 原来读取当前 run 下载的独立引用；现在同样可以读取公共库返回的独立引用。 | 阅读不依赖索引或 Lance 行引用；是否给 P2 作为证据继续按既有预算和业务策略。没有新增另一个“read_document 业务环节”。 |
| `fetch_documents/register_documents` 的输出契约扩展 | `collect/contracts.DOCUMENT_RESULT` 新增 `ACQUISITION` struct | 以前来源只能从获取日志推断；现在回执明确 `kind/origin/source_id/revision/registered_at`，旧记录允许为空。 | `retrieved_at` 仍是原始时间；公共命中 `attempts=[]`，不会把历史下载计成本轮新增网络请求。 |
| `run_stream()` 资源观测：公开接口与执行代码不变 | `WebClient.snapshot_metrics` 通过已有资源观测接口暴露 `library_hits/library_misses/library_registrations` | 增加公共库计数，原 `fetch_attempts/http_hops` 继续统计实际网络。 | `max_attempts/max_new_documents` 仍是行内获取/证据配额，公共命中不扩大 P1/P2 材料数量。 |

`DocumentLibrary(index_path, object_directory, policy='reuse', max_age_s=None, lock_timeout_s=120, parser_versions=(当前解析版本,))` 只是这些 Dataset API 绑定的惰性资源配置。平台没有项目默认目录；业务默认位置及完整含义统一见 [概念 README](../preparation/concepts/README.md#公共文档复用配置输出与责任边界)。搜索服务和代理参数没有被本轮移动或替换。

### 其他修改的归属与复核

文档、测试及 DF-012/执行计划更新均属于上面两个模块的实施与验证记录。`_demiflow/document_library_validation_20261001/` 仅保存有界验收脚本、日志和固定结果引用，不作为生产依赖或第二套入口。

复核结论：业务层没有残留本轮新增的通用索引、锁、对象搬运或 HTTP；平台层没有新概念字段、P1/P2 状态、指定模型或项目路径。失败结果不进入公共索引；同 run 的固定引用不随公共库更新；旧导入不能用导入时间压过较新资料；索引损坏/文件丢失不静默改用网络；锁超时明确记为未发送 HTTP。锁文件采用 4096 个哈希槽，避免每个 URL 永久占用一个文件。

验收记录：平台相关 44 项测试通过；概念完整回归最初 37 项通过、1 项因执行期间外部提示词更新而请求身份变化，随后该项按当前提示词重跑通过；概念模块的布局与 CLI 两项检查也通过。新增业务测试覆盖两个独立 run、配置指定目录、正文免下载进入 P2、独立搜索以及显式关闭公共库。真实材料验证将历史固定 fetched@7 中的 13 份 Wikipedia 文档登记到隔离公共库，再由两个独立 run 读取：每轮 13 次公共命中、0 次下载、0 次模型调用，所有正文块/来源/解析信息/原始内容 SHA 一致。首次登记约 0.44 秒，两轮复用含落表分别约 0.56/0.58 秒；这不是全量索引、搜索或模型吞吐基准。详细回执见 [验收报告](../../_demiflow/document_library_validation_20261001/report.json)。

### 2026-10-01 Wikipedia 本地导入增量（全量执行已按用户要求停止）

| 归属 | 入口与接口变化 | 数据与底层能力 | 执行状态 |
| --- | --- | --- | --- |
| 业务 pipeline `preparation/wiki_documents/` | `wiki_documents_pipeline.config → run_pipeline`；输入固定表版本、语言、标题及条数范围均可配置 | 解释旧 wiki 分节、来源身份、页面版本和重定向；输出每页 `registration` 技术回执与固定 summary；不包含文件存储、网络客户端或索引实现 | 标准入口、小批和公共获取命中已验收；全量按用户最新要求停止，保留部分成果 |
| 平台 `Dataset.register_documents(...)` | 新增 `batch_size=1, prepare_workers=4`；保留 request/output/library、大小上限、concurrency、queue_depth、when；request 新支持 `format=wikitext_sections` 与 `format=redirect` | 原生批节点管理准备进程池，格式适配器保存模板等未展开源码，DocumentLibrary 以单批短事务登记；对象独立 URI 与 SHA，未知获取时间不伪造 | 平台回归通过；业务表名、Wikipedia 标题和语言映射不下沉 |
| 平台 `Dataset.fetch_documents(...)` | Dataset 签名不变，使用 `WebSession(document_library=...)` | 公共库接受新增解析版本；有界解析显式重定向（默认最多 8 跳），缺目标或环视为未命中；仍执行对象校验和原有下载策略 | 正式 13 页全部本地命中，0 HTTP；不变成全文搜索服务 |

详细契约见业务 [README](../preparation/wiki_documents/README.md) 和 demiflow `docs/web_evidence.md`。本轮未修改概念审定规则或提示词，未调用模型，未运行全量概念。
