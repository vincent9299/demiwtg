# 2026-09-18 工作区整理与恢复

## 现役位置

- `collect/`：原工作区 `demiwtg-data/`，包含最新未提交的 image_backfill 等工作。
- `collect/archive/collect_v2/`：原 data/collect_v2 兼容层；原入口 data/collect_v2 为仓库内相对链接。
- `collect/archive/collect_v2_staging`：指向统一快照中的旧 _staging/data/collect_v2 源码。
- `tools/recovery_snapshots/workspace_20260918/`：工作区根脚本、_staging 与 state 的遗漏源码、文档、配置、部分小型结果。原路径及纳入/排除明细见其 MANIFEST.json；共 4,580 个快照文件。另有 `tools/recovery_snapshots/ignored_sources_20260918/` 保存其他被忽略目录中的 1,035 个源码与文档（包括历史评测工具和 modelhub 本地脚本），合计 5,615 个快照文件。
- `tools/environments/`：三个 Python 环境的 pip freeze 清单；包含本地路径引用，恢复时须按新机器调整，不能替代系统/CUDA/conda 配置。

## 保留与排除

归档 notebook 去掉输出、执行次数、附件和 widget 状态，原始 notebook 不修改。代码快照不是完整实验数据备份：模型、依赖安装目录、环境、图片、数据库、大型 JSONL/压缩包、agent 历史目录、密钥均未纳入。state 中逐请求/逐材料运行缓存也未整体入库。全部原文件保留。

历史快照按原目录结构保存，不应直接视为可运行的新入口。`collect/kb_audit/raw/sdc_attach2.py` 原文件已有第 81 行缩进错误，当时原样保全，不宣称所有历史脚本都可运行（kb_audit 目录已于 2026-09-20 整体清理，git 历史可溯）。

## 旧路径兼容

按用户后续要求，工作区 `demiwtg-data` 兼容链接已移除，现役本地脚本使用 `demiwtg/collect`；不再创建旧入口。`kb_audit -> demiwtg/collect/kb_audit` 链接已于 2026-09-20 随目录清理一并移除。历史快照保留当时的路径文字。未重启采集服务、修改集群路径或改 COS 键。

## Git 历史

原采集仓库历史导入主仓 `archive/demiwtg-data-20260918` 分支（本地与 origin 均在，顶端 1d92d21）。原目录不再是独立仓库，后续在 demiwtg 主仓提交。demiflow、pan123、modelhub 仍为各自仓库。2026-09-20：经核对该分支已推至远端后，`../backup_audit/`（含 demiwtg-data-before.bundle 与 demiwtg-data.git 两份历史副本及迁移审计文件）已整体删除。

## 核验

每个快照的 source_sha256 对应原始内容，snapshot_sha256 对应脱敏/去输出后的归档内容。备份时保留脚本可执行位。新采集代码通过 AST 语法检查（上述历史 raw 文件例外），兼容 collect_v2 仓库根定位通过检查；未启动真实采集或模型评测。

2026-09-20：恢复资料已迁至 tools/recovery_snapshots，并删除经内容哈希核对的重复副本。历史总文件数不再表示当前实际保留数；以该目录 duplicate_removals.json 为准。

2026-09-20：collect/archive/collect_v2_staging 的过期软链接已移除；恢复备份按去重清单定位真实源文件，不再创建兼容软链接。
