# 任务：整合三份二轮评审意见，产出 t2i 出题 Prompt V5 全文

## 一、背景

你是文生图评测基准出题 Prompt 的设计者。现行版本是 V4（下文全文）。V4 发布后，三个模型对它做了第二轮设计评审，意见全文见后。现在请你整合这三份意见，修订产出 V5。

## 二、现行 V4 全文

================ V4 全文开始 ================

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

================ V4 全文结束 ================

## 三、评审意见一：openrouter/anthropic/claude-fable-5（第一评审人自己的意见）

<!-- openrouter/anthropic/claude-fable-5 · 2026-08-26 · 评审包 data/review_v4_request.md（V4 二轮） -->
<!-- tokens: prompt=9883 completion=10701 · 耗时 172s -->

# 整体判断：**需大改**

框架层面（双线判分、知识熔断、gate 划界、五字段 check、条目级 verified）方向正确，V4 确实堵住了 V3 的几个大洞。但存在 **3 处契约级自相矛盾**，且**新重写的示例本身大面积违反本文档自己的红线**——示例是出题 LLM 的少样本模板，这个错误会被批量复制到全部题目，属于必须返工的阻断性缺陷。以下逐条列出。

---

## A. 示例问题（最严重，示例即模板）

### A1. 示例 checks 2/3/4 全部泄题，合计权重 0.7 是「指令遵循」不是「知识探针」

**结论：废除该示例，重写。**

逐条对照：

| check | prompt 原文 | 泄漏形式 |
|---|---|---|
| check 3「生物占据画面上方约三分之二」 | 「生物占据画面上方约三分之二」 | **逐字重复** |
| check 4「街道空无一人」 | 「街道空无一人」 | **逐字重复** |
| check 2「胶片颗粒可见 + 偏冷低照度」 | 「胶片颗粒质感，色调偏冷」 | 近义改写 |

红线 1 要求「隐含约束的正确画法不得在 prompt 中明写或近义改写」，质量自检第 1 条要求「prompt 与所有 check 词面零重叠」。示例四条 check 中只有 check 1（东京塔红白钢架）是真知识探针，其余三条是 prompt 明说就能画对的 Alignment 项——这正是第三节第 5 步明令禁止的。**大魔神示例因泄题被废，新示例犯了一模一样的病，且更隐蔽（分散在多条 check 里）。** 出题 LLM 学到的信号将是：「把 prompt 里的显式约束抄成 check 就能凑够 2~4 条」，整个知识线判分随之空心化。

**改写方向**：check 2/3/4 要么降级为 facet_tags（composition / lighting_atmosphere 已在通用线覆盖），要么替换为真隐含知识点，例如：
- 「1960 年代东京街道不出现现代元素（LED 屏、智能手机、新干线 N700 系）」——prompt 只说年代，正确画法靠时代知识；
- 「塔的高度显著高于周边建筑（1960 年代东京无摩天楼，东京塔是绝对制高点）」——prompt 未提天际线，靠历史知识。

### A2. 示例 prompt 未点名东京塔，与 gate_spec、红线 4 双重冲突

**结论：修改。**

prompt 写「东京著名地标之畔」，但 `gate_spec.required_subjects=["东京塔"]`。模型合法地画出浅草雷门、国会议事堂（均为 1960 年代已存在的东京地标），完全符合 prompt 字面，却被 gate 直接封顶 20 分。这违反两条自家规则：
1. 红线 4「prompt 须中文明写主体」——东京塔是判分主体却未明写；
2. 反例测试——「符合 prompt 但违反 check 的合理画面」显然可构造（画晴空塔以外的任何老地标），按第三节第 4 步本题应弃题或改写。

如果设计意图是「1960 年代 + 著名地标 → 必须推出东京塔」这个知识触发链，那么身份判定应由 **check 1 承担（权重加大到 0.4~0.5）**，gate.required_subjects 只放 prompt 显式主体（「巨型外星生物」「城市街道」）。**gate 是钝器，不应承载需要知识推理的判定**——否则知识失败被 check 扣分 + gate 封顶双重处罚，且封顶抹平了「画了晴空塔（时代错误）」和「什么塔都没画」的区分度。

### A3. 示例 facet 配比核验自打脸：`real_world_scene` 属于 Alignment，不是 RWF

**结论：修改。**

第五节词表中 `real_world_scene` 明确挂在 **Alignment → Scene** 下，示例的配比核验却写「RWF 2（主考）」把它算进 RWF。实际是 RWF 1（temporal_characteristics）+ Alignment 1 + Aesthetics 2。核验文字对着自己的词表数错数，说明「配比核验」这个环节形同虚设——如果人写的示例都数错，出题 LLM 的自检更不可信。建议配比核验改为程序侧硬校验，不依赖 LLM 自报。

### A4. 示例其他硬伤（合并列出）

**结论：修改。**
- check 1 rubric 2 档「接近 333m 量级」——judge 从单张图**无法判定绝对高度**，违反红线 2 可判性。改为「塔高显著超过画面内所有建筑」；
- check 2 的 acceptable_variant「彩色颗粒偏暖」**直接抵触 prompt 显式约束「色调偏冷」**——知识线放行、通用线（lighting_atmosphere）扣分，judge 无所适从。多解不得与 prompt 显式约束冲突，此条应删；
- check 3/4 的 knowledge 把「特摄构图惯例」「人类清场惯例」标注 `[solid]`——这是类型片创作倾向，最多算 likely，标注纪律被示例自己带头破坏；
- 示例缺 `evidence_audit`、`notes`、`qid` 等 schema 必填字段，却自称「照此颗粒度产出」——出题 LLM 会学到「这些字段可省」。示例必须是 schema 完整实例。

---

## B. 规则契约矛盾

### B1. 红线 2「多步演绎（≥2 步）不得作为 check 主判据」与 L3 定义「多步因果推演」直接矛盾

**结论：修改（二选一）。**

第六节 L3 = 「反事实前提下的逻辑演绎，**或多步因果推演**」，且 L3 占比高达 ~40%。但红线 2 但书只允许「一步演绎」，多步演绎禁作主判据。结果是：**非反事实的 L3 题（多步因果型）在现行红线下造不出任何合法 check**，这 40% 配额里的相当一部分是空集。要么 L3 定义收缩为「仅反事实前提 + 一步演绎」，要么红线 2 放宽为「≤2 步且每步均为 solid 规则、链条完整写入 knowledge」。我建议前者——多步链的 judge 可判性本来就差。

