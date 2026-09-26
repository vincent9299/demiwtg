# 最终并账交接文档(2026-09-24 04:xx,下载/补全会话 → 新并账会话)

> 写给重开窗口执行"最终并账"的会话。目标:把已发布 v2(16M 行)升级为包含全部四条补全线增量的终版。
> 本文自包含:现状、原料坐标、设计红线、任务清单、坑清单。执行前通读一遍。

## 0. 一句话现状

已发布权威版 `kb/images.v2.jsonl.gz`(16,016,943 行,一图一行)已含 wm 修复/tmdb/gbif/nc 分区;
**尚未包含**四块增量:si2(320 万图)、th1200 升级(27.4 万)、met 修复(5,809)、wm404 终局(40 新增 + 48,666 死信标注)。
最终并账 = 在 v2 基线上做 Wave2 增量合并这四块,重验后重发布。

## 1. 权威现状(不要动的东西)

| 资产 | 位置 | 说明 |
|---|---|---|
| **已发布 v2** | COS `lhcos-data/demiwtg-data/datasets/demiwtg/kb/images.v2.jsonl.gz`(1.74GB,16,016,943 行) | 一图一行 + qids/refs 嵌套;qids 总数 28,078,153;过 G1/G2/G3 验证门;**wh 回填已基于它构建,改 schema 会连锁** |
| v2 quarantine 三件 | 同目录 | deadletter.v2(8,412,364)、qid_images_v2_pid.jsonl.gz(6,369,645)、rebuild_report.json(水位线 0923b) |
| v1 对照基线 | `kb/qid_images.jsonl.gz`(1.13GB,毒账本) | 只读对照,不删 |
| wh 元数据回填 | GZ 桶 `kb/wh_backfill/`(images.v2.wh.jsonl.gz 16.02M 宽高/格式/真实字节 + wh_patch 机制) | **衔接协议:你 publish 新 v2 后通知 wh 会话增量补扫新 sha** |
| 协调机制 | sgx `~/MERGE_COORDINATION.md` + `~/stage.sh` + flock | 执行权登记制,先读后写;并账代码在 sgx `~/merge/`(或 `/tmp/merge` 共同编辑版) |

## 2. 待合入的四块增量(本会话产出,全部已落 COS)

### 2.1 si2 —— Smithsonian 1920 回补(最大增量)
- **账本**:COS `kb/fleet_manifests/final_20260924/si2/`(20 机 tgz,234MB,含 ledger.jsonl+dead.jsonl);sgx 新快照 `kb/manifest_snapshots_0923/`(4.98GB,内容 0924)亦含完整 si2 账本
- **规模**:任务 2,738,477(99.7% NMNH);账本行 330 万(含回收重领重复);**按 extid 去重后约 3.2M 唯一媒体**(si1 原图 56.2 万 + si2@1920 265 万,后者占大头);边(qid×图)约 3.9M(qids 在账本行内)
- **合并规则**:extid(形如 `media:NMNH-xxx`)为主键;**si1 原图与 si2@1920 同 extid 时保留 si1 原图(tier 优先),仅 si2 有的用 si2(标 tier=thumb1920)**;账本行字段 {extid, url, sha256, ext, bytes, license, qids[], ts}
- **质量**:金丝雀 49/50;全程死信率 <1%(跨洋流断为主);抽样 20/20 PIL 完整解码;源 = `https://ids.si.edu/ids/deliveryService?id=<ID>&max=1920`

### 2.2 th1200 —— 降档行升级 1920
- **账本**:COS `kb/fleet_manifests/final_20260924/th1200/`(20 机 tgz,40MB,manifest.jsonl 含成功 sha/失败 miss)
- **规模**:334,570 个唯一 commons_file;**成功 274,334(82%)**(新 sha=1920 版,已在 kb/blobs);miss 46,175 = http:400 44,565(PDF/TIF 无 1920 缩略,**策略:保留原 thumb1200 不动**)+ 404 651(真死)+ 其他
- **合并规则**:按 commons_file 匹配 v2 中 tier=thumb1200 的行 → 成功者换 sha/size_bytes/tier=thumb1920;44,565 个 400 保留原状;651 死信标注

### 2.3 met —— 截断修复
- **fix 账本**:`met_redo_fix.jsonl`(5,809 行,字段 extid/old_sha/sha/bytes/w/h/qid/verdict=replaced)
- ⚠️ **COS 副本 404 丢了**(原传 `kb/batch2/met/`,HEAD 已 -1,原因不明);**湖副本 `/tmp/qid_scan/dl/met_redo_fix.jsonl` + r8 `/tmp/met_redo_fix.jsonl` 均完好(5,809 行)**——新会话第一步先重传 COS
- **合并规则**:v2 中 source=met 且 extid 匹配 → 替换 sha256/size_bytes/blob_path(新 sha 的完整图**已在 kb/blobs**,验收过 20/20)

