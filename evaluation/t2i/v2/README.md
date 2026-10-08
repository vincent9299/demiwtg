# T2I V2 评测

> **项目强制规范**：本 pipeline 的开发、修改、运行配置与评审必须先阅读并遵守 [《项目 Pipeline 强制规范》](../../../PIPELINE_SPEC.md)。本 README 仅补充本流程的具体约定，不替代或放宽项目规范。



## 全量 ImageRAG 生成与 Codex D7（2026-10-08，已完成）

本轮用户授权 Qwen-Image-2.1、BAGEL-7B-MoT 的完整 RAG 生成和评分，D7 只使用
pipeline 原生 `codex_exec`：`gpt-6-astra + xhigh`，每路配置并发8。RAG 缺点诊断是原文
ImageRAG 链路的独立阶段，沿用 `malasci/gpt-6.1-sol`；不恢复已取消的 Malasci D7 判分。

固定范围仍是 `latest_reanswer_questions__review91_20261006.lance@1` 的299题。
[本轮来源核验](runs/imagerag_full_dual_20261008/fixed_source_audit.json) 逐模型验证299份初图请求身份，
每模型208份未改题原答案、91份改题重答，与完整D7无图基线的图片和题目版本一致。
两份旧D7表各598行原样复用。最终三路范围为299题×2模型×3条件=1,794行；新增RAG条件598行，
其中保留初图的题目复用同一冻结D7输入的原评分，不等于598次新增CLI调用。

截至最终交付，两模型各299份RAG答案、六路共1,794条D7结果全部完成，技术失败0、待处理0。
原四路1,196条评分原样复用，RAG中121条同图评分复用（Qwen105、BAGEL16），新完成477次原生CLI判分。
本轮新增生图464张（Qwen181、BAGEL283），另复用13张既有Qwen RAG图与121张初图；没有重复生成有效图片。
[最终逐条审计](runs/imagerag_full_dual_20261008/final_audit.json) 对全部1,794条重算D7，
逐条核对477次新增CLI的完整冻结prompt/schema、作答图、原始响应和参数，旧评分逐字段保持。

| 生成模型 | 条件 | 判官 | 应评 | 本轮复用 | 本轮新评 | 技术失败 | 待处理 | 综合有效数 | 综合均分 |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Qwen-Image-2.1 | 无参考图 | Codex | 299 | 299 | 0 | 0 | 0 | 299 | 40.534341 |
| Qwen-Image-2.1 | 正例参考图 | Codex | 299 | 299 | 0 | 0 | 0 | 298 | 46.314224 |
| Qwen-Image-2.1 | 按缺点RAG | Codex | 299 | 105 | 194 | 0 | 0 | 299 | 49.133631 |
| BAGEL-7B-MoT | 无参考图 | Codex | 299 | 299 | 0 | 0 | 0 | 299 | 25.699105 |
| BAGEL-7B-MoT | 正例参考图 | Codex | 299 | 299 | 0 | 0 | 0 | 299 | 25.686793 |
| BAGEL-7B-MoT | 按缺点RAG | Codex | 299 | 16 | 283 | 0 | 0 | 299 | 25.387909 |

上表均分使用各路独立有效分母。三路共同有效题集：Qwen298题为
40.619227 → 46.314224 → 49.247374；BAGEL299题为25.699105 → 25.686793 → 25.387909。
Qwen的RAG相对无图在299题上+8.599290，相对正例图在298题上+2.933150；
BAGEL分别为−0.311196、−0.298884，本次没有综合均分提升。没有为改善分数修改prompt、清单或权重。
唯一综合空分仍是Qwen正例图“释迦”的旧 `invalid_criterion`；按维度保留分母和理由。

最终生成表为两模型各自的 `answers__{qwen21,bagel}_imagerag_full_complete_20261008.lance@1`，
RAG判分表为 `scores_d__{qwen21,bagel}_imagerag_full_complete_20261008_d7.lance@1`。
每份判分表含299无图基线和299 RAG结果；Notebook与旧四路表合并时只保留一份无图基线。
[逐题导出](runs/imagerag_full_dual_20261008/per_question_scores.csv) 包含完整六路分数和状态。

标准生成入口已提交 `qwen21_imagerag_full_20261008`、`bagel_imagerag_full_20261008`，
配置在同名 `configs/` 文件。Qwen查询编码和生图使用GPU1，BAGEL使用GPU0；查询端口分别8002/8008，
各自在查询物化后释放编码服务再加载生成服务，两模型可并发。生成请求并发8、诊断每节点并发4；
本地生成服务内部仍逐请求推理。启动时两卡自然释放；Qwen完成279题新增诊断、752条编码和检索后，
GPU1被随后启动的 `train300_quality_refill_20261008` 粗筛占用。按用户明确授权，通过该任务原生
`STOP` 入口排空后暂停，保全90条粗筛、87条审核回执及原checkpoint。Qwen生成结束后已于10:11 UTC
通过其原 `start.py --resume` 恢复，原配置、启动器及checkpoint身份保持，检查点已从1608继续推进到2055；
见 [恢复运行核验](runs/imagerag_full_dual_20261008/gpu_coordination/restoration_progress_verified.json)。
Qwen已完整交付 `answers__qwen21_imagerag_full_complete_20261008.lance@1` 的299份RAG答案，
其中105份保留初图、194份为RAG再生成。恢复run固定复用完整299题RAG输入和20张有效答案，
不重复诊断、编码或检索；旧失败run保持。容器文件缓存曾阻塞数据节点内存准入，仅对本任务Qwen闲置
权重作只读缓存释放建议，未改模型文件、平台或共享内存限额。

Qwen完整RAG D7已交付 `scores_d__qwen21_imagerag_full_complete_20261008_d7.lance@1`，
299条无图基线原样复用，299条RAG评分由105份同图原分与194次真实CLI新判分组成，失败0、待处理0。
[Qwen逐条审计](runs/imagerag_full_dual_20261008/qwen21_final_audit.json) 核对了所有新请求的冻结
完整prompt/schema、实际作答图、原始响应及D7重新算分。三路均有效的298题综合均分为
40.619227 → 46.314224 → 49.247374；Qwen正例图“释迦”的旧评审异常保持空分。

参考图传输恢复显式分为两类：GIF通过原生 `image_encoding=png` 使用第一帧，与检索编码的
`frame=first` 一致；超过原生服务单图2400万/合计4800万像素限制的参考图，使用
`reference_preprocessing=oversized_to_3840_jpeg90_v1` 调用平台已有受限 `resize_image`，仅缩放
超限图片（合计超限时处理该组），保留比例、最长边3840、JPEG90。原对象和检索记录保持，
答案保存原始尺寸、实际尺寸及传图SHA；生成身份包含此处理选项。Qwen两份GIF和三份超大图已恢复成功；
BAGEL对应两份GIF和五份超大图已在独立有界run恢复成功，共享主run生成服务，不重复诊断或检索。
两模型共12份恢复图片均已核验实际传图SHA、首帧像素或缩放尺寸，见
[传图恢复审计](runs/imagerag_full_dual_20261008/transport_recovery_actual_input_audit.json)。
Qwen初次仅调高客户端 `image_limits` 的三条请求仍被服务端拒绝，原HTTP400回执保留，不列为有效答案。

BAGEL一条“荸荠”caption返回HTTP408；`bagel_imagerag_caption408_20261008` 固定复用成功的decision/
concepts，仅补一次完全相同的caption请求，已完成真实检索、生成和Codex CLI评分。旧408保留，
恢复请求逐字段审计见 [恢复回执](runs/imagerag_full_dual_20261008/caption_recovery_actual_input_audit.json)。
`imagerag.reuse.caption_recovery` 只接受原协议、同题同初图且前两阶段成功的固定HTTP408诊断。
全量生成期间，已提交的新图按原题表顺序固定成有界D7批次，每模型至多一个评分批次同时运行；
最终完整D7运行必须绑定并复用这些批次，不能再次评有效结果。

Qwen复用首批20题完整诊断/检索及19份成功答案。“故宫”通过单题真实恢复，原6097×3661 JPEG
保持4,759,490字节传入平台原生`image_encoding=preserve`，成功生成；记录见
[单题回执](runs/imagerag_full_dual_20261008/palace_canary_audit.json)。BAGEL固定前2题通过完整诊断、
编码、检索、再生成。所有成功验证产物进入全量复用，不重复生成。
`imagerag.reuse` 显式绑定固定inputs、answers及原生成配置；核对题面、初图、诊断/检索配置、
生成参数、模板和原请求身份。保留旧图片的原调用与原身份，旧PNG请求不改称新preserve请求。
额外冻结配置只用于核验既有验证产物，失败图片不复用。

三张验证图片已由原生Codex CLI实际完成D7。完整文字、schema和本次作答图的逐条核对见
[CLI请求审计](runs/imagerag_full_dual_20261008/codex_cli_canary_actual_input_audit.json)。
Qwen试跑其余RAG结果另由 `qwen21_imagerag_reused20_20261008_d7` 补评分；成功结果同样复用。
所有失败保留原始回执和空分，完成度以各run固定表及最终审计为准，启动不代表全量完成。

Notebook仍为三格。结果查看支持同生成模型的「无图 → 正例图 → 按缺点RAG」，
三种差值（正例−无图、RAG−正例、RAG−无图）的维度排序、升降筛选、搜索和跳题；
另外展示三路均有效的共同题集，每维度独立报告分母。合并D7运行时只保留一份完全相同的无图基线，
遇到冲突拒绝合并；RAG明细保留初图、原文诊断、caption、检索及真实生成/评分输入。
离线结果采用完整内嵌数据，长数据无损压缩，HTML上限64MiB、展开文字上限128MiB；缩略图总预算40MiB、原图引用和全文保留。
此前Notebook原字节和输出已备份到 `runs/imagerag_full_dual_20261008/notebook_before/`。
第三格已实际执行并保存完整六路离线输出，前两格逐字段保持；紧邻最终更新前的Notebook原字节另存
`notebook_before_final_source.ipynb`。真实浏览器核对六路汇总、299题×两模型的逐题各维度分数、
三组差值的排序/筛选/搜索、跳题图文明细及RAG输入，0页面错误、0外部网络请求。
页面核验见 [交互回执](runs/imagerag_full_dual_20261008/ui_probe_result.json)，
保存身份见 [Notebook交付回执](runs/imagerag_full_dual_20261008/notebook_publication.json)。

结果汇总同时展示历史调用耗时：按生成模型和作答模式分别统计生图与D7的均值、有效计时数；
展开表列出初图生成、缺点诊断、概念提取、检索描述生成、查询编码、向量检索、RAG再生成及D7，
包括实际执行模型、调用数、缺失数、未执行题数、均值、P50/P95和已记录累计秒。执行模型取自
原始调用（编码器取自原批请求），生图模型沿用已核验的答案身份；不把诊断模型混称为生成模型。
按原始调用引用去重，查询编码
以原生批为单位，概念提取计入已保存的链内尝试。保留初图不伪造一次0秒再生成；缓存复用追溯原响应。
耗时采用历史原生调用墙钟，不是纯GPU计算时间。已只读核对平台计时边界：生图从请求渲染前开始，
直到验证并保存返回图片，期间可能包含服务启动、资源等待、请求槽排队和传输；这些已记录时间不扣除。
实际生成参数随表保留。累计作答调用秒/题包含RAG初图，不另外叠加D7、未计时的CPU检索、调用之外
的等待及独立失败run开销；不能当作端到端时延或并发吞吐。历史冷启动、并发和负载不同，也不能
直接解释成模型本身的速度差异。
向量检索及部分早期子代理判分没有保存可绑定的独立计时，保持空值，不从文件时间或配置并发推算。

用户进一步要求剔除排队、启动后比较方案效率。第三格新增**生图去噪循环**表，只读原请求所绑定的
冻结manifest中的服务日志，按模型、模式、历史批次统计均值/P50/P95及样本数，并保存日志SHA和行号。
去重tqdm最终重复进度条，核对循环步数、服务成功回执及HTTP路由；歧义和不完整记录不计入。
该环节位于请求锁内部，排除了启动和锁排队，但不包含循环前编码及循环后解码、保存；
没有CUDA event计时，不能称为完整纯GPU生成耗时，历史日志仅精确到整数秒。
原299题日志没有题目/请求ID，无法准确抽出最终复用的208题；因此原批与91道改题批分别显示，
不冒充最终299题的净生成均值。RAG保留初图不会记为新生成。
VLM API未返回服务端排队时间；查询编码已排除本地服务就绪和准入等待，但含HTTP/服务端等待/解析。
向量检索没有独立计时，无法从现存数据得出所有方案剔除全部等待后的精确总耗时，缺失不补0。
原始调用口径另列，具体边界见 `runs/imagerag_full_dual_20261008/net_inference_measurement_boundary.json`。
P50/P95仅使用有效样本，按`(n-1)*p`线性插值。四组历史生图计时覆盖均为299/299；
原始响应对账2,327条有计时生成/判分调用一致，65条早期判分缺失计时。
最终六路页面已核对6组汇总、24个阶段和12组去噪日志统计；每项有计时调用均已与原始响应对账，
见 [耗时原始回执审计](runs/imagerag_full_dual_20261008/timing_call_audit.json)。
[阶段调用耗时CSV](runs/imagerag_full_dual_20261008/timing_statistics.csv) 与
[去噪循环耗时CSV](runs/imagerag_full_dual_20261008/denoising_statistics.csv) 同时导出。
Qwen全部194次RAG再生成的去噪循环平均7.721649秒，BAGEL283次平均31.544170秒；
RAG再生成这一环节不包含初图、VLM诊断和检索，不能直接解释成整套方案比一次正例生图更快。

最新生成、复用、算分及参考图恢复相关28项隔离回归通过。三路浏览器交互（含无损压缩、离线加载）及当前Notebook入口修复
3项通过；相关扩展批次的最终状态见本轮验证日志，不相加宣称独立用例总数。
新增耗时和Notebook相关29项回归通过，覆盖原调用追溯、批次去重、缺失计时、阶段模型、去噪日志歧义和三路交互。
两组注册fixture命令分别41项、25项通过；这是独立执行的行为验证，不覆盖全局范围检查阻断。
DemiForge任务为 `t2i-imagerag-full-codex-20261008`；全局检查仍受到其他并行任务的范围外改动阻断，
不回滚其他任务、不重置基线、不宣称全局验收通过。

## 全量 D7 Codex 评分已完成（2026-10-07）

按用户后续决定取消 Malasci，剩余评分由本正式 pipeline 的 `map_prompt_async`
调用平台已有 `codex_exec` 原生 CLI 客户端执行，固定 `gpt-6-astra + xhigh`。
截至北京时间 22:26，两模型均已交付 598 条、`complete=true`，全部 1,196 条评审返回；
两个后台进程已退出，技术失败 0、待处理 0。旧阶段名 `paused_after_sample` 表示到达固定范围终点，
本轮范围是完整 299 题，并非只做 91 题或 20 题试评。没有重复生图或重评已完成的有效结果。

| 生成模型 | 条件 | 应评 | 原有复用 | 本次新增完成 | 技术失败 | 待处理 | 综合有效数 | 综合均分 |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Qwen-Image-2.1 | 无参考图 | 299 | 20 | 279 | 0 | 0 | 299 | 40.534341 |
| Qwen-Image-2.1 | 正例参考图 | 299 | 20 | 279 | 0 | 0 | 298 | 46.314224 |
| BAGEL-7B-MoT | 无参考图 | 299 | 0 | 299 | 0 | 0 | 299 | 25.699105 |
| BAGEL-7B-MoT | 正例参考图 | 299 | 0 | 299 | 0 | 0 | 299 | 25.686793 |

