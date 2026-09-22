# 第 2 批运行手册（B2_RUNBOOK · 2026-09-20 建）

> 架构 = 第 1 批第三代同款（COS 队列 + worker 认领），机制全部由 demiflow 平台承担
> （cosio/cosqueue/queue_runner），本目录只有业务算子与配置。
> **红线**：图片批量下载须用户放行；SI/iNat 等流量包到位后再放（出向 8-12TB 会穿 1TB/机套餐）。

## 组件

| 件 | 作用 |
|---|---|
| `b2_op.py` | 批算子：curl_fetch 下载 → 魔数/尺寸闸门 → sha256 blob 直传 COS → ledger/dead 双账本；Met 两段式 URL 解析（进程内缓存） |
| `cut_lists.py` | 四源 tsv → 统一 jsonl 任务行 → `queue-b2-<src>/batches/`（2000 行/批）+ manifest.json |
| `deploy_b2.sh <src> [n台]` | vendor demiflow_collect 五件套 + b2_op + .cos_creds → r 机 ~/wk_b2/ |
| `launch_b2.sh <src> [n台] [lanes]` | 每机 1 worker setsid 发射（幂等），末尾打队列快照 |
| `lists/` | 四源 fetch_list 已拉湖（met 1.1M / oi 133M / si 131M / inat 130M） |

## 已验证（2026-09-20 晚，湖侧金丝雀）

- OI：19/20 成功、blob 回读尺寸 19/19 一致、1 个真 404 入死信（预期损耗）
- Met：20/20 成功（两段式 API + 主图下载全通）
- **OI URL 已改写**：GCS `storage.googleapis.com/openimages/` 已收回公开读（403
  Anonymous caller，湖侧与 SG 出口同现）→ 改 `open-images-dataset.s3.amazonaws.com/`
  S3 镜像（同构路径、匿名可读、字节验证 JPEG）
- SI：307 跳转后 200 JPEG（exec_curl `-sSLk` 天然支持）；iNat：直 200

## 队列（COS `lhcos-data/demiwtg-data/queue-b2-<src>/`）

OI 289 万行 / SI 329 万独立 media（474 万行聚合）/ iNat 358 万行 / Met 5.7 万行。
行级幂等：blob 同尺寸跳过 + worker ledger done-set。

## 启动序（放行后）

```bash
cd /yzp/zhaozy/yangzepeng/0905/demiwtg/collect/batch2
bash deploy_b2.sh met 20 && bash launch_b2.sh met 20 4   # 先 Met（5.7万，~分钟级）
bash launch_b2.sh oi 20 4                                # OI ~500GB
# SI/iNat：等流量包，届时同款两行
```

监控：各机 `~/wk_b2/b2_<src>.log`；湖侧 `COSQueue.snapshot()`；收尾并账 = 各机
`~/wk_b2/run_*/ledger.jsonl` 拉湖合并（按 extid 去重取最新）→ `qid_images_ext/batch2_<src>.jsonl.gz`。

## 已知坑（本批新增）

1. OI GCS 403 → S3 镜像改写（cut_lists 内固化）；
2. OI fetch_list 第二列是**消歧后 Q 号数字部分**（无 Q 前缀；与 mid_map 值域 891/891
   命中实证），mid_map 仅审计留档不参与展开；
3. 湖侧 demiflow 是 PEP660 editable 静态映射，新模块要 `sys.path` 指仓库（b2_op 已内置）；
4. Met primaryImage 空值占位 = 对象无图，入死信 `met:no_primary_image`。
