# 概念知识库·全景确认稿 v6(2026-09-11,全直写:25 IP 分布式写 COS,零内部搬运)

> **终态快照(2026-09-14 补记)**:①~④ 及档2 全部收官——
> 图片 **882.3 万唯一文件 / 0.772TB**(P18 主图 223 万、正文内嵌 310 万、
> 属性图 62.5 万、P935 图库 79.4 万、档2 有语料扩展 182.5 万,五波合计);
> 账本 qid_images(886.1 万行)双落位 COS kb/ + 训练机 meta/;语料 28GB
> 已同步训练机 corpus/;图片字节向训练机的副本搬运收尾中。
> 采集器与提取器已晋升入仓(flow_images_batch.py + tools/);
> 平台升级(demiflow 活性层+BatchMapOp)已实现,见 demiflow/UPGRADE_PLAN.md。
> 事故全档:logs/night_watch_2026-09-12_14.log。


> **意图**:用 wiki/Wikidata 原料跑完 增肥→配图→合流,得到一套与旧体系
> "同构"的概念知识库——**meta 账本层新旧并行各一套,真数据层共用同一个池**。
> **数据归宿原则**:账本入训练机器 meta;真数据入 COS;训练机器不留真数据、
> 旧数据一个不动、事后按需抽样取数。(~~湖机器~~→统一称**训练机器**)

---

## 一、已经干完的

### ① 概念正文入库(Phase 1)✅
- **为什么**:概念库根基是百科正文;dump 快照一次拿全 1,900 万概念。
- **输入**:enwiki dump 26.8GB + zhwiki 3.6GB(官方全站快照,用完已删)。
- **动作**:flow_kb.py 解析 → wikitext 结构化 → 百万行一 part 压缩上传 COS。
- **输出**:COS kb 桶 `pages-en-part1~20.jsonl.gz`(1,926 万行)+
  `pages-zh.jsonl.gz`(300.7 万行);行含标题/页号/分节正文/分类/内链
  (qid 字段实证全空——正文里没人写自己的编号)。

### ② 概念骨干(Phase 2 前半)✅
- **为什么**:页↔QID 的对应在维基 page_props 登记表,不在正文。
- **输入**:page_props(471MB+54MB,与语料同日 20260901 快照)+①语料。
- **动作**:抽映射(en 1,032 万/zh 218 万)→按页号精确 join→双语折行。
- **输出**:训练机 `meta/qid_concepts.jsonl` **7,826,266 行**
  `{qid, en/zh:{标题,页号}}`(双语 91.2 万/仅en 630.9 万/仅zh 60.5 万)
  + COS gz 备份。

### ③ 代码与基建(今夜)✅
- 六个新文件全部 demiflow 风格、全部冒烟通过:
  ③ `operators/wikidata.py`+`flow_wikidata.py`(filter/enrich)
  ⑤ `operators/wiki_clean.py`+`flow_docs.py`(清洗→passages→pages池+账本)
  ④ `operators/commons.py`+`flow_images.py`(API+原图+守门+fleet分片)
- 实测坑留档:Commons API 的 UA 联系方式必须是格式完整邮箱(否则 WAF 403)。

---

## 二、进行中(00:0x 实况)

```
段下载 ✅ 字节精确到账(限速尾巴已由 b/c 绕行补齐)
→ ③ 过滤 🟢 运行中(23:35 点火,预计 ~00:40 完)
→ 增肥 → fat/graph 发训练机 meta + COS
→ fleet_launch v2 自动(已挂):25 下载 IP 起跑 + 五路中转常驻
```

## 三、还没干的(③增肥跑完即解锁两项)

### ③' 骨干增肥(Phase 2 后半)🟡 自动链进行中
- 输入:维基数据精简文件 43.4GB + ②的 782 万 QID 集合。
- 输出:增肥版 qid_concepts(p18/p373 字段+覆盖率报告)+ qid_graph.jsonl
  (P31/P279/P361/P527 边,顺路产物),落训练机 meta + COS。

### ④ 概念配图(Phase 3)🟡 等概念文件,机器已全就位
- **范围拍板**:全量 782 万概念按 QID 系统扫(长尾优先级平等,双语不插队);
  **嵌图路整体移出**;原图档,>10MB 降 1200px 缩略图记 tier。
- **Fleet 拓扑 v6(25 下载 IP 全直写,2026-09-11 0:2x 定稿)**:
  - 下载 25 分片:r1~r20=0~19(本地缓冲);a~d=20~23(直写 COS);
    本机=24(直写 COS)——**天花板是 Wikimedia 每 IP 礼貌限速,
    25 IP 满开,预估 25-50 张/秒,250-350 万张 1-1.5 天收完**;
  - **桶策略已放开(r 机挂载齐全,20/20;http/https PUT 实测 200)——
    25 机全部直写 COS kb/blobs,零内部搬运,五路中转架构作废**;
    账本各机本地 ~/imgbuf/qid_images-local.jsonl,D2 合并去重。
- **流量口径**:流量包只计出站且下载为入站,r 机月包消耗 <1GB。
- 桶策略注意:SG 桶是 IP 白名单制(r 机匿名 GET/PUT 均 403 实测),
  中转架构不依赖加白;若日后加白可切直写,非必需。
- 输出:COS `kb/blobs/`(目标 250-350 万张)+ 全局 `kb/qid_images.jsonl`;
  **COS 升 5T 后水位检查点:用量过 ~4T 触发档位收紧决策**。

