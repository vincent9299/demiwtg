# T2I Benchmark V2

> **项目强制规范**：本 pipeline 的开发、修改、运行配置与评审必须先阅读并遵守 [《项目 Pipeline 强制规范》](../../../PIPELINE_SPEC.md)。本 README 仅补充本流程的具体约定，不替代或放宽项目规范。

`run_pipeline(config)` 从 preparation 读取独立文字和图片，可同时接入已采纳的概念审定，每概念返回一道待审题或不足原因。出题必须指定 `agent_config`，统一通过平台 `agentmap_async` 按需补读本行审定文档并继续出题。图文清洗和审核由 preparation 完成；T2I 不重复解读图文审核 JSON 或推导文章配图关系，也不对同概念多篇文章增加程序去重/判冲突。审定资料交接及其引用读取见下文。

## 两概念重新出题（2026-10-06）

Notebook 第4格优先展示全量最新题单，查看算子为
`operators/question_review_latest.py`。名单固定为原300概念 cohort@4，来源按原299题
Review、123题补跑、凯拉萨重新出题 Review 的顺序取同概念最后结果；hold 或技术失败
也保留，不按状态挑较好的旧结果。母狮的新作者 insufficient 单列，不伪造题目或审核。
当前全量为299 ready、1 insufficient；91题的最终题面与最初正式题不同。界面左侧比较
最初正式题，右侧显示最新定稿，实际审核输入仍由所选调用引用读取，两者不混同。
全局概念搜索与仅改题筛选在加载完整输入前执行；每批最多20题，默认10题，实际请求、
参考图、文档工具观察和固定清单只读本批。每次刷新固定新的表版本，翻页沿用该快照。
仅导出查看HTML，不调用模型、不改写业务表或发布新的统一题目版本；历史对比默认折叠。

`ready_positive2_reauthor_v17_review_20261006` 从原正式 300 概念 cohort@4 仅选取
“母狮无鬃”和“埃洛拉石窟凯拉萨神庙”，通过 Dataset 筛选落为独立 cohort@1。
前者原作者返回 insufficient，后者原题 Review 为 hold；本轮独立重新出题，再接固定清单
Review，不消费旧评分或模型作答，不覆盖原题或原审核结果。两题并发 2，作者与审题分别最多
2 次调用，均为 `gpt-6-astra / xhigh`；本轮不调用图像作答或探测评分模型。

作者配置 `agent_codex.yaml` 更新为 `t2i-v2-independent-materials-17`：质量门槛、例子和
题目输出 schema 沿用 v16，仅将材料读取入口改为与 Review 相同的标准 `read_documents`
回调及本行固定资源；关闭 shell 和本地看图，正例图仍由模板实际附入。上下文、会话和
事件预算与已验证的 Review 配置一致，文档读取次数及材料预算显式限制。旧无 resources 的
Codex YAML 仍可使用原文件入口，不由 pipeline 运行参数注入工具。

## 题目 Review 与固定检查清单（2026-10-05）

在出题结果后增加题目审校，正式入口仍为 `config → run_pipeline` / 同一CLI。
本阶段不接收待评作答图、探测结论或历史分数。它核实题面与考点的范围，补充必要考点，
收敛无价值的附带要求，输出完整定稿题面、考点及固定检查清单。全部保留要求列入清单；
任务正确性内部用 `is_core` 区分核心/非核心，其他题面遵循的 `is_core` 为 null。
必要展示尽量并入对应判据，避免重复拆项；质量、美感和计分权重不属于本阶段。

Prompt与agent配置唯一维护在 [question_review.yaml](prompts/question_review.yaml)，当前版本
`t2i-v2-question-review-4`。Codex `gpt-6-astra / xhigh` 通过标准 `agentmap_async` 执行。
YAML 在 `operators.read_documents.fn` 直接配置 `demiflow.collect.reading:read_documents`，并绑定
`resources: document_resources`，按需读取
本题固定文档。这个回调与 Dataset.read_documents 共用 `demiflow.collect.reading.read_documents`
的原生参数、实现和限制，没有自定义“读题／审题”业务工具，也不需要额外的 Python 注册代码。必要的补充检索仍用 Codex live Web。
该环节需要根据中间证据选择资料和停止条件，业务不实现工具循环或子 Dataset。

Dataset 按固定版本读取题面、考点、概念定义、既有文字依据和正例引用，通过标准模板发送
原题及考点，实际附入作者正例图。文档正文不预读，引用进入本行资源目录；Codex 在
read_documents 调用时才获得正文。回调按原始 document_ref 校验本题范围及 SHA，保留参数、
返回材料和错误。`shell_tool=false`、`view_image=false`，已有图片由初始模板实际附入；
原生只读沙箱设置保持。网络来源与固定快照明确区分，不能将链接清单当成已读证据。

输入不包含作答图、探测结论、旧评分或原作者思考过程。不额外关联原作者设计表和搜索历史，
`review_design_source` 仍已移除。缺失正例图、材料结构或编码预算失败记 invalid_materials，
未提交模型；文档读取失败交回模型，仍无法核实决定性事实时返回 hold。技术调用错误与
业务 hold 分开保存。审题标准和输出 schema 沿用 v3，质量／美感标准及评分计算没有修改。

已有题目的后续审核使用 `through='review'`：指定 `review_source={uri,version}`、
`sample_size`、`agent_config`、独立 `max_calls`，并发和队列均限制为1–8。
样本数显式限定0–1000；0为合法空交付。可选`review_task_ids`选择固定题目ID，必须唯一、
与sample_size等长，并全部存在于固定来源；未选择的题目不占预算。
当前全量v4配置为 `runs/ready_positive299_question_review_v4_callbacks_20261005/config.json`，
固定候选@300、299题、max_calls=299、并发8、队列1。用户审阅V1/V4对比后授权全量，
已于2026-10-05 22:40（北京时间）通过正式CLI在后台启动，模型为`gpt-6-astra/xhigh`。
原轮已于10月6日02:33中断，补跑见下节。运行及只读巡检的进程身份分别保存在各run的`process.json`、`monitor_process.json`；
实时完成数、ready/hold/技术失败及异常以`monitor_status.json`和提交表为准，启动不代表完成。
Notebook第3格当前与后台共用123题补跑配置，`RUN_QUESTION_REVIEW=False`，避免查看时重复提交。
第4格先展示123题补跑进度，再展示原299题已中断结果；每批10题，支持刷新、选批、前后批及批内逐题翻页。
下方保留按原题ID配对的V1/V4三题对比，默认上海青；V4的U形谷采用成功补测结果，
此前失败的运行名、原因与请求引用仍保留。查看格只读已有结果。

```bash
python -m benchmark.t2i.v2.t2i_v2_benchmark_pipeline \
  --config benchmark/t2i/v2/runs/ready_positive299_question_review_v4_callbacks_20261005/config.json
```

新出题流程可显式增加 `question_review={"agent_config": ".../question_review.yaml",
"max_calls": 300, "concurrency": 4, "queue_depth": 1}`：候选提交后由同一入口接后续审题，
使用 `<原run>__question_review` 的独立结果。`through='probe'` 时原探测先完成，审题随后
从固定候选绑定本题材料；已有探测针对原题，不冒充修改后定稿的作答。无需重跑作者即可用
`through='review'` 审核历史候选。作者尚有pending或技术失败时等待作者完成，
不冻结不完整范围；探测评分失败不阻止已完成的题目进入审题。续跑遇到父表重写但题目身份
完全相同时复用首次交接快照，题目变化须使用新run。关闭该配置时原出题与探测行为不变。

结果为 `question_reviews__<run>.lance`（每原题一行，含ready、hold、pending及失败）和
`reviewed_questions__<run>.lance`（只含ready定稿）。保留原题面、原考点、source_task_id、
固定来源、prompt版本、完整响应及原生调用引用。定稿附 `requirements_json` 和
`question_revision`；题面逐字改变时生成新task_id并置 `requires_new_answer=true`。
只改变判据或清单时task_id保持、question_revision变化，旧作答可复用但需重新判分。
未知问题不生成可用定稿；处理完成与可用题数分别统计。代码只做结构解析、版本/身份派生与交付，
不检查引用逐字匹配、考点覆盖顺序，也不替代模型进行事实判断。

原始候选、探测和evaluation结果保持固定历史版本。新定稿是显式交付表，现有evaluation
不会自动换表或把旧D分数当成固定清单分数；后续需显式消费定稿表与question_revision。

Notebook第4格下方对比区左侧为V1、右侧为V4，各侧显示从真实请求读取的结果版本、运行名和查看刷新时间，避免将旧输出误认为新版。
同题按`source_task_id`配对，清单按核心正确性、非核心正确性和其他题面遵循分组；
两版条目不按可能变化的编号强行配对。显示原题／定稿、完整考点与判据、修改说明、未决问题，
以及调用日志中的完整实际初始文本与附图。依据、实际输入和工具记录可展开。
回调区展示实际 read_documents 参数和返回材料；原生 Web 工具事件另列。
全部作者正例引用保留在下方。图片须与实际请求摘要匹配，不按当前模板伪造历史输入；
v3的零初始附图仍按旧日志显示。4000px浏览区支持同步搜索、选题和逐题翻页，原第1、2格保持。
对比函数只接受显式指定的2–8个不同run，右侧按传入顺序采用同题最后一条结果（包括失败），
保留被替代尝试的来源、状态、原因和请求引用；不会按成功状态挑选结果。任一侧缺题明确显示缺失。

资源边界：单题记录2MiB、完整上下文180000字符（10月6日补跑调整）；正例最多8张，沿用已有SHA校验及受限
图像读取／缩图，实际图片data URL累计最多12MiB。超限整题保留失败，不删图或截断正文。
每行最多64份固定文档、8次回调；每次材料12000字符、单次观察最多60000字符、每文档8MiB、
读取并发2、读取超时30秒；Codex会话1800秒、事件最多32768条。所有历史观察计入180000字符的总上下文限额。原生Web次数和模型内部轮数不冒充已受回调
次数限制。并发驻留和Python序列化另有开销，上述预算不等于进程RSS上限。
查看最多1000题、单行512KiB、总文字16MiB、媒体64MiB、HTML96MiB。
对比页的来源文字与媒体同样受合计预算约束，媒体按原图SHA复用预览；不截断判据或真实输入。
全量浏览先读取最多1000题的ID、概念、状态短字段，再按选定批次读取完整行、实际调用与图片。
每批默认10题、允许1–20题，各批仍执行上述文字和媒体预算；切批固定同一提交版本，
点击刷新才切到新版本，统计为全量、明细明确标注当前批次。Notebook控件需要运行中的kernel，
每次选批同时保存完整离线HTML。巡检只生成首批10题的HTML，避免每轮将299题完整输入合并。

### 失败恢复（2026-10-06）