顺带：红线 2 内嵌「反事实但书」+ 红线 7 又单列「反事实豁免」，两处表述范围不一致（但书说的是 check 构造方式，豁免说的是「豁免必须 solid 约束」——而"必须 solid"其实写在第三节第 2 步，不在红线 2 正文里）。**合并为一条**，消除引用错位。

### B2. `verified=false` 条目的判分语义未定义

**结论：修改。**

第三节说 likely 知识「判分时仅作辅助参考，不作 check 主判据」，但 schema 里 `verified=false` 的 check 照样有 weight，且「weight 合计恰 1.0」按全部 check 计。那么 judge 到底：(a) 照常打分？(b) 跳过并按 Σwᵢ 归一化（公式里的 /Σwᵢ 暗示了这个可能，但从未明说）？(c) 打分但降权？三种实现给出的知识线分差可达 40 分以上。必须写死，建议：**verified=false 条目单条 weight ≤0.2，judge 照常打分但该条得 0 不触发熔断计算（即熔断只看 verified=true 条目的加权分）**——或者干脆规定 verified=false 不得进入 implicit_checks，只能写入 notes。

### B3. `acceptable_variants` 自相矛盾：「≥1 项；可为空数组但需声明 []」

**结论：修改。**

同一括号内既要求 ≥1 项又允许空数组。改为：「原则上 ≥1 项；确无合法多解时允许 `[]`，但须在 notes 中一句话说明为何本 check 唯一解」。

### B4. `subject_prominence` 条件激活是死代码

**结论：修改。**

条件是「gate_spec.required_subjects 非空时激活」，而质量自检第 5 条强制**每题** required_subjects 非空——条件恒真，规则无意义。要么放宽自检 5（纯氛围/抽象题允许空 gate），要么删掉条件激活表述直接写「subject_prominence 恒计入」。

---

## C. 结构性风险

### C1. RWF 不进通用线 + 配比规则叠加后，通用线可能只剩 1~2 个计分维度

**结论：修改。**

合规组合「2 RWF + 1 Alignment + 1 Quality」（总数 4，全部配比过关）的通用线只有 2 个计分维度；「2 RWF + 1 Quality」若被产出则只剩 1 维——总分 30% 押在单个 {0/60/100} 的三值变量上，方差巨大且 judge 单点误判被放大。**增补硬规则：非 RWF（即实际计分）facet ≥3。** 示例本身（扣掉 temporal_characteristics 后计分维度 3 个）勉强达标，纯属侥幸。

### C2. weight 离散取值集不闭合

**结论：修改。**

「只许离散取值（0.5 / 0.3+0.3+0.4 / 0.25×4 **等**）」——用「等」定义白名单等于没定义（讽刺的是 check 字段自己禁用「等」字）。写死枚举：2 条 → {0.5+0.5, 0.6+0.4, 0.7+0.3}；3 条 → {0.4+0.3+0.3, 0.5+0.3+0.2}；4 条 → {0.25×4, 0.3+0.3+0.2+0.2, 0.4+0.2+0.2+0.2}。

### C3. 红线 5 禁负向约束 vs 示例「街道空无一人」

**结论：修改（澄清边界）。**

「空无一人」在语义上就是「必须不出现人」。如果这算合规，红线 5 的「负向约束」边界就需要明确定义：建议改为「禁止以否定句式表达的排除约束（'不要出现 X'）；允许以正向场景状态描述隐含排除（'空旷的''无人的'）」。否则出题 LLM 面对示例与红线会各自随机站队。

### C4. 评审元信息混入操作 prompt

**结论：修改。**

「版本说明」整块（三家评审整合、"用户拍板"、"论文自报 60 分"、review_responses.md 引用）是写给人类评审看的，不是给出题 LLM 的指令。留在系统 prompt 里除了消耗上下文，还会诱导 LLM 在输出里模仿这种元讨论口吻。投用版应剥离全部版本沿革内容。同理，红线 1 中「如'石像'→'石质'」引用的是已废除的大魔神示例，属僵尸引用，应换成现行示例的实例。

### C5. L3 占比 40% 与「真实图片锚定」的路线冲突

**结论：保留但降配。**

L3 强制含反事实前提，但整个链路的根基是「真实样本图作知识锚点与判分参照」。反事实题（"假设地球无重力"）的画面必然大幅偏离原图，原图的判分参照价值趋近于零，gate 的主体判定也会变得诡异。40% 的 L3 配额意味着近半题目脱离了这套基础设施的设计前提。建议 L3 降到 20~25%，L2 提到 55%，并明确反事实题的 gate_spec 主体判定以 prompt 显式主体为准、完全不参照原图。

### C6. 通用线 N/A 处理未明说

**结论：修改（一句话补丁）。** 「逐维打 {0,1,2,N/A} 后取均值」——补一句「N/A 维度不计入分母」，否则不同 judge 实现会产生系统性分差。

---

## Top 5 问题清单（按严重度）

| # | 问题 | 严重度 | 处置 |
|---|---|---|---|
| 1 | **示例 checks 2/3/4（权重 0.7）与 prompt 词面重叠，泄题违反自家红线 1**——示例即模板，错误将被批量复制，知识线空心化 | 阻断 | 废除重写示例 |
| 2 | **红线 2 禁多步演绎 vs L3 定义含「多步因果推演」直接矛盾**，~40% 配额中的多步因果题无合法 check 可造 | 阻断 | 收缩 L3 定义为仅反事实型 |
| 3 | **示例 gate_spec 含 prompt 未明写的「东京塔」**，合法多解被 gate 一票封顶 20，且与红线 4「明写主体」冲突 | 高 | gate 只收 prompt 显式主体，身份判定归 check |
| 4 | **`verified=false` 条目判分语义未定义**（是否计分/是否入 Σwᵢ/是否触发熔断三种实现分差 >40 分） | 高 | 写死规则：单条 weight ≤0.2 且不触发熔断，或禁入 checks |
| 5 | **示例配比核验把 Alignment 的 `real_world_scene` 数成 RWF** + 通用线可退化到 1~2 个计分维度 | 高 | 配比改程序硬校验；增补「非 RWF facet ≥3」 |

