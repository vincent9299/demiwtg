# T2I V2 评测

`t2i_v2_eval_debug.ipynb` 分为两个独立单元格，对应正式 Python 中的两个步骤。

1. **答题**：`run_pipeline(run, source, config, target_uri=...)` 从 benchmark 的指定 `uri/version` 读取题目，多模型依次生成答案，写入目标表。评分字段全部为空，不调用 judge。未指定 `target_uri` 时写入本目录 `datasets/results__运行名.lance`。
2. **打分**：`run_judging(run, source, config, target_uri=...)` 从源表的指定 `uri/version` 读取已有图片，调用 judge，完成后写入指定目标表并返回新版本。源、目标可以配置成同一张表，也可以不同。可以独立运行或更换 judge，不需要答题配置，也不会重新生成图片。已有评分或 judge 失败的行，只要保留图片都可以重新打分；生成失败行保留原因和空分数。

两个步骤均返回 `state['target']`，包含实际表路径和固定版本。新答题后，把第一格返回的路径和版本填入第二格。第二格默认指向此前已保存的 12 张答案（`answers__three_models_gpt6_sol_20260925_01.lance`，版本 13），可直接跳过第一格。

两格的表配置互相独立：第一格顶部填写 `INPUT_TABLE_URI`、`INPUT_VERSION`、`OUTPUT_TABLE_URI`；第二格同样填写 `INPUT_TABLE_URI`、`INPUT_VERSION`、`OUTPUT_TABLE_URI`，由使用者决定是否写回源表。两格分别保留自己的模型配置和运行名，输出版本单独显示，不改写输入版本参数。

- 输入题表每行须有唯一 `task_id` 和非空 `instruction`；`concept` 可选。读取指定版本的全部行，不自动筛选审核状态。
- 每个答题模型回答全部题目，每题一张图。目标表每行用 `task_id` 与完整 `answer_model` 标识，保留图片引用、状态、22 项评分及理由、三个维度均分。
- 答题默认使用题面原文；本地模型设置 `use_references=True` 时，读取题表 `references_json` 中冻结的参考知识和图片，结果的 `answer_model` 加 `+参考信息` 区分。同一模型的两路可同时配置。judge 只接收题面与生成图，不接收模型名、参考材料、考点或题目级判据。
- 评分沿用 `prompts/tasks.yaml` 的 `judge.template`：0/1/2 映射为 0/60/100，N/A 不参与均分；整个维度不适用时为 null。调用失败不计为 0 分。
- 答题和打分使用独立运行名。答题按“模型＋题目”复用缓存；judge 使用自己的 `calls__运行名.lance` 日志。同名可续跑；修改输入版本、模型或规则，以及修复接口后主动重试失败调用时，使用新的对应运行名。换 judge 运行名不会重跑答题。
- judge 完成前不清空目标表；运行中断或覆盖写入失败时原版本仍可读取。运行正常结束后，成功和失败状态一并写入新版本。
- 图片存 `images__答题运行名.lance`，运行记录存 `records__运行名.lance`，数据均平铺在本目录 `datasets/`。

## 模型配置

第一格配置 `config(answer_models=[...])`，当前只运行新增的 `Qwen-Image-2.1＋参考信息`、`BAGEL-7B-MoT＋参考信息` 两路，`WRITE_MODE='append'` 追加到配置的目标表。两路都读取题表中的参考知识和参考图。第二格单独配置 `config(judge_model={...})`，当前为 `openrouter/openai/gpt-6-sol`，通过 model hub 请求。

- `diffusers` 使用权重目录中的 `model_index.json` 加载对应 pipeline；`device`、`seed`、`parameters` 可改。
- `pythonpath` 配置该模型子进程的依赖路径。Qwen-Image-2.1 沿用 Edit 流程的 `/tmp/edit_qwen21_dependencies` 和 `/tmp/edit_diffusers_official/src`（官方提交 `256e9fbc0bf9447aab4ec58cb74b419a4586e7b8`），使用 40 步、`output_resolution=1024`；BAGEL 仍使用自身环境。
- `bagel` 复用 `evaluation/bagel/adapter.py` 的加载与推理设置。
- `python` 指定本地模型生成阶段的解释器，`cuda_visible_devices` 指定子进程可见 GPU。Qwen 使用 `env`，BAGEL 使用 `env-bagel`，依次执行并释放资源；子进程仍执行相同的标准 demiflow 生成链。
- `modelhub` 的 `api='images'` 对应 `/images/generations`，`api='chat'` 对应 `/chat/completions`；直接使用指定模型名和参数，不自动切换接口或模型。
- judge 可配置 `model`、`base_url`、`api_key_env`、`max_output_tokens`、`timeout_s`；`mode='offline'` 将请求写入原生离线日志。网关需有相应模型路由，客户端不要求模型先出现在 `/v1/models` 列表中。

`env-bagel` 已安装 `pylance==12.0.0`、`pyarrow==23.0.1`，子进程显式使用当前 demiflow 源码。维护测试使用隔离表和模拟响应，不实际调用模型。

答题和打分各自配置 `WRITE_MODE`，传入 `write_mode='append'` 或 `'overwrite'`。追加新增答题路时，用新 `RUN_ID`，在 `ANSWER_MODELS` 中只选择本次新增的模型配置，并将目标指向已有结果表。追加不会更新原行；judge 如需替换原表里的未评分答案，使用 `overwrite`。答题逐题缓存结果，全部结束后一次提交目标表；返回的 counts 只统计本次答题。 相同评分快照只提交一次，待响应或失败结果的重复查看也不会再次追加。CLI 的两个阶段均支持 `--write-mode overwrite|append`。

本地参考图接口：BAGEL、Qwen-Image-Edit-2511/2509、Qwen-Image-2.1、FLUX.2-klein-9B；Qwen-Image-2512 的接口只支持文字。Qwen-Image-2.1 需要项目已有的专用 Diffusers 依赖。
