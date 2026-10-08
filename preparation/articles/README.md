# 历史文章材料契约

2026-10-03：按用户要求退役旧文章生产入口，由 [documents](../documents/README.md) 承担当前文档准备。`articles_pipeline.py` 已删除，不再提供旧文章整理、配图审核或发布的执行入口。

`datasets/articles.lance` 及其固定历史版本、原有运行表和调用日志保留。原 notebook（含已保存输出）与说明位于 [archive/retired_20261003](archive/retired_20261003/README.md)，仅供历史查阅。

本目录保留训练、出题、评测和迁移工具实际使用的历史材料读取、阶段行格式、来源绑定与测试夹具；不再登记为现役 pipeline。读图和图片历史记录契约归 [catalog/operators/records.py](../images/catalog/operators/records.py)，这些公共函数不调用旧配图审核。新概念正例图使用 [concept_positive_images](../concept_positive_images/README.md)。
