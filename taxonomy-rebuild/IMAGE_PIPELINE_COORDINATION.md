# 图片标注和用途审核的改造分工

## 2026-10-02 用户确认的最终归属（覆盖下方旧分工）

共用层只包括 P1–P4 与共用概念正例图，归 preparation：P1–P4 实现本轮不动；旧 benchmark/t2i/image_audit 现为 `preparation/concept_positive_images`，仅交付固定定义的正例对齐／可见证据，取消通用训练评测用途判断。Edit 场景原图完整迁至 `curation/edit_scene_images`；训练入口按产物改名 `curation/t2i_training_samples`、`curation/edit_training_pairs`。2026-10-03：旧概念配图和 articles 生产入口已退役；文档主线使用 documents，图片正例主线使用 concept_positive_images，历史材料读写契约单独保留。

中性 annotation 已停并退役；原字节编码接口随唯一现役消费者移至 `preparation/concept_positive_images/operators/pixels.py`，已有数据和日志保留。目录、引用迁移和验证见 [curation README](../curation/README.md)。下方为旧协作记录，不用于恢复已退役入口。

## 负责人和文件范围

| 工作 | 负责人 | 修改范围 |
| --- | --- | --- |
| 上游中性描述、历史响应复用、证据读取接口、旧 review 消费者兼容 | 护航会话 `01a0f581-9664-7b60-8174-10abfdbabfdb` | `preparation/images/annotation/`；必要的 `preparation/images/review/` 接口整理和对应 articles 消费者、测试。保留历史 release 读取语义，不整目录搬迁 |
| 下游概念图片适用性审核、覆盖缺口及后续采样协议 | 本规划会话 `01a0f387-2b76-7d42-86be-a6fbbc285f8d` | 新建 `benchmark/t2i/image_audit/`；本文件、`benchmark/t2i/README.md` 和统一布局检查的新增登记 |
| QID 公共资产整理 | 原公共数据会话 `01a0f198-1ef8-75a3-95b3-618230fd467a` | 保持其既有范围，不在本轮重复实现 |
| 概念审定 | 原概念会话 `01a0ee22-9984-7502-9b1f-cf7c3293b135` | 保持其既有范围；下游只读固定审定快照 |

本规划会话不修改 annotation、review、articles、catalog 公共 schema、概念审定或 collect。护航会话不修改新 image_audit 模块。双方需要改同一文件或扩展 demiflow 时先登记具体 API 和调用方，再分配单一维护方；不各写一套执行、缓存、对象读取或锁。

首版下游显式复用 `preparation/images/annotation/operators/images.py::encode_pixels(raw, max_edge=..., jpeg_quality=...)`，返回 `(data_url, input_sha256)`。该函数是当前编码接口的维护位置；上游如要迁移或改变返回值，需同时登记迁移并协调下游 import 和缓存测试。本轮不改变它。

## 护航会话可以继续的工作

1. 完成原 journal 的只读完整性检查，保留暂停的 run、响应、日志和未提交文件。当前授权为代码改造及隔离验证；不自动恢复全量描述、179 万关系评分、失败清扫或公共表提交。
2. 将上游正式用途收敛到中性描述和通用观察证据。概念评分与用途发布保留历史兼容边界，不能因为新流程上线就删除旧记录或消费者字段。
3. 梳理实际使用的描述读取接口，明确归属；articles、旧 review 可复用同一生产者接口，不建立平行版本。新下游首版直接看实际图片，不依赖尚未确定的描述接口，也不要求重复生成中性描述。
4. 解耦图片描述与概念评分的配置身份时，分别证明实际模型请求缓存和公共记录身份的兼容。先做隔离、零新请求的历史回放；不承诺未完成的 64 条预留请求可直接复用，不重发未知状态请求。
5. 验证取消不会继续评分或发布；旧固定 release、其他生产者列以及 articles／T2I／Edit 消费保持兼容。只改必要调用点，不扩成整个 preparation 重写。
6. 实施情况、测试和仍需协调的问题继续写原 `preparation/images/annotation/archive/CODEX_ESCORT_full_label_v1_20261001.md`，不要与本会话并发修改本文。

## 下游首版输入和输出

下游正式入口为 `benchmark/t2i/image_audit/t2i_image_audit_pipeline.py` 的 `config → run_pipeline`。首版完成“固定输入 → 看图审核 → 按概念输出覆盖缺口”；候选召回、补图下载和最终采样各自是后续 pipeline，不藏进审核行函数。

