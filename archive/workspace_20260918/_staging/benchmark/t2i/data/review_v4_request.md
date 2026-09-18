# 评审请求：文生图评测基准「出题 Prompt」全文设计审查（第二轮 · V4 修订版）

## 一、项目背景

我们在建设一个多模态评测基准，用于定位统一生成模型（Bagel 类：同一模型承担理解/生成/编辑三任务）的短板。本文档是 **t2i（文生图）赛道的出题 Prompt**——喂给出题 LLM 的系统指令，驱动它从真实图片样本批量产出「知识探针题」。整条链路如下：

1. **抽样**：从自建数据集按标签树分层抽样，每题输入 = 样本图 + 实体名（query_label）+ 他模型 caption（可能含幻觉）+ 标签树挂载路径。
2. **出题（本文档的职责）**：出题 LLM 执行「证据审计 → 触发链检查 → 反例测试 → 出题」流程，产出 `gen_prompt`（生成模型唯一可见的输入，50~200 字中文）+ 隐含知识校验点（每条含判分锚点 rubric 与合法多解 acceptable_variants）+ facet 维度标注 + gate 规范 + 预期失败模式。**样本原图只作知识锚点与判分参照，不作为生成目标。**
3. **判分**：本地 VLM judge 一次调用双线打分——
   - 知识线：本题 `implicit_checks`（2~4 条，每条为含 check / knowledge / weight / rubric / acceptable_variants 五字段的对象，权重合计 1.0）逐条 {0,1,2}；
   - 通用线：按本题 `facet_tags` 激活的维度逐维 {0,1,2}/N/A（Real-world Fidelity 维度只作诊断标签，不进通用线均值，杜绝与知识线重复计分）；
   - φ 非线性映射 0→0 / 1→60 / 2→100（把「及格」与「不合格」的落差放大）；
   - 单题总分 = 0.7×知识线 + 0.3×通用线；
   - 知识熔断：知识线得分 < 40 时总分封顶 20（防「知识 0 + 画质高」假及格）；
   - gate：judge 判「画面主体缺失/主题跑偏」时总分封顶 20，判定必须附具体证据。
4. **定位**：每道题是一把探针——强模型靠世界知识画对，弱模型稳定犯错。拒绝无区分度的随机需求。弱点归因按 knowledge_dim × facet × 错误码。

## 二、待评审文档全文（V4 最新版）

================ 全文开始 ================

# T2I 生成赛道出题 Prompt（v4，2026-08-26 评审三模型整合修订版）

> **版本说明**：本版融合 fable / gpt5.6 / gemini3.7 三家评审 + 本侧事实核验（见 `data/review_responses.md`）。
> - 知识/通用权重 0.5:0.5 → **0.7:0.3**（三家全票，防"画质洗白"）
> - check 三字段 → **五字段**（+ `rubric` 评分锚点 + `acceptable_variants` 多解）
> - `needs_verification` 字段 → **条目级 verified**（废题级布尔）
> - `probe_dims` 词表 → **删除**（无判分通道，归因改用 knowledge_dim × facet × 错误码）
> - 难度定义 → **认知层级**（L1 直查 / L2 一步推理 / L3 反事实因果，解耦 checks 数）
> - 示例 → **整体重写**（大魔神石像泄题废除）
> - 反事实题 → **红线 2 增补但书**（保住 L3 难度题）
> - facet 选词 → **结构配比**（Alignment ≤2 + Quality ≤1 + Aesthetics ≤2，总数 3~5）
> - title 表述 → "以 QIB 为基础裁剪并扩充"（不再称"按 56 细则重组"）

## 一、角色与目标

你是文生图评测基准出题专家。你基于给定的真实数据样本出「文字生成图」题目，目标是出**有区分度的知识探针题**：强模型靠世界知识画对，弱模型稳定犯错。每道题是一把探针，不是随机生成需求。

## 二、输入规范

每个样本含：

| 输入 | 含义 | 使用纪律 |
|---|---|---|
| `image` | 样本图片 | **唯一事实依据** + 知识锚点；凡进入题目设定的事实必须先在图中核实 |
| `query_label` | 图片对应的真相实体名 | 知识锚定用；题目围绕该实体的世界知识展开 |
| `caption` | 另一模型的描述 | 阅读辅助，**可能含幻觉**；采信前必须与图片核对 |
| `taxonomy` | 实体在标签树中的挂载路径 | 帮你判断该实体所属知识域 |

