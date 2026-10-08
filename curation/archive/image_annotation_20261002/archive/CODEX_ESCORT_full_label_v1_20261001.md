# CODEX 护航交接：公共图片标注全量运行 full_label_v1_20260928

> **续跑补充：2026-10-01 16:46 北京时间，用户已授权继续标注，新 run `neutral_images_v2_20261001` 已启动。**
> 原 full_label 进程保持停止，原 journal 不修改；新 run 使用其副本及原 ready 名单，仅运行图片中性描述。用户随后明确正式运行应合并公共表，现已切换 through=export、publish_policy=valid_rows，继续同一 journal，自动合并有效 descriptions/image_scores，失败行保留；不启动概念评分、失败清扫或旧护航安排。现役配置、日志和恢复方式以 annotation README 及新 run 的 config.json/process.json 为准。
>
> **原 run 暂停记录：2026-10-01 11:38 北京时间按用户指令暂停，等待 annotation／review 职责边界协调。**
> 主进程与本次 vLLM 服务均已退出，双卡显存已释放。保留原 run、journal、日志、已提交表及未提交文件。
> **下文原有定时护航、故障自动恢复、全量后续评分和 v3 清扫安排均暂停；不得直接执行旧启动命令。**
> 本次暂停与依赖盘点见 §9；该节覆盖下方旧快照和执行安排。共享业务代码、prompt、公共表、demiflow 均未因本次协调修改。

日期：2026-10-01。写于 GLM 会话；本文只交出**运行护航职责**，代码与契约已交付并冻结（见同目录 `GLM_HANDOFF_image_annotation_20260928.md` 与 `IMAGE_ANNOTATION_PIPELINE_SPEC_20260928.md`）。

## 0. 一句话任务

`preparation/images/annotation/` 的全量打标 run `full_label_v1_20260928` 正在双 H100 上跑：**图片级 1,306,045 张 + 关系级 1,791,771 条**，全部完成后自动部分列 merge 公共表 `datasets/images.lance`。你的职责：周期巡检、故障恢复、GPU 协调；**不改协议、不改平台、不动别人任务**。

## 1. 快照（2026-10-01 03:26 UTC，接手时重新核对）

- 进度：图片级 464,750 / 1,306,045（**35.6%**），失败 19,411（**4.18%**，稳定已知问题见 §5）。
- 吞吐：~200–216 张/分钟（DP=2 独占满速）；GPU 双卡 ~91–100%、各 ~75GB。
- journal：`runs/full_label_v1_20260928/model_calls.sqlite`，6.0 GB（所有已付费响应，中断零重复付费的唯一凭据）。
- 公共表 `datasets/images.lance` 当前 @16：smoke 已提交四列新 schema（@13 加列、@14 首次数据）；**本 run 尚未提交**（图片级阶段未完成，设计如此）。
- 进程：nohup 的 `full_label_v1_20260928.launch.py`（python，非 bash 包装行）+ 6 个 vLLM 进程。

## 2. 关键路径

| 项 | 路径 |
| --- | --- |
| 启动脚本（参数唯一来源） | `demiwtg/preparation/images/annotation/runs/full_label_v1_20260928.launch.py` |
| 运行日志 | 同目录 `full_label_v1_20260928.log` |
| journal / vLLM 服务日志 | 同目录 `model_calls.sqlite`、`image_vllm.log`、`concept_vllm.log` |
| 阶段表 | `demiwtg/preparation/images/annotation/datasets/{image_inputs,image_results,concept_results,patch,summary}__full_label_v1_20260928.lance`（本 run 目前只有 image_inputs 已写） |
| 正式入口/代码 | `demiwtg/preparation/images/annotation/image_annotation_pipeline.py`（`config → run_pipeline`） |
| 只读查看 | notebook 第三/四格或 `operaters/preview.py` |

## 3. 巡检例程（建议每 30 分钟）

1. 进程：`ps aux | grep full_label_v1_20260928.launch.py`（排除 bash 行；python 行消失=故障）。
2. 日志：`tail -30 .../full_label_v1_20260928.log`。健康=已处理递增、失败/已处理 ≤5%、阶段提交行正常。
3. GPU：`nvidia-smi --query-gpu=index,utilization.gpu,memory.used --format=csv`。健康=双卡高占用（DP2 各 ~75GB）。
4. 磁盘：journal 每 10 万请求约 +2.5GB，`df -h /yzp`（余量数十 TB，无近忧）。

