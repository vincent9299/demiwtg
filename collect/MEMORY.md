# 采集线·记忆总档（2026-09-20 合成）

> 本文合并 collect/ 下 14 份历史文档的**现行有效知识**：任务全景、存储契约、
> 机器拓扑、运行手册、关键数字、坑全集。新会话读本份即可接手；
> 历史细节去查文末索引的原文档（均已标注时效）。
> 存储拆分细节的权威版本在 `COS_BUCKETS.md`（2026-09-20 节），本文只留要点。


---

## 〇、新窗口开工指引（2026-09-20 11:30 快照，先读这节）

### 在途（2026-09-20 22:20 复核）
| 任务 | 位置 | 状态 |
|---|---|---|
| fleet 第1批尾段 | r1-r20（0.9rps/3lanes） | **仍在跑**：22:12 实测全网 ~96.7 张/s、20/20 主机、429=0（r9 七 worker 停摆=老 ssh 病）。11:30 快照"剩140万/ETA 4h"已失准，余量未收割勿再估；`fleet_pending_all.py` 会先停进程，只许收官用 |
| ~~images 回流 Phase B~~ | 本机 relay_b2 | ✅ **22:47 真 ALL_DONE 闭环**（22:20 复核所记"残留 103 死链"系误判：实为 GZ 中转 404、SG 源完好 → sg1 relay_a 补推 103 后 pass41 收敛）。核验：57,307 尺寸 100%、sha256 抽检 30/30 对（⚠ key 是 sha256 非 md5） |
| ~~根树 wm 删除 + GZ 清空~~ | sg1/cn1 | ✅ **22:50-22:55 已执行**：SG 88,899 key 精确删（`del_keys_exact.py` ok=88899 miss=0 fail=0，~65GB，绝不前缀删）；GZ 桶清空（`gz_purge.py` cn1 内网，blobs 57,307 + tmp/sg_relay_test 3；桶仅剩 `test/api-demo.txt` 43B 演示文件）。清单=`wm_del_keys_88899.txt`（root−orphan 差集+四路验证+本地全量实存 88,899/88,899） |
| fleet 监控 | fleet_monitor.py | **已死**（/tmp/mon 被清）。重拉时日志放 `checkpoints/monitor/`（持久位），勿再放 /tmp |

### ⚠→✅ 2026-09-20 22:31-22:40 孤儿删除收场（用户亲自推进，已了结）
22:31 起用户从 116.238.240.2（新 IP，同一公钥）亲自登 sg1，另起修正版删除把孤儿**全部删完**（~340/s，5 分钟余量）。终态（22:40 count）：`datasets/demiwtg/blobs/` 根树 **0 数据对象，仅剩 ~250 个 0 字节目录 key**，0.00GB。**至此 439.7GB 旧系根树彻底清完** = 88,899 wm（本会话精确删）+ 516,791 孤儿（用户拍板直删，含早前 10,370）。早期过程存档：本机 IP 的并行会话曾传 del_keys.txt 上 sg1 跑 mass_del/mass_del2 两版，均因 `_call` 三元组解包 bug `ok=0` 全 fail（一个没删掉，22:52 跑完 fail=516791）；22:36 又起一轮幂等确认（md3.log，无害）。孤儿"当 123pan 载荷/搁置"选项就此作废——**b) 直接删 已执行**。`root_tree_after_wm_del.txt`（22:12 中间态快照 506,671）仅存对账价值。
教训：多 actor 并行操作机器/COS 时动手前先 ps+auth.log 巡检；本机日志=UTC、sg/cn1=+0800，排查时间线先 `date +%s` 对齐；删除脚本 ok/nf/fail 必须对总数强校验；gz_stream miss 静默 + relay_a fail 静默曾致 103 张零告警死循环。

### 主任务：123pan 中继链（方案 v2 定稿 2026-09-20 本会话逐项拍板，取代旧 6 点修正版）

**范围（拍板）**：全量 qid 体系闭集（kb/ 全家 + raw/ + candidate/ + docs/），123pan 镜像原目录结构；仅 kb/blobs 散图 tar 打包（≤4GiB/卷 + manifest 成员 key/md5/size，卷名 `<批次>/part-NNNN.tar`），其余原文件。123pan 容量 **100TB**（远期 ~30-40TB 无压力）。孤儿 blob 51.7 万不在闭集，处置另议。

**链路**：SG 桶（源，**中继只读、永不回写**）→ sg1/sg2 常驻流式管线 → GZ 桶 `pan123-relay/` 前缀（队列，消费后删）→ cn1 内网免费 41MB/s → cn1 daemon → 123pan。瓶颈 sg→GZ 30MB/s ≈ 2.6TB/天。

**sg 端 = 常驻流式管线（非轮次批）**：
- 环形扫描 daemon 永续 list 翻页。COS 无变更推送、内容寻址 key 随机分布、list 不支持时间过滤 → 增量只能连续扫，检测延迟 = 单圈（分钟级）。fleet manifest tail 可作快路径，二期可选。
- 生产账本 sqlite（key→etag/size；已推+已见都记，毒 blob 记跳过标记防每圈重查），定期快照 GZ `_ledger/sgN/` 防机器级丢失。
- 打包 worker：HEAD 拿 size → 下载流边算 md5 边**顺序写本地 tar（散图从不单独落盘；tar 只归档不 gzip——图已压缩，白烧 CPU）**→ 4GiB 或 linger 超时封卷 → 简单 PUT 上传 GZ（≤4GiB 免分片，回包 ETag=md5 即校验）→ 记账 → 删本地卷。
- 可变文件（主账本/概念层，已知小 key 集）旁路周期 HEAD 比 etag，变了单文件重推（123pan 端删旧传新，**同名覆盖语义试点实测**）。
- blobs 毒过滤 = 时间闸门 `last_modified ≥ 09-17`（毒 blob 钉死 09-12~14 窗）；存量基线等毒清理后补扫。

**背压（拍板：绝不动 fleet 下载，只调中继自身扫描/封卷/推送速率）**：
- 闸门 A：GZ 积压 >50GB 或 >24h → 停推（结构性安全：41 消费 >30 灌入，前缀常态近空）。
- 盘闸（sg 机不崩的保证）：占用结构性有界 = 并发卷数×4GiB + 待传预算 + 账本 sqlite（~2-3G 封顶）；60G 盘设 35-40G 硬顶预留系统 10G+；过水位停封卷/扫描但上传继续排水；半卷 `.tmp` 永不上传、重启清理；卷只在 GZ ETag 对上后才删（最坏重传幂等）。
- fleet 与中继经 SG COS 完全解耦；SG 未同步积压由 COS 付费盘吃下（5TB 内免费，超出 0.0056 元/GB/日）。**总量长期封顶唯一手段 = 闸门 D 消费后删 SG**（政策门：谁还从 COS 读 blobs 待审计；四道安全门：账本确认过 / 闭集前缀白名单 / tar 抽读回含秒传路径 / 先 blobs 试点跑稳再扩）。

**cn1 daemon（不变）**：list-marker → 下载 → md5 → 上传 v2 create{etag=md5}（秒传幂等）→ 消费账本（本地 + 每 N 分钟快照 GZ `_ledger/`）→ DELETE 前代码校验 `pan123-relay/` 前缀。死信队列。1G 内存纪律：不聚合、sqlite、并发 ≤4×4GiB 暂存。

**实施**：① pan123.py（`/yzp/zhaozy/yangzepeng/0905/pan123/pan123.py`）加 mkdir+upload+删除，本机冒烟（实测秒传+同名覆盖语义）② sg 端 `cos_relay_push.py`（流式管线）③ cn1 端 `cos123_relay.py` ④ 100 文件全链试点（sg 推→cn1 消费→123pan 读回对 md5，实测 cn1↔123pan 带宽）⑤ 铺开：非 blobs 子树先行全量，blobs 增量并行（时间闸门），存量基线等毒清理 → 转常驻。

### 实施状态（2026-09-20 21:40，①~④ 全部完成并验证；**⑤ 转正放行：2026-09-20 深夜用户指令，半夜由新窗口启动，见下方启动指引**）

工具链（全部落地，本机母本在 collect/image_backfill/，已部署 sg1/sg2/cn1 ~/pan123-relay/）：
- `pan123.py`：新增 mkdir/dirid(幂等)/upload_file(md5 秒传+分片PUT+complete+异步轮询)/trash；端点 `/upload/v1/file/*`（create 用 parentFileID、mkdir 用 parentID 返回 dirID、trash 用 fileIDs——字段名不一致勿统一）
- `cos_relay_push.py`（sg 端）：环形扫描+毒闸门+分片(sha2%2)+sqlite 生产账本+流式 tar(单 packer 线程)+standalone 多分片 PUT+边车 `_meta/`+水位背压+崩溃恢复(成对收集 manifest/tar)
- `cos123_relay.py`（cn1 端）：消费 daemon（cos-internal 列举→下载边算 md5→对边车核验→123pan 镜像上传(同名冲突=trash 旧再传)→读回抽样→DELETE 前缀硬校验→账本+心跳 `_status/cn1.json`）
- 守护脚本 run_pan_relay.sh / run_pan_consume.sh（未启动）

试点实测（全绿，100% 读回全对）：docs 7 件全链；4.3GiB tar（2449 成员）全链 11 分钟。带宽：sg→GZ ≈16-20MB/s；cn1 内网下载 ≈35MB/s；**cn1↔123pan ≈8-10MB/s（单流，链路新瓶颈，30-40TB 全量需 ~1.5 月常跑或多流并行）**。123pan 同名拒绝（code=1）→可变文件必须先 trash 后传。
本会话修掉的坑：worker 停机排空被中途 stop 检查丢弃（单元一旦开始必做完）；sqlite check_same_thread；**recover 逐文件迭代会把已封 tar+manifest 当孤儿对删**（改配对收集，曾损失一个 4GiB 卷但零数据损失——账本最后落+SG 源不动救了）。

**现场已清理**（2026-09-20 21:40）：三机进程杀净、spool 清空、GZ pan123-relay/ 前缀 0 对象、123pan demiwtg-data 测试树已回收（根目录原有 5 项完好）、SG 源全程只读未动。三机代码+凭证留驻（重启即用）。

### 🔌 冷备链启动指引（2026-09-20 深夜交接：半夜开始跑，新窗口照此执行）

**启动顺序（三台机，各自 `~/pan123-relay/` 目录下）**：
1. cn1（**先起消费者**，心跳就位）：`nohup ./run_pan_consume.sh >> supervisor.log 2>&1 &`
2. sg1：`nohup ./run_pan_relay.sh 0/2 >> supervisor.log 2>&1 &`
3. sg2：`nohup ./run_pan_relay.sh 1/2 >> supervisor.log 2>&1 &`

默认参数即生产口径，无需改：四子树闭集（kb/raw/candidate/docs），blobs 时间闸门（last_modified≥2026-09-17，毒 blob 钉死在 09-12~14 不会进来），读回抽样 2%，盘预算 35G/机，GZ 积压 >50GB 或 cn1 心跳 stale>24h 自动停推，fleet 下载完全解耦零影响。

**⚠ 启动前置（必查）**：GZ `pan123-relay/` 若仍有对象（此前观察到 ~29 个），是并行会话 T5 端到端测试的在途数据——**先与对方确认 T5 收尾并清零队列再启动 cn1**，否则 cn1 会把这些对象当正常单元消费并删除，打断对方测试。

**启动后观察点**：
- GZ 心跳 `pan123-relay/_status/cn1.json`（backlog_bytes 应缓慢爬升不失控；41MB/s 消费 >30MB/s 灌入，常态近空）
- sg 日志：封卷/上传速率（实测 sg→GZ 16-20MB/s）、盘占用（60G 盘，35G 硬顶）
- 123pan 根下 `demiwtg-data/` 镜像树增长（cn1↔123pan 单流 ~8-10MB/s 是全链瓶颈：30-40TB 全量 ≈1.5 月常跑，急可后续加多流）
- 已知语义：123pan 同名拒绝（code=1）→ 可变文件自动 trash 旧再传；同内容秒传幂等；崩溃重启自愈（账本续跑）

**停止（如需）**：三机 `pkill -f 'run_pan_(relay|consume)[.]sh'; pkill -f 'cos_(relay_push|123_relay)[.]py'`——停 sg 后 GZ 队列自然不再增长，cn1 可继续排空。

### Lance 议题（Phase 0 已完成，实施状态唯一入口 = LANCE_PLAN §十五/尾巴验证节）
方案见 [LANCE_PLAN.md](LANCE_PLAN.md) 与 [总方案](../curation/layout/datasets_lance_migration_plan.md)。**Phase 0 全过**：边界 8/8；三表转换行数与历史值逐位一致（1.43亿/5332万/2547万）；SDC 关联链新旧字节级 md5 等价（全链 2.8-3.0x、s2 段 5.6x、点查复跑 ~2.5万倍）；COS S3 在线读写可用（虚拟主机风格端点组合）；T1-T5 尾巴全过（索引增益有限、免解压优势实证、有界 join 149s/6.1G、compaction/钉版本/tag保护/kill-9 全验证、**Lance 目录过中继往返恢复可打开**）。**COS Lance 发布 23:50 被用户叫停（未遂零写入），Phase 1 双写就绪、开工服从用户放行**。本地 spike 大数据已清，脚本/证据 JSON 留档 `_staging/lance_spike/`，env（pylance 12.0.0 + pyarrow 25.0.1）留驻。

### 收尾待办（按序，2026-09-20 22:45 复核）
1. ✅【已了结 22:40】旧系根树 439.7GB 全清：88,899 wm（本会话核验后精确删）+ 516,791 孤儿（用户拍板直删、亲自执行，"123pan 载荷/搁置"选项作废）。根树仅剩 ~250 个 0 字节目录 key（可顺手清可留）。kb/ 根 5 probe、meta 14 件此前已删；`datasets/demiwtg/{meta,pages}` 残件待查（若 meta/pages 前缀仍有对象，属收尾小活）
2. fleet 完→死信清扫（~千条）→最终并账→对账报告（工具齐：snap_collect + _staging/fleet202609 的 merge 输入）
3. 并账后：毒 blob 按引用计数清理（kb/blobs 内 790 万）
4. 小活：pages-en 30.8G 比对；wm 候选池丢失的 ~2.6 万差集反推；pages/ 空标记清扫
5. 挂起：第 2/3 批放行、缩略升级（等流量包）

### 资产位置（2026-09-20 定稿，22:45 增补）
- 并账输入/种子/键集/切片/删除清单：`_staging/fleet202609/`（7.8G+root_tree_keys；root_tree_keys.txt 与 root_tree_after_wm_del.txt 均为历史快照仅供对账，根树实况以现场 list 为准）
- 新沉淀运维脚本（collect/image_backfill/）：`del_keys_exact.py`（按清单逐 key 删，幂等 204/404，绝不前缀删，sg1 有副本）、`gz_purge.py`（cn1 内网前缀清空 list→DELETE 循环）；已有 cos_list/cos_util/relay_a/gz_stream/relay_b2/snap_collect 等
- 国内源终账：`_staging/fleet202609/domestic_final/`
- 3 个中间表（mid_to_file 等）：COS `datasets/candidate/wikimedia/`（权威）+ 本地 meta/
- 旧系 meta 14 件已从 COS 删（本地 meta/ 有全）
- 运维脚本（cos_list/relay_a/gz_stream/relay_b2/snap_collect 等）：`collect/image_backfill/`
- state/curation/image_backfill_full_v1/ 已被用户清空（损失：m 机明细账、wm 候选池——键级无损，详见会话记录）

---

## 一、两条数据体系（先分清这个，再谈一切）

| | **qid 系（新）** | **images 池（旧）** |
|---|---|---|
| 核心 | `qid_images.jsonl.gz` 主账本 886 万行 | `meta/images.jsonl` 索引 290 万行 / 216 万唯一 sha |
| 含义 | KB 概念(QID)↔Commons 图 | 概念搜索图池（bing/baidu/toutiao/huaban/so360/wikimedia_zh…） |
| 图片 | COS SG `kb/blobs/`（内容寻址） | 本机 `datasets/demiwtg/blobs/` ~207 万 |
| 归宿 | **COS 为权威**（本机 meta 留工作副本，md5 已对齐） | **本机为最全**（COS 残留待删、广州桶临时中转用完即清） |
| 两系关系 | sha 重合仅 2.1%；blob 内容寻址天然去重，同图只存一份 | |

## 二、任务全景（DOWNLOAD_PLAN 三大批次 + 池体系）

### 第 1 批：毒行重收 + 缩略升级 + wm 补图（进行中）
- **背景**：老采集器不查状态码，09-12~14 被 429 限速后 ~2.1KB 错误页 HTML 被当图写入，
  887 万行账本中毒 790 万（89.2%），真图仅 95.7 万；另有 34.1 万行 thumb1200 缩略降级存量。
- **毒行重收**：7,904,315 行 → 141 worker fleet（r1-r20），2026-09-20 尾段重切后 ~99 张/s。
- **缩略升级**：340,951 行大图（>10MB）原图重收，**待流量包**（每台 +250-350GB 会顶穿 1TB）。
- **wm 中文补图**：candidates_deduped 168,424 行已跑完（r 机 done 88,899 → COS）。

### 第 2 批：四源融合采图（清单已交付+审计，待放行 ~1,127 万张）
Met 5.7 万 / OpenImages 289 万 / Smithsonian 474 万行(329 万独立媒体) / iNat 358 万。
清单在 COS `kb/batch2/<源>/`；四源不打 wikimedia，无礼貌冲突。

### 第 3 批：SDC 新图（清单就绪，等决策 + 放行）
4,736,141 张 / img_size 20,973GB（原图口径，改缩略砍 ~90%）。
**与 WM 任务同打 wikimedia 必须全局串行**（等第 1 批收完）。

### images 池补全（本会话完成）
- 国内源 39,809 行本机跑完（done 26,652 + 死信 13,157，死站/CDN 换图不可恢复）
- wm 补充 88,899 张经 sg→广州→cn1→本机回流
- 池完整性终态 = 2,163,475 − 死信 18,026

### 四路融合（已入库，qid_images_ext/）
DF20 202,336 边 / PlantNet 275,200 / SDC 补挂 2,288,880 / PubChem 26,659；
钥匙表 concept_xref 2,547 万 / mid_to_file 1.43 亿 / sdc_depicts 5,332 万（COS candidate/wikimedia/）。

## 三、存储契约（2026-09-20 拆分定稿，详见 COS_BUCKETS.md）