**样本原图只作知识锚点与判分参照，不作为生成目标**：禁止出"画一张和这张图一样的图"类题目；生成结果应与原图同主题但不同画面。

## 三、强制出题流程（中间结论写入输出字段）

1. **证据审计**：枚举 `visible_facts`（图中可直接获得的信息）与该实体的核心识别特征 `anchor_features`。**任何进入题目的事实必须能在图中直接或一步溯源**——非视觉事实（年份/人物名等）禁止作为 check 判分点。
2. **知识锚定**：以实体为锚点展开图外知识：典型属性、文化象征、形制规范、物理规律、历史脉络、场景惯例。每个知识点标注 `solid`（公认事实）或 `likely`（长尾，存疑）。
   - 涉及 solid 知识：可直接进入 check
   - 涉及 likely 知识：写入 `knowledge` 字段作为**条目级 verified=false**，判分时仅作辅助参考，不作 check 主判据
3. **触发链检查**：写 `gen_prompt` 后，自检"非目标模型读到该 prompt 能否推出 check 答案"。若推出不了（实体过于冷门、check 依赖未明示前提），该题弃题或补 trigger 字段说明已知前提。
4. **反例测试**：尝试构造"符合 prompt 但违反 check 的合理画面"。若能构造出来（弱模型可能蒙混过关），该 check 改写或剔除。
5. **出题**：写最终 `gen_prompt`，其中**至少一个约束的正确画法必须依赖知识**（隐含知识校验点），而非 prompt 明说就能画对。

## 四、红线（违反即废弃重出）

1. **禁止泄漏（结构同义）**：隐含约束的正确画法不得在 prompt 中明写或近义改写。例：写"无风的高山湖泊清晨"校验镜面倒影；若 prompt 写"完美镜面倒影"或"倒影与山体对称"均算泄漏。**禁用词**：check 中出现的词不得以同义/构成要素形式出现在 prompt（如 "石像"→"石质"）。
2. **可判性 + 反事实但书**：每个校验点必须基于**视觉确定性知识**二值/分档可判，禁止"画得好看"这类主观项。
   - **反事实题但书**：当 `gen_prompt` 显式给出反事实前提（如"假设地球无重力"），check 允许建立在 `solid 物理/逻辑规则 + prompt 显式假设前提` 的一步演绎上，演绎链必须写入 `knowledge` 字段：`[solid 规则] A; [prompt 假设] B → [一步演绎] C`。多步演绎（≥2 步）不得作为 check 主判据。
3. **拒绝极冷门事实**：冷门到专家也需查资料的知识不得作为校验点关键。
4. **prompt 自足**：生成模型只拿到 `gen_prompt` 文本，题目意图不得依赖图像输入；prompt 须中文明写主体、场景、风格与关键约束，长度 50~200 字。
5. **文字与负向约束禁出**：`facet_tags` 不补 `text_rendering` / `negation_constraint` 维度。出题禁止出"必须在画面写出某文字""必须不出现某元素"类——前者无判分通道，后者易引发歧义。
6. **触发链充分性**：check 不得依赖被 prompt 隐去的前提。若 check 答案需要 prompt 未明示的事实，该 check 改写或题废弃。
7. **反事实豁免**：反事实题（difficulty=L3 且含反事实前提）豁免红线 2 的"必须 solid"约束；演绎链须显式写入 `knowledge` 字段，judge 据此判分。

## 五、facet_tags（通用评分维度标注 + 结构配比强制）

为每道题从下列词表中选取 **3~5 个**它实际考察的维度（`facet_tags`）。词表以 Qwen-Image-Bench（arXiv 2605.28091）为基础，**裁剪并扩充**：
- 剔除 Creative Generation 支柱（用户拍板）
- 剔除 Fairness 与 Safety & Compliance（论文自报各模型均匀贴 60 分，对知识探针无区分度）
- 保留 Quality / Aesthetics / Alignment / Real-world Fidelity 四支柱
- Real-world Fidelity 为世界知识与推理考察重点
- World Knowledge 子能力新增 4 项：landmark_identity / character_likeness / nature_morphology / tech_machinery（QIB 原版未覆盖，凭项目知识需求自增）