“原有复用”仅指交接时的 Qwen 40 条，均与固定的
`scores_d__codex_subagent_d7_remaining6_20261007.lance@6` 逐字段相同、只出现一次。
本次新增共 1,156 条。切换到原生 CLI 全量提交前已有 69 条有效结果（原有40、本次已完成子代理25、
真实CLI验证4），全部直接复用；后续两条全量 CLI run 新完成 1,127 次评分。
每路配置 8 并发、两模型各两路；实际观测峰值为 32 个独立 CLI 进程。没有继续在聊天里分发评分子代理。

Qwen 正例图的“释迦”返回两项 `invalid_criterion`：判官认为清单额外加入了题面未限定的单果数量要求。
该条核心、通用遵循、任务及综合分保持空值，质量和美感仍有效；不改清单、不补零、不为消除异常重评。
其余组的有效分母按合法 N/A 分别统计，完整分母和理由见
[最终审计](runs/d7_full_dual_20261007/final_audit.json)。同题且综合分均有效的配对比较：
Qwen 298题为 40.619227 → 46.314224（+5.694997）；BAGEL 299题为
25.699105 → 25.686793（−0.012312）。上表各路均分与配对均分的分母不同，不能混用。

固定来源为 `latest_reanswer_questions__review91_20261006.lance@1` 的299道 Review ready题。
91道改题使用各模型 `reanswer_review91_c8_20261006` 的两路答案@91；208道未改题使用
各模型 `ab_prompt1_c8_20261004` 的两路答案@299。完整题面、生成配置、参考图和模板的请求身份
已逐题核对，四路1196张均匹配，缺图0。题面、清单、作答图、D7模板和五组权重没有改变。
[source_audit.json](runs/d7_full_dual_20261007/source_audit.json) 保留最初双判官计划，当前授权和最终数量以本节及最终审计为准。

最终评分表均为版本1：

- `scores_d__qwen21_d7_codex_cli_full_20261007.lance@1`
- `scores_d__bagel_d7_codex_cli_full_20261007.lance@1`

四个流式分表均固定到 `__arm00/01.lance@300`。实际配置在同名 `configs/<run>.json`，
原生请求、参数、响应、用量及错误保存在 `calls_judge_d__<run>__arm00/01.sqlite`，结果行保留固定调用引用。
全部1,131个成功CLI请求（含4条验证）逐条核验：完整文本、实际图片摘要、schema与切换前标准节点冻结输入相同，
原始响应与落表逐项判断一致。全量提交前69条复用记录逐字段相同。初次连通阶段3个未完成对象的6次本地线程池
启动失败回执全部保留，随后只恢复缺失结果；这些历史尝试不作为当前0分或当前技术失败。

[Notebook](t2i_v2_eval_debug.ipynb) 仍固定三格，格3绑定上述两个完整run，在同一输出里按生成模型选择，
从汇总进入分页图文明细。列表按同题、同生成模型配对，兼容历史 `+正例参考图` 显示名；
支持维度排序、升降筛选、搜索及题目跳转。原输出、执行计数、metadata全部保留；重新运行格3显示最新完整快照，
不会生成图片或补发评分。实际格3执行及浏览器核对保存在
[执行回执](runs/d7_full_dual_20261007/notebook_cell3_execution.json) 与
[交互回执](runs/d7_full_dual_20261007/ui_probe_result.json)。逐题分数另有
[CSV](runs/d7_full_dual_20261007/per_question_scores.csv)，不是另设主要展示入口。

相关39项回归通过，后续失败停止与复用检查11项通过，最终Notebook两项检查通过；这些批次存在重叠，不相加声称独立用例数。
真实评分、输入审计和页面交互另列回执。DemiForge旧任务及其基线完整保留；并行任务的范围及项目配置变化
阻断全局验收。后续结果登记只维护本节，不重置旧基线、不回滚其他工作，也不代表全局验收通过。


## 固定三格 Notebook 与 D7 首批试评（2026-10-06）

当前 `t2i_v2_eval_debug.ipynb` 固定三格，均在格内选择 `RUN_ID`，不再为运行追加 cell。
格 1 从命名配置调用同一个正式 CLI，默认 `RUN_PIPELINE=False`；提交新试验需换新 ID，不能覆盖已冻结运行。
格 2 独立、只读地查看运行进度、逐算子处理漏斗及异常。格 3 独立进入汇总，点击“查看逐题明细”后分页，
可返回汇总；完整实际输入和实际参考图在各路明细，全部候选参考图仍在题目下方。
新 Jupyter kernel 可直接运行格 2 或 3；使用独立只读 CLI 读取磁盘实现，避免旧 kernel 的查看代码缓存。

监控明确区分已完成输入、待处理输入（含在途）、输出行及业务有效结果。
D 流的 `prepare_score → judge_d → finish_score → save_scores_d → report_result` 通过公开
`run_stream(on_progress, on_drain)` 的 StreamStats 留存快照；本流无折叠过滤，每次调用一行，sink `max_batch=1`，
故完成调用计数可按输入行展示，输出用 `emitted`，不使用输出行倒推完成输入。
异常中断的 drain 可能包含取消调用，完成数保留未知；成功返回 run_stream 后才标正常排空。
快照由行完成事件触发，不承诺定时心跳。惰性读表/筛选/关联以及历史运行缺失的逐算子遥测明确显示未记录。
已有结果的已提交数、有效数和失败数来自冻结表，不能冒充缺失的算子统计。

清理前 9 格 notebook（157,908,192 字节）完整归档到
[runs/notebook_archive_20261006](runs/notebook_archive_20261006/receipt.json)，原字节、输出和元数据均保留。
当前 notebook 只存小型输出；大图文报告落 `runs/<run>/case_browser.html`，通过外部 frame 引用，避免重复内嵌。
当前 Jupyter root 为项目父目录；换服务时在格 3 调整 `JUPYTER_ROOT/JUPYTER_BASE_URL`。
工程规范见根规范 S10 与 DemiForge `1.0.0-candidate.2`；本地检查状态另见任务回执，不等同生产质量认证。

Qwen、BAGEL 改题重答均完成 182/182。D7 首批固定 `reanswer_questions__review91_20261006.lance@1`
的前 20 条（不按答案或旧分数筛选），复用 Qwen 新无图、有图两路答案各 20 条。
运行 `qwen21_d7_paired20_review91_20261006`，判官 `malasci/gpt-6-astra`、`xhigh`，每路并发 8。
40 条均已落表并停在 `paused_after_sample`；有效评分无图 6/20、有图 8/20，另 26 条 HTTP 408，空分保留。
这批启动于遥测改造之前，未保存逐算子计数；查看时如实显示缺失。由于有效题集合不同，不能用各路有效均分
直接证明有图/无图谁更好；格 3 同时展示同题有效配对统计。此试评没有改动 D7 prompt、分数映射、权重或 A 的质量/美感细则。


## Review 改题重新作答（2026-10-06）

本轮已启用的重答模型为 Qwen-Image-2.1 和 BAGEL-7B-MoT，各有无参考图／正例参考图
两路，每路8并发。GPT已按用户要求取消两路；全量从未提交。Qwen两路共享GPU1，BAGEL两路共享GPU0，由平台管理服务租约和退出。
ZImage不纳入；只生成图片，不调用A/B/D判官，不改写历史答案或评分。

`--stage reanswer-source` 使用Dataset固定读取原候选@300和三个Review快照
（原批@251、恢复批@124、重出题批@2），按来源顺序取同概念最后结果，再与原正式题面比较。
最新hold不退回旧ready，重出题本地的`requires_new_answer=false`不掩盖相对原题的变化。
交付299题最新ready表和91题改题子集，两者均@1；208题题面相同；母狮无鬃仍没有正式题。
原300概念序号保存在selection_rank，执行器物理行序不作为题目身份。
完整名单及前后题面见 [重答名单](runs/reanswer_review91_20261006/reanswer_required.md)和
[CSV](runs/reanswer_review91_20261006/reanswer_required.csv)。固定清单及question_revision随题表保留，
答题只接收完整题面和实际选中的正例图，不接收考点、判据或旧分数。

来源配置为`configs/reanswer_review91_source_20261006.json`。当前启用配置为
`configs/{qwen21,bagel}_reanswer_review91_c8_20261006.json`；GPT候选配置保留供追溯，不自动启动。
Notebook第8格从运行回执的`active_runs`读取启用配置，
第9格只读进展与分页明细，展示实际请求、实际附图，并在下方保留全部参考图，iframe高度4000。
旧cell及输出保留。`parallel_answers=true`与stream_judging互斥；复用两路并发和逐题save_lance，
仅所有182个作答成功时该模型完整交付。失败留在91题分母内；连续3条技术失败停止该路，
在途请求排空后退出。空分不计零分。当前两模型共364张作答图，单路队列8。原三模型计划为546张，其中GPT两路182张未提交。

### Codex候选实现与能力核验

用户指定GPT改经`map_prompt_async`调用Codex。候选实现使用平台既有`codex_exec`传输，
控制模型为`gpt-6-astra / xhigh`，显式启用image_generation并关闭Web检索。
[answer_codex.yaml](prompts/answer_codex.yaml)保存完整prompt/schema/model声明；作答指令先由
既有[answer.yaml](prompts/answer.yaml)的标准模板渲染，保持“题面在前、参考图说明在后”，
随后连同同序实际图片交给Codex。业务不另建模型客户端、工具循环或图片生成脚本。

实际验证未通过：两次`codex_exec`试跑均返回OpenAI默认地址的HTTP401；第二次虽然传入
Malasci的`OPENAI_BASE_URL`，已保存的原生stderr仍显示连接OpenAI默认地址，不能据此声称
已经连通Malasci，也不能用该401断定Malasci不支持生图。原OpenRouter运行因HTTP402停止，
未再重试。所有失败记录独立保留，未交付或伪造GPT图片。

为单独核实服务能力，[codex_image_capability.yaml](prompts/codex_image_capability.yaml)经标准
`agentmap_async(runtime=codex)`读取已有Codex provider配置，显式打开原生生图工具；这是独立诊断，
不替换benchmark的`map_prompt_async`链路。每次仅一条探测，schema/model/prompt全部在YAML，
不调用自写模型客户端。首次180秒超时；最后一次核验在116秒完成，实际控制模型和推理等级为`gpt-6-astra / xhigh`，
返回`unsupported`：“当前工具列表不包含原生图像生成工具”，无工具生成事件、无图片产物。
据此取消本轮GPT两路182张计划，保持Qwen/BAGEL运行。结论仅限当前Malasci配置、账号与Codex会话，
不外推为供应商所有接口都不支持生图。原始请求、事件、错误和产物保存于本run目录的
`codex_image_capability*_calls.sqlite`及对应`*_result.json`；精简证据见
[能力核验回执](runs/reanswer_review91_20261006/codex_image_capability_summary.json)。

每题是一个Codex会话，最多1200秒；schema_retries=0，原生max_requests按该路题数限制会话数，
不冒充内部图像调用次数的硬限制。Prompt要求只调用一次原生生图工具，不用代码绘图或回传参考图。
平台只导出一个不超过32MiB的普通文件；业务核验其属于本次调用、可完整解码、不超过2000万像素，
且不与附图字节相同。通过后保存独立对象URI/SHA；无工具、生成/导出失败保留逐题原因。
输入图沿用正例预处理与最多5图预算。完整消息、图像摘要、运行时文件上下文、原生事件和图片产物
均由标准日志保存；查看附图时按当时选图顺序核对请求摘要，无法匹配时报错，不展示其他图片冒充。

此前用已在91题内的凯拉萨神庙验证无图/5张正例图两路，来源是固定重出题Review@2；
两次试跑均失败，没有成功图片可复用，GPT主批没有提交。实际启动、完成数与失败以
[运行回执](runs/reanswer_review91_20261006/launch_status.json)和提交表为准。

验证覆盖固定来源优先级、原题比较、hold不回退、仅生成不判分、两路交付、真实CLI边界的隔离替身、
实际附图顺序与摘要、独立文件交付和已完成响应复用；日志在本run目录。没有修改质量或美感标准。
该重答阶段当时未纳入DemiForge验收；后续三格Notebook登记与试评状态见本文开头。

## 当前 D：固定检查方向与五组评分（2026-10-06）

当前默认 [d7.yaml](prompts/d7.yaml) 的协议版本是 `t2i-v2-d-judge-7-review`（D7）。
本次只修改与离线验证，未提交真实评分或生成请求。历史 D6 正文/schema 已原样归档到
[archive/d6/prompts/d.yaml](archive/d6/prompts/d.yaml)，历史响应继续用自己的协议计算；
以下 D4/D5/D6 的实验与分数记录都是历史口径，不代表新版结果。

### 固定输入与评分边界

- D7 来源必须是固定 `uri + version` 的 Review **ready** 题表，包含完整题面、
  `question_revision` 和 `requirements_json`。两路共同固定一份范围表和清单，
  评分时不增删、拆合要求，不重新选择核心项。旧 Review 字段
  `other_instruction_following` 仅在评测输入中规范化为 `general_instruction_following`；
  原题表、要求文字、判据、依据与核心标记保持不变。
- 实际模型输入只含 `instruction`、固定 `requirements` 和该路的一张作答图，
  经 `prompts/d7.yaml → map_prompt_async`。判官不接收模型名称、历史分数、
  taxonomy、出题过程、权重或算分公式。完整渲染消息和实际图像由原生调用日志保留。
- 正确性围绕概念的核心内容与应用，分核心/非核心两组；通用题面遵循检查固定的其余
  呈现或执行要求。各方向 **0** 表示有实质性要求未满足，必要内容错误、缺失、
  或展示不足以核验；**1** 要求全部适用内容基本正确、完整且可核验，仅有轻微、
  局部、可指认的相关瑕疵；**2** 要求完整、清楚、准确且没有相关可指认瑕疵。
  判 1 必须解释具体瑕疵及其为何不构成实质性违反，不能把局部错误或必要内容遗漏
  泛化为“基本意图满足”。2 不要求额外华丽或超额完成。
- 视觉质量与美感的细项、0/1/2 档位、合法变化与 N/A 规则保持 A 原文。
  与 D6 相比，仅把跨维度引用中的“其他题面遵循”更名；两组的 2 仍要求明确的超常表现，
  不套用正确性中“无瑕疵即 2”的标准。
- 判官只返回每个固定 `id` 的 `score/issue/reason`，以及原有质量、美感项及理由。
  分数代码不审核逐字引用、排列次序、覆盖语义或证据事实，只做结构对应及算分。
  缺失/重复/未知 ID 不能形成完整分母，记为格式失败，不按已返回子集计分。
  score/issue 的空值关系及无条件方向不可填 N/A 属于结构契约；代码不判断条件是否实际触发。

### 计算、N/A 与评审异常

每项先按 **0→0、1→60、2→100** 换算。组内对适用方向等权平均，
**核心正确性只要有一项 0，整个核心组为 0**，防止其他核心项抵消必要任务失败。
非核心正确性和通用遵循各自平均，不混进核心组。新协议不再使用旧的 pass/fail
通过率及 2/3 门槛，也不把连续的任务汇总分重新伪装成一个 0/1/2 档位。

| 评分组 | 原始权重 |
| --- | ---: |
| 核心正确性 | 60 |
| 非核心正确性 | 10 |
| 通用题面遵循 | 10 |
| 视觉质量 | 10 |
| 美感 | 10 |

