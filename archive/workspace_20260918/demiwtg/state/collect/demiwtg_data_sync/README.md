# demiwtg-data 概念契约同步包（2026-09-08）

## 湖侧清单更名告知（2026-09-08 晚）

主仓 meta 权威主清单已由 `instance_images.jsonl` 更名为 **`images.jsonl`**
（用户拍板复用简名；demiwtg-data 侧 VM 本地清单 `image.jsonl` 命名不受影响，
但回湖合并/rsync 时注意湖侧文件名已变）。

## 图片还原状态与补采工作面（2026-09-08，本包核心）

湖机器图片丢失后已恢复**全量 preloss 清单**（meta/images.jsonl 2,849,013 行），
blob 实存仅 152,081 个（Sep 5 备份截断于 80GiB + 本地收割）。补采分两路：

### 产物（本目录）

| 文件 | 内容 |
|---|---|
| **`refetch_min.jsonl.gz`（157MB）** | **补采工作清单（主件）**：缺 blob 且带 URL 的行，极简五键 `{c:[概念], u:url, s:sha256, e:ext, src:源}`，gzip 压缩（全字段版 2.4GB 的 7%） |
| `lake_coverage_for_cluster.jsonl`（180MB） | blob 实存行 194,449（集群 schema，concepts 键）——湖内已有覆盖 |
| `lake_concept_coverage.json` | 概念覆盖摘要（134,941 概念有图 / 15,191 质量门合格） |
| `merge_lake_coverage.py` | 集群侧：覆盖行合并进本地 image.jsonl（幂等） |
| `refetch_missing.py` | 集群侧：按 URL 补采（支持极简/全字段两种清单；sha256 复验/断点/死信轮） |
| `concepts_batch_200.docs.jsonl` | bench283 docs 种子（105 条） |
| `0001-concepts-docs-sidecar.patch` | 概念模式打标 kb 接 docs sidecar（已含在本地 commit） |

### 集群操作序（VM，demiwtg-data 仓根）

```bash
# 1) 湖内覆盖合并（补采不重下已有图；--skip-covered/配额/去重立即生效）
python3 merge_lake_coverage.py lake_coverage_for_cluster.jsonl

# 2) 缺图 URL 补采（极简清单 157MB；先试跑再全量）
python3 refetch_missing.py refetch_min.jsonl.gz --limit 1000
python3 refetch_missing.py refetch_min.jsonl.gz --workers 64
python3 refetch_missing.py refetch_min.jsonl.gz --retry-dead   # 死链重试轮

# 3) blob + 清单回湖：既有 rsync 通道（湖侧清单文件名已是 images.jsonl）

# 4) 常规检索线补采照旧（10,319 无 URL 行 + 质量门未达概念）
```

### 机制要点

- 极简行五键：`c` 概念数组 / `u` 下载直链 / `s` sha256（内容寻址校验与
  blob 落盘名）/ `e` 扩展名 / `src` 采集源（**按源防盗链头表**键：
  baidu/pixiv/huaban 需 Referer）；
- **sha256 复验是唯一入库闸门**：下载内容哈希不符即死信，错内容进不了湖；
- 回写行由 refetch_missing norm_rec 补全 24 字段（缺省 null）；湖侧全量清单
  在册，回灌后按 (sha, concept) join 还原 license/author/打标元数据，零丢失；
- 断点续跑：state/refetch_done.jsonl（done）+ .dead.jsonl（死信）；
- 死信预期：CDN 签名过期/源删除的 URL 会失败，属正常损耗（死信清单供巡检，
  常规检索线兜底）。

### 极简清单再生成（湖侧）

```bash
python3 curation/export_refetch_min.py    # → refetch_min.jsonl.gz（blob 实存过滤 + sha 去重合并）
```

## 背景

主仓 instances.json → concepts.json 概念化迁移（架构决策 2026-09-07）后：

- **批次契约（已对齐）**：demiwtg-data 上游已上线 `--concepts` 批任务模式
  （`operators/concepts.py`），原生消费四字段概念行，v2 适配层直接吃主仓
  `state/collect/concepts_batch_200.json`（283 行），text-only 概念图像线自动跳过。
- **docs sidecar 补丁（commit 056f4e5+，本地 main ahead）**：概念模式打标 kb 的
  知识文本自 `<批文件>.docs.jsonl` 伴随文件注入；种子 105 条。

## 应用路径（本机 GitHub 推送凭据不可用——HANDOVER#13：CN 机 github 不通）

1. **SG 授权机直接推送**（首选）：/tmp/kilo/demiwtg-data 本地 main 已含
   056f4e5（docs sidecar）与 merge/refetch 两笔，`git push origin main`；
2. **VM 部署**：代码走既有 rsync 通道；本目录数据件随同步包上集群。
