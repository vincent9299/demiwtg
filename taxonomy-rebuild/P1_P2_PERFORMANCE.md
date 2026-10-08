# P1→P2 真实运行与性能报告

> 后续原生 Dataset 边界改造的真实验证见 [本轮 review 第 5 节](P1_P2_BOUNDARY_IMPLEMENTATION_REVIEW.md)。种子搜索／下载及零调用重放成功；“天牛”的 P1 成功，12,000／4,000 材料 tokens 两次 P2 均返回 HTTP 408；后续确认是上游 Cloudflare 524 被 LiteLLM 转换，且网关每次自动重试两次。没有本轮新增的有效 P2 审定。下文此前成功或失败记录保留其当时配置，不能拿旧记录替代新代码的真实语义验收。


日期：2026-10-01 UTC。模型：`malasci/gpt-6.1-sol`，通过本机 LiteLLM 的 OpenAI 兼容 HTTP 接口调用。输入为 `datasets/master_concepts.lance@4`，源表共 385,292 行；本次仅使用明确选择的小集合，未改写公共概念或图片表，未运行全批。完整规范和实现复核分别见 [实施规范](P1_P2_SPEC.md) 与 [复核记录](P1_P2_REVIEW.md)。

最终两个概念已完成 P1、真实搜索与下载、P2 首轮、一次补查及 P2 第二轮。首次运行 242.1 秒；同配置重跑 12.1 秒，零新增模型调用、零搜索、零下载。两个概念的身份均未核实、出题均暂缓：当前搜索服务返回大量不相关材料。程序执行完成不等于事实审核通过；本报告不构成扩大到 40 万条的质量或容量验收。

## 1. 范围与可复现配置

开发联调先使用天牛、银杏、小熊猫、珊瑚、剪刀、液压剪六个概念；随后以银杏、天牛、液压剪验证修复；最后以银杏、天牛进行最终冷运行和相同配置重跑。后两个集合是前六个的子集，共六个不同原概念，不能把多次运行相加称为不同样本数。另有三次独立计数校准，不计入概念吞吐。

正式入口为 `preparation.concepts.concepts_pipeline.config → run_pipeline`。最终 run 为 `concept_final2_20261001`，P1 / 首轮 P2 / 第二轮 P2 的累计新请求额度各为 2，最多 6 次；输入名单和所有预算均可配置。Notebook 已同步此实际配置，`RUN_PIPELINE=False`，查看结果不发起请求。

使用讨论确定的默认并发：P1 4、搜索 8、获取 8、读取 4、两轮 P2 各 4；概念内查询/网页/文档各 2；共享模型 8、搜索 8、获取 16。每主机并发 2、每秒启动 1 次。每张表单写者，32 行或首行等待 5 秒提交。模型超时 600 秒；搜索/获取总计 30 秒，连接 10 秒；GET 瞬时失败最多一次重试，demiflow 模型客户端没有自动重试。后续发现现有 LiteLLM 网关会默认重试两次；本文模型调用数是客户端向网关发起的请求数，不能当作上游尝试次数或费用硬上限，详见最新 review 第 5.4 节。

最终输入/输出预算仍为 P1 8,000/4,000，P2 32,000/8,000；材料首轮上限 12,000，补充新增上限 6,000，第二轮材料上限 18,000。读取时为完整请求预留空间，不为填满材料额度删掉先前证据。

原机器未运行搜索服务。试验使用仓内 SearXNG 源码，在独立临时虚拟环境启动本机服务，启用 Google/Bing/DuckDuckGo/Wikipedia；没有改写旧 collect 采集代码。网页显式经过当前机器可用出网代理，搜索服务本身走本机直连。最终样本是新 run、新请求日志，因此不复用先前模型/网页请求；独立对象目录允许按内容散列去重，上游服务自己的缓存不由本程序控制。

## 2. 最终冷运行与缓存重跑

| 项目 | 首次运行 | 同配置重跑 |
| --- | ---: | ---: |
| 端到端耗时 | 242.12 秒 | 12.15 秒 |
| 有效执行交付 | 2/2 | 2/2 |
| 身份核实 / 出题就绪 | 0/2；0/2 | 0/2；0/2 |
| 新模型请求 | 6 | 0 |
| 新搜索请求 | 12 | 0 |
| 新网页获取尝试 | 24 | 0 |
| 新模型输入 tokens | 116,213 | 0 |
| 新模型输出 tokens | 9,592 | 0 |
| 父进程峰值 RSS | 398.6 MiB | 397.3 MiB |

耗时使用正式入口外侧的单调时钟，包含读取、模型、网页、阶段提交和最终导出。父进程 RSS 不包含所有同时运行的子进程，不可当作整条链的内存硬上限。重跑仍重新读独立对象、编排和验证材料、写阶段快照，因此不是零耗时。

冷运行的两个概念、审定 ID、全部审定内容、文档引用、阅读块及补查回执均与重跑一致；冷运行返回的旧固定 Lance 版本仍可读取。模型日志前后各 2+2+2 条，累计数完全不变；网页和模型的新请求计数同时为零。

## 3. 模型性能与 token 分布

| 节点 | 实际调用数 | 输入 tokens | 输出 tokens | P50 延迟上界 | P95 延迟上界 |
| --- | ---: | ---: | ---: | ---: | ---: |
| P1 | 2 | 3,921 | 2,259 | 43.50 秒 | 71.50 秒 |
| 首轮 P2 | 2 | 49,000 | 3,573 | 56.50 秒 | 58.00 秒 |
| 第二轮 P2 | 2 | 63,292 | 3,760 | 66.50 秒 | 73.25 秒 |