**正常现象，勿误判**：
- 重启后前 ~46 万条会以每分钟数千张的速度"回放"（journal 命中，不加载模型）——不是异常加速，回放完才加载 vLLM。
- 失败率 ~4% 恒定：已知模型偶发把 objects 写成裸字符串，**计划跑完后用 v3 清扫（§6），中途严禁改协议**。
- 日志无输出几分钟：模型阶段长请求正常；看 journal mtime 是否在动。

## 4. 故障处理

**进程死了**：
1. 读日志尾部定位。区分：业务代码错（traceback 在我们模块）→ 修复代码；模型服务错（vLLM 崩）→ 直接重启；环境（OOM/被杀）→ 处理环境。
2. 残留 vLLM 要手动清：`ps aux | grep -iE vllm`，杀掉 `vllm.entrypoints.cli.main` 那个 PID（子进程随之退出），确认 `nvidia-smi` 显存归零再重启。
3. 同 run 名重启（**唯一正确方式**，journal 零重复付费）：
   ```bash
   cd /yzp/zhaozy/yangzepeng/0905/demiwtg/preparation/images/annotation/runs
   nohup /yzp/zhaozy/yangzepeng/0905/env/bin/python full_label_v1_20260928.launch.py > full_label_v1_20260928.log 2>&1 &
   ```

**陷阱**：
- `pkill -f "full_label..."` 会自匹配你自己的命令行把命令链杀断；用 `ps` 找准 PID 再 `kill`。
- 启动脚本在启动瞬间固定 `image_source` 版本（当前写法取最新 head）；公共表若被其他 pipeline 推进新版本，重启后范围按新版本重算——历史上 @14→@16 已验证无影响（skip 858,626 / ready 1,306,045 不变）。若数字大变，先停下报告用户。

**GPU 协调（用户已建立的惯例）**：
- 用户要求让卡给其他对话时：杀 python 主进程 + 手动清 vLLM（同上），journal 保一切；恢复时同 run 名重启。
- 若要与其他程序共存（对方占卡）：把启动脚本 SERVICE 改 `'tensor_parallel_size': 2, 'data_parallel_size': 1, 'gpu_memory_utilization': 0.60` 再重启（每卡预算 48GB；对方 >26GB/卡 时 DP2 放不下）。服务配置**不进请求身份**，随意切换不损复用。独占时用 DP=2/0.92（当前值）。

## 5. 已知问题（不要在中途"修"）

- **~4% 图片级失败**：模型把 `objects` 写成裸字符串（如 `"独角"`），schema 校验拒绝。v2 prompt + schema_retries=1 已从 7.7% 压到 4%。**中途改 prompt/参数会换请求身份，导致全部已完成响应作废重付费——禁止**。
- **demiflow 排序进程池在受限 shell 崩**（BrokenProcessPool）：业务侧已全面规避（无大流 join/sort）；平台侧建议已登记 `/yzp/DEMIFLOW_PLATFORM_TODO.md`（DF-0XX，待用户 review，勿实施）。
- 布局测试 `test_every_pipeline_is_registered_for_layout_checks` 因他人未跟踪的 `benchmark/t2i/zimage_probe` 失败——与本模块无关，勿动。

## 6. 跑完之后（自动 + 建议）

**自动发生**（无需干预）：图片级 materialize → 写 `image_results` → 概念级（模型重新加载一次，1,791,771 条，输出更短、预计更快）→ 写 `concept_results` → patch 聚合 → 锁内对公共表加列核对 + 部分列 merge（幂等，缺 catalog SHA 会显式报错）→ 写 summary。**成功判据：日志 `committed=True` 且 summary 表 `committed` 为 true、给出目标版本号；进程存活≠成功。**

**建议的 v3 失败清扫**（跑完并确认提交后执行）：
1. 从 `image_results__full_label_v1_20260928.lance` 取 `status != 'done'` 的 SHA 列表（预计 ~5.5 万）。
2. `prompts/tasks.yaml` 的 describe prompt 升 v3（保留 v2 的对象结构要求，可再加一句强约束；`schema_retries: 2`）→ 新协议新身份，失败行重新请求（旧成功行不在范围内，零重复付费；公共表两版本记录并存，规范允许）。
3. 新 run（如 `sweep_v3_<date>`）+ `image_shas=<失败列表>` + `annotation_parts='all'`，概念级会自动只处理这些图的关系。
4. 跑 `preparation/images/annotation/tests/`（22 项）回归后执行。

