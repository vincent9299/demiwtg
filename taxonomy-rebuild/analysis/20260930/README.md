# 2026-09-30 只读研究证据

给用户阅读的入口是 [真实数据与案例验证](../../11-真实数据与案例验证.md) 和 [pipeline 讨论稿](../../PIPELINE_PROPOSAL_v0.1.md)。这里保存研究的原始摘要，未写公共 Lance，也不作为新的权威业务表或生产 pipeline 入口。

| 文件 | 内容 |
| --- | --- |
| profile.json | master@4、images@16、articles@8 的全量窄列统计；耗时只对应这些本地读查，不是生产吞吐 |
| cases-source.json | 17 个定向概念的原名、全部旧路径、别名与 carriers |
| case-image-counts.json | 定向概念的全量历史图关联数量、少量低关联数样例，以及过滤门槛的影响分析 |
| random32-source.json | 固定哈希抽样方法、seed 与 32 条原始记录 |
| gbif-responses.json | 6 次真实 GBIF v2 响应、URL、抓取时间及原响应摘要哈希；response_body 已与当时原字节 SHA 核对一致，外部服务未来可能变化 |
| image-availability-crosscheck.json | 全量交叉检查 availability 与 URI：available 无 URI 为 0，metadata_only 有 URI 为 0 |
| retrieval-and-pixels.json | 非空改名集合回放、两来源并集、12 张图片 SHA 与完整解码回执 |
| profile_readonly.py | 当次统计脚本快照：固定生产版本只读，摘要写 `/tmp` |
| case_images_readonly.py | 当次关联扫描脚本快照：读取当时 `/tmp/taxonomy-design-cases-master.json`，只读生产表，摘要写 `/tmp` |

脚本保留当次路径用于追溯，不是日常运行界面。复核 case_images 脚本时可把本目录 cases-source.json 复制到其注明的临时输入位置；正式实现需要改用仓库标准 Dataset 数据流与 Lance 阶段结果。不能把这些研究脚本包装成已经实现的通用分类器。

case-image-counts 中最初的 Mooncake 空集合回放仅是过程记录；有效的非空改名验证在 retrieval-and-pixels 中，报告没有把空集合通过算成迁移保证。图片视觉判断在 Markdown 中由本次助手记录，SHA/解码回执不等于视觉审核通过。