```
SG 桶 lhcos-368f6（qid 系专属）
└── lhcos-data/demiwtg-data/
    ├── datasets/demiwtg/kb/{blobs, batch2, qid_images_ext, sdc_fetch, 主账本+概念层+pages语料}
    ├── datasets/raw/{df20,inat,plantnet300k,pubchem,metmuseum,openimages,smithsonian}
    ├── datasets/candidate/（中间产物）
    └── docs/（16 份历史文档）
    【待删】datasets/demiwtg/{blobs根树 60.6万/439.7GB, meta, pages} + kb/根 5 probe

本机（旧系专属）datasets/demiwtg/{blobs ~207万, meta(images.jsonl★+概念层+qid工作副本), corpus/, kb/blobs(毒blob待清)}
广州桶 lhcos-cee54 = 临时中转（回流核验后清空）

GZ 中转链（跨境免费通道）：SG桶→(sg1/sg2,15MB/s×2)→GZ桶→(cn1 cos-internal 内网免费 41MB/s)→本机(ssh 20MB/s)
```

## 四、机器拓扑（2026-09-20 现役）

| 机器 | 规格 | 角色 | 连接 |
|---|---|---|---|
| r1-r20 | 轻量 2c4G，1TB 流量包/台 | 第 1 批下载 fleet（141 worker） | ssh 直连（公网 IP，~/.ssh/config） |
| sg1/sg2 | 轻量 2c3G 60G，200Mbps **不限流量** | COS 中继（SG↔GZ 搬运） | lighthouse_key，走公司代理 |
| cn1 | CVM 广州 2c1G | 内网免费读 GZ 桶的桥墩 | cn_key，走公司代理，20MB/s |
| 本机(lake) | 192c，/yzp 80T 共享卷（曾 98% 满） | master/指挥 + 旧系家 | — |
| 已退役 | m1-m5（09-19，账本已归档）、p1-p5（09-17，资产在 archive/ 已并入 docs/）、CN 组 pipeline-e~i（09-10） | | |

**网络实测矩阵**（决策依据）：
- 本机→境外公网 ≈2MB/s（跨境墙）；公司代理单流 0.35MB/s；本机↔cn1 20MB/s
- sg 机→SG COS ~100MB/s；sg→GZ ~15MB/s/台；cn1→GZ 内网 41MB/s **免费**
- r9：09-20 重装换新实例（新公网出口 43.134.90.68；ssh 公钥仍=lighthouse_key 对 skey-60cw04nz，湖侧 known_hosts 需清旧 key）；旧条目"ssh 劣化"随旧实例作废
- 公司代理惩罚突发 CONNECT：全舰队并发建连会连坐断隧道——巡检顺序+sleep 错峰

## 五、Wikimedia 下载制度（实测科学）

- **每 IP 墙 ≈1.0 请求/s（60/分钟）**：rate.log 记录的是减半后值（实际撞墙=×2）；
  坐在 1.0 平均 1-3 分钟必被滑动窗口逮住 → 锯齿震荡
- **最优工作点 0.9 rps / 3 lanes**（AIMD：429 减半+60s 冷却，恢复 +0.05/20 成功）
- 文档 200/min 是 API 值，CDN 图片下载不适用
- UA 制度：1 IP↔1 合规学术 UA 终身绑定（ua_assign.tsv，池余 900+）
- 巨物（>64MiB）认缺：主账本无此档，需另行流式改造

## 六、运行手册（现行工具链）

**fleet**：`collect/image_backfill/` — `rebuild_fleet.py`（部署，幂等）、
`kb_backfill.py`（worker）、`kb_rate.py`（AIMD）、`fleet_monitor.py`（监控 60s/轮）。
部署参数 `--rps-start 0.9 --rps-max 0.9 --lanes 3`；重启三步序：**停 unit → 删 rate_state → rebuild**（顺序错=爬坡 2.6h）。

**队列时代（2026-09-20 13:30 起现行）**：任务=COS `queue/{batches,claims,done}`，
947 批 × 2000 行；`queue_worker.py --identity <w>` 认领→跑 kb_backfill→标 done，身份
（proxy/UA）来自 `/tmp/qw_env_<w>`（湖侧 hub 表生成，worker→host 映射=`checkpoints/hub/worker_hosts.json`）。
r 机完整依赖（重装必带齐）：`queue_worker.py kb_backfill.py kb_rate.py kb_transport.py cos_util.py fleet_curl.py fleet_dl.py`
+ `~/wk_backfill/.cos_creds`；**kb_backfill 动态加载 fleet_curl.py，且 r 机磁盘上 fleet_curl/fleet_dl
与湖侧有版本漂移——补机时以现役机的拷贝为准**。09-20 晚补丁（湖+全 fleet 磁盘已推）：
①kb_backfill 失败(rc≠0)绝不标 done、删 claim 释放、退避 60s 重试（旧版把 374 批假标 done，已全量清洗回收）；
②list_prefix 对 403/429/5xx 三次退避重试（新 IP 突发 LIST 会触发 COS 瞬时限频）。
**第 2 批就绪（09-20 晚，见 collect/batch2/B2_RUNBOOK.md）**：算子 b2_op.py（消费
demiflow cosio/cosqueue/queue_runner/exec_curl）+ 切批 cut_lists.py + 部署/发射脚本；
金丝雀湖侧全通（OI 19/20 blob 回读一致、Met 20/20 两段式全过）。**OI 直链已废**：
GCS openimages 桶收回公开读（403），改写 S3 镜像 open-images-dataset.s3.amazonaws.com
（同构路径匿名可读）；OI fetch_list 第二列=消歧后 Q 号数字部分（无 Q 前缀）。
队列 queue-b2-<src>/（对齐第 1 批布局，2000 行/批）；SI/iNat 队列已备但发射等流量包。
**B4/B5 v2（09-21 重排，见 B4_B5_PLAN.md）**：**口径变更——本轮起全 qid 挂载（不限 Wikipedia 条目）**。
B4=立即可下源：TMDB（key 已验 batch5/.tmdb_token）+ GBIF（EOL 平替，匿名 API 200 实测）；桥表前置
=Wikidata truthy dump 属性切片（P4947/P4983/P4985/P846/P3151），sg1 流式抽取进行中（不落地 90GB）。
B5=待注册暂缓：Mindat/Mapillary（匿名全 40x/400 实测）；EOL 降级可选。零下载元数据轨（WLM/OSM/GeoNames/
GADM/Getty）改并行基建不占批。NC 许可源独立分区 kb/blobs-nc/ + 账本 license_zone。
**源登记**：`kb/SOURCES.md`（COS 账本旁活文档，10 源类型/挂载/许可/限流实查）。
**第 1 批 09-20 深夜收官**（947/947 全 rc=0；假完成事故当晚清洗零丢失）；**第 2 批同夜上线**：
Met+OI 已发射 20 机（r 机 ~/wk_b2，启动器 launch_one_b2.sh——**ssh 只传 argv 不内联引号**，
内联转义断裂坑实战再犯一次后固化为文件）；OI 预计过夜跑完；SI/iNat 等流量包放行。
**平台化（09-20 晚已入库）**：第三代队列机制沉淀为 demiflow 引擎原语
`demiflow/collect/{cosio,cosqueue,queue_runner}.py`（签名三处一致性/瞬态退避重试/
成功才 complete/超龄认领回收/身份 KEY=VALUE env 注入；12 用例 + 全 suite 125 过 +
真 COS 只读冒烟过）。后续新采集线（如第 2 批 OI/Met）只写批算子+配置消费平台，
不再 fork queue_worker；存量 kb 线不动、跑完即弃。
**在线代码收编（09-20 晚完成）**：全 fleet 20 机 md5 扫描一致；在线原样快照
`image_backfill/fleet_running_20260920/`（14 件 + MANIFEST，补机以此为准）；
零散件补入 image_backfill/（launch_one.sh、repair_ledger、h2test、proxybench、
proxycheck、ab_referer、pan123.py）；中继四件套（cos_relay_push/run_pan_relay/
cos123_relay/run_pan_consume）湖机本就一致。fleet_curl/fleet_dl 湖侧为部署后
未下发的新版（路径迁移/UA 中央表），差异与口径见快照 MANIFEST。
**_staging 清理（09-20 晚）**：_staging 是湖侧数据暂存/留档区**不是代码区，勿整删**——
inat_local 46G（审计原料留档）、batch2_audit 432M（第 2 批审计证据）、benchmark/bagel
（训练数据）、demiwtg/_staging/fleet202609（**第 1 批并账输入，收官要用**）均须保留。
混入的代码已清：_staging/{sdc_fetch,raw} 为 collect 同名件的旧稿或重复（md5 核验后删），
第 2 批融合工具 13 件收编 `collect/batch2/`（fuse_*/met_fetch/link_bio/cos_* 等）；
backup_audit/ 是 demiwtg-data git bundle 备份，保留。

**永久死信 checkpoint**（已生效）：load_manifest_keys 跳过 404/not_image/over_cap，
重启重扫从 30-60 万请求降到 ~万。

**并账**（收官用）：collect 全机 manifest 成功行（`snap_collect.py`）→ 按 (qid,commons_file)
去重取最新 → join 主账本 → 出 done/dead_perm/dead_retry 三表 + 对账恒等式
`done + perm死 + retry死 + 未跑 ≡ 任务总数`。

**COS 操作**：`cos_util.py`（put/head/_call，env 换桶）；列举用 `/tmp/cos_list.py`（签名+分页）；
服务端复制 `PUT + x-cos-copy-source`；删除单 key DELETE。
SG 桶 IP 白名单制（本机签名 API 可用，匿名 403）。

**监控**：`/tmp/mon/fleet_monitor.log`（state 目录会被清理，勿放那）。

## 七、关键数字速查

| 项 | 值 |
|---|---|
| 主账本 | 8,861,355 行（orig 8,520,404 + thumb1200 340,951） |
| 第 1 批任务 | 7,904,315 毒行；2026-09-20 重切时剩 ~161 万 |
| images 池 | 2,899,895 行 / 2,163,475 唯一 sha；本机实存回流后 ~207 万 |
| 第 2/3 批 | 1,127 万 / 474 万（21TB 原图口径） |
| 融合账本 | 2,793,075 边（df20+plantnet+sdc_attach+pubchem） |
| COS 存储 | 5TB 已付费，超出 0.0056 元/GB/日（≈0.168 元/GB/月） |
| 待删空间 | 根树 439.7GB + 脏树已清 126GB + 毒 blob（并账后按引用计数） |

## 八、血泪坑全集（合并六份文档 + 本会话，按复发率排序）

1. **pkill/pgrep 自匹配自杀**（历史 6 次 + 本会话 4 次）：模式串出现在自己命令行即自杀。
   铁律：kill 与含目标名的命令拆开执行；或脚本文件方式；或按 `/proc/<pid>/exe` 精确杀。
2. **COS ETag 分片后缀**：`-6` 结尾 ≠ md5；内容比对必须 GET 后算。
3. **cosfs 三坑**：大文件静默截断（>100MB 分块+读回校验）；目录缓存延迟（存在性用 API HEAD）；
   刚上传其他节点不可见。
4. **双树前缀**：一切 COS key 带 `lhcos-data/`；桶根同名文件是历史误传副本。
5. **http.client 非线程安全**：多线程用 threading.local；节点 3-7GB 内存，千万行 dict 必 OOM（用 sqlite）。
6. **dump 解析**：JSON 数组行尾带逗号先 rstrip；bz2/gzip 单流只能顺序过；Wikimedia 用 dated 路径。
7. **代理纪律**：惩罚突发 CONNECT（顺序+错峰）；湖出口只适合少量长流（16 并行塌缩 2.3MB/s）。
8. **r9 慢 ssh**：数据走 COS 中转；探测用 `ssh r9 true` 而非直连 TCP。
9. **watcher 神秘自死**：保姆 supervisor 常驻；pod 重启带走常驻（setsid nohup 重拉）。
10. **匹配检查用读回**：cat|wc / 重算 hash；stat/大小相等 ≠ 内容正确（空档案骗局教训）。
11. **匿名 COS 限频 ~10 req/s/IP**；批删 403 单删 204（470 对象/秒）。
12. **下划线/空格**：page_title 下划线 vs commons_file 空格，join 前统一。
13. Commons API UA 联系方式必须完整格式邮箱（否则 WAF 403）。
14. `/tmp` 会被清理：产物、凭证、监控日志放家目录或 /tmp 要有自愈。

## 九、历史时间线（一分钟版）

- **09-05~07** 七机 searxng 概念搜索时代（SG 五机+CN 两机，44 引擎池）
- **09-08~10** lake_sync 增量回湖体系；CN 组五机释放，GZ 桶闲置
- **09-11~14** 概念 KB v6：25 机直写 COS，882 万图 + 语料 28GB + 账本双落；
  传输线（COS→湖）空档案事故（清单路径少 kb/ 段为主犯）→ 修复 → 82.02% 暂停
- **09-17** 毒行审计（89% 是 429 错误页）；p1-p5 释放（25 机重编 r1-r20）；
  四路融合入库；三张钥匙表；DOWNLOAD_PLAN 重构三批次
- **09-19** m 机 115 unit 迁 r 机（seed 修复 39.6 万漏收）；141 worker 扩容；
  限速科学（0.9 定档）；死信 checkpoint；持续监控
- **09-20** 国内源本机跑完；images 盘点闭合；两系拆分定稿；COS 大扫除（清 126GB+待清 440GB）；
  sg1/sg2+cn1+GZ 桶跨境免费通道建成；GZ 定位=临时中转；毒行收尾改 COS 队列制（947 批×2000 行，13:30 起跑，
  峰值 ~165 批/h ≈103 张/s）；r9 重装重部署（换新实例，公钥同 key）；假 done 事故（r9 缺 kb_transport，
  w8 以 rc=1 空标 374 批≈74.8 万行，一行未下）当晚清洗回收+补丁修复；wm 补图 fleet_curl 自然收尾归零

## 十、文档索引（历史文档已归档至 archive/）

| 文档 | 性质 | 时效 |
|---|---|---|
| **MEMORY.md（本文）** | 现行记忆总档 | 持续更新 |
| **COS_BUCKETS.md** | 桶登记+拆分设计权威版 | 现行 |
| **DOWNLOAD_PLAN.md** | 三批次活文档 | 现行（进度以本文/监控为准） |
| archive/HANDOVER_TRAINING_MACHINE.md | 09-17 总交接+四路融合+钥匙表 | 历史（数字仍准） |
| archive/HANDOFF_AUDIT_2026-09-17.md | 毒行审计方法论 | 历史（工具仍在 audit/） |
| HANDOFF_V2/V3、RESUME、SHIP_STATUS | 传输线事故与修复 | 历史（传输线已弃） |
| HANDOVER.md、NIGHT_HANDOFF.md | 七机搜索时代护航 | 历史（架构已退役） |
| archive/CONCEPT_KB_PLAN.md | 概念 KB v6 全景 | 历史（①~⑤已完成） |
| archive/SYNC_HANDOFF.md | lake_sync 增量回湖 | 历史（lake_sync 已停） |
| archive/DEPLOY.md | 25 机部署手册 | 历史（p 系已退役） |
| README.md | 审计工具链索引 | 现行（工具在 audit/） |

### 🌙 冷备链首夜护航日志（2026-09-21 00:19 起，新窗口执行）
**启动（00:21-00:25）**：前置查 GZ 队列 0 对象 ✅；坑：三机 run_pan_*.sh 无 +x（已 chmod）。cn1→sg1(0/2)→sg2(1/2) 顺序启动，全链 15 分钟贯通，首单元 md5 全对。
**首夜三事件（均已处置）**：
1. **123pan 单文件上限 10GB**（结构性）：inat/observations.csv.gz(12.71GiB)、photos.csv.gz(19.72GiB) 消费端死信（重试5次全拒）。SG 源完好未动；GZ 队列死信副本已删（204×4，backlog 恢复纯净）；cn1 ledger 预标 dead_oversize_pre 防后续空转。**待用户拍板 >10GB 文件的分卷方案**（sg 端 split 或 cn1 端 split+manifest，恢复时 cat）。
2. **sg→GZ 跨境带宽深夜塌方**：00:42 前正常（16-20MB/s，tar/大文件全过），01:10 起劣化，02:10 实测仅 0.15-0.24MB/s（16MB/110s）。multipart 64MB/256MB 分片均 3 连败死信（sg1 积压 part-0003~6、sg2 part-0001~3，共 ~30GB 在 spool 等待，35G 预算有界）。非代码问题，等链路自愈。
3. **cn1 盘满事件**：死信单元 21GB bin 未清理 + doomed 重试重下载 → 39G 盘 100% 满 → daemon 崩溃循环（sqlite WAL 初始化失败）。清 bin 后自愈（01:39:26 干净启动，账本 integrity ok）。**死信路径不清 bin 是已知缺陷**，护航中人工清。
**代码补丁（sg1/sg2，.bak.0921 备份）**：MULTIPART_TH 4GiB→256MB；tar 上传按大小路由 multipart；PART_SIZE 64→256MB。md5 完整性由 _meta 边车保证不受影响。母本（collect/image_backfill/）未同步——**次日需回母本**。
**requests 发送阻塞盲区**（已遇 2 次）：对端半死连接+零窗口时 PUT 的 send() 永久挂起（timeout 只管读）。症状：CPU≈0、线程 do_poll、连接 CLOSE-WAIT。处置：pkill python 由 supervisor 重拉（幂等恢复）。
**护航策略（至 08:30）**：巡逻脚本 pan123/patrol.sh 每 ~20 分钟（进程/心跳/盘/spool/死信）；每小时 4MB 探测带宽，≥5MB/s 才重启 sg daemon 冲刷死信 tar；其余保持低频不烧管道。

