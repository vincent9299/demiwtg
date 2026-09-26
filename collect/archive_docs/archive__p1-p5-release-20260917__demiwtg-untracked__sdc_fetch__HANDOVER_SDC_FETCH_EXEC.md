# 交接文档：SDC 取新图 · 执行阶段护航与后续工作（训练机交接版）

> 生成：2026-09-17 09:20 ｜ 作者：SDC 执行夜航（ZCode 会话，master VM-0-14）
> 上游：`/lhcos-data/demiwtg-data/HANDOVER_SDC_FETCH.md`（任务定义版，坑①-⑧仍全部有效）
> 现态：**舰队已按用户指令全线暂停（09-17 08:55），零进程残留，随时可无损续跑**
> 交接对象：训练机（lake）侧护航会话 + 指挥位（master）操作员

---

## 一、现状快照（读这段就够）

| 事项 | 状态 |
|---|---|
| 清单生成 | ✅ **完成**。4,736,141 文件 / 5,415,631 边 / 877,720 概念 / img_size 合计 ~21TB（原图口径） |
| 下载器 | ✅ 建成+实测调优（300/300 成功），v3 参数定稿 |
| 舰队 | ⏸️ **用户令暂停**。22 台（r1-r20+pipeline-b/d）已杀干净并抽样验证；已入库 ~10,253 账本行（≈9.0 千文件） |
| 待决策 | ⚠️ 21TB 全原图 vs 缩略分层（见第五节，验证脚本已备好未跑） |
| 下载物理上限 | Wikimedia 已收紧限流：**单 IP ~0.7 张/秒**（Retry-After:11），23 机 ≈15 张/秒，全量约 **3.5 天**连续 |

**一切幂等**：每机 `~/sdc_fetch/ledger.jsonl` 是 done 集（重启自动跳过已收），blob 内容寻址天然去重，恢复=一条命令。

## 二、硬产出与资产清单

### COS（桶 lhcos-368f6-1256345599，路径均带 `lhcos-data/` 前缀——头号坑不变）

| 路径 | 内容 |
|---|---|
| `.../kb/sdc_fetch/fetch_list.tsv.gz` | **待取清单**。125MB gz，格式 `fname\tmid\tqid:rank,...\timg_size`，LC_ALL=C 序 |
| `.../kb/sdc_fetch/funnel.json` | 漏斗终值：files=4,736,141 / edges=5,415,631 / qids=877,720 / gb=20,973 |
| `.../kb/sdc_fetch/image_bitmap.tsv.gz` | Commons 全站 BITMAP∈[50KB,64MB] 预筛表（1.23 亿行，`fname\tsize`，有序） |
| `.../kb/qid_images_ext/sdc_fetch/parts/` | 节点账本分片（**目前只有测试期 3 个文件**，舰队暂停时未到 5000 行检查点，节点账本待收集，见第六节第 3 步） |

### 漏斗全数字（验收第 1 项直接引用）

```
sdc_depicts 53,320,331 边 ×概念集(7,826,266) → 命中 M-id 21,428,019（与上游文档分毫不差）
→ 命中边 35,609,707 → 配额 ≤30/概念 → 7,162,831 边（1,329,759 概念）
→ 排除已有(qid_images commons_file=8,295,087) → 5,756,232 边
→ image 预筛丢弃 297,778 文件（非BITMAP/超界）
→ 终态 4,736,141 文件 / 5,415,631 边 / 877,720 概念 / 20,973 GB
```

### 代码仓（master `/home/ubuntu/demi/demiwtg-data/sdc_fetch/`，各节点 `/tmp/` 有运行所需两件）

| 文件 | 用途 |
|---|---|
| `sdc_fetch_fleet.py` | 下载器 v3：MD5路径URL→sha256→cosfs blob→双落账本。参数 `--shard I/23 --dl-conc 4 --dl-rate 3` |
| `run_fetch.sh` | 节点看门狗：崩了 30s 重拉，stdout 尾行含 DONE 则自然退出 |
| `fleet_relaunch.sh` / `fleet_stop.sh` | 指挥位（master）一键发/停全舰队，逐台回报，**已实战验证** |
| `manifest_stages.py` + `run_manifest.sh`/`resume*.sh` | 清单生成全套（已完成，留档重跑用） |
| `extract_image.py` | image 表抽取器 v3（反斜杠转义修复版） |
| `hist_ledger_stats.py` | 21TB 决策的验证脚本（r1:/tmp 也有），**未跑** |

## 三、恢复护航手册（命令级，指挥位=master）

