# IP 标签图片采集 · 产物说明

本目录（`/root/data/demiwtg`）下是「IP 标签体系叶子实例 → 每实例 4 张不同原图」采集流程的全部产物。

> ⚠️ 设计要点（与早期版本的关键区别）：**不再把 1 张图缩放成多档**。每个标签选 **4 张不同内容**的图，
> 全部**保持原始分辨率**（不改分辨率），再按原始宽度分桶（≈768 / ≈1024 / ≈2048 / 最大）过滤保留最合适的。

## 1. 目录与文件总览

| 路径 | 说明 |
|---|---|
| `images_full/` | **全部来源**（CC 授权 + 非 CC 未授权）实际图片（SHA-256 内容寻址：`images_full/<aa>/<sha256>.<ext>`），重复图只存一份。每张图在清单中带 `source`/`source_authorized` 字段可追溯来源。 |
| `images_by_tag/` | 按标签组织的软链树（含授权与未授权，见 §2）。 |
| `data/image_manifest.csv` | **全量**清单（标签/来源/尺寸/许可证），含 `source_authorized` 列，可按其切分纯 CC 子集。 |
| `data/ip_instances.json` | IP 标签体系的叶子实例源数据。 |
| `data/ip_query_aliases.json` | 实例的英文检索别名（含人工校正）。 |
| `data/image_collect_config.instances.json` | 由实例生成的采集 job 配置（全量）。 |
| `data/multimodal_pilot/` 等 | 流水线元数据 JSONL（candidates / success / rejected / failed / stats）。 |

## 2. 软链树结构（`images_by_tag/`）

按实例标签路径（按 ` / ` 切分）建立多级目录，每个叶子实例目录下按**选中的原始宽度档位**命名软链——
**每个文件都是一张不同的原图**（不再是同图多分辨率）：

```
images_by_tag/
└── 内容作品 IP/
    └── 动漫作品/
        └── 儿童动画/
            ├── 小猪佩奇/
            │   ├── r768.jpg    # 选中档位≈768px 的原图（不同内容）
            │   ├── r1024.jpg   # 选中档位≈1024px 的原图
            │   ├── r2048.jpg   # 选中档位≈2048px 的原图
            │   └── rmax.jpg    # 选中最大宽度原图
            └── 熊出没/
                ├── r768.jpg
                ├── r1024.jpg
                ├── r2048.jpg
                └── rmax.jpg
```

**要点：**
- 软链为**相对路径**，指向 `images_full/` 真实文件，整体迁移不断链。
- 每个标签最多 4 张**内容不同**的图；若合格候选不足 4 张（或原图宽度 < 768px 被丢弃），目录中文件数可能少于 4。
- 同标签同档位若同时有授权与未授权两张不同图，未授权那张追加短 hash 后缀避免互相覆盖；来源可追溯至清单 `source_authorized` 列。

## 3. 清单字段（`data/image_manifest.csv`，全量含授权与未授权）

| 列 | 含义 |
|---|---|
| `tag` | 完整标签路径 |
| `leaf` | 叶子实例名 |
| `query` | 实际使用的检索词 |
| `query_lang` | 检索词语言：`en` / `zh`（用于统计中文源占比） |
| `source` | 来源名（`wikimedia` / `wikimedia_zh` / `baidu` / `bing` / `so360` / `sogou` / `baidu_baike` / `douban`） |
| `source_kind` | 来源类型（目录 / 未授权来源 …） |
| `source_authorized` | 是否授权（CC）：`True` / `False` |
| `selected_tier` | 被选中的原始宽度档位：`768`/`1024`/`2048`/`0`(最大)；`None`=original |
| `tier_file` | 软链文件名（`r768.jpg`/`r1024.jpg`/`r2048.jpg`/`rmax.jpg`/`original.jpg`） |
| `width`/`height` | 实际像素尺寸（= 原始分辨率） |
| `orig_width`/`orig_height` | 原图宽度/高度（与 width/height 一致，保留便于核对） |
| `mime`/`size_bytes` | 实际 MIME 与字节数 |
| `license` | 许可证原始声明（CC 源有；未授权源为 `未知(未授权来源,非CC)`） |
| `author` | 作者 |
| `sha256` | 内容哈希 |
| `local_path` | 真实文件相对路径 |

