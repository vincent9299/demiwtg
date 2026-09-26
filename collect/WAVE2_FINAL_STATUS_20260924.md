# Wave2 最终并账总账单(2026-09-24,Wave2 会话)
> 自包含:完成了什么、还剩什么、全部文件坐标。发给 wh_backfill 会话即可开工(第二节就是它的任务书)。
> 本文取代此前的 WH_STAGEB_HANDOFF_20260924.md(内容已全部并入)。

## 0. 一句话现状

三方会话冲突已裁定并执行完毕:权威 `images.v2.jsonl.gz` **16,016,943 → 18,657,248 行**,已发布(备份+版本化,非裸覆盖),三道验证门+v2c 独立交叉全部通过。**下一步是 wh 做增量补扫套补丁(第二节)**。

---

## 一、完成了哪些

### 1.1 冲突裁定与执行路线
- 权威 v2(会话 f16097ae 发布)为基座;会话 6e1ae346 的 v2c 边表降级为对账参照(四缺陷属实:缺 tmdb/gbif、边表违反一图一行、无 nc 分区、会覆盖权威版)。
- 路线 = 增量补丁(非从零重建),在 sgx 独立工作区 `~/merge_wave2/` 执行,sgx `~/MERGE_COORDINATION.md` 登记过执行权。

### 1.2 原料与基线锚定
- 66 件原料拉齐:权威三件、final_20260924/{si2,th1200,wm404}、快照 manifest_snapshots_0923(4.98GB)、met fix。
- 基线三方锚定:我的拉取 = 对方独立副本(/tmp/v2check.jsonl.gz) = COS 现件,md5 `37518d0d580662424372fc886b426700`,gzip 完整、行数 16,016,943 与发布报告一致。
- met 修复账本重传 COS(原副本 404):`kb/batch2/met/met_redo_fix.jsonl`,5,809 行,md5 `f1f3f1158af881e1d8eb63f0dd0b57a4` 回读一致。

### 1.3 四块增量落账(核心数字)

| 增量 | 处理 | 结果 |
|---|---|---|
| **si2** Smithsonian@1920 | 账本 3,341,407 行(含重领)→ 265.08 万唯一 extid,全 CC0/jpg;si1 原图优先 | **+2,640,447 新行**;10,311 撞 si1 跳过;0 无 qid 丢弃 |
| **th1200** 升级 1920 | 先对账后发现:权威版构建时(0923b 水位线)**已隐式吸收**——19.06 万行已是 thumb1920(sha 抽样 89% 与账本逐字节一致)、1.82 万行已是 orig | **0 补丁**(避免 orig→1920 倒退);6.74 万 cf 无主孤儿(有 blob 无账,救援属后续可选) |
| **met** 截断修复 | 按 extid 校验 old_sha 后换新 sha;新行自带实测宽高 | **5,744 换 sha**;65 行 old_sha 不符未动(行内已是别的修复;wh 侧确认截断库内为 0) |
| **wm404** 死链终局 | 48,706 任务全量裁决 + 逐行 blob 实存 HEAD | 恢复 **36 净新增**(22 Q 入主表 + 14 pid 入 sidecar;另 5 候选已在库跳过);**48,665 判死终标**;其中 **11,994 行"死链但 blob 活"保留主表未动**,仅 **121 真挂空**整行移入死信(带 moved_row 全量重载信息) |

### 1.4 总账变化
- 主表:16,016,943 − 121 + 2,640,447 + 22 − 43(桶内同 sha 吸收) = **18,657,248 行**(逐项精确闭合)
- qids:28,078,153 → **31,116,098**;refs:16,038,217 → **18,678,550**
- 分源终值:si 3,202,136 / wm 5,097,670 / sdc 5,134,018 / inat 3,564,704 / openimages 616,645 / tmdb **526,844 完整保留** / gbif **3,688 完整保留** / 其余不变——四条设计红线(一图一行、blobs-nc 分区、tmdb/gbif 在库、schema 不变)全部保住
- 死信:8,412,364 → **8,424,478**(+121 移入 +11,993 从 v1 补录);pid 死行 35,415 在 sidecar `quarantine/wm404_pid_dead_final.jsonl`;pid 恢复 14 行在 `quarantine/pid_additions.jsonl`

