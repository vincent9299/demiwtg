# 任务：ImgEdit 官方 rubric 原文盲评（实验臂，两模型 × 20 题）

判分用你自己的内置多模态模型直接看图，禁止调用任何 API/网关。产物只落
`scores_official/`，与主口径 `scores_qib/` 互不读写。

路径：`<R>`=`/yzp/zhaozy/yangzepeng/0905/demiwtg`，`<pilot>`=`<R>/benchmark/edit/synth_v61_pilot`，`<env>`=`/yzp/zhaozy/yangzepeng/0905/env/bin/python`。

## 1. 盲评纪律

只准读：本文件、`<pilot>/scores_official/prompts/`（eNNN.txt + index.jsonl）、
`<pilot>/scores_official/<model>/blind_manifest.jsonl` 及其 `inputs/{before,after}/` 图片。

禁读：`identity_private.jsonl`、`scores_qib/`、`scores/`、`questions.jsonl`、
`plan.jsonl`、HANDOFF、其他 TASK 文件、notebook、`edit_score_prompts.json`
（判分文本已物化进 prompts/，不需要也不许回读源文件）。判 Gemini 臂与
Bagel 臂相互隔离，一题只用该题该臂的输入。

## 2. 判分方法（逐字使用已物化的 prompt）

每题的 judge prompt 已逐字物化：**`<pilot>/scores_official/prompts/eNNN.txt`**
（= ImgEdit 官方 rubric 原文，`<edit_prompt>` 已替换为该题指令；禁止改写、
压缩、翻译或补充任何文字）。图片绑定关系见 `prompts/index.jsonl`
（每行含 qid、prompt_sha256、before、after_gemini_arm、after_bagel_arm）。

判一题：按顺序呈现 **BEFORE 图、AFTER 图**，随后附上对应 eNNN.txt 全文，
由你的内置模型作答。输出遵循该 prompt 自身要求：`Brief reasoning`
（**≤20 词**，官方纪律）+ 三维各一个 **1–5 整数分**。不因题难/易调松紧。

## 3. 落盘

写入 `<pilot>/scores_official/<model>/parts/part_{a,b,c}.jsonl`
（`<model>` = gemini / bagel；a=e001-07，b=e008-14，c=e015-20），每行一个 JSON：

```json
{
  "schema": "edit-codex-v1",
  "qid": "e001", "candidate_id": "c001", "edit_type": "replace",
  "judge": "gpt-5.6-sol-built-in-imgedit-official",
  "inputs": {"source_sha256": "...", "output_sha256": "...", "instruction_sha256": "..."},
  "validity": {"status": "ok", "detail": ""},
  "raw_dimensions": [
    {"key": "d1", "label": "Prompt Compliance", "score": 4, "reason": "<=20 words"},
    {"key": "d2", "label": "Visual Naturalness", "score": 4, "reason": "<=20 words"},
    {"key": "d3", "label": "Physical & Detail Integrity", "score": 3, "reason": "<=20 words"}
  ],
  "raw_total": 3.667,
  "official_dimensions": {"d1": 4, "d2": 4, "d3": 3},
  "official_total": 3.667,
  "critical_failures": [],
  "confidence": "high"
}
```

契约：`qid`/`candidate_id`/`edit_type`/`inputs` 逐字照抄该臂 blind_manifest 对应行；
`label` 与该题 prompt 里的三维名逐字一致；`score` 整数 1–5；
`official_dimensions`=[d1, min(d2,d1), min(d3,d1)]；`reason` 放官方要求的
≤20 词 Brief reasoning；图不可解码记 `model_failure` 并 1/1/1。

## 4. 聚合（40 行全部落盘后，两模型各一次 + 配对一次）

```bash
<env> <R>/benchmark/edit/eval_codex_score.py aggregate \
  --questions <pilot>/questions.jsonl \
  --manifest <pilot>/scores_official/<model>/blind_manifest.jsonl \
  --scores-dir <pilot>/scores_official/<model>/parts \
  --out-dir <pilot>/scores_official/<model>          # model = gemini, bagel 各跑一次

<env> <R>/benchmark/edit/eval_codex_score.py compare \
  --questions <pilot>/questions.jsonl \
  --left-scores <pilot>/scores_official/gemini/scores.jsonl \
  --right-scores <pilot>/scores_official/bagel/scores.jsonl \
  --left-name gemini-3.1-flash-image --right-name BAGEL-7B-MoT \
  --out-dir <pilot>/scores_official/paired
```

## 5. 完成标准

6 个 part 文件共 40 行、aggregate×2 与 compare 零错误。回复两个 report 的
overall（1–5 刻度）与 paired W/T/L，不展开逐题理由。