**投用前置条件**：#1、#2 必须解决；#3~#5 及 B3/B4/C2/C3/C4/C6 属一轮可完成的修补。框架不必推倒，但当前版本直接投产会产出一批「看起来像知识探针、实际是指令遵循复读」的题目。


## 四、评审意见二：openrouter/openai/gpt-5.6-sol

<!-- openrouter/openai/gpt-5.6-sol · 2026-08-26 · 评审包 data/review_v4_request.md（V4 二轮） -->
<!-- tokens: prompt=7147 completion=11116 · 耗时 186s -->

# 整体判断：**需大改，当前版本不宜直接投用**

V4 增加了 rubric、acceptable variants、gate 和证据审计，方向正确，但核心契约仍没有闭合。最严重的问题不是措辞，而是：

- “图片是唯一事实依据”与“考察图外世界知识”互相冲突；
- `verified=false` 条目仍处于计分数组中，存在把未核验事实写进正式分数的风险；
- “禁止泄漏”被写成词面零重叠，反而导致实体触发不足；
- 示例自身违反多条红线，不能作为 few-shot；
- 只排除 RWF 重复计分，却没有排除 `physical_logic`、`anatomical_fidelity` 等跨支柱重复；
- 缺少拒题输出、judge 输入字段白名单和不可见时的判分规则。

这些问题会直接污染题库有效性和最终分数，不属于“小修”。

---

# 逐条审查

## 1. “图片是唯一事实依据”与图外知识出题目标矛盾

**结论：修改。**

### 理由

第二节称图片是“唯一事实依据”，第三节又要求展开典型属性、历史脉络、物理规律等图外知识。图片无法核实：

- 东京塔建成年份；
- 某文化器物的规范形制；
- 某动物的典型栖息行为；
- 某机械结构的工程原理。

“任何进入题目设定的事实必须能在图中直接或一步溯源”也没有可执行定义。若严格执行，大多数世界知识题都不能出；若宽松执行，“一步溯源”会成为任意外推的口袋条款。

此外，`anchor_features` 混合了“图中实际可见特征”和“实体的规范特征”，容易把模型先验伪装成图像证据。

### 建议改写

将第二节改为：

> `image` 是样本身份核验与可见事实的第一手依据，不是全部世界知识的来源。  
> - 图中直接可见内容只能写入 `visible_facts`；  
> - 实体的规范特征、历史事实、物理规则等图外知识必须来自允许的参考知识源，并记录核验依据；  
> - 不得仅凭模型记忆把图外知识声称为“图中已核实”；  
> - 若图片、`query_label` 与参考知识无法共同确认实体身份，必须拒题。

并拆分字段：

```json
"evidence_audit": {
  "visible_facts": [],
  "observed_anchor_features": [],
  "canonical_features": [],
  "entity_match": {
    "status": "confirmed | uncertain | mismatch",
    "reason": ""
  }
}
```

---

## 2. `verified=false` 仍被放进计分数组，判分契约不闭合

**结论：修改；禁止未核验条目进入 `implicit_checks`。**

### 理由

第三节说：

> likely 知识写入 knowledge，verified=false，仅作辅助参考，不作 check 主判据。

但输出 schema 中它仍是 `implicit_checks[]` 的正式条目，具有：

- `check`
- `weight`
- `rubric`
- `acceptable_variants`

知识线公式也没有排除 `verified=false`。因此评测侧若按 schema 通用实现，仍会把它计入分数。

更根本的问题是：让出题 LLM 自己声明 `verified=true` 不等于事实经过核验。“solid”只是模型判断，不是证据。

### 建议改写

正式计分条目一律要求：

```json
"verification": {
  "status": "verified",
  "source_type": "reference_db | approved_document | benchmark_policy",
  "source_id": "..."
}
```

并明确：

> `implicit_checks` 中只允许出现已核验条目。任何 `unverified` 或 `likely` 候选不得设置权重，不得进入知识线，应写入 `discarded_check_candidates` 或直接删除。

如果不希望输出来源，至少应改成：

> `implicit_checks[].verified` 必须恒为 `true`；`false` 是 schema 校验错误。

---

## 3. “词面零重叠”式防泄漏规则是错误的

**结论：废除词面零重叠，改成“答案属性不得泄漏”。**

### 理由

当前规则要求：

> prompt 与所有 check 词面零重叠，含同义/构成要素。

这会阻止最关键的实体触发。例如要考东京塔的红白钢架形制，prompt 必须能明确触发“东京塔”；实体名出现在 prompt 和 check 中并不是泄漏。真正泄漏的是“红白”“钢架镂空”等答案属性。

词面重叠也不是可靠的语义判据：

- 无词面重叠仍可通过描述泄漏答案；
- 有词面重叠不一定泄漏；
- “石像→石质”这类规则过宽，甚至会阻止陈述主体材质类别；
- 自动执行同义词禁用几乎不可控。

### 建议改写

用以下规则替换红线 1 和自检 1：

> **禁止答案属性泄漏**：  
> `gen_prompt` 可以且通常应明确实体名、场景前提和任务目标；不得明示或近义改写本题要测试的目标属性、派生结论及评分阈值。  
> 实体名、类别名、反事实前提和必要触发条件不视为泄漏。  
> 泄漏检查基于语义角色，不采用“prompt 与 check 词面零重叠”规则。

建议增加字段：

```json
"trigger_chain": [
  {
    "prompt_cue": "东京塔",
    "required_prior": "东京塔的规范外观",
    "target_attribute": "红白相间钢桁架",
    "leaked_in_prompt": false
  }
]
```

---

## 4. 触发链检查目前会鼓励形式化自答“能”

**结论：修改。**

### 理由

`trigger_check` 当前是自由文本，并在质量自检中要求“自答能”。这不是审计，只是在要求模型输出结论。真正需要验证的是：

1. prompt 中哪个词触发了哪个实体或规则；
2. 需要调用什么常识；
3. 能否在不读取隐含 check 的情况下得到目标答案；
4. 是否存在实体歧义或缺失前提。

“非目标模型读到 prompt”表述也不清楚。是指另一个 LLM、普通人，还是被测生成模型？

### 建议改写

改为结构化字段：

```json
"trigger_audit": {
  "prompt_cues": ["东京塔"],
  "required_priors": ["东京塔的规范外观"],
  "inference_steps": 0,
  "missing_premises": [],
  "result": "pass"
}
```

并改写流程：

