# 总交接文档：概念多模态知识库 · 数据融合护航（训练机器接班版）

> 生成：2026-09-17 12:00 ｜ 交出方：数据融合夜航（ZCode 会话 @ VM-0-14/master）
> 接班方：训练机器侧
> 性质：整体护航交接——在途任务、基础设施、数据契约、坑与基准，全在此一份

---

## 〇、三十秒摘要

两天内完成：**353GB 原始数据集下载与内容级验证**（10 源）、**三张钥匙表**产出
（concept_xref 2547万桥行 / mid_to_file 1.43亿 / sdc_depicts 5332万）、
**四路融合入库**（DF20 202,336 边 + PlantNet 275,200 边 + SDC 补挂 2,288,880 边 +
PubChem 26,659 张，共 ~279 万新边、48.6 万新 blob，全部验证通过）。
**在途 3 件**（iNat 元数据关联 / Smithsonian 解析 v2 / OI+Met 关联未启动），
**外部依赖 2 件**（B 任务下载已另文交接、lake_sync 待拉起）。

---

## 一、全局架构（必读，含头号坑）

**COS 桶**：`lhcos-368f6-1256345599`（ap-singapore）
**⚠ 双树**：cosfs 挂载根 = 桶内 `lhcos-data/` 前缀。一切 API/路径必须带
`lhcos-data/` 前缀；桶根下的同名文件是历史误传副本（已迁移合并，以正树为准）。

```
datasets/
├── raw/                       ← 原始层（不可变镜像，只进不改）
│   ├── wikimedia/  mediainfo57块+truthy41块+image表18块+page表7块
│   │                + concept_xref.tsv.gz + mid_to_file.tsv.gz + sdc_depicts.tsv.gz
│   ├── df20/ plantnet300k/ inat/ pubchem/ smithsonian/ openimages/ metmuseum/
│   └── ../INVENTORY.md        ← 原料总账（需更新，见待办）
└── demiwtg/                   ← 组织层（KB 产品）
    ├── kb/qid_images.jsonl.gz      886万行老账本（wiki三路，勿改）
    ├── kb/qid_concepts.jsonl.gz    782.6万概念集（EN/ZH sitelink 口径）
    ├── kb/blobs/<2hex>/<sha256>.<ext>   字节池（882万老+48.6万新，内容寻址）
    └── kb/qid_images_ext/          ← 新融合账本（本轮成果）
        ├── df20.jsonl.gz  plantnet.jsonl.gz  sdc_attach.jsonl.gz  pubchem.jsonl.gz
```

**机器分工现状**：master(VM-0-14，SSH跳板勿重用) / a(投喂清所在地) /
b、c（COS 通道今日变慢，勿用于大读取） / d（42GB 空闲，最健康）。

---

## 二、已完成资产与验证结论（可直接信赖）

### 2.1 钥匙表（`datasets/raw/wikimedia/`）

| 表 | 行数 | 格式 | 验证 |
|---|---|---|---|
| concept_xref.tsv.gz | 25,475,205 | `QID\tP属性\t外部ID值` | P2581=103,261 vs 工单基准103,321（99.94%）；P662/P8814/P18 均 ~103% ✓ |
| mid_to_file.tsv.gz | 142,861,248 | `M-id\t文件名(下划线式)` | page.sql 全量 ns=6 抽取 ✓ |
| sdc_depicts.tsv.gz | 53,320,331 | `M-id\tQID\trank\tqualifiers` | 命中概念集 70.8%=37,699,560（两次独立复算一致）✓ |

### 2.2 融合账本（`kb/qid_images_ext/`，schema 统一）

行字段：`qid, sha256, blob_path, path, source, license, size_bytes,
relation_type, external_id, confidence, orig_path/orig_file, fused_at`

| 分片 | 行数 | 覆盖概念 | 新blob | relation_type | 验证 |
|---|---|---|---|---|---|
| df20.jsonl.gz | 202,336 | 878 真菌种 | 184,006 | definitional | 7项全过（sha256回读30/30、毒蝇伞959张→Q131227） |
| plantnet.jsonl.gz | 275,200 | 740 种（791 sid 去异名后=740，无缺失） | 275,200 | definitional | 6项过（回读15/15） |
| sdc_attach.jsonl.gz | 2,288,880 | ~百万级 | 0（零下载白挂） | depicts_part | 采样验证 |
| pubchem.jsonl.gz | 26,659 | 24,036 化合物 | 26,659（RDKit 渲染2D结构） | definitional(符号) | 双桥命中 25,998（97.5%） |

