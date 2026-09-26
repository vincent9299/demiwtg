# Evaluation · 模型评测

固定 rubric 编制正文已放入 `prompts/evaluation.yaml`。逐题判分协议仍按 `task_type/edit_type` 选择，保留其必要动态输入；历史冻结协议文件不改写。


`write_mode='overwrite'`（默认）覆盖最终目标表，`write_mode='append'` 追加本次输出，不去重或更新已有行。notebook 显式配置 `WRITE_MODE`，CLI 使用 `--write-mode overwrite|append`。目标路径和写入模式随运行冻结；新增批次用新 `RUN_ID`，更新已有结果用覆盖。中间阶段表仍按原来的断点规则保存。

独立 `run_judging(run)` 复用启动时冻结的目标路径和 `write_mode`。

现役通用入口的题表、材料表可以传 `uri/version`；`target_uri` 指定最终评分表，独立 judge 沿用该运行冻结的目标。T2I V2 的答题与打分各自配置源和目标，见 `t2i/v2/t2i_v2_eval_debug.ipynb`。 `evaluation_debug.ipynb` 已列出可修改的源、目标参数。

本模块负责模型作答、判分、结果冻结核验和分析。题目与输入材料由 [benchmark](../benchmark/README.md) 构建。

- 当前 pipeline：[执行与逐算子审核](evaluation_debug.ipynb)、[评测 prompts](prompts)。CLI：`python -m evaluation.evaluation_pipeline --help`，算子在 operaters/；分区审核用 `--judge-only --backend <模型>`。
- [T2I V1](t2i/v1/README.md)：旧版模型作答、评分与结果查看。
- [T2I V2](t2i/v2/README.md)：答题先写入目标表，独立读取答案表打分，写入指定目标表；[evaluation_debug.ipynb](t2i/v2/t2i_v2_eval_debug.ipynb) 的两格可分开运行，已有图片无需重新生成。
- [Edit V1](edit/v1/README.md)：旧版编辑作答、判分、冻结核验与结果查看。
- [BAGEL](bagel/README.md)：模型适配器及官方基准评测套件。

当前 pipeline 的定位为 evaluation/datasets/<run>，沿用原湖内命名空间。V1 的两套正式结果与冻结证据均从 datasets 的固定版本读取，原题目、评分和协议字节不变。

T2I 的旧固定题库、源图、800 张模型输出及评分统一存于 `benchmark/t2i/v1/datasets/`，本模块保留评测代码和固定基准查看册。其他 T2I 历史设计实验及专属查看册已于 2026-09-24 清理。

Python 流程入口在 `evaluation_pipeline.py`；debug notebook 是按需运行命令的地方，只保留导入、参数、调用和简单查看。

数据表直接平铺本模块 `datasets/`，运行名/题目编号仅作表名后缀；不建立 runs 或 history 数据子目录。历史固定引用和共同路径根说明见仓库 `tools/lake_migration/FLAT_DATASETS.md`。
