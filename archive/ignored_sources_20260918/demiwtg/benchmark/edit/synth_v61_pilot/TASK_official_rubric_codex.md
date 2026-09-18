# 任务：用 ImgEdit 官方评分标准补一轮盲评（对照实验）

## 1. 背景：这是什么任务，为什么做

demiwtg 项目的 edit 赛道在建设一个图像编辑评测基准：从自有图池出了 20 道图像编辑题（v6.1 pilot 批次），让两个模型作答——Gemini 3.1 Flash Image 与 BAGEL-7B-MoT。两模型已各自生成完 20 张编辑结果图（共 40 张，都在本地）。

这 40 张图已经用我们自研的"QIB 主口径"（0/60/100 三档制）判过分，结果已冻结。**本任务不碰主口径**，而是做一个对照实验：用 ImgEdit-Bench（公开图像编辑基准，论文 arXiv:2505.20275）的**官方评分 rubric 原文**（1–5 分制）把同一批图再判一轮。目的有两个：

1. 量化"评分协议本身"的影响——同一批图、同一个判官模型（与 QIB 主口径两轮一致，固定 gpt-5.6-sol），只换 rubric 文本，看分数和排序会动多少；
2. 得到一把可与 ImgEdit 公开口径对话的尺子。

本实验的全部产物只写入 `scores_official/` 目录，与主口径目录 `scores_qib/` 物理隔离，互不读写。

## 2. 已经为你准备好的材料

盲评输入已由上游管线生成并逐一验证过（图片存在、哈希登记），你不需要准备任何题目或图片，直接使用：

- **派发清单**：`<pilot>/scores_official/prompts/index.jsonl`，20 行、每行一题。字段含义：
  - `qid`——题号（e001…e020）；
  - `prompt_file`——该题判官 prompt 的文件名；
  - `before`——BEFORE 原图的绝对路径；
  - `after_gemini_arm` / `after_bagel_arm`——两个模型各自的 AFTER 结果图绝对路径。
- **判官 prompt**：`<pilot>/scores_official/prompts/eNNN.txt`（共 20 个文件）。内容已完整写好 = ImgEdit 官方 rubric 逐字原文 + 该题编辑指令（已嵌入）。判分时必须**逐字**呈现给判官，一个字都不要改。
- **盲评 manifest**：`<pilot>/scores_official/gemini/blind_manifest.jsonl` 与 `<pilot>/scores_official/bagel/blind_manifest.jsonl`（各 20 行，含 qid、匿名编号 candidate_id、edit_type 与三类哈希）。你不需要读懂它的字段——后面第 3、4 步的管线脚本会自动消费它。

路径缩写（下文通用）：

- `<R>` = `/yzp/zhaozy/yangzepeng/0905/demiwtg`（仓库根）
- `<pilot>` = `<R>/benchmark/edit/synth_v61_pilot`（本批次数据目录）
- `<env>` = `/yzp/zhaozy/yangzepeng/0905/env/bin/python`（项目 Python 解释器）

## 3. 你的角色：派发者，不是判官

**你自己不给任何一题打分，也不许调用任何 API/网关模型代打分。**打分由你派发的子代理完成——**判官模型固定用 gpt-5.6-sol**（与 QIB 主口径两轮同一判官；派发子代理时显式指定该模型，不要用你当前默认的其他模型），规则如下：

每一次判定，派一个**全新的子代理上下文**，它只收到两样东西：

1. 该题 `eNNN.txt` 的全文（逐字复制；不改写、不压缩、不翻译、不加注释、不套系统提示词）；
2. 两张图，先后顺序固定：BEFORE、AFTER。

除此之外**零附加**——不要向子代理解释任务背景，不要粘贴任何其他文件的内容。这样做的原因：全新上下文意味着判官物理上不可能知道"这张图出自哪个模型""别的题判了几分"，盲评由结构保证，而不是靠自觉遵守。

子代理的回复会是 ImgEdit 官方的响应格式，形如：

```
Brief reasoning: Target fully replaced; minor edge artifacts remain.
Prompt Compliance: 4
Visual Naturalness: 4
Physical & Detail Integrity: 3
```

即一句不超过 20 词的理由，加三行"维度名: 1–5 整数分"（三个维度名随题型不同，prompt 里已写死，判官自会照此输出）。收到格式正常的回复后，把它**原样**保存到对应文件（不要改写、不要加自己的批注；目录不存在就先创建）：

