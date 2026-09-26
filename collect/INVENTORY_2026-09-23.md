# 数据资产全面盘点(2026-09-23 凌晨,并账前置)

> 方法:COS 队列对账(11 队列 batches/done/claims)+ 20 机账本全量清点(grep/sort,只读)
> + blob 抽查 83 张(HEAD 存在性+尺寸+魔数)+ COS 前缀清册(直接列举/抽样外推)。
> **未动任何原账本文件**;新鲜镜像只增不删(COS `kb/manifest_snapshots_0923/`)。

## 0. 总表(账本口径,含跨机重复,并账时去重)

| 线 | 任务规模 | 账本成功行 | 死信行 | 唯一 sha | 状态 |
|---|---|---|---|---|---|
| b1 毒行重收(2026-09-20~21) | 7,904,315 毒行→947 批 | 9,473,234(旧 schema 无 src,含重扫) | —(checkpoint 时代录) | 并入下行 | ✅ 收官 |
| b3 SDC 主体 | 2408 批(+b003000 升级批) | 4,571,437 | 52,545(400×50.9K/404×1.2K/429/503) | 同 b3_backfill 组 | ✅ 收官 |
| b3x SDC 差集 | 482 批 | 960,936 | 25,312(400×25.0K 主) | ↓ | ✅ 收官 |
| b1u/b3v 缩略升级 | 340,951 行(=orig 21,916+thumb 318,885+400 补) | 360,291(含重扫) | 46,869(400×44.1K→b3w 补采中) | ↓ | ✅ 收官(b3u 队列废弃被 b3v 取代) |
| **b3w 400 行原图补采** | **36,516 行/19 批** | 在途(done 1/19) | — | ↓ | 🟢 **在跑(本夜收官)** |
| b2 四源(Met/OI/SI/iNat) | ~1,127 万(队列 30/1446/1647/1793 批) | 4,897,996 | ~5,380,000(dead.jsonl,no_media 为主) | 4,887,424 | ✅ 收官 |
| b4 TMDB | 706,451(354 批) | 564,622 | 191,934(无图实体) | 564,502(blobs-nc ≈525K 件/143GB 抽样外推) | ✅ 收官 |
| b4 GBIF 滴灌 | 1662 批(停) | **13,134**(ledger 行级) | 8,159 | 11,466 | ⛔ 用户停线 |
| sdc_fetch test 期 | 10,253 | 8,539 | — | 7,009 | ✅ 历史完成 |
| wm 中文补图(更早) | 1,958,026 候选 | 88,899(done 清单在 COS/images 系) | 13,157 | 旧系 | ✅ 历史完成 |
| images 池(旧系) | 2,899,895 行 | 2,163,475 唯一 sha(本机) | 18,026 | 与 qid 系重合 2.1% | 独立体系,并账口径待拍板 |

**b3_backfill 聚合组**(r 机 run_kb_w* manifests,含 b1+b3+b3x+b1u 全部):92,081,868 行 = 15,373,427 ok(有 sha)+ 86,053,564 旧 schema;其中 **76,580,330 行是"任务种子"**(b1 时代 kb_backfill 把任务行预写进 manifest 当断点 checkpoint,非数据行,样例 `{"qid":...,"commons_file":...}` 裸行),并账**只取有 sha256 的行 + miss 死信行**。唯一 (qid,commons_file) 任务键 25,877,632,唯一 sha 14,870,662(跨线内容寻址去重后)。

## 1. 账本/元数据/过程文件位置(逐线)

### r 机侧(权威账本,均已镜像上 COS)
| 机器路径 | 内容 | 说明 |
|---|---|---|
| `~/wk_backfill/run_kb_w<身份>/manifest.jsonl` | b1/b3/b3x/b1u/b3v/b3w 全部行(混合 schema:老种子行+新 src 行) | **身份=worker id(4~140,900-919 直连)**,每机 7~8 个;manifest 按身份跨队列共享 |
| `~/wk_b2/run_r<机号>/` | b2 四源:ledger.jsonl(成功)+dead.jsonl+finished.jsonl | 目录按主机命名 |
| `~/wk_b4/run_t1-3/`(tmdb)、`run_g1-3/`(gbif) | b4:ledger/manifest/dead/finished.jsonl | **gbif 以 ledger.jsonl 为权威**(manifest.jsonl 伴生文件有未完成残留,抽查 404 即来源于此,勿用) |
| `~/sdc_fetch/ledger.jsonl` | sdc test 期 8,539 行 | |
| `~/osm_slice_gbif/`(progress/fail.log 等) | GBIF 拉片过程文件(死线,历史) | |

