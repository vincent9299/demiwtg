# demiwtg

Pipeline 实现遵循 [AGENTS.md](AGENTS.md)：固定 prompt 正文与响应 schema 同放 YAML，数据行只绑定实际变化的输入；以 [T2I V2](benchmark/t2i/v2/t2i_v2_benchmark_pipeline.py) 为标杆，规范开头总结配置、数据流、算子边界和调试原则，并附当前图文独立取用、每概念单题的示例。


业务代码仓库；通用 Dataset、Lance 与执行能力由相邻的 demiflow 提供。

- [采集](collect/README.md)：原始材料获取与入湖。
- [基础处理](preparation/README.md)：文章整理、图片基础标注与视觉材料审核。
- [基准构建](benchmark/README.md)：T2I/Edit 各自维护 V1、V2，负责题目与输入材料。
- [训练数据构建](curation/README.md)：独立的 T2I 与 Edit pipeline，当前逐算子讨论。
- [模型评测](evaluation/README.md)：模型作答、判分和结果分析，包含 V1 评测与 BAGEL 官方套件。
- [统一环境](tools/environment/README.md)：工作区 env/bin/python。

Lance 为唯一业务存储层，业务表直接存放所属模块 datasets/，共享表平铺工作区 datasets/；路径解析根为共同工作区。源码、测试和说明纳入 Git；环境、模型、密钥与运行数据不入库。Benchmark V1 保留固定对照与回放代码；题目、图像、输出、评分和冻结证据按固定版本保存在 datasets，工作目录不留数据副本。focus1000 与旧试跑已退役，不再补跑。

目录和架构约束见 [AGENTS.md](AGENTS.md) 顶部。

现役 pipeline 按“固定版本读表 → Dataset 行/字段变换 → 必要模型调用 → 校验/展开 → 写表”组织。关联键、筛选条件、处理粒度、输出模式及提交版本在正式入口可见；同步链使用 `Dataset.write_lance(mode=..., schema=...)`，异步模型链用 `run_stream()`。训练的反馈循环直接写在入口，达到既有预算立即停止。注释说明具体操作、结果状态及落表边界；API 缺口先说明并确认，不自行新增平台能力。

2026-09-23 已完成工作目录收敛：移除 17,626 个旧文件/链接（文件约 15.18 GiB），保留 14,839 个固定证据引用，10,912 个唯一 Blob 已逐字节核验。采集和所有 `_staging` 未动。现役公共证据由 `project.HISTORICAL_EVIDENCE` 定位；该体积是工作目录移除量，不是磁盘净释放量。2026-09-24 顶层共享 `datasets/` 又清理了 19 张临时或旧表，保留 11 张公共表；旧维护运行记录已退役，本次清理清单与回执在共同工作区 `_demiflow/datasets_cleanup_20260924/`。


现役业务 pipeline 统一使用带业务前缀的 `<前缀>_pipeline.py`、`<前缀>_debug.ipynb`、`operaters/`、`prompts/`、`tests/` 和 `README.md`。Python 是唯一正式流程入口，notebook 只导入调用、手动读表看图；不设额外调试或对照 pipeline。目录与依赖边界由 `preparation/tests/test_pipeline_layout.py` 检查，完整约定见 [AGENTS.md](AGENTS.md)。

当前物理表位置、命名和历史引用迁移见 [数据布局](tools/lake_migration/FLAT_DATASETS.md)。

2026-09-24 T2I 专项清理后：旧 200 题及 800 张输出的完整证据归入 `benchmark/t2i/v1/datasets/`；删除混合 20 题与其他旧 T2I 开发实验。当前公共证据索引为 14,424 项，历史数量以上述日期快照理解；现役 V2、独立 Edit/BAGEL 与公共材料保留。清理回执在共同根 `_demiflow/t2i_cleanup_20260924/`。


现役 preparation、训练、benchmark 与 evaluation 的阶段编排遵循 [Dataset 规范](AGENTS.md)：
阶段内直接连接 reader、字段算子、模型节点与 writer；复杂行函数位于各入口的 `operaters/`。
异步结果使用 `materialize()` 固定后写表，来源表通过真实 reader 合并，`from_items` 仅用于已有内存输入。
输入版本和输出 append／overwrite 配置保持显式；运行快照仍检查源码，修改后的代码需使用新的运行名。
