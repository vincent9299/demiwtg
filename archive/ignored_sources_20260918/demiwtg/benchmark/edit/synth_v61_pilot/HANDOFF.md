# Edit v6.1 首轮测试交接（给 Kilo）

更新时间：2026-09-03 UTC

## 1. 当前结论

首轮 20 题的出题、Gemini 生成、Bagel 生成均已完成。题库严格校验为
`PASS 20 / WARN 0 / REJECT 0`；两个图像模型均为 `20/20 ok / 0 fail`。

最终评分口径已经从旧 ImgEdit 1–5 分制改成与 T2I/QIB 对齐的离散三档：

- tier 0 → 0（Fail）
- tier 1 → 60（Pass；普通正确只能到此档）
- tier 2 → 100（Excel；必须给出具体、可定位的超常执行证据）

Gemini 的 QIB 重评已完成，当前总分 `42.0/100`。Bagel 的匿名输入已准备
完毕，但 QIB 逐题盲评尚未落盘；不要把旧 `3.167/5` 当最终结果。

## 2. 权威输入与协议

- 20 题计划：`plan.jsonl`
- 20 题终稿：`questions.jsonl`
- 不可构造终态：`cannot_construct.jsonl`（本轮 0 条）
- 严格校验：`validation_report.json`
- image-first 出题协议：`../../synthesize_prompt_edit_v6.1.md`
- QIB 最终评分协议：`../../codex_score_prompt_edit_v2.md`
- 匿名输入/校验/聚合工具：`../../eval_codex_score.py`

出题的关键原则：BEFORE 像素是源场景唯一事实；caption、desc、分类路径和
素材清单只是待核假设或概念知识，不能证明图中存在某物。评分同样只看
BEFORE、AFTER、`edit_instruction`、`edit_type`，不得读取出题 reasoning、
evidence、level、suite、模型身份或另一候选。

## 3. 题库与难度分布

- 题数：20
- T2I paired level：L1=2、L2=12、L3=6（10% / 60% / 30%，逐实例继承）
- suite：basic=16、knowledge=4（各 level 内独立铺开）
- edit_type：9 类轮转；本轮覆盖 replace/add/remove/adjust/background/action/
  style/extract/compose
- 严格校验：PASS=20、WARN=0、REJECT=0、终态=20/20

注意：paired label 和总体配比已经对齐 T2I，但不等于 edit 的经验难度天然
同构。本轮 Gemini 的 QIB 分层分数为 L1=30.000、L2=25.556、L3=78.889，
没有随难度单调下降。该结果被 edit_type 与来源批次严重混杂，且 n=20 太小；
正式跑 200 题前应检查并平衡 `level × edit_type × source_batch`，不能以本轮
分层均值声称难度校准成功。

## 4. Gemini 生成结果

实际模型：`openrouter/google/gemini-3.1-flash-image`

入口：本机 OpenAI-compatible 网关 `http://127.0.0.1:4001/v1/chat/completions`

产物：

- 图片：`gemini/imgs/gemini-3.1-flash-image_a1d35ce0/e001.png` … `e020.png`
- 响应：`gemini/responses_gemini-3.1-flash-image_a1d35ce0.jsonl`
- 错误：`gemini/errors_gemini-3.1-flash-image_a1d35ce0.jsonl`（0 条）

核验：20 个唯一 qid、20 ok、0 error；累计上游推理时间 494.4 秒；响应记录
费用合计约 1.3565 美元。每条带 source/instruction/output 指纹或哈希，续跑
会按 fingerprint 跳过一致请求。

## 5. Bagel 生成结果

模型：`bagel/models/BAGEL-7B-MoT`，编辑路径使用 runner 内 official cfg，
`num_timesteps=50`，base seed=42 并按 qid 派生确定性 seed。

产物：

- 图片：`bagel/imgs/e001.png` … `e020.png`
- 响应：`bagel/responses_shard0.jsonl` … `responses_shard3.jsonl`
- 题库快照：`bagel/questions.jsonl`

运行方式：4 张 A800、4 分片、每卡 5 题，各分片独立 cwd/offload 目录。