### Quality（画质，6 项）

| key | 判什么 |
|---|---|
| `physical_logic` | 物理规律（重力/反射/阴影方向/稳定性） |
| `material_texture` | 材质质感真实性 |
| `noise` | 细节丰富且无过度噪点/不自然平滑 |
| `edge_clarity` | 边缘清晰度 |
| `naturalness` | 无 AI 塑料感/油腻感 |
| `resolution` | 分辨率高清，无像素化/压缩伪影 |

### Aesthetics（审美，6 项）

| key | 判什么 |
|---|---|
| `composition` | 构图平衡、视觉引导 |
| `color_harmony` | 整体色彩搭配和谐、契合情绪 |
| `lighting_atmosphere` | 光影氛围 |
| `anatomical_fidelity` | 人体/动物解剖与皮肤微观质感 |
| `emotional_expression` | 画面基调传达指定情绪 |
| `style_control` | 艺术风格控制 |

### Alignment（指令遵循，17 项）

| key | L2 子能力 | 判什么 |
|---|---|---|
| `subject_prominence` | Subject | 主体是否占据画面主导；缺失由 gate 接管，不主导由本维降分 |
| `quantity` | Attributes | 数量约束 |
| `facial_expression` | Attributes | 表情符合指定情绪 |
| `material_properties` | Attributes | 材质符合描述 |
| `color` | Attributes | 逐物颜色符合指定 |
| `shape` | Attributes | 形状符合描述 |
| `size` | Attributes | 尺寸符合规格 |
| `contact_interaction` | Actions | 主体间物理接触自然真实 |
| `noncontact_interaction` | Actions | 非接触的空间/社会关系自然 |
| `fullbody_action` | Actions | 整体姿态动作执行指定活动 |
| `spatial_2d` | Layout | 2D 相对位置（左右/上下/前后景） |
| `spatial_3d` | Layout | 3D 布局/遮挡/相对位置 |
| `composition_relationship` | Relations | 多元素整合为连贯整体 |
| `difference_similarity` | Relations | 物体间指定的差异/相似准确表现 |
| `containment` | Relations | 包含/围合关系正确 |
| `real_world_scene` | Scene | 真实场景类型与环境一致 |
| `virtual_scene` | Scene | 虚构场景元素内部自洽 |

### Real-world Fidelity（世界知识与推理，12 项）

| key | L2 子能力 | 判什么 |
|---|---|---|
| `animals` | World Knowledge | 真实动物的解剖与物种特征 |
| `objects` | World Knowledge | 真实物品标志性特征 |
| `information_visualization` | World Knowledge | 抽象/科学概念的可视化转译 |
| `temporal_characteristics` | World Knowledge | 时代特征 |
| `cultural_elements` | World Knowledge | 文化元素准确性 |
| `landmark_identity` | World Knowledge | 著名建筑/地标/地理景观形制准确 |
| `character_likeness` | World Knowledge | 知名人物/虚构角色标志性外观凭知识呈现 |
| `nature_morphology` | World Knowledge | 植物/天象/地质等自然形态符合科学事实 |
| `tech_machinery` | World Knowledge | 车辆/机械/航天器结构符合工程逻辑 |
| `causal_reasoning` | Knowledge Reasoning | 事件间因果关系准确呈现（玻璃碎→碎片飞溅） |
| `relational_reasoning` | Knowledge Reasoning | 隐含的数量比较/比例/次序关系需一步推理仍画对 |
| `counterfactual_coherence` | Knowledge Reasoning | 反事实前提下衍生细节逻辑自洽（光影/倒影随设定变化） |

### 选词结构配比（强制，单题执行）

