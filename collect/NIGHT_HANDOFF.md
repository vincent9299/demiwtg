# 概念知识库·夜间护航交接(2026-09-11 00:40 定稿)

> 新窗口开场白:**"读 demiwtg-data/NIGHT_HANDOFF.md 接手夜间护航"**
> 总规划见同目录 CONCEPT_KB_PLAN.md(v6);本文件只管"今晚盯什么、坏了怎么救"。

## 一、使命

守护今夜全自动链,异常自愈/重跑,不加新特性不改架构;拓扑变更先报用户。

## 二、当前运行态(00:40 实况)

```
③ 过滤 🟢 运行中(23:35 起,props.jsonl ~122MB 且在涨)
   └ 完成后 run_phase2b_v2.sh 自动:增肥 → fat/graph gzip
      → scp 训练机 meta + cp 到 COS kb/ → 打 [relay2] 全部完成
→ fleet_launch.sh(已挂,轮询等 fat)自动:
   scp 概念文件到 24 机 → 25 分片起跑(r1~20=0~19,a~d=20~23,本机=24)
   → 全部直写 COS kb/blobs(桶策略已放开,20/20 r 机挂载验证过)
```

- 已完成:代码六文件全冒烟;COS 旧层已清(184,369 对象零失败);
  训练机器 lake_sync 已停(用户拍板);GZ 桶待用户控制台整删。

## 三、巡检操作手册(每 30-60 分钟一轮)

1. **过滤**:`wc -c ~/kb_night/qid_build/props.jsonl`(应持续增长);
   `ps aux | grep [b]zcat`(管道活着)。预计 ~01:00 前后完。
2. **增肥+发运**:`tail ~/kb_night/qid_build/run_phase2b_v2.log`
   出现 `[relay2] 全部完成` 即链成;失败看落盘标记逐段手工重跑(命令都在该脚本里)。
3. **fleet**:`tail ~/kb_night/qid_build/fleet_launch.log`(25 行"起跑");
   抽查 `ssh rN tail -3 ~/imgbuf/run/images.log`(有引擎统计行=健康);
   `ls /lhcos-data/demiwtg-data/datasets/demiwtg/kb/blobs | wc -l` 应持续涨。
4. **产物核验**:fat.gz 应同时出现在 训练机
   `/yzp/zhaozy/yangzepeng/0905/demiwtg/datasets/demiwtg/meta/` 与 COS kb/。

## 四、坏了怎么救(预案)

| 症状 | 动作 |
|---|---|
| props 不涨且 bzcat 死 | 看 run_phase2b_v2.log;过滤可整体重跑(幂等,props 由 enrich 归并去重) |
| 增肥/发运失败 | 脚本内命令逐条手工执行;失败标记行会写明哪段 |
| fleet 未起 | fat 到位后手工 `bash ~/kb_night/qid_build/fleet_launch.sh` |
| 单机 images.log 崩 | 重跑该机命令(幂等,manifest 续传);机器别名 r1~r20 |
| 429 大面积 | 正常重试耗尽会认缺计数,不救;明早补二遍跑即可 |
| 训练机隧道断 | ssh lake 不通时等几分钟再试(pod 重启自愈);fat 重发即可 |

## 五、关键路径速查

- 本机基目录:`~/demi/kb_night/qid_build/`(run_phase2b_v2.sh / fleet_launch.sh /
  cos_wipe_old.py / img_relay.py[已弃用] / props.jsonl / qid_concepts*.jsonl*)
- 代码仓:`~/demi/demiwtg-data`(flow_wikidata/flow_images/flow_docs.py +
  operators/{wikidata,commons,wiki_clean}.py);平台 `~/demi/demiflow`
- fleet 各机:`~/venv/bin/python` + `PYTHONPATH=~/demiwtg-data:~/demiwtg-flow`;
  账本 `~/imgbuf/qid_images-local.jsonl`(D2 合并)
- COS:SG 桶 `lhcos-368f6-1256345599`(策略已放开;kb/ 是语料+账本,勿动);
  匿名 API 从本机可用(LIST 注意:XML 无命名空间;前缀斜杠勿编码)
- 训练机器:`ssh lake`(无外网;meta 家;lake_sync 已停勿重启)
- 机器别名:r1~r20(新,内网 IP+lighthouse_key)、pipeline-a~d(老,直写)、本机=协调

## 六、本夜新增坑索引(勿再踩)

1. `pkill -f` 图案若与自己的 ssh/bash 命令串同文会自杀 → 用 `[x]xx` 方括号图案
2. 运行中的 bash 脚本不可改(bash 边读边执行)→ 改版先 kill 再换文件
3. COS LIST:前缀的 `/` 不要 percent-encode;返回 XML **无 xmlns**(别套 S3 命名空间)
4. Commons API 的 UA 联系方式必须格式完整邮箱(无点后缀 → WAF 403)
5. 匿名批删接口 403,单删 204 通(24 并发单删实测 470 对象/秒)
6. `bash -c` 配 xargs 的 $0 传参会含整行 → 显式循环最稳
7. cosfs 挂载是惰性的:挂上≠能写,写探针+桶侧 GET 双验才算数

## 七、明早(人工段)

1. 验收:fat/graph 落位+P18 覆盖率;25 机健康度;kb/blobs 增速与字节量
2. 部署 ⑤ 合流到训练机(发语料 29GB + venv;`flow_docs.py`,双语探针→全量)
3. D2 对账:四件套数字 + COS 水位(~4T 触发档位决策)
4. 段文件 43GB 可清(qid_build/truthy/ + 三节点 ~/truthy_chunk/)

## 八、人工门

加/停机器、动桶策略、动 kb/ 目录、改分片拓扑——先报用户拍板。
