# Codex 任务书：focus1000 生成图片判分

## 背景与目标

focus1000 实例池（benchmark 抽样 1000 实例）已完成 V6.0 协议改编出题 2000 条
（`gen_prompts.jsonl`，每实例 2 条），并用 qwen-image-3.0-pro 批量生图（`gen_imgs/`，
后台循环 `focus1000-imgloop.service` 仍在跑，**判分对象是已落盘的图，增量可续判**）。
你的任务：对已生成的图按 t2i v6.0 V2 判分协议打分，产出分数文件与汇总。

## 输入（全部只读）

| 文件 | 说明 |
|---|---|
| `/tank/demiwtg/benchmark/t2i/data/focus1000/gen_prompts.jsonl` | 每行一题：`prompt_id`（=「实例#变体号」）、`instance`、`variant`、`gen_prompt`（题面）、`key_visual_conclusions`（3-6 条视觉结论，可作 fidelity 核验项）、`level/combo_type/scene_types/...`（维度标注） |
| `/tank/demiwtg/benchmark/t2i/data/focus1000/gen_results.jsonl` | 每行一图：`prompt_id`、`file`（相对 `data/focus1000/` 的图路径）、`ratio/width/height/sha256`；**`error` 非空的行跳过** |
| `/tank/demiwtg/benchmark/t2i/data/focus1000/gen_imgs/*.png` | 生成图本体（2752/1536 档高清 PNG，判分前建议压到长边 ≤1024 控上下文） |

## 判分协议（唯一权威）

`/tank/demiwtg/benchmark/t2i/judge_prompt_gen_v6.0_V2.md` —— 对齐（判官自推隐式蕴含）
+ 质量 + 美感三维，QIB 刻度 {0,1,2,N/A}，φ 映射聚合。**不要自创打分维度**；
判分执行方式见下节（你自己判），可参考 `eval_score.py` 里 V2 路径的 φ 聚合实现
（`PHI` 映射与 `_line_weighted`）来算 `generic_score`。

## 判官：你自己（Codex），不部署任何推理服务

**不要起 vLLM，也不要调外部判分 API**——判分由你自己逐图完成。你是判官，对每张图
按 V2 协议的判分总则与三维准则亲自落档：

1. **看图**：图片在 `gen_imgs/`（2752/1536 档高清 PNG）。若你的会话读原图吃力，
   允许先用脚本批量生成长边 ≤1024 的缩略图副本（放 `data/focus1000/thumbs/`，
   临时产物）再逐张查看。
2. **判什么**：每题 = `gen_prompt` 题面 + 对应生成图。对齐维度先从题面推显式规定与
   隐式蕴含（概念固有/自然规律/前提锁定/组合同框，见 V2 协议「全局前提」节），
   再逐轴核验图中证据；质量与美感按 V2 各自准则。
3. **刻度**：{0,1,2,N/A} 三档 + 不适用，落档纪律照 V2（拿不准 1/2 给 1；记 0 必须
   能指认图中可核验证据；证据引用图中可见内容，不脑补）。
4. **输出行**（每题一行，追加写 `data/focus1000/gen_scores.jsonl`）：
   ```json
   {"qid": "<prompt_id>", "instance": "...", "gen_prompt": "...", "image": "gen_imgs/xxx.png",
    "alignment": {"<轴名>": {"score": 0|1|2|"N/A", "reason": "图中证据一句话"} },
    "quality": {"score": ..., "reason": ...}, "aesthetics": {"score": ..., "reason": ...},
    "generic_score": <0-100 综合分，按 φ 映射聚合>, "judged_by": "codex"}
   ```
   对齐轴名沿用 V2 协议的十轴命名。**reason 与打分写同一行/同一对象**（用户拍板的
   汇报格式，多行展示会被截断）。
5. **断点续判**：开头读 `gen_scores.jsonl` 已有 qid 集合，跳过已判行。生图循环仍在
   跑，每次续跑吃新增图即可，不必等生图收尾。
6. **分批推进**：建议每批 20-30 题（看图→落 JSON→追加写盘），批间可中断恢复；
   全部完成后生成 `gen_scores.report.json`（各维均值/分布、按 level 与 combo_type
   分桶、N/A 率、0 分轴 Top 榜）。

## 约束（硬性）

- 写入只允许两个位置：`benchmark/t2i/data/focus1000/`（判分产物）与
  `benchmark/t2i/focus1000_score*.py` 类薄脚本（如需要）；**其余一律只读**。
- **V1/V2 评测数据只读保护**：`benchmark/t2i/data/` 下的 `bench_v1/`、`synth_gen*`、
  `eval_*`、`images*`、`samples*.jsonl`、`scores*` 等既有目录/文件是历史评测资产，
  严禁写入/改名/移动/删除；判分也不消费它们。
- 禁止改 `datasets/demiwtg/meta/`（真相区）、`data/collect_v2/`、`state/` 既有文件。
- 不碰 `focus1000-imgloop.service`（生图循环）与其写入的
  `gen_results.jsonl/gen_imgs/`——只读。
- 结果汇报格式（用户拍板）：单题展示时 reason 与打分合并同一行，多行展示会被截断。

## 交付

1. `gen_scores.jsonl`（逐题判分行，schema 见上）与 `gen_scores.report.json`
   （各维均值/分布、按 level 与 combo_type 分桶、N/A 率、0 分轴 Top 榜）。
2. 缩略图生成脚本（若做了 thumbs/）与续判辅助脚本（可选，尽量薄）。
3. 一页 markdown 汇总：总体均分、三维分桶、失败率 Top 维度、代表性差图 qid 列表
   （reason 与打分同行）。
