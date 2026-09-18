# 交接：t2i v5.5 夜批终态（v5.5.3 出题 + v5.5.4 聚合）· 2026-08-29 深夜

> 本文 = 新窗口接活的全量入口。过程沿革见姊妹文档 `handover_20260829_new_protocol_batch.md` §6（v5.5.2→v5.5.3→v5.5.4 三次拍板逐条记录）；本文只写**终态**，与姊妹文档冲突处以本文为准。

## 0. 一句话现状

10 题端到端批（`synth_gen_20260829_v3` + `eval_bagel_v55`）已跑完并三次口径迭代落码；pilot 旧批（`synth_gen_20260828_v2` / `eval_bagel_v54`，v5.4 口径）保留原样不重判，与新批不可比。

## 1. 协议终态（唯一口径，v5.5.3 出题 + v5.5.4 聚合）

**四维出题**（出题册 `synthesize_prompt_gen_v5.5.md`，§四/五/六 已重写为平行结构）：
- **真实保真度**：出题人产出，**恰好 10 条**，锚定约束清单（直挂/组合/推导链），defining×2.0；唯一需要个性化产出的维度（携带题面外长尾知识）
- **文本一致性**：出题人**不产出**（不输出 alignment_checks）；判官从题面提取显式规定对照生成图直判 **Alignment 18 项全量**，题面无该类规定记 N/A 剔除
- **质量 7 / 美感 3**：出题人不产出，判官全量判，N/A 按附录「激活条件」列定
- 出题人对三支柱的责任 = 保证题面可判（显式规定写得明确无歧义）

**判分**（`eval_score.py`）：
- 刻度 QIB：{0 Fail, 1 Pass, 2 Excel, N/A} → φ 映射 {0, 60, 100}，N/A 剔除出均值
- **聚合（v5.5.4）**：`total = G × (0.2 + 0.8×F/100)`（常量 `FIDELITY_FLOOR = 0.2`）；G = 对齐/质量/美感等权均值；F=保真线（defining×2）。FLOOR=0 即退回纯乘子。无封顶无熔断（violated/fidelity_low 仅诊断标记）
- 判官路由 `openrouter/google/gemini-3.7-flash`（temp=0/seed=42，确定性已验证，网关 127.0.0.1:4001）

**机审**（`eval_synthesize.py` audit_v53，唯一机审，随出题实时、只告警不拦截；LLM 质量审计层早已废除）：
- 保真检查恰好 10 条、锚点 ⊆ 清单编号、泄漏对照全「无」、rubric 三档齐、**不得输出 alignment_checks / quality_facets / aesthetic_facets**（输出即告警）
- 验收 = 出题日志 **0 warn**

## 2. 批次产物盘点（均在 `benchmark/t2i/data/`）

| 产物 | 状态 |
|---|---|
| `synth_gen_20260829_v3/constraints.jsonl` | 10 实例（0016~0019、0021~0026；0020 阿斯顿马丁与 0019 同实例且仅 3 条清单导致锚点幻觉，已换 0026 宁波轨道交通补位），预检全过（剔除 1/87） |
| `synth_gen_20260829_v3/questions_v55_gpt-5.6-sol.jsonl` | 10 题（L3×7/L2×3），alignment_checks 已剥离，机审 0 warn |
| `synth_gen_20260829_v3/raw/` | sol 出题原始响应（重出需先删） |
| `eval_bagel_v55/` | questions.jsonl 快照 + responses_shard0 + imgs/（10/10 图）+ `scores_gemini-3.7-flash.jsonl`（v5.5.3 判 + v5.5.4 聚合，离线重算，判官输出未重调）+ `*.report.json` |

**终读**：overall **20.34** ｜ fid 23.14 / align 47.77 / qual 56.57 / aes 56.0 ｜ judge_fail=0。4 道知识全灭题由 0 → 8.7~12.0（地板效果）。

## 3. 代码落点索引（排查用）

1. `eval_synthesize.py`：`audit_v53` exact10 分支结构（曾修复：恰好 10 条误报「不在 5~7」）；v5.5 分支「输出 alignment_checks 则告警」，旧 2~4 规则仅留 v5.3 分支
2. `eval_score.py`：`GENERIC_ALL` 三组词表；`build_v53_user_prompt` 对齐节=全 18 项无附挂；`finalize_v53` 加地板聚合（`FIDELITY_FLOOR`）；解析兼容旧 index 列表式 alignment；schema 标签按保真条数推导
3. `synthesize_prompt_gen_v5.5.md`：§四/五/六 平行三段（判分机制→出题人责任=保证可判→边界）；§八 schema 无 alignment_checks；§九 自检清单同步
4. `question_dev.ipynb`：三段式重排（评测集构建 / 题目审阅全链 ①~⑥ / 附录·密度预探）；顶部 `_BT` 总开关已指向 `20260829_v3` + `eval_bagel_v55`；① 探针审阅格含预检剔除展示；⑥ `_align_rows` 双格式（新批 facet 字典 / 旧批 index 列表）。全簿幂等可 Run All（唯一写盘格在附录密度预探，`data/density_probe/`）

