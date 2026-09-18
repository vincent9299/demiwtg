# 交接：新协议首批出题（2026-08-29 晚定稿）

> 上一窗口（本日）完成 t2i 评测协议的 QIB 对齐全面改造。本文件 = 新窗口执行新协议首批的全量上下文。

## 0. 现状（不要动的东西）

- **pilot 批**（15 实例全链产物，保留原样、不重判，用户拍板）：
  - 探针清单：`data/synth_gen_20260828_v2/constraints.jsonl`（15 记录，旧合并册产物 5~9 条/概念）
  - 题库：`data/synth_gen_20260828_v2/questions_v54_gpt-5.6-sol.jsonl`（15 题，旧协议：带图出题、7 条检查）
  - Bagel：`data/eval_bagel_v54/`（15/15 图）；判分 `scores_gemini-3.7-flash.jsonl`（15 题，**判官 prompt 为对齐准则修复前的版本**，report 缺失——重跑 score 会按 resume 只补 report 但会用新 prompt 重判，等于换口径，故不重跑）
  - `scores_*.jsonl` 之外勿删；判官确定性已验证（同 prompt 两连跑 0 漂移）
- **新批与 pilot 不可比**：出题输入（去图）、检查条数（7→10）、判档刻度（掌握度→QIB）、聚合（封顶→纯权重→乘子）全变了。

## 1. 本日协议变更全量清单（已落码落册，验证过）

**探针（eval_probe.py + 两册 prompt）**
- 合并册：清单 **10~12 条**（对齐出题 10 条检查）；规约性升为第一剔除类 +「剔除从宽、保留从严，宁缺毋滥」；**不喂 desc**（裁决靠五路交叉印证 + sol 自身知识）；sol 职责句=合并编辑
- 生成册：职责=「硬约束判分规则设计员」；极性双普遍性（都对该概念**所有合法画法**成立）+ 禁单写部分画法的极性约束
- precheck：`_precheck_done` 增量标记（已预检记录跳过 gemini）；判官 gemini-3.7-flash
- 实例级并发（`--workers`=并发实例数，实例内五路全并行）

**出题（eval_synthesize.py + synthesize_prompt_gen_v5.5.md）——本批协议版本 = v5.5**
- **无图无 caption**：v5.3+ 分支消息纯文本；题面场景自由设计（一般领域知识）
- 任务先行：基于概念+考察内容（清单+三大支柱）出题
- 保真检查**恰好 10 条**（不足如实全用+notes）；三档=**0 Fail / 1 Pass / 2 Excel**（QIB 刻度）
- **核心规则·粒度一致**：检查锁定粒度不得超过题面锁定粒度（构图自由度/变体/无中生有三情形；无中生有=「题面与硬约束达成一致的前提下提供」）
- 纵排鼓励**多跳**（中间结论作后段前提）；锚点推导链模板支持多跳
- 质量/美感：出题人**不选不输出**判分项（判分端 10 项全量判）；**对齐同制**（v5.5.3，见 §6）：判官基于题面×生成图直判，出题人不再产出 alignment_checks；schema 已删 quality_facets/aesthetic_facets/weight/alignment_checks
- 机审 `exact10`（恰好 10 条）；负向独立成条、维度权重节已废除；**LLM 质量审计层（eval_audit.py + audit 册）已废除**（2026-08-29 拍板）——结构机审（audit_v53，随出题实时、只告警不拦截）是唯一机审，验收标准 = 机审 0 warn

