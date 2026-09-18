# 总评

**结论：当前版本不宜直接上线，需做结构性修改。**

最大问题不是 facet 词表，而是**“隐含校验点是否由 `gen_prompt` 唯一、合理地蕴含”没有被验证**。现在的规则允许出题器从原图或实体知识中挑一个真实属性，却不保证生成模型仅凭 prompt 就应当画出它。这样测到的可能不是知识能力，而是出题器的任意期待。

文末《大魔神》示例恰好暴露了这个问题：`gen_prompt` 没有明确点名《大魔神》，只描述“1960 年代日本特摄中的石像魔神”，却要求双目发光、武士盔甲等特定属性。即使这些属性本身真实，也未必由该 prompt 唯一推出。**该示例按你们自己的规则应判无效。**

---

# A. 出题流程与红线

## A1. 三步流程是否足够

**结论：修改。现有三步缺少“实体确认、知识验证、蕴含性审查、视觉可判性审查”四个关键环节。**

### 理由

#### 1. “图片是唯一事实依据”与“展开图外知识”存在直接矛盾

图片只能证明：

- 该图片里出现了什么；
- 该实例的可见属性是什么。

图片不能单独证明：

- 某建筑的规范形制；
- 某物种的典型特征；
- 某历史时期的服饰惯例；
- 某角色的 canonical 设定；
- 某文化符号的固定含义。

因此应区分：

- `sample_evidence`：原图支持的样本事实；
- `external_knowledge`：外部资料支持的世界知识；
- `prompt_entailment`：为什么 prompt 会触发该知识。

否则“唯一事实依据”会迫使模型要么不使用图外知识，要么偷偷把自身记忆当事实来源。

#### 2. `solid/likely` 是出题 LLM 的主观置信度，不是可信度分级

LLM 很可能把流行误传标为 `solid`。可靠性至少需要以下维度：

- 来源类型：官方、一手资料、权威二手资料、普通网页、模型记忆；
- 来源数量及是否独立；
- 是否为典型特征、必要特征或仅常见特征；
- 是否存在时代、地区、亚型、版本差异；
- 是否可在最终图像中稳定观察。

#### 3. 缺少“prompt—check 蕴含性”审核

必须检查：给定 prompt 和公认知识后，该校验点是否应当成立，而不是“有可能成立”。

建议采用反例测试：

> 能否构造一幅完全符合 `gen_prompt`、同时不满足该校验点的合理图像？若能，该校验点无效。

例如：

- “无风高山湖泊”不必然意味着“完美镜面倒影”，还取决于视角、水面扰动、雾气、光照等；
- “1960 年代日本特摄石像魔神”不必然是《大魔神》；
- “中世纪骑士”不必然穿某一具体国家、具体世纪的甲胄。

#### 4. 缺少弃题出口

第八节要求必须出指定题数，但红线又要求无可靠知识时弃题。对不适合的样本，出题模型最终只能被迫编造。

### 建议将流程改为六步

> 1. **样本与实体确认**：分别核验图片内容、`query_label` 与 taxonomy 是否一致；无法确认时输出弃题状态，不得强行出题。  
> 2. **样本证据审计**：仅记录原图可见事实，并区分“直接可见”与“基于实体识别推断”。  
> 3. **外部知识提案与核验**：列出候选知识点，标注来源、适用范围、版本差异和视觉可观察性；只有已核验知识可进入评分。  
> 4. **Prompt 构造**：明确主体或可唯一识别的概念，但不直接泄漏待测属性。  
> 5. **蕴含性与反例审查**：确认每个校验点由 prompt 与公认知识合理推出；若存在符合 prompt 但违反校验点的常见合理画法，则删除该校验点。  
> 6. **评分原子化审查**：检查校验点是否独立、可见、可分档、无重复计分，并生成评分锚点。

### 建议新增字段