投喂清单（lake_sync 口径，均含 blob_path 字段）：
- a 节点 `~/lake/meta/image-shard-extdf20.jsonl` 202,336 行
- c 节点 `~/lake/meta/image-shard-extplantnet.jsonl` 275,200 行
- a 节点 `~/lake/meta/image-shard-extsdc.jsonl` 2,288,880 行
- a 节点 `~/lake/meta/image-shard-extpubchem.jsonl` 26,659 行
（lake_sync 拉起后按 glob `image-shard-*.jsonl` 自动尾随拾取，blob 实存即已同步）

### 2.3 原料层（353GB，全部内容级验证：解压实测通过，非仅尺寸）

DF20 108块（295,938图/1,604种实测）/ PlantNet 30块 / iNat 元数据 33块 /
PubChem 9块 / Smithsonian 46批（13,606片）/ mediainfo 57块 / truthy 41块 /
image表 18块 / page表 7块 / OI 标注 + Met CSV。

---

## 三、在途与待办（接班后按此推进）

### ③-1 iNat 元数据关联（卡在解 tar，差一步）
- 位置：尝试在 a 解 tar；**失败原因**：a:/tmp/cos_cat.py 是旧版（无 Range 断点续读，
  中途重试会重头重读导致流错位，tar 报 "not a tar archive"）
- **下一步**：把 `/home/ubuntu/demi/raw/cos_cat.py`（新版，master 上）scp 到 a 覆盖，
  重跑抽取（wildcard `*taxa.csv *photos.csv *observations.csv`，a 盘 19G 够）；
  然后 join：taxa→P3151+P225 双桥→概念集→photos/observations 链→**采图清单**
- 注意 b/c 节点今日 COS 通道慢，大读取放 a/d/master

### ③-2 Smithsonian 解析 v2（需按新认知重写）
旧解析结果作废（769 条、全空）。**已侦察到的真实结构**：
- 每分片 txt = **JSONL**（一行一记录），非单 JSON
- 记录字段：`id/unitCode/title/content.freetext.*/content.indexedStructured.*/content.metadata_usage.access(CC0等)/content.digital_assets_available(bool)`
- **indexedStructured 无 Getty ULAN/AAT/TGN 键**（工单假设不成立）——只有
  date/name/object_type/online_media_type；挂载须降级**名字匹配**（工单预案内）
- **图片本体在同一个 S3 桶的 `media/` 前缀下（可直接匿名 S3 同步，非 IIIF API）**，
  另有 `images/`（缩略图）与 `3d/`；记录↔media 文件名映射关系待 v2 解析时确认
  （找 digital_assets_available=true 的记录解剖）
- **下一步**：重写 smith_parse.py（JSONL 流式、抽 id/title/unitCode/license/
  digital_assets/name 列表），跑 d 节点（42G 空闲健康），产出记录表后再定挂载与 media 清单

### ③-3 OI 消歧（未启动）
MID→QID：P646 一对多（工单给了三条消歧规则：优先 EN sitelink → P279 取通用 →
字符串相似度）。产出命中率后决定 500GB 图片下载。输入：
`raw/openimages/oidv6-class-descriptions.csv` + `oidv7-train-annotations-human-imagelabels.csv` + concept_xref 的 P646。

### ③-4 Met 元数据关联（未启动）
MetObjects.csv（24.8万 PD 对象）× Artist ULAN URL 列（第27列）→ strip 出 ULAN 号 →
concept_xref P245 → 艺术家 QID（instance 级挂载）+ Object Wikidata URL 列直挂（461 个在概念集）。
图在 Met API（两段式采集器已写好：`/home/ubuntu/demi/raw/met_fetch.py`，需改 blob key 加 lhcos-data 前缀）。

