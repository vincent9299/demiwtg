# Bagel t2i V5 评测工作交接(2026-08-26 创建,同日 18:50 多窗口汇总更新)

> **本文档是 t2i 赛道跨窗口工作的唯一入口**。新窗口开工先读本文档;干完活把增量写回 §4/§5/§6 并更新 §0/§1。
> 项目级硬约束见仓库根 `AGENTS.md`;t2i 子模块布局见其中「架构决策 2026-08-24」。

## 0. TL;DR 现状(先读这五条)

1. **Bagel V5 评测(30 题)已全链路跑完**:推理 30/30 + 三套判分交叉验证完毕。当前正式口径 = `scores_ctx32k.jsonl`(overall **46.65**,judge_fail 0,解析 30/30);对照判官 = `scores_gemini-3.1-pro.jsonl`(overall 35.55)。两判官 |diff|≤15 的 22/30,**剩余分歧方向一致:qwen 系统性偏宽**(见 §3 分歧榜),Bagel 真实水平待人工看图裁决。**2026-08-26 晚 V5.2 批次(20 题/2 模型)已出+已审+已判,见 §3b:质检 20 过 0 拒,双判官 gap 扩大,仲裁指向 gemini 判官 gate 过敏。**
2. **判分管线修过两轮**(都在 2026-08-26):① judge `max_tokens` 2048 截断 → 逐题动态预算;② vLLM 上下文 8192→**32768** + `extract_json` 抗示例污染重写,把「17/30 题 schema 缺判默认 0 分」的误判彻底消除(首跑 19.43 → 修复后 46.65)。
3. **v5.1 A/B 已完成,设计定案**:v5.1 出题+质检 prompt 落地并跑通 20 题(gpt56+gemini×10,0 拒题;v5 基线 gemini 泄漏拒题 2/10→0);三轮讨论后**设计轴从「题型开放度」改为「样本 solid 知识密度」,全 closed+多节点题目,题型路由作废**(见 §4b)。密度预探第一级(存量代理)已落 notebook 并校准:θ≥1 粗筛有效、θ≥2 误杀全部 pilot 样本,分层须靠第二级 LLM 探针(§6.3)。V5.2(判分链)另窗已跑完 20 题批次(见 §3b)。
4. **V5 题库(现役 30 题)已过机审+语义审**:28 accepted / 2 rejected(`gemini-003`、`gemini-0008`,后者即拉奥孔题),quality 均分 91.4 —— 见 `synth_gen/audit/questions_v5_audit.jsonl`。
5. **环境**:judge vLLM = 单卡 TP=1 @ GPU1、`--max-model-len 32768`(17:02 重启);GPU0 空闲;modelhub 网关 4001 可用(详见 §2)。
6. **2026-08-27 下午起:出题协议 v5.3 定稿 + 密度探针横评收口**:探针经三轮纠偏重定义为**概念级硬约束**(极性/变体枚举,无图无 caption,输出=出题知识供给);`synthesize_prompt_gen_v5.3.md` 写成纯终态(四维判分 0.5/0.2/0.2/0.1、保真检查 5~7 条锚定约束清单、调用方式仅 L2 组合/L3 推导、机械关键项封顶);旧版 prompt 全删。20 case × 4 模型横评定案生成源组合 **gpt-5.6-sol + glm-5.3-flash + qwen3.8-flash 三路生成→合并去重**(单模型 3.3~5.1 条撑不满一题,合并去重前均 16.3;gemini-3.7-flash 出局)。待办:代码物化(见 §6.9~6.11)。
7. **2026-08-27 晚~28 晨:v5.3 出题全量 24/24**:5 个强样本 × 5 模型(gpt-5.6-sol/glm-5.3-flash/qwen3.8-flash/gemini-3.7-flash/gpt-5.6-luna),机审 23/24 干净、泄漏对照全「无」;弱样本(挪威峡湾/魔戒有声书/东阿阿胶块)排除;缺 0005_glm(glm 对重概念病理四连败)。详见 §4c。
8. **2026-08-28:v5.3 判分协议落地 + Bagel 首评(120 判分 0 失败)**:`eval_score.py` v5.3 重建(四维 0.5/0.2/0.2/0.1 + 机械封顶),五判官(本地 vllm 27b / gpt-5.6-sol / gemini-3.7-flash / qwen3.8-flash / glm-5.3-flash)判 Bagel 24 图:overall 57.3 / 44.8 / 38.7 / 53.0 / 50.1,qwen 系偏宽、gemini 最严(与 V5.2 同向)。**出题人对比**(五判官×所出题均分):luna 70.7 > gemini 66.1 > glm 47.0 > qwen-flash 33.2 > gpt-sol 26.5——分数低≠题差,可能是题更严暴露短板,待人工核判据裁定(§4d 待办)。`question_dev.ipynb` 已有「出题人质量对比」格。
9. **2026-08-28 下午:协议裁定全落地 + 原图基线 + 出题人裁定 + 千题管线定案(详见 §4e)**:① 五项判分/出题裁定全部实施——保真检查恰好 7 条(v5.4)、L3 占 70%、defining×2 计权、质量乘子 total×(0.6+0.4q)、判分项词表结构清理(7/3/18 三组);② 原图基线判分 120 条(`eval_bagel_v53/scores_orig/`)——醒狮 20/咯吱盒 25.6/TCG 35.7 连原图都过不了,暴露 checks 超纲;③ gemini-3.7-flash 定案单判官;出题人裁定 sol=公平难题/glm=超纲成分(逐题点评见 §4e);④ 探针选型:五路免费生成(glm-5.2/glm-5.3-flash/qwen3.8-flash/ds-v4-flash/minimax-m3)+ sol 合并编辑(`probe_merge_prompt.md`)+ 双机检门;⑤ `eval_score.py` 曾因 RWF 删除正则吞尾事故,经 git 尾段+pyc 反汇编 oracle 重建,**240 条存量判分逐字段回放验证等价**(69 条差异=词表清理定案变更,非重建误差)。⚠️ benchmark 代码自 8-25 提交后未再 commit,本次险些无法恢复——下窗口务必先 commit。

## 1. 索引

