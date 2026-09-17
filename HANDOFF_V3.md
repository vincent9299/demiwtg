# 传输线交接 V3（2026-09-17 11:10 定稿，接手方：训练机器侧）

> 开场白建议："读 HANDOFF_V3.md 接手传输线护航"
> 前序：HANDOFF_V2.md（前任→本窗口）、RESUME_2026-09-17.md（暂停快照+重启手册）、SHIP_STATUS_2026-09-14.md（过程实录）

## 一、一句话现状
传输线（COS→湖 882 万图）按用户指示**已干净暂停在 82.02%**：
湖 7,236,412 / 账本 8,822,987，缺失 1,586,575，账本外杂散 0。
暂停原因：用户判断"882 万图可能有问题"，**等修复方案定夺后重传**（路径见 §四）。

## 二、本窗口已完成（相对 HANDOFF_V2 的增量）
1. **真根因修正**：清单路径少 kb/ 段（非单纯限频）；24 机 cosfs 全换认证挂载（/etc/passwd-cosfs 600，cos-reader 子账号）
2. **drip_ship2 v5**：pipefail/严格tar/DEST=datasets/demiwtg/逐块1000验数/隧道自愈/随机退避；分块 %24（v3 的 %25 漏 353 块）
3. **隧道基建**：湖侧 /root/tunnel_keepalive.sh（**仍在跑**，30s 错峰重建+超龄换血）；湖 sshd MaxStartups→300:30:600；coordinator 同样已调
4. **护航自动化**（coordinator 侧 kb_night/，已全停待重启）：watch2（5min 巡检+救活）、night_scaleup（双门槛自动扩缩）、watch2_supervisor（保姆）、tunnel_guard、night_sweep（定时总扫荡）
5. **网络画像实测**：湖出口唯一通路=公司代理 10.127.48.4:3128；单流 8.75MB/s / 8 流 11.6 / 16 流塌 2.3 / 请求-响应延迟 3-18s（湖直拉 COS 方案已验证废弃）；白天 30-60 张/s，深夜 00:00-05:00 可达 90-150

## 三、湖侧资产位置（接手者直接可用）
| 路径 | 内容 |
|---|---|
| `datasets/demiwtg/kb/blobs/` | 已传 723.6 万图（82.02%），每块经 1000/1000 计数验数 |
| `datasets/demiwtg/kb/.resume/` | **权威对账四件**：ledger_paths.txt（882.3万全量）/ lake_paths.txt（暂停时点实有）/ **missing_paths.txt.gz（缺失 158.7 万）** / extra_paths.txt.gz（空） |
| `datasets/demiwtg/meta/qid_images.jsonl.gz` | 账本（path 字段=blobs/xx/sha.ext，page_bytes=COS 对象字节数，已抽样三方核实） |
| `/root/tunnel_keepalive.sh` | 8 条反向隧道保活（22022-22029→湖:2222，经 pconn.py 代理），**唯一在跑的设施，勿动** |
| `/root/lake_pull.py` | 湖直拉 COS 实验件（因代理请求-响应延迟废弃，留档） |

## 四、后续工作（按优先级）
1. **等用户澄清"图片问题"**：哪一侧、什么症状。然后二选一：
   - **路径 A（源图问题，全重传）**：湖清 kb/blobs/* + 各机清 shipdone → 按 RESUME 手册 §四 runbook 重启
   - **路径 B（已传 82% 有效，续传缺失）**：用 missing_paths.txt.gz 生成新块清单（sha 取模分块）→ 各机 minichunks2 替换 → 清 shipdone → 发射
   - **路径 B'（部分已传图损坏）**：先全量字节校验（page_bytes 或 sha256，湖本地可做）精确定位坏图 → 只重传坏集
2. **重启护航**（coordinator 侧 kb_night/）：tunnel_guard → watch2 → night_scaleup → watch2_supervisor（顺序！保姆最后）→ launch_drip5
3. **收官对账**：传满后重跑 .resume 的 diff 流程，要求缺失=0、多余=0
4. **收尾杂项**：cos-reader 密钥轮换（CAM）；湖顶层 demiwtg/blobs/ 三个残渣目录清理（待用户拍板）；与 vLLM 共存协调（白天负载敏感）

## 五、红线（沿用 HANDOFF_V2，仍然有效）
- COS kb/ 永久不动（本线只读）；湖旧 blobs/pages/meta 旧四件不动；lake_sync 勿启
- 湜侧概念集 = EN∪ZH 有页面（782 万）
- 湖上 vLLM 服务是共存租户（load 30+ 时段避开大扫描）
- /yzp 是 80T 共享卷（曾 98% 满，现 ~5.4T 可用），大批量写前查 df

## 六、血泪教训（操作前必读）
1. **pkill 自匹配六连坑**：pkill 与任何含目标明文名（drip_ship2.sh/tunnel_keepalive.sh/watch2_supervisor.sh...）的命令**必须拆成两次调用**，括号模式只保护模式文本本身
2. watcher 会神秘自死（原因未明，无 OOM 痕迹）→ 保姆 supervisor 必须常驻
3. 代理惩罚突发 CONNECT：隧道重建务必错峰（keepalive 已内置）；重连风暴会压垮全部隧道（症状：全队同秒 got=0）
4. 探测命令陷阱：`pgrep -c X || echo 0` 在计数 0 时双重打印（pgrep -c 打 0 且退 1）→ 字段错位；直接用 `pgrep -c X` 即可
5. 隧道假死症状：TCP 通、SSH banner 无、湖侧进程在——kill coordinator 侧 sshd 属主（ss -tlnp 查 pid）触发湖侧重建
6. 速率窗口规律（公司代理）：白天差、深夜好；震荡（扩张→塌→缩编）本身浪费管道，稳态 4-12 台常优于满编 24 台

## 七、协作界面（coordinator 侧坐标）
- 机器：VM-0-14（10.3.0.14 / 公网 43.160.250.196），ubuntu 用户
- 脚本目录：/home/ubuntu/demi/kb_night/（launch_drip5/watch2/night_scaleup/tunnel_guard/ship_watch/night_sweep/watch2_supervisor）
- 日志：/home/ubuntu/demi/demiwtg-data/logs/ship_watch.log（全部决策留痕）
- fleet：r1-r20 + pipeline-a~d（~/.ssh/config 有全部别名，lighthouse_key/ship_key）
- COS 密钥：各机 /etc/passwd-cosfs（cos-reader，只读；待轮换）
