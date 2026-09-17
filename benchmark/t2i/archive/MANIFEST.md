# t2i/archive · 归档目录（历史批次全量保留，不参与现行链路）

> **现行终版评测集在 `../bench200/`**（自闭环：200 题 = r10 十题定稿 + r11 百九十题，含 samples 200 图、四模型出图与 V2 判分、provenance 源批次与抽样清单）。
> `../data/` 已清空（消融分数移入本目录 v60_ladder/ablation_scores/），仅作为 eval 脚本未来产出的默认落点保留空目录。
> 数据一律不删：题目/图/样本清单/分数全部保留在本目录，按世代分桶。
> 排除集机制：eval_sample*.py 的 DEFAULT_EXCLUDES 整目录递归扫描三赛道（vlm/t2i/edit）全部 samples*.jsonl（edit 已去 data/ 层、批次目录落顶层），任何历史批次样本永不回流。

## 桶清单

| 桶 | 内容 | 体量 |
|---|---|---|
| `v1_bench/` | （已移出 → ../bench200/，即终版评测集本体） | — |
| `v5_line/` | v5.x 世代：bagel 六轮评测、API 商用模型对照（eval_models_v55）、v5.6/v5.7 影子试跑、旧抽样批次（samples 首批/千实例清单+729M 图/20260829_v3/images/synth_gen 旧版） | ~865M |
| `v60_ladder/` | v6.0 判分阶梯试跑 r2–r9（同批 10 题 qid 60001–60010 反复调参 + 生成图 + ladder 分数；r10 定稿已并入 ../bench200/provenance/r10_synth）；`ablation_scores/` = 十题消融分数（判官×V2/V5×图源，notebooks_retired/results_review_v60.ipynb 的分析对象，notebook 的 glob 已指向此处） | ~100M |
| `v60_v5_scores/` | v60 题目的 V5 旧判分口径分数（bagel/gpt-image-2 × gpt-5.6-sol，与 V2 同源不同口径） | <1M |
| `r11_images/` | r11 抽样期图片拷贝 192 张（终版题源图以 ../bench200/samples/ 200 张为准） | 165M |
| `probe/` | density_probe 探针线全部验证轮 | 332K |
| `baseline/` | baseline 基线图 + judge_prompts 判分物化产物（可由 eval_score 再生） | 175K |
| `notebooks_retired/` | 退役审阅 notebook：results_review_v60.ipynb（v6.0 十题消融审阅——判官×V2/V5×图源三消融 + case 逐项对比 + r10 批次对照；分析对象即 `v60_ladder/ablation_scores/`，bench200 终版后归档） | 现役在 `../reviews/`：question_dev.ipynb + results_review.ipynb（bench200 四模型正式呈现，../gen_results_review.py 生成） |

## 追溯关系

- 十题消融分数（`../data/scores_t2i_v60_V2_*`）的题面/图对应 `v60_ladder/` 各轮，可追溯；消融分析见 `notebooks_retired/results_review_v60.ipynb`。
- v1_bench 命名勘误（2026-09-05）：曾误判为"v1 世代旧基准"，实为**终版评测集第 1 版**，已拎出为 `../bench200/`。
