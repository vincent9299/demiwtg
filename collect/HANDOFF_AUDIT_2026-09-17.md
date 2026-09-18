# 交接文档：kb 图池质量事故审计与重收护航（2026-09-17）

> 移交方：sg 节点（pipeline 机，cosfs 挂载在用）
> 接收方：训练机器（lake）
> 性质：**882 万图池存在大范围 429 错误页污染 + 缩略图降级存量，需精确审计后重收**。
> 修复代码已就位未提交（本机工作区），审计在本机后台进行中，产物路径全部在 §4。

---

## 1. 事故定性（已实锤）

- **根因**：`flow_images_batch.py` 旧版 `download()` 不查 HTTP 状态码。
  2026-09-12~14 fleet 25 机跑配图期间被 Wikimedia 边缘 429 限速，
  ~2.1KB 的 "Wikimedia Error" HTML 响应体被 sha256 内容寻址当图写入
  blobs，账本记 tier=orig、sunk+1。实锤样本：
  `kb/blobs/de/de163a105f…4a.jpg`，内容 `Error: 429, Your bot is making
  too many requests`，`Sat, 12 Sep 2026 17:30:04 GMT`。
- **流引擎无辜**：`demiflow/collect/net.py` 的 `net.stream()` 本来就有
  状态码分类重试（429→30/120/300s 退避，非 2xx 不交给调用方）。
  只有批式采集器裸用 httpx 的路径有洞。`sdc_fetch_fleet.py` 自查安全
  （status==200 校验 + Retry-After 退避）。
- **波及规模的当前证据**（精确数字以 §3 join2 输出为准）：
  - 合并账本 8,861,354 行 / 0.772TB；COS blobs 8,866,002 对象。
  - **≤6KB 的唯一 blob 有 7,925,013 个（89%）**——错误页档案带。
  - 首批抽样：账本头部 3000 抽 86% HTML；候选前 5000 抽 96.6% HTML；
    **全量嗅探进行中实时命中率 ~99.8% HTML**（15 万样本）。
  - 即"882 万图"里真图可能只有 ~100 万上下，其余是错误页。
    （0.772TB 中约 756GB 在 93 万个 >6KB 的大图里，均值 ~800KB，
    与"真图平均数百 KB"自洽。）

## 2. 已完成的修复（本机工作区，未 commit）

| 文件 | 改动 |
|---|---|
| `flow_images_batch.py` | ① 补回 7 个缺失 import（此前一 import 就 NameError，fleet 跑的是已消失的 kb_night 副本）；② download() 重写：200 校验 + 429 按 RETRY_BACKOFF 重试 + HTML 首字节兜底（`HTML_ERR_HEADS`），新计数 `miss_html`；③ 只取原图（`info.url`），废除 >10MB 降级 1200px 守门；meta 查询删 `iiurlwidth` |
| `operators/commons.py` | 流引擎同口径：直取原图、tier 恒 orig、HTML/空体兜底（net 层本就拦非 2xx）；删 ORIG_GUARD_BYTES/THUMB_WIDTH |
| `backfill_orig.py`（新） | 重收工具：`--tier thumb1200` 筛账本，或 `--tasks-file` 直喂毒行清单；独立账本断点续跑、fleet 分片、`--hard-cap-mb` |
| `smokes/download_guard.py`（新） | 离线冒烟 9 断言全过（429 重试/用尽/404 直弃/200-HTML 拦截/超封顶/真图放行/流引擎同口径） |

注意：**只取原图后 64MB 封顶仍在**（内存上界=dl_conc×cap），数百 MB 的
巨物全景图会认缺；当年缩略图通道正是为它们设的，如必须收需磁盘流式改造。

## 3. 审计方法论（可全量复跑，不依赖 cosfs）

cosfs 随机读仅 ~110 文件/s（4 核 GIL+网络盘双重瓶颈），故全部绕行
**匿名 COS API 直连**（本机 IP 在桶白名单内；训练机是否放行需先验证，见 §6.0）。

步骤与脚本（都在 `/home/ubuntu/demi/raw/`，产物在 `/home/ubuntu/demi/raw/state/`）：

