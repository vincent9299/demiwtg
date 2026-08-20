# 交接文档：collect_v2 采集系统从零重写（进行中）

日期：2026-08-21（更新）｜ 状态：**六模块全部完成**（op_seed/getsource/infra/op_search/op_download/op_annotate/op_sink 均冒烟通过，annotate 真实 VLM、op_seed 真实 LLM 判定、sink 跨进程并发验证过）；**下一步 chain.py（最后串，零业务逻辑），先提契约待拍板；待办清单见 §6.1**
旧代码参考仓：`_reference/old_repo`（GitHub 浅克隆，已 gitignore；只可参考纯业务逻辑如源接口细节，V2 契约优先，不得照搬旧架构）。
本文档**取代** `.qoder/handoff_rewrite_collect.md`（那份的"旧代码冻结""booru 一期四源"等口径已全部作废）。
病根证据链仍有效，参考 `.qoder/handoff_probe_retrieval.md`（注意其中 booru 相关内容仅作历史教训，booru 已彻底出局）。

## 0. 工作区现状（重要，先读这条）

- **旧代码已全删**（用户在另一窗口手动执行，git 状态为 deleted 未提交）：`collect/`、`curation/`、`AGENTS.md` 全部不存在。
- 还活着的关键数据：
  - `data/dataset/meta/images.jsonl` —— 31.3 万张图的唯一真相主清单；
  - `data/dataset/blobs/` —— 内容寻址图片字节区（只增不删不改）；
  - `data/taxonomy/instances.json` —— 实例花名册（约 58k 条，采集全程只读；**其 query 字段混有类属词，新系统禁止信任**）；
  - `state/` —— 运行时状态（整体 gitignore），含 annotate_vlm 队列、DLQ 等旧物。
- `.qoder/` 下三份历史交接文档可读，但本文档优先级最高。
- 数据契约文档（原 AGENTS.md）已删，**契约本身仍然有效**，摘要见 §5。

## 1. 用户定的开发纪律（最高优先级）

1. **一步一步写，用户没讲过的不实现**，一步一确认，不许擅自加功能；
2. **不选词**：检索词直接用 instance 名（语言投影 A/B 未拍板，见 §4）；类目词零容忍、兜底词零容忍、旧 query 字段零信任；
3. **宁缺毋滥**：配额吃不满就认缺，任何环节不存在回落、放宽、扩召回路径；
4. **booru 系是开源数据集，彻底排除**，禁止在任何设计/举例中出现（danbooru/safebooru/yandere/konachan/gelbooru 全部）；
5. 开源数据集统一不走检索模式，单独维护 pipeline（本期不写数据集代码）。

## 2. 用户定案的需求（原文要点）

### 2.1 流式算子链（不严格分阶段 1/2）
六个模块，**一个算子一个文件**（2026-08-20 用户拍板 search 前加种子生成，链路变为）：

```
collect_v2/                    # 仓库顶层新目录（旧 collect/ 已删，无冲突）
├── infra.py        # 基础设施层：并行控制、限速、重试（独立于业务算子）
├── op_seed.py      # 种子算子：中文实例 → seed（语言投影，LLM 判定 + 词表缓存）
├── getsource.py    # 域路由算子：seed → (seed, 源) 配对，配置表驱动
├── op_search.py    # 检索算子：输入 seed → 输出候选元数据；adapter 在此文件内
├── op_download.py  # 下载算子：输入候选元数据 → 输出图片 + 元数据
├── op_annotate.py  # 标注算子
├── op_sink.py      # sink 算子：寻址幂等；数据处理/merge 唯一显式落点
└── chain.py        # 只做算子衔接（队列/流转）——零业务逻辑，硬约束
```

链路顺序：**op_seed → getsource → op_search → op_download → op_annotate → op_sink**。

- 检索算子可以有适配（每源一个 adapter）；
- **数据处理、merge 逻辑只在 sink/链层显性做**，adapter 只产结构化候选，不碰主清单；
- **chain.py 不允许写任何业务逻辑**（用户逐字强调）。

### 2.2 数据源范围
- **开源数据集全排除**（不进检索管道）：bulk_danbooru2023、coco、hf_dataset、**booru 系**；
- **旧版剩下的检索模式源全部支持**：wikimedia、wikimedia_zh、inaturalist、fandom、baidu、huaban_api、bing、toutiao、so360。

