# ops/ — fleet 运维件(策略归消费方, 与 demiflow 机制层分工)

| 件 | 作用 |
|---|---|
| launch_shards.sh | ④ 采集分片参数化发射器(全 fleet / 单机;幂等续跑) |

待并入(排期): 夜航 patrol/stallheal 的"账本增量判活+幂等重启"思想
并入本目录 patrol/supervise 工具族(与根目录 supervise.py 同族演化),
不平行新增 shell。