| 主题 | 入口 | 说明 |
|---|---|---|
| **评测产物** | `data/eval_bagel_v5/` | imgs/ 30 张 + responses_shard0.jsonl + 三套分数(§3) |
| **结果审阅** | `results_review.ipynb`(由 `gen_results_review.py` 生成) | SCORES→`scores_ctx32k.jsonl`、SCORES_B→gemini 对照;分析格第 2-7 节 + 逐题明细卡(双 judge 总分并排) |
| **题目审阅** | `question_dev.ipynb` | 抽样+分布+**密度预探**(供给面/按域缺口 + pilot 校准,§6.3)+题库审阅 |
| **判分脚本** | `eval_score.py` | `score`/`dump` 子命令;改动记录见 §5 |
| **出题 prompt** | `synthesize_prompt_gen_v5.3.md`(终态草案,待用户终审) | 四维判分+硬约束锚定;旧版(v5/v5.1/v5.2/route)已删,内容看 git 历史;修改史 `data/v5_change_log.md` |
| **密度探针契约** | `probe_prompt_solid_facts.md`(硬约束定义,无图无 caption)+ `probe_review_prompt.md`(复审环节,暂挂) | 判定 ≥2 条硬约束;产物 `data/density_probe/`(cases v4 + results v1.1~v1.3+v4 横评 80 行) |
| **题目质检** | `eval_audit.py` + `audit_prompt_question_quality{,_v5.1}.md` | 机审(结构断言)+语义审(默认 galaxy/qwen3.8-max),quality=0.3×机审+0.7×语义,红线一票否决 |
| **题库与出题中间产物** | `data/synth_gen/` | 现役 `questions_v5.jsonl`(30 题);V5.2 批 `questions_v52.jsonl`(20 题);`audit/` 审计结果;`raw/` 出题原始响应;`archive/` 旧批归档(三个日期目录) |
| **v5.1 A/B 产物** | `synth_gen/questions_v5.1.jsonl` + `audit/questions_v5.1_audit.jsonl` | 20 题(gpt56+gemini×10),20 过 0 拒;结论见 §4/§4b |
| **~~题型路由 prompt~~** | ~~`route_prompt_sample_class.md`~~ | 已删(§4b 定案作废,2026-08-27) |
| **V5.1 pilot(进行中)** | `synth_gen/questions_xiaoyao_*.jsonl` + `questions_or-claude-fable-5.jsonl` | 状态见 §4 |
| **judge prompt 契约** | `eval_score.py:161-184`(源)+ `data/judge_prompts/`(物化:`eval_score.py dump`) | 输出格式:`{gate, knowledge_checks:[{index,score}], facets:{key:档}}` |
| **环境/部署** | §2;原始部署脚本 `models/deploy_watchdog.sh` | vLLM 启动参数沿革在该脚本注释里 |
| **历史过程文档** | 本目录其余 `*.md`(review_*/fable-5-review*/gpt-5.6*/v5_*) | 前序窗口的评审与请求记录,只读参考 |

## 2. 环境现状

- **judge vLLM(qwen3.8-27b,`localhost:8000`)**:单卡 **TP=1 @ GPU1**,当前运行参数:
  `--tensor-parallel-size 1 --port 8000 --host 0.0.0.0 --max-model-len 32768 --gpu-memory-utilization 0.98 --max-num-seqs 320`。
  启动需带:`HF_HUB_OFFLINE=1`、`CPATH/LIBRARY_PATH` 指向 `models/.venv-vllm` 内 `nvidia/curand`、去掉代理(完整模板见 `models/deploy_watchdog.sh`,其中参数是旧双卡版:`--max-model-len 8192` + TP=2;恢复双卡吞吐按它重启,并把 `JUDGE_CTX_LEN` 常量同步改回)。
  **判分主口径与该 judge 绑定,勿换模型当主判官。**
- **GPU0 空闲**,可跑下一轮推理。Bagel 推理命令(按 qid 断点续跑):
  ```bash
  CUDA_VISIBLE_DEVICES=0 /tank/demiwtg/.venv/bin/python bagel/Bagel/scripts/run_wkbench.py \
    --tasks t2i --questions <题库.jsonl> --out_dir benchmark/t2i/data/<批次目录>
  ```
- **modelhub 网关** `127.0.0.1:4001`(LiteLLM,OpenAI 兼容):`openrouter/*` 通配透传。实测可用:`openrouter/google/gemini-3.1-pro-preview`(对照判官)、`openrouter/google/gemini-3.7-flash`(出题用过)。**`gemini-3.7-max`/`3.7-pro` 经 OpenRouter 校验不存在**,勿再试。
- 外部模型判分:`eval_score.py score --endpoint http://127.0.0.1:4001/v1/chat/completions --model <名称> --out <产物>`(预算走 `--max-tokens` 兜底,无需 /tokenize)。

## 3. Bagel V5 评测结果(已跑完,待人工裁决)

**三套判分对照**(同一批 30 张图):

| 判分产物 | judge | overall | gate 封顶 | 知识熔断 | judge_fail | 定位 |
|---|---|---|---|---|---|---|
| `scores.jsonl` | qwen3.8-27b(ctx 8192) | 19.43 | 1 | 22 | 0(但 17 题 schema 缺判→默认 0) | 首跑,已被 ctx32k 取代 |
| **`scores_ctx32k.jsonl`** | qwen3.8-27b(**ctx 32768**) | **46.65** | 2 | 12 | 0(解析 30/30) | **当前正式口径** |
| `scores_gemini-3.1-pro.jsonl` | gemini-3.1-pro-preview | 35.55 | 15 | 19 | 0 | 对照判官(用户拍板 double check) |

**已确认的结论**:
- 首跑的 17 个「0 分」多数是判分侧缺省误判(schema 缺判→0 档缺省→熔断/封顶连锁),ctx32k 修复后已正名;
- 两判官 |diff|≤15 的 **22/30**;剩余分歧**方向一致 = qwen 偏宽**(check 档位给 1/2、gemini 给 0)。分歧榜前 5:`gpt56-0008`(ctx32k=100 vs gemini=20,拉奥孔群像/蛇之争)、`gpt56-0005`(91.6 vs 20,空间站结构,gemini gate)、`gemini-0001`(72 vs 20)、`gpt56-0002`(67.6 vs 20)、`gemini-0005`(53.8 vs 18.6)。
- gemini 的 gate 判据具体且可验证(如"雕像单人无蛇""空间站形似火箭插方块");**哪边对需人工看 `results_review.ipynb` 分歧题的图**。注意 `gemini-0008`(拉奥孔)同时被题目质检 rejected(见 §4),该题本身可疑。
- 通用线普遍高于知识线:Bagel 图「观感尚可、知识不中」;且 512×512 分辨率对细节类 rubric 天然吃亏。

## 4. 出题协议 v5.1:已跑完(2026-08-26~27)

- **v5.1 出题 prompt**(`synthesize_prompt_gen_v5.1.md`):只加三项——① 每条 check 声明题型 `coverage`(品类题必须把合法派别写全进 rubric 档,否则收窄题材);② 反例测试三方向各构造一次(漏派别/挡视角/透视变形);③ 泄漏检查产出 `leak_check` 对照表,全「无」才准输出。核心主轴不变:「每张合法画法必须恰好落进 rubric 某一档」。
- **审计侧同步**:`audit_prompt_question_quality_v5.1.md`(D4 对质 leak_check、D5 核题型、D6 三方向反例);`eval_audit.py` 支持 `--mech-only`;断点续审;`extract_json_object` 改 `raw_decode` 取首对象(质检 judge 偶发输出多个 JSON,2/20,确定性解码稳定复现,修后 0)。
- **v5.1 pilot 完成**:同 10 样本 × (gpt-5.6-sol + gemini-3.7-flash,用户拍板剔除 fable-5),galaxy/qwen3.8-max 质检 → **20/20 accepted,0 拒题**。产物 `synth_gen/questions_v5.1.jsonl` + `audit/questions_v5.1_audit.jsonl`;v5 原始分文件+raw 归档 `archive/2026-08-26_v5_pilot/`。
- **A/B 结论**(v5.1 vs v5 同模型同样本):
  - 泄漏对照表有效:gemini 结论句直抄泄漏 **2/10 → 0**,D4 尾部归零;gemini qid 缺位也消失;
  - **救援失败项**:gemini D6 反例 1.10→1.10 纹丝不动;D5 反而 1.30→1.00(十个 1)——gemini 24/28 声明 `closed_entity`、0 收窄,**把开放题谎报定形题绕过谱系要求**,被新审计的声明真实性核查抓住。能力墙坐实;
  - gpt56 D6 1.20→1.40(2/10→4/10 满分),规则对强模型也有小幅增益;
  - **口径警告**:v5.1 审计反例构造密度 3.3 条/题(v5 为 1.9),绝对分跨版本不可比,趋势只比拒题率/红线数。