> 检查一个只获得 `gen_prompt` 的合格解题者，能否依据 prompt 中的明确实体或前提调用所需常识。若实体名未出现、存在多个同样合理的实体解释，或必须依赖样本图片才能知道考什么，则拒题。

---

## 5. 反例测试的判定对象没有定义清楚

**结论：修改。**

### 理由

当前要求：

> 尝试构造符合 prompt 但违反 check 的合理画面；若能构造，check 无效。

问题在于知识探针本来就是要区分：

- 语法上满足 prompt；
- 但因世界知识错误而画错。

例如 prompt 写“东京塔”，画成纯白混凝土塔，从表面词面看“有一座塔”，但从实体语义看并不符合“东京塔”。这种画面不应被算作“合理反例”。

必须区分：

- **合法解释差异**：确实符合 prompt，也不应扣分；
- **知识性错误**：违反实体定义或世界规则，应扣分；
- **构图导致不可见**：需要另行规定。

### 建议改写

> 反例测试只寻找“在完整语义上符合 `gen_prompt`，但会被当前 rubric 错误扣分”的合法解释。  
> 由于实体知识错误、物理错误或文化形制错误而产生的画面不属于合法反例。  
> 若存在合法解释，应将其加入 `acceptable_variants` 或删除该 check。

字段改为：

```json
"counterexample_test": {
  "candidate": "候选合法解释",
  "is_prompt_compliant": false,
  "reason": "违反东京塔实体身份",
  "result": "pass"
}
```

---

## 6. 当前东京塔示例必须整体废除

**结论：废除并重写，不应保留为 few-shot。**

### 理由

该示例同时违反多条正式规则：

1. **prompt 没有明确写“东京塔”**  
   只写“东京著名地标”，可以合理生成东京晴空塔、涩谷路口、彩虹桥等。  
   `gate_spec` 却要求东京塔，触发链明显不充分。

2. **大量 check 被 prompt 直接泄漏**
   - “1960 年代日本特摄电影风格”直接对应第二条；
   - “生物占据画面上方约三分之二”直接对应第三条；
   - “街道空无一人”直接对应第四条；
   - “胶片颗粒质感，色调偏冷”也直接对应第二条。

   这些是 Alignment 项，不是隐含知识项。

3. **违反负向约束红线**  
   prompt 明写“街道空无一人”，本质上就是零数量/否定约束。

4. **多个所谓 solid 事实不够稳固**
   - “1960s 日特摄以 16mm + 低照度为典型工艺”不是足够普适的单一事实；
   - “主角体型惯例为建筑物数倍”并非稳定规则；
   - “人类清场是构图惯例”也不足以作为确定性知识检查。

5. **333 米量级不可从单张画面可靠判定**  
   rubric 要求“接近 333m 量级”，没有已知尺度参照时无法视觉判断。

6. **expected_failure_modes 数量违规**  
   正文要求 1~3 种，示例列了 4 种。

### 建议改写要求

新示例至少应满足：

- `gen_prompt` 明确写出目标实体名；
- prompt 不写被测试的规范属性；
- check 只考实体规范形制或由前提推出的视觉结果；
- prompt 明示的“胶片、冷色、空街、画面占比”放入 Alignment/Style 评分，不进入知识线；
- 不使用无法从图像可靠估测的真实米数；
- 全部事实经独立核验。

在新示例完成前，宁可删除示例，也不要保留当前错误示范。

---

## 7. “禁止负向约束”过于绝对，且与示例冲突

**结论：修改。**

### 理由

完全禁止“不出现某元素”会误伤很多可判的视觉任务，例如：

- 一个密闭棋盘格中没有重复棋子；
- 指定道路上行人数量为零；
- 日食场景中太阳盘被完全遮挡。

真正有问题的是开放世界式否定：“画面中不能有任何现代元素”，这种几乎无法穷尽判断。有限区域、有限类别、零数量约束可以判定。

### 建议改写

> 禁止开放世界、边界不清的负向约束，如“不得出现任何现代元素”“不能有不协调物体”。  
> 允许对明确对象、明确区域和可穷举类别设置零数量约束，如“桌面上无人”“三个杯子中没有破损杯”；此类约束应使用 `quantity` 或专门 rubric 判分，不得伪装成隐含知识点。

如果项目坚持全面禁用，则示例中的“街道空无一人”必须删除。

---

## 8. 反事实红线互相冲突，“豁免 solid”尤其危险

**结论：修改；废除红线 7 的 solid 豁免。**

### 理由

红线 2 要求：

> solid 规则 + prompt 假设 → 一步演绎。

红线 7 又说：

> 反事实题豁免“必须 solid”约束。

二者冲突。反事实前提可以是虚构的，但推导规则必须比普通题更明确、更稳定，而不是允许使用 likely 规则。否则评测的是出题者任意设定，而不是模型推理。

此外：

- L3 允许“多步因果推演”；
- 红线 2 又说多步演绎不得作为 check 主判据；
- 难度定义与可判规则没有统一。

### 建议改写

删除红线 7，统一为：

> 反事实前提可以与现实不一致，但必须由 prompt 明示；从该前提到视觉结论所使用的逻辑、几何或物理转换规则必须经过核验。  
> L3 可包含 2~3 个明确、可复核的推导步骤；每一步必须写入 `derivation`。超过 3 步或存在分支解释时拒题。

增加：

```json
"derivation": [
  {"premise": "...", "rule": "...", "conclusion": "..."}
]
```

---

## 9. 难度定义仍不稳定，40% L3 会诱导批量制造伪反事实题

**结论：修改。**

### 理由

L2 同时被定义为：

- 一步推理；
- 多步知识组合；
- 至少两个知识条目协同。

L3 又同时包括：

- 反事实；
- 多步因果。

这些并不是同一难度轴。一个简单反事实可能比跨三个文化知识点的 L2 更容易。固定 40% L3 还会迫使出题模型对不适合的样本强行加“假设无重力”等包装，削弱实体锚定。

### 建议改写

把难度拆成两个字段：

```json
"difficulty": "L1 | L2 | L3",
"reasoning_profile": {
  "retrieval_units": 2,
  "inference_steps": 1,
  "counterfactual": false
}
```

推荐定义：

- L1：1 个知识单元，0 步推导；
- L2：2 个以上知识单元，或 1 步确定性推导；
- L3：2~3 步确定性推导，或反事实前提下至少 1 步派生。

