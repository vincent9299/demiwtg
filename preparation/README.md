# 基础材料准备

公共材料按职责拆分子 pipeline；多个生产者可按约定更新同一目标表的不同字段。

| 职责 | 正式入口 | 手工配置与查看 | 公共目标 |
| --- | --- | --- | --- |
| 视觉概念审定与分类树 | [concepts_pipeline.py](concepts/concepts_pipeline.py) | [concepts_debug.ipynb](concepts/concepts_debug.ipynb) | 本模块独立采纳快照；master/images 只读 |
| 增量分类树与概念挂载 | [taxonomy_pipeline.py](taxonomy/taxonomy_pipeline.py) | [taxonomy_debug.ipynb](taxonomy/taxonomy_debug.ipynb) | 固定 P2 → 大模型上下文/操作循环 → 全量挂载、复核与修订；独立候选表 |
| 共用概念正例图 | [concept_positive_images_pipeline.py](concept_positive_images/concept_positive_images_pipeline.py) | [concept_positive_images_debug.ipynb](concept_positive_images/concept_positive_images_debug.ipynb) | P2 身份核准概念 → 多图视觉／综合机审；公共图片表只读 |
| 已核准概念补齐正例图片 | [concept_image_backfill_pipeline.py](concept_image_backfill/concept_image_backfill_pipeline.py) | [concept_image_backfill_debug.ipynb](concept_image_backfill/concept_image_backfill_debug.ipynb) | 固定最新 ready 范围 → 已有正例缺口 → 平台图片搜索／获取 → 正式关系审核；默认每概念至少 10 张 |
| QID 图片元数据与技术特征 | [qid_images_pipeline.py](qid_images/qid_images_pipeline.py) | [qid_images_debug.ipynb](qid_images/qid_images_debug.ipynb) | `demiwtg/preparation/qid_images/datasets/qid_images.lance`；每 SHA 一行 |
| QID 概念元数据与完整图片供给 | [qid_concepts_pipeline.py](qid_concepts/qid_concepts_pipeline.py) | [qid_concepts_debug.ipynb](qid_concepts/qid_concepts_debug.ipynb) | `demiwtg/preparation/qid_concepts/datasets/qid_concepts.lance`；每 QID 一行 |
| QID 本地视觉考察价值初筛 | [qid_review_pipeline.py](qid_review/qid_review_pipeline.py) | [qid_review_debug.ipynb](qid_review/qid_review_debug.ipynb) | 固定 QID 与关联文档 → 有界正文选段 → 本地考察价值初筛；独立候选方向、低优先和待补证结果 |
| 统一视觉概念与候选择值 | [visual_concepts_pipeline.py](visual_concepts/visual_concepts_pipeline.py) | [visual_concepts_debug.ipynb](visual_concepts/visual_concepts_debug.ipynb) | 固定 QID 保留项＋旧身份审核 → 显式等价对齐 → 生效值及全部类型化候选；完整原记录保留 |
| 图片目录与尺寸 | [image_catalog_pipeline.py](images/catalog/image_catalog_pipeline.py) | [image_catalog_debug.ipynb](images/catalog/image_catalog_debug.ipynb) | `demiwtg/preparation/images/catalog/datasets/images.lance` 基础字段 |
| 已获取图片统一登记与审核状态 | [image_consolidation_pipeline.py](images/consolidation/image_consolidation_pipeline.py) | [image_consolidation_debug.ipynb](images/consolidation/image_consolidation_debug.ipynb) | 已有下载/缓存/队列/审核固定来源 → SHA 去重 → 公共图片目录部分列合并 |
| 公共图片向量 | [image_embeddings_pipeline.py](image_embeddings/image_embeddings_pipeline.py) | [image_embeddings_debug.ipynb](image_embeddings/image_embeddings_debug.ipynb) | 本模块 `datasets/image_embeddings__<encoder_id前16位>.lance`；WeMM-Embedding-9B，通过平台 map_embeddings 编码和管理服务，按 SHA 增量维护、契约分表 |
| 公共文档元数据与结构 | [documents_pipeline.py](documents/documents_pipeline.py) | [documents_debug.ipynb](documents/documents_debug.ipynb) | 文档宽表、章节与段落区间；正文复用现有公共对象库；正在验收 |
| 公共文档向量 | [document_embeddings_pipeline.py](document_embeddings/document_embeddings_pipeline.py) | [document_embeddings_debug.ipynb](document_embeddings/document_embeddings_debug.ipynb) | 调用平台 `Dataset.document_embeddings`，按模型契约增量维护文档代表与正文块向量；全量尚未启动 |

每条 pipeline 的入口、notebook、`operators/`、`prompts/`、`tests/` 和 README 都在自己的目录。父级只负责分组，不保留混合算子/提示词或额外入口。图片基础字段边界与审核迁移说明见 [图片准备](images/README.md)。旧 articles 和概念配图生产入口已退役，当前文档准备使用 documents，概念正例图使用 concept_positive_images。