- **旧批归档**:`synth_gen/archive/2026-08-26_v5_pilot/`(v5 首跑分模型源文件+raw)、`archive/2026-08-26_v5旧版分文件/`、`archive/2026-08-26_旧批遗留/`。

## 4b. 设计定案:全 closed + solid 密度轴(2026-08-27 三轮讨论,用户确认)

1. **所有题的 rubric 必须闭合**——合法画法全落档;open 从来不是成品态。「定形/品类」只是生产成本标签,不是成品属性;判别力与开放性正交(住在派别内部视觉知识里),收窄问法几乎不丢判别力。
2. **覆盖面靠一题多节点**(checks 沿推理链纵排/场景实体横排,2~4 条与题型解耦)+ 题目分布保证;全 closed 题库无实质代价(案例证据:gpt56-0003 三节点链、gpt56-0007 金襴手+置行灯横排、东京塔示例 4 节点)。
3. **题型路由作废**:`route_prompt_sample_class.md` 已写但废弃(弱模型分工只剩纯成本考量);gemini 连定形题的 rubric 写作也弱于 gpt56(四个纯定形样本 D5 全 1),路由只降风险不拉平差距,最终靠修复环兜底。
4. **设计轴 = 样本 solid 知识密度**:收窄约束联合后可行题空间=密度函数;密度高出多节点题,不够拒题/换样。**拒题应前移到抽样环节做密度预探**(存量代理=instances.json 富知识程度;准=LLM solid 事实探针,列不出 ≥2 条即弃)。
5. **跨模块接口**:gen_instance_kb 实例富化 = 题库密度供给;长尾域题少的解法是富化该域实例,不是放宽出题标准。
- 存档:项目记忆 `t2i_design_axis_solid_density`(定案)、`t2i_v51_ab_results`(A/B 数据)。

## 4c. v5.3 出题全量(2026-08-27~28,24/24)

- **批次构成**:5 个强样本(最终幻想TCG/国际空间站/拉奥孔/广东醒狮/咯吱盒)× 2 模型 = 目标 10 题。弱样本(挪威峡湾/魔戒有声书/东阿阿胶块,诊断见 §6.3 补充与项目记忆)不入试。
- **知识供给**:探针产物**人工语义合并去重**(未走 LLM 合并)落 `synth_gen/constraints_v53_trial.jsonl`,每样本 6~7 条编号约束(极性/变体/依据/来源模型)。这是 §6.9 合并步的第一份金标准样例。
- **结果**:24/24(初轮 9/10 + 增补三模型 15/15)。增补批:qwen3.8-flash 5/5、gemini-3.7-flash 5/5、gpt-5.6-luna 5/5(后两者单题 20~40s 极快,qwen 慢且 429 频但退避自愈)。机审 23/24 干净(唯一软发现仍是 0002×glm 的 alignment 重复 quantity);泄漏对照表全「无」;L3 题推导链齐全。产物 `questions_v53_<模型短名>.jsonl` ×5,题库审阅格(`question_dev.ipynb`)QUESTION_SOURCE 已含全部 5 文件。缺 `0005_glm`(四连败放弃)。
- **增补批的工程发现**:① gemini/luna 爱写 `[1]` 括号锚点与尾逗号(`"tier_2": "...",}`)——解析器已加尾逗号容错(`_lenient_object` 逐次剪逗号重试),锚点审计归一化去括号;② 解析失败即弃 raw(防续跑复用死循环);③ 重复进程并发写同一题库会产生重复行——去重策略=按(样本)保留审计缺陷最少的一行;④ 0010×luna 曾产出尾逗号坏 JSON,容错解析从既有 raw 直接救回(零重调)。
- **0005_glm 四连败定案**(不再重试):16k 假拒题(自创 insufficient_constraints,实际 6 条在 5~7 内)→ 16k 中段截断 → 32k L3 思考死循环 → 32k L2 仍死循环。raw 实锤:32,768 reasoning token 全耗,尾迹停在泄漏措辞纠结("hmm…Ugh. Decision: keep 「绝对主体」"),正文零。结论:**glm-5.3-flash 对重概念(多负向约束+严防泄漏)存在病理性思考内耗**,生成源三路定案不受影响(该模型在轻量样本 4/4 正常)。
- **模型对比**(同题异模):gpt-5.6-sol 题面克制、锚点纪律好、L3 推导链规整;glm-5.3-flash 场景创意更足(醒狮暴雨反事实、咯吱盒掰开朝镜头),但纪律性弱(对齐项重复、曾假拒题)。与探针横评结论一致。

## 4d. Bagel 推理 + 五判官判分(2026-08-28,120 判分 0 失败)

- **推理**:合并题库 `synth_gen/questions_v53_all.jsonl`(24 题,补 `task` 字段、qid 撞车 0009-L3-01 加 @模型后缀)→ `run_wkbench.py --tasks t2i` GPU0 → `eval_bagel_v53/`(imgs/ 24 张 + responses,4.4 分钟,512×512)。
- **判分协议落地**:`eval_score.py` v5.3 重建(§5)——判官只看生成图+题面+四类检查项(**不看样本图、不看约束清单、关键项零标注**),四维线分 φ 映射后加权 0.5/0.2/0.2/0.1,机械封顶四条(负向关键/对齐/质量缺陷 0 档封顶 20、保真整线 <40 熔断);判分 prompt 物化 `judge_prompts/t2i_judge_template_v5.4.md`(2026-08-28 由 v5.3 改名,内容不变——v5.4 判分侧改动全在机械层不进 prompt),用户拍板:不给原图、reason 暂不限字数。
- **五判官结果**(各 24 题,均 0 解析失败):

  | 判官 | overall | 保真 | 对齐 | 质量 | 美感 | 负向/对齐/质量/熔断封顶次数 |
  |---|---|---|---|---|---|---|
  | 本地 vllm qwen3.8-27b(--think) | 57.28 | 66.6 | 85.2 | 85.7 | 92.1 | 3/6/0/3 |
  | qwen3.8-flash | 53.01 | 64.4 | 84.4 | 84.2 | 93.8 | 7/6/1/3 |
  | glm-5.3-flash | 50.12 | 62.5 | 79.5 | 84.7 | 94.2 | 4/8/1/4 |
  | gpt-5.6-sol | 44.75 | 58.9 | 77.0 | 72.5 | 87.4 | 6/11/1/4 |
  | gemini-3.7-flash | 38.68 | 56.9 | 78.4 | 79.2 | 87.7 | 5/11/0/6 |