### 2.4 wm404 —— 死链终局
- 48,706 个 404 死链全量探测完毕(fleet 判定 27,407 + 湖/r 机补测 21,299,**恢复率 0.08%**)
- **40 张恢复图已入 kb/blobs**:fleet 27(在 b3_manifests/snap1 账本内)+ 湖 13(清单 `final_20260924/wm404/lake_fix_13.jsonl`);新会话把这 40 行按新 sha 入账
- **48,666 判 `dead_404_final`**(永久死,不再投入);如需完整判定清单,湖侧 /tmp/qid_scan/{wm404_tasks.jsonl.gz, wm404_rest.jsonl, wm404_probe_out_*} 有推导过程

## 3. 原料全景(坐标速查)

| 原料 | 位置 | 备注 |
|---|---|---|
| wm/sdc 线 manifest(1.1 亿行) | COS 旧路径 `demiwtg-data/kb/b3_manifests/r1..r20.jsonl.gz`(5.9GB)+ snap1(`kb/fleet_manifests/snap1/`,4.15GB,wm 补图/队列期) | v2 已消费到 0923b 水位;**si2/th1200 用新快照** |
| b2 四源 ledger(si/inat/oi/met) | `kb/fleet_manifests/snap1b/`(461MB,20 机 tgz) | v2 已消费;si2 增量见 2.1 |
| 新快照(含 si2) | sgx `kb/manifest_snapshots_0923/`(20 件 4.98GB,内容 0924) | **wm 线之外增量的最新水位** |
| sgx 本地唯一件 | `~/merge_input/ext/` 四件 + `qid_images.jsonl.gz`(**COS 副本已删,勿清 sgx**) | |
| 湖工作副本 | `/tmp/qid_scan/dl/`(10.6GB 原料+ledgers)+ `/tmp/qid_scan/v2/`(本会话并行 v2c,见 §5) | **/tmp 重启即失**;要留就拷 /yzp |
| blob 实存清单 | 湖 `/tmp/qid_scan/inv_pages/`(kb/blobs 全量 2652 万键,0922 拍摄)+ sgx `~/cleanup/blobs_keys.txt` | 增量验证可重扫子前缀 |

## 4. 设计红线(已拍板,不可回退)

1. **一图一行 + qids/refs 嵌套**单文件 schema(16M 行版式);**不是**边表
2. **blobs-nc 物理分区**(kb/blobs-nc/)+ license 取最严合并(NC 合规双层)
3. COS 输出为权威;原始账本全程只读;输出全为新文件;双树前缀必须 `lhcos-data/demiwtg-data/...`
4. wm 线 tier 优先级 **orig > thumb1920 > thumb1200**;th1200 的 44,565 个 PDF/TIF 保留 thumb1200
5. si 线 si1 原图 > si2@1920(同 extid)
6. publish 后通知 wh_backfill 会话做增量补扫(衔接协议在其 sgx 说明里)
7. 收尾一律小批(150 行/批);执行权先在 MERGE_COORDINATION.md 登记

## 5. 本会话的并行 v2c(定位:验证参照,不发布)

- `/tmp/qid_scan/v2/qid_images.v2c.jsonl.gz`:33,116,557 **边**(边表 schema),2.26GB;wm 95.3% resha;met/th1200/si2/wm404 均已体现
- **四个缺陷属实,不得发布**:无 tmdb/gbif、边表违反一图一行、无 nc 分区、上传会覆盖权威 v2
- 唯一价值 = 独立对账数字:wm resha 7,845,533 / si 边 3,906,858 / inat 3,582,546 / oi 边 12,266,896(616,752 图)/ sdc b3 线 4,399,648 / sdc 旧 attach 229 万判死信 —— 新会话完成后可与此交叉核对量级
- 若想跨重启保留:cp 到 /yzp/zhaozy/yangzepeng/0905/demiwtg/collect/_staging/(未做,新会话自行决定)

## 6. 任务清单(新会话执行序)