## 4. 执行序列（下批照抄，批次标签自定）

```bash
cd /tank/demiwtg; BT=20260830_v4   # 例
O=15; L=10                          # 续上一批的 offset
# 2026-08-29 改版：合并模型换 glm/glm-5.3（用户 token 包）+ 清单不设数量上限；
# 原图一致性预检（gemini）环节废除，precheck 子命令已删，序列中不再出现。
python3 benchmark/t2i/eval_probe.py generate \
    --samples benchmark/t2i/data/samples_20260828_v2.jsonl \
    --limit $L --offset $O --workers 3 --out-dir benchmark/t2i/data/synth_gen_$BT
python3 benchmark/t2i/eval_synthesize.py --schema v5.5 \
    --models openrouter/openai/gpt-5.6-sol \
    --samples benchmark/t2i/data/samples_20260828_v2.jsonl \
    --constraints benchmark/t2i/data/synth_gen_$BT/constraints.jsonl \
    --out-dir benchmark/t2i/data/synth_gen_$BT --max-tokens 32768 --workers 2
mkdir -p benchmark/t2i/data/eval_bagel_$BT
cp benchmark/t2i/data/synth_gen_$BT/questions_v55_gpt-5.6-sol.jsonl benchmark/t2i/data/eval_bagel_$BT/questions.jsonl
CUDA_VISIBLE_DEVICES=1 /tank/demiwtg/.venv/bin/python bagel/Bagel/scripts/run_wkbench.py \
    --questions /tank/demiwtg/benchmark/t2i/data/eval_bagel_$BT/questions.jsonl \
    --out_dir /tank/demiwtg/benchmark/t2i/data/eval_bagel_$BT --tasks t2i
python3 benchmark/t2i/eval_score.py score \
    --questions benchmark/t2i/data/eval_bagel_$BT/questions.jsonl \
    --responses benchmark/t2i/data/eval_bagel_$BT/responses_shard0.jsonl \
    --out benchmark/t2i/data/eval_bagel_$BT/scores_gemini-3.7-flash.jsonl \
    --endpoint http://127.0.0.1:4001/v1/chat/completions \
    --model openrouter/google/gemini-3.7-flash \
    --constraints benchmark/t2i/data/synth_gen_$BT/constraints.jsonl --workers 2
```

## 5. 已知坑（实测）

- **PATH 的 python 无 transformers**：run_wkbench 必须 `/tank/demiwtg/.venv/bin/python`
- 网关冷连接首并发被掐（RemoteDisconnected），30s 退避自愈勿手动干预
- 改过出题/判官 prompt 后重跑 → **先删产物 jsonl + raw/**（resume 按 qid 跳过）
- 同实例多样本（如两个阿斯顿马丁）：探针按实例名去重合并，出题侧同一实例可共享清单、但**每实例限一题**（共享清单的多余样本出题必锚点幻觉，实测三次）
- 聚合纯改（判官 prompt 不动）可离线重算：scores 已存三支柱分 + 保真线分 + generic_score，零 LLM
- report json 只反映最后一次运行的题集，全量统计看 scores jsonl / notebook

## 6. 遗留观察点（不阻塞）

- 负向约束占比 49%（验收带 15~35%，0025/0026 轨道交通类拉高；下批看是否系统性）
- 清单 <10 条实例多（4~11 条，0024 仅 4 条）：知识密度偏低，属样本特性非缺陷
- 三支柱贴 60（QIB 三档天性，1 Pass=60 为基线）；区分度主力在保真线，地板版总分下 8.7~12（全灭）与 31~38（高分）间约 2.5 倍差
- overall 20.34 为 Bagel-7B 读数；30~60 预期带按强模型校准

## 7. 下一步候选

1. 上量：按 `data/probe_pilot_handover.md` §4 配额公式扩批（新协议已跑顺）；继续用 `samples_20260828_v2.jsonl` 顺延 offset（本批用到 0026）
2. 换强 T2I 模型验证 QIB 量尺校准（判分链路不动，只换生成侧）
3. notebook 审阅：`_BT` 已指新批，Run All 即可看全链
