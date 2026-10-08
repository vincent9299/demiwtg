# demiflow 网页取证 API 草案与 P1→P2 架构复核

> 当前状态入口：[实际模块／Dataset API 清单](P1_P2_API_INVENTORY.md)与[本轮实现复核](P1_P2_BOUNDARY_IMPLEMENTATION_REVIEW.md)。本文下述缺陷、行号、接口草案与测试数记录修复前／此前的历史快照；原生 API 已实现，不能再把历史“尚未下沉”描述当作当前代码状态。


日期：2026-10-01（北京时间）。状态：**架构复核和待实施的 API 设计，未完成代码迁移。** 本轮只新增离线复现材料和文档，没有修改运行代码，没有调用模型或真实搜索，没有启动服务或重跑概念批次。

用户随后纠正 API 粒度：下文 `map_async(SearchWeb(...))` 形式没有达到 Dataset 原生接口的要求，不能继续作为目标调用方式。最新 [业务 pipeline 与 API 清单](P1_P2_API_INVENTORY.md)明确已实现、既有复用与待改造接口，并以 `Dataset.search_web/fetch_documents/read_documents` 等原生方法替代该公开调用草案。本文的源码复核、缺陷证据和业务/平台职责划分仍保留；第 2 节旧调用代码只用于理解此前提案，不作为实施依据。

阅读依据是 [项目 Pipeline 强制规范](../PIPELINE_SPEC.md)、[概念流程 README](../preparation/concepts/README.md)、[P1→P2 实施规范](P1_P2_SPEC.md)，以及用户最新要求：“通用算子和服务管理沉淀到 demiflow，尽量不增加其他平台组件”。本文不重新打开已确认的业务规则，不把输入固定为 3,315 条。

## 1. 复核结论

当前实现完成了部分底层平台能力，但**尚未完成通用取证算子的沉淀**。业务侧的 `Search`、`Fetch`、`Read` 混合了通用执行与概念审核规则，不能作为最终架构验收通过。旧测试通过、能够下载网页和能够给出审定，都不能代替这一验收。

应该保留的主体结构是：一行代表一个概念，主图明确展示 P1、搜索、获取解析、阅读、P2、一次补查以及阶段提交。每行内部有界地处理多个查询和文档；主图无需展开成查询行、网页行，再增加归并机制。

需要改正的不是所有业务算子的位置，而是混合的职责：

| 应由 demiflow 负责 | 应由概念 pipeline 负责 |
| --- | --- |
| 搜索服务部署配置、受管进程、HTTP 客户端、供应方响应适配 | 为哪个概念核验什么问题、生成什么查询、如何解释旧名称和别名 |
| 行内请求并发、资源限流、技术重试、去重、预算执行、执行回执 | 首轮与补查的业务额度、是否需要补查、最多补查一次 |
| 网页获取、结构化正文提取、独立对象交付、校验读取、正文块选择 | 资料支持什么事实、概念是否成立、原名是否等义、视觉任务是否可行 |
| 完整模型请求的 token 计量和材料适配、模型调用日志 | P1/P2 prompt、不可删的业务上下文、模型响应的业务一致性校验 |
| 节点队列、执行指标、资源收尾、通用 Lance 提交机制 | 选哪些输入、各表 schema/主键/路径、完成分母、结果采纳和分类挂载 |

这次没有发现新加的平台模块已经把“物种必须到种”“概念通过”“只允许一次补查”等概念政策写死。主要问题是**通用能力仍留在业务，以及平台内部能力没有接完整**。其中服务模块的模型专用命名、执行器对旧 collect 全局变量的依赖属于平台内部边界问题，不能混称为概念业务规则泄漏。

## 2. API 改造后应是什么样

### 2.1 保留现有 Dataset，只补少数明确的接口

以下接口名是本次建议，**当前不能直接导入运行**：`SearxNGService`、`WebSession`、`SearchWeb`、`FetchDocuments`、`ReadDocuments`、`PromptContext`，以及 `run_stream(resources=..., collect_metrics=True)` 的两个新参数。

已有并继续使用的接口是 `map`、`map_async`、`map_prompt_async`、`batch_map`、`run_stream`、`StreamLanceWriter`、`TokenBudget`、`RequestGate`、独立对象存储和 `services.manage`。不新增另一套 pipeline 执行器，不为每种取证步骤再发明一组 Dataset 方法。

下面展示首轮取证的主图片段。`cfg`、`pack`、`counter`、模型 `gate`、输入 `planned` 和业务函数由正式入口提供；阶段 writer 在入口中显式声明。它是 API 设计样例，不是第二个执行入口。