1. `python3 cos_inventory.py state/blobs_inventory.tsv 24`
   —— list-type=2 翻页列全量对象键+大小（190s 完成，8,866,002 对象）。
2. `python3 audit_join1.py` —— 账本×库存对账，产出：
   - **missing=0**（账本行全有对象）、**size_mismatch=1**
     （`blobs/32/321a1c…jpg` 账本 416,138B vs COS 0B，cosfs 残骸对象）、
   - **孤儿对象 43,015 个 / 2.24GB**（库存有账本无，多为 2144B 错误页——
     写了 blob 但账本行丢了，含那 1 条撕裂 JSON 行）、
   - `cand_small.tsv`（≤6KB 唯一 blob 7,925,013 个）、
     `cand_large_sample.tsv`（>6KB 随机 2 万）、
     `thumb1200_rows.jsonl.gz`（340,951 行，重收清单②）、
     `join1_stats.json`。
3. Range-GET 魔数嗅探（`cos_sniff.py`，前 2KB，class+错误码提取，**支持
   断点续跑**——输出已存在的行自动跳过）：
   **本机后台进行中**，4 lane × 24 线程 ~730 行/s，预计 ~3h 完成
   7.93M 候选。产物 `state/sniffout_00..07.tsv`（rel\tclass\terr）。
   ⚠️ 教训：进程拓扑必须是 4 进程×24 线程级别；8 进程×48 线程在 4 核上
   GIL 互踩直接停摆（328 连接 0 产出）。且后台进程必须用受管后台任务拉起，
   前台工具调用里 `( … &)` 起的进程会被会话回收杀掉。
4. `python3 audit_join2.py`（**待嗅探完成后跑**）—— 产出最终：
   - `poison_html_rows.jsonl.gz`（精确毒行，重收清单①）
   - `poison_other_rows.jsonl.gz`（empty/missing/err 异常行）
   - `audit_final_stats.json` / `audit_report.md`（分 tier/日期/错误码/
     账本十等分分布、去重任务数）
5. 大文件上界验证：`python3 cos_sniff.py state/cand_large_sample.tsv state/sniff_large.tsv 24`
   （join2 自动读取；HTML 页 ~2.1KB，>6KB 预期 0 命中）
6. 内容完整性抽样（**被中断未跑完，需重跑**）：
   `python3 cos_deepcheck.py` —— 400 全量 GET 重算 sha256 + 300 张 PIL
   全解码验截断/尺寸 → `state/integrity_sample.json`

## 4. 工件清单（交接收割）

本机 `/home/ubuntu/demi/raw/state/`（账本本地副本 5.1GB 在同目录
`qid_images.jsonl`，勿重复拉）：

| 文件 | 说明 |
|---|---|
| `blobs_inventory.tsv` | 全量对象清单（476MB，8.87M 行）|
| `cand_small.tsv` / `sniffshard_00..07.tsv` | 嗅探候选及其 8 分片 |
| `sniffout_00..07.tsv` | 嗅探结果（进行中，完成标志: 各 lane 日志见 SNIFF_DONE）|
| `rows_missing.jsonl.gz` `rows_sizemiss.tsv.gz` `thumb1200_rows.jsonl.gz` | join1 产物 |
| `poison_html_rows.jsonl.gz` `poison_other_rows.jsonl.gz` `audit_final_stats.json` `audit_report.md` | join2 产物（待跑）|
| `integrity_sample.json` | deepcheck 产物（待重跑）|

代码：`demiwtg-data` 仓库本机工作区（git status: M flow_images_batch.py、
M operators/commons.py、?? backfill_orig.py、?? smokes/download_guard.py，
**未 commit**）；工具脚本 `raw/cos_inventory.py cos_sniff.py audit_join1.py
audit_join2.py cos_deepcheck.py audit_blob_magic.py(旧版cosfs方案,弃用)`。

建议移交动作：本机 commit+push 后训练机 pull；或直接
`rsync -av raw/{cos_*.py,audit_*.py} state/ lake:~/kb_audit/`。

## 5. 训练机器侧执行序（接收后照此跑）

