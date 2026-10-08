# GLM 护航交接：四级 keep+hold → 全量 add 原图标注

## 2026-09-28 性能修订与当前入口（覆盖下方历史启动状态）

- 当前仍暂停，本次仅修改代码、做隔离测试；未恢复全量推理。Notebook 默认 `monitor`，用户恢复时重启内核，再依次执行导入、配置、提交格；配置格显式改为 `run` 才提交。
- 保持同一 run、TP=2/DP=1、客户端256/服务端160、读图32、队列16、6144输出、原 prompt 和图片字节。后台必须执行当前 notebook 同款配置。
- 本地模型算子改用 demiflow 的 `sqlite_journal`，路径为同目录 `calls__scene_pool_add_v5_l4_keep_hold_tp2_v1.sqlite`。请求键只算一次，缓存和写入移出事件循环；只保存紧凑请求元数据与完整响应，不保存图像 base64，不逐请求扫描 key/合并文件。
- Notebook 的 `legacy_calls_source` 固定旧 Lance 调用表 `@46240`。22,693条旧响应已导入并逐值核验，GLM 已开始续跑。后续维护已清掉46,239个旧快照，仅保留 `@46240` 与 SQLite；旧历史回执不能按原版本号读取，原请求/响应仍可从 `@46240` 按 key 查询。新调用只写 SQLite，原图和业务结果未改动。维护记录在共同根 `_demiflow/maintenance/edit_call_cleanup_20260928/result.json`。
- 无响应占位继续明确报不确定，迁移不授权自动重发。旧记录不删，新响应必须提交成功后才交给后续算子。不要沿用 §9 历史运维中的删预约/迁日志操作。
- `state.call_journal` 指向 SQLite；原 `state.calls` 留作 offline/历史定位。不要继续通过旧 calls 行数监控新增进度。业务 image_results/pool 仍整阶段提交，运行中看进度日志；原图在续跑时仍会读取以核对请求身份。

下文为此前接线及运行记录，涉及首次启动状态、并发和日志位置时以上述修订及 notebook 当前参数为准。

用户已确认：用最新四级4B结果召回keep+hold图片，配置好notebook后交给GLM启动和护航。请按本文和notebook执行，不重复询问是否启动。Codex本次完成了接线、只读范围核对和隔离测试，**尚未启动本轮生产模型**。

## 1. 唯一入口与当前状态

- 项目：`/yzp/zhaozy/yangzepeng/0905/demiwtg`。
- Python：`/yzp/zhaozy/yangzepeng/0905/env/bin/python`。
- Notebook：`/yzp/zhaozy/yangzepeng/0905/demiwtg/benchmark/edit/source_images/source_image_debug.ipynb`。
- 正式入口：`benchmark.edit.source_images.source_image_pipeline.config(...) → run_pipeline(cfg)`。
- Prompt：`benchmark/edit/source_images/prompts/tasks.yaml`，视觉版本`edit-source-image-5`。
- 本轮run：`scene_pool_add_v5_l4_keep_hold_tp2_v1`。
- Notebook当前`notebook_action='run'`、`mode='local'`、`through='pool'`，执行导入、配置、提交格即可开始全量。
- Notebook内核为`demiwtg`，已核对其argv指向上述env/bin/python。不要用不确定的系统Python。

业务决定输入范围、模型和资源；**demiflow负责模型服务启动、就绪检查、释放和异常清理**。这部分已下沉，不需要新写部署脚本，不要另起vLLM抢占8000端口。不要绕开demiflow自写HTTP模型循环或用Python全量概念集合替代Dataset关联。

## 2. 固定输入与预期范围

下表路径均相对`/yzp/zhaozy/yangzepeng/0905/demiwtg/benchmark/edit/source_images/datasets/`。

