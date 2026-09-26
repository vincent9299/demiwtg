# 交接文档：SDC 取新图（sdc_depicts × mid_to_file → 清单 → 下载入库）

> 生成：2026-09-17 00:35 ｜ 作者：数据融合夜航（ZCode 会话）
> 范围：**仅下载新图任务**。零下载的"补挂"（交集部分）由原窗口执行，请勿重复。
> 预计工作量：清单生成 1 小时（CPU）+ 下载按清单规模（首期限额后约 300-600 万张，数天级，可分批）

## 一、任务定义

Commons 每个文件的"depicts（画的是什么概念）"结构化标注已全量抽取。
其中 **2,140 万个文件**画着我们的概念（QID 命中概念集），但多数图片字节我们还没有。
你的任务：算出"该下哪些文件"的清单，把它们下载入库（sha256 字节池 + 账本）。

```
sdc_depicts(53.3M 边) ──×概念集──▶ 21,428,019 个命中文件(M-id)
        │                              │
   mid_to_file                    减去现有 837 万文件名
        ▼                              ▼
      文件名 ──────────────▶ 待取清单(预估 1500-1800 万，限额后 300-600 万)
                                       │
                          image 表预筛 → 采集下载 → blobs + 账本
```

## 二、输入数据（COS 桶 lhcos-368f6-1256345599）

**⚠ 头号坑——双树前缀：一切路径/API key 必须带 `lhcos-data/` 前缀。
桶根下的同名文件是历史误传副本，勿用勿信。**

| cosfs 路径 | 内容 | 行数 | 格式 |
|---|---|---|---|
| `/lhcos-data/demiwtg-data/datasets/raw/wikimedia/sdc_depicts.tsv.gz` | 全部 depicts 边 | 53,320,331 | TSV: `M-id\tQID\trank\tqualifier属性列表(多为空)` |
| `/lhcos-data/demiwtg-data/datasets/raw/wikimedia/mid_to_file.tsv.gz` | M-id↔文件名 | 142,861,248 | TSV: `M-id\t文件名` |
| `/lhcos-data/demiwtg-data/datasets/demiwtg/kb/qid_concepts.jsonl.gz` | 概念集(782.6万) | 7,826,266 | jsonl，取 qid 字段 |
| `/lhcos-data/demiwtg-data/datasets/demiwtg/kb/qid_images.jsonl.gz` | 现有账本(排除用) | 8,861,354 | jsonl，取 commons_file 字段 |
| `/lhcos-data/demiwtg-data/datasets/raw/wikimedia/commonswiki-latest-image.sql.gz.part-00000..00017` | 全站文件预筛（原始 SQL dump，18 块按序流式解压，**不能从中间块解**） | 1.47 亿 | INSERT 语句里的 img_name/img_sha1/img_size/img_media_type |

真实样例：
```
sdc_depicts:  M99	Q17454866	normal	
mid_to_file:  M99	Quail2.png
qid_images:   {"qid":"Q100087","commons_file":"Polinago chiesa parrochiale.jpg",...}
```

## 三、清单生成（算法）

```python
concepts   = qid 概念集                                  # 7.8M
have_files = commons_file 集合 from qid_images           # ~837万

# M-id 映射太大(1.43亿行)不能整读 dict(约需10GB)——两种方案:
#  a) 排序归并: 两文件都按 M-id 数值序,归并扫描
#  b) sqlite 临时表: mid_to_file 入库(约15分钟),建索引后 join
want = {}   # 文件名 -> [QID...] (概念集内)
for (mid, qid) in sdc_depicts if qid in concepts:
    fname = mid2file[mid]
    want.setdefault(fname, []).append(qid)

# 配额: 每概念 ≤30 张(工单硬规定,防"巴黎铁塔"类高频概念吃掉预算)
#   按文件 sha/名字稳定排序后截断,保证可复现
# 排除已有:
fetch_list = [(f, qids) for f in want if f not in have_files]

# image 表预筛(流式解18块,INSERT行正则抓 img_name/img_size/img_media_type):
#   丢掉 非BITMAP图 / img_size<50KB / img_size>64MB
# 输出 fetch_list.tsv: 文件名 \t QID列表(逗号) \t img_size
```