- **判官一致性**:两两 |diff|≤15 占比 54%~83%(vllm↔glm 83%、vllm↔qwen-flash 79%);gemini 系统性最严(vs vllm 均差 -18.6),与 V5.2 时代「qwen 宽/gemini 严」同向;比 v5.2 时代(29%~63%)明显收敛。产物 `eval_bagel_v53/scores_<judge>.jsonl` ×5 + `.report.json`。
- **出题人对比**(五判官×所出题均分,`question_dev.ipynb`「出题人质量对比」格可视化):**gpt-5.6-luna 70.7 > gemini-3.7-flash 66.1 > glm-5.3-flash 47.0 > qwen3.8-flash 33.2 > gpt-5.6-sol 26.5**。gpt-sol 的题 5 样本里 4 个被五判官一致压到封顶 20(TCG/拉奥孔/醒狮/咯吱盒)。**解读注意:分数低≠题差**——可能恰是题更严更深、暴露了 Bagel 真实短板;需逐图核封顶判据(判官 reason 未限字数,可直接查证据)区分「测出短板」与「误伤」。
- **题目效应 > 判官效应**:同一张拉奥孔图,luna 出的题五判官 66~100、gpt-sol 出的题五判官一致保真 0 档熔断 20。出题模型差异是真实分数主来源;对齐线封顶是 Bagel 当前最高频失分点(指令遵循细节)。
- **新窗口待办**:① 逐图人工核判官分歧题(重点:0008 拉奥孔五判官全灭的 gpt-sol 题、0002×qwen-flash 20 分),定主判官口径;② 出题人质量结论裁定(上条解读);③ `results_review.ipynb` v5.3 适配(gen_results_review.py 还是 v5.2 schema)。

## 4e. 判分协议裁定落地 + 原图基线 + 千题管线(2026-08-28 下午)

### 4e.1 原图基线判分(120 条,`eval_bagel_v53/scores_orig/`)
- 五判官按各题同一套 checks 给**样本原图**打分(responses_orig.jsonl,qid×原图;重跑入口 `eval_score.py score --responses .../responses_orig.jsonl`)。
- 按样本基线:**0008 拉奥孔 99.4 / 0005 ISS 46.9 / 0002 TCG 35.7 / 0010 咯吱盒 25.6 / 0009 醒狮 20.0(封顶)**——后三者原图自己都过不了,证明其 checks 依赖照片偶然内容(风向/挂青/logo/断面色),撞样本适宜性红线。
- 注意口径:原图 alignment 失败是预期(照片不照题面画);判断超纲看**原图保真线**。

### 4e.2 出题人逐题裁定(sol 5 题 + glm 4 题,gemini 口径)
- **sol = 公平难题**:4/5 题原图保真≥80(TCG/拉奥孔原图满分),低分全因 Bagel 真短板(计数布局/文字渲染/IP 版式/实体名唤起/物理瞬间);0009 醒狮保真 88.6 仅因"风向"单项 alignment 封顶 = 规则杀题非题杀题。0008 极简题面(37 字只给实体名)是合法知识探针(测名字→知识唤起),但与 luna 题不同构念。
- **glm = 场景创意好、口径纪律弱**:2/4 题把场景道具(补充包/骰子/3+2 布局)或反事实前提效应(雨/湿垂)写进保真线,原图保真被污染(39.3);0008 题(描述性题面+公平 checks)是其最佳题。
- 共同坑:0010 两家 F4/F6 连原图都 0 档——根因是金标准约束"内里黄绿色/无馅"与样本照片断面实貌冲突 → 出题上游需**约束↔样本图一致性预检**。
- gemini 单判官口径难度排名:glm 20.0(4/4 全封顶)≈ sol 20.0(5/5) > qwen 32.8 > luna 51.2 > gemini 自题 65.7。

### 4e.3 判分/出题协议五项裁定(全部已实施)
| # | 裁定 | 落点 |
|---|---|---|
| ① | 保真检查恰好 7 条 | v5.4 prompt 三处 + audit_v53(exact7) |
| ② | L3 占 70% | eval_synthesize --invocation 默认 L3:7,L2:3(最大余数交错轮转) |
| ④ | defining×2 计权 | eval_score defining_check_idx/DEFINING_WEIGHT(清单无 tier 字段时空集回退=v5.3 行为);constraints_v53_trial.jsonl 已回填 tier;probe_merge_prompt 第五步标 tier |
| ⑤ | 质量乘子 | finalize_v53:封顶/熔断后 total×(0.6+0.4×质量线/100) |
| ⑥ | 词表清理 | FACETS 7/3/18 三组:删 resolution/emotional_expression/RWF 12 死条目;noise→detail_richness+artifacts;anatomical_fidelity→质量组;style_control→对齐组;29 条准则全部重写(三段式 0/1/2 档锚点) |
- alignment 封顶**保留不改**(单判官定案后判官噪声论失效;治理移到出题端 rubric 0 档措辞:必须"明确相反或完全缺失",禁"不够明确")。
- 三档 {0,1,2}/φ(1)=60 **维持**(1 档真实使用率 22%/17%,砍二值化会更糟;真问题是 rubric 1 档撰写规范,列入 6.15)。