原299题run在02:33（北京时间）因回调回复遇到`ConnectionResetError`退出，
固定审核表@251保留176个ready、74个failed，另49题没有提交结果。
74个失败包括47个app-server提前关闭、13个启动配置读取失败、6个总上下文超限、
5个事件数超限和3个900秒超时（旧业务原因字段为空，原始错误类型为TimeoutError）。
运行摘要已明确标记interrupted；原结果和调用日志保留。

补跑配置为`runs/ready_positive123_question_review_v4_recovery_20261006/config.json`，
显式选定74个失败题和49个未交付题；仍读取原候选@300，只使用原题材料。
176个成功题不进入补跑名单，新增会话最多123，目标并发8、队列1。
恢复清单、旧agent配置与原结果引用保存在同run中。补跑已于02:46（北京时间）启动，
首批8个真实请求均为gpt-6-astra/xhigh；启动状态看process.json及monitor_status.json。

保留V4正文、输出schema、模型和xhigh；总上下文60000→180000字符、
事件4096→32768、会话900→1800秒。每次材料12000、观察60000字符、
最多8次回调及16MiB输入/输出传输预算保持，不截断材料。
平台仅将Codex管道断开转换为单行技术失败，保存原始错误和已读材料，
防止连接重置作为通用OSError终止整批；文件或日志存储失败仍按原有边界抛出。
超时等空detail现在至少显示原始错误类型。没有增加自动模型重试。
15项平台回归与5项业务/布局检查通过，Notebook第3、4格只读执行通过，前两格及输出保持。
启动前实际initialize/thread-start检查通过，未发turn请求；当次初始化耗时207秒，不把进程已创建当作模型已开始作答。

### 3题真实回调验收（2026-10-05，已完成）

通过正式CLI运行 `ready_positive3_question_review_v4_callbacks_smoke_20261005`，模型仍为
`gpt-6-astra/xhigh`，并发3、最多3个Codex会话。大教堂和上海青完成；U形谷在原生读取返回后，
因完整观察25071字符超过旧24000上限而技术失败。使用日志中的原参数单独复现读取，正文材料
11771字符、完整提示38744字符，证明总上下文60000足够，问题是单次观察限额与原生返回不匹配。
现把单次观察上限对齐到60000，总上下文和每次材料12000不变，平台仍按完整输入校验、不截断。

仅对U形谷建立 `ready_positive1_question_review_v4_callbacks_retry_20261005`，并发1、最多1个会话。
补测完成，三题最新结果全部ready，题面均保持原文，可复用原作答，但后续判分应使用新固定清单。

| 题目 | 成功文档回调 | 每次返回正文块 | 实际输入正例图 | 最新结论 |
| --- | --- | --- | --- | --- |
| U形谷 | 1 | 8 | 1 | ready（补测） |
| 佛罗伦萨圣母百花大教堂 | 1 | 12 | 5 | ready |
| 上海青 | 2 | 7、5 | 5 | ready |

真实成功回调均有实际参数、正文和返回状态，模型随后完成审核；不同回调块可能重叠，不将上表相加当作唯一块总数。
[完整工具证据](runs/ready_positive1_question_review_v4_callbacks_retry_20261005/tool_call_evidence.json)
从原始会话日志提取，不是手写示例；[最终验收](runs/ready_positive1_question_review_v4_callbacks_retry_20261005/final_audit.json)
保留初测失败与补测来源、固定候选@300、真实请求/响应引用及原图摘要核验。共新增4个Codex会话，未运行299题全量。

已修复监控与查看器对失败会话的读取：从原错误日志获得partial_response，成功响应不存在时不再错误访问空response记录。
32项T2I相关测试通过，覆盖成功读取、参数纠正、读取后会话失败的展示与监控；Notebook只读刷新初测与补测结果，
前两格除目录更名外保留，完整实际输入、原图和工具正文可翻页查看。算子目录已统一为 `operators/`。

v4隔离验证：31项相关回归及2项布局／规范检查通过，使用模拟Codex协议进程验证实际题面／图片、按需回调正文、
越界文档拒绝、固定版本、精确回放、改题身份、hold隔离及回调巡检。三道真实题目材料与模板渲染预检通过，附图1／5／5张；Notebook第3、4格只读执行通过，原两格及输出保持。没有调用真实模型。旧v1实测和
v3读取失败保留为历史，不能据此宣称v4全量或真实模型质量已验收。

历史v1验证（材料注入旧协议）：相关30项隔离测试通过（审核7项、原agent入口与逐题探测23项），两条受影响的
notebook布局检查通过。新kernel只执行第3、4格，无报错，原两格内容及输出摘要保持；
Chromium验证未提交状态、隔离双题翻页/筛选、真实请求文本与核对后的单张输入图均通过。
真实第9题的材料准备和标准模板渲染通过，带5张实际作者正例及39条历史来源线索，未调用模型。
原出题和探测评分prompt的SHA与原批启动回执一致。全仓布局检查另有一项既有失败：
`preparation/concepts/operators/review.py`反向导入pipeline，本次未改动该模块。
预检与浏览器回执见本次run的`material_preflight.json`、`browser_validation.json`。
这些检查验证接线、身份/恢复和呈现，不代表真实Codex审核质量已验收。

## 全量 Review v3 启动与读取阻塞（2026-10-05）

用户授权全量299题与持续护航，固定候选@300、Codex gpt-6-astra/xhigh、并发8、队列1。
首个run `ready_positive299_question_review_v3_native_files_20261005` 在标准算子绑定阶段发现
磁盘prompt又为v2旧变量，发请求前退出。保留失败配置/日志，恢复已确认的v3后，8项审核回归通过。
重试run `ready_positive299_question_review_v3_native_files_retry_20261005` 于11:52 UTC启动，
原生请求记录确认v3、xhigh、shell/view_image/live均启用。

首批6个响应全部因 `bwrap: Failed to make / slave: Permission denied` 未能读取题目而hold，
有效完成审核为0；护航检查在总共14个已提交会话时停止本run及其8个在途子进程，未继续消耗其余题目。
返回的hold是读取阻塞结果，不代表题目质量不合格。原始调用/响应和固定结果版本保留，
`stop_receipt.json`、`interruption_audit.json`说明中断原因。主状态为question_review_interrupted、complete=false。
直接运行原生只读终端复现挂载错误，当前容器AppArmor为enforce；未修改宿主策略。
后续只读诊断再次复现同一挂载错误；没有关闭执行隔离或启动下一轮。该原生终端路径仍未恢复，诊断保存在`environment_blocker.json`；后续v4改用显式文档回调，保持暂停。

只读护航入口为`operators/question_review_monitor.py`，每30秒读取有界状态投影，新响应仅检查一次原生工具与算子回调记录，
输出`monitor_status.json`、只在状态变化时追加`monitor_events.jsonl`，每60秒内按新结果刷新HTML。
v4不再把缺少原生终端事件当成异常，按实际回调另计文档读取；无回调不自动等于审题失败。它不调用模型、不自动重试、不结束其他进程；本轮的异常停止由当前会话执行并留回执。
流水线退出后巡检退出。该轮中断记录保留在原run。当前Notebook已切换到上文已启动的v4全量配置。

## Review v3：Codex按位置自主读取（2026-10-05）

本次只调整输入交接与工具配置，保留v2的冗余交付措辞边界、审校标准和输出schema。
隔离测试通过固定版本题表→原生Codex协议→自行读本题/本地文档/图片→定稿交付的完整链路，
并核对真实调用日志仅带位置、同名恢复不新增会话、改题身份、hold隔离及旧版本固定。
本次相关31项隔离回归通过；新kernel只读执行notebook第3、4格，原第1、2格保持，查看脚本语法检查通过。
测试使用本地模拟Codex进程，不代表真实模型已完成新读取方式验收；本次没有启动真实Review。

## Review v2：排除交付措辞产生的冗余项（2026-10-05）

针对上海青实测清单中的“交付一张图像”，在“覆盖与粒度”处限定：检查清单列出单张
生成图像的内容、关系与呈现要求；“生成一张图像”“请画一幅”等请求措辞不单独列项。
图内对象数量、分栏/分镜数量及布局仍须覆盖，例如一株上海青、同图并列两个观察视角。
该边界按语义判断，不在代码中按“一张”等词语删项，也不修改题面的正常开场措辞。

版本升至`t2i-v2-question-review-2`，输出字段、质量/美感标准及计分规则保持。
核对本地文件时发现此前真实试跑已验证的原生schema显式type声明未保留，已恢复到成功
请求记录使用的定义。本轮只加载/渲染检查，没有新增模型调用。已保存的3题v1结果和
notebook真实输出不回写；上海青旧清单仍显示6个其他遵循项，新版效果须独立run验证，
不能把预计剔除后的5项冒充模型新结果。全量旧预设未启动；下一轮须按当前版本另建run。

## 3题真实 Review 试跑（2026-10-05，已完成）

用户确认prompt后授权小样本测试，并再次确认xhigh。本轮为
`ready_positive3_question_review_v1_smoke_retry_20261005`，北京时间18:17:27经同一正式CLI启动。
固定选择佛罗伦萨圣母百花大教堂、U形谷、上海青，实际作者正例图分别为5、1、5张；
`gpt-6-astra / xhigh / live`、并发3、max_calls=3，prompt正文/schema保持用户已review的v1。
只做题目审核，不生图、不重跑作答评分。Notebook第3、4格已切到该run，默认提交开关仍为False。

本次直接使用原题/考点中的引用、概念固定文档入口和实际作者正例图，允许Codex原生检索；
`review_design_source=null`，没有额外关联原作者的搜索历史。原因是共享容器当前file cache
占用很高，原生关联执行器在24GiB内存预留处等待；未降低平台资源保护或停止其他任务。
当时原设计表关联为可选能力，299题旧预设保留原配置；v3已移除此能力。小型状态摘要仅读本轮最多1000个
短状态字段，不再为控制统计启动关联执行器；业务数据仍经Dataset处理和交付。

首轮`ready_positive3_question_review_v1_smoke_20261005`的3条请求均在审核前被HTTP 400
拒绝：Codex原生output_schema中的decision常量和dimension枚举缺少显式type。仅补齐
string类型后建立上述独立重试run，prompt正文与通用response_schema逐项比对保持不变。
原失败日志、实际xhigh参数与回执保留。另修复显式筛选产生的296条范围外记录被计入miss：
先物化本轮选择，再执行审核流；回归已确认指定子集全部ready时complete为True。

新增固定ID范围测试及全部审核回归9项通过，原agent入口16项通过，含只提交指定题目、
缺失/重复ID拒绝、子集完成状态、续跑身份、改题与暂缓、材料失败、真实请求图片及工具读取。
重试3条均为ready、complete=True、miss为空，审核表@4、定稿表@1。三题题面逐字保持，
requires_new_answer均为False；旧作答可复用，但后续评分须显式使用新清单重新判分。