```bash
# 恢复下载（22 台自动分发 shard 0..21/23）
bash /home/ubuntu/demi/demiwtg-data/sdc_fetch/fleet_relaunch.sh

# 再暂停
bash /home/ubuntu/demi/demiwtg-data/sdc_fetch/fleet_stop.sh

# 航行巡检（抽样；全量把机器列表换全）
for h in r1 r5 r10 r15 r20 pipeline-b pipeline-d; do
  ssh $h 'tail -1 ~/sdc_fetch/run.log; wc -l < ~/sdc_fetch/ledger.jsonl'
done
# run.log 行样例: sunk=12,000 fail=31 dedup=44 429=2,100 (0.68/s)
```

看门狗语义：python 死了 30 秒内自动重拉（幂等）；`DONE sunk=...` 打印后看门狗退出=该分片跑完。**分片参数必须是 `I/N` 完整格式**——传裸 `0` 会 ValueError 崩溃循环（本航次空转一夜的教训）。

### 机器清点（09-17 09:15 实测）

| 机器 | 状态 |
|---|---|
| r1-r20（20 台） | ✅ 空闲。r1 的 /tmp 有清单中间产物（~11GB，跑完可清）；r2/r3/r4 /tmp 有测试残留（sdc_test、ab_a.tsv 等，可清） |
| pipeline-b / pipeline-d | ✅ 空闲（b 的 /tmp/sdc_extract 中间产物 ~5GB 可清） |
| pipeline-a | ✅ **已复活**（00:40-02:00 曾失联，09:15 恢复）——**建议直接发 shard 22/23**，立即变 23 机 |
| pipeline-c | ⚠️ DF20 融合仍在跑（`pgrep -f fuse_df20`，已远超原估 00:40）。**勿动**；结束后可再领失败重试任务 |
| master VM-0-14 | 只做指挥/中转（r 机互不相通，文件中转都走 master） |

## 四、限流情报（本航次实测，写死了的物理规律）

1. **单 IP ~0.7 张/秒**是当前上限（持续 0.5/s 也会周期性 429）；429 响应带 `Retry-After: 11`
2. 已内置应对：尊重 Retry-After（不盲退避 30/120/300）、`Connection: close` 每请求新连接（**keep-alive 持久连接是限流靶点**，历史 fleet 就是关 keep-alive 的）、429 时对所属桶集体降速 5s+、退避加 ±40% 抖动
3. UA 无关（A/B 实测新老 UA 同速）；顺序 curl 1/s 完全干净，并发才是触发器
4. 三天前历史 fleet 100 张/秒**不可复现**，是 Wikimedia 侧收紧，不是我们的问题
5. 结论：23-24 机 ≈ 15-16 张/秒，4.74M 文件 ≈ **3.5 天**连续航行。失败率极低（测试 300/300）

## 五、待用户决策：21TB 原图 vs 缩略分层

- 现方案按工单规范=全原图（MD5 路径），img_size 合计 **20,973 GB**
- 历史对照：882 万张仅 0.772TB（均值 87KB）——但注意那是**五波来源混杂、多数图本身就小**，">10MB 转 1200px 缩略"守门规则只是次要因素（`operators/commons.py` 的 `ORIG_GUARD_BYTES`），87KB 均值不能全归功于缩略
- 决策支撑：`hist_ledger_stats.py`（r1:/tmp）跑一次即得——① 历史账本 tier 真实占比 ② 本清单"全原图 / >10MB转缩略 / >1MB转缩略"三档精确 TB 数
- 改缩略方案改动很小：`fetch_bytes` 加分支取 `.../thumb/1200px-<quoted>`，账本加 `tier` 字段

## 六、后续工作清单（按序执行）

1. **等用户口令**恢复下载（或先跑第五节脚本、用户改选缩略方案再恢复）
2. 恢复后建议立即给 **pipeline-a 发 shard 22/23**（`ssh pipeline-a 'bash /tmp/run_fetch.sh 22/23 &'` 模式同 fleet_relaunch）
3. **收集节点账本到 COS**（暂停时未到检查点的缺口，恢复航行后定期做）：
   ```bash
   for h in r1 ... pipeline-d; do ssh $h 'gzip -c ~/sdc_fetch/ledger.jsonl > /lhcos-data/demiwtg-data/datasets/demiwtg/kb/qid_images_ext/sdc_fetch/parts/$(hostname).jsonl.gz'; done
   ```
