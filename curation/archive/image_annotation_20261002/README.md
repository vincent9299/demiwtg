# 已退役源码快照：公共中性图片标注

2026-10-02 从 preparation/images/annotation 封存，供历史审计，不是可运行入口。历史表、原始请求响应与日志仍在原目录；下文是退役前说明。不要执行其中的旧命令。

# 图片中性标注 annotation

**项目强制规范**：本 pipeline 的开发、修改、运行和评审必须先阅读并遵守 [项目 Pipeline 强制规范](../../../PIPELINE_SPEC.md)。本文维护本流程的职责、配置、数据契约及验收方式。

annotation 是独立的图片 pipeline，与文章 pipeline 没有业务编排关系。唯一入口是 `image_annotation_pipeline.config(...) → run_pipeline(config)`；CLI 与四格 notebook 调用同一实现。

## 职责与最终交付

catalog 整理独立图片资产、来源与技术属性；annotation 根据图片本身产生通用视觉描述和信息丰富度证据。下游根据审定概念、限定条件和自身任务，独立判断匹配、训练/评测适用性和采样。通用 Dataset、对象读取、journal、预算和服务机制归 demiflow。

本入口不读取文章、知识正文、概念定义、历史审核或 release，不产生概念匹配、identity、focus、kb_match、quality、keep 或用途结论。旧 `score_concept` prompt、关系阶段、全库 Python 索引和 `annotation_parts` 参数已移除；旧参数会报错，不转发到兼容执行分支。现有公共表的历史字段及原始响应保留为数据证据，不再由本入口生产或更新。

## 固定输入、行粒度与字段血缘

`image_source={'uri': ..., 'version': N}` 必须指定已提交版本，至少包含 `sha256` 和 `image_uri`。URI 指向独立图片对象，不按 `source_refs` 回查像素或新增 Blob。URI 缺失、SHA 不符、完整解码失败均保留失败行；未请求模型不等于业务否决。

每个阶段主键均为 **SHA256**，一图一行。重复 SHA 报错。可用 `image_shas` 或固定 `scope_source`（每行一个 SHA）显式选范围，二者互斥；名单里缺目录记录的 SHA 保留失败，不从分母消失。`max_images` 按 SHA 排序截取候选范围；`skip_annotated_images=True` 显式排除已有有效 done 描述的图片，排除数量单列，不为旧描述补写当前配置。

| 表（本模块 datasets/，按 run 平铺） | 内容与权威引用 |
| --- | --- |
| `image_inputs__<run>.lance` | SHA、独立 URI、本轮 ready/明确跳过状态和原因 |
| `image_results__<run>.lance` | URI、SHA、实际编码输入 SHA、描述、丰富度、标注身份、状态、原生调用引用与错误 |
| `patch__<run>.lance` | 仅 export 阶段生成；有效图片的 descriptions/image_scores 两列 |
| `summary__<run>.lance` | 本轮固定来源与阶段引用、计数、stage_complete/complete/committed、目标版本 |

`description_record` 和 `score_record` 共用 annotation_id。描述中的对象、OCR、视角、可观察性和不确定性来自实际像素；richness 仅表示画面信息量。模型参数、编码和响应 schema 参与语义身份；并发、run、范围以及其他 pipeline 的 prompt 不参与。provenance 绑定固定来源、输入摘要及实际 response_ref，不把当前配置冒充旧请求配置。

## 执行阶段与配置

`through='prepare'|'annotate'|'export'` 控制停止阶段，默认 **annotate**，只交付本轮证据。prepare 只提交范围；export 明确允许更新公共表。`complete` 表示本次选定的 through 已完成，并不暗示后续 pipeline 已完成；`committed` 专指公共目标合并成功。

正式生产运行使用 `through='export', publish_policy='valid_rows'`：标注阶段结束后自动将有效描述与丰富度合并到公共表，不需要另开 pipeline 或人工再执行 export。有失败时依然保存完整失败名单，`committed=True` 不表示 `complete=True`。`annotate` 是显式选择的中间停止位置。

| 参数 | 默认值与含义 |
| --- | --- |
| `image_shas / scope_source / max_images` | None，全来源；空名单或 max_images=0 为合法空输入 |
| `skip_annotated_images` | False；开启后记录明确跳过数量 |
| `model / base_url` | qwen3.8-27b / 本地 8000 /v1，仅允许 loopback 服务 |
| `model_revision` | None，表示未记录部署标识；同地址换权重/推理部署时由调用者设置新标识，实际 journal 身份随之变化 |
| `max_edge / jpeg_quality` | 1536 / 90；EXIF 转正、白底 RGB、首帧、JPEG |
| `temperature / max_output_tokens / enable_thinking` | 0 / 4096 / False |
| `timeout_s` | 600 秒 |
| `prepare_concurrency / prepare_queue_depth` | 8 / 8，平台线程读图 |
| `image_concurrency / image_queue_depth` | 64 / 8，原生异步模型节点 |
| `image_max_calls` | None；非负整数限制该节点新增请求，0 只允许复用；schema 重试也消耗预算 |
| `image_service` | None，使用外部服务；可显式配置原生 VLLMService，首个新请求前加载，节点退出释放 |
| `model_mode / replay_journal` | online / None；replay 要求显式 journal 路径，禁止服务，零新增请求、只读原日志 |
| `publish_policy` | complete_only；valid_rows 明确允许发布成功子集，但失败仍在整轮分母，complete=False |
| `progress_every` | 50；完成行触发日志，不是定时心跳 |

CLI 从 JSON 接收与 notebook 相同的参数：

