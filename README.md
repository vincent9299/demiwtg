# demiwtg

平台 0.2 升级迁移了当前 preparation、benchmark、curation、evaluation 的日志和运行状态：模型调用用 SQLite，业务摘要和提交回执使用明确的 Lance schema。迁移、显式恢复、空 run 重置与下载平台入口见 [平台升级指南](../demiflow/docs/platform-upgrade.md)。

**所有 pipeline 工作必须先阅读并遵守 [《项目 Pipeline 强制规范》](PIPELINE_SPEC.md)**，包括新建、修改、运行配置和评审。[AGENTS.md 第一条](AGENTS.md) 强制引用该规范，每个现役 pipeline 的 README 同样必须引用；以 [T2I V2](benchmark/t2i/v2/t2i_v2_benchmark_pipeline.py) 为数据流编排标杆。

后续 review 对规范的改进统一登记在 [Pipeline 规范 TODO](PIPELINE_SPEC_TODO.md)，按编号跟踪证据、修订和验证。


业务代码仓库；通用 Dataset、Lance 与执行能力由相邻的 demiflow 提供。

- [采集](collect/README.md)：原始材料获取与入湖。
- [QID 子集](subset/README.md)：负责采样，`qid_sub_*_concepts` 与 `qid_sub_*_images` 归本 pipeline 的 datasets；冻结 100k 保留采样值、历史版本及图片 URI，迁移状态见该说明。
- [基础处理](preparation/README.md)：公共文档、图片与概念处理，以及独立的 [QID 图片](preparation/qid_images/README.md) / [QID 概念](preparation/qid_concepts/README.md) 公共宽表；两条 QID 流程以固定 release 交付配套版本。
- [基准构建](benchmark/README.md)：T2I/Edit 各自维护 V1、V2，负责题目与输入材料。
- [任务素材与训练数据](curation/README.md)：Edit add 场景原图、T2I 训练样本和 Edit 训练图对；共用概念正例图归 preparation。
- [模型评测](evaluation/README.md)：模型作答、判分和结果分析，包含 [T2I 案例评测与标注](evaluation/t2i/case_annotation/README.md)、V1 评测与 BAGEL 官方套件。
- [统一环境](tools/environment/README.md)：工作区 env/bin/python。
- [视觉分类树重建方案](taxonomy-rebuild/README.md)：两批合并的 3,315 个不同名称、3,439 条来源记录，附 Markdown 标注与质量检查；当前为固定批次方案回放，无模型 API 调用。

业务表使用 Lance，按生产职责落到所属模块的 `datasets/`：collect 保存原始材料与历史导入底库，preparation 保存共用准备结果，curation/benchmark/evaluation/subset 保存各自的任务产物。公共消费不另设顶层数据目录，平台登记归工作区 `_demiflow/registry/`。路径、职责图与实际迁移状态见 [归属说明](docs/asset_ownership_20261002.md)：图片模块和全部公共资产已迁，概念底库改名为 collect 的 concepts，原顶层 datasets 已移除。源码、测试和说明纳入 Git；环境、模型、密钥与运行数据不入库。

Pipeline 的目录、数据流、范围/复用、完成状态、prompt 和验收要求统一见 [强制规范](PIPELINE_SPEC.md)；项目业务与数据决策见 [AGENTS.md](AGENTS.md)。

现役 pipeline 按“固定版本读表 → Dataset 行/字段变换 → 必要模型调用 → 校验/展开 → 写表”组织；关联键、处理粒度和提交边界在正式入口可见。具体要求以强制规范为准，不在此维护第二份规则。

2026-09-23 已完成工作目录收敛：移除 17,626 个旧文件/链接（文件约 15.18 GiB），保留 14,839 个固定证据引用，10,912 个唯一 Blob 已逐字节核验。采集和所有 `_staging` 未动。现役公共证据由 `project.HISTORICAL_EVIDENCE` 定位；该体积是工作目录移除量，不是磁盘净释放量。2026-09-24 顶层共享 `datasets/` 又清理了 19 张临时或旧表，保留 11 张公共表；旧维护运行记录已退役，本次清理清单与回执在共同工作区 `_demiflow/datasets_cleanup_20260924/`。


每个 pipeline 自行维护入口、notebook、`operators/`、`prompts/`、`tests/` 和 README。README 持续记录该流程的定位、逻辑、设计与实际验收，维护职责遵循 [S01.8](PIPELINE_SPEC.md#s01--必须阅读引用与维护)。目录、依赖、notebook 边界及规范引用由 [统一布局检查](preparation/articles/tests/test_pipeline_layout.py) 验证；具体职责和存储位置见各模块 README。

当前物理表位置、命名和历史引用迁移见 [数据布局](tools/lake_migration/FLAT_DATASETS.md)。

2026-09-24 T2I 专项清理后：旧 200 题及 800 张输出的完整证据归入 `benchmark/t2i/v1/datasets/`；删除混合 20 题与其他旧 T2I 开发实验。当前公共证据索引为 14,424 项，历史数量以上述日期快照理解；现役 V2、独立 Edit/BAGEL 与公共材料保留。清理回执在共同根 `_demiflow/t2i_cleanup_20260924/`。


新增 pipeline 必须同时登记布局检查和 README 强制引用。结构检查通过仅说明目录及引用符合要求，业务范围、失败处理、缓存身份与交接语义仍须按 [规范 S12](PIPELINE_SPEC.md#s12--必须按行为验收) 验证。