| 配置 | 固定表 | 版本 | 用途 |
| --- | --- | --- | --- |
| concept_input_source | concept_inputs__scene_pool_category_l4_v1.lance | 1 | 88,878个概念及完整taxonomy |
| category_result_source | category_results__scene_pool_category_l4_v1.lance | 1 | 已完成的31,336项四级分类/实例判断 |
| checked_image_source | image_checks__scene_pool_category_l3_v1.lance | 1 | 四级粗筛的原始183,013张ready图片及固定Blob |

四级分类分布为keep14,317、hold12,857、reject4,162。选择keep+hold，共27,174项，按原有分类归属关联后得到**82,331个概念、172,297张唯一图片**。已使用正式行算子和原生Dataset只读核对，未回扫公共图片表、未读图片像素。

注意：四级`image_checks__scene_pool_category_l4_v1@1`只有138,955张keep召回图，直接接它会丢hold；这就是图片来源固定到三级原候选的原因。此次范围仍受四级原输入183,013张限制，不恢复更早三级reject/hold范围，也不使用旧500张视觉试跑或255张池。

数据流：

1. 读固定概念/原候选 → 还原四级分组；与固定分类目标核对分类键、大域、类型和成员数。
2. 原样复用完整分类决定，`category_decisions=['keep','hold']`召回；**4B调用=0**，`max_text_calls=0`。
3. 按concept关联图片，按SHA合并，沿用原Blob、尺寸；短边严格>1024。
4. `max_images_per_concept=None, max_images=None`，全量172,297张进入视觉，绝非500图或每概念4图。
5. 单图调用27B → 校验原始标注 → 代码派生decision和五组retrieval_tags → 全部结果写image_results，high/medium/low写pool。

输入筛选的hold是**4B分类hold**，不是最终视觉hold。视觉unsuitable/uncertain仍分别为reject/hold，保存在image_results，最终pool只含已确认可用的high/medium/low。

## 3. 模型与GPU配置

用户明确要求TP，不是两个完整权重副本。当前配置：

| 参数 | 值 |
| --- | --- |
| 模型 / 本地权重 | qwen3.8-27b / models/Qwen3.8-27B |
| GPU / TP / DP | [0,1] / 2 / 1 |
| 请求并发 / 服务max_num_seqs | 128 / 128 |
| gpu_memory_utilization | 0.92 |
| max_num_batched_tokens / max_model_len | 16384 / 16384 |
| 单图最大输出 / thinking | 6144 / False |
| 图像预处理 | size.shortest_edge=65536，longest_edge=2048×32×32 |
| 请求超时 / 加载超时 | 600秒 / 900秒 |
| queue_depth / max_keepalive_connections | 4 / 0 |
| 新视觉请求预算 | 172297 |
| 服务端点 | http://127.0.0.1:8000/v1 |

两张卡为H100 80GB，交接检查时均空闲；启动前重新查看，不能假设一直空闲。单份权重分两卡，为KV cache和并发留显存；实际GPU负载、吞吐、KV缓存和预抢占情况需要运行验证，不能预先宣称“打满”。

输出上限6144，不会强制模型生成到上限。Prompt/schema约束最多3区域、每区域2候选、每候选3关系，已去掉重复decision/检索汇总输出。本地tokenizer估算模板1233+schema745+视觉预算约2048+输出6144≈10,170 tokens，16384留有格式余量；这不是完整图像processor/服务输入预检。

图像像素预算只影响模型看图，原图Blob与入池尺寸不缩小。模型只收到图片和任务说明，不传采集概念、taxonomy或上游筛选理由。不要擅自把这些信息补回去。

## 4. 从notebook启动

先确认没有同run写入者、没有其他任务占用GPU/8000端口。已有本轮任务则直接监控，不重复启动，不杀不明归属进程。

交互启动：在`demiwtg`内核依次执行导入格、配置格、提交/监控格。配置格只构造cfg；提交格通过`await asyncio.to_thread(run_pipeline, cfg)`执行，避免Jupyter活动事件循环冲突。