1. 读 sgx `~/MERGE_COORDINATION.md` 登记执行权;重传 `met_redo_fix.jsonl` 到 COS(从湖或 r8 取)
2. 拉齐原料:新快照 0923(si2/th1200/wm 增量)+ final_20260924/{si2,th1200,wm404} + met fix
3. Wave2 增量合并(在 v2 16M 基线上):si2 3.2M 图入账(si1 优先规则)→ th1200 274,334 换 sha → met 5,809 替换 → wm404 40 新增 + 48,666 死信终标;保持一图一行 + nc 规则
4. 三道验证门重跑(G1 守恒/G2 抽样/G3 恒等式;可用湖侧 v2c 数字交叉核对量级)
5. publish 新 v2(新文件名或版本化,不裸覆盖;具体命名与对方会话/用户确认)→ 通知 wh_backfill 增量
6. 之后(可选,等用户令):800 万毒页 blob 清理 + wh 指出的 13,770 HTML 残渣 blob(等新 v2 发布后统一清,避免悬空引用)

## 7. 坑清单(前人踩过,直接绕行)

- **b1 manifest ok 行带 `"miss": null`**:判 ok 必须是 `miss 为 null 且 sha256 非空`( miss 键存在≠失败);计数器曾因此 bug
- **毒时代假 sha**:09-12~14 旧 collector 把 2KB HTML 的 sha 当成功写账;判真须 `tier 字段存在 或 page_bytes≥3000`(仍有 ~1% wm 补图时代记录指向湖本地池而非 COS——88,899 张,量小可标注后补传)
- sdc 两代不重叠:attach 账本(M238 式旧 ID)≠ b3 下载线(M163xxx 新 ID);229 万 attach 边判死信,b3 线 4,399,648 真边入账
- oi 是多概念图:616,752 图但 12.27M 边(一图平均 20 qids)——一图一行 schema 下应聚进 qids[],行数增量为图数不是边数
- 跨境通道:湖→COS 2MB/s(大文件用 12 路 Range);r 机→COS 同区秒级;sgx 16c30G 是并账主场
- COS LIST 必须签名(匿名 403)、并发 ≤16 路否则限频;exec_curl 429 耗尽后 reason=retries_exhausted/status≠429(改它需改 exec_curl 本体)
- /tmp 重启即失:湖 /tmp/qid_scan 全部工作副本无持久保障

## 8. 相关文档索引(repo collect/ 下)

- MERGE_SPEC.md / MERGE_PLAN.md(对方会话并账规格)
- INVENTORY_2026-09-23.md + INVENTORY_AUDIT_20260923.md(全量清点+质量审计)
- MEMORY.md(全程运维记忆,含所有战役始末与教训)
- 本文:FINAL_MERGE_HANDOFF_20260924.md

---
# ✅ 执行完毕(2026-09-24 14:1x UTC,Wave2 会话回填)

本文档任务清单 §6 已全部执行完成,结果:

| 项 | 结果 |
|---|---|
| 新权威 images.v2 | **18,657,248 行** = 16,016,943 −121(挂空移除) +2,640,447(si2) +22(wm404) −43(吸收);canonical key 已原子更新,另存 images.v2.20260924.jsonl.gz |
| 旧版备份 | kb/backup/{images.v2,quarantine.deadletter.v2,rebuild_report}.20260923*(三件验长一致) |
| si2 | +2,640,447 行(账本 2,650,758 extid,10,311 与 si1 撞键按 si1 优先跳过;全 CC0/jpg) |
| th1200 | **0 补丁——已隐式完成**:权威 v2 在 0923b 水位线已吸收该战役(19.06 万行已是 thumb1920,sha 抽样 89% 与账本一致;1.82 万行已是 orig;6.7 万 cf 无主行未入账,救援需 qids 属后续可选项) |
| met | 5,744 换 sha + 65 未命中(90 行 old_sha 不符,行内已是别的修复);账本已重传 COS batch2/met/ |
| wm404 | 恢复 36 净新增(22 Q+14 pid sidecar)+5 已在库;死 48,665 终标;**11,994 死链但 blob 活→保留主表**;121 真挂空整行移入死信(moved_row 留全重载信息) |
| 死信 | deadletter.v2 = 8,424,478;pid 死行 35,415 在 quarantine/wm404_pid_dead_final.jsonl sidecar |
| 验证 | G1 恒等(行数精确;refs/qids 差 15/9 已归因吸收去重;死键闭包 48,665/48,665;drop 残留 0)· G2(blob HEAD 400/400,魔数 16/16)· G3(byte-equal 1499/1500)· v2c 交叉(si 3,906,669 vs 3,906,858=0.005%) |
| wh 衔接 | 已在 sgx MERGE_COORDINATION.md 通知:新 sha ≈ **2,646,213**,40 分钟估算作废 |

坑的新增记录:tar 成员名无前导 `./`(过滤要 startswith);ssh 命令行含目标脚本名时 pgrep -f 自杀(用独立短连接);b1u/th1200 类战役成果可能已被权威构建的水位线消费,补丁前先对账(本次 th1200 因此免于倒退 orig→1920)。
