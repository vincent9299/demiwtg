# 并账执行方案(MERGE_PLAN v1,2026-09-23)

> 设计依据:`MERGE_SPEC.md`(单文件一图一行/refs 嵌套/隔离区/增量机制)。
> 数据依据:`INVENTORY_2026-09-23.md` + `INVENTORY_AUDIT_20260923.md`(两会话互检版)。
> 原则:**原始账本全程只读;输出全部为新文件;每道验证门不过不进下一阶段。**

## 一、目标与范围

把已回收的全部采集线并成**唯一主表 `images.v2.jsonl.gz`**(一图一行,qids/refs 嵌套,~2,000 万行 / 2.5-3GB gz),死信/非图/毒页进 `quarantine/`,并出具对账报告。后续新增走增量(水位线+补丁+单文件重发布)。

**纳入 Wave 0(已收官 10 线)**:wm 主账本重建(b1 真图 762 万) / sdc_attach 重挂(229 万) / b3+b3x(553 万 ok) / b1u 升级(替换式) / b2 四源(482.5 万唯一) / 三域+pubchem(50 万,自包含) / tmdb(56.5 万) / gbif 滴灌(1.3 万,nc 修正) / sdc test(8,539) / wm 补图(blob 在 COS,边账本开案项,缺则 Wave 0 先跳过)。
**Wave 1(首个增量)**:b3w2 收官后的 0923b 增量——顺带验证"增量 ≡ 全量"。

## 二、运行位置与输入

**sgx** 独占执行(16 核/30G/272G 盘;COS 同区 180MB/s)。输入已就绪 `~/merge_input/`:
- `snapshots_0923/`(20 tar:b1/b3/b3x/b1u/b4/sdc 全账本)
- `snap1b/`(b2 四源 dead/finished 拆件)
- `qid_images.jsonl.gz`(v1 主账本,毒行重建的对照基线)
- 待补:`0923b` 增量(b3w2);`datasets/candidate/wikimedia/` 三钥匙表(sdc_depicts 重挂用,并账会话自拉,~6GB)

## 三、流水线(六阶段,全部在 sgx)

| 阶段 | 做什么 | 关键规则 |
|---|---|---|
| **S0 规范化** | 解包各源账本 → 统一中间行 `{qid, sha256, source, external_id, orig_url, relation_type, tier, license, size_bytes, width, height, fetched_at, dead?}` | 旧 schema 种子行(裸 qid+commons_file)直接丢弃;gbif 只取 ledger.jsonl;字段映射按 SPEC(page_bytes→size_bytes 等) |
| **S1 边去重** | 按 `(qid, source, external_id)` 去重 | 保 fetched_at 最新;tier 优先 orig>thumb1920>thumb1200;b1u 行**替换**原 thumb 边 |
| **S2 sha 聚合** | 一图一行主表 | 客观字段冲突→报告异常;license 取最严;宽高 coalesce;qids/refs 并集去重排序 |
| **S3 毒行重建+重挂** | wm 真图以 b1 manifests 的 sha 为准(v1 行的 sha 作废重对);sdc_attach 229 万边按 (qid, commons_file) 重挂真 sha | 挂接失败(毒行无真图)→ quarantine(resha_fail 类);`fix_state` 标 resha/new |
| **S4 隔离区** | deadletter.v2(带 dead_class:perm_404/not_image/over_cap/no_media/si_over8mb/oi_404/met_trunc/400_orig_le1920/poison_page/bad_row)+ pid 副本 | 死信行保留可重放字段(orig_url/external_id)——后续按类重载的原料 |
| **S5 对账+发布** | rebuild_report.json(逐线恒等式+组成统计+异常清单+冲突样本)→ 发布 COS 权威 + 湖侧工作副本 → 登记**水位线 0923b** | 恒等式:done+perm+retry+未跑 ≡ 任务总数(逐线,引用盘点基线) |

## 四、验证门(每门不过不进)

- **G0 输入门**:S0 后逐线行数 vs 盘点表(§0 总表)偏差 <0.5%,超差停下查
- **G1 守恒门**:S2 后 refs 总数 ≈ 2,135 万 ±1%;主表行数 ≈ 2,000 万 ±5%(与唯一 sha 推算对)
- **G2 抽查门**:主表随机 200 行 → blob HEAD 100% 存在;30 张实下解码全过;随机 50 个 refs 的 orig_url 可构造(HEAD 源站不强求 200,能构造即可)
- **G3 恒等门**:S5 逐线恒等式全平
- **G4 金丝雀**:正式跑前,si 单源走 S0→S5 全流程(si 量小 62 万行),人工验后再全量
- **G5 增量等价门**(Wave 1):b3w2 增量后,抽 1,000 行与全量重建对应行 diff=0(结合律实证)

## 五、时间估算(sgx)

S0 解包+规范化 ~1.5h;S1+S2 ~1h(16 核并行分片,sha 分桶 256);S3 重挂 ~0.5h;S4+S5 ~0.5h。**合计 ~3.5-4.5h**,金丝雀另加 ~0.5h。

## 六、风险与回滚

- 原始账本只读、镜像三套(0923/snap1/snap1b),任何阶段可从输入重跑
- 输出写 COS 新 key(`datasets/demiwtg/kb/images.v2.jsonl.gz`),不覆盖 v1(v1 保留作对照,标 read-only 心智)
- met 截断重下(8,612 张)、oi 404 清单调查、si 缩略回补——**独立小任务,不阻塞主并账**(死信已在隔离区分类)
- 湖盘 98%:湖侧只放 v2 工作副本(3GB)+报告,过程数据全留 sgx

## 七、分工

- **并账会话**:按本方案施工,过 G0-G4,产出三件套+水位线
- **值守会话(本会话)**:b3w2 收官 → 出 0923b 增量镜像 → 通知开跑;G2 抽查门独立复验(交叉验证);Wave 1 触发与 G5 见证
- **用户**:仅在 pid: 副本确认、旧系池去向、oi/si/met 三个后续重载项上拍板
