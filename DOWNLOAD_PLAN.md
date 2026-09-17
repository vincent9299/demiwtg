# 下载计划（2026-09-17 起版 · 活文档）

> 维护方：训练机（lake，代码/文档权威源，仓库 `demiwtg-data`）
> 执行舰队：r1-r20（p1-p5 已于 2026-09-17 退役，全部数据已保全 COS）
> COS 桶：`lhcos-368f6-1256345599`（ap-singapore），树根 `lhcos-data/demiwtg-data/`
> 通道实测（2026-09-17）：r 机匿名读 COS ✓（206）；写走签名（`/tmp/cos_creds`，r5-r9 已补发）

## 〇、总览

| 部分 | 内容 | 体量预估 | 前置条件 | 状态 |
|---|---|---|---|---|
| 一 | kb 图池毒行重收 + 缩略图升级 | ~6-10TB | 无（清单已就绪） | **可执行** |
| 二 | wm 中文概念补图续跑（wk_backfill） | ~2TB | 无（断点在 COS） | 可执行（在途接续） |
| 三 | iNat 采图 | 待出清单 | ③-1 元数据关联差一步 | 排产前先做清单 |
| 四 | Smithsonian media 直取 | 待 v2 解析出清单 | ③-2 | 待解析 |
| 五 | OpenImages 图片 | ~500GB | P646 消歧命中率达标 | 门控未过 |
| 六 | Met 采图 | 小（24.8万 PD 对象） | met_fetch.py 加前缀 | 小活 |
| 七 | 挂起项 | WIT 27GB 等 | 解封/凭证 | 等外部 |

---

## 第一部分：kb 图池毒行重收 + 缩略图升级（最高优先级）

### 1.1 事实基础（2026-09-17 全量审计定案）

887 万行账本（8,861,354 行 / 8,866,002 对象 / 0.772TB）经**全量字节级嗅探**：

- **毒行 7,904,315 行**（89.2%）：内容为 Wikimedia 429 错误页（err_code=429 × 7,904,265，
  99.999%），账本名义字节合计仅 16.9GB（每行 ~2.1KB）。
- **真图 957,039 行**（10.8%）：>6KB 大图 930,104 + ≤6KB 小真图 26,934
  （2 万大档抽样仅 1 个 HTML，0.005%，上界验证通过）。
- 异常行（empty/missing/err）**0 行**；账本×库存 missing=0（join1）。
- 污染时间窗：**09-12 ~ 09-14**（4.65M / 3.04M / 0.22M，三天占 99.96%）；
  账本十等分每档 7.7-8.2 万毒行均匀分布——全池性，非局部。
- 根因：旧版 `flow_images_batch.py` 不查 HTTP 状态码（已修复入库，
  冒烟 `smokes/download_guard.py` 9 断言全过）。

### 1.2 重收清单（输入，全在 COS）

| 清单 | 路径（`lhcos-data/demiwtg-data/` 下） | 行数 | 说明 |
|---|---|---|---|
| ① 毒行 | `audit/2026-09-17/poison_html_rows.jsonl.gz` | 7,904,315 | 1GB，行内含 err_code |
| ② 缩略图 | `node-backup/2026-09-17/p5/demi/raw/state/thumb1200_rows.jsonl.gz` | 340,951 | 真图但降级存了 1200px，升 orig |
| ③ 异常行 | `audit/2026-09-17/poison_other_rows.jsonl.gz` | 0 | 空文件，无需处理 |

去重任务数 ≈ 7,904,219（(qid, commons_file) 口径，与行数基本 1:1）。

### 1.3 执行工具与口径

```bash
# ① 毒行重收（每台 r 机一片，i=0..N-1）
PYTHONPATH=<repo> python3 backfill_orig.py \
  --tasks-file /path/poison_html_rows.jsonl.gz \
  --blobs-root <COS 写通道，见 1.5> \
  --manifest qid_images-backfill-poison-shard-i.jsonl --shard i/N

# ② 缩略图升级
PYTHONPATH=<repo> python3 backfill_orig.py \
  --ledger .../kb/qid_images.jsonl.gz --tier thumb1200 \
  --blobs-root <同上> \
  --manifest qid_images-backfill-thumb-shard-i.jsonl --shard i/N
```

- 采集口径（修复后）：原图直取（`info.url`，不降级）、200 校验、
  429 按 RETRY_BACKOFF 退避、HTML 首字节拦截（`HTML_ERR_HEADS`）、
  新计数 `miss_html`（>0 即又被限流，立即降速）。
- 64MB 封顶仍在：巨物全景图（原缩略图通道服务的那批）会认缺；
  如必须收，加 `--hard-cap-mb` 并核算内存 = dl_conc × cap。

### 1.4 排产纪律（血泪红线）

- **礼貌上限 ≤8 机 × 2rps** 起步（25 机 × 4rps 曾触发 429 连坐，即本次事故根源）。
- 分批放量：先 1 机 × 1 万行试跑 → 验收（miss_html=0、sha 回读抽检）→ 再放量。
- r 机磁盘小（40-60GB）：产物**直写 COS**，不留本地大文件。
- 双实例教训：同一 shard 只允许一个采集进程（发射脚本加锁/先 pgrep）。

