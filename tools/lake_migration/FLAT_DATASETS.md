# 按生产职责组织数据

**2026-10-02 当前约定：** 公共材料也归其生产模块的 datasets，registry 归 `_demiflow/registry/`，不再独立保留工作区顶层公共 datasets。共用 P1–P4 和概念正例图归 preparation；任务素材和训练数据归 curation。实际迁移、概念底库改名、文档库迁移及顶层 datasets 退役状态见 [职责说明](../../docs/asset_ownership_20261002.md)。迁移入口为 `curation_ownership.py` 与 `dataset_ownership.py`，不重写冻结行和版本。

## 2026-09 历史布局与迁移记录

下文保留当时的记录，其中根 datasets、Blob 与旧模块名不代表当前布局；勿据此恢复已退役内容。

当前布局按数据职责分层，表直接平铺在对应 `datasets/`，不建立运行、阶段、case 或 history 数据子目录。路径相对共同工作区。

| 层 | 物理位置 | 内容 |
| --- | --- | --- |
| 原始采集层 | `demiwtg/collect/datasets/` | 原始 images/documents、四张 qid 表及采集事实 |
| 公共样本层 | `datasets/` | preparation 的样本目标表、概念/分类主数据和跨 pipeline 公共材料 |
| Pipeline 应用层 | `demiwtg/<pipeline>/datasets/` | 必要中间结果、模型调用、原图候选池、构题、训练及评测结果 |

采集写原始层 → preparation 从固定采集版本清洗、补尺寸、按需标注并更新公共样本列 → 下游从显式公共 URI/version 读取材料并写应用结果。图片字节沿交付 source_refs/BlobRef 读取，不在消费者另选原始表。公共目录的位置不表示样本已审核通过。

具体表与应用位置：

- 原始：`demiwtg/collect/datasets/{images,documents,qid_images_v2,qid_edges,qid_concepts_fat,qid_concept_xref}.lance`。
- 公共：`datasets/{images,articles,master_concepts,taxonomy_nodes,taxonomy_edges}.lance`。概念挂载已合并进 master_concepts；全局登记及保留的公共证据仍在公共层，是配套设施，不是第四层。
- preparation 的现役公共目标为 images/articles。起初一并迁移的 `datasets/knowledge_base__bench200_production_20260920_v8.lance`、`datasets/metadata__test__configured_answering.lance` 已按用户后续要求删除；不再恢复，公共层不新增每运行一份的样本目标表。
- 应用：`demiwtg/benchmark/{t2i,edit}/{v1,v2}/datasets/`、`demiwtg/benchmark/edit/source_images/datasets/`、`demiwtg/curation/{t2i,edit}/datasets/`、`demiwtg/evaluation/{t2i,edit}/<pipeline>/datasets/`。preparation 的流程若需暂存中间结果，仍使用本模块 datasets。

2026-09-27 历史迁移入口：[three_layer_storage.py](three_layer_storage.py)。首批移动四张 qid 和 articles/images；第二批迁走 preparation 剩余两张目标表，用户随后要求删除这两张表。迁移回执分别在共同根 `_demiflow/three_layer_storage_20260927/` 和 `_demiflow/three_layer_storage_20260927_remaining_preparation/`；删除回执在 `_demiflow/preparation_legacy_targets_cleanup_20260927/`，记录移除的历史登记与位置映射。历史迁移计划不代表当前保留范围，不应重跑补充批或恢复已退役表。迁移时逐表保留写锁并整目录移动，核对文件 inode/大小/mtime、每个版本的行数/schema 和实际解码样本，再更新精确位置映射。

2026-09-27 按用户要求合并概念挂载：`master_concepts.lance@3` 新增 `taxonomy_metadata`，完整保存原关系表的排序和来源，原 taxonomy 及其他字段保持；主表@1/@2 继续可读。`concept_taxonomy.lance` 已删除，旧四表发布、关系登记和两个位置映射已退役，新默认采集发布为 `master_data_merged_20260927`。入口 [merge_concept_taxonomy.py](merge_concept_taxonomy.py)，回执在共同根 `_demiflow/concept_taxonomy_merge_20260927/`。旧迁移清单中该表仅是历史记录，不得据此重建。

本次不执行全量图片补齐或任何模型标注。运行入口已采用公共样本新路径；`preparation/images/catalog/image_catalog_debug.ipynb` 仍由用户手工运行。

`project.resolve_root()` 默认返回共同工作区（包含 demiwtg/ 和 datasets/ 的父目录）。`DEMIWTG_DATASETS_ROOT` 若设置，也应指向这种布局的共同根；测试使用隔离根。消费者直接使用完整实际表路径和固定版本调用标准 `read_lance` 或 `lance.dataset`。

运行参数使用 `<pipeline>/datasets/<run_id>` 作为身份前缀，不创建 `<run_id>/` 目录。例如 preparation 的阶段表为 `knowledge_base__<run_id>.lance`，Edit 单题表为 `question__<run_id>__<task_id>.lance`。锁在同级 `_demiflow/`。

2026-09-23 的迁移通过 [flatten_datasets.py](flatten_datasets.py) 按 [当时完整清单](flat_datasets_plan.json) 同文件系统整目录重命名，保留表的全部版本、索引、行和 Blob 字节。迁移回执保存在共同根 `_demiflow/flat_datasets_receipt.json`。

2026-09-24 按用户要求清理顶层共享目录：删除 19 张临时维护、进度和已被替代的表，保留 11 张现役公共表，包括正式发布所引用的两张审计表和公共评测证据。旧分类历史、初版证据索引和 `relocation__pipeline_datasets_20260923.lance` 核验表已经退役；现役登记和位置映射已同步清理。清单与回执见共同根 `_demiflow/datasets_cleanup_20260924/`。历史迁移清单不代表当前保留范围，不应再次执行一次性迁移脚本。

旧 DatasetRef、RecordRef、BlobRef 及历史表中的来源 URI 保持原值。共同根 `_demiflow/lance_locations.json` 是精确的物理位置映射，供 demiflow 标准引用解析；它不扫描表名、不改变版本，也不重写历史模型响应。原始登记行的 `relative_uri` 是冻结身份，查看实际位置用 `ref.resolve(root)`。新代码和 notebook 直接使用新路径，旧目录和软链接不保留。

每个表的 `_demiflow/<表名>/` 存放锁和写入回执。它与位置映射、迁移回执都是控制文件，不是额外的业务表。所有 `datasets/` 排除 Git 和源码快照。

2026-09-24 T2I 专项清理：混合 20 题和旧 T2I 开发实验已退役；T2I V1 证据独立存入 `bench200_artifacts.lance` / `bench200_blobs.lance`。公共证据索引更新为 `retained_artifacts__t2i_cleanup_20260924.lance`，其公共 Blob 更新为 `blobs__retained_benchmarks_20260924.lance`；无关证据原字节保留，旧被替代的两张表删除。清单与验证见共同根 `_demiflow/t2i_cleanup_20260924/`。
