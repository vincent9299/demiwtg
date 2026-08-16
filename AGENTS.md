# AGENTS.md · 项目架构原则与数据约束

本文档是**定死的架构约束**。任何代码修改、脚本新增、数据整理，都必须遵守。修改本文件本身就是一次架构决策，需要显式说明理由。

## 1. 项目分区

```
demiwtg/
├── taxonomy/                   # 【代码】体系构建与富化（build_unified / gen_taxonomy_kb / gen_instance_kb）
├── collect/                    # 【代码】图片采集系统（cli/config/downloader/pipeline/queue/sources…）
├── curation/                   # 【代码】数据策展（retry_failed / filter_vlm）
├── viewer/                     # 【代码】查看器闭环：tag_tree_explorer.html + build_viewer.py + build/ 产物（gitignore）
├── data/                       # 【纯数据】统一数据根目录
│   ├── taxonomy/               #   标签体系数据
│   │   ├── taxonomy.json       #     树结构权威源
│   │   └── instances.json      #     实例权威源
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

## 1.5 标签体系数据契约（只有两个概念，定死）

整个标签体系**只存在两个概念**，代码、数据字段、文档一律使用这两个词，禁止再引入其他分类术语（category、leaf、root 已废除）。数据模型以本节为准（原 schema/tag_taxonomy.schema.json 已删除：无校验消费者、与 instances.json 顶层结构不符，勿恢复）。

### taxonomy.json —— 树（结构权威源）

```
{ "schema_version": "...", "meta": {...}, "tree": <node> }

node = {
  name: str                    # 节点显示名
  path: str                    # 完整路径，' / ' 分隔，从根『融合世界标签体系』起算
  depth: int                   # 根为 0
  children?: [node]            # 子树；末端节点省略
  instances?: [str]            # 挂在本节点下的实例名列表（结构指针）
  knowledge_intro?/aliases?/representative_cases?/related_tags?: [KB 字段，可选；knowledge_intro 为 150-350 字维基百科词条风格]
}
```

### instances.json —— 实例（扁平权威源）

```
{ "schema_version": "...", "meta": {...}, "instances": [instance] }