并补充：

> 难度配额是目标分布，不得覆盖样本适配性；不适合 L3 的样本应拒题或回退，不得强行反事实化。

---

## 10. 强制 2~4 个知识 check 会制造填充项和重复计分

**结论：修改。**

### 理由

不是每个样本都天然支持 2~4 个独立、稳定、视觉可判的隐含知识点。强制数量会诱导：

- 把 prompt 明示约束塞进知识线；
- 把一个整体特征拆成多个高度相关 check；
- 用风格、构图和画质填充；
- 同一错误被多次扣分。

东京塔示例已经出现这种退化：四条中真正可能属于隐含世界知识的只有塔形制。

### 建议改写

两种可选方案：

**方案 A：保持 2~4，但增加拒题机制**

> 仅在能形成至少 2 个非同义、非因果包含、非共享同一视觉证据的知识检查时出题；否则输出 reject，不得用 Alignment、Quality 或 prompt 明示属性填充。

**方案 B：改为 1~4，推荐 2 条**

这更符合真实数据分布。

还应新增独立性审计：

```json
"check_independence": {
  "overlap_pairs": [],
  "result": "pass"
}
```

---

## 11. 缺少拒题输出，会迫使出题模型幻觉造题

**结论：修改，必须增加 reject schema。**

### 理由

文档多次写“弃题”“废弃重出”，但最终格式只允许输出完整题对象。如果图片模糊、实体标签错误、知识点不足或事实无法核验，模型没有合法退出路径，只能：

- 编造事实；
- 生成低质量检查；
- 伪造 `trigger_check=能`；
- 伪造 `verified=true`。

这是批量出题系统中的高风险漏洞。

### 建议改写

允许两类对象：

```json
{
  "qid": "...",
  "sample_id": "...",
  "task": "t2i",
  "status": "reject",
  "reject_code": "ENTITY_MISMATCH | INSUFFICIENT_VISUAL_EVIDENCE | UNVERIFIED_KNOWLEDGE | NO_INDEPENDENT_CHECKS | AMBIGUOUS_TRIGGER",
  "reason": "..."
}
```

正常题增加：

```json
"status": "accepted"
```

---

## 12. 只排除 RWF 重复计分，不足以阻止知识线与通用线重复

**结论：修改。**

### 理由

目前只规定 Real-world Fidelity 不进通用均值，但重复计分跨支柱依然存在：

- 重力、倒影、稳定性可能同时计入知识 check 和 `physical_logic`；
- 动物解剖可能同时计入 `animals`、知识 check 和 `anatomical_fidelity`；
- 材质知识可能同时计入知识 check、`material_properties`、`material_texture`；
- 时代风格可能同时计入知识 check、`style_control`、`lighting_atmosphere`。

因此“RWF 不计分”并没有真正堵住重复计分。

### 建议改写

> 任一通用 facet 若与某个 `implicit_check` 使用同一视觉证据或惩罚同一错误，只能作为诊断项，不得进入通用线均值。重复判定按视觉证据而非支柱名称判断。

schema 可改为：

```json
"facet_tags": [
  {
    "key": "physical_logic",
    "mode": "diagnostic",
    "overlap_with_checks": ["c1"]
  },
  {
    "key": "composition",
    "mode": "scored",
    "overlap_with_checks": []
  }
]
```

这比单纯排除整个 RWF 支柱更可靠。

---

## 13. facet 没有题级评分锚点，judge 一致性不足

**结论：修改。**

### 理由

知识线有题级 rubric，通用线只有词表中一句泛化定义。一次 VLM judge 很难稳定区分：

- `composition=1` 与 `2`；
- `naturalness=1` 与 `2`；
- `lighting_atmosphere=1` 与 `2`；
- `style_control=1` 与 `2`。

同时，出题者可以选择对该题更容易得高分的 facet，造成题间不可比。

### 建议改写

至少二选一：

1. 为每个 facet 建立全局固定 0/1/2 rubric，并版本化；
2. 输出题级 `facet_rubrics`，但必须继承全局定义。

例如：

```json
"facet_rubrics": {
  "composition": {
    "0": "...",
    "1": "...",
    "2": "..."
  }
}
```

同时明确：

> facet 的选取只能由 prompt 中可验证的显式要求触发，不得为了凑够 3~5 个标签加入未实际要求的审美项。

目前“知识题必选 3~5 个”仍可能迫使加入无关 Aesthetics 标签。

---

## 14. “纯审美/构图题”与项目目标、强制流程矛盾

**结论：废除该类示例措辞。**

### 理由

第一节和第三节明确要求每题至少一个约束依赖知识，整个项目也定义为知识探针。第五节却允许：

> 纯审美/构图题：0 RWF。

这为非知识题进入题库打开了后门，并与“知识型题必含 RWF”以及 `implicit_checks` 2~4 条冲突。

### 建议改写

删除“纯审美/构图题”配比示例，改成：

> 本赛道所有 accepted 题均为知识型题，必须至少包含 1 个 RWF 诊断标签。纯审美、纯构图或纯画质题不属于本赛道，应拒题或转入其他赛道。

---

## 15. gate 的主体契约不清楚，`subject_prominence` 条件实际上是死规则

**结论：修改。**

### 理由

第五节说：

> `required_subjects` 非空时，subject_prominence 激活。

第十节又要求：

> `required_subjects` 非空。

因此该条件永远成立；“否则仅诊断”的分支不可达。

此外，多主体题中缺少任一配角是否 gate 不清楚。例如 required subjects 为“东京塔、怪兽”，怪兽缺失显然跑题；若“废弃车辆”也被列进去，缺失它是否也封顶 20？

`theme_definition` 由出题模型自由写，可能比 prompt 更窄，造成额外隐含要求。

### 建议改写

```json
"gate_spec": {
  "required_subjects": [
    {
      "name": "东京塔",
      "role": "primary",
      "absence_triggers_gate": true
    },
    {
      "name": "怪兽",
      "role": "primary",
      "absence_triggers_gate": true
    }
  ],
  "theme_definition": "...",
  "entailed_by_prompt": true
}
```

明确：

> 只有 primary 主体缺失或整体主题变更才触发 gate；辅助元素缺失按 Alignment 扣分。  
> `theme_definition` 不得增加 `gen_prompt` 中没有的要求。

