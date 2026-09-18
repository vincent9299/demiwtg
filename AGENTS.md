# AGENTS.md · 项目架构原则与数据约束

本文档是**定死的架构约束**。任何代码修改、脚本新增、数据整理，都必须遵守。修改本文件本身就是一次架构决策，需要显式说明理由。

## 1. 项目分区

```
demiwtg/
├── viewer/                     # 【代码】查看器闭环：tag_tree_explorer.html + build_viewer.py + build/ 产物（gitignore；英文平行页已随 2026-09-06 统一版退役删除）
├── benchmark/                  # 【代码】评测基准：按三大题型拆成 vlm/、t2i/、edit/ 三子模块（抽样-出题-判分流水线 + reviews/ 下 question_dev/results_review notebooks）；评测数据不入 git：t2i=bench200/+archive/+data/，edit=无 data/ 层（批次目录、archive/、活素材全落子模块根，见架构决策 2026-09-05）；bagel/=第 4 场景（BAGEL-7B-MoT 官方基准评测：README/results_review.ipynb/gen+vlm 脚本入库，data/ 与 vendored 官方仓不入库）
├── taxonomy/                   # 【代码】标签体系维护与富化（audit_nodes / mount_map / gen_taxonomy_kb / gen_instance_kb / upgrade_v31；2026-09-05 还原盘起提升根目录，data/ 同名件为兼容 shim）
├── curation/                   # 【代码】V4策展编排（v4/，demiflow底座）＋公共预标注工具；历史代码按archive/pre_v1、v1、v2、v3、shared、legacy_tools归档
├── bagel/                      # 【子项目】Bagel 官方模型包（Bagel/ 训练/推理代码 + 权重区；2026-09-05 起入主仓——代码入库，Bagel/models 权重与 eval/vlm/data 重物 gitignore；见架构决策 2026-09-05）
├── modelhub/                   # 【子项目】LLM 网关（LiteLLM）+ 静态出口代理（mihomo）：本地 vLLM/Galaxy 直连、OpenRouter 走静态住宅 IP；独立仓库，整体不入主仓（见架构决策 2026-08-25）
├── .venv/                      # 【环境】项目公共 Python 环境（conda py3.10，torch 2.6+cu124；原 bagel/env，2026-08-24 提升为公共并由 env/ 改名；不入 git）
├── datasets/                   # 【纯数据】数据集根目录（一数据集一目录；原 data/datasets/，2026-08-24 升为顶层）
│   ├── demiwtg/                #   自建数据集 demiwtg（硬约束见第 2 节）
│   │   ├── blobs/              #     图片原始字节区（内容寻址，不可变，不入 git）
│   │   └── meta/               #     真相区：images.jsonl（统一权威主清单，2026-09-08 由 instance_images.jsonl 更名）+ taxonomy 两件套（taxonomy.json/concepts.json 入 git；2026-09-06 起中英统一，英文平行件与 alias_western 已退役；2026-09-07 instances.json 概念化为 concepts.json）
│   └── .../                    #   开源数据集落盘区（danbooru2024/coco2017 等，不入 git）
├── data/                       # 【兼容 shim】collect_v2.* import 面的 re-export 层（2026-09-05 还原盘布局适配：focus_sample/search_kb/llm_common/mount_map 转发顶层模块；infra/op_annotate 为缺失占位——真实现在 demiwtg-data 仓库；不放新代码，不入 git）
├── state/                      # 运行时状态，按模块归属分子目录（不入 git）
│   ├── collect/                #   datasets/（下载过程脚本，只读归档）+ v1 遗留运行时状态（死信/health/runs，只读归档）+ concepts_docs_draft.jsonl（docs 层摘要草稿）+ query_terms_cache.json（检索词运行时缓存）+ docs_clean/（历史清洗产物的兼容链接，已归档；新编排禁用）
│   ├── dataset_index/          #   COCO 标注缓存
│   ├── taxonomy/               #   taxonomy 模块 LLM 断点缓存与审计报告
│   ├── curation/               #   curation 历史分析残留（标签树 CSV、watermark 实验产物等）
│   └── .lancedb/               #   Lance 查询索引
├── logs/                       # 运行日志（不入 git）
├── AGENTS.md                   # 唯一权威约束/说明文档
└── README.md                   # 极简指针，只指向本文档
```

- `datasets/` 下**只是数据存储**：任何代码、页面、生成产物都不许放进去。
- 代码只允许放在顶层 `taxonomy/`、`curation/` 与 `viewer/`、`benchmark/`（2026-09-05 还原盘起模块提升根目录；`data/` 为兼容 shim，不放新代码）。
- 仓库顶层禁止新增散落的脚本或数据目录（`datasets/`、`data/`、`state/`、`logs/` 是明确登记过的例外；`bagel/` 为登记的子项目例外（2026-09-05 起入主仓），内部布局自治，不受本仓模块/数据边界规则约束，权重与评测数据等重物仍不入 git；`modelhub/` 为登记的独立子项目例外（LLM 网关 + 静态代理），内部布局自治，同不受约束；`.venv/` 为登记的公共环境例外，只放环境不放代码）。
- 常规文档为 `AGENTS.md`（约束）与 `README.md`（指针）。**明确例外（2026-09-10，用户要求固化研究方向）：[`curation/DESIGN.md`](curation/DESIGN.md) 为 curation 模块知识核心集的长期设计约定。** 新增此例外的理由是防止后续策展、审核与出题偏离用户已确认的研究目标；不是恢复历史过程文档。其他历史过程文档（docs/、子目录 README）仍不恢复，过程记录看 git 历史。
- **相关工作必读**：修改 curation 模块知识核心集的提取、筛选、审核及策展校准流程前，先阅读 `curation/DESIGN.md`。其中的研究设计约束适用于这些工作；存储和布局遵循本文件。当前代码并未全部符合设计约定，不得以现有实现反向替代设计标准。
- **图片预标注入口（2026-09-10）**：`curation/image_preannotate.py`；用户已授权本地 8000 Qwen 小批验证后全量处理 images.jsonl 清单关联图片。协议与任务边界见 `curation/DESIGN.md` 第 11 节，状态与结果在 `state/curation/image_preannotation_v1/`，不回写权威清单或人工标签。

### Curation归档与V4编排（2026-09-14，用户授权的架构调整）

理由：用户要求历史实验按V1／V2／V3归档，V4独立，并在curation内使用已安装的demiflow编排。以下分工替代旧“curation根目录pipeline.py为现役入口”的说明；历史记录与评分不覆盖。

- `curation/v4/`为新流程；入口`/yzp/zhaozy/yangzepeng/0905/env/bin/python -m curation.v4.flow`支持inventory／prepare／review材料准备；`-m curation.v4.pipeline`支持小批本地模型候选提取及逐阶段停靠、检查、续跑。业务算子、来源适配、材料契约均在此，不能依赖archive中的旧实验实现。用户本次明确要求先小批实现pipeline、逐过程审查，故接入现有本地Qwen，不启动正式出题或改评分，暂停notebook工作。机器候选不能自动转为已核验知识。
- `curation/archive/pre_v1/`为首轮12题之前的核心集、校准与知识probe；`v1/`为首轮及非物体／场景补充；`v2/`为expansion20；`v3/`为version3_20；`shared/`为跨版本旧运行器、展示、诊断和未采纳评分草案；`legacy_tools/`保存此前_archived内容。不能把早期core_pilot_v3误当第三版20题。
- `curation/DESIGN.md`继续是长期约定；`common.py`、`blob_presence.py`及图片预标注／守护／启动脚本继续服务当前任务，未当实验退役。现有服务不重启、不换模型；公共工具不依赖归档实验。
- `curation/knowledge_application_v1`与`curation/_archived`为旧路径兼容符号链接；`curation.__path__`保留旧core／pipeline等导入兼容，不能将这些兼容入口误当V4实现。归档代码仅调整路径和可核验的冻结哈希兼容，不修改冻结题目、输出、评分或请求；原始源码快照与移动映射由`python -m curation.archive.manage verify`核验。
- 运行数据仍在`state/curation/`；历史实验数据目录与case notebook原位置不变。V4试运行放`state/curation/v4/`。其中内部ID登记为新结构的试运行注册表，不修改现有concepts.json主键、权威taxonomy或新旧清单；全库身份合并／拆分与正式迁移另行实现。
- 模型配置、输入、版本和状态按DESIGN第21节执行。demiflow并发与落盘成功不等于知识已核验；当前入口验证不宣称全库覆盖、COS取图接通或V4题目已完成。

### 知识整理端到端入口约定（2026-09-14，用户明确更新）

理由：旧小批使用了历史临时清洗文件，无法检验从采集材料到知识库的完整链路。当前概念知识整理的入口限定为datasets中的采集原始材料及必要元数据；整体目标输出为干净、可追溯、带审核状态的知识库，详见curation/DESIGN.md第25节。页面保存格式不一定是HTML，采集正文与派生文本必须区别。

- 不将历史clean_docs、拼接摘要或旧候选当新编排输入；解析、规则清洗、过滤、去重等需要的能力重新编排为现役版本化算子，可复制改造旧代码，但不运行依赖archive的业务链。继续复用demiflow执行底座。
- 历史4件清洗产物已移至state/curation/archive/legacy_processing/docs_clean_20260908/，原state/collect/docs_clean路径仅保留历史兼容链接，校验清单为同级docs_clean_20260908.sha256。旧脚本已归档不再迁移。旧实验可读，不修改冻结输入输出或评分。
- 现役入口已移除clean_docs并拒绝用含其的旧材料包启动新提取；inspect/status仍可查看历史结果。新清洗和最终知识库验收尚未实现，不宣称已端到端完成。运行结果仍放state/curation，代码在curation，不回写datasets原始材料或迁移权威概念名契约。

### 逐算子调试入口（2026-09-14，用户最新要求）

用户已重新授权notebook，覆盖此前暂停决定。入口curation/v4/knowledge_debug.ipynb逐cell经demiflow运行真实业务算子；数据展示支持limit及固定种子抽样。源码与运行输出分别在curation和state/curation。新增流程内CleanMaterials（cleaning.py、knowledge_stages.py），在identity前从采集材料生成可追溯清洗版本，不读取历史clean_docs。清洗初版与审核限制见DESIGN第26节；默认notebook执行至清洗，模型步骤显式配置后逐cell调试，当前不宣称知识库端到端质量验收。此条更新上一节“清洗尚未实现”的状态，原始数据和历史实验仍不修改。

### 采集记录数据流取代概念查询入口（2026-09-14，用户要求）

现役入口改为`python -m curation.v4.record_flow`及`curation/v4/knowledge_debug.ipynb`。按采集文件逐条处理，顺序记录概念页面对应、附加已有概念关联，再通过可选ID／固定种子采样过滤；后续读取、清洗、材料关联和知识算子不接收入口ID列表。小批与完整文件处理共用实现，关闭过滤／采样／读入上限即可使用同一主线。记录与关联落盘state/curation，demiflow执行流式算子，不将全量材料装入列表。细节及当前语义覆盖缺口见DESIGN第27节；不宣称全库或跨窗口知识已核验。

旧flow.py／pipeline.py查询批次入口保留历史兼容、status／inspect及共享实现，不能继续称为现役端到端主线。旧notebook保留在其历史run中的knowledge_debug_query_snapshot.ipynb；原始datasets、历史结果、评分和运行服务不改动。

### 概念驱动知识整理，资料层共享执行（2026-09-14，用户再次明确）

理由：用户确认业务主线为“概念→原始资料→多模态知识”，此前将避免重复扫描误解为以材料行决定业务入口。现役入口更新为`python -m curation.v4.concept_flow`，notebook仍为`curation/v4/knowledge_debug.ipynb`。此条覆盖上一节将record_flow作为业务主线的说明；其流式读取、磁盘索引、共享清洗、断点复用保留为底层能力。

概念过滤／采样在资料关联之前执行；未入选概念不因共享资料自动进入任务，无资料概念保留缺口，未知／歧义材料单独保留待识别。资料和知识分别保存并关联，概念结果汇总不能替代跨材料语义整合。具体当前实现、标识及限制见DESIGN第28节。仅本流程冻结且解析器与原始文件版本一致的原始读取结果可复用，历史clean_docs和旧清洗／知识输出不作为这轮原始输入；原始datasets、评分、历史输出不改写。

### 分类型Dataset与显式关联（2026-09-14，用户明确要求）

理由：通用kind/record封装遮蔽概念、文档、图片的字段与关联过程。现役notebook和命令行转为`curation/v4/dataset_flow.py`，业务schema在`curation/v4/datasets.py`：分别保存概念、文档、图片、关联、全文、清洗、图片检查及知识表，字段直接可见；同schema分片可以进入同类Dataset。demiflow负责惰性Dataset与算子执行，已安装版本没有通用join API，因此使用显式SQL关联并将结果流交给demiflow，不伪称调用了不存在的API。concept_flow/record_flow保留原始读取及旧知识算子的内部兼容能力，不再作为业务表契约。

原始完整字段在独立来源追溯存储中保留，不能把来源额外字段作为事实默默丢弃，也不让统一record封装贯穿业务算子。文档与图片分别关联，避免笛卡尔积；输入输出字段、关联键、未匹配行在notebook可看。知识输出仍是机器候选，身份及跨窗口整合限制见DESIGN第29节。原始datasets、已有实验及评分不改写。

### 三条Dataset扩列、按需汇集（2026-09-14，用户采纳第三种方案）

理由：用户认为第29节逐处理步拆表过散，明确选择概念、文档、图片分别处理／扩列，必要时才嵌套联合处理。现役交互入口仍为knowledge_debug.ipynb，使用column_flow.ColumnFlow。文档链实际经demiflow map_async连续读取、保存读取列、清洗、保存清洗列；图片链独立扩展字节检查列；概念选择和覆盖计数扩在concepts。关联索引、来源追溯和断点为底层实现，不作为必须逐表查看的业务主线。详情见DESIGN第30节。

读取／清洗／图片检查的旧分步表名在新run中仅作兼容视图，不保存独立步骤输出。新run保留原始字段和完整扩列结果，不覆盖历史run。仅在需要身份匹配或多材料推导时按概念汇集，继续保留联合核验与知识审核边界，不提前把所有资料嵌入概念。

### demiflow 原生算子主线，取消 SQLite（2026-09-14，用户明确要求）