### 2.3 基础设施层（用户原话）
并行控制、限速、重试。限速已定：**按源适配，尽量快，但要避免被封**（每源一个速率配置，具体数值实现时按源特性定，如反爬源保守、官方 API 按文档限制）。

## 3. 讨论中用户认可的设计结论

### 3.1 检索算子契约（关键）
- 输出**有界有序候选列表**（top-K，按源原生相关度排序），**不是单条 top-1**；
- 链层消费（§4 拍板更新）：top-N（N 可配置）候选**全部**逐条过「结构过滤 → 真值校验」再进下载，**不是首个幸存者即停**；重复由 sink 幂等去重。列表有序性仍保证幸存者中首条即语义 top-1；
- K 封顶不分页深翻：结构化源（inaturalist）K 可到 10-20；语义检索源（wikimedia/搜索爬虫）K ≤ 5；
- 列表耗尽 = 认缺，绝不放宽条件凑数。

### 3.1.1 数据算子流（2026-08-21 用户拍板）
- 算子链是**数据算子流**（类似 Ray Dataset）：全链路流转统一的 `Item` 记录
  （定义在 op_search.py），各算子在同一 Item 上只追加自己的产出字段，
  不改写上游字段；不设独立的 Candidate/DownloadResult 类型；
- 字段分层：种子（instance/query，instance 即种子实例名，解决了旧 Candidate
  缺实例名的契约缺口）→ 检索产出（content_url/landing_url/declared_*/license/
  author/native）→ 下载产出（data/sha256/ext/actual_*/size_bytes）→
  标注产出（kb_match/richness/caption/identity，失败则全为 None）。

### 3.1.2 inaturalist 真值校验（2026-08-21 用户拍板）
**本期不做**（"必须时再加，控制复杂度"）；§3.4 的 taxon 逐条校验挂起，
inaturalist adapter 开工时再重新拍板位置（当时候选：检索出口）。

### 3.2 过滤口径（2026-08-21 用户拍板更新，覆盖原"过滤三处"定案）
- **下载算子不做任何过滤**：结构过滤（host 白名单/https/声明尺寸粗筛）与分辨率门全部移除；
  理由：优先保障候选列表的语义质量排序，过滤会把对的候选误杀；
- 解码（Pillow 完整解码）仅保留为提取实测元数据（宽高/mime/ext）的手段，
  拒收仅限"不是图"（解码失败），这是正确性验证不是质量筛选；
- **不设分辨率门**（用户拍板：先移除，不要；后续若需要另行讨论位置）；
- 域路由不变：仍在种子流入口侧（op_search 之外），防错配不杀候选；
- **禁令不变**：下载前语义过滤（模型判相关性）——旧系统已证伪。

### 3.3 压缩
**不做**。实测二次压缩收益趋零，且违反 blobs 原始字节不可变契约。任何算子都不放。

### 3.4 真值校验 → 见 §3.1.2（本期不做）

### 3.5 sink 契约（2026-08-20 拍板定稿，实现于 op_sink.py）
- sha256 内容寻址：`data/dataset/blobs/<aa>/<sha256>.<ext>`，临时文件同目录 + os.replace 原子替换；
- **sha 撞车直接跳过**（拍板，覆盖原「并入 instances」口径）：不写 blob、不追加行、不合并；
- **无标注照写**：标注四字段键存在值 null（区分「未标注」与「打分 0」）；
- **多 worker 并发写入保护**（拍板）：fcntl 跨进程锁（.meta.lock，§5 白名单）+ asyncio 进程内锁；
- **字段集最小兼容集**（拍板）：实测值写 width/height，声明值写 orig_width/orig_height，
  含 content_url/landing_url/fetched_at（float 时间戳）/path/instances/queries；
  V2 无信息源的旧概念字段不写（tiers/source_rank/source_score/source_kind/
  source_authorized/credit/query_langs）；存量 31 万行实测键集已逐一比对；
- **落盘方式为追加**（非旧系统全量重写）：旧系统每次 flush 全量重写 443MB 不可持续；
- 去重索引：各 worker 进程内存 sha 集（load_index 全量扫清单构建，快路径）+
  锁内吸收式增量尾扫（_absorb_tail）做跨进程权威判定；
  竞态教训：曾「miss 也推进偏移但不吸收区间内其他 sha」致共享图双写，已修（探针实证）；