4. **pipeline-c 解禁**（DF20 完成后）：领失败重试轮（把各机 `~/sdc_fetch/failures.tsv` 收集合并，直接当 `--list` 喂给 c 跑——第 5 列是失败原因会被自动忽略）
5. 全部跑完后：**合并 parts** → `zcat parts/*.jsonl.gz | gzip > .../kb/qid_images_ext/sdc_fetch.jsonl.gz`（工单要求的 COS 真相单文件）
6. **验收五项**（原工单）：① 漏斗数（见第二节，已备好）② 下载成功/退避/失败数（各机 run.log+failures.tsv 汇总）③ **blob 数=账本行数** + sha256 抽 50 张回读（从 parts 随机抽 sha，cosfs 读 blob 校验）④ 随机 20 URL 人工核 depict 真伪（commons 搜文件名看 SDC 标签）⑤ **新增覆盖概念数**（fetch_list 的 877,720 概念 − 已有图概念集；已有图概念可从 qid_images 账本 qid 字段取）
7. **lake_sync 恢复后**：确认各机 `~/lake/meta/image-shard-extsdcfetch.jsonl` 被消费（行内 `blob_path` 字段已逐行验证存在——湖侧只认这个字段名）

## 七、本航次新增坑清单（上游文档坑①-⑧之外，按踩中排序）

1. **分片参数必须 `I/N` 格式**：传裸 `0` → ValueError → 看门狗 30s 崩溃循环 → 22 台空转一夜零下载
2. **cosfs 负缓存**：轮询 `[ -s 文件 ]` 首次 miss 后可能永远 miss（即使新 shell ls 能看到）→ 判断前先 `ls` 目录强制刷新，或干脆轮询 `ls | grep`
3. **pkill -f 自匹配三连**：① pattern 出现在自己命令行 ② 发射命令行本身含目标名 ③ `pgrep|grep -v $$` 的子 shell 也自匹配 → 统一用 `for p in $(pgrep -f X); do [ $p != $$ ] && kill $p; done`，且 pattern 用 `[x]` 字符类
4. **GNU join 双侧同序**：mid_to_file 是 M-id **数值**序（M99<M238），sort/join 是字典序（M238<M99）——join 前必须两文件都过 `LC_ALL=C sort -k1,1`
5. **MariaDB dump 用反斜杠转义**（`\'` `\"`），不是 `''` 双写：名字或 EXIF 带撇号的行（~6%）会被朴素解析器丢掉；闭引号=「未转义的第一个 `'`」，判定要数前置反斜杠奇偶+`''` 双写
6. `with sys.stdout as out` 退出时会**关闭 stdout**，后续 print 崩溃
7. 多线程**共享生成器不安全**（CPython 并发 next 抛 ValueError）→ 加锁包 next_task
8. r 机之间**无互信密钥**，所有文件中转走 master
9. ssh 起 setsid 后台任务后本地 ssh 可能不退——`timeout 30 ssh` 包一层，远端 setsid 子进程能活

## 八、给训练机（lake）侧的特别说明

- 指挥脚本（fleet_relaunch/stop）依赖 master 的 ssh 密钥（`~/.ssh/lighthouse_key`）——**在 lake 上无法直接跑**，lake 侧职责是：lake_sync 消费 feeds、终态对账（blob 数 vs 账本行数）、sha256 抽检、以及第七节坑 3 的自匹配变体在 lake shell 同样存在
- 各节点投喂文件：`~/lake/meta/image-shard-extsdcfetch.jsonl`（追加式，行含 `blob_path`，lake_sync 只认它）
- 账本行 schema（与 DF20 同构，已实测入库）：
```json
{"qid":"Q243","sha256":"...","blob_path":"blobs/xx/sha.ext","path":"blobs/xx/sha.ext",
 "source":"sdc","license":null,"size_bytes":N,"relation_type":"depicts_part",
 "external_id":"M99","confidence":"sdc-p180","rank":"normal","orig_file":"...","fused_at":...}
```
- blob 落点：`.../datasets/demiwtg/blobs/<sha2>/<sha>.<ext>`（与 DF20 同树，**非** kb/blobs 旧池——旧池 882 万张勿动勿混）

## 九、暂停时点各机账本行数（fleet_stop.log 摘录，恢复后以此为基线看增量）

r1:1038 r2:976 r3:1077 r4:1721 r5:848 r6:397 r7:136 r8:73 r9:1714 r10:1452 r11:173 r12:148 r13:84 r14:23 r15:127 r16:75 r17:55 r18:40 r19:66 r20:30 pipeline-b:0 pipeline-d:0（合计 10,253 行 ≈ 8,955 文件；r2/r3/r4 的 /tmp 测试另有 ~1,700 行不入正式账本）