## 7. 红线（重复一遍最重要的）

1. 不改 `prompts/tasks.yaml` 协议、模型、预处理参数（max_edge/JPEG 质量）、`schema_retries`——任何一项都会换身份、全体重付费。
2. 不改 demiflow；平台问题写 `/yzp/DEMIFLOW_PLATFORM_TODO.md`。
3. 不手动写公共表；不用别的 run 名；不删 journal。
4. 不停其他对话的任务；让卡/回卡按 §4 的惯例，由用户指示。
5. Python 环境：`/yzp/zhaozy/yangzepeng/0905/env/bin/python`。

## 8. 交接给 Codex 的任务消息（可直接用）

> 请读取 `/yzp/zhaozy/yangzepeng/0905/demiwtg/preparation/images/annotation/archive/CODEX_ESCORT_full_label_v1_20261001.md`，接手 full_label_v1_20260928 的持续护航：每 30 分钟按 §3 巡检，故障按 §4 恢复，遵守 §7 红线；图片级完成后盯概念级与公共 merge 的 committed 状态并向用户汇报；全部提交后可按 §6 建议 v3 清扫（需用户确认）。GLM 的护航定时已全部撤销，运行本身与任何会话解耦（nohup 独立进程）。

## 9. 2026-10-01 暂停记录与职责协调盘点

### 9.1 已执行的暂停与落盘状态

用户要求配合 benchmark 规划会话拆分上游通用证据与下游用途判断，先暂停、保全、盘点，共享代码修改待协调后进行。已读取规划会话 `01a0f387-2b76-7d42-86be-a6fbbc285f8d` 的最近讨论。本会话未建立定时护航或自动恢复。

- 对已核验命令行的主进程 PID 9614 先发送 SIGINT，随后发送 SIGTERM 完成退出；本次服务 PID 37032 及其子进程已退出。未使用宽泛 pkill，未停止其他任务。暂停后 GPU 0/1 均为 0 MiB、0% 利用率。
- 停止前最后一个按 50 行输出的日志检查点：图片级已处理 **466,700 / 1,306,045（35.73%）**，失败 **19,492（4.18%）**。这不是逐条终态精确计数，也不能与 journal 的请求次数直接比较；后者包括 schema 重试和历史恢复。
- journal 为 `runs/full_label_v1_20260928/model_calls.sqlite`，**6,449,209,344 字节**；原路径、leases、日志和启动脚本保留。通过 `mode=ro`、`query_only=ON` 核查：现有506,981条请求记录，其中 **506,917条完整响应、64条无响应预留**，无 error_json；累计 request_count=507,166，另有185条历史 recovery_events。完整响应包含可能不符合业务 schema 的原始回复，不能当作有效图片数。此次未修改或重排这些记录。共享存储逐页扫描较慢，改对停止后复制到临时内存文件系统的快照执行只读 `PRAGMA quick_check`，返回 **ok**；临时副本已删除，原 journal 大小和 mtime 均保持不变。
- 已提交的本 run 阶段只有 `image_inputs__full_label_v1_20260928.lance@7`，2,164,671 行，其中 ready 1,306,045、skipped_annotated 858,626。
- 退出期间日志出现 image_results writer 的建表提示；该目录仅有 `data/`，没有 `_versions`，`lance.dataset` 明确报告无数据集。**不是已提交结果表**，未删未改；不能据目录存在宣称阶段完成。
- 本 run 没有已提交的 image_results、concept_results、patch、summary；概念评分未启动。公共 `datasets/images.lance` 实查仍为 **@16，2,164,671 行**，没有本次全量提交。
- 已完成模型调用的持久成果在 SQLite 完整响应中，需按原输入和协议回放才能重建业务阶段表；不能宣称 46.67 万行已经进入公共表。

### 9.2 恢复约束（仅说明，当前不执行）