| 题目 | 原考点→定稿考点 | 核心正确性 | 非核心正确性 | 其他遵循 | 实际输入图 |
| --- | --- | --- | --- | --- | --- |
| U形谷 | 1→2 | 2 | 0 | 1 | 1 |
| 佛罗伦萨圣母百花大教堂 | 2→3 | 3 | 1 | 2 | 5 |
| 上海青 | 2→3 | 3 | 0 | 6 | 5 |

U形谷补充完全退冰状态并区分普通积雪；大教堂补充肋连接，覆面细节为非核心正确性，
相连屋面为普通遵循；上海青补充新鲜及未抽薹边界，写实/景别/单株等为普通遵循。
三题均存在真实Codex Web工具完成事件，原生线程回执和请求参数均确认为xhigh。
单题总耗时约358、431、533秒；该耗时包含原生客户端启动，不是纯模型计算时间。

人工阅读后的待关注项：上海青“交付一张图像”是接口层已约束的要求，可能属于冗余检查项；
本次保留模型原始输出，未未经确认改写prompt。U形谷与上海青报告本地固定快照读取失败、
改用实际打开的网页正文核实；因此本次已验证原生Web链路，不能宣称本地文件工具已成功验收。
未额外关联作者搜索历史的材料边界保持，299题全量尚未启动，旧作答/评分保持。

Notebook第4格保存完整3题结果，可翻页查看定稿、清单、实际文本、11张核对后的实际输入图
及调用记录中的模型/xhigh/live参数。新kernel刷新通过，原第1、2格及输出SHA保持；Chromium
验证3题翻页、搜索、完整输入和全部图片，错误及外部网络请求均为0。实际HTML约大小以
浏览器回执为准，无图片预算超限。

本次配置、真实请求、启动回执和日志均独立保留，完整验收见
[runs/ready_positive3_question_review_v1_smoke_retry_20261005/final_audit.json](runs/ready_positive3_question_review_v1_smoke_retry_20261005/final_audit.json)
和同目录`browser_validation.json`。这些结果只说明3题行为，不能替代全量审核质量检查。

## Notebook 全文浏览与按钮翻页（2026-10-04）

用户在 VS Code 中遇到查看格输出截断。第 2 格改为一个固定高度的 HTML 浏览区，默认每页一题；支持上一页/下一页、页码跳转、直接选概念、按题面/考点搜索、按原三类与评审状态筛选。全部概念、完整题面、考点/依据/判据、作答图、逐考点评审、失败原因及原分类来源均在同一固定快照内。只创建当前页的图文元素，长文字自然换行，不裁掉题面或用省略号代替全文。原有长查看格与输出保存在 `archive/notebook_before_click_paging_*.ipynb`。

翻页在浏览器内执行，无需重跑单元格、连接 kernel 或调用模型。第 2 格仅在结果更新时用于刷新；通过 Dataset API 读取本轮已提交固定表，另存 `runs/<run>/case_browser.html`，也可在浏览器独立打开该离线文件。如果 VS Code 的信任设置或渲染器限制内嵌脚本，使用独立 HTML 查看。第 1 格保持原配置及 `RUN_PIPELINE=False`，业务表、调用日志和 prompt 不变。

只读渲染逻辑在 `operators/case_viewer.py`，页面在同名 HTML 模板。有限快照最多 1,000 个概念，逐行读取所需列，单行文字最多 512 KiB、总文字 16 MiB；逐张使用已有受限图片读取/缩图路径，作答预览最长边 640、作者参考预览最长边 160。预览媒体最多 64 MiB、最终 HTML 最多 96 MiB，超限明确失败，不发布截断题目。预览缩图不改变作者模型输入、作答原图和原结果，页面保留原图地址及 SHA。上述限制是导出预算，不声称浏览器或 Python 的 RSS 硬上限。

实际本批已保存 300 项、299 道题及 1,708 张去重查看缩略图，读取失败 0。Chromium 验证首/末页、上一页/下一页、第一类 85 项、技术失败 18 项、有效 fail 191 项、出题不足项及最长题面/判据全文；独立 HTML 和保存后的 notebook 内嵌输出均在无 kernel 时可交互，浏览器错误与外部网络请求均为 0。配对查看的现有隔离回归 1 项通过，未新增模型调用。回执为本 run 的 `case_browser_validation.json`；此验证未操作用户的 VS Code 界面。

## 当前 notebook：300 概念本批已结束，18 项评审待补（2026-10-04）

北京时间 **09:35** 已观察到本批收尾并退出。固定 300 个概念全部返回：299 道候选题、1 个 `insufficient`（母狮无鬃）；299 张作答图全部生成。281 道评审有效，其中 pass 41、partial 36、fail 191、inconclusive 13；另有 18 项技术失败（上游 HTTP 错误 3、JSON 缺末尾右括号 5、总体与逐项结论矛盾 10）。技术失败保留原响应，不按零分或有效 fail 处理，因此正式 `complete=False`。最终 summary@4、designs@301、其余三阶段@300；正式入口的概念覆盖及题目键一致性检查通过，无未提交阶段或在途调用。详见 [最终核对回执](runs/ready_positive300_common85_codex_v16_probe_20261004/final_audit.json)。

出题共 300 次、评审 299 次，未追加模型重试；原评审预算只剩 1 次，无法覆盖 18 项失败，补评须另设有明确预算的新 run。作者与评审 prompt 摘要均与启动时一致。两路本任务管理的 Z-Image 服务已通过 demiflow 正常停止，GPU 0/1 显存均为 0 MiB；[服务收尾回执](runs/ready_positive300_common85_codex_v16_probe_20261004/service_cleanup.json) 保留进程身份与停止结果。Notebook 查看格已切至本批最终已提交结果；后台健康快照保留检查时点，不表示模型服务仍在运行。

用户授权按已确认的300项全量运行并护航。新 run `ready_positive300_common85_codex_v16_probe_20261004` 已于北京时间 **02:37:34** 从 notebook 第1格后台提交，PID 24209；正式入口仍为 `config → run_pipeline` / 同一CLI。出题、Z-Image作答、GPT-6联合评审预算各300，出题并发4、生图/评审各2、队列1。仅运行有正例图版；作者v16/xhigh/live与最新definition补充句、联合评审Malasci GPT-6-astra/xhigh均已核对。两路GPU服务8003/8004复用demiflow管理的健康实例。

启动前全量检查在其余215个概念的参考图中发现“月亮”的一张5425×5481 PNG超限，因此通过正式名单入口从该概念另8张已审核正例中改选；生成名单为同一 `cohort__ready_positive300_common100_priority_20261004.lance@4`。已核对300个名称和selection_rank全部与@3一致，只有月亮的正例列表变化，原@3保留。全部1,416条参考图关系已经过读取/缩图验证，固定来源及核验在本run `launch_contract.json` / `preflight_images.json`。随后补齐通用大PNG缩图，见下文；本次已启动进程继续使用启动时加载的实现及固定@4，不中途更换输入。

Notebook已切到本run，`RUN_PIPELINE=False`防止重复提交；第2格现使用上节的按钮分页浏览器，替代手工修改 `PAGE` 等变量后重跑的方式。旧20项notebook和保存输出在 `archive/notebook_before_full300_probe_20261004.ipynb`；旧业务表保持。第2格可在新kernel独立刷新固定名单、原三类、题面/考点/判据、实际作答图、评审和失败原因。

本run `watch_run.py` 是本次操作的只读运行观察脚本，不是业务执行入口：每60秒记录进程身份、阶段提交版本/数量、技术失败、原生调用日志计数和两路服务健康；最多12小时或进程退出时结束。输出 `health.json` 和有界 `health_history.jsonl`，notebook直接展示。无进展20分钟、服务异常和非完整退出均有明确标记；不调用模型、不写业务表、不自动重试未知付费状态。实际状态以新摘要和这些回执为准，开始或监控运行不表示全量完成。

## 300 概念名单修订：第一类目标 100（2026-10-04）

用户要求尽量纳入原标注“常见但容易错”的 case，优先替换第 3 类，并确认第一类至少保留 1 张审核正例：先纳入当前 85 个，补齐后增至 100。独立 run 为 `ready_positive300_common100_priority_20261004`，配置位于 `runs/<run>/config.json`；`through=inputs` 只提交名单，未把原 20 概念模型额度扩成 300，也没有自动重跑历史失败。

`cohort_revision` 保存原 300 名单的固定 `base_cohort`、原标注的 `case_category_sources`（每项含 uri/version/kind）、`common_limit=100` 和 `min_common_positive_images=1`。其他概念仍须至少 5 张正例。原两份标注任一有效记录归类 1 即进入优先范围，多来源分类冲突完整保留；只有类别集合恰为 `[3]` 的旧概念按纯专业类优先替换。旧概念尽量保留原 selection_rank，新概念填补空位；同级优先移出靠后的旧项。所有来源仍需通过 accepted/ready 和同一 assessment 的正例审核。新增显式 `common_allow_unassigned_taxonomy=True`：优先第一类缺少固定 taxonomy 挂载时仍纳入，抽样路径标记“未匹配固定 taxonomy”、节点 ID 留空、原审定 taxonomy 原样保留；其他概念继续要求精确匹配。此次“禁止停车黄网线”已通过概念审核且有 3 张正例，因缺挂载按此规则纳入，并在修订摘要单列，未编造新分类或放宽概念/图片审核。类别只参与选样与展示，不进入出题模型上下文。

原第一类共 169 个，均进入过概念审核：104 个身份明确且 ready，62 个 assessed/hold，3 个 supplement_failed。104 个 ready 中，按原 35 个固定图审快照有 50 个至少 5 张、35 个 1–4 张、19 个 0 张；85 个有图概念合计 472 条正例概念—图片关系，单概念 1–17 张，出题最多取 5 张，共 341 条参考图关系。同一 SHA 跨概念可能重复，关系数不冒充全局唯一图片数。新补图队列仍在处理；本次核对的新 `adopted2261_target30_audit_20261004` 已提交 reviews@1 的 16 行没有覆盖这批第一类，不把队列任务数当成已审核正例。

作者图片检查另发现“镰刀”的一张已审核 PNG 为 11785×11769，无法在解码前降采样。修订配置的 `image_exclusions` 显式记录该 SHA256 和技术原因，只从本轮候选正例池排除，原审核与对象保持；该概念另有 6 张审核正例，仍从中确定性选择 5 张。此配置最多 1,024 项，每项原因最多 1,024 字符，排除发生在图数计数和参考图选择之前。原覆盖报告的 472 条表示审核关系，不包含本轮技术可用性筛选；实际作者图以固定 cohort 为准。

