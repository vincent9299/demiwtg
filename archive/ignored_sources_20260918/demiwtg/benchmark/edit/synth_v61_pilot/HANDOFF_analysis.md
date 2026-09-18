# edit 评测分析交接手册（2026-09-06，接续会话专用）

> 用途：20 题 pilot（synth_v61_pilot）三口径判分已全部完成，本手册交接给下一会话继续分析。先读本文件，再按需读 `AGENTS.md` 架构决策 2026-09-05/06 各条、`prompts/codex_score_prompt_edit_v2.md`（唯一编排协议）。批次历史细节在同目录 `HANDOFF.md`（§10 为最新状态，§1–9 是 9 月 3 日旧机器快照，路径已作废）。

## 1. 环境与路径

- 仓库根 `<R>` = `/yzp/zhaozy/yangzepeng/0905/demiwtg`；模块根 = `<R>/benchmark/edit/`；批次目录 `<pilot>` = `<R>/benchmark/edit/synth_v61_pilot`
- Python：`/yzp/zhaozy/yangzepeng/0905/env/bin/python`（下称 `<env>`；pandas/matplotlib/scipy/nbconvert 已装）
- Jupyter 内核：`demiwtg`（`<env>` 注册的 user kernelspec）；审阅 notebook 在 `reviews/` 下用 `jupyter nbconvert --execute --inplace --ExecutePreprocessor.kernel_name=demiwtg` 原地重跑
- LLM 网关：127.0.0.1:4001（modelhub，`restart.sh` 重启；OpenRouter 路由 2026-09-06 已换 key 并复测可用）；判官模型**钉定 gpt-5.6-sol**（codex 子代理内置，禁外部 API 代判）

## 2. 数据地图（全部在 `<pilot>/`）

| 路径 | 内容 | 状态 |
|---|---|---|
| `questions.jsonl` | 20 题（qid e001–e020，level/edit_type/suite/_batch/edit_instruction/_sample_image） | 冻结 |
| `scores_qib/{gemini,bagel}/scores.jsonl + report.json` | **QIB 主口径**（0/60/100 三档，钳制） | 冻结：G=42.0，B=17.0；判官输入是 v2 旧合并文本（拆分前），无 raw/ |
| `scores_qib/paired/` | 主口径配对 | W/T/L(G)=9/9/2，Δ=25.0 |
| `scores_qib/prompts/` | v2 模板的复建渲染（审计对照）+ index sha | 冻结轮的判官文本凭证；**不是 v2.1 的渲染** |
| `scores_official/{gemini,bagel}/{raw,parts,scores.jsonl,report.json}` | **官方 ImgEdit 1–5 对照臂**（2026-09-06 跑完） | G=2.667/5，B=2.05/5，n_invalid=0；raw/ 为判官原文（官方格式） |
| `scores_official/paired/` | 官方臂配对 | W/T/L(G)=9/7/4，Δ=0.617 |
| `scores_official/prompts/` | 官方臂物化判官 prompt（20 份 + index sha，含两臂图片绑定） | 判官实际所见文本 |
| `scores/codex_blind/` | 旧 1–5 诊断分（仅 Gemini，3.167/5，v1 口径） | 冻结审计，禁换算 |
| `gemini/ bagel/` | 两模型出图与 responses（各 20/20 ok） | 冻结 |

## 3. 协议/管线/文档现状

- 判官模板现役：`prompts/judge_prompt_edit_qib_v2.1.md`（分型逐档判据，ImgEdit 布局；**从未跑过真实轮次**，只做过渲染冒烟）；`prompts/judge_prompt_edit_imgedit_official.md`（官方原文九块，render 前与契约 `edit_score_prompts.json` 逐字节核对）
- 编排协议唯一一份：`prompts/codex_score_prompt_edit_v2.md`（六步流程 prepare→render→派发→ingest→aggregate→compare + 五条全轮次约束 + 官方臂变体节）；批次执行任务书 `TASK_official_rubric_codex.md`（已执行完毕，留档）
- 管线 `eval_codex_score.py`：render 支持 QIB md / 官方 md / 官方 json 三种模板；ingest 支持 `--format json`（QIB）/`imgedit`（官方文本响应）；全链路已合成数据实测
- Astra 评审报告：`reviews/judge_prompts_review_gpt-6-astra_20260906.md`（QIB 23 条 + 官方机制 7 条 + **官方文本观察项 18 条**——三臂解读必读）
- 审阅 notebook：`reviews/results_review_v61.ipynb`（20 cells 六节：整体分布/维度消融/配对/双口径/**三口径对比**/20 题明细；由 `/tmp/kilo/nb/build_nb.py` 生成——该脚本在 /tmp，会话销毁会丢，重建则按 notebook 现状改）

## 4. 已定结论（数字均经 report 对账）