```python
from demiflow import data
from demiflow.collect import (
    WebSession, SearchWeb, FetchDocuments, ReadDocuments,
)
from demiflow.services import SearxNGService
from demiflow.operator_llm import PromptContext
from demiflow.operator_llm.tokens import TokenBudget
from demiflow.execution.stream_lance import StreamLanceWriter

# 纯声明。这里不创建 HTTP 客户端、不联网、不启动子进程。
service = SearxNGService(
    root=workspace,
    name="web-search",
    profile=cfg["search_profile"],
    lifecycle="shared",
)
web = WebSession(
    search=service,
    object_directory=cfg["object_directory"],
    journal_path=cfg["retrieval_journal"],
    limits=cfg["web_limits"],
)
p2_budget = TokenBudget(counter, max_input=32000, max_output=8000)

# 业务声明保存位置、字段和主键；writer 实现仍是平台能力。
writer = StreamLanceWriter(
    cfg["evidence_uri"], EVIDENCE_SCHEMA, key="source_record_id",
)
# 与现有方式一样，在正式运行锁内、执行阶段初始化 writer。
writer.initialize()

stream = (
    planned
    .map(make_search_requests)
    .map_async(
        SearchWeb(web, requests="search_requests", output="search_results",
                  inner_concurrency=2),
        concurrency=8, queue_depth=8, label="search",
    )
    .map(make_fetch_requests)
    .map_async(
        FetchDocuments(web, requests="fetch_requests", output="fetch_result",
                       inner_concurrency=2),
        concurrency=8, queue_depth=8, label="fetch_parse",
    )
    .map(make_read_request)
    .map_async(
        ReadDocuments(
            web, request="read_request", output="reading",
            inner_concurrency=2,
            prompt_context=PromptContext(
                prompt=pack.prompt_definitions["assess_concept"],
                build_inputs=build_p2_inputs,
                inputs_output="prepared_p2_inputs",
                budget=p2_budget,
            ),
        ),
        concurrency=4, queue_depth=4, label="read",
    )
    .map(finish_reading)
    .batch_map(writer, max_batch=32, flush_interval=5,
               concurrency=1, queue_depth=64, label="save_evidence")
    .map(bind_p2_request)
    .map_prompt_async(
        "assess_concept", config=pack, options=p2_options,
        inputs={"payload": "payload"}, output="response",
        call_output="prompt_call", error_output="prompt_error",
        when=lambda row: row["status"] == "ready",
        max_requests=cfg["assess_max_calls"], request_gate=gate,
        token_budget=p2_budget, concurrency=4, queue_depth=4,
    )
    .map(finish_p2)
)
# 正式入口在此显式接上 P2 阶段 writer 和后续节点。
stats = stream.run_stream(resources=[web], collect_metrics=True)
```

片段中的 `EVIDENCE_SCHEMA` 保存引用、回执和状态，不保存正文。writer 像现有实现一样将当前行传给下一节点，临时正文只留在内存。`bind_p2_request` 只把 `prepared_p2_inputs['payload']` 绑定到原生调用字段，不重新组装另一份未计量的请求；原生模型节点仍执行最终 token 准入。

业务函数的职责很具体：

| 函数 | 实际做什么 | 不应包含什么 |
| --- | --- | --- |
| `make_search_requests` | 从 P1 的断言与查询生成请求，保留原名查询，绑定问题 ID；不执行的概念给空请求并保留业务状态 | HTTP、引擎 JSON 解析、服务启动、协程信号量 |
| `make_fetch_requests` | 把候选交给获取算子，声明各查询组额度和本轮总额度 | 自己遍历 URL 下载、按标题淘汰、处理 HTTP 重试 |
| `make_read_request` | 指定资料引用、问题文本、保留材料、显式补读位置和阅读额度 | 打开文档对象、计算章节范围、自己挑块裁预算 |
| `build_p2_inputs(row, reading)` | 用当前阅读结果构造完整模板变量，返回 `{'payload': ...}` | 联网、模型调用、读整表、改变业务状态或写表 |
| `finish_reading` | 把技术结果映射为 `ready/retrieval_failed/supplement_failed/context_budget` 等本业务状态 | 从网络失败推断“概念不成立” |
| `finish_p2` | 校验审定内容、已读引用、名称关系，保存业务判断 | 根据来源数自动宣布概念正确 |

`PromptContext` 只是本地请求构造与计量契约，不是新模型算子。平台有界地重新选择本轮可选材料，调用纯函数 `build_p2_inputs`，按原生消息包装计量；不会调用 P2，也不会自行删掉前轮材料、判断或失败回执。第一次适配成功的完整输入直接交给模型。不可删部分已经超额时返回明确失败。

