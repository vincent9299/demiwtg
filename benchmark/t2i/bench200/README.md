# bench200 · t2i 终版评测集（200 题，自闭环）

**v6.0 出题协议 + V2 判分口径**的唯一现行终版。本目录自包含全部评测资产，无需依赖 `data/` 或 `archive/` 即可复跑、复核、扩展新模型。

## 构成

- **200 题 = r10 定稿 10 题（qid 60001–60010，ladder 阶梯试跑的最终轮）+ r11 批量 190 题（qid 60011–60202，其中 60029/60036 两号出题质检废弃）**
- 出题协议：`../prompts/synthesize_prompt_gen_v6.0.md`（gpt-5.6-sol 生成）；判分协议：`../prompts/judge_prompt_gen_v6.0_V2.md`（gpt-5.6-sol 判分）
- 验证：200 题与 `provenance/{r10,r11}_synth/` 源题库逐字一致（gen_prompt 全等；曾以一文件 sources_r10_r11.jsonl 合并核对，确认纯冗余后删除，round 溯源由 batch 字段承载）
- **实例覆盖：200 题 = 200 个不同实例（一题一实例，抽样 --per-instance 1）**。`instance` 字段为显式记录（2026-09-05 由 provenance 抽样清单按 qid 全量 join 补齐，与 `_query_label` 零失配；此前 instance 仅以 `_query_label`/`_sample_image` 文件名/`_job_sample` 形式间接记录）

## 目录

```
bench200/
├── README.md                    # 本文件
├── questions.jsonl              # 终版 200 题（canonical，status 全 constructed；
│                                #   batch 字段 = 轮次溯源 r10(10)/r11(190)；instance 字段 = 实体名 200/200）
├── samples/                     # 200 张题源图（qid_实例名_哈希 命名）
├── bagel/ gemini/ qwen2512/ zimage/   # 四模型出图：responses_shard*.jsonl + imgs/
├── scores/                      # V2 判分：每模型 scores_v60_V2_gpt-5.6-sol_<model>.jsonl (200行) + report.json
└── provenance/                  # 出题溯源
    ├── r10_synth/  r11_synth/   # 两轮批次的 questions/batch_stats/raw（API 原始轨迹）
    └── samples_v60_uniform_r11{,b}.jsonl   # r11 抽样清单（190+2；在排除集账册内）
```

## 终版成绩（V2 口径，n=200，判官 gpt-5.6-sol）

| 图像模型 | Alignment | Quality | Aesthetics | 判分完整性 |
|---|---|---|---|---|
| gemini-3.1-flash-image | **56.26** | **68.38** | **75.19** | 200/200 |
| Qwen-Image-2512 | 34.16 | 63.19 | 70.02 | 200/200 |
| Z-Image-Turbo | 32.33 | 62.26 | 61.80 | 200/200 |
| bagel（BAGEL-7B-MoT 底座） | 22.25 | 48.83 | 58.09 | 200/200 |

⚠️ `scores/*.report.json` 中 bagel（n=127）与 gemini（n=16）是判分中途的过时快照；**以 jsonl 为准（四模型均 200/200 完整）**。上表由 jsonl 重算（2026-09-05）。

Alignment 全线偏低是题库设计意图（知识密度 gating 拉开模型差距），非判分异常。

## 复跑 / 扩展新模型

```bash
source /yzp/zhaozy/yangzepeng/0905/activate.sh   # 项目 env（判分走本地 vLLM judge 需 modelhub 网关:4000）

cd /yzp/zhaozy/yangzepeng/0905/demiwtg/benchmark/t2i

# ① 新模型出图（读终版题库，经 modelhub 网关调图像 API）
python3 eval_t2i_gen.py --questions bench200/questions.jsonl \
    --out bench200/<new_model> --models <模型名>

# ② V2 判分（写回 scores/，判官 gpt-5.6-sol）
python3 eval_score.py score --v60 V2 --model gpt-5.6-sol --source <new_model> \
    --eval-dir bench200 --out bench200/scores/scores_v60_V2_gpt-5.6-sol_<new_model>.jsonl

# ③ 成绩汇总刷新
python3 - << 'EOF'
import json, statistics as st
from pathlib import Path
for f in sorted(Path('bench200/scores').glob('scores_v60_V2_*.jsonl')):
    rows = [json.loads(l) for l in open(f)]
    m = lambda k: round(st.mean(r[k] for r in rows if isinstance(r.get(k), (int,float))), 2)
    print(f"{f.name.split('sol_')[1][:-6]:<28} n={len(rows)}  align={m('alignment_score')}  qual={m('quality_score')}  aes={m('aesthetic_score')}")
EOF
```

（②的 eval_score 具体参数以 `--help` 为准；历史四模型即用此链路产出。）

## 关联

- **正式呈现审阅册**：`../reviews/results_review.ipynb`（四模型 bench200 成绩的正式呈现，三段：整体得分对比 / 关键维度对比 / 抽样看 case；由 `../gen_results_review.py` 生成，`--execute` 重跑回写输出）
- 十题消融实验（判官×V2/V5×图源三消融，r2–r10 ladder 期产物）：分数在 `../archive/v60_ladder/ablation_scores/scores_t2i_v60_*`，分析册已归档 `../archive/notebooks_retired/results_review_v60.ipynb`（glob 已指归档位），题面/图归档在 `../archive/v60_ladder/`
- 抽样账本：`../data/`（现行）+ `provenance/`（r11 批次）+ `../archive/`（历史批次），排除集机制防任何已用样本回流
- 千实例样本线已独立为 `../../focus1000/`（与本评测集无直接依赖）