1. 主口径：Gemini 42.0 vs Bagel 17.0（official 钳制）；mapped 未钳 54.7/34.0；Bagel 钳制损失比例（50%）是 Gemini（23%）两倍——"渲染有档但任务塌"的典型
2. 协议效应（官方 1–5 vs QIB）：**宽松不对称**——线性归一 (x−1)/4 后 Gemini 41.7% vs 42.0%（≈不变），Bagel 26.3% vs 17.0%（+9.3pp）→ 1–5 中间档同情分精准落在弱模型上，证实切 QIB 的动机
3. 逐题秩相关仅中等：Spearman G=0.686 / B=0.647；Fail 一致率两模型均 80%（各 2 题仅 official 判 Fail、2 题仅 QIB 判 Fail）；W/T/L 9/9/2→9/7/4，9 题胜者变动几乎全是 tie↔胜负（1–5 刻度更细）
4. **action、background 两型的 G−B 方向在 QIB mapped/official 与官方 1–5 三口径下均翻转**——对协议敏感的稳定现象，正式批重点盯
5. e003/e018/e019 QIB 满分 100 但官方臂仅 3.0/5——旧 Excel"超常规"门槛与官方 5 分锚不同向；v2.1 已改 Excel 判据（全部精确+可核验），这几题在 v2.1 下分数可能变
6. **难度倒挂（L1 30 / L2 25.6 / L3 78.9）纯属三重混杂**：level×source_batch 完全共线（L3 六题=全部补充批，L1/L2=全部 main 批）+ 类型排布失衡（Gemini 死穴 background×3/action×2/extract×2 全在 L2，强项 replace/style/compose 集中 L3）。pilot 的 by_level/by_source_batch 表**不可引用为结论**
7. 筛除统计：两级筛选全零——判分级 n_invalid=0（三口径均是）、出题级 cannot_construct=0 / reject=0 / warn=0；invalid_question、model_failure 等异常路径**从未被实战检验**

## 5. 未完成的分析线头（下一会话候选）

1. **逐题深钻双口径分歧**：8 道 Fail 判定不一致题（每模型 4 道）的根因归类——两侧判官证据都在（QIB reason 字段 vs 官方 raw/ Brief reasoning），对照看图归因"刻度粒度 / 锚点措辞 / 钳制可见性 / 语言"哪个因素主导
2. **action/background 翻转机制**：是真能力差异还是 protocol artifact（官方 background 锚与 QIB 严格前景锁定语义的宽严差）
3. **v2.1 验枪**（可选）：用 pilot 40 张图首跑 QIB v2.1（管线就绪：render 默认即 v2.1），量化 v2→v2.1 判准效应，四臂对照；跑前按协议走 prepare→render→子代理派发→ingest 全流程
4. **200 题正式批设计**：level×edit_type×source_batch 交叉平衡（每 level 内 9 类均衡、批次打散、每格 n≥20）；出题材料含 main 批需带"程度夸大"分级注入（eval_synthesize 已内置）；判准 v2.1；判官 gpt-5.6-sol
5. astra 评审未消化的中低优先项（A08 必然后果边界、A10–A19 等在报告内）可在 v2.1 首战数据出来后决定是否二次修订

## 6. 纪律红线（违反即数据作废）

- 分数文件（parts/scores/report/paired）只能由管线脚本生成，禁止手改；修复路径唯一：改 raw → 重跑 ingest → aggregate
- 主口径两轮 + 官方臂已冻结：不追溯重算；审计只能在冻结后进行且不回写
- `scores_qib/` 与 `scores_official/` 物理隔离互不读写；1–5 与 0/60/100 禁线性互换（归一化仅限"松紧示意"表述）；v2 与 v2.1 分数不可直接互比
- 判官=codex 子代理内置 gpt-5.6-sol，每题全新上下文、输入只有物化 prompt+两图；编排者不亲判、不向子代理粘贴任何隔离清单内容
- 新判分轮必须 prepare→render 物化后逐字判，未物化产物无效
- 判官 prompt 文件只含标题+模板块；机制说明只写编排协议；文档不嵌历史过程（决策记录唯一落点 = AGENTS.md）

## 7. 常用命令

```bash
cd /yzp/zhaozy/yangzepeng/0905/demiwtg
E=/yzp/zhaozy/yangzepeng/0905/env/bin/python
P=benchmark/edit/synth_v61_pilot
$E benchmark/edit/eval_codex_score.py render --questions $P/questions.jsonl --out-dir <新轮次>/prompts [--manifest <新轮次>/blind_manifest.jsonl]
$E benchmark/edit/eval_codex_score.py ingest --format json --judge gpt-5.6-sol-built-in --manifest M.jsonl --raw-dir raw/ --out-dir parts/
$E benchmark/edit/eval_codex_score.py aggregate --questions $P/questions.jsonl --manifest M.jsonl --scores-dir parts/ --out-dir <model_dir>
$E benchmark/edit/eval_codex_score.py compare --questions $P/questions.jsonl --left-scores A/scores.jsonl --right-scores B/scores.jsonl --left-name <A> --right-name <B> --out-dir paired/
cd benchmark/edit/reviews && /yzp/zhaozy/yangzepeng/0905/env/bin/jupyter nbconvert --to notebook --execute --inplace results_review_v61.ipynb --ExecutePreprocessor.kernel_name=demiwtg --ExecutePreprocessor.timeout=900
```