Notebook 第 1 格的 `REBUILD_COHORT` 通过同一 `config → run_pipeline` 重建独立名单，默认 False；第 2 格的浏览器按原分类筛选本批名单，并在“名单、原分类与固定来源”中保留全部 169 个原第一类的审核/正例覆盖。增删项的历史查看输出仍在归档 notebook。当前 `RUN_ID` 已切至上节的300项全流程；旧20项可通过其原run只读查看。未来图审补齐需固定新来源快照并建立新的修订 run；不从活动表头自动替换当前冻结名单。

本次通过 notebook 正式入口已提交 `cohort__ready_positive300_common100_priority_20261004.lance@3`：300 个、第一类 85 个；保留旧名单 241 个，新增 59 个，移出的 59 个全部为纯第 3 类。最终原标注分布：纯 1 类 82、纯 2 类 212、纯 3 类 2、1/2 分歧 2、1/3 分歧 1、2/3 分歧 1。第一类按任一有效来源为 1 计，共 85。第 3 类纯类从 61 降至 2。原 300@1 和修订中间版本保留。

对实际选用的第一类全部 341 条参考图关系（339 个唯一 SHA）执行独立对象读取、SHA 校验、受限解码、方向校正及作者缩图，全部通过，输出最长边不超过 1,536，最大 JPEG 652,796 字节。查看格在新 kernel 独立执行通过；90 项相关定向测试通过（平台 77、V2 13）。[固定名单和图片核验](runs/ready_positive300_common100_priority_20261004/sampling_verification.json) 与 [原第一类覆盖报告](runs/ready_positive300_common100_priority_20261004/category1_audit.json) 仅作只读核验，业务来源为配置及固定 Lance 表。此次名单调整和验证没有调用模型；目标 100 尚缺 15 个有正例概念。

## Codex 单条事件限制调整（2026-10-04）

`Codex event exceeds max_message_bytes` 来自 demiflow 接收 app-server JSON-RPC 事件的本地单条 4 MiB 限额。四骑士（丢勒）的失败日志只有被截断前的初始化事件，超限事件本身未保存，因此不能确定其具体内容。按用户要求，唯一 agent YAML 现在显式 `options.codex_agent.max_message_bytes: null`，取消该独立单条限制；会话累计 stdout 16 MiB、事件数、超时和进程资源限制仍有效。流缓冲使用累计字节预算，JSON 解码前校验累计长度；不是完全取消传输保护。平台默认值保持兼容，其他 pipeline 的配置不变。

平台验证 77 项通过，含真实隔离 stdio 子进程发送 5 MiB 合法事件成功，以及取消单条限制后累计输出超限仍终止并留失败。未调用真实模型验证四骑士重跑，旧失败记录保留。出题 v16 正文、xhigh、live 和已确认的 definition 补充句保持。

## 历史：v16 固定 20 概念逐题探测（2026-10-04）

本轮 run 为 `ready_positive20_taxonomyv8_codex_v16_probe_20261004`，配置在 `runs/<run>/config.json`。直接沿用旧 `ready_positive20_taxonomyv8_codex_20261003__with_positive_images` 配置中的 `cohort_source@1`，保留同一 20 个概念、分类、原始 definition、来源与正例图；不重新抽样、不替换失败概念。新 run 使用独立候选表、阶段表和调用日志，旧批 18 题与 2 项失败保持历史。

第 1 格使用 `through='probe'`；出题、生图、评审新请求预算分别为 20。出题 Codex `gpt-6-astra/xhigh`、并发 4、live 检索；Z-Image 双端点 8003/8004 和联合评审各并发 2、队列 1。生成 seed/部署/尺寸/步数沿用既定设置。提交前明确核对 v16、xhigh、live 及“概念说明中的特征/实例/任务建议不限定考点”补充句，避免旧 prompt 被覆盖后误启动。两路 GPU 服务由 demiflow 服务管理单独启动。本机 4001 网关无需客户端密钥，notebook 只在环境缺省时补 `MODELHUB_API_KEY=anything` 以满足客户端配置要求，不覆盖已设置的值。

第 2 格可在全新 kernel 独立只读执行：从本轮摘要固定各阶段已提交版本，列出完整 20 概念的出题/生图/评审状态和原因，分别展示固定 cohort 与本轮输入引用。`PAGE/PAGE_SIZE/CONCEPT` 控制 case 分页，`LIVE_PAGE/LIVE_PAGE_SIZE` 控制已返回出题响应分页；每次重跑刷新，不调用模型、不补写结果。明细展示题面、考点、依据、判据、作答图、逐考点评审、联合分类及错误。无评分不按零分处理，后台退出不代表完成。新 kernel 只读查看和配置加载已验证；真实执行状态以本轮 `process.json`、日志和已提交阶段表为准。

本轮已于北京时间 2026-10-04 01:03:46 从 notebook 第 1 格正式后台提交（PID 46787），两路 `z_image_gpu0_full` / `z_image_gpu1_full` 已经 demiflow 管理启动并通过健康检查。新输入 @1 已提交，首批 4 个 Codex 会话已开始；这是启动记录，不代表 20 项已完成。notebook 保存真实提交回执，并将 `RUN_PIPELINE` 保持为 False 以免重复提交。相关出题原文读取与逐题探测的 13 项隔离测试通过；实际模型质量、失败项和完成状态以本轮结果为准。

按用户要求，查看输出同时带上**原概念已标注的三类**：①常见但容易错、②有显著的视觉特征、③有视觉特征但需要专业支持才了解。只读来源为 `evaluation/t2i/case_annotation` 的 7000 抽样联合分类 @1、469 历史主审复核 v2 @1，其固定 summary/result 引用保存于本轮 `case_category_sources.json`，沿用概念 notebook 的既有来源。按原概念名精确关联，共覆盖 20 概念、23 条历史标注；3 概念的双来源类别一致。进度表、出题响应预览和 case 详情显示原类别，详情保留每条原理由、来源、case ID 与原图 SHA。无关联/暂缓不强行补类，多来源不投票覆盖。该关联仅用于 notebook 输出，不进入出题或评审请求，不重标、不重启后台、不修改正在写入的阶段表；本轮评审建议类别另列。

启动后已观察到首 4 题完成真实出题→生图→评审（3 pass、1 fail），侗琵琶因固定正例图超过 2400 万像素限制记 `invalid_positive_image`，其余项仍在后台处理。这是阶段性观察，不代表整轮结束。新 kernel 已只读验证上海青的原分类、完整题面/判据、实际生成图和评审均可显示；20 概念分类与进度输出已保存到 notebook。另通过原配对查看回归 1 项，总计相关隔离测试 14 项通过。

## 出题过程中直接探测（2026-10-03）

`through='probe'` 在同一条 Dataset 流内执行：`出题 → 校验 → 保存设计/候选 → Z-Image 作答 → 保存图片引用 → GPT-6 联合评审 → 保存结果`。每行仍是一概念一题，后一概念可继续出题，已出题行立即进入后续阶段。没有逐行子 pipeline，也不等待全批出题后才启动探测。`through='author'` 保留原仅出题行为，`inputs` 只准备输入。

出题的唯一 agent 配置仍是 [agent_codex.yaml](prompts/agent_codex.yaml)，`gpt-6-astra/xhigh`（2026-10-04 按用户要求提高出题推理强度）。普通 `map_prompt_async` 的唯一完整配置是 [tasks.yaml](prompts/tasks.yaml)，新增 `review_answer`：模型 `malasci/gpt-6-astra`，正式 review 节点显式发送 `reasoning_effort=xhigh`。`tasks.yaml` 只含 `review_answer`；重复的 GLM `design_question` 及普通单次出题分支已删除。出题正文/schema 只在 `agent_codex.yaml` 中维护，不从评审文件转引。评审不调用 Codex，不继承出题上下文，也未注入网络工具。

2026-10-04 材料改为按需查阅，出题 prompt 升为 v16：初始输入只保留原始 definition 概念说明、规范名/原名，以及审定材料的来源、章节、块编号；Codex 附本地文档路径，HTTP 可通过原生算子读取。已核实事实、待核实事项、附加限定、概念类型和此前任务方向均不发送，原始审定记录完整保留用于溯源；审定原文不再展开。prompt 明确这些材料只覆盖此前审定涉及的部分内容，不是完整资料或知识清单，不限定考点；模型可按需读取本地快照，也可直接联网检索其他可靠来源，无需先读完本地材料。只有实际读过的内容才能作为已读依据。固定对象与块引用校验、原生算子回调保持；本批 article_source=None，没有独立文章正文输入，历史文章入口及结果未改写。输出 schema、模型 xhigh 设置及预算保持；本次只修改材料交付和 prompt，尚未启动新的 20 概念批次。已只读查看旧批 20 行 definition，部分仍带形态事实或教学示例；用户确认保留上游原始定义，并在 prompt 补充一句：其中的具体特征、实例或任务建议仅供理解，不限定考点或出题方向，仍应在保留身份与必要范围的前提下独立设计任务。

v16 最终定向验证 6 项通过：实际输入不带事实/缺口/附加限定及审定原文、本地路径可读、HTTP 算子和 Codex 原生读取均可继续执行、固定前缀保持、未发送的缺口变化可复用而 definition 变化产生新请求。全部使用隔离数据和模拟协议，未调用真实模型；配置加载核对确认输出 schema、xhigh、live 检索及执行预算不变。

2026-10-03 输入进一步收敛为 v15：出题只读取概念资料、依据材料和单列的正例图，模型载荷及新输入/设计/候选表移除 `references` / `references_json`。已有独立文章正文存入 `evidence_json` 并在依据材料中排版，图片统一放进 `authoring_images_json`。审定原文仍按固定文档引用读取；材料和图片都只供作者使用，作答节点只接收题面。prompt 不再解释有图/无图条件，不传 `authoring_condition`；配对标记只留在业务记录中。删除“已核实的相关内容”“尚未核实的事项”的重复字段说明，输入中的事实、条件、合法变化与未决问题仍完整保留，统一按材料使用规则核验。输出 JSON schema、模型和预算未变；候选 ID 改为绑定分开的依据与正例图字段。历史数据和调用日志不迁移、不重跑，新协议使用新运行名和目标表。

v15 定向验证：44 项通过，覆盖实际请求不含 references/实验条件标签、固定前缀、文字与图片分开交付、材料版本与图片数量、预算内读取、候选落表、逐题探测及配对只读预览。验证均为隔离数据和模拟响应，未执行真实模型调用。

2026-10-03 回写复核：agent 正文中仍残留此前已要求删除的“可考虑的出题方向”和精简前的“任务需要”长段，已按用户确认恢复：依据质量标准独立选择方向；最终任务检查只核对题面、考点和判据是否一致，不重复前文的价值与区分潜力标准。版本更新为 `t2i-v2-independent-materials-14`；五条质量门槛、禁止照着正例图出题的要求及输出 schema 保持。结构化历史记录仍可保留 task_sketch 溯源，但不进入作者上下文。已有 20 概念批次的请求、结果和配置不改写、不重跑；后续使用新 prompt 的实验须另设运行名。

