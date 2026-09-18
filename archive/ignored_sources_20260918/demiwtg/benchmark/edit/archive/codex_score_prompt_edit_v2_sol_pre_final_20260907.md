# edit QIB 判分编排协议（v2 · codex 任务书契约，不入判官输入）

**角色分工**：

| 文件 | 职责 | 读者 |
|---|---|---|
| `prompts/judge_prompt_edit_qib_v2.1.md` | 现行判官 prompt 文本的权威源（判分准则 + 判官输出格式；九类判据在模板块外的 TYPE 块内，渲染时只注入本题类型）。前一版模板 `judge_prompt_edit_qib_v2.md`（pilot 冻结两轮所用判准）已删除，审计凭 `synth_v61_pilot/scores_qib/prompts/` 的渲染产物与 index.jsonl sha256 登记保存 | 判官（内置多模态模型）：经 render 逐题渲染后逐字呈现 |
| 本文件 | 编排协议：判分全流程与各步约束，通用可复用，不含批次状态 | codex（编排者）：本文件内容永不进入判官输入 |
| `eval_codex_score.py` | 判分管线：prepare / render / ingest / aggregate / compare 五个子命令 | 本地命令行 |

职责边界：判官的职责只有看图给三个维度各落一个档位（tier ∈ {0, 1, 2}）并写明证据。φ 映射（0→0、1→60、2→100）、钳制计算（第二、三维档位不得高于第一维）、分数行字段拼装，全部由编排侧脚本确定性完成；这些规则不向判官展示，原因是判官知道钳制规则时，可能据此反推期望结果并调整原始落档。判官 prompt 文件只含标题与模板块，不写渲染机制与维护者注；维度名称的唯一权威源 = eval_codex_score.py 的代码表 EDIT_DIMS（ingest/aggregate 依它校验），判官文本中的维度名由各分型判据块头承载，不在任何文档维护快照副本。

**全轮次约束**（共五条，从开轮到收官始终生效）：

1. **判官固定**：判官 = codex 子代理的内置多模态模型，模型固定为 gpt-5.6-sol——本赛道每个判分轮次使用同一判官模型，保证跨轮次、跨协议的对比不混入判官模型差异；禁止调用任何外部 API 或网关代判。
2. **隔离**：判官输入只有该题渲染出的 prompt 与两张图，盲评由这一结构保证，不依赖执行者自律。以下内容不出现在判官输入中，编排者在判分轮次也不得读取、转述或粘贴给子代理：出题人元数据（caption、desc、素材清单、reasoning、evidence、level、suite）、identity_private.jsonl、另一候选模型的结果、任何已退役口径的分数、本文件与任何任务书/交接文档。编排者判分轮的只读范围：render 产物（prompts/eNNN.txt 与 index.jsonl）、blind_manifest.jsonl 对应行、inputs/ 下的匿名图、raw/。
3. **分数文件只能由管线脚本生成**：parts/part_*.jsonl、scores.jsonl、report.json 全部由脚本写出，禁止任何形式的手工修改。
4. **口径统一**：正式结论（overall、分组均值、W/T/L）一律采用 official 钳后口径；mapped 未钳分只用于审阅 notebook 的诊断分析，不进入正式结论。
5. **冻结与审计**：本轮主分冻结后，才可以另开一遍读取出题 reasoning/evidence 的题目审计，审计结论不得回写主分。已退役的判分口径（如 v1 的 1–5 分制）只保留供审计，禁止线性换算混入现行结论；并行的对照实验臂（如官方 rubric 臂）与主口径物理隔离，目录互不读写。

## 流程（六步，按顺序执行；各步的规则写在各自步骤内）

### 步骤 1 prepare——盲评输入匿名化与清单生成

把每题的原图与模型结果图拷贝到 inputs 目录并改名为匿名编号（cNNN），生成 blind_manifest.jsonl：每题一行，含题号 qid、匿名编号 candidate_id、题型 edit_type、编辑指令、两张图路径、源图/结果图/指令文本三个文件的 sha256 指纹。"哪个匿名编号属于哪个模型"的对应关系单独写入 identity_private.jsonl——判分轮全程无人读取（全轮次约束第 2 条）。

```bash
eval_codex_score.py prepare --questions Q.jsonl --responses R --out-dir <blind_dir>
```

### 步骤 2 render——判官 prompt 逐题渲染