0. **验证匿名 COS 访问**（白名单是按 IP 的，不保证放行训练机）：
   ```bash
   curl -s -r 0-63 "https://lhcos-368f6-1256345599.cos.ap-singapore.myqcloud.com/\
   lhcos-data/demiwtg-data/datasets/demiwtg/kb/blobs/de/de163a105f035e7de8e4dff2b8a68eafd236fbe9aa897c4aaa815ba651b0c4a.jpg" -o /dev/null -w "%{http_code}\n"
   # 期望 206；403 则需从白名单节点跑审计/或给训练机配签名凭证
   ```
1. 等/收本机嗅探产物（或训练机自跑 §3.1-3.3，脚本可复用）。
2. 跑 join2 + 大文件抽样 + deepcheck（§3.4-3.6）。
3. **重收执行**（修复后的采集器，可用 fleet 分片；礼貌默认 api 2rps/
   dl 2rps/并发 4，单机口径）：
   ```bash
   # ① 毒行重收（数量以 join2 为准，抽样提示可能达数百万行，建议分批）
   PYTHONPATH=<repo> python3 backfill_orig.py \
     --tasks-file <path>/poison_html_rows.jsonl.gz \
     --blobs-root /lhcos-data/demiwtg-data/datasets/demiwtg/kb/blobs \
     --manifest qid_images-backfill-poison-shard-i.jsonl --shard i/N
   # ② 缩略图重收（340,951 行；原图均 >10MB，预计总量 ~5TB 级，先确认容量！
   #    巨物 >64MB 会认缺，如需收加 --hard-cap-mb 并核算内存=dl_conc×cap）
   PYTHONPATH=<repo> python3 backfill_orig.py \
     --ledger /lhcos-data/demiwtg-data/datasets/demiwtg/kb/qid_images.jsonl.gz \
     --tier thumb1200 --blobs-root …/kb/blobs \
     --manifest qid_images-backfill-thumb-shard-i.jsonl --shard i/N
   ```
4. 收尾清理（重收验收后）：
   - 并账：merge 时同 (qid,commons_file) 保 tier=orig 新行，剔旧 thumb/毒行；
   - 毒 blob（错误页 sha 名单在 poison 清单里）与孤儿 43,015 个对象、
     1 个 0B 对象按引用计数删除，**勿直接 rm 前缀**；
   - 撕裂账本行 1 条（`pid:25031539` 附近 `license_url": nul{`）重收或剔除；
   - sdc 通道（`datasets/demiwtg/blobs`，fetcher 有 200 校验）建议同样
     跑一遍 join1+sniff 抽查确认无污染。

## 6. 关键口径与风险提示

- **重收体量**：毒行数×原图均值(数百 KB) + 34 万缩略图×原图(>10MB)。
  缩略图项 ~5TB 级；毒行项视 join2 结果可能 TB~数 TB 级。先出数再排产。
- 下载礼貌红线：25 机 ×4rps 曾触发 429 连坐；重收建议 ≤8 机 ×2rps 起步，
  盯 `miss_html`（>0 即又被限流，降速）。
- 审计只读不写；所有删除动作留到重收验收后按引用计数做。
- 账本行 schema 未变（tier 字段保留，新行恒 "orig"），下游兼容。

## 7. 本机遗留运行物

- **已全部安全停机（2026-09-17 13:40，p1-p5 释放前）**。嗅探进度
  **7,272,840 / 7,925,013（91.7%）**：分片 00/02/04/06 完整（各 990,6xx），
  分片 01/03/05/07 完成至 ~826k/990k。
- 全部产物 + 工具已双备份：
  - **COS**：`/lhcos-data/demiwtg-data/audit/kb_images_20260917/`（2.6GB，
    含 8 片嗅探结果/候选清单/inventory/join1 产物/完整性抽样/日志/脚本，
    回读校验行数一致）；
  - **git**：审计脚本在仓库 `audit/`（本文件同 commit）。
- 脚本已支持 `KB_AUDIT_STATE` / `KB_AUDIT_LEDGER` 环境变量覆盖路径，
  r 系机器免改代码。

## 8. r1-r20 并行恢复手册（人工执行，按序）

