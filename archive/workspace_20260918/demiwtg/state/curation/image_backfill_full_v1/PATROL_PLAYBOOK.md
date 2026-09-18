# 夜间护航轮训预案（agent 本体持续在岗，勿退出）

## 意图（用户 2026-09-17 拍板）
等占用机器的他任务释放 → 自动存量直传 COS 腾盘 → 部署增量下载（AIMD 节拍贴上限、
出口唯一诚实 UA、COS 直传 ETag 校验）→ 每 10 分钟巡检 × 10 小时；遇问题停机修正、
及时冷却；严查数据质量（空内容/错误内容）；全程不退出。

## 关键路径
- 守护脚本：`_staging/image_backfill_local/guardian.sh`（已启动，setsid 常驻）
- 守护日志：`demiwtg/state/curation/image_backfill_full_v1/guardian.log`（唯一真源）
- 巡检明细：`.../image_backfill_full_v1/guardian/`（stats_NN.txt / quality.log / EXITED）
- 我的巡逻日志：`.../image_backfill_full_v1/patrol_agent.log`（每档一行摘要）
- 部署器/代码：`_staging/image_backfill_local/`（deploy_all.py, fleet_curl.py,
  cos_util.py, cos_stock_upload.py, quality_probe.py）
- 待下源：`hub/pending_all/r_r*.jsonl`（25,315 行）；COS 前缀
  `lhcos-data/demiwtg-data/datasets/demiwtg/blobs`

## 每档动作（Bash 单次 ≤600s：sleep ~540 + 采集）
1. tail -30 guardian.log；检查 guardian 进程存活（ps + log mtime >15min 视为死）
2. 若 guardian 死：重启（cd staging && setsid nohup bash guardian.sh >> log），
   巡逻日志记 RESTART
3. 若 guardian/guardian/EXITED 出现：读尾部判定完成/到期，做收尾报告
4. 每小时第 6 档左右加深度抽检：ssh 2 台 r 机看 pz*.log 尾行 + rate.log
   trip/md 计数 + quality.log 最近 20 条非 OK verdict

## 处置预案（阈值外才动手，其余交给 guardian）
- guardian.log 出现重复 ERROR / deploy_all 连败 → 我介入：跑 deploy_all 前先
  `ssh rN "pgrep -f '[f]leet_curl'"` 抽查实况
- quality.log 出现 MISSING/EMPTY/BAD_MAGIC ≥3 → 停：pkill 对应机器 worker，
  保全 done.jsonl/日志，写警报到 patrol_agent.log，等用户（不猜原因）
- COS 凭据失效（put 连续 403）→ 全停，写警报
- 守护反复自杀（3 次）→ 我接管其 CRUISE 逻辑手动循环
- 严禁：改 UA 方案/节拍参数/COS 前缀/元数据；删任何 done 账本

## 结束条件
- guardian/EXITED=finished（全量完成）或 10 小时到期（02:40 前后）
- 结束后：汇总 patrol_agent.log + guardian.log 出终报（成功数/死信/质量/冷却次数）
