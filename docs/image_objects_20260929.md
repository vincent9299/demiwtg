# 图片对象引用改造记录（2026-09-29）

本次按用户确认，覆盖全部现役图片消费、生成、评测和模型文件交付。图片引用遵守 [Pipeline 规范 S11](../PIPELINE_SPEC.md#s11--存储与平台边界)：独立对象的稳定 URI＋内容 SHA256。用户随后明确要求彻底导出并切换，因此本项目不再新增表内图片。

## 实际落点

| 范围 | 新交付/读取方式 |
| --- | --- |
| collect | 图片先按 SHA 写独立 objects/；采集表只保存 image_uri 和元数据，拒绝向尚未迁移的旧 schema 继续写入 |
| preparation catalog/review | 发布公共 image_uri；source_refs 仅保留来源追溯，不再交给下游定位像素 |
| preparation annotation/articles | 按显式 URI 读图并核 SHA；缺引用不回查默认 collect 表 |
| benchmark T2I/Edit、source_images | 公共行 image_uri 转为 object_ref={uri,sha256}；输入/池/产物不复制跨表 Blob 引用 |
| zimage_probe、curation、evaluation | 生成图写共享 objects/，结果行持有 object_ref；训练材料不再强制绑定默认 raw 图片表 |
| demiflow Codex 文件产物 | artifact_store={directory:绝对路径}，交付 name/byte_size/object_ref；文件协议版本更新，完整请求相同时验证对象后复用 |
| 命名证据 ArtifactSet | 新索引保存 object_uri；旧索引允许只读审计，发布新引用前要求显式迁移 |

通用能力在 `demiflow.objects`：ObjectRef 读取普通 URI、校验内容；LocalObjectStore 以 SHA 命名普通文件，分块写入并原子发布，相同内容共用对象，已有损坏文件不被静默覆盖。file URI 使用稳定的绝对挂载路径；更换挂载位置须显式迁移 URI。远程读取使用标准 HTTP/PyArrow 对象存储接口，本次没有部署远程对象存储写入服务。

原生 Lance Blob 读写保留。跨表 LanceBlobStore 写入和通用 BlobAssetReader 已移除；项目专属采集读取归 collect。BlobRef 仅保留给冻结证据/历史预览，不产生新的生产引用。Notebook 已保存输出、执行计数和元数据保留；只读历史查看同时识别旧引用。

## 生产迁移结果

已执行全量导出、原址切换与旧图片载荷退役。审计目录为共同工作区 `_demiflow/image_object_cutover_20260929/`，不是运行时读取依赖。

- 导出并登记 **2,133,062 个独立对象，共 991,348,115,437 字节（923.265 GiB）**。其中原始可用图片 2,127,760 张，另含生成图、基准图和请求预处理产生的不同内容。每个导出对象核验源 SHA、目标 SHA 和大小；失败账本为 0。相同 SHA 共用一个普通文件。
- 原始表共 2,164,671 行，36,911 行在迁移前就没有图片字节。这些行保留元数据和空 image_uri，没有伪造图片。此次迁移结束时 subset 的 490,575 行仍为 pending_cos；随后按用户补充交接另行全量核验下载文件，已发布 subset@5、collect QID@11，见 [QID 下载回填记录](../collect/download/HANDOFF_subsets_pipeline_数据交接.md)。
- **75 张表完成切换和原目录退役**：6 张原生图片资产表、公共图片目录、文章插图表、62 张 typed/JSON 引用表和 5 张 preparation 请求阶段表。原始采集、生成和评测的新写入路径统一保存独立对象，Lance 当前业务表只带 URI、SHA 及元数据。
- 切换前后逐行核对业务内容；只改引用列的表保留原数据文件，评审、评分、描述等嵌套内容不重新编码。迁移时全表重编码曾触发 Lance 嵌套字符串错位，对账阻止了该结果提交，最终采用只更新引用列的实现。
- 106 篇文章的 678 张插图也从旧本地路径改为共享对象 URI；正文、选图结论、原始来源路径和引用证据保持，6 份 notebook 的文章输入从同一行范围的 articles@4 更新为 @8。
- preparation 请求表的内嵌 data URL 已外置，消费时恢复值与原请求完全相同；请求身份和已有审核结果保持。旧图片资产表历史版本仍可读：data 字段保留为指向独立文件的 External Blob 描述，实际像素已不在 Lance 文件内。新 head 删除 data。
- 原 `.blob` 文件和切换用 `.lance.original` 目录已删除；原始版本/事务清单留在审计目录。`retirement_summary.json` 分别记录移除的目录逻辑字节数和独占文件字节数；硬链接不冒充释放空间。独立图片文件成为唯一资产实体，不能将这次搬迁宣称为额外节省了整库图片大小。

主要固定版本：

| 表 | 当前版本 | 行数 |
| --- | ---: | ---: |
| collect/datasets/images.lance | 8 | 2,164,671 |
| 工作区 datasets/images.lance | 16 | 2,164,671 |
| 工作区 datasets/articles.lance | 8 | 53,091 |
| 工作区 datasets/qid_sub_100k_bucket_v1_images.lance | 4（后续下载挂接为 5） | 490,575 |
| benchmark/t2i/v1/datasets/bench200_blobs.lance | 2 | 1,225 |
| benchmark/t2i/v1/datasets/bench200_artifacts.lance | 4 | 1,225 |
| zimage_probe/datasets/blobs__zimage_concept_probe_v3_20260929.lance | 470 | 469 |
| evaluation/t2i/v2/datasets/images__three_models_gpt6_sol_20260925_01.lance | 13 | 12 |
| evaluation/t2i/v2/datasets/answers__three_models_gpt6_sol_20260925_01.lance | 17 | 12 |

表的完整路径和所有迁移版本以 `commits/*.json` 为准。原公共 images@5/@9 的选样范围分别保存在 preparation/datasets/image_catalog__object_migration_public_v5.lance@1、public_v9.lance@1；这些是保留旧范围的持久元数据视图。11 份 notebook 更新固定来源，输出、执行计数和元数据逐项保留。review 的 raw@5 继续保持旧审核身份，读取的实际像素已经外置。迁移前打开的 notebook 内核需重启，以释放旧模块和 Lance 文件缓存。

全量 annotation 用原 run、模型参数及 SQLite journal 恢复，目录输入切至 public@16，语义 config_id 不变。停机前的 117,980 条完整响应全部保留；185 条被迁移打断的未完成本地请求先归档恢复事件，再解除占位。恢复后观察到至少 16,000 张图片通过旧请求响应回放，持久调用计数仍为 118,165，没有因存储迁移重做完整响应。进度日志中的“复用”仅统计公共表业务记录，不统计 SQLite 响应命中。恢复 PID、日志、停止快照及恢复记录见 annotation_resume.json / annotation_recovery.json。全量标注仍在后台运行，其整库完成不属于本次迁移验收。

历史模型调用日志按既有证据保全规则原样保留，部分旧请求日志仍含当时的 base64 图片；本次资产迁移不重写这些日志。新 annotation 的 SQLite journal 只存请求摘要和响应，不复制图片。归档源码、模型结果和保存的 notebook 输出也不因迁移删除。

本次生产工具见 [维护工具说明](../tools/lake_migration/README.md#图片导出为独立对象2026-09-29)。运行时 ObjectRef 只需要普通 URI 与内容 SHA，不需要迁移账本、源表版本或 Lance 内部 .blob 路径。按图解码、缩放和请求编码仍会占用内存。

## 验证

本次生产回执包括 objects.sqlite、tables/、column_references/、requests/、commits/、retirement_summary.json 和 final_verification.json。当前 284 张现役表的 head 已复查，无图片二进制列；179 张业务表的 JSON/typed 引用完整扫描无待迁移项。56 条登记的固定 DatasetRef 仍可打开，删除旧文件后重读历史像素与当前 URI 均通过。文章插图 678 项完整对账，评测对象 44 次读回通过。最终完成标记为 completion.json。

对象导出、历史版本保留、空图片状态、裸 JSON 引用转换与物理退役的隔离回归 8 项通过；collect 写入回归 4 项、notebook 隔离执行 3 项通过。preparation 本轮相关回归 215 项通过，通用对象验证 10 项通过。布局检查 43 项通过；zimage 旧布局用例仍要求 5 格而当前用户 notebook 为 6 格，此轮不删除新增单元格来迎合旧断言。

下列为首次代码改造的隔离验证记录（有交叉，不累计为总用例数）：

- preparation articles/review/catalog/annotation：270 项通过；涵盖新对象在删除测试源表后可读、存储失败不发布、部分列更新、内容损坏拒绝复用。
- curation Edit/T2I：55 项通过。
- benchmark Edit V2/source_images/T2I V2/zimage_probe 与 evaluation T2I V2：237 项通过。
- collect、对象存储、Codex 产物、ArtifactSet、旧位置映射及迁移基础检查：57 项通过。随后新增的 ArtifactSet 迁移检查与本地 writer URI 检查单独通过。
- evaluation 根流程：29 项通过；剩余一项旧用例改为验证“删除采集表仍可评分、删除独立对象必须失败”，重跑通过。
- 统一布局检查、上述评测用例与对象存储检查合并重跑：54 项通过。
- V1 与平台合并回归：63 项通过，5 项历史回放因工作区缺少 `datasets/retained_artifacts__t2i_cleanup_20260924.lance` 无法完成；未造数据替代历史证据。
- 源文件语法、diff 空白检查，以及有改前备份的 8 份 notebook 的保存输出/执行计数/元数据逐项核对通过。

生产完成依据实际导出、逐行对账、原址提交和退役回执；上述隔离测试不证明真实模型质量或生产吞吐。
