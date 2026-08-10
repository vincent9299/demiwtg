# demiwtg · V2融合世界标签体系治理

> 在迁移自上一个项目的 Safe-Gated Scheduler（安全门控调度器）之上，修复、归并并安全扩展 V2融合世界标签体系（33,339 节点树）。

当前状态：仓库刚初始化（仅 `chore: init`），已有标签体系审查报告、6 份审查清单与 149.8 万条完整词条拼音表（`data/raw/`）；调度引擎代码尚未迁入；原始树文本尚未入库。

---

## 1. 项目定位

两条主线：

- **主线 A：标签体系修复** —— 清理 V2融合世界标签体系在"双源融合 + 机器翻译"后遗留的内容层问题：重复子树、兄弟重名、父子同名、子树错挂、facet 异常、机翻脏命名。
- **主线 B：安全关系扩展** —— 迁入上一个项目的 Safe-Gated Scheduler，以"概率模型预测关系、图引擎计算收益、双模型 Gate、闭包传播"的方式，用尽量少的模型调用判定 EQUIVALENT / BROADER / DISJOINT 关系，并尽量多地取消待调用候选。

继承自历史设计的核心原则：

> 优先级只决定先问哪个 pair，不决定哪个关系可以写图。所有硬关系必须经过 Generator、必要的 Reviewer 和 SafeRelationEngine 约束门禁。

---

## 2. 背景

### 2.1 V2标签体系审查结论

审查对象为 33,339 行的树形文本，详见 `标签体系审查报告.md`。关键数字：

| 指标 | 数值 |
|---|---:|
| 节点总数 | 33,339 |
| 叶子节点 | 24,164 |
| 去重后标签名 | 29,353 |
| 兄弟重名 | 552 处 |
| 父子同名冗余嵌套 | 353 处 |
| 重复子树对（后代重叠 >50%） | 327 对 |
| 空 facet / 错挂 facet | 40 / 120 |
| 机翻残留与可疑命名 | 219（其中 81 个含 `+` 残留） |
| 出现 ≥2 次的标签名（需裁决） | 2,926 名 / 3,986 多余副本 |

核心判断：**树形格式零损坏、可直接程序化解析与修复；问题集中在内容层**，带明显的双源融合与机翻痕迹。

### 2.2 迁入引擎：Safe-Gated Scheduler（历史项目）

历史项目面向约 136.4 万 Label 的 Catalog、1,000 万～2,000 万候选 pair，解决"候选远多于预算时如何调度"。已实现并验证：

- `SafeRelationEngine`：Union-Find 等价组件、BROADER DAG + reachability、组件级 NONE、DISJOINT 子树传播、transitive reduction（assertion ledger 永不删除）；
- `ClosureGain` 收益估计 + `SingleGainScorer` 优先级（ExpectedGain / ExpectedCost）；
- 调用前候选取消（`INFERRED_BEFORE_MODEL`，当前为每 Wave 全量扫描）；
- Versioned Priority Queue、Frozen Wave（严格 barrier）、SQLite ledger、snapshot/restart（真实进程恢复已验证）;
- GPT-5.6 Generator（batch 300）+ GPT-5.5 Reviewer（batch 25，仅高风险：EQUIVALENT_TO / NONE_DISJOINT_SUBTREES）；
- 六分类概率模型 `aepgs-prob-v0001`（Group Holdout Log Loss 1.1206 vs 证据启发式 1.4654；任务边界版本化反馈学习，任务内冻结版本）。

尚未实现（历史设计 §19 状态矩阵）：`QueuePruneGain` / ExpectedSavedModelCost 入 Priority、`GraphDelta`、`PendingCandidateIndex`、增量候选取消与增量重评分、micro-wave 剪枝、千万候选持久化 Queue。

完整历史设计文档原文计划归档于 `docs/safe_gated_scheduler_历史设计文档.md`（见该占位文件说明）。

### 2.3 两者的契合点

V2 树本身就是大量**现成的结构知识**，清洗后可直接映射为引擎的关系契约：

| V2 树事实 | 关系契约映射 | 说明 |
|---|---|---|
| 父子边 | BROADER_THAN 断言种子 | 清洗后约 2.4 万+ 条父子边构成图骨架，闭包 / DISJOINT 传播可免费计算 |
| 重复子树、兄弟重名 | EQUIVALENT_TO 候选 | 合并后 m×n 等价 pair 放大，并传播与第三方的已有关系 |
| 跨类目同名（2,926 组） | EQUIVALENT / BROADER / NONE 裁决候选 | 最高价值的模型调用池 |
| 子树之间 | NONE / DISJOINT 候选 | DISJOINT 强证书可向整棵后代子树传播 NONE |

