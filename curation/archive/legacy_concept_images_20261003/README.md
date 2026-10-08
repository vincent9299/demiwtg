# 旧版概念配图审核

> **项目强制规范**：本 pipeline 的开发、修改、运行配置与评审必须先阅读并遵守 [《项目 Pipeline 强制规范》](../../PIPELINE_SPEC.md)。本 README 仅补充本流程的具体约定，不替代或放宽项目规范。

2026-10-02：从 `preparation/images/review` 迁入 `curation/legacy_concept_images`。这是文章配图审核和旧训练材料消费者仍在使用的历史流程；它的公共 keep／发布列不作为新概念—图片对齐或最终任务验收。共用概念正例图见 [preparation/concept_positive_images](../../preparation/concept_positive_images/README.md)。本次迁移保留审核协议和已有消费者，未触发公共发布。

新运行请选择 `curation/legacy_concept_images/datasets/<run>`；旧 `preparation/datasets` 业务表、原生调用日志与冻结 manifest 原址保留。旧 manifest 的 prompt 路径仅在读取时映射，正文仍逐字校验，历史记录不重写。notebook 保留历史 run 和已保存输出供查看，执行格要求先配置 curation 下的新 run，避免误写历史运行；旧已完成审核可通过 `reviewed_run` 显式复用。

正式入口 [legacy_concept_images_pipeline.py](legacy_concept_images_pipeline.py)：`config(...) → run_pipeline(config)`。参数及查看在 [legacy_concept_images_debug.ipynb](legacy_concept_images_debug.ipynb)，没有选样或目录同步步骤。

## 本轮 469 个概念

当前 notebook 配置 `t2i_dual_keep469_visual_v4_scope3_20260928`，通过 `reviewed_run` 复用 v3 已提交的审核，`max_calls=0`。用户手工运行；本轮没有模型节点，不部署 vLLM、不申请 GPU。新审核时的双卡配置见下节。

- 固定概念来源为 fine_screening `results__high_l3_categories_uniform1000_v1_20260927@1` 的全部有效双模型 keep，共469个唯一概念；原图为 collect `images@5`。仍保留完整 taxonomy 身份上下文，不传概念筛选理由。
- 原图关联的**全库去重概念数 >= 3** 时，整张图排除；计数不取469概念交集。条件下推到原图扫描，排除图片只取 SHA/概念关联，不读取其庞大 `sources`。保留图片携带固定来源引用、SHA、关联计数和必要元数据，不再复制全部来源。
- 从旧 `image_requests@1` 只提取概念、case_id、图片 SHA/编号及模型输入 SHA，再与新范围对齐；保留原批次审核依据，不重分批或重新提示模型。已有 `image_relevance@1` 直接固定引用；旧26.29GiB准备表不再读取。重验保留图片原始字节，缺失、变化或没有旧审核绑定均拒绝借旧结论发布。
- 469行全部保留，过滤记录单独写入 `image_filter_exclusions`；被过滤的旧 pending 不影响剩余图的状态。规则是本次材料范围筛选，不改写模型原始判断。下游自行决定概念选样和通过图数门槛。

更新后重新打开磁盘上的 notebook 并重启内核，依次执行：

1. 第1格只读配置和固定输入，显示父审核引用及零模型请求。
2. 第2格手工执行原图过滤、字节校验、旧审核复用和公共表 merge。旧运行必须已停止，入口同时持有新旧运行锁。
3. 第3格只读查看全部469概念、通过图数、至少5张通过图的概念数、规则排除和双模型明细。`CONCEPT_FILTER=None` 为全部，字符串/列表可单选/多选；`DETAIL_PAGE` 切页，`ROWS_PER_PAGE=40`，设为None展示全部。缩略图在自适应表格中，`detail_frame` 保存所选概念全量明细；固定版本查看缓存有界。
4. 第4格只读当前阶段和旧SQLite调用汇总。第2格占用内核时，可在另一内核执行第1、4格；不要再次启动第2格。

旧 v3 的 Qwen1165次、Gemma935次调用已完成，原响应及1165批审核保留。用户授权后已停止旧后处理；停止后核实无 `results/visual`，公共图片仍为2026-09-27的 `images@9`。历史 notebook 输出保留，不能将旧输出视为当前 v4 已执行。[GLM交接](archive/GLM_HANDOFF_dual_keep469_tp2_20260928.md) 顶部补充说明覆盖旧启动安排。

只读核对：唯一候选图3956→3110，概念图片关系4258→3118；846张图/1140条关系被规则排除。新路径读取范围和旧请求绑定用时6.88秒、峰值RSS约900MiB，产生3.23MiB精简关联数据；这些数值不含后续像素校验和正式写表，不能作为整条pipeline性能承诺。

状态区分 `no_images`（原本无图）、`no_eligible_images`（全部被规则过滤）、`no_available_images`（无可读图）、`review_pending`（待定/技术失败）、`no_published_images`（审核完成但无可发布图）、`visual_only`（存在通过图且无待定）。

## 服务与日志