理由：用户要求使用 demiflow Dataset 算子串联，不再用 SQLite／SQL 实现资料关联和处理。现役入口更新为 `curation/v4/stream_flow.py:StreamFlow`（命令行 `python -m curation.v4.stream_flow`）与 `knowledge_debug.ipynb`，覆盖前述 column_flow／dataset_flow 主线决定。直接从原始 datasets 读入，概念、文档、图片分别扩列，必要时分批汇集。通用 join、reduce_by_key、group_batches、map_cached、checkpoint 已扩展到兄弟 demiflow 仓库并安装到公共环境，当前 local backend 采用可落盘排序和文件缓存，不调用数据库。具体语义和限制见 DESIGN 第31节。

旧 SQLite 编排代码与运行保留历史兼容，不作为新主线输入。原始数据、blobs、旧实验、服务和评分不变。知识阶段沿用共享算子并保存文件，默认不重新发模型请求；本次执行方式修正不等于身份、冲突、图像支持及最终知识库质量已经验收。

## 1.5 标签体系数据契约（两类独立资产，定死；两文件同居 datasets/demiwtg/meta/）

整个标签体系**只存在两类资产**——树（taxonomy.json）与概念（concepts.json），代码、数据字段、文档一律使用这两个词，禁止再引入其他分类术语（category、leaf、root 已废除；instance/实体 一词由 concept/概念 取代，2026-09-07）。数据模型以本节为准（原 schema/tag_taxonomy.schema.json 已删除：无校验消费者，勿恢复）。

**两类资产彻底解耦（架构决策 2026-08-19；2026-09-07 概念化迁移沿用）**：概念是资产，taxonomy 是视角。概念的生灭与富知识完全不依赖树；树只是展示/导航视图，同一套概念未来可被多套树视角引用。挂载关系的**真相**写在树一侧（节点 instances 名单）；concepts.json 行内的 `taxonomy` 字段是**快照**（消歧与源路由用，树变更后跑 `curation/migrate_concepts.py --refresh-taxonomy` 刷新）。原 build_unified.py（树推导实例表的重建器）已删除：它维护的正是被废除的耦合。

**概念不区分 class/individual（SKOS 语义：统一主键空间）**。新概念准入规则：可指称（收「词」不收「算式」，成员拼装键禁止入库）、可复现（跨题复现频次 ≥K）、有供给（通用图源可搜、VLM 可认）。

### taxonomy.json —— 树（展示视角）

```
{ "schema_version": "...", "meta": {...}, "tree": <node> }

node = {
  name: str                    # 节点显示名
  path: str                    # 完整路径，' / ' 分隔，从根『demiwtg』起算（前缀精简：域直挂根，无中间层）
  depth: int                   # 根为 0
  children?: [node]            # 子树；末端节点省略
  instances?: [str]            # 挂在本节点下的概念名列表（对 concepts.json 的引用，挂载关系的唯一真相落点）
  knowledge_intro?/aliases?/representative_cases?/related_tags?: [KB 字段，可选；knowledge_intro 为 150-350 字维基百科词条风格]
}
```

### concepts.json —— 概念（独立权威源，统一主键空间）

```
{ "schema_version": "...", "meta": {...}, "concepts": [concept] }

concept = {
  name: str                    # 概念主键（全局唯一；正名纪律：定了一次不再动，变化由 aliases 吸收）
  aliases: [str]               # 别名/英文名（身份字段：判重与英文源路由；无别名为 []）
  carriers: str                # 载体："image+text" | "text"（链路由：图像采集线据此跳过 text-only 概念）
  taxonomy: [str]              # 挂载路径快照（' / ' 分隔，从域起算，树遍历序；真相在树，仅供消歧与源路由，不承载知识）
}
```

- **概念独立于树**：未挂载任何树节点的概念是合法状态（taxonomy=[]，待认领池）；增删树节点不造成概念的创建或删除。
- **`name` 全局唯一是硬约束**：一个概念一条记录；多处挂载表现为多个树节点的 instances 名单同时含该名字（行内 taxonomy 快照同步多路径）。
- **退役字段（2026-09-07 概念化迁移，历史 schema 溯 git）**：`desc`（52,980 条）→ `state/collect/concepts_docs_draft.jsonl`（docs 层草稿：{name, kind: summary, body}；被消费后另批转正）；`query`（52,980 条）→ `state/collect/query_terms_cache.json`（{name: [检索词]}，采集 planner 冷启动先验——检索词从静态资产改为运行时状态）；`source` → 行内退役（迁移时分布存 concepts.json meta.source_stats：derived 330,842 / llm 54,262 / curated 158）。
- **英文平行两件套已退役（架构决策 2026-09-06）**：EN 实体对齐后成为中文概念的 aliases 或独立概念，英文知识以别名形态存活；四个平行文件物理移出 meta/ 归档 state/taxonomy/retired_meta/，search_kb --lang en 与 viewer --lang en 入口拒绝退役提示。
- 图片打标只存**概念名**（标签不含路径）——体系演化（改路径/重生成树）不需要迁移图数据。看图入口（viewer 的 build/imgs.js）由 meta/images.jsonl 的 instances 字段现场聚合（字段名 instances 沿用 demiwtg-data 采集链契约不改，语义=概念名）、相对路径指到 blobs 原图（相对 viewer/ 的 ../datasets/demiwtg/blobs/...），不再建软链树。
- 数据字段定义即契约，改字段 = 改本节 + 同步全部消费代码。

## 2. datasets/demiwtg/ 硬约束（定死，逐条执行）

### 2.1 blobs/ —— 原始字节区（不可变）

```
datasets/demiwtg/blobs/<aa>/<sha256>.<ext>   # aa = sha256 前两位；sha256 = 文件内容哈希
```

- 图片**只增不删、不重命名、不改动**。
- 新增图片必须：先算内容 sha256，再按 `blobs/<aa>/<sha256>.<ext>` 落盘；已存在同名文件则直接跳过（内容寻址天然去重）。
- 文件名中的哈希**必须是文件内容的 sha256**，禁止沿用下载器给的不可信文件名。
- 删除任何旧图片目录之前，必须逐文件验证其内容已存在于 blobs（sha256 比对），否则先并入 blobs 再删。

### 2.2 meta/ —— 真相区（只放真相，别的什么都不放）

**允许的文件（穷举，不允许出现清单之外的东西）：**

| 文件 | 角色 |
|---|---|
| `images.jsonl` | **统一权威主清单**（2026-09-06 起统一，时名 instance_images.jsonl；2026-09-08 更名复用简名 images.jsonl）：去重键 (sha256, instance) 一行一对，逐实例炸开；含 EN 并入行（实例名已归一为中文正名，new 实体保留 EN 名）与原 v1 images.jsonl 并入行（identity/focus/quality=null 待 annotate_backfill 补标）；VLM 补标、质量门、viewer imgs.js 均以它为单一来源（v1 同名退役件在 state/taxonomy/retired_meta/，同名不冲突，勿混淆） |
| `taxonomy.json` | 标签体系树（展示视角，权威源，入 git；含并入的 EN 新实体挂载） |
| `concepts.json` | 概念资产库（统一主键空间权威源，入 git；四字段契约见 1.5；2026-09-07 由 instances.json 概念化迁移而来，历史 schema 溯 git） |
| `.meta.lock` | 跨进程写锁（运行时瞬态） |

**禁止出现在 meta/ 下的东西：**

- ❌ 审计日志（只写不读的账本一律不建；先有读取代码才允许写入）
- ❌ 备份文件（*.bak-*、*.bak-sync 之类）
- ❌ 派生索引（LanceDB、实例名→图反向索引等；需要时由消费者从 images.jsonl 现场聚合）
- ❌ 运行时状态（死信队列 sqlite、健康账本、done flags、COCO 缓存）

**判据（新增任何文件前先回答）：**

1. 有消费者吗？——**必须先有读取它的代码，才允许写入它**。
2. 是真相还是派生？——派生的东西不进 meta。
3. 删掉它会丢数据吗？——丢了数据才是真相；能重建的不进 meta。

### 2.3 运行时状态在顶层 state/（不属于数据湖，按模块归属分子目录）

`state/collect/`：下载过程脚本（datasets/download_all.sh、character_resume.sh、hf_mirror_hf.py，只读归档；HF 数据集落盘区已迁至 `datasets/`）；v1 遗留状态（死信队列 `.dlq_*.sqlite3`、`source_health.json`、`runs/<run_id>/`、`source_registry.jsonl`）只读归档不再写入；`state/dataset_index/`：COCO 缓存；`state/.lancedb/`：Lance 查询索引；`state/taxonomy/`：taxonomy 模块 LLM 断点缓存与审计报告；`state/curation/`：curation 历史分析残留（标签树 CSV、watermark 实验产物等；评测数据已迁入 benchmark/，见架构决策 2026-08-24）。代码约定：仓库根由 `--meta`（默认 `datasets/demiwtg/meta`）向上三级推导（datasets/demiwtg/meta → 仓库根）。永远不进 meta/、不进 datasets/、不进 git。

### 2.4 一致性规则

- `images.jsonl` 是唯一真相（前身链：metadata.jsonl（2026-09-06 更名）→ instance_images.jsonl → images.jsonl（2026-09-08 更名复用简名，用户拍板，见 2026-09-08 决策块））；**不建任何派生索引文件**（历史上先后废除的派生件：instance_images.json（旧实例→图反向索引，2026-08-21 废）与 v1 images.jsonl（2026-09-06 退役，其名 2026-09-08 起被现清单复用）；双份存储有一致性漂移风险；需要实例名→图关系时由消费者从 images.jsonl 现场聚合，如 viewer/build_viewer.py）。
- `images.jsonl` 的 instances 字段只应是当前体系的概念名（字段名沿用 demiwtg-data 采集链契约）；体系演化后残留的死名打标从 images.jsonl 剥离（无隔离区）。
- 一张图的 instances 变更（改名/隔离）改的是 images.jsonl，**图字节不动**。
- 新元数据字段设计时必须先问"哪个消费者读它"；答案为空就不加。

## 3. 代码模块职责


> **知识核心集试点补充（2026-09-09）**：当前默认 `state/curation/core_pilot_v3`，只调用本机8000上的 `qwen3.8-27b`（仍兼容8001；4001付费网关硬拒绝）。新版图片协议独立判断 T2I 与编辑：完整T2I考点要求 full 覆盖且无未支持部分；编辑允许结构和状态改变，要求可见锚点、充分初始条件与知识依赖，不要求源图已符合目标知识。图片可视化自报值不是删除闸门。`fork --reuse-docs` 仅在相同文本协议下复用 calibration 知识候选；`compare` 对同知识/同图片比较协议结果，变化不等于准确率改善；`run --repair-invalid` 显式限额重试并保留原输出与反馈。人工可纠正图片结果，原模型结果不覆盖；更改知识陈述仍需新批次重核关联图片。`comparison.json`、`review_hints.json` 与 `report.json` 的材料缺口由审核入口消费；助手提示不算人工标签。图片来源只区分已声明生成与未核实，不把网络来源推断为真实照片。



> **架构决策（2026-09-09，小规模知识核心集）**：用户授权将旧 curation 归档后建设新流程。现役入口为 `python3 -m curation.pipeline`，模块为 `core.py`（存储/校验/人工准入）、`prompts.py`（六类知识内容与模型协议）、`pipeline.py`（选材/物化/推理/收录/统计/导出）、`review.py` + `review.ipynb`（人工审核），历史 `_archived/` 不作为新流程依赖。知识内容与29域分离；概念、树和湖文件只读，不给 concepts.json 恢复 desc 等退役字段。运行产物在 `state/curation/<run>/`，由流水线和审核 notebook 消费：manifest 冻结概念/来源片段/图片候选，tasks 绑定输入哈希，results 保存校验通过的模型候选，reviews 保存人工决定，report/core 为可重建统计与导出。先有来源支持与条件核验、再看图片证据；模型候选不自动成为核心集，fact 与 evidence 均人工接受且对应任务可用才导出。旧门通过/拒绝/未标注三组保留；六类知识多标签不作难度计数。calibration/holdout 按概念隔离，后者需人工校准后 freeze 才能推理/收录；不宣称未见 benchmark 泛化。命令顺序：prepare → render docs → run docs（显式 --limit）→ render evidence → run evidence → notebook/review → report/export。**用户明确限定仅使用本地 Qwen3.8-27B；4001 属付费网关，任何付费接口使用前必须另获明确确认。** 当前 runner 硬限制本机8000/8001、模型 qwen3.8-27b，禁代理及重定向；不启动/停止用户模型服务。事实或提示词需修改时使用新 run，旧模型结果不覆盖。


> **简单实拍补充（2026-09-07，用户拍板）**：已完成的 23 题原样计入总量，剩余题适量增加简单实拍，避免过度降低整体难度。当前选图为 55 张照片候选与 145 张生成图，其中 19 张采用独立 `synthesize_prompt_edit_v6.1_simple.md`；原 v6.1 标准协议不变。简单配置沿用原脚本的证据、图题绑定与收录校验，取消多跳及高义务数量门槛；`construction_profile=simple`、`difficulty=simple` 显式区分，`level` 只保留 T2I 逐实例参考层级，不宣称为编辑实测难度。新增清单合并为 bench200/source_review/combined_sources.jsonl 供 emit-plan 消费；最终比例按成功题实际来源统计。

> **选图补充（2026-09-07，用户拍板）**：edit bench200 改为采集照片候选与生成图共同覆盖同一 200 实例，一实例最终一图一题，已完成合格题保留。用户明确指定可复用 `_staging/benchmark/t2i/data/` 的历史采集源图副本；不改湖、不重抽实例。`eval_complexity.py prepare-mixed/select-mixed` 以历史样本质量快照和成功复杂度记录初筛，再由显式 `gpt-5.6-sol/high` 子代理逐图补核身份、实拍外观与编辑适配；缺 identity 不从 kb_match 推断。复核通过的图片按 SHA256 原字节复制到 edit/focus200/collected/，清单在 bench200/source_review/selected_sources.jsonl，由 eval_synthesize --source-manifest 消费；原生成图清单不改。照片优先；生成图沿原层级素材偏好，并复用 rank_key 在同批次内择复杂图，不能混用照片 10 分制质量与生成图百分制评分。候选失败沿原三步尝试序列补位，最终来源以 questions 的实际绑定为准，统计分来源并按 level 分层。

