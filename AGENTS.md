# AGENTS.md · 项目架构原则与数据约束

本文档是**定死的架构约束**。任何代码修改、脚本新增、数据整理，都必须遵守。修改本文件本身就是一次架构决策，需要显式说明理由。

## 1. 项目分区

```
demiwtg/
├── taxonomy/                   # 【代码】体系维护与富化（mount_map 挂载聚合 / gen_taxonomy_kb / gen_instance_kb / audit_nodes）
├── collect/                    # 【代码】图片采集系统（cli/config/downloader/pipeline/queue/sources…）
├── curation/                   # 【代码】数据策展（retry_failed / filter_vlm）
├── viewer/                     # 【代码】查看器闭环：tag_tree_explorer.html + build_viewer.py + build/ 产物（gitignore）
├── data/                       # 【纯数据】统一数据根目录
│   ├── taxonomy/               #   标签体系数据（两个独立权威源，互不推导）
│   │   ├── taxonomy.json       #     树（展示视角）
│   │   └── instances.json      #     实例（实体资产库，独立于树）
│   └── dataset/                #   图片数据湖（不入 git）
├── state/                      # 运行时状态，按模块归属分子目录（不入 git）
│   ├── collect/                #   死信队列 / source_health.json / runs/<run_id>（含 _latest 软链）
│   ├── dataset_index/          #   COCO 标注缓存
│   ├── filter_vlm/             #   VLM 过滤结果
│   └── .lancedb/               #   Lance 查询索引
├── logs/                       # 运行日志（不入 git）
├── AGENTS.md                   # 唯一权威约束/说明文档
└── README.md                   # 极简指针，只指向本文档
```

- `data/` 下**只是数据存储**：任何代码、页面、生成产物都不许放进去。
- 代码只允许放在仓库根的 `taxonomy/`、`collect/`、`curation/`、`viewer/` 四个模块之一。
- 仓库顶层禁止新增散落的脚本或数据目录（`data/`、`state/`、`logs/` 是明确登记过的例外）。
- 文档只有两份：`AGENTS.md`（约束）与 `README.md`（指针）。历史过程文档（docs/、子目录 README）已删除，**不再恢复**——过程记录看 git 历史。

## 1.5 标签体系数据契约（两个独立概念，定死）

整个标签体系**只存在两个概念**，代码、数据字段、文档一律使用这两个词，禁止再引入其他分类术语（category、leaf、root 已废除）。数据模型以本节为准（原 schema/tag_taxonomy.schema.json 已删除：无校验消费者、与 instances.json 顶层结构不符，勿恢复）。

**两者彻底解耦（架构决策 2026-08-19）**：实例是资产，taxonomy 是视角。实例的生灭与富知识完全不依赖树；树只是展示/导航视图，同一套实例未来可被多套树视角引用。关联关系（谁挂在哪）**不写进任何一方文件**，需要时由消费者从树的 instances 名单现场聚合（`taxonomy/mount_map.py`）。原 build_unified.py（树推导实例表的重建器）已删除：它维护的正是被废除的耦合。

### taxonomy.json —— 树（展示视角）

```
{ "schema_version": "...", "meta": {...}, "tree": <node> }

node = {
  name: str                    # 节点显示名
  path: str                    # 完整路径，' / ' 分隔，从根『融合世界标签体系』起算
  depth: int                   # 根为 0
  children?: [node]            # 子树；末端节点省略
  instances?: [str]            # 挂在本节点下的实例名列表（对 instances.json 的引用，挂载关系的唯一落点）
  knowledge_intro?/aliases?/representative_cases?/related_tags?: [KB 字段，可选；knowledge_intro 为 150-350 字维基百科词条风格]
}
```

### instances.json —— 实例（独立权威源，实体资产库）

```
{ "schema_version": "...", "meta": {...}, "instances": [instance] }

instance = {
  name: str                    # 实例名（全局唯一主键：一个实体只允许一条记录）
  source: "curated" | "llm" | "derived"   # curated=人工精写；llm=LLM 生成；derived=未富化占位（templated 为历史值，不再新写）
  desc?: str                   # 详细介绍（唯一富描述字段；150-350 字，维基百科词条风格：具体知识点，拒绝空话套话）
  aliases?: [str]              # 别名/英文名
  query?: [str]                # 检索扩展词（LLM 生成，含英文/简称）
}
```

- **实例独立于树**：未挂载任何树节点的实例是合法状态（待认领池）；增删树节点不造成实例的创建或删除，富知识（desc/query/aliases）只存在 instances.json。
- **`name` 全局唯一是硬约束**：同一实体的知识字段只维护一份；多处挂载表现为多个树节点的 instances 名单同时含该名字（原 taxonomy_paths 字段已废除：它是树的影子，不进实例表）。
- 没有 `type` 字段：有没有子树看 `children`，挂不挂实例看 `instances`。
- 图片打标只存**实例名**（实体标签，不含路径）——体系演化（改路径/重生成树）不需要迁移图数据。看图入口（viewer 的 build/imgs.js）由 meta/images.jsonl 的 instances 字段现场聚合、相对路径指到 blobs 原图（相对 viewer/ 的 ../data/dataset/blobs/...），不再建软链树。
- 数据字段定义即契约，改字段 = 改本节 + 同步全部消费代码。

