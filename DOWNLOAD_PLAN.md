# 下载计划（2026-09-17 重构版 · 活文档 · 三大批次）

> 维护方：训练机（lake，代码/文档权威源，仓库 `demiwtg-data`）
> 执行舰队：r1-r20（p1-p5 已于 2026-09-17 退役，全部数据已保全 COS）
> COS 桶：`lhcos-368f6-1256345599`（ap-singapore），树根 `lhcos-data/demiwtg-data/`
> 通道实测（2026-09-17）：r 机匿名读 COS ✓（206）；写走签名（`/tmp/cos_creds`）
>
> **🔴 红线（2026-09-17 用户指令）：一切图片下载须等用户放行。本计划当前阶段只产出下载清单。**

## 〇、总览：三大批次

| 批次 | 内容 | 体量 | 状态（2026-09-17 晚） |
|---|---|---|---|
| **第 1 批** | kb 图池毒行重收 + 缩略图升级 + wm 中文补图续跑 | ~6-10TB + ~2TB | 毒行/缩略清单就绪**待放行**；wm 补图在途（20 机 fleet_curl，调度器已随 p5 退役，自然收尾） |
| **第 2 批** | iNat / Smithsonian / OI / Met 四源（先挂载出清单再采图） | OI ~500GB；SI/iNat 待清单；Met 小 | 挂载进展见 §二：**Met ✅ OI ✅**，SI 8 机分片 🏃，iNat 抽取 🏃 |
| **第 3 批** | SDC 新图（fetch_list 2026-09-17 03:24 终版） | ~21TB（原图口径） | 等**原图 vs 缩略**决策 + 放行（与第 1 批 WM 任务串行，fleet_curl 收尾后） |

**决策队列（按阻塞面排序）**：
1. 概念集放宽（影响第 2/3 批清单规模——建议在 SI/iNat 清单定稿前拍板）
2. Wikimedia 全局礼貌预算（第 1 批毒行重收 + wm 补图 + 第 3 批 SDC 同打 WM，须全局串行记账）
3. 湖侧容量口径（湖 /yzp 仅 ~5T 空闲 vs 第 1 批 6-10TB + 第 3 批 21TB；桶 256T 无压力，需定"哪些回湖"）
4. OI 500GB 采不采（命中率已出：89.4% 类挂载 / 289 万图）
5. SDC 原图 vs 缩略（`hist_ledger_stats.py` 可出三档 TB 数）
6. 桶根脏树 ~200GB 清理（`demiwtg-data/` 根级误传副本，实测仍在）

---

## 一、第 1 批：kb 图池修复与补图

### 1-1 毒行重收 + 缩略图升级（最高优先级，清单已就绪）

事实基础（2026-09-17 全量字节级嗅探定案）：887 万行账本中毒行 7,904,315（89.2%，err=429 占 99.999%），真图 957,039；污染窗 09-12~14；根因旧采集器不查状态码（已修复 + 冒烟 9 断言过）。

| 清单 | 路径（`lhcos-data/demiwtg-data/` 下） | 行数 |
|---|---|---|
| ① 毒行 | `audit/2026-09-17/poison_html_rows.jsonl.gz` | 7,904,315 |
| ② 缩略图 | `node-backup/2026-09-17/p5/demi/raw/state/thumb1200_rows.jsonl.gz` | 340,951 |
| ③ 异常行 | `audit/2026-09-17/poison_other_rows.jsonl.gz` | 0（空） |

执行：`backfill_orig.py --tasks-file/--tier`，r 机分片；礼貌红线 **≤8 机 × 2rps 起步**（25 机 × 4rps 曾引发 429 连坐）；先 1 机 × 1 万行试跑验收（miss_html=0 + sha 回读）再放量；产物直写 COS（stream_cos 签名直传）；体量预估 2.4-6.3TB（毒行）+ ≥3.4TB（缩略升级）。
收尾：重收 blob 复嗅探抽样（HTML 应=0）→ 并账（同 (qid,commons_file) 保 orig 新行）→ 按引用计数清理毒 blob 7,904,311 + 孤儿 43,015 + 0B 残骸 1。

### 1-2 wm 中文概念补图续跑（在途接续）

- 主候选池 `node-backup/2026-09-17/p5/candidates.jsonl.gz`（1,958,026 行）；断点 done/dead 在 `node-backup/2026-09-17/p2|p3|p4/`。
- 工具 `fleet_curl.py`（200 校验+SHA256 复验+20MB 上限+幂等）——当前 20 机在跑（各领 ~4K 行分片，r11 已近完）。
- 调度器（原 p5）已退役，**不会再派新分片**；各机跑完自然结束。
- p2/p3/p4 未发货的 ~11GB 已随机器释放放弃（done 清单在，可重下）。

---

## 二、第 2 批：四源融合挂载 → 采图清单（先融合后下载）

### 融合方法与状态（2026-09-17 晚实况）