为避免VS Code断线影响操作，GLM也可后台执行**同一本notebook**。以下命令尚未执行：

```bash
cd /yzp/zhaozy/yangzepeng/0905/demiwtg
nohup /yzp/zhaozy/yangzepeng/0905/env/bin/jupyter nbconvert \
  --to notebook --execute --inplace \
  --ExecutePreprocessor.kernel_name=demiwtg \
  --ExecutePreprocessor.timeout=-1 \
  benchmark/edit/source_images/source_image_debug.ipynb \
  > benchmark/edit/source_images/datasets/submit__scene_pool_add_v5_l4_keep_hold_tp2_v1.log 2>&1 < /dev/null &
```

保存启动PID和时间。该命令直接执行notebook中的cfg，没有另写第二套参数。执行中以业务日志监控；nbconvert通常在结束后才写回notebook输出，不能以文件输出未刷新判断停滞。运行期间不要同时编辑/保存同一notebook，避免完成写回覆盖新配置。另一个内核只监控时，可在内存把`notebook_action='monitor'`再执行提交格，不必改写磁盘配置。

如先核对输入而不调用模型，可先把配置格`through`改成`'image_inputs'`运行；核对172,297张后，将同格恢复`'pool'`再提交。这会重建准备表，但不会重跑4B；护航无需另造一个500图实验。

## 5. 监控与调优

全部文件位于：`/yzp/zhaozy/yangzepeng/0905/demiwtg/benchmark/edit/source_images/datasets/`。

- 主日志：`run__scene_pool_add_v5_l4_keep_hold_tp2_v1.log`。
- 服务日志：`model__scene_pool_add_v5_l4_keep_hold_tp2_v1__vision.log`。
- 后台提交日志：`submit__scene_pool_add_v5_l4_keep_hold_tp2_v1.log`。
- 摘要：`records__scene_pool_add_v5_l4_keep_hold_tp2_v1.lance`的`state`键。
- 完整请求/响应：`calls__scene_pool_add_v5_l4_keep_hold_tp2_v1.lance`。

首先确认日志出现“本轮文本调用=0”，视觉输入为172,297；服务启动参数为TP=2、DP=1。若范围不符，先定位版本/来源/过滤条件，不在错误范围上继续长跑。

首批关注加载/就绪、第一条成功标注、首种错误；进入稳定段后观察至少数百张的真实产出，再估算剩余时间。主日志含处理数、响应复用数、五档分布、状态、含复用速度；复用速度不能当作新推理吞吐。服务日志关注Running/Waiting、生成token吞吐、KV cache占用、预抢占及OOM。

```bash
nvidia-smi --query-gpu=index,name,utilization.gpu,memory.used,memory.total --format=csv
tail -n 30 /yzp/zhaozy/yangzepeng/0905/demiwtg/benchmark/edit/source_images/datasets/run__scene_pool_add_v5_l4_keep_hold_tp2_v1.log
tail -n 50 /yzp/zhaozy/yangzepeng/0905/demiwtg/benchmark/edit/source_images/datasets/model__scene_pool_add_v5_l4_keep_hold_tp2_v1__vision.log
```

调优原则：

- 显存已分配不代表算力已打满。若Running长期偏低且Waiting为0，检查读图/CPU预处理/请求日志供给，不先盲增并发；若Waiting很多且GPU已忙，增加并发只会加长排队。
- 当前128并发是TP=2的起始配置。只在有实测证据时调整，并记录调整前后有效图/分钟、token/秒和错误率。不切回用户未选择的DP=2，也不另加Gemma。
- 资源/并发/日志调整先同步notebook，再确认旧写入者退出后重启同款cfg；不要只在后台命令里覆盖。通常这些参数不改变请求身份，可复用完整响应。
- max_tokens、thinking、像素预算、Prompt或模型改变会改变请求身份。不能为了几条截断把全库换参数重跑；先统计并定位受影响请求，再制定只处理失败项的恢复方案。
- 主日志由完成行触发，无完成行时不会定时心跳。分类关联/排序和首次编译可能暂时无行日志；结合进程、服务日志、GPU、日志更新时间判断，避免因为VS Code界面不动就再启动一次。