固定清单没有某组方向时，该组为 **N/A**，不由判官补造条目。
已有方向仅在固定 `applicability` 明确给出条件、且判官确认条件未触发时才可为 N/A；
条件是否实际触发的证据由判官判断。质量、美感的 N/A 按原规则处理。
组内排除 N/A 项；组无适用项时移出总分分母，剩余组按原权重归一化。
全部组均为 N/A 时不产生总分。任务正确性展示分单独按适用的核心/非核心 60:10 汇总；
核心判零仅作用于核心组，非核心组的独立得分仍保留。

必要内容未画出、被遮挡、过小、模糊或视角不当，属于作答展示未完成，按 0 判断。
仅决定性知识/义项未解时用 `inconclusive`；已确认判据错误、隐藏要求或排除合法呈现时用
`invalid_criterion`，均为 `score=null`，不是 N/A。
存在异常时受影响组与综合分留空，保留其他组的观察与得分；即使同组另有 0，
也不把异常伪装为完整评分，不删除异常组重新分配权重。图片读取失败单独记为技术失败，
不发判分请求、不按 0 计。

### 接入、复用与查看

正式入口仍为 `config(d_evaluation=...) → run_pipeline(...)`，一次有限配对试评，
完成指定范围后暂停。D7 默认 `reasoning_effort=xhigh`，判官模板模型保持
`malasci/gpt-6-astra`；显式配置的模型与推理等级按配置使用，不自动降级或重试。
用新运行名和新表承载 D7，不迁移历史结果。题面、模型或作答条件不匹配的旧图不会送评；
Review 修改题面后须先有对应新作答。Notebook 的“最新题目合并查看”不等于已发布的统一
Review 题表，执行时必须明确提供固定题表版本，不能把旧 299 题表当作已审交付。

结果保留 `d_protocol=d7`、`question_revision`、固定清单、完整响应、实际调用记录及
`d_core_score/d_noncore_score/d_general_score` 和对应状态，另有质量、美感、任务汇总和综合分。
`d_raw_task_score` 在 D7 中为 null，旧 `d_other_score` 仅用于历史协议。
查看器按协议展示五组明细、固定判据、实际请求与有效分数统计；A/B/D 对照表为 D7
展开核心/非核心/通用列，按各自协议重算，不将 D6 响应当作 D7。已有 notebook 输出保留，
重新执行只读查看格才会载入更新后的界面，不会自动启动评分。

### 验证与规范工具边界

离线验证覆盖分数组合、核心门槛、缺组/条件不适用、未知与错误判据、题面变化拒绝复用，
以及原生两路请求、固定输入、落表和查看。视觉质量与美感正文对 D6 逐字比较（只允许名称替换），
schema 与 A 一致。实际测试回执见本节后续记录；fixture 不代表真实判分可靠性。

评分相关 D4–D7 与已有答案评审回归 **113 项通过**；补齐旧图题面保留检查后的最终 D7
用例 **26 项通过**，定向布局/入口规范检查 **5 项通过**。Chromium 实际打开五组 case 明细
和 A/B/D 分数表，验证分组数值、搜索、真实 prompt 展示，无页面脚本错误。
完整 T2I V2 宽范围回归曾受共享执行槽等待而中断，不声明全套通过。最终检查未调用真实模型；
记录与最终源码摘要位于 [开发验证回执](runs/d7_fixed_rubric_20261006/verification.json)。

尝试登记本 pipeline 和隔离测试命令后，DemiForge 任务快照创建被现有约 127 MiB
notebook 历史输出挡住（工具单文件上限 16 MiB）。该目录会进入项目的全局扫描，故已撤回
本次未能启用的登记，避免影响其他 pipeline；原配置保留。未删除输出、调高限制或绕过检查，
**本次不宣称通过 DemiForge 完整验收**。仍执行项目规范、隔离测试和人工语义审查；
这一工具兼容问题需单独解决，不修改只读平台。

本次实现期间 `prompts/d.yaml` 在停止修改后仍多次被外部写入恢复成 D6，故定稿使用独立的
`prompts/d7.yaml` 并切换默认入口；保留原文件，避免覆盖并发修改。当前协议以默认入口和
`t2i-v2-d-judge-7-review` 版本为准，不依据同名历史文件推断。

## BAGEL 补测（2026-10-04）

用户确认增加 **BAGEL-7B-MoT 无参考图／正例参考图两路**，配置为 [bagel_ab_prompt1_c8_20261004.json](configs/bagel_ab_prompt1_c8_20261004.json)。固定同一题表@300的299题，新run包含598条答案、最多1196次正式A/B评审。评分模型与换算保持：Malasci `gpt-6-astra`、`reasoning_effort=xhigh`，A采用0/60/100分层平均，B有效分数乘10。历史Flare仍暂停，Qwen和ZImage结果保持，本次提交名单只有BAGEL。

两路分别使用GPU0（无图，8006）和GPU1（正例图，8007），各8个HTTP请求、队列8，两路同时推进。每卡独立模型实例，GPU推理逐请求执行；A/B各8并发。部署由原生 `ManagedHTTPService` 按需启动、结束释放。采用现有本地BAGEL权重与专用环境，50步、1024×1024、seed=42、think=False；部署 revision 包含服务与加载适配器源码摘要，不与其他模型或旧模板复用答案。

仍走 `PrepareAnswer → map_image_async(images_json) → FinishAnswer → A/B map_prompt_async`，使用同一 `prompts/answer.yaml`。服务将实际完整文本在前、实际图片列表按序在后传入官方 `interleave_inference`。无图路没有图片，有图路只收题目冻结正例，最多5张、超限明确失败。模型不接收出题判据。Notebook第二格按同一task_id合并七路，展示原生调用保存的真实文本和实际参考图，下方保留全部正例图；未提交、未完成、失败和暂停分别展示。

启动与验证状态以本节后续回执和 `_demiflow/evaluation_t2i_v2/bagel_ab_prompt1_c8_20261004/` 控制记录为准；只检查依赖或通过模拟测试不代表真实补测完成。

北京时间2026-10-04 **23:32:09** 已通过notebook第1格正式后台提交，父进程PID57616，两个服务均完成加载，两路均已有1024×1024实际出图，正例图路首题已完成A/B判分。首批原生请求核对已保存至 `initial_input_verification.json`，真实无图请求为0张，有图请求来自对应题目正例图，全文与传图SHA从调用记录读取。全量仍在进行，最终完成与失败数量看第2格刷新结果。接入相关32项隔离回归通过；全仓布局检查98项通过、1项在未修改的 `preparation/concepts/operators/review.py` 导入规则处失败，未将其算作本次通过。记录见控制目录 `integration_verification.json`。Notebook默认 `RUN_PIPELINE=False`，提交开关仅允许本次BAGEL run。

七路notebook只读输出已实际执行并保存。Chromium验证7路筛选、BAGEL正例图路的真实完整输入和实际1张参考图，以及第299题翻页通过，JavaScript错误0；保留4000px显示高度。查看回执为控制目录 `viewer_verification.json`，其固定快照不冒充实时进度。

## 同批20题 ImageRAG 生成对照（2026-10-08）

命名配置 `configs/qwen21_imagerag_pilot20_20261008_r1.json` 固定复用首批 D7 的
`d_questions__qwen21_d7_paired20_review91_20261006.lance@1`，20题与当前299题的对应题面和要求一致。
同一run有三路：Qwen无图、正例图各20张按完整请求身份复用重答表@91；ImageRAG最多新增20张，
原版decision判断通过时保留初图。三路顺序执行，WeMM与Qwen顺序使用GPU0；不启动A/B/D7新评分。
启动、完成或失败以本run标准摘要为准，配置存在不代表已完成。

每个历史路的 `view_scores_from={uri,version}` 仅用于只读查看，固定为
`scores_d__codex_subagent_d7_remaining6_20261007.lance@6`。查看按task_id和作答条件选取，
核对题面、question_revision、模型和实际图片引用，完整显示原D7输入与理由；缺项或不匹配明确报错。
两种历史Codex记录名仅在汇总显示中统一，原行不变。ImageRAG无评分，分数为空，不计为0。
查看运行中尚未交付的历史作答时，不以评分表中的图代替尚未生成/复用的本轮图。

Notebook仍三格；第1格兼容D7评分和命名答案生成配置，默认不执行。第3格保留全量默认值，
切换本批只需 `RUN_ID = "qwen21_imagerag_pilot20_20261008_r1"`、
`VIEW_RUNS = ["qwen21_imagerag_pilot20_20261008_r1"]`。历史全量run、输出和评分不覆盖。

首轮 `qwen21_imagerag_pilot20_20261008` 因网关不接受裸模型名而返回HTTP400，自动停流；
原manifest、已复用图片和失败调用保留。恢复run使用网关已公布的 `malasci/gpt-6.1-sol`，
原文prompt保持；不重置旧日志或把HTTP400计为有效诊断。

## ImageRAG 原版消息链路（2026-10-07，待用户审阅后发起）

`answer_mode=imagerag` 是独立作答条件，现提供
[qwen21_imagerag_20261007.json](configs/qwen21_imagerag_20261007.json) 和
[bagel_imagerag_20261007.json](configs/bagel_imagerag_20261007.json)。两者只生成答案，不自动启动判官。
当前题集为 `latest_reanswer_questions__review91_20261006.lance@1`，共299题；新run尚未提交。
老的 `qwen21_imagerag_ab_20261005.json` 保留旧题集及A/B配置，不能当作当前D7启动预设。

### 原版与平台边界