清理验证使用隔离数据和模拟响应：配置、概念资料及探测 32 项通过；筛选与配对（含新内核只读 notebook）5 项复测通过；平台 HTTP/Codex 参数错误反馈修复 9 项通过。旧测试已改为核对抽样名单和显式选择顺序，不依赖 join 后物理读出顺序；配对查看 fixture 也按现行 notebook 契约提供独立 config.json。上游 concepts→P5 整链 fixture 在进入 V2 前返回 `retrieval_failed`（没有可读材料），因此未将该整链报告为通过；V2 独立的 adopted 资料交接检查通过。本次没有真实出题、生图或评审调用。

`review_answer` 将 case_annotation 的 `image_review.yaml`、`joint.yaml`、`blind.yaml` 中适用的要求合并成**每题一次联合判断**：按现有 `test_points[{point,basis,criterion}]` 核对像素证据、给出整体分数/错误诊断，同时提供知识门槛、熟悉度和概念可核对性。不会再生成另一套概念考点，不再独立盲标，不读取旧人工标签。每项为 pass/fail/inconclusive/invalid_criterion；判据自身错误导致总体 inconclusive，不能归责作答模型。总体不是按通过项数平均；关键身份/关系错误不能被普通特征抵消。inconclusive 的 -1 是无评分标记，统计时必须排除，不等同失败。

Z-Image 只收到完整 `instruction`，不接收考点答案、判据或作者正例图；评审收到该题、全部考点/依据/判据和此次作答图。联合分类依据使用概念名及全部 taxonomy 消歧，不添加题面没有确定的隐藏要求。`case_category` 沿用旧联合分类派生规则，暂缓为 null，分类不自动覆盖历史人工标签，也不自动淘汰候选。成功评审和整轮 `complete` 只代表处理完成，不表示题目或图像通过。

配置必须显式提供 `probe.revision`，服务须已部署；pipeline 不会自动启动 GPU 服务。v15 改变了出题输入协议，新协议使用独立运行名和目标表，不能把旧版出题响应当作新协议的精确复用。当前 v16 notebook 的配置与提交状态见上方记录。

```python
config(..., through='probe', probe={
    'revision': 'zimage-turbo-fullgpu-dp2-8steps-guidance1-v1',
    'endpoints': ['http://127.0.0.1:8003/v1', 'http://127.0.0.1:8004/v1'],
    'image_size': '1024x1024', 'steps': 8, 'run_seed': 20261003,
    'concurrency': 2, 'queue_depth': 1, 'max_generation_calls': 20,
    'review_concurrency': 2, 'review_queue_depth': 1, 'max_review_calls': 20,
    'review_timeout_s': 600, 'review_max_output_tokens': 8192,
})
```

部署配置见工作区 `demiwtg/docs/model_services/z_image_gpu{0,1}_full.json`。更换权重/推理配置须更改 revision。生成默认 1024²、8 步、单图字节预算 32 MiB、单请求 600 秒；解码上限 2,400 万像素。读取 HTTP 生成回包时先限制编码体积，再解码，不把超限图悄悄缩小。生成与评审分别并发 2、队列 1；评审完整文字上下文默认 60,000 字符、输出 8,192 token、超时 600 秒，不截断题目/判据。并发、队列、单图、单响应和本轮有限行数共同限制驻留载荷，但不是进程 RSS 硬保证。

预算默认各为本批概念数：`max_generation_calls` 限制本次执行的新生图 HTTP 次数，缓存命中不计数；`max_review_calls` 通过原生日志限制累计新评审请求，schema 重试为 0。生成不自动重试；缓存保存已完成的成功/失败行，避免同名执行重复请求。进程在服务收到请求、结果缓存尚未提交之间中断，无法证明远端是否已经生成，重启可能重新发送该请求；生成预算不是跨崩溃的 exactly-once 保证。显式重试已记录失败使用新 run，不暗中清缓存或扩大预算。

每个阶段通过 `save_lance(max_batch=1)` 提交后才向下游传行，最终 `run_stream` 收尾；探测只支持本轮 overwrite 快照，不支持 append。表平铺于本模块 datasets：原 `designs__<run>`、`candidates__<run>` 加 `probe_generations__<run>`、`probe_reviews__<run>`。生图和评审以 task_id 对齐；评审行的 `generation_source` 是该图片已提交的精确 uri/version。图片只存独立 ObjectRef，表和调用元信息不重复保存图片字节。图片请求实现由本模块 [generation.py](operators/generation.py) 维护，旧 case_annotation 显式导入同一实现；历史数据不移动。

空题和出题失败留在 designs，不进入生图。生图失败/预算耗尽继续交付评审表中的跳过状态；评审失败保留生成图和错误。运行结束检查概念键、各阶段题目键及数量；任何技术失败均不报 complete。中断时收尾保存已提交快照；Notebook 运行中只展示本次 writer 启动后提交的版本，防止旧表头冒充本轮结果。第二格只读显示阶段计数、题面、生成图、逐考点评审、联合分类依据与固定来源。

生成缓存身份绑定题目输入、部署、端点池、seed、步数和尺寸；评审参数/模型改变不会重画。生成响应和评审响应复用相互独立，评审使用原生 SQLite 日志 `probe_calls__<run>.sqlite`；相同请求复用，prompt/模型/参数改变生成新请求。新增验收覆盖真实 Dataset 流的阶段重叠、逐题提交、双层复用、失败留行、生成与评审预算、回包字节上限；均为隔离 HTTP fixture，不是模型质量评估。 本次原出题/配置回归 68 项、新探测用例 8 项通过；另通过共享生图的失败恢复、端点池、坏图和本地 ASGI 契约检查。Notebook 两格已独立只读执行，未提交模型调用；真实 Malasci xhigh 评审和 GPU 生成尚未运行。

## 已结束：有正例图版 20 个概念（2026-10-03）

用户在 300 概念批次启动后要求缩小范围，并明确当时有图版处理 20 个概念后暂停。原进程及其 Codex 子进程已停止，原日志与输入保持。当时 notebook 指向的配置保留在 `runs/ready_positive20_taxonomyv8_codex_20261003__with_positive_images/config.json`，仍使用同一正式入口，`cohort_source` 固定为本批 20 行名单，`authoring_variants=None`、`authoring_variant=with_positive_images`，退出后没有继续无图版或剩余概念。

为复用已经返回的结果，保留原 `inputs@1` 实际执行顺序的前 20 个概念，而非重新抽取均衡 20 个；原分类、审定与正例图绑定不变。范围及固定来源见本 run 的 `scope_reduction.json`。5 个完整响应已在续跑中确认 `reused=True`；4 次中断保留归档并在相同 20 个范围内恢复。累计会话预留预算 24 包含这 4 次中断，不表示处理 24 个概念。模型仍为 `gpt-6-astra/high`、并发 4。图片超限或其他失败按原契约记录，不换概念凑题；“侗琵琶”仍是已知图片超限项。只读查看格已适配单版状态及明细。 此批已于 北京时间 22:16 结束并停止：18 道候选题，侗琵琶为 invalid_positive_image，四骑士（丢勒）因 Codex event exceeds max_message_bytes 为 failed；complete=False。未追加运行无图版、其余概念或真实探测。

## 原 300 概念配对配置与抽样记录（已停止）

正式入口仍是 `config(...) → run_pipeline(config)`；`authoring_variants=['with_positive_images','without_positive_images']` 启用同一入口内的配对编排。CLI `--config <JSON>` 和 notebook 使用同一份完整配置。当前正式配置只指定 `agent_config=prompts/agent_codex.yaml`，平台由其解析 Codex 后端和原生工具，不注入 `read_documents`；Codex 直接读取固定文档文件并按需检索网络。模型按用户确认使用 `gpt-6-astra`、推理强度 `high`。用户于 2026-10-03 授权正式出题，已从 notebook 第 1 格后台提交同一 CLI 入口；进程与日志位置保存于本 run 的 `process.json`。Notebook 保存的 `RUN_PIPELINE=False` 防止重复提交；当前执行状态以进程、原生调用日志和结果摘要为准。 启动核验已看到首批 3 个真实 Codex 会话完成、业务日志记录 candidate，独立执行第 2 格成功显示题面与判据；这不代表整批完成或题目已经质量审核。首批还发现“侗琵琶”有图版触发既有 2,400 万像素限制，记录为 invalid_positive_image，其他任务继续；未替换选定图片或放宽解码预算。

本轮配置及全部 35 个固定图审来源在 `runs/ready_positive300_taxonomyv8_codex_ab_20261003/config.json`。新分类的 200 概念配置与输入表保留。旧分类的 Codex 配置 `runs/ready_positive200_codex_ab_20261003/config.json` 保留。原 HTTP 配置 `runs/ready_positive200_agent_ab_20261003/config.json` 及已有日志保持原样。采纳来源为 `preparation/concepts/datasets/adopted__taxonomy3315_adopted_20261003_v1.lance@1`；不用会继续变化的 assessments 表头。筛选要求 accepted、ready 及有效审定身份，并按 `(concept_id, assessment_id)` 关联图审。只有 `status=reviewed`、`aligned=True`、`combined_review.decision=match` 才计正例；同 SHA 跨批只计一次，有效复核冲突排除，旧 assessment 的图片不补足新版门槛。批次整体未完成不妨碍消费其已提交的有效单行。

本轮按用户要求改用独立 taxonomy 的固定候选分类：`sampling_taxonomy_source` 指向 `summary__taxonomy_v01_full_v8.lance@3`，由摘要读取同一轮 `nodes@3 / placements@1`，不自行取表头。候选共有 100 节点、40 个一级类别，处于 `review_incomplete / quality_passed=false`，只作为抽样分层依据，不宣称已发布或全部语义复核通过。

先保留 accepted、ready、同一 assessment 下至少 5 张审核正例图的概念，再按 `source_record_id + assessment_id` 精确关联唯一 assigned 主挂载。挂载 tree_hash 必须匹配固定节点表；无挂载、旧审定或缺少匹配的概念从抽样候选中排除并计数，绝不回退 old_taxonomy。相同键的重复挂载、未知节点、混用树版本均明确报错。节点原有的中间层挂载也有效。

固定种子 20261003 抽取 300 个：先在一级类别间轮转，再在该类别的深度 2 分类桶间轮转，桶耗尽后将名额分给其他桶。完整新路径、原节点 ID 和实际分桶保存为 sampling_taxonomy/sampling_node_id/sampling_category。此分类只影响抽样，不改上游概念审定、原 taxonomy 或出题的知识事实。树控制数据最多 4,096 节点 / 8 MiB，完整路径映射另限 8 MiB；只以批量 8 读取节点、限制预读。抽样累积器只保留短控制键，最多 4,096 个 / 8 MiB，资料与图片引用经 Dataset join 回接。序列化容量不代表 Arrow 解码或进程 RSS 上限。