## 4. 来源与授权分级

| 来源 | 语言 | 授权 | 说明 |
|---|---|---|---|
| `wikimedia` | en | ✅ CC | Wikimedia Commons（英文检索） |
| `wikimedia_zh` | zh | ✅ CC | Wikimedia Commons（中文检索，本环境可达，召回与英文相当） |
| `openverse` | both | ✅ CC | Openverse CC 聚合（Flickr/博物馆等）；**本沙箱被墙，网络放行后启用** |
| `baidu` | zh | ❌ 未授权 | 百度图片 |
| `bing` | zh | ❌ 未授权 | 必应图片 |
| `so360` | zh | ❌ 未授权 | 360 图片 |
| `sogou` | zh | ❌ 未授权 | 搜狗图片（本沙箱 403，网络放行后可用） |
| `baidu_baike` | zh | ❌ 未授权 | 百度百科图册 |
| `douban` | zh | ❌ 未授权 | 豆瓣（本沙箱难稳定抓取，best-effort） |

> **中文源扩种策略**：中英文**同时**检索（中文源用中文 query、英文源用英文 query）。CC 中文召回靠
> `wikimedia_zh`；更多中文源以**未授权爬虫**形式接入（百度/必应/360/搜狗/百度百科/豆瓣），与授权源
> **统一落盘、统一清单**，每张图保留 `source`/`license_raw=未知`/`source_authorized=False`，下游可按
> `source_authorized` 切分纯 CC 子集，或整体作为带 provenance 的图集使用。任何干净 CC 聚合源
> （Openverse/Flickr）在本沙箱被网络拦截。

## 5. 如何重新生成

```bash
# 1) 实例 → 英文别名（已生成，可重跑）
python3 scripts/build_aliases.py

# 2) 生成 job 配置（默认含全部授权+未授权源；--pilot N 取前 N 个实例试点）
python3 scripts/gen_jobs.py --alias data/ip_query_aliases.json \
    --out data/image_collect_config.instances.json

# 3) 采集（阶段一检索 + 阶段二筛选/选图/下载；授权与未授权统一落盘到 images_full）
python3 -m scripts.multimodal.cli --config data/image_collect_config.instances.json \
    --out data/multimodal_full --images-dir images_full

# 4) 仅检索（看候选/中文占比，不下载）
python3 -m scripts.multimodal.cli --config ... --out data/multimodal_pilot --metadata-only

# 5) 生成清单 CSV（全量，含授权与未授权，带 source_authorized 列）
python3 scripts/gen_manifest.py --runs data/multimodal_full

# 6) 按标签建软链树（单棵树，含授权与未授权）
python3 scripts/link_by_tag.py
```

## 6. 关键设计说明

- **4 张不同原图**：`selector.select_distinct` 按原始宽度档位（768/1024/2048/最大）贪心选最多 4 张**内容不同**的图；原图宽度 < 768px 直接丢弃（用户确认阈值）。下载器**只下载原图、不做任何缩放**，保留原始分辨率。
- **许可证**：CC 源仅采集 CC BY / CC BY-SA / CC0 / Public Domain；未授权源跳过许可证校验，但二者统一落盘、统一清单，每张图带 `source_authorized` 字段可追溯。
- **去重**：`images_full/` 按 SHA-256 内容寻址，跨标签/跨来源重复自动合并。
- **未授权源可追溯**：未授权源下载采用 https-only 校验（host 不可枚举），仍只存 Pillow 能解码的真实图片；其产物与 CC 源同池存储，靠 `source_authorized=False` 区分，下游可随时切分。
- **失败容错**：瞬时 SSL 错误指数退避重试；源站残缺 JPEG 以 `LOAD_TRUNCATED_IMAGES` 容错后重新编码存盘。