> **架构决策（2026-09-07）**：edit 正式出题批次落 `benchmark/edit/bench200/`（运行数据不入 git），沿用 v6.1 出题协议与 `eval_synthesize.py` 的选图、类型尝试序列、削峰及严格机审。实例顺序和难度来自 `benchmark/t2i/bench200/questions.jsonl`；pilot 20 题及其计划原样保留并计入 200 题总配额，只补其余 180 个实例。出题不用 OpenRouter/API，使用显式指定 `gpt-5.6-sol` / `high` 的全新子代理；每题只给物化 md 与绑定源图，不继承调度对话；必须完整读取 md 到文件末尾，不能截取前 240 行而漏掉末尾批次调整。后续 render 从同一校验函数显式附带原有定位方式数量及机械格式自查，修复原 v6.1 表格漏列定位门槛的问题；不修改冻结模板与任何既有验收门槛，旧渲染/题目保留。离线流程为 emit-plan → render-question → 子代理裸 JSON → ingest-question → validate；输入哈希与模型配置保存在批次 dispatch 供收录校验消费。正式判分配置由用户定为修订 QIB v2.2 + `gpt-6-astra` / `medium`，覆盖下文旧 sol 定案；此次先完成出题。

> **架构决策（2026-09-08）**：权威主清单更名 + 图片部分还原（用户拍板两则）。① `meta/instance_images.jsonl` 更名 **`images.jsonl`**（复用简名，实质推翻 2026-09-06「避开 images.jsonl 旧名防混淆」的命名决策——v1 同名退役件仍在 state/taxonomy/retired_meta/，同名不冲突但文档口径以本条为准）；主仓消费端同步：viewer/build_viewer+HTML、benchmark eval_sample×3/eval_complexity/focus1000_caption/focus1000_merge_lake、curation annotate_backfill/focus_sample/search_kb + dataset_analysis.ipynb（共 12 文件）；meta_unify.py 加退役守卫（历史一次性脚本，照 upgrade_v31_en 先例，字符串留作历史记录）。② 图片还原（目标：减少后续下载量）：Sep 5 tar 备份（目录 `/yzp/zhaozy/yangzepeng/0905/1/`，19×4GiB 分片 = 123云盘 DIR"1" 的全部内容）**流在 80GiB 处截断——原上传只到 part_as，缺 at+ 分片**（`.1.part_ad`/`.1.part_ao` 经 md5 证实为同内容重复下载件；云端无更多分片；`demiwtg_all.tar.gz` 10.8GB 为纯代码+结果备份，零 blobs）。已执行：tar 定向抽取 `datasets/demiwtg/blobs`（路径过滤绝不触碰 meta/，防 Sep-5 旧真相覆盖 concepts/taxonomy）还原 150,633 blobs/68GB（归档序前 19 个 sha 前缀目录）；`curation/harvest_blobs.py` 从本地评测样本副本（benchmark/_staging/bagel）按内容寻址收割 +1,453（preloss sha 过滤，生成图不进湖）；`curation/replay_manifest.py` 按 blobs 实存回放 preloss 清单（抽样 64/64 文件名==内容 sha）→ images.jsonl 194,449 行 / 152,081 blobs（含 1,117 无清单行孤儿 blob，blobs 不可变留置）/ 134,941 概念有图 / 质量门合格口径 15,191 概念；viewer 重建（imgs.js 26.3MB，30,565 概念有图；standalone 同步）。preloss 其余 ~196.6 万 sha 的 blob 自该备份不可恢复；云盘残余可选项：results_archive.tar.gz 9GB + taxonomy_sample_cases.zip 937MB（已删评测目录的归档，样本图约 2GB 为湖副本，可选补拉收割）。③ 同日晚用户拍板**恢复全量 preloss 清单**（推翻早间"清单只回放 blob 实存子集"的回放口径）：meta/images.jsonl 直拷归档件恢复 2,849,013 行——缺 blob 行含下载链接（99% 行带 content_url/landing_url），是集群补采的工作面而非悬空行；curation/replay_manifest.py --apply 封死（重跑会截断回 19.4 万子集），干跑保留为 blob 覆盖报表。**图片字节消费者按 blob 实存过滤行**（共用件 curation/blob_presence.py）：t2i/edit eval_sample 池过滤、eval_complexity build_pools、annotate_backfill scan_pending、focus1000_caption load_targets（viewer build_imgs_js 原有逐行 exists 守卫不变，重建验证 imgs.js 输出与子集期一致 26.3MB/30,565 概念）；仅排序/统计类消费者（search_kb 目标排序）不过滤。集群补采双产物（curation/export_cluster_coverage.py + export_refetch_min.py → state/collect/demiwtg_data_sync/）：lake_coverage_for_cluster.jsonl（blob 实存 194,449 行，集群 schema concepts 键——合并后 --skip-covered/配额/去重按湖内覆盖工作）+ **refetch_min.jsonl.gz**（补采工作清单主件：缺 blob 带 URL 行 195.8 万（同 sha 多概念合并），极简五键 {c:[概念], u:url, s:sha256, e:ext, src:源}，gzip 157MB=全字段版 2.4GB 的 7%——用户拍板"只留下载必要字段+概念加一列 url"减传输带宽；无 URL 10,319 行走常规检索线）；集群侧工具 refetch_missing.py（双格式自动识别+gzip 魔数解压；按源防盗链头下载 + sha256 复验唯一入库闸门 + 原子落 blob + norm_rec 补全 24 字段回写行 + 断点/死信轮；湖侧全量清单在册，回灌后按 (sha,concept) join 还原 license/author/打标元数据零丢失）与 merge_lake_coverage.py 已备 demiwtg-data 本地仓（ahead 待推送）。

> **架构决策（2026-09-07）**：instances.json → concepts.json 概念化迁移（用户拍板：改名 + 契约瘦身为四字段；2026-09-05/06「归一化概念体系」方法论讨论的真相层落地，采集交接件 concepts_batch_200.json 已先行新契约）。定案：① 概念行 = {name, aliases, carriers, taxonomy}，顶层 schema_version+meta+concepts；`curation/migrate_concepts.py` 一次性迁移 385,262 行（干跑→--apply，幂等防重跑；--refresh-taxonomy 供树变更后刷新快照），carriers 存量默认 image+text，taxonomy 快照从域起算、树遍历序（全量有挂载；多挂分布 1 处 348,122 / 2 处 25,574 / ≥3 处 11,566，存量按树实况全量保留，多挂≤3 为新概念策展纪律）。挂载真相仍在树——2026-08-19「挂载关系不持久化」就快照维度修订：快照只作消歧与源路由、不承载知识，禁手改、刷新走脚本。② 退役字段去向：desc → state/collect/concepts_docs_draft.jsonl（docs 层草稿 {name, kind: summary, body}，策展精修 105 条优先保留不覆盖，终态 52,980 条；docs 正式层待消费后另批转正）；query → state/collect/query_terms_cache.json（52,980 名，planner 冷启动先验——检索词从静态资产改为运行时状态）；source 行内退役（分布存 meta.source_stats：derived 330,842 / llm 54,262 / curated 158）。③ 消费端同步：viewer（build_viewer 读 concepts.json + docs 草稿 join 进概念行 docs 字段、sidecar 更名 concepts.js、缓存号 v5、HTML fetch 回退/查表键 window.__CONCEPTS__/stats 改「概念」、source 标签删除、standalone 同步；重建产物 concepts.js 115.8MB，imgs.js 空为图片全量丢失后预期态）；curation/focus_sample（mini 表改 concepts 键，四字段整条拷贝，产物名 focus*_concepts.json）；curation/search_kb（targets 改 concepts 键、--only-empty 判据改 docs 草稿名单、english_alias/all_aliases 的 query 半边改读采集缓存、source==curated 跳过删除）；taxonomy/gen_instance_kb 重写为概念富化器（aliases 回写 concepts.json + docs 追加草稿，不再生成 query/source，写盘持 .meta.lock 原子替换）；curation/annotate_backfill（kb 查表改由 concepts + docs 草稿现场构建，脱离 op_annotate.load_instance_kb）；benchmark edit eval_synthesize / dual_carrier_supplement 与 focus1000 caption/genimg/eval_complexity（desc 改读 docs 草稿，mini 表键兼容 concepts/instances 两代）；三册 curation notebook 路径键同步；taxonomy.json meta.description 自指更新；mount_map/README/.gitignore 例外链（!concepts.json，85MB < 原 98MB）。④ 同批修复 data/ 时代 ROOT 深度残留（focus_sample/search_kb/annotate_backfill/gen_instance_kb 原推导到仓库根上一层）与 import 面（顶层 taxonomy.* 直连；data/ shim 仅保留兼容存量 collect_v2.* 调用方）；§1 树形图同步顶层布局。⑤ 历史一次性脚本（en_entity_merge/meta_unify/upgrade_v31/upgrade_v31_en）不改造：读端对已删文件自然 FileNotFoundError 自守卫，历史溯 git。⑥ instance_images.jsonl 的 instances 字段名与 demiwtg-data 采集链契约不动（外部仓 --concepts 批任务模式已由集群侧上线对齐四字段契约；概念模式打标 kb 的 docs sidecar 补丁+bench283 种子 105 条已备 state/collect/demiwtg_data_sync/——本机无 GitHub 推送凭据，待 SG 授权机推送，2026-09-08）；30 个 candidate 新概念键已随人审合入（2026-09-08：+30 新键建议挂载写树生效、385,262→385,292，快照与树零失配校验通过；253 个既有概念批次与真相零差异无需同步；草稿留档 state/collect/concepts_batch_200.json）。

| 模块 | 职责 | 入口 |
|---|---|---|
| `taxonomy/` | 标签体系维护：树审计（audit_nodes 死叶子审查）、挂载聚合（mount_map，只读现算不落盘）、富化（gen_taxonomy_kb 节点 KB / gen_instance_kb 概念富化——aliases 回写 concepts.json、知识文本追加 docs 草稿，各一次 LLM 调用） | 各脚本 `--write` |
| `curation/` | 数据策展与检索接地：search_kb 概念知识检索接地管线（search_kb_sources 直供源扩充 / search_kb_supervise 全量跑监督；--lang en 赛道已随统一版退役）、annotate_backfill 补标驱动（kb_match=None 行 VLM 打标回写）、migrate_concepts（instances→concepts 迁移器与 taxonomy 快照刷新）、harvest_blobs（本地残留图副本按内容寻址回灌 blobs）、clean_docs_pages（集群 docs 页清洗出净版：原始 pages/ 只读，净版落 state/collect/docs_clean/，判定序 short_raw→low_density→nav_listing→listing_page→html_noise→gibberish→duplicate→keep）、en_entity_merge EN/ZH 实体合并（tier0/bulk/escalate/apply/orphans）、meta_unify meta 收口（images 退役并入 / en 归一并入）、focus_sample 重点补图池抽样、质量分析 notebook（download_quality / search_kb_quality / docs_analysis=docs 页质量分级与清洗呈现 / lake_sync_details=回湖同步明细抽样：两代 schema 盘点、打标完整度、缩略图墙、误绑探针）、数据集分析 notebook（dataset_analysis.ipynb，参数写在 cell 内部，直接运行：① danbooru2024 字段下钻；② demiwtg 权威清单分布与过滤；③ taxonomy 视角节点量级与抽样，只读） | 各脚本 `--help`；notebook 直接运行 |
| `viewer/` | 查看器闭环：页面 tag_tree_explorer.html + 构建脚本 build_viewer.py（读 taxonomy/concepts/docs 草稿/主清单四源，docs join 进概念行；imgs.js 只收录 VLM 打标行、每概念 top-50、caption 截断 100 字）+ 产物 build/（sidecar taxonomy.js/concepts.js/imgs.js 与 standalone 单文件，gitignore；英文平行页已随统一版退役删除）；HTML 与 build/ 同址是 file:// 双击可用的硬要求 | `viewer/build_viewer.py` |
| `benchmark/` | 评测基准：按三大题型拆成三子模块（见架构决策 2026-08-24 三子模块拆分）。**t2i/**（生成）与 **edit/**（编辑）各带完整四件套：抽样（eval_sample.py 分层配额，--filter 一条 duckdb SQL WHERE；edit 版默认叠加编辑适配门）、出题（eval_synthesize.py，Galaxy API；t2i 版含 facet 词表审计、edit 版 9 类 edit_type 轮转 + 每第 5 题知识编辑套）、判分（eval_score.py 调本地 vLLM judge，score/dump 子命令；t2i 版 FACETS 权威源 + φ 映射聚合，edit 版 EDIT_DIMS 三维钳制）、gen_results_review.py（生成审阅 notebook）；**vlm/**（理解）暂不拆代码，只放 notebook。每子模块两个 notebook（现在 reviews/ 下）：question_dev.ipynb（抽样+分布+题库审阅，for 题目构造）、results_review.ipynb（打分/评估结果分析）。评测数据布局见架构决策 2026-09-05（t2i：bench200/ 现行 + archive/ 历史 + data/ 默认落点；edit：无 data/ 层，批次目录 synth_v*/、活图池 focus200/、归档 archive/ 全落子模块根）；样本图/题库/判分产物均不入 git（.gitignore 登记）；出题/判分协议 md 在 prompts/、随代码入 git；编辑评分契约 edit/edit_score_prompts.json（ImgEdit 官方原文，随代码入 git） | 各脚本 `--help`；各子模块 `reviews/question_dev.ipynb` / `reviews/results_review.ipynb` |