### 2.2 补查仍用相同的平台算子

首轮 P2 后，业务节点决定是否补查，并将允许执行的 `search/fetch/read` 请求转换为同一套通用请求结构。主图明确再放一组 SearchWeb → FetchDocuments → ReadDocuments → P2 节点，最后停止，不由平台内部偷偷循环。

两轮共用 `web` 和全局模型 gate。只补读时，搜索和获取请求为空，平台回传跳过回执；无需联网，也无需启动搜索服务。首次没有疑问仍按已确认规则生成待核断言和取证请求，不能变成隐式“相信模型记忆”模式。

平台不知道哪一次叫“补查”，也不知道上限为何是 2 个查询、5 个 URL、2 份新文档、3 个补读请求。**数值与业务上限由消费者声明；精确执行额度、防止重跑重置和回报未执行项由平台负责。**

### 2.3 三类算子的输入输出契约

这些是字段级契约草案，实施时由 demiflow 提供共享类型和 Arrow 组件 schema，业务表组合引用，不再复制一套网页结构。

| 算子 | 输入内容 | 输出内容 |
| --- | --- | --- |
| `SearchWeb` | 有界查询列表；每项含调用方 `request_id`、查询文本、语言、结果上限和不解释语义的 `bindings` | 每项的技术状态、规范化候选、实际 provider/engine、原始排名、warnings、尝试回执、请求身份和复用信息 |
| `FetchDocuments` | 有序候选组、各组所需成功文档数、整行尝试和新增文档上限、已有文档引用与关联 | 原始快照引用、规范文档引用、最终 URL、媒体类型、时间、全部请求关联、获取/解析状态与额度回执 |
| `ReadDocuments` | 文档引用、问题文本及 ID、明确的块/章节定位、必须保留的已读块、材料预算 | 阅读目录、选中块引用、未读范围、无效定位和超额回执；仅内存提供完整原文块；可选地产生已适配的完整模型输入 |

每个算子输入一行、输出一行。空请求、失败和额度不足都输出回执，不返回 `None` 丢行，不用 `map_async(catch=...)` 的丢弃机制掩盖错误。未预期的程序错误应停止执行并保留已提交状态，不能被大范围 `except Exception` 变成“没有找到资料”。

`request_id` 是调用方关联身份，不等于缓存身份。平台另外计算实际执行的 `request_key`；相同 HTTP 工作可以复用，但必须保留每个概念、查询和问题的关联。`bindings` 对平台是不透明的关联标签；平台做稳定并集，不把标签解释为“已支持的事实”。

读取问题文本用于材料排序，不用于证明相关性。每个问题先跨文档比较候选块的排序，再对问题和文档分配阅读机会，不能先耗尽输入列表的第一篇文档。预算只容纳一块时优先选择排序更高的候选，不承诺每篇都一定读到；未读的文档和位置必须可见。块超额则说明未读，不从句子中间切掉否定或条件。同一块因多个问题入选，关联 ID 必须合并，不能仅保留第一次遇到的 ID。材料选择规则及其版本影响模型输入，也必须纳入可追溯配置。

### 2.4 服务管理如何归 demiflow

`SearxNGService` 是已有 `ManagedHTTPService` 和 `services.manage` 上的专用声明与适配，SearXNG 本身仍是独立进程。它不是新的调度平台，不需要额外引入工作流服务、消息队列或另一套业务部署系统。

平台管理的服务档案应包含：安装/源码修订、启动 argv、配置文件及摘要、自定义引擎及摘要、启用引擎集合、结果通道、语言策略、显式出网路由、限流、健康检查和日志位置。对业务公开稳定的档案名/引用，不能只传一个无法说明后端实际配置的 `http://127.0.0.1:8080/search`。

已有 `start/status/stop` 监督能力可以复用，但以下能力仍需补齐，不能写成现成支持：

1. 校验请求使用的档案与正在运行的配置一致，包括自定义 `demi_wikisearch`。配置不一致时明确报错，不能静默接管同端口未知服务。
2. 区分运行级 HTTP 会话与共享搜索服务。运行结束关闭本次客户端、释放使用登记；共享服务按其管理策略继续存活，不能因某个概念流程结束而误停另一个流程使用的服务。
3. 在执行器拥有的同一个事件循环中惰性绑定和关闭资源。构造配置、打印计划、空输入、全缓存命中都不因此启动服务。服务确需启动时才触发 readiness。
4. 同机多个运行共享服务时，搜索限额按共享服务协调；不能把每个 run 各自的 8 并发称为总共 8 并发。可复用本机锁与受管状态机制，不在本轮扩成跨机资源调度。
5. 分别报告 HTTP/进程可用性、引擎技术可用性与离线/抽样检索质量。HTTP 200 不证明正确召回；服务启动探活也不必每次额外跑一批语义检索。质量探针是显式诊断，不能隐含增加常规请求成本。