核验：20 个唯一 qid、20 ok、0 fail；每条均有 source/instruction/output
SHA256 和输出尺寸。累计 GPU 题目推理时间 2706.0 秒，均值 135.3 秒，范围
82.8–178.1 秒；四卡并行墙钟约 13.4 分钟。模型进程已全部退出。

所有 20 张 Bagel 输出已人工打开确认可解码。总体观察仅供复核、不代替盲分：
模型经常重绘全景，background、extract 和局部 adjust 题尤其容易破坏源图保持；
因此判分时必须严格对照 BEFORE，不能只看 AFTER 是否漂亮。

## 6. 最终评分状态

### Gemini（已完成）

- 匿名 manifest：`scores_qib/gemini/blind/blind_manifest.jsonl`
- 私有身份映射：`scores_qib/gemini/blind/identity_private.jsonl`
- 三分片盲分：`scores_qib/gemini/blind/parts/part_{a,b,c}.jsonl`
- 逐题终分：`scores_qib/gemini/scores.jsonl`
- 汇总：`scores_qib/gemini/report.json`
- overall：42.000/100
- by level：L1 30.000 / L2 25.556 / L3 78.889
- by suite：basic 47.083 / knowledge 21.667
- 较弱类型：action 0、background 0、extract 0
- 较强类型：replace 86.667、add 86.667、style 80.000、compose 73.334

### Bagel（待 Kilo 完成盲分）

- 匿名 manifest：`scores_qib/bagel/blind/blind_manifest.jsonl`
- 匿名 BEFORE/AFTER：`scores_qib/bagel/blind/inputs/`
- 私有身份映射：`scores_qib/bagel/blind/identity_private.jsonl`
- 待写：`scores_qib/bagel/blind/parts/part_a.jsonl`（e001–e007）
- 待写：`scores_qib/bagel/blind/parts/part_b.jsonl`（e008–e014）
- 待写：`scores_qib/bagel/blind/parts/part_c.jsonl`（e015–e020）

盲评分片只允许读取 QIB v2 prompt 和 Bagel blind manifest 对应行。manifest 行内
已经包含 qid、candidate_id、edit_type、edit_instruction、BEFORE/AFTER 路径与
三类哈希；不要读取 `identity_private.jsonl`、Gemini 目录、旧分数、questions
扩展字段、reasoning/evidence/level/suite。

## 7. Kilo 收尾命令

在仓库根 `/tank/demiwtg` 执行。

Bagel 三个 `part_*.jsonl` 完整落盘后，先校验并聚合：

```bash
.venv/bin/python benchmark/edit/eval_codex_score.py aggregate \
  --questions benchmark/edit/data/synth_v61_pilot/questions.jsonl \
  --manifest benchmark/edit/data/synth_v61_pilot/scores_qib/bagel/blind/blind_manifest.jsonl \
  --scores-dir benchmark/edit/data/synth_v61_pilot/scores_qib/bagel/blind/parts \
  --out-dir benchmark/edit/data/synth_v61_pilot/scores_qib/bagel
```

再做 paired 对账（左 Gemini、右 Bagel；W/T/L 也是从 Gemini 视角）：

```bash
.venv/bin/python benchmark/edit/eval_codex_score.py compare \
  --questions benchmark/edit/data/synth_v61_pilot/questions.jsonl \
  --left-scores benchmark/edit/data/synth_v61_pilot/scores_qib/gemini/scores.jsonl \
  --right-scores benchmark/edit/data/synth_v61_pilot/scores_qib/bagel/scores.jsonl \
  --left-name gemini-3.1-flash-image \
  --right-name BAGEL-7B-MoT \
  --out-dir benchmark/edit/data/synth_v61_pilot/scores_qib/paired
```

预期产物：

- `scores_qib/bagel/scores.jsonl`
- `scores_qib/bagel/report.json`
- `scores_qib/paired/paired_scores.jsonl`
- `scores_qib/paired/report.json`