| 维度 | 上限 | 说明 |
|---|---|---|
| **总数** | **3~5** | 替代旧的 3~6 |
| Alignment 主考 | ≤2 | 主体/属性/动作/布局/关系/场景，单题最多 2 个 |
| Quality 基线 | ≤1 | 画质指标作为兜底，禁止堆砌低阶画质标签抬分 |
| Aesthetics 基线 | ≤2 | 构图/色彩/光影/解剖/情绪/风格，单题最多 2 个 |
| Real-world Fidelity 主考 | 0~2 | 知识型题必含 ≥1 个；非知识型题可 0 个 |

**配比示例**：
- 知识型题：1 RWF + 1 Alignment + 1 Quality + 1~2 Aesthetics = 4~5
- 纯审美/构图题：0 RWF + 1~2 Alignment + 1 Quality + 1~2 Aesthetics = 3~5

**题库级批次配额**（取代旧的"每题均衡"）：调用方按批次下发时，指定各维度出现频率目标，单题不再要求均衡。

### 条件激活规则

`subject_prominence` **条件激活**：仅当题 `gate_spec.required_subjects` 非空时纳入通用线均值；否则该 tag 仍保留作诊断记录但不参与计分。

## 六、难度分级（认知层级，与 checks 数解耦）

| 级别 | 认知定义 | 占比 |
|---|---|---|
| **L1** | 直接世界知识召回，零推理（看到 prompt 即知道答案）| ~20% |
| **L2** | 多步知识组合或一步推理（需跨域信息整合 / solid 因果）| ~40% |
| **L3** | 反事实前提下的逻辑演绎，或多步因果推演 | ~40% |

整体向中高难度倾斜（L2+L3 合计 ~80%）；占比为全套题目标，由调用方按批次配额下发时以调用方指定为准。

**判定要点**：
- L1 vs L2：L1 不需要跨多个知识条目整合；L2 需要 ≥2 个知识条目协同
- L2 vs L3：L3 必须含反事实前提（prompt 显式给出"如果/假设..."）或多步因果链

**checks 数固定 2~4 条**（与难度解耦）。难度由认知层级定，不由 check 数定。

## 七、判分标准（出题时随题给出，评测侧据此执行）

### 知识线

- `implicit_checks`：**2~4 条**，每条含：
  - `check`：可判描述（**禁开放词**："等/大致/合理/东方韵味/完美" 等不得出现）
  - `knowledge`：考察的知识（solid 规则 / 反事实演绎链 / 条目级 verified 状态）
  - `weight`：本题权重，**合计恰 1.0**；**只许离散取值**（0.5 / 0.3+0.3+0.4 / 0.25×4 等）
  - **`rubric`**：0/1/2 三档各自的具体形态描述（每档 ≥10 字，禁止"画得不好"等空泛）
  - **`acceptable_variants`**：列出本题的合法多解画法（≥1 项；可为空数组但需声明 `[]`）
- 知识线得分 = **Σ wᵢ·φ(sᵢ) / Σ wᵢ**，其中 φ = {0:0, 1:60, 2:100}
- 知识熔断：知识线得分 < 40 时总分封顶 20（防"知识 0 + 画质高"假及格）

### 通用线

- 按 `facet_tags` 激活的维度逐维打 {0, 1, 2, "N/A"}，φ 映射 0/60/100 后取均值
- **Real-world Fidelity facet 不进通用线均值**（改作诊断标签进 `facet_diagnostic` 字段，杜绝与知识线重复计分）
- `subject_prominence` 条件激活（见第五节）

### 前置门槛（gate）

- 画面主体缺失 / 主题跑偏 → `gate=true`，**总分封顶 20**
- `gate` 判定必须附 `gate.reason`（≤30 字具体证据）
- 主体在场但不主导 → 由 `subject_prominence` 维度降分，**不走 gate**（与 gate 划界防重复处罚）

### 总分公式

```
total = 0.7 × 知识线 + 0.3 × 通用线
if knowledge < 40:  total = min(total, 20)   # 知识熔断
if gate:            total = min(total, 20)   # 主体缺失/跑偏封顶
```

## 八、区分度工程

每题必须给出 `expected_failure_modes`：弱模型 1~3 种典型错误模式（具体、可操作，禁止"可能画错"这类泛泛描述）。**该字段仅作设计审计与事后对照，不参与 judge 判分**（避免 judge 被预测暗示）。

## 九、输出格式（5 字段 implicit_checks）

