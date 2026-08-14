# R8 续跑与 newtags 合并计划（2026-08-13）

> **状态：已完成（2026-08-14）**。R8 全队列 1,168 叶 ≥38 实例；newtags 774 实例已固化 `scripts/v3/exp_newtags.py`；`build_v3.py` 重跑、结构验收全绿、幂等；`build/tag_tree_v3.json`、`tag_tree_explorer_v3.html` 已重建。
> 续跑管线：`/tmp/opencode/r8_launcher2.sh`（rem/top 双队列，worker `/tmp/opencode/r8_worker2.sh`，并发 20），合并脚本 `/tmp/opencode/r8_merge.py`。

## 背景
- R8 逐叶扩充：1168 个叶子队列，前会话完成 406，后台续跑剩余 762（启动器 /tmp/opencode/r8_launcher.sh，结果 /tmp/opencode/r8/r8_*.json）。
- `data/image_collect_config.newtags.json`（08-13 14:29，774 jobs / 773 实例 / 192 叶子）：V2 review 新增标签。
  - 现状：773 条实例已全部在 `data/ip_instances_v3.json` 中（0 缺失）；
  - 但其中仅 344 条被 exp_*.py 模块覆盖，429 条只存在于 json（非模块来源），需固化进模块保证可重建。

## R8 跑完后要做
1. 合并全部 r8_*.json（含前次 16 个漏合并）→ 重新生成 `scripts/v3/exp_round8_auto.py` 的 INSTANCES。
2. 新增模块 `scripts/v3/exp_newtags.py`：从 image_collect_config.newtags.json 生成 INSTANCES（leaf 路径用 " / " 分隔），并入 build_v3.py 的 MODULES。
3. 重跑 `python3 scripts/v3/build_v3.py`，然后结构校验（孤儿键/重复/叶子覆盖）。
4. 校验：773 条 newtags 实例全部在位；R8 各叶列表 ≥38 且无重复。
5. 更新 `data/V3融合世界标签体系.txt` 与 `tag_tree_explorer_v3.html`（如需）。

## 监控
- 进度：`ls /tmp/opencode/r8/r8_*.json | wc -l`（目标 1168）
- 失败：`grep -l FAILED /tmp/opencode/r8_logs/*.log`

## deepseek-harness 安装（进行中）
- 仓库已 clone 至 `/root/data/deepseek-harness`（node v22.23.2 满足 ^22.19.0，corepack 已启用 pnpm@11.7.0）
- `pnpm install` 首次因网络超时中断（typescript 下载超时），已设置 fetch-timeout=600000 / fetch-retries=5，待重试
- 完成后：`pnpm run build`，再 `pnpm dsh web`（默认 http://127.0.0.1:3080）