- **queries[实例]=真实检索词必须透传，禁止回落成实例名**（旧系统溯源失真第一现场，缺陷 3 的直接修复点；Item 自带真实 query 字段，结构上无法回落）。

### 3.6 旧系统五大缺陷对照（新系统如何根治）
| 旧缺陷 | 新系统对策 |
|---|---|
| 短词优先排序 | 不选词，无词池无排序（§1.2） |
| 早停 | 逐词/逐候选试完，无早停 |
| query 回落造假 | sink 透传真实检索词（§3.5） |
| 域路由缺失 | 过滤三处之第一处（§3.2） |
| 真值在场不用 | inaturalist taxon 出口逐条校验（§3.4） |

## 4. 拍板记录（2026-08-20 新窗口开工前确认完毕）

1. **语言投影** → **A 方向**：aliases 只做同实体名的语言形态投影（中文→英文/拉丁），取不到就跳过该英文源。已澄清 wikimedia(en)/inaturalist 是检索源（官方 API），不是开源数据集。存量 aliases 覆盖 90.5%（西文 89.7%）但混有类目泛词，**拍板：先清洗再启用**——LLM 逐条判"是否同实体别名"，清洗结果落盘后英文源才启用；清洗前英文/拉丁源挂起，本期先跑中文源；query 字段零信任不变；
2. **标注算子消费方** → **端到端**：op_annotate 含 VLM 消费方，且**位于 sink 之前**，链路顺序为 search → download → annotate → sink；
3. **驱动方式与配额 N** → **无状态全量种子流**：输入为种子 instance/别名（可提前做域路由过滤），N 可配置（如 top3），**top-N 候选全部过管道**（不是首个幸存者即停），重复靠 sink 幂等去重；不做缺口驱动的存量检查；
4. **AGENTS.md 重写** → 未问，不阻塞 infra.py，后续再谈；
5. **重试细节** → **分类重试**：确定性失败（403/404 等）不重试、直接认缺；瞬态失败（超时/连接重置/429/5xx）有界重试；**不做指数退避**（固定次数 + 固定间隔）；
6. **验收手段** → 未问，不阻塞 infra.py，后续再谈。

### 4.1 op_search 追加拍板（2026-08-20）

- **范围**：adapter 框架 + 代表源先行（一个 API 源 + 一个爬虫源跑通，其余逐个补），不一次写九源；
- **域路由**：在 op_search 之外完成实例×源匹配，op_search 只收路由后的 (种子, 源) 对；（2026-08-20 更新：域路由实体化为 getsource 算子，见 §4.3）
- **别名清洗工具**：原登记为独立脚本不进算子链；**已作废**，2026-08-20 用户拍板实体化为 op_seed 算子（流式 LLM 判定 + 词表缓存，见 §4.3）。

### 4.2 op_sink 追加拍板（2026-08-20）

- **sha 撞车**：直接跳过（不合并 instances，不追加行）——用户四选一拍板；
- **无标注落盘**：写 null（键存在值 null），不是不写字段也不弃图；
- **并发保护**：用户明确「肯定是多 worker 并发的，需要考虑写入保护」→ fcntl 跨进程锁 + asyncio 进程内锁；
- **字段集**：最小兼容集（存量读端已识别字段 + identity；旧概念字段不写）。

### 4.3 op_seed + getsource 追加拍板（2026-08-20，用户发起：「再 search 前面再加一个算子根据中文生成 seed」）

- **算子切分**：op_seed 只管语言投影（中文实例 → seed 形态）；域路由单独成算子 getsource（用户提议）；
- **seed 形态**：每实例产中文本体 seed（必有，query=实例名）+ 西文投影 seed（最多一条）；
  **中文别名变体不产 seed**（守住不选词纪律，用户拍板推荐项）；
- **西文投影来源**：流式让 LLM（本地 qwen 端点）判存量 aliases 的西文候选是否同实体西文名（用户原话），
  不直接消费脏 aliases（旧拍板 query 零信任）；防幻觉：选中项必须是送判候选之一；
- **LLM 判定结果落盘词表 + 增量补判**（用户拍板）：data/taxonomy/alias_western.json，
  判过的查表零 LLM；判定失败不落表下次重判（宁缺毋滥）；