### 1.5 验证证据
- **G1 恒等**:行数精确闭合;refs/qids 差额 15/9 **精确等于**桶内吸收的并集去重量;死键闭包 48,665/48,665 全覆盖;移除 sha 在新主表残留 0
- **G2 抽查**:新 sha blob HEAD 400/400 存在;实下魔数 16/16 正确
- **G3 恒等**:未触碰行 byte 级一致 1,499/1,500(1 个为 met 换 sha 的旧值,本该消失);死信行数恒等;两份输出 gzip 完整
- **v2c 独立交叉**:si 挂接 3,906,669 vs 3,906,858(**偏差 0.005%**);inat 3,569,593 vs 3,582,546;oi 616,645 vs 616,752
- 死信 22 个 (qid,external_id) 重复键经查全部为基线存量(最大重复 5),非本轮引入

### 1.6 发布(先备份后替换,可回滚)
- 备份(三件验长一致):`kb/backup/images.v2.20260923.jsonl.gz`(1,735,152,758)、`backup/quarantine.deadletter.v2.20260923.jsonl.gz`、`backup/rebuild_report.20260923.json`
- 新件:canonical `images.v2.jsonl.gz` = `images.v2.20260924.jsonl.gz`(2,223,519,487,md5 `08be0f3a2651e4b77f74538f5824c924`);死信 canonical + 日期件(115,160,702,md5 `c9fa1d7d60cfc3b05f19f052ff6990b5`)
- 发布后回读:行数 18,657,248、md5 一致
- 全程报告(含验证细节)在 `kb/quarantine/rebuild_report.json` 的 wave2_verify 节

---

## 二、还要做哪些(按顺序)

### 2.1 【下一步,wh 的任务书】增量补扫 + 套补丁 + 重发布(GZ kb/wh_backfill/ 层)
1. **登记执行权**:sgx `~/MERGE_COORDINATION.md`(现无人占用)
2. **拉新主表并先校验**:md5 必须 = `08be0f3a2651e4b77f74538f5824c924`,行数 = 18,657,248(同区约 1 分钟)
3. **增量补扫新 sha ≈ 2,646,213 个**(si2 2,640,447 + met 新 sha 5,744 + wm404 22;**th1200 为 0**,别按旧预期排 27.4 万)。si2 全 CC0/jpg 且 width/height 全 null,应接近 100% 覆盖;"~40 分钟"旧估算作废重排
4. **套 wh_patch 重发布你们那层**。适用性明细:
   - sha 未变的约 1,601 万行:现有补丁 100% 继续适用(sha 键控,不受行数变化影响)
   - met 换 sha的 5,744 行:新行**已预填实测宽高**,你们"非空不覆盖"规则天然兼容;旧 sha 补丁条目作废
   - 121 个移除行:对应补丁条目成孤儿,可弃
   - 行序仍按 sha 前 2 位分桶(00..ff)拼接,schema 一字未改
   - 死信新基线 8,424,478(你们的 13,770 HTML additions 并入时以此为准)
5. **验收标准**(Wave2 侧事后会查):产出层行数对齐 18,657,248 或明确报告差集;新 sha 覆盖率 ≥99%;met 5,744 行宽高与修复账本一致;**不许触碰 SG 桶主表 canonical key**

### 2.2 【等 wh 发布后】统一清理
- 800 万毒 blob + 13,770 HTML 残渣 blob(先并死信再删,避免悬空引用;wh 的 deadletter_additions 并入也在此步)

### 2.3 【将来 wh 层转正前】
- 给并账代码补"非空 coalesce、时间新者胜"规则(format/size_actual/ext_match 等新字段,否则静默丢字段)