### 4e.4 千题管线定案(成本 ~$100~120/千题)
- **探针生成五路(全 token plan 零费)**:glm-5.2(供给 6.0 条/例+33% 变体)+ glm-5.3-flash(负向专线,唯一产"不得带馅")+ qwen3.8-flash(变体纪律)+ ds-v4-flash(跨家族)+ minimax-m3(边际+2.8)。否决:hy3(全维最差)/qwen3.8-max(薄+慢 247s)/ds-v4-pro(无增益)/qwen3.7-plus(变体懒)/gemini(判官冲突)/luna(留出题)。
- **sol 合并编辑**(`probe_merge_prompt.md`):去重(判定点同则合一,variants 并集)+修订(补变体域/收窄负向作用域)+剔除(空泛/不可判/矛盾/可疑虚构)+受限补充(≤2 条,provenance=editor)+标 tier/sources 交叉印证。~$25/千。
- **双机检门**:负向作用域审计(禁"画面中不得出现X"全场禁令)+ 原图一致性预检(gemini,连样本图都违反的约束剔除,~$13/千)。
- 出题端:sol 主笔 L3 + luna 补 L2(风格多样性),~$50/千。
- 真瓶颈:**eval_probe.py 未物化**(三路生成缓存已有 probe_val_results_v5test.jsonl 50 行可复用)+ 机审还是"只告警不删"。
- 工程教训:探针产物提取必须 raw_decode 容 markdown 围栏(json.loads 遇尾随``` 误判空产出,冤枉过 minimax/qwen3.7-plus)。

### 4e.5 eval_score.py 事故与恢复(重要教训)
- 事故:词表清理删 RWF 死条目时正则 `.*` 吞掉 FACETS 闭括号及**其后全部函数**;且 git HEAD 停在 8-25(v5.3 重建从未提交),尾段拼接成 v5.4 头+v5.2 尾。
- 恢复:头部(常量+FACETS)完好保留;丢失区用 `__pycache__/eval_score.cpython-314.pyc`(13:48 快照=v5.3+新准则)反汇编提取 oracle(4539 行 dump 存 `/tank/tmp/kilo/oracle_dump.txt`),逐函数重建 + 本会话逐字读过的 run/main/模板 + 自写补丁重放。
- 验证:240 条存量判分**逐字段回放等价**(0 解析失败;69 条 quality 差异=词表清理滤除 resolution/noise 所致,旧键补回后 0 差异);单元回归全过。
- 教训:① 大改先写临时文件再整体替换,禁止在正位上用无界 `.*`;② `python3 -m py_compile` 会覆盖 pyc(oracle 快照差点二次丢失);③ **benchmark 代码 8-25 后未 commit 是根因之一——下窗口第一件事 commit**。

## 5. 代码改动记录(按文件,含前序窗口)

**`eval_score.py`(判分,2026-08-28 v5.3 四维重建)**:
1. 新增 `--schema` 自适应(检测题目含 `fidelity_checks` 即走 v5.3):判官只看**生成图 + 题面 + 四类检查项**;不给样本图、不给约束清单、关键项零标注(口径定稿见项目记忆 `t2i_v53_dim_weights_critical`)。
2. 四维加权:`V53_WEIGHTS = fidelity 0.5 / alignment 0.2 / quality 0.2 / aesthetics 0.1`;各线 φ 映射(0/60/100)、N/A 剔除;总分按存在维度归一。
3. 机械封顶四条(出题端零标注,判分侧确定性执行):负向关键项(锚点引用 must_not_have 约束,`negative_critical_idx`)/对齐项/质量缺陷项得 0 档 → 封顶 20;保真整线 <40 → 熔断 20;美感永不封顶。负向检测覆盖全部锚型(极性翻转仅 must_have→must_not_have 一向)。
4. judge prompt 物化 `data/judge_prompts/t2i_judge_template_v5.4.md`(8-28 由 v5.3.md 改名,内容零改动);reason 暂不限字数(用户拍板)。
5. 工程:`--workers` 并发 + 断点续跑(按 qid);429/529 退避;reasoning-only 回退 reasoning_content;输出 `scores_<judge>.jsonl` 含四维线分/封顶标记/判分证据。

**`eval_synthesize.py`(出题,2026-08-27 v5.3 接线)**:
1. `--schema v5.3`:`synthesize_prompt_gen_v5.3.md` 为 prompt;用户消息按 v5.3 §二模板注入(图说明/概念名/分类路径/知识库描述【与探针输入同源,取 `probe_cases_v4.jsonl`】/**已核硬约束清单**`--constraints` jsonl 按实例名匹配/本题调用方式 `--invocation` 槽位轮转);无约束清单的样本直接跳过(唯一知识来源,不得裸出题)。
2. 输出单题 JSON 对象(`extract_json_object` raw_decode 抗污染);结构审计 `audit_v53`(保真 5~7 条+锚点回溯清单编号+三档+可见条件、对齐 2~4+Alignment 词表、质量/美感 2~4+词表、泄漏对照表齐全且全「无」、废字段检查),只告警不删。
3. 工程:`--workers` 并发(ThreadPool)、断点续跑(按 `(样本号,模型)` 对账,模型自带 qid 不可信)、`raw/<qid>.json` 复用、垃圾产出守卫(解析成功但缺 gen_prompt → 弃 raw 重出)、`--max-tokens`(reasoning 模型 32768 起,16k 必截断)、写盘即 flush。
4. 产物:`synth_gen/questions_v53_<模型短名>.jsonl` + `_job_sample/_job_qid/_generator_model` 回注字段。

**`eval_score.py`(v4/v5.2 历史记录,口径敏感!)**:
1. `CRITICAL_CAP=20`:任一 critical check 得 0 档 → 总分封顶 20(字段 `critical_capped`);
2. judge check 块追加「判分最低可见条件」(`visibility_requirement`);
3. 删除 `subject_prominence` 条件激活(被选取即恒计分);
4. `max_tokens` 预算化:`_judge_budget()` 走 /tokenize 计量,`min(--max-tokens, ctx-文本-图像预留)`;截断拉满重试、400 收紧重试;外部端点(无 /tokenize)用 `--max-tokens` 兜底;`content=null` 显式报错。**不改判分口径**(确定性解码,加长仅补全续写);
5. `JUDGE_CTX_LEN` 8192→**32768**(与部署同步);`extract_json` 重写抗「输出格式示例」污染(旧版取第一个合法 JSON 会被 prompt 示例带偏 → 判分全空);
   常量快照:`KNOWLEDGE_WEIGHT=0.7` / `PHI={0:0,1:60,2:100}` / `GATE_CAP=20` / 熔断<40→20 / critical→20,与 V5 prompt 第七节一致。

**`gen_results_review.py`(notebook 生成器,改它再重生成,勿手改 .ipynb)**:
布局 = 参数+过滤 / 整体分析(含第六节「judge 输出合规性」+ 第七节「双 judge 对照」) / 逐题明细卡(得分计算链、rubric 命中档高亮、judge 分析原文、双判官总分并排、缺判红条警告);图片内联前压 JPEG。

**`eval_audit.py`(新增)**:题目质量质检,机审+语义审,产物 `synth_gen/audit/<题源>_audit.jsonl`。2026-08-27 修 `extract_json_object`→`json.JSONDecoder().raw_decode` 取首个完整对象(质检 judge 偶发多 JSON 输出致 Extra data,2/20 稳定复现)。

**2026-08-28 下午(本窗口,eval_score 事故恢复后全量复核)**:
1. `eval_score.py`:⑤ 质量乘子(封顶后 total×(0.6+0.4q));④ defining_check_idx+DEFINING_WEIGHT=2.0(finalize 保真线加权均值,无 tier 回退);⑥ FACETS 结构清理(7/3/18)+ 29 条准则三段式重写+judge_prompts 重物化;RWF 删除正则事故后的完整重建(§4e.5)。
2. `eval_synthesize.py`:--schema v5.4(prompt 自动解析/audit exact7/产物 questions_v54_*);--invocation 支持 L3:7,L2:3 配比(最大余数交错,默认 70% L3);词表集合同步 7/3/18;美感选择范围 2~3。
3. `synthesize_prompt_gen_v5.4.md`:v5.3 copy + 恰好 7 条三处 + 附录三组重构(与 FACETS 同步)+ 正文质量/美感措辞;附录加 RWF 取代说明。
4. `probe_merge_prompt.md`(新):sol 合并编辑契约(五步:去重/修订/剔除/受限补充/tier+置信标注),输出 schema 兼容 constraints jsonl 并加 sources/provenance/tier。
5. `constraints_v53_trial.jsonl`:回填 tier(拉奥孔{1,2} 醒狮{1,2,7} TCG{1,3,6} ISS{1,2,3} 咯吱盒{1,6}),旧判分可离线复算 defining×2。
6. `data/eval_bagel_v53/`:responses_orig.jsonl + scores_orig/(五判官×24 题原图基线,120 条)。
7. `data/density_probe/probe_val_results_v5test.jsonl`(50 行):9 模型探针横评补充测试(glm-5.2/5.3/max、qwen3.7/3.8-max、ds-v4-flash/pro、hy3、minimax-m3、luna;含耗时)。
8. `question_dev.ipynb`:出题人对比格加原图基线(原N+基线行);判分明细钻取格(F_SAMPLE/F_AUTHOR 列表/F_JUDGE 筛选,逐 check 档位+理由,负·封顶项标记,原图基线+细项,高清原图链接);负标签 off-by-one 修复;封顶标记全称化。
9. (2026-08-28 傍晚,版本化批次配套)`eval_sample.py`:`stratified_pick` 加 sha 去重(多实例图同 sha 多行,一图一题);DEFAULT_EXCLUDES 改 glob `samples*.jsonl`(全版本自动排除)。`eval_synthesize.py`:新增 `--samples`/`--out-dir`(原硬编码 SAMPLES 常量与 synth_gen 输出目录);`load_samples(path)` 参数化。`eval_score.py`:物化件改名 `t2i_judge_template_v5.4.md`(内容零改动)。

## 3b. V5.2 批次(2026-08-26 晚,本窗口产出,20 题 / 2 模型)

- **出题**:`synthesize_prompt_gen_v5.2.md` × 2 模型(用户拍板剔除 fable-5)= gemini-3.7-flash + gpt-5.6-sol,
  各 10 题,配额 L1:2/L2:4/L3:4,20/20 出齐零拒题;产物 `synth_gen/questions_openrouter_*_v52.jsonl`
  与合并 `questions_v52.jsonl`(qid 已加模型前缀;gemini-0007 首生出链结构缺陷、单题重新生成后补入)。
  旧 V5 分文件已归档 `synth_gen/archive/2026-08-26_v5旧版分文件/`。
- **质检**(`audit/questions_v52_audit.jsonl`):机审 99.2 / 语义 91.8 / quality 94.0 / **20 过 0 拒**
  (V5 基线 99.0 / 88.1 / 91.4 / 28 过 2 拒)。分模型语义:gemini-flash 82.9→87.1,gpt-5.6-sol 90.7→96.4,
  两模型出题质量差 7.8→9.3 略扩(都涨、gpt 涨更多)。弱项维度:counterexample_resistance 61.7→77.5、
  check_rubric_soundness 68.3→72.5。
- **Bagel 应答 + 双判官对照**:`data/eval_bagel_v52/`(imgs/ 20 张 + responses + 两套分数)。
  | 判官 | overall | gate 封顶 | 知识熔断 | judge_fail |
  |---|---|---|---|---|
  | 本地 qwen3.8-27b(ctx32k,主口径) | **59.2** | 0 | 5 | 0 |
  | gemini-3.1-pro-preview(对照) | 32.94 | 4 | 10 | 0 |

  判官 gap **没缩反扩**:mean|diff| 13.8→28.7,|diff|≤15 的 22/30→9/20,11 道大分歧里 10 道 qwen 偏高。
  **独立 VLM 仲裁**(gpt-5 + gemini-2.5-pro 按 V5.2 探针对 11 道分歧图逐条作答):7 道 gemini 判官错
  (gate/critical 误触发,必需主体明明在场、探针明确通过却封顶 20)、2 道 gemini 对
  (gemini-0010 critical 档 0、gemini-0006 反向)、1 道居中、1 道双判官均漏 gate( gemini-0009 高桩缺失)。
  结论:gap 主要是 **gemini 判官对 V5.2 题 gate/critical 过敏**,不是 qwen 单边偏宽;两判官都偶漏
  gate。哪边口径为主仍待用户拍板(§6.1)。
- **判分链执行质量**(验收抽看 + 机械校验):20 题 50 checks 新字段零结构缺失;探针全为纯感知问句,
  2 条启发式知识词标记人工复核均为误报;软发现:7 条 check 的 tier_map 无字面「不可见」处置且
  全部出自 gemini-flash(gpt56 零),gemini 还抄过协议示例占位词「lineage」1 处。
- **判官模板升级:探针+查表(2026-08-27)**:`eval_score.py` 的 judge 模板加入判分链消费
  (check 块附 visual_probes + tier_map,规则「先答探针→查表落档→rubric 仅交叉校验→失真≠gate」,
  输出含 `probes` 作答数组,落盘 `check_probes` 供对质)。用同 20 图重判:
  | 判官 | 旧模板 | 新模板 |
  |---|---|---|
  | 本地 qwen3.8-27b | 59.2(gate 0/fused 5) | 51.86(gate 0/fused 5) |
  | gemini-3.1-pro | 32.94(gate 4/fused 10) | 36.98(gate 2/fused 11) |
  | mean\|diff\| / \|diff\|≤15 | 28.7 / 9-of-20 | **22.0 / 11-of-20** |
  gap 收窄 ~23%,两判官向中间移动;收敛样板:gemini-0001/0004 双方从分歧到完全一致(83.2/68)。
  但 gemini-0005 的「航天飞机缺失」gate 幻觉依旧(两 referee 均确认在场)——指令修不了感知幻觉,
  需 gate 拆逐主体机械判定(§6)。产物 `scores_ctx32k_probe.jsonl` / `scores_gemini-3.1-pro_probe.jsonl`,
  旧模板分数原样保留。剩余分歧已从「gate 误触发」下移到「探针感知分歧」(512px 细节辨识)。

## 6. 待拍板 / 待办(续作窗口从这里领活)

1. **人工裁决分歧题**:看 `results_review.ipynb` 第七节 + 明细卡,重点 §3 分歧榜前 5;裁决结果决定主判官口径去留(纯本地 qwen-32k vs 引入更强判官/人工兜底)——**协议变更,须用户拍板**。
2. **处置 2 道审计拒题**(`gemini-003`、`gemini-0008`):剔除或退回修题,同步更新 `questions_v5.jsonl` 与既有分数。
3. **密度预探落地(§4b 定案第 4 条)**:抽样侧加密度预探——先存量代理档(`instances.json` 富度,零成本),后加 LLM solid 事实探针(列不出 ≥2 条即弃);拒题从出题环节前移到抽样环节。
   **2026-08-27 第一级(存量代理)已在 `question_dev.ipynb` 「密度预探」段落地并校准完成**,数据结论:
   - 富化构成:296,010 实例中全富化仅 52,815(17.8%),其余占位;富化分 = desc≥150 字记 1 / 部分 0.5 / 占位 0,图密度 = Σ 打标实例分。
   - 候选池(pilot 门 q≥9.5+1080~1600)= 1,944 图:θ≥1 通过 89.9%,θ≥2(≥2 富实例同图)仅 3.7%;基线门(q≥8+identity)= 225,901 图,θ1 89.3% / θ2 2.7%。
   - **供给瓶颈在「富实体×合格图」配对**:全富化实体只有 1,675 个(3.2%)在 pilot 窗口内有合格图,每实体图数中位 1、max 4——零冗余。
   - 按域缺口(θ1 通过率,n≥50):植物 10.5% / 人造物体 70.1% / 动物 72.3% / 食物 83.0% → 这四域实例是富化优先级(对接 §4b-5 的 gen_instance_kb 供给)。
   - **校准结论**:pilot 10 样本全部单实例×全富化(density=1.0),实际成题 2~4 节点;两道拒题样本密度也是 1.0(拒因题目质量非密度)→ θ≥2 会误杀全部试点样本,**存量代理只能作 θ≥1 粗筛(弃全占位图),「撑不撑得起多节点」必须走第二级 LLM solid 事实探针**。
   - **第二级探针已验证通过(2026-08-27)**:`probe_prompt_solid_facts.md` v1.2 定稿,验证集全存量(35 case:正例 10/虚构负例 10/长尾占位 10/薄富化 5)× 双模型(本地 qwen3.8-27b + gemini-3.7-flash)= 70 判定,notebook 密度预探段 5 格可复跑(缓存 `data/density_probe/probe_val_results_{v1.1,v1.2}.jsonl`)。裁决四条全过:正例 10/10×2、虚构 0/20 放行、分离轴拉奥孔 5~6 条过(密度≠题目质量)、一致率 97%(唯一分歧 D_01 二十四节气边界例)。迭代史:v1.0 正例 6/10~9/10 → v1.1 加 caption 语境救 0006 魔戒有声书假阴 + 8192 预算 → v1.2 解析取最后 facts 对象(qwen 思考流复述格式占位陷阱)+虚构组合硬规则+占位条目过滤。
   - **验证期三个设计级发现**:① 探针测的是 LLM 自身知识而非 KB 富度——长尾占位组 20/20 全放行(昆明地铁/保温水壶/雪纳瑞皆常识实体),拒题力只作用于真未知长尾,探针不替代富化;② caption 语境必须随样本绑定(救假阴的关键),探针按样本缓存不做纯实例缓存;③ 薄富化组无单调性(茅台/特斯拉是著名实体)再证代理与真值解耦。
   - **v1.3 定义重构(用户纠偏,2026-08-27 下午)**:① caption 删除——单张图是概念的偶然实现,对约束无证据力;②「solid 事实」重定义为**概念级硬约束**(对所有合法画法成立;含 must_not_have 负向约束;派别差异写进 variants 枚举域,如刘备狮黄/关公狮红/张飞狮黑);③ 约束知识分层明确:check=文本可推导的图硬约束,知识分 L0 多模态常识接地(坐姿/颜色,随题面自带,不探)与 L1 实体/领域知识(探针目标)。同 35 case 重跑:四条裁决全过且**双模型一致率 100%**,产出物形态质变(醒狮出现「不得以写实狮子/北狮替代」负向约束+脸谱三狮变体枚举;东阿阿胶「不得以驴皮原料替代成品」)。v1.2 的 caption-语境补丁被证伪方向(魔戒有声书救回靠的是复读图说明),此产出才是可供给出题的知识形态。
   - **v4 横评收口(2026-08-27,产物 `probe_val_results_v4.jsonl` 80 行)**:20 case × 4 模型(gpt-5.6-sol/gemini-3.7-flash/glm-5.3-flash/qwen3.8-flash)全绿(正例 10/10×4,虚构拦截 19/20,唯一漏网仍是「全息投影竹编」× glm-5.3-flash)。**单模型供给不足**(均条数 3.3~5.1 < 一题需求 5~7),四模型合并去重前均 16.3 → **生成源定案:三路异族生成→合并去重 = gpt-5.6-sol + glm-5.3-flash + qwen3.8-flash**(各家独有约束互补:肖像可辨识/时代防穿帮/防换人);gemini-3.7-flash 出局。复审环节( probe_review_prompt.md + glm-5.3)实测串行过慢 + 解析器不认 `reviews` 键的 bug,**已暂砍**(产物已删),恢复需并行化+修解析。
   - **下一步(见 §6.9~6.11)**:物化 `eval_probe.py`(三路生成+合并去重)、出题/判分/机审按 v5.3 接线。
4. **修复环**:出题 → 质检 → 把 `suggested_action` 回喂强模型修订 → 复审。v5.1 数据证明首过优化收益递减(15/20 题带可执行修复指令),修复环是出厂质量的正式工序。
5. **机审增强(小改,已验证缺口)**:① 开放词扫描从 `check` 字段扩到 rubric 三档文本(v5.1 批 gemini 2 题漏网);② `coverage=taxonomy` 时断言 `lineage` 非空且每个派别出现在某档文本;③ qid/sample_id 格式断言。
6. **清理**:`route_prompt_sample_class.md` 已删(2026-08-27);`question_dev.ipynb` 质检审阅格接入 v5.1/v52 批次。
7. **v5.3 出题协议已定稿(2026-08-27,逐轮用户拍板)**:`synthesize_prompt_gen_v5.3.md` 终态,要点:四维判分(真实保真度 0.5/文本一致性 0.2/质量 0.2/美感 0.1);保真检查 5~7 条、唯一知识来源=「已核硬约束清单」(探针产物)或从清单推导;调用方式仅 L2 组合 / L3 推导两代号(无难度概念、无配额);文本一致性逐条归入 Alignment 词表 17 项;质量/美感各从词表缺陷类/美感类选 2~4(41 维词表四支柱见 `eval_score.py:FACETS`);关键项为判分侧机械规则(负向约束/一致性/缺陷类自动关键项封顶 20,正向保真不设关键项、整线 <40 熔断,美感永不封顶)不进出题输出;防泄漏=清单关键词对照表;输出字段删掉 gate_spec/evidence_audit/expected_failure_modes/critical。待用户终审后走代码接线(6.10)。
8. **vLLM 部署口径**:当前 32k 单卡;若采集链要吞吐,恢复 `deploy_watchdog.sh` 的 TP=2(并同步 `JUDGE_CTX_LEN`)——**改部署先问用户**。
9. **物化 `eval_probe.py`**(v4 横评定案):挂 `eval_sample` 抽样后;三路生成 **gpt-5.6-sol + glm-5.3-flash + qwen3.8-flash**(网关 4001,均经 OpenRouter 通配已实测;glm 系 reasoning 需 ≥2048 预算且**出题类长输出须 32768 起——首试实测 16k 截断/死循环**,qwen-flash 有 429 需退避)→ 合并去重 → 阈值 ≥2 条硬约束放行;产物=约束清单随样本流转进出题。**进度(2026-08-27 晚)**:生成步已有缓存(`probe_val_results_v4.jsonl` 覆盖 pilot 10 样本);**合并去重暂为人工**(首试金标准 `synth_gen/constraints_v53_trial.jsonl`),LLM 合并器待物化;阈值放行与随样本流转未接入 `eval_sample`。
10. **v5.3 代码接线**(2026-08-28 更新):① `eval_synthesize.py` 已完成并全量出题 24/24(§4c);② `eval_score.py` 判分重建**已完成并首评**(§4d,四维加权+机械封顶+五判官 120 判分 0 失败);**剩** `gen_results_review.py`/`results_review.ipynb` 适配 v5.3 schema(现为 v5.2 结构);③ `eval_audit.py` 断言增强**未做**:check↔约束锚点回溯、清单关键词泄漏扫描、qid↔sample↔概念一致、rubric 档开放词扫描(首试暂由 `audit_v53` 内联承担基础结构审)。
11. **复审环节处置**:probe_review_prompt.md + glm-5.3 复审暂挂(串行过慢 + `reviews` 键解析 bug,产物已删);恢复条件 = 并行化 + 修解析,或直接并入出题后质检。
12. ~~出题人质量对比裁定~~ **已完成**(§4e.2:sol=公平难题/glm=超纲成分; gemini 口径排名 glm≈sol 最难)。
13. ~~主判官口径裁决~~ **已定案**:gemini-3.7-flash 单判官(2026-08-28 用户拍板,五判官中最严;出题/探针端禁用 gemini 保分立)。
14. **【下窗口首务】git commit**:benchmark 代码 8-25 后未提交,eval_score 事故几乎不可恢复(§4e.5 教训③)——先 commit 再干活。
15. **eval_probe.py 物化 + 50 实例 pilot**:五路生成(glm-5.2/glm-5.3-flash/qwen3.8-flash/ds-v4-flash/minimax-m3,plan 路由)+ sol 合并(probe_merge_prompt.md)+ 阈值放行(≥2 条且≥2 路非空)+ 原图一致性预检子命令;产物并入 constraints jsonl 随样本流转(eval_sample 接线)。pilot 指标:负向占比/变体率/预检剔除率/editor 补充率/机审告警率/盲抽人检。
16. **机审硬门化**:audit 只告警不删 → 不合格自动重出;补两条断言:题面完整性(断尾检测,qwen 0009 教训)、保真线纪律(检查须锚实体通用属性,场景道具/前提效应归 alignment——glm 教训);rubric 1 档撰写规范收紧(枚举子情形)。
17. results_review.ipynb / gen_results_review.py 适配 v5.3 schema(原 6.10 剩项;question_dev 侧已就绪)。
18. vLLM 判官已重启在 GPU1(TP=1/32768,nohup setsid);modelhub 网关 4001 正常。
19. **t2i 千题批次抽样定案(2026-08-28 傍晚用户拍板)**:① 必须从有图抽——原图是全链唯一非 LLM ground truth(预检证伪器+基线资格门+出题由头);探针/出题/判分全是 LLM 断言,LLM 判 LLM 抓不住系统性幻觉(咯吱盒错条目五路探针无一报错,只有原图抓出)。② 门 = `quality>=9.0 AND identity=true AND least(width,height)>=768`(≈45.9k 图):9.5→9.0 换 8.5× 池子且修域偏科(pilot 门 A 池文化艺术 32%/植物仅 36 实体);去 1600 上限+降到 768 的依据 = encode_image 判分侧本来就压到长边 1024,更高门对机器消费者纯浪费;**此门只放 t2i,edit 赛道原图是像素输入维持 1080**。③ 规模:超采 1000(折损链:探针放行 40~70%→出题 95%→机审硬门+预检 ~85%→终题 ~500,500 是人工审阅 10~15% 抽检制下的甜点;1000 终题等协议冻结后再说,v5.4 裁定刚改完全量不可比)。④ 分层沿用 eval_sample.py 现制((L1,L2) sqrt 配额+最大余数+每实例限张+三赛道排除集),dry-run 实测 1000 张铺 354 分支无刷屏;⑤ `eval_sample.py` 补 sha 去重(多实例图同 sha 多行可能重复抽,一图一题);⑥ 正式抽前先归档现役 samples.jsonl(pilot 10 样本)进排除集,防旧样本回流;⑦ 预检子命令落地后先拿 constraints_v53_trial 5 样本回放验证(咯吱盒 F4/F6 应剔除/醒狮风向应放行是已知答案)。**顺序:§6.15 探针 pilot 先行量出真实通过率,回填超采倍数,再正式抽**。⑧ **版本化批次管理(2026-08-28 用户拍板)**:批次标签=日期+V编号(如 `20260828_v2`,pilot 即 v1 不改名就地冻结);文件布局 `data/samples_<批次>.jsonl` + `data/images_<批次>/` + `data/synth_gen_<批次>/`(含 raw) + `data/eval_<批次>/`,新旧批次零覆写;配套两处代码已改:eval_sample.py DEFAULT_EXCLUDES 升级 glob `samples*.jsonl`(全部历史版本+归档副本永久在册,永不回流),eval_synthesize.py 新增 `--samples`/`--out-dir` 参数(原为硬编码常量);run_wkbench/eval_score 本就全参数化无需改(run_wkbench 样本图根目录随题库位置两级推导,新布局天然兼容);question_dev.ipynb 各批次指针格(QUESTION_SOURCE/F_SAMPLE/判分钻取)在批次落地时更新;eval_probe.py 物化时必须带 `--samples` 参数。正式抽样命令:`eval_sample.py --n 1000 --filter "quality >= 9.0 AND identity = true AND least(width, height) >= 768" --out data/samples_<批次>.jsonl --img-dir data/images_<批次>`。
20. judge prompt 物化件改名 `t2i_judge_template_v5.4.md`(内容零改动,v5.4 判分侧改动全在机械层不进 prompt;旧 v5.3.md 已删,eval_score.py:619 同步)。

---

## 附录 A:题库构成(现役 `questions_v5.jsonl`)

| 出题模型(_generator_model) | qid 前缀 | 题数 | 难度分布 |
|---|---|---|---|
| openrouter/openai/gpt-5.6-sol | `gpt56-` | 10 | L1×1 / L2×6 / L3×3(0001 升 L2、0010 降 L2,见 notes) |
| openrouter/anthropic/claude-fable-5 | `fable-` | 10 | L1×2 / L2×4 / L3×4 |
| openrouter/google/gemini-3.7-flash | `gemini-` | 10 | L1×2 / L2×5 / L3×3(1 处回退) |

样本 = 手抽 10 张(杜波依斯/最终幻想TCG/挪威峡湾/东阿阿胶块/国际空间站/魔戒有声书/伊万里瓷/拉奥孔/广东醒狮/咯吱盒),图在 `data/images/`,run_wkbench 按 `_sample_image` 自动解析。3 道难度回退题均带合规 notes(`gpt56-0001` L1→L2;`gpt56-0010`、`gemini-0010` L3→L2,样本无反事实前提拒绝强行包装)。

## 附录 B:首窗口三步执行流水(已执行完毕,留作重跑模板)

1. 关 vLLM 腾卡(`ss -ltnp | grep :8000` 找进程,记录启动命令再停);
2. `run_wkbench.py --tasks t2i`(单卡、断点续跑、512×512 / 50 步 / cfg 4.0);
3. 重启 vLLM 后 `eval_score.py score`(本次实际演化为:8k 首跑 → 修管线 → 32k 重判 → gemini 对照)。

**已知风险备忘**:30 题为 V5 首跑且未经人工逐题审题(审计仅机审+语义审);judge(qwen)弱于出题模型,偏宽风险已被双判官对照证实;Bagel 512×512 对分辨率敏感条目吃亏;0006 样本(魔戒有声书)首跑上游截断已单独补跑。