### ③-5 台账收尾
- INVENTORY.md 更新（加四路融合结果 + media/ 前缀发现 + 本文档要点）
- 旧误传树（桶根）如确认无用可清理（约释放 200GB）

---

## 四、外部依赖与已交接项

| 事项 | 状态 |
|---|---|
| B 任务（SDC 新图下载） | **已交接**：`/lhcos-data/demiwtg-data/HANDOVER_SDC_FETCH.md` |
| lake_sync | **未运行**（湖 pod load 36 忙别的）。拉起命令：湖 pod 上 `python3 lake_sync.py`（常驻）。停多久都不丢——清单按字节偏移尾随，重启自动补 |
| WIT 27GB | GCS 对全机房 ASN 级限速 ~1KB/s，等解封（重试命令在历史里） |
| ImageNet/VisualSem/Rijksmuseum/Europeana | 等用户：HF token / 作者密码邮件 / API key |
| 概念集放宽（未决项①） | 等用户拍板。放宽后 SDC 可挂载边 +40%、Met 直挂 461→4.6万、PubChem/iNat 保留率翻数倍；**任务 A 重跑需切 4 机并行（sqlite 版脚本 sdc_attach3.py 就绪）** |

---

## 五、基础设施工具箱（master `/home/ubuntu/demi/raw/`）

| 脚本 | 用途 |
|---|---|
| stream_cos.py | 纯标准库 COS multipart 直传（签名已修：参数名小写）。用法：`cat x | python3 stream_cos.py <带lhcos-data前缀的key> <字节数>` |
| cos_cat.py | COS 对象流式读（**新版带 Range 断点续读**——部署时认准这版） |
| worker_v5.sh | 多节点分块下载 worker（支持 OFFSET 列，配合 stream_cos 零本地盘） |
| fuse_df20.py | 融合模板：tar 流→sha256→blob 直传→账本双落（线程本地连接+4线程上传） |
| fuse_plantnet.py | 同上 zip 版 |
| sdc_attach3.py | 任务 A sqlite 版（防 OOM、不赌输入排序） |
| fetch_generic.py / met_fetch.py | 通用 URL 采集器 / Met 两段式采集器（**met_fetch 的 blob key 需加 lhcos-data/ 前缀**） |
| xref_extract2.py + x4scan.sh | 按属性子集并行扫 truthy（4 机各 13MB/s） |
| audit_file.py / cos_head_check.py | 块级真值审计（cat|wc 读回）/ 预检（HEAD 尺寸） |
| page_parse2.py / sdc_extract.py / sdc_join.py | page 表解析 / P180 抽取（**注意 dump 是 JSON 数组、行尾带逗号**）/ 概念集 join 统计 |
| monitor_lanes.py / restart_monitor.sh | 值守看板 / 监控重启（用脚本文件重启，防 pkill 自杀） |
| pubchem_annot.py | 双桥重标注（want_cids 过滤版） |

凭证：各节点 `/tmp/cos_creds`（70字节 sid:key，来自 /etc/passwd-cosfs；**master 的 /tmp 会被清理，丢了从 d 节点拷**）。

---

## 六、血泪坑全集（按复发率排序，写代码前必读）

1. **双树前缀**：所有 COS key 带 `lhcos-data/`；桶根有历史脏副本
2. **blob_path 字段名**：投喂清单行必须 `json.loads(line)["blob_path"]`（lake_sync 只认这个键）
3. **pkill/pgrep 自杀**：kill 模式串出现在自己命令行里就先杀自己——用括号技巧
   `pkill -f 'xxx[y]'` 或脚本文件方式重启，绝不把目标名写进含 kill 的命令
4. **mediainfo/truthy 等 dump 是 JSON 数组**：行尾带逗号，`json.loads` 前先 rstrip(',')
5. **bz2/gzip 单流**：分块只能顺序流过（cat parts | zcat），不能从中间块解压；
   `latest-*` 是可变路径（吃过换版亏）——**Wikimedia 一律用 dated 路径**
6. **cosfs 三坑**：大文件直写静默截断（>100MB 必分块+读回校验）；目录/属性缓存
   延迟可达小时级（存在性判断用 API HEAD，别信 stat/ls）；刚上传的块其他节点看不到
