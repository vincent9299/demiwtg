# taxonomy-instances · 融合世界标签体系（数据模型 + 工具链）

子项目，承载「标签树（taxonomy）+ 实例元（instances）」的统一数据模型及其构建 / 富化 / 查看 / 消费工具链。
原仓库 `demiwtg` 的总览见根目录 `README.md`；本目录是其中「标签体系数据」这一组件的独立归集。

## 目录结构

```
taxonomy-instances/
├── data/
│   ├── taxonomy.json          # 标签树：节点结构 + KB 字段 + instances 实例名列表（结构指针）
│   ├── instances_meta.json    # 实例元：扁平 instances[]，{name,category,source,intro?,definition?,desc?,aliases?}
│   ├── taxonomy.js            # 生成产物（viewer 无服务模式 sidecar，勿手改、勿提交）
│   └── instances_meta.js      # 生成产物
├── schema/
│   └── tag_taxonomy.schema.json   # JSON Schema Draft 2020-12（node 定义树 / instance 定义实例元）
├── scripts/
│   ├── build_unified.py       # 以 taxonomy.json 为结构权威源，重产出两份文件
│   ├── gen_full_enrich.py     # 补 IP 分支节点分类 KB
│   ├── gen_role_intros.py     # 生成虚构角色 IP 等实例富描述
│   ├── build_viewer.py        # 生成 viewer 的 sidecar / 单文件 standalone（无需 HTTP 服务）
│   ├── relink_orphan_tags.py / retry_failed.py / migrate_to_dataset.py / link_by_tag.py / gen_manifest.py
│   └── multimodal/            # 多模态图片采集系统（消费 instances_meta 做打标）
├── tag_tree_explorer.html         # 查看器（运行时懒加载两份 JSON）
└── tag_tree_explorer.standalone.html  # 生成产物：内联数据单文件，双击即用
```

## 打开查看器（无需服务器）

```bash
python3 scripts/build_viewer.py                 # 生成 data/*.js sidecar，双击 tag_tree_explorer.html 即用
python3 scripts/build_viewer.py --standalone    # 或生成单文件 tag_tree_explorer.standalone.html（最便携）
```

也可走 HTTP：`python3 -m http.server 8000` 后访问 `http://localhost:8000/taxonomy-instances/tag_tree_explorer.html`。

## 重新生成数据

顺序：`build_unified.py --write` → `gen_full_enrich.py --write` → `gen_role_intros.py --write`。
详见 `scripts/README.md`。

> 所有脚本以「自身文件两级父目录」为 `ROOT` 定位 `data/`、`schema/`，因此本子项目可整体移动而不破坏路径。
> `data/*.js`、`tag_tree_explorer.standalone.html` 为生成产物，已被根 `.gitignore` 忽略，数据改动后需重跑 `build_viewer.py`。