规模取决于所选 universe：若仅用 V2 树节点（3.3 万），Top-20 召回候选在几十万量级；若以已入库的完整词条表为 Catalog（1,498,781 条，与历史项目 136.4 万 Labels 同量级），候选 pair 将重新回到千万级，"千万候选持久化 Queue"不可长期后置。两种情形下，增量取消 / 重评分都是性能关键。

---

## 3. 关系契约（正式图）

```text
EQUIVALENT_TO           合并为等价组件，正式图不保留等价边
BROADER_THAN            方向恒为 更宽泛 → 更具体（类别—子类与类别—实例统一）
NONE                    两组件间不存在等价或任一方向 BROADER
NONE_DISJOINT_SUBTREES  正式结果仍为 NONE，附可向后代子树传播的强互斥证书
UNCERTAIN               未解决，不写硬关系，也不能当作 NONE
```

安全规则（继承，不可放宽）：

- 所有硬关系必须过 Generator →（高风险必过 Reviewer）→ SafeRelationEngine 约束门禁；
- EQUIVALENT_TO 与 NONE_DISJOINT_SUBTREES 为高风险，双模型一致才可提交；
- 等价合并前必须模拟冲突检查并支持完整回滚；
- 正式图只保留 transitive reduction 后的骨架，原始安全断言永久保留在 ledger；
- 候选取消必须由 `engine.infer()` 精确确认，反向索引只负责缩小范围，不能自行授权关系。

---

## 4. 当前项目资产

| 文件 | 内容 | 条数 |
|---|---|---:|
| `标签体系审查报告.md` | 审查总览、问题明细、修复优先级 | — |
| `标签审查_重复兄弟节点.csv` | 同父同名重复 | 552 |
| `标签审查_重复子树.csv` | 高重叠重复子树对 | 327 |
| `标签审查_父子同名.csv` | X/X 冗余嵌套 | 353 |
| `标签审查_空facet与错挂facet.csv` | facet 结构异常 | 160 |
| `标签审查_可疑命名.csv` | 机翻残留 / 可疑节点 | 219 |
| `标签审查_多名节点.csv` | 出现 ≥2 次的标签名及位置 | 2,926 |
| `data/raw/CustomPinyinDictionary_IBus.txt` | 完整中文词条拼音表（`词条 拼音`，2～29 字词，零重复、零格式异常，详见 `data/README.md`） | 1,498,781 |

注意：审查清单以**行号**定位节点，而任何修复都会使行号漂移。进入修复前必须先建立稳定节点 ID（见 §8 开放问题）。审查输入 `pasted-text.txt`（33,339 行树本身）尚未入库，**修复工作的前置条件是把原始树文本入库并冻结版本**。

---

## 5. 路线图

### 阶段 0 —— 冻结基线（纯机械，零模型调用）

1. 原始树文本入库并冻结版本；
2. 编写树解析器（缩进 → 稳定节点 ID / 路径 / 父子边）；
3. 用解析结果逐条复现 6 份审查清单，数字必须完全对得上；
4. 产出结构化快照（节点表 + 父子边表），作为后续一切 diff 的基线。

### 阶段 1 —— 机械修复（低风险，脚本批处理）

1. 删除 552 处兄弟重名的冗余副本；
2. 压平 353 处父子同名层；
3. 每步输出 diff 报告，并重新审查验证（对应计数应归零，其余计数不得恶化）。

### 阶段 2 —— 子树合并与错位迁移（半自动，清单驱动）

1. 按 `标签审查_重复子树.csv` 合并 327 对重复子树（从大户开始：可穿戴设备/衣服、劳动者、开花植物、水禽、信号、住处、桌子、航空器）；
2. 迁移典型错位：手臂下的武器树、坦克下的储罐（tank 一词多义）、食物下的摩托人力车、爆炸下的流行音乐、亲属树误译等；
3. 补 40 个空 facet 取值、把 58 个悬浮 facet 重新挂回实体节点；
4. 清洗 81 个 `+` 残留与形容词/量词/数字误作节点的命名。

### 阶段 3 —— 引擎迁移（按历史设计 §21 清单）

1. 迁移 `SafeRelationEngine` 及测试；把清洗后树的父子边作为 BROADER 断言载入，构建闭包 / reachability；
2. 回答三个迁移问题：
   - 目标图中哪些关系具有可证明的传递或传播规则？（树父子边 + 等价合并 + DISJOINT 传播）
   - 推导出的关系能命中多少真实 Pending Candidate？
   - 相同调用预算下，调度策略是否提高新增已知关系与调用剪枝？