`gate.reason ≤30 字`也太短，建议改为 ≤80 字，并要求指出位置和缺失对象。

---

## 16. 知识熔断存在明显边界跳变和补偿漏洞

**结论：修改并实证校准。**

### 理由

当前规则中：

- 3 条权重为 0.4/0.3/0.3；
- 只答对 0.4 权重的一条，就得到知识分 40；
- 因条件是 `<40`，恰好不熔断；
- 总分可能从知识 39 时的封顶 20，跳到知识 40 时约 58~67。

这产生不自然的巨大边界跳变，也未必能实现“核心知识失败就封顶”。

### 建议改写

增加关键检查：

```json
"critical": true
```

评分规则改为：

> 若任一 `critical=true` 的实体身份或核心知识 check 得分为 0，则总分封顶 20。  
> 其余知识检查按加权分计算。知识阈值及 φ 映射须经开发集校准，不应仅凭规则直觉固定。

如果不增加 critical，至少应重新评估 `<40` 是否应为 `<=40`，但这仍不能解决两条 0.5 权重时“一对一错得 50”的问题。

---

## 17. rubric 还缺少互斥、穷尽和不可见处理规则

**结论：修改。**

### 理由

“每档至少 10 字”只能防止过短，不能保证 rubric 可用。当前示例存在：

- check 写“红白配色与钢架结构”，rubric 2 又加入“333m 量级”，评分目标被偷偷扩充；
- acceptable variant 允许遮挡，但没有规定遮挡到什么程度仍可判；
- 0/1/2 档未必互斥；
- 生成模型可以通过裁切、烟雾或背面视角隐藏关键特征。

知识线没有 N/A，因此必须规定“关键证据不可见”如何处理，否则 judge 会随意决定。

### 建议改写

增加硬约束：

> rubric 的三个档必须互斥、覆盖全部可预见输出，并且只评价 `check` 中声明的属性，不得在 rubric 中新增判分条件。  
> 若模型构图导致关键属性不可见、被完全遮挡或尺度不足以判断，默认记 0；只有 prompt 明确允许该视角且存在其他充分视觉证据时，才按证据评分。知识 check 不使用 N/A。

并为每条增加：

```json
"visibility_requirement": "塔体至少有一半可见，配色和桁架均可辨认"
```

---

## 18. `acceptable_variants` 的要求自相矛盾

**结论：修改。**

### 理由

正文同时说：

- “≥1 项”；
- “可为空数组但需声明 []”。

二者不能同时成立。强制至少一项还会诱导模型编造不必要变体；允许空数组则应明确其语义。

### 建议改写

> `acceptable_variants` 必填，允许为空数组。只有存在会影响判分的合法替代形态时才列出；不得为了满足数量要求编造变体。

或者若确实希望每题考虑多解，则规定至少一项必须是“标准形态”，但这种做法信息价值较低。

---

## 19. schema 不够机器可执行，多个字段仍靠自然语言约定

**结论：修改。**

### 理由

当前只是 JSON 示例，不是正式 schema。仍存在：

- `knowledge_dim` 为单值，但 L2 强调跨域知识；
- qid 模板固定 `-t2i-1`，多题时如何递增未说明；
- 权重“0.5 / 0.3+0.3+0.4 / 0.25×4”不是正式枚举；
- 50~200“字”如何计算未定义；
- 是否允许额外字段未定义；
- `verified=false` 如何处理未定义；
- N/A 全部出现时通用均值如何处理未定义；
- expected failure modes 正文 1~3、示例 4 条。

### 建议改写

发布正式 JSON Schema，并至少规定：

- `schema_version`；
- `additionalProperties: false`；
- qid 递增规则；
- 合法权重组合：
  - 2 条：`[0.5, 0.5]`
  - 3 条：`[0.4, 0.3, 0.3]`
  - 4 条：`[0.25, 0.25, 0.25, 0.25]`
- 中文字符长度的计算方式；
- 空通用分母的处理；
- accepted/reject 两类对象；
- `knowledge_dim_primary` 与 `knowledge_dim_secondary[]`。

---

## 20. 必须明确 judge 的输入白名单，否则 expected failure 会直接暗示判分

**结论：修改，属于评测契约级问题。**

### 理由

文档说 `expected_failure_modes` 不参与 judge 判分，但如果整个题对象被原样传给 VLM judge，模型仍会看到这些预测错误并受到强烈暗示。同样，`evidence_audit.counterexample_test`、notes 等字段也可能改变判分。

原始样本图如果传给 judge，也可能造成不合理的构图相似度比较，与“原图不是生成目标”冲突。

### 建议改写

明确 judge 仅接收以下字段：

```text
generated_image
gen_prompt
implicit_checks[check, rubric, acceptable_variants, visibility_requirement]
scored facet tags / facet rubrics
gate_spec
```

明确禁止传入：

```text
source image
caption
taxonomy
evidence_audit
expected_failure_modes
notes
discarded candidates
```

补充：

> 原始样本图仅用于出题阶段的身份与证据审计，不进入正式 judge 输入，除非另设独立的参考核验流程；不得按与原图的构图相似度评分。

---

# 其他建议

## 21. `knowledge_dim` 单标签不适配 L2 跨域定义

**结论：修改。**

建议改成：

```json
"knowledge_dim": {
  "primary": "geo_landmark",
  "secondary": ["history_event", "art_style"]
}
```

否则跨域题只能任意选择一个主类，定位结果会失真。

---

## 22. facet 分类本身存在概念错位

**结论：修改，但优先级低于计分去重。**

`anatomical_fidelity` 放在 Aesthetics 不合理，更接近 Quality/RWF；`physical_logic` 与知识推理高度重合；`noise` 的描述同时混入“细节丰富”和“无噪点”，不是单一维度。

建议先不急于追求支柱名义一致，而是确保：

- 每个 facet 只对应一种错误；
- 不与知识 check 使用相同证据；
- 有固定 rubric；
- 分类版本固定。

外部论文映射也应给出确切版本和字段映射表，否则“以 QIB 为基础”不可审计。

---

# 按严重度排序的 Top 5

## Top 1：示例与禁止泄漏/触发链规则同时失效

东京塔没有在 prompt 中被明确触发，而其余三条 check 又几乎全部被 prompt 明示。该示例会直接教会出题模型把 Alignment 项伪装成知识项。**必须整体废除重写。**

## Top 2：事实来源和 `verified` 契约没有闭合

