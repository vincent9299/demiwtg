# 基础材料准备 · V2

交付边界（2026-09-26）：清洗在 preparation 出口完成，T2I 下游只按公开状态取独立文字和图片。

- `article_entity` 检查预检错误、正文依据及引用完整性；存在错误时不能交付为 reviewed。正文中明确的图号、方位图或指图表达（如“如图1所示”“图1”）所在段落排除，其他文字不改写，剩余段落的元信息索引同步调整。被排除段落保留在 context_json 的 audit.excluded_figure_paragraphs 中；全部依赖配图时状态为 insufficient_materials。此规则识别明确指代，不声称解决所有语义上的图像依赖。
- `write_curation` 按传入的原图 DatasetRef 读取来源元数据，统一来源和生成标记；已知生成图、公开状态矛盾、缺支持范围的关系不交付为 published/keep。source_refs 保留实际存储路径和版本；外部 URL 缺失不等于来源缺失，可追溯的原始表绑定仍保留，不编造 URL。原始审核 JSON 保留作审计，下游无需解释。
- T2I 不再匹配文章配图或消费原始引用。上游内部的图文审核和溯源字段继续保留，供准备过程和其他消费者使用；不重跑模型来完成上述出口清洗。
- 新规则作用于新的实体导出；旧固定版本和已保存的历史结果不回写。使用新规则时需重新导出 preparation 结果，并在消费者配置中指定新版本。

文章目标使用 `write_mode`，文章流程中的图片目标使用 `visual_write_mode`；独立视觉流程与重放入口使用 `write_mode`。可选 `'append'`（按行追加，不合并同主键）、`'overwrite'`（仅保留本次结果，空结果也会清空目标）或 `'merge'`（默认，保留原主键合并）。notebook 对应 `WRITE_MODE`、`IMAGE_WRITE_MODE`，CLI 对应 `--write-mode`、`--visual-write-mode`。目标和模式随运行冻结，变更时使用新运行名；原始图文表不受结果写入模式影响。

文章入口的 `sources` 配置 `concepts/documents/images` 的 `uri/version`；`target_uri` 指定文章结果表，`visual_target_uri` 指定图片结果表。独立视觉入口用 `input_path` 指定固定输入，`target_uri` 指定图片结果表。结果默认沿用原有按主键合并语义（`merge`），也支持显式追加和覆盖。 `preparation_debug.ipynb` 已列出可修改的源、目标参数。

原始材料 → 清洗与筛选 → 文章整理／视觉审核 → 保存结果。没有独立的发布流程。

```text
preparation/
  operaters/                         文档/图片 I/O、模型协议及专用校验
  prompts/                     prompt 源码、组装、标注配置、离线请求／响应
  preparation_pipeline.py                  文章/视觉流程、必要配置与统一 CLI
  preparation_debug.ipynb                  按需调用、读表和看图命令
  tests/
```

| operaters 模块 | 职责 |
| --- | --- |
| inputs | 固定输入、材料转换、证据范围与图片角色校验 |
| documents | 文档解析、正文清洗、来源结构修复 |
| identity | 材料可用性与身份审核响应校验 |
| text | 正文分块、相关性选择 |
| images | 图片 schema 与字节绑定、标注复用、双模型审核、结果校验 |
| routing | 图文关联、embedding 与容量分组 |
| article | 文章请求与解析、引用终审、文章 schema 与写入 |
| results | 阶段行转换、保存 curated 实体及固定结果引用 |
| runfiles | Lance run/stage 绑定、来源与源码冻结 |

map、join、reduce、分批、并发模型调用、锁和通用进程工具直接使用 demiflow。`preparation_pipeline.py` 在主线展开来源谓词、关联键、材料数量统计及字段投影；同步阶段用 `Dataset.write_lance(mode=..., schema=...)`，异步响应按批交给官方 Lance writer，用 `read_lance` 读取已提交固定版本；阶段表位于数据根的 `demiwtg/preparation/datasets/<stage>__<run>.lance`。业务模块定义字段、规则和输入绑定；没有 data、runtime、configs、inspection 辅助层。

CLI：`python -m preparation.preparation_pipeline --help`；文章用 `--flow article --ids ...`，视觉用 `--flow visual --input ...`。CLI 与 notebook 直接调用 `preparation_pipeline.py` 中的同一个函数。离线请求查看与回填入口为 `python -m preparation.prompts.responses --help`。

运行定位为 `preparation/datasets/<run>`，业务数据只写 Lance。原始表只读，视觉结果绑定固定 raw DatasetRef；结果写 `preparation/datasets/articles.lance`、`preparation/datasets/images.lance`，不登记 release。历史 run、旧 release 和存量字段保留原数据契约；历史 release 导入归 `tools/lake_migration/`。

Python 流程入口在 `preparation_pipeline.py`；debug notebook 是按需运行命令的地方，只保留导入、参数、调用和简单查看。

`preparation_debug.ipynb` 第一格用已安装的 Lance 原生 SQL（`dataset.sql(...).build().to_batch_records()`）读取固定版本，直接写实际表路径。顶部 `CONCEPT_WHERE` 过滤概念，`ARTICLE_WHERE` 过滤文档，`IMAGE_WHERE` 统一过滤全部已发布图与文章配图；例如 `width >= 512 AND height >= 512`、`width > height` 或 `ext = 'png'`。`width/height` 来自原图表的 `resolution.stored_width/stored_height`，是存储图片的像素尺寸；NULL 不通过尺寸比较。概念先过滤再取 `CONCEPT_RANGE`，图片先过滤再按 SHA 排序并取 `IMAGE_RANGE`；尺寸过滤不改变概念编号，仍先显示文档，再显示缩略图及原图分辨率。

数据表直接平铺本模块 `datasets/`，运行名/题目编号仅作表名后缀；不建立 runs 或 history 数据子目录。历史固定引用和共同路径根说明见仓库 `tools/lake_migration/FLAT_DATASETS.md`。