7. **http.client 非线程安全**：多线程上传必须 threading.local 连接
8. **内存上限**：节点 3-7GB；千万行 dict 必 OOM——用 sqlite（WITHOUT ROWID+批量插入）
9. **下划线/空格**：page_title 下划线 vs 账本 commons_file 空格，join 前统一
10. **/tmp 会被清理**（master）：产物和凭证放家目录
11. **timeout+nohup 的"静默死亡"多半是没到首个 print 就被杀**：长任务先加心跳输出再诊断
12. 匹配检查用读回（cat|wc / 重新下载算 hash），stat/大小相等≠内容正确
13. b/c 节点 COS 通道今日变慢（原因未明），大读取放 a/d/master
14. master 是 SSH 跳板（多路 220xx 端口转发），勿跑重活

---

## 七、验收基准（复核用）

- sdc_depicts 总边 53,320,331；概念命中 37,699,560（70.8%）
- concept_xref P2581=103,261 / P662=1,376,841 / P225=3,995,578 / P846=3,325,203 / P646=4,470,508 / P18=6,408,208
- 融合总边 2,793,075（202,336+275,200+2,288,880+26,659）
- DF20 漏斗：295,938 → 203,151（99%种过桥，878种入概念集）→ 202,336 去重后
- PubChem 双桥 25,998/26,659
- 投喂清单四文件行数：202,336 / 275,200 / 2,288,880 / 26,659

---

## 八、建议的接班顺序

1. 拉起 lake_sync（解锁全部账本同步）
2. 修 a 的 cos_cat.py → iNat 抽取 → 关联 → 采图清单
3. Smithsonian v2 解析（d 节点）→ media 清单（media/ 前缀 S3 直取）
4. OI 消歧 + Met 关联（都是小 CPU 活，a/d 各一）
5. INVENTORY 更新 + 桶根清理
6. 催用户拍板：概念集放宽（影响最大）/ Met 采集 / HF token

祝顺利。历史会话里有每一步的完整上下文与命令记录。

---

## 九、接班后变更记录（2026-09-17，湖机侧实施）

> 本节由接班方（湖机）追加：**舰队重编号 + 全公网直连 + sg-master 降级**（用户拍板）。
> 上文 §一"机器分工现状"中的旧名（master/a/b/c/d）按下表换算。

### 9.1 新旧编号对照（25 台全量，2026-09-17 实测 metadata API）

| 新名 | 旧名 | 公网 IP | 内网 IP | hostname | 密钥 |
|---|---|---|---|---|---|
| p1 | pipeline-a | 43.160.215.28 | 10.3.4.14 | VM-4-14 | lighthouse_key |
| p2 | pipeline-b | 43.160.238.29 | 10.3.4.16 | VM-4-16 | lighthouse_key |
| p3 | pipeline-c | 43.160.201.131 | 10.3.0.17 | VM-0-17 | lighthouse_key |
| p4 | pipeline-d | 43.160.240.239 | 10.3.8.9 | VM-8-9 | lighthouse_key |
| p5 | sg-master(VM-0-14) | 43.160.250.196 | 10.3.0.14 | VM-0-14 | cluster_key |
| r1 | r1 | 43.156.233.114 | 10.3.4.17 | VM-4-17 | lighthouse_key |
| r2 | r2 | 43.156.225.41 | 10.3.12.11 | VM-12-11 | lighthouse_key |
| r3 | r3 | 43.156.246.155 | 10.3.12.10 | VM-12-10 | lighthouse_key |
| r4 | r4 | 129.226.214.105 | 10.3.8.4 | VM-8-4 | lighthouse_key |
| r5 | r5 | 43.156.91.233 | 10.3.12.5 | VM-12-5 | lighthouse_key |
| r6 | r6 | 43.156.135.168 | 10.3.4.13 | VM-4-13 | lighthouse_key |
| r7 | r7 | 43.156.63.213 | 10.3.4.6 | VM-4-6 | lighthouse_key |
| r8 | r8 | 43.134.87.246 | 10.3.4.12 | VM-4-12 | lighthouse_key |
| r9 | r9 | 101.32.108.237 | 10.3.8.14 | VM-8-14 | lighthouse_key |
| r10 | r10 | 43.156.96.37 | 10.3.4.2 | VM-4-2 | lighthouse_key |
| r11 | r11 | 43.163.101.40 | 10.3.8.2 | VM-8-2 | lighthouse_key |
| r12 | r12 | 150.109.13.134 | 10.3.4.11 | VM-4-11 | lighthouse_key |
| r13 | r13 | 43.159.45.36 | 10.3.12.4 | VM-12-4 | lighthouse_key |
| r14 | r14 | 43.156.132.95 | 10.3.12.2 | VM-12-2 | lighthouse_key |
| r15 | r15 | 43.156.75.127 | 10.3.8.11 | VM-8-11 | lighthouse_key |
| r16 | r16 | 43.133.36.106 | 10.3.4.7 | VM-4-7 | lighthouse_key |
| r17 | r17 | 43.156.96.115 | 10.3.12.7 | VM-12-7 | lighthouse_key |
| r18 | r18 | 43.156.112.107 | 10.3.4.8 | VM-4-8 | lighthouse_key |
| r19 | r19 | 43.134.10.196 | 10.3.4.3 | VM-4-3 | lighthouse_key |
| r20 | r20 | 129.226.83.4 | 10.3.4.15 | VM-4-15 | lighthouse_key |