> **架构决策（2026-09-05）**：bagel/ 子项目入主仓 + benchmark/bagel/ 第 4 场景入 git（用户拍板，推翻 2026-08-23「独立 git 仓库 + 主仓整体排除」方案——该子仓 .git 已随旧机迁移不复存在）。定案：① 主仓 .gitignore 撤销未锚定 `bagel/` 整体排除，bagel/ 以普通目录随代码入库；重物仅定向排除：`bagel/Bagel/models/`（BAGEL-7B-MoT 权重 ~28G）与 `bagel/Bagel/eval/vlm/data/`（VLM 评测下载数据 mmbench ~50M）；上游 Bagel 自带 .gitignore（wandb/results/eval_results/notebooks/tests 等）在子树内继续生效；`bagel/models -> Bagel/models` 兼容软链随库入库；子项目内部布局自治不变。② 未锚定规则撤销的连带效应：`benchmark/bagel/`（第 4 场景：以 BAGEL-7B-MoT 为被测模型的标准基准评测，2026-09-05 物理整合，详见其 README 与 results_review.ipynb）此前被整体遮蔽未入 git，本次入库——仅代码与文档入 git（README、results_review.ipynb、gen/+vlm/ 脚本），data/（~1.2G 题库/出图/运行缓存）与 vendored 官方 git 仓（gen/qib_official、vlm/VLMEvalKit，可再克隆）不入主仓。③ t2i 线评测数据排除边界同步登记：archive/（仅 MANIFEST.md 入库）、bench200/（仅 README.md 入库）、focus1000/data/ 不入 git（题库/出图/判分/溯源留本地，口径见各目录文档）；edit/ 边界以下条「edit 子模块目录对齐 t2i 并废除 data/ 层」为准。④ /data/ 登记 .gitignore 不再入库：现存为 collect_v2 退役残件（根目录 taxonomy/、curation/ 现行版本的迁移前旧副本/重复件），留档本地不删。
>
> **架构决策（2026-09-06）**：edit 判分协议角色分离（用户拍板：判官提示词不得混入 codex 任务书内容——原 v2 合并文本会把「不得读取 caption/reasoning/level/suite/模型名/另一候选」等编排语义喂给判官，构成判定噪音与锚定风险）。定案：① 新增 `edit/prompts/judge_prompt_edit_qib_v2.md` = 判官唯一权威源（TEMPLATE 块 + 9 个 `<!--TYPE:x-->` 分型块；只含 rubric 三档定义、分型核对重点、落档硬判据、判分流程、特殊情况与裸输出 JSON；φ 映射与 d2/d3≤d1 钳制规则**刻意不向判官展示**——换算与钳制是管线确定性计算，防钳制语义反向锚定判官原始落档，此点与 ImgEdit 官方把钳制句写进判官 prompt 的做法有意分歧）。② 原 `codex_score_prompt_edit_v2.md` 重写为编排协议（任务书契约）：判定主体与盲评隔离的物质保障、prepare→render→逐字判定流程、分数行字段契约（身份/哈希字段从 manifest 逐字照抄，mapped=φ(tier) 与 official_* 机械换算）、有效性政策（model_failure 计 0 / invalid_question 成对剔除 / 基础设施重试）、aggregate/compare 命令、冻结后审计规则——其内容永不进入判官输入。③ `eval_codex_score.py` 新增 `render` 子命令（解析模板标记块，按题渲染 eNNN.txt + index.jsonl 登记 prompt_sha256/instruction_sha256，--manifest 时附盲评图片绑定），与 t2i 的 judge_prompt_gen_v6.0_V2.md 惯例对齐；新增 `ingest` 子命令（判官裸输出 raw/<qid>.txt → 机械补齐身份/哈希/换算字段并逐题校验维度契约 → part_*.jsonl；--format json=QIB 裸 JSON / imgedit=官方 Brief reasoning+分数行，MODEL_FAILURE 标记按各自口径记失败；part 文件禁止手写），管线成 prepare→render→派发→ingest→aggregate/compare 五段。判官调用方式定为**每题一个全新子代理上下文**（输入只有单题物化 prompt + BEFORE/AFTER 两图，零附加）——上下文隔离由结构保证，编排者只调度不亲判；任务书因此收缩为派发规则+命令清单。④ pilot 已冻结两轮（Gemini/Bagel QIB）判定用旧合并文本，`synth_v61_pilot/scores_qib/prompts/` 为新协议复建渲染（审计对照）；此后任何新判分轮必须 prepare→render 后逐字判，未物化产物无效。⑤ v1（1–5，冻结）与官方 rubric 实验臂（本就逐字物化）不受影响。⑥ edit 赛道判官模型钉定 `gpt-5.6-sol`（用户拍板：与 pilot 已冻结两轮一致，新轮次含实验臂一律沿用，跨协议对比不混入判官差异；曾短暂考虑换 gpt-6-astra，否决），任务书 `--judge` 固定写 `gpt-5.6-sol-built-in-imgedit-official`。⑦ 对照臂文档对称化与 prompt 世代归档：官方 rubric 臂增两份 `prompts/judge_prompt_edit_imgedit_official.md`（判官原文载体：ImgEdit 官方九类 rubric 逐字内置为 OFFICIAL_TYPE 九块，只 `<edit_prompt>` 单处替换；render 与契约 `edit_score_prompts.json` 逐字节一致性校验，块或契约任一侧被改写即拒跑（已负测试）；官方钳制句与 ≤20 词纪律原样保留——对照臂忠实性优先，与 QIB 臂"钳制不进判官输入"的防锚定策略有意分歧；判官文档不含任何编排内容）与 `prompts/codex_score_prompt_edit_imgedit_official.md`（通用编排协议，与 QIB 协议结构平行；1–5 与 QIB 百分制禁线性互换，臂产物永不进主口径结论）；`render` 支持官方臂 md/json 与 QIB md 三种模板（pilot 官方臂 20 题以 md 模板重渲染 sha256 20/20 复现）；退役 prompt 归档 archive/prompts_v1_v60/（出题协议初版+v6.0、判分协议 v1）与 archive/audit_doublecheck_prompt.md（复核轮已结、结论已落地 eval_synthesize，留作模板），archive/MANIFEST.md 登记；reviews/ 两册退役审阅 notebook 移 archive/notebooks_retired/（results_review.ipynb 的冒烟分析对象本机已不存在、audit_review.ipynb 属复核轮），question_dev.ipynb 按惯例留位且路径文案同步新布局。⑧ 两份判官 prompt 经 gpt-6-astra 全文评审（报告存档 `reviews/judge_prompts_review_gpt-6-astra_20260906.md`：文档 A 23 条 + 文档 B 机制 7 条 + 官方文本观察项 18 条）。处置：无副作用修复即时落地——QIB md 补九类三维名称附录快照（A01：拆分时维度表只留在代码 EDIT_DIMS 的权威源闭合回归）、官方 md 头部来源登记（B-M01：GitHub 出处 + 契约 sha256 基线 f9468dc8…，并如实声明双副本校验的已知边界）、render 加载期闭锁（B-M03：块唯一性/九类完整性/占位符唯一校验，重复块负测试通过）；两臂渲染 sha 修复前后 20/20 一致，判官所见零变化，pilot 冻结分数不受影响。判据类修改（A02 Excel 门槛机会偏差、A04 通用硬判据误罚 style/background/extract 合法重绘、A09 异常态与 JSON 契约闭合等）一律不在冻结期动，汇入 200 题正式批次前的 QIB v2.1 修订；官方 rubric 原文一字不动，18 条观察项（钳制非独立、钳制句锚定、style 无参考图、compose 部分成功奖励差、"两臂比较不只换分制"等）作为三臂结果解读的必读注记。⑨ QIB 判官 prompt 去内部键名（用户拍板 2026-09-06）：判官输入/输出全面改用维度名与"维度一/二/三"（d1/d2/d3 降为纯存储层位置键，仅存在于 EDIT_DIMS、分数行 schema 与 aggregate 校验中），TEMPLATE 增设显式「输入/输出」节，判官上下文不跳转；ingest 校验改为按序匹配维度名并自动补 key（兼容带 key 输出，错维度名负测试拒收）；pilot 复建渲染已按新模板刷新（sha 变化，冻结两轮分数不受影响），官方臂渲染经回归验证 20/20 不变。⑩ QIB 判准 v2.1 落定（用户拍板：v2 不再开新轮次）：新增现役判官模板 `prompts/judge_prompt_edit_qib_v2.1.md`——逐档硬判据全部下沉到九个 TYPE 块（每型 =「类型前提」+ 三维度各自的 0/1/2 判据，按该维在该类型下的真实语义写），通用节只留类型无关骨架（角色/输入/三档定义/三条通用判定原则/流程/特殊情况/输出契约）；吸收 astra 评审修正项：A02（Excel 改为"全部精确达成 + 任务内可核验的精确执行"，废除"超常规"相对参照，简单题同有可达档 2）、A03（0/1 边界 = 硬约束违反 vs 连续量偏差；记 0 必须有可指认证据，存疑不记 0）、A04（style/background/extract 的全图重绘属授权操作，不再被通用"大范围重绘 Fail"条款误伤）、A05（独立归因：任务失败不等于其他维度自动失败）、A06（编辑归因：只判新引入缺陷，源图既有缺陷不扣分、未授权修复不奖励）、A07（辨识限度条款）、A09（invalid/judge_unscorable 的 detail 必填、model_failure 的 reason 规则）、A21（extract 可见部分不得补全、天然浅色轮廓不算白边、相对布局改变属偏差）、A22（background 前景投影归属规则：投在背景区域的阴影/倒影按新光源重建、归 background 区域，协调性入 Physical Consistency 判）、A23（compose 先拆两个子操作逐个核对，细节偏差不等于少做一项）。render 默认模板切至 v2.1，编排协议角色表与步骤 2 同步；v2 原文保留（pilot 冻结两轮的判准，`scores_qib/prompts/` 即其渲染，审计对照），pilot 分数不追溯重算；**v2.1 与 v2 的分数不可直接互比**（档位语义与判据均变），跨批次对比必须显式标注判准版本；200 题正式批次首轮起用 v2.1。⑪ 判官文档纯净化（用户拍板：判官文件不写渲染机制——判官拿到的已是渲染成品，维护者注对判官是噪音、对人是错位置）：两份现役判官 md（judge_prompt_edit_qib_v2.1.md、judge_prompt_edit_imgedit_official.md）删除头部维护者引语与 v2.1 文末附录，只含标题 + 模板块；机制信息归位编排协议——九类三维名称快照表 + EDIT_DIMS 权威源声明 + "判官文件不写维护者注"规则入 codex_score_prompt_edit_v2.md 附录，官方臂来源登记（arXiv/GitHub 出处 + 契约 sha256 + 校验边界 + 钳制句与 20 词要求属官方口径逐字保留）入 codex_score_prompt_edit_imgedit_official.md；清理后三臂渲染回归：v2 冻结模板与官方臂 sha 20/20 不变、v2.1 渲染 20/20 零占位符；v2 历史模板头部原样保留（审计定位，不改写历史文件）。⑫ 判官 prompt 精简终态（用户拍板，推翻⑪中"v2 头部保留"处置：分型判据 = 全部判定文本，通用节即噪音；对齐 ImgEdit 官方的精简形态）：v2.1 TEMPLATE 收敛为「角色→输入→本题判据（分型块）→输出→题面」——判分规则元规则节、三条通用判定原则、判分流程、特殊情况节、{{DIMS}} 维度清单节全部删除；validity 四态语义与 observations 四组定义折叠进输出节（字段定义所在处）；"存疑不记 0、拿不准 1/2 给 1、同题同标准、任务失败不连坐"等跨类型纪律随通用节移出判官文本——分型判据的逐维锚点承载档位边界，未来校准如需恢复纪律条款，以分型判据形态写回；v2 冻结模板同步瘦身为"标题+模板块"（删头部维护者注与附录快照；TEMPLATE/TYPE 一字未动，渲染 sha 回归 20/20 不变）；编排协议附录快照表删除（EDIT_DIMS 代码表为维度名唯一权威源，任何文档不维护快照副本，规则并入职责边界段）；TYPE 分型块改为 ImgEdit 官方同款布局（维度名单独行 + "0/1/2 + 两空格 + 判据"纯文本行，无 markdown 修饰，"类型前提"取消、其语义并入对应档位判据），v2.1 单题判官 prompt 6313→3922 bytes，三臂渲染回归全绿。随后用户手排 v2.1 定版布局（一、输入 / 二、打分规则（九类判据逐节陈列）/ 三、输出 / 四、题面 四部分编号结构）并删除 v2 模板文件；机械修复恢复可执行：补 TEMPLATE-END、恢复四、题面与"两张图"收尾句、九个 TYPE 块移出 TEMPLATE 至模板块外（渲染只注入本题类型判据，判官不见其他八类，杜绝跨型锚点污染与提示词膨胀）、`## 类型` 标题归一到标记外；v2 删除后的审计凭 `scores_qib/prompts/` 渲染产物与 index sha 登记保存（协议角色表同步）；v2.1 终版 = 四段编号结构（一输入 / 二打分规则＝{{TYPE_NOTES}} 注入本题类型判据 / 三输出 / 四题面＝{{EDIT_TYPE}}+{{INSTRUCTION}}），九个 TYPE 块在模板块外作素材区（渲染只注入本题类型，判官不见其余八类，杜绝跨型锚点污染），中途试验过"判据内嵌模板、渲染剪裁"方案经用户定夺回退为占位注入式；单题判官 prompt ~3.8KB，QIB 渲染 20/20、官方臂 sha 回归 20/20 绿。⑬ 文档去重收口（用户质疑两份任务书冗余后定案）：官方臂独立编排协议 `codex_score_prompt_edit_imgedit_official.md` 删除，其独有内容（变体参数对照表、口径禁令、来源与完整性登记）并入主协议 `codex_score_prompt_edit_v2.md` 的「官方 rubric 对照臂」一节——编排协议全仓只此一份，两臂走同一六步流程仅参数不同；批次执行仍由各批次 TASK 承担（冷启动执行者需要实例化路径的具体工作指令，通用协议带占位符不可直接执行）。文档终态：判官文本 ×2（QIB v2.1 / 官方原文载体）+ 编排协议 ×1（含对照臂变体节）+ 批次任务书 ×1（TASK_official_rubric_codex.md）+ 契约 edit_score_prompts.json + 管线 eval_codex_score.py；官方臂渲染回归 sha 20/20 不变。
>
> **架构决策（2026-09-05）**：edit 子模块目录对齐 t2i 并废除 data/ 层（用户拍板），同日登记 ImgEdit 官方 rubric 实验臂。定案：① edit/ 新布局：prompts/（出题协议 synthesize_prompt_edit*.md + 判分协议 codex_score_prompt_edit_v{1,2}.md + audit_doublecheck_prompt.md，随代码入 git）、reviews/（四册 notebook：question_dev / results_review / results_review_v61 / audit_review）、archive/（synth_v60 世代批次 + complexity_audit 账本 + audit_doublecheck 复核产物，MANIFEST.md 入 git）、批次目录与活素材落子模块根（synth_v61_pilot/、focus200/、complexity_audit_synth.jsonl、judge_prompts/ 物化区，均 gitignore）；data/ 层撤销（t2i 维持既有布局不动）。② 排除集机制统一升级：三份 eval_sample*.py 的 DEFAULT_EXCLUDES 改为三赛道整目录递归扫描 samples*.jsonl（原 data/+archive/+bench200 定向桶列表在 edit 去 data/ 后会漏扫顶层落点），任何布局演化下历史样本永不回流；t2i/archive/MANIFEST.md 排除集条款同步改写。③ 全部脚本目录常量随迁（EVAL_DIR/OUT_DIR/DATA→子模块根，audit_doublecheck 默认读物→archive/，gen_results_review 产物→reviews/），py_compile 全过。④ 12 份盲评 manifest/identity（scores_qib×4、scores_official×4、scores/codex_blind×2 等）的绝对路径全部改写至新址并逐一验证文件存在，旧机器 /tank 前缀残件一并清理。⑤ results_review_v61.ipynb 迁 reviews/ 重建重跑（demiwtg 内核，零报错，report 对账一致）。⑥ ImgEdit 官方 rubric 实验臂（判分文本物化）：官方 prompts.json 逐字渲染 20 题 judge prompt 落 synth_v61_pilot/scores_official/prompts/{eNNN.txt,index.jsonl}（sha256 登记，判官不得回读源 rubric），判官=codex 内置模型盲评（TASK_official_rubric_codex.md），产物只落 scores_official/，与 QIB 主口径物理隔离、永不混入正式结论；动机=量化 rubric 文本本身的协议效应，并保留与 ImgEdit-Bench 官方口径对话通道。
>
> **架构决策（2026-09-06）**：meta 真相区统一收口（用户拍板：EN 并入中文湖成统一版、images.jsonl 收官退役、VLM 补标后置另跑）。EN/ZH 实体合并由 `curation/en_entity_merge.py` 五层执行：tier0 确定性配对（别名/西文串≡EN 名限同节点，38,701 对，0 调用）→ bulk 逐节点 LLM 对齐（本地 vLLM Qwen3.8-27B @8001，16,527 节点全完成、341,302 对、0 API 调用）→ escalate 红旗节点复核（四模型轮转 glm/glm-5.3-flash + qianwen/qwen3.7-plus + galaxy/qwen3.6-flash + galaxy/glm-5.3，关思考防思维链截断，368/375 有效）→ apply 写库（matched/variant→中文实体 aliases 326,272 名、new→新实体 88,249 个 source=derived 入库挂树 99,013 处、tier0 回填 1,330 对；instances 296,010→384,259）→ orphans 兜底（对齐未覆盖的 EN 独有节点实体 1,003 个全量入库挂树）。配套 `curation/meta_unify.py`：metadata_en.jsonl 73,962 行归一并入（EN 实例名按对齐映射归一中文正名，(sha,instance) 去重）+ images.jsonl 322,332 行炸开并入（v1 采集字段 tiers/credit/source_rank 等照 migrate.py 先例丢弃，identity/focus/quality=null 待补标）。收口：images.jsonl/metadata_en.jsonl/taxonomy_en.json/instances_en.json 四文件物理移出 meta/ 归档 state/taxonomy/retired_meta/（改前另有 backup_pre_en_merge、backup_pre_orphan_ingest）；2.2 白名单收敛为 metadata.jsonl+三件套+.meta.lock，metadata.jsonl 升统一权威主清单；消费端同步（viewer/build_viewer.py 改读 metadata.jsonl、imgs.js 精选口径=打标行+top50+caption 截断、缓存号 v4、--lang en 与 tag_tree_explorer_en.html 删除；search_kb --lang en 拒绝退役；upgrade_v31_en.py 加防误跑守卫；eval_sample×2/annotate_backfill 注释更新；.gitignore 英文件例外链移除）。终态校验：metadata.jsonl 2,849,013 行 / 2,116,511 sha / 304,190 实例全部在册、name 唯一性通过、并入零新增重复键（存量遗留 261 个重复键为老采集链时代产物待后续清理）。追加定案（同日）：alias_western.json 退役归档 retired_meta/——49,197 个非空西文串 100% 已含于 instances.aliases（数据零独有），246,813 个 null 的负缓存语义（op_seed 防重问）由用户后续改 demiwtg-data 仓库 op_seed 读端承接；en_entity_merge tier0 读端已容错缺文件；meta/ 白名单收敛为 metadata.jsonl + taxonomy 两件套 + .meta.lock。追加定案（同日晚）：metadata.jsonl 更名 **instance_images.jsonl**（用户拍板：语义=实例×图片观测对，避开已退役的 images.jsonl 旧名防止契约混淆；本条目前文中的 metadata.jsonl 均指此文件）；主仓 11 个引用文件同步改名（bagel 的 geneval 自带同名文件不动）；instances.json 维持实体+富知识一体（拆分否决：desc/query 是实体 1:1 正典记录而非模态样本，aliases 是结构性匹配字段；体积增长留观，每实体一份 desc 的规模问题届时再议）。
>
> **架构决策（2026-08-25）**：新开 `modelhub/` 独立子项目（用户拍板：可单独 push GitHub、其他机器 pull 直接复用；静态代理全套并入）。定位：本地 LLM 统一接入层——LiteLLM 网关（127.0.0.1:4000，OpenAI 兼容）路由三条线：本地 vLLM（qwen3.8-27b，no_proxy 直连）、Galaxy 专线（qwen3.7-plus，no_proxy 直连）、OpenRouter 通配（进程级代理注入 → mihomo 按域名分流走静态住宅 IP 出口）；静态代理模块即原 `/root/gpu-static-proxy`（mihomo v1.19.30 双层链式：10808 隧道换源 IP → 静态 IP 节点 216.132.205.99；modelhub 只维护「AI API 域名走 STATIC 双层静态出口」一层规则，其余流量 modelhub 视角直连、继承宿主策略、不感知不维护）整体迁入 `modelhub/static_proxy/`（二进制与 GeoIP 库随迁，旧目录作废可删）。定案：① 照 bagel 先例：独立 git 仓库、主仓 .gitignore 整体排除、内部自治（自带 README，不受主仓「文档只两份」约束）；② 机密零入库：.env（API keys）与 static_proxy/config.yaml（节点凭据）只进 gitignore，仓内只有 *.example 模板；mihomo 二进制不入库（fetch_mihomo.sh 下载/旧机拷贝）；③ Python 环境独立：modelhub/.venv（litellm[proxy]==1.98.0 锁定），不碰主仓 .venv（不动主仓 openai/httpx/pydantic）；④ 消费端零改动启用：网关为 OpenAI 兼容端点，既有脚本换 LLM_BASE_URL=http://127.0.0.1:4000/v1 即接入，逐步迁移；上游端点与 key 全部 .env 可配置，启动时 gateway/gen_local_models.py 自动发现各 *_API_BASE 端点的模型并注册进 /v1/models（openrouter/* 通配 litellm 原生展开；Cline 按 OpenAI Compatible 配置网关地址即自动带出模型列表）；⑤ 端口登记（均仅本机监听）：4000 网关 / 7891 mihomo mixed / 9091 mihomo API / 1053 mihomo DNS。
>
> **架构决策（2026-08-24）**：英文版标签体系入库（用户拍板三则：扩白名单同居 meta/、实例名轻量清洗、查看器 --lang en 独立页面）。「融合世界标签体系 v3.1」交付包英文底稿（taxonomy_tree_instances_en.csv：21,406 行，中文路径/英文路径/英文实例清单三列）由 `taxonomy/upgrade_v31_en.py` 建成一套完全独立的英文平行数据（干跑→--apply，照中文版惯例；不动中文三件套与 alias_western.json）。定案：① taxonomy_en.json / instances_en.json 同居 meta/，2.2 白名单扩两行 + .gitignore 例外链放行入 git；② 前缀归一为中文 norm_path 的英文同款（剥根 Fused World Label System + General Classification Tags，换根 demiwtg），底稿缺行的 4 个骨架域与中文同源隐式补齐；③ 底稿实测 119 组翻译撞车（不同中文节点译成同一英文路径，如 炊具/锅具 → Cookware，名单重合度中位数仅 0.02）用户拍板自然合并（名单取并集），另 2 个译名折叠展开隐式节点（中文段『帝王蟹/蟹』译成 King Crab / Crab 两段）→ 英文树 21,291 节点 = 中文树 21,409 - 撞车合并 120 + 折叠展开 2，域级对齐（撞车与折叠清单落 state/taxonomy/en_merge_report.json 供后续精译修复）；④ 英文实例名轻量清洗（按词：全小写/全大写词转首字母大写，全大写缩写与混排词保留），清洗后同名大小写变体合并（name 唯一主键），413,329 → 382,341 全量 source=derived 占位入库（不做富知识、不映射中文知识），清洗合并明细落 state/taxonomy/en_clean_report.json；⑤ viewer 复用：build_viewer.py 加 --lang en，读英文两件套写 viewer/build_en/ sidecar（imgs.js 注入 null——英文实例与 images.jsonl 打标零交集，英文版无图是预期），页面 tag_tree_explorer_en.html 由主页面现场替换生成（标题 demiwtg (EN)、sidecar ?v=1 独立缓存号、fetch 回退改英文两件套），页面入 git、build_en/ 产物不入。结构对齐验证以中文路径列为桥：归一中文路径 21,405 ⊆ 中文树，差集恰为 4 骨架域。纯新增零覆写，故无入库前备份。图数据（images.jsonl/metadata.jsonl）零改动。
>
> **架构决策（2026-08-24）**：`data/datasets/` 整体升为项目根目录 `datasets/`（用户拍板：自建与开源数据集同居一处；data/ 保留原名只住代码）。理由：`data` 作顶层名太泛，且目录里早已住着 collect_v2/taxonomy 两套代码名实不符；拆开后 `datasets/` 语义精准、`data/` 收敛为数据构建代码区。定案：① 同盘 rename 零拷贝（~1.1TB：自建 demiwtg 631G + 23 个开源数据集；24 目录全量在位，blobs/meta 完好）；② 路径改址全链路：data/collect_v2 与 data/taxonomy 的 REPO_ROOT/ROOT 推导补一层（此前代码被搬入 data/ 后已暗指 data/ 而非仓库根，state//logs/ 类路径实已失效，本次一并修复）+ 数据集路径常量去 `"data"` 段，import 实链路断言验证；benchmark t2i/edit 的 eval_sample 默认路径同步；viewer 页面/构建脚本改址并重建 build/ 产物（20,100 实体）；③ .gitignore 例外链改 `datasets/*` 逐级放行三件套；④ 历史决策块旧路径为当时快照不回改；⑤ curation 模块在搬移中被误删的 dataset_analysis.ipynb 已由快照恢复归位 data/curation/（20-cell 终态：基线 16 格逐字对齐 8/22 快照 + 全部补丁/内联写命令按时序重放 + 拆分后删尾四格；抢救过程产物在 state/curation/_nb_recovery/，可清理），路径与 import 已按 data/ 新位置与顶层 datasets/ 调整。本会话未动 .qoder 交接文档与 logs/ 归档脚本（历史快照）。
>
> **架构决策（2026-08-24）**：benchmark 按三大题型拆成三子模块（用户拍板）：`vlm/`（理解）、`t2i/`（生成）、`edit/`（编辑），推翻上一条「评测数据在 benchmark/ 根三目录」布局。定案：① t2i/edit 各带完整四件套（eval_sample/eval_synthesize/eval_score 由原跨赛道脚本拆分，通用工具各留一份子模块自闭环，不建 common）；vlm 赛道协议未拍板暂不拆代码，只放 notebook；② 旧评测数据三目录（eval_v1/eval_v2/taxonomy_sample_cases，合计 ~2GB）用户拍板删除重抽，重抽时指定目录分落三子模块 `data/`（不入 git，.gitignore 登记；仅保留契约文件：出题 prompt 两册与 edit_score_prompts.json 随子模块入 git）；③ notebook 两册变一册：每子模块 question_dev.ipynb（合并原 eval_analysis + eval_review：抽样委托 + 分布分析 + 题库审阅，for 题目构造）与 results_review.ipynb（原 wkbench_review.ipynb 改名+按赛道裁剪：打分/评估结果分析，由各自 gen_results_review.py 生成）；④ 抽样排除集改三子模块 data/samples.jsonl 互斥（跨赛道防重复出题）；⑤ bagel 侧 `run_wkbench.py` 同步改址：DEFAULT_QUESTIONS 改指 t2i 赛道新题库（`benchmark/t2i/data/synth_gen/questions.jsonl`），样本图根目录随 --questions 位置自动推导（题库目录与其上级两级候选，适配各赛道 data/imgs/ 布局）。判分/出题协议本身零改动（FACETS 权威源、φ 映射、三维钳制、9 类轮转配额原样随迁）。
>
> **架构决策（2026-08-24）**：公共环境 `env/` 改名 `.venv/`（用户拍板；最初提议 `.env`，因与 dotenv 密钥文件约定撞名——密钥扫描/搜索排除类工具对 `.env` 有特殊处理——改用 Python 环境惯例隐藏名）。同盘 rename 零拷贝；bin/ 内 100 处旧路径（97 shebang + config 脚本）批量重写，conda-meta/lib 零引用无需动（内部亦无绝对路径软链）；bagel 侧 4 处脚本引用与 Jupyter kernel demiwtg 同步改址；.gitignore 登记改 `.venv/`。历史决策块中的 env/ 旧路径为当时快照，不回改。

