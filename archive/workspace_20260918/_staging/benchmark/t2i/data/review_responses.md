# t2i 出题 prompt 三模型评审记录

- 评审对象：`benchmark/t2i/synthesize_prompt_gen.md`（v3）
- 评审包：`benchmark/t2i/data/review_synthesize_prompt.md`（用完删除）
- 进度：fable ✅ ｜ gpt5.6 sol ✅ ｜ gemini 3.7 flash ✅ —— **三份齐，待用户裁定**
- 裁定纪律：契约三件套（eval_score.py FACETS+注释头 / synthesize_prompt_gen.md 镜像 / `eval_score.py dump` 刷新物化），验证用 `/tank/demiwtg/.venv/bin/python`；判分协议改动（权重/φ/gate）须 eval_score.py 常量与出题 prompt 第七节两处同步。

## 〇、本侧事实核验基线

- `needs_verification` 只出现在出题 prompt L23/L144，eval_synthesize/eval_score 零消费（纯装饰）。
- 知识线公式代码已定死：**逐条 φ 后加权求和** Σwᵢ·φ(sᵢ)/Σwᵢ（eval_score.py:278-279），文档第七节未写明。
- 通用线 = 激活且非 N/A facet **拉平取均值**（eval_score.py:281-285），非 QIB 逐层聚合。
- `KNOWLEDGE_WEIGHT=0.5`、`GATE_CAP=30`、`PHI={0:0,1:60,2:100}`（eval_score.py:35-37）、gate 只看 `subject_missing`；K=0+G=100 时总分=50（三家引用的数字均属实）。
- judge 模板（T2I_USER_TPL, eval_score.py:145-164）**不向 judge 暴露** expected_failure_modes / probe_dims / needs_verification / difficulty。
- 选词纪律 2（prompt L101）**强制**知识约束激活对应 RWF facet ⇒ 知识线与通用线重复计分是结构性设计，非偶然。
- 41 项 facet 无负向约束（negation）、无文字渲染（text_rendering）维度；probe_dims 的 `negation_constraint`/`ocr_shortcut_resist` 无判分通道。
- 出题 prompt 内部矛盾：L108（L1=1 个校验点）vs L116（checks 2~5 条）；L109/110（2~3 vs ≥3）重叠；L13（图为唯一事实依据）vs 第三节步骤 2（展开图外知识）；L23 vs L29（likely/needs_verification vs 只准 solid）；L64（"缺失"）vs L117（"缺失/主题跑偏"）措辞不一致。
- **示例自带泄漏（gemini 指出，属实）**：L160 prompt"巨型石像魔神" ⊃ L161 check"躯体为岩石/石像质感而非血肉"——直接语义泄漏，弱模型照抄文本即得分。三家均点名示例需废除重写。
- QIB 事实（三家交叉确认 + arXiv 摘要核对五支柱/56 细则/训练 judge Q-Judger+80 标注员三审）：Fairness 与 Safety & Compliance 挂在 Real-world Fidelity 下；聚合为 L3→L2→L1 逐层、L1 等权。我方标题"按 QIB 56 细则重组"失实。

## 一、fable 评审（已到）

| 条 | 判定 | 要点 |
|---|---|---|
| A1 | 修改 | 缺触发链验证（trigger 字段，防"没猜中实体"误判）+ visible_facts 交叉印证硬约束；流程补第 4、5 步 |
| A2 | 修改 | ① needs_verification 废字段、likely 限氛围描写（方案甲）；② 红线 1/5 给判别测试（删约束仍可判⇒隐含）；③ 泄漏是梯度，补共现捷径软纪律（高频固定搭配降权/标 shortcut_risk）；④ 反事实题豁免红线 2（solid 规则+显式前提一步演绎）；⑤ 红线 4 改"百科首段判据" |
| B3 | 修改 | 表述纠正（Fairness/Safety 原在 RWF 下）改 scope 声明；标题改"以 QIB 为基础裁剪并扩充"（4 个自增 L3） |
| B4 | 修改 | 补 1 档定义（2=视觉焦点/1=可辨认但被压制/0=边缘配角）；gate 分界硬规则（不借 prompt 可辨认⇒本维，否则 gate）；五/七节措辞统一；composition 判词加"不重复评判主体主导性" |
| B5 | 修改 | 纪律 3 废除改批次配额；**subject_prominence 默认激活**不占名额（堵"在场配角"漏洞）；近邻维度消歧规则；facet_tags 拆 implicit/explicit 两子数组 |
| C6 | 修改 | ① 知识 0+画质高保底 40+，三选一：权重 0.7 / 第二 gate（知识线=0 封顶 50）/ RWF 加权；② 拉平均改支柱内平均+支柱间等权（对齐 QIB）；③ 文档写明知识线"逐条 φ 后加权"（代码已是）；④ gate 悬崖：判定附证据，gate 触发率与 N/A 率列入出题质量监控 |
| C7 | 修改 | check 禁"等/大致/合理"开放限定词，每条附逐条 rubric（2/1/0 具体形态）；知识字段加事实性审核；强弱参照模型 pilot 剔双通过/双失败，统计 judge 重测一致性 |
| D8 | 修改 | probe_dims 无定义无映射、`negation_constraint`/`ocr_shortcut_resist` 无判分通道；砍掉（归因用 knowledge_dim×facet×difficulty）或补定义+映射表二选一；failure_modes 挂 linked_check+judge 勾选，统计预测命中率 |
| D9 | 修改 | 难度改二维取高：max(数量档 1/2~3/≥4, 推理档 直接/一步/多步反事实)；自报难度仅配额用，发布按参照模型通过率校准 |
| E10/E11 | 修改 | 五空子（通用线搭便车/共现捷径/模糊 check/judge 宽大+φ 抬分/L1 热门免费分）；赛道拥挤（WISE/WorldGenBench/T2I-ReasonBench/PicWorld），差异化在图锚定+分层抽样+归因链；弱点：亲缘偏置、无人工环节 |