### 9.2 变更内容（全部 2026-09-17 落地并验证）

1. **湖机 `~/.ssh/config` 全量重写**：r1~r20 + p1~p5 全部公网 IP 直连（pconn.py 代理 CONNECT），废除 sg-master 跳板与 ProxyJump；pipeline-e~i（已释放）条目删除。25 别名逐一冒烟通过（hostname 一一对应）。旧配置备份 `~/.ssh/config.bak-20260917-jumpera`。
2. **sg-master → p5 降级为普通节点**：不再承担跳板/指挥职责；当前仍在它上面跑的活——kb 审计嗅探（4 lane，重启后 11:44 重拉）与 `~/demi/raw/` 工具箱——**收割/备份完成后该机可退役**。
3. **机器间内网与隧道不碰**：10.3.x.x 互通、22022-22029 反向隧道（湖侧 `/root/tunnel_keepalive.sh` 维持）、各机 cosfs 认证挂载，全部原样。
4. **lake_sync 别名迁移**：`demiwtg/lake_sync.py`（湖侧运行副本）与 `demiwtg-data/lake_sync.py`（仓内副本）的 GROUPS/NODE_GROUP 改为 p1~p5；断点状态同步迁移——`sync/state.json` 节点键改名、`sync/manifests/{sg-master,pipeline-a~d}` 目录改名（e~i 死键/死目录留档）、`sync/merge_state.json` 镜像路径改名；迁移后校验全部偏移 ≤ 镜像文件尺寸（备份 `*.bak-20260917`）。**r1~r20 尚未入 NODE_GROUP**，接入 SDC r 机投喂清单时需补（见 AGENTS.md §7.2）。
5. **本地舰队脚本改名**：`_staging/sdc_fetch/fleet_relaunch.sh` / `fleet_stop.sh` 机器列表 pipeline-b/d → p2/p4。注意 p5 上 `~/demi/demiwtg-data/sdc_fetch/` 还有一份旧名副本，以湖机 `_staging` 版为准。
6. **文档同步**：demiwtg/AGENTS.md §7.1/§7.2 已按新拓扑改写；demiwtg-data/DEPLOY.md 重写为 25 机新口径。
7. **旧名换算注意**：历史文档/脚本中 sg-master→p5、pipeline-a~d→p1~p4；投喂清单位置更正：extdf20/extplantnet 在 **p3**（原 c），extsdc/extpubchem 在 **p1**（原 a）——§2.2 所写"a 节点 extdf20"有漂移，以本节为准。

### 9.3 新增红线

- **代理突发 CONNECT 惩罚**（HANDOFF_V3 实测教训）：对全舰队并发建连会触发，巡检/发射脚本必须错峰（顺序 + sleep）；8 条反向隧道共用同一代理出口，连坐会全断。
- 公网直连消耗各机 Lighthouse 流量包：控制命令是零头，**数据搬运仍走隧道/COS 通道**，勿经 ssh 直连推大文件。

