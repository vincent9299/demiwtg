# p1–p5 释放归档（2026-09-17）

> 背景：湖机接管 master，舰队重编号 r1–r20 / p1–p5（原 pipeline-a~d / sg-master），
> p1–p5 计划释放。本目录是五机一次性资产的集中归档，230 件对象已从湖侧
> 签名 HEAD 逐一校验（尺寸全符）。对照表见 demiwtg-data/HANDOVER_TRAINING_MACHINE.md §九。

## 内容

| 目录 | 内容 | 来源 |
|---|---|---|
| `raw/` | 基础设施工具箱 58 件（stream_cos/cos_cat 新版/fuse_*/backfill 审计等 *.py *.sh *.md） | p5 ~/demi/raw/ |
| `raw/state/` | kb 审计状态 74 件：blobs_inventory.tsv(738M)、cand_small.tsv(658M)、sniffshard_00-07、thumb1200_rows.jsonl.gz(50M)、join1 产物、met/df20/q_* 中间件 | p5 ~/demi/raw/state/ |
| `logs/` | 工具箱运维日志 | p5 ~/demi/raw/logs/ |
| `kb_night/` | 传输线/夜间护航脚本 45 件（launch_drip5、watch2*、night_scaleup、tunnel_guard、ship_watch、night_sweep、drip_ship2 等） | p5 ~/demi/kb_night/ |
| `demiwtg-untracked/` | git 未提交交接文档（HANDOFF_V2 / RESUME_2026-09-17 / SHIP_STATUS_2026-09-14）+ sdc_fetch/ 整目录 | p5 ~/demi/demiwtg-data/ |
| `feeds/p1/` | 投喂清单 image-shard-extsdc(986M) / extpubchem(12M) | p1 ~/lake/meta/ |
| `feeds/p3/` | 投喂清单 image-shard-extdf20(107M) / extplantnet(126M) | p3 ~/lake/meta/ |
| `private/` | cos_creds.p5 / cos_creds.p4（70B sid:key）、p5_bash_history.txt（WIT 重试命令在历史里） | p5 / p4 |

## 有意不归档的（去哪找）

- `concept_xref / mid_to_file / sdc_depicts` 三张钥匙表（3.0G）：正树 `datasets/raw/wikimedia/` 有同尺寸权威副本（已比对）
- `raw/state/qid_images.jsonl`（5.1G 解压账本）：湖 meta/ 有 `qid_images.jsonl.gz`（1.13G），zcat 即得
- `sniffout_00..07.tsv`（嗅探产物，**在跑**）：完成后从 p5 补传到本目录 `raw/state/`（完成标志各 lane 日志 SNIFF_DONE）
- p2/p3 的 /tmp 中间产物（SDC extract 残料，工单标注可清）
- git 主线代码：origin（github.com/vincent9299/demiwtg-data）@ 6fd4e0e

## 释放 p1–p5 前必须完成的重定向（否则湖侧设施断粮）

1. **lake_sync readers** p1–p5 → r 机（r 机 cosfs 须可读 `datasets/demiwtg/blobs` 树）
2. **反向隧道**（22022–29，现落 p5）→ 迁某台 r 机；传输线续传（82%→补缺 158 万）依赖
3. **投喂清单重落位**：本目录 feeds/ 抓回某台 reader 的 `~/lake/meta/` 并入 NODE_GROUP
4. 在途任务改址：iNat 抽取（原 p1）/ Smithsonian v2（原 p4）/ OI+Met（原 a/d）→ r 机或湖本机
5. p5 等嗅探完成 + sniffout 收割后再放
