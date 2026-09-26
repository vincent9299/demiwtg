# T2I V1 · 模型评测

`target_uri`（CLI `--target`）指定新运行的逐题评分输出表。`write_mode='overwrite'`（默认）覆盖最终目标表，`write_mode='append'` 追加本次输出，不去重或更新已有行。notebook 显式配置 `WRITE_MODE`，CLI 使用 `--write-mode overwrite|append`。目标路径和写入模式随运行冻结；新增批次用新 `RUN_ID`，更新已有结果用覆盖。中间阶段表仍按原来的断点规则保存。

## demiflow 框架版（当前活动入口）

- [执行与逐算子检查 notebook](t2i_v1_eval_debug.ipynb) · CLI：`python -m evaluation.t2i.v1.t2i_v1_eval_pipeline --help`
- 数据链：入口的 `from_items`／字段投影（读取冻结题库，标记空题面）→ `BuildAnswerJobs`（在线出图作业，或 `--responses` 显式导入历史响应）→ `GenerateT2I`（`map_async` 网关出图，chat 端点优先 images 端点回退；默认 offline 记 `pending_generation`）→ `prepare_judge`/`apply_judge`（v6.0-V2 SYSTEM/USER 模板按 md 代码块原样提取；键集合校验+`schema_retries=1` 对应历史「重判一次」；`_v60_norm`/`validate_v60`/`finalize_v60`/`aggregate` 逐条移植）。
- 历史模型输出可经 `--responses` 导入并用新判官链复判（图片按 SHA 入湖，路径仅溯源）。阶段停靠 `--through {questions,answers,judge_requests,scores,summary}`。
- prompts：`prompts/tasks.yaml`（完整 v6.0-V2 判分契约与响应 schema；直接绑定题面、答题模型及图片）。
- `tests/`：离线图、判官响应绑定与三线分、历史 800 行判分逐题等价回放、作答请求形状（不泄漏题库内部字段）。

## 正式结果与回放

固定基准的数据实体位于 `benchmark/t2i/v1/datasets/bench200_{artifacts,blobs}.lance`（版本 1），包括 200 题和四模型共 800 张输出。

历史结果归档：[results_review.ipynb](archive/results_review.ipynb)。从固定 Lance 版本读取题目、图片、模型输出、评分和溯源，原执行输出保留；不依赖源码树内的 bench200/pilot 文件。

CLI 题库输入为 `--questions evidence:benchmark/t2i/v1/bench200/questions.jsonl`；历史模型输出用 `--responses evidence:<同批响应证据ID>`。Notebook 默认已经绑定固定引用；外部文件仍需显式导入。

`tests/fixtures/eval_score.py` 保留为判分等价性测试参照；正式口径为 v6.0-V2，不继续旧 v5/ladder 实验。

默认离线；此次整理没有调用模型或产生新评分。

Python 流程入口在 `t2i_v1_eval_pipeline.py`；debug notebook 是按需运行命令的地方，只保留导入、参数、调用和简单查看。

数据表直接平铺本模块 `datasets/`，运行名/题目编号仅作表名后缀；不建立 runs 或 history 数据子目录。历史固定引用和共同路径根说明见仓库 `tools/lake_migration/FLAT_DATASETS.md`。