1. **不要直接重跑现有 launch.py**：当前默认 `annotation_parts='all'`，图片完成后会自动进入概念评分与公共 merge；而且启动时取公共表最新 head，会随其他会话写入改变范围。
2. 后续如协调决定继续上游中性描述，可利用现有 `annotation_parts='image'`，沿用原 run/journal、图片来源 **images@16**、原 prompt/模型/预处理/端点及生成参数。该模式本身仍会在图片阶段结束后写两列公共结果，不是“只回放不发布”模式；是否完成和发布须在协调方案中明确。
3. 保留日志中的语义身份：`image-c810de7eae83ce59f62f6d49`、`concept-bb95bda6475dba260947d362`。启动脚本 SHA256 为 `d7186919d28e9758a33c841d3bfd916d68c3938add88c865dc85dd2f91c7797a`；当前 tasks.yaml SHA256 为 `6316a8e3b9478305b219a136cb6fc1d0dbd1b2e981fe38afd18de043c7202322`。
4. 只对**已有完整响应且实际请求身份一致**的请求承诺缓存复用。SQLite 对“已预留、无完整响应”的请求抛 `UncertainPromptCall`，不自动重发；后续若需重试，必须另行核对具体请求并使用原生显式恢复机制。此次未 requeue、未删 reservation、未清 journal。
5. 未提交的 image_results 文件保持原样；恢复前先在隔离环境验证重建、取消行为、范围完整性和零新请求的回放。当前不启动离线导出、清扫、后续评分或自动恢复。

### 9.3 现有代码、字段和实际消费者

以下代码路径相对 `demiwtg/`；表路径明确区分工作区公共表与模块历史阶段。

| 当前生产者／维护处 | 当前字段或行为 | 已核查依赖与建议边界 |
| --- | --- | --- |
| `preparation/images/catalog/image_catalog_pipeline.py`、`catalog/operaters/schema.py` | 公共图片 schema；SHA、image_uri、source_refs、concepts、尺寸、availability、byte_size、generation_origin、dimension_* | 上游保留资产事实及来源证据；catalog 只按列更新。`concepts` 当前还会由历史匹配／审核关系合并扩展，不能把它整体解释为“已审定关系”或纯 QID 来源声明。 |
| `annotation/image_annotation_pipeline.py` 的 `describe_image` | `descriptions` 与 `image_scores.richness`，共用 annotation_id；描述只看实际图 | 上游保留中性描述、可见物体、文字、观察限制和带协议的可复用证据。richness 是模型按规则给出的通用评分，不是训练／评测合格判据。 |
| 同入口的 `score_concept` | `concept_matches`；`concept_scores` 内 kb_match、identity、focus、quality，绑定图片评分与知识摘要 | 建议迁到下游有明确概念定义和用途的判断阶段。旧公式 `0.4kb+0.4focus+0.2richness` 只是历史规则，不自动成为新适用性分数。 |
| `review/image_review_pipeline.py`、`review/operaters/images.py` | 分批初审、独立复审、范围过滤、逐图材料、知识汇总、export；写 `concept_assessments` 并派生 `published_concepts/release_ids`，也维护基础引用和关系并集 | 概念匹配、限定条件、材料发布策略归下游。旧 assessment 身份为 release＋concept＋SHA，已有 release 范围，但没有显式的新审定概念定义版本／训练评测用途契约。 |
| `review/operaters/images.py::ReuseImageAnnotations` | 读取固定公共表版本的 descriptions，可按 config_id 选取；多个有效描述未指定版本时拒绝任取 | review 和 articles 都依赖它；应作为上游证据读取接口保留清晰归属，不能随 review 整目录搬走或改成无条件取最新。 |
| `preparation/articles/articles_pipeline.py` 及 inputs/article/routing/publication | 调用 `review_image_batches`，导入读图、编码、身份、复用和 assessment 转换；按 published_concepts／release_ids 读审核材料 | review 与 articles 有双向代码依赖（review 又使用 articles 的 runfiles／结果结构），拆分要同时梳理。通用 I/O/执行可归 demiflow；文章用途与图片用途判断仍留业务层。 |
| `benchmark/t2i/fine_screening/t2i_fine_screening_pipeline.py` | 读取 published 的 concept_assessments 中 description、visual_support | 保留历史固定引用；新流程不得把旧 keep 自动当作审定概念的新用途通过。 |
| `benchmark/t2i/v2/t2i_v2_benchmark_pipeline.py`、`benchmark/edit/v2/edit_v2_benchmark_pipeline.py` | 依赖 published_concepts／concept_assessments 统计或读取已发布图片 | 迁移前定义旧调用方如何按固定 release 或新用途结果读取，不能先删列。 |
| `benchmark/edit/source_images/source_image_pipeline.py`、`operaters/sources.py` | 读 URI、SHA、技术状态；published_only 模式还读已发布且 keep 的概念审核关系 | 基础资产入口继续稳定；下游采样、来源排除、用途筛选保留在自身流程。 |

