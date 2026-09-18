# 任务：用 QIB 判准 v2.1 对 pilot 40 图跑一轮盲评（判准换代首战）

## 1. 背景：这是什么任务，为什么做

demiwtg 项目 edit 赛道的 v6.1 pilot 批次：20 道图像编辑题（e001–e020），两个模型已各自作答完毕——Gemini 3.1 Flash Image 与 BAGEL-7B-MoT，共 40 张结果图，全部在本地且已冻结。

这 40 张图此前已用自研 QIB 主口径（三档 0/1/2，φ 映射 0/60/100，判准 v2）判过分，该轮已冻结在 `scores_qib/`。之后判准修订为 **v2.1**（逐档硬判据下沉到九个分型块、渲染只注入本题类型的判据、输出契约不变）。本任务 = **v2.1 首战**：同一批图、同一判官模型（固定 gpt-5.6-sol，与 v2 冻结轮一致），用 v2.1 判分文本把 40 张图重判一轮，得到可与 v2 轮对照的新分数（对照分析由后续分析会话做，**不在本任务内**）。

纪律要点，先读一遍再开工：

- 本轮全部产物只写入 `<round>` = `<pilot>/scores_qib_v21/`；
- `scores_qib/`（v2 冻结轮）、`scores_official/`（官方对照臂）、`scores/`（旧 1–5 审计）**一律不读不碰**——读了会锚定本轮判定，且属红线违规；
- v2.1 与 v2 的分数不可直接互比；你只负责产出 v2.1 的数字，禁止做任何跨轮/跨口径比较；
- 分数文件一律由管线脚本生成，禁止手写手改。

## 2. 已经为你准备好的材料

盲评输入已由上游管线（prepare → render）生成并逐项验证（40 图齐全、哈希登记、两臂同题 prompt 逐字节一致、无占位符残留），你不需要准备任何题目或图片，直接使用：

- **派发清单（每臂一份，各 20 行）**：
  - Gemini 臂：`<round>/gemini/prompts/index.jsonl`
  - Bagel 臂：`<round>/bagel/prompts/index.jsonl`

  每行字段：`qid`（题号）、`edit_type`、`prompt_file`（判官 prompt 文件名，与 index 同目录）、`prompt_sha256`/`instruction_sha256`（已登记，供审计，你不用管）、`candidate_id`（匿名编号）、`before`/`after`（**该臂**判定时用的两张匿名图绝对路径）。
- **判官 prompt**：`<round>/{gemini,bagel}/prompts/eNNN.txt`（各 20 个）。内容 = QIB v2.1 判准按该题类型渲染的成品（四部分：一输入 / 二打分规则（仅本题类型判据）/ 三输出 / 四题面）。同一题两臂的 prompt 文本逐字节相同，差异只在 index 里绑定的图片。判分时必须**逐字**呈现给判官，一个字都不要改。
- **盲评 manifest**：`<round>/{gemini,bagel}/blind_manifest.jsonl`——第 4 节的管线脚本会自动消费，你不需要读懂或打开它。
- `identity_private.jsonl`（模型身份映射）在同目录存在但**全轮次任何人禁读**。

路径缩写（下文通用）：

- `<R>` = `/yzp/zhaozy/yangzepeng/0905/demiwtg`（仓库根）
- `<pilot>` = `<R>/benchmark/edit/synth_v61_pilot`（本批次数据目录）
- `<round>` = `<pilot>/scores_qib_v21`（本轮产物目录）
- `<env>` = `/yzp/zhaozy/yangzepeng/0905/env/bin/python`（项目 Python 解释器）

开工自检（两条命令，各应输出 20）：

```bash
wc -l <round>/gemini/prompts/index.jsonl <round>/bagel/prompts/index.jsonl
ls <round>/gemini/prompts/e*.txt <round>/bagel/prompts/e*.txt | wc -l   # 应为 40
```

## 3. 你的角色：派发者，不是判官

