# edit/archive · 归档目录（历史批次与审计产物，不参与现行链路）

> **现行评测批次在 `../synth_v61_pilot/`**（v6.1 pilot 20 题自闭环：题库、双模型生成、
> QIB 主口径判分 scores_qib/、paired 对账、官方 rubric 实验臂 scores_official/）。
> edit/ 已废除 data/ 层（2026-09-05，对齐 t2i）：批次目录与活素材（focus200/、
> complexity_audit_synth.jsonl —— eval_synthesize 仍在读）落子模块根，历史产物入本目录。
> 数据一律不删。

## 桶清单

| 桶 | 内容 | 备注 |
|---|---|---|
| `synth_v60/` | v6.0 出题批次 plan.jsonl | v6.1 前身 |
| `synth_v60_smoke/` | v6.0 冒烟批（questions_v60 + raw + img_cache + run_report） | |
| `complexity_audit.jsonl` | 逐图复杂度审计账本（9.2MB，append 历史） | eval_complexity.py 旧默认落点；新轮次落 `../complexity_audit.jsonl` |
| `audit_doublecheck_sample.jsonl` | 初审虚报复核抽样 | audit_doublecheck.py 默认读物已指向本目录 |
| `audit_doublecheck_results.jsonl` | 复核逐条结果 | |
| `audit_doublecheck.report.json` | 复核汇总报告 | eval_synthesize 素材分级注入的结论来源 |
| `audit_doublecheck.summary.md` | 复核摘要 | |

## 追溯关系

- `complexity_audit.jsonl`（初审，偏乐观）→ `audit_doublecheck_*`（复核修正）→
  `../complexity_audit_synth.jsonl`（复审后活素材，v6.1 出题注入）→ `../synth_v61_pilot/questions.jsonl`
- 旧 1–5 诊断分与 QIB 主口径均在 `../synth_v61_pilot/scores*/` 内，不在本目录
