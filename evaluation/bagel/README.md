# BAGEL 模型接入与按需官方回归

模型接入遵守 [项目 Pipeline 强制规范](../../PIPELINE_SPEC.md)；本目录维护模型加载适配器，不另建业务评测入口。

`adapter.py` 由工作区 `models/serve_z_image.py --backend bagel` 调用，向标准图片算子提供本地 JSON Images API。当前 T2I V2 评测通过 `map_image_async` 和平台 `ManagedHTTPService` 接入；题目、答题模板、A/B 判分、缓存、业务表和 notebook 均由 [T2I V2](../t2i/v2/README.md) 维护。不得直接从业务行函数调用 `load_model` 或 inferencer。

服务使用已有 `env-bagel` 和 `models/BAGEL-7B-MoT`。文本与有序参考图通过官方 `interleave_inference([完整模板文本, *实际参考图])` 传递，不使用会颠倒图文顺序的单图 convenience 接口。固定1024×1024、50步、text CFG=4、image CFG=1.5、think=False；输入图保持各自比例，最终输出尺寸不随最后一张参考图变化。请求锁同时保护随机种子和模型推理；并发HTTP请求不表示同一GPU并行去噪。权重进入显卡前要求至少42GiB空闲显存，模型配置与部署代码绑定显式 revision。完整请求由原生算子调用前保存，服务不拼接提示词。

`--check-runtime` 只检查权重文件和可导入依赖；通过不等于真实生成成功。`gen/`、`vlm/` 及必要官方依赖保留为按需回归工具，不另列研究主线。

历史结果归档：[results_review.ipynb](archive/results_review.ipynb)。官方题库、原始输入/输出和结果已保存在固定 Lance 证据中，源码树不再保留 data/。权重和环境未移动。

第三方官方工具要求文件输入时，显式导出可丢弃的交换目录：

```bash
python -m evaluation.bagel.materialize_inputs /tmp/bagel-regression-inputs
export BAGEL_EVAL_WORKDIR=/tmp/bagel-regression-inputs
```

此导出只含保留的 prompts 与 DPG 题库，不调用模型。之后可按需运行 gen/ 或 vlm/ 工具；缓存与临时输出都归显式交换目录。导出不是数据真源，新增有价值结果需要另行固定入湖。官方环境及模型依赖沿用现有配置，未在此次清理中安装或实跑。

历史缺项只记录，不默认补跑。分布式 DPG 评分代码归 gen/dpg_bench/。