“图片是唯一事实依据”与图外知识矛盾；`verified=false` 仍能进入正式计分；LLM 自报 `verified=true` 没有证据价值。**必须建立外部核验来源，并禁止未核验条目计分。**

## Top 3：防泄漏规则被错误实现为词面零重叠

这会阻止实体名和必要前提进入 prompt，造成题目不自足，同时又无法真正阻止语义泄漏。**应改成答案属性级防泄漏，并结构化记录 trigger chain。**

## Top 4：知识线与通用线仍存在跨支柱重复计分

仅排除 RWF 不够，`physical_logic`、`anatomical_fidelity`、`material_texture` 等都可能和知识 check 重复。**必须按“是否使用同一视觉证据”决定 scored/diagnostic。**

## Top 5：没有 reject 输出和 judge 输入白名单

没有拒题路径会迫使模型编造检查；若 judge 看见 expected failure、evidence audit 或原图，会产生严重提示污染和参考图偏置。**必须增加 reject schema，并明确评分输入投影。**

---

**最终建议：V4 不应进入正式批量出题。至少先完成上述 Top 5、重写示例、统一反事实规则，并用正式 JSON Schema 和 30~50 道人工验题集做一次端到端 dry run，再考虑投用。**


## 五、评审意见三：openrouter/google/gemini-3.7-flash

<!-- openrouter/google/gemini-3.7-flash · 2026-08-26 · 评审包 data/review_v4_request.md（V4 二轮） -->
<!-- tokens: prompt=6897 completion=5457 · 耗时 45s -->

### 一、 整体判断

**需大改（不可直接投用）**

**核心问题概括**：
V4 版在宏观框架（如 0.7:0.3 权重、五字段 check、熔断机制）上思路清晰，但在**示例设计、字段判分闭环、规则自洽性**三个方面出现了致命的内部脱节。
最严重的硬伤是：**第十一节的示例彻底背叛了第四节的红线与第一节的知识探针定位**——4 条 check 中有 3 条是直接把 prompt 的显式指令换皮塞进 `implicit_checks`，出题 LLM 照此示例学习必将产出大量「把 Alignment 伪装成 Knowledge」的伪探针题。此外，`verified=false` 缺乏判分契约、通用线物理维度与反事实题冲突等结构性漏洞亟待修复。

---

### 二、 逐条问题审查

---

#### 1. 示例严重泄漏与「伪知识点」泛滥（致命缺陷）
* **结论**：**废除并整体重写第十一节示例中的 Check 2、Check 3、Check 4**。
* **理由**：
  * **Check 3** 判定「外星生物占据画面上方约三分之二」，而 Prompt 显式写着「生物占据画面上方约三分之二」；
  * **Check 4** 判定「街道空无一人」，而 Prompt 显式写着「街道空无一人」；
  * **Check 2** 判定「画面色调、颗粒符合 60 年代质感」，而 Prompt 显式写着「胶片颗粒质感，色调偏冷」。
  * 这不仅严重违反**红线 1（禁止泄漏）**，更直接颠覆了评测目标：这些明明是 **Alignment（指令遵循）**，却被包装成 `[solid]` 知识塞入 `implicit_checks`。当这道题的知识线权重高达 70% 时，一个完全没有 60 年代特摄知识、仅靠 prompt alignment 画出「大怪兽+空街道」的模型就能拿到 70% 的知识分。
* **具体改写建议**：
  * **Prompt 改写**（去除泄漏）：
    > 「一张 1960 年代日本特摄电影风格的剧照：一只巨型怪兽现身于东京地标建筑之畔，画面构图展现出人造都市在庞然大物面前的绝对绝望感与破坏痕迹，带有当时典型的胶片拍摄质感。」
  * **implicit_checks 改写**（纯化为真实知识探针）：
    1. *Check 1（地标形制）*：保持东京塔红白相间与桁架镂空结构（检验实体世界知识）。
    2. *Check 2（特摄工艺道具特征）*：怪兽表皮呈现乳胶皮套褶皱或微缩模型质感，而非现代 CG 流体/肌肉解剖真实感（检验 60 年代皮套特摄实拍工艺知识）。
    3. *Check 3（建筑比例尺度）*：怪兽身高达周围 5~8 层居民楼的数倍，与东京塔下层基座比例符合特摄微缩景观惯例（检验特摄比例常识，而非由 prompt 机械指定 2/3）。
    4. *Check 4（年代车辆/设施形制）*：街道废墟中的废弃车辆呈现 1960 年代圆润/方正老爷车特征，无现代流线型汽车（检验时代特征知识）。

---

#### 2. `implicit_checks[].verified=false` 判分契约断裂
* **结论**：**修改第七节判分公式与第三节定义**。
* **理由**：
  * 第三节规定：「涉及 likely 知识：写入 `knowledge` 字段作为条目级 `verified=false`，判分时仅作辅助参考，不作 check 主判据」。
  * 但在第七节判分公式中，计算公式为 $Score = \frac{\sum w_i \cdot \phi(s_i)}{\sum w_i}$，完全没有定义 `verified=false` 时的判分与权重逻辑：
    * 若 Judge 对其打分并计入总分，违反了「不作主判据」；
    * 若直接剔除，原有权重合计 1.0 的归一化离散规则（如 0.3+0.3+0.4）瞬间被打破；
    * 若只打分不计分，出题 LLM 为其分配的 `weight` 毫无意义。
* **具体改写措辞**：
  * 在第七节「知识线」下补充动态权重重分配规则：
    > 「**Likely 知识处理规范**：若某条 check 标记为 `verified: false`，该条仅作为模型在长尾知识上的能力观察项，**不计入知识线总分**。计算知识线得分时，分子分母均剔除该条权重（即由剩余 `verified: true` 的条目按原始权重相对比例重新归一化计算）。若整道题所有 check 均为 `verified: false`，该题直接废弃。」

---

#### 3. 示例中 `facet_tags` 词表分类与配比核验自相矛盾
* **结论**：**修改第十一节示例的配比标注与 tag 分类**。
* **理由**：
  * 示例的 tag 为：`["real_world_scene", "temporal_characteristics", "composition", "lighting_atmosphere"]`。
  * 示例下方的配比核验写道：「RWF 2（主考）+ Aesthetics 2 + Alignment/Quality 0」。
  * **事实错误**：查阅第五节词表，`real_world_scene` 属于 **Alignment（Scene 分类）**，而非 RWF；真正的 RWF 只有 `temporal_characteristics` 1 项。此处示例在自检时把 `real_world_scene` 错算入 RWF，导致出题 LLM 在模仿时产生混淆。