`aggregate` 会复算并强制校验：schema、qid 唯一性、edit_type、三维标签、
raw tier∈{0,1,2}、φ 映射、d2/d3≤d1 的官方钳制、三类输入哈希和终分。

## 8. 旧分数与禁用口径

以下是旧 ImgEdit 1–5 诊断分，不是最终横向比较口径：

- `scores/codex_blind/scores.jsonl`
- `scores/codex_blind/report.json`（Gemini 3.167/5）
- `../../codex_score_prompt_edit_v1.md`

请保留作审计，但最终报告不要混用或把它线性换算为百分制。1–5 不能可靠地
后处理成 0/60/100，必须按 QIB v2 重新看图落档。

## 9. 代码变更与边界

本轮相关实现：

- `benchmark/edit/eval_synthesize.py`
- `benchmark/edit/synthesize_prompt_edit_v6.1.md`
- `benchmark/edit/eval_edit_gen.py`
- `benchmark/edit/codex_score_prompt_edit_v2.md`
- `benchmark/edit/eval_codex_score.py`
- `bagel/Bagel/scripts/run_wkbench.py`

上述 Python 文件已通过 `py_compile`，相关 diff 已通过 `git diff --check`。
本轮未执行 git commit/reset/checkout，也未改数据湖真相区、原始 plan v6.0 或
manifest。工作树中另有大量用户既有改动，整理时只处理上述明确相关文件，
不要清理或覆盖其他 dirty changes。

## 10. 现状更新（2026-09-06，新机器；§6–§7 待办已全部完成，§6/§7 内命令路径已作废）

> **接续分析请先读同目录 `HANDOFF_analysis.md`（2026-09-06 三口径完成后的分析交接手册，含数据地图/已定结论/待办线头/纪律红线/常用命令）。本节仅为状态摘要。**

- **收尾完成**：Bagel QIB 盲评已落盘（overall 17.0），paired 已产出（Gemini 42.0 vs Bagel 17.0，W/T/L = 9/9/2，Gemini 视角）；审阅 notebook 重建为 `../reviews/results_review_v61.ipynb`（整体分布 → 维度消融 → 配对 → 双口径 → 20 题明细，原地执行零报错，与本目录 report.json 对账全部一致）。
- **目录重组**：edit 已对齐 t2i 废除 data/ 层——协议在 `../prompts/`、notebook 在 `../reviews/`、审计产物在 `../archive/`、本批次目录与 `focus200/` 提至子模块顶。本文件其余章节中的 `data/...` 路径均为历史快照，以新布局为准；盲评 manifest 内绝对路径已全部改写至新址。
- **判分协议角色分离**：判官 prompt 权威源 = `../prompts/judge_prompt_edit_qib_v2.md`（纯判官输入）；编排协议 = `../prompts/codex_score_prompt_edit_v2.md`（通用可复用，不含批次状态）；`eval_codex_score.py` 新增 render（逐题物化判官 prompt + sha256 登记）与 ingest（自动组装标准分数行）子命令；判官模型钉定 gpt-5.6-sol。本批 `scores_qib/prompts/` 为冻结两轮（判定输入系拆分前旧合并文本）的复建渲染，仅作审计对照。
- **对照臂已执行完毕（2026-09-06）**：ImgEdit 官方 rubric 1–5 逐字臂两模型 40 题判定完成（判官 gpt-5.6-sol 子代理、n_invalid=0、raw/parts/scores/paired 全落盘）——**Gemini overall 2.667/5、Bagel 2.05/5、W/T/L = 9/7/4（G 视角）、Δ=0.617**。三口径对比（官方 1–5 / QIB official / QIB mapped）已入 `../reviews/results_review_v61.ipynb` 第五节：逐题 Spearman G=0.686 / B=0.647，Fail 判定一致率两模型均 80%，线性归一后官方臂对弱模型更宽松（Bagel 26.3% vs QIB 17%；Gemini 41.7% vs 42% 几乎不变），action/background 两型的 G−B 方向在两口径下依旧翻转。本批 20 题至此收官；下一批（200 题正式批）起 QIB 用 v2.1 判准。