第三方 SearXNG 程序、平台维护的部署模板和自定义引擎要有清楚的版本来源。不是把整个第三方仓库随意复制进 demiflow 核心包，也不是让 demiflow 在运行时反向导入 `demiwtg.collect.webgate`。平台负责可复用的安装说明、资源包/适配和部署声明；业务只选择档案。

### 2.5 并发、持久化与缓存边界

| 约束 | 声明位置和执行责任 |
| --- | --- |
| 每个主图节点并发与队列深度 | 业务入口显式调用现有 `map_async` 参数，执行器实施背压 |
| 每概念查询/网页/文档读取并发 | 通用算子的 `inner_concurrency`；业务选择数值，平台实施 |
| 搜索、获取、每主机请求限额 | `WebSession` 的共享 gate；跨 run 的搜索服务限额另按服务协调 |
| 模型共享并发与三节点调用额度 | 保留原生模型 gate 和各节点持久日志预算 |
| 阶段表提交 | 业务显式声明 `StreamLanceWriter`，单 writer 小批提交，保存固定版本 |
| 请求日志、原始网页、规范文档 | 平台维护执行记录和独立对象；业务表只存引用及必要元数据 |
| 审定状态和完成检查 | 业务校验完整输入覆盖、合法业务结果和表提交成功，不由平台推断通过 |

缓存至少分清三层：搜索请求身份包含实际源档案与适配版本；网页获取身份包含会影响响应的请求/路由配置；文档解析身份包含原始快照 SHA、解析器版本和解析配置。解析器改变应允许对已有快照重新解析，不应被迫重新下载同一网页。正文时效和跨 run 历史复用保持显式策略，本次不擅自开启全库历史材料复用。

同一次运行完整复用不能重置失败尝试额度；不确定请求不能无记录地重发。WebSession 的多个算子共用同一日志所有者及进行中的请求去重；本轮不承诺多个独立进程可无锁共享同一检索日志。共享服务管理与共享业务请求日志是不同问题。

## 3. 全量职责清单：哪些移、哪些拆、哪些保留

复核范围为本轮概念流程全部生产 Python 模块、prompt 组织、阶段 schema，以及直接相关的新增/修改平台代码和旧 collect 文本搜索接入。既有 P3/P4/P5 兼容入口检查职责，不在本轮重新验证视觉分类算法。没有声称完整审计所有历史 collect 脚本或整个 demiflow 仓库。

### 3.1 业务侧