```json
{
  "item_status": "accepted | rejected",
  "rejection_reason": null,
  "knowledge_audit": [
    {
      "claim_id": "k1",
      "claim": "...",
      "claim_type": "canonical | typical | causal | historical",
      "source_type": "primary | authoritative_secondary | other",
      "sources": ["..."],
      "scope_conditions": ["..."],
      "verified": true,
      "visually_observable": true
    }
  ],
  "entailment_audit": [
    {
      "check_id": "c1",
      "trigger_in_prompt": "...",
      "required_knowledge_ids": ["k1"],
      "counterexample_found": false,
      "justification": "..."
    }
  ]
}
```

---

## A2. 红线是否冲突，`needs_verification` 能否兜底

**结论：修改。`needs_verification` 当前不能兜底，且与“校验点只能建立在 solid 知识上”逻辑冲突。**

### 理由

当前规则同时规定：

- `likely` 用作答案关键点时设 `needs_verification: true`；
- `implicit_checks` 只能建立在 `solid` 知识上；
- 输出只有题目级布尔值 `needs_verification`。

问题是：

1. 如果 `likely` 不能进入校验点，最终题目的 `needs_verification` 理论上应始终为 `false`；
2. 如果允许 `likely` 进入校验点，那么它会污染正式分数；
3. 题目级布尔值无法指出是哪条知识、哪条 check 待验证；
4. 没有规定谁验证、如何验证、验证失败后怎么处理。

### 还存在三处覆盖漏洞

#### 漏洞一：真实但非必要的属性

“防幻觉”只管知识是否真实，却没管它是否是 prompt 所要求的。真实属性也可能成为不合理答案。

#### 漏洞二：多个合理版本

历史人物、神话角色、建筑修缮版本、影视角色造型都可能有多个合法表现。当前规则容易把一个版本当唯一答案。

#### 漏洞三：校验点在图中不可见

例如内部机械结构、看不见的因果状态、被遮挡的徽记，即使知识正确，也无法稳定判分。

### 建议改写

> `likely` 知识只能进入候选池，不得进入已发布题目的 `implicit_checks`。任何 `implicit_check` 必须引用至少一个已核验知识条目，并通过适用范围、视觉可观察性和 prompt 蕴含性审查。  
> `needs_verification` 改为知识条目级字段；正式发布对象中所有被评分条目的 `verified` 必须为 `true`。若无法完成验证，题目应输出 `item_status: rejected`，不得带风险上线。

建议新增三条红线：

> 6. **禁止任意期待**：校验点虽真实，但不能由 prompt 与公认知识合理推出的，视为泄漏的反面——“答案未授权”，必须删除。  
> 7. **允许多解**：若存在多个常见、事实正确的视觉版本，评分规则必须明确接受集合，不得把单一版本设为满分条件。  
> 8. **必须视觉可证**：校验点必须能从最终单张图像中直接观察，不得要求 judge 推断不可见状态。

---

# B. facet 词表

## B3. 四支柱裁剪是否合理

**结论：修改。裁剪范围基本合理，但裁剪理由和 Qwen-Image-Bench 的继承关系表述不准确。**

### 理由

- 删除大部分 Creative Generation：对“世界知识探针”是合理的；
- 删除 Safety：如果题库明确不含安全、违规内容，可以删除；
- 删除 Fairness：不能以“各模型无区分度”为理由。跨地区文化知识本身就可能存在代表性和刻板印象偏差；
- `causal_reasoning`、`counterfactual_coherence` 从创意逻辑中抽出来合理，但它们已经是你们自己的重组，不再是原始 QIB 词表。

Qwen-Image-Bench 官方结构是五个一级支柱、56 个细则，Fairness 和 Safety & Compliance 位于 Real-world Fidelity 下；其 judge 也依赖专业标注和训练，不只是把词表拿来使用。因此你们应称为“受 QIB 启发的适配词表”，避免暗示与 QIB 原始口径可直接比较。

### 具体改写

将标题改为：

> **T2I 生成赛道出题 Prompt（v3，采用受 Qwen-Image-Bench 启发、面向知识探针任务重新定义的 facet 体系）**

将裁剪说明改为：

> 本词表不宣称与 Qwen-Image-Bench 56 facets 等价。基于知识探针定位，本基准暂不计入 Safety & Compliance，并将部分 Creative Generation 中的逻辑能力重定义为知识推理维度。Fairness 不纳入单题得分，但应在题库级记录地域、文化和群体覆盖，以监控数据偏差。

