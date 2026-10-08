# collect 后续标准化 TODO

更新：2026-10-01（北京时间）。本文件登记 collect 的具体问题与验收要求；整体推进顺序以 [执行计划](../taxonomy-rebuild/EXECUTION_PLAN.md) 为准，现状证据见 [collect 全景分析](../docs/collect_inventory_20260930.md)。用户要求随相关模块标准化一起改造，本次仅登记，不修改旧采集实现或重跑历史数据。

## COLLECT-001：名称种子链的别名未传到网页相关性筛选

- 状态：待修复；在概念名/别名种子与文本搜索链路标准化时处理。若新消费者准备复用该链路，应先完成这一修复及验收。
- 范围：`collect.flow` 的名称种子在线文本分支；不能据此推断 QID、dump 等其他采集链路也存在相同问题。
- 证据：[种子展开](operators/concepts.py)、[调用入口](flow.py)、[评分与过滤](operators/text_engines.py)，以及 [离线复现记录](../docs/collect_inventory_20260930.md#61-正确别名命中可能被当前文本链误删)。
- 原因：`ConceptSeedStage` 用 aliases 生成查询，但生成的种子没有完整别名列表；`TextSearchStage` 从独立 `aliases_by_name` 映射取别名，而 `flow.py` 没有传入该映射。实际英文查询发出后，结果评分只使用中文原名及空别名列表，没有使用实际查询补足这些线索。
- 已复现：输入原名“天牛”、别名 `longhorn beetle`，模拟搜索返回标题 `Longhorn beetle` 的 Wikipedia 候选。真实代码中英文查询正常发出；缺别名时得分 8，低于 18 阈值，被过滤并返回 None；传入别名时同一候选得分 75。这是离线模拟网络返回的复现，尚未量化历史生产漏采。

标准化时的处理要求：

1. 明确概念身份、原名、别名线索、实际查询及查询来源的交接，避免搜索与筛选使用不同的输入语义。具体接口随标准化设计确定，不限定必须保留旧映射参数。
2. 将别名传递 bug 与“候选排序/过滤策略是否合适”分别处理。修正输入不能自动证明别名语义正确；网页命中别名也不等于概念成立。
3. 保留候选来源、选择/排除原因及失败结果，避免用无记录的 None 隐藏漏采。采集候选选择与下游证据审定的标准分别定义。
4. 复用通用执行、获取及记录机制到 demiflow；概念线索与资料用途的业务判断留在消费者，不把旧硬阈值直接固定为平台通用规则。

验收：

- 经正式入口运行隔离测试，中文原名＋英文别名的相关候选不会仅因交接丢失别名而被误删；不只单测手工传入正确别名的评分函数。
- 覆盖原名查询、别名查询、空别名、多别名、同一 URL 的多查询关联；实际查询与概念归属可追溯。
- 同时保留同名异义/无关候选案例，验证修复不把“命中名称”升级成语义正确。
- 如评分策略被替换，不要求新结果仍为 75 分；验收关注候选是否按新协议交付、原因是否可查。
- 不凭此修复宣称历史语料已恢复；历史影响评估和补采需要另定范围。


## COLLECT-002：搜索服务的可用性与结果质量需要分别验收

2026-10-01 真实概念核验测试发现，本机原搜索端口没有运行服务。使用仓内 SearXNG 源码和独立临时环境后，Google/DuckDuckGo 等分支出现验证码，Bing 对“剪刀”和“Cerambycidae”等简单查询返回了不相干主题的候选。查询参数仍保持原值，因此不能只用 HTTP 200 或候选数判断服务健康。证据在工作区 `_demiflow/concepts_live_review_20261001/search_diagnostics.json` 与 `search_candidate_diagnostics.json`；完整性能报告见 [P1→P2 真实验证](../taxonomy-rebuild/P1_P2_PERFORMANCE.md)。

后续接入或扩大文本搜索前，先固定并记录引擎、部署版本、语言、出网路由及限流配置，使用小型已知概念集合核对返回内容，区分传输健康、检索相关性与下游事实审核。对失败引擎的调整应在服务配置层留记录，不恢复“按名称命中分数硬删”的旧策略，不用自动绕过验证码掩盖服务不可用。当前通用下载与审定流程能够保留失败、拒绝无关证据；它不能修复上游搜索服务本身的召回质量。

2026-10-01 根因补充：已通过真实单引擎对照确认，临时配置漏掉旧模板中有效的 `wikisearch/demi_wikisearch`；内置 `wikipedia` 默认只交付 `infoboxes`，当前消费端只取 `results`，导致有效条目链接未被交付。补回旧引擎的隔离诊断中，“银杏”“天牛”“Cerambycidae”均取得正确首候选，且当前下载器成功取得三份相关正文。原先长银杏查询也取得正确首候选，查询长并非本次全面失败的主因。Bing 上游原始 HTML 已包含偏题结果，异常发生在结果解析之前；服务与代理之间的最终归因尚未完成。详见性能报告第 9 节。

验收补充：必须确认有效源确已启用、信息框链接是否作为候选交付、原始 query 与引擎归属可追溯。单引擎验收不要同时传 `engines` 和 `categories=general`：当前版本将二者取并集，后者会引入其他引擎。查询语言、每源速率和真实正文相关性必须实测；不能把临时诊断成功写成正式修复完成。本轮未修改旧 collect 或正式业务配置，相关修复仍待实施。

## COLLECT-003：文本搜索适配与服务管理迁入 demiflow

2026-10-01 用户明确平台承担通用算子和相关服务管理；[架构复核](../taxonomy-rebuild/DEMIFLOW_API_AND_BOUNDARY_REVIEW.md)给出 API 草案、函数级职责清单和验收。状态：待实施；本轮只做 review 与离线复现，没有迁移现役代码或服务。

- 将 SearXNG HTTP/字段适配、可复用部署模板和自定义 wikisearch 引擎纳入 demiflow 管理，复用既有 ManagedHTTPService 和 services.manage，不另造平台。
- `TextSearchStage` 的概念归属、权威度判断和名称硬筛不能随适配器整类迁移。COLLECT-001 的别名交接 bug 与相关性策略分别处理。
- 新概念流程与种子采集应使用同一套平台搜索/获取算子。平台不反向导入 demiwtg；服务档案固定引擎、插件、版本和路由，不能仅交付 URL。
- 迁移前核对实际消费者与正在运行的任务，逐个替换文本链；本项不自动扩大为图片下载、机群和全量历史重采。
- 新旧接口职责说明须同步更新，避免继续按旧“接口归平台、具体实现归消费者”的规则新增重复适配。

通用执行器、文档阅读、缓存身份与共享资源的实现项统一登记工作区 `DEMIFLOW_PLATFORM_TODO.md` 的 DF-012，不在本文件维护第二份平台实现清单。

### 2026-10-01 新消费者平台化进展

新概念取证已经使用原生 Dataset.search_web/fetch_documents/read_documents；平台维护 wikisearch 引擎、SearXNG 配置／受管服务和共享请求限额。真实种子消费者与概念消费者复用同一平台 API。详见 [实际改动与边界](../taxonomy-rebuild/P1_P2_API_INVENTORY.md)。

COLLECT-003 的“新通用能力缺失”部分已补齐，但旧 TextSearchStage、旧 webgate 部署和既有生产消费者尚未逐一切换；COLLECT-001 别名漏传仍待旧消费者标准化时修复。此处不删除旧执行入口，不把新路径修复当作旧生产链已经迁移。

## COLLECT-004：图片检索与获取的平台化（2026-10-03）

已实现新路径：统一 `Dataset.search_web` 的 candidates 保留 img_src/thumbnail_src；`Dataset.fetch_images` 负责有界获取、隔离解码、原始字节 CAS、跨 run URL/SHA 复用和持久失败回执。能力归 demiflow，概念范围、搜索词、正例数量与关系判断归 [concept_image_backfill](../preparation/concept_image_backfill/README.md) 和共用审核。

工作区旧 objects 与 collect/download/blobs 已通过同 inode 硬链接统一接入 preparation/datasets/images；保留历史引用。Bing 图片及 Wikimedia Commons 已取得线上有效线索；Google 图片返回 403。实际下载、复用和正式审核验收见 [交付记录](../docs/image_acquisition_20261003.md)。

边界：旧 TextSearchStage、ImageSearchStage 与 COS 下载生产线未逐个切换，本项不把平台新 API 的交付冒充旧全部消费者已迁移，也不删除旧账本。后续旧消费者须改用 Dataset API 并单独核对其固定来源、预算与恢复协议。没有可读 URL→SHA 的历史映射时，提供真实已知的 SHA/URI 可本地复用；不建立凭猜测生成的来源映射。
