# 任务：Bagel 候选 QIB 盲评 + 聚合对账（v6.1 pilot，20 题）

更新时间：2026-09-05。执行者：Codex（判分必须用你自己的内置多模态模型直接看图）。

## 0. 硬性规则

1. **只用内置模型判分**：逐题打开 BEFORE/AFTER 两张图，由你自己的视觉能力落档。
   **禁止**为判分调用任何 HTTP API / 网关 / 其他模型（包括 127.0.0.1:4000、
   127.0.0.1:4001、OpenRouter 等）。运行本任务里的 `eval_codex_score.py`
   aggregate/compare 是允许的——那是纯本地校验聚合脚本，不调 API。
2. **盲评纪律，只允许读取**：
   - 本文件
   - 判分协议：`/yzp/zhaozy/yangzepeng/0905/demiwtg/benchmark/edit/codex_score_prompt_edit_v2.md`
   - 盲评清单：`.../scores_qib/bagel/blind/blind_manifest.jsonl`（仅 qid、
     candidate_id、edit_type、edit_instruction、before/after 路径、三类哈希）
   - 图片：`.../scores_qib/bagel/blind/inputs/{before,after}/cNNN.png`
3. **禁止读取**（读了即盲评作废）：
   - `.../scores_qib/bagel/blind/identity_private.jsonl`
   - `.../scores_qib/gemini/` 整个目录
   - `.../scores/` 整个目录（旧 1–5 诊断分）
   - `questions.jsonl`、`plan.jsonl` 及其 reasoning/evidence/level/suite 字段
   - `HANDOFF.md`
   - 任何暴露候选模型身份或此前分数的文件
4. **每题独立判**：一次只看一对图，不得跨题、跨候选比较；判分依据只有
   BEFORE、AFTER、`edit_instruction`、`edit_type`。
5. 路径根：仓库根 = `/yzp/zhaozy/yangzepeng/0905/demiwtg`，
   pilot 目录 = `<仓库根>/benchmark/edit/data/synth_v61_pilot`（下文以 `<pilot>` 代替）。
   manifest 内 before/after 已是本机绝对路径，可直接打开。

## 1. Step 1 — 盲评落盘（写 3 个 part 文件）

按协议 v2 逐题判分（20 题，manifest 全部行），输出目录：

```
<pilot>/scores_qib/bagel/blind/parts/
├── part_a.jsonl   # e001–e007，7 行
├── part_b.jsonl   # e008–e014，7 行
└── part_c.jsonl   # e015–e020，6 行
```

每行一个 JSON 对象（JSONL，UTF-8，一行一题），schema 见协议 §六。
`aggregate` 强制校验以下契约，任何一处不符整批报错，请严格遵守：

- `"schema": "edit-codex-v2-qib"`（20 行必须一致）
- `qid` / `candidate_id` / `edit_type` **逐字符等于** manifest 对应行（qid 不得缺、不得重复）
- `inputs` 三哈希（source/output/instruction_sha256）照抄 manifest 对应行
- `raw_dimensions` 恰好 3 个元素，`key` 依次为 `d1`/`d2`/`d3`，`label` 必须与
  协议 §三 该 edit_type 的三个维度名**逐字符一致**（如 replace →
  `Prompt Compliance` / `Visual Naturalness` / `Physical & Detail Integrity`）
- `tier` 只能是整数 0/1/2；`mapped` 必须等于 φ 映射：0→0、1→60、2→100
- `reason` 写具体 BEFORE/AFTER 可见证据（协议 §五 硬判据；tier 2 必须给出可定位
  的超常执行证据，拿不准 60 与 100 时给 60）
- `validity.status` ∈ {ok, model_failure, judge_unscorable, invalid_question}；
  `confidence` ∈ {high, medium, low}
- `judge` 填 `<你的内置模型ID>-built-in`（沿用 Gemini 轮 `gpt-5.6-sol-built-in`
  的命名惯例，按你实际模型写）
- `raw_tier_mean` / `mapped_dimensions` / `official_tiers`（d2、d3 钳制到 ≤ d1）/
  `official_dimensions` / `official_total`（钳制后三映射分均值）按协议填写；
  aggregate 会复算覆盖，但请自行按钳制规则算对，用于自查
- `observations` 四组、`critical_failures` 按协议填（可空数组）

注意：`background` 类按像素保持的严格语义判（协议 §四）；`compose` 恰好两个
子操作，只完成一个 d1 必须 Fail。

## 2. Step 2 — 聚合校验（aggregate）

3 个 part 文件全部落盘后执行（`<env>` = `/yzp/zhaozy/yangzepeng/0905/env/bin/python`）：

```bash
<env> <仓库根>/benchmark/edit/eval_codex_score.py aggregate \
  --questions <pilot>/questions.jsonl \
  --manifest <pilot>/scores_qib/bagel/blind/blind_manifest.jsonl \
  --scores-dir <pilot>/scores_qib/bagel/blind/parts \
  --out-dir <pilot>/scores_qib/bagel
```

预期产物：`scores_qib/bagel/scores.jsonl`（20 行）+ `scores_qib/bagel/report.json`
（stdout 打印 report）。若报契约错误（label 不符 / tier 非法 / candidate 不匹配 /
缺题），修 part 文件后重跑，直到零错误通过。

## 3. Step 3 — 配对对账（compare，左 Gemini、右 Bagel，W/T/L 为 Gemini 视角）

```bash
<env> <仓库根>/benchmark/edit/eval_codex_score.py compare \
  --questions <pilot>/questions.jsonl \
  --left-scores <pilot>/scores_qib/gemini/scores.jsonl \
  --right-scores <pilot>/scores_qib/bagel/scores.jsonl \
  --left-name gemini-3.1-flash-image \
  --right-name BAGEL-7B-MoT \
  --out-dir <pilot>/scores_qib/paired
```

预期产物：`scores_qib/paired/paired_scores.jsonl` + `scores_qib/paired/report.json`。
此步只准按命令原样跑，禁止打开读取任何一侧 scores 内容做人工"分析"——配对结论
以 report.json 机器输出为准。

## 4. Step 4 — 重跑审阅 notebook（补全 Bagel 卡片）

内核 `demiwtg` 已注册（指向 `<env>`），nbconvert 已装：

```bash
/yzp/zhaozy/yangzepeng/0905/env/bin/jupyter nbconvert --to notebook --execute --inplace \
  '<仓库根>/benchmark/edit/results_review_v61.ipynb' \
  --ExecutePreprocessor.kernel_name=demiwtg \
  --ExecutePreprocessor.timeout=600
```

跑完后确认：无 cell 报错、Bagel 卡片不再是占位（展示分数与理由）、paired
统计出现在 notebook 输出中。**不要手动编辑 notebook 的判分数字**，一切以
scores.jsonl / report.json 为准。

## 5. 完成标准（自查清单）

- [ ] `parts/part_{a,b,c}.jsonl` = 7+7+6 行，每行过上述契约
- [ ] aggregate 零错误，`bagel/report.json` 的 n=20、n_invalid 如实记录
- [ ] compare 零错误，`paired/report.json` 生成
- [ ] notebook 原地重跑成功、Bagel 卡片补全、无报错 cell
- [ ] 全程未读禁读文件、未调用外部 API 判分

完成后回复：三步命令的输出摘要（bagel report 的 overall/by_level/by_edit_type/by_suite
+ paired 的 W/T/L）即可，不要在回复里展开逐题理由。