**你自己不给任何一题打分，也不许调用任何外部 API/网关模型代打分。**打分由你派发的子代理完成——判官模型固定用 **gpt-5.6-sol**（与 v2 冻结轮同一判官；派发子代理时显式指定该模型，不要用你当前默认的其他模型），规则如下：

每一次判定，派一个**全新的子代理上下文**，它只收到两样东西：

1. 该臂该题 `eNNN.txt` 的全文（逐字复制；不改写、不压缩、不翻译、不加注释、不套系统提示词）；
2. 两张图，先后顺序固定：BEFORE、AFTER——只用该臂 index 行里的 `before`/`after` 两个路径，不要回溯任何原始图片目录。

除此之外**零附加**——不要向子代理解释任务背景，不要粘贴任何其他文件的内容。全新上下文意味着判官物理上不知道图和模型的对应关系、不知道别的题判了几分；盲评由结构保证，不靠自觉。

判官的回复是**一个裸 JSON 对象**（v2.1 输出契约，无其他文本），形如：

```json
{
  "validity": {"status": "ok", "detail": ""},
  "raw_dimensions": [
    {"label": "<维度一名称>", "tier": 2, "reason": "<具体 BEFORE/AFTER 可见证据>"},
    {"label": "<维度二名称>", "tier": 1, "reason": "<具体可见证据>"},
    {"label": "<维度三名称>", "tier": 1, "reason": "<具体可见证据>"}
  ],
  "observations": {
    "explicit_edit": [], "necessary_consequences": [],
    "preservation": [], "artifacts": []
  },
  "critical_failures": [],
  "confidence": "high"
}
```

收到回复后**原样**保存（不改写、不美化、不补字段、不去掉围栏；即使带 ```json 围栏或夹了多余文字也原样存，管线会取第一个可解码的 JSON 对象）：

- Gemini 臂 → `<round>/gemini/raw/eNNN.txt`
- Bagel 臂 → `<round>/bagel/raw/eNNN.txt`

（目录不存在就先创建。）

## 4. 分步执行

### Step 1 派发判定（共 40 次）

两臂各遍历自己的 index.jsonl 20 行，每行派一个子代理（prompt 用该臂 `prompts/<prompt_file>`，图用该行的 `before`/`after`）。两臂互不影响，先后顺序、并发与否随意。

异常情况处理：

- **判官正常返回 JSON 但 `validity.status` 不是 `ok`**（`invalid_question` / `model_failure` / `judge_unscorable`）：原样保存，管线会按契约处理（invalid_question 会在 compare 步成对剔除并进复审名单）；把该 qid 与状态记入最终报告。
- **图片无法解码、子代理拒绝作答、拿不到可解析 JSON**：在该臂 raw/<qid>.txt 写入如下固定 JSON（三个 `label` 从该题 eNNN.txt「二、打分规则」部分逐字照抄三个维度名；detail 必须写明原因），并把该 qid 记入最终报告：

  ```json
  {"validity": {"status": "model_failure", "detail": "<拒绝/空图/不可解码，写明哪种>"},
   "raw_dimensions": [
     {"label": "<维度一名称>", "tier": 0, "reason": "<同 detail>"},
     {"label": "<维度二名称>", "tier": 0, "reason": "<同 detail>"},
     {"label": "<维度三名称>", "tier": 0, "reason": "<同 detail>"}],
   "observations": {"explicit_edit": [], "necessary_consequences": [], "preservation": [], "artifacts": []},
   "critical_failures": [],
   "confidence": "low"}
  ```

- **基础设施故障**（子代理崩溃、超时、网络）：同一题、同样内容重试一次；仍失败就停下来报告卡在哪一题——不要跳过，更不要编造分数。

### Step 2 组装标准分数行（ingest，两条命令）

raw 里是判官裸输出；标准分数行的身份字段（qid/candidate_id/edit_type/三哈希/judge）与全部换算（φ 映射、钳制、均值）由 ingest 从 manifest 照抄并按固定公式算出，全自动。**禁止手写或修改 parts。**

```bash
<env> <R>/benchmark/edit/eval_codex_score.py ingest --format json \
  --judge gpt-5.6-sol-built-in \
  --manifest <round>/gemini/blind_manifest.jsonl \
  --raw-dir <round>/gemini/raw \
  --out-dir <round>/gemini/parts
