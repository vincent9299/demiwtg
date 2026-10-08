# 按任务用途构造素材与训练样本

共用前置材料归 [preparation](../preparation/README.md)：P1/P2 审定概念、P3/P4 组织分类，以及 [共用概念正例图](../preparation/concept_positive_images/README.md)。正例图依赖 P2 技术完成且身份核准的固定定义，不依赖 P3/P4 分类，也不替下游判断具体任务可用性。

curation 的现役主线按产物用途命名：

| Pipeline | 定位与用途 | 输出边界 |
| --- | --- | --- |
| [t2i_positive_pairs](t2i_positive_pairs/README.md) | 固定 benchmark 300 概念，每概念完整输入全部审核正例，一个 prompt 联合出题和选一张或多张外观参考 | 任务、正例图二元组及同一任务、参考图列表、正例图三元组；逐目标留存结果，拦截同图与模型判定可直接回答任务的参考 |
| [image_facts](image_facts/README.md) | 为已选正例素材标注对象、关系、视角和观察限制，并比较三个模型 | 逐图事实、详细caption、原调用证据与一致性；当前仅10概念64图试验 |
| [concept_image_tasks](concept_image_tasks/README.md) | 依据当前/训练分布分析缺口，由固定 Dataset 图与独立 map_prompt 节点完成正例复用、多视角或组合场景补图、图审、题目合成及题审 | 共用图片对象库；仅交付题审通过的任务候选，保留失败与剩余缺口 |
| [edit_scene_images](edit_scene_images/README.md) | 为既有 172,297 张场景候选原图计算 WeMM 向量，构建 Edit 检索素材池 V1 | 原图 URI、SHA 和向量；任务适用性由下游看图判断 |
| [t2i_training_samples](t2i_training_samples/README.md) | 按学习目标构造并审核 T2I 训练样本 | 训练输入、目标图与样本审核结果 |
| [edit_training_pairs](edit_training_pairs/README.md) | 按学习目标设计编辑、合成另一端并审核图对 | 源图→目标图、编辑指令与图对审核结果 |

2026-10-03：旧概念配图审核目录已删除，现役正例审核使用 preparation/concept_positive_images；旧 notebook 输出与审核档案保存在 `archive/legacy_concept_images_20261003/`。公共中性 caption/richness 标注已退役，源码封存 [archive/image_annotation_20261002](archive/image_annotation_20261002/README.md)，不自动恢复全库标注。2026-10-04 用户另行授权 image_facts 在已选10概念64图上试验上述检索事实标签；该小范围试验不发布公共列。其数据与日志仍保留在 preparation/images/annotation，公共历史列未删除。

新概念正例图只审核 concept_id × SHA 与固定定义的对应关系、限定条件和可见事实，不再输出 training/reference_eligible 或通用配额。Edit 场景背景、待纠错图可能有意不匹配目标概念，按实际素材角色消费，不能把正例对齐设为所有选图的必经门槛。

## 迁移与运行

正式入口、notebook、CLI、调用方和源码清单统一使用用途名称，无旧 Python 转发入口。Edit 场景原图的业务表、SQLite 调用日志和历史材料完整迁入本模块，T2I/Edit 训练模块随重命名迁移自己的数据；固定表版本、请求身份、历史判断和 notebook 已保存输出保持。

旧固定表引用通过工作区 `_demiflow/lance_locations.json` 精确解析；旧 SQLite／离线输入引用通过 `_demiflow/artifact_locations.json` 解析。只在读取位置层映射，不修改冻结记录。数据迁移回执为工作区 `_demiflow/curation_ownership_20261002/`。重命名改变源码身份，新的训练实验使用新 run；既有结果保持只读可查。

公共表按生产者归 preparation，master 底库归 collect，registry 归 `_demiflow`；具体位置与待迁移项见 [归属说明](../docs/asset_ownership_20261002.md)。

2026-10-02 的目录迁移验收只使用隔离测试和模拟响应，没有调用生产模型或重新发布公共业务表；后续 Edit 向量实跑见下方记录。

2026-10-03：Edit 场景原图改为复制公共 image_embeddings 的标准向量生产流程，固定读取原候选 image_inputs@13。旧分类/视觉标注源码、prompt 和 README 已替换；原表和调用日志保持，历史 reviews 与 Notebook 输出移至 `archive/edit_scene_images_annotations_20261003/`。全范围 172,297 张已处理，172,292 个成功向量发布为素材表 @9，IVF_HNSW_SQ/cosine 索引完整覆盖；5 张截断源图保留失败，整轮编码状态仍为 incomplete。实际检索和索引复用已验收，双卡释放。固定引用与证据见 [模块 README](edit_scene_images/README.md)。