> **架构决策（2026-08-24）**：`bagel/env` 提升为项目公共环境 `/tank/demiwtg/env`，`.venv-notebook` 退役（用户拍板）。理由：主仓侧实验（水印检测 pilot 等）需要 torch 推理栈，为它单独建环境浪费且易漂移；环境本体与 bagel 代码仓无关，提升后 bagel 脚本与主仓实验共用一套。同盘 rename 零拷贝；bin/ 85 个 shebang + bagel 内 4 处脚本引用 + conda-meta 批量重写（二进制内嵌串与 conda history 旧路径不影响运行，沿用 2026-08-23 迁移先例）。`.venv-notebook` 唯一消费者是 Jupyter kernel demiwtg，改指 env/ 并补装分析栈（duckdb/ipykernel；pandas 用 env 既有 2.3.3），dataset_analysis.ipynb 全 12 cell 复跑零错误后删除。`models/.venv-vllm` 维持 vLLM 部署专用不动。env/ 不入 git（.gitignore 登记）。

> **架构决策（2026-08-24）**：标签体系升级 v3.1（用户拍板三则：死名保留回挂、新增实例分批入库、英文底稿仅扩名单）。外部交付的「融合世界标签体系 v3.1」终版底稿（29 域 / 21,406 路径 / 29.6 万实例，三批迁移终版：域级前缀替换 + 22 个 IP 域吸收并入通用域 + 知识与学科清理）由 `taxonomy/upgrade_v31.py` 一次性升级进仓（干跑→裁定确认→--apply，幂等：每批实例入库后可重跑刷新树名单）。定案：① taxonomy.json 按底稿重建（21,410 节点 = 底稿 21,406 + 4 个底稿缺失域骨架隐式补齐；schema 1.1.0），节点 instances 名单只留在册名（∩ instances.json，跨节点多挂 9,075 名属契约允许），「IP 分类标签」分支随吸收自然消失；② 失配节点 KB 用名单指纹法抢救（前缀替换名单随行 ⇒ 直接名单/子树名单精确匹配 + Jaccard≥0.85 唯一命中，命中 1,483/1,491，报告落 state/taxonomy/v31_kb_recovery_report.json，未命中 8 条可 gen_taxonomy_kb 重生成）；③ 95 个死名实例（粗伞名，如主战坦克/黄道十二宫，涉及 1,010 次打标）不剥离：保留实例与图，按原挂载路径回挂存活节点（零歧义，无退化）；④ 新增 237,781 实例本次不写 instances.json，按域分组候选清单落 state/taxonomy/v31_pending_instances.json，分批富化入库（用户逐批确认）；⑤ alias_western.json 仅扩名单（+522 条 null 占位，英文实例中英不对齐不做别名填充）。图数据（images.jsonl/metadata.jsonl）零改动。交付包归档 state/taxonomy/v31_交付包/（不入 git）；升级前三件套+metadata.jsonl 物理备份在 state/taxonomy/backup_pre_v31/。
>
> **架构决策（2026-08-24）**：v3.1 新增 237,781 实例占位全量入库（用户拍板，推翻上一条定案④的分批富化后才入库）。理由：树名单只留在册名导致 viewer 只见 5.8 万实例，用户要求全量可见。定案：① 237,781 个待入库名以 source=derived 占位（仅 name，与存量 derived 同构）一次性写入 instances.json（58,229 → 296,010），富知识（desc/aliases/query）留待后续按域分批 LLM 富化；② 重跑 upgrade_v31.py --apply 幂等刷新树名单（丢弃引用 0、死名回挂 95 与 KB 保留 1,493 不变；v31_pending_instances.json 清零）；③ alias_western.json 按既定「仅扩名单」策略 +237,781 null 占位；④ viewer 重建（taxonomy.js 11.1 MB / instances.js 72.9 MB，树名单唯一名 296,010 = 在册数，名单⊆在册校验过）。入库前物理备份在 state/taxonomy/backup_pre_placeholder_ingest/。
>
> **架构决策（2026-08-24）**：标签树路径前缀精简（用户拍板）：根『融合世界标签体系』→ `demiwtg`，废除一级分支『通用分类标签』，29 域直挂根（路径口径 `demiwtg / 域 / 二级 / ...`；历史双树期 IP 路径同样归一）。定案落在 `data/taxonomy/upgrade_v31.py`（norm_path 归一函数：底稿路径与旧树路径统一归一，幂等可重跑不回退旧结构）；底稿根行与中间层行同归 demiwtg 空名单按序合并（合并 1 行，节点 21,409 不变）。重跑 --apply 重建：KB 保留 1,493 / 死名回挂 95 / 实挂引用 346,729 / 唯一名 296,010 全不变；KB 指纹抢救失配 0（上轮已全部归位，本轮路径全精确匹配）。消费端同步：① benchmark t2i/edit eval_sample.py 的 branch_of 注释与层级口径（域现在是 segs[1]，抽样分层粒度随之为 域×二级）；② viewer 页标题改 demiwtg、废除已失效的 IP/通用双树过滤器下拉框、sidecar 缓存号升 ?v=3（build_viewer.py SIDECAR_MARK 同步）；③ curation dataset_analysis.ipynb 注释旧路径示例。图数据零改动；改前备份 state/taxonomy/backup_pre_prefix_simplify/。
>
> **架构决策（2026-08-24）**：评测数据由 state/curation/ 迁入 benchmark/（用户拍板）：eval_v1/（360M）、eval_v2/（893M）、taxonomy_sample_cases/（775M，无代码消费者，人工审阅用）三目录同盘 rename 零拷贝。理由：这些是评测**结果数据**而非运行时状态，与出题/判分代码同模块闭环（沿用 bagel/env 提升的同盘迁移先例）。配套：① 全部引用同步改址——eval_sample/eval_synthesize/eval_score 的默认路径常量改自脚本自推（`Path(__file__).parent`）、三个审阅/分析 notebook、gen_wkbench_review.py、bagel 侧 run_wkbench.py 的题库与图根绝对路径；② .gitignore 登记三目录不入 git（代码仍入库）；③ judge prompt 物化目录随 EVAL_DIR 落在 eval_v2/judge_prompts（仍不入 git）。历史决策块中的旧路径为当时快照，不回改。
>
> **架构决策（2026-08-23）**：覆盖口径带质量门（用户拍板）：op_coverage.load_coverage 新增 min_quality/require_identity 两参（默认 8.0/开，即 notebook 默认过滤同款口径），只数合格行；「有图但全不合格」的实例按 0 图对待继续采，缺 quality 字段的存量迁移行按不合格计。chain 新增 --min-quality/--require-identity（BooleanOptionalAction）。重点下载零合格图实例用 `--skip-covered 1`（当前实测范围 5,001 个）；两门全关退化为旧口径（回归验证与无门时计数完全一致）。分区排序（0 图排队首）自动沿用合格计数。
>
> **架构决策（2026-08-23）**：wkbench 首跑冒烟修复（runner 侧补丁，不动官方模型代码）：edit 任务将输入图过 fp32 VAE 编码，在 bf16 autocast 区内 conv_in 直接炸 dtype 不匹配，且编码产物经 NaiveCache 进 vae2llm 时 accelerate dispatch 钩子挂不上。修法两条，都在 run_wkbench.py：① vae_model.encode 包一层（内部禁 autocast + 显式对齐 VAE 设备，因实例属性影子掉 dispatch 钩子的设备搬运）；② forward_cache_update_vae 包一层 shim，在 vae2llm 自身 device/dtype 上做投影（与 generate_t2i.py 的 decode_image 设备修复对称）。vlm/t2i/edit 三任务单卡冒烟全过后才起双卡分片全量。
>
> **架构决策（2026-08-23）**：bagel 项目由 `/tank/bagel` 整体迁入本仓 `bagel/`，成为登记子项目（用户拍板，顶层目录禁令的登记例外）。同盘 rename 零拷贝；定位：Bagel（BAGEL-7B-MoT 统一模型）的训练/推理/评测全链路（权重 28G、hf_home 78G、LMUData 33G、conda env 5.1G 等重物随迁）。配套定案：① 子项目自成一个独立 git 仓库（迁入时新建，原目录系 rsync 落地无历史），主仓 .gitignore 将 `bagel/` 整体排除，子项目内部 .gitignore 只放行代码与配置、重物全部不入仓；② 全部硬编码路径 `/tank/bagel` 已批量改写为 `/tank/demiwtg/bagel`（101 文本文件 + conda env 的 75 个 shebang + pip/conda-meta 元数据，复查零残留），`Bagel/bagel` 自指符号链接改为相对链接，conda 环境迁移后验证通过（torch 2.6.0+cu124，CUDA 可用）；③ 与主仓的接口不变：评测题库读 `state/curation/eval_v1/`，评测 runner 为 `bagel/Bagel/scripts/run_wkbench.py`；④ `bagel/HANDOVER.md` 为子项目内部的下载/环境交接文档（子项目自治，不受主仓“文档只两份”约束）。
>
> **架构决策（2026-08-23）**：新开 `benchmark/` 顶层模块（用户指定，顶层目录禁令的登记例外），用于多模态世界知识+推理评测基准建设（目标：定位 Bagel 类统一模型的短板）。分工定案：出题 prompt（`state/curation/eval_v1/synthesize_prompt.md`）与合成脚本（`curation/eval_synthesize.py`，qwen3.7-plus API）归属 curation，题库与评测样本落 `state/curation/eval_v1/`（不入 git）；benchmark/ 只放审阅/评测入口。首批预合成 10 样本 × 3 任务（vlm/edit/t2i）= 30 题已产出，出题 prompt 核心机制：证据审计防 OCR 捷径、caption 防幻觉传染、probe_dims 短板探针维度 + expected_failure_modes 失败模式预测、JSON 机读输出。bagel 侧评测 runner 落在 bagel 子项目 `bagel/Bagel/scripts/run_wkbench.py`（单卡 accelerate-dispatch 加载 BAGEL-7B-MoT，三任务推理：vlm 走 think+understanding 出文本、edit 走官方 imgedit 口径 cfg 出图、t2i 沿用 generate_t2i.py 参数；按 qid 断点续跑、支持 --shard 分片与 LORA_PATH 注入，产出 responses_shard*.jsonl + imgs/）。