**判分（eval_score.py）**
- 刻度全对齐 QIB：{0 Fail, 1 Pass, 2 Excel, N/A}，N/A 剔除出均值；判官总则含旧掌握度措辞 rubric 的映射规则（及格档→0/1 按明显度，满分档→1）
- 对齐/质量/美感**三组全量判**：判官全部直判、题面无对应规定记 N/A 剔除（对齐组判据为题面显式规定、质量/美感为 ACTIVATE 条件）；**双语准则**（中文操作主文 + QIB Tab.7 英文原文锚）。落码沿革：v5.5.2（2026-08-29 晚）对齐全词表判+出题人检查作附挂 → **v5.5.3（同日晚，最终口径）附挂层废除、alignment_checks 出题端全废**（见 §6）
- 聚合：`total = G × (0.2 + 0.8×F/100)`（v5.5.4 保真乘子加地板，见 §6）；G=对齐/质量/美感**三支柱等权**；F=保真线（defining 检查 ×2.0）；**无封顶无熔断**（neg/align/quality violated + fidelity_low 仅诊断标记）
- 28 条词表 = QIB Tab.7 中文化裁剪（Q6→7 拆noise增解剖、A6→3 删emotional挪style、Al16→18 增subject_prominence挪style）；`判档（QIB 刻度）`内嵌各facet锚，对齐组问句已归位

**notebook**（question_dev.ipynb）
- 顶部「批次版本总开关」格 `_BT`：probe_dir/probe_file/samples_f/bank_dir/bank_files/bagel_bank/bagel_run 一处配齐；判官自动发现 `JUDGES=[]`

## 2. 模型路由（网关 127.0.0.1:4001）

| 角色 | 模型 |
|---|---|
| 探针五路 | glm/glm-5.2、glm/glm-5.3-flash、qianwen2/qwen3.8-flash、galaxy/deepseek-v4-flash-0731、galaxy/minimax-m3 |
| 探针合并 | openrouter/openai/gpt-5.6-sol（通配路由，列表不枚举属正常） |
| 预检 | openrouter/google/gemini-3.7-flash |
| 出题 | openrouter/openai/gpt-5.6-sol |
| 判分 | openrouter/google/gemini-3.7-flash（temp=0/seed=42，确定性已验证） |
| Bagel | GPU1（vLLM judge 在 GPU0 :8000，勿占） |

## 3. 新批执行序列（建议批次标签 `synth_gen_20260829_v3`，规模建议 20）

```bash
cd /tank/demiwtg
BT=20260829_v3   # 新目录，与旧批零覆写

# 1) 探针（新合并册：10~12 条供给）
python3 benchmark/t2i/eval_probe.py generate \
    --samples benchmark/t2i/data/samples_20260828_v2.jsonl \
    --limit 20 --offset 15 --workers 3 \
    --out-dir benchmark/t2i/data/synth_gen_$BT
# offset=15 跳过 pilot 已用的前 15 实例；断点续跑同命令重发

# 2) 预检（增量；gemini）
python3 benchmark/t2i/eval_probe.py precheck \
    --samples benchmark/t2i/data/samples_20260828_v2.jsonl \
    --constraints benchmark/t2i/data/synth_gen_$BT/constraints.jsonl \
    --workers 2

# 3) 出题（v5.5 册：无图、恰好 10 条、QIB 刻度；sol）
python3 benchmark/t2i/eval_synthesize.py --schema v5.5 \
    --models openrouter/openai/gpt-5.6-sol \
    --samples benchmark/t2i/data/samples_20260828_v2.jsonl \
    --constraints benchmark/t2i/data/synth_gen_$BT/constraints.jsonl \
    --out-dir benchmark/t2i/data/synth_gen_$BT \
    --max-tokens 32768 --workers 2

# 4) Bagel（GPU1；题库 → 运行目录快照）
mkdir -p benchmark/t2i/data/eval_bagel_v55
cp benchmark/t2i/data/synth_gen_$BT/questions_v55_gpt-5.6-sol.jsonl \
   benchmark/t2i/data/eval_bagel_v55/questions.jsonl
CUDA_VISIBLE_DEVICES=1 python bagel/Bagel/scripts/run_wkbench.py \
    --questions /tank/demiwtg/benchmark/t2i/data/eval_bagel_v55/questions.jsonl \
    --out_dir /tank/demiwtg/benchmark/t2i/data/eval_bagel_v55 --tasks t2i

# 5) 判分（gemini，终态判官 prompt）
python3 benchmark/t2i/eval_score.py score \
    --questions benchmark/t2i/data/eval_bagel_v55/questions.jsonl \
    --responses benchmark/t2i/data/eval_bagel_v55/responses_shard0.jsonl \
    --out benchmark/t2i/data/eval_bagel_v55/scores_gemini-3.7-flash.jsonl \
    --endpoint http://127.0.0.1:4001/v1/chat/completions \
    --model openrouter/google/gemini-3.7-flash \
    --constraints benchmark/t2i/data/synth_gen_$BT/constraints.jsonl \
    --workers 2
```

