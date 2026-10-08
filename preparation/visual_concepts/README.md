# 统一视觉概念与无损候选择值

项目强制规范：修改或运行本流程前，必须先阅读并遵守 [PIPELINE_SPEC.md](../../PIPELINE_SPEC.md)。

## 实现状态（2026-10-06）

已按用户恢复指令完成入口、schema、身份对齐、候选择值、来源保留及 notebook。
10 月 5 日的中途暂停已结束；本次已完成隔离验收，未启动生产导入。
正式运行前仍需指定 QID 筛选固定版本、2574 最终审核名单、已确认的 exact 映射
及图片/文档资源引用。这些是运行输入，不会由 pipeline 猜测或从名称自动生成。

## 定位与当前交付

将 QID 视觉价值初筛保留项和旧 P2 身份确认概念对齐成每概念一行。普通字段是
下游直接使用的生效值，`<字段>_candidates` 是带类型的完整候选列表。既有概念、
筛选结果、审核轮次、补证和关联图片记录按生产者完整 schema 保存，避免只搬运
公共交集字段时丢失主图声明、文档候选、未知属性或原始回执。

本流程是可重复执行的确定性批量 pipeline，零模型调用；它不启动或轮询 QID
筛选，不重新审核概念，不自动从参考文章或同名字符串推断 exact 身份。
正式全量导入可在 QID 筛选完成后绑定其固定版本执行；开发、小批导入与后续更新
走同一入口。实现可用于已提交的部分快照，但这种输出仅表示该快照的范围。

第一版实现 schema、来源适配、显式身份对齐、候选选择、相关记录保留、持久身份登记和快照发布。
隔离验证使用虚构身份映射和临时 Lance，不代表真实 2574 条映射已经完成。
没有启动全量 QID 导入，也没有把存在候选 QID 的旧概念自动认定为等价。

## 输入与准入

所有 Lance 输入均为 `{"uri": "…lance", "version": N}`，版本必须为正整数。
相对 URI 从 `project.resolve_root()` 解析。URI/version 在来源记录中按传入值保留。

| 配置 | 内容、行粒度与准入 |
| --- | --- |
| `qid_source` + `qid_review_source` | 两者一起配置；前者每 QID 一行，后者以 `status=reviewed` 且 `review.decision=candidate` 的 QID 准入；旧侧已准入且 exact 对齐的 QID 也接入元数据及全部原判断，即使判断是 low_priority。candidate 缺少源概念时报错，旧 exact 目标不在该元数据库时保留旧概念 |
| `legacy_sources` | 旧 RESULTS/REVISED/ADOPTED 固定表；仅 `status=assessed` 且 `assessment.identity_status=resolved` 准入，ready/hold 均保留 |
| `legacy_selection_source` | 最终名单，每行 `source_uri/source_version/concept_id/assessment_id`。多张旧来源表必填，按四列从名单左连接原记录；缺失或不合格记录报错，避免漏项或捞出历史 ready。URI 须与配置来源逐字一致 |
| `alignment_source` | 每条旧概念到 QID 的身份声明，schema 为 `ALIGNMENTS`。只有 `status=confirmed, relation=exact` 且绑定本行 assessment_id、有证据引用的记录才能合并；related/broader/narrower/candidate/rejected 原样保留，均不合并 |
| `choice_source` | 每个 `identity_key + field` 一条显式选择：candidate_id、reason。candidate_id 为 null 表示显式暂不选择；不存在或不具备资格的候选报错；当前显式选择覆盖继承选择，已有显式选择默认沿用 |
| `previous_source` | 上一版统一概念表，复用稳定 ID 并保留历史快照链接；未指定且目标已存在时，在运行开始固定目标当前版本；更新已有目标时拒绝用其他表或过期版本覆盖身份/人工选择历史 |
| `related_sources` | `{kind, source}` 列表；kind 支持 `positive_images/image_reviews/image_coverage/placements`，按旧 concept_id 关联完整原记录，不扩大概念准入范围 |
| `resource_sources` | `{kind, source}` 列表；登记图片库、文档库、来源 manifest、分类树等固定依赖。不复制或重读图片/文档对象 |