公共字段契约现在集中在 `catalog/operaters/schema.py`。本轮未修改该共享文件、任何生产者／消费者、notebook 参数或历史输出。

### 9.4 历史成果及复用边界

- annotation smoke：image_results@2 共4张、concept_results@2 共5条；summary@1 确认 committed=true、目标@14。公共表后续升至@16，不将 smoke 提交误认成本轮全量提交。
- 469 概念 review：`preparation/datasets/image_relevance__t2i_dual_keep469_visual_v3_tp2_20260928.lance@1` 共1,165批；v3 `visual_image_meta@1` 共4,258关系、`knowledge_base@1` 共469概念；v4_scope3 的相应表均为@1、分别3,118关系和469概念。实际读取运行发布记录：v3 的 `results/visual` 为空；**v4 已提交到公共 images@10**，release 为 `visual_65d089869af0d503e976eaa0`，绑定 collect images@5 与 v4 visual_image_meta@1。已有表、固定发布引用与旧调用库均保留，不能根据旧 README 的停止快照认定 v4 未执行／未发布。
- 旧中性描述可在 SHA、实际图片、协议与引用一致时复用；旧概念匹配和双模型 keep 只作为其旧输入／旧规则下的证据。概念定义、限定范围、用途或评审规则变化后，必须重新判断适用性，不能原地改写历史评分。
- 当前 `annotation_prompt_pack` 解析包含两个节点的整份 tasks.yaml，`semantic_config_id` 包含整包 content_hash 及 quality_formula_id。只拆删关系级 prompt／改公式也会改变图片级记录 config_id。**公共记录身份与 journal 请求身份不是同一层**：后者按实际模型请求计算；改造必须分别验证两者，不笼统声称“挪目录肯定零重算”或“整包变化必定全部重发”。
- 原 §6 的 `schema_retries: 2` 配方还需更正：当前 demiflow parser 仅接受0或1；本轮不修改平台也不执行该配方。

### 9.5 建议分工与协调顺序（待共同定案）

1. **QID 公共数据整理会话**继续负责采集／下载、URI＋SHA、来源关联及资产目录；不替其修改 collect、subset、图片对象或公共目录基础列。来源声明与审定结论分别保留。
2. **概念审定会话**负责稳定 concept_id／source_record_id、概念定义、限定条件、证据、审定状态与版本；图片关系不能只靠名称或 QID 等同。读取已提交的固定结果，不改 master 和该会话开发中的文件。
3. **本会话的拟议范围**是上游 annotation 描述与证据接口、历史结果复用边界及旧 review 依赖迁移清单；先保全现有协议，协调后再改业务代码。是否先只恢复中性描述由协调方案决定。
4. **benchmark 规划会话**负责新下游图片适用性协议与正式入口：按审定概念召回 → 核对身份和限定条件 → 训练／评测用途判断 → 缺口与补图 → 采样及防泄漏划分。输出独立用途结果表，绑定 SHA、概念身份／定义版本、规则版本、用途、证据引用、模型及本轮范围；不把通过与否回写成公共图库的全局合格标签。
5. **demiflow 的改动集中协调给同一维护方**：复用现有对象读取、模型调用、journal、预算、服务生命周期、表版本、锁和部分列写入；取消不推进后续阶段、已提交阶段与未提交文件识别等机制问题也在平台统一验证。筛选、评分、概念规则和用途判断留在业务层。
6. 修改前先对齐字段所有权、上下游固定引用、历史 release 读取和请求身份兼容；再分文件实施。回归重点为旧响应复用、概念版本变化不误复用、取消不继续评分／发布、部分列保留、空结果与技术失败、articles／T2I／Edit 旧消费者兼容。本轮只做运行核查和依赖盘点，未运行模型、未进行代码改造回归。

## 10. 按已分配职责拟实施的上游改造方案

