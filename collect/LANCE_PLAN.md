# SG／COS 元数据 Lance 迁移方案（2026-09-20 修订 · 活文档）

> 状态：方案修订完成，Phase 0 尚未因本次修订启动。本文与 DOWNLOAD_PLAN 同级，由 MEMORY §〇 指向。
>
> 本方案是 [datasets 统一存储总方案](../curation/layout/datasets_lance_migration_plan.md) 的采集／SG／COS 分案，复用相同的目录分层、DatasetRef、schema、提交回执和发布契约。
>
> 本阶段先迁主账本、钥匙表、融合表、池索引、概念和批次元数据；现有 kb/blobs、fleet 下载写入及散图 tar 链保持当前协议。此阶段完成不等于原始图片／页面字节已全部转成 Lance。后续字节统一归入总方案 P5，另按原字节校验、消费者适配和恢复验收推进。
>
> 123pan 中继转正保持 MEMORY 中的用户暂停状态；修改方案或验证 Lance 不隐含重新启动三机 daemon、放行下载批次或清理远端数据。

## 一、修订后的关键决策

| 项目 | 统一口径 |
| --- | --- |
| 数据布局 | 使用逻辑根 datasets/demiwtg/{raw,reference,intermediate,releases,catalog}；本地／SG 通过 store_id 映射，不另建独立 datasets/lance 体系 |
| 表层范围 | 原始元数据、融合结果、批次过程表与发布清单均按 Lance 组织；用途和来源明确区分 |
| 单写者 | lake 上并账／发布工具是同一发布域的唯一提交协调者，worker 不直接并发更新 Lance 表 |
| 权威源 | 过渡期维持登记的旧权威源；切换后 qid 采集域以 SG 上完整发布的 Lance release 为消费权威，lake 为构建／工作副本；旧系本机资产不因此自动变成 SG 唯一权威 |
| 快照 | 固定表版本＋完整依赖文件清单＋校验＋最后发布；增量传输按缺少的依赖文件判断，不只传 fragment |
| 中继 | 尽量复用 standalone 字节运输；必须增加发布完整性协调／验收，可放独立工具。不能预先承诺只加 --subtrees 就足够 |
| 123pan | 冷备而非在线 Lance 查询端；归档完成由目标端完整清单验证决定，不能靠文件先后到达顺序 |
| 双格式过渡 | 同一个冻结 batch 派生旧格式和 Lance，旧源暂时权威；切换后 JSONL／TSV 只作带版本的导出或 worker 入湖暂存 |
| 回退 | 切换前保留旧读写链；切换后回滚须处理新增数据和在途 batch，不能承诺无条件整体回退 |
| 清理 | 先审计、后按受保护版本的引用闭包清理；不以“最新版不用”或“三侧零孤儿”为删除依据 |
| 环境 | 独立 env 固定经验证的 SDK＋pyarrow＋demiflow 组合，记录文件格式版本；不直接修改训练环境 |

“不改 fleet”指本阶段保留既有原始 blob 上传和 worker 临时账本协议；原始账本进入权威数据层时须完成 Lance 入湖。它不允许长期同时维护两个可以各自写入的业务真相源。

## 二、现状盘点与证据等级

以下规模继承 GLM 原方案和仓库交接记录。本次修订核对了本地文档及相关代码，未从 SG 重新下载全量计数；不把历史记录标成此次远端实测。

| 资产 | 原方案登记位置 | 规模与现有证据 | 写入／消费对象 |
| --- | --- | --- | --- |
| qid_images 主账本 | SG COS kb/，本机工作副本 | 原方案 8,861,355 行／1.13 GB gz；以最终并账快照复核 | 并账、派单、审计、后续批次 |
| qid_images_ext 融合 | SG COS kb/ | 原方案 2,793,075 边／约百 MB | 四源融合、挂载、分析 |
| concept_xref | 原方案 candidate/wikimedia/ | 约 2,547 万；历史交接精确值 25,475,205 | 外部 ID 挂载 |
| mid_to_file | 原方案 candidate/wikimedia/ | 约 1.43 亿；历史交接精确值 142,861,248 | MID → 文件名关联 |
| sdc_depicts | 原方案 candidate/wikimedia/ | 约 5,332 万；历史交接精确值 53,320,331 | MID、QID、rank、qualifiers |
| images.jsonl 旧系池 | 本机 meta/ 为最全来源 | 原方案 2,899,895 行／2.66 GB | 池盘点、反推、后续读取 |
| 概念、taxonomy、alias 等 | 本机与 COS | 原方案约 1.3 GB；存在退役件，需按资产登记区分 | 概念／标签工具、分析 |
| batch2、sdc fetch_list | SG COS kb/ | 原方案约 1,127 万＋474 万；区分文件数、边数、候选数 | 第 2／3 批派单 |
| pages-en／zh | 本机 corpus 和 COS 相应位置 | 原方案 28 GB+，压缩与未压缩口径待盘点 | 本分案暂不转字节，纳入总方案后续覆盖 |