按每题 edit_type 从 `judge_prompt_edit_qib_v2.1.md`（--template 可指定其他模板）渲染出该题的判官 prompt eNNN.txt，四部分结构：一、输入（两图 + 题面说明）→ 二、打分规则（**只注入本题 edit_type 的逐档判据**，其余八类不进入判官输入）→ 三、输出（JSON 契约与字段语义）→ 四、题面（edit_type、edit_instruction 与"下面是 BEFORE 与 AFTER 两张图"收尾句）。每份产物的 sha256 登记进 index.jsonl（带 --manifest 时，index 同时附该题的盲评图片路径）。渲染前校验模板块唯一性、九类齐全、占位符唯一，校验不过拒绝执行。

每个新判分轮次必须先完成本步、再逐题逐字呈现；未渲染判官 prompt 的轮次，产物无效。

```bash
eval_codex_score.py render --questions Q.jsonl --out-dir <blind_dir>/prompts \
    [--manifest <blind_dir>/blind_manifest.jsonl]
```

### 步骤 3 派发——子代理逐题判定

每题开一个全新的子代理上下文作为判官，只给两样输入、不附加任何其他内容：eNNN.txt 全文（逐字；不改写、不压缩、不翻译、不加注释、不套系统提示词）+ BEFORE、AFTER 两张图（按此顺序）。判官返回的原始输出（只含各维档位与证据，无其他字段）原样存入 `raw/<qid>.txt`。一题一个子代理，不做跨题比较；编排者不亲自判任何一题。

判官返回的 validity.status 四种取值及处理方式：

- `ok`——正常判定；
- `model_failure`（模型拒绝、空图、图片不可解码）——三维记 0，计入 overall；
- `invalid_question`（题目本身无效：BEFORE 中目标不可判、题面多义）——同一 qid 的各模型结果成对剔除（compare 自动执行），题目进入复审名单；
- `judge_unscorable`——无法凭可见事实落档。

网络、渲染崩溃等基础设施故障不属于上述状态：同一题按同配置重试一次，仍失败则停下报告，不计为模型失败、不得跳过。

### 步骤 4 ingest——组装标准分数行

ingest 解析 raw 中的判官原始输出，自动补全一批不含任何判断的固定字段，写入 `parts/part_*.jsonl`（按 qid 顺序连续分段，--split 可分成多个文件）；`--judge` 填固定值 `gpt-5.6-sol-built-in`。

```bash
eval_codex_score.py ingest --format json --judge gpt-5.6-sol-built-in \
    --manifest M.jsonl --raw-dir raw/ --out-dir parts/ [--split 7,7,6]
```

#### 附表：一行标准分数的字段构成（ingest 依此组装并校验）

判官原始输出只含两类内容：每个维度的档位（0/1/2）与证据文字。ingest 在它之外补全五组机械字段：

| 字段组 | 具体字段 | 字段来源 |
|---|---|---|
| 格式版本 | schema = "edit-codex-v2-qib" | 固定值，全批一致 |
| 题目身份 | qid、candidate_id、edit_type | 从 blind_manifest.jsonl 对应行逐字照抄 |
| 文件指纹 | 源图、结果图、指令文本三个 sha256 | 从 blind_manifest.jsonl 对应行逐字照抄 |
| 判官登记 | judge = `gpt-5.6-sol-built-in` | 固定值（全轮次约束第 1 条） |
| 分数换算 | mapped = 档位换算分（0→0、1→60、2→100）；official_tiers = 钳制后档位（第二、三维不得高于第一维）；official_dimensions 与 official_total = 钳制后三维换算分及其均值；raw_tier_mean、mapped_dimensions = 两个派生均值 | 按固定公式计算 |

前四组是抄写与固定值填充，最后一组是算术计算——没有任何字段包含新的判断，因此本步只能由脚本完成。ingest 每组装一行立即校验一行：三个维度齐全、维度名与代码表 EDIT_DIMS 逐字一致、档位是 0/1/2 整数、照抄内容与 manifest 一致。键名映射：判官输入与输出只使用维度名称与"维度一/维度二/维度三"；d1/d2/d3 是管线存储层的位置键，由 ingest 按输出顺序为三个维度映射补全，分数行与 aggregate 的存储契约使用位置键、保持不变。

校验失败的唯一修复路径：修正 raw/<qid>.txt（判官输出本身有问题就在同配置下重判该题）→ 重跑 ingest → aggregate；禁止手改 parts（全轮次约束第 3 条）。