2574 的正式输入应从最终选定审核记录建立 selection；不能只读 2261 行 adopted，
也不能对全部历史审核表取 resolved 并集。本流程的最终名单输入契约已实现，
真实名单整理、exact 映射表和正式资源 manifest 由导入配置明确提供。

## 行粒度、字段与无损保留

公共输出 `demiwtg/preparation/visual_concepts/datasets/concepts.lance` 每概念一行。
配套 `concepts__identities.lance` 保存跨批身份与人工选择登记；概念暂时移出名单后
再次加入，仍能找回原 ID 和选择，不会把登记表中的旧概念自动放回本轮范围。
自定义目标 `x.lance` 对应 `x__identities.lance`，两者由本流程共同维护。
`schema.py` 是权威类型定义；候选 value 与对应正常字段同类型，不把结构化业务
值包装成 JSON 字符串。已有 `*_json` 来源字段按原样保存，不改变其字节内容。

| 最终字段 | QID 来源 | 旧来源 |
| --- | --- | --- |
| `concept_id/identity_key` | QID 形成身份键 | 无 exact 映射时使用 legacy:concept_id；确认后使用 qid:Q… |
| `qid` + `_candidates` | 自身 QID | 已确认 exact 的目标 QID |
| `canonical_name` + `_candidates` | 中文名 → 英文名 → 筛选输入名 | 最终审定名称优先，原名最后兜底 |
| `name_en/name_zh` + `_candidates` | 原语言名称 | 不推测旧名称语言 |
| `definition/concept_kind/qualifiers/taxon_rank/name_relation/identity_status/task_status/task_sketch` + 各自 `_candidates` | 无同构审定值时为空 | assessment 对应字段，hold 不被 QID candidate 覆盖 |
| `visual_value_decision/visual_value_reason` + `_candidates` | review.decision/reason | 不将 task_status 伪装成同一审核 |
| `primary_document` + `_candidates` | 中英文主体文档完整结构及页面 ID、标题；明确匹配、非已知重定向/消歧者可选，中文优先 | 下载/审核引用文档保留在原记录，不自动提升为主体词条 |
| `primary_image` + `_candidates` | P18 对应的全部 SHA 候选，单候选声明可以选择 | 已审正例须绑定当前 assessment_id、有 image_uri；按 selection_rank 选择 |
| `classification` + `_candidates` | 完整分类方案，仅 mapped 可作为生效值 | 旧分类及挂载保留独立原结构，不混用体系 |
| `names/core_facts/properties/relations` | 全部名称、xref、关系及来源 | 名称、审定事实及来源；事实编号按原审核记录定位 |
| `image_sha256s` | 全部供给图片 | 配置的相关图片记录；并集不代表全部匹配通过 |
| `qid_records` | **完整 QID 原行**：P18/P373、主图匹配证据、页面/文档、原名称角色、属性特殊值、关系/分类候选、原统计、audit_flags 等全部字段 | — |
| `qid_review_records` | **完整筛选原行**：上下文、材料清单、选段、payload、tokens、短判断、调用与执行状态 | — |
| `legacy_records` | — | **完整旧原行**：输入、plan、搜索、抓取/阅读/补证、assessment、rounds、revision、采纳及分类字段 |
| `positive_images_records/image_reviews_records/image_coverage_records/placements_records` | — | 配置的关联来源完整原行，包括图片输入、监督、像素引用、模型/协议/判断、定义版本与状态 |
| `identity_links/source_members/resource_sources` | 身份映射和完整来源入口 | 同左 |
| `resolutions/conflict_fields` | 每字段选择状态、规则、被选候选 ID | 同左 |
| `previous_records/redirected_concept_ids` | 历史完整快照与旧统一 ID 的重定向 | 同左 |

每个原记录包都保存固定 `source`、原 `present_fields` 和带类型的 `value`。
所有源字段必须被声明的原记录 schema 接住；发现未知顶层或嵌套字段报错，
不让 Arrow 投影静默丢掉。完整原记录和引用是审计入口，正常字段是消费投影。
名称和事实的来源包含原记录及原字段，E1/F1 等局部编号不会跨记录混用。