分位数来自 0.25 秒桶的上界；每节点仅两个样本，P95 接近最慢请求，不能当成稳定服务分位数。最后样本模型在途峰值只有 2，尚未压满配置的共享上限 8，因此不是并发扩展能力测试。

平均每概念 58,106.5 输入 tokens、4,796 输出 tokens。两轮 P2 占输入 tokens 的约 96.6%。两条都补查，补查率 100%；这与当前资料失配有关，不应作为全部概念的固定补查率。降低 P1 几百 tokens 不会解决这里的主要成本，先恢复有效检索更有意义。

usage 直接来自服务响应。`completion_tokens` 已包含服务报告的 reasoning 部分，不能再把 reasoning tokens 额外加到输出总量；部分响应还报告上游 cached_tokens，但它仍包含在 prompt_tokens 内，与本程序“整请求复用、零新调用”是两回事。缺少对应单价，本报告不报金额，也不把模型列表中的零价格元数据当作免费。

如果纯算术地把本次失配样本的平均量复制到 40 万条，将是约 232.4 亿输入和 19.2 亿输出 tokens。这不是全库预测，仅展示为何必须先改善资料质量和重新测量。默认预算的理论最坏上限为 288 亿输入、80 亿输出 tokens，实际应低于上限；当前样本不能给出可靠全批工期或费用。

## 4. 搜索、获取、读取与排队

| 测量项 | 实际结果 |
| --- | --- |
| 逻辑搜索 | 12 次；11 次有候选，1 次引擎不完整 |
| 网页获取 | 24 次；17 份技术可读、6 次 HTTP 错误、1 次解析失败 |
| 技术可读率 | 17/24，70.8%；不等于相关或可信率 |
| 网络传输 | 1,207,408 字节传输，5,422,221 字节解压后内容；包含搜索与网页 |
| HTTP 跳数 | 37，包含搜索请求和网页重定向 |
| 检索复用 | 4 次，包含同 run 重复使用或并发合并 |
| 搜索单请求延迟 | P50 ≤2.25 秒，P95 ≤4.25 秒 |
| 网页获取与解析延迟 | P50 ≤1.75 秒，P95 ≤5.50 秒 |
| 在途峰值 | 模型 2、搜索 4、网页获取 2 |

| 每概念算子 | 调用数 | 处理总时长 | P95 上界 |
| --- | ---: | ---: | ---: |
| 首轮搜索 | 2 | 11.88 秒 | 6.00 秒 |
| 首轮获取与解析 | 2 | 24.57 秒 | 12.75 秒 |
| 首轮阅读 | 2 | 4.57 秒 | 2.50 秒 |
| 补查搜索 | 2 | 5.17 秒 | 3.50 秒 |
| 补查获取与解析 | 2 | 15.11 秒 | 8.00 秒 |
| 补查阅读 | 2 | 6.12 秒 | 3.50 秒 |

这里“处理总时长”将并发调用分别累计，不能与其他阶段相加当作整批耗时。算子时长包含内部资源等待；输入队列等待和向下游发送的等待另计。批提交者按批计时，攒批等待不计作写入时间。本小样本各输入队列等待 P95 均不超过 0.25 秒，尚未形成持续队列压力；不能据此证明 40 万条无需调整队列。

补查时，天牛的新增材料额度收缩为 2,995 tokens，银杏收缩为 2,593；第二轮实际材料分别为 14,776、14,440 tokens。完整输入分别为 31,552、31,740，均低于 32,000。之前阅读的证据和首轮判断保留，未纳入的新增块仍标记未读。

## 5. 质量复核及这次不能得出的结论

最终两条全部逐项查看。P1 提出名称桥接、分类等级/范围和核心视觉内容的问题；修订后的 query 是检索关键词。P2 两轮都区分了待核候选与已证事实，没有把旧分类、候选学名或无关页面充当证据；结果为 unresolved/hold，草案为 null，没有编造核心事实引用。两条都完整保留。

这只证明这几个失配案例没有被错误通过。没有获得足够相关材料，因此本次没有真实验证到“天牛核实为科、银杏核实为种”的正例，更没有测出物种识别准确率或视觉任务质量；这些知识不能由助手代填进结果冒充 pipeline 证据。

当前搜索服务存在可复现的问题：简单“剪刀”或“Cerambycidae”查询也会得到无关主题；Google/DuckDuckGo 等分支出现验证码，另外试查的引擎出现限流、拒绝访问或解析失败。候选页并不都相关，技术可读率不是证据成功率。具体部署排查已记录到 [collect TODO](../collect/TODO.md)，此处没有恢复旧名称命中硬筛，也没有通过人工替换网页来造出成功结果。

当前不建议直接跑 3,315 或 40 万条。下一项前置是修复/更换可用的检索服务配置并做已知概念的内容核对，然后用涵盖身份明确、多义、物种等级和专业器具的样本检验正例与拒绝例，再做逐步增加并发的测量。P3/P4 新树算法、历史正文和图片下载仍按既定主线单独推进。

## 6. 开发试验成本与失败记录

最终数据不能覆盖前面联调的失败。首次直连尝试被中断并保留回执；配置代理后六条开发记录中五条遇到 nullable 输出问题，一条收到 HTTP 408，均没有交付假成功。三条修复试验首轮有效，但补查曾因完整上下文超额而停止；再次恢复时又发现文档完成顺序与持久预算衔接问题。相关修复、验证和边界见复核记录。