本节为用户要求“先详述方案”的设计交付，不表示代码已实现。分工依据为 `taxonomy-rebuild/IMAGE_PIPELINE_COORDINATION.md`；该共享文件由规划会话维护，本会话不修改。执行遵循 `PIPELINE_SPEC.md`，现有生产运行继续暂停。

### 10.1 完成后的职责和入口

- catalog 继续提供 SHA、独立 image_uri、资产技术状态和来源关联；本会话不修改 collect／subset／catalog 公共 schema，不接管 QID 整理。
- `preparation/images/annotation/image_annotation_pipeline.py` 保持唯一 `config → run_pipeline` 入口，正式新运行只做图片中性描述和通用观察证据；一张 SHA 一次描述任务。概念名称、定义、QID、采集关联和用途不进模型载荷，图片没有概念关联也可描述。
- 保留 DESCRIPTION、richness 和其理由的原有含义及协议。richness 作为带规则版本的机器观察保留，不据此自动筛选或宣布训练／评测合格。缺什么新字段另开协议版本，不在这次拆分里顺带改 prompt。
- 新上游路径只允许更新公共 `descriptions/image_scores` 两列；不执行 score_concept，不生成新的概念匹配、quality 或用途发布。旧关系结果及其字段仍可读取，不删除旧记录，不另建可生产评分的平行 legacy pipeline。
- `review` 保留旧文章／旧 release 的正式调用和读取语义；此次收敛其通用描述依赖，不整体搬迁或重写。新概念用途判断由另一会话的 `benchmark/t2i/image_audit/` 实现，本会话不改该目录。

### 10.2 主线阶段、配置与状态

仍在一个正式入口内显式编排：固定输入 → 建立本轮 SHA 范围 → 复用或描述 → 校验结果及键覆盖 → 本轮阶段表 → 可选公共列 merge → summary。

拟增加 `through='prepare'|'annotate'|'export'`，新入口默认止于 annotate：prepare 只落准备范围，annotate 落本轮描述结果，export 才显式提交公共两列。阶段续跑消费固定结果引用，不因目录存在或旧摘要成功就跳过核验。`annotation_parts='all'/'concept'` 不再作为新上游生产路径；旧结果查看和旧请求身份重建独立于启用旧评分节点。

模型执行与阶段选择分别配置：拟提供 `model_mode='online'|'replay'`。replay 严禁新模型请求、模型列表探测、GPU 服务启动和 uncertain 自动重试；缺响应保留明确状态。所有参数在 config 校验，CLI／notebook／后台调用同入口，不另建 replay.py 或 notebook 私有流程。

| 业务结果 | 一行与主键 | 交付要求 |
| --- | --- | --- |
| image_inputs | 本轮范围内一 SHA 一行 | 独立 URI、准备／排除状态、固定来源；保留缺图与排除理由，不以技术失败缩小分母 |
| image_results | 每个应描述 SHA 一行 | 原描述记录、图片评分记录、输入摘要、响应引用及 done/reused/缺响应/技术失败；失败不造分数 |
| patch | 每个有有效新增证据的 SHA 一行 | 仅 descriptions/image_scores；未进入 export 不写公共目标 |
| summary | 本 run 当前明确阶段的一行摘要 | 固定输入／结果引用、应处理与实际有效数量、失败分类、阶段完成、整轮完成、公共提交状态与目标版本 |

沿用现有表结构能表达的字段；新状态协议若与旧 summary 不兼容，使用明确的新表 schema／独立表路径，旧 summary 与其读取语义保留，不原址重解释历史记录。旧全量 run 的表、journal 和日志不作为开发测试输出。

结果行覆盖完整、所有必需描述有效、公共提交成功分别检查；计数还需核对 SHA 主键的缺失／重复／多出。默认 export 要求必需描述全部有效；若调用方显式选择 valid_rows 部分发布，允许只提交有效证据，但仍记录整轮未完成和失败／缺失数量，committed 不能替代 complete。取消或中断不得进入 export。

### 10.3 一份描述选择实现，两类明确的读取契约

把实际复用选择逻辑归回 annotation 生产者，拟放 `annotation/operaters/evidence.py`，由 articles 和旧 review 显式导入；只保留一份选择与校验实现。

