# 一次性数据迁移工具

原 data_access/migration 已迁到此处。运行形式为 `python -m tools.lake_migration.<模块名>`。来源解释、业务字段映射和一次性对账属于本项目；引用、登记、版本提交、发布和 Blob 访问来自 demiflow。

2026-10-04 补图库存整理已按用户要求收敛到独立标准
[images/consolidation pipeline](../../preparation/images/consolidation/README.md)。
原 consolidate_downloaded_images 临时入口和测试已移除，不保留平行执行入口。
首批已提交数据及冻结源码、日志、配置与备份保留；正式运行与恢复使用新目录的
config → run_pipeline，完整说明和验收结果见该 pipeline README。

`schemas.py` 只汇总各业务模块的 schema，`write.py` 仅为历史导入工具按 schema 名选择定义。活动 pipeline 直接调用 demiflow，不依赖这里。旧迁移记录/命令保留历史原文；不要因目录调整再次运行全量迁移或删除已登记表。

当前单表整理程序：`single_material_tables` 负责真实物理合表，`organize_source_evidence` 按业务粒度展开旧证据，`verify_single_table_pixels` 核验已发布像素，`publish_single_tables` 验收后切换发布并退役旧表。它们是一次性维护程序，不是 pipeline 读取层。运行、逐行对账与退役证明保存在湖内 runs/maintenance。

已经完成且会重建退役布局的旧迁移程序已删除。持续传输入湖入口属于采集业务，已归 `collect.import_materials`；生成图片实验也使用该入口，不依赖一次性迁移工具。

2026-09-27 概念挂载合并已完成：`merge_concept_taxonomy.py` 将关系表的完整排序/来源合并进 `master_concepts.lance@3`，发布新的三表固定组合后删除 `concept_taxonomy.lance` 并清理旧登记/映射。控制计划与回执位于共同根 `_demiflow/concept_taxonomy_merge_20260927/`；主表旧版本保留，重入只验证已完成结果。隔离回归见 `tests/test_merge_concept_taxonomy.py`。


## 原始/策展分离更正（2026-09-21）

当前修复工具为 `python -m tools.lake_migration.separate_raw_curated`，依次执行 `prepare`、`pixels`、真实材料隔离验收、`cutover`、`pixels_after`。验收证据在维护 Lance 中登记，缺证据不能退役。该程序是一次性业务迁移，不是 pipeline 的兼容读取层。

此前错误合并 raw 的 `entity_tables.py` 及其专用 verifier 已移除，保留原维护记录和历史报告，不再保留可再次执行错误布局的入口。其他历史入湖工具不是活动 pipeline。

## 图片导出为独立对象（2026-09-29）

生产导出、原址切换和旧图片载荷退役已经执行。实际统计、固定版本和验证结果见 [迁移记录](../../docs/image_objects_20260929.md)；审计根为工作区 `_demiflow/image_object_cutover_20260929/`。这是一轮已完成的维护操作，不应重新执行全库导出。

本次使用以下工具，均须显式调用，不在 import 时执行迁移：

- `object_cutover.py`：固定快照、分块导出、SHA/大小双向核对、SQLite 断点与失败账本。文件位于 `<root>/objects/<SHA前两位>/<SHA>`，表内保存绝对 file URI。
- `blob_snapshot_rebuild.py`：旧图片快照保持逻辑 schema、行数、版本号和非图片字段，内部图片改为指向普通文件的 External Blob 描述；新 head 删除 data、增加 image_uri，并恢复查询索引。历史描述只为读取既有冻结证据，新生产表不写图片 Blob。
- `column_reference_cutover.py`：复制快照后仅替换图片引用列（含裸 JSON 图片引用），保留其他列的数据文件及字段 ID。避免重新编码已有嵌套评审内容；提交前逐行对照未变字段和引用映射。
- `request_image_cutover.py`：将 preparation 请求阶段的 data URL 外置为对象，消费时恢复原请求；完整恢复值与原值逐行比较，保留请求身份和审核结论。
- `commit_object_cutover.py`：检查源 head、获取表锁、原址切换、登记新版本；验收后按精确回执退役旧目录，原清单保存在审计目录。目录移除字节数与独占文件字节数分列，硬链接不能算成释放空间。