**网络前提（2026-09-17 实测）**：训练机 lake **无外部出口**——GitHub
DNS 不通、COS 域名与 IP 直连均 000。因此：
- lake 只能做**离线计算**（join2 等，需先把 $A 工件拉到 /yzp，且
  /yzp 仅剩 **5.1TB**——重收的原图目标存储必须是 COS blobs，不能写 /yzp）；
- **剩余嗅探（~65 万行 Range-GET）与后续 Wikimedia 重收必须在有出口的
  机器跑**（r1-r20 若为出口机型则承担全部线上步骤）。每台 r 机启动前
  先跑 §5.0 curl 自检（期望 206），不通的机器别进队列；
- 文档+代码已直递 lake：`/yzp/zhaozy/yangzepeng/0905/demiwtg/collect/`
  （`HANDOFF_AUDIT_2026-09-17.md` + `audit/*.py`）。lake 无 GitHub 出口，
  **git pull 不可用**，以直递内容为准（与 sg 侧 origin/main bf73857 同源）。

0. 前置：每台 r 机验证匿名 COS 访问（§5.0 的 curl，期望 206；403 则
   整套审计只能从白名单节点跑）。挂载或可访问
   `/lhcos-data/demiwtg-data/audit/kb_images_20260917/`（下称 $A）。
   代码取 `audit/` 目录（或从 lake 的 /yzp 仓库副本分发）。
1. **领任务**（每台 r 机 k=0..19 不同值）：
   ```bash
   mkdir -p ~/kbstate && cd ~/kbstate
   cp $A/cand_small.tsv .                 # 658MB
   cp $A/sniffout_0{1,3,5,7}.tsv . && cat sniffout_0*.tsv >> my_out.tsv && rm sniffout_0*.tsv
   awk -F'\t' -v k=$k -v n=20 'NR%n==k' cand_small.tsv > my_slice.tsv
   # my_out.tsv 是续跑基线(自动跳过已嗅探的 91.7%), 本机只补自己切片的缺口
   python3 audit/cos_sniff.py my_slice.tsv my_out.tsv 24
   ```
   单机 ~40 万候选 × 大多已嗅探 → 实际只补 ~3.3 万，几分钟级。
   ⚠️ 每台并发别超 24 线程；20 台合计 ~480 并发对匿名桶是压测级，
   如出现 err 类先降线程。
2. **汇总**（任一台）：
   ```bash
   cat r{0..19}:~/kbstate/my_out.tsv 汇成一文件  # 或各自 cp 回 $A/merge/
   awk -F'\t' '!seen[$1]++' merged.tsv > sniffout_final.tsv   # 7,925,013 行
   cp $A/{blobs_inventory.tsv,rows_missing.jsonl.gz,rows_sizemiss.tsv.gz,\
   thumb1200_rows.jsonl.gz,join1_stats.json,sniff_large.tsv,integrity_sample.json} \
      ~/kbstate/ 2>/dev/null
   # join2 需要 state/ 布局: sniffout_final.tsv 改名 sniffout_00.tsv 放 ~/kbstate/
   KB_AUDIT_STATE=~/kbstate/ KB_AUDIT_LEDGER=<账本路径> \
     python3 audit/audit_join2.py
   ```
   （账本本地副本 5.1GB 未上传，用 kb/qid_images.jsonl.gz 解压，或
   KB_AUDIT_LEDGER 直指 .gz 时需先解压——join1/join2 读明文。）
3. 产出 poison_html_rows.jsonl.gz 等 → 按 §5.3 起重收（backfill 支持
   fleet 分片，注意礼貌限额与 miss_html 盯防）。
4. 已完成的参考结论（r 机不必重跑）：
   - >6KB 档 2 万抽样：HTML 仅 1 例 → 大图干净；
   - sha 400 抽样全对、PIL 300 抽样零解码失败（dims 不匹配 107 例中
     106 例为 thumb1200 语义、1 例 EXIF 方向）→ **池子无字节级损坏**；
   - 问题 = 错误页(≤6KB 档) + 缩略图(34 万行) 两类,无第三类。