- 输入必须是固定表引用＋SHA，按明确的 config_id／annotation_id 选择；多条有效证据未指定选择时返回歧义，不随意取最新或第一条。
- 新证据读取可返回 SHA、原 annotation_id/config_id、实际来源固定引用、描述、richness、响应／输入摘要及证据状态；明确是模型观察，不能伪装成人工认证。
- 旧 `ReuseImageAnnotations` 调用的输出结构和 record_sha256 计算先保持一致；需要新证据字段时通过具名投影交付，不给旧模型输入悄悄加字段。必要的旧结构适配与选择逻辑分开，不能复制第二份业务选择实现。
- 缺描述只返回缺失，不暗中调用模型、补表或将概念审核说明冒充中性描述。普通查询只读本行必要字段，缓存有界。
- 新 image_audit 首版看实际图片，不将此接口变成对方开工的前置条件；未来接入只作为可选证据，不替代看图及本轮判断。

### 10.4 旧请求和旧记录的兼容

先保留现有 v2 图片描述正文、响应 schema、预处理和生成参数，再拆分身份计算。新图片节点指纹仅包含自身的有效协议、模型／部署及实际输入处理参数，剔除未使用的 score_concept 定义、关系评分公式和调度参数。

历史兼容需要分别验证三层：

1. **实际模型请求**：重建历史请求键，证明相同输入在拆分前后命中原完整响应；schema 重试也按原请求历史复现，不能改变反馈文本后冒称同一请求。
2. **图片节点语义**：用可核验的原协议绑定确认历史记录与当前描述任务等价；不能只按图片 SHA 或模型名称通配复用。缺部署版本的历史记录不补填成新部署认证。
3. **公共记录**：复用时保留原 annotation_id/config_id/provenance；新运行的协议身份另行记录，不把旧记录改名成新结果，也不因本次改造重复追加等价记录。描述与 richness 继续按同一 annotation_id 配对。

原全量 run 保持暂停。开发先在隔离湖、隔离 journal 副本／有界历史样本上做零新请求回放，原 journal 不写入、不 requeue。64 条无完整响应预留保持 uncertain。全量回放、真实模型续跑、失败清扫和公共表提交不包含在本次代码及隔离验证授权内。

### 10.5 对规范的具体落实

- **S02/S03**：保留入口、四格 notebook、operaters、prompts、tests、README 的标准布局。read_lance、map_prompt_async、必要 materialize、键关联、write_lance 和更新列在主线可审查；行函数只处理当前行／组，不接管流程。生产明细不 take_all/list 后重新装回 Dataset，不增加全库 Python 明细索引。
- **S04/S06**：来源使用固定版本，本轮范围、历史复用与新增响应分别记账；显式输入改变时重新核验范围，已有记录保留原证据身份。
- **S05/S07**：缺图、无响应、协议无效、存储错误、取消分别记录／传播，不作低分或业务否决。取消后不继续写公共目标，未提交 Lance 目录在预览中显示为未提交。
- **S08/S09**：预算、并发、模型部署、预处理与阶段开关显式配置；固定 prompt 和 schema 只在其 prompt pack 维护。此次先不改变模型判断内容。
- **S10**：配置／执行／结果查看／运行观察四格保持职责明确；查看格可在新 kernel 独立运行，保留原 notebook 输出和用户参数，历史关系表清楚标为历史。
- **S11**：独立 URI＋SHA，公共部分列 merge，保留其他生产者列、其他 SHA 和历史记录；通用对象 I/O、journal、预算、服务、取消和锁依赖 demiflow。
- **S12**：结构检查与业务行为回归分别报告。不会用“测试全绿”代替真实描述质量验证，也不在本次发起生产模型请求。

平台边界有两项需在复现／验证后按共享分工登记单一维护方：只读历史 journal 的零请求回放能力，以及取消传播到 materialize／writer 的终止保证。目前 annotation config 拒绝 image_max_calls=0，平台模型节点接受0；不能只改业务参数校验就宣称已获得“原 journal 绝对只读”的保证。若需补平台 API，不在业务层写一套 SQLite 恢复器或私有执行器。

### 10.6 实施顺序与验收