### 2.4 【可选小尾巴,等用户拍板】
- th1200:5 张三层升级全漏边缘 + ~1.4 万污染窗误送死信行(当时标"暂缓")
- th1200 6.74 万无主孤儿图救援(有 blob 无账,qids 需从 v1/deadletter 挖)
- wm 湖本地池 88,899 张补传 COS(只动 blob 不动账本)
- oi2(剩 1,087 批)/oi3(637 批)已按用户指示停跑,无动作
- th 账本 651 个 404 miss:核查无对象可标(对应文件已升级或本就无行)
- 湖 `/tmp/qid_scan`(48GB,含 v2c 参照件、wm404 推导过程)在易失目录,要留需拷持久卷——判定结论均已写入已发布死信/报告,不留也不影响正确性

---

## 三、文件坐标速查

**COS(SG 桶 lhcos-data/demiwtg-data/)**
| 内容 | key | 校验 |
|---|---|---|
| 权威主表 | datasets/demiwtg/kb/images.v2.jsonl.gz | 2,223,519,488 B 内;md5 08be0f3a2651e4b77f74538f5824c924;18,657,248 行 |
| 主表日期件 | datasets/demiwtg/kb/images.v2.20260924.jsonl.gz | 同上 |
| 死信 | datasets/demiwtg/kb/quarantine/deadletter.v2.jsonl.gz | 115,160,702 B;8,424,478 行 |
| 死信日期件 | datasets/demiwtg/kb/quarantine.deadletter.v2.20260924.jsonl.gz | 同上 |
| pid 死行 sidecar | datasets/demiwtg/kb/quarantine/wm404_pid_dead_final.jsonl | 35,415 行 |
| pid 恢复 sidecar | datasets/demiwtg/kb/quarantine/pid_additions.jsonl | 14 行 |
| 全程报告 | datasets/demiwtg/kb/quarantine/rebuild_report.json | wave2_verify 节 |
| 旧版备份 | datasets/demiwtg/kb/backup/*20260923*(三件) | 验长一致 |
| met fix 重传 | datasets/demiwtg/kb/batch2/met/met_redo_fix.jsonl | 5,809 行;md5 f1f3f1158af881e1d8eb63f0dd0b57a4 |

**sgx**:`~/merge_wave2/`(wave2.py/wave2_verify.py/wave2_publish.py + 全部日志 + in/ 原料副本)、`~/MERGE_COORDINATION.md`(执行权登记+完成回写)、`~/wh_backfill/`(wh 的脚本链)、`~/merge_input/qid_images.jsonl.gz`(v1 唯一源,只读勿清)

**湖**:`demiwtg/collect/`(本文 + FINAL_MERGE_HANDOFF_20260924.md 已附完成回填 + MEMORY.md)、`demiwtg/collect/archive_docs/wave2/`(四份脚本+wave2_report.json)、`/tmp/qid_scan/`(易失,见 2.4)

**坑的增量记录**:tar 成员名无 `./` 前缀(过滤用 startswith);ssh 命令行含脚本名时 pgrep -f 会自杀(独立短连接操作);战役成果可能已被权威构建水位线消费,打补丁前必须先 join 对账(本次 th1200 因此免于倒退);"判死"前必须逐行 blob 实存探测(本次 11,994 行因此免于误杀)。

---
# ✅ 2.1 验收 + 2.2 清理执行完毕(2026-09-24 18:0x 回填)

## wh Stage B 验收(独立复核,全部通过)
- SG canonical 复验:wh 全程未动(回读 md5 08be0f3a… 一致)
- 抽检:si2 4,000/4,000 覆盖且宽高全填;met 700/700 宽高与修复账本逐值相等;wm404 22/22;增件 sha 在 wh 层残留 0;wh 层 18,643,599 行精确

## 2.2 执行结果
- 主表剔除 HTML 残渣 13,649 行后发布,期间发现并修复 wh 误判:**9 颗 UTF-16 SVG 真图**(嗅探器不认 UTF-16 `<?xml`)被复位进主表 → **canonical 终态 = 18,643,608 行,md5 1ddfc0ef0401be7fbdc539851d78743e**(日期件 images.v2.20260924c)
- 死信终态 = **8,438,118**(并入真 HTML 13,640 条;9 条误判已摘除)
- blob:13,640 颗 HTML 毒页逐颗验证(<4KB+严格 HTML 魔数)后删除;9 颗 SVG 被过滤器拦下保全
- 毒 blob 终扫守护(sgx ~/merge_wave2/wave2_c2.py,可续跑):候选 8,026,512 = 0922 库存≤4096B(8,069,327) − canonical 保护(42,815);实测 99.99% 已被前夜 del_poison/del_round 大扫除清掉,本次仅清漏网(前 40 万颗:4 删/6 真图拦下);最终统计落 out2/c2_stats.json

## 给 wh 的一条待办
deadletter_additions 的 9 颗 SVG 误判清单在 sgx `~/merge_wave2/out2/svg_restore_shas.json` —— 修嗅探器(UTF-16 BOM `\xff\xfe` + `<?xml`→SVG)并在 wh 层补回这 9 行,之后 wh 层行数将与 canonical(18,643,608)差 0。

## 剩余(均为 2.3/2.4 既定项)
- 2.3 wh 层转正(coalesce 规则先行,时点用户拍板)
- 2.4 可选尾巴(th1200 边缘/孤儿救援、88,899 本地池、易失目录归档)

---
# ✅ 2.3 转正 + 2.4 收口(2026-09-24 19:4x 回填)——全线闭环

## 2.3 wh 层转正(已执行)
- canonical 终态:**18,643,608 行,带 wh 字段**(format/ext_match/size_actual/blob_last_modified + 宽高 99.7% 覆盖),md5 `3e882234481f86491a46ad5047e70340`,日期件 images.v2.20260924d;回滚链:20260923(原始)→20260924(Wave2)→20260924c(剔HTML)→20260924d(转正)
- 构造 = wh 层(18,643,599)+ 9 行 SVG 按桶位补齐;验证:抽样 1,000 行原始字段**零差异**,path_fixed 5/ext_fixed 13/宽高补 997 与 wh 修复率吻合;回读 md5 一致
- wave2.py 已落 wh 字段 coalesce 规则(非空保留/时间新者胜/宽高 null 才填,单测过)——下次水位线并账不丢字段

## 2.4 尾巴处置(全部关闭)
- th 孤儿救援:**关闭**——67,853 cf 实测 67,848(99.99%)是 pid 命名空间,Q 可救仅 5,不值一版;67,848 颗真图 blob 留 COS(毒扫过滤器天然保护)
- "88,899 本地池":**旧估算被实测否掉**——现库逐行 HEAD 仅 121 挂空(已移死信),无此欠账
- 易失件归档:湖 collect/_volatile_backup_20260924/(9.3GB:v2c 参照件/0922 库存快照/wm404 推导全程/全部脚本)
- th1200 的"5 张三层漏边缘 + ~1.4 万污染窗误死信":维持暂缓(需逐张人工核,收益小)

## 终态速查
| 件 | 值 |
|---|---|
| canonical 主表 | images.v2.jsonl.gz = 18,643,608 行,md5 3e882234481f86491a46ad5047e70340 |
| 死信 | quarantine/deadletter.v2.jsonl.gz = 8,438,118 行 |
| 毒 blob 终扫 | sgx 后台(6.15M/8.03M):漏网毒页 101 删/真图 61 拦/1 错,最终数 c2_stats.json |
| met fix | kb/batch2/met/met_redo_fix.jsonl(5,809 行) |

## 终扫最终数(20:21 收官)
8,026,512 候选 = 8,026,302 已被前夜大扫除清掉(99.997%) + **122 颗漏网毒页(本次验证后删)** + 87 颗小真图(安全过滤器拦下) + 1 错(记 c2_errors.txt)——精确闭合。
