# Curation

按 pipeline 组织；唯一 Python 环境为工作区 `env/bin/python`。

| Pipeline | 入口 | 职责 |
| --- | --- | --- |
| preparation | [debug.ipynb](preparation/debug.ipynb) | 文本整理、图片基础标注与视觉审核 → 可复用基础材料 |
| benchmark | [stepbystep.ipynb](benchmark/stepbystep.ipynb) | 已发布材料 → 评测题目 |
| training | [stepbystep.ipynb](training/stepbystep.ipynb) | 候选目标与参考材料 → 训练样本 |
| evaluation | [stepbystep.ipynb](evaluation/stepbystep.ipynb) | 冻结题目 → 作答、审核与评分 |

CLI 为 `python -m curation.<pipeline>.pipeline --help`，加载对应 notebook 的同一条 demiflow 链。独立视觉子图入口为 `python -m curation.preparation.visual_pipeline --help`。

基础图片标注配置在 [preparation/configs/image_annotation_v2.json](preparation/configs/image_annotation_v2.json)。输入绑定固定 raw DatasetRef，标注写入湖内 `curated/images.lance`；文章写入 `curated/articles.lance`。发布引用固定版本，训练与评测消费明确发布。

业务算子与 prompt 归各自 pipeline；下游读取材料发布的契约归 `preparation`。Dataset、Lance、锁和 checkpoint 使用 demiflow。运行定位符为 `curation/<pipeline>/runs/<run>`，数据存入仓库外的数据湖；查看输出放相应 pipeline 的 `reviews`。

当前没有知识库；线上 RAG 的索引与召回将来单独做 pipeline。

训练 pipeline 仍在逐算子讨论；编辑原图来源和循环的最终编排尚未定稿。未启动正式数据生产或训练。修改前先讨论，明确要求修改后再动代码。
