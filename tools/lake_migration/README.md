# 一次性数据迁移工具

原 data_access/migration 已迁到此处。运行形式为 `python -m tools.lake_migration.<模块名>`。来源解释、业务字段映射和一次性对账属于本项目；引用、登记、版本提交、发布和 Blob 访问来自 demiflow。

`schemas.py` 只汇总各业务模块的 schema，`write.py` 仅为历史导入工具按 schema 名选择定义。活动 pipeline 直接调用 demiflow，不依赖这里。旧迁移记录/命令保留历史原文；不要因目录调整再次运行全量迁移或删除已登记表。

当前单表整理程序：`single_material_tables` 负责真实物理合表，`organize_source_evidence` 按业务粒度展开旧证据，`verify_single_table_pixels` 核验已发布像素，`publish_single_tables` 验收后切换发布并退役旧表。它们是一次性维护程序，不是 pipeline 读取层。运行、逐行对账与退役证明保存在湖内 runs/maintenance。

已经完成且会重建退役布局的旧迁移程序已删除。持续传输入湖入口属于采集业务，已归 `collect.import_materials`；生成图片实验也使用该入口，不依赖一次性迁移工具。


## 原始/策展分离更正（2026-09-21）

当前修复工具为 `python -m tools.lake_migration.separate_raw_curated`，依次执行 `prepare`、`pixels`、真实材料隔离验收、`cutover`、`pixels_after`。验收证据在维护 Lance 中登记，缺证据不能退役。该程序是一次性业务迁移，不是 pipeline 的兼容读取层。

此前错误合并 raw 的 `entity_tables.py` 及其专用 verifier 已移除，保留原维护记录和历史报告，不再保留可再次执行错误布局的入口。其他历史入湖工具不是活动 pipeline。
