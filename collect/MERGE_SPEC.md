# 并账施工图(MERGE_SPEC v2,2026-09-23,经两会话互检+用户拍板)

## 用户已拍板
1. **主账本一图一行**(sha 为键);概念挂接以 `qids` 数组同表携带;构建中间产物边表顺带发布为轻表
2. **死信/非图/毒页一律不并**,进隔离区 `quarantine/`,后续按 dead_class 决定是否重载
3. 原始账本文件全部保留只读,输出全部为新文件
4. `pid:` 命名空间拆副本文件,不混主表
5. 运行位置 **sgx**(输入已预拉 `~/merge_input/` 5.5GB,85s 到位);输出上 COS 为权威

## 产物清单
| 文件 | 内容 | 预估 |
|---|---|---|
| `images.v2.jsonl.gz`(**唯一主表,用户拍板单文件**) | 一图一行,sha 唯一;qids 数组=挂接概念,refs 数组=每条采集记录(source/external_id/orig_url/relation_type/fetched_at 嵌进行内,重下能力自包含) | ~2,000 万行,~2.5-3GB gz |
| `quarantine/deadletter.v2.jsonl.gz` | 全部死信+非图+毒页,带 `dead_class` | ~1,500 万行(si 254 万超限+oi 233 万 404+wm 毒 800 万+tmdb 19 万无人像+met 5.6 万+…) |
| `quarantine/qid_images_v2_pid.jsonl.gz` | pid: 命名空间行 | 113.9 万 |
| `rebuild_report.json` | 逐线恒等式(done+perm+retry+未跑≡任务)+组成统计 | |

## 行 schema

**images.v2(一图一行)**
```json
{"sha256":"…", "blob_path":"blobs/4a/4a94….jpg", "ext":"jpg",
 "size_bytes":1896735, "width":3072, "height":2048,
 "tier":"orig|thumb1920|thumb1200|null",
 "license":"CC BY-SA 4.0", "license_url":"…", "license_zone":"nc|\"\"",
 "source":"wm|b1|b3|b3x|b1u|wmsupp|sdc|df20|plantnet|pubchem|si|inat|openimages|met|tmdb|gbif",
 "external_id":"File:…|media:…|photo_id|hex|met_id|CID…",
 "orig_url":"…", "author":"…",
 "fetched_at":1789692711, "fix_state":"ok|resha|meta_refetch|meta_empty|new",
 "qids":["Q7750", …]}
```

**(edges 不再是独立文件——用户拍板单文件防丢;其为构建内存中间产物,不落盘不发布)**

## 规则
- **边去重**:(qid, source, external_id) 保 fetched_at 最新,tier 优先 orig>thumb1920>thumb1200;b1u 升级行**替换**原 thumb 边的 sha(不是新增)
- **图聚合**:按 sha 聚合,metadata 取 tier 最高且最新行;qids=该 sha 全部挂接(去重排序)
- **sdc_attach 229 万边**:88.5% 挂毒 sha,按 (qid, commons_file) 重挂 b1 重建后真 sha(resha);挂不上 → quarantine
- **gbif 滴灌 13,134 张**:license 含 nc → license_zone=nc(blob 物理在非 nc 区,报告单列"迁移建议",账本层先纠标记)
- wm 毒页/坏行/空行 → quarantine,不进 v2
- b2 width/height 多为 null、tier=null,有则填

## 输入清单(sgx ~/merge_input/)
已就绪:snapshots_0923/(20 tar 全账本)+ snap1b/(b2 dead/finished 拆件)+ qid_images.jsonl.gz(主账本 v1)
**待补**:b3w2 done=112 后增量镜像 `0923b`(护航会话自动做)
并账会话自拉:`datasets/candidate/wikimedia/` 三钥匙表(sdc_depicts 挂接重建用)

## 开跑前置(收到 b3w2 收官通知后)
1. 增量镜像 0923b → sgx
2. 恒等式预检(逐线)
3. 金丝雀:单源(si,量小)全流程 → 抽验(存在率+解码) → 全量

## 字段冲突策略(一图一行聚合时,2026-09-23 用户问审定)
- **客观属性**(size_bytes/ext/tier/sha256):覆盖(唯一真值);同 sha 出现冲突=数据 bug,全部进 rebuild_report 异常清单
- **法律属性**(license/license_url/license_zone):覆盖=**取最严格**;冲突样本单独列出供人工抽查(预计极少)
- **渲染语义**(width/height):coalesce,与胜出行(tier 优先、fetched_at 新)对齐取非 null
- **每边事实**(source/external_id/orig_url/relation_type/fetched_at):**不聚合,嵌进主表行内 `refs` 数组**(每条采集记录一个元素);重下能力随单文件自包含
- `qids` 为唯一 list 字段(去重排序)
- 安全底线:主表任何被覆盖丢弃的信息,均在同行 `refs` 数组内或可从原始行重建

## 增量并账机制(2026-09-23 用户拍板:先全量,后续新增增量并)
模式:**增量摄取 + 单文件全量重发布**(不做 delta 文件——多文件违单表原则,且 2-3GB gz 重写仅分钟级)
1. **水位线**:每次并账记录已消费的镜像快照号(0923→0923b→…);下次只处理新快照相对水位的增量行,不重扫原始账本
2. **行级补丁**:增量行按 sha 归类——新 sha 追加新行;已有 sha 打补丁(qids/refs 并集、tier 升级、license 收紧、宽高 coalesce)
3. **重发布**:流式读旧文件+套补丁+写新文件,消费者永远面对一个一致文件
4. **quarantine 联动**:死信重载成功 → 移入主表并在 deadletter 标 resolved
**正确性**:全部合并规则满足结合律(license 最严/tier 最高/集合并集/时间最新)⇒ 增量结果 ≡ 全量重建,无累积漂移;任何时点可从原始账本确定性重跑对账
