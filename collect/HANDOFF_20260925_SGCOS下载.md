# 交接:本机从 SG COS 拉回 39.75GB(39 件)

**任务**:把 `HANDOFF_20260925_SGCOS下载清单.tsv`(key\t字节数,39 行,总 39.75GB)全部下载到本机 `/yzp/zhaozy/yangzepeng/0905/sg_cos_mirror/<完整key路径>`,逐件字节数校验。

## 访问约束(必读)

1. **桶按 IP 白名单**:本机直连 `lhcos-368f6-1256345599`(ap-singapore)一律 403;**只能从 r 机(r1-r20,ssh 别名已配)访问**。COS 凭据:r1 `/tmp/cos_creds`(也在 `~/wk_backfill/.cos_creds`)。
2. **ssh 劣化窗口**:本机↔r 机单流 ~25KB/s,19 机并行聚合 ~0.5-1MB/s(时好时坏)。39.75GB 预计 11-22 小时,需护航循环(断了重启,sleep 轮询)。
3. 根前缀 `lhcos-data/demiwtg-data/`(清单里已省略,拼 key 时加上)。

## 现成工具(都已部署过,若 /tmp 被清按命令重装)

- r 机侧(19 台):`/tmp/ci/cosio.py`(demiflow 的 cosio 模块)+ `/tmp/pull_range.py`(参数:key start end,Range GET 写 stdout,自带 3 次重试+长度自校验)。重装命令:
  ```
  for h in r1 r2 ... r20; do ssh $h 'mkdir -p /tmp/ci'; scp <demiflow>/demiflow/collect/cosio.py $h:/tmp/ci/cosio.py; scp /tmp/pull_range.py $h:/tmp/pull_range.py; done
  ```
  (pull_range.py 源码在本机 /tmp/pull_range.py 或仓库 collect/qid_edges/ 交接附件)
- 本机侧:`/tmp/relay_pull.py` —— 多机 Range 分块调度器(16MB 分块、38 并发、断点续传 .parts、组装后校验、日志 /tmp/relay_pull.log)。用法:改脚本里的清单来源为 HANDOFF tsv(或直接把 /tmp/cos_keys_light.tsv 换成交接清单),`nohup python /tmp/relay_pull.py &`。
- 优先级建议:qid_edges(1.06G,唯一副本)→ concepts.fat/bridge → pages → meta_dumps。

## 校验与收尾

- 每件下载后对清单字节数;qid_edges 另有 md5 边车(key+.md5,湖里仓库 collect/qid_edges/ 已有本地副本可比)。
- 完成后把 HANDOFF tsv 的"已到"状态回写,并通知用户。

## 坑

- 别动 `blobs/`、`blobs-nc/`、`relay1m/`(另一会话的图库迁移活队列)。
- pgrep/pkill -f 会匹配自身 ssh 命令行(用 [x]yy 括号式)。
- r 机是租的,若某台退了就从 HOSTS 列表剔除。
- 拉 qid_edges 时如遇 COS 对象消失(清理进行中),立即停下报告用户。

## 完成记录（2026-09-25 08:45）
- **39/39 全部拉回并逐件字节级校验相符**（40.808GB，清单精确字节数为准；含 qid_edges md5 边车共 40 件）。
- 路线变更：白名单已放行本机，**未走 r 机中继**，本机直连 ap-singapore，3 并发单流 Range 断点续传（/tmp/direct_pull.py，日志 /tmp/direct_pull.log），聚合 8–12MB/s，07:26–08:43 共 77 分钟跑完。COS 外网下行流量消耗约 41GB。
- qid_edges.tsv.gz：字节 1,057,650,748 ✓，md5 `ebe448a03a88bdf77fb635270be5dc60` 与边车一致 ✓。
- 期间零 MISSING、零 GIVEUP、零重试；.parts 里旧中继会话的 images.v2 残块未动。