图片字节、图片许可/位置/监督详情和文档正文继续归对应资源表及对象库；配置的
resource_sources 固定版本并校验可打开。导入方须显式提供需要交付的资源/来源
登记快照，本流程不通过最新表头猜测依赖。未配置的外部资源不宣称已完成挂接。

## 合并、选择和更新规则

1. QID 使用 `qid:Q…` 身份键；旧侧默认 `legacy:<concept_id>`。名字相同不合并。
   一个旧概念出现相互冲突的 confirmed/exact 目标、映射绑定过期 assessment，
   都中止发布。跨 QID 等价合并及已发布身份拆分不在第一版自动执行。
2. 首次 ID 由身份键确定性生成；新增 exact 映射时复用上一版 ID。若合并两个
   已发布 ID，按 ID 字典序确定保留者，其他 ID 写入 redirected_concept_ids。
   同一身份内更新数据不改 ID；移除/更换已发布 QID 身份映射须单独处理，不自动拆分。
   登记表同时保存来源键和统一身份键，暂时移出范围的旧成员重新加入时可沿身份键
   找到现行 ID，避免已经重定向的 ID 被复活。
3. 候选 ID 由字段名和值生成；相同值合成一个候选，全部来源引用保留。
   候选保存 priority、rank、eligible、selected、selection_reason。
   原值为 null/空字符串时不生成可选候选，原记录中的空值仍保留。
4. 同优先级的不同值保留为冲突：生效值为 null，候选全部未选中。
   唯一最优候选或 choice_source 明确选择才生成正常字段；不会根据输入顺序任取一个。
   业务“候选冲突”不是数据导入失败，完整交付候选后 complete 可以为 True。
5. 主图优先已审核的 P18 正例，再其他正例，最后唯一 SHA 的 P18 来源声明。
   同档按原 selection_rank，缺排名或并列不猜测。来源声明保留 basis=source_declared，
   不冒充图片匹配通过；它的 image_uri 可能为空，SHA 仅是资产标识，不能当像素 URI。
   需要消费像素时必须使用已提供的 image_uri 或由资源生产者交付独立 URI。
6. 主体文档选择只采用生产者已有元数据匹配证据，不新读正文，也不声称正文可读性已复核。
   歧义文档、未匹配的主图声明等仍完整保留在候选/原记录中。
7. 每次输出是**本轮来源准入范围的完整快照**，不是 append。previous_source 只用于
   身份及历史追溯，范围外旧概念不会复活。历史版本保存旧选择和候选；当前候选
   来自本轮配置的来源。上版 resolutions 中的显式选择默认继承，保留理由及选择
   来源；本轮 choice_source 优先。继承的候选消失/不再合格，或多个合并概念的
   人工选择冲突，明确失败并要求提供新选择，避免静默退回默认值。显式 null
   选择也会继承，不会被下一次自动择值填回。

## 阶段、配置、写入与恢复

正式入口只有 `config(...) → run_pipeline(config)`。主线显式执行固定表读取、
筛选、left/semi join、按身份键 reduce、候选选择、原生 Lance writer。
不调用其他 pipeline，不下载、调用模型或解析正文。

默认 `workers=1, worker_mode=thread`，处理轻量元数据，避免每段行算子反复启动 Python
进程；配置可改为 process。reader 每批 32 行、预读各 1；Dataset 关系预算 512 MiB，
每个聚合组最多 32 MiB UTF-8 编码数据。超限报错，不截断；这些不是 RSS 硬上限，
Arrow 解码、Python 对象、编码校验与写入副本可能同时驻留。旧准入集合、QID 范围和历史身份分组
在多分支复用前由平台 materialize；缓存按平台默认内存/溢写策略执行，退出本次
上下文释放，不把明细拉成 Python 列表。大规模吞吐、巨型源行
及临时盘压力尚未实测，不把小样本验收外推为 535 万 QID 的资源保证。

