# T2I V1 · 基准构建

`target_uri`（CLI `--target`）指定新运行的题目输出表。`write_mode='overwrite'`（默认）覆盖最终目标表，`write_mode='append'` 追加本次输出，不去重或更新已有行。notebook 显式配置 `WRITE_MODE`，CLI 使用 `--write-mode overwrite|append`。目标路径和写入模式随运行冻结；新增批次用新 `RUN_ID`，更新已有结果用覆盖。中间阶段表仍按原来的断点规则保存。

## demiflow 框架版（当前活动入口）

- [执行与逐算子检查 notebook](t2i_v1_benchmark_debug.ipynb) · CLI：`python -m benchmark.t2i.v1.t2i_v1_benchmark_pipeline --help`
- 数据链：入口的 `from_items`／`flat_map`（冻结样本×出题模型，qid 保持历史规则）→ `prepare_synth`/`apply_synth`（v6.0 无图出题请求与解析）→ `AuditSynthesized`（`audit_v60` 原样移植，只告警不删）→ 入口的字段 `map`（追加 `_generator_model` 等溯源字段）。
- 模型调用走平台 `map_prompt_async`；默认 offline 只生成绑定请求，缺响应记 `pending_synth`，不伪装成功。阶段停靠 `--through {samples,jobs,requests,synthesize,audit,export}`。
- prompts：`prompts/tasks.yaml`（完整 v6.0 出题正文、模型配置和响应 schema；固定正文不再经数据行透传）。
- `tests/`：离线图、响应绑定、状态机、历史 r11 raw 解析等价、bench200 复审等价、真实历史响应端到端回放。

## 固定基准

固定基准的全部数据存于本目录 `datasets/`，两张表均固定版本 1：

- `bench200_artifacts.lance`：1,225 项原始证据索引，保留证据名称、SHA256、字节数及 Blob 引用。
- `bench200_blobs.lance`：对应 1,225 份原始字节，包含 200 题、200 张源图、四模型各 200 张输出，以及构题与评分记录。

本地独立读取：

```python
from demiflow.lance.artifacts import ArtifactSet
from project import default_root, T2I_V1_EVIDENCE
baseline = ArtifactSet(default_root(), T2I_V1_EVIDENCE)
questions = baseline.rows("benchmark/t2i/v1/bench200/questions.jsonl")
```

跨赛道历史查看仍可使用 `project.historical_evidence()`；CLI 显式传 `evidence:<证据ID>`。旧 bench200 路径只是证据 ID，不是文件系统回退。外部 JSONL 仅作为显式导入边界。

当前 V1 保留旧 v6.0 协议与回放代码；后来混合 20 题、5 题对齐实验使用的新方法在现役 V2/evaluation 中继续，不是本 V1 的源码快照。它们的专属数据和旧查看入口已退役。

`tests/fixtures/eval_synthesize.py` 仅作解析与审核等价性测试参照；协议词表位于 `operaters/facets.py` 和 `prompts/facet_taxonomy.json`。

模型对比统一在 [evaluation/t2i/v1](../../../evaluation/t2i/v1/README.md)，新构题方法在 [V2](../v2/README.md) 继续。

Python 流程入口在 `t2i_v1_benchmark_pipeline.py`；debug notebook 是按需运行命令的地方，只保留导入、参数、调用和简单查看。

数据表直接平铺本模块 `datasets/`，运行名/题目编号仅作表名后缀；不建立 runs 或 history 数据子目录。历史固定引用和共同路径根说明见仓库 `tools/lake_migration/FLAT_DATASETS.md`。
