# Edit 训练数据 pipeline

训练样本表用 `write_mode`，题目/审核表用 `questions_write_mode`，各自选择 `'overwrite'`（默认）或 `'append'`。notebook 对应 `WRITE_MODE`、`QUESTIONS_WRITE_MODE`，CLI 对应 `--write-mode`、`--questions-write-mode`。追加保留已有行，不去重或更新；同一批次的阶段更新通常使用覆盖，新增批次使用新 `RUN_ID`。两张表分别登记已提交版本，第二张表失败后续跑不会再次追加已提交的第一张表。

`sources` 显式列出文章、图片审核结果及原图表；`target_uri` 指定训练样本表，`questions_uri` 单独指定题目与审核结果表。 `edit_train_debug.ipynb` 已列出可修改的源、目标参数。

`edit_train_pipeline.py` 直接定义正式流程与 CLI；`edit_train_debug.ipynb` 第一格直接按表路径/版本读 Lance，可按概念筛选、显示原图/目标/参考和耗时，第二格手动调用并可替换模型、算子。`RUN_ID` 只表示一次运行；已有 `task_id` 是题目关联主键，不作为查看参数。

学习方向（概念视觉特征、参考信息应用、条件或关系应用）和编辑类型分别记录。VLM 根据监督信号决定已有图是原图还是目标：前者合成目标，后者反向合成原图。训练指令始终指向原图→目标，合成指令单独保存。默认本地 qwen3.8-27b 构题和审核、本地 Qwen-Image-2.1 合成。

## 流程和存储

固定版本基础材料 → 当前已有图和分批材料 → 联合设计 → 合成另一端 → 完整图对审核 → 合格训练条目。文本/参考可选，构题依据、合成材料、实际训练参考分别选择。材料角色不预分池；每次图访问得到一条合格样本即换图，每概念接受数达到 `samples_per_concept`（默认 5）即在入口停止，不足按实际交付。默认两轮、每概念最多20次尝试，可改参数。

所有表直接放 `curation/edit/datasets/`；`--run` 的末段仅为运行标识，不创建子目录：

- `question__<run>__<task_id>.lance`：题目、学习目标、编辑类型、角色和两种指令。
- `generated__<run>__<task_id>.lance`：合成图片 Blob。
- `pair__<run>__<task_id>.lance`、`review__<run>__<task_id>.lance`：图对、审核、耗时。
- `questions__<run>__<snapshot>.lance`：这轮所有结果，失败也保留。
- `training_samples__<run>__<snapshot>.lance`：审核通过的条目；无通过项时保留显式 schema 空表。
- `metadata__<run>.lance`、`calls__<run>__<task_id>.lance`：运行绑定和原生请求响应。

读写在 pipeline 中直接使用 `read_lance`、`write_lance`、`lance.write_dataset`。`map_prompt_async` 后直接用标准 `run_stream()` 执行、收集单 case 结果，然后显式 `write_lance` 写题目/审核表；不使用 checkpoint 代替写表；历史引用通过标准存储的位置映射解析。Blob/空表采用官方 Lance writer。所有单 case 表固定 v1，重跑读取既有结果；更换配置/源码需要新 run。未完成的合成调用预约阻止自动重试，避免不明调用被重复执行。

`design_seconds`、`synthesis_seconds`、`review_seconds` 是各次调用耗时；`model_load_seconds` 独立记录图像模型加载耗时，不混入单次合成耗时。审核是第二次 VLM 调用，默认同一模型，不等于人工验证。

## 调用

```bash
../env/bin/python -m curation.edit.edit_train_pipeline \
  --run /absolute/workspace/demiwtg/curation/edit/datasets/my_run \
  --sources /absolute/sources.json --config /absolute/config.json --stage design
```

`sources.json` 包含 `articles`、`visuals` 固定发布引用和 `raw_images_ref`。每个实体来源明确选 `release_id`。配置字段见 `config()`；`--stage design|synthesis|review|all` 支持分阶段显存让位，pipeline 本身不停止/重启模型服务。小试跑配置使用两个概念、每概念 `samples_per_concept=1, max_attempts_per_concept=1`。

图像模型使用官方 `diffusers.QwenImage21Pipeline`，需要含该类的 Diffusers 源码版本；共享环境 0.40.0 不包含它。试跑使用官方提交 `256e9fbc0bf9447aab4ec58cb74b419a4586e7b8`，仅在合成进程通过 PYTHONPATH 使用临时安装，不修改共享环境。生产调用前应明确固定依赖。

[历史公开图对试看](reviews/public_pair_probe_20260922/review.ipynb) 保留，不能作为本轮新增训练数据。T2I/Edit 业务算子相互独立，仅消费 preparation 材料契约和 demiflow 标准能力。

## 两例真实试跑（2026-09-23）

`edit_train_debug.ipynb` 第一格已保存两例的题目、图片、参考、逐项审核和耗时输出；第二格供手动新建 run。实际结果在 `curation/edit/datasets/`，历史表 `questions_1f69373914f05bb7b96b__qwen21_pilot_20260923_02.lance` 有2条，`training_samples_1f69373914f05bb7b96b__qwen21_pilot_20260923_02.lance` 有1条，均固定 v1。

| 概念 | VLM 选择已有图角色 | 机器审核 | 构题 / 合成 / 审核（秒） |
| --- | --- | --- | --- |
| 莜面栲栳栳 | 目标图，反向合成原图 | 拒绝 | 19.39 / 13.16 / 19.84 |
| 绞胎瓷 | 目标图，反向合成原图 | 通过 | 13.21 / 7.94 / 18.21 |

图像模型加载30.98秒，复用同一实例完成两例。VLM 在 GPU0 单卡运行（上下文65536、最多16并发序列）；图像模型在 GPU1，完成后释放显存。莜面案例暴露了训练指令与实际目标矛盾，且审核解释混淆了目标与依据图片编号；原始结论保留，拒绝样本没有进入训练表。绞胎瓷的通过是模型审核结论，不是人工标注。

试跑进程使用官方 Diffusers 0.41.0.dev0（上述固定提交）、Transformers 5.17.0、huggingface-hub 1.32.0；依赖暂存在 `/tmp/edit_diffusers_official/src` 和 `/tmp/edit_qwen21_dependencies`。这些临时路径若被清理，需要重新安装同版本依赖后修改 notebook 路径。共享 env 未更新。CLI 合成使用：

```bash
PYTHONPATH=/tmp/edit_qwen21_dependencies:/tmp/edit_diffusers_official/src \
  ../env/bin/python -m curation.edit.edit_train_pipeline \
  --run /absolute/workspace/demiwtg/curation/edit/datasets/new_run \
  --sources /absolute/sources.json --config /absolute/config.json --stage all
```

数据表直接平铺本模块 `datasets/`，运行名/题目编号仅作表名后缀；不建立 runs 或 history 数据子目录。历史固定引用和共同路径根说明见仓库 `tools/lake_migration/FLAT_DATASETS.md`。