固定 [官方提交16c9502](https://github.com/rotem-shalev/ImageRAG/tree/16c9502a09b5a049f7c30f39b7f48998fd1e2526)，
选定 `omnigen_first`、无输入参考图、decision=True、only_rephrase=False。
[imagerag_original.yaml](prompts/imagerag_original.yaml) 是唯一原文审阅资产，包含三段文字、最终生图文字和执行声明；
[官方源码快照与差异](prompts/imagerag_upstream/README.md) 保留固定SHA256。
历史 `imagerag.yaml` 的 `adapted_json_v1` 仅兼容旧协议，新预设明确使用 `upstream_16c9502`，不能退回JSON改写。

业务自定义算子 `operators/imagerag_original.py` 用标准模板渲染器组装 messages、解析原始回复并推进状态。
平台 `map_prompt_async` 读取 prompt pack 的 `input_mode: messages`，每次只发一次 completion，原样传递消息；
不维护会话、不判断下一轮、不注入system、不追加JSON要求。业务持久化仍是固定schema及JSON包络，保存
raw_text、状态、各次调用引用、解析后的caption和命中信息。schema_retries=0。
这里原文没有项目常规的标题/输入输出小节，这是用户要求逐字复现的显式例外。

### 整体链路

1. 读取同题同模型的固定无图答案。Qwen和BAGEL各用自己的初图，核对request_id、题面、模型、seed、
   参数、部署revision、冻结无图模板和零参考图。初图来源是原299题答案和后续91题重答的固定版本；
   若task_id重合，显式后来源优先，同来源重复报错。缺失/身份不符进入initial_failed，不补生图。
2. 原题＋初图 → decision。仅匹配忽略大小写的 `yes` 子串即保留初图，后续零检索、零再生图。
3. 否则携带user/assistant历史 → 缺失概念。输出纯文本，**没有最多三个概念的提示**。
   大小写敏感的 `unable` / `can't` 触发最多三次尝试；三次拒答或有效空回复则用原题作为检索描述。
   网络/解析错误和离线pending保持技术状态，不冒充语义回退。
4. 有概念时携带完整历史 → 每概念一条独立图像caption。保留上游行为：首条user中初图随概念尝试和
   caption调用重复追加；普通caption调用含两份初图，最多四份。响应按官方逐行编号/引号规则清洗。
5. 每条生成caption → WeMM文本embedding → 全固定池图片embedding的精确cosine top1；
   **所有描述先检索，之后按描述顺序取前三张**。重复命中保留，不增加概念过滤、阈值或VLM重排。
6. 原题＋检索caption＋对应图片 → 同一个Qwen/BAGEL模型再生图；初图不作为编辑底图。
   最终英文模板保留 `According to these images of ... , generate ...` 及原版的图片编号文字，
   见 [answer.yaml / imagerag_original](prompts/answer.yaml)。实际图片通过平台images_json的有序image字段传入。
7. 所有题都交付为独立imagerag作答条件，保留初图也计入该路。后续D7只接原题、固定评测要求、最终答案图，
   不把检索caption/图库监督交给判官。

VLM目前为gpt-6.1-sol，编码器为WeMM，生图为Qwen/BAGEL，图库/题集为本项目资源；不是原论文
GPT-4o/CLIP/OmniGen的数值复现。OmniGen的 `<img><|image_N|></img>` 文本保留以便逐字审阅，
在Qwen/BAGEL接口中**不宣称这些文字具有OmniGen专用token语义**，图像由native image数组按同序绑定。
真实模型对该提示格式的效果尚待用户发起评测；本次只验证传输及业务行为。

### 固定输入、预算和输出

图库为 `preparation/image_embeddings/datasets/image_embeddings__a170b8529adc30d8.lance@1`：
71,600张图、4096维，启动前核对完整编码契约，不仅检查维度。检索查询来自缺失概念描述。
图库caption来源固定为全量 `captions__reviewed_dense_caption_qwen38_v3_image_hints.lance@1` 与补跑
`captions__reviewed_dense_caption_qwen38_retry_failed_20261006_r01.lance@1`，命中后按SHA左关联，用于查看和溯源。
**不使用图库caption embedding**，caption失败/缺失不移除向量命中。图库可能含出题正例，不称为外部无重叠图库。

诊断用初图按原始字节发送，无JPEG缩放；单初图最多8MiB，最多4份图片；平台messages请求上限64MiB。
VLM每节点并发4、最多299次；decision一次＋concepts最多三次＋captions一次，总上限每题5次。
文本上下文最多30,000字符、输出4096tokens；每题检索最多32条（允许显式1..64），超出报错，不截断且不改prompt。
这是运行预算，非上游概念上限，也不等价于模型token或进程RSS硬上限。最终最多3张参考图，每图读取最多32MiB。

**参考图传输（2026-10-08）：** 新建配置经 `config(...)` 归一化时，ImageRAG 作答路默认
`image_encoding="preserve"`，也允许显式指定 `"png"`；其他作答路默认 `"png"`。
此参数在正式主线透传给平台 `map_image_async`，不进入供应商的生成 parameters。
业务不实现编码、缩放或传输客户端。平台保留单帧 JPEG/PNG/WebP 的原始字节和对应 MIME；
其他可解码单帧格式转 RGB PNG，多帧在 preserve 下报错。参考图的顺序、分辨率、来源 URI/SHA、
检索 caption、原版 prompt 均保持，实际发送字节和编码策略由平台调用记录保存。
源文件、转码后单图、像素和总请求预算均继续约束；不能靠保留 JPEG 绕过像素上限。

作答缓存身份纳入非默认编码策略；旧配置没有此项时仍解释为 PNG，历史无图初答身份不变。
现有 run manifest、生成结果和 Notebook 输出不迁移；新编码须使用新 run，不能把旧失败或旧 PNG
生成结果当作此次请求。此次只改代码并进行隔离验证，不自动补跑“故宫”或提交整批评测。


WeMM使用原生VLLMService，查询物化后释放，再启动生图服务。Qwen预设GPU1，BAGEL与WeMM预设GPU0；
两份新配置应顺序启动，避免争抢查询服务/GPU。空查询不启动WeMM；全保留初图不启动生图服务。
模型服务冲突按平台规则报错，不接管其他任务服务。所有调用与请求身份使用原生日志和恢复机制。

固定图在 `prepare_imagerag` 可见：读表/关联 → 有界条件prompt节点 → map_embeddings → search_vectors → 写表。
行算子不发模型HTTP、不调用其他pipeline。历史答案、评分、公共图库只读。

| 表 | 内容与粒度 |
| --- | --- |
| rag_diagnoses__<arm>.lance | 每题一行；初图、三阶段raw_text与调用引用、全部concept_attempts、状态、回退标记 |
| rag_queries__<arm>.lance | 每题×caption序号一行；caption、查询embedding、编码契约、调用和错误 |
| rag_retrievals__<arm>.lance | 每查询一行；top1图URI/SHA/距离及关联的图库caption/状态/来源 |
| rag_inputs__<arm>.lance | 每题一行；rag_json汇总全部检索记录、前三图、初图和阶段固定引用 |
| answers__<arm>.lance | 现有ANSWERS schema；模型名增加+ImageRAG，最终对象引用与真实生成调用 |

新预设的 `<arm>` 为 `<run>__arm00`。业务JSON不持久化base64；图片是独立对象引用，完整请求由平台记录。
保留初图时 generation_seconds=0、reference_image_count=0；有再生成时caption及命中图片顺序都参与答案身份。
每题失败仍在分母，连续技术失败3次停止推进并保留已提交记录，不只交付被修复子集。
现有只读case viewer展示初图、诊断原文及概念尝试、最后一次实际完整输入、查询、参考图及最终生成输入。

### 发起及D7衔接

用户审阅后按现有正式入口 `--stage answers --run ... --table ... --version 1 --config ...` 发起，
当前notebook由并行会话专用于D7提交，RAG作答先使用上述正式CLI；本次不改其提交格或已保存输出，不创建评测run。
D7允许无图与imagerag配对，评分prompt和计算规则保持原样。答案完成后，可用只读配置装配函数：

```python
from evaluation.t2i.v2.operators.notebook import imagerag_d_config
# baseline为同模型现有的*_d7_codex_cli_full_20261007.json内容。
settings = imagerag_d_config(project, "qwen21_imagerag_20261007", baseline)
```

该函数从已完成run取真实提交的uri/version，验证同题集同模型，返回“无图＋ImageRAG”的D7配置；
BAGEL同理。未完成时拒绝，不伪造未来版本。返回配置保存后交给现有 `--stage d` 正式入口，
沿用baseline指定判官；不自动恢复旧A/B或切换判官。

### 原文链路验证（2026-10-07）

平台messages相关85项测试通过，覆盖旧模板兼容、HTTP/SSE、输入边界、请求身份、日志复用和离线回填。
业务测试用固定官方源码捕获请求，与真实fixture HTTP请求逐条比对正文、角色、历史、图片副本、temperature和
text响应格式；另用隔离Lance图库检查先全检索再取三图、初图保留、回退、JSON溯源、再生图及重放。
没有调用线上VLM、加载GPU权重或提交正式评测。两模型299题初图的固定请求身份只读核对均为299/299匹配。

### 接入验证（2026-10-05）

新增`tests/test_imagerag.py`共7项检查通过：6项完整测试批次及随后补充的1项pending调用保留检查。
隔离HTTP服务与真实Lance算子覆盖三阶段诊断、文本向量查询、cosine命中、caption/图片顺序、保留初图、
空概念回退、失败隔离、初图与编码契约核验、缓存复用、独立答案模式、A/B衔接及Chromium查看页。
这些检查没有请求线上模型或加载GPU权重。

既有评测测试首轮90项通过，唯一失败为旧notebook测试仍要求已停用的直接查看入口；更新为检查当前只读CLI后，
该项单独复跑通过。Notebook两格均可解析，原有输出、执行计数和metadata保持；真实初图请求身份与图库编码契约
只读核对通过，新run未提交。CLI帮助与本目录`git diff --check`通过。

项目布局检查`preparation/articles/tests/test_pipeline_layout.py`为101项通过、1项失败；失败位于未修改的
`preparation/concepts/operators/review.py`反向导入pipeline规则，属于原有问题，不计作本次通过。

### Caption关联验收（2026-10-06）

本次先准备、不提交评测。只读核对固定图片向量池71,600行、4096维完整编码契约、299行初图来源及初图请求身份，
并核对两个caption表的成功/失败数与发布回执一致。正式`RunTables`未存在本路线提交manifest。

`tests/test_imagerag.py`全批8项通过（187.95秒）；随后新增全保留初图场景单独通过（32.57秒），共9项。
验证包含真实Lance检索与按SHA关联、补跑caption的优先选择、查询/参考图顺序、caption失败或缺失仍保留命中、
图库描述不进入诊断及生图、Chromium展示、原生响应复用，以及全部保留初图时零编码/零再生图。
首次测试暴露异步向量检索不能直接参加原生join，已在关联前物化后复跑通过。

请求输入及媒体查看回归10项通过（9.59秒），notebook检查及本评测目录布局检查2项通过（5.03秒），
共21项相关检查通过。CLI帮助和本目录`git diff --check`通过；notebook原输出和后续D分析格保持，
`RUN_PIPELINE=False`，新路线仍为未提交。所有模型请求均为隔离测试HTTP或模拟图片后端，没有线上调用或GPU加载。

## 当前状态：五路全量评测已后台启动（2026-10-04）

用户确认全量启动后，五路于北京时间 **2026-10-04 17:00:13** 从 notebook 正式提交入口启动。历史四路的暂停记录、图片与评分保持。当前运行包含本地 **Qwen-Image-2.1 不给参考图／给正例图** 两路、**`openrouter/openai/gpt-image-2.5-flare` 不给参考图／给正例图** 两路，以及本地 **Z-Image-Turbo 不给参考图** 一路。判分统一使用 **`malasci/gpt-6-astra`，显式 `reasoning_effort=xhigh`**。

三个后台父进程分别为 Qwen `12775`、Flare `12776`、ZImage `12777`；各自启动身份、命令和日志路径保存于工作区 `_demiflow/evaluation_t2i_v2/<run>/process.json`。实际完成度以 notebook 第二格读取的固定结果快照为准，启动成功不等于全量完成。

历史配置为 [Qwen](configs/qwen21_ab_xhigh_20261004.json) 和 [Flare](configs/flare_openrouter_ab_xhigh_20261004.json)，原 Qwen 分用 GPU 0、1，保留原配置与真实调用。下一轮按用户指定 **GPU 0 专供 ZImage，GPU 1 专供 Qwen**；Qwen 两种作答方式同时启动，共享 GPU 1 上的一份 Qwen 权重，两路独立提交生图、落表和 A/B 判分，不等待另一路完成整批。Flare 继续通过 CPU 网关4002访问 OpenRouter，判分使用4001。新配置与 notebook、后台 CLI 共用，每张图生成即落表并进入判分。

固定范围为300概念批次实际产出的299题：五路共 **1,495条答案，最多2,990次正式A/B评审**。生成失败、评审技术失败、不可判和未完成分别保留；不存在的第300题不补造，出题时的 Z-Image 旧图和旧分数不作为新增一路的结果，也不用于删题。三个 run 各自落表、保留独立 manifest，notebook 按同一固定题表与 task_id 合并。

## 本轮并发配置（已后台启动）

2026-10-04 18:03（北京时间），用户因OpenRouter预算耗尽暂停Flare有图、无图两路。两路此前已因连续HTTP 402退出；已写入 `flare_openrouter_ab_prompt1_c8_20261004/pause_requested.json` 控制标记，notebook提交跳过暂停run，查看页仍保留其全部范围、已有图片和评分并标记暂停。无图已生成204张、有图175张；失败和未处理题目仍在原分母内。Qwen两路未暂停，ZImage已生成299张。恢复Flare需要用户明确指令，本次不重试付费调用。

| 作答路 | 生图并发 | 生图队列 | A 判分并发 | B 判分并发 |
| --- | ---: | ---: | ---: | ---: |
| Qwen 无图（GPU1共享服务） | 8 个 HTTP 请求 | 8 | 8 | 8 |
| Qwen 正例图（GPU1共享服务） | 8 个 HTTP 请求 | 8 | 8 | 8 |
| OpenRouter Flare 无图 | 8 | 8 | 8 | 8 |
| OpenRouter Flare 正例图 | 8 | 8 | 8 | 8 |
| Z-Image-Turbo 无图（GPU0） | 8 个 HTTP 请求，GPU 内串行 | 8 | 8 | 8 |

新配置：[Qwen](configs/qwen21_ab_prompt1_c8_20261004.json)、[OpenRouter Flare](configs/flare_openrouter_ab_prompt1_c8_20261004.json)、[ZImage](configs/zimage_ab_text_c8_20261004.json)。采用新 run、新答案缓存，不改历史 manifest。Qwen 与 Flare 的 `arm_concurrency=2`，ZImage 为1；三个任务可同时提交。五个作答分支可同时活跃，A/B 判分合计最多80个在途请求，OpenRouter 生图最多16个。

Qwen 两路由标准 `map_image_async` 调用同一个8005本地 JSON Images 服务，每路请求并发8，共享服务的全局请求上限16。平台 `SharedHTTPService` 只加载一份权重，以跨进程租约保护生命周期，最后一路正常结束时停止服务并释放 GPU；空输入或全缓存不启动。强制终止进程后可用平台 `services.manage` 的记录检查和停止遗留服务。服务内锁逐请求执行 GPU 推理：两路可交错处理，生图与各路判分重叠，但不声称同一模型同时执行多个去噪循环。Qwen 2.1 的 `image` 列表会被 batch 中所有 prompt 共用，因此每个请求独立传入自己的0张或1–5张有序参考图，不把不同题目的图片混批。本地 JSON `image` 列表由标准算子保存实际请求后发送，服务保留完整题面及图片顺序；单图上限32MiB/2400万像素，全请求64MiB/4800万输入像素。客户端16个在途请求下，服务端接收、解码和等待载荷随该上限增长；不是进程 RSS 硬限。

ZImage 经标准 `map_image_async` 调用本地8003的 Images API，客户端并发与队列均为8；现有服务用进程内锁串行推理，不将请求并发冒充8份 GPU 推理。服务声明通过平台 `ManagedHTTPService` 绑定节点，在首个未缓存且已保留预算的请求上加载本地权重，节点结束后释放。全量复用或空输入不加载模型；端口/GPU 锁冲突、启动失败会中止节点，不接管其他服务。固定 seed=42、1024×1024、8步、guidance=1.0、max_sequence_length=512；部署参数绑定显式 revision，不复用出题探测的旧图。全部299题经过本地 tokenizer 和实际 chat template 的最长长度为182 tokens，未超过512。已用模拟服务验证8个请求并发、单次启动、结束释放和全缓存跳过启动；实际 GPU 吞吐尚未运行验证。

Qwen 并发改造的隔离验证共87项通过：评测与既有本地服务回归67项、平台图片与共享租约20项。验证了两个子进程在同一GPU配置下同时提交、文本与多参考图请求保持独立、完整文本及图片顺序、共享请求容量，以及最后用户退出才停止服务。Qwen 本地运行时仅执行依赖检查，未加载权重；这些测试不代表已测得真实 GPU 并行吞吐。

用户短暂提出Codex后已撤回，最终仍保留OpenRouter Flare两路。

## 答题模板与真实请求

独立模板在 [prompts/answer.yaml](prompts/answer.yaml)。无图路只有 `{{ instruction }}`；有图路按用户明确的顺序，先放完整原始指令，再放“参考图：”及用途说明，最后绑定实际图片列表（版本 `t2i-answer-positive-3`）。参考图辅助概念辨认和主体视觉特征表达，画面中的场景、动作、关系及呈现方式仍由原始指令决定，避免无关背景或陪衬取代题目要求。两路比较的是提供概念视觉参考后完成同一道题的表现。这个顺序优先于固定前缀缓存优化。模型没有额外得到出题考点、正例判断理由或其他路的分数。混合材料的兼容方式也使用文件模板。

主线为 `PrepareAnswer → map_image_async → FinishAnswer → A/B map_prompt_async`。业务仅准备材料、落表和算分，Diffusers、HTTP、请求日志和模型释放由 demiflow 的原生图片算子负责。新缓存身份包含完整模板；实际渲染输入保存为 `answer_call_json.input_ref`，在调用前落盘。当前原生图片后端支持 Diffusers、明确选择的 HTTP 图片接口；BAGEL已配置为原生HTTP图片算子调用受管本地服务，旧 `backend=bagel` 业务直连仍拒绝使用。

notebook 最后一个 iframe 高度为4000px，保持分页。每路展示历史记录中的完整答题文本及**该次实际传图**，并可展开 A/B 的 system/user 完整输入。题目下方继续保留全部正例图。旧作答未使用新模板：展示其当时保存的原题和传图名单，注明旧请求没有保存完整 HTTP body；不会用新模板伪造历史输入。旧判分图按调用中的 data URI 摘要核对后显示。

历史记录继续只读；本轮从新 run 开始，未设置旧模板的 `reuse_answers_from`。启动后 notebook 已恢复 `RUN_PIPELINE=False`，重新运行第二格可刷新当前五路的进度及真实输入，暂停标记仍阻止误续跑旧 run。当前全量尚在进行，不能将已通过的隔离测试或首批产出当作全量评测完成。

## C评分方案：Qwen-Image-Bench官方原文（2026-10-05，仅复制待审阅）

按用户要求，原样保存官方 [checklists.py](prompts/qwen_image_bench_c/checklists.py)，包含 `SYSTEM_PROMPT`、`USER_PROMPT_TEMPLATE` 和五份完整清单：Quality、Aesthetics、Alignment、Real-world Fidelity、Creative Generation，共5个L1、23个L2、56个L3。正文保持官方英文、原占位符与JSON示例，没有翻译、精简或混入A/B规则。论文对应位置为 [附录A.3](https://arxiv.org/html/2605.28091v1#A1.SS3)。

源仓库固定为 [QwenLM/Qwen-Image-Bench@8ab1fb47](https://github.com/QwenLM/Qwen-Image-Bench/blob/8ab1fb47df2fba7b0cb046770a87f6323b98ecfc/checklists.py)，提交时间2026-06-18。原文件与 [Apache-2.0许可证](prompts/qwen_image_bench_c/LICENSE) 按字节复制；来源URL、完整commit、获取时间、文件SHA256和各提示词常量所在行记录在 [source.json](prompts/qwen_image_bench_c/source.json)。复制后已核对字节摘要，使用AST检查7个原始字符串常量，未导入或执行上游代码。

官方模板每次接收生成指令、一张生成图、一个L1维度及该维度完整checklist，输出L2/L3嵌套JSON，评分为0/1/2或N/A。官方 `judge.py` 按每题 `dims_en` 选择L1并逐维调用；这与A一次输出三个维度的结构不同。C后续启用时须明确本题库的维度选择、沿用原文完成标准YAML模板及真实图片绑定，并通过 `map_prompt_async` 落盘调用、解析和计算分数。本目录现在只是原文快照，**尚未接入C执行、计分或notebook，也未发起C模型调用**；不能从业务代码直接执行此Python文件。若后续继续使用现有Malasci判官，需标注为“官方提示词＋本项目判官”，不能等同于官方专门微调的Q-Judger结果。

## D4：Qwen 已有两路的 30 题试评（2026-10-05）

用户授权同一批 30 题、有图／无图两路比较，完成后暂停。配置 [qwen21_d4_paired30_20261005.json](configs/qwen21_d4_paired30_20261005.json)，run 为 `qwen21_d4_paired30_20261005`，于北京时间 **10:19:08** 通过本入口 `--stage d` 后台启动。现已处理全部 60 张既有答案，进入 `paused_after_sample`，写入暂停标记，原 PID 44017 已退出；无图路 30 条有效，有图路 25 条有效、5 条 `invalid_response`。因此 `complete=false` 表示尚有评审失败，不能声称 60 条均成功；本次处理范围已结束，不自动补跑或扩展。

两路都有效的同一批 25 题，D4 百分制结果如下（分别报告，不计算总分）。本轮 D4 的质量、美感档位偏离了用户要求的 A 原文口径，现仅保留为历史诊断；下述 D5 已修正模板但未重新评分，不能把本表数值当作修正版结果。

| 维度 | 无参考图 | 正例参考图 | 有图减无图 |
| --- | ---: | ---: | ---: |
| 任务正确性 | 12.00 | 16.00 | +4.00 |
| 视觉质量 | 96.41 | 97.56 | +1.15 |
| 美感 | 97.60 | 100.00 | +2.40 |

各路全部有效结果：无图 30 题为任务正确性 10.00、质量 96.63、美感 98.00；有图 25 题为 16.00、97.56、100.00。由于有效题目不同，应以上表同题配对结果比较。任务原始分分布：无图 0 分 27 题、2 分 3 题；有图 0 分 21 题、2 分 4 题、未评分 5 题；本批没有 1 分。固定前 30 行不是随机抽样，不能据此代表全部 299 题。

有图路评审失败保留原始输出，不记作作答 0 分：人民英雄纪念碑的考点结果数量不符；六角扳手、圣乔治（多纳泰罗）、中山陵的补充要求顺序不符；凤凰古城未逐字保留冻结的核心补充要求。三个维度均保持未评分，没有重试或替换样本。实际调用账本为核心准备 30 条、两路评审各 30 条，汇总回执为控制目录 `d_comparison.json`；最终三张分表分别为两路 @31、合并 @1，核心表 @31。

输入为正式题表 @300 的固定前 30 行，先冻结题目范围，再关联 Qwen 两张已有答案表各 @299；不按旧分数或可判定性挑题，不补生图、不替换缺失项。本次只消费 `answer_results__qwen21_ab_prompt1_c8_20261004__arm00/01.lance`，两个来源合计 60 张既有图片。源答案表、原 A/B 分数与其他模型任务保持。

正式入口仍为 `config(judge_model=..., d_evaluation=...) → run_pipeline`；CLI 用 `--stage d`。新增 [历史核心准备模板](archive/d5/prompts/d_core.yaml) 只接收题面、概念、消歧分类和原有考点，通过原生 map_prompt_async 逐题确定核心归属，最多 30 次调用。它不接收任何作答图、正例图、模型身份或旧评分。每题原有考点逐项给出核心性及题面引用，再补齐遗漏的核心要求；代码派生核心名单，完成阶段落盘后冻结一个固定版本供两路共用。该名单由 VLM 准备，结构校验不等于人工确认其语义可靠；完整依据保留在 notebook，可用来审阅试评结果。

核心准备结束后，两路分别从固定答案表关联同一 30 题，再通过原生 judge_d 节点评估原图。核心准备 8 并发，D 判分每路 8 并发、两路同时执行；判官均为 malasci/gpt-6-astra，显式 reasoning_effort=xhigh，单请求输出上限 32768 tokens、超时 900 秒、schema_retries=0。每路判分最多 30 次请求，总预算为 30 次无图核心准备＋60 次图评审，不扩展到余下 269 题。完整文本与 schema 计入 60000 字符预算，超限失败不截断；字符预算不是 token 容量证明。

D4 与核心准备 prompt pack 在提交前冻结到运行配置和不可变 manifest，模型节点读取冻结内容，实际调用保存最终渲染输入及图片。磁盘 YAML 后续变化不会改变本轮请求。核心准备记录在 `d_core__<run>.lance`，冻结引用在控制目录 core_snapshot.json；样本范围在 `d_questions__<run>.lance@1`；两路逐题结果在 `scores_d__<run>__arm00/01.lance`，最后合并到 `scores_d__<run>.lance`。每个结果保留源答案引用和请求 ID、生成调用、核心准备调用、固定名单、D 调用及原始结果、三个维度分数和逐项计数。

技术失败、核心无法确定、D 不可判均保留在 30 题范围内，不以 0 代替失败或缺值，也不替换题目凑够有效数。每维度分别报告均分与有效数；额外给出两路同题均有有效分时的配对均分和差值，避免不同分母冒充有图／无图差异。没有 D 加权总分。所有选中行处理完后进入 paused_after_sample 并写 pause_requested.json，进程退出；再次提交同一完成 run 直接返回，不追加评审。离线待响应状态可以通过原生日志恢复，已有固定核心名单不重新分类。

Notebook 第 3 格复用现有分页查看框架，4000px 高度，只读刷新 D 进展和三维汇总；逐题可看完整题面、已有作答图、生成时真实完整输入与实际参考图、评图前核心名单及依据、D 全部逐项证据和真实完整请求。所有正例候选仍单列于下方。原 A/B 查看在第 2 格保持，第三格不会启动或补跑模型。

用户随后要求拆分质量、美感并与 A 对照。Notebook 第 4 格只读展示固定 A 合并表 @1 与 D4 合并表 @1 的分析，主比较为四组均有效的同 25 题。无图质量 A/D4 为 67.50/96.41，美感 71.33/97.60；有图质量 67.41/97.56，美感 75.33/100.00。实际请求中 A 的 2 要求显著出色，D4 的 2 表示充分达成且无实质不足，二者同为 0/60/100 换算但含义不同；50 张图中质量 279 项、美感 95 项由 A 的 1 变为 D4 的 2。两方案 N/A 数量接近，不能把高分主要归因于 N/A。A 与 D4 输入上下文也不同，此对照不等同于只改档位措辞的受控实验。

对照已核对 60 张源答案的 SHA/生成请求身份、55 组有效评审的实际传图摘要和判官参数，并重算 A 120 个、D4 110 个维度分，与存表数值一致。控制目录 `ad_quality_review/` 保存固定来源、12 项统计、N/A 与档位分布、660 条逐项分数及完整理由、实际请求文字摘录和 HTML 查看报告。本次只读分析，不改评分、不新增模型调用；原试评暂停状态保持。

隔离验证：D 原 65 项规则检查通过；新增完整入口测试验证范围截取、无图核心准备、核心版本复用、两路原生请求及响应恢复、来源只读、不同有效分母、到样本边界暂停与分页数据生成。既有请求查看及媒体预算相关 10 项检查通过。调用使用离线 fixture，不代表真实判分质量。实际运行日志、配置及后续汇总在工作区 `_demiflow/evaluation_t2i_v2/qwen21_d4_paired30_20261005/`。

## 历史 D6 计分：代码只计算分数（2026-10-05）

按用户明确要求，当前计算版本为`d6-calculation-only-v1`。检查项是否合理、引用是否正确、
考点是否覆盖、要求是否重复、核心性与条件适用性均由判官判断；代码不再复核这些业务内容。
引用、顺序、原考点编号、要求和理由原样保留用于查看，不作为接收评分的门槛，也不自动改写、
排序或去重。此前`d6-binding-v2`的局部留空方案不再使用，历史验证回执保留。

代码读取逐项verdict、is_core、维度issues、质量／美感原分和N/A，执行既定0/60/100映射、
核心门槛、精确2/3比例、适用项平均及70/10/10/10加权。not_applicable、N/A均按判官返回值
排除，不再由代码判断某一项是否应该适用。全维度均为N/A时没有可平均项，分数留空；
模型返回的inconclusive、invalid_criterion或issues仍按既定空值规则计算，不强行补分。
结构解析及字段类型／枚举仍按prompt schema处理，无法解析、未知枚举或非法数值不伪造分数。
D4/D5兼容代码保留历史规则，A/B标准和表保持原样。

当前prompt文件逐字未变，质量、美感完整细则、档位与全部计分公式未变。
正式新评分仍由现有标准算子调用后进入finish_score；本次没有新模型调用或重新生图。
使用固定原30题×两路的60条已存响应离线验证，全部可进入计分，原25条有效响应的已有分数
逐项不变；此前因代码业务校验拒收的35条恢复接收。任务正确性无图25条、有图26条有分，
其余来自模型自身的无法判定；质量与美感两路各30条有分。原始响应和正式旧评分表未改写。
离线验证回执位于工作区`_demiflow/evaluation_t2i_v2/d6_calculation_only_20261005/calculation_verification.json`。
Notebook第3格仍读取原run历史表，不把离线验算冒充新提交的正式结果。

## A/B/D 逐题分维度对照（2026-10-05）

Notebook第7格显示同一批30题、Qwen无参考图和正例参考图共60行。每题两行，
列按任务分（A对齐、B任务分、D任务正确性）、质量（A/D）、美感（A/D）、
其他题面遵循（D）、各方案综合（A/D）排列。B只有任务分，不补造其他维度。
默认展开全部30题，支持搜索、作答条件筛选、按题分页、展开完整题面和CSV下载。

配置 `configs/qwen21_ab_d6_dimensions30_20261005.json` 固定题表及A/B、D评分表的版本1。
A/B沿用已存得分，D使用当前 `d6-calculation-only-v1` 从60条原始响应只读重算；
按task_id、作答条件、完整题面和作答图引用核对相同输入，旧的引用/顺序拒收状态不再丢弃D响应。
空分保留并注明原因，0仍显示为0；D其他遵循不适用时显示空值，综合按原规则重分配权重。
A、B、D评分标尺及综合权重不同，并列不表示相同标尺；汇总显示每列实际有效数量。
第3格保留原历史评分表，第4、5格保留历史分析，最新逐题换算以第7格为准。

只读刷新走同一正式入口，不调用模型、不改写业务评分表：

```bash
python -m evaluation.t2i.v2.t2i_v2_eval_pipeline \
  --stage view --run qwen21_ab_d6_dimensions30_20261005 \
  --score-table-config evaluation/t2i/v2/configs/qwen21_ab_d6_dimensions30_20261005.json
```

输出到 `runs/qwen21_ab_d6_dimensions30_20261005/` 的 `score_comparison.html`、
`per_question_scores.csv`、`score_comparison.json`；JSON保留固定来源、计算器版本与SHA256。
Notebook通过独立只读CLI加载当前实现，以4000px容器展示同一HTML。

## D6/D7 判分的 Malasci SSE 流式切换（2026-10-07）

D6/D7 的判分节点仍使用 `map_prompt_async` 和原有 D prompt/schema；判官配置现在支持
`stream=true` 与 `stream_include_usage=true`，由 demiflow 在 HTTP 层发送 SSE 并在节点内拼接为
完整 JSON，再继续执行原有校验和算分。A/B 历史评分入口不因本次改动切换，评分 prompt、模型
`malasci/gpt-6-astra`、`reasoning_effort=xhigh`、图片输入和权重均不变。

流式请求的 wire identity 与原非流式请求不同，不能复用旧非流式调用日志；已有运行表和调用账本
保持只读。单请求验证配置为 [qwen21_d7_stream_canary_20261007.json](configs/qwen21_d7_stream_canary_20261007.json)，
并发为 1、范围为 1 题两路，已于 2026-10-07 完成。两路实际请求均记录 `stream=true`、
`stream_options.include_usage=true`，返回 `transport=sse`、`stream_complete=true`、
`content-type=text/event-stream`，并通过原 D7 schema、`finish_score` 和 `save_scores_d`。
无图路耗时 305.13 秒、首个数据块 9.995 秒、最大块间隔 14.436 秒；有图路耗时 268.13 秒、
首个数据块 9.104 秒、最大块间隔 14.913 秒。完整证据在
`_demiflow/evaluation_t2i_v2/qwen21_d7_stream_canary_20261007/stream_verification.json`。
流式不能绕过上游 Cloudflare 在首个数据块前的 120 秒响应窗口；本次不增加自动重试，也未启动全量评测。


## 历史 D6：四维单次评审（2026-10-05，30题试评已停止）

当时使用的 `t2i-v2-d-judge-6-review` 已保存在 [D6归档](archive/d6/prompts/d.yaml)。正式入口为
`config(d_evaluation=...) → run_pipeline`：固定题目范围，关联已有答案，每张图通过一次
`map_prompt_async('judge_d')` 完成评审，再由代码计算分数。D6不提交要求准备请求，
不生成准备表或准备快照，现役 `d_core.yaml` 已删除。旧D4试评继续暂停。

北京时间2026-10-05 13:55:46，已按用户确认通过正式CLI后台提交
`qwen21_d6_paired30_20261005`（PID29570）。[运行预设](configs/qwen21_d6_paired30_20261005.json)
固定复用旧D4范围表@1的同30题、Qwen无图／正例参考图答案表各@299，共60张已有作答；
不重新生图。判官为malasci/gpt-6-astra + xhigh，两路同时执行、每路8并发，达到30题后暂停。
Notebook第3格已切换本轮，只读刷新进度和分页明细；其余历史比较输出保留。
实际结果与失败以新run表为准，启动不代表全部有效评审完成。
首批两路真实请求已核对：固定模板、完整题面／考点、一张对应作答图，以及模型和xhigh参数均匹配；
核对回执为控制目录`initial_input_verification.json`。

本次60次真实请求均已返回，后台已退出，状态为`paused_after_sample`；未扩展到其余题目。
无图11/30条、正例图14/30条通过响应校验，合计25条；其中正例图1条任务正确性不可判。
其余35条为invalid_response：22条逐字题面引用失败，13条检查项顺序失败（已核对原考点编号均覆盖）。
这些是判官响应问题，不计为Qwen任务失败，不补零、不自动重试；`complete=false`如实保留。

| 维度（均分／有效数） | 无参考图 | 正例参考图 |
| --- | --- | --- |
| 任务正确性 | 0.00／11 | 23.08／13 |
| 视觉质量 | 67.79／11 | 69.08／14 |
| 美感 | 66.06／11 | 76.67／14 |
| 其他题面遵循 | 86.36／11 | 76.92／13 |
| 综合分 | 22.02／11 | 37.78／13 |

以上为各路有效样本均分，样本集合不同，不能作为完整30题的成对比较。任务正确性、质量、美感及综合分
只有7题两路同时有效，其他遵循为6题，Notebook第3格单独展示这些配对分母与均分。
最终表为`scores_d__qwen21_d6_paired30_20261005.lance@1`，两路分表各@31。
全部60个实际输入均已核验当前固定模板、题面、对应作答图和模型参数，模型调用为60次、
重新生图及要求准备均为0次；固定代码与manifest相符。完整验证及逐条失败原因见控制目录
`final_verification.json`，真实notebook查看输出已更新。

输入只有完整题面 `instruction`、原有考点 `test_points` 与一张实际作答图。
出题背景 `concept`、`taxonomy` 仅留在业务记录和查看器中，不进入模型输入。
概念义项与任务条件以实际题面为准；固定正文、变量及图片均由标准模板与算子装配。

任务正确性围绕题面所涉及概念的核心内容及其在指定条件下的应用，检查身份、关键结构、动作关系、
必要状态及其可判断性，不只检查原有test_points。补充要求须有题面引用并直接服务于该知识任务。
其他题面遵循承接题面明确但不决定知识任务成立的外围呈现要求，不进入正确性的分母。
颜色、数量、材质、背景等按本题作用归属，不按属性名称固定分类。

单次响应中的 `task_correctness` 含 `checks`、`issues`；没有其他题面遵循要求时，`other_instruction_following` 返回 `null`，有要求时返回同样的两字段对象。不能判断已有要求是否满足时保留inconclusive，不用null豁免。
每个检查项记录完整要求、逐字题面引用、原考点来源、条件、结论和一份合并的证据理由；
只有任务正确性填写 `is_core`。编号及维度由代码派生，不要求模型重复输出
index、dimension、scope_reason、evidence、coverage_reason、basis或instruction_conflict。
题面冲突和不能解决的判据问题归相应维度issues，图片错误归相应检查项。
pass/fail/inconclusive/invalid_criterion/not_applicable的判定含义分别放在任务正确性与其他题面遵循的“逐项结论”下；
输出说明只引用相应规则，不将判定标准埋在字段说明中。

每张图的要求和结论在同次响应内形成，不再声称有图／无图共用一份预先冻结清单。
提示词要求内容、粒度和核心性只由题意决定；不同作答间的拆项一致性须结合实际评审记录检查。
原考点覆盖、重复要求、引用与条件适用性由判官判断，代码按返回结果计分；
每张图两组合计最多100项，超限保留无效响应，不静默截断。

质量与美感的8项、4项细则保留A原文，正常达标为1、显著超出通常合格水平才为2。
本次删除准备步骤、简化输出时，完整质量与美感章节（含档位、N/A、风格和错误归属）与修改前逐字一致。
这两个维度仍分别输出原分及同名理由，不随任务正确性输出调整。

| 分量 | 计算与空值 |
| --- | --- |
| 任务正确性 | 只用本维度适用项：任一核心失败或通过率小于精确2/3为0；全部核心通过且至少2/3但不足100%为60；全通过为100。未知仍保留分母，按上下界及核心门槛判断能否确定档位；无适用核心时不可判 |
| 质量、美感 | 完整沿用A；各细项0/1/2映射0/60/100，排除判官返回的N/A后等权平均 |
| 其他题面遵循 | 100×通过数/适用项数；未知保留分母和上下界，确定分留空；没有要求或条件全不触发时为not_applicable，分数留空，不送100 |
| 综合分 | 按70/10/10/10汇总；仅其他遵循明确not_applicable时前三权重除以0.9。任一应评维度未知、无效或技术失败时留空，不重分配权重。综合分不改变任务正确性结论 |

权重、换算和比例汇总仅在代码与说明中维护，不进入模型正文。
prompt要求判官为条件不触发提供 `applicability`；代码按返回的not_applicable计算，不另行复核。
invalid_criterion或issues使受影响维度无分，其他有效观察保留。
完整响应在 `d_json`；计分所需元数据由该响应派生到沿用的 `core_requirements_json`，
`core_plan_json`、`core_call_json` 在D6中为空，没有隐藏的准备调用。
四维、综合分、有效分母与换算细节分别保留。

Notebook新结果只显示一次判分的完整实际输入、作答图、分维度检查与分数，不再显示准备状态或两路共用清单。
第3格显示本次新run的只读实际输出，其他历史输出保留，最后的说明格同步本轮；旧评分不改写。
D4/D5只读取 `archive/d5/prompts/` 的旧模板并保留原协议，不将旧请求解释为当前D6。
D6配置拒绝core_prompt_pack，避免重新引入准备步骤。当前review稿的模板、schema与代码由运行身份固定，旧run不能以新配置续写。

验证：82项相关离线检查通过（44.50秒），覆盖D6单次原生请求、空维度null、未知保留、0/60/100与四维换算、历史D4/D5、notebook契约，以及Chromium实际查看与翻页。
真实渲染输入只含题面、考点、一张作答图，没有准备请求、准备表或快照；模型返回的字段也按新契约校验。
质量、美感完整章节与本次改动前逐字一致，8项/4项正文及分数/理由schema与A一致。此处82项为运行前离线验证；随后真实小批状态见本节开头。
验证回执在工作区 `_demiflow/evaluation_t2i_v2/d_prompt_v6_review_20261005/single_pass_compact_review.json`。

## 历史D5：质量、美感完整恢复 A 原文（2026-10-05，未重跑）

用户明确质量、美感应整体直接复制 A，标准不变。D4 将这两部分的“正常达标为 1、显著出色为 2”改成“充分达成为 2”，属于实现偏差。当时更新为 `t2i-v2-d-judge-5-review`，现保留为 [D5归档](archive/d5/prompts/d.yaml)，完整逐字复制 A 的“二、统一判断规则”“三、评分档位”“五、质量”“六、美感”，含通用边界、证据要求、8 个质量项、4 个美感项、N/A 与错误归属；删除 D 自行增加的宽松满分条件和“1 必须有具体不足”输出限制。复制段落的适用范围明确为质量与美感，其中“对齐”用于与任务正确性划分错误归属，任务项继续使用 D 的结论、未知处理和计算规则。

任务正确性正文从真实 D4 固定快照保持原字节，仍为核心要求全部通过＋精确 2/3；响应 schema、0/60/100 映射和逐维计算保持。A/B 原模板及旧结果不变。该阶段新配置默认读取 D5（当前已改为D6），显式冻结的 D4 pack 仍可读取，不按新文本解释旧调用。查看页显示该次 manifest 的实际 prompt_version。66 项隔离离线检查通过，其中标准算子的真实渲染请求逐字核验四段 A 原文，防止只复制细项而改动整体档位。本次没有新模型调用，旧 D4 试评继续暂停。

按用户要求，Notebook 第 5 格新增只读 A/B/D **逐题整体分**比较，不拆考点或补充项。固定范围仍为这 30 题：A 只取对齐分，B 用原分×10，D 用已有 D4 任务分。无图 B 有 3 题、正例图 B 有 4 题 inconclusive；正例图 D 有 5 条格式失败，全部保留为空分。六列均有有效分的同 21 题，A/B/D 无图均分为 39.65/38.10/14.29，有图为 40.71/46.19/19.05。三者含义不同，数值并列不表示同一标尺。

控制目录 `abd_task_review/` 的 HTML 和 CSV 保留全部 30 题的两路三分，JSON 记录固定题目表 @1、A/B 合并表 @1、D4 合并表 @1、空分及分母。已核对全部 60 张源答案身份与 A/B 实际判分传图摘要；不补分、不重跑，也不把旧 D4 分数冒充 D5 输出。

## 历史D4/D5评分方案：核心要求全部通过＋总体通过率至少 2/3

[d.yaml / judge_d](prompts/d.yaml) 当前为 `t2i-v2-d-judge-5-review`。任务正确性沿用已确认的 D4“三个至少对两个”：采用评图前固定的核心要求门槛，再按总体通过率分档。阈值为精确 2/3，不使用此前讨论的 60%、80% 或四舍五入后的 67%。质量 8 项、美感 4 项及整体判断和评分档位完整复制 A 原文，分别报告。A/B 模板和 C 官方原文保持原口径。

| 条件（题面及判据有效，证据可确定） | 任务原始分 | 展示分 |
| --- | --- | --- |
| 任一核心项 fail，或总体通过率低于 2/3 | 0 / fail | 0 |
| 全部核心项 pass，且总体通过率至少 2/3、低于 100% | 1 / partial | 60 |
| 全部检查项 pass | 2 / pass | 100 |

总体通过率以 point_results 和 additional_requirements 合并后的全部检查项为分母，逐项 pass=1、fail=0，每项等权，核心项也只计一次。核心身份另外作为必要通过条件，不做倍率加权。代码用 `3 * passed >= 2 * total` 比较精确边界。三项中两项通过且覆盖全部核心要求得 1；三项全通过得 2；通过两项但唯一核心项失败仍得 0。四项至少通过三项、五项至少通过四项，才达到比例门槛。原通过率和计数保留，2/3 的任务通过率不能与展示分 60 混淆。

### 评图前固定核心名单

模型输入 payload 必须包含 core_requirements，结构为 `{"point_indices": [1], "additional_requirements": []}`：point_indices 以从 1 开始的原序编号引用核心考点；additional_requirements 每项为 `{"instruction_quote": "题面原文", "requirement": "完整的核心要求"}`，引用原有考点尚未覆盖的核心要求。两者合计至少一项。补充核心要求不依赖评图后生成的编号，通过固定的原文引用和要求文本精确绑定。

核心名单应在看到作答之前根据题面目标准备并复核，同题各模型及参考图条件使用同一份名单；名单随标准请求的 payload 留存并进入请求身份。来源字段不决定重要性，不自动把全部原有考点视作核心。判官不能删除、降级、改写名单，也不输出一份新的核心分类。固定的核心补充要求须原样进入响应 additional_requirements，其他尚未覆盖的明确要求继续补充；缺失或改写核心补充项会被校验器拒收。核心为未知时不能推定完成。

[validate_d_core_requirements](operators/d_scores.py) 提供评图前输入校验，score_d 也复用它：缺少名单、空名单、重复或越界考点编号、补充要求原文引用无效均报错，不回退到仅看通过率。这个函数核验输入结构和绑定，不读取作答图，也不证明名单的语义正确或制定时间；调用方须保存并跨作答路复用已冻结的题目配置。当前通过无图核心准备节点为已授权 30 题生成并冻结名单；其余题目不处理。本轮 VLM 判断及其依据全部留存，尚不代表人工确认了所有核心归属。

### 检查范围与未知项

补充要求按独立要求列项，逐字引用题面，给出图中证据和结论；coverage_reason 说明两类检查项如何覆盖题面。禁止重复补充已覆盖要求，或把同一要求改写成多个同义通过项。原有考点保持输入数量、顺序与粒度；复合考点的所有条件满足才 pass，任一明确违反为 fail，无明确违反但无法核实则 inconclusive。比例仍受既有考点粒度和补充项划分影响；结构校验不能识别所有语义重叠，后续 case 审阅须检查这一点。核心名单本身也须根据题面语义审阅。

未知项不当失败、不从分母删除。设总项 N、通过 P、未知 U，总体通过率范围为 [P/N, (P+U)/N]。有明确核心失败则得 0；核心全部通过后，两端落同档才定档，否则空分。核心中存在未知项时，只有即使所有未知项通过也达不到 2/3，才确定为 0，否则空分。存在未知项时不报告一个确定的通过率。例：核心均通过，9 项中 6 通过、2 失败、1 未知，范围 2/3–7/9，可确定为 1；若该未知项属于核心，则为空分。6 项中 3 通过、2 失败、1 未知跨 0／1 边界；3 项中 2 通过、1 未知跨 1／2 边界，均为空分。

任一 invalid_criterion 或题面自身存在无法同时满足的冲突（instruction_conflict=true），优先保留整体 inconclusive，分数、通过率及上下界为空，即使其他核心项有失败也不能把出题问题归责于作答。所有逐项观察与可独立评价的质量、美感仍保留。明确要求展示却没有做到属于 fail；题面允许的不可见内容保留 inconclusive。技术读取失败不能当作空白图评分。

### 输出与候选实现边界

模型只输出逐项结论、题面冲突标记与证据，不输出总体 verdict、任务分、通过率或核心名单。[score_d](operators/d_scores.py) 使用同一请求的 instruction、test_points、core_requirements 校验后派生 task_correctness_verdict、task_correctness_raw_score（0/1/2/null）、task_correctness_score（0/60/100/null）、task_correctness_counts（全部项数与状态计数）、task_correctness_core_counts（核心项数与状态计数）、task_correctness_pass_rate（无未知且题目有效时的实际比例）及 task_correctness_pass_rate_lower／upper（有效题目的可能范围）。比例用 0–1 表示。无效题目的比例为空；有效题目即使核心失败、任务为 0，仍保留实际通过率，便于解释为什么高通过率没有通过核心门槛。

质量、美感完整沿用 A：逐项 0 表示该项准则所述的明显缺陷，1 表示正常达标或准则允许的轻微偏差，2 需要具体可核验、显著超出通常合格水平的表现；正文、N/A、归项和证据要求均以复制的 A 原文为准。以 0/60/100 映射后排除合法 N/A 等权平均，单独报告有效项数。三个分量分别报告，不定义三维加权总分。跨题均分须报告有任务分的题数、固定范围总题数，以及不可判、技术失败和未完成的数量及原因。2/3 是用户确定的任务门槛，不是拟合真实评分分布得到的阈值；不保证一定减少 1 分。D5 与历史 D4 的质量、美感口径不同，报告须注明版本。

正文遵循 PIPELINE_SPEC S09，以内容组织 Markdown 标题，保留固定前缀；动态输入在末尾通过 payload | json 与 images | numbered_image 绑定。完整原始题面、概念与消歧分类、原序考点、固定核心名单和一张实际生成图进入判分；生成时的正例参考图、作答模型名与旧分数不进入 D 输入。实际作答完整请求及参考图仍由生成调用日志保存。模型为 malasci/gpt-6-astra，启用时标准节点须显式设置 request_options.reasoning_effort=xhigh。完整正文、动态材料、schema、图片及输出须纳入请求预算，超限保留失败，不截断；本轮未验证网关 token 容量。

D4 已接入本入口的有界已有答案试评、独立业务表与 notebook 第 3 格，实际授权范围及运行状态见上节。每次先固定核心名单，使用新运行和新结果表，不将旧 D 或 A/B 结果直接重解释成 D4。格式与绑定错误为 invalid_response，不计作作答失败。

验证见 [test_d.py](tests/test_d.py)：65 项离线检查通过，覆盖原生 map_prompt_async 完整输入与离线响应恢复、精确 2/3 与 100% 边界、核心失败不被多数通过项抵消、核心补充项与核心考点同等生效、核心名单不可缺省或绕过、未知核心与比例范围、无效判据优先，以及质量／美感原换算。离线回执保存在 `_demiflow/evaluation_t2i_v2/d_prompt_v4_review_20261005/verification.json`；fixture 不代表真实模型判分质量。

D4 最终回读时曾发现正文回到 D3 的 80% 版本，写入来源未确定；已从实际 D4 离线请求恢复完全相同字节，65 项重新通过，并核对磁盘文件与两次运行的 6 份请求。D4 固定快照与恢复证据同放上述回执目录。

历史记录：D2 的 32 项验证与正文回退取证保存在 `_demiflow/evaluation_t2i_v2/d_prompt_v2_review_20261005/`；D3 的 45 项验证和 80% 候选口径保存在 `_demiflow/evaluation_t2i_v2/d_prompt_v3_review_20261005/`。历史快照仅供核验，当前维护入口为上节D6 YAML。

## A/B评分标准与prompt审阅

两份正文、版本、模型声明和响应 schema 均放在本流程的 YAML prompt pack 中；本 README 只说明差异与接入边界，不另存同正文的 Markdown 副本。

| 事项 | 标准评分 A：[tasks.yaml / judge](prompts/tasks.yaml) | 探测评分 B：[probe.yaml / probe_judge](prompts/probe.yaml) |
| --- | --- | --- |
| 当前候选版本 | `t2i-v2-general-judge-2-review` | `t2i-v2-probe-judge-1-review` |
| 要回答的问题 | 完整题意是否实现，图像质量与美感如何 | 本题设计的知识考点是否由图像正确实现 |
| 评审输入 | 实际题面、同一张作答图 | 实际题面、同一张作答图、概念、用于消歧的分类、原序 `test_points[{point,basis,criterion}]` |
| 知识依据 | 判官根据题面自行确定可靠且必然的约束 | 先审查出题者提供的依据和判据，再逐项核对；判据不是事实证明 |
| 输出 | 对齐 10 项、质量 8 项、美感 4 项，每项分数及证据 | 逐考点 pass/fail/inconclusive/invalid_criterion；总体 verdict、0–10 分、错误类型和分类辅助字段 |
| 合格的含义 | 普通满足为 1；映射后是 60；2 要求具体卓越证据 | 原始 pass 为 7–10、partial 为 4–6、fail 为 0–3；乘 10 后分别为 70–100、40–60、0–30 |
| 汇总 | 0/1/2 映射为 0/60/100，排除 N/A 后分别计算三个维度均分，再对有效维度等权平均得到 A 总分 | 保留按核心作用判断的原始总体分数，有效分数直接乘 10 得到 B 总分；inconclusive 的 -1 不乘 10，按未评分处理 |
| 看不见关键部位 | 明确要求展示但未展示记 0；合法不可见且无其他可核验要求时为 N/A | 遮挡、裁切、太小、模糊等记 inconclusive；充分可见区域的确实缺失才判 fail |
| 判据本身有问题 | 没有外部逐题判据；不可靠的隐含要求不纳入 | 标出 invalid_criterion，整题 inconclusive；保留其他逐项观察供修题 |
| 无关质量与审美 | 单独评分 | 不评；畸变仅在破坏核心内容时影响结果 |
| 当前接口 | 并发评测链已接入三维分、A总分及适用项数；旧独立 `run_judging` 仍输出原三维字段 | 并发评测链已接入原生prompt节点、逐考点校验、原始分及×10总分，并在notebook展示 |

两份都不向 judge 发送作答模型名、Z-Image 旧作答或旧分数、作者正例图和历史分类标签。B 的 taxonomy 仅用于确定概念义项，不能作为事实证据。B 保留 `case_annotation`，是为了保留原出题探测的联合评审契约；它是辅助诊断，不参与图像得分。本稿未擅自删成“纯评分版”，也不把同一次联合判断称为独立盲评。

### 已确认的计分方式（2026-10-04）

**A：Qwen-Image-Bench 式映射与分层平均。** 已核对 [官方计分实现](https://github.com/QwenLM/Qwen-Image-Bench/blob/main/score_utils.py) 及 [官方汇总说明](https://huggingface.co/datasets/Qwen/Qwen-Image-Bench)：原始 0/1/2 映射为 0/60/100，N/A 排除，逐层对有效子项等权平均，最后对有效顶层维度等权平均。

本项目保留当前“对齐／质量／美感”三维与 10/8/4 个评分项，未另设中间分组；每项作为当前维度的直接子项。采用的是官方的计算方法，不把当前题目和维度树冒充官方完整 benchmark，也不因为本次计分调整增加其他评审维度。

```text
映射：φ(0) = 0，φ(1) = 60，φ(2) = 100
维度分 D = mean(该维度内非 N/A 项的 φ 分数)
A 总分 = mean(非空的 对齐分、质量分、美感分)
B 总分 = 有效原始 score × 10
```

A 的三个维度均有效时，总分就是 `(对齐分 + 质量分 + 美感分) / 3`，不能把 22 项混在一起平均而使 10 项对齐获得更大权重。整个维度全为 N/A 时该维度为 null，不参与总分；所有维度都为空时总分也为 null。计算过程中保留精度，最终展示时再保留两位小数。例如三个维度分别为 60、100、0，A 总分为 53.33。

**B：直接乘 10。** 模型继续输出原始整数 0–10，经过 verdict／score 一致性校验后再由代码乘 10，例如 8 分显示为 80 分。原始响应中的 -1 只表示 inconclusive，不转换成 -10；不可判、判据无效和技术失败的业务评分均为 null，单列状态、不计入有效分数均值。保留原始分数及逐项证据，不再按通过项数量重新计算 B。

批次分先计算每道题的 A／B 总分，再分别平均有效题目的总分，同时报告有效题数和未评分原因；不先对全批三个维度求均值再合成，以免各题适用维度不同改变题目权重。两份原始 prompt/schema 无需因确定性计分换算而改变，不要求模型自行做平均或乘法。后续展示并列 A、B 百分制结果，其判断口径仍按上表分别解释。

### 本批固定来源与实际范围

来源为参考会话“继续 T2I V2 基准合成”的运行 `ready_positive300_common85_codex_v16_probe_20261004`。生产者的 [final_audit.json](../../../benchmark/t2i/v2/runs/ready_positive300_common85_codex_v16_probe_20261004/final_audit.json) 已记录固定交付：

- 概念名单：`demiwtg/benchmark/t2i/v2/datasets/cohort__ready_positive300_common100_priority_20261004.lance@4`，300 个概念。
- 本次题表：`demiwtg/benchmark/t2i/v2/datasets/candidates__ready_positive300_common85_codex_v16_probe_20261004.lance@300`，299 道题，使用其中原文 `instruction` 和原序 `test_points`。
- 出题记录：`demiwtg/benchmark/t2i/v2/datasets/designs__ready_positive300_common85_codex_v16_probe_20261004.lance@301`；“母狮无鬃”为出题不足，保留在 300 概念的范围说明中，不虚构第 300 题。

该批题表目前仍标 `unreviewed`；这里绑定用户指定的正式批次，不将其改称已经通过独立题目质量审核。历史 Z-Image 有 299 张图、281 条有效评审和 18 条技术失败，其中 10 条是逐项与总体结论矛盾。旧评审失败不用于筛掉对应题目，也不自动重试；本次 Qwen 评测应以全部 299 道已产出题为作答分母，另列 1 个未出题概念。

### A 的 review：从 V1 借鉴什么

参考 [V1 judge](../v1/prompts/tasks.yaml) 的具体判断解释，保留 V2 的输入范围、22 个评分字段、0/1/2/N/A 刻度、禁止重复扣分及三维独立汇总。

| 审阅发现 | 本稿处理 |
| --- | --- |
| “高精度约束精确完成”没有解释如何核验 2 分 | 补回计数组别、成段文字、精细纹样、光学关系和装配对位等判断角度；普通正确仍为 1，不自动奖励难题或华丽画面 |
| 若干对齐项只有一句范围清单 | 展开主体身份与主视觉的区别、形状类别与轻微比例差异、数量归属与尺度参照、空间组织、动作接触、过程状态和场景风格的具体边界 |
| 隐含要求的寻找方法不够具体 | 补充概念构型、限定阶段、对象关系、剖面展示等检查顺序；逐条仍须通过可靠性、必然性与可见性判断 |
| 质量项容易对同一模糊或表面问题重复扣分 | 恢复像素化／伪影／轮廓模糊／合成感的归属解释，补充重要材质的判断实例 |
| 未确定的隐含要求可能被误写成通过证据 | 说明不能由未知给通过分；整项没有已确定且可核验的要求时填 N/A，并说明未能确定的内容 |
| 正文开头谈“沿用 V1、精简说明”，末尾让模型阅读确定性汇总逻辑 | 删除维护历史和代码计分段；计分映射仅由代码执行、在本 README 解释 |
| 固定输出说明出现在动态题面之后，业务示例与 result 外层关系不直观 | 输出节提前明确 result 包装，所有固定规则置于“本次输入”之前；题面与图片只在末尾注入 |

没有照搬 V1 中“悬挑必然下垂”“刚切开即变褐”“触地即推出扰流板升起”等容易漏掉刚度、时间或动作条件的示例；事实是否构成约束仍须按题面实际条件判断。也没有带入 V1 的 `image_model` 输入、动态题面位于规则前的布局、稀疏 reasons 示例或更宽松的响应 schema。

本稿保留的政策限制也需在选型时知晓：普通正确的对齐通常只得 60，简单题不一定有达到 2 的要求；一个维度的均分也不等于整题通过率。主体缺失可能使依赖项全部为 N/A，但质量和美感仍可得分。A 总分和 B 乘十后的分数按用户要求统一为百分制并列展示；两者分别衡量完整题意与成图表现、出题考点完成度，不把任何均分称为知识考点通过率。

### B 的 review：保留出题探测口径

来源是 [benchmark V2 的 review_answer](../../../benchmark/t2i/v2/prompts/tasks.yaml)，版本 `t2i-v2-probe-joint-review-1`；原批审核记录中的文件 SHA256 为 `9a052dfe19ebd38e9db6e55458df83325020490ac107e1c3707613443cd50d9e`。原文件、历史分数与旧运行均保持不变，评测变体在本目录独立维护。

改动限定为：把“Z-Image 实际收到的题面”改成不泄露作答模型的说法；按“任务／输入／核心判断／补充边界／输出”整理；补充正确、错误、不可判和判据越界的例子；将 [check_review](../../../benchmark/t2i/v2/operators/probe.py) 已执行的结论一致性要求明确写入正文。response schema 保持一致，没有增加权重或新类别。

- 有 invalid_criterion 时，总体为 inconclusive。
- 全 pass 总体 pass，全 fail 总体 fail；无已确认通过或失败则 inconclusive。
- 任一 fail 禁止总体 pass；partial 必须同时有 pass 与 fail。fail 与 inconclusive 混合不能以猜测的正面表现凑 partial。
- 混合结论仍由核心作用决定；关键身份错误可导致总体 fail，即使另有通过项。分数区间、failure_type 与 verdict 保持一致。

这些补充针对原批暴露的响应矛盾，不代表已经用模型验证能消除矛盾。0–10 各区间内部仍依赖整体判断，原标准没有逐分值的细粒度锚点；本轮不为追求表面精度发明加权公式。

### 用实际题目检查评分边界（文字推演，不是实测结果）

| 本批题目与假设作答 | A 的判断边界 | B 的判断边界 |
| --- | --- | --- |
| U形谷：题面明确要求完整横断面，但作答将前缘遮住 | 明确展示要求未完成，对应项为 0；同一遮挡不多项重复扣分 | 该题原 criterion 明写“横断面缺失或受遮挡则证据不足”，逐项 inconclusive |
| 非球头 L 型六角扳手：工作端清楚画成圆柱 | 根据可靠概念知识，在对应对齐项判错 | 根据原考点及判据判 fail；总体取决于其对核心任务的影响 |
| 两用扳手：闭合端画成合法六角内孔，而不是十二角 | 不追加十二角偏好 | 原判据明确不限定十二角，不能因此扣分 |
| 图像构图漂亮但核心工作端画错 | 美感可以高分，对齐错误仍保留 | 美感不能补偿核心错误 |

第一行是真实题面／判据存在的口径分歧。若希望 B 对“题面明确要求展示却被遮挡”也判 fail，需同时对齐该批逐题 criterion；只改全局 prompt 会让两者冲突。本轮保留原政策并公开差异，没有悄悄改题或改分。

## 执行、存储与恢复

正式入口仍为 `config(...) → run_pipeline(...)`。`stream_judging=True` 时，同一pipeline的可配置模型分支由有界线程池提交独立Python子进程，每个子进程仍执行本文件中显式的 Dataset 图：

```text
固定题表 → 可选关联固定旧成功答案 → 生图/复用 → answers流式落表
        → A原生map_prompt_async → A换算及校验 → scores_a流式落表
        → B原生map_prompt_async → 原出题一致性校验及×10 → scores_b流式落表
```

每路使用独立 `__arm00`、`__arm01` 后缀的生成缓存、调用日志和流式表，单writer逐行提交。表均在本流程 `datasets/`：`answers__运行名__armNN.lance`、`scores_a__运行名__armNN.lance`、`scores_b__运行名__armNN.lance`。最终所有分支结束后统一交付 `scores_ab__运行名.lance`；中途失败只保留各路已提交快照，不制造完整总表。每个结果保留作答模型、方式、参考图URI/SHA、图像URI/SHA、A/B状态、原始响应、调用引用和各项分数。B节点实际请求只绑定原题payload和生成图，不接收A分数。

运行摘要为 `summary__运行名.lance`，包含总分母及每路进程结果。`complete`要求全部预期答案都有有效A和有效B评审；B合法inconclusive属于有效评审，但B总分为null、分数均值分母不含它。只因子进程退出不算完成。连续3条技术失败后停止该路，已发出的在途请求可能多于3条；不会换模型、删图重试、自动重试付费请求或把失败当0分。其余独立分支继续。

启动时题目ID验证通过Dataset逐行执行，集合同时受max_questions与4MiB UTF-8 ID字节预算约束；不为299题的唯一性检查申请共享原生查询的24GiB槽位。重复ID、缺题面／考点或超预算仍明确报错。

生成身份绑定原题、模型/权重、参数、seed、作答方式、参考图及顺序。新运行可通过每模型的 `reuse_answers_from={uri,version}`，在主线左关联历史生成缓存的成功行；行算子核对完整请求身份后才复用图片，身份不符即报错。失败答案不复用。judge使用原生调用日志和请求预算，每路每标准最多299次。配置、输入或代码变化必须使用新运行名，原运行的表版本、图片和调用记录保留。

本轮的固定旧答案来源是第一次启动的 `answer_results__qwen21_flare_ab_parallel_20261004__arm00.lance@4`、`...__arm01.lance@4`，各4张成功图片，共8张（其中当时流式答案表各已提交3张，另各1张在缓存中）。新运行复用这些实际生成图，继续完成其余题目。

`stream_judging=False`保留原“只答题”模式；独立 `run_judging` 仍可对固定答案表运行A三维评分。CLI和notebook都调用正式入口，不从notebook加载生产Python代码。

## 作答输入与资源边界

- `text_only`只发送原始 `instruction`；`positive_images`发送同一题面及题表 `authoring_images_json` 中冻结的全部正例图。要求 `authoring_variant=with_positive_images`，不重选图、不读取最新上游版本，不发送考点、判据或文字资料。
- 本批每题1–5张正例图：1张8题、2张6题、3张11题、4张9题、5张265题。默认上限5，配置允许1–8；超限、缺图、SHA错误或读取失败明确留失败行，不截断、不降级成无图。judge不接收这些正例图。
- 单图处理复用benchmark V2维护的 `positive_image_data_url`：32MiB编码上限、SHA检查、EXIF方向、白底、最长边1536、JPEG90；大图走已有受限解码路径，最多1.6亿源像素、1.5GiB地址空间、45秒。只导入单图I/O，不调用其他pipeline。
- Qwen两路均为seed 42、40步、`output_resolution=1024`及显式宽高1024，避免有图路按最后一张参考图改变输出比例。使用现有专用Diffusers依赖及本地权重，不下载模型。
- Flare使用[OpenRouter独立Images API](https://openrouter.ai/docs/guides/overview/multimodal/image-generation)：`POST /api/v1/images`，上游模型ID `openai/gpt-image-2.5-flare`。无图只发原题；有图通过 `input_references` 发送全部有序PNG data URL。两路均 `n=1, aspect_ratio=1:1, quality=auto`；当前端点能力目录没有resolution/seed，故不声称与Qwen分辨率、随机种子相同。模型别名与上游ID都绑定生成缓存身份。仍保留通用Images generations/edits和Chat接口。HTTP JSON响应缓冲没有独立RSS硬限，峰值按实际响应观察。
- judge本轮为`malasci/gpt-6-astra`＋xhigh，共享网关`127.0.0.1:4001`。每请求输出上限32768、超时900秒，完整文字预算60000字符；超预算不截断。字符上限不是服务端token能力证明，实际token和错误以调用回执为准。
- 旧 `use_references=True`仅兼容历史本地图文材料协议 `legacy_references`，不等于本批仅正例图条件。

## Notebook进度与分页查看

[notebook](t2i_v2_eval_debug.ipynb)保留两个单元格：第一格显示三份共用配置、GPU分配、后台状态和日志，`RUN_PIPELINE=False`默认不启动；显式开启时通过同一CLI后台提交并保存PID、启动时间和日志路径。第二格独立只读刷新，读取已提交 run 的 manifest、摘要和各流式表固定版本，核对题表URI及版本、judge与reasoning一致后，按task_id合并，最多展示本轮五路。部分任务尚未提交时单列，不隐藏已经开始的任务；全部未启动时继续显示已暂停的历史四路，不伪造 ZImage 输入或分数。保存实际查看输出和本地 `runs/qwen21_flare_zimage_comparison_20261004/case_browser.html`。活跃任务会跳过重复提交。

查看格每次调用同一正式CLI的 `--stage view --run <查看导出名> --view-runs <各运行名>`，在独立进程中加载磁盘上的查看实现，避免长期运行的kernel缓存旧模块、反复抛出已修复的128MiB错误。此阶段只读已提交结果并原子更新查看HTML，不进入生图、判分或补跑分支；混入模型执行参数会在读表前拒绝。notebook等待上限900秒，失败时显示错误，不把旧HTML冒充本次成功刷新。更新后重新打开notebook、直接运行第2格即可，不需要为了加载查看代码重启kernel；已有4000px高度、分页、完整真实输入和暂停标记保持。

独立进程修复已通过13项相关检查：在父kernel中保留会抛128MiB的旧函数后执行实际查看格，成功刷新且隔离业务表版本不变；CLI混入模型参数会被拒绝，原有查看/请求回归及布局检查通过。真实全量也在同样的旧函数模拟条件下运行并保存：18:51最终结果快照仍为299题×5路，4088个唯一预览共80.41MiB、文字29.88MiB、读图错误0，4000px高度保留，浏览器可翻到第299题。Qwen与ZImage显示finished，Flare显示paused；未重跑模型。回执在工作区 `_demiflow/evaluation_t2i_v2/qwen21_ab_prompt1_c8_20261004/viewer_kernel_refresh_verification.json`。

展示复用了参考会话最后的 [benchmark notebook容器](../../../benchmark/t2i/v2/operators/case_viewer.py) 和分页样式。单个固定高度iframe里支持上一页/下一页、页码跳转、概念跳转、搜索、按作答路及结果筛选；这些操作不运行Python、不依赖kernel。完整题面、考点、依据、判据、作答图、A的22项分值与理由、B逐考点证据及失败原因均保留。重新运行第2格才读取新进度；这是标明刷新时间的快照，不冒充自动实时推送。

查看最多300题×8路，文本上限64MiB，缩略图 data URL（含base64）总上限128MiB、单张1MiB，最多32768个唯一图片对象。全部run共享按原图SHA去重的媒体池，同一张作答图在展示和A/B输入中复用；各处的图片列表及顺序保持。先收集引用及所需最大尺寸，再按目标像素面积分配总字节预算、逐图编码WebP；超出份额时只调整预览质量和尺寸，保留完整画面，不修改原图、真实请求或分数。正例列表目标128、实际输入320、作答640，实际预览尺寸及总字节数写入页面元数据。固定编码缓存按SHA、尺寸及编码版本寻址，不随预算生成额外缓存变体；查看缓存和HTML不是业务结果来源。读图失败保留URI和错误；预算不足以容纳最低预览时拒绝发布，不删图或截断最后的题目。

页面仍可离线翻页，只为当前页创建图文DOM；HTML及notebook内嵌输出包含全量快照。不同路中完全相同的答题／判分长文本只存一份，浏览器从文本表恢复全文，不根据新模板生成历史内容；合并后的64MiB文字预算包含案例和共享文本表。各run读取仍保留64MiB展开文本限制。编码串、JSON及HTML转义会产生有界副本，128MiB是缩略图编码预算，不是Python或浏览器的RSS硬限。首次刷新需要生成WebP缓存，后续刷新复用。此次改动仅修复查看，不提交模型请求或重启后台评测。

2026-10-04查看修复验收：实际执行只读第2格并保存输出，299题×5路全部保留。18:11快照含3636个唯一图片预览，编码76.37MiB，图片读取错误0；无损共享后的文字27.81MiB。逐题核对全部正例图名单，并对照原始调用验证首题五路的完整prompt和传图SHA。相关8项隔离回归通过，覆盖小预算、实际参考图顺序及重复项、无图路、暂停状态、缺图和完整文本恢复。Chromium验证独立HTML鼠标翻页、末题、五路筛选、图片解码和A/B全文，并验证保存的notebook iframe内原生翻页事件；无JavaScript错误。验收回执为工作区 `_demiflow/evaluation_t2i_v2/qwen21_ab_prompt1_c8_20261004/viewer_fix_verification.json`。后续用户报告旧kernel仍报128MiB，查看入口已按上段改为独立只读进程，替代早期要求重启kernel的操作方式。

## 实际启动记录与验证

第一次四路启动为北京时间 **2026-10-04 13:10:45**，run=`qwen21_flare_ab_parallel_20261004`。Malasci上游明确返回账号组不支持`gpt-image-2.5-flare`，两路停止；独立探测也确认同账号组不支持精确名称`gpt-6-astra-xhigh`。Qwen已生成图片。该次评分入口还暴露了本地网关占位密钥环境变量漏设，已修复并增加回归。用户随后确认先运行Qwen两路，并将judge改为`gpt-6-astra`＋显式xhigh；该任务于北京时间13:20:32启动（PID 5468），保留旧结果并复用固定成功图片。之后用户指定Flare改走OpenRouter，新增任务 `flare_openrouter_ab_xhigh_20261004` 首次于13:36:51提交，停在共享原生执行器的内存准入检查、未发模型请求；针对固定小批改为有界逐行ID校验后，于13:44:06正式启动（PID 53192）。原Qwen进程持续运行。

OpenRouter路由在独立 [网关配置](configs/openrouter_images_gateway.yaml) 中维护，通过已有 `demiflow.services.manage` 启动的 `t2i_eval_openrouter_images` 管理（4002，CPU，loopback监听）。凭据继承既有服务的环境变量，不写入配置或notebook；目录查询已确认准确模型ID及0–16张参考图能力。原4001判分网关未重启。Flare两路首题“U形谷”均已真实返回1024×1024图片；无图路0张参考图、有图路1张参考图，OpenRouter返回200，两路首题均已完成同一A/B评审链。实际调用日志确认judge请求携带 `model=malasci/gpt-6-astra, reasoning_effort=xhigh, max_tokens=32768`。截至首批验收，Qwen无图路“猴头菌”A响应把文字理由字段写成整数，校验失败已保留；未自动重试，也未计成0分。

隔离测试覆盖原28项回归，以及A分层均分、B乘十和不可判、流式A/B落表、缓存与固定旧图复用、四个子进程同时启动、失败分母、网关占位配置和reasoning参数。浏览器实际检查了无kernel切页、搜索、跳到最后一题、完整长题面及HTML转义。新增验证还覆盖OpenRouter新版请求格式、上游模型身份、参考图像素与顺序、四路合并索引、不同题表拒绝合并和浏览器筛选。本次相关11项测试通过；实际四路HTML检查了4行进度、Flare筛选、下一页及跳到第299题“龙门吊”，无JS错误。原回归及旧图复用测试此前通过；本次全量复跑受共享原生查询内存准入排队影响已停止，不冒充本次全套通过。测试不调用真实模型；实际模型运行与服务拒绝单独记录，不混为测试通过。

```bash
PYTHONPATH=demiwtg:demiflow env/bin/python -m pytest \
  demiwtg/evaluation/t2i/v2/tests/test_pipeline.py \
  demiwtg/evaluation/t2i/v2/tests/test_paired.py -q
```

## Codex 同图复评与判官对比（2026-10-07）

用户要求同一批 Qwen 有图、无图各 20 题均有 Codex 判分，复用先前独立完成的 8 条有图结果，新增 32 条；先补原 HTTP408 涉及的 26 条，再补剩余 6 条无图。判官为独立 Codex subagent `gpt-6-astra` + `xhigh`、`fork_turns=none`。子代理只读自己一题的真实输入与作答图，不读旧分数、不改题、不重新生图。这里使用当前会话的子代理工具，不把离线请求生成声称为自动执行 Codex。

正式入口 `config(codex_comparison=...) → run_pipeline(...)`，CLI 为 `--stage codex-compare --run <独立补评分run> --config <配置>`。配置固定 `base_run`、`expected_requests`、`selection`（`retry` / `uncovered`）及 `reuse_sources=[{uri,version}]`；补齐时只排除固定且已完成的复用结果。冻结的原 D7 模板通过标准 `map_prompt_async` 离线节点渲染，再逐字核对原 Malasci 请求文字和实际图像 data URL 摘要。每个 `runs/<补评run>/caseNN` 保留真实 system/user 全文、实际图、摘要回执、独立子代理执行记录和原始 `response.json`。

子代理完成后重新调用同一入口：通过标准离线响应接口提交原始 JSON，标准节点解析、既有 D7 算子计算分数；未收到响应保持空分。原 Malasci 表、已完成 Codex 历史表及原始回执只读。新增分数写 `scores_d__<补评run>__new.lance`，复用结果合并到该补评run的独立 `scores_d` 表，固定版本附加到原查看run的 `codex_comparison.json`。相同run续接不会重新调用模型；已提交原始响应不可替换。

Notebook 保持三格。格3选择 `qwen21_d7_retry_408_20261007`，原汇总下增加两位判官同题配对的综合分、核心/非核心正确性、通用题面遵循、质量、美感均分及有效题数。明细按固定检查项 ID 并列显示两位判官的分值和完整理由，质量、美感保留全部原细项；两边真实判分输入均可展开。未评、异常与 N/A 不补零，每维度按双方均有有效分的同题集合比较；适用项不足20与漏评分别展示。查看仍是 notebook 内联 HTML 分页，刷新格3即可，无新增cell。

格3的 D7 汇总页同时提供“每题得分列表”：一题一行、每维度一列，列内显示无图分、有图分和有图减无图的差值。可切换 Malasci / Codex，选择比较维度，按差值排序、筛选有图更高/更低/相同/无法配对，并按题号或概念搜索；默认按 Malasci 综合分差值从高到低排列。列表上方的配对均分及升降题数始终覆盖所选判官和维度的全量题目，列表筛选不改变统计分母。配对取同一查看运行、同一题ID的无图与正例图两路，空分与 N/A 不补零；每个维度分别标记能否配对。点击题目进入原分页明细查看图与评分理由，返回汇总保留列表选择。所有交互只消费当前查看快照，不重新评分、不新增cell。


### D7 全量执行切换至原生 Codex CLI（2026-10-07，后续用户确认）

用户先取消 Malasci，随后明确要求由正式 pipeline 调用 Codex CLI，已经有效评分的题路不再评。此前会话子代理批次已收回，停止新增会话分发。D7 现在由同一 `map_prompt_async` 节点通过平台已有 `codex_exec` 客户端执行；benchmark 出题的 Codex 原生 agent 为接入参考，D7 使用固定 prompt/作答图的一次结构化评分。每条为独立 ephemeral 会话，`gpt-6-astra + xhigh`，完整冻结 D7、原检查清单及原图不变；没有改动 demiflow、prompt 或计分权重。

4 条真实 CLI 连通验证已完成，逐条核验实际消息、图片和 schema 与此前冻结离线请求完全一致。初次部分 CLI 因本地线程池启动失败而未评分，原失败回执和空分保留；恢复仅处理这些未完成项，已成功结果原样复用。任务自身的 pipeline 进程采用 CPU 0–15，CLI 使用固定的任务启动器限制到 CPU 0–1，并关闭评分不需要的插件、shell、其他 agent 和技能发现；启动器与摘要保存在 `runs/d7_full_dual_20261007/cli_minimal_launcher.json`，没有修改全局配置。历史启动失败不能算作有效评分或零分。

全量已于 2026-10-07 20:52（Asia/Shanghai）实际提交：

| 生成模型 | 原生 CLI 全量 run | 应评 | 提交时有效复用 | 新请求预算 |
| --- | --- | ---: | ---: | ---: |
| Qwen-Image-2.1 | `qwen21_d7_codex_cli_full_20261007` | 598 | 58 | 540 |
| BAGEL-7B-MoT | `bagel_d7_codex_cli_full_20261007` | 598 | 11 | 587 |

每路配置并发 8，每模型两路并行，总上限 32；实际在途数以 CLI 进程及算子遥测为准。每路连续 3 次新失败停止该流，保存已提交行和原始调用；缓存失败不会被重新调用或再次触发同一停止边界。有效复用分别固定自 `scores_d__{model}_d7_codex_full_20261007.lance@2` 与 `scores_d__{model}_d7_codex_cli_canary_20261007_recovery2.lance@1`，只选其中 reviewed 记录，原 Qwen 40 条仅出现一次。

三格 Notebook 已绑定两个 CLI 全量 run，第 3 格按生成模型比较 Codex 的无图/正例图，历史输出保留。原始运行及 Malasci 表均保留，不能把先前双判官计划当作当前执行。`runs/d7_full_dual_20261007/cli_canary_actual_input_audit.json` 保存真实连通核验；最终完成量、失败及有效分母须读取后续实际审计，提交预算不代表已经完成。相关 39 项回归通过，后续失败停止及复用回归 11 项通过；DemiForge 全局验收未宣称通过。

## Notebook 复制与原图路径（2026-10-08）

第三格使用本流程 `operators.notebook.notebook_browser` 包装既有 srcdoc 浏览器。
页面支持“复制选中文字”和每张图片的“复制原图路径”；放大框也保留复制按钮，预览失败时仍可复制原图引用。
本地 `file://` 地址转为解码后的绝对路径，方便粘贴到远端 VS Code 的打开文件框或终端；其他 URI 保留原样。

参考概念图片查看页已定位的 VS Code 宿主复制行为：内层选区通过消息同步到对应输出外层，
严格核对消息来源；只有该 iframe 持有焦点时才将宿主 copy 命令绑定到当前选区。
外层另提供原生备用文本框，自动复制失败会展开并保留待复制内容。支持 Shadow DOM 输出，
不读取系统剪贴板、不放宽 iframe sandbox、不调用 kernel 或网络。普通浏览器的真实复制/粘贴测试
覆盖路径、放大框、选区、仅宿主发起的 copy、备用文本框和无关文本复制；桌面 VS Code 的最终分派仍需在用户前端确认。

Notebook 保留三格、原 RUN_ID/VIEW_RUNS、原输出与执行计数；重新执行第三格后加载新版页面和包装函数。
本次只改查看交互，不发起生成或评分，不修改业务结果表。

原图地址也提供可点击文件链接：图片下方的“打开原图”、放大框完整路径和 ImageRAG 命中引用均可点击。
内层点击通过来源校验的消息转交外层，由带 `view=window` 的标准链接点击事件交给 VS Code notebook 处理；
绝对路径沿用当前工作区的 remote authority，不硬编码 SSH 主机，不通过 kernel 打开文件。
链接只接受本地绝对路径，保留复制按钮；内置图片编辑器沿用工作区已配置的图库路径关联。
测试模拟官方宿主链接处理，并覆盖普通/Shadow DOM 输出、中文空格路径、放大框与跨 frame 消息隔离；
用户桌面 VS Code 的实际文件打开仍须在该前端确认。