## 6. 中断与异常

完整请求响应持续保存在原生日志中；同run、相同图片/Prompt/schema/模型参数可以复用。关联和输入表会重建，不是整个pipeline从任意节点断点恢复。视觉阶段结束前image_results/pool可能尚未落表，不能由它们暂时不存在断言前面白跑。

Notebook中断await不保证工作线程停止。确认原生运行锁和宿主进程状态后才能重提；强杀宿主可能留下服务，须确认进程组确属本轮再清理。正常退出/异常由demiflow释放本轮服务，业务不另写清理循环。

- 端口/GPU占用：查明归属；不得直接杀其他任务。
- OOM/加载失败：查看服务日志与实际参数，必要时调整notebook的资源参数；确认旧进程组退出后重提。
- `finish_reason=length`、残缺JSON、结构错误：记为技术失败，不能降级当hold/keep；响应可能被缓存，同参数重提不会自动修正它。
- `UncertainPromptCall`或“Request reserved without complete response”：请求曾占位但没有完整回执，不自动再发；保留request_ref、错误与已完成数量，检查demiflow原生恢复能力。不要删除calls/锁记录伪造可续跑，也不要另开run盲目重算17万图。
- 遇到需要恢复接口或局部重试但现有入口不支持时，明确报告具体阻塞及已保存数量；不要声称完整成功。Prompt质量/范围调整不属于吞吐调优，不擅自改本轮判定规则。

## 7. 交付与验收

以下目标均使用本run后缀：`concept_inputs/category_inputs/category_results/concept_candidates/image_checks/image_inputs/image_results/pool`，公共datasets和历史源表不回写。最终目标为：

`/yzp/zhaozy/yangzepeng/0905/demiwtg/benchmark/edit/source_images/datasets/pool__scene_pool_add_v5_l4_keep_hold_tp2_v1.lance`

完成后运行notebook统计格和图卡，按摘要固定版本核对：

1. `category_count=31336`，原分类分布保持；selected_category_decisions为keep/hold，candidate_concept_count=82331。
2. `image_checks.ready=172297`、`image_count=172297`，image_results行数等于输入数；max_images/max_images_per_concept均为None。
3. `image_prompt_version=edit-source-image-5`；五档计数总和等于annotated数，其余技术状态单列。
4. pool_count等于high+medium+low；unsuitable/uncertain以及技术失败不得混入pool，原始SHA/Blob/尺寸保留。
5. `complete=True`才表示所有行完成有效处理；只要有pending/failed/invalid_response/unreadable等，报告为未完整完成并给明细。进程退出和锁释放不能替代此判断。
6. 抽看high/medium/low/uncertain/unsuitable，核对区域定位、候选适配、物理依据和等级是否合理。结构校验不代表语义已经人工验真，不要求改变任何档的占比。

向用户交付：实际模型和TP/DP、真实输入/成功/失败数、五档分布、最终表URI/version、耗时和有效吞吐、错误与处理状态、少量可回看的图片例子。保留原始响应，更新护航记录，不伪造notebook执行输出。

## 8. 接线验证记录

- 只读原生Dataset核对172,297张范围，源固定版本保持。
- 已执行notebook导入/配置格，校验实际cfg及demiflow生成的服务命令；提交格未执行，GPU模型未加载。
- 65项隔离测试通过：固定分类版本复用、keep+hold召回、全量/限量、范围不匹配与技术失败拒绝复用、零文本请求、视觉响应回放、GPU服务生命周期、notebook图卡及标准布局。

后续GLM实际运行的时间、PID、参数调整、吞吐和完成结果，请追加在此文件末尾；开发验证不等于本轮生产已完成。

