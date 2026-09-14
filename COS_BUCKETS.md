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