> **架构决策（2026-08-23）**：eval_v2 生成（T2I）+编辑双赛道评测定案，出题与判分代码归 benchmark/，运行时产物落 state/curation/eval_v2/（不入 git）。题目构成：生成赛道由本仓数据分层抽样驱动（新增 eval_sample.py——质量门 quality≥8.0 且 identity=true 读 metadata.jsonl 权威清单，口径修正：质量字段不在 images.jsonl；(L1,L2) 分支配额 ∝ sqrt 且最大余数法恰分 n；排除集剔 eval_v1+eval_v2 既有样本 sha 防背答案；每实例限张），首批实测 500 张（279 张 edit_ok），图片索引自 1099 续编兼容前序产物；编辑赛道按 ImgEdit-Bench 套系构成（9 类 edit_type 轮转配额 + 每第 5 题强制知识编辑套，改动方向须由图外知识唯一决定）。判分：生成赛道照 Qwen-Image-Bench 协议——双线判分（知识线 implicit_checks 权重加和、通用线按题面 facet_tags 激活 22 个裁剪 facet，各 0.5 合成，主体缺失/主题跑偏封顶 30），刻度 {0,1,2,NA} 经 φ 非线性映射 0/60/100 后自底向上聚合，facet 词表单一权威源在 eval_score.py FACETS；编辑赛道原文采用 ImgEdit 官方 prompts.json 九类三维 5 分制 rubric（benchmark/edit_score_prompts.json 作为评分契约代码入 git，按 edit_type 路由，二三维不得高于一维的硬约束在判分侧强制钳制）。配套：eval_synthesize.py 迁入 benchmark/ 并重构（--task all 维持 v1 语义不变，gen/edit 为 v2 专项批次）；出题 prompt v2 两册（synthesize_prompt_{gen,edit}.md）与 judge 模板物化（judge_prompts/）均不入 git；judge 用本地 Qwen3.8-27B vLLM（localhost:8000，thinking + 确定性解码，解析失败计 0 并入异常率），出题走 Galaxy API（qwen3.7-plus，需 GALAXY_API_KEY）。冒烟已过：edit 判分端到端（原图冒充产出正确判「无变化」给 1 分）、t2i 判分（gate 封顶语义正确、0 解析失败）、抽样分层配额与排除集生效。

> **架构决策（2026-08-22）**：下载侧恢复连接复用（显式推翻 2026-08-21 keepalive=0 定案，用户拍板）。新增下载专用客户端（infra.get_download_client，双池直连/代理，Limits 128/64），只挂 dl: 档流量，检索侧维持禁复用不动。三层防线：① 病根已除（stream yield-in-retry 已修，全链零任务取消源，结构性不再产生半读连接进池）；② op_download 每请求硬超时 90s（asyncio.wait_for），超时取消任务经 stream 的 finally 关响应，未读完的连接销毁不入池，永久阻塞降级成丢一张图；③ read=30s 读超时与 supervise 12 分钟自愈兜底不动。理由：下载打 CDN，每图一次全新 TCP+TLS 握手已成实测主瓶颈（py-spy 实锤堵在 do_handshake），实测 6.8 张/s 对闸门理论上限 105 张/s。回退条件：任何停摆/风控迹象 → DOWNLOAD_LIMITS 的 max_keepalive_connections 改回 0 一行回退，重启即可。
>
> **架构决策（2026-08-22）**：覆盖过滤后实例队列稳定分区：0 图实例排队首、有存量（1~N-1 张）的难啃实例沉底（实现在 chain.py 启动期，只改顺序不改集合）。理由：重试区实测撞车 89%、实例速率不到干净区一半，先吃干净区把进度跑出来，难啃实例最后兜底；用户曾疑「降 --skip-covered 阈值能缩小重试区」，实测分布推翻：重试区主体是 36,205 个 0 图新实例（阈值降到 1 也跳不掉），1~7 张存量实例仅 2,811 个，降阈值只会放弃这批已投过资的实例。

> **架构决策（2026-08-22）**：打标前撞车快查回归，推翻 2026-08-21「职责清晰优先于微优化」定稿。Sink 新增无锁只读快查 contains()，chain 的 annotate_worker 打标前先查 (sha, instance) 索引，命中即跳过打标。理由：--skip-covered 8 重试区实测撞车占下载量 89%，原「微优化」场景已变主导成本——不前置则 ~9 成 VLM 槽位烧在重复图上；咨询语义不变契约：索引漏查（跨进程新行）时照常打标，权威判定仍在 sink 锁内，最坏多打一次标永不双写。

> **架构决策（2026-08-21）**：存量迁移链开工：新增 op_backfill（补标算子，与全量打标同 prompt 同口径，只重打 kb_match 并补 identity/focus，richness/caption 沿用存量）与 migrate.py（记录驱动：images.jsonl 炸开为每实例×图一条记录 → 读 blob 复验 sha256 → 补标 → 补 queries/query_langs → 追加 metadata.jsonl）；原算子（op_annotate/op_sink/chain）零改动。配套定案：① 迁移后一图多行合法（去重键 (sha256, instance)，对 sink 的 sha 撞车跳过契约定向豁免，仅本入口）；② danbooru 处置：双源（danbooru/bulk_danbooru2023，共 16,102 行）整体从迁移链剔除、不写 metadata.jsonl，由用户另走开源数据集元数据链路单独处理（migrate.py EXCLUDE_SOURCES；含早前确定性认领写回的 7,324 行，认领成果随 images.jsonl 留存供其链路复用）；③ 迁移收官后 metadata.jsonl 升为唯一权威主清单、删 images.jsonl（届时同步改 2.2/2.4 与 viewer 读端）。

