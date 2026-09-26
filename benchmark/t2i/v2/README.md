# T2I Benchmark V2

`run_pipeline(config)` 从 preparation 读取独立文字和图片，每概念请求一次，返回一道待审题或不足原因。清洗和审核由 preparation 完成；T2I 不重复解析审核 JSON、来源、引用或文章配图，也不对同概念多篇文章去重/判冲突。

```python
CONFIG = config(
    run=RUN_DIR, concepts=CONCEPTS,
    article_source={"uri": "demiwtg/preparation/datasets/articles.lance", "version": ARTICLE_VERSION},
    visual_source={"uri": "demiwtg/preparation/datasets/images.lance", "version": IMAGE_VERSION},
    target_uri=OUTPUT_TABLE_URI, write_mode='overwrite',
    mode='modelhub', model=MODEL,
    concurrency=4, queue_depth=1, temperature=0,
)
state = run_pipeline(CONFIG)
```

来源可分别为 `None`，也可均为空。没有可用材料时仍然请求模型，prompt 明确告知没有提供参考材料；不生成 `invalid_materials` 或缺失概念状态。下游信任 preparation 的公开可用状态；存量表必须经过新版 preparation 导出才具有新的清洗保证，旧固定版本不会自动改变。

材料数据流直接使用 `from demiflow import data`：

- 文章 `read_lance(review_status='reviewed') → 展开正文 → 按 concept 聚合`。只读 concept/content；不读 citations、illustrations 或 context_json。多篇文章、重复正文均按上游交付内容保留，暂不增加取舍策略。
- 图片 `read_lance(published_concepts) → 展开 published 且 keep 的概念关系 → 按 concept 聚合并限制数量`。按 preparation 的 source_refs 读取存储引用，不指定原始表，不匹配文章中的 image_id，不解析 review_json/observation_json。
- 配置概念列表分别 left join 文字和图片，空分支为零材料；文字在前、图片在后从 1 编号，写 inputs 表。
- `read_lance → map(prepare_request) → map_prompt_async → map(check_response) → materialize → write_lance` 执行出题和响应校验，再投影有效单题写目标表。

`max_reference_images` 默认 8。按数据流顺序取前 N 张可用图片，不承诺排序；当前没有文章配图关系，不推导“必需图”。以后有明确的图文联合输入契约时再扩展优先策略。材料编号与实际图片顺序一起保存，开卷时可原样提供，闭卷可省略。

`max_context_chars` 默认 60,000，计算提示词及文字载荷的字符数。超限记录 `needs_context_budget` 并跳过，不截断正文。预算检查通过后才读取图片：BlobRef 校验 SHA，pixels 解码并识别 MIME，然后编码为模型输入；每次执行每张选中图片只读一次。引用缺失、SHA 错误、解码失败直接抛错。

`concurrency` 默认 1，控制同时执行的概念请求数；`queue_depth` 默认 1（None 采用平台的 concurrency）；`temperature` 默认 0。`max_calls` 默认概念数，限制该模型节点的新请求。并发不会把多个概念拼成一个请求。默认模式 offline；local/modelhub 才发 HTTP 请求。模型节点直接配置 pack、options、max_requests。

```bash
python -m benchmark.t2i.v2.t2i_v2_benchmark_pipeline --run example \
  --concept 莜面栲栳栳 \
  --article-table demiwtg/preparation/datasets/articles.lance --article-version 4 \
  --visual-table demiwtg/preparation/datasets/images.lance --visual-version 5 \
  --concurrency 4 --queue-depth 1 --temperature 0 --mode offline
```

上例版本仅示意，实际使用新版 preparation 导出的版本。可省略两组来源参数进行无参考出题。每组 table/version 必须同时配置。

模型返回 `question: {instruction, test_points[{point,basis}]}`；无法出题时返回 `question: null` 和非空 reason。材料编号用于说明考点依据，不携带上游来源引用或逐项评分清单；没有单独审题、打题或评测。

表平铺在本模块 datasets/：inputs 保存本次材料，designs 保存每概念结果及调用引用，candidates 每题一行（unreviewed），calls 保存原生请求/响应，records 保存结果状态及 append 提交记录。同名执行使用当前参数重算输入与设计表，不冻结配置或源码。相同模型请求可以复用原生日志；overwrite 每次覆盖目标，append 对同运行同目标同内容避免重复追加。候选写入成功后才登记结果状态。

Notebook 统一配置 CONFIG，使用 `await asyncio.to_thread(run_pipeline, CONFIG)`。沿用单格入口，在运行后只读展示题面、考点与独立图文材料；空材料显示无参考说明。历史输出保留，正式模型仅在手动执行时调用。

设计表 `designs__<run>.lance` 和最终候选表（config.target_uri）均保存 nullable `reasoning` 列，直接读取即可调试；Notebook 折叠展示该列。内容来自服务响应的 `choices[0].message.reasoning_content`（或 `reasoning`），通过模型节点的调用元信息沿当前行传到 writer，不在业务算子里回查日志。失败/截断响应中已有的 reasoning 保留在设计表；未返回时存 null。`reason` 是业务不足/错误原因，与 `reasoning` 不同；reasoning 不纳入模型题目 schema、题目 ID 或后续 prompt。

完整原始 HTTP 响应仍保存在 `calls__<run>.lance`。设计表 `call_json.response_ref` 定位固定版本的响应，使用 `RecordRef.from_dict(ref).read(DATA_ROOT)` 读取。Notebook 同时展示耗时、token 用量、finish_reason，并提供完整响应的折叠预览；call_json 本身不重复保存 reasoning 正文。旧固定版本不会新增列，新执行写出的表才有此字段；旧调用日志若包含 reasoning，复用响应时可自动提取并写入新输出。向旧 schema 表 append 前需先统一 schema 或使用新目标表。