### 9.4 p1–p5 释放归档（2026-09-17，用户拍板释放五机）

一次性资产已集中归档到 COS 正树：**`/lhcos-data/demiwtg-data/archive/p1-p5-release-20260917/`**
（清单/排除项/释放检查单见该目录 README.md；**230 件对象湖侧签名 HEAD 逐一校验尺寸全符**）。

- 内容：p5 工具箱 58 件 + 审计状态 74 件（blobs_inventory/cand_small/sniffshard×8/thumb1200/join1 产物/met/df20 中间件）+ kb_night 护航脚本 45 件 + git 未提交交接文档（HANDOFF_V2/RESUME/SHIP_STATUS）+ sdc_fetch 整目录 + p1/p3 投喂清单四件（extsdc 986M/extpubchem/extdf20/extplantnet）+ private/（cos_creds.p5/.p4、p5 bash 历史——WIT 重试命令在内）。
- 大文件（≥10MB）走 stream_cos.py 分片直传（避开 cosfs >100MB 截断坑），小文件 cosfs cp，全部经湖侧签名 HEAD 读回校验。
- 有意不归档：三张钥匙表（正树 `datasets/raw/wikimedia/` 有同尺寸权威副本）、qid_images.jsonl 解压版 5.1G（湖 meta/ 有 gz）、p2/p3 /tmp 残料、git 主线（origin@6fd4e0e）。
- 湖本地另留小副本：`demiwtg-data/{HANDOFF_V2,RESUME_2026-09-17,SHIP_STATUS_2026-09-14}.md`、`_staging/sdc_fetch/`（补齐 hist_ledger_stats.py 等）、`_staging/raw/`（工具箱部分）。
- **释放前置（未完）**：① 嗅探已按用户指令**停止并转为可恢复**——8 片中 00/02/04/06 完成且已归档校验，01/03/05/07 待跑（恢复手册+worker 已入归档 `RESUME_SNIFF.md`/`sniff_worker.sh`，任意 r 机 ~45 分钟/片，r 机匿名 COS 与 24 线程速率 ~380 行/s 已实测）；② lake_sync readers 迁 r 机（**r1 cosfs 读 `datasets/demiwtg/blobs` 树已实测可行**，256 前缀目录全可见）；③ 反向隧道 22022-29 迁 r 机（传输线续传依赖）；④ feeds/ 重落位到新 reader 的 `~/lake/meta/` 并把 r 机加进 NODE_GROUP；⑤ 在途任务改址：iNat 抽取（原 p1）/Smithsonian v2（原 p4）/OI+Met（原 a/d）→ r 机或湖本机。

### 9.5 ⚠ p1–p5 释放硬阻塞：image_backfill_full_v1 行动在跑（2026-09-17 13:15 发现）

终扫发现**另一活跃会话**正在跑全量图片回补行动，p1–p5 是其骨干，**释放必须等它收官或迁移**：

- **p5 = 调度中枢**：`~/lake/image_backfill_full_v1/`（candidates_{wikimedia,western,nonwm}.jsonl.gz + 46 分片 + fleet_curl/fleet_dl 工具），dispatcher 在往 r 机/p2-p4 派发分片
- **p2/p3/p4 + r10–r20 = worker**：`~/wk_backfill/fleet_curl_watchdog.sh`（fleet_dl.py --concurrency 3 --rps 4）
- **p1 = 节点备份**：`ship_node.sh` 把 `~/lake/meta/*` 清单逐件上传 COS `node-backup/2026-09-17/p1/`（head_cos 校验幂等）
- **湖侧配套**：`_staging/image_backfill_local/`（backfill/fleet_curl/fleet_dl/import_blobs.py，当日仍在更新）
- **p3/p4 有未提交 operators 改动**（concepts/page/search/text_engines.py）——是行动的活代码，释放前须收割
- 隧道（p5 落点）疑似被该行动的湖侧回流使用，迁移须选行动间歇

释放前置更新：等本行动收官 → 收割 p3/p4 operators → 隧道迁移 → 最后 sweep → 释放。