**输出示例**：`Eiffel_Tower_from_Trocadero.jpg  Q243  1048576`

## 四、下载入库（执行规范）

- **URL 构造（不走 API）**：文件名 UTF-8 取 MD5 →
  `https://upload.wikimedia.org/wikipedia/commons/<md5[0]>/<md5[0:2]>/<percent_encode(文件名)>`
  备选：`https://commons.wikimedia.org/wiki/Special:FilePath/<percent_encode(文件名)>`（重定向）
- **UA 必须**：`ConceptKB/1.0 (mengdebin@bytedance.com)`；单机并发 ≤8；遇 429 指数退避
- **字节入库**：sha256 内容寻址，key =
  `lhcos-data/demiwtg-data/datasets/demiwtg/blobs/<sha前2位>/<sha>.<ext>`
  （有持久连接的 multipart/PUT；上传前 HEAD 查重跳过）
- **账本行**（jsonl，与 DF20 融合同构）：
```json
{"qid":"Q243","sha256":"...","blob_path":"blobs/xx/sha.ext","source":"sdc",
 "license":"<从下载页或image表带出,缺失置null>","size_bytes":N,
 "relation_type":"depicts_part","external_id":"M99","confidence":"sdc-p180",
 "rank":"normal","orig_file":"Eiffel_Tower_....jpg","fused_at":...}
```
- **两处落账**（缺一不可）：
  1. COS 真相：`/lhcos-data/demiwtg-data/datasets/demiwtg/kb/qid_images_ext/sdc_fetch.jsonl.gz`
  2. 节点投喂：`~/lake/meta/image-shard-extsdcfetch.jsonl`——**行内必须有 `blob_path` 字段**
     （lake_sync 只认 `json.loads(line)["blob_path"]`，字段名错=整批静默丢失）
- 可复用参考实现：`pipeline-*:/tmp/fetch_generic.py`（COS 直传+幂等+账本）与
  `/home/ubuntu/demi/demiwtg-data/flow_images_batch.py`（令牌桶+分片，历史 100张/秒/25机）

## 五、验收（三段漏斗 + 抽检）

1. 清单数 / 限额后数 / 预筛后数（GB，用 img_size 求和）
2. 下载成功数 / 429退避数 / 失败数（失败行保留可重试）
3. 入库 blob 数与账本行数一致；sha256 抽 50 张回读校验
4. 随机 20 个 URL 人工核 depict 真伪（commons.wikimedia.org 搜文件名看 SDC 标签）
5. 报"新增覆盖概念数"（此前无图的概念）

## 六、已知坑（血泪清单，按踩中概率排序）

1. **双树前缀** `lhcos-data/`（不带的路径可能读到大同小异的旧副本）
2. **`blob_path` 字段名**（写成 path 则 lake_sync 全部跳过）
3. mid_to_file **不能整读内存**（10GB+），用排序归并或 sqlite
4. 文件名**三重转义**：mid_to_file 已做 SQL 反转义；构造 URL 要 percent-encode；
   MD5 用未转义的 UTF-8 原文
5. **http.client 非线程安全**：多线程必须线程本地 HTTPSConnection
6. image 表 18 块是**单流 gzip**，只能按序整流解压
7. image 表的 img_sha1 是 **SHA1(base64)**，与账本 sha256 不可混用（只能做下载校验参考）
8. 机器资源：pipeline-a/b 空闲可用（各 2 核）；**勿用 master（VM-0-14）**——在跑同步跳板；
   pipeline-c 正在跑 DF20 融合（00:40 左右结束，之后也可用）

## 七、状态快照（勿动项）

- DF20 融合进行中于 pipeline-c（203,151 张，看门狗自动续跑）——**勿在 c 节点起新任务至其完成**
- 桶根→正树迁移已完成；三张输入表已验证在正树
- lake_sync 未运行（湖 pod 忙）；账本行会积压，等它重启自动拉，**不丢**
