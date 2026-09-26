# 传输线修复状态（2026-09-15 00:45 更新，接手窗口实录）

## 一句话
fleet 96 worker 稳定运行 ~143 张/秒（网络管道实测上限附近），逐块 1000/1000 验数，ETA ~16h，三重守卫自动护航。

## 网络管道画像（00:00-00:40 实测结论，重要！）
湖→外部唯一通路=公司代理 10.127.48.4:3128，行为：
- 单条长流 8.75MB/s；8 条并行合计 11.6MB/s；16 条并行塌缩 2.3MB/s
- 请求-响应模式每请求延迟 3-18s（湖直拉 COS 方案因此废弃，湖侧 lake_pull.py 已停）
- 结论：管道只适合"少量长流式"，fleet 的 tar|ssh 长流架构正好匹配
- 143 张/秒 ≈ 13MB/s 已贴上限；再想快只能让网络团队给湖入口提 QoS（10×杠杆）


## 比交接文档更深一层的根因
HANDOFF_V2 说根因是"匿名限频→负缓存→stat 失败"。实际验证发现**更根本的问题**：
fleet 机清单 `~/minichunks2/m*.txt` 指向 `/lhcos-data/demiwtg-data/datasets/demiwtg/blobs/`，
但 COS 里真实文件在 `.../datasets/demiwtg/kb/blobs/`（少一段 kb/）——旧路径下只有目录占位符。
tar 配 `--ignore-failed-read` 对着不存在的文件打出空包还报成功。限频负缓存是叠加因素，路径错误才是主犯。

## 修复清单（全部已落地）
1. **认证 cosfs**：24 机全部换认证挂载（/etc/passwd-cosfs 600 + cos-reader 子账号，去掉 public_bucket）。
   每机 5 个挂载点（/lhcos-data 及 data2~data4 用于 worker 并行读）。
2. **清单修正**：全部 8824 块 sed 为 kb/blobs/ 路径（幂等，noKB=0 已验证）。
3. **drip_ship2 v5**：pipefail；删 --ignore-failed-read；DEST=/yzp/zhaozy/yangzepeng/0905/demiwtg/datasets/demiwtg
   （落地 kb/blobs/xx/sha.ext，与账本 path 直对应）；分块 n%24（v3 的 %25 会漏 353 块）；
   flist sort -u + 湖侧 tar -v 提取计数==清单行数才标 done（1000/1000 精确验货）；
   每次尝试前重估 -L 隧道；重试随机退避 20-45s。
4. **隧道基建**：湖侧 tunnel_keepalive（30s 轮询重建 22022-22029）；
   coordinator 侧 tunnel_guard（2 分钟探测 SSH banner，连续 2 次失败杀挂死 sshd 属主触发湖侧重建）；
   湖 sshd MaxStartups 10:30:100 → 300:30:600（96 并发重连不再随机丢）。
5. **coordinator 磁盘**：清掉 /tmp 前朝中转残留（pieces 26G/merge2 6G/shipimg 3.3G），95%→48%。

## 为什么是 82 张/秒（不是预估的 3~14h 收官）
湖→coordinator 唯一通路是公司 HTTP 代理（10.127.48.4:3128，pconn.py）。
实测：fleet 8 隧道总吞吐 ~7.5-8MB/s 顶格，新开隧道分不到 0.8MB/s——代理总带宽硬墙。
COS 侧已无限制（认证后单机 SDK 可 75+ 张/秒），瓶颈纯在网络。
82 张/秒 × 91KB ≈ 7.5MB/s。剩余 ~860 万张 → ETA ~29h。加隧道/加 worker 无效（已实测）。

## 护航体系（全部在跑）
- 湖：/root/tunnel_keepalive.sh（root，30s 轮询）
- coordinator：kb_night/ship_watch.sh（5 分钟巡检 24 机 done/fail/存活，死机自动重发，8824 齐则退出，48h 超时）
- coordinator：kb_night/tunnel_guard.sh（挂死隧道探测自愈）
- 日志：demiwtg-data/logs/ship_watch.log、tunnel_guard.log；各机 ~/ship.log

## 红线遵守情况
COS kb/ 未动（只读）；湖旧 blobs/pages/meta 未动；lake_sync 未启；lake 上 vLLM 共存服务未干扰（load 33 容忍）。
湖 /yzp 80T 共享卷剩 2.0T，本任务还需 ~0.75T，装得下但要留意其他租户。

## 教训（今晚 pkill 自匹配踩了三次，三种伪装）
1. 命令行含明文 drip_ship2.sh + pkill 'drip_[s]hip' 同场 → 自杀（括号只保护模式文本本身）
2. cp /tmp/.../drip_ship2_v5.sh ~/drip_ship2.sh 与 pkill 同一条命令 → 同上
3. pkill -f "tunnel_keepaliv[e]" 后跟明文 /root/tunnel_keepalive.sh → 同上
铁律：**pkill 与任何含目标明文名的命令必须拆成两次 ssh/exec**。

## 收官后待办
1. 终局对账：湖 kb/blobs 文件数 vs 账本 882.3 万；随机抽样 sha 比对 COS 字节数。
2. 残渣清理：湖顶层 demiwtg/blobs/ 三个错误目录（前朝+本轮早期）待用户拍板。
3. CAM 轮换 cos-reader 密钥（已在聊天明文过）。
4. 湖侧 codex 活动范围协调（vLLM 共存）。
