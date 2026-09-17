# 传输线暂停与恢复手册（2026-09-17 11:00 用户叫停）

## 一、暂停时点状态（权威数字）
用户指示"882 万图片可能有问题，修复后重新传"，已于 2026-09-17 10:55 干净暂停：
- 全部 worker 已停（24 机抽样 0 存活），全部自动化已停（watch2/scaleup/supervisor/guard）
- 湖侧隧道保活（lake:/root/tunnel_keepalive.sh）保留运行（仅维护管理通道，不传数据）

**湖侧对账（blobs 文件级，权威）**：
| 项 | 数 |
|---|---|
| 账本全量唯一路径 | 8,822,987 |
| 湖已落盘 | **7,236,412（82.02%）** |
| 缺失 | **1,586,575（17.98%）** |
| 多余（账本外杂散） | **0** —— 传输质量干净 |

## 二、恢复资产位置
**湖（权威）**：`/yzp/zhaozy/yangzepeng/0905/demiwtg/datasets/demiwtg/kb/.resume/`
- `ledger_paths.txt`（8,822,987 行，账本全量相对路径 blobs/xx/sha.ext）
- `lake_paths.txt`（暂停时点湖侧实有清单）
- `missing_paths.txt.gz`（**缺失 1,586,575 条**，恢复传输的直接输入）
- `extra_paths.txt.gz`（空）

**coordinator 备份**：`demiwtg-data/logs/missing_paths.txt.gz`

**各机 shipdone 快照**（仅供参考，权威以湖清单为准；r1-r8 含增援他机的重复标记）：
r1:516 r2:506 r3:494 r4:505 r5:427 r6:425 r7:418 r8:425 r9:368 r10:368 r11:368 r12:368
r13:362 r14:368 r15:364 r16:361 r17:78 r18:257 r19:259 r20:140 pipeline-a:84 pipeline-b:113 pipeline-c:166 pipeline-d:103

## 三、修复后重启的两种路径
### A. 若修复=源图问题（COS 里的图要重下/替换）
COS 是源头，湖侧全清重来：
1. 湖：`rm -rf .../kb/blobs/*`（确认红线后）；各机 `rm -f ~/shipdone/*`（注意 pkill 规则！）
2. 走 HANDOFF_V2 §五 的序列（本手册第四节的全套基建仍有效）

### B. 若修复=湖侧/校验问题（已传的 82% 有效或部分有效）
按缺失清单续传（推荐）：
1. 用 `missing_paths.txt.gz` 生成新清单（可按 sha 前缀或哈希取模重新分块成 m*.txt）
2. 各机 `~/minichunks2/` 替换为新清单 → 清 shipdone → 发射
3. 若只是部分已传图损坏：对 ledger_paths 全量做字节校验（page_bytes/sha256），坏的重传

## 四、重启 runbook（基建全部仍在，顺序执行）
1. **隧道**：湖侧 keepalive 在跑即 8 条自愈；coordinator 侧 `kb_night/tunnel_guard.sh` 需重启
2. **监控**：`kb_night/watch2.sh` + `kb_night/night_scaleup.sh` + `kb_night/watch2_supervisor.sh`（保姆，最后启动）
3. **发射**：`kb_night/launch_drip5.sh`（24 机 × 4 worker；清单/标记见上）
4. **定时扫荡**（可选）：`kb_night/night_sweep.sh`（清失败账全量重发）
5. 速率预期：白天 30-60 张/s，深夜 00:00-05:00 可达 90-150 张/s；网络墙=公司代理 ~8-12MB/s 总量

## 五、本次会话沉淀的关键教训（重启前必读）
1. **pkill 自匹配六连坑**：pkill 与任何含目标明文名的命令必须拆成两次调用（ drip_ship2.sh / tunnel_keepalive.sh 都栽过）
2. 隧道是"少量长流"型管道：>12 机并发会抖塌，靠 scaleup 双门槛自动找平衡
3. watch2 会神秘自死：保姆 supervisor 必须常驻（60s 巡检复活）
4. 代理 CONNECT 突发会被惩罚：隧道重建要错峰（keepalive 已内置 sleep 4）
5. 湖/coordinator sshd MaxStartups 已调 300:30:600（两台都已改）

## 六、待办（修复期间可做）
- 确认"882 万图可能有问题"的具体问题（哪一侧、什么症状）→ 决定走路径 A 还是 B
- cos-reader 密钥轮换（已在聊天明文过）
- 湖顶层 demiwtg/blobs/ 残渣目录清理（待拍板）