| 当前位置 | 判断与处理 |
| --- | --- |
| `audit.py::prepare_input/prepare_plan` | 保留业务。定义概念输入、材料登记、P1 的断言与查询要求，旧分类/资料不是已确认知识。 |
| `concepts.py::prepare_source` | 保留业务。来源身份、原名、别名、旧 taxonomy 的字段解释不是通用网页职责。 |
| `audit.py::finish_p1`、`concepts.py::finish_plan` | 保留业务的响应校验、查询上限和原名保留规则；调用错误类别通过平台稳定字段消费。 |
| `audit.py::queries_for` | 保留查询构造与问题绑定；查询的执行缓存身份交给平台，不能只用文本摘要代表全部请求。 |
| `audit.py::bounded` | 移入平台取证实现内部。它是有界异步处理和取消收尾，不应每个业务复制；无需单独发明面向用户的任务框架。 |
| `audit.py::Search` | 拆开。业务准备请求和业务状态；平台执行搜索、归一化结果、内部并发与回执。 |
| `audit.py::Fetch` | 拆开。候选组、轮次和业务额度由业务提供；下载并发、URL 去重、成功额度、关联合并、稳定结果顺序由平台执行。 |
| `audit.py::terms/ranges/requested_blocks` | 移入平台阅读模块。中英词项提取、未读范围、章节定位适用于其他文档消费者。不要把原名强行写进通用阅读器。 |
| `audit.py::select_material` | 拆开。业务提供问题、保留集、优先顺序和预算；平台负责块选择、上下文、文档间机会、去重关联、目录与计量。 |
| `audit.py::select_for_assessment` | 拆开。完整 P2 载荷由业务纯函数构造；有界适配完整 prompt 的机制进入平台 `PromptContext`，不得内置 `assess_concept`、轮次或概念字段。 |
| `audit.py::Read` | 拆开。对象读取、校验、隔离执行、超时、关闭属于平台；是否进入 P2 及技术失败如何影响本概念状态属于业务。 |
| `audit.py::assessment_request` | 保留业务，重构为纯输入构造函数，供最终原生调用与本地 token 适配使用同一结果。 |
| `audit.py::validate_assessment/finish_p2` | 保留业务。问题覆盖、事实与引用、名称范围、首轮保留、最后停止属于审定协议；平台提供对象/块存在性等通用校验。 |
| `audit.py::start_supplement` | 保留是否补查、已知范围、一次停止等规则；通用请求的语法、定位和额度执行复用平台组件。不能把整个补查决策迁走。 |
| `audit.py::clear_request`、`schema.py::project` 等小函数 | 保留必要的业务字段投影；不为少量通用 Python 表达式建立工具库。 |
| `schema.py` | `PLAN/QUESTION/ASSESSMENT/名称关系/分类节点` 等留业务；文档引用、技术尝试、候选、文档目录和块引用由平台维护组件 schema，业务增加 `round/claim_ids` 等字段或关联结构。 |
| `settings.py` | 业务默认预算、节点并发和一次补查上限留业务。技术选项的类型与合法值由平台各配置对象校验，业务可以进一步收紧。不能将数值配置本身误判为放错层。 |
| `concepts.py::call_json/error_status` | 保存哪些调用字段由业务决定；供应方 usage、技术异常分类由平台提供。业务不宜长期按内部异常类名字符串判断错误性质。 |
| `metrics.py::AuditMetrics` | 概念完成数、补查比例留业务；调用尝试、复用、usage、延迟归平台，避免靠最终概念行反推技术请求总数。 |
| `concepts_pipeline.py::run_audit` | 保留数据范围、图、预算绑定、模型节点、阶段 writer、表路径、schema、固定引用、覆盖检查；迁出 HTTP 生命周期、队列观测安装、原生日志用量解释等通用运行胶水。 |
| `unique/counts/verify_coverage` | 当前业务指定概念主键和必须覆盖哪些输入，保留。可复用平台基础操作，不把本业务完成规则搬成平台规则。 |
| `Progress` | 通用进度统计用平台事件；概念流程的显示措辞可留业务。不是所有 print 都必须新增平台 API。 |
| `prompts/__init__.py` 与 `tasks.yaml` | prompt、模型选择、模板变量、响应语义留业务；原生调用序列化、日志、token 准入已在平台，继续使用。 |
| `trees.py`、`markdown.py`、独立 adopt/place/tree 分支 | 分类定义、候选节点、树合法性、挂载和 Markdown 呈现留业务。P3/P4 的旧字符级限制不等于已经接入本次完整 token 协议，后续主线使用时再专项验收。 |
| notebook | 继续只做参数、正式入口调用与查看，不能承接迁出的服务脚本、HTTP 请求或平台实现。 |

### 3.2 平台侧与旧 collect 接入

| 当前位置 | 判断与处理 |
| --- | --- |
| `collect/web.py::WebClient` | 放在平台正确；当前同时混合 HTTP 和 SearXNG 响应知识，需要拆出 provider 适配、补标准算子，并修复第 4 节缺陷。 |
| `collect/documents.py` | 独立文档解析、存储、SHA 校验、版本与块定位属于平台，位置正确。正文提取质量问题也在平台修，不能交给业务额外清洗一遍。 |
| `objects/*` | 独立对象存储正确。网页正文不用 Lance 行引用定位，不恢复把正文塞进业务表的方式。 |
| `execution/request_limits.py` | gate、延迟统计和队列观测属于平台；缺的是统一接入及清楚的 run/服务作用域，不是搬回业务。 |
| `execution/isolation.py`、`processes.py` | 隔离和子进程清理属于平台；通用文档算子直接复用，不让每条业务实现 shield/cancel/drain。 |
| `execution/stream_lance.py` | 单 writer、小批提交、提交回执与不确定提交校验属于平台，位置正确。业务显式构造 writer 完全合理。 |
| `operator_llm/tokens.py` | tokenizer 档案、完整消息计数和准入属于平台。`role_content_copies` 是可配置的协议计数，不是概念业务；应绑定实际服务校准信息，不能靠模型别名猜测。 |
| `operator_llm/client.py/runtime.py/call_ref.py` | 通用消息构造、原生请求日志、共享 gate、请求预算和累计 usage 属于平台。业务不再另写供应方调用循环。 |
| `demiflow/schema.py` | 允许具体类型与 null 的 schema 校验属于通用能力，位置正确。 |
| `services/http.py/manage.py` | 已有受管 HTTP 服务能力可复用；补搜索档案和资源共用。`ModelServiceError`、模型 GPU 锁目录等通用服务中的模型专用命名应整理，但不重复造 supervisor。 |
| `data/dataset.py::run_stream` | 应提供资源集合与统一指标。当前尾部直接清理旧 `collect.net/llm` 全局变量是已有平台耦合，跟随资源生命周期改造消除，不能让所有业务继续手工补。 |
| 旧 `demiflow.collect.search` | 旧文档明确“接口归引擎、实现归消费方”，与当前新方向冲突。迁移时更新职责说明与使用者，不能只新增新类又保留相反规则。 |
| 旧 `demiwtg.collect.operators.text_engines.SearxngGeneralEngine` | HTTP 请求和 SearXNG 字段适配可迁为平台 provider；来源权威度等业务解释不随之固化到平台。 |
| 旧 `TextSearchStage` | 概念归属和相关性硬阈值不能整类搬进平台。已登记别名漏传 bug，仍需按新采集目标决定替换策略，不恢复名称命中硬淘汰。 |
| 旧 `collect/webgate` 的启停、模板、自定义引擎 | 通用服务部署、管理、插件归平台维护；逐个核对消费者后迁移，不热切正在运行的历史采集，也不先整体重写图片链路。 |