- **概念输入**：已明确采纳的固定概念表，使用生产者现有 `concept_id / source_record_id / assessment_id / assessment / adoption_status`。保留原名、规范名、定义、限定、核心事实与来源。未采纳、身份未决、范围变化或上游技术失败保留为阻断状态，不进入模型，不自动重新审定。
- **候选关系输入**：固定 Lance 表，每行 `concept_id × sha256`，提供独立 `image_uri` 和来源线索。该表是下游召回的候选关系，允许无 QID；关联不等于已确认匹配。审核不实现公共库清洗，也不以名称或 QID 自动当作通过。
- **实际读图**：使用 demiflow 独立对象读取与 SHA 校验，完整解码后按明确参数送入模型。技术失败保留行，不能当成否决或无候选。
- **判断单位**：每个审定概念定义 × 图片；身份匹配、限定条件、可见事实及用途支持分别记录。首版训练用途仅表示材料候选，评测用途仅表示参考材料候选；均不代表具体训练指令或最终题目已经通过审核。
- **交付**：本模块独立的 typed Lance 阶段表、逐图判断和逐概念覆盖表。绑定图片 SHA、实际输入摘要、概念审定身份及定义摘要、规则／模型部署版本、用途和固定来源；不写公共 `published_concepts`，不覆盖历史评分。
- **状态**：执行完成、身份不符、证据不足、用途不适用、技术失败、没有候选分别呈现。无候选概念仍保留在覆盖分母；不可判不是低分；覆盖不足不等于审核程序未完成。
- **复用**：首版使用平台原生请求日志，只有实际请求一致才复用。旧 review 的 keep 和 annotation 关系分数不自动升级成新概念的新用途审核通过。

## 继续保留的三个接口问题

以下问题等 QID 公共表交付后共同确定，本轮不自行发明字段和规则：

1. 审定概念到 QID 的映射及等价、宽窄、待确认状态由谁生产。
2. 新下载但没有 QID 的图片如何归入公共资产库。
3. 公共概念／图片表的配对固定版本和各生产者列所有权。

首版审核通过显式候选关系表与以上问题解耦；该输入契约不要求 QID 公共整理会话提前按我们的业务规则筛图。近重复分组、来源／实例多样性、最终 train/dev/test 划分尚待对应 pipeline，不把 SHA 去重宣称为已解决泄漏。

## 验收与交接

双方开发只用隔离数据和模拟模型响应。下游验证范围和主键保全、缺图与坏图、限定条件不足、任务不适用、同请求复用、定义／规则／部署变化不误复用、模型技术失败不假完成、空输入、旧版本保护及 notebook 独立只读查看。真实模型选择与小规模质量验证另用明确配置，不启动生产批量。

完成后分别交付正式入口、固定 schema、测试命令与结果及未验证范围。本文件表示已分配工作，不表示双方实现已经验收。

## 协调进展

- 已读取护航会话在原交接文档第 10 节的回执。对方明确承接上游 annotation、描述选择接口及旧 review／articles 兼容，采用 prepare／annotate／export 分阶段，生产任务仍暂停；当时交付为设计，尚不等于实现完成。新下游与该方案没有共享写入冲突。
- 下游 `image_audit` 已实现独立入口、prompt、固定结构、四格 notebook 和隔离行为测试；未改公共表或调用生产模型。详细使用方式在该模块 README。
- 下游验收：`PYTHONPATH=demiwtg:demiflow env/bin/python -m pytest demiwtg/benchmark/t2i/image_audit/tests -q` 为 **31 passed**。统一布局检查为 **46 passed / 1 failed**，失败位置如下；未放宽规范，也未争写旧 review 文件。本轮交付审核及覆盖缺口，不声称候选召回、下载或最终采样划分已实现。
- 统一布局检查发现旧 `preparation/images/review/image_review_debug.ipynb` 第 3 格仍有 `audit_decisions/compact_image/compact_review/read_view_rows/image_sha/html_text/thumbnail_html/decision_html/model_html` 函数定义，违反 notebook 不定义函数的现行检查。属于上游会话的文件范围，请在整理 review 查看接口时一并修复并保留已保存输出；本规划会话不修改该 notebook，也不放宽检查。