- **getsource 配置表驱动**（用户拍板），本期最小路由表：zh → wikimedia_zh + baidu（已实现），
  latin → wikimedia（adapter 待建）；inaturalist/fandom 无路由依据挂起；
- **Seed/Item 加 lang 字段**（zh/latin），sink 补写 query_langs={实例:lang}（用户拍板，存量本有此字段）。

## 5. 数据契约（原 AGENTS.md 已删，此处摘要即权威）

- blobs 内容寻址、只增不删不改；文件名哈希必须是内容 sha256；
- `data/dataset/meta/` 白名单：只许 `images.jsonl` + `.meta.lock`；不建派生索引、不建审计日志、不存放运行时状态；
- images.jsonl 是唯一真相，一张图一行按 sha256 去重；instances 字段只存实例名（V2 落盘口径见 §3.5：追加写 + 撞车跳过，旧「upsert 合并」已废）；
- 运行时状态进顶层 `state/`（gitignore），不进 meta/、不进 data/dataset/、不进 git；
- 仓库根由 `--meta`（默认 `data/dataset/meta`）向上三级推导；跨模块 import 用 `from <模块>.<文件> import`；
- 代码只进仓库根模块目录（collect_v2/ 为本次新增）；
- 现有 images.jsonl 记录字段参考（新记录必须逐字段兼容）：sha256/ext/source/source_kind/source_authorized/license/author/credit/width/height/orig_width/orig_height/size_bytes/mime/instances/queries/query_langs/asset_ids/landing_url/content_url/fetched_at/path（+ 标注字段 kb_match/richness/caption）。

## 6. 下一步（开发顺序）

1. ~~**第一步：`collect_v2/infra.py`**~~ **已完成**（asyncio + httpx；并行/限速/分类重试均按 §4 拍板实现，另新增 `stream()` 流式原语供下载用；冒烟 `python3 -m collect_v2.smoke_infra` 7 项全过）；
   - ~~**第二步：`collect_v2/op_search.py` 框架 + 代表源**~~ **已完成**（wikimedia_zh 打 commons.wikimedia.org；baidu 用旧系统经验：www.baidu.com 预热拿 cookie、middleURL 优先不用加密 objURL、尺寸取 URL 查询串；冒烟 `python3 -m collect_v2.smoke_search` 6 项全过，实网两源实测有召回）；
   - ~~**第三步：`collect_v2/op_download.py`**~~ **已完成**（无过滤、20MB 流式封顶、解码提元数据、按源下载头；冒烟 `python3 -m collect_v2.smoke_download` 7 项全过；实网复验通过：wikimedia_zh rank0 原图 2288x1712 jpeg、baidu rank0 500x667 webp，query 透传正确）；
   - ~~**第四步：`collect_v2/op_annotate.py`**~~ **已完成**（同口径 prompt + 新增 identity 字段；VLM 失败无标注放行；只打分不把关；冒烟 `python3 -m collect_v2.smoke_annotate` 6 项全过；真实 VLM 验证：慕田峪长城实图 kb_match=9/identity=True/caption 正常）；同步完成全链路 Item 化（op_search/op_download/两个冒烟）；
   - ~~**第五步：`collect_v2/op_sink.py`**~~ **已完成**（撞车跳过/写 null/fcntl+asyncio 双锁/最小兼容字段集/追加写/吸收式增量尾扫；冒烟 `python3 -m collect_v2.smoke_sink` 8 项全过含跨进程并发，30 轮压测无随机失败；真实链路端到端验证：search→download→真实 VLM→sink 全通；Item 新增 local_path/fetched_at 落盘产出字段）；
   - ~~**第六步：`collect_v2/op_seed.py` + `collect_v2/getsource.py`**~~ **已完成**（用户发起：search 前加种子生成；语言投影流式 LLM 判定 + 词表落盘增量补判；中文别名变体不产 seed；域路由配置表驱动最小路由表；Seed/Item 加 lang，sink 补写 query_langs；冒烟 `python3 -m collect_v2.smoke_seed` 7 项全过；真实 LLM 判定验证：慕田峪长城/跳绳/大熊猫选名正确、笛子认缺正确）；