## 4. 发现的问题、证据与修复优先级

“高优先级”表示进入下一轮正式质量验收前必须解决；“中优先级”表示本次标准化实现应收口。这里的级别不沿用 P1/P2，避免与业务节点混淆。

### A01 · 高：平台只交付了底层客户端，通用算子仍由概念业务承担

位置：[audit.py](../preparation/concepts/operators/audit.py) 第 55、67、82、185、290、315 行；[concepts_pipeline.py](../preparation/concepts/concepts_pipeline.py) 第 315、377 行起。

换成另一个种子采集流程，目前仍需复制内部并发、候选获取、文档读取、阅读预算及收尾代码；这不满足“标准模块可从最开始种子下载复用”。迁移的最小闭环是三个平台算子、共享资源、文档/候选契约和服务适配，不是把 `audit.py` 改个目录。

### A02 · 高：搜索源档案缺失，结果通道处理不完整

位置：[WebClient.search](../../demiflow/demiflow/collect/web.py) 第 205–224 行；旧自定义引擎位于 `collect/webgate/searxng/searx/engines/demi_wikisearch.py`。

当前只读 `payload['results']`，会漏掉 `infoboxes` 中有效条目链接，并丢掉候选的 engine 信息。之前的临时 SearXNG 配置还遗漏了旧部署有效的 `wikisearch`。这两个问题叠加，不能归因于模型“不懂怎么搜”。

历史真实单引擎诊断已取得“银杏”“天牛”“Cerambycidae”的正确首候选及正文；本轮离线响应仅含有效 infobox 链接时，当前代码输出 `no_results`，再次确认消费端缺陷。修复应在平台 provider 和服务档案完成；信息框标题/摘要只是候选信息，不直接升级为正文证据。参考 [真实验证报告](P1_P2_PERFORMANCE.md)第 9 节。

### A03 · 高：阅读预算可能被第一篇文档占满

位置：[select_material](../preparation/concepts/operators/audit.py) 第 227–244 行。

代码先把每篇文档的块追加到同一个问题列表，再仅在问题之间轮转，因此没有实现文档之间的机会分配。离线例子中两份单块文档各自都能放入 126-token 预算，第二份直接包含 Cerambycidae 的相关内容，结果仍只读第一份 Windows 内容。

已有真实失败批次也出现银杏 48 块全部来自第一篇 Windows 文档、天牛 42 块全部来自第一篇归档文档。必须在平台阅读选择中同时考虑问题与文档，且保留条件、上下文及未读范围；这不等于让程序认证正文语义。

### A04 · 高：去重没有保全所有问题与请求关联

位置：[select_material](../preparation/concepts/operators/audit.py) 第 257–271 行，以及 `Fetch` 第 130–132 行。

有两个已复现的路径。第一，同一正文块先为 c1 入选，随后为 c2 候选时被去重跳过，`claim_ids` 只剩 c1。第二，补查搜索再次命中已经取得的 URL，代码直接以 `already_available` 跳过，没有合并新查询和 c2 的关联；文档仍标 round=1，第二轮自动选块又只遍历 round=2 文档。