`image_objects.py` / `reference_cutover.py` 保留作为单独新表或冻结目录视图的基础工具，不能据其新表成功便宣称生产切换完成。跨表图片引用必须是 image_uri、object_ref={uri,sha256} 或 object_uri；source_refs 只作来源追溯。

独立对象相同内容只保留一份；图片解码/缩放仍按需占内存。file URI 依赖稳定挂载位置，移动对象目录须显式迁移引用。不可改写原 SHA 对应的文件；新像素应生成新 SHA。历史模型调用日志按既有证据规则原样保留，其中旧请求的内嵌图片不参与本次资产表退役。

## QID 下载文件挂接（2026-09-29）

`qid_image_uris.py` 是用户授权的单次元数据维护，不是新的采样 pipeline。`verify` 固定公共子集版本，按 `sha256/ext` 定位已有独立文件，8 路线程逐图流式复算 SHA 和大小；窄核验表及固定身份写审计目录。`publish` 重新检查文件大小/修改时间和两表 head，只给源账本新增 `image_uri`、按 SHA 部分列更新公共子集的 `image_uri/availability/storage_mode`。缺文件、坏 SHA、源大小冲突、并发版本改变均拒绝发布。

源账本允许同 SHA 多条原始记录，因此使用原生增列，不按 SHA merge 或重建账本。原字段文件及字段 ID 保留；子集未更新列逐值核对，选图及变体不改。两表各自提交，完成回执保存实际版本；若中途退出，按回执重入，不将一张表成功等同于全部完成。审计位置：工作区 `_demiflow/qid_image_uris_20260929/`；活动 pipeline 直接读取表内 URI，不依赖该目录或本维护工具。

```bash
# 工作区根；此批次已执行，勿用同目录重复 verify。
PYTHONPATH=demiwtg:demiflow env/bin/python -m tools.lake_migration.qid_image_uris --help
PYTHONPATH=demiwtg:demiflow env/bin/python -m pytest demiwtg/tools/lake_migration/tests/test_qid_image_uris.py -q
```


## QID subset 归属迁移（2026-10-01）

`qid_subset_ownership.py --root <共同根> --release-id <QID公共发布>` 先检查冻结 100k 的全部 QID、SHA 和选中关系都被公共 release 覆盖，再移动两张 Lance 的完整目录到 `demiwtg/subset/datasets/`。每个文件核 SHA256，全部历史版本核行数和 schema，精确旧路径映射合并进现有 relocation manifest，其他映射保留。回执在 `_demiflow/qid_subset_ownership_20261001/`；迁移不重写任何行或图片文件，日常消费不调用本工具。


2026-10-02 按生产职责迁移：`curation_ownership.py` 迁移 Edit 场景和训练数据整目录；`dataset_ownership.py` 分 core/master/documents/finish 四部分搬迁公共资产及每表控制文件，并维护精确旧引用映射。实际完成情况见 [归属说明](../../docs/asset_ownership_20261002.md)。master/documents/finish 会拒绝已有 concepts/wiki_documents 运行锁；不得为迁移绕过锁或中止用户要求继续运行的 P1/P2。

2026-10-03 T2I 案例评测归属迁移已完成：`t2i_case_ownership.py` 将案例模块迁至
`evaluation/t2i/case_annotation`，把退役 zimage_probe 的历史数据并入同一 datasets，
移走运行锁并维护旧表与调用日志的精确路径映射。61 张表的全部历史版本保留，
6,508 个数据及日志文件逐个核对 SHA256，未重写业务行或调用模型。
回执及只读验收在工作区 `_demiflow/t2i_case_ownership_20261003/`；旧目录已移除，
不应重复执行迁移。现役流程直接依赖平台解析器，不导入此工具。