下表按原生 SQLite 日志去重请求计数，含失败；同一概念因修复产生的新请求仍计费意义上是新请求，不能计作复用。

| 调用范围 | 不同请求数 | 有 usage 的响应数 | 已知输入 tokens | 已知输出 tokens |
| --- | ---: | ---: | ---: | ---: |
| `calibration` | 3 | 3 | 35,407 | 36 |
| `concept_live6_20261001` | 12 | 11 | 116,773 | 19,039 |
| `concept_live3_fixed_20261001` | 12 | 12 | 246,363 | 21,337 |
| `concept_final2_20261001` | 6 | 6 | 116,213 | 9,592 |

HTTP 408 的记录没有 usage，不能当成已知零费用。表中 token 总量仅覆盖有回执 usage 的部分。搜索诊断和环境安装单独留记录，不计入最终两概念的吞吐。全部有 usage 的请求均按当前计数规则复核，输入偏差为零；这仍不是厂商对未来路由的保证。

## 7. 留存证据与复现

- [最终配置](/yzp/zhaozy/yangzepeng/0905/_demiflow/concepts_live_review_20261001/final_config.json)、[计数档案](/yzp/zhaozy/yangzepeng/0905/_demiflow/concepts_live_review_20261001/token_profile.json)、[最终实测代码散列](/yzp/zhaozy/yangzepeng/0905/_demiflow/concepts_live_review_20261001/final_source_hashes.json)。
- [首次运行结果与固定表引用](/yzp/zhaozy/yangzepeng/0905/_demiflow/concepts_live_review_20261001/final_cold_result.json)、[首次运行进程指标](/yzp/zhaozy/yangzepeng/0905/_demiflow/concepts_live_review_20261001/final_cold_process.json)。
- [重跑结果与固定表引用](/yzp/zhaozy/yangzepeng/0905/_demiflow/concepts_live_review_20261001/final_warm_result.json)、[重跑进程指标](/yzp/zhaozy/yangzepeng/0905/_demiflow/concepts_live_review_20261001/final_warm_process.json)、[零新增调用及内容一致核验](/yzp/zhaozy/yangzepeng/0905/_demiflow/concepts_live_review_20261001/verification.json)。
- [逐请求性能与 usage](/yzp/zhaozy/yangzepeng/0905/_demiflow/concepts_live_review_20261001/measured_calls.json)、[全部工程测试日志](/yzp/zhaozy/yangzepeng/0905/_demiflow/concepts_live_review_20261001/final_tests.log)。
- [独立网页对象目录](/yzp/zhaozy/yangzepeng/0905/_demiflow/concepts_live_review_20261001/document_objects)；实际正文引用由阶段表和模型原生日志绑定，不通过 Lance 文档记录定位。
- [统一 notebook](/yzp/zhaozy/yangzepeng/0905/demiwtg/preparation/concepts/concepts_debug.ipynb)，默认不运行；可独立查看已提交摘要和 Markdown。

运行仍走正式入口，例如：

```bash
PYTHONPATH=demiwtg:demiflow env/bin/python -m preparation.concepts.concepts_pipeline \
  --run concept_final2_20261001 \
  --config /yzp/zhaozy/yangzepeng/0905/_demiflow/concepts_live_review_20261001/final_config.json
```

需要模型服务与 api_key_env 已就绪；本机此次网关未开启鉴权，测试进程使用 EMPTY 占位值，没有将上游密钥写入配置或报告。临时搜索服务在测试结束后关闭；同 run 的完整缓存重跑不需要新增网络请求，新 run 必须配置并启动可用搜索服务。正文对象与调用日志为持久审计资料，不能当作临时缓存删除。

## 8. 搜索词、候选结果与读取范围逐项复核（用户追问后补充）

本节只读复核 `concept_final2_20261001` 首次运行保存的固定版本，不重新搜索，不新增模型调用。日期为 2026-10-01（北京时间）。下面的搜索词是实际执行字符串，候选是当次服务返回并由 pipeline 保留的前五项；没有用人工找到的页面替换结果。首轮每概念四条（程序保留原名查询，P1 生成另外三条），补查每概念两条，由首轮 P2 提出。共十二条，十一条各有五个候选，一条没有候选。

复核结论：此前“相关性不足”的描述过于笼统。正常的原名查询也返回完全偏题的页面，说明本次检索链路存在严重异常；同时，模型生成的部分查询堆叠过多别名和核验方向，查询质量也有问题。二者应分开处理。不能把“搜索不到可用资料”解释成银杏、天牛没有可靠资料或概念不成立。

### 8.1 能确认的边界与尚未定位的根因

