# Edit V1 · 基准构建

`target_uri`（CLI `--target`）指定新运行的题目输出表。`write_mode='overwrite'`（默认）覆盖最终目标表，`write_mode='append'` 追加本次输出，不去重或更新已有行。notebook 显式配置 `WRITE_MODE`，CLI 使用 `--write-mode overwrite|append`。目标路径和写入模式随运行冻结；新增批次用新 `RUN_ID`，更新已有结果用覆盖。中间阶段表仍按原来的断点规则保存。

## demiflow 框架版（当前活动入口，v6.1 image-first 构题链）

- [执行与逐算子检查 notebook](edit_v1_benchmark_debug.ipynb) · CLI：`python -m benchmark.edit.v1.edit_v1_benchmark_pipeline --help`
- 数据链：`read_plan`（plan.jsonl 显式导入边界；原图定位+SHA 校验+入湖）→ 入口的 `flat_map`／`map`（按 plan 的原图×请求文本展开，job_id 保持 `qid_a{n}_{image_index}_{edit_type}` 语义）→ `prepare_construct`/`apply_construct`（v6.1 出题请求与 expected_meta join）→ `AuditConstruct`（按协议 §6/§8/§9 重推的结构机审：封闭枚举、回显、cannot_construct 契约）。
- 唯一请求图片是 before 原图（角色 `edit_source_before`）；caption/desc 等辅助文本只作为 plan 请求文本中的待核假设。默认 offline 缺响应记 `pending_construct`。阶段停靠 `--through {plan,dispatch,requests,construct,audit,export}`。
- prompts：`prompts/tasks.yaml`（完整 v6.1 协议正文、模型配置和响应 schema；历史 dispatch 记录不改写）。
- `tests/`：plan 导入、dispatch、响应绑定、expected_meta 与冻结 bench200 的逐字段等价、协议枚举与 md 交叉验证。

**已知缺口**：历史构题驱动脚本（plan 生成策略、削峰禁令计算、严格校验器实现）未随目录迁移保留——本链从 plan.jsonl（批次策略的自记录载体）开始；`AuditConstruct` 是按协议文本重推的结构校验，不是历史校验器的复原。plan 生成本身的业务政策如需重建，需用户决定。

## 固定基准

正式题库、源图和必要构题溯源已保存在固定 Lance 版本，工作目录的 bench200、候选池和旧试跑已删除。debug 直接使用 `project.historical_evidence().reference(...)`；CLI 显式传 `evidence:<证据ID>`，外部 JSONL 仅作为显式导入边界。

历史构题复杂度审核记录保留在 [archive/complexity_audit_synth.jsonl](archive/complexity_audit_synth.jsonl)。

模型对比统一在 [evaluation/edit/v1](../../../evaluation/edit/v1/README.md)，新构题方法在 [V2](../v2/README.md) 继续。

Python 流程入口在 `edit_v1_benchmark_pipeline.py`；debug notebook 是按需运行命令的地方，只保留导入、参数、调用和简单查看。

数据表直接平铺本模块 `datasets/`，运行名/题目编号仅作表名后缀；不建立 runs 或 history 数据子目录。历史固定引用和共同路径根说明见仓库 `tools/lake_migration/FLAT_DATASETS.md`。