## 二、gpt5.6 sol 评审（已到）

| 条 | 判定 | 要点 |
|---|---|---|
| A1 | 修改 | 缺实体确认/知识验证/蕴含性审查/视觉可判性四环节；"图为唯一事实依据"与"图外知识"措辞矛盾；solid 改来源级审计（knowledge_audit 条目：来源/独立性/典型性/版本差异/可观察性）；**反例测试**（能构造符合 prompt 但违反 check 的合理图⇒废该 check）；弃题出口（item_status: rejected，不得被逼凑题数）；六步流程 |
| A2 | 修改 | needs_verification 改知识条目级（题级布尔无效），正式版被评分条目 verified=true 否则弃题；三条新红线：禁止任意期待（真实但未被 prompt 授权=答案未授权）、允许多解（多合法版本列 acceptable_variants）、必须视觉可证（不得判不可见状态） |
| B3 | 修改 | 标题改"受 QIB 启发的适配词表"不宣称等价；删 Fairness 不能只用"无区分度"理由，题库级记录地域文化覆盖监控偏差；补 text_rendering 或明确排除含文字题 |
| B4 | 修改 | 四维边界表（gate/subject_prominence/size/composition）；**条件激活**（仅 prompt 要求以主体为中心时），不建议默认激活（群像/全景/多主体无单一主导主体）；补独立 `gate_spec`（required_subjects+theme_definition）；0 档与 gate 重叠时本维记 N/A 防重复处罚 |
| B5 | 修改 | 强制跨两支柱会逼塞无关 facet；均衡归调度器批次配额；3~6 可被容易项抬分，改 **2~5 核心 facet**；facet_check_map 显式映射；同一视觉事实禁多维度重复计分 |
| C6 | 修改 | ① **知识重复计分**：知识同时进 checks 与 RWF facet（纪律 2 强制），实际权重≠0.5/0.5 ⇒ RWF 改诊断标签不进 G，G 只含 Alignment/Quality/Aesthetics，S=0.7K+0.3G，K、G 同时公开；② 通用线分母随题变，facet 集合改固定规则；③ 知识线 φ 文档缺写；④ 二值 check 只许 0/2，可分档的逐题给 score_anchors；⑤ gate 收紧契约+独立输出证据 |
| C7 | 修改 | 相关误差（出题错误与 rubric 错误同源）；提案—验证—判分分离；check 拆原子项+逐条 score_anchors+acceptable_variants；judge 不看 expected_failure_modes（我方实现已满足）；双人标注校准题集测 judge 一致率 |
| D8 | 修改 | failure_modes 是预测非观察，只做设计审计+事后对照，不参与归因；probe_dims 混合层级，逐 check 映射；评测后输出证据化错误码（observed_error_schema） |
| D9 | 修改 | 数量≠难度；四轴 profile（knowledge_rarity/reasoning_depth/composition_load/visual_judgment_difficulty）+试测后按通过率/IRT 校准；初版 25/50/25 |
| E10/E11 | 修改 | 最大空子=画原型刻板印象+VLM 宽松识别；文字标签伪装红线（图中写字冒充实体身份）；关键题双 judge；近邻基准（GenEval/CompBench/QIB/WISE/WorldGenBench/T2I-FactualBench/FAGER，FAGER 最接近）；弱点：原图非 canonical、judge 未必比被测模型可靠、动态题需冻结版本、生成方差未定义（张数/seed/均值 vs 最佳） |

## 三、gemini 3.7 flash 评审（已到）

