<!-- V5 草案 · 由 openrouter/anthropic/claude-fable-5 整合三份二轮评审产出 · 2026-08-26 -->
<!-- 输入包 data/v5_synthesis_request.md · finish_reason=stop · tokens: prompt=28831 completion=26431 · 耗时 370s -->
<!-- 未经用户裁定，未接入 eval_synthesize.py（其仍读 v4 synthesize_prompt_gen.md） -->

# 修改说明（逐项取舍）

## 一、总体判断

三份评审在以下三点上完全一致，均属阻断级，全部采纳：①示例 checks 2/3/4 抄写 prompt 显式约束、泄题且把 Alignment 伪装成知识探针——示例整体废除重写；②`verified=false` 条目的判分契约断裂——V5 直接禁止未核验条目进入 `implicit_checks`；③"词面零重叠"防泄漏规则错误——改为"答案属性级防泄漏"，实体名与场景基础名词放行。

## 二、逐项取舍

### 采纳（阻断/高危）

| # | 来源 | 处置 |
|---|---|---|
| 1 | fable A1 / gpt#6 / gemini#1：示例泄题、伪知识点 | 示例整体重写：prompt 明写"东京塔"（解决触发链），4 条 check 全部改为真隐含知识（塔形制 / 皮套工艺 / 年代车型 / 年代天际线），并给出 schema 完整实例（采纳 fable A4"示例必须完整"） |
| 2 | fable B2 / gpt#2 / gemini#2：verified=false 语义未定义 | 三家方案取最干净者：`implicit_checks` 只允许 solid 且 verified=true 条目；likely 知识写入 `unverified_observations` 诊断字段，不设 weight、不计分。gemini 的"动态重归一化"不再需要（无 false 条目可剔），其"全 likely 则废题"精神并入拒题码 |
| 3 | gpt#3 / gemini#6：词面零重叠规则错误 | 红线 1 重写为答案属性级：实体名、类别名、反事实前提、场景基础名词不算泄漏；被测属性、结论性状态、评分阈值不得明写或近义提示。删除"石像→石质"僵尸引用（fable C4） |
| 4 | fable B1 / gpt#8：红线 2 vs L3"多步因果"矛盾；红线 2 但书与红线 7 引用错位 | 合并为一条：反事实前提须 prompt 明示，演绎规则本身必须 solid（废除红线 7 的 solid 豁免，采 gpt 立场）；演绎链上限 2 步且逐步写入 knowledge；≥3 步拒题。L3 定义同步收缩 |
| 5 | fable A2 / gpt#15 / gemini#5：gate 越界承载知识判定 | gate 只收 prompt 显式主体，且用**类别级名称**（"塔状地标建筑"而非"东京塔"），身份正确性由 critical check 判；背景知识锚点禁入 gate。`gate.reason` 放宽至 ≤50 字 |
| 6 | gpt#16：熔断边界跳变 | 新增 `critical` 标记（每题恰 1 条，实体身份/核心知识），critical 得 0 → 封顶 20；保留 <40 熔断，注明阈值待开发集校准 |
| 7 | gpt#11：缺拒题出口 | 新增 reject schema（status + reject_code + reason），强制流程各弃题点均落到 reject 输出 |
| 8 | gpt#20：judge 输入白名单 | 新增第十节：明确 judge 可见/禁见字段；原图不进 judge 输入；反事实题演绎链（knowledge 字段）纳入白名单（judge 判反事实必需） |
| 9 | gpt#12 / fable C1：跨支柱重复计分 + 通用线维度退化 | facet 拆为**计分组/诊断组**两字段：RWF 全部入诊断组；任一通用 facet 与某 check 共享同一视觉证据 → 降诊断；计分 facet 硬性 ≥3 |
| 10 | fable A3 / gemini#3：示例把 `real_world_scene` 数成 RWF | 新示例配比核验按词表正确归类；并注明配比应由程序侧硬校验兜底 |
| 11 | gemini#4：反事实题与 `physical_logic` 死锁 | `physical_logic` 增加反事实豁免：按 prompt 设定的物理系统内部自洽性判 |
| 12 | gpt#1：图片"唯一事实依据"与图外知识矛盾 | 重写第二节：image = 身份核验与可见事实第一手依据；图外知识须为 solid 公认知识。`evidence_audit` 拆分 visible_facts / canonical_features / entity_match（mismatch → reject） |

### 采纳（中危/修补）