两个 `map_prompt_async` 节点已接 demiflow 原生 `VLLMService`。GPU0+1、TP2/DP1，模型节点首次缓存未命中时加载，节点结束释放，再启动下一个模型；异常和取消也回收本次服务。显存比例0.92、上下文32768、batched_tokens16384、每请求最多4图，enforce_eager=False启用编译/CUDA Graph路径，加载超时900秒。

Blob校验和图片编码通过原生 `map_async(..., execution='thread')` 使用独立线程池，准备并发8/输入队列8；模型输入队列为16/8。保持原1536最大边、JPEG质量90、4图批次和全部审核协议。队列深度与客户端并发分别控制缓存行数和在途请求。上述数值是供手工实测的吞吐起点，未声称双卡已达到性能极值；不能直接把Edit单图215张/分钟外推到4图双模型审核。若Waiting持续很高并有KV抢占/超时，应降低在途并发；若Waiting为0且Running低，先查供给速度。

运行前需确保 8000/8001 端口与 GPU 可用。服务仅管理自己启动的进程，不停止或恢复外部已有服务。若用户自行部署两个模型，可把相应 `image_primary_service/image_review_service` 设为 None；endpoint 和模型名仍须一致。不能让外部 Qwen 占满同一组 GPU，再期望 Gemma 自动获取显存。

新审核入口默认使用平台 SQLite 调用日志 `model_calls__<run>.sqlite`，两个模型各有请求预算，完整响应可复用。模型启动日志在 `preparation/datasets/_demiflow/<run>/{primary,review}_vllm.log`；业务阶段仍写 Lance。未完成占位/失败响应不因同名重跑自动重试；历史日志不迁移或删除。仅旧调用方保留可显式指定的 Lance journal。

## 阶段与公共写入

固定图片输入 → 像素校验/分批 → 初审 → 独立复审 → 本轮逐图材料/元数据 → 本轮概念汇总 `knowledge_base` → 公共图片 merge。`through='prepare'` 只准备，`review` 只审核，`export` 才提交公共目标。全部本轮结果完成前不写公共表。

`target_uri` 默认 `demiwtg/preparation/images/catalog/datasets/images.lance`；仅允许 `write_mode='merge'`。业务算子按 assessment 身份合并审核关系，然后调用 demiflow `write_lance(mode='merge', on='sha256', update_columns=..., when_not_matched='insert', expected_version=...)`。保留旧审核和其他生产者列，重复导出不产生无效数据更新。缺列由平台显式增列，DDL 与数据提交是独立版本。

本轮 `knowledge_base` 是 469 个概念结果，`visual_image_meta` 是逐图审核记录；公共新版本仍是全量 SHA 图片目录。实际输出固定引用记录于 `results/visual`。本轮通过数量取本轮结果，避免把历史其他审核混入。

`run_pipeline(config)` 是唯一正式入口，CLI 和 notebook 直接调用它。`review_image_batches` 是文章也实际调用的子图，只写调用方的阶段，不发布公共图片；无调用方的额外重放入口已删除，正常阶段/完整响应续跑保留。

实际图片提示词为 [prompts/tasks.yaml](prompts/tasks.yaml)，同时包含正文、版本、模型缺省和响应 schema。两个模型使用同一正文，运行配置只覆盖模型名与端点。原 `image_annotation_v2.json` 是未被本流程读取的旧描述/匹配配置，已删除；此次迁移没有增加或改变审核标准，也不代表新增了人工审核。

CLI：`python -m curation.legacy_concept_images.legacy_concept_images_pipeline --run <curation/legacy_concept_images/datasets/新运行名> --input input_ref.json --through export --config model_config.json`。Python 与 CLI 不读取 notebook 代码；配置和代码变更使用新运行名，历史数据保持可读。

`prepared_run`只复用准备结果：要求来源、概念范围、taxonomy身份上下文、批大小及预标注绑定一致，记录父manifest指纹与两个固定表引用；每次执行仍重验原图SHA。配置或范围不匹配则拒绝复用，旧manifest/阶段/调用库不改写。资源并发可以不同。新run稳定后同名续跑复用已提交阶段及完整响应，未完成/不确定调用不自动重发。

`reviewed_run` 与 `prepared_run` 互斥，只支持 review/export。要求固定原图版本、概念范围、taxonomy身份上下文、批大小、预标注、模型和prompt协议一致；范围阈值可收紧，不能放宽。新run保存父manifest指纹与请求/审核表固定引用，并沿用旧图片ID和case_id。公共表写入仍只有最终export阶段一次数据merge，其他阶段仅写本轮中间表；公共行按SHA合并，嵌套来源保留引用而不复制原始sources。原图表和原审核表不修改。

2026-09-29 图片对象交付：审核生产者可以读取配置固定的采集表内 Blob，并在发布公共图片时导出共享独立对象、补 image_uri；新阶段引用该 URI。旧阶段仅在只读预览中保留历史 Blob 读取。 生产图片已完成导出和原址切换，当前表使用独立对象 URI；固定历史输入的范围和已有判断保留。实际版本与退役回执见根目录 docs/image_objects_20260929.md。