| 条 | 判定 | 要点 |
|---|---|---|
| A1 | 修改 | 缺"解空间闭环推演"（非目标模型遇该 prompt 是否有合理画面违背 checks，防假阴性）；solid/likely 混淆"视觉确定性"与"语义事实"——非视觉事实（年份/人名）禁作判分点，版本不唯一的争议细节禁作唯一判分点；知识必须是**视觉确定性知识** |
| A2 | 修改 | **needs_verification 彻底废除**，solid 一票否决、需查证/有流派争议的知识从源头弃题；**示例泄漏（石像→石质 check）**；红线 1 升级为禁同义/构成要素词泄漏 |
| B3 | 保留 | 赞同裁剪（Safety/Fairness 归独立安全套件、CG 拆解升格合理）；未理会标题表述问题 |
| B4 | 修改 | **三重共线性**：主体小 ⇒ size+composition+subject_prominence 三处同扣，通用线崩塌；给 0/1/2 锚点（2=视觉焦点/1=被背景严重干扰退化配景/0=无法辨识第一视觉中心——措辞略漂移，0 档写成画面级而非主体级） |
| B5 | 修改 | 纪律 3 单题不可执行；补**防水分兜底结构配比**：3~5 个 = 主考 1~2（限 RWF/Alignment，对应 checks）+ 基线 2~3（Quality ≤1、Aesthetics ≤2，严禁堆低阶画质标签）+ 真实映射原则 |
| C6 | 修改 | 知识 0+通用 100=50 伪及格；**φ 改 0/40/100**（压中庸 1 档）；**S=0.7K+0.3G**；**知识熔断**：知识线 <40 时总分封顶 30；gate 收紧至封顶 20 或归 0 |
| C7 | 修改 | checks 2~4 条原子断言，三段式语法槽位 `[判定对象/部位]+[显式特征状态]+[排除状态]`；**权重只许离散取值**（0.5 / 0.3,0.3,0.4 / 0.25×4），禁自由臆造零碎权重 |
| D8 | 修改 | failure_modes 结构化带 mode_id，供 judge 打勾回传形成归因闭环（注：与 gpt"judge 不看预测"方向相反）；probe_dims 与 facet_tags 词义交叉（physical_causality vs causal_reasoning） |
| D9 | 修改 | 废数量定义，改认知层级：L1 直接召回零推理 / L2 跨域组合或一步推理 / L3 反事实与因果动力学，占比维持 20/40/40 |
| E10/E11 | 修改 | 三空子：关键词降维打击（石像前验词蒙混）、画质审美洗白、主观宽泛校验点假阳性；对比表仅覆盖 GenEval/CompBench/QIB（未提知识型近邻基准） |

gemini Top5：示例泄漏+needs_verification 死结 / 0.5 权重画质洗白（改 0.7+熔断+φ40）/ 难度刷配额 / subject_prominence 共线性 / 画质标签堆砌。

## 四、三方横向对比

**全票共识（3/3，无分歧）：**
1. 缺触发链/蕴含性/解空间审查（A1）——三家各自给了方案（trigger 字段 / 反例测试 / 解空间收敛），大魔神示例三家点名
2. needs_verification 现行形态必须废除（甲：废字段；gemini：废+一票否决；gpt：条目级重构）
3. 难度"数量定义"错误，L1 与 2~5 条矛盾（改法分叉，见裁定清单）
4. check 需结构化：原子断言+逐条锚点/rubric+禁模糊措辞（"等/大致/东方韵味"）
5. probe_dims 与 facet 交叉、词表无定义、部分词无判分通道
6. 选词"均衡"纪律单题不可执行 + 低阶画质标签堆砌抬分风险
7. 知识线被画质洗白 → **知识权重 0.7，三家独立收敛同一数字**（决定性信号）
8. subject_prominence 需 0/1/2 锚点 + 与 size/composition/gate 划界防重复处罚
9. 示例废除重写
10. "按 QIB 56 细则重组"表述失实（fable/gpt 明说，事实已核；gemini 未理会但裁定不变）

