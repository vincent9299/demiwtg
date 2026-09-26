# Edit V1 · 模型评测

`target_uri`（CLI `--target`）指定新运行的逐题评分输出表。`write_mode='overwrite'`（默认）覆盖最终目标表，`write_mode='append'` 追加本次输出，不去重或更新已有行。notebook 显式配置 `WRITE_MODE`，CLI 使用 `--write-mode overwrite|append`。目标路径和写入模式随运行冻结；新增批次用新 `RUN_ID`，更新已有结果用覆盖。中间阶段表仍按原来的断点规则保存。

## demiflow 框架版（当前活动入口）

- [执行与逐算子检查 notebook](edit_v1_eval_debug.ipynb) · CLI：`python -m evaluation.edit.v1.edit_v1_eval_pipeline --help`
- 数据链：入口的 `from_items`／字段检查（冻结的 Edit 题库，检查题号及 edit_instruction）→ `BuildEditJobs`（before 原图解析含 focus200 允许根约束、SHA 入湖；请求=图在前+edit_instruction 在后，不携带 reasoning/evidence/level）→ `GenerateEdit`（`map_async` chat 编辑作答，内联图、宽高比；默认 offline 记 `pending_generation`）→ `prepare_judge`/`apply_judge`（`edit_score_prompts.json` 按题 edit_type 路由；自由文本判分、缺项 1.0、二三维硬钳制——ImgEdit 契约逐条移植）。
- 历史模型输出可经 `--responses` 导入复判。阶段停靠 `--through {questions,answers,judge_requests,scores,summary}`。
- prompts：`edit_score_prompts.json`（冻结契约，字节不变）＋ `prompts/tasks.yaml`。
- `tests/`：作答请求形状、判官文本解析与钳制、导入响应绑定。

## 正式结果与回放

历史结果归档：[results_review.ipynb](archive/results_review.ipynb)。从固定 Lance 版本读取题目、图片、模型输出、评分和溯源，原执行输出保留；不依赖源码树内的 bench200/pilot 文件。

CLI 题库输入为 `--questions evidence:benchmark/edit/v1/bench200/questions.jsonl`；历史模型输出用 `--responses evidence:<同批响应证据ID>`。Notebook 默认已经绑定固定引用；外部文件仍需显式导入。

`tests/fixtures/eval_codex_score.py` 与 QIB 模板保留原字节；`operaters/frozen_scores.py`、`frozen_comparison.py` 从湖中核验冻结哈希、盲评绑定及判官会话。`tests/fixtures/eval_score.py` 作为旧 LLM 评分契约的对拍参照。查看命令直接写在 notebook；历史调度和报告生成脚本已退出。

默认离线；此次整理没有调用模型或产生新评分。

Python 流程入口在 `edit_v1_eval_pipeline.py`；debug notebook 是按需运行命令的地方，只保留导入、参数、调用和简单查看。

数据表直接平铺本模块 `datasets/`，运行名/题目编号仅作表名后缀；不建立 runs 或 history 数据子目录。历史固定引用和共同路径根说明见仓库 `tools/lake_migration/FLAT_DATASETS.md`。