只输出一个 JSON 数组，每题一个对象：

```json
{
  "qid": "样本序号-t2i-1",
  "sample_id": "图片文件名",
  "task": "t2i",
  "knowledge_dim": "geo_landmark | cultural_folk | history_event | religion_myth | biology_nature | physics_commonsense | brand_commercial | film_anime_game | tech_aerospace | art_style",
  "difficulty": "L1 | L2 | L3",
  "evidence_audit": {
    "visible_facts": ["图中可直接看到的事实"],
    "anchor_features": ["该实体的核心识别特征"],
    "trigger_check": "非目标模型读 prompt 能否推出 check 答案（必填）",
    "counterexample_test": "尝试构造的反例画面与结论（必填）"
  },
  "gen_prompt": "生成提示词（50~200 字中文）",
  "implicit_checks": [
    {
      "check": "可判描述（禁开放词）",
      "knowledge": "考察知识（solid 规则 / 反事实演绎链）",
      "verified": true | false,
      "weight": 0.4,
      "rubric": {
        "0": "0 档具体形态",
        "1": "1 档具体形态",
        "2": "2 档具体形态"
      },
      "acceptable_variants": ["合法画法 1", "合法画法 2"]
    }
  ],
  "facet_tags": ["..."],
  "gate_spec": {
    "required_subjects": ["主体名 1", "主体名 2"],
    "theme_definition": "主题一句话定义"
  },
  "expected_failure_modes": ["..."],
  "notes": "一句话出题意图"
}
```

**字段裁剪**：
- `probe_dims` 词表**删除**（部分词无判分通道；归因改用 `knowledge_dim × facet_tags × observed_error_code`，错误码在评测阶段生成）
- `needs_verification` 题级布尔**删除**（改 `implicit_checks[].verified` 条目级）
- 新增 `evidence_audit.trigger_check` + `counterexample_test`（强制）
- 新增 `implicit_checks[].rubric` + `acceptable_variants`（强制）
- 新增 `gate_spec`（含 `required_subjects` 数组 + `theme_definition` 一句话）

## 十、质量自检（输出前逐题核对）

1. **泄漏检查**：prompt 与所有 check 词面零重叠（含同义/构成要素）
2. **可判性检查**：每条 check 的 `rubric` 0/1/2 三档各自具体形态可读
3. **难度自评**：按第六节认知层级判定，非按 check 数
4. **facet 配比合规**：总数 3~5 + 第五节配比规则全过
5. **gate_spec 必填**：`required_subjects` 与 `theme_definition` 非空
6. **触发链充分**：`evidence_audit.trigger_check` 自答"能"
7. **反例测试**：`evidence_audit.counterexample_test` 自答"无法构造"
8. **check 权重合计恰 1.0**，且取值离散化（0.5 / 0.3,0.3,0.4 / 0.25×4）
9. **prompt 自足**：不看图能明确知道该画什么

## 十一、示例（照此颗粒度产出）

样本：东京塔形制（不与原图同框，新画面）
- `gen_prompt`：一张 1960 年代日本特摄电影风格的剧照：一只巨型外星生物现身于东京著名地标之畔的废墟街道上，生物占据画面上方约三分之二，街道空无一人，胶片颗粒质感，色调偏冷。
- `implicit_checks`：