- 判的是 Gemini 的图 → `<pilot>/scores_official/gemini/raw/eNNN.txt`
- 判的是 Bagel 的图 → `<pilot>/scores_official/bagel/raw/eNNN.txt`

## 4. 分步执行

### Step 1 派发判定（共 40 次）

遍历 index.jsonl 的 20 行；每行派两个子代理：一个 Gemini 臂（AFTER 用 `after_gemini_arm` 那张图），一个 Bagel 臂（AFTER 用 `after_bagel_arm`）。两臂互不影响，先后顺序随意。

异常情况处理：

- 子代理表示图片无法打开/解码，或拒绝作答：对应 raw 文件只写一行 `MODEL_FAILURE`（管线认识这个标记，会按模型失败记录，不影响其他题）；
- 子代理崩溃、超时等基础设施问题：同一题、同样内容重试一次；仍失败就停下来报告卡在哪一题——不要跳过，更不要编造分数。

### Step 2 组装标准分数行（ingest，两条命令）

raw 文件里存的是判官的"裸分数"——只有一句理由加三个数字，别的信息都没有。而最终的分数文件（part_*.jsonl）每行还要带上：这是哪道题（qid）、对应哪张匿名图（candidate_id）、编辑类型、三个文件哈希、判官标识，以及按固定规则换算出的分数。这些字段不含任何判断，全部由 ingest 脚本从 manifest 照抄、按规则算出，全自动。**分数文件一律由脚本生成，禁止手写或手动修改。**

```bash
<env> <R>/benchmark/edit/eval_codex_score.py ingest --format imgedit \
  --judge gpt-5.6-sol-built-in-imgedit-official \
  --manifest <pilot>/scores_official/gemini/blind_manifest.jsonl \
  --raw-dir <pilot>/scores_official/gemini/raw \
  --out-dir <pilot>/scores_official/gemini/parts
```

Bagel 臂同一条命令，把路径里的两处 `gemini` 换成 `bagel`。`--judge` 是分数行里的判官标识，固定填 `gpt-5.6-sol-built-in-imgedit-official`，不要改动。ingest 若报校验错误，按它提示的 qid 检查对应 raw 文件，修正后重跑（修 raw，不改 parts）。

### Step 3 聚合（aggregate，两条命令）

```bash
<env> <R>/benchmark/edit/eval_codex_score.py aggregate \
  --questions <pilot>/questions.jsonl \
  --manifest <pilot>/scores_official/gemini/blind_manifest.jsonl \
  --scores-dir <pilot>/scores_official/gemini/parts \
  --out-dir <pilot>/scores_official/gemini
```

Bagel 臂同样换路径。产物是各目录下 `scores.jsonl`（逐题终分）与 `report.json`（聚合报表）；`report.json` 里的 `overall` 就是该模型在官方 1–5 口径下的总分均值。

### Step 4 配对比较（compare，一条命令）

```bash
<env> <R>/benchmark/edit/eval_codex_score.py compare \
  --questions <pilot>/questions.jsonl \
  --left-scores <pilot>/scores_official/gemini/scores.jsonl \
  --right-scores <pilot>/scores_official/bagel/scores.jsonl \
  --left-name gemini-3.1-flash-image --right-name BAGEL-7B-MoT \
  --out-dir <pilot>/scores_official/paired
```

产物 `paired/report.json`：`overall` 里有两模型均分、分差，以及 W/T/L（按每题胜负计数，Gemini 视角的胜/平/负）。

## 5. 禁止事项（针对你这个调度层）

你的上下文有全仓权限，为保实验干净，以下几条要守住：

- 不读、不引用、不转述：`scores_qib/`（主口径分数）、`scores/`（更早的历史分数）、任何 `identity_private.jsonl`（模型身份映射）、HANDOFF 与其他 TASK 文件；
- 不把上述任何内容粘贴给子代理；
- 除新建 raw/ 与脚本产物外，不修改、不删除任何既有文件；发现输入异常（图片缺失、manifest 行数不对等）就停下来报告。

## 6. 完成标准与回复格式

完成标准：40 份 raw 文件齐全；ingest×2、aggregate×2、compare×1 全部零报错退出；两份 `scores.jsonl` 的每行 `judge` 字段均为 `gpt-5.6-sol-built-in-imgedit-official`。

回复只需报告：两个 report.json 的 `overall`（1–5 口径）与 `n_invalid`、paired 的 W/T/L。不要展开逐题理由。