本轮扩展版已提交 `cohort__ready_positive300_taxonomyv8_codex_ab_20261003.lance@1`：仍从同一批 904 个合格概念按原种子和新分类分层选择，原 200 个概念及其 selection_rank 全部保留，新增 100 个。共覆盖 25 个一级类别、58 个抽样桶，每个概念至少 5 张审核正例。完整分布及前缀保留核验见 [300 概念抽样核验](runs/ready_positive300_taxonomyv8_codex_ab_20261003/sampling_verification.json)。以上是启动前的输入核验。Notebook 已切到本批，正式后台出题已另行启动；真实调用与完成状态查看本轮日志和摘要。

此前 200 概念版本通过正式入口的 `through=inputs` 提交 `cohort__ready_positive200_taxonomyv8_codex_ab_20261003.lance@1`：908 个满足审定和正例门槛，904 个精确匹配新分类，4 个未匹配并排除。选中 200 个，覆盖 25 个一级类别、58 个抽样桶，每个概念至少 5 张审核正例。人工器物与饮食制品、建筑与人工场所、生物与生物组成、视觉艺术与图像设计各 34 个；其余类别按可用合格数量取尽。与旧名单重合 89 个、替换 111 个。固定来源、完整分布及对账见 [抽样核验](runs/ready_positive200_taxonomyv8_codex_ab_20261003/sampling_verification.json)。只完成输入阶段，未执行真实出题，notebook 的 RUN_PIPELINE 仍为 False。

不配置 sampling_taxonomy_source 的历史入口仍使用审定 taxonomy，空时回退 old_taxonomy。此前旧分类批次有 908 个图数合格概念，选中 200 个、覆盖 20 个一级类别和 111 个细分桶；这些是历史统计，不当作新分类分布。新批次的实际覆盖、未匹配数量及固定来源保存于本轮 summary 和 notebook 查看格。

两版从同一固定 `cohort__<run>.lance@version` 读取，每概念各一个独立 agent 任务，文字、原文资源和质量标准相同。有图版固定提供按 seed/concept/SHA 选择的 5 张作者正例，无图版发送零图。prompt 明确禁止照着正例图出题、把图片当作目标答案反推题面；先选概念核心考点，再设计独立任务，并区分实例细节与概念必要属性。`authoring_variant/authoring_images_json` 独立留存并参与候选 ID，作者图片和补读原文仅供出题使用。两版使用 `agent_codex.yaml` 内的同一任务正文，模型只看到实际提供的依据和图片，不再接收实验条件标签。

作者图片经独立 ObjectRef 校验 SHA，单图编码最多 32 MiB，最终发送图按 EXIF 方向等比缩至最长边 1,536、透明背景白底、JPEG 90 编码。2026-10-04 按用户要求，大尺寸正例不再仅因原图超过 2,400 万像素直接拒绝：先通过解码器 `draft` 尝试降采样，确认实际解码尺寸不超过 2,400 万像素后才加载像素。JPEG 支持这一方式；PNG等无法预降采样或降采样后仍超限时，调用平台 `resize_image` 在独立进程中缩图：源图最多1.6亿像素、编码32MiB、地址空间1.5GiB、墙钟45秒、每调用进程至多2个解码器。最终统一最长边1536，2400万仅是主进程解码支路边界，不再直接拒绝常规大PNG。损坏或超过隔离预算仍按行保留技术失败。Pillow 原有解压炸弹保护保持，不修改原对象。未超限图片沿用原处理路径，保持已完成请求的图片字节与缓存身份。这些限制约束输入字节和解码位图，不代表解码器内部缓冲或进程 RSS 硬上限。普通作答图片路径沿用原行为。

最初JPEG修复已通过定向检查；后续通用缩图的10项定向测试通过，覆盖大PNG由独立进程解码、主进程不展开超大像素、EXIF/透明白底、JPEG路径保持、源像素预算、损坏图和超时清理。真实月亮PNG（5425×5481）输出1520×1536，镰刀PNG（11785×11769）输出1536×1534，均保留原对象；核验记录在本300项run的 `large_image_resize_validation.json`。用本轮 inputs@1 中侗琵琶的真实固定输入只读执行 `prepare_request`，5 张正例均通过，原 6720×4480 JPEG 输出为 1536×1024；整个输入状态为 ready。未调用模型，已结束批次中的历史失败状态不因代码修复自动变成成功，尚未补跑。

原 HTTP 方案的 ModelHub 可用 `model_deployment_id` 固定已核对属于该模型的部署，并要求显式 `model_revision`；请求模型字段使用部署 ID，配置中的 model 保留业务模型名。部署 ID 不在 `/models` 的组名清单中，因此不做错误的组名匹配校验。当前网关同组存在部分 401 响应，此前已通过一次有界原生调用探测固定可用部署 `40318b0703b9cb3ba79f8bde8690195bbb13dc7f87368b1e80633bd39dd43152`；探测与此前失败请求均保留在有图版 journal 并计入原 800 次累计上限，旧响应不冒充新部署的精确缓存。

当前 Codex 配置每版最多 300 个新会话，两版共 600 个；并发 4、队列 1、整会话超时 600 秒、初始文字上下文 60,000 字符。每行的工具循环和上下文由 Codex 管理，原生读取与检索不经过算子回调。会话数不代表内部模型调用数；HTTP 的轮数、材料字节／字符限制及 max_output_tokens 不限制 Codex 原生工具或内部 token，平台仍执行会话超时、传输字节、事件数及进程资源保护。具体保证见平台文档。

原 HTTP 历史配置和日志保留。平台仍支持两套 runtime，但本业务只有一份现役 agent 配置，当前选择 Codex。切后端须在该完整配置中显式修改 runtime、对应模型连接和执行设置，不新增第二份业务 agent 配置。

`through='inputs'` 只提交共同名单，不调用模型；`through='author'` 依次运行两版。父 run 保存 cohort、selection 和最后的 comparisons；两个 `<run>__<variant>` 分支各自保存 inputs/designs/candidates/原生 SQLite 调用日志。每版验证交付概念键与输入完全一致；candidate 与有理由的 insufficient 才算技术完成，质量仍待审核。错误和空题均保留，不为凑数换概念或自动换模型。

Notebook 保持两格：第 1 格默认通过后台进程调用正式 CLI（与前台共用 `config → run_pipeline`），`BACKGROUND=False` 可前台等待；第 2 格可在全新 kernel 独立只读查看分类分布与候选质量状态、未匹配分类数量、两版状态、题面/考点/依据/判据、原生文件／检索事件、算子观察及实际正例图。PAGE/PAGE_SIZE 或 CONCEPT 控制已提交结果的展示。运行时另外通过平台只读 `SQLitePromptJournal` 展示每版已提交会话、已保存响应、失败和等待状态；LIVE_PAGE/LIVE_PAGE_SIZE 对已返回响应分页，显示题面、考点、依据、判据和空题原因。预览只读取日志，不代表设计表已提交；单条最多 8 MiB、每页最多 24 MiB，超限明确显示。后台日志末尾最多读取 16 KB。查看格不调用模型、不写业务表。此前 Codex 5 例的原 notebook 与保存输出移至 `archive/notebook_before_ready200_20261003.ipynb`，没有重新标记为本轮结果。

分类交接验证在 `tests/test_sampling_taxonomy.py`：固定树与挂载版本、同一审定关联、旧挂载和未分类排除、不足时明确失败。配对验证在 `tests/test_paired_authoring.py`：SHA 去重、同审定约束、复核冲突、均衡选样与不足报错；同一概念两版的实际多模态请求；缺原生 request 字段 → 平台错误反馈 → 模型修正 → 原生阅读 → 最终候选；逐轮预算状态；精确复用时零新增 HTTP。真实服务执行状态另见 notebook，不以模拟响应证明题目质量。

## 按需补读审定原文

`config(agent_config=...)` 是 agent 的唯一完整配置入口。本轮为 `prompts/agent_codex.yaml`，直接包含 `gpt-6-astra`、Codex 后端、工具、预算、出题正文和最终输出 schema。没有 `tasks: tasks.yaml` 引用，不读取普通 prompt 文件，不另留 HTTP agent 配置。

| 算子 | 唯一完整配置 | 文件开头 |
| --- | --- | --- |
| `agentmap_async` | `prompts/agent_codex.yaml` | `schema_version: demiflow_agent_v2` |
| `map_prompt` / `map_prompt_async` | `prompts/tasks.yaml` | `schema_version: demiflow_prompt_pack_v2` |

当前配对出题只走第一行。修改出题正文、schema、模型或工具，直接修改 `agent_codex.yaml` 中对应字段；`tasks.yaml` 只用于 `review_answer` 评审，对出题 agent 不生效。当前 run 的 config.json 与 notebook 只指定 agent 文件及数据范围、并发和日志位置，不重复填写模型或执行预算。平台负责解析装配，业务代码不修改模型、不单独拼 environment。

agent 文件内的 `budgets.max_requests: 300` 限制每版新会话数，`options.timeout_s: 600` 限制整会话；任务直接内联在 `tasks.design_question.template/response_schema`。节点 max_calls 只可收紧总额度。配置类型不匹配、文件引用代替内联任务、重复传入冲突模型或工具参数均在执行前报错。

HTTP/offline 和 Codex 两套 loop 仍由平台支持。原生算子回调同样保留，模型参数按公开单行 API 验证、原样执行，错误交回模型修复；agentmap 不做业务规则转换。平台契约见 [算子环境](../../../../demiflow/docs/operator_environment.md)。

入口开启 `codex_agent.shell_tool=True`，在 read-only sandbox 中读取文件；`codex_web_search` 默认 live。业务将固定文档的绝对路径、SHA256 和来源链接直接放入概念资料，不生成工具参数。Codex 接收原业务模板和最终 schema，保留默认基础指令；不注入 HTTP envelope 或算子状态，也不继承桌面聊天历史。原生工具上下文由 Codex 管理。

`max_calls` 按新会话数计，默认每概念一次；整会话受 timeout_s、输入输出字节和平台进程保护约束。内部模型轮数、token 和搜索费用没有事前硬上限。原生文件读取不受 demiflow 的文档白名单或阅读材料预算约束，读取权限由 Codex sandbox 决定。Codex agent 的 model 只包含 name，使用 Codex 自己的认证与 provider。

模型决定直接交付、补读一次或根据返回内容继续补读；程序只运行通用 loop，不预设两次 prompt 调用。行内限制统一在 agent YAML 的 budgets 中维护；节点只能进一步收紧总请求数。`operators: []` 关闭 demiflow 回调，不关闭 Codex 原生工具，未实现的名称在声明时拒绝，YAML 不支持导入或执行任意代码。HTTP/offline 所有轮次及重试共享节点 `max_calls`。缺少 agent_config 时在执行前明确报错，不回退 GLM、普通 prompt 或自动启动 Codex。

