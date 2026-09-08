# edit/archive · 归档目录（历史批次与审计产物，不参与现行链路）

> **现行出题批次在 `../bench200/`，已有 pilot 题并入总量；历史评测批次保留于 `../synth_v61_pilot/`**（v6.1 pilot 20 题自闭环：题库、双模型生成、
> QIB 主口径判分 scores_qib/、paired 对账、官方 rubric 实验臂 scores_official/）。
> edit/ 已废除 data/ 层（2026-09-05，对齐 t2i）：批次目录与活素材（focus200/、
> complexity_audit_synth.jsonl —— eval_synthesize 仍在读）落子模块根，历史产物入本目录。
> 数据一律不删。

## 桶清单

| 桶 | 内容 | 备注 |
|---|---|---|
| `judge_prompt_edit_qib_v2.md`、`judge_prompt_edit_qib_v2.1.md` | 旧 QIB 判官模板 | 现行定版为 `../prompts/judge_prompt_edit_qib_v2.2.md` 修订版 |
| `judge_prompt_edit_qib_v2.2_pre_revision.md` | 未修订 v2.2 模板快照 | 原件保留于 pilot/scores_qib_v22/prompt_snapshot.md，现行修订版另存 prompts/ |
| `codex_score_prompt_edit_v2_sol_pre_final_20260907.md` | 定版前 sol 判分编排协议 | 现行编排显式指定 gpt-6-astra / medium；历史判分产物不改 |
| `synth_v60/` | v6.0 出题批次 plan.jsonl | v6.1 前身 |
| `synth_v60_smoke/` | v6.0 冒烟批（questions_v60 + raw + img_cache + run_report） | |
| `complexity_audit.jsonl` | 逐图复杂度审计账本（9.2MB，append 历史） | eval_complexity.py 旧默认落点；新轮次落 `../complexity_audit.jsonl` |
| `audit_doublecheck_sample.jsonl` | 初审虚报复核抽样 | audit_doublecheck.py 默认读物已指向本目录 |
| `audit_doublecheck_results.jsonl` | 复核逐条结果 | |
| `audit_doublecheck.report.json` | 复核汇总报告 | eval_synthesize 素材分级注入的结论来源 |
| `audit_doublecheck.summary.md` | 复核摘要 | |
| `prompts_v1_v60/` | 退役提示词世代：出题协议初版（synthesize_prompt_edit.md）与 v6.0（synthesize_prompt_edit_v6.0.md）、判分协议 v1（codex_score_prompt_edit_v1.md，1–5 初版；其唯二消费轮次的 Gemini 分数冻结在 `../synth_v61_pilot/scores/codex_blind/`） | 现役世代在 `../prompts/`：出题 v6.1、QIB v2 判官/编排两份、官方 rubric 臂两份 |
| `audit_doublecheck_prompt.md` | 复杂度审计抽样复核轮（2026-09-02）任务提示词：codex double-check 独立重判 qwen3.8-27b 的结构化审计，揪虚高/编造/漏报；产物 = 本目录 audit_doublecheck_* 四件 | 复核结论已落地为 eval_synthesize 的素材分级注入；未来新图池开复核轮可复用为模板 |
| `notebooks_retired/` | 两册退役审阅 notebook：results_review.ipynb（8 月 wkbench_v0 冒烟审阅，其分析对象 bagel/results/wkbench_v0 与 synth_edit 批次本机已不存在）、audit_review.ipynb（复杂度复核轮审阅，数据即本目录 audit_doublecheck_*） | 现役在 `../reviews/`：question_dev.ipynb（路径文案已同步新布局）+ results_review_v61.ipynb |

## 追溯关系

- `complexity_audit.jsonl`（初审，偏乐观）→ `audit_doublecheck_*`（复核修正）→
  `../complexity_audit_synth.jsonl`（复审后活素材，v6.1 出题注入）→ `../synth_v61_pilot/questions.jsonl`
- 旧 1–5 诊断分与 QIB 主口径均在 `../synth_v61_pilot/scores*/` 内，不在本目录