### COS 侧
| Key 前缀 | 内容 | 量 |
|---|---|---|
| `kb/manifest_snapshots_0923/` | **今夜新鲜镜像** 20 机 tar.gz(b1~b4+sdc 全账本) | 20 件(上传中) |
| `kb/b3_manifests/` | 09-22 11:00 旧镜像(**过期**:b3x 尾段/b3v/b3w 未含) | 20 件 5.92GB |
| `queue-b{2,3,4}-*/{batches,claims,done}` | 11 条队列任务与状态(见 §0;b3u 170 批废弃未跑、b4-gbif 1662 批未跑) | |
| `datasets/demiwtg/kb/qid_images.jsonl.gz` | **主账本** 8,861,355 行(湖侧副本 /tmp/qid_images.jsonl.gz) | 1.13GB |
| `datasets/demiwtg/kb/qid_images_ext/` | 四路融合边表 4 件 + sdc_attach | 0.24GB |
| `datasets/demiwtg/kb/sdc_fetch/` | fetch_list.tsv.gz(SDC 任务清单)等 5 件 | 1.91GB |
| `datasets/demiwtg/kb/batch2/<met|openimages|smithsonian|inat>/` | b2 四源任务清单 | |
| `datasets/candidate/wikimedia/` | 三张钥匙表(mid_to_file 1.43 亿/sdc_depicts 5332 万/concept_xref 2547 万) | 权威 |
| `datasets/demiwtg/kb/blobs/` | qid 系图库(内容寻址,由 manifest 唯一 sha 推算 ~1500 万+件;今晚抽查 65/65 在) | |
| `datasets/demiwtg/kb/blobs-nc/` | NC 分区(TMDB 为主) | ≈525K 件 ≈143GB(抽样外推) |
| `kb/wikidata_bridges/` | 桥表×2(b4 4,897,790/meta 12,096,056 行)+sitelink_qids 38,414,271 | 0.18GB |
| `kb/meta_dumps/` | GeoNames/Getty×3/GADM 五件 + osm/ 8 洲 4.27GB | 10.25GB |
| `kb/wd_full/parts/` | Wikidata latest-all.json.gz 切片 300 件(sitelink 已用,完好) | 156.1GB |
| `kb/osm_parts/gbif/` | GBIF zip 碎片 71 件(**死线资产,待拍板删**) | 36.28GB |

### 湖侧(本机)
`collect/`(工具+文档+batch4/gbif_zip_route 工程归档);`_staging/fleet202609/`(b1 并账输入 7.8G+键集)、`batch2_audit` 432M、`inat_local` 46G;`meta/`(旧系 images.jsonl+概念层);/tmp 副本若干(易失)。**湖盘 98% 满,任何大数据勿落湖。**

### sgx
`~/wd_full/sitelink_uniq.txt.gz`(38.4M qid)、`~/latest-all.json.gz`(**今夜新装配 156GB**,P31 备用)、`~/wd_b4_bridge.nt`(3,325,204 P846)、`~/gbif_members.json`(zip 成员表)、GBIF 工程件;磁盘 443G 用 36%。

## 2. 质量验证记录(今夜实测)

1. **blob 抽查 83/83 全过**:b2 15/15、b3 线 41/41、TMDB 9/9(blobs-nc)、gbif 滴灌 9/9(ledger 口径);魔数全部合法(含 1 个 SVG DOCTYPE 正常变体);gbif 首验 1 例尺寸 826,342B 与账本逐字节吻合。
2. **队列恒等式**:11 队列中 9 条 done≥batches(负差=重认领批重复标 done,manifest 有对应跨机重复行,并账去重即可);b3u 废弃 0/170(行已重切 b3v,无丢失);b4-gbif 0/1662(停线);b3w 在跑。claims 残留对象(b3 2266/inat 1579 等)为陈壳,不影响账。
3. **两处口径坑已定性**:①76.58M 种子行=checkpoint 非数据;②b4_gbif 的 manifest.jsonl 伴生残留行不可作数(用 ledger.jsonl)。