| 源 | 桥 | 状态 | 产物（COS `kb/batch2/<源>/`） |
|---|---|---|---|
| Met | P245(ULAN) + Object Wikidata 直挂 | ✅ **完成** | fetch_list：484,956 对象→PD 248,472→**挂载 56,819(artist)+461(direct，与基准分毫不差)** 行；URL 由 met_fetch 两段式 API 取 |
| OpenImages | P646 消歧（EN sitelink 优先；P279 不可得已记录） | ✅ **完成** | 19,994 类挂 17,855（89.4%）→扫 4,210 万标注→**2,889,193 图**；fetch_list 133MB + mid_map + disambig.json |
| Smithsonian | scientific_name→P225 学名桥 + 名字→概念标题桥 | 🏃 8 机分片（r2/r3/r4/r6-r9/r13，两遍扫+全局建桥，~40 分钟） | 阶段1 记录表 1,733 万已入；media 直下 URL 在记录内（`ids.si.edu/ids/download`）+ 每 media 独立 license |
| iNat | P3151(taxon_id) + P225 双桥 | 🏃 r5 流式抽取 | observations.csv ✅（**2.76 亿行/32GB**→13.6GB gz 入 COS，含 taxon_id）；photos/taxas 抽取中；join 待照片列头 |

目录结构与机器纪律见 git 历史"批次 2 融合执行设计"节（本版并入上表）；内存纪律：概念集 220M 位图（27MB）防 r 机 OOM。

### 第 2 批执行历史坑（本轮新增，写代码前必读）

位图上限 140M 不够（Q 号已超，扩 220M）；bytes/str 混用；SI media 路径在 `content.descriptiveNonRepeating.online_media.media[]`（连错两层）；SI 学名真字段 `indexedStructured.scientific_name`（taxonomicName 是分类路径串）；MetObjects CSV 无图列（两段式 API 取）；pkill 自匹配再犯两次；r 机解 iNat 须流式边解边压（三 CSV 解压 ~45G 超单机盘）。

---

## 三、第 3 批：SDC 新图（fetch_list 已就绪，待决策）

- **清单**：`kb/sdc_fetch/fetch_list.tsv.gz`（125MB，2026-09-17 03:24 终版）——4,736,141 文件 / 5,415,631 边 / 877,720 概念 / img_size 合计 **20,973GB（原图口径）**；漏斗数与上游文档分毫不差。
- **关系现状**：SDC 挂老池的 2,288,880 边已入库（sdc_attach，零下载，实测行数吻合）；本批下载的是**排除已有后的新图**，下完账本入库（parts 收集→**去重合并**→`kb/qid_images_ext/sdc_fetch.jsonl.gz`）SDC 线即闭环。
- **断点**：test 期 10,253 行（各机 `~/sdc_fetch/ledger.jsonl` + COS parts），幂等续跑。
- **工具**：`sdc_fetch_fleet.py` v3（MD5 路径 URL→sha256→COS blob→双落账本；分片必须 `I/N` 完整格式）；发射/停止脚本 `fleet_relaunch.sh`/`fleet_stop.sh`（纯 r 机版，r1-r20 各领 `i/20`）。
- **⚠️ 舰队已由 /23 重分片为 /20**（p1-p5 退役所致）：旧 done 集按机器本地 `orig_file` 记账，重分片后约 95% 已完成文件会换机重下（blob 内容寻址去重，无害），**各机 ledger 将出现跨机重复行（预期 ~1 万）**——收官合并必须去重，勿用裸 zcat 直灌：
  ```bash
  zcat parts/*.jsonl.gz | python3 -c '
  import sys, json
  seen = set()
  for l in sys.stdin:
      r = json.loads(l)
      k = (r["qid"], r["orig_file"])
      if k not in seen:
          seen.add(k)
          sys.stdout.write(l)' | gzip > kb/qid_images_ext/sdc_fetch.jsonl.gz
  ```
- **放行硬前置**：r1-r20 与第 1 批 wm 补图（fleet_curl）**同机**——放行前须确认 fleet_curl 已全部自然收尾（`fleet_relaunch` 只清 sdc 自己的进程，不会动它）；两线并发即双打 WM，必触发 429 连坐。
- **前置决策**：①原图 vs 缩略分层（改缩略改动小：fetch_bytes 加 thumb 分支+账本 tier 字段；`hist_ledger_stats.py` 可出精确 TB 数）②Wikimedia 限流现实：**单 IP ~0.7 张/秒**（Retry-After:11），r1-r20 满编 20 机 ≈14 张/s，全量 **≈3.9 天**连续；礼貌预算与第 1 批共享（WM 任务全局串行）。

---

## 四、挂起项（用户裁定 2026-09-17：五源搁置不追）

WIT 27GB（GCS ASN 限速）；VisualSem（图源即 Commons，重叠最高）；Rijksmuseum / Europeana（博物馆轴已有 Met+Smithsonian 覆盖）；ImageNet（唯一留意：图多来自 Flickr 等非 Commons 渠道、WordNet 标签独立，未来扩类别标注再单独立项）。凭证到手也不主动开线。

---

## 附：公共约定

- 凭证：各 r 机 `/tmp/cos_creds`（母本 `/root/cluster_backups/`）；丢失从备份补发。
- 发货/验收：`stream_cos.py` 签名直传 + `head_cos`/湖侧签名 HEAD 读回校验（同尺寸跳过=验证通过）。
- 巡检错峰：代理惩罚突发 CONNECT，顺序 + sleep；ssh 黑洞包 `timeout` + 重试。
- **图片下载一律等用户放行**；WM 类任务全局礼貌预算串行。
- 匹配检查用读回（cat|wc / md5），stat 相等 ≠ 内容正确。
- 本文档随仓库走：改完 commit+push（训练机为权威源）。