目标仅允许本模块平铺 datasets/*.lance。输入只读，输出仅含本流程所有列。
目标锁覆盖概念表、身份登记和摘要提交，同 run 另有运行锁；目标初始不存在使用
create，存在使用 overwrite + expected_version；身份登记按 source_key 使用原生
merge 保留本轮未出现的键。并发冲突由平台锁和版本检查拒绝。整段计算后提交一个版本，未实现逐概念
断点提交。失败重跑重建这段确定性计算，既有已提交版本不删除；不做模型请求。

顺序为概念表提交 → 身份登记提交 → `summary__<run>.lance` 完成摘要提交。
摘要包含实际配置、概念和登记的固定版本、输入登记版本、行数与规则版本。
这是三次提交，不是跨表原子事务；后续提交失败则调用报错，旧摘要不是本次成功。
重跑优先使用已提交概念表的身份/选择，再合并登记中的历史成员，恢复尚未完成的
登记及摘要。只有摘要交付后才将本轮交给下游。合法空范围提交有 schema 的空
概念快照，身份登记保留历史键。

```bash
# 在共同工作区根，配置文件须给出实际固定来源。
PYTHONPATH=demiwtg:demiflow env/bin/python -m \
  preparation.visual_concepts.visual_concepts_pipeline --config /path/to/config.json
```

Notebook 四格分别为导入、参数、显式执行、独立只读查看。默认 RUN=False，
不会自动挑选正在增长的 QID 审核表版本，也不会触发生产导入。

## 验收与下游消费

隔离测试覆盖 exact 合并、同名/related 不合并、来源全字段往返、未选候选保留、
同优先级冲突和显式改选/继承/过期拒绝、旧最终 selection 缺失拒绝、hold、主图/文档歧义、审核版本绑定、
身份补全/暂时移出再加入后 ID 保持、范围缩小、空快照、未知字段拒绝、组预算失败
及身份登记提交失败后的恢复。
集成测试使用真实 Lance 读写，局部更新规则用纯行回归；不调用真实模型或改动公共生产表。

下游从 summary.output 固定版本读取正常字段，身份追溯使用 summary.identity_registry，需查看来源时展开对应候选和原记录。
不能把 complete=True 当作所有概念适合出题、已完成图片匹配，或所有字段都有生效值。

### 2026-10-06 验收记录

20 个不同业务用例已分批验证。一次完整开发批次 18 项通过（324.05 秒）；随后
补齐身份键重定向与登记失败恢复边界，最终代码的行级回归 8 项通过（2.22 秒），
受最终修改影响的 5 项入口集成回归通过（271.41 秒）。这些是临时 Lance 和虚构
映射的工程验证，不是全量真实数据验收。

```bash
# 在 demiwtg 目录；完整业务用例现在共 20 项。
PYTHONPATH=.:../demiflow ../env/bin/python -m pytest preparation/visual_concepts/tests -q
# 最终修改的专项入口回归
PYTHONPATH=.:../demiflow ../env/bin/python -m pytest \
  preparation/visual_concepts/tests/test_pipeline.py -q \
  -k 'identity_enrichment or equal_priority or registry_failure or exact_old_admission or final_selection_missing'
# 本模块入口、布局、跨流程边界及全仓流程登记
PYTHONPATH=.:../demiflow ../env/bin/python -m pytest \
  preparation/articles/tests/test_pipeline_layout.py -q \
  -k 'visual_concepts or every_pipeline_is_registered_for_layout_checks'
```

最后一条检查 4 项通过（4.90 秒）。Notebook 默认四格和独立查看格在新命名空间
执行通过，临时数据根没有新增 Lance 表；CLI --help、代码语法与新增文件空白检查通过。

扩大执行仓库已有入口/布局检查时，96 项通过、1 项失败、8 项未选中；失败来自
既有 `preparation/concepts/operators/review.py:12,61` 反向导入
`preparation.concepts.concepts_pipeline`，与本模块改动无关，本次未修改该文件。
不能把本模块通过表述为全仓布局全部通过。

当前没有生产统一概念表、没有修改 QID/旧概念源数据、没有真实模型调用。
正式导入使用 notebook 明确配置实际固定来源；完整规模的吞吐、峰值内存和临时
磁盘仍需在实际导入范围下观察，本次不据小样本承诺全库资源或耗时。