> **架构决策（2026-08-21）**：删除 curation/filter_vlm.py（VLM 图片质量过滤 run/report）。理由：质量/身份类打分字段（kb_match/richness/identity/caption/focus/quality）已由 collect_v2 的 op_annotate 随采集链路内联产出，独立质检流水线无运行进程；state/filter_vlm/ 目录不存在、无任何结果残留，删前核实全仓无引用。curation/ 脚本化流水线至此全部移除，只留数据分析 notebook。

> **架构决策（2026-08-21）**：`data/dataset/` 更名 `demiwtg` 并入 `data/datasets/`（同盘 rename 零拷贝）；`data/taxonomy/` 三件套（taxonomy.json/instances.json/alias_western.json）迁入 `data/datasets/demiwtg/meta/`，`data/taxonomy/` 目录撤销。理由：自建数据本身就是一个数据集，与开源数据集统一收编到 data/datasets/ 下，消除 data/dataset 与 data/datasets 双轨；标签体系三件套是该数据集的标注模式资产，与 images.jsonl 同居，meta/ 成为 demiwtg 的唯一真相区。配套变更：仓库根由 `--meta` 向上推导由三级改四级；gen_taxonomy_kb/gen_instance_kb 断点缓存改放 state/taxonomy/（运行时缓存不许进 meta/ 白名单，llm_common.JsonlCache 自动建目录）；三件套继续入 git（.gitignore 逐级例外），blobs 与 jsonl 大文件维持不入 git。

> **架构决策（2026-08-21）**：删除 curation/annotate_vlm.py（VLM 知识打标 run/stream/apply）、curation/emerge.py（taxonomy 涌现缺口分析）、curation/util.py（meta_lock 随唯一消费者一并失去意义），及四个一次性脚本 fix_abs_paths.py（绝对路径迁移，实测 images.jsonl 已 0 条残留）/ backfill_provenance.py（溯源回填，数据源 runs/ 批次产物已清）/ probe_identity.py 与 probe_retrieval.py（数据依赖 bulk posts_meta 与 v1 DLQ 均已删）；同期清理 state/annotate_vlm/、state/emerge/ 运行时目录。理由：打标职责已由 collect_v2 的 op_annotate 接管（打标随采集链路内联），独立打标流水线无运行进程、无消费者；emerge 依赖 annotate_vlm 打标产物且产物全部可从 images.jsonl 重算；删前逐一核实无残留 import。curation/ 此后只留 filter_vlm。
>
> **架构决策（2026-08-21）**：删除 collect v1 采集系统（整模块 40 文件）与 curation/retry_failed.py（v1 死信重试器，随 v1 失去意义）；采集职责由 collect_v2 接管。理由：v1 已被 v2 三层架构完全替代且无运行进程；残留耦合仅 curation 两处引用 v1 的 meta_lock 文件锁工具，已原样迁入 curation/util.py（annotate_vlm/backfill_provenance 改 import 该处）。同时清理 COS 迁移过程文件（remote_pull.sh、cos_pull/）：186/186 分片迁移已于 2026-08-21 收官并验证（blobs 496G/323164 文件，内容寻址抽检通过），过程脚本不再需要。
>
> **架构决策（2026-08-21）**：HF 数据集落盘区由 state/collect/datasets/ 迁至 data/datasets/（23 个数据集目录整体搬移，同盘 rename 零拷贝；下载过程脚本留 state/collect/datasets/ 只读归档）。理由：开源数据集是长期数据资产而非运行时状态，data/ 才是数据根（.gitignore 早有 data/datasets/ 预登记）；state/ 回归纯运行时语义。同期清理：coco2017 标注 zip 为 0 字节下载失败残留，已删。解压教训：多线程共享单个 ZipFile 并发解压会因共享文件指针竞态产生大量伪 CRC 错误（首轮误判 38% 文件损坏，串行 testzip 复验全部 zip 实际完好），必须每线程独立打开 ZipFile。
>
> **架构决策（2026-08-19）**：标签体系解耦——instances.json 升为独立权威源（实体资产，生灭与富知识不依赖树），taxonomy.json 降为展示视角（树 + 挂载引用）；废除 instances.taxonomy_paths 字段（schema 2.0，实例表 56,789 条一次性迁移零丢失）并删除 build_unified.py。理由：树决定实例生死的反向控制是唯一残留耦合，斩断后数据处理链路（采集/打标/涌现）全部只读实例表；树可自由重生成/多视角并存而不伤资产。需要挂载关系的消费者（collect gap 聚簇、gen_instance_kb 与 emerge 的 prompt 上下文）改由 taxonomy/mount_map.py 从树现算。
>
> **架构决策（2026-08-17）**：broader/ 模块（Open-BROADER 上下位关系模型）迁出本仓库，回归独立项目 `/root/data/projects/open_broader/`（代码、55G 训练语料、训练产物、历史日志整体搬移，脚本内绝对路径已批量改写至新家）。理由：上下位判断本质依赖世界知识，通用大模型（Qwen3.8-27B 批审计 + 现成 embedding 检索）已可覆盖 taxonomy 树审计场景，且训练语料正确性存疑、课题短期难推进，故冻结训练、语料与 checkpoint 原地归档。本决策推翻 2026-08-16 的并入决策；未来如复活，先做大模型 vs BROADER 的 head-to-head 评测再立项。

- 跨模块 import 一律 `from <包>.<文件> import ...`：`taxonomy/`、`curation/`、`viewer/`、`benchmark/` 位于仓库根（消费者先 `sys.path.insert(0, REPO_ROOT)`）；`data/` 为 collect_v2.* 兼容 shim（re-export 顶层模块），仅存量调用方使用，新代码直连顶层包。
- 路径常量一律从脚本自身向上推导到仓库根（顶层模块脚本推导两层），不依赖 cwd 之外的魔法。
- 新增脚本必须先归属到一个模块；归不进去的说明职责边界有问题。

## 4. 数据与代码的边界

- `datasets/`、`state/`、`logs/` 是本地数据/运行时产物，**不入 git**（.gitignore 强制；例外：datasets/demiwtg/meta 下 taxonomy 两件套）。
- 入库的只有：代码（taxonomy、curation、viewer、benchmark，含 viewer 页面 HTML）、约束文档（AGENTS.md、README.md）、以及 `datasets/demiwtg/meta/` 下的权威 JSON（taxonomy.json/concepts.json）。
- 大 JSON（images.jsonl、blobs）永远不进 git；需要备份走独立通道。
- 生成产物（`viewer/build/`）不入 git，数据改动后重跑 build_viewer.py。

## 5. 关键命令

```bash
# 标签体系富化（LLM 各一次调用；需 LLM_API_KEY 等环境变量；dry-run 零成本预览）
python3 taxonomy/gen_taxonomy_kb.py --only-empty --write       # 节点 KB（knowledge_intro 等 4 字段）
python3 taxonomy/gen_instance_kb.py --only-empty --write   # 概念富化（aliases→concepts.json；知识文本→docs 层草稿）

# 概念体系维护（2026-09-07 概念化迁移器；instances.json 已退役，勿恢复）
python3 curation/migrate_concepts.py --refresh-taxonomy    # 树变更后刷新 concepts.json 挂载快照

# viewer 产物重建（数据改动后）
python3 viewer/build_viewer.py

# 数据策展与检索接地（curation/，各脚本 --help；notebook 直接运行）

# 图片采集链（已独立为 demiwtg-data 仓库：flow.py + operators/ + smokes/，另含 source_health/source_plan 源策略）
# 见 https://github.com/vincent9299/demiwtg-data

# modelhub LLM 网关 + 静态代理（独立子项目；详见 modelhub/README.md）
bash modelhub/start.sh && bash modelhub/smoke.sh   # 启动+冒烟；停止: bash modelhub/stop.sh [--all]
```

## 6. 禁止事项速查

- ❌ 在 `meta/` 里建除 2.2 清单外的任何文件
- ❌ 手改 blobs/ 下的文件（包括"顺手修一下坏图"——正确做法是重新采集）
- ❌ 删除图片目录前不做 blobs 内容比对
- ❌ 新增只写不读的"审计/日志"文件
- ❌ 在 `taxonomy/`、`curation/`、`viewer/`、`benchmark/` 之外新增脚本（`bagel/`、`modelhub/` 子项目内部自治，不受此限）
- ❌ 往 datasets/ 里放代码、页面或生成产物（viewer 页面与产物在 viewer/ 内闭环）
- ❌ 恢复历史过程文档（docs/、子目录 README）
- ❌ 在数据/代码里使用 category、leaf、root 作为分类概念（instance/实体 一词亦已由 concept/概念 取代，2026-09-07）
- ❌ 在 concepts.json 里为同一 name 写多条记录（一个概念一条；多处挂载表现为多个树节点名单同名 + 行内 taxonomy 快照多路径）
- ❌ 手改 concepts.json 行内的 taxonomy 快照（挂载真相在树；树变更后跑 `curation/migrate_concepts.py --refresh-taxonomy` 刷新，不手工编辑）
- ❌ 恢复 instances.json 或 desc/query/source 行级字段（已退役：desc→docs 层草稿、query→采集运行时缓存、source→meta.source_stats；历史溯 git）
- ❌ 把 `datasets/`（demiwtg/meta 权威 JSON 例外）、`state/`、`logs/`、`data/`（collect_v2 退役残件）或 `modelhub/` 提交进主仓
- ❌ 把 `bagel/` 与 `benchmark/bagel/` 的重物（模型权重、评测数据、vendored 官方 git 仓）提交进主仓
- ❌ 把运行时状态塞进 data/（放顶层 state/ 对应模块子目录）

## 7. 网络与下载约定（2026-08-20 新增：环境里残留已宕机代理 100.89.199.67:7890，pip/curl 会被拖死，故将代理策略定死）

### 7.1 采集集群 SSH 互信互通（2026-09-08 建成；2026-09-17 重构：舰队重编号 + 全公网直连 + 废除 sg-master 跳板）

**架构决策（2026-09-17，用户拍板）**：湖机（本机）接管 master 指挥位；全部 25 台腾讯 SG 机改**公网 IP 直连**（经公司代理 CONNECT），不再经 sg-master 跳板；机器重新编号 r1~r20 / p1~p5，sg-master 降级为普通节点 **p5**（kb 审计嗅探收割 + raw/ 工具箱备份完成后可退役）。机器间 10.3.x.x 内网互通、22022-29 反向隧道、各机 cosfs **不受本配置影响**（原样保留）。CN 组 pipeline-e~i 已于 2026-09-10 释放。

**舰队 25 机**（别名=公网 IP + pconn.py 代理直连；同表在湖机 `~/.ssh/config` 头部）：

| 新名 | 旧名 | 公网 | 内网 | 密钥 |
|---|---|---|---|---|
| p1~p4 | pipeline-a~d | 43.160.215.28 / 43.160.238.29 / 43.160.201.131 / 43.160.240.239 | 10.3.4.14 / 10.3.4.16 / 10.3.0.17 / 10.3.8.9 | lighthouse_key |
| p5 | sg-master（VM-0-14） | 43.160.250.196 | 10.3.0.14 | cluster_key |
| r1~r20 | （09-16 起新机，编号不变） | 见 `~/.ssh/config` | 10.3.x.x | lighthouse_key |

```bash
ssh p1   # 任意别名直连，无跳板；湖机→集群唯一通路=公司代理 10.127.48.4:3128
ssh r7
```

**红线**：公司代理惩罚突发 CONNECT——避免同时对全舰队并发建连（巡检/发射错峰，sleep 间隔）；旧名（sg-master/pipeline-*）已从 ssh config 删除，历史文档中出现的旧名按上表换算。

**集群 → 湖机**（唯一反向隧道在 VM；湖机是无公网 k8s pod 只能反向打通）：

- 隧道（2026-09-14 传输线起扩为 8 条）：`/root/tunnel_keepalive.sh`（常驻保姆，30s 错峰重建 + 4h 超龄换血）——**p5(VM-0-14):22022-22029 → 湖机 sshd(127.0.0.1:2222)**，经 pconn.py 代理反向打通；传输线（882 万图 82% 暂停中）续传依赖它，**勿动**。旧单隧道脚本 `/root/.ssh/lake_tunnel.sh` 进程仍在但已被 keepalive 实质取代。
- **全部 7 机均已配 `Host lake` 别名**：VM 直连 `localhost:22022`；a/b/c/d/e/f 一律 `ProxyJump`（a–d 跳 ubuntu@43.160.250.196；e/f 跳各自 `Host sg`），密钥统一 lighthouse_key（pub 已入湖机 authorized_keys，标注 "demiwtg 采集集群"；a–d/f 的私钥已分发，e 原有）。

```bash
ssh lake             # 集群任一机上执行 → 登录湖机 root
rsync -e ssh data/ lake:/yzp/zhaozy/yangzepeng/0905/demiwtg/...   # 数据回湖
```

- **隧道重启**（湖机 pod 重启后须重拉）：`setsid nohup /root/.ssh/lake_tunnel.sh >> /root/.ssh/lake_tunnel.log 2>&1 &`
- **带宽**：SG 组→湖机流量经湖机↔VM 单隧道（跨境），实测 ~10 MB/s 下行 / ~19 MB/s 上行；CN 组→湖机同样经 VM 隧道绕行（CN→SG→湖机）。传清单/代码秒级，传图片级大件按此估算（百万图回湖约数天，建议分批/先 blob 后清单）。
- 坑位记录：pipeline 首连需 `StrictHostKeyChecking accept-new`；ssh 内 heredoc 会丢（复杂配置本地写好整文件 scp，demiwtg-data HANDOVER 坑 #8）；腾讯云 hostname 按内网尾号命名（pipeline-e 与 SG 的 c 撞名 VM-0-17-ubuntu，非路由错乱）；a/b/c/d/f 原不持 lighthouse_key 私钥（已分发）；ssh -o 选项经 shell 变量展开会打碎含空格的 ProxyCommand（隧道脚本用函数直写命令，勿用 $OPTS 变量拼）。
- 采集集群全量机器清单（七机二维架构/分片）见 demiwtg-data 仓 HANDOVER.md。

### 7.2 增量回湖管线 lake_sync（2026-09-08 起，仓库根常驻 daemon）