三张钥匙表在旧文档出现 datasets/raw/wikimedia/ 与 candidate/wikimedia/ 两种路径。Phase 0 必须 HEAD／列举指定来源并核验 hash、长度和实际 schema，登记当前 source_uri；不得只凭目录名假定两份相同或任选其一。

每项补齐 source_id、确切 URI、不可变快照身份、文件大小／完整性摘要、来源水位、schema 报告、主键与重复统计、writer 状态、消费者清单。ETag 可作变更线索，但 multipart ETag 不通用等于内容 MD5；迁移内容校验使用流式 SHA-256 等明确算法。

GLM 原采样的主账本字段：qid、commons_file、sha256、ext、tier、license、license_url、author、content_url、page_bytes、width、height、fetched_at、path。该字段表作为采样记录保留，不代替全量字段与可空性分析。

历史精确计数依据：[训练机交接 §2.1](archive/HANDOVER_TRAINING_MACHINE.md)、[SDC 上游说明](sdc_fetch/HANDOVER_SDC_FETCH.upstream.md)。不同快照的数量差异必须解释，不强行使当前数据匹配历史数值。

## 三、目标目录与发布架构

逻辑数据根可分别映射到 lake 的真实目录和 SG 的新增前缀；旧对象 key 不随新布局重命名。

```text
datasets/demiwtg/
├── raw/
│   ├── wikimedia/
│   │   ├── concept_xref.lance/
│   │   ├── mid_to_file.lance/
│   │   └── sdc_depicts.lance/
│   ├── kb/qid_image_observations.lance/
│   └── legacy/images_pool.lance/
├── reference/
│   ├── concepts.lance/
│   └── taxonomy_{nodes,edges}.lance/
├── intermediate/collect/<run_id>/<node>/<revision>/<attempt>/*.lance/
├── releases/collect/<release_id>/*.lance/
└── catalog/
    ├── datasets.lance/
    ├── commits.lance/
    ├── releases.lance/
    └── replication/<release_id>/        # 非业务运输清单、校验回执

lake 单写构建、冻结 batch
    → 本地表提交＋节点校验（built，尚非 SG 已发布结果）
    → 上传 SG 缺少的依赖文件＋固定版本清单
    → SG 依赖完整性验证（sg_verified）
    → 在切换规则允许时发布／切换消费版本（sg_published）
    → SG→GZ→cn1→123pan 运输（保持用户当前暂停状态）
    → 123pan 独立完整性核验（archive_verified）
```

几点约束：

- 大型不可变 raw 表可以长期保留在稳定表 URI；release 记录固定版本引用，不为每次并账复制 1.43 亿行钥匙表。
- catalog 的协议与总方案共用，由其权威 store 登记；本地 catalog 是草稿／副本，不在本地与 SG 各自独立演进同一个表版本。
- 运输 inventory／完成回执可为小型 JSON：它们用于在尚未打开 Lance 时定位并验证依赖，属于控制文件例外，不是业务元数据双主存储。业务 catalog 仍为 Lance。
- qid 域沿用 SG 发布权威；本机独有旧系池只有经完整发布和显式资产级切换后才改变权威位置，不能由本分案一次性推翻。
- SG 发布成功与 123pan 归档成功分开记录。中继未转正时允许标记“SG 可用、冷备未完成”，不能伪称三侧备份已完成。
- SG 单写发布账号可向新增前缀 PUT；“SG 只读”继续约束中继身份，不限制经授权的并账发布者。中继本身不回写或删除 SG 源。

## 四、Schema、身份与关联语义

### 4.1 核心表契约

| 表族 | 行粒度／身份 | 必须保留 |
| --- | --- | --- |
| concept_xref | 来源快照＋来源行；派生关系可按 QID／属性／外部 ID 复合键 | 原始 P 属性、字符串 ID、重复和缺失 |
| mid_to_file | 来源行；MID 是否唯一先做全量核验 | MID 原字符串、原始文件名／字节、下划线、转义和来源位置 |
| sdc_depicts | MID／QID／rank／qualifiers 关系；不假定 MID 唯一 | 一图多概念、同对多断言、限定词与 rank |
| qid_image_observations | batch／source／task／attempt 下的一次观测 | done／dead 的具体事实、错误、时间、来源、blob key/hash |
| qid_images 当前发布 | 经既有并账规则得出的媒体／概念关系 | SHA、QID、Commons 身份、tier、许可、路径、版本 |
| images_pool | 保留旧复合身份及 source ordinal | instances、queries、caption、未知原始字段和状态 |
| batch_fetch | 冻结批次＋task_id／source identity | 去重依据、候选／已派单／结果区分、来源水位 |
| concepts／taxonomy | 原有 ID 与树／边身份 | QID／legacy 命名空间、挂载顺序、独立概念契约 |