还应考虑保留一个 `text_rendering` 或明确排除所有含文字生成要求的题。否则涉及品牌、路牌、历史海报时，OCR 能力会成为未建模混杂变量。

---

## B4. `subject_prominence` 的边界是否清晰

**结论：修改。概念有用，但当前措辞不足以让 judge 稳定区分它与 gate、`composition`、`size`。**

### 应明确四者边界

| 维度 | 应判内容 |
|---|---|
| gate | 必要主体不存在，或图像主题与 prompt 根本不一致 |
| `subject_prominence` | 主体存在，但是否承担 prompt 指定的视觉中心或叙事中心 |
| `size` | prompt 明确要求的绝对或相对尺寸是否满足 |
| `composition` | 全画面的视觉平衡、层次、留白和视觉引导是否合理 |

“主体大”不等于“主体突出”：

- 小人物可以通过高对比度、中心位置成为视觉中心；
- 巨大背景建筑可能尺寸正确，但不是叙事中心；
- prompt 未要求主导主体时，不能因其不突出而扣分。

### 建议评分锚点

> `subject_prominence` 仅在 prompt 明确要求“以某主体为中心、特写、主视觉、主体占主导”或题目语义明显以单一主体为核心时激活。  
> - 2：主体清晰可识别，并承担明确视觉或叙事中心；  
> - 1：主体存在且可识别，但被背景、配角、裁切或低对比度明显削弱；  
> - 0：主体虽可能局部出现，但无法承担题目要求的核心角色；若主体不可确认或主题整体跑偏，则由 gate 处理并将本维度记为 N/A，避免重复处罚。

### 不建议默认激活

`subject_prominence` 不应所有题默认激活。群像、信息图、全景地标、对比图和多主体关系题未必有单一主导主体。应改用一个独立的 gate 必要主体列表：

```json
{
  "gate_spec": {
    "required_subjects": ["..."],
    "theme_definition": "...",
    "partial_presence_is_gate_failure": false
  }
}
```

---

## B5. 选词纪律是否可执行

**结论：修改。当前纪律会诱导标签凑数和分数操纵。**

### 主要问题

#### 1. 强制跨两个 L1 支柱容易制造无关 facet

有些纯知识题实际只考：

- `landmark_identity`；
- `temporal_characteristics`；
- `cultural_elements`。

为了满足跨支柱，出题器可能硬加 `composition`、`resolution`，稀释知识信号。

#### 2. “频率均衡”不应由单题出题器负责

全局均衡需要调度器基于题库统计分配配额。单题 LLM 不知道完整题库分布，容易为了均衡给错误标签。

#### 3. 3~6 个 facet 仍可被用于抬分

出题器可以加入容易得分的 `resolution`、`edge_clarity`、`naturalness`，把难知识项平均掉。

#### 4. facet 与 check 缺少显式映射

目前只能人工猜测哪条 check 对应哪个 facet，无法做归因审计，也无法发现重复计分。

### 建议改写

> 1. 每个 `facet_tag` 必须至少关联一个 prompt 显式要求或一个有效 `implicit_check`，并通过 `facet_check_map` 明确映射。  
> 2. 不强制单题跨两个 L1；跨支柱覆盖由调用方在批次级通过配额控制。  
> 3. `resolution`、`noise`、`edge_clarity` 等模型输出基础质量维度不得仅为满足数量要求而激活；只有当它们对本题可判性或 prompt 风格要求有直接影响时才激活。  
> 4. 高度相关的 facet 不得对同一视觉事实重复计分。例如同一个甲胄结构不得同时作为 `objects`、`temporal_characteristics`、`cultural_elements` 三次独立奖励，除非三者有不同且明确的观察标准。  
> 5. `subject_prominence` 不默认激活；由题目语义和 `gate_spec` 决定。

建议把数量改为 **2~5 个核心 facet**，而不是为了丰富度强制 3~6 个。

---

# C. 判分契约

## C6. 双线权重、φ 映射与 gate 的组合

**结论：修改。当前组合存在重复计分、分母漂移和目的错配。**

