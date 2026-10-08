# 采集与入湖

后续标准化问题集中记录在 [collect TODO](TODO.md)，按 [主线执行计划](../taxonomy-rebuild/EXECUTION_PLAN.md) 随相关模块接入处理。`COLLECT-001` 已登记名称种子链别名未传入网页筛选的 bug；当前仅记录，尚未修复或重跑。

本模块 `datasets/` 是三层架构的**原始采集层**：保存原始 `images.lance`、`documents.lance`，以及从公共目录迁入的 `qid_images_v2.lance`、`qid_edges.lance`、`qid_concepts_fat.lance`、`qid_concept_xref.lance`。QID 表只移动存储位置，历史版本、索引和数据不变。preparation 从这里读取固定来源，准备后的公共样本写所属 preparation pipeline 的 `datasets/`；出题、训练和评测结果归各 pipeline 的应用层。详见 [数据布局](../tools/lake_migration/FLAT_DATASETS.md)。

当前本地采集入口均从仓库根按模块运行，使用工作区 `env/bin/python`：

- `python -m collect.qid_identity.qid_identity_pipeline --config ...`：固定 QID 范围 → Wikidata 名称、短说明、百科关联补取，独立保留原始事实，供审核显式绑定版本；详见 [QID 身份资料补取](qid_identity/README.md)。
- `python -m collect.wiki_documents.wiki_documents_pipeline --config ...`：固定 collect 文档快照 → 原生公共文档登记。代码与新回执归本模块；对象和索引统一归 `preparation/datasets/documents/`，旧引用兼容并保留历史表，详见 [Wikipedia 导入](wiki_documents/README.md)。
- `python -m collect.flow`：固定 master release → 概念种子 → 图片检索/下载，以及文档检索/抓取 → 单张图片/文档 Lance 表。`--concept` 可重复，`--carriers image text` 选择分支。采集事实和来源落 raw；视觉标注与支持审核归 V2 策展子图。
- `python -m collect.flow_kb --dump ... --lang zh`：外部 Wikipedia dump → 解析 → demiflow 原生批写，一页版本一行，写同一文档表。
- `python -m collect.import_base --input ...`：显式导入外部文本，清洗后写文档表。
- `python -m collect.import_materials images transfer.jsonl`：显式接收外部传输记录与 `bytes_path`；校验 SHA、解码并实测尺寸后写图片表。`documents` 模式写文档表。传输输入不参与运行时文件回退。

业务转换在 `ingestion.py`/`materials.py`，schema 在 `material_schema.py`，关联合并规则在 `material_writer.py`。提交锁、登记、回滚与独立对象 URI 读写归 demiflow。采集输入字节先按 SHA 发布到工作区 `objects/`，`images.lance` 只保存 `image_uri` 和元数据；`assets.py` 支持固定版本目录读取，旧 Blob 分支仅供历史审计和迁移。

同一图片的概念和来源追加到该 SHA 行；不同文档正文版本分别保留。原始正文只存一份，文档里的图引用图片 SHA。图片查重和文档版本查重使用 Lance 索引。

`batch2/`、`image_backfill/`、`sdc_fetch/` 以及 Commons/Wikidata 远端执行程序包含正在运行的下载/传输协议；本轮没有重新部署或重启它们。其队列、COS 对象与传输清单不能作为当前策展的数据源，必须经显式入湖，详见 `MEMORY.md`。它们的远端续跑协议尚未统一为 Lance，不能据本地链路测试声称远端部署已完成迁移。`sync/` 按用户纠正保留。

已删除被新入口替代的本地 JSONL sink、清单合并器、旧内联预标注/别名推导入口与对应旧 smoke；当前离线验证在 `tests/`。检索/下载源策略和限流来自现有算子，不在这次整理中新增网络调用。


`master_concepts` 定位为 collect 的历史导入概念底库，现文件为 `collect/datasets/concepts.lance`，历史 JSON 为 `concepts.json`。当前 `concepts.py` 是固定 release 的读取接口，实际历史生产来自导入与合并工具；用户确认当前没有额外加工逻辑，不新增 preparation 主表构建 pipeline。已按用户要求等原 P1/P2 退出后完成物理移动与默认路径切换；旧 master 文件名和发布 ID 保留在冻结引用中，通过精确位置映射读取。P1/P2 审定与 P3/P4 分类的采纳快照仍由 preparation 独立管理，见 [职责与迁移状态](../docs/asset_ownership_20261002.md)。

历史导入的 `taxonomy_nodes.lance`、`taxonomy_edges.lance` 及对应 JSONL 也保存在本模块 `datasets/`，用于保留旧分类数据和历史引用；当前流程不消费它们，也不将其视为 P3/P4 的采纳结果。工作区根曾遗留的 `collect/datasets/` 已归并清除，重复 `taxonomy_tree.json` 只保留本模块内经 SHA256 核验的一份。