---

## 9. GLM 夜间护航实录（2026-09-27，追加）

### 启动与六次重启时间线（均通过后台 nbconvert 执行同一 notebook）

| # | 启动时间 | nbconvert PID | 说明 |
| --- | --- | --- | --- |
| 1 | 18:35:15 | 36967 | 初次启动，范围核对全部正确（文本调用=0、82,331概念、172,297图、TP=2） |
| 2 | 18:53:11 | 48618 | 业务层修复后重启（见下），复用835条响应 |
| 3 | 19:09:41 | 54822 | demiflow 两处补丁后重启，复用1,220条 |
| 4 | 19:20:41 | 37016 | journal 专用执行器精化后重启，复用2,044条 |
| 5 | 19:44:20 | 5914 | calls journal 迁本地盘（符号链接）后重启，复用3,380条 |
| 6 | 20:04:12 | 26633 | 最终配置：256在飞/160_seqs/队列16，复用4,637条，**持续运行中** |

每次停机均：确认进程树属本run→强杀→清理vLLM孤儿→清理calls表"有request无response"孤儿预约（lance覆写新版本，历史可审计）→重启。请求身份全程未变，已完成推理零重算。

### 吞吐诊断与修复（四层瓶颈，层层实测）

1. **同步 map 单 worker**（demiflow 流式路径把同步 MapOp 固定 concurrency=1 且在事件循环上执行）：prepare_image 读图/解码/base64 串行供给，41行/分钟。修复（业务层）：source_image_pipeline 增加 `image_prepare_concurrency` 配置，prepare_image 走 map_async+to_thread 并行级（现配32）。→ Running 可达128但锯齿。
2. **journal 同步写在事件循环 + 每32笔compaction秒级停顿**（put p50=25ms、max≈2s，随表增大恶化）：46行/分钟。修复（demiflow，经用户review确认保留）：client.py journal 调用下沉专用4线程执行器；records.py compaction 阈值 32→512。提案见 `demiflow/PROPOSAL_journal_off_event_loop_20260927.md`。→ 119行/分钟，仍有周期塌落。
3. **request 记录内联整图 base64（2-13MB）× flock 串行 × PVC 慢**（8MB put：PVC 143ms vs 本地盘37ms）：128个worker各await自己的reserve，串行排队~60s/波。修复（运维）：calls 表迁 `/root/demiflow_calls_local/`（本地盘），datasets 下符号链接保持验收路径。→ 调度串行段~5s，塌落基本消除。
4. **客户端装配波与服务端消化波的空窗**：配置 128→256 在飞、queue_depth 4→16、服务端 max_num_seqs 128→160（KV 59%无抢占），vLLM Waiting 队列吸收抖动。→ Running 持续钉160、双卡100%。

### 最终稳态（20:19-20:24 实测窗口）

- 处理 ~113行/分钟（含复用口径），服务端 Running=160 持续、Waiting 19-46、KV 58-59%、prefix cache 42%、生成吞吐峰值 4043 tok/s、双卡 100%。
- 服务端每请求 p50 26.6s（128并发时）；160并发下放大到 ~85s，仍在 600s 超时内。
- 剩余 ~166.5k 张，预计 ~20-24 小时完成（对比原始配置的 ~70 小时）。
- 状态健康：annotated 5,785、unreadable_image 2（数据问题，确定性）、failed 0（孤儿已清）。

### 夜间护航机制

- 定时巡检已建立：每20分钟检查进程/主日志/服务日志/GPU，异常按交接原则处置（孤儿清理+同命令重启），完成则进入第7节验收并把记录追加到本文件。
- 用户另一作业（PID 41584，19:37:13 起）与本 run 无关，护航不触碰。

### 平台文档沉淀（供 demiflow 后续演进）