### 1. 知识被重复计分

知识属性先进入 `implicit_checks`，又通过 `animals`、`objects`、`landmark_identity` 等进入通用线。这样单个知识错误可能被扣两次。

例如地标形制错误：

- 知识线对应 check 扣分；
- 通用线 `landmark_identity` 再扣分。

与此同时，构图或分辨率只扣一次。最终实际权重并非声明的 $0.5/0.5$。

### 2. 通用线分母随题目变化

有的题选 3 个 facet，有的选 6 个；有的全是困难维度，有的混入容易维度。即使简单平均，题间也不可直接比较。

### 3. 文档没有完整定义知识线公式

第七节只明确通用线使用 $\phi$，没有明确知识线的 $0/1/2$ 是否也经过 $\phi$。这会导致实现分叉。

### 4. $0\rightarrow0$、$1\rightarrow60$、$2\rightarrow100$ 可以保留，但要定义“1”

对原子事实：

- 完全错误；
- 部分正确；
- 完全正确。

这三档未必总成立。数量、主体是否存在等更适合二值。强迫 judge 给中间档会扩大主观性。

建议：

- 二值 check 只允许 $0$ 或 $2$；
- 可分档 check 必须逐题给出 `score_anchors`；
- 不允许 judge 自行理解什么叫“部分满足”。

### 推荐设计

首选：**不把所有能力压成单一总分**，同时报告：

- 知识事实分；
- 指令遵循分；
- 视觉质量分；
- gate 通过率；
- 各 facet 分。

若必须保留总分，建议：

$$
K=\sum_i w_i\phi(s_i)
$$

$$
G=\frac{1}{|F|}\sum_{f\in F}\phi(s_f)
$$

$$
S=0.7K+0.3G
$$

但需满足：

- Real-world Fidelity 类 facet 作为诊断标签，不再进入 $G$，避免与 $K$ 重复；
- $G$ 只包含非知识的 Alignment、Quality、Aesthetics；
- facet 集合由固定规则决定，不能由出题器自由添加容易项；
- 同时公开 $K$ 和 $G$，不只公开 $S$。

### gate 建议

30 分封顶可以保留，但必须增加明确契约：

> gate 仅在所有必要主体均不可确认，或图像主题与 prompt 的核心事件、实体或场景不一致时触发。局部属性错误、主体不够突出、构图差、风格错误不得触发 gate。

此外建议 gate 独立调用或至少独立输出证据，避免 judge 先看到低质量后滥用“主题跑偏”。

---

## C7. 出题方写 rubric、判分方执行是否有风险

**结论：修改。存在明显的自证循环和口径漂移风险。**

### 理由

同一个出题 LLM可能同时：

1. 误认实体；
2. 生成错误知识；
3. 把错误知识写进 check；
4. 再用自洽的 rubric 让 judge 扣分。

这不是传统意义上的利益冲突，而是**相关误差**：出题错误与 rubric 错误来自同一认知来源。

此外，自由文本 `check` 会导致：

- 不同题目的 $0/1/2$ 口径不一致；
- 一条 check 混合多个事实；
- judge 借助自身知识补写 rubric；
- judge 被“expected failure”暗示，产生确认偏差。

### 建议采用“提案—验证—判分”分离

> - 出题器：生成 prompt 和候选检查点；  
> - 知识验证器：独立核查事实、适用范围和 prompt 蕴含性；  
> - rubric 规范化器：把检查点拆成原子项并写分档锚点；  
> - judge：只根据图像、prompt 和已批准 rubric 判分，不查看 `expected_failure_modes`；  
> - 抽样人工复核：重点复核高权重、长尾知识和模型分歧大的题。

建议结构：

```json
{
  "implicit_checks": [
    {
      "check_id": "c1",
      "check": "...",
      "knowledge_ids": ["k1"],
      "facet_tags": ["temporal_characteristics"],
      "probe_dims": ["perception_knowledge_coupling"],
      "observable_evidence": "...",
      "score_anchors": {
        "0": "...",
        "1": "...",
        "2": "..."
      },
      "acceptable_variants": ["..."],
      "weight": 0.4
    }
  ]
}
```