* **具体改写措辞**：
  * 修改示例的配比核验文本：
    > `- facet_tags: ["temporal_characteristics", "character_likeness", "composition", "lighting_atmosphere"]`
    > `- 配比核验：RWF 2（temporal_characteristics, character_likeness）+ Aesthetics 2（composition, lighting_atmosphere）+ Quality 0 + Alignment 0 → 总数 4，完全符合结构配比。`

---

#### 4. 反事实（L3）题目与 Quality 线 `physical_logic` 的判定冲突
* **结论**：**修改第五节 Quality 维度定义，补充反事实题判定豁免**。
* **理由**：
  * 第五节中，Quality 支柱包含 `physical_logic`（物理规律：重力/反射/阴影/稳定性）。
  * 若出一道 L3 反事实题（例如「无重力环境下的倒茶场景」），知识线要求「茶水呈球状漂浮」，但通用线的 VLM Judge 在打 `physical_logic` 这一维度时，会按真实物理世界判定其「重力异常/违背物理」，从而打出 0 分。这导致通用线与反事实知识线直接冲突。
* **具体改写措辞**：
  * 在第五节 `physical_logic` 描述中增加：
    > `physical_logic`：物理规律（重力/反射/阴影方向/稳定性）。**【反事实豁免】**：若题目声明为 L3 反事实题，本维度不以真实世界物理为准，而以 prompt 设定的假设物理系统内部自洽性为准。

---

#### 5. Gate 职责与背景实体锚定的越界风险
* **结论**：**修改第七节 Gate 规范与示例中的 `gate_spec`**。
* **理由**：
  * 示例中 `gate_spec.required_subjects` 填了 `["东京塔"]`。
  * 但在 Prompt 中，主体是「巨型外星生物」，东京塔只是场景背景（「地标之畔」）。
  * 按照规则，Gate 触发条件是「画面主体缺失/主题跑偏，总分封顶 20」。若模型画了一只巨大的怪兽在废墟街道上肆虐（完美符合主题），但由于构图原因东京塔被截在画外，此时触发 Gate 会导致总分直接封顶 20。这把原本属于「Check 1 未命中扣 30 分」的知识项，升级成了「主体缺失封顶 20」的毁灭性惩罚，破坏了评分阶梯。
* **具体改写措辞**：
  * 在第七节「前置门槛（gate）」中补充限定：
    > `required_subjects` 必须且仅能填 **Prompt 显式要求的主体角色/核心对象**；背景知识锚点实体（如作为背景的特定地标、年代道具）禁止填入 `required_subjects`，其呈现正确与否由知识线 checks 独立计分，严禁由 Gate 越权顶格封顶。
  * 示例 `gate_spec` 改为：
    > `"gate_spec": {"required_subjects": ["巨型外星生物/怪兽"], "theme_definition": "60年代日特摄风格的怪兽袭击城市场景"}`

---

#### 6. 红线 1「禁用词规则」过宽导致合法 Prompt 编写受阻
* **结论**：**修改第四节红线 1 的禁用词界定**。
* **理由**：
  * 红线 1 规定：「check 中出现的词不得以同义/构成要素形式出现在 prompt（如 '石像'→'石质'）」。
  * 这在出题执行中会导致 LLM 无法描写场景基底。例如：Check 考察「水面波纹因重力改变呈现特定干涉」，若按此规则，Prompt 里连「水面」、「湖泊」这类承载场景的基础名词都不能写，否则就算「同义/构成要素泄漏」。这会导致出题 LLM 为了避词而写出语焉不详的怪异 Prompt。
* **具体改写措辞**：
  * 将红线 1 细化为：
    > **禁止泄漏（特指解题关键特征）**：check 中作为**判分判据的核心属性、结论性状态与特征词**（如倒影的“对称”、金属的“氧化铜绿”、特定建筑的“红白相间”），不得在 prompt 中明写或近义提示。但场景基础实体名（如“湖泊”、“建筑”、“车辆”）在 prompt 中正常出场不属泄漏。

---

### 三、 按严重度排序的 Top 5 问题清单

| 排名 | 严重度 | 缺陷位置 | 核心缺陷阐述 | 处置结论 |
|---|---|---|---|---|
| **Top 1** | **P0（致命）** | 第十一节（示例） | **示例显式泄漏，伪知识点泛滥**：示例将 Prompt 明写的 3 项要求（生物占比 2/3、街道无人、颗粒冷调）强行包装为 `implicit_checks`，彻底破坏了基准区分度，会导致出题 LLM 批量产出无测试价值的假探针。 | **废除并整体重构示例 Check** |
| **Top 2** | **P1（严重）** | 第七节 vs 第三节 | **`verified=false` 判分契约脱节**：定义了 Likely 知识条目级标注，但在判分公式、权重归一化中完全未定义处理逻辑，导致评分引擎无法执行。 | **补齐动态剔除与权重重归一化算法** |
| **Top 3** | **P1（严重）** | 第七节 vs 示例 | **Gate 越界接管知识判分**：示例将背景地标「东京塔」误设为 Gate 核心主体，导致本应在知识线扣分的局部缺陷被 Gate 误杀并封顶 20 分。 | **严格限定 Gate 仅能绑定显式核心主体** |
| **Top 4** | **P2（中危）** | 第五节 vs 第十一节 | **Facet 示例标注分类自相矛盾**：示例将 Alignment 的 `real_world_scene` 误标为 RWF，给出题 LLM 提供了错误的配比示范。 | **纠正示例 Tag 与配比核验描述** |
| **Top 5** | **P2（中危）** | 第五节 vs 第四节 | **反事实题与画质线物理逻辑死锁**：L3 反事实因果题会在通用线 `physical_logic` 维度被 VLM Judge 按常识物理误判打 0 分。 | **增加反事实题目通用线物理维度豁免条款** |


## 六、任务要求

整合上述三份评审意见，修订并输出完整的 V5 出题 prompt 全文。采纳与否、如何取舍由你自行判断决定；对不采纳的意见请简述理由。输出顺序：先给修改说明（含逐项取舍与理由），再给 V5 全文。