- `demiflow/PROPOSAL_journal_off_event_loop_20260927.md`：两处已落地变更的提案（用户已确认保留，client.py 部分待正式入库）。
- `demiflow/EXECUTION_MODEL_SELECTION_20260927.md`：执行模型选型备忘（数据流图与并发底座正交、协程/线程/进程适用域、Flink式平台化并发三选项、journal配套改进）。

---

## 10. 最终完成与验收记录（2026-09-28 16:03，追加）

**全量完成**：172,297/172,297 处理完毕（第十次启动 12:23:39 → 16:03，本段含 ~71 分钟回放复用 139,833 条 + 新推理 31,900 张）。

### 期间事故（两次同模式外部击杀）

- 09:54 与 12:04 两次：本 run 的 nbconvert 链无声死亡 + notebook_action 被外部改回 monitor，每次损失 ~250 条在飞请求成为不确定档。证据：/root/evidence_pattern_two_deaths.log。cgroup oom_kill=0、本栈内存正常，疑用户侧自动化（agent/编辑器）清理所致，已请求用户排查。另注：10:11 的重启系 GLM 误判自身暂停为外部事故所致（已向用户致歉并复盘）。

### 验收结果（对照第 7 节清单）

| 项 | 结果 |
| --- | --- |
| category_count | 31,336 ✓（concept 27,162 + taxonomy 4,174） |
| 原分类分布 | keep 14,317 / hold 12,857 / reject 4,162（复用固定目标，文本调用=0）✓ |
| selected_category_decisions | ['keep','hold'] ✓；candidate_concept_count=82,331 ✓ |
| image_checks.ready | 172,297 ✓；image_count=172,297 ✓；每概念/总图限额=None ✓ |
| image_prompt_version | edit-source-image-5 ✓ |
| image_results | 172,297 行 @1 ✓（=输入数） |
| 五档计数 | high 39,354 / medium 63,908 / low 41,200 / uncertain 246 / unsuitable 26,780，合计 171,488 = annotated ✓ |
| decision 派生 | keep 144,462 / reject 26,780 / hold 246 ✓（与等级映射一致） |
| pool | 144,462 行 @1 = high+medium+low ✓；unsuitable/uncertain/技术失败零混入（抽验分布仅三档）✓ |
| complete | False——技术状态 809 条单列（见下），处理覆盖 172,297/172,297 |

### 技术状态明细（未入池，留待决定）

- failed 755 = 240（09-27 暂停在飞孤儿）+ ~514（09:54、12:04 两次被杀的在飞占位）+ 1（损坏 TIFF 图服务端解码失败）。前 754 条请求身份未变，授权后可低成本补算（服务端约 6 分钟），需按新语义显式批准重发。
- invalid_response 10（结构校验拦截，0.006%）；unreadable_image 44（源图损坏，不可恢复）。

### 资源与吞吐

- 模型：qwen3.8-27b，TP=2/DP=1（双 H100），max_num_seqs=160/max_model_len=16384/gpu_mem=0.92；客户端 256 在飞/读图 32 线程/队列 16。
- SQLite 版稳态 213-219 张/分钟、双卡持续满载、Running 钉 160；本段 image_elapsed_s=12,716s。
- 磁盘：calls SQLite 最终 ~2GB（对照 Lance 方案的 1.6TB+）；旧 Lance 表 @46240 只读保留。

### 交付物

- pool__scene_pool_add_v5_l4_keep_hold_tp2_v1.lance@1：144,462 行（机器 keep，status=unreviewed）
- image_results__scene_pool_add_v5_l4_keep_hold_tp2_v1.lance@1：172,297 行（含全部技术与业务状态）
- calls__scene_pool_add_v5_l4_keep_hold_tp2_v1.sqlite：全部请求/响应
- 抽样预览：reviews/GLM_preview_20260928/（preview.html 全字段版 + cases.md + 6 例 PNG）
- notebook 已复位 monitor；GPU/8000/锁全部释放。