审定材料入口按 E1 等证据编号提供，并标明所属 D1 等文档资源编号；初始输入不展开所引原文。同一固定 `document_ref` 共用一个 D 编号，HTTP 回调只开放当前概念实际引用的文档。模型请求相关完整块、具体块或章节，拿到 `materials/readings/receipts` 后继续判断。读取沿用固定对象的 SHA、结构和字节检查，不重新下载网页。未读范围、文档损坏或预算不足如实返回，不能声称已核实未交付的内容。

出题后端由必填的完整 agent_config 确定；将 agent 配置改为 HTTP 后默认在线，`mode='offline'` 可显式登记离线请求。CLI 用 `--agent-config` 指定入口；notebook 保持 RUN_PIPELINE=False。下表的行内参数对应 HTTP agent YAML 中的 budgets，旧 document_read_* 参数只作为已解析配置的记录；它们不能覆盖 YAML。

| 配置 | 默认值 | 含义 |
| --- | --- | --- |
| `budgets.max_turns` | 4 | 每行模型轮数，含最终答复 |
| `budgets.max_calls_per_turn` | 2 | 每轮最多几个阅读调用 |
| `budgets.max_material_chars` | 12,000 | 每次阅读完整材料的字符预算，含材料元数据 |
| `budgets.max_document_bytes` | 8 MiB | 单份 normalized 文档读取解码前的字节上限 |
| `budgets.timeout_s` | 30 | 单次读取或选择阶段的超时秒数 |
| `max_context_chars` | 60,000 | demiflow 提供的文字；HTTP 含环境、schema 和观察，Codex 不含内部原生工具历史 |
| `max_calls` | HTTP 为概念数 × 轮数；Codex 为概念数 | 节点新请求／会话总额度；显式值优先，全部行及重试共享 |

字符预算不等于图文 token 或 RSS 上限。HTTP 阅读回调另有限制每次最多 4 份文档、观察值最多 24,000 字符等。保留完整块，超限块报告为未读；历史不悄悄删减。未交付最终结果就耗尽轮次或节点预算属于技术失败。offline 缺下一轮响应时仍记 pending；补交后同名续跑，复用已完成的模型轮次并重新核验固定文档。

designs 和 candidates 的 `authoring_context_json` 保存 demiflow 算子调用及原生阅读返回值；无回调时为 `[]`。Codex 原生文件读取／检索记录保存在 `call_json.response_ref` 指向的完整会话 journal，viewer 单独展示；不伪造阅读算子回执。`call_json.environment.turns` 保存各轮日志引用。有补读的候选将补读记录纳入 `task_id`，绑定实际依据。补读仅进入作者上下文，作答模型仍只接收题面。旧表不自动升级 schema，请使用新目标或显式迁移。

当前 agent 中的任务正文为 `t2i-v2-independent-materials-16`；正文和输出 schema 直接放在 agent 文件，配对入口也读取相同内容。概念资料渲染只发送名称、原始概念说明和依据入口，原文按需读取；事实、缺口、附加限定、类型和 task_sketch 保留在原记录用于溯源，不进入初始模型上下文。

## 原有输入、出题与交付契约

新增审定入口：`concept_audit_source={uri, version}` 消费 [概念整理 V1](../../../preparation/concepts/README.md) 明确采纳的快照，与 `concepts/screening_source` 三选一，仍要求显式 `sample_size`。只选身份明确、名称关系为原名或同义规范化、`task_status=ready` 的采纳记录；未达到数量会报错。`concept_record` 在 inputs/designs/candidates 中保留原名、规范名、定义、限定、核心事实、任务方向和审定来源，并保留身份依据、未决事项及实际引用的原文块位置；材料按原名关联，模型按规范名和范围出题。上游 task_sketch 仅保留在数据记录中溯源，不进入出题模型上下文；模型依据本轮质量标准独立选择方向，最终仍可零题。未挂载不阻断出题，taxonomy 可为空。

审定资料由本 pipeline 的 `operators/concept_context.py` 做确定性排版，只呈现原始 definition 概念说明和依据材料入口。core_facts、gaps、qualifiers、concept_kind 和 task_sketch 均保留在原记录而不发送，不调用模型重新摘要或改写。固定审定表中的 `selected` 提供 `evidence_id/document_ref/block_id`；只投影身份与核心事实实际引用的块，抽样完成后复用 `demiflow.collect.documents.read_document` 读取独立对象，提取来源、章节与块位置并核验 SHA、文档结构和块存在性。初始请求构造时同一概念内每个文档只读一次，引用原文不加入初始上下文。HTTP 算子读取及回放再次核验固定对象；Codex 自行读取文件和按需检索网络，其会话回放不重做原生工具。T2I 续跑仍先构造并核验固定审定资料；新增网络来源使用当时保存的会话证据。

新增字段由 T2I 消费侧 schema 维护，上游审定表与平台 API 不变：

| inputs/designs/candidates 中的字段 | 内容与来源 |
| --- | --- |
| `concept_record.identity_evidence_ids` | 原 `assessment.identity_evidence_ids`，说明概念身份依据 |
| `concept_record.gaps` | 原 `assessment.gaps`，完整保留未决事项 |
| `concept_record.evidence[]` | 原 `selected` 的引用子集；每项为 `evidence_id`、`document_ref={uri,sha256}`、`block_id` |

模型输入使用正文 `concept_context`，不直接发送内部 `concept_record`、表路径或审定版本。引用按身份、事实首次引用顺序稳定编号为“审定材料 E1…”；结构化记录保留编号对应的来源，初始输入附标题、链接、章节及块编号。Codex 收到本地文档路径，HTTP 入口提供算子资源，审定原文均按需查阅。它们供作者出题和核验；`basis` 使用实际读过的材料编号，保留具体来源及支持范围。新表结构不自动迁移旧候选表，使用新 run/目标。Notebook 同时展示结构化来源，以及按固定引用读取、按当前代码排版的审定资料；实际历史请求仍以调用日志为准。

候选身份绑定 `concept/question/evidence_json/authoring_images_json`，以及实际提供的完整审定上下文、补读记录和实验记录；绑定不同依据或图片的同题面可能产生不同 ID。v15 显式更新身份协议，不将旧混合材料字段补回新记录。行 `concept` 仍保存原名，不因模型载荷使用规范名而切断旧材料关联。该变化与新增字段、输入筛选和 prompt 说明属于本出题 pipeline 的业务改动，完整前后对账见 [P1→P2 改动账](../../../taxonomy-rebuild/P1_P2_API_INVENTORY.md)。

第一版不会改写原始概念或图片表，也不会自动升级旧候选表 schema；接新来源请使用新 run/目标。历史 notebook 参数与输出已归档；通用 config 的 concept_audit_source 默认仍为 None。CLI 增加 `--concept-audit-table/--concept-audit-version`。原显式名称和筛选表入口仍可运行，审定入口不再串行调用粗筛/精筛。

历史 5 例 notebook 接第二轮粗筛目标 `results__high_l3_categories_uniform1000_v1_20260927.lance@1`：共 1,000 个概念，其中 469 个 `screened/keep`、471 个 `screened/hold`、60 个技术失败。表已提交，但整个粗筛 run 的 `complete=False`；只消费已有效通过的 469 项，不修改上游状态。

`config(screening_source={uri, version}, sample_size=5, sample_seed=20260928, ...)` 在正式入口读取固定结果，过滤 `status='screened' AND decision='keep'`，按概念去重，再按 seed/name 哈希选择 5 个。顶层 keep 是上游全部模型通过的合并契约；概念名和完整 taxonomy 进入出题输入，精筛理由、候选方向及日志不进入模型输入。概念行顺序变化不影响选样，数量不足在模型调用前报错。`screening_source` 与 `concepts` 二选一；来源必须固定版本、显式填写数量，不能省略数量而意外调用全部通过概念。实际名单与 taxonomy 保存在 inputs/designs/candidates，固定来源、数量、种子及名单保存在 state.selection。历史显式 `concepts` 名称入口未提供 taxonomy 时为空列表，不按名称编造分类。

历史 5 例批次使用原 notebook 图文来源 `demiwtg/preparation/articles/datasets/articles.lance@4` / `demiwtg/preparation/images/catalog/datasets/images.lance@5`：按选中概念关联 reviewed 正文与 published/keep 图片，完整正文及最多8张真实图片进入请求；固定来源也记录在 state.sources。配置仍为 `gpt-6-astra / xhigh`（Extra high，极高）、`mode='codex'`、并发1、最多5次新 exec、每次超时900秒、按需 live 检索。模型名来自本机模型缓存，实际可用性由用户试跑验证。新 run 为 `dual_keep_codex5_criteria_v3_20260928`，候选目标为 `datasets/candidates__dual_keep_codex5_criteria_v3_20260928.lance`；前两版配置 `dual_keep_codex5_v1_20260928` / `dual_keep_codex5_materials_v2_20260928` 对应的已有表及保存输出保留，新旧题目结构不混写。

只读核对：本批相同5个概念（Spanish Sparrow、金枪鱼腹刺身、Narcissus Tazetta、灰腹角雉、叩甲）在上述原固定图文版本中均没有可用的已审核材料。接入来源不等于材料已覆盖这些概念；本批请求实际会带完整 taxonomy，references 仍为空，不改选样、不借未审数据、不把主动检索冒充 preparation 参考材料。有材料的概念会走同一图文链。需要使用后续公开版本时在 notebook 显式修改 URI/version，并使用新 run 留对照。

Notebook 第一格手工运行选样与正式出题，第二格可在新 kernel 独立只读看全部5个 case。先列固定来源、表版本、taxonomy、文字段数、图片数与候选数，再逐项显示题面、全部考点、依据与判据、状态及原因、模型/effort/usage/耗时/复用等调用信息。`CASE_CONCEPT` 可按准确名称只看一个。运行未落设计表时按已提交输入显示等待状态；不因零题或失败而隐藏 case。已有 notebook 输出保留；本次修改只做隔离测试，未运行 criteria v3 生产出题。查看旧表时，缺少的判据明确显示为“本历史版本未输出判据”，不替历史结果补造标准。

```python
CONFIG = config(
    run=RUN_DIR, concepts=CONCEPTS,
    article_source={"uri": "demiwtg/preparation/articles/datasets/articles.lance", "version": ARTICLE_VERSION},
    visual_source={"uri": "demiwtg/preparation/images/catalog/datasets/images.lance", "version": IMAGE_VERSION},
    target_uri=OUTPUT_TABLE_URI, write_mode='overwrite',
    agent_config='benchmark/t2i/v2/prompts/agent_codex.yaml',
    concurrency=4, queue_depth=1,
)
state = run_pipeline(CONFIG)
```

来源可分别为 `None`，也可均为空。没有可用材料时仍然请求模型，输入按实际内容提供；不生成 `invalid_materials` 或缺失概念状态。下游信任 preparation 的公开可用状态；存量表必须经过新版 preparation 导出才具有新的清洗保证，旧固定版本不会自动改变。