公共文档生产归上述两个 preparation 入口。此前 embedding 选型的小规模检索评测已按用户要求移出项目，保留在工作区 `_demiflow/document_embeddings_20261003/wiki_retrieval/`，不属于现役 pipeline，也不是两条生产流程的依赖；原固定 Lance 引用通过平台搬迁映射继续可读。宽表范围包括 Wikipedia 导入和其他流程已经下载的文档，来源接通及固定版本以 documents 的实际验收记录为准。

Wikipedia 存量登记的代码与新 run 回执已迁至 [collect/wiki_documents](../collect/wiki_documents/README.md)。共享正文对象统一位于 `preparation/datasets/documents/objects`，公共索引位于 `preparation/datasets/documents/library/index.sqlite`；旧对象 URI 通过两个目录兼容链接继续访问同一资产，历史登记表和版本留在原处。公共文档宽表、结构节点与向量仍由 preparation 维护。

部分列写入和显式增列直接使用 demiflow 的通用 `Dataset.write_lance(mode='merge', on=..., update_columns=...)` 与 `data.add_lance_columns`。项目负责列归属、业务去重、来源绑定与审核规则；平台负责键、类型、版本冲突和原子数据提交，不包含图片业务。

469 是已退役概念配图流程的历史处理范围，固定结果保留。材料优先 200 个名单代码已移到 T2I V2，历史名单和 CSV 原样保留。

各现役入口按自身 README 读取固定版本并交付本模块数据。旧 `articles/datasets/articles.lance` 与 `preparation/datasets` 中的中间表、固定版本和调用日志保留；旧 notebook 输出已归档，不作为当前生产入口。

CLI 使用各子目录的完整模块名，例如 `python -m preparation.images.catalog.image_catalog_pipeline --help`。旧 `images_pipeline --flow` 入口已移除。离线响应传输由 T2I 训练维护，评测显式复用，工具为 `python -m curation.t2i_training_samples.prompts.responses --help`。

跨 pipeline 契约均有归属：公共图片 schema 在 `images/catalog/operators/schema.py`；图片像素与历史记录转换在 `images/catalog/operators/records.py`；文章及公共材料读取、阶段行格式和固定引用在 `articles/operators/`。这些是已有消费者实际使用的契约，不是新的 pipeline 入口。布局检查在 `articles/tests/test_pipeline_layout.py`，不再为 preparation 跳过目录检查。

2026-09-29 图片对象交付：图片目录、审核与文章发布交付独立对象的 `image_uri + sha256`，curation 的任务审核直接消费这一对字段。collect 的表内 Blob 只由采集生产者适配器读取；公共 `source_refs` 保留来源追溯。 生产图片已完成导出和原址切换，当前表使用独立对象 URI；固定历史输入的范围和已有判断保留。实际版本与退役回执见根目录 docs/image_objects_20260929.md。


QID 全集由 `qid_images`（一 SHA 一行）和 `qid_concepts`（一 QID 一行）分别维护，通过显式 release 绑定版本，供下游采样消费。它们与名称体系 `concepts/images` 的身份契约分开。`qid_sub_*` 是 subset 的采样产物，归 `demiwtg/subset/datasets/`；不能作为公共全集定义。

2026-10-02：共用层包括 P1–P4 和共用概念正例图；P1–P4 判断规则不改，仅调整共享资产默认路径与搬迁解析，正例图从 benchmark/t2i/image_audit 迁入 concept_positive_images。Edit 场景原图与训练样本构造按任务用途归 [curation](../curation/README.md)。公共中性标注已退役，原有字段和结果保留。


公共表按生产者归属，不再维护工作区顶层公共 datasets。master 是 collect 的历史导入底库，文档库由 wiki_documents 管理；已按用户要求等原 P1/P2 退出后完成迁移，当前默认路径已更新，冻结配置仍可按旧引用读取。详细位置、架构图与实际完成状态见 [归属说明](../docs/asset_ownership_20261002.md)。

2026-10-03：图片内容统一接入 `preparation/datasets/images/objects`，名称图片目录与 QID／100k subset 共用 CAS；旧对象路径保留同 inode 硬链接。新平台获取在 `preparation/datasets/images/library/index.sqlite` 保存已验证 URL 索引。整合回执和运行验收见 [图片获取交付](../docs/image_acquisition_20261003.md)。

2026-10-02：新增 [公共图片向量准备](image_embeddings/README.md)，直接消费固定图片来源及独立对象，供任务侧检索复用，不依赖概念审核或 Edit 标签。本轮只实现与隔离验收，不启动生产向量计算；索引管理和出题接线属于后续阶段。
