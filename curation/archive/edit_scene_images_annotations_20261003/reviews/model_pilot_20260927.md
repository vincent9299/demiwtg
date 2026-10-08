# 原图准备小样本验证 · 2026-09-27

后续规则更新：当前 pipeline 已增加原图短边严格大于 1024 的检查；概念范围采用可配置 taxonomy 子树剪枝，不按概念名关键词过滤（见[剪枝统计](taxonomy_scope_20260927.md)）。下文保留的是增加尺寸门槛前的模型选型记录，不能把其中 6 张历史候选理解为已通过新门槛。用当前尺寸算子只读检查 `pool__pilot_qwen4b_20260927.lance@2`：仅书桌图（1080×1438）通过，其余 5 张均被尺寸条件排除；历史表未回写。

来源固定为 `datasets/master_concepts.lance@2` 与 `demiwtg/collect/datasets/images.lance@5`。未改写主表或 preparation，也未恢复全库标注。

校准概念：餐桌、书桌、茶几、展台、客厅、厨房、庭院、花园、茶壶、可口可乐。前七项应进入看图阶段；花园的别名包含乳制品品牌含义，应待定；最后两项不是这轮共用场景来源。每概念最多一张，按 SHA 排序；图片不重复调用。

这些样本用于发现问题并修订提示词，部分边界示例已写进 prompt，不能当作独立测试集准确率。

## 文本模型观察

| 本地模型 | Prompt 版本 | 结果及问题 |
| --- | --- | --- |
| Qwen3-4B-Instruct-2507（默认） | concept-4 | 7 keep、1 hold、2 reject，符合校准判定。concept-3 曾把庭院错挂的动物分类当成多义，因此修订为优先依据名称和别名，异常分类不单独构成 hold 理由。 |
| Qwen2.5-0.5B-Instruct | concept-3 | 10 条结构化响应均成功，但错误保留可口可乐，对茶几、庭院无依据地 hold；理由也有明显概念混淆。不作为默认粗筛模型。 |
| Qwen3.8-27B | concept-2 | 10 条响应成功，但错误排除茶几；concept-3 因此补充了有可见承载面的家具应保留。此记录不能代表修订后的效果。 |
| Gemma-4-26B-A4B-it | concept-3 | 7 keep、1 hold、2 reject，符合上述校准判定；可作为文本模型备选。 |

早期 Gemma 使用 concept-1、`json_object` 时有 2 条 schema/截断失败。当前本地请求使用 YAML 中的 `json_schema` 约束，修订后的 Gemma 与 0.5B 均无这类失败。结构化约束只能保证字段形式，不能消除语义误判。

另用 10 个补充概念检查 concept-4：卧室、公园、街道、咖啡桌、边桌、地板、埃菲尔铁塔 keep；牛奶、冰箱 reject；斑马因别名同时指动物和文具品牌而 hold。冰箱在 concept-3/4 间从 keep 变为 reject，说明单品电器的召回边界仍需后续样本评估，不能宣称粗筛没有漏召回。这组没有据结果继续修改 prompt，但其中部分类型已在规则中举例，也不是完整独立评测。

4B 权重通过公司代理下载到 `/yzp/zhaozy/yangzepeng/0905/models/Qwen3-4B-Instruct-2507`；三片 safetensors 的 398 个 tensor 与索引一致。服务使用 GPU 1、8192 上下文、16 个最大序列、eager 模式、0.2 显存配额；试跑文本并发 4、图片并发 2。现有 Qwen 27B 服务保持原配置。临时 0.5B/Gemma 服务已关闭，4B 的 8002 端口保留供 pipeline 使用。

本机 4B / concept-4 的单请求耗时：校准组中位数 1.01 秒（0.79–1.16 秒），补充组中位数 0.87 秒（0.66–1.19 秒），两组各 10 次新请求，没有响应复用。该数字包含本地 API 请求耗时，不包含整个 pipeline 的读写阶段。

## 图片模型观察

Gemma 的 image-2 试跑共 7 张，6 keep、1 reject，全部完成。它正确排除了来源概念为“茶几”、实际为 CHAGEE 饮料杯的图片（SHA 前缀 `1ab2927c5d26`）。

对餐桌、书桌、庭院三张原图检查后，仍发现细节误差：Gemma 将餐桌周围六把椅子写成四把，庭院视角混用“俯视/全景/近景”，书桌图的床下抽屉空间描述不够严格。Qwen 早期 image-2 记录在椅子数和庭院视角上更贴近画面，但也有物品与区域细节误差。因此暂保留 Qwen3.8-27B 为视觉默认，Gemma 可配置替换，不把两者的标签当作人工审核结果。

检索提示用于召回，具体对象是否已存在、承载面是否够用、编辑是否能考察概念核心内容，都需要下游结合实际像素再判断。

最终默认组合为 4B / concept-4 + Qwen 27B / image-2：10 个概念全部响应成功，7 个进入取图阶段，7 张图全部完成，6 keep、1 reject；Qwen 同样排除了错绑饮料杯。结果为小样本机器候选，未提升审核状态。最新运行共约 32 秒，其中部分图片请求复用了相同输入的原生响应，该时长不代表全部新调用的吞吐量。

## 可追溯记录

表均在 `benchmark/edit/source_images/datasets/`。每个结果行的 `call_json` 指向原生调用请求和响应，含耗时及 usage；表版本固定可回看。

- `concept_results__pilot_qwen05_20260927.lance@1`：0.5B / concept-3。
- `concept_results__pilot_qwen27_20260927.lance@1`、`image_results__pilot_qwen27_20260927.lance@1`：27B / concept-2 + image-2，6 张图。
- `concept_results__pilot_gemma_v3_20260927.lance@1`、`image_results__pilot_gemma_v3_20260927.lance@1`：Gemma / concept-3 + image-2，7 张图。
- `pool__pilot_gemma_v3_20260927.lance@1`：6 张机器筛选候选，状态 `unreviewed`。
- `concept_results__pilot_qwen4b_20260927.lance@2`、`image_results__pilot_qwen4b_20260927.lance@2`：默认模型组合，concept-4 + image-2。相同表的 @1 保留 concept-3 的早期结果。
- `pool__pilot_qwen4b_20260927.lance@2`：最终默认组合的 6 张机器候选。
- `concept_results__pilot_qwen4b_expansion_20260927.lance@2`：10 个补充概念，只跑文本阶段。

本轮不是严格性能跑分：模型启动方式、缓存、并发、响应长度和选中图片有差异，不能仅以总 wall time 判断模型优劣。