上一版 prompt `t2i-v2-independent-materials-8` 将 v7 的审定输入说明展开为当时的五部分（含此前方向，现已在 v14 移除），并区分作者依据与作答材料；第二至六节的标准、示例、顺序和响应 schema 保持不变。保留五条质量门槛与完整 taxonomy 的用途及歧义处理：分类路径帮助理解领域/义项，不是事实证明，不只按第一条路径消歧。五条规则仍为直接考察概念核心、具有值得测的核心区分潜力、差异主要来自核心内容、正确性边界可靠公平、方向自然且保住对象。出题者依据最终题目重新检查，不把粗筛通过当作必须出题。无材料是正常输入，可充分使用可靠已有知识；有影响题目成立或判分的疑问时，使用当前可用检索工具核验。有把握的稳定知识不要求逐题检索。检索事实、必要条件和实际查阅的来源链接写入原有 `basis`；作者检索资料不自动加入作答模型的 references，不虚构材料编号。

为便于服务端复用 KV cache，固定职责、输入解释、质量标准、示例和输出约定全部位于首个变量之前；末尾才绑定 `payload → concept_context → images`。JSON 使用平台稳定序列化，资料按固定字段与首次引用顺序排版，不加入运行时间、随机标识或内部来源路径。实际命中率受后端路由及缓存策略影响，本实现不宣称已测得命中率；原生调用日志对完整相同请求的响应复用是另一种机制，资料正文改变会形成不同请求。

2026-10-01 交接验收：T2I V2 测试 74 项、概念流程测试 38 项通过。覆盖固定来源、同请求复用、资料变化产生新请求、审定 ready 后返回空题、证据错误留行、完整资料预算及稳定前缀；第二至六节原文、模型配置、响应 schema 与 notebook 已保存输出逐项比对未变。执行命令（工作区根目录）：

```bash
PYTHONPATH=demiwtg:demiflow PYTHONDONTWRITEBYTECODE=1 env/bin/python -m pytest demiwtg/benchmark/t2i/v2/tests -q -p no:cacheprovider -x
PYTHONPATH=demiwtg:demiflow PYTHONDONTWRITEBYTECODE=1 env/bin/python -m pytest demiwtg/preparation/concepts/tests -q -p no:cacheprovider -x
```

只读样例来自 `assessments__quality18_optimized.lance@7` 的六角扳手：4 条事实、4 项未决事项、13 个原文块完整进入资料，资料 6,936 字符；无额外图文时完整渲染输入 16,807 字符，共享固定前缀 9,820 字符。该来源是审定结果快照，不是已采纳出题快照，本检查没有执行采纳或正式出题，也不证明新题目质量或服务端缓存命中率。

首次回归中，旧 HTTP 测试仅 mock `get/post`，未拦住平台当前使用的 `stream`，误发了真实 ModelHub 请求；发现后已中断该轮。测试现改用 `MockTransport`，并禁止 T2I 测试使用真实 HTTP transport，上述 74 项在此隔离下通过。误调用结果仅写临时测试目录，没有发布生产表，也不计作模型质量验收。

最终检查集中到 prompt 第五节“在最终的具体任务中检查题目是否成立”：知识可靠（依据支持具体判据及适用条件）、任务需要（要求属于最终题面且保有核心考察价值）、图像可判（必要展示、具体正确/错误边界、合法变化及证据不足）。三项共同落实原有五条门槛，不以二筛通过代替最终任务检查，也不另输出自检过程。必要展示条件应写入题面，核心答案留给作答模型；关系判据整体描述，不退化为元素出现清单。

未达到质量门槛时返回 `question: null` 和具体原因，包括考点太薄、核心联系不足、范围不自然或事实/判据不稳，不必等到完全无法绘制才放弃。有效空题记录为 `insufficient`，保留在 designs，不能进入 candidates；调用失败仍单列技术失败。每项 `test_points` 新增必填非空字符串 `criterion`，与 `point`、`basis` 一起通过响应校验并原样保存在 designs/candidates。缺失、空白或类型错误的判据属于无效响应；结构通过仍不代表内容已经审核。

材料数据流直接使用 `from demiflow import data`：

- 文章 `read_lance(review_status='reviewed') → 展开正文 → 按 concept 聚合`。只读 concept/content；不读 citations、illustrations 或 context_json。多篇文章、重复正文均按上游交付内容保留，暂不增加取舍策略。
- 图片 `read_lance(published_concepts) → 展开 published 且 keep 的概念关系 → 按 concept 聚合并限制数量`。按 preparation 的 image_uri 与本行 sha256 读取独立对象，不指定原始表，不匹配文章中的 image_id，不解析 review_json/observation_json。
- 配置概念列表分别 left join 文字和图片，空分支为零材料；文字在前、图片在后从 1 编号，写 inputs 表。
- `read_lance → map(prepare_request) → agentmap_async → map(check_response) → materialize → write_lance` 执行出题和响应校验，再投影有效单题写目标表。

`max_reference_images` 默认 8。按数据流顺序取前 N 张可用图片，不承诺排序；当前没有文章配图关系，不推导“必需图”。以后有明确的图文联合输入契约时再扩展优先策略。依据材料与正例图分别编号，仅供出题使用。

`max_context_chars` 默认 60,000，计算实际初始输入中的提示词、概念载荷、概念说明及材料入口，历史独立文章入口的正文仍计入；HTTP 后续算子读取继续受其材料/上下文预算约束，Codex 后续原生读取由其上下文管理。这仍是字符预算，不代表多模态 token 上限。超限记录 `needs_context_budget` 并跳过，不截断正文。审定引用缺失、ID 与对象/块不符、对象不可读、SHA 错误或块不存在时记录 `invalid_evidence`，保留该概念和具体原因、跳过模型，整轮未完成；不转为上游 `hold` 或出题 `insufficient`。预算检查通过后才读取图片：ObjectRef 校验 SHA，pixels 解码并识别 MIME，然后编码为模型输入；每次执行每张选中图片只读一次。独立发布图片引用缺失、SHA 错误、解码失败仍直接抛错。

`concurrency` 默认 1，控制同时执行的概念请求数；`queue_depth` 默认 1（None 采用平台的 concurrency）；`temperature` 默认 0。`max_calls` 在单次模式默认概念数，限制该模型节点的新请求。并发不会把多个概念拼成一个请求。出题 runtime 与请求设置由 agent YAML 决定；HTTP agent 可显式用 offline 登记请求。模型节点只传完整 agent、存储、行字段绑定、并发与收紧后的额度。

```bash
python -m benchmark.t2i.v2.t2i_v2_benchmark_pipeline --run example \
  --concept 莜面栲栳栳 \
  --article-table demiwtg/preparation/articles/datasets/articles.lance --article-version 4 \
  --visual-table demiwtg/preparation/images/catalog/datasets/images.lance --visual-version 5 \
  --agent-config benchmark/t2i/v2/prompts/agent_codex.yaml \
  --concurrency 4 --queue-depth 1
```

上例版本仅示意，实际使用新版 preparation 导出的版本。可省略两组来源参数进行无参考出题。每组 table/version 必须同时配置。

模型返回 `question: {instruction, test_points[{point,basis,criterion}]}`；无法出题时返回 `question: null` 和非空 reason。`point` 说明考什么，`basis` 说明支撑考点及判据的事实与适用条件，`criterion` 给出最终任务中的可观察正确表现及实质错误，必要时说明合法变化和无法确认的边界。判据可包含答案，但不作为作答指令提供给生成模型。原仅出题模式不新增分值或权重；可选 probe 阶段按上文进行作答和评审。

表平铺在本模块 datasets/：inputs 保存本次材料，designs 保存每概念结果及调用引用，candidates 每题一行（unreviewed），calls 保存原生请求/响应，records 保存结果状态及 append 提交记录。同名执行使用当前参数重算输入与设计表，不冻结配置或源码。相同模型请求可以复用原生日志；overwrite 每次覆盖目标，append 对同运行同目标同内容避免重复追加。候选写入成功后才登记结果状态。

Notebook 统一配置 CONFIG，后台提交前把相同参数保存到 config.json，通过 CLI `--config` 执行；前台仍使用 `await asyncio.to_thread(pipeline.run_pipeline, CONFIG)`。第一格提交，第二格独立只读展示运行进度、日志响应预览及正式题目与实际图文材料；空材料显示无参考说明。历史输出归档，模型只在 RUN_PIPELINE=True 执行第 1 格后由正式入口调用。

设计表 `designs__<run>.lance` 和最终候选表（config.target_uri）均保存 nullable `reasoning` 列，直接读取即可调试；Notebook 折叠展示该列。内容来自服务响应的 `choices[0].message.reasoning_content`（或 `reasoning`），通过模型节点的调用元信息沿当前行传到 writer，不在业务算子里回查日志。失败/截断响应中已有的 reasoning 保留在设计表；未返回时存 null。`reason` 是业务不足/错误原因，与 `reasoning` 不同；reasoning 不纳入模型题目 schema、题目 ID 或后续 prompt。

完整原始 HTTP 响应仍保存在 `calls__<run>.lance`。设计表 `call_json.response_ref` 定位固定版本的响应，使用 `RecordRef.from_dict(ref).read(DATA_ROOT)` 读取。Notebook 同时展示耗时、token 用量、finish_reason，并提供完整响应的折叠预览；call_json 本身不重复保存 reasoning 正文。旧固定版本不会新增列，新执行写出的表才有此字段；旧调用日志若包含 reasoning，复用响应时可自动提取并写入新输出。向旧 schema 表 append 前需先统一 schema 或使用新目标表。

旧普通 HTTP 单次出题及直接 `codex exec` 出题分支已退役；旧请求、结果、冻结配置和 notebook 输出仍保留，但不再从正式入口恢复这些旧执行方式。现役 Codex/HTTP 两种后端都走 `agentmap_async`，由同一 agent 文件的 runtime 选择；平台本身的普通 `map_prompt_*` 能力保持，V2 用它做评审。历史单次调用的缓存不会冒充 agent 请求的精确复用。

## 材料覆盖与实验选样

`reference_selection_config(...) → run_reference_selection(config)` 已从公共 preparation 移到本模块；算子在 `operators/reference_selection.py`。它统计通过概念的可用候选图、已审核图文，并按材料档位、taxonomy 分类与固定种子选样，无模型调用，不更新公共表。新输出在本模块 `datasets/reference_{coverage,concepts}__<run>.lance`。

历史 `t2i_dual_keep_reference200_v1_20260928` 两张 @1 表仍在 `preparation/datasets`，CSV 仍在 `preparation/images/archive`。当前公共审核直接处理全部 469；无需先选 200。审核完成后由下游按本轮结果决定名单，原 5-case 出题记录已归档；当前配对实验使用上文的新来源和独立 run。