### ⑤ 正文合流 ❌ 代码就绪,待部署
- 输入:COS kb 29GB 语料(一次性发训练机)+ 增肥版骨干。
- 动作:训练机 192 核逐行清洗切段→质量门→pages/aa/sha(url).md+记账;
  **产物打包回 COS**(kb/pages/,~35GB,隧道 ~50min),训练机只留 meta。
- 输出:训练机 `meta/qid_docs.jsonl` ~870 万行(path 指 COS)+ COS pages 池。

---

## 四、数据归宿一页图

```
COS 桶(升 5T;清旧数据,保留 kb/ 语料与账本)
├── kb/pages-{en,zh}*.jsonl.gz     语料素材(原地)
├── kb/blobs/aa/sha.ext            图片真数据(④产出,新家)
├── kb/pages/aa/sha.md             文档真数据(⑤产出回传)
├── kb/qid_images.jsonl            全局图片账(中转器/直写机汇入)
└── kb/qid_concepts.fat.jsonl.gz 等 备份

训练机器(原"湖")
├── meta/qid_concepts(.fat).jsonl / qid_graph.jsonl / qid_docs.jsonl  账本唯一家
├── 旧四件 + 旧 blobs/pages        一字节不动
├── lake_sync 常驻                  已停(2026-09-10 用户拍板:采集已死,
│                                    无事可做;图不经它;明早合流独占 I/O)
└── 临时工作区                      任务完即清
```

## 五、机器分工与流量守则(谁干什么,流量从哪走)

**设计原则(2026-09-11 撤约束后)**:速度优先——天花板是 Wikimedia
每 IP 礼貌限速,故下载 IP 满开(25 个);出 COS 上行按白名单机五路分担。
流量包只计出站且下载为入站,不再构成任何约束(r 机月包消耗实测口径 <1GB)。

**机器清单与属性**:

| 机器 | 属性 | 流量 |
|---|---|---|
| 本机(sg-master) | 2c/7G,COS 白名单,协调者 | 无限制 |
| pipeline-a~d | 4×2c/3G,COS 白名单 | 无限制 |
| r1~r20(新) | 20×2c/3G,非白名单 | 出站 1536GB/月+30Mbps;入站/内网免费 |
| 训练机器 | 192c/2T,无外网(仅隧道) | 不计(隧道) |

**逐阶段分工**:

| 阶段 | 有限机器 r1~r20 | 无限机器 a~d | 无限机器 本机 | 训练机器 |
|---|---|---|---|---|
| ③ 段下载 | —(不参与) | a/b/c 各下载一段(入站免费) | 本机两段(入站免费) | — |
| ③ 过滤/增肥/发运 | — | 仅供段文件(内网 ssh,免费) | 全部计算;产物→训练机(隧道)+COS(白名单) | 收 meta |
| ④ 下图 | 分片 0~19:Commons 下载,字节落本地 | 分片 20~23:下载+直写 COS;**兼中转**(各带 4~5 台 r 机) | 分片 24 直写 COS;兼中转(r1~4)+协调 | — |
| ⑤ 合流 | —(不参与) | — | 从 COS 读语料(入站)→隧道发训练机;回收产物→上 COS | 192 核清洗计算,产物经隧道回 |
| 对账/监控 | — | — | 全部 | 盘点 |

**r 机流量实测口径**:每台出站仅 API 请求头(千字节级/次),1536GB 月包
预计消耗 <1GB;30Mbps 峰宽是出站口径,入站下载不受其限。

## 六、排期

| 时间 | 干什么 |
|---|---|
| 今夜(自动) | 段齐→过滤→增肥→fat/graph 发运→fleet 24 分片起跑+中转器常驻 |
| D1 | ④全量下载中转;⑤部署训练机(发语料 29GB+依赖)并开跑(双语探针→全量) |
| D2 | 对账:P18 覆盖率/qid_graph 边数/qid_images 行数=COS blobs 实存/qid_docs 行数=pages 实存/COS 水位;段文件 43GB 可删 |

## 七、后置不做(按需再启)

嵌图路(整段移出,将来做法=对 P18 已收集合做差后增量)、P373 分类路、
别名(将来并入 qid_concepts 字段)、descriptions 完整版(全量 JSON dump
156GB,机具与 truthy 同套)、旧资产桥接(旧 290 万图挂 QID)、
抽样上传脚本(训练机器从 COS 抽样喂训)。

## 八、环境与地址备忘

- 训练机器:ssh lake(隧道);无外网;湖盘 80T 余 2.8T;
- venv:本机 /home/ubuntu/demi/.venv;fleet 各机 ~/venv(httpx/pyyaml/click/packaging);
  PYTHONPATH=~/demiwtg-data:~/demiwtg-flow;
- 基建脚本:~/demi/kb_night/qid_build/(dl_seg2 分段下载/dispatch_bc 派发校验/
  run_phase2b 接力/fleet_launch 启动器/img_relay 中转器/configure_rnode 初始化);
- 新机 ssh 别名 r1~r20(内网 IP,lighthouse_key);
- 桶:SG `lhcos-368f6-1256345599`(升 5T 中,清旧留 kb/);
  GZ 桶 `lhcos-cee54-1256345599` 已废弃待删;
- UA 联系方式:跑 ④ 前可 export DEMIWTG_CONTACT=<真实邮箱>(Wikimedia 规范)。