```bash
PYTHONPATH=demiwtg:demiflow env/bin/python -m preparation.images.annotation.image_annotation_pipeline \
  --run neutral_example --config /absolute/path/config.json --through annotate
```

模板与严格响应 schema 唯一维护于 `prompts/tasks.yaml`。本次保留原 `image-neutral-describe-richness-v2` 的描述正文、响应结构和像素编码，不为恢复把裸字符串 objects 猜补成对象。

## 写入归属、完成与失败

输入、描述结果、patch 和摘要均通过主线明确的 Dataset writer 提交；不把生产明细取到 Python 后拼回 Dataset。模型阶段先完成 materialize，随后整段提交 image_results，不能宣称每条业务结果已经逐行提交。完整模型响应由平台 SQLite journal 逐请求保存。

公共目标默认 `datasets/images.lance`，必须已由 catalog 创建。export 在注册事务中按 SHA 部分列 merge，仅维护 **descriptions/image_scores**；不改概念关系、审核、release、目录列及未知其他生产者字段。按 annotation_id 幂等合并，相同 ID 不同业务内容报错。目标缺 SHA 报错交回 catalog；写入协调使用平台锁与 expected_version 检查，版本冲突直接中止，不在业务层自动重试。显式重跑仍复用原生模型响应。

完成检查核对本轮必需 SHA 与输出 SHA 的缺失、多出、重复，再统计有效描述。无效响应、读取失败、预算不足和未完成请求均不产生假分数。默认有失败不 export；合法空输入提交空 schema 表并完成，不启动模型。

同名重跑首先写入未完成摘要，防止上轮成功被误认为本轮结果。取消传播出 materialize，不继续提交后续结果或公共表。查看仅使用当前 summary 的固定引用；旧阶段和只有文件但没有提交版本的目录分别显示，进程存活/退出均不表示完成。

## 复用和恢复

online 使用 `runs/<run>/model_calls.sqlite` 原生请求复用；公共同协议、同 SHA、同输入身份的描述与丰富度成对复用，不任取最新记录。修改部署、prompt、响应结构或预处理会产生新身份；只改调度和范围不会重复请求。

replay 通过 demiflow 的只读 SQLite 模式打开显式日志，既不补写历史请求元数据，也不创建新 reservation/recovery event。缓存缺失（PromptReplayMissError）、请求预算耗尽和 uncertain 分别记录错误原因，均保留未完成状态，不隐式重发；模型服务和 HTTP 验证也不执行。没有 revision 的历史请求按真实缺失值回放，不能凭当前配置替历史记录造版本。

旧 `full_label_v1_20260928` 仍暂停，原 run、journal、已落盘文件保持。原后台启动脚本包含已退役的概念参数，**不能用它自动恢复**。需要恢复中性描述时，应以新 run 显式指定固定图片来源、原请求参数与 `model_mode='replay'`、原 journal；先只交付 annotate 结果，再独立决定是否启动新增描述或 export。本文不是生产启动授权。

2026-10-01 用户已授权继续图片标注。本次续跑使用 `neutral_images_v2_20261001`：固定公共 `images.lance@16`，范围为旧 `image_inputs__full_label_v1_20260928.lance@7` 中 `status='ready'` 的 1,306,045 个 SHA，保存为 `resume_scope__neutral_images_v2_20261001.lance@1`，不扩大范围。原已停止的 SQLite journal 完整复制到新 run，原文件保留；新 run 复用已保存响应、只对未记录请求进行新调用，64 个旧 uncertain 请求不自动恢复。参数仍用原描述协议及同一模型部署，不为历史请求补造 revision。

续跑唯一配置为 `runs/neutral_images_v2_20261001/config.json`，CLI 与 notebook 共用。使用双 H100、DP2、显存比例 0.92；平台服务在首次未缓存请求前加载。用户已明确这是正式运行，现配置为 `through='export', publish_policy='valid_rows'`：逐请求保存 journal，整段结束后提交阶段结果并自动合并公共 `descriptions/image_scores`；失败行保留，不执行概念评分或自动清扫。初期回放速度和 GPU 空闲不能用来推断任务异常。复制来源与范围来源分别记录于该 run 的 `journal_seed.json`、`scope_source.json`。

该 run 最初于 2026-10-01 16:46:21 北京时间以 annotate 启动；随后按用户确认的正式发布要求切换为 export，保留同一个 run 和不断追加的 journal，不从原始副本重新覆盖。当前 PID、命令和时间见 `runs/neutral_images_v2_20261001/process.json`，历次进程见 `process_history.jsonl`，实时日志为同目录 `annotation.log`。启动不代表完成；以本轮 summary 与提交版本为准。启动前相关业务／模型日志回归 69 项、annotation CLI／目录检查 4 项通过；8 张旧图已确认原请求精确命中只读日志，没有新增模型请求。

## 下游消费与验证

下游接收本轮 `image_results` 固定 URI/version，按 status 读取有效描述，保留原始 SHA、URI、annotation_id 和 response_ref。不得从 descriptions 的存在推导概念审核通过，也不得把 richness 当成训练/评测价值。

`encode_pixels(raw, *, max_edge, jpeg_quality)` 是本模块维护、其他图片消费者显式导入的共享编码接口；本次保持行为和签名。文章 pipeline 不参与此入口的配置、执行或完成判断。

隔离测试使用临时图库、模拟 HTTP 端点和真实 Dataset/journal/Lance 写入，覆盖范围收缩、部署变更、只读回放、uncertain、无效响应、失败分母、零预算、重复 SHA、两列写入保留性和中断。结构规范与行为测试分别运行；模拟验证不证明真实模型描述质量或全量吞吐。
