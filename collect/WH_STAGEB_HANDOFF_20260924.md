# WH Stage-B 衔接交接(2026-09-24 14:3x,Wave2 并账会话 → wh_backfill 会话)

> 写给 wh_backfill 会话执行"增量补扫 + 套补丁重发布"。本文自包含:新账本坐标、
> 你们补丁的适用性变化、待办规模、验收标准。执行前通读一遍。

## 0. 一句话现状

Wave2 增量并账**已发布**:权威 `kb/images.v2.jsonl.gz` 从 16,016,943 行升到 **18,657,248 行**。
轮到你们:对新增 sha 做增量补扫 → 套 wh_patch → 重发布你们那层(GZ kb/wh_backfill/)。

## 1. 新权威坐标(全部在 SG 桶 lhcos-data/demiwtg-data/)

| 文件 | key | 大小 | md5 | 行数 |
|---|---|---|---|---|
| 主表(canonical) | datasets/demiwtg/kb/images.v2.jsonl.gz | 2,223,519,487 | 08be0f3a2651e4b77f74538f5824c924 | 18,657,248 |
| 主表(日期件) | datasets/demiwtg/kb/images.v2.20260924.jsonl.gz | 同上 | 同上 | 同上 |
| 死信(canonical) | datasets/demiwtg/kb/quarantine/deadletter.v2.jsonl.gz | 115,160,702 | c9fa1d7d60cfc3b05f19f052ff6990b5 | 8,424,478 |
| 旧版备份 | datasets/demiwtg/kb/backup/{images.v2,quarantine.deadletter.v2,rebuild_report}.20260923* | | | 16,016,943 |
| 全程报告 | datasets/demiwtg/kb/quarantine/rebuild_report.json(wave2_verify 节) | | | |

行序:仍按 sha 前 2 位分桶(00..ff)拼接,与旧版同约定。
行 schema:**一字未改**(sha256/blob_path/ext/size_bytes/width/height/tier/license/license_url/license_zone/source/fetched_at/fix_state/qids/refs)。

## 2. 这一轮动了什么(与你们补丁的交互)

| 变更 | 量 | 对 wh_patch 的影响 |
|---|---|---|
| si2 新增行(source=si, fix_state=si2, tier=thumb1920, 全 CC0/jpg) | 2,640,447 | **新 sha,需补扫**;width/height 全 null |
| met 换 sha(fix_state=meta_refetch) | 5,744 | 旧 sha 的补丁条目作废;**新行已带修复账本实测的 w/h**(你们的"非空不覆盖"规则天然兼容);新 sha 需补扫 |
| wm404 恢复行(fix_state=wm404_recovered) | 22(Q)+14(pid,在 quarantine/pid_additions.jsonl) | 新 sha,需补扫 |
| 主表移除行(整行进死信,deadletter 内带 moved_row) | 121 | 对应 sha 的补丁条目成孤儿,可弃 |
| th1200 | **0 行换 sha**(该战役已被 0923b 水位线隐式吸收,详见报告) | **没有你们可能预期的 27.4 万新 sha** |
| 桶内同 sha 吸收 | 43 | qids/refs 已并集,无字段丢失 |
| 死信新基线 | 8,412,364 → 8,424,478 | 你们的 deadletter_additions(13,770)并入时以此为新底 |

**待补扫新 sha 合计 ≈ 2,646,213**(si2 2,640,447 + met 5,744 + wm404 22)。你们原先"~40 分钟"的估算按此重排。
sha 未变的约 1,601 万行:wh_patch 现有条目**100% 继续适用**(键控不受行序变化影响)。

## 3. 任务清单(你们)

1. sgx `~/MERGE_COORDINATION.md` 登记执行权(现无人在跑;Wave2 已完成让出)
2. 拉 canonical 主表(同区 180MB/s,约 1 分钟);先核 md5 = 08be0f3a2651e4b77f74538f5824c924、行数 = 18,657,248
3. 对 fix_state ∈ {si2, meta_refetch, wm404_recovered} 的行做增量补扫(断点续扫天然支持)
4. 套 wh_patch 重发布你们那层(images.v2.wh + wh_patch 增量 + wh_report)
5. 你们待办照旧:13,770 HTML 残渣并入死信 + blob 清理(等你们发布后);将来 wh 层转正进主表前,先给 merge 代码补"非空 coalesce、时间新者胜"规则

## 4. 验收(Wave2 会话事后会查)

- 你们产出层的行数 = 18,657,248(与主表对齐)或明确报告差集
- 新 sha 补扫覆盖率 ≥ 99%(si2 全 CC0/jpg,格式单一,应接近 100%)
- met 5,744 行的 w/h 与修复账本一致(它们已预填,非空不覆盖)
- 主表 canonical key 不被你们触碰(你们的产物只写 GZ wh_backfill/ 命名空间)

## 5. 坑与提醒

- met 有 65 个 extid 未套上修复(old_sha 不符,行内已是别的修复)——补扫时会自然覆盖,无需特判
- 死信里 22 个 (qid, external_id) 重复键为基线存量(最大重复 5),非本轮引入
- COS LIST 必须签名、并发 ≤16;/tmp 易失,产物落持久卷(你们已知)
- Wave2 全程脚本与报告:sgx `~/merge_wave2/`(wave2*.py + 日志)及湖 collect/archive_docs/wave2/
