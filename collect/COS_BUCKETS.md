# 组桶登记(2026-09-10,CN 释放前留档)

两组共享桶均为 **匿名(public_bucket=1)cosfs 挂载,无凭证文件**;
挂载点 `/lhcos-data`(桶内前缀 /lhcos-data),同一 APPID 1256345599。

| 组 | 桶名 | 地域 | 挂载节点 |
|---|---|---|---|
| CN(GZ) | `lhcos-cee54-1256345599` | ap-guangzhou | pipeline-e~i(2026-09-10 释放) |
| SG | `lhcos-368f6-1256345599` | ap-singapore | sg-master、pipeline-a~d |

CN 节点释放后的桶管理:

- **2026-09-10 05:10Z:lake_sync 已摘除 cn 组并重启(单实例),设施不再读写 GZ 桶——删桶无阻碍。**
- 节点没了 → 没有 cosfs 挂载;清桶/删桶走 COS 控制台(需桶主腾讯账号)。
  匿名 API 实测 403(桶策略按来源 IP 限制,仅原 CN 节点可用),程序化删除不可行。
- 删除前可选抢救:`telemetry/`(JOURNAL.md 等 CN 组运维史,湖无镜像,控制台手down 即可,纯历史价值)。
  `meta/synced_shas.jsonl` 是 CN 组防重下账本,组已撤=废账,不必留。
- CN 桶残留:已回湖 blob 由 lake_sync cleanup 以 2 万/轮在删
  (节点存活期间继续);gz-restore 死尾 32,141 个从未存在,无残留。
- 湖侧留档:`/yzp/.../demiwtg/sync/gz-restore-parked-20260910.jsonl`
  (复原计划全量 1,137,313 行;其中 32,141 全集群无内容,放弃)。

SG 扩容提醒:新机配公网 IP + lake 侧 pconn 直连别名,
勿走 sg-master 跳板(现有 15 流挤跳板是 SG 慢的根因)。

---

# 两系拆分与广州桶新定位（2026-09-20 用户拍板，本节为现行权威设计）

## 定位（三句话）

- **SG 桶（lhcos-368f6）= qid 系专属**：kb/ 全家（blobs/batch2/qid_images_ext/sdc_fetch/主账本+概念层）+ raw/ + candidate/ + docs/。
- **本机（lake）= 旧系（images 池）专属**：`datasets/demiwtg/blobs/` 实体 + `meta/images.jsonl` 权威索引（290万行）；
  meta/ 内 qid_*.jsonl.gz 为工作副本（权威在 COS，2026-09-20 md5 已对齐）。
- **GZ 桶（lhcos-cee54）= 临时中转站**：只做"跨境免费通道的桥"，**用完即清**，不是备份。
  当前载荷：images 池补充图 88,899 张（2026-09-20 中继），回流本机完成并核验后即删。

## 跨境免费通道设计（为什么需要 GZ 中转）

本机直连 SG 桶公网 ≈2MB/s（跨境公网）；实测全矩阵：

| 路径 | 实测 | 计费 |
|---|---|---|
| 本机→SG桶(公网endpoint,20线程) | ~2-9MB/s | 计费(外网下行) |
| sg机→SG桶(公网endpoint) | ~100MB/s | 同区域腾讯骨干 |
| sg机→GZ桶(公网endpoint) | ~15MB/s/台 | 计费但走腾讯骨干 |
| cn1→GZ桶(**cos-internal内网endpoint**) | ~41MB/s | **免费** |
| 本机↔cn1(公司代理ssh) | ~20MB/s | 免费COS无关 |

→ 通道：`SG桶 →(sg1/sg2 拉+推,15MB/s×2)→ GZ桶 →(cn1 内网免费读+ssh回推)→ 本机`。
要点：**cos-internal 只对同地域生效**（cn1 在广州读广州桶✓；Lighthouse/CVM 跨地域均不可达）；
GZ 桶是"让跨境流量落地国内、再走免费内网"的桥墩。

## 目录契约（拆分后两侧终态）

```
SG桶 lhcos-data/demiwtg-data/
├── datasets/demiwtg/kb/{blobs,batch2,qid_images_ext,sdc_fetch,qid_*.jsonl.gz,pages-en/zh}
├── datasets/raw/{df20,inat,plantnet300k,pubchem,metmuseum,openimages,smithsonian}
├── datasets/candidate/（中间产物，如 wikimedia 三映射表）
└── docs/（历史交接/审计16份）
【已删完 2026-09-20 22:55】旧系 blobs 根树 439.7GB 全清：88,899 wm（回流核验后精确删，del_keys_exact.py ok=88899 fail=0）
  + 516,791 孤儿（用户拍板直删，用户侧 22:31-22:36 执行）；仅剩 ~250 个 0 字节目录 key（无存储占用，可顺手清或留着无碍）。
  kb/根 5 probe + meta 14 件此前已删；datasets/demiwtg/{meta,pages} 若仍有残件见 MEMORY.md 收尾待办。

本机 datasets/demiwtg/
├── blobs/（旧系实体~207万+wm回流88,899 = 池2,163,475 − 不可恢复死信18,026；2026-09-20 核验闭环：尺寸100%+sha256抽检30/30）
├── meta/{images.jsonl★, 概念层共用件, qid_*工作副本}
├── corpus/ + pages/
└── kb/blobs/（老collector存量，含毒blob，待并账后按引用计数清理）

GZ桶 lhcos-cee54 ← 【已清空 2026-09-20 22:50】中转使命结束：blobs 前缀 57,307 + tmp/sg_relay_test 3 + 试点残留(_meta/_ledger/_status/pan123-relay) 全部 0 对象；
  桶内仅剩 test/api-demo.txt（43B 桶开通演示文件）。未来重启 123pan 链时该桶复用作 pan123-relay/ 队列。
```

## 两系关系事实（2026-09-20 实测）

- sha 重合仅 2.1%（1,864/88,899）：混居 kb/blobs 的 2.4 万旧图不删（2.1% 被 qid 引用，删除收益 34GB/月≈6元 vs 风险不值）。
- 旧系完整性上限 = 池索引 − 死信（国内源死13,157 + r机wm死4,869，均 CDN 换图/死站不可恢复）。
- blob 内容寻址天然去重：两系同图只存一份对象。

## 坑与技巧（本日新增）

1. `pgrep -f`/`pkill -f` 模式会匹配执行命令的外层 shell 自己（当日4次事故）——一律改"脚本文件+按 /proc/exe 精确杀"。
2. COS ETag 含分片后缀（`-6`）时 ≠ md5，比对内容用 GET 后算 md5。
3. 服务端复制：`PUT + x-cos-copy-source: /<bucket>/<key>`，零流量零内存，大对象搬家首选。
4. Lighthouse 机器连不上 cos-internal（即使同地域）；内网 endpoint 实测仅 CVM 同地域可行（cn1✓）。
5. r9 的 ssh 数据通路长期劣化（重启无效），大文件走 COS 中转（对象上传 tmp/ 再自拉），ssh 只发命令。
6. 时区差：本机日志时间戳=UTC，sg 机/cn1=UTC+8（sg1 文件 mtime 与本机日志相差 8h）——比对 auth.log/mtime/日志排查并行操作时必须先对齐（`date +%s` 两边跑）。
7. 批量删除脚本：cos_util._call 返回**三元组** (st, headers, body)，解包成两个会 ValueError 全 fail（当日 mass_del 两版 ok=0 事故）；且 ok/nf/fail 总数必须等于清单行数才算跑完。