- 当前 `WebClient.search` 把查询原文作为 HTTP `q` 参数，缓存身份包含该查询。重新计算十二条查询的缓存键，全部与阶段表中的前五个候选一致；未发现这两处传错查询或串用缓存的证据。这不能证明上游链路全部正确。
- 十二条返回均附有 Google 验证码暂停、DuckDuckGo 验证码错误。已有独立单引擎诊断还显示：Bing 查询“剪刀”得到 King 游戏/电影页面，查询“Cerambycidae”得到 Google Translate 页面，返回的 query 字段仍与请求相同。即使不用长查询，也能复现异常。
- 正式搜索结果只保存规范化候选与问题回执，丢掉了每候选的引擎归属、服务返回的 query 和原始搜索响应；没有保存 Bing 原始 HTML 或完整网络追踪。因此不能进一步断言一定是 Bing 本身的问题，也尚不能排除 SearXNG 适配、代理或上游返回异常。原始网页快照的保留不等于原始搜索响应也已保留。
- 发现长查询，例如银杏一条同时涉及分类、落叶性、保护状况、野生/栽培和“活化石”，补查又把形态和保护状况放进同一条。P1 prompt 已要求“一个查询聚焦一个明确的取证方向”，实际输出仍未充分遵守；不能仅因结构校验通过就称查询设计合格。
- 获取阶段按可读正文计入额度，不对语义相关性作提前裁决；因此不相关页面仍进入读取。这符合当前职责约定，但缺少检索服务整体健康检查时，会持续消耗下载、阅读与 P2 预算。
- 读取实现按问题轮流选块，但每个问题的候选块列表按文档顺序串接：只在各文档内部排序，没有跨文档交错。首轮银杏选入的 48 个正文块全部来自第一个文件管理器页面；首轮天牛选入的 42 个正文块全部来自第一个文章档案页面。其余页面仍提供目录信息，不能说完全不可见，但没有正文块交给 P2。这是独立的阅读分配问题，不是搜索返回偏题的起因。

### 8.2 全部实际查询和候选

表中的“正文块数”指第二轮材料准备完成时，该文档累计实际交给 P2 的正文块数量，不包括标题、URL 等来源目录信息；“未尝试”不等于失败。跨概念共享 URL 的网络请求会复用，不能将下面的文档行数相加当作独立下载次数。

#### 银杏 1：首轮

来源：程序保留原名。状态：`ok`。

```text
银杏
```

服务提示：Provider reported retrieval problems: [["duckduckgo", "CAPTCHA"], ["google", "Suspended: CAPTCHA"]]

