# demiwtg

业务代码仓库；通用 Dataset、Lance 与执行能力由相邻的 demiflow 提供。

- [采集](collect/README.md)：原始材料获取与入湖。
- [策展](curation/README.md)：知识、出题、训练数据、评测四框架，当前为 V2。
- [概念与分类主数据](curation/taxonomy/README.md)：策展内部模块，固定 master release 驱动采集与策展。
- [架构 review](curation/pipeline_v2/reviews/lance_boundary_review_20260921.md)：边界调整与验证范围。
- [统一环境](tools/environment/README.md)：工作区 env/bin/python。

Lance 为唯一业务存储层，数据根默认工作区 datasets/。源码、测试和说明纳入 Git；环境、模型、密钥与运行数据不入库。只保留最新源码，归档代码已删除，历史样本与评分保留原版本身份。

目录和架构约束见 [AGENTS.md](AGENTS.md) 顶部。