## 3. 已知风险/缺口(并账会话必读)

1. **gbif 滴灌 13,134 行 license_zone 全空**,而 license 多为 CC BY-NC(如 inaturalist 原图)——并账须按 license 字段重判 nc 分区(行数据在,可修复)。
2. 主账本 **pid: 命名空间 1,139,488 行**(属性概念,非 item)——保/剔待拍板。
3. sdc 线 /23→/20 重分片**跨机重复 ~1 万行**——合并必须去重,勿裸 zcat。
4. b1u 行 tier 混合(orig 21,916 + thumb1920):同 (qid,commons_file) 保 orig 新行。
5. b3w 在跑(36,516 行):并账前收官或显式排除。
6. 待拍板清理:COS gbif 片 36.28GB、queue-b4-gbif 1662 批、queue-b3u 170 批、kb/osm_parts/eu|na 残片、旧 b3_manifests 过期镜像。
7. 巨物政策:>64MiB 认缺(over_cap);b001938 毒尾 126 行未做(降档 1280px 可补)。
8. 唯一 sha 交叉:b3 组 14.87M + b2 4.89M + tmdb 0.56M + gbif 0.01M ≈ 20.3M(未跨组去重;主账本另有 8.82M)。

## 4. 并账输入映射(只列不执行)
主账本(qid_images) ⊕ run_kb_w manifests(取 sha 行) ⊕ b2 run_r*/ledger ⊕ b4 run_t*/ledger(+gbif ledger 含 nc 重判) ⊕ sdc test ledger → 按 (qid,commons_file) 取最新/tier 优先 → done/dead_perm/dead_retry 三表 + 恒等式对账。工具输入齐:`_staging/fleet202609/`(b1 输入)+ 今夜 `kb/manifest_snapshots_0923/`。

---

## 5. 与 INVENTORY_AUDIT_20260923.md(另一会话)核对结论(2026-09-23 03:0x UTC)

**一致项(核心全部对上)**:11/9 条队列批数与 done 状态逐条相同;b2 四源成功(其 per-source 4,825,015 vs 我聚合 4,897,996,差=我含 manifest/ledger 重复);blobs-nc 530,590 vs 我外推 525,376;TMDB 70.8 万/死信=无人像;b1u 4.1 万 400=原图≤1920px;sdc_attach 88.5% 挂毒两边同标。**他们做了 blobs 全量列举(26,526,816+重灌 32 万≈2,684 万对象)——比我抽样外推硬,以他们为准。**

**三处实质差异(已裁决)**:
1. **GBIF 滴灌 ≠ 0 张**:他们按队列 done=0 记"0 张";实测 ledger 有 **13,134 张成功已入库**(blob 9/9 实证,含 license_zone 空的 BY-NC 误标)。**并账必须含这 1.3 万张并按 license 重判 nc。**
2. **旧系 images 池失联:他们对,我错**(我引旧文档未实测)。已验证 `0905/datasets/demiwtg/` 整个子树不存在——**216 万搜索池图 + meta/images.jsonl 账本双失联**,去向需用户确认(自行清理?迁移?123pan 有 342GB 子集;COS 侧根树 09-20/22 已清)。
3. **b2 死信语义:他们的分解对**(si 254 万=超 8MB 上限策略死信可回补缩略、oi 232.7 万=404 清单逻辑待查、met 5.6 万=合法无图);我原文"no_media 为主"系臆断,**以他们为准**。

**采纳与整合**:质量抽检以他们为深(Pillow 解码逮到 met 25% 截断 0-44B,行动项:重下 8,612 张比对);镜像重复两套(其 fleet_manifests/snap1+snap1b 4.6GB vs 本档 manifest_snapshots_0923 4.68GB)——**并账以 0923 为主**(最新,含 gbif ledger+b3w/b3v 尾段),snap1b 的 b2 dead/finished 拆件可作补充。终数采用他们口径:清理后真图 ≈ **1,930 万张**,毒页 800 万/17.1GB 待账本重建后删。
