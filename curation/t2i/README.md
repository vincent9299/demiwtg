# T2I 训练数据 pipeline

固定设计／审核正文与响应 schema 同放 `prompts/tasks.yaml`，逐行只绑定材料和图片，不再加载 MD 再注入 YAML。


`write_mode='overwrite'`（默认）覆盖最终目标表，`write_mode='append'` 追加本次输出，不去重或更新已有行。notebook 显式配置 `WRITE_MODE`，CLI 使用 `--write-mode overwrite|append`。目标路径和写入模式随运行冻结；新增批次用新 `RUN_ID`，更新已有结果用覆盖。中间阶段表仍按原来的断点规则保存。

文章、图片来源用 `article_sources` / `visual_sources` 的 `uri/version` 指定；`target_uri` 指定最终训练样本表。 `t2i_train_debug.ipynb` 已列出可修改的源、目标参数。

[t2i_train_pipeline.py](t2i_train_pipeline.py) 定义正式流程、配置与 CLI。[t2i_train_debug.ipynb](t2i_train_debug.ipynb) 只保留一个代码格：从 preparation 文章和图片结果表筛选概念，调用 GLM 构题并审核，写入新的训练表，再读回按条目展示；参考图与监督目标可点击预览。

## 执行顺序

1. 读取固定版本的基础材料，按概念组合文本、视觉材料和目标候选，固定图片 Blob 版本。
2. 每概念在正式入口执行独立的反馈循环。当前目标逐批看兼容材料，没有相关性 top-k，也不预分参考/目标池。
3. `design_candidates` 联合确定具体学习目标、`draft.instruction`、构题依据、实际作答材料及目标支持说明。
4. `ExpandCandidates` 展开并固定材料选择，`ValidateSample` 检查契约、隔离和像素身份。
5. 单独调用 `review_sample`，核验题目依据、实际输入充分性、目标可见学习信号、完整指令满足度、图像可用性及泄漏，并逐条审核判据。不是采用设计者自评；默认仍使用同一配置模型，不声称模型间独立审核或人工 golden。
6. 审核全部通过后，`BindTrainingInputs` 只组装实际所选正文和像素；`ExportSample` 输出可加载的训练条目。
7. 每次目标访问得到一条合格样本就换目标。每接受一条更新计数；达到 `samples_per_concept`（默认 5）即停止，不再设计下一条。默认最多两轮目标访问，耗尽不足 5 条按实际交付。没有全局样本条数配额。

概念遍历与反馈控制在 `t2i_train_pipeline.py` 编排层。业务算子不共享全局计数器。设计/审核通过原生 `map_prompt_async` 执行，每次仅提交一个目标批次。离线待响应或调用失败暂停当前概念，其余概念继续；不会把没收到响应算作不合格样本。

## 材料和学习契约

- 知识文本可选，图片不要求是文章配图。已有图作为监督目标，T2I 没有编辑原图，也不执行生图。
- 学习方向为概念视觉特征、参考信息应用、条件或关系应用，可重叠，也允许其他有依据的方向。它们不是分类配额。
- `evidence` 是构题/审核依据；`input_materials` 是实际作答材料，可为空。两者分别选择和补全必需配图。
- 所选图片只交付像素和顺序标识，所选文本只交付正文。caption、来源、审核意见、`knowledge_application`、学习目标和判据不会自动进入输入。
- 目标及近重复不能进入当前样本的依据或作答输入；一张图可在另一条样本中换角色。后续不得替换已选择材料，改变内容必须重新设计、审核。
- 数量按训练条目计，同题不同目标可分别交付。程序排除同概念内指令、实际输入、目标完全相同的重复条目；回访时提供已接受条目的简要记录，不引入题目唯一性配额。
- 当前按批查看，没有跨批主动保留参考的专门策略。两轮回访是可调整预算；训练价值和迁移收益仍需后续独立验证。

## 配置和入口

只准备材料：

```bash
../env/bin/python -m curation.t2i.t2i_train_pipeline \
  --run curation/t2i/datasets/<run> \
  --sources <sources-config.json> --through materials
```

完整流程默认 `offline`，只产生绑定请求并等待显式响应；不调用真实模型：

```bash
../env/bin/python -m curation.t2i.t2i_train_pipeline \
  --run curation/t2i/datasets/<run> \
  --sources <sources-config.json> --config <run-config.json>
```

`sources-config.json` 直接指定 preparation 结果表路径与固定版本，`articles` 和 `visuals` 至少一项非空：

```json
{
  "articles": [{"uri": "demiwtg/preparation/datasets/articles.lance", "version": 4}],
  "visuals": [{"uri": "demiwtg/preparation/datasets/images.lance", "version": 5}]
}
```

相对路径从工作区根解析。文章只取已审核记录，图片只取审核保留且可交付的概念关系；不查询发布登记。实际来源及版本冻结到运行记录中。

`run-config.json` 可省略，默认关键值：

```json
{"mode": "offline", "samples_per_concept": 5, "max_target_cycles": 2,
 "max_training_attempts": null, "reference_batch_size": 8,
 "max_reference_images": 16, "max_context_chars": 60000}
```

`max_training_attempts` 是每概念调用尝试预算，不是全局样本数量。`concepts`/`max_units` 限定探索概念；不提供则遍历声明来源中的全部概念。`split_registry` 可传现有隔离注册表对象；省略时仅采用 development_only 的空保留范围，不能声称已经对正式测试集完成隔离。

