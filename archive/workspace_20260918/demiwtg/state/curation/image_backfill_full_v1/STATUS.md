# 全库补图本机运行状态（2026-09-16 暂停）

## 现状（用户要求暂停，进程已全部停止）

- 分片 0/9：成功 621（含 17 行多写者窗口重复）、死信 588，进行到约 900/18714 行
- 分片 1/9：成功 109、死信 317，进行到约 400/18714 行
- 死因：DeterministicError 为主、ProxyError 次之、sha_mismatch 少量；死信集中在 wikimedia/wikimedia_zh
- 已验证：抽样的 wikimedia 死信 URL 走代理可正常下载（200），死信多为共享代理 IP 触发限速，适合跑完后 `--retry-dead` 复活
- 速率：单分片约 0.7-0.9 行/s；两分片并行可翻倍（瓶颈是源站限速，非代理带宽）

## 本机运行环境（与 SG 版差异）

- 代码副本：`/yzp/zhaozy/yangzepeng/0905/demiwtg-data/image_backfill/`
  （backfill.py + operators/{download,search,domain_sources}，从 SG ca07981b 取回，
  md5 见 code_version.md5；**backfill.py 删掉了代理清除逻辑**——本机 CN 外网必须走
  HTTP(S)_PROXY=10.127.48.4:3128，删掉则全部请求瞬态失败、0 落盘）
- Python：`/yzp/zhaozy/yangzepeng/0905/env/bin/python`，demiflow 用本仓
  `/yzp/zhaozy/yangzepeng/0905/demiflow`
- 候选：`candidates_deduped.jsonl.gz`（168,424 行，本地实存扣重为 0）
- blob 直接落权威区 `datasets/demiwtg/blobs/`（SHA 闸门 + 原子写，已验证无 0 字节冲突）

## 续跑命令

```bash
cd /yzp/zhaozy/yangzepeng/0905/demiwtg-data/image_backfill
PYTHONPATH=/yzp/zhaozy/yangzepeng/0905/demiflow:/yzp/zhaozy/yangzepeng/0905/demiwtg-data/image_backfill \
nohup /yzp/zhaozy/yangzepeng/0905/env/bin/python -u -m backfill \
  --candidates /yzp/zhaozy/yangzepeng/0905/demiwtg/state/curation/image_backfill_full_v1/candidates_deduped.jsonl.gz \
  --shard I/9 --dataset /yzp/zhaozy/yangzepeng/0905/demiwtg/state/curation/image_backfill_full_v1/run \
  --blob-root /yzp/zhaozy/yangzepeng/0905/demiwtg/datasets/demiwtg \
  --concurrency 24 --log-every 100 >> .../shardIof9_attempt1.log 2>&1 &
```

幂等续跑：done ∪ blob 实存自动跳过；`--retry-dead` 可复活死信（wikimedia 限速类值得）。
全量 168,424 张按当前双分片 1.6 行/s 估约 29 小时；建议 2-3 分片并行 + 完成后统一 retry-dead。
注意：**kill 要杀 python 子进程本体**（ps 查 `-m backfill`），杀 nohup 包装进程杀不掉。

# 2026-09-17 深夜改造：COS 直传 + 动态节拍 + 出口唯一 UA（未启动，等指令）

- **COS 直传**：fleet_curl 新增 `--cos-prefix lhcos-data/demiwtg-data/datasets/demiwtg/blobs`
  （deploy_all/deploy61/ip_recovery 已带）。SHA 过闸 → PUT → ETag(md5) 复核 → 删本地腾盘；
  重启幂等：COS HEAD 命中即跳过下载补账本（cos_only:1）；上传失败本地保留记 cos_fail:1，
  由存量直传补。凭据 `~/wk_backfill/.cos_creds`（600，部署器从 staging/.cos_creds 下发，
  /tmp/cos_creds 为易失回退）；模块 cos_util.py（COS_SCHEME/COS_HOST/COS_BUCKET 可注入测试）。
- **存量回收改道**：`cos_stock_upload.py` 在 r 机跑（HEAD 跳过/ETag 校验/--purge 腾盘，
  产出 cos_stock.done.tsv / fail.tsv），替代 collect_fleet.sh 的 tar 单流回收；账本 done.jsonl
  仍需小体量 rsync 回中枢。
- **动态节拍（AIMD）**：fleet_curl `--rps-start 0.25 --rps-min 0.08 --rps-max 0.45`；
  25 连胜+0.02；429/5xx 减半+尊重 Retry-After；窗口 60 过半且占比>5% 熔断 600s×2^n（上限 1h）。
  变速写 meta/rate.log。fleet_dl 直连路线改 --concurrency 1 --rps 0.15（无 governor）。
- **出口唯一 UA**：hub/ua_assign.tsv（assign_uas.py 生成）= 61 代理 + 20 r 机 ↔ 81 条
  诚实清单（含 PKU-YuanGroup-fetch/1.0）一一对应；代理密码 fiXcBpP8ty（单P）已修 4 个池文件。
  下载器默认查表（--proxy→proxy:ip，直连→ua.env→host:主机名），部署器同步池表+ua.env。
  线上验证：代理/公司代理三路 httpbin 回显 UA 逐字一致。
- **验证状态**：mock-COS E2E 5 场景过（含 fail-fast）；r5 真凭据 PUT/HEAD/DELETE 200/200/204；
  r 机硬件 2C3G/59G，r1(100%)/r10(97%) 磁盘红，先跑存量直传腾盘再接增量。

# 2026-09-17 夜间护航终报（守护 agent 2.2h 收官）
- 增量：done 唯一 19,272 / 唯一待下 ~19,289（源 r_r* 原始 25,315 行含跨机重复）
- 429：全程 AIMD trip=0；首轮 220 条 429 死信 → 重试轮(rq_*，0.15保守节拍+新out-dir)复活 236/253
- 最终死信 17：11×http:404（源已删）、5×sha_mismatch（内容变更）、1×curl:63 → 留给 alternative_urls 阶段
- 质量：随机20张签名Range-GET抽检 OK 20/20；全量下载1张 SHA256 与权威清单逐字节一致
- 存量直传：20 台 DONE，fail=0 corrupt=0；skip 的已存在文件未删本地（可选清理）
- 账本已回收 state/curation/image_backfill_full_v1/closing/（r*.jsonl + rq_r*.jsonl + final_dead.json）
- 遗留：国内 39,809 行待用户开国内机（DOMESTIC_PENDING.md 不变）；r 机磁盘已大幅释放(r1 100%→64%)