（`--format imgedit` 是本步的官方 rubric 对照臂变体：解析"Brief reasoning + 三行维度分数"的文本响应，对应 schema edit-codex-v1（1–5 分制），见该臂编排协议。）

### 步骤 5 aggregate——复算校验与聚合

aggregate 逐题复核分数行字段、重算换算与钳制（official = [第一维档位, min(第二维档位, 第一维档位), min(第三维档位, 第一维档位)]，official_total = 钳制后三维换算分的均值），与步骤 4 的计算构成两道相互独立的校验；全部通过后产出 scores.jsonl（逐题终分）与 report.json（overall 与分组均值，均为 official 钳后口径）。校验失败的处理与步骤 4 相同：修 raw 重跑，禁改产物。

```bash
eval_codex_score.py aggregate --questions Q.jsonl --manifest M.jsonl \
    --scores-dir <parts/> --out-dir <model_dir>
```

### 步骤 6 compare——同题配对比较

把两个模型在同一题上的 official_total 直接比较：左方高记 W（胜）、相等记 T（平）、右方高记 L（负）；产出 paired_scores.jsonl 与 report.json（两模型均分、分差、W/T/L 及分组对比）。任一模型该题 validity 非 ok，则该题成对排除并记入 excluded。

```bash
eval_codex_score.py compare --questions Q.jsonl \
    --left-scores A/scores.jsonl --right-scores B/scores.jsonl \
    --left-name <A模型名> --right-name <B模型名> --out-dir <paired_dir>
```

## 官方 rubric 对照臂（本协议的变体参数）

对照臂走完全相同的六步流程与五条全轮次约束（其判分轮的禁读清单在本协议第 2 条基础上追加 `scores_qib/` 主口径分数），仅以下参数不同：

| 项 | QIB 主口径 | 官方 rubric 对照臂 |
|---|---|---|
| 判官 prompt 模板 | `judge_prompt_edit_qib_v2.1.md` | `judge_prompt_edit_imgedit_official.md`（ImgEdit 官方原文逐字内置，render 前与契约 json 逐字节核对） |
| 刻度 | 三档 0/1/2，φ 映射 0/60/100 | 官方 1–5 整数分 |
| 判官输出 | JSON（各维档位 + 逐维证据 + validity 四态 + observations + confidence） | 官方文本格式：Brief reasoning（≤20 词）+ 三行"维度名: 分数" |
| ingest | `--format json --judge gpt-5.6-sol-built-in` | `--format imgedit --judge gpt-5.6-sol-built-in-imgedit-official` |
| 分数行 schema | edit-codex-v2-qib | edit-codex-v1（`MODEL_FAILURE` 标记记 model_failure、三维 1/1/1） |
| 钳制可见性 | 判官不见钳制，管线后算（防反向锚定） | 官方钳制句在判官文本内（官方口径组成部分，逐字保留） |
| 产物目录 | `scores_qib/` | `scores_official/`（与主口径物理隔离，互不读写） |

臂特有规则：

- **定位**：对照实验——同一批图、同一判官（gpt-5.6-sol），只更换判分 rubric 文本，量化判分协议对分数与排序的影响，并保留一把可与 ImgEdit-Bench 公开评测对话的尺子。
- **口径禁令**：1–5 与 0/60/100 禁止线性互换；跨臂比较只允许秩相关、Fail 判定一致率等分布层面的分析；臂的结果不进入主口径正式结论。
- **来源与完整性登记**：判官文本官方出处 = ImgEdit-Bench（arXiv:2505.20275，github.com/PKU-YuanGroup/ImgEdit）；入库契约 `edit_score_prompts.json`（sha256 f9468dc848776ee0a6c2e2c31e678c7224e9cb61b013747c8c52d4a05627b5a0，随代码入 git）与判官 md 的九个 OFFICIAL_TYPE 块为逐字节一致的双副本，render 渲染前核对；该核对能发现单侧改写，不能发现双副本被同时改写，来源与官方发布文本的一致性由 git 提交历史与 sha256 登记保证。

## 与批次任务书的关系

本协议是通用契约；每个批次的执行任务书（如 `synth_v61_pilot/TASK_official_rubric_codex.md`）由本协议派生，负责实例化真实路径与批次规模，冷启动执行者只看任务书即可完成轮次。