`--mode local` 调用现有本地 Qwen；`--mode modelhub` 调用显式配置的网关，默认 `http://127.0.0.1:4001/v1` 与 `glm/glm-5.3-flash`。pipeline 不启动或重启服务。`model.max_calls` 分别限制每个已声明模型节点的新请求数，默认无限制，不是整个 pipeline 的共享预算；网关鉴权用 `model.api_key_env`，不保存密钥。

数据迁移后首次使用，先 **Restart Kernel**，再从第一格运行。迁移前启动的内核可能仍缓存不支持位置映射的 `BlobRef` / `LanceRecordStore`，仅重新运行 cell 或修改数据根不会刷新这些类。此时会报 `raw/images.lance` 或 `runs/pipeline/.../model/calls.lance` 不存在；这些历史引用由新版 demiflow 通过工作区 `_demiflow/lance_locations.json` 解析到实际表，不应修改历史引用或重建旧目录。

Notebook 显式设置 `DATA_ROOT = PROJECT.parent`，并直接填写 preparation 的 `articles.lance` v4 和 `images.lance` v5。它与 benchmark T2I 使用相同输入格式和读取规则，`CONCEPTS` 筛选本次概念。

`MODEL` 默认 `glm/glm-5.3-flash`，通过同一个 `run_pipeline()` 执行材料准备、构题、校验、审核和导出。默认每概念最多 2 条、最多 4 次目标尝试、最多 24 次模型请求；输出预算为 16384 tokens，不沿用上次对照的 4096-token 限制。达不到目标时只交付实际通过审核的条目，失败与未交付原因保留在 `incomplete` 表。

`RUN_ID` 命名一次运行，换来源、概念或配置时使用新名字；同一运行可续跑或复用已完成结果。训练条目由正式 pipeline 写入 `datasets/training_samples__<RUN_ID>__<指纹>.lance`，notebook 显示实际 `TABLE_URI` 和固定 `VERSION`，再用标准 `read_lance()` 读回。表格一行一条，显示样本 ID、概念、完整训练输入与监督目标；图片从固定 BlobRef 读取，点击放大/收起。仅在展示时生成 HTML，不把图片预览或构题审核信息混入训练输入。

手动执行此 cell 会调用模型。维护代码时只用隔离数据和模拟响应验证完整链路，不替用户自动启动正式生成。

离线请求和响应存于运行目录对应的 Lance model 表。通过 `demiflow.operator_llm.lance_journal.submit_response` 对请求的 `RecordRef` 提交原生 `{"result": ...}` 响应，metadata 需有实际 `reviewer`/`reviewer_kind`；随后用相同配置续跑。旧阶段表和请求不覆盖，新增响应参与后续阶段身份。来源、源码、配置变化需用新 run。

## Lance 输出

表路径、schema、`lance.write_dataset` 和 `data.read_lance(..., version=...)` 直接写在 pipeline 中。异步构题/审核用标准 `run_stream()`，分批接官方 Lance writer；同步阶段使用 Arrow RecordBatchReader 流式写入。空表同样保留 schema。没有 checkpoint 封装；历史固定引用通过标准存储的位置映射继续读取。

运行定位符 `curation/t2i/datasets/<run>` 仅提供运行标识，表直接写在 `curation/t2i/datasets/`。唯一业务存储是 Lance；JSON 文件仅为输入配置，不是结果副本。

| 结果 | 含义 |
| --- | --- |
| `training_samples` | 仅合格训练条目，单独 Arrow schema，可直接按固定 DatasetRef 读取 |
| `concepts` | 每概念合格条数、停止原因、尝试与交付引用 |
| `attempts` | 本次执行中每次目标/批次的最终结果，包括接受、拒绝、等待与失败 |
| `incomplete` | 未交付记录与具体原因 |
| `<stage>__<run>__<task_id>__<fingerprint>.lance`、metadata/model 表 | 候选、设计/审核请求响应、判据、来源、版本及恢复证据 |

汇总阶段写到 `<stage>__<run>__<fingerprint>.lance`，合格训练条目写到 `training_samples__<run>__<fingerprint>.lance`。写入成功后才登记完成记录；目录存在但未完成的表会显式报错，不作为可复用结果。历史 checkpoint 表已整表迁移并平铺，原固定引用仍可解析，历史行不重写。

CLI 返回 `stages.training_samples.dataset_ref`。最终训练表每行：

- `sample_id`、`concept`、`task_type`、`instruction`。
- `input_content`：实际输入的有序文本/图片列表；图片为固定版本 `blob_ref`，无 base64 副本。
- `target`：单张监督图的 MIME 与固定 `blob_ref`，不属于作答消息。
- `audit_ref`：内部构题/审核记录的固定引用；训练加载器不将其拼入输入。

`operaters/results.py:sample_messages` 按需还原实际模型消息，目标用 `demiflow.lance.blobs.BlobRef.read` 单独加载。图片读取验证 SHA；旧文件路径仅为来源记录。0 条合格样本也提交带完整 schema 的空表。后续续跑得到新快照，旧 DatasetRef 仍可读；消费者使用返回的固定引用，不跟随表 head。

## 验证

`tests/` 使用隔离数据根、合成图片及明确标注的模拟响应，执行真实 demiflow/Lance/离线传输链路。测试不证明真实样本语义质量，也不调用真实 VLM。

[Edit pipeline](../edit/README.md) 独立维护算子、prompts 与合成契约。

Python 流程入口在 `t2i_train_pipeline.py`；debug notebook 是按需运行命令的地方，只保留导入、参数、调用和简单查看。

数据表直接平铺本模块 `datasets/`，运行名/题目编号仅作表名后缀；不建立 runs 或 history 数据子目录。历史固定引用和共同路径根说明见仓库 `tools/lake_migration/FLAT_DATASETS.md`。