至少应建立一批双人标注、专家审定的校准题，测 judge 的逐 facet 一致率，而不能默认“一次 VLM 调用”足够可靠。

---

# D. 区分度工程与输出格式

## D8. `expected_failure_modes` 与 `probe_dims` 能否支撑归因

**结论：修改。当前只能提供假设性解释，不能支撑可靠归因。**

### `expected_failure_modes` 的问题

它是出题前预测，不是评测后观察。若直接据此归因，容易变成：

> 先预测模型会画错盔甲，再从结果中寻找盔甲错误。

judge 不应看到该字段，否则会产生确认偏差。该字段更适合：

- 题目设计审计；
- 评测后与实际错误做对照；
- 不直接参与判分。

### `probe_dims` 的问题

当前词表混合了不同层级：

- 能力：`instruction_following`；
- 错误来源：`hallucination_resist`；
- 操作类型：`counting_quantification`；
- 输入模式：`perception_knowledge_coupling`；
- 单图不一定可定义的概念：`identity_consistency`。

尤其是：

- t2i 模型不看原样本图，因此“看得见但不认识”不适用于生成阶段；
- `identity_consistency` 若只有一张单图，没有跨帧、跨视角或多个实例，很难成立；
- `ocr_shortcut_resist` 只有在题目含可读文字且禁止仅靠文字标签冒充视觉结构时才适用；
- `hallucination_resist` 和 `fine_grained_attr` 经常重叠。

### 建议

1. 每条 check 单独映射 `probe_dims`，不要只做题目级多选；
2. 将“预期错误”和“实际错误”分开；
3. 评测后由 judge 输出证据化错误码；
4. 做归因时使用实际错误码，而不是预期失败模式。

建议新增：

```json
{
  "expected_failure_modes": [
    {
      "failure_id": "f1",
      "description": "...",
      "related_check_ids": ["c1"],
      "hypothesized_cause": "knowledge_recall"
    }
  ],
  "observed_error_schema": [
    "missing",
    "wrong_variant",
    "anachronism",
    "attribute_swap",
    "causal_inconsistency",
    "judge_uncertain"
  ]
}
```

并重命名部分 `probe_dims`：

- `perception_knowledge_coupling` → `entity_to_visual_knowledge_grounding`；
- `identity_consistency` → 仅在同图多实例或多视角任务中使用；
- `hallucination_resist` → `canonical_fact_fidelity`；
- 增加 `knowledge_retrieval`、`knowledge_application`、`variant_disambiguation`。

---

## D9. 难度分级是否可操作

**结论：修改。以检查点数量定义难度不可操作，且文档内部矛盾。**

### 明确矛盾

- 第六节 L1：1 个隐含知识校验点；
- 第七节：`implicit_checks` 必须 2~5 条。

二者不能同时成立。

### 数量不等于难度

三个高度相关的简单属性可能比一个反事实物理推理更容易。例如：

- “熊猫应为黑白、圆耳、眼周黑斑”是三个 check，但几乎是一个原型记忆；
- “镜中物体在反事实光源下应出现相应阴影与倒影变化”可能只有一个 check，却更难。

### 建议将难度拆成四轴

```json
{
  "difficulty_profile": {
    "knowledge_rarity": 1,
    "reasoning_depth": 2,
    "composition_load": 1,
    "visual_judgment_difficulty": 1
  },
  "difficulty": "L2"
}
```

定义建议：

- `knowledge_rarity`：大众常识、领域常识、受控长尾；
- `reasoning_depth`：直接召回、一步组合、多步因果或反事实；
- `composition_load`：需同时绑定的实体、属性和关系数量；
- `visual_judgment_difficulty`：特征是否小、易遮挡、易混淆。

最终 L1/L2/L3 应在试测后按经验通过率或项目反应理论校准，而不是只靠出题器预测。

### 占比建议

暂不固定 L3 为 40%。先做 pilot：

- 剔除通过率接近 0 或接近 1 的题；
- 保留能稳定拉开模型差异、judge 一致性高的题；
- 再决定正式题库比例。