1. 先加隔离行为用例，固定旧描述读取结构、请求键和记录身份，复现取消／未提交目录问题；有界历史回放只读原数据，产物在测试湖。
2. 抽取单一证据选择实现，更新必要 articles／review 导入和测试，验证旧 release 与旧请求载荷不变。
3. 收敛 annotation 正式描述主线、显式阶段及提交策略，解耦节点身份，保留旧记录读取与原协议回放。
4. 补齐 notebook、README、CLI、状态统计与恢复说明。统一布局检查只运行验证；新增 image_audit 的登记由规划会话修改，本会话不争写该文件。
5. 回归覆盖：无概念图片、同 SHA 多关系只描述一次、复用零新请求、改变实际图片协议不误复用、只改旧关系 prompt 不影响新图片节点、64类 uncertain 不重发、坏图与非法响应不造成功、取消不发布、未提交目录可查看、公共两列以外逐值保留、幂等与冲突处理、旧 articles／review／T2I／Edit 读取兼容。
6. 交付代码、明确字段和配置、模拟回归结果、有界历史回放证据及未验证范围；原生产 run 继续暂停。进展记录仅更新本文和本会话负责的模块文档，不并发修改共享分工文档、QID／概念模块或新 image_audit。

## 11. 2026-10-01 实施中：图片与文章为独立 pipeline

用户在本会话明确要求彻底实现，并随后纠正“image 与文章是两条不同的 pipeline”。**第 10 节里把旧 review/文章兼容维护纳入新图片职责的方案已撤回**；不是已确定的新图片要求。新 annotation 不读取文章、历史 release 或概念定义，不编排文章及下游用途审核。

已实施（隔离验证中）：删除 annotation 的 score_concept prompt、关系级执行、concept_results 生产、关系评分参数和全库 Python 复用索引；新入口仅中性图片证据，prepare/annotate/export 显式分段，默认 annotate。公共只更新 descriptions/image_scores。新 run 使用固定输入和逐 SHA 完成检查；只读 replay 不修改原 journal，未完成请求不自动恢复。细节以 annotation/README.md 为现役入口。

本次通用机制修改归本会话维护，未引入业务私有执行器：

- `demiflow/operator_llm/sqlite_journal.py`：显式 read_only，RO 连接、禁止补写元数据及所有变更。
- `demiflow/operator_llm/client.py`：HTTP 请求身份支持可选 model_revision；未给值保持旧键；禁止只读日志发送新请求。
- `demiflow/operator_llm/runtime.py`：只读未命中在服务加载前结束；保留 schema 重试失败的实际响应引用。
- `demiflow/execution/stream.py`：外部取消不得被当作正常投喂结束，不返回部分物化成功。

平台测试 `test_readonly_prompt_replay.py`、`test_stream_cancel_propagation.py` 及相关已有回归首轮 68 项通过；annotation 原生 HTTP/SQLite/Lance 隔离行为首轮 26 项通过，后续记录以最终测试输出为准。没有真实模型调用、公共数据写入或生产恢复。

用户进一步明确：本次改造对象是图片 annotation。实际直接代码耦合发生在 articles 与 review 之间：文章调用 review 的配图审核与读图函数，review 使用 articles 的运行记录与阶段结构；annotation 与二者没有直接代码调用。review 可消费公共表 descriptions，这是数据契约关系，本次保持字段兼容，无需改造其代码。此前关于解除 articles/review 耦合的范围选择已撤回，该事项不属于本次任务，也不是 annotation 完成的前置条件。articles、review、其他会话的 QID、概念审定及 image_audit 文件均未修改。

最新验收记录：annotation 行为测试 28 项；连同该入口 CLI/布局相关检查为 32 项通过。demiflow 只读回放/取消及相关回归 68 项通过。共享编码下游 image_audit 的 31 项隔离测试通过（仅验证接口，没有修改该会话文件）。全库布局检查发现两个非本轮范围问题：旧 review notebook 仍定义函数；其他会话新增 qid_concepts/qid_images 尚未出现在当时读取的布局注册表。annotation 自身 README 首段格式已修正并通过定向布局检查。notebook 第三/四格在空隔离根、新 namespace 中分别执行通过；未保存伪造生产输出。

保全复核：原 journal 仍为 6,449,209,344 字节、mtime_ns=1790825862043685000；原主进程 9614 和 vLLM 37032 均不存在。没有触碰公共表、原运行阶段数据和生产模型服务。文章的 review 调用位置为 articles_pipeline.py:70/1270，仅说明 articles/review 之间的代码关系，不构成对 annotation 的代码依赖，本次不处理。
