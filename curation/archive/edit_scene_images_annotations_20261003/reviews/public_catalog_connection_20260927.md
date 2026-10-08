# 全量公共目录接入验证 · 2026-09-27

原图 notebook 已固定读取 `datasets/master_concepts.lance@2` 和 `datasets/images.lance@9`。公共图片表共 2,164,671 行，dimension_status 无空值；图片字节沿 source_refs 绑定 `demiwtg/collect/datasets/images.lance@5`，不从公共表版本推导 Blob 版本。

候选模式只读公共基础列，不展开历史描述/审核；新增未标注行同样参与 concept 关联。已交付宽高直接参与短边严格大于 1024 的过滤；已知 read_error 留下技术失败和原因，避免重读。未交付技术状态的旧行仍保留历史尺寸投影及缺尺寸补读。尺寸过滤发生在每概念及全局图片限量之前。

只读统计：全库有 **430,517 张**满足 availability=available、byte_size>0、dimension_status=ready、宽高均 >1024，且未明确标记 generated/synthetic/ai_generated。这是技术条件候选数，不代表图片语义匹配、视觉模型 keep 或人工审核通过。

| 概念 | 公共目录关联图 | 满足上述技术条件 |
| --- | ---: | ---: |
| 餐桌 | 10 | 3 |
| 书桌 | 12 | 5 |
| 茶几 | 10 | 3 |
| 客厅 | 10 | 3 |
| 厨房 | 224 | 60 |
| 庭院 | 9 | 4 |

验证使用临时隔离根，固定真实公共来源，文本响应仅为三概念 keep 的模拟连通性输入。实际执行正式入口至 image_inputs：餐桌、书桌、厨房共 223 张图进入尺寸检查，155 张 filtered_resolution、68 张 ready；每概念最多 2 张、全局最多 6 张，最终交付 6 张，complete=True。所有候选均复用 preparation 尺寸，引用均为 collect@5。隔离运行表已随临时目录清除；无新模型请求、无公共表写入、无生产池生成。

抽查实际输出 SHA `101e7d992f7888a368913868f18d047aaaf199e27f39764ff261901884c02acb`：按固定 Blob 读取 593,879 字节，SHA 一致，Pillow 文件验证通过，实测 1920×1439 与公共目录相符。

测试：原图 pipeline 29 项通过，原图模块布局检查 1 项通过。覆盖旧尺寸投影、缺尺寸补读、严格阈值、按 SHA 合并、未标注全量行、已知读图错误、固定 Blob 版本、离线重放与技术失败/业务过滤分离。

手工配置：运行名 `scene_pool_images_v9`，taxonomy 范围和预算保持在 notebook；最多 200 个概念、每概念 2 图、总计 16 图，local 模式，文本 8002 / 视觉 8000。本次未执行该生产批次。

运行准备：检查时 GPU 1 空闲，已用本地已有权重恢复 Qwen3-4B-Instruct-2507（8002，GPU 1，显存配额 0.2），`/v1/models` 返回正确模型名，`/health` 返回 200。现有 Qwen3.8-27B（8000）模型列表正常，服务未调整。启动日志为 `qwen4b_service_20260927T074420Z.log`；未为接线验证调用生成接口。
