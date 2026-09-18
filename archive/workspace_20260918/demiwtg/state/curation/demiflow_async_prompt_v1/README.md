# demiflow

声明式数据管线引擎（自 [Demiurge](https://github.com/vincent9299/Demiurge) 独立化，2026-09-04 起独立维护）。

## 定位

- **采集底座**（`demiflow.collect`，2026-09-04 起，机制归引擎、策略归消费方）：
  - `net`：按源限速闸门、分类重试、双池代理客户端、流式下载原语
    （限速表/代理名单/身份 UA 由消费方 `register_limits`/`PROXY_URL` 注册）；
  - `fetch.fetch_tiers`：多候选档位轮转 + 字节封顶 + 硬超时 + verify 内容钩子
    （verify 返回业务元数据随 Fetched.extra 透传）；
  - `store.AppendManifestStore`：内容寻址 blob 原子写 + jsonl 追加清单 +
    跨进程 fcntl 幂等去重（吸收式尾扫索引）；
  - `resume.scan_counts`：清单现算 done-set/计数（断点续跑依据，不落盘）；
  - `crawl.PageCrawler`：URL → 正文 Markdown（Crawl4AI 进程内封装，
    新旧版本兼容 shim + 惰性依赖，extras `[crawl]`）；
  - `images.verify_image`：字节 → 图像元数据（Pillow 全量解码 +
    mime/ext 规范表，fetch_tiers 的 verify 钩子，extras `[images]`）；
  - `search`：SearchEngine 协议 + 注册表 + `is_connect_failure`；
  - `llm`：AsyncLLMClient（单次 chat，重试归消费方口径）+ 端点资源注册表
    （`register_endpoint(base_url_env=...)` 配置驱动，env 覆盖跨机器零代码）；
  - 调度：`data.plan.StreamStage` 规范算子（策略字段随算子声明）+
    `Dataset.map_async` + `Dataset.run_stream`（Dataset 串联执行 +
    退出期平台资源统一收尾）；
- **惰性批式路径**：Ray Data 兼容超集的 Dataset API（`from_items/map/filter/take_all/write_*`），
  确定性物理计划 + 本地线程池执行器；Ray 为可选 extras；
- **流式路径**（2026-09-04 新增）：`map_async` + `run_stream`——常驻 worker 协程 +
  有界队列 + 无序发射 + sentinel 逐级排空，为长夜跑采集/富化管线设计
  （认缺分级、字节级内存上界、Ctrl-C 收尾钩子）；
- **IO**：json/parquet/csv sink；Lance 版本化读写为 extras（`pip install demiflow[lance]`）；
- **LLM 算子**：`map_prompt`（schema 校验 JSON + 重试 + 图片输入，extras `[llm]`）。

## 快速开始

```bash
pip install -e .              # 核心：pyarrow/PyYAML/click/packaging（net/fetch/store/resume 零额外依赖）
pip install -e .[dev]         # + pytest
pip install -e .[collect]     # + 采集栈：crawl4ai（crawl）+ pillow（images）
```

依赖口径：crawl4ai 等重依赖全部 extras 化、机制内惰性 import——核心安装零重物。
SearXNG 类**服务**依赖不是 Python 包（PyPI 同名包为占位包），由消费方
自行部署（如 demiwtg-data 的 data_pipeline/webgate 模块），不进本库依赖。

python smoke_standalone.py  # 惰性路径冒烟
python -m pytest tests/ -q  # streaming 路径 10 用例
```

```python
from demiflow.standalone import local_data

ctx = local_data()

# 惰性路径
out = (ctx.from_items([{"x": i} for i in range(10)])
       .map(lambda r: {**r, "y": r["x"] * 2})
       .filter(lambda r: r["y"] > 5)
       .take_all())

# 流式路径：async 算子 + 认缺分级 + 有界背压
def build(ds):
    return (ds
            .map_async(fetch, concurrency=32, queue_depth=64,
                       catch=(TransientError,), label="fetch")
            .map_async(score, concurrency=48, label="score"))

stats = build(ctx.from_items(rows)).run_stream(
    on_progress=lambda s: print(s.summary()),
    on_drain=lambda s: cleanup(),
    log_every=100)
```

## 双执行路径语义

| | 惰性路径（sync 算子） | 流式路径（`map_async`） |
|---|---|---|
| 触发 | `take*/count/write_*` 动作 | `run_stream()`（同步入口，勿包进 asyncio.run） |
| 并发 | 物理计划按 stage 分配线程 | 每级 `concurrency` 个 worker 协程（单事件循环） |
| 顺序 | 保序（滑窗） | 无序（吞吐优先） |
| 背压 | 滑窗宽度 | 每级 `queue_depth`（载字节级深度即内存上界） |
| 失败 | 异常上抛 | `catch` 白名单=认缺计数；白名单外经 watchdog 终止整链 |
| 中断 | — | Ctrl-C → `on_drain` 收尾钩子（同步落盘放最前） |

混用约束：计划中含 `map_async` 时只能 `run_stream`；streaming 计划只接受
`map_async` 与 `filter`（折叠），其余 sync 算子显式拒绝。

## 独立化沿革

原为 Demiurge 的 `demiurge.demiflow` 子包；独立化时内联了两个共享模块
（`demiflow/_compat/error_transport|observability`），补写了原仓快照缺失的
`planning/__init__.py`，斩断了 Candidate/wheelhouse 平台耦合（零配置入口
`demiflow.standalone.local_data`）。工程细节见各模块 docstring。


## 同一提示词契约：同步与异步执行

`map_prompt`走惰性执行；`map_prompt_async`使用框架原生actor与流式队列，是真正的异步HTTP请求。二者共用`demiflow_prompt_pack_v2`，包括模板（文字/JSON/图片）、模型、response_schema、schema_retries、inputs和output/outputs映射。业务不需要再写模型调用actor。

```python
from demiflow.standalone import local_data
from demiflow.operator_llm.parser import load_prompt_pack

# knowledge.yaml中定义extract，变量为materials，响应必填属性为facts。
data = local_data(
    prompt_packs={"knowledge.yaml": load_prompt_pack("knowledge.yaml")},
    max_prompt_requests=100,  # 同一context累计预算，同步/异步及schema重试共用
)
materials = data.from_items([{"concept": "示例", "passages": ["来源正文"]}])
knowledge = materials.map_prompt_async(
    "extract", config="knowledge.yaml",
    inputs={"materials": "passages"},
    outputs={"facts": "facts"},
    concurrency=4, queue_depth=8,
)
# checkpoint执行流式链，并返回可读取、可继续join的Dataset。
# version须由应用冻结输入、提示词、模型及代码；配置变化使用新版本/目录。
saved = knowledge.checkpoint("run/knowledge.jsonl", version="frozen-run-v1")
print(saved.take(3))
print(data.prompt_usage())
```

- `.map_prompt_async(...).map_async(ReviewActor()).run_stream()`也可直接串联，接收原生concurrency/queue_depth/catch/label；输入输出字段约定与map_prompt一致。catch默认为空，错误中止；显式白名单可作为认缺统计。
- 异步客户端在执行时创建，在**同一事件循环**中关闭。取消或失败会释放请求预算预留的在飞状态；已发生请求仍计入累计预算，不退款。
- 同步/异步transport均不隐藏HTTP重试（同步客户端原来的默认重试已取消）；网络/HTTP失败直接上报。schema_retries仅允许配置声明的0或1次结构修复，修复请求同样占预算。
- 输入映射必须覆盖全部模板变量，output用于单个必填响应属性，outputs覆盖全部必填响应属性；保留原行其他字段。模板含图片时沿用现有image占位符，不另造多模态协议。
- 当前异步提示词支持local流式执行器；未实现Ray提示词流式调度，其他执行器显式拒绝。join/reduce_by_key等仍须先checkpoint。
- prompt_usage/请求预算是当前context的内存统计，不是跨进程持久调用账本。checkpoint可复用完整输出；中断的部分输出不能保证外部模型调用exactly-once。需要完整请求响应归档及不确定调用阻塞的业务，迁移前仍需单独接入这些能力。
- 安装`demiflow[llm]`包含requests（同步）与httpx（异步）。现有map_prompt配置和用法继续可用。
