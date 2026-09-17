# 图片补下载交接：玻璃棒试跑与剩余缺图

日期：2026-09-16。交接对象：GLM5.3。用户要求先补玻璃棒，再交由 GLM5.3 补其余图片；本文件为用户明确要求的交接文档。

## 已完成什么

入口是 **本地权威 `datasets/demiwtg/meta/images.jsonl`，不是 QID 数据集**。本任务只恢复原清单对应的图片字节，不改概念、清单、知识候选或模型服务。

玻璃棒原有 73 条记录，6 张本地存在，67 张缺失。复用采集机 `demiwtg-data/backfill.py` 实际补下载后：

- **57 张恢复成功**，均核验原 SHA256，并经 Pillow 图片校验后原子写入本地原路径。
- 当前 **63 张本地可用、10 张仍缺失**。
- 10 张均为 `sha_mismatch`：9 张 quark_images、1 张 giphy。同一 URL 可能对应多个历史 SHA，不能因为 URL 一样就合并历史内容身份。
- 没有把 SHA 不同的新响应冒充原图，没有修改 `images.jsonl`。
- 没有重跑知识 pipeline。旧 `glass_operator_debug_v3` 的 `not_local` 检查结果是当时快照，仍保留。补图不代表已通过概念筛选、联合提炼或支持核验；下次知识运行需新版本，不能直接改旧检查点为通过。

## 路径

本地项目根：`/yzp/zhaozy/yangzepeng/0905/demiwtg`

本次结果目录：`state/curation/glass_image_backfill_v1/`

| 文件 | 内容 |
|---|---|
| `summary.json` | 最终数量及范围 |
| `original_records.jsonl` | 67 条玻璃棒输入记录及原清单行号 |
| `candidates.jsonl` / `download_candidates.jsonl` | 67 条五键补下载输入 |
| `sg_probe.json` | 采集端已有文件探测 |
| `remote_invalid_files.json` | 采集端 5 个同名文件实为 0 字节的证据 |
| `attempt1/meta/backfill-shard-all.jsonl` | 57 张下载成功记录 |
| `attempt1/meta/dead-shard-all.jsonl` | 10 张失败记录，含 URL、源、SHA、原因 |
| `attempt1.log` | 完整下载日志 |
| `remote_code_version.txt` | 实际代码提交、关键文件 SHA 与 Python 版本 |
| `recovered.tar` | 本次采集端成功图片归档，已导入本地 |
| `imported.json` | 57 张本地入库记录、路径、字节数、校验结果 |
| `remaining_glass_records.jsonl` | 尚未恢复的 10 张玻璃棒原记录 |
| `all_missing_candidates_at_start.jsonl.gz` | 开始时全库 168,481 张有 HTTP(S) content_url 的缺图候选 |
| **`remaining_candidates_for_glm.jsonl.gz`** | **扣除已恢复 57 张后，168,424 张可交给 GLM 的候选** |
| `alternative_urls.jsonl.gz` | 同 SHA 的其他历史 URL，可用于首 URL 失败后的定向尝试 |
| `missing_without_content_url.json` | 7,417 个缺少 HTTP(S) content_url 的 SHA；不代表没有 landing_url |

全库剩余 175,841 个缺失 SHA = 168,424 个有 content_url + 7,417 个需另行查找。上述数量为本次快照推算，应在正式启动前重新按本地实存排除已被其他任务恢复的文件。

全库起始盘点：`state/curation/image_presence_audit/20260916T134402Z.json`。原始清单 2,899,895 行、2,163,475 个 SHA。盘点只做存在性检查，不表示约 199 万个现存文件都经过内容校验。另有 4 条路径不匹配但同 SHA 本地存在，属于路径核对，不应重复下载。

## 实际用的采集程序

SSH：`sg-master`

- 仓库：`/home/ubuntu/demi/demiwtg-data`
- 提交：`ca07981b1c77a18f1311f75851b4c6ce12117ac4`；以 `remote_code_version.txt` 和后续实际检查为准。
- Python：**`/home/ubuntu/demi/.venv/bin/python`**。系统 `python3` 缺 demiflow/httpx，不能直接用。
- 当前入口是 **`backfill.py`**，历史文档的 `refetch_missing.py` 在该机器当前仓库不存在。
- 复用其 demiflow `BackfillStage`、`fetch_tiers`、来源防盗链头、限速与 SHA 闸门。
- 输入：`{"c":["概念名"],"u":"原图片URL","s":"原SHA256","e":"扩展名","src":"源名称"}`。
- `--shard I/N` 按原输入行号分片，清单每分片独立；`--sources` 可路由来源；gzip 输入原生支持。
- 本次下载并发 6，单图 hard timeout 90 秒，上限 20 MB；全批 67 张约 1.2 分钟。
- 程序的临时网络耗尽和写盘异常可能不记 dead：必须核算 `输入 − 成功 − 失败 = 未决`，不能只看程序退出 0。