初版更稳妥的目标是 L1/L2/L3 约为 25%/50%/25%，而不是未经校准就让 40% 都进入 L3。

---

# E. 整体

## E10. 弱模型最容易钻什么空子

**结论：修改。当前最大空子是“生成高质量的典型刻板印象图”，再利用 VLM judge 的宽松识别拿分。**

### 空子一：画原型，不做精确知识推理

题目从真实实体展开，但 prompt 常使用宽泛类别。弱模型只需生成：

- 一个像地标的建筑；
- 一个像古代的场景；
- 一个像特摄怪物的主体；
- 一个带文化符号的泛化画面。

VLM judge 容易因为整体相似而把细节判为存在。

### 空子二：利用自由文本 rubric 的语义宽松

例如要求“武士风格盔甲”，弱模型画出任何东方盔甲，judge 可能给 1 或 2。检查点越抽象，越容易被视觉近似骗过。

### 空子三：用高画质冲淡知识错误

如果通用线包含 `resolution`、`composition`、`lighting_atmosphere` 等容易项，一张漂亮但事实错误的图仍可获得较高总分。

### 空子四：通过文字标签伪装知识

模型可以在图中写出地标名、物种名或科学术语，却不画对视觉结构。judge 可能被文字诱导。这正是 `ocr_shortcut_resist` 应解决的地方，但当前没有统一规则。

建议新增红线：

> 图中文字、标题、标签或水印不得替代校验点要求的视觉证据；除非该题明确考察文字生成，否则 judge 应忽略文字对实体身份和结构正确性的声明。

### 空子五：针对单一 judge 优化

一次本地 VLM 调用会形成固定偏好。模型可以通过：

- 大主体；
- 高对比度；
- 标志性色块；
- 文字标签；
- 常见视觉符号；

让 judge 产生“似乎满足”的判断。应至少用人工样本校准，并对关键题采用双 judge 或不确定性复核。

---

## E11. 与现有基准相比的差异化价值和弱点

**结论：修改定位表述。相对 GenEval 和 T2I-CompBench 有明显差异，但相对近年的知识型 T2I 基准，创新性不能只表述为“隐含世界知识检查表”。**

### 与主要基准的比较

| 基准 | 核心重点 | 你们的差异 |
|---|---|---|
| GenEval | 物体存在、数量、颜色、位置、属性绑定，偏可检测的显式组合 | 你们考察未在 prompt 中展开的实体知识、文化知识和物理常识 |
| T2I-CompBench | 属性绑定、空间/非空间关系、复杂组合 | 你们更关注知识召回与知识驱动推理，而非纯组合遵循 |
| Qwen-Image-Bench | Quality、Aesthetics、Alignment、Real-world Fidelity、Creative Generation 的创作者导向综合评测 | 你们是样本锚定、单题 checklist 式知识诊断，目标更窄、更强调失败归因 |
| WISE | 世界知识语义评测 | 与你们目标高度接近；你们需要靠样本锚定、证据审计和细粒度诊断形成差异 |
| WorldGenBench | 世界知识与隐含推理，使用知识 checklist | 与你们的“隐含校验点”机制非常接近 |
| T2I-FactualBench | 知识密集概念的事实性及多层组合 | 你们需要证明自动从真实样本出题比人工概念题更有覆盖价值 |
| FAGER | 基于参考图和外部事实构造事实 rubric，再由 VLM 评估 | 与你们“原图锚定 + checklist”尤其接近，是必须正面对比的方案 |

### 真正可成立的差异化价值

1. **从自有真实图片标签树自动扩展题库**，而不是固定人工 prompt 集；
2. **同一实体可生成多个不同场景的知识探针**；
3. **知识 check、通用 facet、失败模式和能力归因在同一数据结构中联结**；
4. **可服务统一模型三赛道的横向归因**，例如同一实体在理解、生成、编辑中的表现比较；
5. 如果落实独立验证，可形成“样本证据—外部知识—prompt 触发—视觉检查点”的完整可追溯链。

### 潜在弱点

