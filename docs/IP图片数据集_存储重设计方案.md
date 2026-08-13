# IP 图片数据集 · 存储重设计方案

> 目标：把当前"多个顶层目录 + 散落 metadata"的混乱布局，收敛为单一、清晰、类数据湖（data-lake）的数据集根目录 `dataset/`。
> 本文档为**设计方案**，不含任何文件迁移或代码改动。后续若执行，按文末"迁移步骤"与"代码改动点"落地。

## 1. 当前问题

- 顶层图片目录过多：`images_full/`(6475) · `images_unauthorized/`(111) · `images_validate/`(31) · `images_pilot3/`(4) · `images_full_unauthorized/`(0) · `images_by_tag/`(6864 软链)。
- 元数据散落：唯一主清单 `data/image_manifest.csv`(7213 行) + 9 个 `data/multimodal_X/` 目录，各自带 `candidates/success/failed/rejected/stats.jsonl`，互相重叠、难辨"哪份是最终真相"。
- 内容寻址方案本身**没问题**（`images_full/<aa>/<sha256>.<ext>`，天然去重、不可变），问题在"目录用途不清 + metadata 无统一家"。

## 2. 设计原则

1. **三区分离（data-lake 思想）**：原始字节(blobs) / 元数据(meta) / 视图(by_tag) 各归其位。
2. **单一内容寻址库**：所有来源（CC 授权 + 未授权）统一进 `blobs/`，**不再按来源/批次分子目录**；来源可追溯性靠 manifest 的 `source/source_authorized` 字段，而非目录。
3. **单一真相清单**：`meta/images.jsonl` 是主 manifest（每张图一行）；每批运行的 JSONL 仅作为 `meta/runs/<run_id>/` 下的过程产物，不再散落在顶层 `data/`。
4. **tag 关系双承载**：保留可浏览的软链树 `by_tag/`，同时落地可移植的 `meta/tags.json` 索引（source of truth）。
5. **格式零依赖**：manifest 用 JSONL（stdlib 可读、易追加），不引入 parquet 等额外依赖。

## 3. 目标布局

```
dataset/                              # 数据集根目录（替代 images_full / images_by_tag / data/multimodal_X）
├── blobs/                            # [RAW ZONE] 原始图片字节，内容寻址、不可变、全源统一
│   └── <aa>/<sha256>.<ext>          #   例：blobs/e7/e7ee1e65…7277230.webp
│
├── meta/                             # [META ZONE] 所有元数据
│   ├── images.jsonl                  #   主清单：每张已存图一行（见 §4）
│   ├── tags.json                     #   tag↔图 关系索引（见 §5）
│   ├── runs/                        #   各批次流水线过程产物（替代 data/multimodal_X/）
│   │   ├── <run_id>/                #     例：20260812_verify_cn2/
│   │   │   ├── candidates.jsonl
│   │   │   ├── downloads_success.jsonl
│   │   │   ├── downloads_failed.jsonl
│   │   │   ├── candidates_rejected.jsonl
│   │   │   └── stats.jsonl
│   │   └── _latest -> <run_id>      #   指向当前最新批次（symlink）
│   └── schema.md                     #   字段说明（images.jsonl / tags.json）
│
├── by_tag/                           # [VIEW ZONE] 按标签组织的软链树（浏览用，由 tags.json 派生）
│   └── 内容作品 IP/动漫作品/儿童动画/小猪佩奇/
│       ├── r768.jpg  ->  ../../../blobs/e7/e7ee1e65…7277230.webp
│       ├── r1024.jpg ->  ...
│       ├── r2048.jpg ->  ...
│       └── rmax.jpg  ->  ...
│
└── README.md                         # 数据集卡片（来源、布局、用法、许可边界）
```

## 4. 主清单 schema —— `meta/images.jsonl`

每行一个**已落盘 blob**（按 sha256 去重；同一图命中多个 tag 时，`tags` 为数组）。

| 字段 | 类型 | 说明 |
|---|---|---|
| `sha256` | str | 内容哈希（文件名主体，主键） |
| `ext` | str | 真实扩展名（由 magic bytes 推断：jpg/png/webp/gif/tiff/bmp） |
| `source` | str | 来源：`wikimedia`/`wikimedia_zh`/`baidu`/`toutiao`/`bing`/`so360`/`huaban_api`/`duitang`… |
| `source_kind` | str | 来源类型：目录/数据集/领域社区/搜索引擎/未授权来源 |
| `source_authorized` | bool | 是否 CC 授权（`False`=未授权，需法律隔离） |
| `license` | str | 许可证原始声明（CC 源）或空（未授权源） |
| `author` | str? | 来源署名 |
| `credit` | str? | 署名信息 |
| `width` / `height` | int | 实际像素（=原始分辨率） |
| `orig_width` / `orig_height` | int? | 来源声明原始尺寸（如有） |
| `size_bytes` | int | 文件字节数 |
| `mime` | str | 实际 MIME |
| `tags` | list[str] | 命中的全部标签路径（同一图可属多标签） |
| `tiers` | list[int?] | 该图被选中服务过的原始宽度档位（768/1024/2048/0=最大） |
| `landing_url` | str? | 来源落地页 |
| `fetched_at` | float | 采集时间戳 |
| `path` | str | 相对 `dataset/` 的物理路径（`blobs/<aa>/<sha256>.<ext>`） |

