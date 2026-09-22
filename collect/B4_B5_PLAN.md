# 第 4/5 批计划（v2 · 2026-09-21 重排：过滤不可下源 + 全 qid 口径）

> **口径变更（用户裁定 09-21）：本轮起锚定范围从"Wikipedia 条目集"扩为
> "Wikidata 全体 qid"——凡属性可匹配的实体全部挂载下载，不再要求有条目。**
> 架构不变：demiflow 队列+worker+算子；图片下载红线照旧（B4 发射前等放行令）。

## 第 4 批：立即可下（零注册：2 新图源 + 5 元数据 dump，全 qid 直锚）

| 项 | 源 | QID 桥（Wikidata 属性直锚，零条目依赖） | 规模 | 状态 |
|---|---|---|---|---|
| P4-5 | **TMDB** 影视 | P4947(movie)/P4983(tv)/P4985(person) | ~220 万张+头像，**全量新概念** | ✅ key 已验证（`batch5/.tmdb_token`）；每日 ID dump 提速 |
| P4-9 | **GBIF** 生物多样性（EOL 平替） | **仅 P846(taxonKey)**；~~P3151 兜底~~（金丝雀 09-21 纠偏：P3151 实为 iNat taxon ID，其图归 b2-iNat 线，不经 GBIF） | 带图 occurrence 数千万，首期取 500-1000 万 | ✅ 匿名 API 实测 200；与 iNat 内容 sha 自然去重 |

**金丝雀（09-21，`batch4/b4_op.py` + canary_*.jsonl，湖侧直跑）**：
TMDB 8/10（2 person 无头像死信，jpg 33-330KB，全进 `kb/blobs-nc/`，license_zone=nc）；
GBIF 4/10（8 个 no_media 均核实为冷门物种全球无带图记录——count=0 属数据现实；
带图的出 CC-BY 图正常入 `kb/blobs/`）。随机 P846 物种 GBIF 带图率 ~35%，
×3 张/物种 → 全量 ~300 万行可出 ~500-900 万张，与首期规模预估吻合。
token 文件含标签/中文备注，算子用正则只取 JWT（latin-1 头报错由此修复）。

**全 qid 桥的准备（关键前置）**：需要 Wikidata **truthy dump** 的属性切片——
`latest-truthy.nt.bz2`（压缩 43.5GB）→ 双桥表（产物各百 MB 级）：
- `wd_b4_bridge.nt`：P4947/P4983/P4985/P846/P3151（图源桥）
- `wd_meta_bridge.nt`：P1566/P245/P1014/P1667/P1435/P6265/P830/P225（元数据五件套+B5 桥，同一次解压顺带切出，免二次下载）
通道（09-21 实施纠偏）：sg1 **8 路并行 range 分段下载**（Wikimedia CDN 单 IP 并发
range 连接 ~3-4 路稳定 ~5MB/s/路；单流仅 2.5MB/s 且 curl -m 超时必死）+ 停滞检测
（--speed-limit 10K/60s）+ 分段断点续传；到齐后 cat|bzip2 -d 一次解压 tee 双路 grep。
教训：流式管线 curl -m 14400 硬超时前下不完会整体重零；pkill -f 会自杀（模式匹配
到自身 ssh 命令行），杀进程必须按 PID。
许可分区：TMDB CC BY-NC → `kb/blobs-nc/` 独立分区 + 账本 license_zone。

## 第 4 批（续）：元数据 dump 五件套（零注册，与上图同步推）

| 项 | 源 | QID 桥 | 产出 |
|---|---|---|---|
| P4-3 | GeoNames allCountries（1.5GB 一次拿） | P1566 | 200-300 万地名层级/坐标/别名（GeoNames 先行做底座） |
| P4-8 | Getty ULAN/AAT/TGN RDF | P245/P1014/P1667 | 艺术家/器物/地名权威层级 |
| P4-2a | OSM planet wikidata=*（osmium tags-filter） | OSM 原生 tag | 300 万空间对齐 |
| P4-1 | WLM 元数据（Toolforge + Monuments db） | P1435+遗产属性 | 200-300 万对齐边（存量图免下） |
| P4-4 | GADM 边界（3-5GB） | GeoNames/ISO 中转 | 30 万行政概念边界 |

## 第 5 批：注册即发（三项全部保留，凭据到位即发射）

| 项 | 源 | QID 桥 | 规模 | 凭据动作（均免费即时） |
|---|---|---|---|---|
| P4-6 | Mindat 矿物 | P6265 | 80-100 万张（NC 分区） | 注册 → Account→API token |
| P4-2b | Mapillary 街景 | OSM tag 中转，depicts_part | 100-200 万张 | dashboard/developers → client token |
| P4-7 | EOL 策展生物（GBIF 之上的增强层） | P830+P225 | 500-1000 万（去重后） | 注册 → 5000/日档 |

> EOL 与 GBIF 是互补而非二选一：GBIF 管覆盖广度（匿名先跑），EOL 管策展质量
> （vetted 分级、部位/发育期标签），注册后作为增量层接入。

## 里程碑

1. **现在**：sg1 启动 truthy dump 流式抽取（桥表前置，~3-4h）
2. b2/b3 收官并账期间：桥表落地 + TMDB/GBIF 算子 + 金丝雀（各 10 张端到端）
3. 用户放行 → B4 发射（20 机队列）
4. 凭据到位 → B5 随时跟进
