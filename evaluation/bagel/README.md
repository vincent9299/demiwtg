# BAGEL 模型接入与按需官方回归

`adapter.py` 供当前 evaluation pipeline 使用。`gen/`、`vlm/` 及必要官方依赖保留为按需回归工具，不另列研究主线。

历史结果归档：[results_review.ipynb](archive/results_review.ipynb)。官方题库、原始输入/输出和结果已保存在固定 Lance 证据中，源码树不再保留 data/。权重和环境未移动。

第三方官方工具要求文件输入时，显式导出可丢弃的交换目录：

```bash
python -m evaluation.bagel.materialize_inputs /tmp/bagel-regression-inputs
export BAGEL_EVAL_WORKDIR=/tmp/bagel-regression-inputs
```

此导出只含保留的 prompts 与 DPG 题库，不调用模型。之后可按需运行 gen/ 或 vlm/ 工具；缓存与临时输出都归显式交换目录。导出不是数据真源，新增有价值结果需要另行固定入湖。官方环境及模型依赖沿用现有配置，未在此次清理中安装或实跑。

历史缺项只记录，不默认补跑。分布式 DPG 评分代码归 gen/dpg_bench/。