## 2. data/dataset/ 硬约束（定死，逐条执行）

### 2.1 blobs/ —— 原始字节区（不可变）

```
data/dataset/blobs/<aa>/<sha256>.<ext>   # aa = sha256 前两位；sha256 = 文件内容哈希
```

- 图片**只增不删、不重命名、不改动**。
- 新增图片必须：先算内容 sha256，再按 `blobs/<aa>/<sha256>.<ext>` 落盘；已存在同名文件则直接跳过（内容寻址天然去重）。
- 文件名中的哈希**必须是文件内容的 sha256**，禁止沿用下载器给的不可信文件名。
- 删除任何旧图片目录之前，必须逐文件验证其内容已存在于 blobs（sha256 比对），否则先并入 blobs 再删。

### 2.2 meta/ —— 真相区（只放真相，别的什么都不放）

**允许的文件（穷举，不允许出现清单之外的东西）：**

| 文件 | 角色 |
|---|---|
| `images.jsonl` | 唯一权威主清单：一张图一行（sha256 + 全部字段），按 sha256 增量 upsert；instances 字段只存实例名，实例名↔图关系由它单点承载 |
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

`state/collect/`：死信队列（`.dlq_*.sqlite3` + flags）、`source_health.json`、采集批次产物（`runs/<run_id>/`，含 `_latest` 软链）、`source_registry.jsonl`（源生命周期覆盖层，append-only）、`auto/`（缺口报告/源提案/探测账本）；LLM 生成的源 spec 落 `collect/specs/`（不入 git）；`state/dataset_index/`：COCO 缓存；`state/.lancedb/`：Lance 查询索引；`state/filter_vlm/`：VLM 过滤结果；`state/annotate_vlm/`：VLM 打标结果（results.jsonl）与打标队列（queue.sqlite3，collect/stream.py 生产、curation/annotate_vlm.py 消费）；`state/emerge/`：taxonomy 涌现缺口分析产物（caption embedding/聚类/LLM 命名对齐缓存/差异报告，curation/emerge.py 生产，人审消费，全部可从 images.jsonl 重算）。代码约定：仓库根由 `--meta`（默认 `data/dataset/meta`）向上三级推导。永远不进 meta/、不进 data/dataset/、不进 git。

### 2.4 一致性规则

- `images.jsonl` 是唯一真相；**不建任何派生索引文件**（原 instance_images.json 已废除：双份存储存在一致性漂移风险，需要实例名→图关系时由消费者从 images.jsonl 现场聚合，如 viewer/build_viewer.py）。
- `images.jsonl` 的 instances 字段只应是当前体系的实例名；体系演化后残留的死名打标从 images.jsonl 剥离（无隔离区）。
- 一张图的 instances 变更（改名/隔离）改的是 images.jsonl，**图字节不动**。
- 新元数据字段设计时必须先问"哪个消费者读它"；答案为空就不加。

## 3. 代码模块职责

| 模块 | 职责 | 入口 |
|---|---|---|
| `taxonomy/` | 标签体系维护：树审计（audit_nodes 死叶子审查）、挂载聚合（mount_map，只读现算不落盘）、富化（gen_taxonomy_kb 节点 KB / gen_instance_kb 实例知识，各一次 LLM 调用） | 各脚本 `--write` |
| `collect/` | 图片采集：任务配置、来源适配器（wikimedia/inaturalist/baidu/openverse/scrapers/cn_web/coco/hf_dataset）、下载、队列、增量消费、主清单 upsert、LanceDB 查询索引；常驻流式采集（stream：缺口驱动检索→DownloadQueue→流式下载，与批处理 run 并存） | `collect/cli.py`（含 `stream` 子命令） |
| `curation/` | 数据策展：失败重试（retry_failed）、VLM 图片质量过滤（filter_vlm）、VLM 知识打标（annotate_vlm：run 批量 / stream 常驻消费打标队列 / apply 合并）、taxonomy 涌现缺口分析（emerge：embed/cluster/name/align/report，数据有树无的新概念提议，人审入树） | 各脚本直接运行 |
| `viewer/` | 查看器闭环：页面 tag_tree_explorer.html + 构建脚本 build_viewer.py + 产物 build/（sidecar taxonomy.js/instances.js/imgs.js 与 standalone 单文件，gitignore）；HTML 与 build/ 同址是 file:// 双击可用的硬要求 | `viewer/build_viewer.py` |

