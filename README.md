# demiwtg

标签体系（taxonomy + instances）治理与 IP 图片数据湖项目。

- 架构约束、数据契约、dataset 硬约束：见 **[AGENTS.md](AGENTS.md)**（唯一权威文档）。
- 代码模块：`taxonomy/`（体系构建富化）、`curation/`（数据策展与检索接地）、`viewer/`（查看器：页面 + 构建脚本 + 产物闭环）、`benchmark/`（评测基准：vlm/t2i/edit 三子模块）；图片采集链已独立为 [demiwtg-data](https://github.com/vincent9299/demiwtg-data) 仓库；本地子项目 `bagel/`、`modelhub/` 为独立 git 仓库（主仓 .gitignore 整体排除）。
- 数据：`datasets/`（数据集根；demiwtg = 自建数据集：meta/ 下 taxonomy 三件套入 git，blobs 与清单不入 git）。