raw 层保留原始事件；去重后发布表另存。不能对 raw 直接 merge_insert 丢掉历史重复，也不能把 done/dead_perm/dead_retry 每次简单 append 当作去重后的主账本。

瞬时领取、租约、进程存活属于 runtime；已经发生的采集结果、失败证据和重试历史可以形成持久 Lance 事件表。状态转换沿用现有规则，本轮不新增“成功自动覆盖所有失败”之类业务策略。

### 4.2 类型与复杂字段

- ID 与外部标识保持 string／必要的原始 binary，不把 MID／QID 当作可任意排序的裸整数。
- dimensions、字节数、计数采用明确数值类型与范围；时间采用有时区的明确类型，未知保留 null。
- 列表、struct、map 明确 Arrow schema；长正文保留 large_string，非 UTF-8 文件名保留原始 bytes＋可逆解码策略，不能用替换字符造成键碰撞。
- Map 的同一 value 类型、键可用性、嵌套可空性需要实测；无法直映的异构值保留原始追溯字段，再定义稳定业务列，不能无损假设。
- 字段映射文档须覆盖 source→target、缺失/null/空集合、数值精度、数组顺序和错误行。行数＋主键集合＋少量抽样不足以证明全表等价，须增加全量分区内容摘要与差异定位。
- blob 元数据保留实际原始 key、sha256、可用性和来源；图片缺失不因元数据转换变成“已到位”，采集成功也不等于知识审核通过。

### 4.3 真正的 join 负载

Lance 提供存储、扫描、过滤和索引等能力；是否加速多表 join 取决于执行器与计划。谓词下推不是跨表 join。

用当前业务链作为基准：

```text
固定概念集
    → sdc_depicts 按 QID 过滤（保留关系重数、rank）
    → 生成／去重命中 MID 集
    → 与 mid_to_file 按 MID 关联取文件名
    → 沿用既有文件名规范和老池排除
    → 冻结派单清单
```

现有依据：sdc_fetch/manifest_stages.py 的 s0_filter、s2_filter 以及后续 stages。qid_images 采样未提供 MID，不能直接写 qid_images ⋈ mid_to_file 而省略桥接键。

优先使用 demiflow 的有界 join／分批算子，保留既有排序归并基线。需要额外分析引擎时明确标为可选实验，不让业务迁移依赖另起一套编排。不能为了展示收益用整个 1.43 亿行 Python dict 爆内存的实现充当唯一基线。

## 五、环境与兼容性验证

保留 env 隔离方案；Phase 0 决定准确的 Python、pylance、pyarrow、demiflow 版本和文件格式后写 lock／环境记录，不使用“pip install pylance 1.0.x”作为可执行锁定方案。

