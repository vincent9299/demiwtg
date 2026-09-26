# Benchmark construction · 基准构建

本模块负责构建评测基准：题目、输入材料、参考答案与任务判据。模型作答、判分与结果分析归 [evaluation](../evaluation/README.md)。

| 赛道 | V1 构建 | V2 构建 |
| --- | --- | --- |
| T2I | [说明](t2i/v1/README.md) | [说明](t2i/v2/README.md) · [出题调试](t2i/v2/t2i_v2_benchmark_debug.ipynb) |
| Edit | [说明](edit/v1/README.md) | [说明](edit/v2/README.md) · [逐算子审核](edit/v2/edit_v2_benchmark_debug.ipynb) |

两条 V2 pipeline 各有 operaters/、prompts/、带赛道和版本前缀的 pipeline 与 debug notebook，业务代码互不导入；基础材料来自 [preparation](../preparation/README.md)。

V1 两套 200 题是固定对照：T2I 保留 800 张模型输出，Edit 保留 600 张模型输出和冻结判分。题目、源图、输出与溯源已入湖；从 `project.historical_evidence()` 读取固定版本，旧目录名只是证据 ID。

2026-09-24：T2I V1 的完整固定基准已归入 `t2i/v1/datasets/`。混合 20 题、后续小批对齐实验及其他退役 T2I 实验的数据和专属查看册已清理；V2 现役实现、独立 Edit 基准和公共材料保留。
