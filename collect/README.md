# 采集与入湖

当前本地采集入口均从仓库根按模块运行，使用工作区 `env/bin/python`：

- `python -m collect.flow`：固定 master release → 概念种子 → 图片检索/下载，以及文档检索/抓取 → 单张图片/文档 Lance 表。`--concept` 可重复，`--carriers image text` 选择分支。采集事实和来源落 raw；视觉标注与支持审核归 V2 策展子图。
- `python -m collect.flow_kb --dump ... --lang zh`：外部 Wikipedia dump → 解析 → demiflow 原生批写，一页版本一行，写同一文档表。
- `python -m collect.import_base --input ...`：显式导入外部文本，清洗后写文档表。
- `python -m collect.import_materials images transfer.jsonl`：显式接收外部传输记录与 `bytes_path`；校验 SHA、解码并实测尺寸后写图片表。`documents` 模式写文档表。传输输入不参与运行时文件回退。

业务转换在 `ingestion.py`/`materials.py`，schema 在 `material_schema.py`，关联合并规则在 `material_writer.py`。提交锁、登记、回滚、引用和 Blob 读取归 demiflow。`assets.py` 只绑定 `raw/images.lance`，不再按 SHA 路由多张表。

同一图片的概念和来源追加到该 SHA 行；不同文档正文版本分别保留。原始正文只存一份，文档里的图引用图片 SHA。图片查重和文档版本查重使用 Lance 索引。

`batch2/`、`image_backfill/`、`sdc_fetch/` 以及 Commons/Wikidata 远端执行程序包含正在运行的下载/传输协议；本轮没有重新部署或重启它们。其队列、COS 对象与传输清单不能作为当前策展的数据源，必须经显式入湖，详见 `MEMORY.md`。它们的远端续跑协议尚未统一为 Lance，不能据本地链路测试声称远端部署已完成迁移。`sync/` 按用户纠正保留。

已删除被新入口替代的本地 JSONL sink、清单合并器、旧内联预标注/别名推导入口与对应旧 smoke；当前离线验证在 `tests/`。检索/下载源策略和限流来自现有算子，不在这次整理中新增网络调用。
