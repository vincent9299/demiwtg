# demiwtg

标签体系（taxonomy + instances）治理与 IP 图片数据湖项目。

- 架构约束、数据契约、dataset 硬约束：见 **[AGENTS.md](AGENTS.md)**（唯一权威文档）。
- 代码模块：`taxonomy/`（体系构建富化）、`collect/`（图片采集）、`curation/`（数据策展：质量过滤/重试）、`viewer/`（查看器：页面 + 构建脚本 + 产物闭环）。
- 数据：`data/taxonomy/`（标签体系纯数据）、`data/dataset/`（图片数据湖，不入 git）。