`lake_sync.py`（仓库根的常驻 daemon，每小时一轮）把集群侧新采集的图片与 docs 知识正文增量拉回湖：

- **模型**：清单增量镜像（每节点/文件记字节偏移，tail 只取完整行）→ 缺集现算（**湖侧实存 = 已同步**，无独立传输账本）→ tar 流拉取（按 sha 前缀 aa 分摊到组内多节点口）→ 逐文件校验后原子发布 → 源端回执清理（先写组桶 `meta/synced_shas.jsonl` 记账再删 COS，24h 宽限 + 每轮限量；**pages 暂不清理源端**）。
- **两类资产两套寻址**：blobs（图片，**内容寻址**，闸门 = sha256(内容)==文件名）；pages（docs 知识正文，**URL 寻址** `page_sha=sha256(url)`，闸门 = **版本化**——2026-09-09 起 docs 行带 `content_sha/page_bytes`（采集端 operators/page.py 记账），湖侧强复验内容哈希；旧行缺省走宽松门（sha256(url)==page_sha + 非空 + 文件名自洽）。同一 URL 重抓内容会变，强门只对新数据成立。）
- **状态**：`sync/`（state.json 偏移 / verified.jsonl / verified_pages.jsonl / deleted.jsonl / sync.log 轮摘要 jsonl / manifests/<node>/ 镜像清单）。
- **节点拓扑**（2026-09-17 重编号）：SG 组 p5（原 sg-master）+ p1~p4（原 pipeline-a~d，共享新加坡桶）；CN 组 pipeline-e~i 已于 2026-09-10 释放（湖侧运行副本 `demiwtg/lake_sync.py` 已摘除 cn 组；断点 state.json/镜像目录 manifests/ 已随重编号改名迁移）。**r1~r20 尚未入 NODE_GROUP**——SDC 投喂清单（各 r 机 `~/lake/meta/image-shard-extsdcfetch.jsonl`）待接入，接入时须把 r 机加进 NODE_GROUP（其 blob 在 SG 桶 datasets/demiwtg/blobs，与 kb/blobs 旧池不同树）。
- **首战成果**：pages 一轮补齐 10,049 页 / 224MB（bench283 知识页覆盖 **283/283**，平均 35.4 页/概念；authority=serp 9,132 / wiki 888）。
- **运维**：① 改脚本后须重启 daemon 才生效（Python 已载入的模块不会热更）；启动 `setsid nohup python3 lake_sync.py > sync_daemon.log 2>&1 &`；停 `pkill -f '[l]ake_sync.py'`（注意坑：pkill 模式串若原样出现在自己命令行里会自杀，用 `[l]` 括号法）。② 新节点重装导致主机密钥变更 → `ssh-keygen -R <ip>` 后 accept-new 重建（2026-09-08 pipeline-h/i 即此因被拒，已修）。③ 湖侧 `images.jsonl` 尚未合并镜像清单（新采集图在 `sync/manifests/` 可见，湖侧抽样/覆盖统计待合并后才反映）。
- **pages 清洗（2026-09-08）**：同步回的原始页是爬虫直出、约 80% 字节是站点外壳，故 `curation/clean_docs_pages.py` 出净版另存 `state/collect/docs_clean/`（原始 `datasets/demiwtg/pages/` 只读不动）。全量 10,020 页 → **净版 4,326 页（43%）**、150.9M 字压到 31.0M 字；分源可用率悬殊（wiki 96% / serp 34%）；拒页两大头 gibberish 2,280（反爬诱饵）+ short_raw 2,052（JS 壳站空壳）合占拒页 75%，均属采集端可避免的浪费。净版含 `concept_docs.jsonl`（{name, kind:"passages", body} 与湖侧 docs 层同构，bench283 覆盖 272/283，建议与现存 summary 摘要层**并存不覆盖**）。分析与呈现：`curation/docs_analysis.ipynb`（demiwtg kernel，含质量分级/原始 vs 净版样例/域名诊断/单概念钻取）。已记录的两类内容缺陷：SERP 概念误绑（如 Stargate SG-1 页被绑到「新加坡」）与近似重复页（en.m/en 双站同文，精确 sha 去重抓不到）。
- **已知缺陷与待办（2026-09-09 明细抽样发现，证据见 `curation/lake_sync_details.ipynb`）**：① **`needed_blobs` 只取 `blob_path`**，而集群两代清单路径键不同——`backfill-shard-*`（7 字段极简、无打标）用 `blob_path`，`image-shard-*`（24 字段、含 width/height/fetched_at）用 `path`，故后者被静默跳过（实测 image-shard 63,883 行中 99% 未回湖）。一行修法：`rel = r.get("blob_path") or r.get("path")`；概念键亦须兼容 `concepts`/`instances`（老 image-shard 用 instances）。② **VLM 打标字段全 null**：镜像里 quality/identity/kb_match/richness/caption 非空率 0.0（连 image-shard 行也是），故湖侧质量门对新采图完全失效（合格行 0）；需集群侧确认打标阶段是否落盘。③ `backfill-shard` 行无 `fetched_at`/`width`/`height`（溯源与尺寸门失效）。④ **概念误绑真实存在**：SERP/fandom 关键词撞车，探针「南丁格尔」14 行中 6 行是 Elder Scrolls 的 Nightingale Armor/Hall、鬼灭之刃与 Terra Battle 角色页。⑤ notebook 性能陷阱：PVC 上逐文件 `stat` 求体积会把整本拖到 ~9 分钟（实测 user CPU 仅 33s），改为「只列目录计数 + 用清单 size_bytes 估算」后全本 **63s**；同理逐行 `os.path.exists` 判回湖应改为一次列目录建 sha 集合。
- **2026-09-09 修正版部署**（repo demiwtg-data 为唯一真源，本文件副本由其覆盖）：① pages 缺集**跨组全局去重**（同 URL 两队都抓过只拉一份，修首轮 29 页双拉）；② blob/pages **两组并行拉取** + State 审计写锁 + tar 超时收紧 3600→900s + **逐批进度打印**（blob批/pages批 行，含耗时与 ok/bad/fail——此前 21h 无输出无诊断即此缺口）；③ 「镜像→真 meta」例行化 `merge_meta.py`（lake_sync 每轮末尾自动调用，已取代 merge_docs.py）：docs 全量重合并 → `meta/docs.jsonl` 10,023 行（283 概念，键 (page_sha, concepts)）；images **增量追加** → `meta/images.jsonl`（键 (sha256,instances)、偏移状态 sync/merge_state.json、不重写 285 万行大账；首轮 63,883 镜像行追加 50,882 新行，35 秒）。结构对齐：内容真源 {blobs,pages}/、清单 meta/（湖 meta/ 另有 concepts.json/taxonomy.json 既有真源）。backfill/dead 镜像不进账（复原操作/死信留档）。消费端只读 meta/，不碰 sync/manifests/。daemon pid 以 pgrep 为准，日志 sync_daemon.log（逐批）+ sync/sync.log（轮摘要）。

- 国内下载**不走代理**，优先找国内源（如 pypi 用 `pypi.tuna.tsinghua.edu.cn`；注意部分域名 DNS 只返回 IPv6 记录而本机无 IPv6，需确认 A 记录可达）。
- 确需访问外网（pypi.org、download.pytorch.org、GitHub 等）时才用代理：

```bash
export http_proxy=http://192.168.10.109:10808
export https_proxy=http://192.168.10.109:10808
# 或
export ALL_PROXY=socks5h://192.168.10.109:10808
export no_proxy="localhost,127.0.0.1,192.168.10.0/24,modelscope.cn,modelscope.org.cn,.modelscope.cn"
```

- 执行任何下载前，先 `env | grep -i proxy` 检查残留：发现已宕机的旧代理（100.89.199.67:7890）必须先 unset 或按上述配置覆盖。
- **外网链路直连优先**（2026-08-22 拍板）：外网源能直连通就直连，只有实测直连不通的才走代理，减少代理流量；代理源名单按实测增删（collect_v2 落点在 `infra._PROXY_SOURCES` 白名单制：2026-08-22 实测 mal/bing_images/yandex_images 直连可通走直连，wikimedia(_zh)/anilist/pixiv/deviantart 直连超时留代理池）。

> **图片全量守护补充（2026-09-10，用户授权选模型和失败拉起）**：本轮沿用已验证的本地 Qwen3.8-27B，候选模型未下载完整，不宣称横向实测胜出。`curation/run_image_pipeline.sh` / `curation/image_supervisor.py` 可接管并恢复当前本地 8000 服务及图片标注进程；该明确授权覆盖此前“不启动/停止用户模型服务”的限制，仅限本任务精确匹配的服务。状态、日志、断点仍在 `state/curation/image_preannotation_v1/`，详见 DESIGN 第 11 节。新增此条是记录本次运行管理授权，不扩展到付费接口或其他任务服务。

> **GPU 让位补充（2026-09-10，用户明确授权）**：图片预标注是利用空闲 GPU 的后台材料整理任务。当前研究实验需要资源时，助手可自行暂停该标注及其本地 Qwen 服务，保留断点和结果，实验结束后恢复原服务与标注；不再为同一让位操作重复询问。具体编排见 `curation/DESIGN.md` 第 12 节与 `curation/rag_diagnostic_session.py`。不授权删除标注、不混入其他模型、不影响其他无关任务、不使用付费接口。

### Notebook直接编排Dataset（2026-09-15，用户明确调整）

现役交互入口仍为curation/v4/knowledge_debug.ipynb，但不再使用StreamFlow对象隐藏业务编排。原始来源读取、概念选择、资料关联、计数、分批及知识算子直接通过demiflow Dataset操作连接，业务算子在dataset_operators.py与knowledge_stages.py；notebook_io.py只提供文件读取/版本冻结。StreamFlow保留历史命令行兼容，不作为notebook主线。此条覆盖前述StreamFlow为现役交互编排对象的说明，详见DESIGN第36节；业务数据契约、原始datasets只读和历史运行不可覆盖约定不变。

### 知识pipeline统一原生算子接口（2026-09-15，用户要求）

用户要求迁移模型调用，并将包括读数据在内的通用能力尽量下沉到demiflow。现役knowledge_debug.ipynb直接使用read_datasource(ReadSource)、Dataset.union、join、reduce_by_key、group_batches、map_cached、map_prompt_async、checkpoint和read_json。ReadSource实现原生Datasource/ReadTask，通用文件解码/gzip/JSON数组流式读取在demiflow，采集schema解释/来源范围审计在curation。知识actor仅做准备、业务校验和构造候选；逐行缓存与阶段落盘均由demiflow执行，不再在新知识链调用旧Stage缓存或LocalModel。提示词、完整请求响应、持久预算、不确定调用阻塞和版本冻结继续保留。当前默认新run为knowledge_native_prompt_v5，状态和限制见DESIGN第38节；兼容CLI不代表新主线，原始数据、旧run、评分不改写。

### Source直接使用原生读取（2026-09-15，用户纠正）

现役notebook不再使用ReadSource包装：具体文件直接data.read_records → checkpoint保留原始解码行 → filter/map转换业务字段。ConceptFromRecord、DocumentFromRecord、ImageFromRecord不读文件；通用扫描状态、坏行、gzip、JSON解析在demiflow。ReadSource仅供历史兼容。此条覆盖前述现役read_datasource(ReadSource)入口，文件快照冻结、原始材料只读、知识审核及版本边界不变，详见DESIGN第40节。

### 知识忠实性审核四模型对照（2026-09-15，用户明确授权）

理由：用户要求试用本地四个模型，覆盖此前知识整理只准调用Qwen3.8的模型范围限制，限定为本次小批对照。候选为Qwen3.8-27B、Qwen3.6-35B-A3B、gemma-4-26B-A4B-it、gemma-4-31B-it；通过prompt_config.py中显式local_model_comparison选项，仍只允许本机8000/8001直连，禁止付费网关。该选项不改变公共预标注的原Qwen协议与默认模型。

比较仍使用demiflow PrepareFidelity → map_prompt_async → ApplyFidelity，不重跑提取或开始出题；已有错误回归与基于未参与调参原始文章的受控对照分别记录。每模型两次调用，输入、提示词、参数和全部响应先冻结后比较，不按结果调参重试。按已有GPU让位授权等待标注落盘、暂停其服务、顺序加载模型，结束后恢复原Qwen命令与标注断点。细节与实测结果写入DESIGN第46节及state/curation/v4/fidelity_four_models_v1，不把模型同意或格式合格视为人工事实核验。


### 工作区保全与 collect 合并（2026-09-18，用户明确要求）

理由：用户要求尽量保留未入库代码，并将 demiwtg-data、kb_audit 统一纳入主仓 collect，旧 data/collect_v2 归档。此条覆盖上文关于采集独立仓库、禁止历史文档归档及新增顶层模块的旧限制。

- `collect/` 是原 demiwtg-data 的现役采集模块，`collect/kb_audit/` 保存原工作区审计工具，`collect/archive/collect_v2/` 保存旧兼容层。其源码、配置模板、交接文档纳入主仓；凭据、环境、数据与运行状态不入库。
- 工作区原 `demiwtg-data`、`kb_audit` 及本仓 `data/collect_v2` 保留相对符号链接，以兼容运行中的脚本；后者只是兼容入口，不放新代码。远端集群路径和 COS 对象键不因本地整合改名。
- `archive/workspace_20260918/` 与 `archive/ignored_sources_20260918/` 保存 state、_staging 和工作区根目录遗漏的源码、文档、配置及有限的小型结果，按原路径保留来源。MANIFEST.json 记录纳入／排除及内容哈希；这是历史快照，不作为新业务入口。notebook 快照去执行输出与内嵌附件；原始文件保留不变。
- `tools/` 是仓库保全工具；恢复或备份相关文档允许保留。原始 datasets/state、模型、Python 环境和依赖缓存继续不入 Git；归档源代码不等于备份完整实验数据。
- collect 不保留嵌套 .git；原采集仓库 Git 历史保存在主仓 archive/demiwtg-data-20260918 分支和本地 backup_audit Git bundle。


### 移除采集旧入口（2026-09-18，用户后续要求）

工作区 `demiwtg-data` 兼容链接已移除，唯一现役目录为 `demiwtg/collect`。现役本地脚本的绝对路径已同步，不再重建旧入口；覆盖上一节对此链接的保留约定。Git 历史归档、历史源码快照及远端 COS/集群路径保留，不因本地入口更名改写。
