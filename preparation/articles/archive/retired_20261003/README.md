# 文章准备

> **项目强制规范**：本 pipeline 的开发、修改、运行配置与评审必须先阅读并遵守 [《项目 Pipeline 强制规范》](../../PIPELINE_SPEC.md)。本 README 仅补充本流程的具体约定，不替代或放宽项目规范。

公共目标为共同工作区 `demiwtg/preparation/articles/datasets/articles.lance`。正式入口是 [articles_pipeline.py](articles_pipeline.py)，配置与历史结果预览在 [articles_debug.ipynb](articles_debug.ipynb)。

`default_config(**overrides)` 提供模型和材料参数；`run_pipeline(run, dataset, ids=..., sources=..., model_config=..., target_uri=..., write_mode=...)` 读取固定采集概念、文档和图片，按概念关联材料，进行身份/正文/配图审核、文章整理与终审，返回文章 Dataset 并在导出时写文章目标。

`sources` 中的 `concepts/documents/images` 分别填写 `{uri, version}`；`ids` 限定概念，`group_size` 控制分组，`through` 可选 `gather/identity/organize/extract/final_review/export`，默认只到 gather。`model_config` 配置模型、上下文、响应、并发和材料数量预算。

`target_uri` 默认 `demiwtg/preparation/articles/datasets/articles.lance`；`write_mode='merge'` 按文章主键合并完整业务结果，也可选择 `append` 追加或 `overwrite` 只保留本批结果。目标和模式仍随运行冻结，修改时使用新运行名。

配图审核继续复用图片入口的 `review_image_batches`，其结果在本运行 `visual_materials` 阶段供文章引用、路由和审计使用。文章入口不再接受 `visual_target_uri/visual_write_mode`，也不再顺带提交图片公共表；独立图片交付使用 [图片流程](../images/README.md)。

- `article_entity` 检查预检错误、正文依据及引用完整性；存在错误时不能交付为 reviewed。正文中明确的图号、方位图或指图表达（如“如图1所示”“图1”）所在段落排除，其他文字不改写，剩余段落的元信息索引同步调整。被排除段落保留在 context_json 的 audit.excluded_figure_paragraphs 中；全部依赖配图时状态为 insufficient_materials。此规则识别明确指代，不声称解决所有语义上的图像依赖。

正文、公开状态及依据在 preparation 出口完成检查。T2I 按 reviewed 读取文章，不再解析内部审核 JSON 或匹配文章配图。新规则只作用于新导出，历史固定版本不回写。

CLI：`python -m preparation.articles.articles_pipeline --run <preparation/datasets/运行名> --ids legacy:概念 --sources sources.json --through export`；`--config` 覆盖模型参数，`--target`、`--write-mode` 控制文章目标。

阶段表与日志仍平铺 `preparation/datasets/`，原始采集表只读。Notebook 的调用命令默认注释，原来图文联合预览的已保存输出保持原样；预览图片只读，不表示文章入口写图片表。

`articles_debug.ipynb` 第一格用已安装的 Lance 原生 SQL（`dataset.sql(...).build().to_batch_records()`）读取固定版本，直接写实际表路径。顶部 `CONCEPT_WHERE` 过滤概念，`ARTICLE_WHERE` 过滤文档，`IMAGE_WHERE` 统一过滤全部已发布图与文章配图；例如 `width >= 512 AND height >= 512`、`width > height` 或 `ext = 'png'`。`width/height` 来自原图表的 `resolution.stored_width/stored_height`，是存储图片的像素尺寸；NULL 不通过尺寸比较。概念先过滤再取 `CONCEPT_RANGE`，图片先过滤再按 SHA 排序并取 `IMAGE_RANGE`；尺寸过滤不改变概念编号，仍先显示文档，再显示缩略图及原图分辨率。

正文与响应协议由本目录 [prompts/tasks.yaml](prompts/tasks.yaml) 管理；仅保留文章所用任务。图片审核任务由 review/prompts 管理。`operaters/publication.py` 只交付文章，`operaters/results.py` 维护已有阶段行格式，供图片和下游读取复用。测试在本目录 `tests/`。

2026-09-29 图片对象交付：发布的图片资产带 image_uri 和 object_ref={uri,sha256}；文章/视觉材料处理按显式 URI 读取并核验 SHA。表内采集对象仅在生产者发布时导出，不再只凭 SHA 去默认采集表寻找图片。 生产图片已完成导出和原址切换，当前表使用独立对象 URI；固定历史输入的范围和已有判断保留。实际版本与退役回执见根目录 docs/image_objects_20260929.md。