## 4. 验收点

1. 探针：阈值通过率 ≥70%；清单 10~12 条为主（<10 属知识密度偏低，notes 会注明）；负向占比 15~35%
2. 预检：剔除率 5~15%（过高→样本适宜性问题，对照 `t2i_image_suitability_constraint` 红线）
3. 出题：机审 0 warn（重点：恰好 10 条、锚点 rs⊆清单编号、泄漏对照全「无」、无 alignment_checks / quality_facets / aesthetic_facets 输出）——结构机审是唯一机审，无 LLM 质量审计步骤
4. 判分：judge_fail=0；N/A 主要落在无人物图的 anatomical_fidelity；overall 预期落在 30~60 区间（QIB 量尺下顶模也只有 60 上下，勿按旧 80+ 直觉判读）
5. notebook `_BT` 切新值即可看全链：`probe_dir='synth_gen_20260829_v3'`、`bank_dir` 同、`bagel_run='eval_bagel_v55'`

## 5. 已知坑（上一窗口实测）

- 网关冷连接首并发会被掐（RemoteDisconnected），30s 退避自愈，勿手动干预
- 出题/判分 resume 按 qid 跳过已存在行；**改过 prompt 后要重跑必须先删产物 jsonl**（raw/ 缓存同理）
- `--limit/--offset` 组合从 offset 起取 limit 个（上面 offset=15 即实例 0016~0035）
- report json 只反映最后一次运行的题集（续跑时=增量题集），全量统计看 scores jsonl 或 notebook
- 千题批次配额/超采回填公式见 `data/probe_pilot_handover.md` §4（本批先不管，新协议跑顺再上量）

## 6. 首批执行记录（2026-08-29，本窗口，规模 10 题）

**产物**：探针/题库 `data/synth_gen_20260829_v3/`（题库 `questions_v55_gpt-5.6-sol.jsonl` 10 题 = 0016~0019 + 0021~0026）；Bagel/判分 `data/eval_bagel_v55/`（10 图 + `scores_gemini-3.7-flash.jsonl`）。

**实例换位**：0020 阿斯顿马丁（与 0019 同实例）合并仅得 3 条清单，sol 出题三次均锚点幻觉到 #4/#5（3 条清单撑不起 10 锚定检查，结构性失败非偶然）→ 换 0026 宁波轨道交通补位，0020 记录/题已从本批清除。注意 cmap 按**实例名**键控（`load_constraint_map` 后行覆盖），同实例两样本共享同一清单。

**代码修复（本窗口）**：
- `eval_synthesize.py` audit_v53：exact10 分支原写法 `if exact10 and len!=10 … elif not 5<=len<=7`，恰好 10 条时落入 elif 必然误报「不在 5~7」——已改为 exact10 时只判恰好 10，v5.3 行为不变。**v5.5 出题必须 0 warn 的前提就是此修复。**
- `eval_score.py` 判分记录 schema 标签：原硬编码 "v5.3"，现按 fidelity_checks 条数推导（10→v5.5）；本批 scores jsonl 为修复前产物，标签仍 v5.3，无消费逻辑、仅元数据。

**环境坑**：PATH 的 `python`（miniconda）无 transformers，run_wkbench 必须用 `/tank/demiwtg/.venv/bin/python`。

**验收实况**：探针 11/11 过阈值（含被换掉的 0020）；清单 4~11 条/实例（<10 的有 notes，0024 仅 4 条）；负向占比 41/83=49%，**超出 §4 的 15~35% 带**（0025/0026 轨道交通类 7/10、7/9 拉高，下批观察是否系统性）；预检剔除 1/87≈1.2%（低于 5~15% 带，方向性无害）；出题机审 **0 warn**；Bagel 10/10 图；判分 judge_fail=0，N/A 7 例全落 anatomical_fidelity，**overall 11.08**（低于 30~60 预期带：4 题保真全灭致 F=0，如 0018 画成人类背影非机甲——Bagel-7B 真实失败非流水线 bug，逐题理由已核；30~60 带按更强模型校准，小模型量级另议）。分项：fid 22.8 / align 33.0 / qual 57.0 / aes 59.3。