3. 先做 replay：重复子树 / 兄弟重名清单即"缓存关系"，用它做 EQUIVALENT 裁决的离线回放，不调模型；
4. 再迁 SQLite ledger、Frozen Wave、snapshot/restart。

### 阶段 4 —— 模型裁决与调度（canary）

1. 候选池：2,926 组跨类目同名 + 子树间 Top-K 名称召回；
2. 概率基线先用 evidence-only，再评估在 V2 分布上重训（历史模型 `aepgs-prob-v0001` 的训练数据来自旧 Catalog 分布，不默认直接复用）；
3. control / treatment canary：按候选连通分量切分，evidence-only vs 概率 + 混合 Gain（ClosureGain + λ×QueuePruneGain，λ 经 replay 选择）；
4. 相同预算对比：新增唯一已知关系、关系放大、物理候选剪枝率、Reviewer 比例、token / 延迟成本；安全指标必须全 0。

### 阶段 5 —— 扩展

- 候选召回扩到全树，视候选规模决定是否实现千万级增量持久化 Queue（当前规模预计不需要）。

---

## 6. 验收与安全指标（继承）

**安全指标恒为 0**：cycle、dangling、NONE/BROADER 冲突、DISJOINT/BROADER 冲突、等价组件内部非等价断言、未经 Reviewer 的高风险 hard commit、response.model 静默降级、snapshot mismatch、重复 response usage。

**收益指标**：

```text
RelationAmplification          = 闭包后新增唯一已知 pair / 直接安全关系
KnownPairsPerGeneratorPair     = 新增唯一已知 pair / Generator 处理 pair
PhysicalCandidatePruningRate   = INFERRED_BEFORE_MODEL / (INFERRED_BEFORE_MODEL + 实际调用候选)
```

注意区分：关系闭包放大 ≠ 物理调用剪枝，只有命中 Pending Queue 的推导才节省模型调用，两者必须分开监控。

**Queue 正确性**：graph/model version stale 不得使用旧 Priority；heap 旧 generation 不得再出队；Frozen Wave stale commit 必须失败；相同输入与版本产生稳定顺序。

---

## 7. 代码迁移映射（规划）

| 上一项目 | 本项目（拟） | 说明 |
|---|---|---|
| `src/wlo_pipeline/safe_gated_scheduler.py` | `src/demiwtg/safe_gated_scheduler.py` | 引擎核心，连同测试一起迁 |
| `src/wlo_pipeline/safe_gated_live_runner.py` | `src/demiwtg/safe_gated_live_runner.py` | SQLite ledger / Frozen Wave / 单写提交 |
| `src/wlo_pipeline/aepgs_active_learning.py` | `src/demiwtg/aepgs_active_learning.py` | 概率 provider、版本 / SHA / schema 校验 |
| `src/wlo_pipeline/aepgs_active_training.py` | `src/demiwtg/aepgs_active_training.py` | 数据注册、component 分组、发布门禁 |
| `src/wlo_pipeline/aepgs_runtime_monitoring.py` | `src/demiwtg/aepgs_runtime_monitoring.py` | prequential 质量指标与剪枝率 |
| `tests/test_safe_gated_*.py`、`tests/test_aepgs_*.py` | `tests/` | 与代码同迁，先跑绿再改 |
| `scripts/*` | `scripts/` | 适配 V2 候选源后迁移 |

迁移顺序遵循历史设计 §21：契约 → 引擎+测试 → Catalog/evidence 适配 → replay → ledger/Frozen Wave → 概率模型 → canary。运行数据（live run 输出、旧模型 registry）默认不迁。

---

## 8. 开放问题（待决策）

1. **原始树入库形式**：raw 文本直接入库，还是解析后的 JSON 快照入库？版本号方案？
2. **稳定节点 ID**：行号会随修复漂移，需要稳定 ID（路径哈希 / 首次分配后永久保留），审查 CSV 与引擎断言都依赖它；
3. **facet 的归属**：facet（维度/取值）节点进入正式关系图，还是作为独立属性层单独管理？
4. **概率模型起点**：evidence-only 基线起步（推荐）还是直接迁移 `aepgs-prob-v0001` 再重训？
5. **产出目录命名**：建议 `output/demiwtg_v2_taxonomy_v1/`。
6. **词条表与旧 Catalog 的关系**：149.8 万条完整词条与历史项目 136.4 万 Labels 是否同源？过滤规则是什么？
7. **大文件入库策略**：40MB 词条表按原文提交 git、提交 gzip 压缩版（约 13.4MB）还是外部存放？
