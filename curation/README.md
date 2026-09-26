# 训练数据构建

curation 当前保留两条训练数据 pipeline，各自独立维护 operaters/、prompts/、测试和审核 notebook。

| Pipeline | 入口 | 状态 |
| --- | --- | --- |
| t2i | [执行与逐算子审核](t2i/t2i_train_debug.ipynb) | 已接通材料准备、构题、样本审核、每概念 limit(5) 与 Lance 交付；尚未正式生产 |
| edit | [逐算子讨论](edit/edit_train_debug.ipynb) | 已实现联合设计、双向合成、图对审核与标准 Lance 交付，两个 case 试跑 |

基础材料准备已移到顶层 [preparation](../preparation/README.md)，评测构题已合并到顶层 [benchmark](../benchmark/README.md)，作答与评分已移到顶层 [evaluation](../evaluation/README.md)。

T2I 已按每概念独立子流取五条合格样本、不足按实际交付实现；Edit 默认本地 Qwen3.8-27B 与 Qwen-Image-2.1，可逐阶段运行。没有启动正式数据生产或训练。修改前先讨论，明确要求修改后再动代码。