这不证明模型一定答错，也不证明每次补查都遗漏正文；它证明当前元数据和自动阅读机会会丢失。修复应将“首次取得时间/轮次”和“本次请求引用了它”分开，复用对象仍合并新关联、参与适用的阅读请求。不得用强制重下载来补救关系丢失。

### A05 · 高：搜索缓存身份不包含实际源配置

位置：[WebClient.search](../../demiflow/demiflow/collect/web.py) 第 224 行。

键包含 URL、语言、query 和若干技术选项，缺少启用引擎、自定义插件、响应适配版本等实际源档案。在同一服务 URL 后面改了引擎配置，旧失败或旧候选仍可能完整复用。离线案例用同一日志和 URL 更换模拟响应，第二个客户端没有执行请求，仍返回旧 profile-a 候选。

这是一项“实际配置变化无法表达在身份中”的复现，不是声称所有跨 run 都已经串缓存。修复是平台固定有效档案摘要并校验运行服务，不是人为改 query、删 SQLite 或仅改 run 名来绕过。

### A06 · 高：畸形搜索响应会成为未归类异常

位置：[WebClient.search](../../demiflow/demiflow/collect/web.py) 第 212–222 行。

`results=[null]` 时 `item.get` 抛 `AttributeError`，没有被现有响应形状处理覆盖，可能终止整条流。离线已复现。provider 应在边界验证对象和字段，返回 `invalid_search_response` 等技术回执；不能把它伪装成零结果，也不能用捕获所有程序异常替代响应校验。错误凭据、无效服务档案等运行级错误仍应停止派发。

### A07 · 高：拦截页识别可能误伤有完整正文的公开页面

位置：[parse_document](../../demiflow/demiflow/collect/documents.py) 第 74–77 行。

当前在去除侧栏和 form 之前，只要页面含密码框且没有 article 标签就判为登录拦截。离线 `<main>` 中有完整公开正文、`<aside>` 中有登录表单的页面被返回 `login_required`。验证码检测有类似作用域问题。

应结合正文区域和实际阻断信号区分“网页有登录组件”和“正文被拦截”，不是绕过真实登录限制。此前真实 Wikipedia 下载还保留大量语言导航，正文表格目前仅展平成文本，均需平台解析样例检查。后两项是解析质量观察，没有在本轮给出所有网页的误差率。

### A08 · 中：运行资源、技术指标和服务接入仍靠业务胶水

位置：[run_audit](../preparation/concepts/concepts_pipeline.py) 第 377–398 行；[AuditMetrics](../preparation/concepts/operators/metrics.py)；[Dataset.run_stream](../../demiflow/demiflow/data/dataset.py) 第 479–505 行。

业务自己建立观测队列、按顺序 zip 节点与队列、关闭 WebClient、解读模型 usage。这些技术职责应由平台运行资源与事件汇总接管。只观察最终行可能看不到异常中止前已经发生的调用；现有累计 journal 指标提供历史总量，但不等于精确的本次调用增量。需要分别交付运行调用量、累计量和未知消耗，不能把缺 usage 当成零成本。

服务管理复用现有能力，但共享服务的配置匹配、使用登记、关闭所有权和跨 run 限流目前尚未接入新取证链。现有执行器清理旧 collect 全局对象是历史耦合，不能作为未来 WebSession 生命周期方案。

### A09 · 中：获取与解析缓存绑定，无法独立复用原始快照

位置：[WebClient.fetch](../../demiflow/demiflow/collect/web.py) 第 226–245 行。

`PARSER_VERSION` 在组合 fetch 键中，解析器变化会进入新的网络获取，虽然原始快照已经独立保存。应拆分获取与解析身份，支持对固定快照重新解析；主图仍可保留一个 `FetchDocuments` 复合算子，不需要把每个字节处理都变成主图节点。

### A10 · 中：文档协议与错误分类在业务重复维护

位置：[schema.py](../preparation/concepts/operators/schema.py) 第 20–36 行；[concepts.py::error_status](../preparation/concepts/operators/concepts.py) 第 33 行。

新增消费者不应重新猜 `attempts/document_ref/readings` 的字段。平台维护通用组件 schema 和稳定技术错误类别，业务组合自身 schema、解释业务状态。平台不得维护完整概念审定表，也不得接管原名等义关系和分类节点。

### A11 · 中：旧职责说明与新目标冲突，预算摘要也需准确标明口径

位置：[旧搜索抽象](../../demiflow/demiflow/collect/search.py) 首段、[平台 README](../../demiflow/README.md) 第 50 行附近；[run_audit](../preparation/concepts/concepts_pipeline.py) 第 395 行。

旧说明仍要求具体引擎和部署归消费者，应在新实现落地时同步更新并标明旧接口迁移范围。否则后续又会按旧说明把同类适配写回业务。