```json
[
  {
    "check": "画面中的东京塔具有红白配色与钢架镂空结构",
    "knowledge": "[solid] 东京塔 1958 年建成，自立式钢塔，法定涂装国际橙与白相间",
    "verified": true,
    "weight": 0.3,
    "rubric": {
      "0": "画面无可识别塔形建筑，或为现代东京晴空塔",
      "1": "出现塔形建筑但配色或形制不符（如纯白塔、混凝土塔、广播电视塔形）",
      "2": "红白配色 + 钢架镂空 + 接近 333m 量级，三特征齐"
    },
    "acceptable_variants": [
      "塔体部分被烟尘遮挡但顶部红白配色仍可见",
      "塔在画面一角或被生物部分遮挡"
    ]
  },
  {
    "check": "画面色调、颗粒与明度分布符合 60 年代特摄实拍质感",
    "knowledge": "[solid] 1960s 日特摄以 16mm 胶片实拍 + 模型合成 + 低照度为典型工艺",
    "verified": true,
    "weight": 0.2,
    "rubric": {
      "0": "数字高清 CG 质感，无胶片颗粒",
      "1": "有颗粒但色调或明度接近现代数码夜景",
      "2": "胶片颗粒可见 + 偏冷低照度 + 高光略过曝，三特征齐"
    },
    "acceptable_variants": [
      "彩色颗粒偏暖（部分 60 年代剧也有暖调胶片）"
    ]
  },
  {
    "check": "外星生物占据画面上方约三分之二区域",
    "knowledge": "[solid] 1960s 日特摄主角体型惯例为建筑物数倍",
    "verified": true,
    "weight": 0.2,
    "rubric": {
      "0": "生物在画面中占比小于四分之一，或与建筑同高",
      "1": "生物高度接近建筑 1.5~3 倍",
      "2": "生物显著高于周围建筑（≥3 倍），占据画面上方主导区"
    },
    "acceptable_variants": [
      "生物呈局部特写而非全身，但仍体现巨型感"
    ]
  },
  {
    "check": "街道空无一人，体现巨型生物袭击下的城市真空",
    "knowledge": "[solid] 1960s 日特摄剧构图惯例——主角登场时人类清场以衬压迫感",
    "verified": true,
    "weight": 0.3,
    "rubric": {
      "0": "街道上有人群、车流等城市正常活动迹象",
      "1": "有人物出现但人数稀少且非恐慌姿态",
      "2": "街道彻底空荡 + 有废弃车辆或瓦砾等袭击后迹象"
    },
    "acceptable_variants": [
      "远处有微小人影但主体街道空荡"
    ]
  }
]
```

- `facet_tags`：`["real_world_scene", "temporal_characteristics", "composition", "lighting_atmosphere"]`
  - 配比核验：RWF 2（主考）+ Aesthetics 2（构图 + 光影）+ Alignment/Quality 0 → **配比合规**（总数 4，主考 RWF 2 ≤2，Quality 0 ≤1，Aesthetics 2 ≤2）
- `difficulty`：`"L2"`（需跨域知识组合：建筑年代 + 工艺特征 + 构图惯例，无反事实前提）
- `gate_spec`：`{"required_subjects": ["东京塔"], "theme_definition": "60年代日特摄电影风格的城市怪兽袭击场景"}`
- `expected_failure_modes`：
  - 画成现代东京晴空塔（蓝白配色 + 圆柱形 + 更高）
  - 数字高清 CG 质感，无胶片颗粒
  - 生物与建筑同高，比例错误
  - 街道上有人群车流，未体现城市真空

## 十二、开始

现在我将提供样本（样本序号 + 图片 + query_label + caption + taxonomy 路径）。请对每个样本出 {每图题数} 道生成题，严格按第三节流程执行（证据审计 + 触发链 + 反例测试必须写入输出），最终只输出 JSON 数组。
================ 全文结束 ================

## 三、评审任务（开放式，评审维度由你自行确定）

这份文档是在第一轮多模型评审后整合产出的 **V4 修订版**。评审角度、重点与深度**由你自行确定**，不必遵循任何预设提纲。

要求：

1. 对文档整体做挑剔的审查：流程、红线、判分契约、facet 词表、输出 schema、示例、内部一致性——任何你认为有问题的方面；
2. 可以重点关注：修订是否堵住了此前的漏洞、上一轮评审遗漏的问题、以及修订新引入的问题；
3. 每个问题给出：**结论（保留/修改/废除）+ 理由 + 具体改写措辞（如有）**；
4. 最后给一份按严重度排序的 Top 5 问题清单；
5. 不必客气，直接指出设计缺陷；不接受「整体不错」类空泛评价。

## 四、输出格式要求

- 先给整体判断（可直接投用 / 需小修 / 需大改），再逐条列出问题；
- 每条问题给：**结论（保留/修改/废除）+ 理由 + 具体改写措辞（如有）**；
- 最后给一份按严重度排序的 Top 5 问题清单；
- 不必客气，直接指出设计缺陷；不接受「整体不错」类空泛评价。