1. **自动出题带来的事实污染和 arbitrary expectation**；
2. **原图并非 canonical reference**，可能只是特殊角度、特殊版本或异常实例；
3. **本地 VLM judge 对细粒度知识未必比被测生成模型更可靠**；
4. **动态生成题导致版本不可复现**，需要冻结 prompt、rubric、来源和出题模型版本；
5. **题库文化与标签树分布决定知识覆盖**，容易形成地域和流行文化偏置；
6. **随机生成方差未定义**：每个 prompt 生成几张、如何固定 seed、取均值还是最佳值，会明显影响排名；
7. **知识记忆与推理未真正解耦**：画对可能来自训练集复现，未必是在线推理；
8. **与 WorldGenBench、WISE、T2I-FactualBench、FAGER 的重叠较高**，应避免把“隐含知识 checklist”本身当作唯一创新。

建议在项目定位中明确：

> 本基准的创新重点不是首次评测世界知识，而是从真实多模态样本及标签树规模化构造可追溯、可验证、可归因的动态知识探针，并用于统一理解—生成—编辑模型的跨任务短板分析。

参考：

- [GenEval](https://arxiv.org/abs/2310.11513)
- [T2I-CompBench](https://proceedings.neurips.cc/paper_files/paper/2023/file/f8ad010cdd9143dbb0e9308c093aff24-Paper-Datasets_and_Benchmarks.pdf)
- [Qwen-Image-Bench](https://github.com/QwenLM/Qwen-Image-Bench)
- [WISE](https://arxiv.org/abs/2503.07265)
- [WorldGenBench](https://arxiv.org/html/2505.01490)
- [T2I-FactualBench](https://aclanthology.org/2025.acl-long.1334/)
- [FAGER](https://arxiv.org/html/2605.19111)

---

# 对现有示例的直接判定

**结论：废除并重写。**

现有《大魔神》示例至少有四个问题：

1. `gen_prompt` 没有明确点名《大魔神》，但 check 要求特定角色属性；
2. “特摄魔神视觉惯例”不足以推出“双目发光”；
3. “东方元素”过宽，不足以稳定判 $0/1/2$；
4. `material_texture` 和 `objects` 可能对“石像质感”重复计分。

如果希望考《大魔神》的 canonical 视觉知识，prompt 至少应明确实体，例如：

> 以日本特摄作品《大魔神》中苏醒后的魔神为主体，创作一张具有 1960 年代日本电影摄影质感的城镇灾难场景：魔神矗立于传统建筑之间，人群正在逃离，低照度、压抑氛围、写实微缩模型特摄风格。不要加入现代车辆或现代高楼。

然后只保留经过资料核验、属于该角色稳定 canonical 设定、且在目标视角下可见的 check。若某属性只出现在部分形态、部分镜头或特定海报中，应列入 `acceptable_variants`，不能设为唯一满分答案。

---

# Top 5 问题清单

## 1. 严重：缺少“prompt 蕴含性”约束

当前允许把真实但未被 prompt 授权的属性当答案，导致测量对象从“世界知识”变成“猜出题人想法”。这是上线前必须修复的问题。

## 2. 严重：知识没有外部来源与独立验证闭环

`solid/likely` 只是 LLM 自报置信度；`needs_verification` 又与正式 check 的 solid 要求冲突。必须增加 claim 级来源、适用范围、验证状态和弃题机制。

## 3. 严重：知识线与 Real-world Fidelity facet 重复计分

同一事实可能同时影响知识线和通用线，实际权重不可解释。应让知识事实只进入知识线，Real-world Fidelity facet 主要用于诊断映射。

## 4. 高：judge 契约不完整且缺少稳定性验证

知识线是否经过 $\phi$、二值项如何使用中间分、gate 如何判“跑偏”、不同 facet 的 $0/1/2$ 锚点均未完全定义。一次 VLM 调用不足以自动获得可靠性。

## 5. 高：难度和归因体系目前不可校准

L1 的一条 check 与全局 2~5 条要求冲突；check 数量不能代表难度；`probe_dims` 混合能力、错误类型和任务形式。应改为多轴难度、check 级映射和评测后实际错误码。

**优先修复顺序应为：蕴含性审查 → 知识验证与弃题 → 评分去重 → judge 锚点与校准 → 难度及归因重构。**