> **架构决策（2026-08-19）**：标签体系解耦——instances.json 升为独立权威源（实体资产，生灭与富知识不依赖树），taxonomy.json 降为展示视角（树 + 挂载引用）；废除 instances.taxonomy_paths 字段（schema 2.0，实例表 56,789 条一次性迁移零丢失）并删除 build_unified.py。理由：树决定实例生死的反向控制是唯一残留耦合，斩断后数据处理链路（采集/打标/涌现）全部只读实例表；树可自由重生成/多视角并存而不伤资产。需要挂载关系的消费者（collect gap 聚簇、gen_instance_kb 与 emerge 的 prompt 上下文）改由 taxonomy/mount_map.py 从树现算。
>
> **架构决策（2026-08-17）**：broader/ 模块（Open-BROADER 上下位关系模型）迁出本仓库，回归独立项目 `/root/data/projects/open_broader/`（代码、55G 训练语料、训练产物、历史日志整体搬移，脚本内绝对路径已批量改写至新家）。理由：上下位判断本质依赖世界知识，通用大模型（Qwen3.8-27B 批审计 + 现成 embedding 检索）已可覆盖 taxonomy 树审计场景，且训练语料正确性存疑、课题短期难推进，故冻结训练、语料与 checkpoint 原地归档。本决策推翻 2026-08-16 的并入决策；未来如复活，先做大模型 vs BROADER 的 head-to-head 评测再立项。

- 跨模块 import 一律 `from <模块>.<文件> import ...`（四模块直接位于仓库根，仓库根在 sys.path 上）。
- 路径常量一律从脚本自身向上推导到仓库根，不依赖 cwd 之外的魔法。
- 新增脚本必须先归属到一个模块；归不进去的说明职责边界有问题。

## 4. 数据与代码的边界

- `data/dataset/`、`state/`、`logs/`、`.qoder/` 是本地数据/运行时产物，**不入 git**（.gitignore 强制）。
- 入库的只有：代码（taxonomy/、collect/、curation/、viewer/，含 viewer 页面 HTML）、约束文档（AGENTS.md、README.md）、以及 `data/taxonomy/` 下的权威 JSON。
- 大 JSON（images.jsonl、blobs）永远不进 git；需要备份走独立通道。
- 生成产物（`viewer/build/`）不入 git，数据改动后重跑 build_viewer.py。

## 5. 关键命令

```bash
# 标签体系富化（LLM 各一次调用；需 LLM_API_KEY 等环境变量；dry-run 零成本预览）
python3 taxonomy/gen_taxonomy_kb.py --only-empty --write       # 节点 KB（knowledge_intro 等 4 字段）
python3 taxonomy/gen_instance_kb.py --only-empty --write   # 实例知识（desc/query/aliases）

# viewer 产物重建（数据改动后）
python3 viewer/build_viewer.py

# 数据策展
python3 curation/retry_failed.py
python3 curation/filter_vlm.py run        # VLM 质量过滤（断点续跑）

# 采集（消费 data/taxonomy/instances.json，按 sha256 增量 upsert 进 images.jsonl）
python3 collect/cli.py --taxonomy data/taxonomy/instances.json ...

# 流式三级流水线（两个常驻进程；批处理 run 仍可用）
python3 collect/cli.py stream --taxonomy data/taxonomy/instances.json   # 检索→下载流式
python3 curation/annotate_vlm.py stream          # 消费打标队列，成功逐批合并进 images.jsonl
```

## 6. 禁止事项速查

- ❌ 在 `meta/` 里建除 2.2 清单外的任何文件
- ❌ 手改 blobs/ 下的文件（包括"顺手修一下坏图"——正确做法是重新采集）
- ❌ 删除图片目录前不做 blobs 内容比对
- ❌ 新增只写不读的"审计/日志"文件
- ❌ 在 taxonomy/、collect/、curation/、viewer/ 之外新增脚本
- ❌ 往 data/ 里放代码、页面或生成产物（viewer 页面与产物在 viewer/ 内闭环）
- ❌ 恢复历史过程文档（docs/、子目录 README）
- ❌ 在数据/代码里使用 category、leaf、root 作为分类概念
- ❌ 在 instances.json 里为同一 name 写多条记录（一个实体一条；多处挂载表现为多个树节点名单同名）
- ❌ 往 instances.json 里写树派生字段（挂载路径等）——挂载关系从树现算（taxonomy/mount_map.py），不持久化
- ❌ 把 data/dataset/、state/ 或 logs/ 提交进 git
- ❌ 把运行时状态塞进 data/dataset/（放顶层 state/ 对应模块子目录）

## 7. 网络与下载约定（2026-08-20 新增：环境里残留已宕机代理 100.89.199.67:7890，pip/curl 会被拖死，故将代理策略定死）

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
