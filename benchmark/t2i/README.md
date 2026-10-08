# T2I benchmark construction

- [概念—图片对齐审核](../../preparation/concept_positive_images/README.md)：已迁入 preparation 的共用层，按固定采纳定义核对图片身份、限定和可见事实；具体题目适用性由出题流程判断。
- [粗筛（第一轮分类初筛与全量概念池）](coarse_screening/README.md)：查看三档分类、概念和图片全集规模，全部概念落表；第二轮另定规模从 high 抽样并双模型精筛；入口 [t2i_coarse_screening_debug.ipynb](coarse_screening/t2i_coarse_screening_debug.ipynb)。
- [精筛（第二轮概念逐个判断）](fine_screening/README.md)：按概念核心、区分潜力和判分可靠性独立判断；支持配置 1..N 个模型，单模型直接采用、多模型全部保留才通过。
- [V1](v1/README.md)：旧版基准构建、构题 prompts 与历史材料。
- [V2](v2/README.md)：独立构题 pipeline，入口为 [t2i_v2_benchmark_debug.ipynb](v2/t2i_v2_benchmark_debug.ipynb)。

案例生成、图评审、三类标注与历史人工复核归 [evaluation/t2i/case_annotation](../../evaluation/t2i/case_annotation/README.md)，消费本模块精筛的固定结果。旧版模型作答、判分和结果分析见 [evaluation/t2i/v1](../../evaluation/t2i/v1/README.md)。

各模块 Python 流程入口为对应的 `*_pipeline.py`；debug notebook 只组织导入、参数、调用和查看，不另定义执行图。抽样候选依次进入概念精筛与 V2 正式出题，阶段之间使用显式固定版本来源，不自动启动后续模型。