2. 之后逐个算子写，**每个算子开工前先跟用户确认契约**（用户纪律：没讲过的不实现）；
3. 顺序（已按拍板调整）：infra → op_search → op_download → op_annotate → op_sink → **op_seed + getsource（2026-08-20 补在 search 前）** → chain（最后串，零业务逻辑）；
4. 每步写完跑最小冒烟再进下一步。

### 6.1 开工指引（2026-08-21 更新）

**阻塞项（等用户）**：无。

**当前步骤：提交 chain.py 契约提案待拍板**（算子衔接/队列流转：种子流驱动、并发度、失败统计、认缺语义——注意 chain 零业务逻辑硬约束，数据处理都在算子内）。

**待办队列（按序）**：
1. chain.py（契约确认→实现→冒烟；链路含 op_seed/getsource，词表首批 58k 实例 LLM 判定的跑法在 chain 契约里定）；
2. 其余 7 个源 adapter：wikimedia、inaturalist、fandom、huaban_api、bing、toutiao、so360（逐个补，每源开工前对契约；旧代码同目录可参考接口细节；wikimedia(en) 的拉丁 seed 路由已就绪只欠 adapter）；
3. §4 遗留两项：AGENTS.md 是否重写精简版、验收手段（旧探针已删）。
（原待办 3「别名清洗工具」已由 op_seed 算子实现，见 §4.3，移出队列。）

**已就绪可复验**：`python3 -m collect_v2.smoke_infra`（7 项）、`python3 -m collect_v2.smoke_search`（6 项）、`python3 -m collect_v2.smoke_download`（7 项）、`python3 -m collect_v2.smoke_annotate`（6 项）、`python3 -m collect_v2.smoke_sink`（8 项含跨进程并发）、`python3 -m collect_v2.smoke_seed`（7 项含 getsource 路由）；实网已验 wikimedia_zh/baidu 检索→下载全链路、search→download→真实 VLM→sink 端到端、op_seed 真实 LLM 判定（注：本机外网 DNS 偶发瞬时故障，遇到先 curl 对照确认再怀疑代码）。

## 7. 易误解点提醒

- **"开源数据集"的范围比直觉大**：booru 系也被用户定性为数据集，不在检索管道内，别拿它举例；
- **过滤只剩域路由**：下载算子不做任何过滤（§3.2 更新），别再把分辨率门/host 白名单加回去；
- **wikimedia 下载必须用带真实联系方式的 bot UA**：占位邮箱（example.com）会被 robot policy 在下载层 403，检索 API 层却放行，容易误判；已定案用仓库首页 `https://github.com/vincent9299/demiwtg` 作联系方式（实网 200）；
- **"不选词"不等于不做语言处理**：语言投影（A 方案）若获批，是"同实体名字的形态转换"，不是词池选择——两者界限要在实现时守住；
- **中文别名变体不是 seed**：「慕田峪/慕田峪关」这类变体不产 seed（不选词纪律），只有实例名本体 + 最多一条 LLM 判定合格的西文投影；别把 aliases 直接当检索词用（query 零信任）；
- **alias_western.json 在 data/taxonomy/ 下**：是 op_seed 专属持久化产物，不受 meta/ 白名单管辖，也不是「派生索引」（不进 data/dataset/meta/）；值为 null 表示判过无合格投影（认缺），键不存在才是未判过；
- **sha 撞车是「直接跳过」不是「合并 instances」**：§3.5 已按 2026-08-20 拍板定稿，旧口径（并入实例名）作废；后命中实例的关联就是丢，用户接受；
- **sink 的跨进程去重是「内存快路径 + 锁内吸收式尾扫」两层**：改 op_sink 时别破坏吸收式推进语义（miss 也推进偏移但不吸收区间内其他 sha 会导致撞车双写，已有探针实证）；
- **chain.py 零业务逻辑是逐字强调的硬约束**，数据处理只能在算子文件里；
- 旧交接文档 handoff_rewrite_collect.md 的"一期三源/booru 二期/旧代码冻结"表述全部作废，以本文为准；
- git 工作区有大量 deleted 未提交（旧代码删除），新窗口不要误 `git checkout` 恢复；
- `_reference/old_repo` 是旧代码 GitHub 浅克隆（已 gitignore）：**只参考纯业务逻辑**（源接口端点、反爬技巧、字段取舍），不得照搬旧架构，与 V2 契约冲突时以 V2 为准；长期保留不删。