**v5.5.2 对齐组全量判（2026-08-29 晚，用户拍板，本日第二批执行）**：用户澄清对齐组与质量/美感同制——Alignment 18 项全词表判、题面未含该类显式规定记 N/A 剔除出均值（上文 §1 原写「质量 7 + 美感 3 全量判」漏了对齐组，实现原先按出题人 2~4 条子集判，均已被本条推翻）。落码：`eval_score.py`（GENERIC_ALL 增 Alignment、judge prompt 对齐节全 18 项+出题人检查附为「题面具体化」、输出改 facet 字典、解析/记录/诊断同步）+ 出题册 §四 第 6 条 + notebook ⑥ 格 `_align_rows` 双格式（旧批 index 列表式兼容）。本批 10 题已删旧 scores 重判：judge_fail=0，overall 11.67（原 11.08），alignment 40.41（原 33.0），fid 22.56 / qual 56.0 / aes 60.0；180 项中 N/A 131、实判 49（4.9/题），判官 N/A 判定与出题人 facet 归类吻合且会自主激活未编码类目（如写实风格→style_control）。**判分口径自此以本条为准；此前任何按 2~4 条子集口径的判分产物不可比。**

**v5.5.3 对齐组出题端废除（2026-08-29 晚，用户拍板，终态口径，推翻 v5.5.2 的附挂层）**：用户裁定——对齐规则是题面的显式规定，判官拿题面×生成图即可直判，出题人那层「翻译成 alignment_checks」是多余中间层，与质量/美感同制废除（QIB 即此机制）；只有保真检查保留个性化产出，因其携带题面外长尾知识、判官无从自推。落码：出题册 §四 重写为「对齐判分项不由出题人产出」+ 四维表/schema/自检清单同步（schema 字段 alignment_checks 删除）；`eval_synthesize.py` 机审 v5.5 分支改为「输出 alignment_checks 则告警」（旧规则仅留 v5.3 分支）；`eval_score.py` 构建去题面具体化附挂（判官纯基于题面×图判 18 项）；现批 10 题题库（+ Bagel 快照）剥离 alignment_checks 死字段，不重出题（题面与保真检查不受影响）。本批已删 scores 重判：judge_fail=0，overall **12.06**，alignment **47.77**（判官直判比附挂版多抓 6 项/题，如 0016 新判出 shape/size/difference_similarity），fid 23.14 / qual 56.57 / aes 56.0。**出题与判分口径以本条为准（v5.5.3），v5.5.2 与旧子集口径均不可比；总分聚合另见下方 v5.5.4。**

**v5.5.4 保真乘子加地板（2026-08-29，用户拍板；纯聚合改动，零 LLM）**：背景——本批总分 12.06，corr(F, total)=0.987，总分几乎被保真线单独决定（4 题 F=0 → 全灭 0）；三支柱（对齐/质量/美感）均值 48~57、标准差仅 7.4，贴 60 基线（QIB 三档 1 Pass=60 为合格基线档，小模型支柱天然贴顶），区分度集中在保真线。拍板改方案 B：`total = G × (FLOOR + (1-FLOOR) × F/100)`，`FIDELITY_FLOOR = 0.2`（eval_score.py 常量，0 ⇒ 退化为纯乘子）。知识门控语义保留占主导，但主体画错（F≈0）仍保留 20% 的 G，三支柱信息不被全灭、曲线平滑。落码仅 `finalize_v53` 公式一行 + 常量 + 报表文案；判官 prompt 未动，**离线用已存线分重算 total 与 report（未重调判官）**。重算后本批：overall 12.06 → **20.34**（0018/0021/0023 由 0 → 11.0/12.0/8.7，0024 33.3 → 37.8）。**聚合口径自此以 v5.5.4 为准。**
