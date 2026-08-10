# scripts/ · 清洗与工具脚本

本目录下所有脚本都面向同一份数据：`V2融合世界标签体系_清洗版.txt`（UTF-8 纯文本树，用 `├──`/`└──` 与 4 空格缩进表达层级）。

## 统一约定

- **解析**：每行一棵节点，层级 = `缩进空格数 // 4 + 1`；共享正则
  `NODE_RE = re.compile(r'^((?:[│  ]{4})*)([├└]── (.*))$')`。
- **幂等预览**：所有"会改树"的脚本默认只打印 diff（`unified_diff`），加 `--write` 才真正落盘。
  改之前先用 `python3 scripts/xxx.py` 看 diff，确认无误再 `python3 scripts/xxx.py --write`。
- **落盘后**：用 `python3 -c "import re,sys; ..."` 或任意解析脚本复跑，确认 0 解析错误、无层级跳变（`depth[i]-depth[i-1] > 1`）。

## IP 段清洗脚本（按时间顺序）

| 脚本 | 作用 | 关联批次/issue |
|---|---|---|
| `add_ip_branches.py` | 加入 6 个新 IP 分支（P1–P6），以硬编码 `ORDER` 重建 19 个顶层分支顺序 | 提案落地 |
| `fix_ip_section.py` | IP 段首轮：迁移 `跨品类品牌类别` 至 `品牌 IP`、提升 `新兴物种类别` 为顶层、删机翻残留、合并 7 处跨分支重名 | 第一批次 |
| `split_landmark_weapon.py` | 拆出 `地标 IP` / `武器 IP` | 早期拆分 |
| `split_role_mascot.py` | 拆出 `虚构角色 IP` / `吉祥物与形象 IP` | 早期拆分 |
| `split_works_event.py` | 拆出 `内容作品 IP` / `艺术与文物 IP` / `赛事 IP` / `潮玩互动 IP` / `乐园节庆 IP` | 早期拆分 |
| `clean_ip_phase3.py` | 删 `历史古人/艺术家` 机翻子树；`海洋生物` 去重收拢；`虚拟偶像` 迁 `虚构角色 IP`；`新兴物种类别` 拆分（AI→科技与数字、再生人/幽灵/神→虚构角色）；压平 `武器`/`著名载具` 包装层 | 第三批次 |
| `clean_ip_phase4.py` | 删错位叶子、删错误 facet 包装层、剥除非顶层节点 ` IP` 后缀（194 处改名） | 第四批次 |
| `clean_ip_phase7.py` | 机翻语义残留改名修复（如 `葡萄`→`葡萄弹`、`狗鱼`→`长矛`、`吸盘`→`棒棒糖`） | 第七批次 |
| **`clean_ip_review_fixes.py`** | **review 收尾 #3–#7：删矛盾品牌 facet、清 `真人与人物 IP` 机翻深层节点、删 `巴祖卡`、`人工智能主体—生存状态`→`—运行状态`、`新兴物种 IP` 并入 `科技与数字 IP/前沿科技/生物科技` 并撤销空分支** | review 第八批次 |
| **`unify_ip_facets.py`** | **review #2：双 profile 受控方案下，补齐 `品牌 IP` 内 11 个缺 facet 的子类（Profile B），使 24 子类全覆盖** | review 第八批次 |

## 通用工具脚本

| 脚本 | 作用 |
|---|---|
| `build_tree_view.py` | 把树渲染为 `output/taxonomy_tree.html` 交互式查看器（解析器见 `scripts/build_tree_view.py:18`） |
| `analyze_residuals.py` | 残留命名分析（形容词/量词误作节点等） |
| `fix_residual_names.py` | 残留命名修复 |
| `regenerate_residual_csvs.py` | 重新生成审查 CSV（`标签审查_*.csv`） |

## 运行顺序建议

1. 基线冻结后，按 `add_ip_branches` → `fix_ip_section` → `split_*` → `clean_ip_phase3/4/7` 复现历史清洗；
2. review 阶段依次跑 `clean_ip_review_fixes.py`（`--write` 前务必先看 diff）、`unify_ip_facets.py`；
3. 每次落盘后用任意解析脚本校验树形完整性。

> 注：`add_ip_branches.py` 的 `ORDER` 常量仍含旧名 `新兴物种类别`，该脚本为一次性迁移脚本，已在 `clean_ip_review_fixes.py` 撤销该分支后失去复用意义——后续若重建顶层顺序需先更新 `ORDER`。
