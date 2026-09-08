# demiwtg

标签体系（taxonomy + concepts）治理与 IP 图片数据湖项目。

- 架构约束、数据契约、dataset 硬约束：见 **[AGENTS.md](AGENTS.md)**（唯一权威文档）。
- 代码模块：`taxonomy/`（体系构建富化）、`curation/`（数据策展与检索接地）、`viewer/`（查看器：页面 + 构建脚本 + 产物闭环）、`benchmark/`（评测基准：vlm/t2i/edit 三子模块 + bagel 第 4 场景官方基准评测）；图片采集链已独立为 [demiwtg-data](https://github.com/vincent9299/demiwtg-data) 仓库；`bagel/`（Bagel 官方模型包）已于 2026-09-05 入主仓（模型权重等重物 gitignore 排除）；本地子项目 `modelhub/` 为独立 git 仓库（主仓 .gitignore 整体排除）。
- 数据：`datasets/`（数据集根；demiwtg = 自建数据集：meta/ 下 taxonomy 两件套（taxonomy.json/concepts.json）入 git，blobs 与清单不入 git）。
