# scripts/ · 标签体系数据工具

## 权威数据（全量收敛后，仅此两份 + 一份 schema）

| 文件 | 作用 |
|---|---|
| `data/taxonomy.json` | 标签树：节点结构 + KB 字段（`definition`/`knowledge_intro`/`aliases`/`representative_cases`/`related_tags`）+ `instances` 实例名称列表（结构指针） |
| `data/instances_meta.json` | 实例元：扁平 `instances[]`，每条 `{name, category, source, intro?, definition?, desc?, aliases?}`；与 taxonomy 经 `name + category` 关联 |
| `schema/tag_taxonomy.schema.json` | JSON Schema Draft 2020-12（`node` 定义树，`instance` 定义实例元；`additionalProperties:false`） |

查看器 `tag_tree_explorer.html` 运行时 `Promise.all` 懒加载这两份文件，打开方式：
`python3 -m http.server 8000` 后访问 `http://localhost:8000/tag_tree_explorer.html`。

## 构建 / 富化脚本（活跃链路）

| 脚本 | 作用 |
|---|---|
| `build_unified.py` | 以 `data/taxonomy.json` 为结构权威源，重新产出两份文件；实例富描述/别名从现有 `instances_meta.json` 按 `(name, category)` 携带回写。`--write` 落盘 |
| `gen_full_enrich.py` | 补 `IP 分类标签` 分支节点的分类 KB（definition 等）。读/写 `taxonomy.json`。`--write` 落盘 |
| `gen_role_intros.py` | 生成虚构角色 IP 等实例的富描述（curated 精确 + templated 模板）；别名已并入 `instance.aliases`。读/写 `instances_meta.json`。`--write` 落盘 |

重生成顺序：`build_unified.py --write` → `gen_full_enrich.py --write` → `gen_role_intros.py --write`。
校验：`jsonschema` 对两份文件分别按 `node` / `instance` 校验（见 `schema` 内 `$defs`）。

## 消费脚本

| 脚本 | 作用 |
|---|---|
| `multimodal/cli.py` | 多模态图片采集；`--taxonomy data/instances_meta.json` 直读实例元实时派生 jobs（query 取 `instance.aliases` 首个英文名） |
| `relink_orphan_tags.py` | 孤儿 tag 双向同步（重挂/隔离），以 `instances_meta.json` 为当前体系准 |
| `retry_failed.py` | 重试失败候选下载，复用 `load_taxonomy` 任务配置 |

## 约定

- 所有"会改数据"的脚本默认只打印统计（预览），加 `--write` 才落盘。
- 两份文件是单一权威源；不要手工维护 `ip_instances.json` / `V2*.txt` / `tag_tree.json` 等旧格式（已删除，历史见 git 与 `archive/`）。

## 历史

V2 纯文本树 → `ip_instances.json` → `tag_tree.json` 的构建/清洗链路（含 `scripts/taxonomy/` 各轮 `exp_*.py`）已于 2026-08-15 全量收敛时移除，相关代码保留在 `archive/` 与 git 历史中。