当前 `budget_ceiling` 始终列 3N，即使只运行到 plan 或显式下调调用额度。3N 可以保留为完整结构的理论上限，但必须另外列本轮启用节点及有效请求额度对应的上限，不能当作已配置预算或预估实际消耗。预算数值仍由业务选择，通用调用计量归平台。

## 5. 本轮验证证据及其限制

离线脚本：[reproduce.py](../../_demiflow/concepts_architecture_review_20261001/reproduce.py)。结果：[results.json](../../_demiflow/concepts_architecture_review_20261001/results.json)。结果同时记录三份被执行源码的 SHA256，避免后续代码改变后仍把旧复现当作当前行为。

| 离线案例 | 当前实际结果 |
| --- | --- |
| 只有有效 infobox 条目链接 | `no_results`，候选为空 |
| 候选列表含 null | 抛出未归类 `AttributeError` |
| 同地址后面的模拟源配置改变 | 第二客户端执行 0 次请求，返回旧候选 |
| 公开 main 正文加侧栏登录表单 | 误报 `login_required` |
| 两文档只有一份能进入预算 | 第一份无关文档先占预算，相关文档未读 |
| 同一块对应两个问题 | 只保存第一个问题关联 |
| 补查命中已获取 URL | 新查询与问题关联未合并，仍只有第一轮记录 |

运行命令（工作目录为工作区根目录）：

```bash
PYTHONPATH=demiwtg:demiflow env/bin/python \
  _demiflow/concepts_architecture_review_20261001/reproduce.py
```

这些是直接调用实际实现的受控复现，没有网络、模型和业务表写入；其中阅读 token 档案只用于离线比较，不能用作真实模型容量证明。本轮没有重新运行整套已有测试，也没有新的真实性能数字。真实搜索结论引用前一轮已经保存的诊断，不把合成响应称为真实搜索验证。

## 6. 收敛的实施顺序与验收

第一步，平台固定搜索服务档案和 provider 契约，接通既有受管服务能力，修复 results/infoboxes、引擎来源、错误响应与请求身份。先用小型真实查询对照确认有效源；不在业务新增“搜索失败就换别的后端”的隐藏逻辑。

第二步，迁出三个取证算子的通用部分，统一文档类型和 PromptContext，修复文档机会分配、复用关联及正文提取。业务只保留请求准备、审定状态、一次补查和模型语义协议。获取与解析可以在算子内部组合，主图始终维持概念粒度。

第三步，将 WebSession 生命周期和技术指标接入现有执行器，删除概念侧对应胶水。保留显式 writer、固定版本、覆盖检查和原生模型日志，不为这项工作新增全局状态框架。

第四步，经正式入口完成离线回归与有界真实验收，再切换新消费者。旧 collect 文本适配逐个替换并更新文档；图片下载、历史文档全库接入、机群迁移等仍按既定主线到使用时推进，不成为这次前置条件。

最终验收至少检查：

1. 一个仅提供查询种子的消费者和概念审核消费者使用同一平台搜索/获取实现，不复制 HTTP、并发和解析代码；种子消费者不需要构造概念审核或 P2 字段。
2. 七个已复现问题都有回归案例，且经正式入口检查行数、主键和状态，不能只测新 helper。
3. 配置构造、空输入、全缓存命中不启动服务；取消时关闭自己的资源，两个运行共享服务时一个结束不会停掉另一个的服务。
4. 引擎档案或解析器改变，正确失效对应层的缓存；重试不重置已占额度；明确区分 no_results、部分引擎失败、畸形响应、解析失败和业务未决。
5. 复用同一 URL/正文块时保留所有请求关联；没有新增下载也可以合理补读已有未读内容，且仍受本轮阅读额度约束。
6. 材料与完整 prompt 都计量，保留前轮事实、反证和回执；超预算明确失败；两轮之后不出现隐藏第三次模型调用或自动追加查询。
7. 阶段表只保存稳定引用及元数据，原始快照/规范文档独立存储；当前运行的对象引用可校验，表提交后才登记完成。
8. 实测使用已确定的 `malasci/gpt-6.1-sol` 与真实下载，分别汇报引擎召回、正文质量、问题覆盖、模型判断和耗时/用量；不能用“候选数正常”代替语义验收。

本文不引入新的业务分歧：概念成立与视觉任务仍分开，资料本身仍待核，旧名称和图片关系仍保留，输入范围可配置，默认取证，没有无检索开关。此次应先完成上述边界和质量缺口，再将下一轮结果称为标准化 pipeline 的质量验证。