**共识成立、修法分叉（待裁定）：**
- G 的构成与重复计分：gpt 剔 RWF 出 G（根治，诊断化）｜fable 保 RWF+支柱内平均等权（对齐 QIB）｜gemini 保 RWF 在 G+结构配比（Quality≤1）
- 知识熔断：fable K=0 封顶 50 ｜ gemini K<40 封顶 30 ｜ gpt 不设（靠剔 RWF+0.7 权重）
- gate cap：维持 30 ｜ gemini 收紧 20/0
- φ：维持 0/60/100（fable/gpt 支持保留，QIB 对齐）｜ gemini 改 0/40/100（孤证，且偏离 eval_score.py:35 自注的 QIB 口径）
- subject_prominence 激活：fable 默认激活 ｜ gpt 条件激活+gate_spec ｜ gemini 未表态
- 难度改法：fable 二维取高 ｜ gpt 四轴+试测校准 ｜ gemini 认知层级（保 20/40/40）
- failures_modes 闭环：fable/gemini judge 打勾回传 ｜ gpt judge 隔离（我方实现已隔离）
- probe_dims：fable 砍 ｜ gpt/gemini 重构
- facet 数：现行 3~6 ｜ gpt 2~5 ｜ gemini 3~5+配比
- 弃题出口：gpt item_status 字段 ｜ gemini 红线一票否决弃题（方向同，落点异）

**各家独有且属实：** gemini：示例泄漏、视觉确定性知识（非视觉事实禁入题）、权重离散化、Quality 堆砌；gpt：重复计分结构性证明、弃题出口、视觉可证红线、多解 acceptable_variants、原图非 canonical、生成方差（张数/seed/均值 vs 最佳）、文字伪装红线；fable：gate 触发率与 N/A 率监控、pilot 强弱参照模型、gate 悬崖 ±40、五/七节措辞不一致。

**评审事实错误（对照实现）：**
- gpt："judge 被 expected_failure_modes 暗示"——我方模板不传该字段，不成立；
- gemini：B3"保留"漏掉标题表述失实（两家共识+摘要已核，表述仍须改）；B4 的 0 档锚点措辞漂移到"画面第一视觉中心"，偏离主体级判定；红线 1 禁同义词方案过严，与 fable"触发链必须充分"相冲突（剔光锚定词会反向造成触发不足）；φ 改 40 为孤证且偏离 QIB 对齐。

## 五、裁定清单（3/3 到齐，待用户拍板；★=建议方向）

| # | 事项 | 选项 | 建议 |
|---|---|---|---|
| 1 | 蕴含性/触发链审查（全票） | trigger 字段+反例测试+解空间收敛三家措辞合成 | 合成落地（第三节+输出格式），纯 prompt 改动 |
| 2 | needs_verification（全票废现行） | 废字段+solid 一票否决+弃题 | ★gpt 折中：废除题级布尔、知识条目级验证状态与 item_status: rejected 弃题出口一并落 |
| 3 | 知识/通用权重（全票 0.7） | KNOWLEDGE_WEIGHT=0.5→0.7 | ★直接改（两处同步：常量+第七节） |
| 4 | 知识重复计分（gpt 结构证明） | 剔 RWF 出 G vs 支柱等权保留 | ★剔除：RWF facet 作诊断标签不进 G，根治重复计分（纪律 2 同步改"诊断映射"措辞） |
| 5 | 知识熔断 | K=0 封顶 50（fable）/ K<40 封顶 30（gemini）/ 不设 | ★gemini 版（知识探针定位，宁严） |
| 6 | gate cap | 30 维持 / 20 / 0 | 维持 30 + gate 判定附证据（fable）；监控触发率与 N/A 率 |
| 7 | φ 映射 | 0/60/100 维持 / 0/40/100 | ★维持（保 QIB 对齐；40 仅 gemini 孤证） |
| 8 | subject_prominence 激活 | 默认 / 条件+gate_spec | ★条件：gate_spec.required_subjects 非空才激活（调和两家：弱模型漏洞与群像题都覆盖） |
| 9 | 难度定义 | 二维取高 / 四轴+校准 / 认知层级 | ★gemini 认知层级表 + fable 显式解 L1/checks 数矛盾（checks 固定 2~4 条，难度与数量解耦） |
| 10 | checks 契约（全票） | 原子断言+三段式+离散权重+逐条 score_anchors+acceptable_variants | 合成：gemini 权重离散化 + gpt 锚点/多解 + fable 禁模糊 |
| 11 | probe_dims | 砍 / 重构 | ★砍（首跑减负；归因暂用 knowledge_dim×facet×实际错误码，gpt 错误码可后置） |
| 12 | failure_modes 闭环 | judge 打勾 / 隔离审计 | ★隔离（现实现已隔离；闭环等 pilot 后再议） |
| 13 | facet 数与配比 | 2~5 / 3~5+配比 | ★gemini 配比（主考 1~2 RWF→诊断化后改 Alignment，Quality≤1，Aesthetics≤2，总 3~5） |
| 14 | 示例 | 废除重写 | ★gpt 版（点名实体《大魔神》+剔泄漏 check） |
| 15 | 文档级零争议修复 | QIB 表述/五七节措辞/知识线公式补写/红线 1-5 判别测试/反事实豁免/百科首段判据/非视觉事实禁入题/文字与负向约束禁出（不补 facet） | 全收 |