| 排名 | 返回标题与链接 | 获取结果 | 正文块数 |
| --- | --- | --- | ---: |
| 1 | [File Explorer in Windows \| Microsoft Support](<https://support.microsoft.com/en-us/windows/experience/fileexplorer/file-explorer-in-windows>) | ok | 48 |
| 2 | [Explorateur de fichiers dans Windows \| Microsoft Support](<https://support.microsoft.com/fr-fr/windows/experience/fileexplorer/file-explorer-in-windows>) | ok | 0 |
| 3 | [Open File Explorer in Windows 11](<https://www.elevenforum.com/t/open-file-explorer-in-windows-11.20591/>) | 未尝试 | 0 |
| 4 | [Search in Windows 11 File Explorer](<https://www.elevenforum.com/t/search-in-windows-11-file-explorer.21789/>) | 未尝试 | 0 |
| 5 | [Explorer in Windows \| Microsoft Support](<https://support.microsoft.com/de-de/windows/experience/fileexplorer/file-explorer-in-windows>) | 未尝试 | 0 |

#### 银杏 2：首轮

来源：P1 提出。状态：`ok`。

```text
银杏 白果 公孙树 鸭脚树 蒲扇树 Ginkgo Maidenhair tree 名称 别名
```

服务提示：Provider reported retrieval problems: [["duckduckgo", "CAPTCHA"], ["google", "Suspended: CAPTCHA"]]

| 排名 | 返回标题与链接 | 获取结果 | 正文块数 |
| --- | --- | --- | ---: |
| 1 | [Containerization Using Docker: Complete Beginner’s Guide](<https://www.geeksforgeeks.org/blogs/containerization-using-docker/>) | ok | 0 |
| 2 | [What is a Container? \| Docker](<https://www.docker.com/resources/what-container/>) | ok | 0 |
| 3 | [Docker Docs](<https://docs.docker.com/>) | 未尝试 | 0 |
| 4 | [Build and share a containerized application \| Docker Docs](<https://docs.docker.com/get-started/tutorials/run-an-app/>) | 未尝试 | 0 |
| 5 | [Docker Hub Container Image Library \| App Containerization](<https://hub.docker.com/>) | 未尝试 | 0 |

#### 银杏 3：首轮

来源：P1 提出。状态：`ok`。

```text
Ginkgo biloba taxonomy deciduous conservation wild cultivated living fossil
```

服务提示：Provider reported retrieval problems: [["duckduckgo", "CAPTCHA"], ["google", "Suspended: CAPTCHA"]]

| 排名 | 返回标题与链接 | 获取结果 | 正文块数 |
| --- | --- | --- | ---: |
| 1 | [Wikipedia 和 Wikia 哪个是维基百科；它们和维基解密有 ...](<https://www.zhihu.com/question/24790852>) | http_error：HTTP 403 | 0 |
| 2 | [不同语言的维基百科的条目内容一样吗？ 中文的内容 ...](<https://www.zhihu.com/question/28220417>) | http_error：HTTP 403 | 0 |
| 3 | [bigbang一天一天的歌词、要原版歌词和中文版翻译的如 ...](<https://zhidao.baidu.com/question/1830391065414523220.html>) | ok | 0 |
| 4 | [wikipedia中的pedia在这个词里有什么具体含义？_百度知道](<https://zhidao.baidu.com/question/994562419309312219.html>) | ok | 0 |
| 5 | [为什么这么多人把「Wikipedia」称作「wiki」？ - 知乎](<https://www.zhihu.com/question/19581089>) | 未尝试 | 0 |

#### 银杏 4：首轮

来源：P1 提出。状态：`ok`。

```text
Ginkgo biloba morphology leaf venation long short shoots dioecious seed coat autumn
```

服务提示：Provider reported retrieval problems: [["duckduckgo", "CAPTCHA"], ["google", "Suspended: CAPTCHA"]]

| 排名 | 返回标题与链接 | 获取结果 | 正文块数 |
| --- | --- | --- | ---: |
| 1 | [Wikipedia](<https://www.wikipedia.org/>) | ok | 0 |
| 2 | [Wikipedia, the free encyclopedia](<https://en.wikipedia.org/wiki/Main_Page>) | ok | 0 |
| 3 | [Wikipedia - Wikipedia](<https://en.wikipedia.org/wiki/Wikipedia>) | 未尝试 | 0 |
| 4 | [维基百科，自由的百科全书](<https://zh.wikipedia.org/wiki/Wikipedia:%E9%A6%96%E9%A1%B5>) | 未尝试 | 0 |
| 5 | [維基百科，自由的百科全書](<https://zh.wikipedia.org/zh-tw/Wikipedia:%E9%A6%96%E9%A1%B5>) | 未尝试 | 0 |

#### 银杏 5：补查

来源：P2 首轮提出补查。状态：`ok`。

```text
银杏 白果 公孙树 鸭脚树 蒲扇树 Ginkgo Ginkgo biloba Maidenhair tree 植物学名称 别名 种子
```

服务提示：Provider reported retrieval problems: [["duckduckgo", "CAPTCHA"], ["google", "Suspended: CAPTCHA"]]

| 排名 | 返回标题与链接 | 获取结果 | 正文块数 |
| --- | --- | --- | ---: |
| 1 | [Python for Data Analysis: A Practical Guide](<https://realpython.com/python-for-data-analysis/>) | ok | 9 |
| 2 | [Data Analysis with Python - GeeksforGeeks](<https://www.geeksforgeeks.org/data-analysis/data-analysis-with-python/>) | ok | 1 |
| 3 | [Data Analysis with Python: The Complete Guide (NumPy, Pandas ...](<https://openpython.org/articles/data-analysis-with-python>) | 未尝试 | 0 |
| 4 | [Python for Data Analysis, 3E - Wes McKinney](<https://wesmckinney.com/book/>) | 未尝试 | 0 |
| 5 | [Data Analysis with Python - Coursera](<https://www.coursera.org/learn/data-analysis-with-python?msockid=1f62f5be227d6c5a05a0e25b23816d35>) | 未尝试 | 0 |

#### 银杏 6：补查

来源：P2 首轮提出补查。状态：`ok`。

```text
Ginkgo biloba authoritative taxonomy morphology fan-shaped leaves dichotomous venation long shoots short shoots dioecious seed coat deciduous conservation
```

服务提示：Provider reported retrieval problems: [["duckduckgo", "CAPTCHA"], ["google", "Suspended: CAPTCHA"]]

| 排名 | 返回标题与链接 | 获取结果 | 正文块数 |
| --- | --- | --- | ---: |
| 1 | [Google Translate Help](<https://support.google.com/translate/?hl=en>) | 未尝试 | 0 |
| 2 | [Download & use Google Translate](<https://support.google.com/translate/answer/6350850?hl=en&co=GENIE.Platform%3DDesktop>) | 未尝试 | 0 |
| 3 | [Language File System \| RPG Maker Forums](<https://forums.rpgmakerweb.com/threads/language-file-system.17964/>) | 未尝试 | 0 |
| 4 | [Google Chrome Help](<https://support.google.com/chrome/?hl=en>) | 未尝试 | 0 |
| 5 | [my translator is weird - Google Docs Editors Community](<https://support.google.com/docs/thread/149735100/my-translator-is-weird?hl=en>) | 未尝试 | 0 |

#### 天牛 1：首轮

来源：程序保留原名。状态：`ok`。

```text
天牛
```

服务提示：Provider reported retrieval problems: [["duckduckgo", "CAPTCHA"], ["google", "Suspended: CAPTCHA"]]

| 排名 | 返回标题与链接 | 获取结果 | 正文块数 |
| --- | --- | --- | ---: |
| 1 | [Laurent Gunéyot's Unz Archive 2018-2023 - The Unz Review](<https://archive.org/details/laurent-guyenot-unz-archive/Laurent-Guy%C3%A9not_s-Unz-Archive_-Apr-2018-Nov-2023-The-Unz-Review-_2023_>) | ok | 42 |
| 2 | [Laurent Guyénot Archive - The Unz Review](<https://www.unz.com/author/laurent-guyenot/feature/page/1/>) | http_error：HTTP 403 | 0 |
| 3 | [Laurent Guyénot Archive - The Unz Review](<https://www.unz.com/author/laurent-guyenot/202/page/2/>) | http_error：HTTP 403 | 0 |
| 4 | [Vietnam War's Impact on Israel's Interests \| PDF \| Warren ... - Scribd](<https://www.scribd.com/document/899047066/Laurent-Guyenots-Unz-Archive-Apr-2018-Nov-2023-Laurent-Guyenot-WeLib-org>) | parse_error：empty_body | 0 |
| 5 | [A Holocaust of Biblical Proportions, by Laurent Guyénot - The Unz …](<https://archive.ph/toqSB>) | ok | 0 |

#### 天牛 2：首轮

来源：P1 提出。状态：`ok`。

```text
天牛 天牛科 Cerambycidae 长角虫 Longhorn beetle 名称 对应 分类
```

服务提示：Provider reported retrieval problems: [["duckduckgo", "CAPTCHA"], ["google", "Suspended: CAPTCHA"]]

| 排名 | 返回标题与链接 | 获取结果 | 正文块数 |
| --- | --- | --- | ---: |
| 1 | [Wikipedia](<https://www.wikipedia.org/>) | ok | 0 |
| 2 | [Wikipedia, the free encyclopedia](<https://en.wikipedia.org/wiki/Main_Page>) | ok | 0 |
| 3 | [Wikipedia - Wikipedia](<https://en.wikipedia.org/wiki/Wikipedia>) | 未尝试 | 0 |
| 4 | [維基百科，自由的百科全書](<https://zh.wikipedia.org/zh-tw/Wikipedia:%E9%A6%96%E9%A1%B5>) | 未尝试 | 0 |
| 5 | [维基百科，自由的百科全书](<https://zh.wikipedia.org/wiki/Wikipedia:%E9%A6%96%E9%A1%B5>) | 未尝试 | 0 |

#### 天牛 3：首轮

来源：P1 提出。状态：`ok`。

```text
天牛 Cerambycidae 分类层级 成虫 幼虫 中文名
```

服务提示：Provider reported retrieval problems: [["duckduckgo", "CAPTCHA"], ["google", "Suspended: CAPTCHA"]]

| 排名 | 返回标题与链接 | 获取结果 | 正文块数 |
| --- | --- | --- | ---: |
| 1 | [Poe - Fast, Helpful AI Chat](<https://poe.com/>) | http_error：HTTP 403 | 0 |
| 2 | [Poe - Poe](<https://poe.com/poe>) | ok | 0 |
| 3 | [News - Path of Exile - A Free Online Action RPG](<https://www.pathofexile.com/>) | ok | 0 |
| 4 | [Path of Exile](<https://www.pathofexile.com/game>) | 未尝试 | 0 |
| 5 | [《流亡黯道 PoE》 - Path of Exile](<https://pathofexile.tw/game>) | 未尝试 | 0 |

#### 天牛 4：首轮

来源：P1 提出。状态：`ok`。

```text
天牛科 成虫 触角 体形 鞘翅 识别 特征 种间差异
```

服务提示：Provider reported retrieval problems: [["duckduckgo", "CAPTCHA"], ["google", "Suspended: CAPTCHA"]]

| 排名 | 返回标题与链接 | 获取结果 | 正文块数 |
| --- | --- | --- | ---: |
| 1 | [Wikipedia 和 Wikia 哪个是维基百科；它们和维基解密有 ...](<https://www.zhihu.com/question/24790852>) | http_error：HTTP 403 | 0 |
| 2 | [有什么值得推荐的英文维基镜像网站? - 知乎](<https://www.zhihu.com/question/347202458>) | http_error：HTTP 403 | 0 |
| 3 | [bigbang一天一天的歌词、要原版歌词和中文版翻译的如 ...](<https://zhidao.baidu.com/question/1830391065414523220.html>) | ok | 0 |
| 4 | [法语的几个特殊的字母，列出来一下 - 百度知道](<https://zhidao.baidu.com/question/94366850.html>) | ok | 0 |
| 5 | [不同语言的维基百科的条目内容一样吗？ 中文的内容 ...](<https://www.zhihu.com/question/28220417>) | 未尝试 | 0 |

#### 天牛 5：补查

来源：P2 首轮提出补查。状态：`search_incomplete`。

```text
"天牛" "天牛科" "Cerambycidae" 科普 定义
```

服务提示：Provider reported retrieval problems: [["duckduckgo", "CAPTCHA"], ["google", "Suspended: CAPTCHA"]]

本次没有候选。

#### 天牛 6：补查

来源：P2 首轮提出补查。状态：`ok`。

```text
Cerambycidae adult larval morphology antennae sexual dimorphism exceptions host plants entomology
```

服务提示：Provider reported retrieval problems: [["duckduckgo", "CAPTCHA"], ["google", "Suspended: CAPTCHA"]]

| 排名 | 返回标题与链接 | 获取结果 | 正文块数 |
| --- | --- | --- | ---: |
| 1 | [Cornèr: Private banking, loans and business solutions](<https://www.corner.ch/en/>) | ok | 12 |
| 2 | [Digital banking: online services \| Cornèr](<https://www.corner.ch/en/digital-banking/>) | ok | 0 |
| 3 | [SPC Authentication - SI/PI Log in](<https://ids-ext.bcthk.com/Account/SIPILogin>) | 未尝试 | 0 |
| 4 | [Cornèr Group: Solidity, Security, Tradition and Innovation \| Cornèr Group](<https://www.cornergroup.ch/en/>) | 未尝试 | 0 |
| 5 | [iCornèr App \| Cornèr Bank](<https://www.corner.bs/digital-banking/icorner/>) | 未尝试 | 0 |

### 8.3 改进方向与验证次序（尚未在本次复核中实施）

先用“银杏”“Ginkgo biloba”“天牛”“Cerambycidae”等简短固定查询逐层检查真实检索链路，记录最终发出的请求、服务返回 query、候选引擎和原始响应。先确认结果主题正常，再比较查询策略；仅改 prompt 不能证明检索链路已恢复。

查询改进应让每条保留一个主要对象和一个取证方向，例如“银杏 Ginkgo biloba 中文名”“Ginkgo biloba taxonomy”“Ginkgo biloba leaf morphology”；天牛可用“天牛 Cerambycidae 中文名”“Cerambycidae classification”“Cerambycidae adult morphology”。这些只是拟议写法，本节没有实际执行，也不承诺它们能绕过当前网络或服务故障。每轮总查询数仍需受已确认预算约束，不为每个断言无上限扩展检索。

阅读需要在问题和文档之间分配有限额度，避免首文档独占；相关性和事实判断仍由业务审定负责，不恢复旧 collect 的标题/名称硬过滤。服务健康检查、原始搜索回执留存和正文分配问题均应在进一步质量试验前处理。后续应重新测量检索命中率、有效正文覆盖和正例审核结果，而不是复用这次异常样本推断正常成本。

复核来源：本报告第 7 节中的固定表引用与搜索诊断；缓存对应性核对使用 `_demiflow/concepts/concept_final2_20261001/retrieval.sqlite`，只读查询。当前实现位置：`demiflow/demiflow/collect/web.py` 的 `WebClient.search` 与 `demiwtg/preparation/concepts/operators/audit.py` 的 `select_material`。

## 9. 根因对照实验：遗漏有效搜索源与信息框适配（2026-10-01，北京时间）

本节补充真实网络对照，收紧第 8 节尚未定位的结论。没有新增模型调用，没有重跑或改写原有阶段表；使用独立诊断服务和独立下载对象。结论是：搜索取证路线在这两个概念上能够取得相关正文；前次整体失配的主要原因是试验检索配置和结果交接存在缺口，不能归因于概念没有资料，也不能主要归因于查询太长。

### 9.1 已确认的原因

1. **临时试验漏掉了旧 collect 的有效搜索源。** 旧 `collect/webgate/settings.yml.example` 明确启用 `wikisearch`，实现为 `demi_wikisearch.py`，调用 Wikipedia 的关键词搜索接口。本次初始试验只保留 Google、Bing、DuckDuckGo 和内置 `wikipedia`，没有保留 `wikisearch`。因此不能将这次配置的表现当作旧采集链路的表现。
2. **内置 Wikipedia 的有效结果在接口交接中被忽略。** 当前仓内 `wikipedia.py` 默认 `display_type=["infobox"]`。真实单引擎请求“银杏”和“Cerambycidae”得到 `results=[]`，但 `infoboxes` 中分别有银杏及 Longhorn beetle 的有效条目链接。当前 `WebClient.search` 只读取 `payload['results']`。这意味着“没有候选”并不一定是上游没有找到条目。应在源配置中让它同时输出列表候选，或者在适配层把信息框的明确来源链接作为候选交付；信息框内容本身仍不能冒充已下载正文。
3. **通用搜索剩余分支确有异常。** 前次 Google、DuckDuckGo 报验证码。此次真正单独请求 Bing，保存了 SearXNG 向上游发送的完整 URL 和收到的原始 HTML：查询参数、页面标题和输入框均为“银杏”或“Cerambycidae”，HTML 中的自然搜索结果却已经是完全不相干的主题。SearXNG 解析出的标题与原始 HTML 一致。因此偏题发生在结果解析之前，不能归因于 P1 改词、Lance 关联或正文解析。最底层是否为 Bing 服务、出网代理或其他上游链路的问题，本实验仍无法区分；直连超时，本机另一个代理也连接失败，不能声称完成了独立网络出口对照。

三者叠加形成前次失败：可工作的旧关键词搜索源没有启用，内置维基链接又没有进入候选，通用搜索池则只留下失效或偏题的结果。关键词过长和阅读分配问题仍应改进，但它们不是“银杏”这种短查询也失败的解释。

### 9.2 参数对照中发现的另一个陷阱

首次诊断同时传了 `engines=指定引擎` 与 `categories=general`。该版本 `webadapter.parse_generic` 会把指定引擎与该分类下的引擎取并集，因此这批请求不能当作单引擎实验。例如指定 `wikisearch` 时仍出现 `engines=["bing"]` 的候选。已保留这批原始记录，并重新测试：只传 `engines`，不传 `categories`。下面的结果全部来自重新执行后的真正单引擎记录。旧代码“engines 参数无效”的注释也应按这个更精确的语义理解，不能认定这个参数本身完全不起作用。

### 9.3 真实搜索与当前下载器的验证

使用仓内旧 `demi_wikisearch`，显式选择语言，诊断服务通过本机现有可用代理出网。搜索后将返回的第一条 URL 交给当前 demiflow `WebClient.fetch`，使用默认正文大小边界、独立对象存储、不重试。没有人工猜测 URL 替代搜索结果。

| 原样查询 | 语言 | 第一条候选 | 搜索耗时 | 下载和解析耗时 | 规范化文本字符数 |
| --- | --- | --- | ---: | ---: | ---: |
| 银杏 | zh-CN | 中文“银杏”条目 | 0.817 秒 | 2.564 秒 | 11,781 |
| 天牛 | zh-CN | 中文“天牛科”条目 | 0.660 秒 | 3.547 秒 | 176,698 |
| Cerambycidae | en | 英文“Longhorn beetle”条目 | 0.993 秒 | 3.998 秒 | 17,928 |

三份页面均 HTTP 成功，原始字节和解析对象保留并带 SHA。银杏文本实际包含候选学名和扇形叶片内容；天牛文本包含 Cerambycidae、触角或相应英文分类内容。这里证明“能找到并取得相关正文”，不代表这些来源已经由 P2 完成事实审定，也不代表所有解析块都是高价值正文：解析结果仍含语言导航等残留，中文天牛页面又很长，材料清洗与跨文档阅读分配仍需后续修复、验证。

还将原先实际执行的长查询 `Ginkgo biloba taxonomy deciduous conservation wild cultivated living fossil` 原样交给单独的 `wikisearch`：0.671 秒返回五个候选，第一条仍为 Ginkgo biloba。这直接反驳“主要因为长查询才全面检索失败”的解释；不能由这个单例推导所有长查询都合适。

在混合引擎探索中出现一次 `wikisearch` 限流回执，已记录，没有持续重试或更换身份绕过。随后单引擎对照采用串行、请求间隔 1.5 秒。因此这些少量成功请求不能证明接口能承受全批并发，也不应直接套用之前的搜索并发上限。

### 9.4 对路线的判断和未完成项

可以继续沿用“问题计划 → 搜索候选 → 下载正文 → 有预算地读取 → P2 审定”的路线。这次真实对照已经给出检索与下载正例，无需因此推翻整体流程。下一步应在正式服务配置中补回有效源，明确处理内置信息框链接，正确传递引擎和语言参数，保留引擎溯源及原始响应；同时解决正文噪声与文档间预算分配，再重新跑完整 P1→P2 的小样本质量验收。不同类型的概念仍需相应的可用来源，三个维基页面不等于所有领域和四十万条都已验证。

本轮只完成诊断和独立网络验证，上述正式配置、适配及阅读修复尚未实施，原有失败审定保持不变。诊断服务仅监听本机 8082，完成后已核对进程身份并关闭；没有停止其他服务。

证据目录：`_demiflow/concepts_live_review_20261001/search_root_cause/`。`network_results.json` 是七次网络路由探测；`searx_results.json` 是十一条混合参数诊断，不能标为单引擎结果；`isolated_results.json` 是九条真正单引擎实验；`upstream/` 保存上游请求 URL、状态、原始响应与 SHA；`fetch_results.json` 和 `objects/` 保存三次当前下载器的实际交付。诊断脚本是本次网络排查留痕，不是新增业务 pipeline 入口。

## 2026-10-01：正式 Dataset 流式 HTTP 验证

本次通过原 `concepts_pipeline.config → run_pipeline` 入口，固定 master_concepts@4，仅“天牛”，最多 2 次新模型请求，真实取证；不同于前面的临时请求 replay。原生 `map_prompt_async` 开启 stream + LiteLLM 零重试适配，未修改 prompt/模型或热改共享网关。

| 指标 | P1 | 首轮 P2 |
| --- | ---: | ---: |
| 总请求时间 | 46.21 s | 128.36 s |
| 首 SSE 事件 | 1.61 s | 1.47 s |
| 首个非空正文 | 13.95 s | 11.12 s |
| 最大相邻数据间隔 | 12.34 s | 9.65 s |
| input / output tokens | 1,907 / 880 | 11,032 / 3,729 |
| HTTP / stop / DONE / schema | 全部成功 | 全部成功 |
| 网关 retries / fallbacks | 0 / 0 | 0 / 0 |

冷运行 195.38 s，5 次真实搜索（含补查）与 2 份成功下载的独立文档；首轮材料上限 4,000 tokens。P2 请求补查，而本测试补查模型额度为 0，所以最终 call_budget/complete=False；首轮成功判断保留。这不是完整概念审定质量验收。

同配置重放 11.26 s，模型新请求 0、搜索 0、下载 0；网页复用 7 项，模型累计日志和 usage 不变。详见 [完整复核 5.9](P1_P2_BOUNDARY_IMPLEMENTATION_REVIEW.md#59-正式-dataset-http-流式能力实现与复核2026-10-01) 和工作区 `_demiflow/concepts_stream_20261001/live/verification.json`。这一个案例不能外推 40 万条耗时或费用。


2026-10-01 后续：用户询问额度后，只将同一 run 的补查模型调用上限从 0 调到 1。P1/首轮 P2 与网页全部复用，新增一次第二轮 P2：126.02 秒，首 SSE 2.42 秒，首正文 23.70 秒，input/output=15,764/3,200，HTTP/stop/DONE/schema 成功，网关 retries/fallbacks=0。续跑端到端 136.37 秒。最终因模型将名称改为“天牛科（Cerambycidae）”却声明 unchanged，被业务字段一致性校验记为 invalid_response，而非额度不足；未进一步加预算或自动重试。详情及 P2 契约待办见 review 第 5.10 节。
## 2026-10-01 新增多概念测量

详见 [完整质量与性能记录](P1_P2_多概念质量验证_20261001.md)，旧章节保留为历史运行，不覆盖旧失败。当前八概念 P1 对照：4 并发 102.26 秒、8 并发 46.75 秒，均 8/8 提交、零调用错误，实际共享峰值与设置一致。输出长度与服务时延也不同，约 2.19 倍只是本次观察值。未测完整 P2 8 并发，不能将该倍数或每条耗时直接外推到 3,315 条。

最新 18 概念整链在 484.81 秒因搜索连续故障停止，交付 6 条，27 次模型请求中 23 次完整响应、4 次本地取消。已知输入 246,047、输出 38,585 tokens，取消请求用量未知。64,000 输入容量校准另算 1 次。模型流式成功、搜索技术可用、概念事实核实和视觉任务可用分别统计。
