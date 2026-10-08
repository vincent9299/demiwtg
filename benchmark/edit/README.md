# EDIT benchmark construction

- [V1](v1/README.md)：旧版基准构建、构题 prompts 与历史材料。
- [V2](v2/README.md)：Codex 概念核心出题、可选原图生成与作者检查，入口为 [调试 notebook](v2/edit_v2_benchmark_debug.ipynb)。
- [Edit 可检索原图 V1](../../curation/edit_scene_images/README.md)：对既有 172,297 张候选原图计算 WeMM 向量；检索接入出题由后续任务完成。V2 的旧 source_pool 接口仍读取冻结的标注池协议。

旧版模型作答、判分和结果分析见 [evaluation/edit/v1](../../evaluation/edit/v1/README.md)。
