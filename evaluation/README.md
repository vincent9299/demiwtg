# Evaluation · 模型评测

本目录按评测流程组织。题目与输入材料由 [benchmark](../benchmark/README.md) 构建；所有 pipeline 工作必须先阅读并遵守 [《项目 Pipeline 强制规范》](../PIPELINE_SPEC.md)。

- [T2I 案例评测与标注](t2i/case_annotation/README.md)：按概念生成图片、联合评审和三类标注，维护人工标签与历史复核；原 zimage_probe 已合并。
- [T2I V1](t2i/v1/README.md)：旧版模型作答、评分与固定结果查看。
- [T2I V2](t2i/v2/README.md)：模型作答与答案评分分别执行，消费明确的源表和固定版本。
- [Edit V1](edit/v1/README.md)：旧版编辑作答、评分与冻结证据查看。
- [BAGEL](bagel/README.md)：BAGEL 模型适配器及官方基准评测套件。

根目录只维护分类导航和包标识。各 pipeline 自行管理入口、notebook、`operators/`、`prompts/`、`tests/` 与 `datasets/`；根目录不再放独立评测流程或通用算子目录。

2026-10-03 整理：知识对照评测流程按用户要求删除，相关入口、算子、notebook、测试及登记已清理；用户另存的 prompt 保留。旧 Edit V1 证据读取工具和对应测试归 `edit/v1`。删除目录内没有 datasets，历史业务数据保持。
