# 共用 Python 环境

唯一共用环境为工作区 `env/`（Python 3.11.16）。模型服务原先就在此环境，本次补齐 Brotli 1.2.0、datasketch 2.0.0、fastwarc 1.0.9、jieba 0.42.1、resiliparse 1.0.9；保留 torch/transformers/vLLM、Lance/Arrow 和既定清洗依赖版本。

`env-cleaning`、`env-lance` 已退出，未创建软链接。合并前包清单在 records；当前可复核版本在 requirements.lock.txt（包含本机 editable 源码路径，部署到别处需调整这些路径）。Bagel 独立环境不在本次指定三环境范围内。

统一环境已执行 pip check 与模型/清洗/Lance 导入检查。retired_files 仅可能含并行文件系统暂时拒删的已删除库残片，没有 Python 入口，不是可使用的环境；记录见 records/retired_busy_files.json，可在文件系统释放后删除。
