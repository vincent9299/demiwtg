# tools/ — 采集配套离线工具(2026-09-14 自 kb_night 夜航工作区晋升)

| 脚本 | 作用 | 输入 → 输出 |
|---|---|---|
| extract_embed.py | 从①语料 images 字段抽内嵌图清单 | corpus 分片 → (qid, file) 清单 |
| extract_tier1.py | truthy 全量抽图像属性引用+语义角色 | truthy 流 → (qid, PID, role, file) |
| extract_tier2.py | truthy 抽概念集外 P18 候选(档2) | truthy 流 → (qid, file) |
| expand_p935.py | P935 图库页 API 展开(带图注) | (qid, gallery标题) → (qid, file, caption) |
| filter_sitelinks.py | 批量查 sitelink(有语料实体过滤) | 候选 qid → 有页面 qid 集合 |

已知坑(踩过,勿再踩): Commons titles 的 `|` 分隔符必须裸传(httpx 编码
%7C → WAF 403); UA 联系方式须完整邮箱格式; truthy 分片是单流字节切片,
仅偏移 0 起可独立解压,重放须按拼接序; COS 匿名读 ~10 req/s/IP。