> 与现有 `data/image_manifest.csv` 字段基本对齐，差异：拆出 `ext`/`path`、合并 `tags` 数组、补 `tiers`。可由现有 CSV + 各 `multimodal_X/downloads_success.jsonl` 聚合重建。

## 5. tag 关系索引 —— `meta/tags.json`

可移植的"关系真相"，`by_tag/` 软链树由它生成。

```json
{
  "内容作品 IP / 动漫作品 / 儿童动画 / 小猪佩奇": [
    {"tier": 768,  "sha256": "e7ee1e65…", "ext": "webp", "source": "baidu"},
    {"tier": 1024, "sha256": "a2906eb0…", "ext": "webp", "source": "baidu"},
    {"tier": 2048, "sha256": "0c0e53d8…", "ext": "webp", "source": "baidu"},
    {"tier": 0,    "sha256": "9741c1e8…", "ext": "png",  "source": "toutiao"}
  ],
  "… / 哆啦A梦": [ … ]
}
```

- key = 完整标签路径（按 ` / ` 切分即多级目录）。
- 同标签同档位若同时有授权/未授权两张不同图，均列出；生成软链时未授权那张追加短 hash 后缀避免覆盖（沿用 `link_by_tag.py` 现有逻辑）。
- `by_tag/` 软链为**相对路径**，指向 `blobs/`；整树迁移不断链。

## 6. 当前 → 新布局 映射

| 现路径 | 去向 |
|---|---|
| `images_full/<aa>/<sha256>.<ext>` | `dataset/blobs/<aa>/<sha256>.<ext>`（直接搬，结构不变） |
| `images_unauthorized/` · `images_validate/` · `images_pilot3/` · `images_full_unauthorized/` | 内容并入 `dataset/blobs/`（按 sha256 去重）；空/遗留目录弃用 |
| `images_by_tag/` | 由 `meta/tags.json` 重新生成的 `dataset/by_tag/` 替代 |
| `data/image_manifest.csv` | 聚合为 `dataset/meta/images.jsonl`（并补 `ext/path/tags/tiers`） |
| `data/multimodal_X/`(×9) | 过程产物归入 `dataset/meta/runs/<run_id>/`；`data/` 其余（ip_instances / config 等）保留在原位 |
| `scripts/link_by_tag.py` | 改造为读 `meta/tags.json` → 写 `dataset/by_tag/` |

## 7. 若后续执行：迁移步骤（本文档不含）

1. `mkdir -p dataset/{blobs,meta/runs,by_tag}`。
2. 搬 blob：`mv images_full/* dataset/blobs/`，再把 `images_unauthorized/validate/pilot3` 中**不重复**的文件并入 `dataset/blobs/`（按 sha256 文件名去重）。
3. 重建主清单：聚合 `image_manifest.csv` + 各 `downloads_success.jsonl` → 去重 → `dataset/meta/images.jsonl`（补 ext/path 由文件名解析，tags/tiers 由 runs 反查）。
4. 生成 `dataset/meta/tags.json`：从 images.jsonl 按 `tags` 聚合。
5. `python3 scripts/link_by_tag.py --manifest dataset/meta/tags.json --out dataset/by_tag`（改造后）。
6. 归档旧目录：`mv images_full images_unauthorized images_validate images_pilot3 images_full_unauthorized images_by_tag /archive/` 或删除；`mv data/multimodal_X dataset/meta/runs/<run_id>`。

## 8. 若后续执行：代码改动点

- `scripts/multimodal/cli.py`：`--out` 默认 `data/multimodal` → `dataset/meta/runs/<run_id>`；`--images-dir` 默认 `images` → `dataset/blobs`。新增 `--run-id`（默认时间戳）。
- `scripts/multimodal/pipeline.py`：阶段二末尾额外写 `dataset/meta/images.jsonl`（增量 upsert by sha256）与更新 `_latest`；维持现有 `downloads_*.jsonl` 写入 `runs/<run_id>/`。
- `scripts/multimodal/downloader.py`：无需改目录逻辑（仍写 `images_dir/<aa>/<sha256>.<ext>`），但建议补 **magic-byte 嗅探**（当前漏 GIF，导致 `.bin`）以正确定 ext。
- `scripts/link_by_tag.py`：输入由 CSV 改为 `meta/tags.json`，输出改为 `dataset/by_tag`。
- `scripts/gen_jobs.py`：引用 `DEFAULTS["unauthorized_sources"]` 不变；仅路径约定随之调整。

## 9. 开放事项 / 备注

- **`.bin` 尾巴**：downloader 的 `_mime_to_ext` 对 GIF 等未识别格式回退 `.bin`；重设计时应加 magic-byte 嗅探（PNG/JPEG/WEBP/TIFF/GIF/BMP）。历史 `.bin` 已按真实格式重命名（见上轮对话）。
- **symlink 脆弱性**：`by_tag/` 仅为便利视图；关系真相在 `tags.json`，故即便软链断链也不丢数据。
- **Parquet 可选**：若未来图量很大、需列式过滤，可把 `images.jsonl` 额外转一份 `images.parquet`（需 pyarrow），但非必需。
- **许可边界**：未授权源（baidu/toutiao/bing/so360/…）仅作技术验证，商用前需法律评估；`source_authorized` 字段即为此切分而留。
