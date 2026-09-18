# 融合世界标签体系 v3.1 · 交付包说明

> 打包时间：2026-08-24 ｜ 最终状态：29 域 / 21,406 行 / 16,665 叶 / 346,634 中文实例 / 473,942 英文实例
> 本包 = 最终数据 + 说明文档 + 可复用工具（已脱敏，不含任何 API 密钥）

---

## 包结构

```
taxonomy_v3.1_交付包/
├── README.md                      ← 本文件
├── data/                          ← 数据（核心交付物）
│   ├── taxonomy_merged_progress_instances.csv   ★ 中文终版底稿（2列：路径+实例）
│   ├── taxonomy_tree_instances_en.csv           ★ 英文终版底稿（3列：中英路径+英文实例）
│   ├── taxonomy_source_full_v3.1.csv            ★ 检索源终版底稿（3列：路径+源清单+源详情）
│   ├── originals/                 原始基线（21,520 行，永不改动，仅作追溯）
│   └── backup/                    三批迁移各自落盘前的备份（迁移批1前/IP吸收批前/迁移批2前）
├── docs/                          ← 说明文档
│   ├── 收尾清单_v3.1终版文件一览.md      全项目盘点
│   ├── 世界目录_骨架v3.1_29域全览_裁定后.html  骨架全览（人读版，浏览器打开）
│   ├── 世界目录_骨架全览_v3.1.json/md   骨架全览（程序读/人读）
│   ├── 迁移改动清单_批1与IP吸收批.md     逐条改动清单①
│   ├── 迁移改动清单_批2_知识与学科清理.md 逐条改动清单②
│   ├── 检索源同步批_改动清单.md          逐条改动清单③
│   ├── 分层评审计划_v1.md               未来评审路线图（域内重复16,875/超大叶337/主题扫描）
│   └── 世界目录_域元数据.json            29 域元数据
├── tools/                         ← 可复用工具（非临时性脚本）
│   ├── health_check.py            ★ 数据体检（每批落盘后必跑）
│   ├── apply_migration_batch1.py  迁移批1执行器（51条域级前缀替换+9条人工裁定）
│   ├── apply_ip_absorption.py     IP吸收批执行器（22个IP域并入29域）
│   ├── apply_migration_batch2.py  迁移批2执行器（知识与学科域清理）
│   ├── apply_source_sync.py       检索源同步批执行器（重放三批映射）
│   ├── build_batch2_draft.py      批2草案生成器（裁定用草案的生成方法）
│   ├── build_skeleton_v3_1.py     骨架构建器（骨架工件生成）
│   ├── verify_batch2.py           独立复核脚本范例
│   ├── review_run.py              三模型背靠背结构评审（密钥走环境变量）
│   ├── mappings/                  四份映射表（每批迁移的程序化依据）
│   └── enrichment/                实例扩产工作线工具
│       ├── generate_task_files.py       任务书生成器
│       ├── prompts_v3.py                实例生成提示词
│       └── 通用子树任务提示词模板.md
└── review_artifacts/              L1+L2 结构评审产物（三模型原始意见+prompt）
```

---

## 核心数据（3 份终版底稿，逐行对齐）

| 文件 | 列 | 行数 |
|---|---|---|
| `taxonomy_merged_progress_instances.csv` | node_path, instance清单 | 21,406 |
| `taxonomy_tree_instances_en.csv` | node_path, node_path_en, instance清单(英文) | 21,406 |
| `taxonomy_source_full_v3.1.csv` | node_path, source清单, source详情(名称;URL;类型) | 21,406 |

- 路径分段符：` / `；实例分隔符：`|`；编码：UTF-8 BOM，行尾 `\n`
- 路径形态：`融合世界标签体系 / 通用分类标签 / <域> / <L2> / ...`，域在第三段
- 三份文件第一列（中文路径）完全一致，可直接按行或按键 join

## 数据红线（重要）

1. `data/originals/` 里的两份原始文件（各 21,520 行）**永不修改**，一切产出物都是新文件
2. 任何数据改动必须走：**备份 → 映射表 → 执行器 → 数量校验 → 逐条改动清单**
3. 执行器套路（所有 `apply_*.py` 通用）：先干跑（默认）→ 人工确认 → `--apply` 落盘；落盘前备份，落盘后断言校验（行数/实例数/路径集/中英一致/EN首段禁中文）

## 工具用法

### health_check.py（数据体检，最常用）
```bash
python3 health_check.py <中文底稿> <英文底稿> [--out 工单目录]
# 例：python3 health_check.py data/taxonomy_merged_progress_instances.csv data/taxonomy_tree_instances_en.csv --out ./工单
```
检查：同父同名碰撞、空中间分支、叶内重复、中英一致、EN首段中文、跨叶重复实例（域内/跨域拆分）、超大叶/小叶、L2深平分解。产出三份工单（域内重复实例/超大叶/同名段观察）。退出码 0=结构全过，2=有结构问题。

### review_run.py（三模型结构评审）
```bash
export OPENROUTER_API_KEY=sk-or-v1-xxxx   # 自备密钥（原仓库密钥已脱敏移除）
export PROXY=http://127.0.0.1:7891        # 可选
python3 review_run.py
```
注意：打包版已改为自包含，密钥只从环境变量读取。

### apply_*.py（迁移执行器）
全部支持干跑：不加参数只打印变更计划与校验结果，加 `--apply` 才写盘。映射依据在 `tools/mappings/`。

## 未竟事项（接手者备忘）

1. **分层评审计划**（见 `docs/分层评审计划_v1.md`）：域内重复实例 16,875 个（平行分支合并候选）、超大叶 337 个、横向主题扫描（地理主题已验证方法）
2. 迁移批2 遗留 8 个待二评桶（点/界限/线/收藏品集合/大纲/报告/表格/描述，1,128 实例）
3. 2 处「帝王蟹/蟹」斜杠段名多义词旧账（中英段位天然不齐，校验时豁免）
4. 空域扩产：医学与健康、宗教与信仰、民族语言文化等域仍偏薄
