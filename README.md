# kb 图池审计工具链（2026-09-17 事故复盘产物）

来源：sg 节点 `~/demi/raw/`（此前未入库，仅 scp 交接）。用法与产物路径见
`HANDOFF_AUDIT_2026-09-17.md` §3/§4，执行序见 §5。

- `cos_inventory.py` — 匿名 COS API 列全量对象（绕 cosfs，190s/887 万对象）
- `audit_join1.py` — 账本×库存对账（missing/孤儿/尺寸错配/≤6KB 候选）
- `cos_sniff.py` — Range-GET 前 2KB 魔数嗅探（4 进程×24 线程为上限，8×48 GIL 停摆）
- `audit_join2.py` — 嗅探×账本 → poison_html_rows / 终报（待嗅探完成后跑）
- `cos_deepcheck.py` — 内容完整性抽样（400 全量 sha256 + 300 张 PIL 解码）
- `audit_all.sh` — 串接入口
- `audit_blob_magic.py` — 旧版 cosfs 方案，已弃用，留档
