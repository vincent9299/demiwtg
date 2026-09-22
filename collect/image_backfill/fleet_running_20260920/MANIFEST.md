# 第 1 批舰队在线代码快照（2026-09-20 收编）

本目录 = r1-r20 `~/wk_backfill/` 当日在线运行字节的原样快照
（全舰队 20 机 md5 逐一核验一致；r9 为当日重装后按本套重部署）。
用途：还原点 + 与湖侧开发版的对照基准。**补机/重装以此为准整目录拷贝。**

## 与湖侧 image_backfill/ 开发版的关系

| 文件 | 在线 md5 | 湖侧状态 |
|---|---|---|
| queue_worker.py | 91485c47 | 同（09-20 晚补丁：失败不标 done/释放认领；list_prefix 瞬态重试） |
| kb_backfill / kb_rate / kb_transport / cos_util / cos_stock_upload / fleet_pending_all / make_pending / test_kb_backfill / *_watchdog / *_restore | 见左 | 全部一致 |
| fleet_curl.py | 0d00fbda | **湖侧为部署后改版**（hub 路径迁移 state/curation→checkpoints；ec3a661c）——未随 fleet 重发 |
| fleet_dl.py | e5f70a10 | **湖侧为部署后改版**（UA 中央分配表 + --ua 覆盖；9837bb0a）——未随 fleet 重发 |
| launch_one.sh | 70300326 | 湖侧原缺，已补入 image_backfill/ |

注意：kb_backfill 动态 import fleet_curl——若用湖侧新版 fleet_curl 重部署，
其 hub 路径指向 `checkpoints/hub/`（r 机需同步该目录或依赖其回退链）。

## 同日零散收编（直接放入 image_backfill/，湖侧原缺）

| 文件 | md5 | 来源机 | 说明 |
|---|---|---|---|
| launch_one.sh | 70300326 | r1（19 机有） | 单 worker 启动器 |
| repair_ledger.py | 5815e886 | r5 | 账本修复工具 |
| h2test.py | 137643a6 | r7 | http2 探针 |
| proxybench.py | d6cdfa3c | r7 | 代理基准测速 |
| proxycheck.py | 084af472 | r7 | 代理可用性检查 |
| ab_referer.py | 5e7ef30f | r20 | baidu/huaban Referer 适配 |
| pan123.py | d5ac2c32 | cn1 | 123pan 客户端（cos123_relay.py 的 import 依赖） |

## 中继线（sg1/sg2/cn1 `~/pan123-relay/`，在跑）

| 文件 | md5 | 湖侧 image_backfill/ |
|---|---|---|
| cos_relay_push.py（sg1 --shard 0/2、sg2 --shard 1/2 在跑） | 8dc9ea65 | 一致 ✓ |
| run_pan_relay.sh | 83c62ffd | 一致 ✓ |
| cos123_relay.py（cn1 在跑） | 9b5c8203 | 一致 ✓ |
| run_pan_consume.sh | 1e92d861 | 一致 ✓ |
| pan123.py | d5ac2c32 | **原缺已补** ✓ |
| creds.json / token.json / spool/ | — | 凭证与运行态，**不入库**（母本见机器） |

## 平台侧（demiflow，机制归引擎）

在线机制已沉淀 `demiflow/collect/{cosio,cosqueue,queue_runner}.py`（2026-09-20，
12 用例 + 全 suite 125 过 + 真 COS 冒烟）。新采集线只写批算子，
不再 fork 本目录的 queue_worker.py。