| # | 来源 | 处置 |
|---|---|---|
| 13 | fable B3 / gpt#18：acceptable_variants 自相矛盾 | 必填、允许 `[]`；仅列真实影响判分的合法变体，禁编造；变体不得与 prompt 显式约束冲突（fable A4） |
| 14 | fable B4 / gpt#15：subject_prominence 死代码 | 删除条件激活规则，作普通 facet 按选取规则使用 |
| 15 | fable C2 / gpt#19：weight 枚举不闭合 | 写死枚举表，删"等" |
| 16 | fable C3 / gpt#7：负向约束边界 | 禁开放世界否定；允许封闭集/零数量约束（须走 `quantity` 或专门 rubric，不得伪装知识点） |
| 17 | fable C5 / gpt#9：L3 40% 过高、强行反事实化 | ~~占比改 L1 20 / L2 55 / L3 25~~ **用户裁定（2026-08-26）：回调为 L1 20 / L2 40 / L3 40**；明确"配额不得覆盖样本适配性，不适配则回退难度或拒题"；反事实题 gate 以 prompt 显式主体为准、判分不参照原图 |
| 18 | gpt#17：rubric 互斥/穷尽/不可见 | 增补：三档互斥穷尽、不得新增判分条件；关键证据不可见默认 0 档、知识线无 N/A；新增 `visibility_requirement` 字段；禁止绝对尺寸数值作判据（"333m"问题，fable A4） |
| 19 | gpt#5：反例测试对象不清 | 明确：反例仅指"完整语义符合 prompt 但会被 rubric 误扣的合法解释"；知识性错误画面不算反例；合法反例应入 acceptable_variants 或删 check |
| 20 | gpt#10：强制 2~4 条诱发填充 | 保持 2~4 但采方案 A：加独立性红线（非同义、不共享同一视觉证据）+ 拒题码 NO_INDEPENDENT_CHECKS |
| 21 | gpt#14：纯审美题后门 | 删除"纯审美/构图题"配比示例；本赛道全部题为知识型题 |
| 22 | fable C4：元信息混入 | V5 全文剥离版本沿革与评审元讨论 |
| 23 | fable C6：N/A 分母 | 补一句"N/A 不计入分母；计分 facet 全 N/A 时通用线按 60 计并标记异常" |
| 24 | gpt#21：knowledge_dim 单标签 | 增 `knowledge_dim_secondary` 可选数组 |
| 25 | gpt#19 部分：qid 递增、字数定义、失败模式条数 | qid 按 `-t2i-1/-t2i-2` 递增；长度按中文字符计；示例失败模式修正为 3 条 |

### 部分采纳 / 不采纳（附理由）

| # | 来源 | 取舍与理由 |
|---|---|---|
| 26 | gpt#2 强版：外部核验来源 `source_type/source_id` | **不采纳强版**。出题 LLM 无检索通道，强制填来源只会诱导编造伪引用，比自报 solid 更危险。改为：checks 仅收 solid 公认知识 + 流程侧人工抽验（写入 prompt 外的配套流程），并在 prompt 中声明"verified=true 是出题方承诺，接受抽验追责" |
| 27 | gpt#13：facet 全局/题级 rubric | **部分采纳**。通用线 0/1/2 锚点属评测侧全局文档，全文展开会使 prompt 长度失控且题级自写 rubric 反而加剧题间不可比；仅采纳"facet 须由 prompt 显式要求触发，禁凑数"规则 |
| 28 | gpt#4：trigger_audit 完全结构化 | **部分采纳**。保留自由文本字段但强制四要素（prompt 线索→所需先验→推理步数→pass/fail），完全 JSON 化收益低、输出负担高 |
| 29 | gpt#9：`reasoning_profile` 新字段 | **不采纳字段**，其难度定义实质已并入第六节判定要点。控制 schema 复杂度 |
| 30 | gpt#15：gate_spec 结构化 role/absence_triggers_gate | **不采纳结构改造**。"仅收 prompt 显式主体（类别级）+ 辅助元素缺失走 Alignment"两条文字规则已覆盖其语义，保持 schema 简单 |
| 31 | gpt#19：发布正式 JSON Schema、additionalProperties | **不采纳入 prompt**。属工程配套物，应在管线侧实现校验；prompt 内写死可枚举项即可 |
| 32 | gpt#22：facet 词表重构（anatomical_fidelity 归属等） | **暂缓**。动词表破坏与既有标注的兼容性；"证据重叠即降诊断"规则已兜住其主要风险 |
| 33 | gpt#10 方案 B（1~4 条 check） | **不采纳**。单 check 题知识线方差过大，保持 2~4 + 拒题出口 |
| 34 | gemini#3 建议的示例 tag `character_likeness` | **不直接采用**（新示例无知名角色），采纳其纠正归类的精神 |

---

