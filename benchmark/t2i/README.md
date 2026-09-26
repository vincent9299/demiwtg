# T2I benchmark construction

- [V1](v1/README.md)：旧版基准构建、构题 prompts 与历史材料。
- [V2](v2/README.md)：独立构题 pipeline，入口为 [debug.ipynb](v2/debug.ipynb)。

旧版模型作答、判分和结果分析见 [evaluation/t2i/v1](../../evaluation/t2i/v1/README.md)。

Python 流程入口在 `pipeline.py`；debug notebook 是按需运行命令的地方，只保留导入、参数、调用和简单查看。