官方 SDK 1.0 公告日期为 **2025-12-15**，修正原文 2025-12-31。SDK 版本、Lance 文件格式、业务 schema 和表提交版本分别管理；不能由“SDK 已 1.0”推定某旧 minor 版本持续维护或具备最新格式功能。[官方 SDK 公告](https://www.lancedb.com/blog/announcing-lance-sdk)

当前官方文档说明 Map 写入需要格式 2.2 或更高；因此应选择能通过真实 Map／List／Struct／长文本读写验证的 SDK 组合，并明确 data_storage_version。文档支持不等于选定环境已验证。[官方类型文档](https://lance.org/guide/data_types/)

必须验证：

1. 零行表、全 null、嵌套 list/map、额外字段、乱码／二进制标识、超长文本和批间 schema 一致性。
2. 创建、追加、固定版本读、既有版本读取、索引、compaction、进程中断恢复。
3. demiflow checkpoint 的幂等、返回 Dataset 语义、异步路径；当前 create/append writer 不能直接当 checkpoint。
4. 独立环境产物被实际消费者环境正确读取。环境隔离解决冲突，但不自动解决跨环境互操作。
5. COS 路径、endpoint、签名、鉴权、虚拟主机／路径寻址及分段读取；写／提交验证在新测试前缀进行，不改生产表。

COS S3 直读不通时，使用现有 COS HTTP／SDK 上传依赖文件，消费者按 release 清单下载到本地验证后打开固定版本。此时仍是“SG 存放 Lance 发布数据”，但应明确“尚未提供在线 S3 扫描”，不能把失败改名为在线成功。[官方对象存储文档](https://lance.org/guide/object_store/)

## 六、并账、幂等与跨表提交

### 6.1 一个 batch、一个可追溯发布单元

每次并账冻结 batch_id、源 parts 清单及 hash、来源水位、并账代码、schema、规则配置、前一发布和 operation_id。worker 可继续写新的 parts，但当前 batch 不追随它们变化；可变文件必须固定完整行边界或先形成一致性快照。

同一 batch 同时生成旧格式结果和 Lance 影子结果，两边不能分别重新读取正在变化的源。若一边失败，标记该 batch 未完成／未对账；不能因另一边成功就推进 source watermark。

单写者协议至少保证：

- 重跑相同 batch 不重复插入事件，不重复应用更新；
- 同键冲突按既有规则处理并保留来源证据；主键唯一性由应用验证，不假定 Lance 自动 enforce；
- 表提交前后分别有耐久 operation 记录；
- 本地表完成但发布回执缺失时先对账，避免盲目重新 append；
- 不同 schema 或输入 fingerprint 使用新 revision；
- 对空结果形成有效提交，不伪装为文件缺失；
- 结果不确定标记 indeterminate，查已有版本／回执后恢复。

### 6.2 单表版本与业务发布分开

一次并账可能更新主账本、状态事件、批次清单及 catalog 多张表。每张表可有一个或多个物理提交，最后将它们组成**一个业务 release**；不能将“每次并账＝一个 Lance 版本”理解为天然跨表事务。

发布引用统一 DatasetRef：dataset_id、store_id／relative_uri、具体 lance_version、schema_version/hash、row_count、内容摘要。消费 run 冻结整组引用，不能每读一表都取最新版本。

table URI 使用稳定 table_id／generation，重建表或重置版本必须新 generation；不能在旧相同 URI 下重新出现内容不同的 version 1，也不能覆盖已归档的同 key 文件。

## 七、SG 文件快照与 123pan 完整性协议

### 7.1 复制完整依赖集

固定 release 后，收集所有被保护的表版本及其依赖：版本 manifest、实际数据文件、索引、删除向量和该格式需要的其他文件；有 base／外部依赖时明确登记并验证可迁移。不能从“最新版 fragment 列表”猜测完整快照。

运输 inventory 至少包含：

```text
release_id, protocol_version, parent_release_id
table_id/generation, relative_uri, pinned_table_version
relative_object_key, size, sha256, dependency_kind
inventory_digest, source_watermark, schema/runtime_versions
```

实际 Lance 内部目录名称以选定 SDK 的产物为准；实现扫描／SDK 枚举与恢复验证，不能只硬编码某几个目录。本阶段元数据表尽量保持表内相对文件引用；旧 blob 路径作为业务外部资产引用，不把本地绝对目录嵌入 Lance 内部布局。

增量只传目标端缺少、且被固定快照依赖的不可变对象。已存在对象 hash 不同是冲突，必须失败；不能当作普通可变文件覆盖。底层 key 在不同快照间可复用，release manifest 指向固定版本，避免每批复制整套大表。

### 7.2 发布与完成判断

1. lake 冻结版本和 inventory；该集合在复制与核验完成前受保护，compaction／GC 不得删除其依赖。
2. 上传 SG 缺少的依赖文件；单文件完成校验，失败续传不更换 inventory。
3. SG 端对固定清单核验缺件、大小和内容摘要；保存 verified 回执。
4. 全部成功后提交发布记录及不可变 release 描述；小型默认指针只用于发现，运行入口立即固定版本。
5. SG→123pan 可以乱序输送，目标端不得仅因见到源端发布描述就视为归档完成。
6. 123pan 独立 verifier 对该 inventory 全量列举对账、核对上传／回读校验记录，并补查缺失／可疑对象；证据不足的文件补充实际回读。只有完整后才生成目标端 archive_verified 回执。
7. 从 123pan 下载已验证 release 的完整依赖闭包，在独立目录按具体版本恢复；目录里“有几个 .lance 文件”不算成功。

完成回执分别记录 built、sg_verified、sg_published、archive_verified／failed；不能共用一个 done 掩盖跨端延迟。恢复测试至少覆盖多次提交、一次 compaction 后旧版＋新版、索引／删除状态，以及传输中断、manifest 先到、重传和缺一个文件。

### 7.3 对中继实际影响

目前 cos_relay_push.py 的非 blob 分支可以走 standalone；cos123_relay.py 用文件校验和消费账本记录完成。这是复用运输层的基础，不是 Lance 多文件事务验证。

需要落实：

- 新逻辑前缀加入实际闭集和 --subtrees；确认 rel_of、分片和 123pan 路径映射不重写表内相对关系。
- 检查 Lance 内部下划线目录运输。当前 cn1 只跳过队列相对路径第一段的保留区；正常嵌套的表目录不应被误排除，仍需用真实产物端到端验证。
- 增加 inventory 生成／校验与 archive_verified 工具，可作为旁路工具，不强迫 sg/cn1 daemon 理解 Lance 表结构。
- 中继 add-only／乱序特点通过不可变 key 和目标 verifier 处理，不依赖可变 current 指针的“trash 后上传”实现事务。
- 目标不可变对象若出现同名不同 hash，不能走旧可变文件覆盖逻辑；用唯一 generation 和发布预检，并在需要时增加最小冲突保护。
- 在 Phase 0／1 就验证完整恢复，不拖到 Phase 3 才发现中继漏传依赖。
- 对账工具不得用中继身份删除 SG。GZ 队列消费后删与 SG／123pan 长期保留对象 GC 是不同操作。

结论是“运输主链尽量复用，新增发布／归档协调和必要的小范围保护”，不再承诺中继零改动或唯一新增为清理工具。当前中继用户暂停决定继续有效。

## 八、四阶段实施与验收门

### Phase 0：小样兼容性、真实负载与容量 spike

先做资源和源快照盘点；不要求第一步就拉取／转换整张 1.43 亿行表，也不承诺半天完成全量。

1. 在独立 env 固定候选依赖，准备真实小样＋边界样本。
2. 先 1 万／100 万级代表性数据验证 schema、MID 文件名、关系重数、零行和哈希；包含真实 skew、重复、缺失与长文本。
3. 跑真实 SDC 关联与派单链，分别测全表扫描、选择性过滤、随机点查、完整 join；新旧同输入、同语义、同输出。
4. 验证 SG 依赖文件上传、固定版本读取／本地恢复；123pan 恢复验证按既有暂停状态记录待执行，不能自行重启正式链。
5. 测实际解压率、转换吞吐、峰值 RSS、新文件体积、索引和工作空间；预算通过后逐步扩大到完整 mid_to_file 副本。
6. 正式门槛：语义一致、有界内存、恢复可靠、版本兼容、资源预算可接受。记录业务可接受的时间阈值后，再决定全面上线。

“≥5x”可作为性能优化目标，不能用“点查可用”代替完整 join 验证，也不能在未测前保证 Lance 格式本身带来 5 倍收益。不达性能目标时分别定位索引／执行计划／存储，不以改变结果口径换取提速。

Phase 0 产出：环境锁定、源快照清单、schema 映射、正确性与性能报告、容量预算、COS 在线能力结论、中继兼容待办。未完成项目明确列出。

### Phase 1：冻结并账 batch，生成 Lance 影子发布

优先挂到一个尚未冻结的新并账 batch；若当前第 1 批已结束，使用只读副本重放验证，不修改已经完成的历史批次。

- 旧格式仍权威，Lance 为同源派生影子；使用本方案提交协议，禁止独立双主写。
- 实现 batch_id 幂等、节点回执、raw／派生区分和 SG 完整快照发布。
- 每表核对行数、主键／重复重数、全量分区内容摘要、null／错误分类及跨表引用。
- 连续两个不同、代表性并账 batch 通过，并补充重复运行、任一侧失败、提交成功回执丢失、空批次等恢复验证。
- 验证 SG 旧／新固定版本可读，传输失败不切换消费版本；若 123pan 未开放，归档门明确保持 pending。

“连续两次”是数据批次门，不替代故障测试；没有真实新批次时可重放已冻结的不同批次，但必须标记为重放验证。

### Phase 2：消费者灰度切换

按实际输入输出依赖盘点 make_pending、SDC stages、审计、报表、融合、分析和 pipeline 入口，逐个接入 DatasetRef 和列投影／分批读取。

- 派单任务身份、顺序要求、去重与排除规则不变；无需让 141 worker 读取 Lance。边界处可导出确定性旧格式任务文件，附 release_ref。
- 列出使用 numpy、Arrow、demiflow 等实际读取 API，不能把“pyarrow scanner”当作 Lance 自动可读的保证。
- 新旧在相同 source watermark 对跑；派单清单按既有语义 diff 为空，审计计数及边关系一致。
- 阴影读取失败时仍使用原权威源；不能一部分表读旧最新、一部分读新最新。
- 第 2／3 批是否放行仍按 DOWNLOAD_PLAN 和用户指令，不将 Lance 改造绑定为隐含放行或无限期阻塞条件。

### Phase 3：权威切换、恢复验收与保留策略

满足全部影子、消费者、故障、备份门后，登记 cutover release、水位和单一发布者。

- qid 采集域切换到 SG 发布的 Lance；本地仅构建／缓存；JSONL／TSV 变为派单／兼容导出或入湖暂存。
- 导出件必须带源版本和生成器 hash，禁止被其他工具独立编辑回写为第二权威源。
- 至少一次完整 123pan 冷恢复通过才标记该冷备链验收完成；用户尚未开放中继时，明确阶段未全部完成，可继续其他独立工作。
- 孤儿工具先只读生成分类报告与候选清单；清理不作为格式切换前置条件，不追求三侧文件集完全相同。
- 全部活动资产／消费者覆盖有清单；pages／原始图片字节标为后续总方案范围，不能将“元数据完成”写成“全部数据统一完成”。

## 九、版本保留与 GC

Lance 的旧版本用于 time travel，删除旧版本会移除相关历史读取能力；compaction 后旧文件是否可删需要结合受保护版本判断。[官方版本清理说明](https://lance.org/guide/read_and_write/)

“孤儿”必须分为：在途文件、已发布当前版本依赖、受保护历史依赖、已完成备份依赖、失败且无引用 attempt、未知来源对象。前四类不属于可直接删除的孤儿，未知类也不能自动删除。

保护根包括：所有保留的 release／tag、活跃 reader／run、未完成复制 inventory、回滚点、备份清单和 catalog 自身依赖。按引用闭包计算可清理集合；三侧因历史保留、归档延迟和恢复策略不同，不要求零差集。

实施顺序：

1. 本地生成只读分类报告；核验 manifest、实际 key 和复制／消费回执。
2. 对候选对象设置保留期并确认没有在途 batch／复制；冻结新的 mark 清单及其摘要。
3. 删除前再次核验 key、hash、引用和执行范围，避免把新发布依赖误删；使用独立 GC 身份和新 Lance 前缀白名单。
4. 删除必须可审阅、可追溯，并保留已验证恢复副本；不能复用中继“消费后删 GZ”的逻辑。
5. 不清历史 kb/blobs、搜索时代无清单原图或其他旧对象；它们与 Lance 无引用文件完全不同。

首轮禁用自动旧版本清理和未验证文件清理。已有部署若启用了自动清理，要先确认保护机制；不直接按“最近 7 天”推断其他运行不再需要。

## 十、回退与跨端恢复

| 时点 | 可执行回退 | 必须处理 |
| --- | --- | --- |
| Phase 0／1 | 保持旧源和旧 consumer；移除新读取配置 | 保留失败报告，Lance 影子不覆盖旧数据 |
| Phase 2 | 单个 consumer 切回同快照旧结果 | 同一 run 不混用不同版本；保存派单／读取记录 |
| 切换后尚无新增写入 | 切回登记的旧权威版本 | 确认没有新 in-flight batch |
| 切换后已有新增数据 | 暂停新并账提交，导出／重放增量后恢复旧 writer | 新事件、状态变更、来源水位和 task_id 全量对账；不得丢掉切换后结果 |
| 本地 writer 丢失 | 从 SG 已发布 release 恢复 lake 并恢复 operation/watermark | 未发布本地 batch 重放；不冒充已发布 |
| SG 受损 | 从明确 archive_verified 的 123pan release 或其他验证备份恢复 | 恢复点及之后未归档 batch 的差额明确列出 |

恢复窗口分开记录：本地未发布 batch、SG 已发布但未冷备 batch、已验证冷备 batch。不能用“分钟级同步”代替可量化的恢复点报告。

缺少可靠增量导出／反向回放时只能回滚读端或暂停修复，不能宣称整体无损回退。原 blob 协议本阶段不变使范围较小，但并不免除元数据新增行与状态的回滚责任。

## 十一、容量、性能与运维约束

- 1.13 GB gzip 不等于新 Lance 表体积；1.43 亿行钥匙表也不能预设为“lake 卷 GB 级、随便放下”。
- 预算包含原件／冻结副本＋转换输出＋索引＋compaction／join 工作空间＋在途复制＋安全余量。
- 输入和输出流式分批；不能一次 read_all／to_pandas／Python dict 聚合整个表。join 输出膨胀与多对多关系也计入预算。
- 分别测冷／热缓存、选择性和 skew、索引创建成本、RSS、恢复耗时、表文件数和小文件 API 请求数。
- 网络时间按实测本地→SG、SG→GZ、cn1→123pan 各段以及小文件请求开销估算。保留 MEMORY 中既有链路测试为参考，不承诺所有发布分钟级。
- 复制任务受自身资源和费用预算约束，保持既有中继背压；不得以 Lance 迁移为由自动暂停／扩容 fleet。
- 指标至少包括 batch 状态、水位差、最后 SG 发布／最后归档完成的 release、复制缺件、校验失败、冲突、活跃 pin、磁盘和队列压力。
- 不引入 LanceDB 服务作为必要依赖；Lance 格式＋选定 SDK＋demiflow／现有运输能力满足本分案目标。

## 十二、GLM 实施清单与代码边界

| 位置 | 工作 |
| --- | --- |
| 总方案 data_access／demiflow 扩展 | 共享 schema、DatasetRef、checkpoint、catalog／release 协议，避免本分案另造不兼容实现 |
| collect 并账工具与实际 _staging 输入链 | 冻结 batch、水位、事件和当前状态派生、幂等回执；先确认活动入口，不全局改历史脚本 |
| collect/sdc_fetch/manifest_stages.py | 映射真实 SDC join 与派单步骤，保持 MID／文件名和多重关系语义 |
| collect/image_backfill/make_pending.py 等消费者 | 边界读取／导出适配；保持 worker 消费协议 |
| collect 下拟新增 Lance 接入／复制工具 | inventory、转换、验证、SG 发布、归档完成、恢复、只读 GC 报告；通用部分复用 data_access |
| cos_relay_push.py／cos123_relay.py | 优先只增加必要前缀／冲突保护及验收；完整性 verifier 可独立部署；不在本轮自动启动 |
| collect/LANCE_PLAN.md／MEMORY.md | 本分案与实施状态唯一入口 |
| 总方案／AGENTS.md／配置模板 | 分案落地时同步新增前缀、身份、目录和权威切换口径；不把计划当现状 |

每个阶段交付准确命令、环境锁、源快照与字段映射、真实验证报告、对账和资源数据、未完成项。不得把旧文档里的“实测过 standalone”写成已完成本次 Lance 恢复验证。

最终验收必须覆盖：全量元数据内容对账、正确 join 和派单、重复 batch、空表、schema 变化、崩溃提交、SG 缺件拒绝发布、123pan 乱序／中断／旧版本恢复、权威切换后增量回退、以及 GC 不误删受保护历史。

## 十三、与整体方案的阶段映射

| 本分案 | 总方案对应 | 说明 |
| --- | --- | --- |
| Phase 0 | P0／P1 的采集与远端验证 | 小样和环境先行，全量规模测试按预算推进 |
| Phase 1 | P1／P2／P5 的入湖与发布机制 | 共用 checkpoint／发布协议，先影子 |
| Phase 2 | P3／P5 的消费者接入 | 独立于四条 pipeline 的业务算法修改 |
| Phase 3 | P5／P6 的切换、备份和恢复 | 本阶段完成元数据；原始字节完整迁移仍按总方案推进 |

时序按验收门推进，批次编号只是可选择的挂载机会。本分案不会重新解释用户对第 2／3 批放行、123pan 转正或历史对象处置的决定。

## 十四、本次修订记录

- 保留原资产盘点，补充证据来源、历史精确计数和源路径冲突，不把近似规模当迁移真值。
- 将“只改元数据、blob 永不改”明确为当前阶段范围，与总方案原始字节最终统一目标衔接。
- 统一目录、schema、引用与发布协议；区分 lake 单写构建和 SG 已发布消费权威。
- 将文件复制升级为固定版本完整依赖快照，增加源端发布与目标端归档验证；去掉“中继天然零改动”承诺。
- 将 5 倍加速改为优化目标，修正 join 负载，先小样和预算再做 1.43 亿行全量转换。
- 去掉无依据的 SDK 维护／半天完工／分钟级复制断言；固定版本依靠实际兼容性测试。
- 将“三侧零孤儿清理”改为引用保护下的只读审计与独立 GC；新增切换后增量回退条件。
- 本次仅修改方案和关联指针，没有安装 env、下载数据、执行 spike、启停 worker／中继或清理任何对象。

## 十五、Phase 0 实测记录（2026-09-20 23:00 GLM 执行，本节为实施状态唯一入口）

**环境锁**：`/yzp/zhaozy/yangzepeng/0905/env`（python3.11.6 venv，清华源）：**pylance 12.0.0 + pyarrow 25.0.1**。
**数据**：三钥匙表从 COS `candidate/wikimedia/` 全量拉取 3.05GB（跨境实测 1.9-2.9MB/s）；工作目录 `_staging/lance_spike/`。

| 项 | 结果 |
| --- | --- |
| 转换对账 | concept_xref **25,475,205** / sdc_depicts **53,320,331** / mid_to_file **142,861,248** 行——与历史精确值逐位一致；小样门（1M 行读回 mid 序一致）过 |
| 体积 | Lance 8.5GB vs 源 gz 3.05GB ≈ **2.0-3.1x**（列式随机访问代价，计入容量预算） |
| 边界 | 8/8 PASS：零行表/全 null/list+map+struct/large_string 60 万字/钉版本读/append 重放重复（幂等=应用层 batch_id 责任）。两条硬约束：**非 UTF-8 文件名 string 列拒写 → binary 列权威 + replace 显示串**；**新增列 append 拒绝 → add_columns 显式演进**。`cleanup_old_versions`+`explain_` GC 工具原生在位 |
| 真实关联链 | 概念集 7,826,266（qid_concepts 全量）：旧 zcat+bisect **274.9s** vs Lance+Arrow **90.8s = 3.0x**（s0 1.8x／s2 6.1x）。**输出排序 md5 逐一相等**（hit 37,699,560 行、m2f 20,329,065 行，双模式等价） |
| RSS | 新 24.9GB（全列一次 is_in）vs 旧 3.5GB——**有界内存实现（acero join/排序归并）为生产实现项** |
| COS S3 在线 | **可用**：`endpoint=https://<bucket>.cos.<region>.myqcloud.com` + `virtual_hosted_style_request=true` + COS sid/skey（SigV4 兼容）；写/读/在线 filter 实测全通。path-style 被 COS 拒（PathStyleDomainForbidden）。测试前缀 `_lance_test/` 已清空归零 |

**教训**：逐批 `pc.is_in` 每批重建 780 万值哈希集 → 慢约 50 倍；全列单次 is_in（或 acero join）才是正解。
**未完成项（Phase 0 尾巴，勿冒充已验）**：标量索引（qid/mid）收益未测；二次查询免解压优势未测；有界内存 join 未实现；进程中断恢复、compaction 后旧版本读未测；Lance 目录经中继（`_versions/_transactions` 下划线目录）端到端未验。
**性能结论**：3.0x 为朴素实现保守值，5x 优化目标未达但路径明确（索引/有界 join/免解压复跑）；**语义一致性已证**。脚本与报告在 `_staging/lance_spike/`（spike1 边界 / spike2 转换 / spike3 COS-S3 / spike4 关联链 + run_bench.sh）。

### 中止记录（2026-09-20 23:50 用户叫停）
COS Lance 发布**中止于未遂**：发布器死于解释器错误（未 import 即崩），`datasets/demiwtg/raw/` 前缀 **0 对象**，SG COS 全程零写入（`_lance_test/` 亦为 0）。本地 spike 大数据已清（13GB→148KB），脚本/报告/日志留档 `_staging/lance_spike/`，env 留驻（并行会话 Lance 工作可能复用）。

### 尾巴验证 T1–T5（2026-09-21 00:00–00:15，叫停后排入清单的受控验证，全过）

中止后用户排入 T1–T5 尾巴清单；T5 为"过中继端到端 + 完事清理"的**有界测试**（非发布，无持久 COS 变更——短暂测试前缀 `datasets/_lance_e2e/` 14 对象用后即删，恢复验证后 GZ/123pan/三机 spool 全部清零）。证据：`spike6_t1t2.json / spike7_t3.json / spike8_t4.json / spike8_t4b.json / spike5_verify.json`。

| 项 | 结果 |
| --- | --- |
| T1 标量索引 | 计数一致；**BTREE 增益有限**（热缓存+192 核下 Lance 列裁剪已把单点/1000IN 打到 ≤0.09s；索引建成 15.7s）→ 生产可暂不建索引，按查询形态再定 |
| T2 二次查询免解压 | **旧 zcat 每查全量解压 8-9s vs Lance 亚 10ms（~2.5 万倍，热缓存口径）**——消费者侧核心收益实证（此为反复查询场景，冷缓存首查差距缩小） |
| T3 有界内存 join | `scanner(batch_size=5M)` 摊销 is_in：**149s/RSS 6.1G**，输出 md5 与全表版/旧版一致。时间-内存曲线：281s/3.5G（旧）↔ 149s/6.1G（有界）↔ 99s/25G（全表） |
| T4 崩溃/版本 | compaction 107→36 片行数三态一致且**钉旧版本可读**；**tag 保护原生**（`ds.tags.create` 后激进清理直接拒删带 tag 版本、窗口清理保 tag、未保护旧版可删=GC 语义实证）；**kill -9 中断后表完好可续写** |
| T5 中继端到端 | 90 万行/4 版本/14 文件（含 `_indices/_transactions/_versions`）：本地上传 SG→sg 推 GZ→cn1 消费→123pan→**拉回恢复后 `lance.dataset()` 直接打开，行数/版本/索引/探针全对**；下划线目录过 cn1 首段守卫无碍；四侧现场清零 |

**Phase 0 终态**：§十五"未完成项"清单已全部补验完毕。语义一致性、COS S3 在线读写、崩溃/版本/GC 语义、中继运输-恢复闭环**全部实证**；性能口径：全链 2.8-3.0x（朴素实现）、s2 段 5.6-6.1x、点查复跑 2.5 万倍。**Phase 1（冻结并账 batch 双写影子）技术就绪，开工与否服从用户对"叫停"的解除决定。**

实施注意（pylance 12 API，写码前必读）：`ds.versions()`（非 list_versions）；`ds.tags.create(tag, version)`；`has_index` 是属性；`cleanup_old_versions(older_than, retain_versions)`（激进清理遇 tag 会拒删，报错点名 tag）；`create_scalar_index(column, index_type='BTREE')`；索引名≠列名。sg 推送器小树停机竞态：--once 快扫结束即 stop、未弹出队列项留待下轮重扫（幂等无害，小批量试点别用 --once）。