instance = {
  name: str                    # 实例名
  taxonomy_path: str           # 所挂节点的 path（与 node.path 一致）
  source: "curated" | "llm" | "derived"   # curated=人工精写；llm=LLM 生成；derived=未富化占位（templated 为历史值，不再新写）
  desc?: str                   # 详细介绍（唯一富描述字段；150-350 字，维基百科词条风格：具体知识点，拒绝空话套话）
  aliases?: [str]              # 别名/英文名
  query?: [str]                # 检索扩展词（LLM 生成，含英文/简称）
}
```

- 两份文件经 `name + taxonomy_path` 关联；`taxonomy/build_unified.py` 是唯一重建入口。
- 没有 `type` 字段：有没有子树看 `children`，挂不挂实例看 `instances`。
- 图片打标只存**实例名**（实体标签，不含路径）——体系演化（改路径）不再需要迁移图数据。看图入口（viewer 的 build/imgs.js）直接按实例名从 meta/instance_images.json 查图、相对路径指到 blobs 原图（相对 viewer/ 的 ../data/dataset/blobs/...），不再建软链树；一个实体可挂多个路径是允许的（采集按实体去重）。
- 数据字段定义即契约，改字段 = 改本节 + 同步 build_unified.py。

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
| `images.jsonl` | 唯一权威主清单：一张图一行（sha256 + 全部字段），按 sha256 增量 upsert；instances 字段只存实例名 |
| `instance_images.json` | 实例名→图 反向索引（键=实例名，不含路径），由 images.jsonl 聚合 |
| `.meta.lock` | 跨进程写锁（运行时瞬态） |

**禁止出现在 meta/ 下的东西：**

- ❌ 审计日志（只写不读的账本一律不建；先有读取代码才允许写入）
- ❌ 备份文件（*.bak-*、*.bak-sync 之类）
- ❌ 派生索引（LanceDB 等）
- ❌ 运行时状态（死信队列 sqlite、健康账本、done flags、COCO 缓存）

**判据（新增任何文件前先回答）：**

1. 有消费者吗？——**必须先有读取它的代码，才允许写入它**。
2. 是真相还是派生？——派生的东西不进 meta。
3. 删掉它会丢数据吗？——丢了数据才是真相；能重建的不进 meta。

### 2.3 运行时状态在顶层 state/（不属于数据湖，按模块归属分子目录）

`state/collect/`：死信队列（`.dlq_*.sqlite3` + flags）、`source_health.json`、采集批次产物（`runs/<run_id>/`，含 `_latest` 软链）；`state/dataset_index/`：COCO 缓存；`state/.lancedb/`：Lance 查询索引；`state/filter_vlm/`：VLM 过滤结果。代码约定：仓库根由 `--meta`（默认 `data/dataset/meta`）向上三级推导。永远不进 meta/、不进 data/dataset/、不进 git。

### 2.4 一致性规则

- `images.jsonl` 是唯一真相；`instance_images.json` 由它聚合。改一处结构必须三处同步。
- `instance_images.json` 的键只应是当前体系的实例名；体系演化后残留的死名打标从 images.jsonl 剥离（无隔离区）。
- 一张图的 instances 变更（改名/隔离）改的是索引文件，**图字节不动**。
- 新元数据字段设计时必须先问"哪个消费者读它"；答案为空就不加。

## 3. 代码模块职责

| 模块 | 职责 | 入口 |
|---|---|---|
| `taxonomy/` | 标签体系构建（build_unified）、富化（gen_taxonomy_kb 节点 KB / gen_instance_kb 实例知识，各一次 LLM 调用） | 各脚本 `--write` |
| `collect/` | 图片采集：任务配置、来源适配器（wikimedia/inaturalist/baidu/openverse/scrapers/cn_web/coco/hf_dataset）、下载、队列、增量消费、主清单 upsert、LanceDB 查询索引 | `collect/cli.py` |
| `curation/` | 数据策展：失败重试（retry_failed）、VLM 图片质量过滤（filter_vlm） | 各脚本直接运行 |
| `viewer/` | 查看器闭环：页面 tag_tree_explorer.html + 构建脚本 build_viewer.py + 产物 build/（sidecar taxonomy.js/instances.js/imgs.js 与 standalone 单文件，gitignore）；HTML 与 build/ 同址是 file:// 双击可用的硬要求 | `viewer/build_viewer.py` |

- 跨模块 import 一律 `from <模块>.<文件> import ...`（四模块直接位于仓库根，仓库根在 sys.path 上）。
- 路径常量一律从脚本自身向上推导到仓库根，不依赖 cwd 之外的魔法。
- 新增脚本必须先归属到一个模块；归不进去的说明职责边界有问题。

## 4. 数据与代码的边界

- `data/dataset/`、`state/`、`logs/`、`.qoder/` 是本地数据/运行时产物，**不入 git**（.gitignore 强制）。
- 入库的只有：代码（taxonomy/、collect/、curation/、viewer/，含 viewer 页面 HTML）、约束文档（AGENTS.md、README.md）、以及 `data/taxonomy/` 下的权威 JSON。
- 大 JSON（images.jsonl、instance_images.json、blobs）永远不进 git；需要备份走独立通道。
- 生成产物（`viewer/build/`）不入 git，数据改动后重跑 build_viewer.py。

## 5. 关键命令

```bash
# 标签体系统一重建（taxonomy.json 为结构权威源）
python3 taxonomy/build_unified.py --write

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
- ❌ 把 data/dataset/、state/ 或 logs/ 提交进 git
- ❌ 把运行时状态塞进 data/dataset/（放顶层 state/ 对应模块子目录）
