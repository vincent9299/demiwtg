# wh_backfill 元数据回填总账单(2026-09-24,wh 会话)
> 自包含:全库 1,866 万 blob 的元数据回填/修复/审计已收口,含 Wave2 2.1 增量。给接手会话即可继续(第二节)。
> 湖侧 `demiwtg/collect/` 与 sgx `~/WH_BACKFILL_STATUS_20260924.md` 双镜像,md5 一致为准。

## 0. 一句话现状

全库(18,657,248 行)宽高/格式/真实字节/Last-Modified 回填完成,四类账实修复(path 78,195 / ext 196,835 / 翻案 1,792 / HTML 分流 13,649),wh 层已按 Wave2 新 canonical 重发布到 **GZ kb/wh_backfill/**;**SG 主表 canonical 从未被动过**。下一步是 2.2 清理与 2.3 转正(见二)。

---

## 一、完成了哪些

### 1.1 全量回填(0923 夜)
- 扫描:s gx 直连 sg 桶,64KB Range 头 + 每线程 keep-alive,96 线程 1,950-2,022/s,16.02M blob ~2.3h 零错误。
- 产出字段(全为 v2 缺失或错值):`width/height`(嗅探真实分辨率)、`format`(纯 py 嗅探 JPEG/PNG/GIF/WEBP/BMP/TIFF/SVG)、`size_actual`(Content-Range 实测,即截断检测)、`blob_last_modified`、`ext_match`、`wh_status`(missing/bad)。
- 质量总账:ok 15,996,722(99.87%)· 真缺失 2(均 sdc,sha 在 wh_patch)· 截断 0 · bad 20,209 → 见 1.3 降为 4,518。

### 1.2 四类账实修复(全部 sha 键控,append-only 进 wh_patch)
| 类 | 数量 | 根因 |
|---|---|---|
| path_fixed(改写 blob_path+ext) | 78,195 | wm 25.5k 记 `.jpg/.tif` 实为 `.jpeg/.tiff`;pubchem 整线 25,806 记 `.jpg` 实为 `.png`;gbif→blobs-nc 迁移 1,934(v2 原记 blobs/,用户手工迁移对象后账本指向修正);其余 LIST 解析 |
| ext_fixed(只改 ext 字段,物理键不动) | 196,835 | sdc 线 95%+:Commons 缩略图对原 PNG/GIF/WEBP 返回原格式字节,账本按原文件名记 .jpg |
| 翻案(bad→ok) | 1,792 | TIFF IFD 偏移超 64KB 头(二段小 Range 取窗口解析)+ JPEG SOF 被巨型 EXIF 顶出 64KB(4MB 窗口重试) |
| dead_html(分流死信增件) | 13,649 | HTML 毒页残渣(2.1KB 错误页,毒清理保留集放行的漏网);从主表剔除→`deadletter_additions.jsonl`(带 sha/blob_path,2.2 删 blob 用) |

### 1.3 Wave2 2.1 增量(0924 下午,按 WAVE2_FINAL_STATUS_20260924.md 任务书)
- 新 canonical 校验:md5 `08be0f3a2651e4b77f74538f5824c924`/18,657,248 行,通过后替换 wh 链输入。
- 增量补扫 2,646,170 新 sha(= 任务书 2,646,213 − 43 桶内吸收)全 ok,2022/s,22 分钟;th1200 确认为 0。
- 并表#5 与新 canonical 逐行对齐:rows 18,657,248 ✓;rows_out 18,643,599 = 减 dead_html 13,649(与 Wave2 移走的 121 行无双计);新 sha 覆盖 100%;met 5,744 预填宽高保留(kept 恰 +5,744);宽高总覆盖 18,605,149(99.72%)。
- bad 残差 4,518 = PDF 802 + DjVu 91 + 真损坏/未解出(非位图与真坏,属终态)。
- 执行权已在 sgx `~/MERGE_COORDINATION.md` 登记并回写完成。

### 1.4 关键实现细节(接手会话必读)
- **wh_patch.jsonl 是唯一事实源**:append-only 审计轨迹,同 sha 多条记录按**字段级合并、后写覆盖先写**(apply v4 定长槽位实现)。任何新修正=追加记录,不改历史。
- **幂等性**:fix_missing 以 blob_path_fixed/verified_by_list 为已处理标记;fix_ext 以 ext_fixed 为标记;readjud 无标记(重跑会重裁定,结论幂等)。fix_gbif 已完成无需重跑(gbif 3,688 行 100% 处置)。
- **sgx 内存 30G**:patch 以 dict 形态装 1,866 万条会 OOM(踩过,16M 行处被杀);apply v4 槽位 list ~8G 安全。同机有分析会话(P31/qid_edges)抢资源,跑大任务前看 free。

---

## 二、还要做哪些(按顺序)

### 2.2 【已可开】统一清理(毒 blob + HTML 残渣)
- 先并死信再删 blob(避免悬空引用):`GZ kb/wh_backfill/deadletter_additions.jsonl`(13,649 行,新死信基线 8,424,478)并入 `kb/quarantine/deadletter.v2.jsonl.gz`;800 万毒 blob 清理材料见 Wave2 文档 2.2。
- 删除清单 = deadletter_additions 的 sha256 列;删前逐颗 HEAD 验 <4KB 且魔数 HTML(字段已带 blob_path)。红线:只删清单内对象。

### 2.3 【wh 层转正前】并账代码补新字段 coalesce 规则
- 字段清单与语义:`format`/`ext_match`/`size_actual`/`truncated`/`blob_last_modified`(新增列,非空保留、时间新者胜);`width/height`(null 才填);`blob_path_fixed`/`ext_fixed`(路径与 ext 改写,语义=以 blob 实存为准);`wh_status`(missing/bad 标记)。
- 转正流程:快照旧件(sg 桶已有 backup/20260923)→ wh 层读回 md5 比对 → 原子 PUT 覆盖 `kb/images.v2.jsonl.gz` → 刷新湖侧 /tmp 缓存与 GZ demiwtg-adhoc 镜像 → 回写 MERGE_COORDINATION.md。转正时点由用户拍板(需与 Wave2 侧协调,避免与下次水位线并账互踩)。

### 2.4 【可选,等用户拍板】
- 嗅探器再升级可再翻案少量远 SOF JPEG;wm 湖本地池 88,899 张补传;Wave2 2.4 的四个尾巴(th1200 边缘/孤儿救援等)。
- 湖 `/tmp/qid_preview_cache/_tables/`(旧 v2 副本+旧死信,md5 dac9ede2…/2191f3aa…)与 `/tmp/qid_scan/` 均在易失目录,留否用户定。

---

## 三、文件坐标速查

**GZ 桶(lhcos-cee54…ap-guangzhou,湖侧快道)`kb/wh_backfill/`**
| 内容 | key | 校验 |
|---|---|---|
| wh 层主表(新基线) | images.v2.wh.jsonl.gz | 2,212,464,389 B;18,643,599 行(=canonical−13,649);multipart 上传尺寸核验 |
| 补丁全集 | wh_patch.jsonl.gz | 1,063,358,951 B;解压后同 sha 字段级合并为终值 |
| 死信增件 | deadletter_additions.jsonl | 5,533,292 B;13,649 行 |
| 总账 | wh_report.json | rows 18,657,248 / filled 18,605,149 / missing 2 / bad 4,518 / truncated 0 |

**SG 桶 canonical(未触碰,仅读)**:`kb/images.v2.jsonl.gz` = 08be0f3a2651e4b77f74538f5824c924 / 18,657,248 行。

**sgx `~/wh_backfill/`**:脚本 wh_scan.py / wh_apply.py(v4 槽位版) / wh_fix_missing.py(v3 线程版) / wh_fix_ext.py / wh_fix_gbif.py / wh_readjud.py / chain_v7.sh / quality_bill.py;数据 images.v2.jsonl.gz(新 canonical 本地副本)、images.v2.0923.jsonl.gz.bak(旧件)、wh_patch.jsonl(原文 ~4GB);日志 wh_scan_full.log / wh_apply5.log / wh_chain*.log / quality_bill.log。

**湖侧**:`demiwtg/tools/qid_preview.ipynb`(按 qid 预览,实测分辨率/签名原图直链/GZ 镜像优先)、`/tmp/qid_preview_cache/`(notebook 缓存,易失)。

**坑的增量记录**:① ssh 命令行含目标名时 pgrep/pkill -f 自杀(踩两次;用 `[m]` 字符类或按 PID);② patch dict 形态 1,866 万条 OOM;③ 远端 nohup 必须 `</dev/null`;④ 幂等标记集与去重集不可共用(fix_missing v1 教训);⑤ 签名 host 随桶走(GZ 镜像 403 教训);⑥ 跨境单流可劣化至 <20KB/s,大文件走 Range 并行或 sgx→GZ 中继;⑦ 修复腿部分字段记录必须配"字段级合并"读取端(整条覆盖会 KeyError+丢数据,踩过)。

## 2026-09-24 wh 会话: SVG-9 收尾完成 ✓(转正后唯一遗留项闭环)
- 嗅探器已修:BOM(UTF-16/UTF-8)归一→文本路径解析 SVG(width/height/viewBox);wh_scan.py 已部署 sgx。
- 9 颗 UTF-16 SVG 全部实测(1408×1313 … 49×60),canonical 外科更新:仅 9 行填 width/height,
  行数 18,643,608 不变;新 canonical md5 2d9f7d13937b9c084cf366c16533f11b(gzip level 9 重压,
  体积 2.21GB < 原 2.64GB 属压缩率差异,非丢数据);日期件 images.v2.20260924e;修复前版备份
  kb/backup/images.v2.20260924d.jsonl.gz;回读 9/9+md5 三方一致。
- wh_patch 已留痕(9 条 ok 记录 note=utf16-svg-fixed);GZ wh 层 deadletter_additions 中那 9 条
  误判以 patch 新记录为准(字段级合并后写覆盖)。wh 会话无在跑任务,执行权不占用。

## 2026-09-24 终局补记(wh 会话): th1200 关闭 + 文件归位清理
- **th1200 关闭(用户拍板不救)**:"5 张边缘 + ~1.4 万污染窗误死信"不做。死信行**保留为记录不清理**(死信即账,删行反而失账);5 张边缘无动作。至此无任何暂缓项。
- **svg9 修复已发布**:canonical md5 2d9f7d13937b9c084cf366c16533f11b(=images.v2.20260924e),修复前版在 kb/backup/images.v2.20260924d.jsonl.gz;回滚链 0923→0924→0924c→0924d(backup)→现canonical。
- **文件归位/清理**:GZ kb/wh_backfill/ 删 images.v2.wh.jsonl.gz(被 canonical 全量取代),留 wh_patch.jsonl.gz(审计唯一源)/wh_report.json/deadletter_additions.jsonl(其中 9 条 SVG 误判以 wh_patch 后写记录为准);GZ demiwtg-adhoc/ 镜像刷新为新 canonical(2,212,465,200B),删旧死信临时镜像;sgx ~/wh_backfill/ 留脚本+wh_patch(raw+gz)+日志+canonical 本地副本,删 .bak 与 wh 层副本;湖 /tmp/qid_preview_cache 刷新为新 canonical 并清过期行缓存。
- **最终态**:canonical 18,643,608 行(wh 全字段+SVG9 宽高) · 死信 8,438,118 行 · 真缺失 2 · 截断 0 · 毒 blob 清零 · wh 会话无在跑任务。
- **可选裁剪(待用户拍板,不急)**:COS 旧代 images.v2.20260924.jsonl.gz / 20260924c 及 backup/*20260923*(合计 ~7GB)建议观察期后删,保留 20260924d(backup)+现 canonical 即一代回滚。