### 1.5 COS 写通道（r 机无 cosfs，二选一）

- 方案 A（推荐）：给 backfill 落盘层加 `stream_cos.py` 签名直传适配
  （模板已在 `audit/fleet/`，creds 各机 `/tmp/cos_creds` 已就位）。
- 方案 B：r 机挂 cosfs（creds 有，但 cosfs 大文件直写静默截断坑，
  >100MB 必分块+读回校验——不推荐重收这种海量小文件场景之外使用）。

### 1.6 体量与容量预估

- 毒行重收：7.90M × 原图均值（数百 KB 量级，参照真图均值 ~800KB 打上限）
  ≈ **2.4 - 6.3TB**。
- 缩略图升级：340,951 × >10MB 原图 ≈ **≥3.4TB**。
- 合计 **~6-10TB** 级；开跑前核 COS 容量与带宽预算，先出试跑实测均值再精算。

### 1.7 验收与收尾（重收完成后）

1. 复查：对重收 blob 重跑 `cos_sniff` 抽样（HTML 命中应 = 0）+
   `cos_deepcheck`（400 sha + 300 PIL）补完整性基线。
2. 并账：同 (qid, commons_file) 保 tier=orig 新行，剔旧毒行/旧行。
3. 清理（按引用计数，勿整前缀 rm）：毒 blob 7,904,311 个唯一 sha、
   孤儿对象 43,015 个（2.24GB）、0 字节残骸对象 1 个、撕裂账本行 1 条。
4. 释放空间回蘸：清完后桶内 kb 树应回落到 ~1TB 量级（真图）+ 新重收增量。

---

## 第二部分：wm 中文概念补图续跑（wk_backfill，在途接续）

- 现状：p2/p3/p4 已下线，三台各自完成 1,294+ / 4,074 行（wm_20/21/22 三片），
  **断点清单（done/dead）与候选已保全 COS**：
  `node-backup/2026-09-17/p2|p3|p4/wk_backfill/`。
- 主候选池：`node-backup/2026-09-17/p5/candidates.jsonl.gz`（1,958,026 行）。
- 工具：`fleet_curl.py`（curl 串行驱动，防护齐全：200 校验 + SHA256 复验 +
  20MB 上限 + done/dead 幂等）——已验证不会重蹈 429 落库覆辙。
- 接续方案：r 机领 wm 分片 + 断点，`--out-dir` 指向新 run 目录，
  产物同样直传 COS（同第一部分通道）。
- 注意：p2/p3/p4 已下载未发货的 ~11GB 图片本体已随机器释放放弃
  （done 清单在，可重下，无净损失）。

## 第三部分：iNat（先清单后下载）

前置：③-1 元数据关联差最后一步——用新版 `cos_cat.py`（带 Range 断点续读）
在任一 r 机重跑 iNat tar 抽取（taxa/photos/observations CSV）→
P3151+P225 双桥 join 概念集 → 产出采图清单后再排下载。
原料：`datasets/raw/inat/` 33 块（已内容级验证）。

## 第四部分：Smithsonian media 直取

前置：③-2 v2 解析（JSONL 流式，按新认知重写 `smith_parse.py`，
产出 id/title/unitCode/license/digital_assets/name 表）→
记录↔media 文件名映射确认后，从同桶 `media/` 前缀匿名 S3 直取。
metadata 已全量在 COS（13,608 片）。

## 第五部分：OpenImages（消歧门控）

MID→QID 消歧（P646 一对多；优先级：EN sitelink → P279 通用 → 字符串相似度）。
产出命中率报表 → 用户拍板是否启动 ~500GB 图片下载。
输入全在 COS：oidv6-class-descriptions + oidv7-train-annotations + concept_xref。

## 第六部分：Met 采图（小活）

`met_fetch.py` 两段式采集器已就绪，**改 blob key 加 `lhcos-data/` 前缀**后即可跑。
MetObjects.csv 24.8 万 PD 对象 × Artist ULAN（P245 桥）+ Object Wikidata 直挂 461。

## 第七部分：挂起项

- WIT 27GB：GCS 对机房 ASN 级限速 ~1KB/s，等解封。
- ImageNet / VisualSem / Rijksmuseum / Europeana：等 HF token / 作者密码邮件 / API key。
- 概念集放宽（未决项①）：放宽后 SDC 可挂载边 +40%、Met 直挂 461→4.6万——
  影响多部分清单规模，建议在各部分出清单前拍板。

---

## 附：公共约定

- 凭证：各 r 机 `/tmp/cos_creds`（70B sid:key；母本备份训练机
  `/root/cluster_backups/`）；`/tmp` 可能被清理，丢了从备份补发。
- 发货/验收脚本模板：`audit/fleet/`（stream_cos 签名直传、head_cos 校验、
  ship_node 幂等发货——同尺寸跳过即验证通过）。
- 巡检错峰：对舰队并发建连会触发代理 CONNECT 惩罚，顺序 + sleep；
  ssh 偶发黑洞是 pconn 代理层问题，命令一律包 `timeout` + 重试。
- 匹配检查用读回（cat|wc / md5），stat 相等 ≠ 内容正确。
- 本文档更新随仓库走：改完 commit+push（训练机为权威源）。