本次执行命令（只供复现/续跑这批；不要把全库任务写进该运行目录）：

```bash
cd /home/ubuntu/demi/demiwtg-data
/home/ubuntu/demi/.venv/bin/python -u -m backfill \
  --candidates /home/ubuntu/lake/glass_image_backfill_v1/download_candidates.jsonl \
  --dataset /home/ubuntu/lake/glass_image_backfill_v1/attempt1 \
  --blob-root /home/ubuntu/lake/glass_image_backfill_v1/recovered \
  --concurrency 6 --log-every 5
```

## GLM 接手步骤

1. 阅读本项目 `AGENTS.md`，检查采集仓当前说明、磁盘、活跃任务和 Python 环境。不要沿用历史 PID，不重启知识模型或图片预标注服务。
2. 使用 `remaining_candidates_for_glm.jsonl.gz`，重新扣除本地已经存在且可用的图片。先在采集端查原字节，再补下载；远端文件必须校验，不能只看存在。
3. 为全库创建**新的运行目录**，复制候选文件并记录哈希、代码版本和命令。按来源分组/行号分片逐批处理，保持相同输入文件及分片配置以续跑。大规模前检查磁盘与吞吐，定期将已完成批次拉回本地。
4. 成功产物经原 SHA 与图片解码校验，原子落入本地 `datasets/demiwtg/blobs/<sha前2位>/<sha>.<ext>`。优先核对原清单 `path`，已有有效文件跳过；已有文件不同内容须单独调查，不能覆盖。清单元数据已存在，无须用下载简表覆盖它。
5. 汇总每批已恢复、网络失败、SHA 改变、未决。失败重试开新 attempt 保留旧证据；可试 `alternative_urls.jsonl.gz` 的同 SHA 其他来源。
6. 10 张玻璃棒 SHA mismatch 可先列入内容变更队列，勿原样无限重试。7,417 个无 content_url 的 SHA 需回查原清单的 landing_url、远端副本或历史下载记录。若只能取得不同字节的新图，应作为新图片采集处理，不能算旧 SHA 恢复成功。
7. 完成后重新盘点，给出成功/仍缺/失败分类及报告路径。不要自动重跑全量知识提炼。

## 本轮踩到的坑

- **原始 source 必须从权威清单取。** notebook 的 `selected_images`/`processed_images` 已将 `source` 用作溯源对象，不能直接传给下载器。此次用 `source.row` 回查原清单，并核对 SHA 后恢复来源名称。全库候选直接读取权威清单，已保留真实源名。
- SG 的 `/home/ubuntu/lake/blobs/` 查到 5 个同名文件，实际全部 0 字节。原生 backfill 对已存在路径只检查 `exists()`，会误跳过这种文件。本次使用独立、初始为空的 `recovered/`，未修改旧文件；全库应对待恢复集合检查大小/哈希或使用独立目标目录。
- 不能盲信旧 done/dead/synced 账本：这是恢复当前缺失文件的工作。**此次不加载历史 synced-ledger，是因为候选已经确认本地缺失且使用独立恢复目录**。普通采集/同步仍按原契约使用账本；不要删除公共账本。
- CN `pipeline-e` 和 `pipeline-g` 本轮 SSH banner 超时，因此未完成广州端副本核对；这不等于那里的文件不存在。SG 的 COS 根及本地目录已对本批探测，最终全部 67 个 URL 均由 SG 发起下载。
- 本轮没有调用 `lake_sync --once`，因为它会处理范围更广且默认含远端清理。仅将独立恢复目录打包传回、验证导入。GLM 若复用 lake_sync，先确认范围，使用无清理模式，不要为补图删除远端副本。
- 下载成功仅代表恢复同一图片内容，与图片是否概念相关、是否能支持知识无关。

## 交接边界

本轮只补玻璃棒，其他候选已准备但**未启动全库下载**。没有发消息给 GLM 或启动其他代理。用户将本文件交给 GLM5.3 即可继续。