```

Bagel 臂同一条命令，把路径里两处 `gemini` 换成 `bagel`。`--judge` 固定填 `gpt-5.6-sol-built-in`，不要改动。ingest 若报校验错误（维度名不匹配、tier 非法等），按它提示的 qid 检查对应 raw：判官输出本身有问题就在同配置下重判该题、覆盖 raw，然后重跑 ingest（修 raw，不改 parts）。

### Step 3 聚合（aggregate，两条命令）

```bash
<env> <R>/benchmark/edit/eval_codex_score.py aggregate \
  --questions <pilot>/questions.jsonl \
  --manifest <round>/gemini/blind_manifest.jsonl \
  --scores-dir <round>/gemini/parts \
  --out-dir <round>/gemini
```

Bagel 臂同样换路径。产物是各目录下 `scores.jsonl`（逐题终分）与 `report.json`（聚合报表，official 钳后口径）；`report.json` 的 `overall` 就是该模型 QIB v2.1 总分（0–100）。aggregate 报校验失败的处理同 Step 2：修 raw 重跑，禁改产物。

### Step 4 配对比较（compare，一条命令）

```bash
<env> <R>/benchmark/edit/eval_codex_score.py compare \
  --questions <pilot>/questions.jsonl \
  --left-scores <round>/gemini/scores.jsonl \
  --right-scores <round>/bagel/scores.jsonl \
  --left-name gemini-3.1-flash-image --right-name BAGEL-7B-MoT \
  --out-dir <round>/paired
```

产物 `paired/report.json`：两模型均分、分差 Δ(G−B)、W/T/L（Gemini 视角胜/平/负）及分组对比。

## 5. 禁止事项（针对你这个调度层）

为保判准换代实验干净，以下几条要守住：

- 不读、不引用、不转述、不粘贴给子代理：`scores_qib/`（v2 冻结分数——本轮就是要防它的锚定）、`scores_official/`、`scores/`、任何 `identity_private.jsonl`、`questions.jsonl`（判官需要的题面已全部嵌在 eNNN.txt 内；Step 3/4 命令里的 `--questions` 只是传给脚本的参数，你自己不要打开它）、`<pilot>/gemini/` 与 `<pilot>/bagel/`（原始出图/响应目录——派发只用 index 里的匿名图路径）、HANDOFF*/TASK*/AGENTS.md/notebook 等一切文档；
- 分数文件（parts/、scores.jsonl、report.json、paired/）只能由管线脚本生成，禁止任何手改；修复路径唯一：改 raw → 重跑 ingest → aggregate；
- 除新建 raw/ 与上述脚本产物外，不修改、不删除任何既有文件；发现输入异常（图片缺失、index 行数 ≠20、两臂同题 prompt_sha256 不一致等）就停下来报告；
- 不做任何跨轮次、跨口径、跨判准版本的比较或评论——比较是分析会话的事。

## 6. 完成标准与回复格式

完成标准：

1. 40 份 raw 齐全（两臂各 20）；
2. ingest×2、aggregate×2、compare×1 全部零报错退出；
3. 两份 `scores.jsonl` 各 20 行，每行 `judge` = `gpt-5.6-sol-built-in`、`schema` = `edit-codex-v2-qib`；
4. `<round>/paired/report.json` 已产出。

回复只需报告（判准版本标注：**QIB v2.1**）：

- 两模型 `report.json` 的 `overall`（0/60/100 钳后口径）与 `n_valid`/`n_invalid`；
- paired 的 W/T/L（G 视角）与 Δ(G−B)；
- 异常事件清单：代写 model_failure 的题、invalid_question / judge_unscorable 的题、重试记录（没有就写"无"）。

不要展开逐题理由，不要与 v2/官方臂/旧分做任何对比。