**首夜收官（08:00 复核）**：跨境链路整夜震荡（正常16-20MB/s ⇄ 塌到0.15-0.4MB/s），策略="塌方期保持(不烧管道)+探测≥5MB/s或连续≥3×2次即重启冲刷"，共执行 3 轮冲刷。08:00 战果：123pan 镜像 75+ 单元/12+ tar 卷(~52GB)；sg spool 各余 4 卷待链路窗口；cn1 侧零积压风险（GZ backlog 21.5GB 存粮可消化至中午）。**夜班人工干预记录**：chmod×3、杀 wedge 进程×4（sg1×2/cn1×2，requests 发送/读阻塞盲区）、cn1 盘满清理×1、GZ 死信对象清理×4、探测重启×3 轮、sg 代码热补丁×2 处×2 机。
**架构重构拍板（07:40 用户指令）**：平台机制沉淀 demiflow（relay.py/cosio 扩展/supervisor.py/pan123.py），业务策略放**独立同步模块 demiwtg/sync/**（不进 collect——同步≠采集），账本/队列格式不变保证断链可续，切机顺序 cn1→sg1→sg2 带 .bak 秒级回滚。重构须固化今晚三修复：multipart 阈值路由/256MB 分片/PUT 阻塞看门狗。

**重构交付（08:50，用户拍板"平台→demiflow、业务→独立同步模块"）**：新代码已离线落地并全测通过（demiflow 套件 138 passed + 新增 13 平台件单测），**生产三机仍跑首夜脚本未切**（切换手册见 demiwtg/sync/README.md：cn1→sg1→sg2、账本 schema 兼容已测、.bak 回滚）。落点：平台 `demiflow/demiflow/collect/{relay,pan123,supervisor}.py`+`cosio.py` 扩展（multipart 路由/256MB 片/流下载/list_entries）；业务 `demiwtg/sync/{config,push_sg,consume_cn1}.py`（策略单点 config.py）+ patrol.sh。母本回填：collect/image_backfill/ 的首夜热补丁（MULTIPART_TH/PART_SIZE/tar 路由）**未回母本**——母本已由新代码取代，勿再从旧母本部署。
**08:00 收班战果**：123pan 镜像 75 单元/12 tar 卷(~52GB)；sg spool 各 5 卷待窗口；cn1 消化 21.5GB backlog 中。护航 8.5h 干预 17 次全记录在案。

**123pan 慢窗口现象与"重连恢复"（09-21 10:00-10:10，待继续验证）**：账号上传 08:00 起骤降至 0.12MB/s（旧新客户端等慢、双 IP 均降、API/鉴权/通用带宽正常、存储节点 m8012 未变）；10:04 **re-login 换 token 后速率回升 ~6MB/s**，且规律=闲置后第一传慢（~1min/8MB）、随后满速。因果未定（恰逢其恢复 vs 会话级 QoS）。已按用户要求实现"慢速重连机制"：demiflow pan123 客户端首片测速 < 阈值 → re-login（换 token）+ 连接全释放 → 整文件重试，记录每次重置前后速率积累证据。工程对应：慢连接剔除 + 会话轮换 + 熔断重试。

**切生产实录（09:00-10:15 用户指令"切生产"）**：三机热切换至新栈（demiflow 平台 + demiwtg/sync 业务），账本/队列无缝续接。切换中修的四个坑（均已固化回母本）：① collect/__init__ 惰性 import（原 eagerly 拉 net→httpx，裸机部署炸）；② **config 桶域名必须带 -1256345599 appid 后缀**（裸桶名导致 sg 上传全灭 fail:24，教训：域名常量进 config 时要与现役脚本逐字 diff）；③ 看门狗 1800s + 批内 300s 心跳刷新（移植漏项）；④ 巡逻脚本进程名同步。运行时新行为：小单元优先消费（慢窗口产出保障）、supervisor rc=3 零退避。**新栈自愈首秀**：看门狗 30min 准点击穿慢上传 → supervisor 同秒重拉；sg 恢复重推真传 13MB/s。当前外部约束：123pan 慢窗口（~0.14MB/s，10MB/80s 实测，功能正常纯慢），tar 卷 30min 看门狗边界化重试，backlog 73GB 堆在 GZ（闸门 A 已停 sg 推送，全有界）。残留排查项：cn1 supervisor 曾被外部 SIGTERM（09:19，疑 logind 会话清理，已 setsid 脱离；巡逻 consume进程=0 即抓）。

**慢速重连机制 v3 上线（10:42）**：Pan123Client 首片测速(仅 ≥64MB 大文件) <0.5MB/s → re-login 换 token 整文件重来(≤2 次) → 仍慢抛 Pan123SlowPath；消费端接住=单元留队歇 5 分钟再战（不烧死信额度不磨看门狗）。小文件免判定慢窗口照传。已知残余缺口：判定时刻窗口好、传一半窗口关——看门狗 30min 兜底（可接受）。全天 123pan 呈窗口式劣化（08:00-10:04 深慢、10:04-10:07 短恢复、10:1x 起再深慢 0.04MB/s），客服否认账号级限速。链路对策齐备：小单元优先+慢让路+看门狗+50GB 闸门，窗口打开即自动放量。

**用户热修并入（11:10）**：用户亲自在母本+线上加了 123pan 慢路径机制——`Pan123SlowPath`（≥64MB 文件分片重置后仍 ≤130KB/s 级别则提前弃传）+ 消费端"留队 5 分钟再战"退避（不烧死信额度不磨看门狗），比看门狗钝杀优雅。母本已与线上一致（diff 空），demiflow 回归 139 passed。此前 ~24min 节律的 SIGTERM 即用户会话（116.238.240.2）重启加载热修所致，supervisor 全部自动兜住。

**坑#1 新变体（11:30）**：`pkill -f "sync/consume_cn1"` 会连 supervisor 一起杀（supervisor argv 内嵌子命令串）。正确姿势：行首锚定 `pkill -f "^python3 sync/consume_cn1"`（supervisor 以 `python3 -m` 开头不匹配）。已入巡逻 SOP。

**进程被 -15 神秘杀死根因+修复（11:35）**：cn1 无 Linger 时，长 sleep 无 ssh 窗口→最后会话关闭→logind 停 user manager→cgroup 全体 SIGTERM（supervisor 连带 child）。已三机 `sudo loginctl enable-linger ubuntu`（Linger=yes 验证）。**教训进坑全集：无头常驻进程必须开 linger，否则死给你看还以为是被谁针对。**

**缺陷③移植修复（12:00 v4，源自并行会话通报）**：consume_cn1 的 done 跳过改 md5 感知——同路径重推时，边车 md5 与账本一致=真重复仅补删；**不一致=新版本，删旧传新（同名冲突 trash 旧再传）**。可变文件刷新（主账本/概念层 etag 变化重推）从此不丢数据。ConsumeLedger.get 返回 (state, md5)；Pan123Client 新增 find_by_name。demiflow 145 全绿。存量 done 项均为首传不可变内容，无需回溯对账；边车 md5 对账逻辑已内建于新代码。

**123pan 慢因双杀突破（19:35 实证）**：①存储簇 m8xxx.123624.com（共享 123.184.218.0/24）个别后端毒（.110 实测 0.12MB/s vs 其余 3-5.6MB/s）→ 客户端级 getaddrinfo 坏IP过滤（install_upload_ip_filter，hosts 钉不住多簇轮询）；②**冷启动按目标目录隔离**：每目录首传 ~0.1MB/s、随后 ~10MB/s（全新目录对照实验坐实）→ 大文件上传前同目录 256KB 暖场（每目录 30min 一次）。三件套（过滤+暖场+workers=2 并行单元）后：**聚合 ~13.7MB/s（单 worker 6.8），双倍旧天花板**。"间歇性慢窗口"的机理=重启后首单元撞冷目录/毒 IP。QPS 提到 10 为并行铺路。工程命名：慢连接剔除+会话预热+并发上传。

**下午护航要点（12:00-20:30）**：① 用户继续热修：暖场预热（进程首传/闲置后首传被服务端压 ~0.1MB/s，暖场探测吸收惩罚——白天的"慢窗口"实为冷启动压制）+ 已同步母本；② 我方补丁：瞬态失败不再永久死信（留队下轮再战，防镜像留洞）；慢路径弃单元即删 bin（防 17×4.3G 憋爆 39G 盘）；③ pkill 新变体入 SOP：`^python3 sync/consume_cn1` 行首锚定防连杀 supervisor；④ 闸门锯齿第二轮运转正常（55.9GB 触发停推→排空）。20:30 战果：新栈镜像 138 单元/41 tar 卷（含 manifest），全链零数据丢失。

**冷启动模型修正（23:50）**：暖场/过滤后单进程首传仍慢、随后即 5-6MB/s——冷启动至少部分是**进程级首传**（目录级实验无法区分两者，_warmup 目录暖场不总有效）。夜间不稳窗口主害转为 SSL UNEXPECTED_EOF 掐断（大传输在途时间长易中弹，与小文件畅通并存）。三件套在稳窗口仍双倍吞吐；不稳窗口由慢路径+看门狗+留队轮转兜住。待办：暖场改"进程首传后即视为热"或上传前 1MB 试传探温。

## 2026-09-21 傍晚~夜：B4 备齐 + 夜巡自动化（护航会话）

**b3 速率真相（16:45-17:00 实测）**：全网 **~84 行/s（manifest 口径，19 机 10min 窗口）**，单 worker 0.58-0.65 行/s 连续零空转（r1 各 worker 2h 无 >45s 间隙）。昨天 96.7 → 今天 ~87%，正常。**队列 done 计数口径偏低（曾误测 38 行/s）——done 增长慢于 manifest 是测量噪声，判断吞吐一律用 manifest fetched_at 窗口统计**。批次均单次认领无回收重跑。死信大头 http:400（10min 内 ~2%）+ http:404/not_image 永久类。

**sg1 桥表管线（v5 终版）**：交接时的"修复重启"没杀旧管线（孤儿写已删 inode 不污染但占带宽）；流式 curl -m 14400 必超时重零。终版 `/tmp/wd_extract5.sh`：8 路 range 分段（Wikimedia CDN 单 IP 并发 range ~3-4 路稳 5MB/s/路）+ `--speed-limit 10240 --speed-time 60` 停滞检测 + 分段 append 续传；到齐后 `cat parts | bzip2 -d | tee >(grep 桥A) | grep 桥B`。**双桥**：wd_b4_bridge.nt（P4947/P4983/P4985/P846/P3151）+ wd_meta_bridge.nt（P1566/P245/P1014/P1667/P1435/P6265/P830/P225，元数据五件套+B5 一次切齐免二次下载 43.5GB）。坑：pkill -f 匹配到自身 ssh 命令行会自杀，杀进程必须按 PID。

**P3151 纠偏（金丝雀逮到）**：P3151 = iNaturalist taxon ID（非 GBIF occurrence！），其图归 b2-iNat 线；B4 GBIF 桥仅 P846。随机 P846 物种 GBIF 带图率 ~35%（冷门物种 count=0 属数据现实，no_media 死信 50-65% 是预期不是故障）。计划文档已改。

**B4 资产（batch4/）**：`b4_op.py`（对齐 b2 架构：qr_run 队列模式 + --tasks 金丝雀直跑；TMDB 两段式 Bearer API→image.tmdb.org 原图，CC BY-NC 进 `kb/blobs-nc/` + license_zone=nc；GBIF species/media 优先、空则 occurrence 搜索回落，≤3 图/物种；finished.jsonl 任务行终局标记防部分成功重跑）；token 文件含标签+中文备注，正则只取 JWT（否则 latin-1 头炸）。金丝雀：TMDB 8/10、GBIF 4/10 全通。`cut_b4_queues.py`（桥表→queue-b4-tmdb/queue-b4-gbif，produce 幂等）、`deploy_b4_fleet.sh`（vendor 五件套+凭证+每机 3tmdb+3gbif worker）、`launch_one_b4.sh`。规模预估：TMDB ~78 万实体行 / GBIF ~365 万行 / 首期图 ~400-500 万张。

**夜巡自动化（用户令：持续护航 24h，b3 下载完→启动 b4）**：CronCreate automation-4621167b（每 20min × 72 轮），手册 `PATROL_RUNBOOK_0921.md`（固定巡检+阶段 A 桥表落地/B 切队列/C b3 收口自动发射 B4/D B4 护航+排障+晨报），日志 /tmp/patrol_0921.log。红线：不动在跑进程/不删 COS/精确 PID/b3 1280→1920 升级 pass（清 manifest 1280 行重下 ~25 万行）夜间只记待办。

**ETA（16:51 UTC 测）**：b3 ~21:10-22:30、inat ~24:00、si ~00:15 UTC 收口；桥表 EXTRACT_DONE ~17:55。

**B4 完整口径 = 7 项**(用户提醒补齐):2 图源(TMDB/GBIF,夜巡阶段 B/C)+ 5 元数据 dump(GeoNames/GADM/Getty 三小件=夜巡阶段 E,sg1 下载→COS kb/meta_dumps/;OSM 分洲串行=白天阶段 F,sg1 盘装不下 planet 全量;WLM 待人工确认 Toolforge 访问)。桥属性 P1566/P245/P1014/P1667/P1435 已在 wd_meta_bridge.nt。巡逻顺序:A→B→E 先行,C 等 b3 收口不挡 E。

## 2026-09-22 凌晨：冷备链夜班续护 + 看门狗喂狗补丁（本会话）

**夜巡实录（23:47-04:15 本地，~10 分钟巡逻循环）**：① 坏窗口 21:30-00:54（111-140KB/s 慢路径+SSL EOF）：机制全部按设计待命（留队退避、看门狗 rc=3 自愈、bin 弃传即删，cn1 盘 38-70% 有界震荡）；② **00:54-01:30 突发好窗口**：8 tar≈34GB/36min（~16MB/s 聚合），155→163 单元/44→52 卷；闸门第三轮锯齿确认（backlog 34.45→55.94GB 穿 50GB 线，sg2 恢复推卷）；③ sg1 盘 84% 真相=B4 会话的 Wikidata dump 8 路分段下载（~/wd_parts 8×5.43G，02:00 落齐未再涨）——非分卷方案，勿动；若闸门重开 sg1 需 35G tar 预算会挤盘，需协调。④ cn1 bin 积累模式：看门狗击穿的化身各留一对 bin（内容寻址可复用不重下），最坏积至全队列数，干预线 spool>24G 或盘>80%。

**边缘速度段摺磨根因+修复（04:15 部署 cn1，.bak.0922 可回滚）**：01:30 后 ~2.3MB/s 段，4.3G tar 总耗时恰越 1800s 单元死线 → 看门狗每 30min 击穿一对"近完成"传输（01:54/02:24/02:54/03:24 四连击，纯烧带宽零产出）。修复三件：relay.py `Watchdog.feed(min_gap_s=60)`（进度续期死线，零进展照杀——兜底语义不变）；pan123.py `upload_file(..., on_progress=rate)`（每片 PUT 成功后回调，回调抛错吞掉）；consume_cn1.py `_feed` 门控（**片速率 ≥ min_slice_rate 才喂**：稳态慢传 ~31min 自然传完；塌到阈值下停喂 → 30min 内仍换路，保慢路径自适应性）。demiflow 234 passed（新增 4 测：喂狗续期/节流/片级进度/回调容错）。零接触部署：supervisor 下次重拉自动加载，无需 pkill。**sg 侧同病未修**：push_sg.py 三处 `with Watchdog` 包下载+COS 上传无喂狗（sg2 03:10 同被摺磨），补丁需 cosio.put_smart 进度钩子，另议。

**喂狗补丁验证+0921 积压全清（04:27-07:14）**：新码经看门狗自然重拉加载后，边缘速度段（~1.6-1.7MB/s/路，旧码下 4.3G tar 数学上不可能 30min 完成）连续 6 对自然传完、零击穿（拉起数钉在 35）——修复前 3h 零产出 vs 修复后 2h50m 传 15 卷 ≈64GB（含晨间突发 ~14MB/s，被摺磨整夜的 part_000020/21/27/28/29/30/31/34 全数落地）。**07:14 backlog 4.30GB=0921 积压清空**。随后链路全面转好：sg1 盘 84%→23%（B4 会话 rm -rf wd_parts 释放 43G，挤盘风险自解）、sg1/sg2 双推恢复、blobs-nc 图片单元洪峰过链（07:47 达 627 单元、+283/10min，B4 TMDB 产物镜像中），backlog 4.4GB 动态平衡。sg 死信整夜稳定 36/37 行。三文件母本=cn1 线上 md5 一致，备份 `.bak.0922`。

**sg 侧同病同修（10:50 部署 sg1+sg2，.bak.0922）**：sg1 push 10:24 又被 30min 死线击穿（part_000048 在库 1h 重推）→ 同款喂狗：cosio.py `put_multipart/put_smart(on_progress=rate)`（每片成功 PUT 后回调，回调抛错吞掉）、push_sg.py `_push_tar_unit`/`_push_file_unit` 两处 wd.feed()（**COS 腿无会话轮换逃生门，按片完成无条件喂**——与 123pan 侧 rate 门控的差异是有意为之）。demiflow 235 passed（新增 cosio 片进度+回调容错测试）。**注意**：母本 cosio.py 比线上多 extra_headers 特性（If-None-Match 条件头，9-21 凌晨加未部署），本次一并部署 sg（向后兼容）；cn1 cosio 未动（consume 不走 put_smart）。零接触：sg push 下次看门狗重拉自动加载。

**下午主动护航战果（15:30）**：① **死信审计零缺口**：sg 死信 36/37 行全为 tar 单元（首夜塌方 3 连败中间态，含重复行），去重 31 卷（sg1 VM-12-3 的 3-18 + sg2 VM-12-13 的 1-19）逐一 grep cn1 日志核对——**全部最终被消费**（崩溃恢复/恢复重推机制事后补齐），镜像无洞，死信文件仅历史记录。② 暖场撞名修复：`_warmup_{ts}_{rand24}.bin` 随机后缀消双 worker 同秒撞名噪音（22 passed，已部署 cn1 md5=286cfca9）。③ 观察确认：喂狗后 cn1 拉起钉在 35、sg 钉在 16/15（10:24 后零摺磨）；13:16 慢路径判定（255KB/s）正确留队=rate 门控按设计工作。

## 2026-09-22 傍晚：123pan 冷备链退役（用户拍板）

**决策过程**：用户质疑可行性——40.3h 仅传 342.4GB（全程均速 ~2.4MB/s，含坏窗口；30TB 需 ~143 天，理想窗口也要 23 天）。单账号单流 2-8MB/s、双 worker 聚合封顶 ~20MB/s 为硬天花板。用户先考虑"换冷备方案（物理盘/Glacier）"，最终拍板：**算了，停掉，退机**（sg1/sg2/cn1 全退）。

**退役执行（16:53-17:10）**：① 三台 relay 守护进程干净停止（supervisor 先杀再杀子进程，行首锚定，残留 0，退出码 -15）；② 状态归档到湖 `demiwtg/sync/retired-0922/{sg1,sg2,cn1}.tar.gz`（11M/8.8M/2.9M：账本/日志/死信/代码，排除凭证与 bin）；③ GZ 队列桶 pan123-relay/ 前缀清空（22829 对象/12.7GB，全是 COS-SG 源副本，零损失；cosio.delete 返回 None 是幂等设计勿误判失败）；④ 机器退还由用户控制台执行。

**终态资产**：123pan 镜像保留 **10796 单元 / 79 tar 卷 / 342.4GB**（demiwtg-data 树，账本快照在归档 cn1.tar.gz）；COS-SG lhcos-data 源树完好未动（全量唯一权威副本）；demiflow 平台+sync 业务母本在湖（含全部喂狗补丁，235 测试绿）。

**⚠ 残余风险（退役后必须知晓）**：① **kb 源树在 COS-SG 成唯一副本，无任何异地备份**——若需冷备，物理盘（3×16T 一次性 ~4000 元）或 Glacier 级存储（~0.012-0.05 元/GB/月）是后续选项；② GZ 桶 lhcos-cee54 清空后仅剩 43B 演示文件，可在控制台整桶删除；③ 123pan 342GB 若不再续可用同样思路处理（账本可对账可清点）；④ 25 机 fleet/B4 采集线**不受退役影响**（直写 COS-SG）；湖盘 80T PVC 已用 98%（剩 2T），B4 产出持续增长需另行规划。



## 2026-09-21 夜:三线收官 + B4 发射(在会话值守,cron 调度器确认失效 runCount=0)

**收官**:inat 21:01 UTC(1793/1793)、si 21:39(1647/1647)、b3 一轮 2368/2369+升级批 b003000-26 全完(约 21:55-22:50,用户/系统所灌);尾批 b001938 两度卡死均因 r19-w58 专属代理 23.142.180.8 黑洞(连接挂起每行烧满超时,manifest 冻结 45 分钟零产出)——处置=杀 w58 树+精确删 claims/b001938(**勿全局 requeue_stale,会误伤活跃认领**)。

**桥表终局(22:25 UTC)**:EXTRACT_DONE **b4=4,897,790 / meta=12,096,056 行**。历程三折:①sg1 突发型实例 CPU 积分耗尽,bzip2 2.6MB/s;②湖侧拉取 scp 直连仅 0.4MB/s→COS 中转(sg1 同区上传 48MB/s→湖侧 24 路 ranged ~10MB/s,湖容器 cgroup 32 核配额、单线程 bzip2 也只 3MB/s→**lbzip2 -n 24 是正解,~20 分钟解完 43.5GB**);③**误删事故**:杀管线成员未先杀父 bash,grep 自然排空退出 0 使 `&&` 链继续跑完 EXTRACT_DONE+`rm -rf wd_parts`——教训:**杀链先杀父;清理步骤必须挂在校验之后**(B4≥200万行才许删)。COS kb/wd_parts/ 8 段是救命副本(总和精确 43458740693)。

**B4 发射(22:30-22:50)**:桥表→COS kb/wikidata_bridges/(gz);cut_b4_queues 切出 **tmdb 706,451 实体(movie 284,522/tv 63,436/person 358,493)=354 批、gbif 3,322,754 物种=1,662 批**;deploy_b4_fleet 20 机×(3tmdb+3gbif)全起。验证:TMDB ~15 分钟 53/354 批(~106K 行),GBIF 在 api.gbif.org 偶发 429 下仍 ~0.8 行/s/worker(TransientFetchError→批回队列语义吸收,账本正常增长)。

**OSM**:改 COS 中转(sg1 cos_put.py 上传→湖侧 download_to;scp 死路 0.4MB/s);22:40 起按洲跑通(antarctica/central-america/australia-oceania/south-america 在途),产物→kb/meta_dumps/osm/。

**值守模式**:用户令不退出对话,sleep→巡检循环。B4 资产在 collect/batch4/(b4_op.py/cut_b4_queues.py/deploy_b4_fleet.sh/launch_one_b4.sh + canary_*.jsonl)。

## 2026-09-22 晨:GBIF 包成+湖侧保底链判死(值守会话)

**GBIF download SUCCEEDED**:key 0000975-260921141020460,**3.14 亿条/164.5GB**(DOI 10.15468/dl.a68pw6,聚合许可 CC BY-NC)。湖侧→GBIF 实测 0.08MB/s 不可用,**接收必须在 SG 机**(并行会话已调度新 SG 机 8-16核/200-300G)。gbifdl 接收管线就绪:b4_op.py 已加 gbifdl 直连分支(金丝雀✓,404/400 死信路径✓)、gbif_dl_pipeline.py(DWCA 解析→P846 过滤→每物种≤3 图→切 queue-b4-gbifdl,按媒体许可分 nc 区)。

**湖侧 wd_full 保底链判死**:latest-all.json.gz 装配后尺寸精确但 **pigz crc32 mismatch,解压在 ~457 万条截断**(20 路装配存在空洞;或 COS parts 源即坏)。两次解析同点卡死实锤。残缺 sitelink_qids(283万)已从 COS 删除。**权威路径=SG 机重新拉 COS parts→重装→CRC 校验→解析**;若 SG 重装仍 crc 错,则 COS parts 源头即坏需重传。湖侧 latest-all.json.gz(156G)判废可删。
坑:`python - <<EOF` 管道+heredoc 同抢 stdin,heredoc 胜出管道断流——解析器必须落文件跑;里程碑 print 别与数据同走 stdout(污染输出文件)。

**GBIF zip 下载器已备**:/root/gbif_zip_pull.sh(24 路 Range 分段+断点续传+装配校验,SG 机可用;单流 206 验证✓)。

## 2026-09-22 晨:全量收官与三大新增线(B4 发射后的下半夜,sgx 新机时代)

### 终局快照(11:00 UTC 交接点)
- **图片总账**:b1+b3 fleet manifests 成功行 **22,942,363**(总行 9993 万含重试/毒行);b2 四源 ~500 万;TMDB 706,451 → 全库 ~2800 万+
- **b3x 差集大军**:862,768 图/570,212 无条目 qid 已切 482 批(queue-b3x),141 worker 在吃(9/482 done)——**bid 偏移坑:produce 每 flush 默认 start_index=0,skip_existing 会把后续 flush 全跳过,必须累计偏移**(第一版只进了 50 批,v5 修复)
- **GBIF**:download key `0000975-260921141020460`(3.14亿条 StillImage,164.5GB zip,账号 mengdebin);API 滴灌 queue-b4-gbif 1662 批重启中(20机×1worker lanes2+节拍器);zip 拉包被 429 深度反爬 → px4 过夜节奏(45分/片,~53/320)
- **sgx 链全就位**:片到 320 自动装配→zipcd 流式解析 occurrence/multimedia→按 P846 桥过滤每物种≤3图→produce queue-b4-gbifdl→重发 worker

### 关键机器:sgx(用户 09-22 晨新开)
- 公网 43.156.134.91 / 内网 10.3.8.9(与 r 机同 VPC);**湖直连其公网 22 被安全组挡,必须 `ProxyJump r1`+内网 IP**;密钥=lighthouse_key;16核/30G RAM/443G盘/Python3.14
- **sgx←COS 同区 180MB/s**(湖跨境仅 3-5MB/s)——一切大转运/解析都在 sgx 做(用户拍板:湖网络慢就不在湖处理)
- 环境:~/demiflow_collect(vendor 5件,cosio 可顶层 import,cosqueue 必须包上下文 `demiflow_collect.cosqueue`)、~/.cos_creds、pigz 2.8、assemble_wd.py(分段装配器)、sgx_gbif_chain.sh+sgx_gbif_parse.py(GBIF zip 接收全链)、sgx_diffset.py(差集漏斗 v5)、sitelink 资产在 ~/wd_full/

### sitelink 全集(差集前置)——生产级资产
- **kb/wikidata_bridges/sitelink_qids.txt.gz = 38,414,271 唯一 qid**(2026-09 版 full dump latest-all.json.gz 156GB)
- 生产链:r 机 20 台切片(fleet_slice.sh,512MB 片→COS kb/wd_full/parts/ 300片)→sgx 180MB/s 装配→grep 管线抽取→sort -u 去重
- **三坑**:①python3 - 的 heredoc 会把代码喂进 stdin,数据零行(用独立 .py 文件或纯 grep 管线);②抽取行含重复(370M 行→38.4M uniq,集合语义无妨但统计要 sort -u);③COS 上传版本可能滞后,消费方读文件前先验行数

### OSM 收官(8/8 洲)
- geofabrik 对 sg1 深度限速(拉过 ~50GB 后 0.04-1.5MB/s,代理无效=按IP不按连接)→ 北美 19.4GB+欧洲 35GB 改 **r 机 12 台分工切片(url_slice.sh→COS kb/osm_parts/)→湖/sgx 装配**;两洲装配器 e[0] 字符串坑(条目是 str 不是 tuple,`e[0]` 取成首字母"l")
- 产物:kb/meta_dumps/osm/<洲>-wikidata.osm.pbf 8 件共 4.07GB(osmium tags-filter w n r wikidata)
- 元数据五件全齐:GeoNames(allCountries 422MB+admin2+countryInfo)、Getty×3(aat 240MB/tgn 1.9G/ulan 608MB,走 aatdownloads.getty.edu 归档;vocab.getty.edu 屏蔽 sg1 但湖可访问)、GADM 2.68GB(gadm_410-levels.zip,ucdavis 会断流,**必须 curl -C - 断点续传**否则每次从头)

### GBIF 反爬实录(教训密集)
- 3.14亿条包 164.5GB;api.gbif.org 302→occurrence-download.gbif.org(206+range 支持)
- 直连/代理(注意:**qw_env 文件 UA 带括号,source 会语法错,代理要用 `grep -oP '(?<=^KBP_PROXY=).*'` 解析**)/20机并行/16路分段全部触发 429;冷却 ~50 分钟恢复但拉几 GB 又封;**当前唯一可行=极温和节奏(px4: 每片 512MB 后睡 45 分钟,轮换身份)**→过夜磨完
- **加速选项(未做)**:全新 IP 境外 VPS 一小时拉完直传 sgx;或 zip 中央目录 trick(读 EOCD 跳过 verbatim.txt 只拉 occurrence+multimedia 字节区间,可省约一半)
- 滴灌线(API 逐物种):queue-b4-gbif 1662 批;b4_op 带 API 节拍器(0.83 req/s/worker)+行内子图串行是瓶颈;GBIF 代理限流下 20 worker × lanes2 是稳态

### b4_op 最终版能力(batch4/)
- src:tmdb(两段式 Bearer)/gbif(API 两路)/gbifdl(URL 直收,download 路线);--tasks 金丝雀直跑;finished.jsonl 行级终局;API 节拍器;token 文件含标签要正则取 JWT
- **湖侧尚未把 gbifdl 分支重部署到 r 机**(fleet 上还是旧版)!GBIF 链产出队列前必须:`scp batch4/b4_op.py 各r机:~/wk_b4/` + launch_one_b4.sh gbifdl
- 金丝雀账本在 /root/wk_b4_c4 等

### 其他
- r 机 b3_manifests/r*.jsonl.gz(每机~300MB gz)= b1+b3 全量账本镜像(已上 COS,并账用)
- 湖侧装配链/拉取器脚本:/root/cos_pull_parallel.py、assemble_wd.py、pull3/pull4 系列(历史产物,湖网慢已弃用,sgx 是唯一大数据通道)
- b001938 毒尾 126 行(大 GIF 1920px 渲染限流,换 IP 也 429)→补采时降档 1280px
- 值守 cron 全失效(runCount=0),纯人工 sleep→巡检循环

### COS qid 图领域分布盘点（2026-09-22 湖侧实测,补上 09-21 遗留问题）
- **kb/blobs ≈ 2590 万张 / 27.8TB**（24/256 sha 子前缀抽样外推,±0.7%;jpg 89%/png 5%/svg 4%;全量 LIST 仍可断点续跑:/tmp/qid_scan/blobs_inv3.py,页文件在 inv_pages/）
- 各域在库:主账本 wm 881 万(覆盖率 99.9%,账本 8,861,355 行/561 万 qid/882.3 万唯一图)/SDC 线 487 万任务收官(sdc_attach 账本 2,288,880 边/94.9 万 qid/174.9 万图,99.8% 在库;其余为无条目 qid 的 sdc 图)/si 329 万/b2-inat 359 万/b2-oi 289 万/b2-met 5.7 万——**b2 四源队列全部 done**(含此前以为未放行的 oi/met);tmdb 70.8 万进 blobs-nc;gbif 1662 批刚起步(19 认领 0 完成);b3x 差集 482 批进行中(done 51→97+ 活跃)
- **df20/plantnet/pubchem 账本图 0 张在 kb/blobs**——原始数据在 datasets/raw/ 归档(df20 116.8GB/plantnet 31.7GB/pubchem 8.9GB),如需入 blobs 要解包materialize
- 未归因孤儿 ≈ 200-400 万(缩略降级存量 34 万+wm 补图 8.9 万+在途差集+老线残留;精确拆分需 b3_manifests 20 台并账,r1 单机 591 万行/成功唯一 sha 90 万可复算)
- 湖侧 COS 操作要点:LIST 必须签名(匿名 403),q-url-param-list 用分号连接;并发 LIST ≤16 路否则触发限频挂死;大文件 GET 用 12 路 Range 分段(~2MB/s 聚合);凭据 /tmp/cos_creds(从 image_backfill/.cos_creds 拷)
- 明细产物:/tmp/qid_scan/(域账本 dl/、分析 analyze2.py、队列计数);qid_images_ext 实为 4 个域文件+3 个 sdc_fetch parts 零头(2,215 行),不是 9 个域

## 2026-09-22 中午:GBIF 全线停(用户拍板)+ 停线前最后一轮工程(本会话)

**用户指令(12:26 UTC):"我们生物图太多了,停掉GBIF吧"。即刻执行,全链停净:**
- r1-r20:px5 拉片树(先杀父 bash 再清孤儿 curl)+ gbif 滴灌 worker(--src gbif)全杀;**b3x worker 每机 14 个未动**;partial *.bin 清理
- sgx:chain2+waiter 杀净(无装配产物);sparse_test 临时件清理
- 未停/未动:TMDB(354/354 已收官)、b3x 差集线(306/482 冲刺中)、COS 全部数据
- 遗留资产待用户拍板:COS `kb/osm_parts/gbif/` 61 片 ~31GB(53 旧 m* 网格+8 新 g* 网格,可整删);queue-b4-gbif 1662 批 idle(0 done);GBIF download key 0000975-260921141020460(GBIF 侧保留数月,可随时重启)

**停线前最后一轮工程(全部归档 `collect/batch4/gbif_zip_route/`,README 有重启手册):**
1. zipcd 成员表到手(修了 zip64 locator 定位 + struct 少一 Q 两坑):occ 84.5G / **verbatim 57.3G 不需要** / mm 22.6G → 选择性方案 201 片/102.4GB,省 38%,ETA 12.8h→8.8h
2. **稀疏 zip 实证通过**:全尺寸稀疏文件(挖 verbatim 洞)+ `unzip -p` 正常提取(sgx 实测 rights.txt)
3. px5 拉片器(全局网格+COS 跳过免睡+429 无限梯子不换片+每片轮换身份+3 pass 自补洞)20 机滚动部署完成,停线时 8/201 片
4. **逮到并修了三个真 bug**:①原 sgx_gbif_parse.py produce bid 偏移坑(每次 flush start_index=0+skip_existing → 全队列只产第一批;parse2 累计 bid_off);②parse 全量 gid2tk 会 OOM(pass1 即按物种 cap 3 gid);③**b4_op _store_image 毒行坑**(死图 URL retries_exhausted 被归瞬态→炸批→回队列→同死链再炸,批永远完不成——gbif 滴灌 0 done/1662 的根因;湖侧母本已修,线上 r 机未部署,gbif worker 已停无影响;**任何未来图源线用 b4_op 前此修复已在母本**)
5. 429 反爬再实证:10:40 的 20 机同时重启脉冲把代理池重新烧热(r1 实测 429),错峰起跑(60s 递增)+温和梯子后部分机器数分钟内恢复下载;api.gbif.org 与 occurrence-download.gbif.org 限流相互独立(API 直连一直 200)

**值守状态(b3x 继续)**:306/482 @12:28 UTC(~100 批/h,ETA ~14:45 UTC)→ 收官后死信统计+待补采记录。b4 剩余源:无(TMDB 完,GBIF 停);B5 待注册源不变。

### 三域重灌入库(2026-09-22 晚,用户令)
- **df20/plantnet/pubchem 503,196 张全部重灌 kb/blobs 完成**(df20 202,336 / plantnet 275,200 / pubchem 26,659,与账本分毫不差,sha 校验零失败,抽样 HEAD 750/750=100%)
- 背案:09-17 四路融合把 blob 上到旧路径 datasets/demiwtg/blobs/,09-20 22:55 清树时被当孤儿删了;账本/qid_images_ext 与 raw 归档完好
- 通道:r8(df20 tar 流式)/r3(plantnet zip 落盘)/r14(pubchem RDKit 渲染);脚本 /tmp/qid_scan/remat_*.py + pubchem_render.py
- **pubchem 是 RDKit 2026.03.6 从 CID-SMILES 渲染的 2D 结构图**(512x512 kekulize),字节级可复现(金丝雀 30/30);慢在扫 1.8 亿行 dump,用 `zcat|awk 集合匹配|渲染器` 管线 11 分钟收尾
- **raw/df20 有脏键 part-00055.tmp(1GB 上传残留)**,流式拼接必须严格正则 part-\d{5}$ 过滤——两次 tar 流崩在此
- 终态:qid 系全部账本图(并集 9,326,123 唯一sha)100% 实体在 COS;kb/blobs 总量 ≈ 2600 万+/27.8TB+(blobs_inv3 快照 256/257 完成但早于今晚重灌,精确总数需重扫);sdc_attach 174.9 万图系主账本完全子集
- b3x 差集收官(done 490/482);GBIF 用户拍板放弃(0 张已下,队列未动,待对方停)

### 收尾盘点重大更正(2026-09-22 深夜,用户问"这批图是不是很小"引出)
- **wm 主线"99.9% 覆盖率"是假象:8,007,799 个 blob(全库 30.2%,17.1GB)是 2.1KB 的 429 毒 HTML 页**;wm 账本 89.8% 行(7.92M)的 sha256 就是毒页内容 sha(<5KB 抽样 14/14 全 HTML);sdc_attach 账本(sample size_bytes=2144)同样带毒
- **真图没丢**:b1 毒行重收当年实际成功——r1 manifest 成功 sha 抽样 3000 = 100% 在 kb/blobs、仅 5.9% 在 wm 账本;r1 成功行 89.6% ≥50KB(真图);r1 单机 90.4 万唯一真图,全网真图估计 700-900 万,躺在 1777 万"未归因"blob 里
- **修复路径(未执行,待排期)**:①用 kb/b3_manifests/r1..r20 成功行重建 wm 主账本+sdc_attach(真实 sha 替毒 sha)②b2 四源账本用 fetch_list+worker manifest 并账 ③账本重建后删 800 万毒 blob(17GB)+ 无引用残件 ④重扫总量
- **queue-b3v = b1u 升级 pass**(PDF/TIF 等取 thumb1920,160 批×2000,done 116,在跑勿动——用户令);此前的 queue-b3/b3x 都是 sdc 线,不是 wm 毒行修复
- 巡护定时任务(每20分钟,24h/72 次)automation-4621167b 仍挂在本 workspace——待用户点头删除
- 本节全部为盘点,未执行任何删除/停机

### r 机账本抢回 snap1 完成(2026-09-22 深夜)
- **19/19 台 worker manifests 已上 COS `kb/fleet_manifests/snap1/`(19 tgz + 19 index,共 4.12GB)**;r18 抽验 7 身份 477.6 万行 0 坏行
- tar 期间 b1u 在写(另一会话在补 b1u 原画,勿动其 worker):按快照收,各 manifest 字节偏移在 snap1/rNN.index.txt;b1u 结束后跑 snap2 增量(tail -c +offset)
- **r9 已换新机(新 IP 未知,ssh config 仍注释)**:旧 r9 账本随旧实例丢失,但旧 b1+b3 镜像在 kb/b3_manifests/r9.jsonl.gz;新 r9 工作目录只有近期行,待 IP 后补拍
- 并账原料齐备:kb/b3_manifests/r1..r20(6GB,b1+b3 线)+ snap1(b1u/b3v/b2 四源线);三规则(取最新成功≥3KB/tier 优先;毒行元数据作废重取;b2 进 ext)待用户拍板后开跑
- 补拍:r9 新机(43.134.90.68,ssh r9 别名走代理可达)已补,snap1 20/20 齐;r9 新机仅 8 身份/35MB(近期 b1u/b3v 行,历史在 kb/b3_manifests/r9.jsonl.gz);旧 config 双 r9 块未动(功能无碍)

## 2026-09-22 下午~夜:全线收官 + thumb1920 定档 + 直连身份 + 分布分析(本会话续)

**b3v 收官→全 fleet 下载线结束**:b3v(缩略升级 thumb1920 版)166/160 done;b1u 线终账 ok 360,291 行(含跨机重扫);死信大头 http:400 44,135 行(commons 规定请求宽≥原图宽拒绝缩略→**这些行原图≤1920px,当初因字节>10MB 被降档但分辨率并不高**)。用户拍板:400 行改走**原图直链补采**=queue-b3w(19 批 36,516 行,tier=orig)已上线。剩余小死信:404×723(永久)+curl63×1,915+interrupted×81≈2,700 行(未处理,低值)。

**决策链(用户拍板存档)**:①缩略升级从 orig 改 **thumb1920 定档**(与 b3 一致;生图训练分辨率结论:SDXL/FLUX 1024 档绰绰有余,原生 2048+ 才需原图;orig 版已跑 21,916 张成果保留 manifests 不重下);②**直连身份 900-919 铸册**(每机第 8 worker 走本机 IP,ua_assign.tsv `direct:r1-r20` 20 个新 UA,queue_worker 空代理=直连已验证;注意 worker "队列空,退出"是设计行为,batch<worker 时多起无用)。

**主账本分布画像(8.86M 全量实测)**:唯一 qid 5,612,184(其中 **pid: 命名空间 1,139,488 行=属性概念非实体,分析须剔除**);唯一 sha 8,822,927(重复率 0.4%);82.3% 概念单图,头部 1% 概念占 19.1% 图量;jpg 82.7%/png 8.2%/**svg 6.9%**;CC BY-SA 54.4%/PD+CC0 31.3%/NC 系仅 1.9%;长边≥1920 占 49.9%。领域快扫(文件名启发式):**生物双名 24.7% 行/25.7% 概念**(最大单一领域,印证停 GBIF),人文建筑~10%,符号平面~5%。

**关键覆盖率结论**:清洗后有图 item 概念 **4,569,289** vs sitelink 全集 38,414,271 = **11.9% 覆盖**,无图 ~3,380 万。P31 精算管线在跑(见下)。

**P31 精算管线(sgx,资产留档)**:COS `kb/wd_full/parts/` 300 片 156.1GB(sitelink 抽取用过的完好源;湖侧判废的是它自己装配的 156G gz,**wd_parts 8 段 bz2 已不在 COS**)→sgx `sgx_pull_wd.py` 流式装配 `~/latest-all.json.gz`→`p31_chain.sh`+`p31_extract.py`(pigz 单流解压是地板,16 fifo worker 并行消费,首段 2000 字符粗取主类)→`p31_all.tsv`→`p31_join.py`(haveimg_final.txt×sitelink_qids.txt.gz 分桶 P31 计数)。join 输入 `haveimg_final.txt`=账本 4.47M Q-ids ∪ fleet manifests(sdc/b2/b4)≈4.57M。

**运维小记**:①r9 ssh config 旧注释块已清(用户提示);②ssh 内联 python 引号嵌套再翻车一次,一律落文件再执行;③manifests 按身份跨队列共享(run_kb_w<id>/),队列切换后断点键通用;b2/wk_b2 目录大,全量 grep 扫描单机 >300s,收集要走 nohup 落远端文件。

## 2026-09-22 深夜:P31 分析暂停,并账优先(用户拍板)

**决策**:P31 精算让位,**另一会话先并账**,并完再回来分析。sgx 上 P31 链已杀净(曾因部署取消未遂起了两实例,按 PID 清);`~/latest-all.json.gz` 拉取自然收尾后留驻 sgx(156GB,磁盘 443G 无压力),恢复分析=跑 `p31_chain.sh` 剩余步骤(~40 分钟)。b3w(400 行原图补采)不受影响继续跑。

**并账会话必读(输入+口径)**:
1. 输入:主账本 COS `datasets/demiwtg/kb/qid_images.jsonl.gz`;b1+b3 manifests 镜像 COS `kb/b3_manifests/r*.jsonl.gz`(注意**老 b1 行无 src 字段**——粗解析会归 unknown,merge 工具按自家 schema 走);b2 账本 r 机 `~/wk_b2/run_*/ledger.jsonl`;b4/TMDB r 机 `~/wk_b4/run_*/ledger.jsonl`(blobs-nc 分区/license_zone=nc);b3v/b3w 产物=wk_backfill manifests 里 src=b1u 行(tier 混 orig/thumb1920)。
2. 口径拍板项:主账本 **pid: 命名空间 113.9 万行**(属性概念)保不保留;sdc /23→/20 重分片跨机重复 ~1 万行**合并必须去重勿裸 zcat**;同 (qid,commons_file) 保 orig 新行;images 旧池(216 万唯一 sha,重合 2.1%)并口径。
3. 对账恒等式:done + perm死 + retry死 + 未跑 ≡ 任务总数(逐线)。
4. 已知死信基线(2026-09-22 实测):b3 1.1%/b3x 2.6%/b1u 11.6%(400 类 4.4 万行在 b3w 补采中);b2 暂时类 ~7,500 + 404/not_image ~1.5 万待终局;b001938 毒尾 126 行降档 1280px 未做。

### 夜间全量清点+质量审计完成(2026-09-23 凌晨,详见 collect/INVENTORY_AUDIT_20260923.md)
- **b2 账本在 ~/wk_b2/run_*/ledger.jsonl+b2_op 体系,snap1 漏拍 → 已补 snap1b(20包 461MB 上 COS)**;b2 内建质量闸(超8MB/404/非图→死信不上blob)
- **b2 实绩修正(队列done≠行成功)**:inat 357万 ✓~100%;si 仅 56.8万/329万(87%死信:254万超8MB+30万流断——可回补缩略);oi 61.8万/289万(232.7万404,清单URL存疑);met 8,521实图+5.6万合法无图
- **b1/b3 线 20 台聚合定论**:wm 唯一真图 7,616,927(86.4%);sdc 4,400,537;真图内容抽检全线 OK;met ~25% 截断 JPEG(0-44字节,行动项重下比对)
- 全线账本坏行:主账本1/b3_manifests 2/b2 全 0;pubchem 16 行合法小图;空行 0
- 旧系池本机路径(datasets/demiwtg/blobs)已不存在,位置待确认
- 原料已全在湖侧:/tmp/qid_scan/dl/{manifests 5.6G, snap1 4.6G, snap1b 441M}——账本重建随时可开
- 三项深查:met 截断实为 55.5%(200抽)、CDN 完整→全量重下 8612 张可修(b2_op met 分支截断 bug 待查);si 缩略端点 deliveryService?max=N 实测可用,287 万死信可回补(+0.5-0.9TB/8-16h);oi 404 根因=免费桶只托管 191 万张,61.7 万即天花板,建议收档

## 2026-09-23 凌晨:全线清零 + 并账就绪(值守会话收官记录)

**b3w2 收官(126/112,含补扫)**:400-清单终账 21,740/36,503 成功(59.6%);未成 14,763 全部政策性死信——41,628 超限(curl:63=文件超 64MB 上限,含 orig 前线史)+4,960 非图(webm 视频)+1,156 404,与 b1 巨物政策一致,**终态无需再补**。至此缩略升级全口径闭环:thumb1920 成功 ~275K + orig 成功 43,656(21,916 前线+21,740 b3w)。

**教训入库**:尾段收慢的根因=批次粒度(19 批×2000 行只养得起 19 worker);重切 112 批×150 行后 160 worker 满编(含直连 900-919 首战),~40 分钟收完。**以后收尾线一律小批**。

**并账就绪包(sgx `~/merge_input/` 共 ~10GB)**:snapshots_0923(5.5G)+ **snapshots_0923b(4.4G,含 b3w2 增量的全量快照,20 机 71s 拉齐)**+ snap1b(b2 dead/finished)+ qid_images.jsonl.gz(v1 对照)。三钥匙表由并账会话自拉。

**定稿文档三件**:`INVENTORY_2026-09-23.md`(盘点+两会话互检裁决:gbif 滴灌实有 13,134 张非 0、旧系池本机失联待用户确认、b2 死信语义以 AUDIT 版为准)、`MERGE_SPEC.md`(单文件 images.v2 一图一行+qids/refs 嵌套+字段冲突策略+增量并账机制[水位线+补丁+重发布,规则满足结合律])、`MERGE_PLAN.md`(六阶段流水线 S0-S5+五验证门 G0-G4+G5 增量等价,sgx 执行 ~4h,si 金丝雀先行)。

**值守终态**:所有下载线清零(b3w2 最后一条);fleet 空闲待并账会话调度;直连身份 900-919 已是常备资产。

### oi2 Flickr 原图补全战役(2026-09-23 进行中)
- **目标**:补 oi 232 万 S3 桶外死信的 Flickr 原图;官方 URL 元数据在 GCS `openimages/2018_04/image_ids_and_rotation.csv`(3.35GB,901 万行,**用官方 download.html 里的真路径,此前 403 是我路径猜错**);死信 227 万 ID 100% 命中 URL
- **链路全通**:存活探测 83.3%;金丝雀 50 行 46 成功;全量队列 queue-b2-oi2 1137 批(producer=cut_oi2_r8.py,r8 上跑,与 cosqueue 字节同构);worker 零改动复用 b2_op(--src oi --queue 显式指向)
- **事故:80 worker×4 lanes(320 并发)把 fleet 20 IP 全部打进 flickr 429 黑名单**(IP 级、UA 无关、lake 出口正常);已全停冷却,oi2_watch429.sh 每 15 分钟复测,解封后按 1 worker×3 lanes/机 错峰重启、渐进提速
- **教训:flickr static CDN 容忍度远低于预期,≤2-4 并发/IP 为安全线**;重启后护航循环盯 429 死信率,超阈值即降速
- 备选(若长封):b1 商业代理出口(qw_env 的 KBP_PROXY)改造 b2_op curl 走代理

### ⚠️ oi2 flickr 429 协调警示(2026-09-23,给所有并行会话)
- flickr static CDN(farm*/live.staticflickr.com 同一限流桶)**对数据中心 IP 极严格:单 IP ~10 rps 即封,封禁全域、按 IP、TTL 至少数小时**
- 当前状态:fleet 20 直连 IP 全封;111 代理池也已被烧(40 worker×3 lanes 几分钟烧光);**lake 出口未封但仅 2MB/s**
- **恳请其他会话暂停 oi208* 直连 worker**——持续重试会不断续期封禁,大家都下不了
- 恢复方案(湖侧已备好):解封后每代理 1 并发、~0.3-0.5 rps/代理 × 40 代理 ≈ 15-20 rps 全局长跑(~40h);oi2_unblock.log 每 30 分钟探测,双通自动温和点火
- 教训追加:flickr 与 wikimedia 完全不同量级,"fleet 猛冲"策略在此源必死;curl_fetch 已尊重 Retry-After,但恢复后也必须限并发

### met 重下完成 + si2 回补进行中(2026-09-23)
- **met 质量缺陷已修复**:8,376 张重下,5,809 张(69.4%)确系截断已替换完整版(新 sha,严格 PIL 解码验收,抽样 20/20 尺寸全对),2,567 张本就完好;修复账本 `kb/batch2/met/met_redo_fix.jsonl`(old_sha→new_sha 映射,账本重建时用);旧截断 blob 成待清垃圾
- **si2 回补发射**:274 万死信(99.7% NMNH 自然史馆藏)× `deliveryService?max=1920`,金丝雀 49/50;队列 queue-b2-si2 1370 批;120 worker(20机×6×3-4lane),全程 0 个 429,ids.si.edu 无封锁迹象;速率 ~40-50 行/s,ETA ~17h
- 教训应用:从 1×2 温和起步、确认零 429 后两轮渐进提速——flickr 事故的正确版

### oi2 Flickr 原图补全战役实录(2026-09-23,已暂停待拍板)
- **映射完美**:官方 image_ids_and_rotation.csv(3.35GB,storage.googleapis.com/openimages/2018_04/ 根路径,注意旧路径 403 是文件名猜错)=227 万死信全量命中 0 缺;存活率实测 83.3%(→预期可补 ~189 万)
- **Flickr 防御实测(重要)**:①单发冷请求通(直连/代理都通);②**任何持续速率(≥0.8rps/出口)直连(腾讯 ASN)或 wm 代理池都迅速 429**;③429 是逐 URL 烧伤+出口连坐混合,烧伤 URL 冷却 >5h 未测到恢复;④429 无 Retry-After;⑤lake 单发可用、持续不可用
- **战果**:burst 波(首发 60w×4lane)抢下 ~50 批 ≈ **8.5 万张原图已入 kb/blobs**(各机 run_oi2*/oix*/oiq*/oj*/oip* 账本);其后全部烧伤
- **备胎判死**:archive.org "Downsampled Open Images V4" 512px 包(56.75GB)= S3 桶 1.9M 张的打包版,死信集命中 0/259——不含我们缺的 232 万
- **队列状态**:queue-b2-oi2(1137 批,claims 495/done 50)+ queue-b2-oi3(637 批,claims 130);worker 全停
- **工具资产**:b2_op_oi2p(代理+令牌桶+AIMD)、b2_op_oi2q(429 快死信);exec_curl 429 耗尽后 reason=retries_exhausted/status≠429(外层 429 分支接不到——改它必须改 exec_curl 或用 reason 判断)
- 候选决策:A 超慢滴灌标定(20 直连×1/15-30s 起,AIMD 探阈值,预计天-周级)B 收档 70 万 C 折中:背景滴灌+主力回账本重建

## 2026-09-23 上午:并账完成(images.v2 发布,两会话协作交付)

**产出(COS datasets/demiwtg/kb/ 权威)**:`images.v2.jsonl.gz`(**唯一主表**,一图一行 16,016,943 行,qids_total 28,078,153,refs 16,038,217,单文件自包含可重下)+ `quarantine/deadletter.v2.jsonl.gz`(8,412,364,按 dead_class 分类待重载决策)+ `quarantine/qid_images_v2_pid.jsonl.gz`(6,369,645)+ `rebuild_report.json`。水位线 manifest_snapshots_0923b。

**组成**:wm 5,097,779(毒过滤后)/ sdc 5,134,018 / inat 3,564,704 / openimages 616,645 / si 561,689 / tmdb 526,844(blobs-nc)/ plantnet 275,200 / df20 202,190 / pubchem 25,830 / met 8,380 / gbif 3,688。tier:orig 4.89M / thumb1920 5.34M / null 5.79M。nc 528,782。

**验证**:G1 守恒(qids 总数=去重边−毒边 ✓ 逐源对上)/ G2 湖侧独立 201 抽查 198 在、0 疑似非图(毒漏清零)、tmdb nc 路径修复 8/8 / G3 恒等(deadletter 8.41M=6.12M 死信+534K 毒+229 万 attach_fail 分毫不差)。

**已知尾差(记录在案,不阻塞)**:①wm ~3-4% 抽样 404(疑似 09-05~06 本地时代行的 blob 缺失,可用 refs.orig_url 重下回收);②~1.4 万 thumb1200 遗产行被污染窗保守误隔离(死信区可按 external_id+size≠2.1KB 捞回);③gbif 3,688 张 BY-NC 物理仍在 blobs(zone 已标 nc,迁移任务单列)。

**过程修复清单(管线 v8,sgx ~/merge_run.py = /tmp/merge 共同编辑版)**:tar 同名覆盖→按机子目录解包;b2/b4 死信无 qid→放宽;qids 数组展开(tmdb);wmdead 误毒化 b1u 失败键→仅无 src 行;**毒窗过滤(fetched_at∈09-12~15 或 size 1.7-2.7KB→poison_page,534,392 行)**;tmdb blob_path 物理分区;ssh 中断重试引发 rm 竞态×2→flock+runner+done 标记;两会话工作区互踩→merge_final 独占+协调文件。**教训:/tmp 共享目录两会话共编辑同一代码文件,改动以磁盘为准不信记忆。**

**增量并账入口(下次新数据)**:①新镜像快照(如 0923c)上 COS;②sgx 跑 merge_run.py(水位线 diff 增量行→行级补丁);③流式读旧 images.v2+套补丁→重发布;规则满足结合律(license 最严/tier 最高/并集/时间最新),增量≡全量重建。sgx 空闲可兼下载节点(WM 系 +1IP≈+0.9rps;非 WM 图源直连 10-50MB/s 是主场;1IP↔1UA 铸册待办)。

## 2026-09-23 并账仲裁记录(值守会话,进行中)

**已发生**:两会话在同一 sgx 并行推进并账,工作区互清三次(merge_work 反复被 wipe,含一次已完成的 16.50M 产物);后我方转独立工作区完成 16,016,943 版,对方在其上做门+修复并**已发布 COS**(images.v2 2.00GB)。

**我方发现的悬案(待 v3 终裁)**:两轮产物 wm 源差 48.7 万图(5.58M vs 5.10M),对应 deadletter poison_page 4,464 vs 534,392——疑与 b1u-400 类死信的 v1 对应行归类有关(400 类行的 v1 thumb1200 是**真图 blob 应保留为图**而非毒)。已查明:manifest 的 b1 时代行全部 ok(9.47M sha 行、0 条 no-src 真死信)——**真毒行全部被 b1 成功重收覆盖**,不存在"b1 死信毒行"。wmdead 集合本应为空。v3=从未动过的 0923b 原始 tar 全新重建,以 v3 输出为准;若与已发布版不一致则以 v3 重发布。
**教训(两会话协作)**:自主会话共享文件系统时,必须先占独立工作区+协调文件再动手;对方已按此共识运作。

## 2026-09-23 并账仲裁终案(值守会话)

**v3 终裁**(从未动过的 0923b 原始 tar 全新重建):images 16,016,943 / deadletter 8,412,364 / pid 6,369,645 —— **与已发布 COS 版逐位一致**。已发布 images.v2 为正确产物;此前 16.50M 一轮系两会话工作区互踩期的污染产物(其多出的 48.6 万"wm 图"实为毒页),已弃。

**毒分类仲裁**:deadletter 中 poison_page 534,392 行——抽样 100/100 命中 v1 的 2142-2144 字节签名(2.1KB HTML 错误页),blob HEAD 30/30 全为 2.1KB 级真毒页,零假阳性。规则(毒窗 09-12~14 OR 字节签名 1700-2700)成立。**毒 blob 清理放行**(对方会话执行,保留集 16,347,063=v2+窗保+wm 补)。

**终局资产**:COS `datasets/demiwtg/kb/images.v2.jsonl.gz`(2.00GB,1,601.7 万唯一图,一图一行+qids/refs)+ deadletter.v2(841 万分类死信)+ pid 副本(637 万)+ rebuild_report;水位线 0923b;增量机制=水位线+补丁+单文件重发布(结合律保证)。
**遗留待拍板**(沿对方清单):wm 404 重下/thumb1200 捞回/gbif 13K 迁移 blobs-nc/sgx 兼任下载节点/sgx 旧工作区清理(盘 61G 余)。

### si2 护航中段记录(2026-09-23)
- 过半(51%);速率波动 54-142 行/s(美东下午低谷);死信 1.6 万(96% 跨洋流断,可重收)
- **并发科学**:400 worker 自拥塞(死信 3.6%)→ 回调 240 后速率反升至 142,死信趋零;代理/sgx 增益可忽略(商代带宽 30-60KB/s;sgx 到美线路差)
- 孤儿认领 ~290 个(回调时被杀 worker 遗留),收尾用 /tmp/qid_scan/si2_endgame.py release_orphans 释放
- 死信重排:收尾把 fleet si2 dead.jsonl 聚合 → cut_si2 再切 queue-b2-si3 一轮补收

## 2026-09-23 下午:毒 blob 清理收官(①完成,零误删)

**删除 11,636,443 个 / 7.4TB,零错误**(毒页 7,950,089 张 17GB[2.1KB 错误页签名]+ 旧 rendition 3,686,354 张 7.39TB[b1u/b3 升级换下的 thumb 旧版,魔数抽验 9/9 真图,v2 已保有更优档])。**删后复核闭合**:28,179,688 − 11,636,443 + SI 在途 523,360 = 实测 17,066,605 ✓;blobs-nc 526,844。库终态 **1,759 万对象全部有账**。
**三道保险全程生效**:①保留集 16,347,063(v2 全量+污染窗误隔离保护 251,047+wm 清单 79,073)②时间闸门(09-23 后上传不删,护住另一会话 SI 回补在途 79.6 万)③金丝雀 100 删 6/6 验+审计清单 kill_audit_0923.tgz 归档 COS quarantine/。删除器 sgx ~/deleter.py(40 线程 1,439-1,584/s,断点续删)。
**方法论沉淀**:大删除面先按"归属"分相(毒签名/替换 rendition/时间闸门),逐相验证(尺寸分布→账本归因→魔数)再动手;>2.7KB 的"other"相抽验全部真图差点误判毒——**尺寸启发式只能缩小范围,内容验证才能定案**。

**二轮清理清单(依赖分析已出,待用户确认)**:COS ~261GB(wd_full/parts 156.1G 三重冗余[P279 用 sgx 整装本,dump 公开可重下]/osm_parts eu+na+gbif 90.7G 残片/过期+冗余镜像 15.2G/九条遗留队列)+ sgx 本地 ~27G(merge 工作区+枚举 tsv)。**保留**:sgx latest-all.json.gz 156G(P279 上卷在用)、0923b 水位线(Wave2 增量并账基准)、raw 157G、r 机 manifests 原件(Wave2 后再议)。

## 2026-09-23 傍晚:二轮清理收官(用户拍板 wd_full 保留)

**删除 27,458 键 / ~106GB,18 秒零错误**(osm_parts eu+na+gbif 残片 185 件 90.7GB / b3_manifests 旧镜 5.9GB / snapshots_0923 被 0923b 取代 4.7GB / fleet_manifests 冗余镜 4.6GB / 14 条非活跃队列 27K 键)。sgx 本地清 merge 工作区+枚举 tsv(盘 69G 余)。审计 round2_cleanup_audit_0923.tgz 归档 COS quarantine/。
**保留(用户指示+依赖验证)**:wd_full/parts 300 件 156GB(**P31/P279 上卷在用**,sgx 整装副本 latest-all.json.gz 亦留);0923b 水位线 20 件;queue-b2-si2 活跃(SI 回补每机 12 worker)+其 oi2/oi3 家族(对方会话资产);OSM 成品 8 洲验证在位后删残片。
**库终态**:COS = blobs 1,759 万(全有账)+ wd_full 156G + raw 157G + meta_dumps 10G + bridges/账本/quarantine ~4G;两轮清理累计释放 **~7.5TB**。

## blobs / blobs-nc 双区设计原则(2026-09-23 用户问询后定稿记录)

**nc = NonCommercial**(CC BY-NC 等禁商用许可;库内 NC 资产:TMDB 52.7 万 + gbif 滴灌数千)。

**为什么分区而不混存 blobs/**——双保险架构(B4 计划时定的):
1. **物理前缀=硬边界(兜底)**:商用导出/同步 = 直接拷 `blobs/` 前缀,不依赖逐行查账本;账本漏查或字段 bug 时,分区仍拦得住 NC 混入商用流程(许可违约风险)
2. **账本 license_zone=精细层**:按行过滤/统计/**发现分区错误**(gbif bug 正是它逮的)

**gbif 事故反证双层价值**:账本 zone 空 + 物理进 blobs/ 两层同错才成隐患;任一层对则影响受控。结论:**维持双区**(同桶同区零成本;合并=丢兜底,再拆=52.9 万张重迁)。

**待执行:gbif 错位对象迁移**(已论证安全):迁移集由 v2 驱动(zone=nc 且目标 key 不存在、源 key 在),~2-3 千对象(7,800 张与 inat 重合 sha 免迁);服务端 copy→HEAD 验尺寸→精确单删源 key→清单落 COS;原始账本 path 悬空无害(管线按 sha+zone 现算路径,不用账本 path 字段);MISSING 项可按账本 url 重下恢复。

## 2026-09-24:P31 自有体系提取+P279 上卷分布完成(分析会话)

**产物**(sgx ~/ 与仓库 `demiwtg/collect/p31/` 双份):p31_all.tsv(qid→P31 主类,1.195亿行/4.3G,worker合计=行数分毫不差)、p279_all.tsv(qid→P279上位+标签,447万行)、p31_distribution_report.md(分布)、p31_bucket_counts.tsv、p31_unmapped_top.tsv、p31_top_classes.tsv(top2000主类+计数)、p31_labels.tsv(API补齐2007标签)、human_candidates_labeled.tsv(Q5子类审定底稿)。

**管线**:p31_chain.sh(16路fifo+pigz单流解压,全程~100min/遍)→p279_chain.sh(第二遍,标签列本趟有bug输出"nguage"已修脚本但未重跑,标签走API补齐)→p31_rollup.py(闭包装载~30s+上卷100s)。脚本归仓:collect/p31/。

**核心结论(456.9万有图QID)**:98.1%有P31;上卷命中98.0%。32顶层桶分布:人类28.7%、建筑结构19.5%、生物类元7.6%、聚居地5.5%、地理位置4.9%、行政区划4.8%、组织机构4.1%、艺术作品3.7%、道路3.6%、载具2.3%、地形1.9%;其余22桶合计~10%;未上卷0.1%+无P31 1.8%。头部主类:human 131万、taxon 33万、教堂14.4万、街道11.4万、building 6.8万、house 6.7万、铁路车站5.4万、村4.1万、画作4.0万。

**P279脏链对抗(关键工程决策)**:P279图跨域脏边泛滥(交通基建→人类、宗教建筑→宗教→组织→人群→人类、人类P279含"杂食动物")。三防线:①人类桶封闭集={Q5}+人工审定白名单6项(p31_human_set.tsv;Q5直接/隔代子类388个候选仅6个是真人类类);②Q5为BFS终结点(不展开其父链防漏进生物域);③其余桶分层BFS最短路径优先+同深度按桶优先级(p31_buckets.tsv行序)。桶QID逐一对照API标签核准(修掉4个手滑:音乐作品Q105543609、保护区Q473972、湖泊Q23397、载具Q42889)。

**已知瑕疵(下轮可修)**:软件桶混入Unicode字符/维基分类/联合国决议(~1.7万),食物桶混入马匹品种,电影桶混入文字作品7.6千,商品桶混入文化财产/军事演习——均为长尾脏路径,单桶<2%;铁路车站5.4万落入地理位置而非建筑结构(最短路径所致)。修法:加桶或加kill边,重跑rollup仅100s。多P31占比96.7%系抽取窗混入reference的numeric-id,主类(首值)不受影响,已停报该统计。

**下一步选项**:①桶→旧taxonomy二级映射(top~2000主类覆盖~99%,一次模型批调);②脏路径精修;③32桶vs旧29领域对照表。

## 2026-09-24 凌晨:P31/P279 自有体系分布落地(分析会话)

**资产归档 `demiwtg/collect/p31/`**:p31_chain.sh/p31_extract.py/p31_join.py 原样拷回;p279_labels_extract.py 修了标签窗偏移 bug(本轮 sgx 产物标签列=坏值"nguage",边列无损);p31_rollup.py(距离主导上卷);p31_buckets.tsv(32 顶层桶);fetch_labels.py+ p31_labels.tsv(2007 类 API 中文标签);p31_distribution_report.md / p31_bucket_counts.tsv / p31_unmapped_top.tsv。数据大件留 sgx:p31_all.tsv 1.195 亿行 4.3GB、p279_all.tsv 447 万类边 214MB(两遍 dispatch 计数一致 121,710,190,完整性核对通过)。

**算法教训**:P279 图有跨域脏长链(教堂→facility→…→protein 10 跳、taxon→organism→…→human 7 跳),"闭包可达×优先级"会把 98% 全吸进 Q5;正确做法=**逐层最短跳数优先,同层按桶优先级;Q5 单独用封闭集(Q5+直接子类+隔代)**。桶表 32 桶 QID 全部对着 Wikidata API 核过标签,修掉 4 个手滑:Q105543609 音乐作品(旧 Q10554360 是虫)、Q473972 保护区(旧 Q4739727 是人名)、Q23397 湖泊、Q42889 载具。

**结果**(有图 456.9 万 QID,98.1% 上卷命中、1.8% 无 P31、0.1% 未上卷):人类 28.7%、建筑结构 19.5%、生物类元 7.6%、人类聚居地 5.5%、地理位置 4.9%、行政区划 4.8%、组织机构 4.1%、艺术作品 3.7%、道路 3.6%、载具 2.3%,其余 22 桶合计 ~7.6%。头部主类:Q5 131万、taxon 33万、教堂 14.4万、街道 11.4万。

**已知噪声(小桶抽屉效应,~3-5% 量)**:铁路车站/地铁站 6.7万落"地理位置"而非"建筑结构";软件桶混 Unicode 字符 0.85万+维基分类 0.47万;商品桶=化学品类 1.2万+文化财 1万+报纸;食物桶混马匹/疾病类;电影桶混文字作品 0.76万。精修方向:增"交通设施/化学物质"桶+top 类人工覆盖表(未做)。

**联动方法1**:对齐 29 领域二级时,"P31 桶→二级"只需映射 32 桶+长尾 top 类,模型调用量从 457 万级降到千级。haveimg_final.txt 仅 2 行坏格式(Q058/Q090659),已忽略。

**raw/wikimedia 132.7GB 已删(2026-09-23 用户拍板)**:commonswiki image.sql dump 切片,09-15 毒行审计时代的 img_sha1/尺寸/MIME 预筛原料,任务早已闭环;零活跃引用(sgx/r 机进程扫描 0);CC0 公开 dump 可随时重下;审计清单 wikimedia_raw_cleanup_audit_0923.tgz 归档 COS quarantine/。raw/ 其余(df20 116.8G/inat 70.2G/si 55.9G/plantnet 31.7G/pubchem 8.9G/oi 5.1G/met 0.3G ≈ 289G)已并入 v2 仍待拍板。

## 2026-09-23 夜:三轮清理收官(COS 大扫除完毕)

**第三轮删除 16,889 键 / ~289GB,12 秒零错误**:raw/ 全家(df20 116.8G/inat 70.2G/si 55.9G/plantnet 31.7G/pubchem 8.9G/oi 5.1G/met 0.3G/_streamtest/wit)+ queue/(b1 残留 2,998 件)+ docs/(16 件先抢救湖侧 collect/archive_docs/ 再删)+ 根散文件(HANDOVER×3/concepts×2+bak)。删除前扫描:r 机+sgx 零进程在读 raw/;si2/oi2/oi3 活跃队列与 wd_full 未动。审计 round3_cleanup_audit_0923.tgz 归档 COS quarantine/。

**当日清理总账**:三轮 + 毒blob ≈ **7.9TB 释放**;COS 现存=真图(blobs ~1,634 万+blobs-nc 52.7 万)+ wd_full 156G(P31/P279 用,用户保留)+ 0923b 水位线 4.7G + meta_dumps 10G + bridges/账本/quarantine 审计件 + 活跃队列族(si2/oi2/oi3)+ raw 空壳。库进入**最小干净态**。

## 2026-09-23 夜:gbif NC 迁移收官(blobs/blobs-nc 双层闭合)

**迁移 1,938 对象**(v2 zone=nc 且物理在 blobs/ 的全集=gbif 滴灌残留;与 inat 重合的 7,800 张 sha 天然免迁):服务端 copy(x-cos-copy-source,双格式自适应)→HEAD 验尺寸→精确删源,断点续(mig_done.txt),18 秒零失败。**账本行级补丁+单文件重发布**(增量并账机制首次实战:16M 行流式改写 1,938 行路径,COS 覆盖发布)。
**终态**:nc 行 528,782 全部 blob_path→blobs-nc(残留 0);HEAD 抽验 8/8;**物理分区≡账本 zone,商用导出边界纯净**。审计件:migset.tsv/mig_done.txt 在 sgx ~/cleanup/。
**待办状态更新**:尾差三项剩 wm 404 重下(~1,690)与 thumb1200 捞回(5 张,用户暂缓);gbif 迁移✅完成。

## 2026-09-24:qid_edges 全量关系边表落地(用户裁定:弃 qid_graph,建 qid_edges)

**定位**:qid/概念上卷用的事实源。四谓词全量边(主体不过滤,全量实体含无图抽象类——与当年 qid_graph 成员集过滤的本质区别):P31 1.907亿 / P279 712万 / P361 578万 / P527 208万,合计 205,647,208 边;另 qid_class_labels.tsv 447万类(en/zh 标签)。行式:qid \t pred \t toqid(首行=主值);谓词 claims 块窗口隔离(到下一 claims 键,防跨谓词捞值)。

**COS(已传,HEAD 尺寸核对通过)**:lhcos-data/demiwtg-data/datasets/demiwtg/kb/qid_edges/{qid_edges.tsv.gz 784MB, qid_class_labels.tsv.gz 75.9MB, 各带 .md5 边车}。sgx 本地留 tsv 原件(4.7GB)+修复版标签;raw_escape 原始坏标签备份在 ~/qid_class_labels.raw_escape.tsv。

**抽取**:第三遍过 156GB dump(~2.5h,16 路 fifo 同架构),dispatch 121,710,190 与前两遍一致;worker 合计=文件行数分毫不差(边/标签双核对)。脚本归仓 demiwtg/collect/qid_edges/(extract/chain/upload/driver + fix_labels)。

**标签两坑(已修)**:①worker 通用转义把 \uXXXX 的反斜杠吃掉(u7345),后处理 u+4hex 贪心解码+surrogate pair 合并(CJK 扩展区)修复,19.8万 zh 解码;②根因已在 qid_edges_extract.py 的 _val_after 修好(\\u 就地按码点转)。

**用法**:任选入口谓词(P31 或 P361)→沿主干(P279 或 P361)BFS→命中选定顶层集。32 桶分布/29领域二级映射/组成维度分析皆派生视图,重算分钟级。

### 四线补全战役全部收官(2026-09-24,终账已归 COS)
- **si2(Smithsonian 1920 回补)**:队列 1370/1370 批排干;行级账本行 330 万(含回收重领重复),唯一成功数以并账时按 extid 去重为准(初步抽样聚合 67 万+,完整去重需并账全量算——终账已全量归档);金丝雀 49/50、全程死信率 <1%(流断为主)
- **th1200(降档升级 1920)**:排干;唯一成功 **274,334/334,570(82%)**;miss 46,175(http:400=44,565 为 PDF/TIF 无 1920 缩略——保留原 thumb1200 即可;真 404 仅 651)
- **wm404(死链重试)**:全量 48,706 探测完毕,**仅 40 张恢复(0.08%)**:fleet 27+湖 13 全部已入库入账;其余 48,666 判 `dead_404_final` 永久死,不再投入
- **met(截断修复)**:早已闭环(5,809 替换,20/20 抽验)
- **终账权威位置**:`kb/fleet_manifests/final_20260924/{si2/,th1200/,wm404/}`(r 机直传 COS)+ `kb/batch2/met/met_redo_fix.jsonl`;各机本地 run_* 目录为原件可随后清理
- **并账就绪**:湖侧已有 b3_manifests+snap1+snap1b(10.6GB)+全部终账;规则=th1200 成功行替换主账本 thumb1200 sha/si 按 extid 保留 si1 原图优先/met 用 fix 映射替换/wm404 40 张新增+死链终局标注;产物 qid_images.v2.jsonl.gz(统一 schema 单文件)
- 教训链:wm 线多 worker 必须每 worker 独立代理+UA(1rps/IP 墙);kb_backfill 的 AIMD 对 404 重试任务会降速到地板(这类任务改用 HEAD 探测+定向补收);队列 worker 重启必伴随陈旧认领释放

## 2026-09-24:COS 降本——wd_full/parts 300 片(156.1GB)已删

用户拍板(sgx 机器暂不退,COS 成本要降):删 COS `lhcos-data/demiwtg-data/kb/wd_full/parts/` 全部 300 对象(156.1GB,ok=300 fail=0,前缀已空)。**2026-09 版 Wikidata full dump 唯一副本=sgx ~/latest-all.json.gz(156,133,651,298B,三遍抽取验证可读)**。机器回收前若还要 dump,先回传 COS;若不再需要新谓词抽取则随机器退掉。wd_full/ 其余小件(sitelink_qids 等)本就只在 sgx 本地,不受影响。

## 2026-09-24:均衡切层(自适应层级)首版完成

**动机**:41,269 个原始主类分布极斜(中位 2 实体/类,均值 108.6,头部 7 类占 46.6%),固定层级两头不讨好;用户要求"每支自适应找层,类内图量均衡"。

**算法**(balance_cut3.py,sgx ~160s):最近桶多源 BFS 向下建树(与 32 桶分布同口径;31 源+Q5 白名单种子、Q5 不收流入防垃圾环)→ 子树实体质量 → 自根递归下切:mass>MAX(2万)且有大子类则下钻,<MIN(200)的子类并"其他",本级直挂实体单独成"本级直挂"类目;落不上树的 5,245 实体=UNMAPPED。

**结果**:897 类目,覆盖 4,483,748 实体分毫不差;中位 848/类目、p90 8,256;852 个(95%)落在 [200,2万] 区间覆盖 205 万实体;19 个超限不可再分(教堂本级 14.4万、画作本级 4万、村本级 4万等——P31/P279 维度已到顶)。产物:cut_categories.tsv / qid_cut_map.tsv(sgx,含每个 qid→类目)。

**下一步自然延伸**:超限大头"人类本级 131万"要再分只能靠 P106(职业);taxon 本级 33万可加 P171(上级分类)——都在 sgx dump 里,一遍谓词抽取的事(2026-09-24 COS 降本删 parts 后 dump 唯一副本在 sgx,见上节)。

## 2026-09-24:COS 精简终态(用户原则:只留图片+元数据账单)

**第四轮删除**(24+10 键 ~11GB,零错误):三钥匙表 3.05G(SDC 重切能力弃,v2 已固化产出;源头 SDC dump 公开可重推)/ v1 主账本 1.13G(**湖侧 /tmp 与 sgx ~/merge_input 尚有副本**,COS 权威版删;wm 404 重下按 v2 refs.orig_url 即可,不再依赖 v1)/ 0923b 快照 4.69G(r 机原件仍在=非孤本;**Wave2 增量并账改为:从 r 机重拍新快照做全量输入,v2 为旧态**)/ batch2+sdc_fetch+qid_images_ext 清单件 2.2G。审计 round4/round4b 归档 quarantine/。
**COS 终态**=blobs 1,607万+blobs-nc 52.9万(图)+ images.v2+quarantine(账)+ **bridges 0.18G+meta_dumps 10.25G(用户点名保留:概念翻译层+外部参考语料)** + wd_full 156G(P31/P279 用) + v1 外账本全无。累计四轮+毒blob ≈ 7.9TB 释放。

### 并账执行权交接(2026-09-24 04:xx,本会话收口)
- **用户重开新窗口做最终并账**;交接文档 = collect/FINAL_MERGE_HANDOFF_20260924.md(自包含:现状/增量坐标/红线/任务序/坑清单)
- 本会话定位收口:v2c 不发布不覆盖权威 images.v2(对方已发布 16M 一图一行版+wh 回填依赖);本会话四块增量(si2/th1200/met fix/wm404)全部已落 COS 待新会话 Wave2 合入
- met_redo_fix.jsonl COS 副本 404 丢失,湖/r8 本地完好 5,809 行——新会话第一步重传
- 跨会话撞车教训:并账执行权以 sgx MERGE_COORDINATION.md 登记制为准,勿双跑

### 账本重建 v2 完成并终验满分(2026-09-24 凌晨)
- **产物**:`kb/qid_images.v2.jsonl.gz`(33,116,557 边 / 20,205,794 唯一图)+ `kb/deadletter.v2.jsonl.gz`(270 万死链终局:wm 41.3 万+sdc 旧时代 229 万)+ `kb/rebuild_report.json`;湖侧持久副本在 `_staging/v2/`
- **终验:9/9 源 × 抽 50 = 100% blob 实存**(wm/si/inat/oi/met/df20/plantnet/pubchem/sdc 全过)
- **分源边数**:wm 8,448,038(主账本 95.3% 修复,784.6 万毒 sha 换真)/ openimages 12,266,896(一图多概念边)/ sdc 4,399,648(b3 线真实 depicts)/ si 3,906,858(si1 原图优先+si2 1920)/ inat 3,582,546 / plantnet 275,200 / df20 202,336 / pubchem 26,659 / met 8,376(含 5,809 截断修复 meta_refetch)
- **关键归并事实**:①sdc_attach 旧账本(M238 式旧 ID)与 b3 下载线(新 dump M91xxx)不重叠——旧 229 万边判 sdc_old_era 死信,b3 的 440 万真实边入账 ②emit 扩展名不能从 extid 猜(si 的 vol 编号带点会炸)——只用源账本真实 ext/manifest path 字段 ③/tmp 会被并行会话清理,产物必须写 _staging 持久卷
- 消费方式:qid_images.v2 为唯一权威账本,schema={qid,sha256,blob_path,source,external_id,relation_type,tier,license,size_bytes,width,height,orig_url,author,fix_state};按 source/tier/fix_state 字段可筛

## 2026-09-24:五谓词补充 + v5 递归瀑布切层(超限=0 达成)

**qid_edges 扩容**:补抽 P106(职业1683万)/P131(属地2211万)/P136(体裁323万)/P140(宗派128万)/P171(物种树456万)共 4,799 万边,合并后 qid_edges.tsv **2.536 亿边**,COS 已更新(qid_edges.tsv.gz 1.06GB md5=ebe448a0)。

**balance_cut5.py(仓库 qid_edges/)**:v3 树切 + 递归谓词瀑布(组内超限→用剩余谓词继续拆,全败才分段)+ 辅助森林内质量递归切(P171 链形森林切在科/属层而非滚到根)+ 选谓词门控(覆盖≥40%+顶簇≤70%+≥2有效组,修"覆盖极小也赢"的打分坑)+ **保险丝**(结束后全局扫描强制分段,结构性保证超限=0)+ 辅助森林根层小组并"其他"(小类目从 3,762 降到 ~115)。UNMAPPED 长尾也入瀑布。四档结果(覆盖均 4,483,748 分毫不差):MAX=2万→1,597类/93%区间内/99.95%实体覆盖;1万→2,322/93%/99.9%;5千→3,541/88%/99.7%;1千→11,982/59%/98.2%。全部超限=0(1千档保险丝兜了18个shard数学bug组,根因未追)。产物 sgx: cut_categories_v51{20000,10000,5000,1000}.tsv + qid_cut_map 同后缀。

**已知取舍**:人类无 P106 值的 ~47 万在 1万档被分段成 48 个语义空段(qid 字母序);后续可用 P569 出生世纪等再拆。shard 数学 bug(分段后仍超限)被保险丝覆盖,根因待查。

**协作提醒**:本会话与另一会话曾同时编辑 /tmp/balance_cut4.py(出现对方 label_of2);v5 起脚本以仓库 demiwtg/collect/qid_edges/ 为唯一源,/tmp 仅作部署中转。

## 2026-09-24 午后:Wave2 终版并账发布(Wave2 会话)
- 权威 images.v2 升级 16,016,943 → **18,657,248 行**(canonical 原子 PUT,md5 08be0f3a2651e4b77f74538f5824c924;旧件备份 kb/backup/*20260923*;新版日期件 images.v2.20260924.jsonl.gz)。
- 四块增量落账:si2 +2,640,447(10,311 撞 si1 跳过)/ met 5,744 换 sha / **th1200 0 补丁(已被 0923b 水位线隐式吸收,19 万行 thumb1920 抽样 89% sha 一致——差点按交接文档盲打 27 万补丁造成 orig→1920 倒退,对账救了)/ wm404 恢复 36+死 48,665 终标(11,994 死链 blob 活保留,121 挂空移死信)**。
- 验证门全过:G1 恒等(refs/qids 差 15/9 精确归因桶内吸收去重)/G2 400+16 抽查全过/G3 byte-equal 1499/1500/死键闭包完整/v2c 独立交叉 si 3,906,669 vs 3,906,858(0.005%)。
- met fix 已重传 COS batch2/met/(md5 f1f3f1158af881e1d8eb63f0dd0b57a4)。
- wh_backfill 已在协调文件收到通知:新 sha ≈2,646,213,补扫估时要按此重排。
- 教训三条:① tar 成员名无 ./ 前缀,过滤用 startswith;② ssh 命令行带脚本名时 pgrep -f 自杀;③ 战役成果可能已被权威构建消费,打补丁前必须先 join 对账。

## 2026-09-24 傍晚:wh 验收 + 2.2 清理收口(Wave2 会话)
- wh Stage B 验收全过(canonical 未动、抽检 4000/700/22 全过、met 宽高逐值一致)。
- 2.2:主表剔 13,649 HTML 行→发布;**逮到 wh 9 颗 UTF-16 SVG 误判**(安全过滤器拦下删除、行复位主表);canonical 终态 18,643,608 行 md5 1ddfc0ef…;死信 8,438,118;13,640 颗 HTML 毒 blob 验后删除。
- 毒 blob "800 万"实际已被前夜 del_poison/del_round 清掉,2.2c 终扫(8,026,512 候选)只捞漏网——教训:**旧交接文档的"待办"可能已被夜间会话顺手做掉,动手前先抽样探测现状**。

## 2026-09-24 晚:2.3 转正+2.4 收口,全线闭环(Wave2 会话)
- 转正:canonical = 18,643,608 行带 wh 字段(md5 3e882234…,日期件 d;回滚链 0923→0924→0924c→0924d);wave2.py 落 coalesce 规则(单测过)。
- 2.4 全关:th 孤儿 99.99% 是 pid(救 5 张不值)、"88,899 本地池"被实测否掉(现库挂空仅 121)、9.3GB 易失件归档 collect/_volatile_backup_20260924/。
- 终扫后台跑(8M 候选 99.99% 已被前夜清过,漏网毒页~百颗级)。
- 教训:①旧"待办"可能已被夜间会话做掉,动手前抽样探测;②估价数字(88,899/15万)与实测(121)可差三个量级,以逐行实测为准;③救援前先 join 命名空间(本次 99.99% 是 pid,白评估不如先查)。
- 终扫收官:8,026,512 候选全处理(删 122 漏网/护 87 真图/错 1),c2_stats.json 留档。全部既定工作(2.1→2.4)闭环。

## 2026-09-24 深夜:v4 瀑布切层终版四档(修复三连)

**修复链**:①GLOBAL_ORDER 构建里一段死代码 O(V×E) 全表扫描(卡 40 分钟的根因)已删;②writer bug(类目表写了 emit 记录而非最终映射,文件缺 ~900 类)已修,rebuild_cats.py 从完整 qid_cut_map 重建无重跑;③P131/P171 森林"加权滚升"(滚到子树≥MIN 的最近祖先)+ small→@other 归并。

**终版数字**(总量均 4,483,748 分毫不差,超限均 0;UNMAPPED=5,245 长尾在 5千/1千档自身超阈):MAX=2万→3,577 类/99% 区间内;**1万→4,358/98%(推荐档)**;5千→5,331/96%;1千→8,891/91%。语义样例:人类→P106→566 职业类目(政治人物 17.5万分9段、作家分5段——大职业本身无再分维度);taxon→P171→菊科 2,730 等科级组;桥/酒馆/地标→P131 国家/省级组。产物:仓库 qid_edges/{cut_categories_v4_*.tsv ×4, 类目总览_四档对比.md, balance_cut4.py(终版), rebuild_cats.py};sgx 有对应 qid_cut_map_v4_*.tsv(448 万行/档)。

**教训入库**:①多会话/多批次同名产物互相覆盖,终版前 rm 旧件+md5 核对+总量守恒验证三步不可省;②pgrep -f 自杀坑再现(用 [b] 括号模式);③重构时死代码先物理删除再验证。

## 2026-09-24 深夜续:1M 实体均衡抽样清单落地(用户拍板:目标=100万实体)

**输入**:qid_res1024.tsv(主账本 8,861,355 行扫描,≥1024 长边的实体 3,261,909 个/4,208,998 张图,平均 1.29 张/实体;领域差异:生物 2.06、法国市镇 1.87、人类 1.17、建筑类 ~1.05)+ qid_cut_map_v4_5000.tsv。

**框架**:5千档语义并类(剥 shard、noaux 并父类)× ≥1024 过滤 → 丢弃合格量<200 的 1,187 个小类 → **3,746 个语义类 / 303 万合格实体**。集中度:池 TOP10 占 9.8%;**水填配额 281 实体/类后样本 TOP10 仅 0.3%,领域最大生物 9.6%/人类 8.7%,均 <10%**。

**产物**(sgx + 仓库 qid_edges/ 双份):sample_1m_entities.tsv(1,000,167 实体:qid/cat_id/合格图数,gz 5.2MB)、sample_1m_classes_labeled.tsv(3,746 类带中文标签:政治人物 281/76,300、足球运动员 281/33,206…)、sample_1m_classes.tsv、make_sample.py(seed=42 可复现,重跑 10s)。**展开图量 1,338,068 张(≥1024)**;触顶类 2,591、全取小类 1,155。标签补齐:2,528/2,533 API 取得。

**下一步挂钩**:图级清单=按 sample_1m_entities 的 qid 过滤主账本行(sha/path 在账本内),毒 blob 剔除等并账会话的 images.v2 就绪后接入。

## 2026-09-25 凌晨:抽样图传输交接(本会话收档)

**交接文档:`collect/HANDOFF_20260924_抽样图传输.md`**(新会话从这接)。要点:1M 抽样已定型为**短边≥1024 口径**(1,001,433 实体×3,335 类×配额333,seed=42;长边口径旧版废弃);图级清单 sgx ~/manifest_1m_images.tsv(130.8万张,HEAD 实测 859KB/张≈**1.15TB**,page_bytes 低估 5 倍勿信)。**两大悬案**:①blob 抽检 24/30 MISS,反查均为 tier=orig+page_bytes≈2KB 毒行(blob 已被清理),待分层缺失率测试定过滤规则;②COS 轻资产(主账本/manifests/docs)今早在、现在全 None,疑并行清理进行中(用户在推 COS 降本),sgx ~/merge_input/ 有账本全套本地副本可兜底。传输建议 sgx 中转分批+cos_util GET+sha 校验,sgx→本机带宽未测。

## 2026-09-25 凌晨:1M 抽样图传输启动(传输会话,用户已拍板终版口径)

**用户终版拍板**:实体集固定(1,001,433×3,335 类×配额 333,seed=42),**不限分辨率、不限图数**,全量传输,夜间护航。

**悬案双解**:
- ①blob 缺失 24/30 MISS 根因=**v1 账本 wm 行 sha 大面积过期**(v2 重建时 784.6 万毒 sha 换真,v1 sha 非实际 blob key),非单纯毒行。解法=**弃 v1 清单,用 v2 canonical 直接重建**:扫 ~/wh_backfill/images.v2.jsonl.gz(18,643,608 行),凡 qids∩抽样实体集≠∅ 即收录。产物 **manifest_1m_v2all.tsv(sgx+湖双份,md5 e7fc427f...)**:**4,683,826 唯一图 / 6,707,470 边 / 真实体积 6.952TB(size_actual 精确合计)**;分源 wm 131万/si 91万/inat 86万/sdc 86万/oi 48万/plantnet 16万/df20 9.6万/长尾 1.3万;实体覆盖 934,748/1,001,433(6.7 万实体 v1 行全是毒/被剔,无图可传)。HEAD 拓验 50/50 命中且尺寸全吻合。
- ②COS 轻资产消失=已收档的正规清理(qid_concepts/bridges/meta_dumps/quarantine/backup 均已删,超出 MEMORY 早前记录);canonical 2.21GB 在 SG+GZ 双活,blobs/qid_edges/pages-en 活。qid_concepts 可从 sgx 156GB dump 重导,bridges≈sgx 本地 wd_b4_bridge.nt.gz(49MB),meta_dumps 10.25G 无副本(晨报提示用户)。

**路由实测**(全过公司代理 10.127.48.4:3128):湖→GZ 桶 12.4MB/s 硬顶(1/4/16 线程同值);湖→SG 8.2MB/s(x64);双腿并发不叠加(共享代理总帽);cn1 ssh 死;ssh 大流量流停滞。**定案:GZ 中转**——sgx 服务端跨区复制(坑:copy-source 必须主机限定格式 `bucket.cos.ap-singapore.myqcloud.com/key`,裸 `/bucket/key` 报 NoSuchBucket)→湖 12.4MB/s 拉。

**管线**(collect/sample_1m_transfer/):sgx copy_driver(256 线程,~135/s,ETA~10h)SG kb/blobs*→GZ relay1m/;湖 pull_driver(16 线程,多轮追前沿,sha256 逐张校验,落盘 sample_1m_images/<blob_path>,blobs-nc 物理分区保留);轻资产经 GZ _assets/(merge_input 11G+canonical+wh_patch+p31/p279+cut_maps+qid_res≈18G);escort.sh 双层护航(组件死了自动重启)。

**踩坑记录**:①pkill -f 自匹配杀 ssh shell 致脚本没写成(用 PID 精杀);②heredoc 引号双层剥壳(stdin 管道可靠);③pull_driver 重写丢 import os→线程首碰 200 即静默死,主线程永等 join;④cos_util requests 持久会话过代理仅 ~1MB/s,urllib 新建连接反而 12.4MB/s;⑤**copy_driver 目的桶写成源桶客户端**——SG 桶内自拷贝 30 万对象垃圾(SG relay1m/ 前缀,待清);⑥NFS 上 470 万行逐行 stat 不可行。

**时长预期**:复制 ~10h;湖拉 6.95TB@~12MB/s≈6.5 天(代理带宽是地板,晨报给用户提选项)。

## 2026-09-25 01:2x:用户指示暂停传输(传输会话)

管线跑通并运行 4h 后用户叫停。**停机现场**:复制 316.96万/468.38万(67.7%)在 GZ relay1m/;湖落盘 142,483 张;轻资产 20.46GB 全部已在 GZ _assets/(湖已拉 ~6.5GB)。断点齐全(done2.sha/done.sha/size 跳过),**恢复=三条 nohup 命令**,详见 collect/sample_1m_transfer/PAUSED_STATUS_20260925.md(含成本提示:GZ ~4.7TB 中转件挂起计费;SG relay1m/ 30万误拷贝待清)。口径与架构决策、完整踩坑记录见 2026-09-25 凌晨一节与 MORNING_REPORT_20260925.md。

## 2026-09-25 凌晨:SG COS 盘点+多机中继拉回(ssh 劣化窗口应对)

**环境剧变**:sgx 已退(156GB dump 唯一副本随之丢失,重抽谓词需重新下载);SG 桶策略收紧为**按 IP 白名单**(本机 403,r 机正常);GZ 中转桶已整体删除(NoSuchBucket,桥不复存在);本机↔r 机 ssh 处于劣化窗口(单流 ~25KB/s)。

**COS 全桶盘点**(r1 全量列举,跳过 4 处 blob 沼泽):非图对象共 **364 个 / 56.26GB**,清单 /tmp/cos_keys_light.tsv。构成:kb/ 37 件 40.2GB(**images.v2 已发布**:images.v2.jsonl.gz 2.2G + 0924b 2.2G + **0924d 2.6G(全量并账版,含 oi 1227万/inat 358万边,见 rebuild_report.json)** + qid_images.v2 + deadletter.v2;**qid_concepts/fat/qid_graph 出现**(当年 wikidata 线其实被跑过);qid_gallery_captions/qid_image_roles;pages-en 20 分片 24G + pages-zh 3.8G)+ 根级 kb/{meta_dumps 10.2G(OSM/Getty/GeoNames),manifest_snapshots_0923 5G,quarantine 0.45G,wikidata_bridges 0.18G} + relay1m/_shards(**另一会话的 1M blob 中转队列,sha→路径分片,勿动**)+ INVENTORY.md(旧 raw 数据集历史清单,raw/docs/b3_manifests 等已全删)。

**拉回方案**:Ranger——本机调度器 /tmp/relay_pull.py,19 台 r 机 × 2 流 = 38 并发,COS Range 16MB 分块(单文件也并行),分块校验断点续传,组装后复核;优先级 md5/报告→qid_edges→images.v2.2022d→qid_concepts 系→其余 v2/死信→relay1m/bridges/quarantine→pages/meta_dumps/snapshots。落盘 /yzp/zhaozy/yangzepeng/0905/sg_cos_mirror/。实测 ~1MB/s 聚合起步,窗口好转自动提速。r 机部署件:/tmp/pull_range.py + /tmp/ci/cosio.py。

## 2026-09-25 深夜:误导向事故与精确清账(传输会话,重大事故记录)

**事故**:尾部同步 128 线程重部署时,把此前改错目标的 GZ 版 worker(写旧GZ桶 relay1m)推遍 r/rr 40 台——尾部实际写入旧GZ并删 SG 源;用户随后"清空桶"(成功),清空后 worker 又续写约 1-2 小时。根因:改完线程数直接重推文件,未核对文件内目标桶;且分发链多次被取消产生半部署状态。
**精确损失**(桶真数据∪done日志∪清单三方对齐):尾部 1,514,219 中——SH 安全 260,305;旧GZ**仍可救 84,184/123GB(桶未删,勿清空!)**;SG 未处理 282,724;**丢失 887,403 张/1.28TB**(可按 canonical refs.orig_url 补下载)。分类视角:265,051/934,748 qid 受损,140,235 qid 全损,3,335 类均匀波及。清账文件:`collect/sample_1m_transfer/loss_report_20260925/`(README+四集合清单+分类聚合+qid统计)。
**当日其他成果**:轻资产 20.46GB 全量在湖(69件核验);gzcos 前缀 3,030,840+去重 57,428 闭环;sgcos 10TB 迁移完成(8,815,252,纯 CopyObject 内网零流量费);SG 前缀存量删除 3,169,607 零失败。
**教训入库**:①改配置后必须 grep 核对再部署(目标桶/线程数同文件);②"停"指令后一切部署冻结,残余进程用脚本文件方式清杀(pgrep -f 自匹配三犯);③大删除/清空桶前必须确认无唯一副本(本次用户清空旧GZ时,误导向副本是唯一副本);④多方(两会话)并行操作同一桶系时,执行权唯一,写前对账。

## 待办挂账(2026-09-25 清账后,等用户拍板)
1. **救援旧GZ桶唯一副本**:84,184 张/123GB(rescuable_in_oldgz.tsv),桶勿清空;方案已备=全清单幂等 CopyObject 回 gzcos(~30-40 分钟零流量费)。
2. **887,403 张补下载**(lost_images.tsv/lost_image_qids.tsv):按湖上 canonical 的 refs.orig_url 从原始源重下;wm 系死链率<1%;可复用 collect/image_backfill 的 fleet 工具;建议与 140,235 全损 qid 的补抽决策(是否换实体)一并考虑。

## 补记:shcos 微涨之谜(用户问询的归因)
128 线程重部署是多轮 ssh 循环,多次被取消/超时,**部分机器的"换新版重启"实际没执行成,旧 SH 目标版二进制继续在跑并写 shcos+删 SG 源**——这是"改目标后 shcos 仍微涨"的机制。当时只用抽样看速率、没逐台核对实际运行的二进制版本,是检测盲区。损失清账以桶真数据为准,已涵盖这些残余写入,数字不受影响。

## 2026-09-26:qid 系 image 权威账本落位（本会话）
**SG COS 轻资产 39 件拉回完成**：白名单已放行本机（交接 md 中"只能走 r 机中继"过时），3 并发单流 Range 断点续传 77 分钟拉完 40.81GB，40/40 逐件字节校验通过，qid_edges md5 一致；镜像 sg_cos_mirror/ + 总览文档 `sg_cos_mirror/SGCOS同步文件总览_20260925.md`（各文件用途/字段/血缘/实测行数）。
**image 权威账本确立**：COS 侧 canonical images.v2.jsonl.gz 与日期件 20260924d 及 kb/backup/ 回滚链已被桶清理删空（列举实证），sgx 同期退租——`collect/sample_1m_transfer/assets/wh_backfill/images.v2.jsonl.gz` 成为全网唯一完整副本。已转存升级为**全项目唯一 image 权威元数据**：`0905/datasets/images.v2.jsonl.gz`（2,212,465,200B，md5 `2d9f7d13…`，18,643,608 行=转正版发布记录精确一致，内容 md5 `849d27bf…`，source 分布 sdc513万/wm508万/inat356万/si320万…合计吻合）。权威声明/谱系/规约见 datasets/images.v2_权威账本说明.md（datasets/README.md 已挂行，目录按用户拍板拍平）；v1（8,861,355 行）归档 datasets/archive/ 带 md5 边车；wh/rebuild 报告随档。字节 md5 与 COS 发布记录 `3e882234…` 不同=异机 gzip 重压缩，内容行一致性由行数+内容指纹锚定（COS 件已删无法逐字节复验）。死信终版（115MB）失联，用户裁定：死信行重建时已从 v2 剔除，主表为活账闭集，不追补。**规约：账本只读，升级走新日期件+回滚链+md5 边车，禁止就地覆盖。**

## 2026-09-25 补:丢失出账交接文档
`collect/HANDOFF_20260925_丢失清单出账.md`(自包含):另一会话按它把 887,403 行从权威 images.v2 出账(18,643,608→17,756,205),主输入 loss_report_20260925/lost_images.tsv;三重验证+sgcos 原子发布;84,184 可救/282,724 未处理不在出账范围。

## 2026-09-26 凌晨:images.v2 出账执行(887,403 丢失图)
按 `HANDOFF_20260925_丢失清单出账.md` 执行完毕,执行权登记 `LEDGER_COORDINATION.md`(湖侧,sgx 退役后的账本写操作登记载体)。**出账**:18,643,608 → **17,756,205 行**(删 887,403,badline=0);验证超规格全量三重(新文件丢失 sha 全扫零命中+旧过滤流与新文件逐字节等价 17,756,205 对 mismatch=0+zcat 独立复数)。**产物**(`0905/datasets/`):canonical `images.v2.jsonl.gz`=日期件 20260926 硬链(2,105,850,340B,md5 `cf356e79…`);**丢失账单永久件** `images.v2.20260926_lost.jsonl.gz`(887,403 行原文整行,109.9MB,md5 `6ad0074e…`)——按需补下载依据(refs.orig_url 在行内,wm 系死链率<1%,下载校验成功后走新日期件+回滚链回账);回滚件 `archive/images.v2.20260924e.jsonl.gz`(md5 `2d9f7d13…`)。**更正**:交接所述 sgcos kb/ 权威副本已随 0925 非图清理 1,038 键删除(nonimg_deleted.tsv 995/1011 行),出账底本=湖侧件;出账后重传 sgcos 因跨境劣化停滞,用户指令取消,**未完成分片会话已中止清零(复核 0,kb/ 无任何账本键)**,湖侧为唯一副本。待拍板:140,235 全损 qid 是否补抽、manifest_1m_v2all.tsv 是否同步瘦身。坑:sgcos 桶名本地无档,实测为 `sgcos-1256345599.cos.ap-singapore.myqcloud.com`(同 appid 凭据直用)。

## 2026-09-26 凌晨:补下载任务清单与交接(用户裁定另一会话执行)
丢失 887,403 张的分析结论:94.6%(839,192)带 orig_url 可补;88.7% ≥1024px;NC 仅 0.16%;实体全局失明 142,069/5,131,518(受累 288,285)。**任务清单已切好** `collect/redownload_20260926/`:精准口径 redownload_blind_qids.tsv.gz(143,462 行,失明实体优先)/全量口径 redownload_full.tsv.gz(839,192)/nourl_unrecoverable.tsv.gz(48,211 不可补留档),均带 md5 边车;交接文档 `HANDOFF_20260926_补下载.md`(流程=下载→sha256 校验→回传 sgcos kb/<blob_path>→死信账→版本化回账;红线=84,184 救援与 282,724 搬运独立、账本只读)。旧样本产物用户已裁定废弃,重采样须从新 canonical(md5 `cf356e79…`)重建,勿用 assets/wh_backfill 旧底本(0924e)。

## 2026-09-26 凌晨:实体层权威件升级落位 datasets/
sg_cos_mirror 拉回件中够格的已升级到 `0905/datasets/`(湖侧唯一副本,镜像原件保留):**实体层 5 件**=qid_edges.tsv.gz(2.54亿边,md5 ebe448a0)+qid_class_labels.tsv.gz(447万标签)+qid_concepts.fat.jsonl.gz(783万实体)+wd_meta_bridge.nt.gz(1210万三元组)+**wd_b4_bridge.nt.gz(490万图源桥,另一会话补拉,P4947/P4983/P4985/P846/P3151)**,均带 md5 边车;**meta_dumps/ 9.6GB 15 件**整目录升级(GeoNames/GADM/Getty×3/OSM×8)。说明文档 datasets/实体层权威件说明.md,datasets/README.md 已挂行。**留镜像未升级待拍板**:pages-en×20+pages-zh(28.9GB 文档语料,collect 管线上游)、kb/manifest_snapshots_0923(4.7GB r机账快照,审计材料)。重采样三源齐备:实体层(datasets/)+图片账本(images.v2 20260926 版)+文档语料(镜像 pages)。

## 2026-09-26 凌晨:qid↔数据源匹配资产盘点(重大更正+补升级)
**重大更正:156GB Wikidata dump 未丢**——`/root/wd_full/latest-all.json.gz`(156,133,651,298B,Sep 22)在本机!(此前 MEMORY"随 sgx 退租丢失"记错;当时本机为抽 sitelink 下载过)。但 gzip -t 校验 **GZIP_FAIL**:表观 156GB 实占仅 60.8GB 块(稀疏残件=下载中断),**不可用**——属性级重匹配仍需重下 dump;已抽取的桥表/concept_xref 在 datasets/,不受影响。**匹配资产存续图**:已升级 datasets/=images.v2(qids+refs.external_id,经验匹配总账)+wd_meta_bridge/wd_b4_bridge(属性桥)+**concept_xref.tsv.gz(25,475,205 行,自 _staging/raw/ 升级,三钥匙表唯一幸存件)**;/root/wd_full/sitelink_qids.txt.gz 是**中断残本仅 283 万行**(全集 3,841 万失联,可从 dump 重抽);失联可再派生=qid_images_ext(可从 images.v2 重导)/qid_image_roles 66MB(sgcos 非图清理删,nonimg_deleted:1027)/mid_to_file 1.43亿+sdc_depicts 5332万(原 Commons/SDC dump 派生,可重导)/qid_gallery_captions。**待拍板**:156GB dump 是否挪 datasets/(现 /root 有 2.4T 空闲,不急)。

## 2026-09-26 凌晨:pages 文档语料升级落位
用户拍板(采集管线无独立 datasets/,统一进项目公共 datasets/):**pages-en×20+pages-zh 升级 `0905/datasets/pages/`**(21 件 29.1GB,en 19,262,554 页+zh 3,007,321 页,湖侧唯一副本),21/21 字节对清单核对+合并 md5 边车 .md5sums,说明文档 datasets/pages_语料说明.md,README 已挂行。至此 datasets/ 齐:**图片账本(images.v2 20260926)+实体层 6 件+meta_dumps 参考语料+pages 文档语料**,KB 三大件(实体↔文档↔图片)全部权威化。

## 2026-09-26 si 重匹配+目标类过滤+clean 账本转正
- **si name 兜底重匹配**: concept_xref P225 索引(398万学名)重挂 1,091,127 media(95.4%, 二名法81.7万/属级19.8万/三名法2.0万/全串5.6万), 验证 230/230 exact_P225; 同名消解=生物桶>GBIF键>取小; 教训: 原始taxon匹配自身有错(雪鸮→乌林鸮), 三名法必须带rank记法查
- **修账链**: canonical(0925版, 已备份 images.v2.20260925_pre_clean.bak.gz) → si_rematch(删109.7万旧边/写106.0万新边) → clean(目标类过滤再删29.0万边; 11垃圾类=页面/数字/年份/姓氏系, junk_entities.tsv 256万实体; si残余24.6万源于gen_si同media多QID聚合)
- **clean 版已转正**为 assets/wh_backfill/images.v2.jsonl.gz(18,643,608行不变, 边30,775,180); COS 双活暂缓(用户裁定)
- **口径变化**: QID宇宙537.5万/短边实体379.4万(+33%)/生物桶+38%(427k实体,750万图)/Q15633587页面类→81实体/过200类3,657/配额291
- **新产物**: rerun_v2_20260925/ 下 si_rematch_map2.tsv, apply_audit.tsv, filter_audit.tsv, qid_cut_map_v4_5000_ext.tsv(566.9万QID逐实体类目映射, 补上此前未决项), project_1m.json(重采样投影: 1,001,616实体/588万图/10.5TB边计)

## 2026-09-26 上午:账本两线合并 20260926b(当前权威)
- **发现正交修订冲突**: 另会话 02:1x 出账版 20260926(剔 887,403 丢失图,datasets/)与本会话 clean 版(si 重匹配+目标类过滤)同底本(20260924e, md5 2d9f7d13 逐位一致)互不包含;本会话先前在 assets/wh_backfill 的就地转正违反 datasets 规约,已纠正(该目录恢复 20260924e 原状,不再承载权威)
- **合并 20260926b**: clean 版剔除 887,403 丢失 sha → datasets/images.v2.20260926b.jsonl.gz(17,756,205 行, md5 b21e0725),canonical 名硬链已指向;回滚链 20260926b→20260926→20260924e;验证:行数/丢失零命中/**与 20260926 出账版 sha 集合完全等价**(两线交叉验证成立)
- **合并版口径**(重跑全链): QID 宇宙 523.2 万/短边实体 364.6 万(丢失去账使 355,691 旧短边 QID 无图)/过 200 类 **3,247**(较 clean 版-410,丢失拖累)/配额 352/边 27,776,844
- **重采样三口径投影**(seed=42 试抽, 1,001,366 实体, project_1m_mrg.json): A 全取短边=606.8 万图/10.53TB(边计); B 每实体封顶8=229.2 万图/3.98TB; C 每实体主图1张=100.1 万图/1.74TB; definitional 标记覆盖率 26%(4,619,599/17,756,205 行)
- 待用户确认: 口径 A/B/C、目标规模、在途 relay1m 队列处置;确认后 30-60 分钟出 manifest_1m_v4

## 2026-09-26 上午:dump 判死+官方重下+v6 重切
- **老 dump 判死**: /root/wd_full/latest-all.json.gz(09-22 装配版)全流实测仅解压 199GB/~1.15TB(17%)即断(crc32 mismatch 两轮复验);run.log 证实=09-23 判死的湖侧装配版,今晨"未丢"说法只对了一半
- **官方重下进行中**(用户拍板): dumps.wikimedia.org latest-all.json.gz 156,251,408,231B(09-24 版)→ datasets/wd_full/;首次 16 连接触发 429 惩罚(教训:wikimedia 限连接数),现冷却后 2 连接无限重试+落地自动 gzip 校验(后台任务);镜像状况: bringyour 503/accum 无 entities/your.org 同尺寸但慢
- **v6 重切完成**(新宇宙 512.8 万有图实体, 5千档): 3,657 类/零超限(最大恰 5000)/零纯QID标签(447万标签全量接入)/91% 类在[200,5000];但 **36.1% 实体(185万)在 426 个强制分段类**(演員×26/畫作·无体裁值×27/人類·无职业值 452k...)——谓词覆盖不足所致
- **已备好**: extract_newpreds.sh(P27/P17/P577/P170 抽取)+balance_cut5x.py(AUX_CHAIN 扩展,P170 平铺);dump 落地→抽取→v6r2(预计分段占比→<10%)
- 采样口径已定(用户裁定): A 全图/不限短边/不封顶/不限制

## 2026-09-26 上午:fleet 下载 dump 成功但回程受阻(挂起中)
- **fleet 下载完成**: 20 台 r 机(r1-r20, ubuntu 用户, ~/dump_slice)全部下完 wikidata latest-all.json.gz(09-24 版, 156,251,408,231B), 300 个 512MB 块断点校验齐全(fleet_parts/tasks.json 有分片表);单机入向 3-6MB/s, 复刻了当初 fleet_slice 方案
- **回程三条路全堵**: ①r→湖直连 scp 仅 ~40-90KB/s/机(机器被其他会话任务占带宽+湖直连入站窄) ②COS 中继用户否决(费用) ③湖直连 wikimedia 仍被限速(惩罚 >3h 未解, 21KB/s;当日 16 连接触发的 429 教训:wikimedia 按 IP 长窗惩罚)
- **当前挂起态**: 礼貌回收器 /tmp/fleet_puller.sh(每机单流+块级断点+全齐自动装配校验, fleet_parts/pull.log)慢速爬行;备用线 direct/aria2c 单连接慢爬;用户去找空闲机器
- **教训**: pkill/pgrep -f 会匹配到包装 shell 自杀, 一律用 /proc cmdline 精确匹配或按 PID; 湖机 ssh ProxyCommand 走代理(/tmp/proxyconnect.py)已验证可用但带宽无改善
