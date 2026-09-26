# EDIT Benchmark V2

每个概念一次模型调用：从本次交付的图片中选择一张原图，围绕概念核心内容出一道完整编辑题。唯一 prompt 是 `prompts/tasks.yaml` 的 `design_question`；选图和出题一起完成。没有合适原图或有效题目时返回 `question: null` 和具体原因，不勉强出题。没有题数参数。

流程：读取固定版本材料 → 按概念关联完整图文并编号 → 一次选图/出题 → 检查单题及原图编号 → 写待审题表。每题可以包含多个紧密相关的考点；当前不调用独立审题、模型作答或评分，不将输出视为已经通过审核的正式基准。

选题先确定概念核心内容，再用增、删、改及其必要组合表达。替换、调整、动作变化、风格修改、背景修改等均为“改”的具体方式，不另设并列的选题类别。组合仅用于实现同一核心结构、关系或规则所必需的联合变化，不拼接独立考点或凑操作数；包括修改风格在内，每项变化都须直接支撑核心目标。仅改风格或背景时遵守题面明确的内容保持范围。当前不设计以抠图、分割或白底提取为主要目标的题目：这些任务通常侧重定位、分割及边缘处理，对概念核心内容的考察有限。输出仍为单题，不新增编辑类型字段。

输入通过 `knowledge_runs` / `visual_runs` 指定材料表的 `uri/version`，CLI 用 `--sources` JSON。已交付的文章配图与独立视觉材料共同作为选图范围，保留审核范围、引用及来源。不存在出题后的本地搜图、外部补搜或再次定稿；缺图时记录 `needs_source_images`，交付图片均不适合时由模型返回不足。来源准备可另行补充后，用新运行名重新出题。

正文、输出字段契约与模型配置同处维护在 `prompts/tasks.yaml`。业务输出为：

```json
{
  "result": {
    "question": {
      "source_image": 1,
      "instruction": "完整编辑题面，含必要条件、锚点、展示与保持范围",
      "test_points": [{"point": "考察内容", "basis": "事实与适用条件及依据"}]
    }
  }
}
```

无题时为 `{"result": {"question": null, "reason": "具体不足"}}`。`question` 是单个对象，不接受列表或旧的 `candidates` 协议。空题、字段完整性、非空文本及原图编号范围由业务响应算子检查；字段结构复用 YAML 中的同一份定义，不修改 demiflow。`source_image` 对应实际发送图片的 1 起始序号，材料另有编号。程序据此绑定固定原图资产，模型不填写路径、Blob 或哈希。

模型输入每概念一行，调用并发为 1。默认上下文预算为正文及文字载荷合计 60,000 字符、图片 16 张；超限留下 `needs_context_budget`，不截断或自动拆批。`max_calls` 限制唯一出题节点的实际新请求，`max_units` 控制概念范围，不是每次返回的题数。`config('offline')` 只保存待响应请求；`local` 使用配置的本地 Qwen。

正式入口 `edit_v2_benchmark_pipeline.py` 用标准 `DataAPI` 和 Dataset API 表达文章/图片读取、关联、筛选、聚合、模型调用与写表。模型链为 `read_lance → map(prepare_design) → map_prompt_async → map(apply_design) → materialize → write_lance`。复杂单行材料转换在 `operaters/transforms.py`，请求及响应检查在 `operaters/prompting.py`，运行冻结在 `operaters/authoring.py`；算子不另起 Dataset 或调度模型。

表直接平铺在本目录 `datasets/`，阶段表后缀包含运行名及指纹：

| 阶段 | 每行内容 |
| --- | --- |
| `knowledge` | 一个概念的固定图文材料及交付问题 |
| `design` | 一个概念的单题响应、选图绑定，或空题/失败原因及调用引用 |
| `candidates` | 一道待审题：题面、考点、原图、原材料与 `unreviewed` 状态 |

`target_uri` 指定最终候选题表；默认写本运行的 candidates 表。`write_mode='overwrite'` 默认覆盖目标表，`append` 追加本次结果。两者随运行冻结，已提交的阶段续跑复用固定版本，不重复调用或追加；模型响应 pending 时可通过原生 `submit_response()` 补交后续跑。目标 writer 成功后才登记提交版本。设计表已保存全部失败，不再额外落重复的 ready/incomplete 表。

[调试入口](edit_v2_benchmark_debug.ipynb) 直接填写源、目标、运行名，按实际表路径及版本查看题目和所选原图。CLI 与 notebook 调用同一正式入口：

```bash
python -m benchmark.edit.v2.edit_v2_benchmark_pipeline --help
```

本协议替代旧的候选意图、选图、定稿、criteria 和独立审题链。请使用新运行名和新目标表；历史 V1、旧 V2 表及 notebook 已保存输出不改写。开发验证使用隔离数据和模拟响应，不运行正式构题或模型生成。
