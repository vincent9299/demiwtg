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
