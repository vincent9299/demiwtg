# 按 pipeline 平铺的 Lance 数据

业务表直接存放所属 pipeline 或模块的 `datasets/`（用户已更正拼写）。没有运行、阶段、case 或 history 数据子目录。运行名仅作为表名后缀；同名表再加题目编号或指纹，避免覆盖。

| 所属模块 | 物理位置（相对共同工作区） |
| --- | --- |
| 原始材料与采集历史 | demiwtg/collect/datasets/ |
| 文章、视觉审核和历史文章运行 | demiwtg/preparation/datasets/ |
| T2I 构题、审核和训练条目 | demiwtg/curation/t2i/datasets/ |
| Edit 构题、图对、审核、训练条目与公开图对探索 | demiwtg/curation/edit/datasets/ |
| 混合赛道历史训练试验 | demiwtg/curation/datasets/ |
| T2I V1 固定基准证据、源图及模型输出 | demiwtg/benchmark/t2i/v1/datasets/ |
| 基准构建各版本的新运行 | demiwtg/benchmark/{t2i,edit}/{v1,v2}/datasets/ |
| 综合评测 | demiwtg/evaluation/datasets/ |
| 分赛道 V1 评测 | demiwtg/evaluation/{t2i,edit}/v1/datasets/ |
| 现役主数据、全局登记、仍被引用的公共证据 | datasets/ |

常用表：`demiwtg/collect/datasets/{images,documents}.lance`，`demiwtg/preparation/datasets/{images,articles}.lance`。共享表包括 `datasets/master_concepts.lance`、`taxonomy_nodes.lance`、`taxonomy_edges.lance`、`concept_taxonomy.lance`、`registry_datasets.lance`、`registry_releases.lance`。

`project.resolve_root()` 默认返回共同工作区（包含 demiwtg/ 和 datasets/ 的父目录）。`DEMIWTG_DATASETS_ROOT` 若设置，也应指向这种布局的共同根；测试使用隔离根。消费者直接使用完整实际表路径和固定版本调用标准 `read_lance` 或 `lance.dataset`。

运行参数使用 `<pipeline>/datasets/<run_id>` 作为身份前缀，不创建 `<run_id>/` 目录。例如 preparation 的阶段表为 `knowledge_base__<run_id>.lance`，Edit 单题表为 `question__<run_id>__<task_id>.lance`。锁在同级 `_demiflow/`。

2026-09-23 的迁移通过 [flatten_datasets.py](flatten_datasets.py) 按 [当时完整清单](flat_datasets_plan.json) 同文件系统整目录重命名，保留表的全部版本、索引、行和 Blob 字节。迁移回执保存在共同根 `_demiflow/flat_datasets_receipt.json`。

2026-09-24 按用户要求清理顶层共享目录：删除 19 张临时维护、进度和已被替代的表，保留 11 张现役公共表，包括正式发布所引用的两张审计表和公共评测证据。旧分类历史、初版证据索引和 `relocation__pipeline_datasets_20260923.lance` 核验表已经退役；现役登记和位置映射已同步清理。清单与回执见共同根 `_demiflow/datasets_cleanup_20260924/`。历史迁移清单不代表当前保留范围，不应再次执行一次性迁移脚本。

旧 DatasetRef、RecordRef、BlobRef 及历史表中的来源 URI 保持原值。共同根 `_demiflow/lance_locations.json` 是精确的物理位置映射，供 demiflow 标准引用解析；它不扫描表名、不改变版本，也不重写历史模型响应。原始登记行的 `relative_uri` 是冻结身份，查看实际位置用 `ref.resolve(root)`。新代码和 notebook 直接使用新路径，旧目录和软链接不保留。

每个表的 `_demiflow/<表名>/` 存放锁和写入回执。它与位置映射、迁移回执都是控制文件，不是额外的业务表。所有 `datasets/` 排除 Git 和源码快照。

2026-09-24 T2I 专项清理：混合 20 题和旧 T2I 开发实验已退役；T2I V1 证据独立存入 `bench200_artifacts.lance` / `bench200_blobs.lance`。公共证据索引更新为 `retained_artifacts__t2i_cleanup_20260924.lance`，其公共 Blob 更新为 `blobs__retained_benchmarks_20260924.lance`；无关证据原字节保留，旧被替代的两张表删除。清单与验证见共同根 `_demiflow/t2i_cleanup_20260924/`。
