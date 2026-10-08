# AGENTS.md 中 Pipeline 条文的历史来源

2026-09-29：按用户要求从 AGENTS.md 抽出通用 pipeline 条文，合并为项目强制规范。

**本文件只保留历史原文，不是执行规范，不恢复旧指令、运行授权或已废弃方案。**
现行要求唯一见 [项目 Pipeline 强制规范](../../PIPELINE_SPEC.md)，强制入口见 [AGENTS.md 第一条](../../AGENTS.md)。
下方条文按原出现顺序保留，包含各轮相互覆盖的决策；原相对路径以当时仓库根为基准。
具体 pipeline 的业务协议、实验记录和数据约束仍保留在 AGENTS.md 或所属模块。

### Pipeline 必须管理自己的边界（2026-09-28，用户明确要求）

本条适用于所有现役 pipeline，并覆盖下方允许 preparation 共用混杂的顶层算子、提示词目录及保留额外入口的旧约定。

- **职责与数据边界明确**：每个 pipeline 说明输入表及固定版本、处理范围、行粒度、输出表、主键、写入模式及自己负责的列。同一目标表可以由多个 pipeline 更新，但各自只能更新所属字段；共享关联字段必须明确合并规则，不能覆盖其他生产者的数据。公共 preparation 决定本次更新范围，下游 pipeline 自行决定选样。
- **目录随职责一起拆分**：入口、配置、notebook、operaters、prompts、tests、README 都归所属 pipeline。拆分不能只移动入口，再把不同流程的实现、提示词和配置混在父级公共目录；不属于任何现役 pipeline、没有实际调用的代码和配置删除，不为旧路径保留转发层。
- **复用必须有归属**：跨 pipeline 复用的 schema、字段约定、算子或子图由明确的生产者维护，消费者显式导入并说明用途；不能借复用把无关阶段、模型调用或多方写入耦合成一个流程。通用执行与存储能力归 demiflow，不在业务目录再造公共写入框架。
- **入口与提示词可审查**：每条 pipeline 只有一个正式执行入口，CLI 和 notebook 调用它；实际复用的具名子图须有真实调用方和明确输入输出。提示词正文、版本及响应协议归调用它的 pipeline，移动目录不表示新增内容已获用户审核，不保留未使用的旧 prompt。
- **变更逐项核对**：同步实际调用方、源码冻结清单、notebook、测试和文档，并验证读写、字段保留、模型粒度、失败与续跑边界。删除代码不等于删除历史数据、冻结结果或正在运行任务依赖的文件；运行中的任务与新实现须安全交接。

### Notebook 与后台提交参数同步（2026-09-27，用户明确要求）

- 用户要求每次后台提交都是对应 notebook 的同款运行，回看 notebook 代码即可核对实际参数。Notebook 配置格作为实验参数来源；运行名、固定输入版本、复用来源、模型/推理参数、预算/并发/超时、抽样策略及输出位置变更时，先同步 notebook，再从其配置生成 CONFIG 提交唯一正式 pipeline。
- 提交前逐字段核对实际 CONFIG 与 notebook 一致，不在后台启动脚本中单独覆盖参数；使用同一正式 pipeline 和 prompt。后台运行授权沿用当前任务约定，此条不额外要求确认，也不自动授权新增实验或下游流程。
- 保留 notebook 已保存输出；后台提交本身不伪造 notebook 执行结果。运行中编辑文件不表示已改变现有进程的配置，下一次提交仍先同步并核对。

### 模型服务下沉与数据流 API（2026-09-27，用户要求）

- 用户明确要求将本地模型启动、就绪检查、释放和异常清理下沉 demiflow，并按现有“数据流 + 数据流水线算子”风格设计 API。本条覆盖前述业务端 `operaters/model_service.py` 管服务的临时实现。
- 正式主线仍为 `data.read_* → map/filter/join 等算子 → map_prompt_async → materialize → writer`；模型节点通过 `service=VLLMService(config, root=..., log_path=..., log=...)` 声明资源。业务只决定模型、GPU、DP/TP、容量、超时及阶段边界；通用进程管理在 `demiflow.services`，沿原生 actor `aclose` 收尾，不新增业务调度层、启动/停止数据行或隐藏模型调用。
- 模型名/端点继续由 prompt pack 绑定；服务配置经 pipeline 的 config 校验，实验值集中 notebook。`service=None` 沿用外部服务，offline 不绑定托管服务；当前托管能力只支持 Local 的异步模型算子。声明不启动进程；首个新请求才加载，同节点并发共用，空输入/跳过/全部复用不加载。服务配置不进入请求身份；完整响应与原生日志复用规则保持。
- 模型资源生命周期到整个流动作退出。复用同组 GPU 的两模型以现有 `materialize()` 分段，前段收尾后执行后段；不假设同一异步链的节点顺序独占 GPU。启动/就绪失败终止节点，不能混成逐行业务失败；端口/GPU 锁冲突不接管未知进程。强杀宿主不保证清理独立子进程。
- 原图 pipeline 和源 notebook 已同步新接口；正在运行的后台内核仍使用启动时载入的旧实现，不为迁移主动中断。新提交直接执行更新后的同一 notebook，不另设隐藏配置。

### Pipeline 标杆：T2I V2（2026-09-26，用户确认）

以 [T2I V2 正式入口](benchmark/t2i/v2/t2i_v2_benchmark_pipeline.py) 为现役 pipeline 编排标杆；[模块说明](benchmark/t2i/v2/README.md) 解释运行和调试方式。下方“Pipeline 编排必须直接表达具体数据流”给出对应代码示例，覆盖其后历史示例中的多题参数、文章配图关联和写死并发。

提炼后的通用原则：

1. **配置直达使用处。** 一个 `run_pipeline(config)` 入口，来源 URI/版本、运行位置、目标表/写模式、概念、预算、并发和模型参数统一配置。reader、writer 和模型节点直接取用；不先打包 manifest 再拆回，不扫描源码冻结运行。
   **所有可调参数经 config 传入，未传时采用默认值（2026-09-27，用户补充）。** 用户要求后续参数不再写死于 pipeline 执行逻辑中：模型与采样、thinking、输入/输出预算、图片预处理、并发、数量、过滤/剪枝阈值和阶段开关等，均由 `config(...)` 声明默认值并校验，调用方显式传值优先。`run_pipeline` 与算子直接消费已解析的配置，不在内部另设实验值或二次覆盖。Notebook 集中填写本次实验参数，CLI 提供相应覆盖入口；新增或修改参数时同步说明单位、默认行为和超限处理。固定表结构与响应合法性约束仍由业务契约定义，不为参数化新增通用策略框架。
2. **主线能看见数据关系。** 不同来源分别具名，明确每行粒度、列投影、过滤、展开、聚合和 join 键。文章一行一篇，图片一行一个 SHA，展开后按概念聚合，模型节点一行一个概念。多个来源不能藏进分派循环或阶段函数。
3. **清洗由生产者负责。** preparation 交付正文、公开可用状态和图片存储引用；下游信任这些契约，保留本业务需要的数量/上下文策略和实际读图校验。不重复清洗、不绕过引用另找默认原始表。未来的排序、去重、匹配策略须有明确需求。
4. **复杂行处理进算子，跨行关系留主线。** 简单投影内联，复杂请求构造和响应检查放 `operaters/`。算子不另读整表、不执行子 Dataset、不调用模型或写业务表；图片算子按本行已绑定的 Blob 引用读取字节。
5. **按任务范围组织可选材料。** T2I 用配置概念 left join 图文；缺材料就是空列表，由 prompt 说明，不人为加缺失状态或补齐流程。其他业务是否允许空材料依其自身契约，不能机械照搬 T2I。
6. **按真实执行边界落表。** 输入、设计、候选各有明确 schema 和 writer；同步链直接写，异步模型链 `materialize().write_lance(...)`。不 `take_all → Python 加工 → from_items`，不以物化冒充边生成边提交。并发由模型节点的 config 控制，每条记录仍独立请求。
7. **运行记录解决实际问题。** 同名运行按当前配置重新读数，原生调用日志复用相同请求；写模式和 append 提交去重在输出处可见。不增加源码/配置冻结、层层引用转换或额外调度框架。
8. **调试跟随实际调用。** Python 为唯一正式流程入口，notebook 调用它并在运行后只读预览。模型原始响应保留在原生日志；模型节点把服务实际返回的 reasoning 沿当前行传给 writer，在设计表与最终候选表保存 nullable reasoning 列，便于直接查表调试。它是调试字段，不纳入模型题目 schema、题目 ID 或后续 prompt。设计行另保存 response_ref，可按引用查看耗时、usage、finish_reason 和完整响应；call_json 不重复保存推理正文。未返回 reasoning 时存 null，失败/截断响应已有的 reasoning 仍保留在设计表。

9. **实验策略留在调用方。** taxonomy 选择、过滤阈值、数量上限等实验值集中在 notebook 配置格；正式 pipeline 接收并执行配置。保持直接参数和可见的数据流，不因可配置性新增通用策略框架。

10. **注释覆盖入口、配置、输入输出和数据流（2026-09-27，用户补充）。** 正式入口及 notebook 必须说明从哪里调用、参数控制什么、读入和交付什么；按实际阶段写明“读哪些数据 → 做哪些处理 → 写到哪里”，包括行粒度、关联键和模型调用粒度。具体要求见下方“代码注释规范”，不能仅在 README 中说明。

11. **必要进度日志是标准入口的一部分（2026-09-27，用户要求）。** 用户无法判断长时间执行的 notebook 是否仍在推进，因此新建或修改 pipeline 时，必须在运行/阶段开始、可观测的处理进展、阶段落表完成和结束/失败处打印必要日志并及时 flush。开始日志说明阶段、输入规模、模型/并发及输出位置；长阶段按 config 中的条数或时间间隔汇总已处理/总数（未知总数须明确）、成功/失败/待处理、真实响应复用数及耗时，不只在全部完成后输出。时间间隔由完成行触发时须写明无新行不会产生心跳，不能把它描述成独立定时监控。模型业务 keep/hold/reject 与技术失败分开，错误包含可定位对象和简短原因，完整详情留原生日志/结果表。落表前的行处理完成不能冒称已提交；全局与 worker 局部计数区分，不用预取数冒充已发送请求。日志频率经 config 配置、notebook 可覆盖；日志不改请求身份、不增加模型调用或业务表，不为日志轮询整库或引入调度框架。中断与续跑说明需区分完整响应复用、未完成请求和阶段结果重建，不能仅凭相同版本名承诺全部复用。

评审时先问：只看入口及其注释，能否指出“从哪里调用、如何配置、输入输出是什么、读什么、每行是什么、如何连接、何时调用模型、写到哪里”？再检查是否存在下游重复清洗、无实际需求的抽象和隐藏 I/O。

### Dataset 入口与模型算子配置（2026-09-26，用户确认）

- 统一使用 `from demiflow import data`，直接 `data.read_*` / `data.from_*` 创建 Dataset，再连接数据处理与写出方法。`Dataset` 只表示数据集类型，业务代码不构造 `DataAPI()`。删除独立 `standalone.local_data` 入口，不增加旧名兼容、二次工厂或全局模型配置。
- 执行器选择留在平台：普通脚本默认 Local，打包 Pipeline 使用 Driver 已选执行器。不把整个 `data` 模块作为参数逐层传递，不再创建 `prompt_data`、`review_data` 等只承载模型配置的入口对象。复杂行算子仍只收实际使用的配置。
- 模型配置直接写在 `map_prompt` / `map_prompt_async` 节点：`config=pack` 或真实 YAML 路径，`options=options`，`max_requests=N`。固定内容仍在 YAML；动态参数及 prompt/schema 在调用处绑定，不能经过数据行透传或先注册别名再查找。
- 请求上限按**一个已声明的算子节点**累计，覆盖其所有行、并发 worker 及 schema 重试。同一节点重复执行继续累计；重新声明节点有独立计数。日志复用与 offline 不计新请求。日志负责保存与复用请求，不再用整张调用日志的行数强加多个节点共用的上限。
- 同步和异步模型节点使用相同的配置参数语义。监控可以汇总调用量，但不得因此合并预算。业务配置的 `max_calls` 传给哪个节点，就限制哪个节点；不能继续把它描述为整个 pipeline 的总上限。
- `data` 模块及 reader 不携带模型业务设置；Local/Ray 执行器仅执行节点已声明的配置。复杂单行算子、图文关联与写表继续遵循下方数据流规则。

```python
from demiflow import data
(
    data.read_lance(input_uri, version=input_version)
    .map(prepare_request, fn_kwargs=request_options)
    .map_prompt_async(
        'design_question', config=pack, options=options, max_requests=max_calls,
        inputs={'payload': 'prompt_payload', 'images': 'prompt_images'},
        output='design_result', concurrency=1,
    )
    .map(check_response)
    .materialize()
    .write_lance(target_uri, mode=write_mode, schema=DESIGNS)
)
```



### Pipeline 编排必须直接表达具体数据流（2026-09-26，用户再次明确）

**Pipeline 是“数据流 + 数据流上的处理算子流水线”。编排层直接描述具体业务数据如何流动，不负责抽象业务流程。** 阅读主线应能看到：读哪张表、一行是什么、筛什么、按什么键关联／聚合、哪个节点调用模型、写到哪里。复杂计算由算子完成，但不能把数据流藏进过程函数。

本节优先于下方关于内联、函数封装及控制结构的旧约定。

1. **按具体数据流分别写，然后显式连接。** 文章有文章的 `read → 处理`，图片有图片的 `read → 处理`，再用 `join(on='concept')` 连接。顺序本身就是结构，不要把固定的两类来源抽象成 `for kind, sources in ...`，在循环中 `if kind` 分派处理，再用 `streams[0]`、`streams[1]` 取回。不要为少写几行而隐藏不同数据流的含义。先核对实际表及业务关系，不把旧接口支持的泛化能力自动当作当前需求，也不反过来要求用户为实现方式选择输入需求。
2. **算子链表达行处理与复杂处理。** 筛选用 `filter`，展开用 `flat_map`，关联用 `join`，聚合用 `reduce_by_key`，模型调用用 `map_prompt_async`，最终接 writer。简单表达式就地写；复杂 fn/actor 放入 `operaters/`，输入输出明确。算子可以处理当前行的嵌套结构或当前分组的累积值，不能另读整表、执行 Dataset 或封装整个阶段。关联键、聚合键和数据流连接留在主线。
3. **中间变量表示数据流，不是过程步骤。** 两条待关联的数据流、真实分支、复用结果和持久阶段边界可以命名。单用的 `requests → responses → results` 不应把一条模型处理链割裂。不能为了形式上的“一条链”取消真正的数据分支或已有续跑表。
4. **外层控制结构必须有真实原因。** 实际业务读取多张同类表时，分别写出具名数据流，再显式 union；不能为了可能出现的多来源，先构造 reader 集合、首表特殊处理或循环追加框架。根据上一次结果决定下一次尝试的反馈循环可以保留。固定业务类型不需要调度循环；行上的条件放入算子。配置、锁和续跑分支控制是否执行数据流，不接管逐行处理。不能把代码组织方便当作增加循环／分支的理由。
5. **外部数据从 reader 进入，沿 Dataset 写出。** 禁止 `take_all → Python 处理 → from_items`，禁止 `from_items([]).union(...)` 空种子，也不能改用 `from_iter` 掩盖相同问题。`from_items` 只用于真正已有的内存输入，如配置列表、测试行或新产生的尝试控制记录。多个实际需要的同类数据流用 union 连接；可选来源为空时明确保留空分支。
6. **执行边界使用标准 API。** 同步链直接 `write_lance`；local 异步链可显式 `materialize().write_lance(...)`，不再用外部 `tables.append` 收集结果。为原有提交指纹读取结果时，先固定 Dataset，明细仍由它写出。物化缓存不能替代持久阶段表，不能宣称物化后写表是边生成边提交。
7. **算子配置在算子调用处声明。** `data.read_*` / `data.from_*` 是统一的数据入口，返回 Dataset；不接收 prompt、调用参数或模型请求预算。`map_prompt` / `map_prompt_async` 直接接收 `config`（PromptPack 或真实 YAML 路径）、`options` 和 `max_requests`，不先注册配置别名。用户已授权本次统一接口；其他 API 缺口仍须先说明并获确认。

**标杆示例：T2I V2 的独立图文 → 每概念请求 → 写表。**

下例摘自正式入口，省略环境初始化、日志和候选提交去重；`config` 是入口参数，`root`、`pack`、`options`、schema 和算子由正式模块提供。可选来源保持显式空分支；同概念允许多篇文章，任务范围由 config.concepts 决定。

```python
from demiflow import data

quoted_concepts = ["'" + c.replace("'", "''") + "'" for c in config['concepts']]

# 文章每行是一篇已审核文章；同概念的多篇文章均作为材料，不去重或判冲突。
# preparation 负责正文清洗；这里仅展开主题正文，忽略引用、配图和内部审核字段。
if config['article_source'] is not None:
    texts = (
        data.read_lance(
            str(root / config['article_source']['uri']),
            version=config['article_source']['version'], columns=['concept', 'content'],
            filter="review_status = 'reviewed' AND concept IN (" + ','.join(quoted_concepts) + ')',
        )
        .flat_map(lambda row: [
            {'concept': row['concept'], 'text': {
                'kind': 'text', 'title': topic['title'], 'text': paragraph,
            }}
            for topic in row['content'] or [] for paragraph in topic['content']['paragraphs'] or []
        ])
        .reduce_by_key('concept', lambda acc, row: {
            'concept': row['concept'], 'texts': (acc['texts'] if acc else []) + [row['text']],
        })
    )
else:
    texts = data.from_arrow(pa.table({'concept': pa.array([], type=pa.string())}))

# 图片每行包含多个概念关系；仅按公开发布/审核状态选取本次概念的图片。
# 使用 preparation 已交付的存储引用，不读取来源或审核 JSON，不与文章匹配。
if config['visual_source'] is not None:
    images = (
        data.read_lance(
            str(root / config['visual_source']['uri']),
            version=config['visual_source']['version'],
            columns=['sha256', 'source_refs', 'concept_assessments'],
            filter=' OR '.join('array_contains(published_concepts, ' + c + ')' for c in quoted_concepts),
        )
        .flat_map(lambda row: [
            {'concept': assessment['concept'], 'sha256': row['sha256'],
             'source_refs': row['source_refs']}
            for assessment in row['concept_assessments'] or []
            if assessment['concept'] in config['concepts']
            and assessment['published'] and assessment['review_status'] == 'keep'
        ])
        # 在聚合中限制图片数量，不为未选中的图片读取字节或解析引用。
        .reduce_by_key('concept', lambda acc, row: {
            'concept': row['concept'],
            'images': ((acc['images'] if acc else []) + [row])[:config['max_reference_images']],
        })
        .map(lambda row: {
            'concept': row['concept'],
            'images': [
                {'kind': 'image', 'blob_ref': image_blob_ref(im)} for im in row['images']
            ],
        })
    )
else:
    images = data.from_arrow(pa.table({'concept': pa.array([], type=pa.string())}))


# 真正的内存输入是配置概念；按任务范围关联，缺少材料的概念也保留。
(
    data.from_items([{'concept': c} for c in config['concepts']])
    .join(texts, on='concept', how='left')
    .join(images, on='concept', how='left')
    .map(lambda row: {
        'concept': row['concept'], 'status': 'ready', 'reason': '',
        'references_json': json.dumps([
            {'number': i, **ref}
            for i, ref in enumerate(row.get('texts', []) + row.get('images', []), 1)
        ], ensure_ascii=False),
    })
    .write_lance(input_uri, mode='overwrite', schema=INPUTS)
)
input_version = lance.dataset(input_uri).version

# 输入表是持久边界；单概念请求构造、模型调用、响应检查保持连续算子链。
(
    data.read_lance(input_uri, version=input_version)
    .map(prepare_request, fn_kwargs={
        'max_context_chars': config['max_context_chars'],
        'prompt_chars': len(pack.prompt_definitions['design_question'].template.source),
    })
    .map_prompt_async(
        'design_question', config=pack, options=options,
        max_requests=config['max_calls'],
        inputs={'payload': 'prompt_payload', 'images': 'prompt_images'},
        output='design_result', call_output='design_call', error_output='design_error',
        when=lambda row: row['status'] == 'ready',
        concurrency=config['concurrency'], queue_depth=config['queue_depth'],
    )
    .map(check_response, fn_kwargs={
        'question_schema': pack.prompt_definitions['design_question'].response_schema[
            'properties']['result']['properties']['question'],
    })
    .map(lambda row: {name: row[name] for name in DESIGNS.names})
    .materialize()
    .write_lance(designs_uri, mode='overwrite', schema=DESIGNS)
)
```

`prepare_request` 只处理当前概念的材料和预算，并按已绑定引用读图；`check_response` 只检查当前响应。两者不决定关联、不执行 Dataset、不循环调用模型。后续从设计表筛选 candidate 并投影到题表，writer 使用 config 的目标和模式；完整实现见标杆入口。

**评审检查：遮住算子内部实现，只看 pipeline，能否画出具体来源、处理节点、关联关系和输出？** 若必须进入某个循环、分派器、`process_materials()` 或 `run_stage()` 才知道读取了什么、关联了什么，编排仍不合格。无需把所有复杂逻辑展开，但数据流及处理职责必须在主线可见。

附：通用 evaluation 保留源表 `number`，缺省使用已有 `task_id`；不为展示顺序将 Dataset 转成迭代器重新编号。


### Dataset 数据流编排与行算子边界（2026-09-26，用户进一步纠正）

修订理由：用户指出仅使用 API 还不够，pipeline 应以连续的 Dataset 变换表达数据流，而不是由外层逐步取数、执行、再拼回数据集。用户明确允许定义函数算子供 `map` 等调用。本条修正下方将所有行逻辑强制内联、只允许图片函数的过度限制；原有业务协议与平台扩展需先确认的规则不变。

- **以数据依赖编排，而非以过程变量分段**：线性的读、行变换、模型调用、响应检查和写入用连续 API 链表达。中间 Dataset 变量用于真实分支、关联或复用；变量赋值本身不是执行边界，也不要求把有分支的图硬写成一条超长链。
- **具名函数是算子，不是子 pipeline**：较复杂的单行请求构造、检查、解析可以写成有明确输入/输出的函数，交给 `map`/`flat_map` 执行；简单字段表达式可内联。函数不能另读整表、创建并执行 Dataset、循环调模型或包住整个业务阶段。模型调用直接使用 `map_prompt_async`；join/reduce/union 等跨行关系仍在主线可见。
- **数据条件在数据流中处理**：行检查、状态转换、筛选、展开放入算子，不先 `take_all`/逐行循环再实现。应报错的行检查可以在 `map` 函数中抛出；不为了抛错先把匹配行取回外层。算子内部处理本行嵌套列表不等于外层遍历数据集。
- **控制逻辑只控制数据流**：配置、锁、固定版本、续跑选择、输出提交和确实必要的反馈循环可以在外层；每次迭代仍构建并执行 Dataset 链，不能用外层循环代替逐行处理和模型调度。
- **明确 action 和物化用途**：链式调用定义变换，`write_lance`/`run_stream` 等终结动作触发执行；`materialize` 同样是执行边界，只有分支复用或固定结果时才用。批式拉取和异步流式都采用数据流表达，但各后端的实际 API 支持以平台实现为准；不因链式写法就宣称全流程流式。
- **保持 Dataset 到 writer 的连接**：不能 `take_all()` 后仅为落表再 `from_items()`。确需为原有提交指纹或小规模运行汇总取回结果时，注明范围和用途，明细仍由同一个 Dataset 写入。已有输入快照/设计结果表如承担续跑和审计职责，可以作为显式边界，不为了视觉上的“一条链”删除。
- **日志随真实执行输出**：行日志放在实际算子执行位置；请求开始须对应原生调用开始，不能把上游预取当成请求发送。日志不改变行内容、输入协议和调用次数；不借日志增加重试、轮询或新的调度包装。

T2I V2 的模型链示例（省略具体配置、日志和字段投影，保留标准调用关系）：

```python
(
    data.read_lance(input_uri, version=input_version)
    .map(prepare_request, fn_kwargs=request_options)
    .map_prompt_async(
        'design_question', config=pack, options=options, max_requests=max_calls,
        inputs={'payload': 'prompt_payload', 'images': 'prompt_images'},
        output='design_result', call_output='design_call', error_output='design_error',
        when=lambda row: row['status'] == 'ready',
        concurrency=config['concurrency'], queue_depth=config['queue_depth'],
    )
    .map(check_response, fn_kwargs={'question_schema': question_schema})
    .map(lambda row: {name: row[name] for name in DESIGNS.names})
    .materialize()
    .write_lance(designs_uri, mode='overwrite', schema=DESIGNS)
)
```


### Prompt 书写与配置规范（2026-09-27，用户要求补充最小输入与单次标注）

修订理由：T2I V2 将 MD 正文经 Python 变量、数据行字段、`inputs` 映射传入 YAML，YAML 只剩占位符；正文与 response_schema 分散维护，导致已删除的 criteria 仍被 schema 强制要求。用户要求合入 YAML，并推广到已经开发的 pipeline。

- **只给当前判断所需的信息**：逐项检查输入和说明能否帮助完成当前任务。采集来源、上游分类/筛选理由、处理历史、平台机制、后续流程等，无明确用途就不传；数据表需要保留不等于模型需要看到。不要先把干扰信息塞给模型，再用“可能错绑、不要受影响、仅供参考”等提醒补救。必要的任务约束仍须写清，不能为缩短文本删去真正影响判断的依据。
- **确定性工作交给业务代码**：等级到状态的固定映射、已有字段的复制/去重/汇总、格式转换等不交给模型重复输出。模型只负责需要判断或生成的原始字段，业务算子校验后派生下游字段；保留原始模型响应，明确落表和展示中哪些字段为派生。删冗余字段时同时删正文要求、响应 schema 和无意义的一致性校验。
- **按标注对象组织结构，信息只标一次**：把整图、区域、对象候选及其关系按归属放在一起；caption、标签和描述也按其所属层级定义，不另设一节让模型复制前文。逐字段说明“含义、标注什么、当前事实还是新增建议、无依据时如何填写”，明确 evidence 与 requirement 的区别。检索索引可从原始标注派生，不让模型再复述一份；必要示例服务于字段含义，不变成所有输入套用的清单。
- **概念和 taxonomy 按任务选择**：分类判断、概念核心出题需要相应概念/分类上下文；只按像素标注通用场景用途时不输入采集概念或 taxonomy。未来任务确实需要身份或目标约束，再明确加入对应输入，不因字段现成或“可能有用”就默认传入。
- **固定规则放在使用它的 YAML `template: |` 中**：同一 prompt 的正文、版本、模型配置、`response_schema` 放在同一份标准 prompt pack。不要通过 `read_text()` → `instructions` → `prompt_instructions` → `{{ instructions }}` 原样转递固定正文；不在每行数据中复制运行级配置，不维护同内容的 MD 镜像。
- **模板参数只表达实际变化的输入**：如概念、材料、题面、图片。已有字段能直接绑定就直接绑定，不为套模板改名、转 JSON 再解析或增加转调函数。字段映射、图片编码、实际载荷构造和来源绑定有明确作用时保留；不以“少变量”为由把真实的数据边界混在一起。
- **不把动态选择当成冗余传递**：不同赛道／编辑类型对应不同规则，保留必要选择；注明按什么字段选择、传入哪部分内容。仅删除共同固定正文的逐行透传，不为消除一个变量复制整套流程，也不把所有类型的规则同时塞给模型。已有历史协议与冻结回放文件不按扩展名批量删除。
- **正文与结构契约同时修改**：`response_schema` 是机器校验的结构定义；正文说明字段含义并给必要示例，两者必须一致。变更输出同步更新 Arrow schema、响应展开／检查、notebook 展示、测试和 README。不要以兼容为由强制输出已废弃字段。平台会把 schema 提供给模型，但这不能代替字段业务含义的说明；正文中的格式示例仍须与 schema 一致。
- **删除中间层要核对它承担的约束**：固定正文移入 YAML 后，原有上下文预算仍需计入正文；源码冻结和版本摘要改读 YAML；原始数据中的花括号不得被当成二次模板处理。更新 prompt 版本，新协议使用新运行名；结构不兼容时写新目标表，或由用户明确选择覆盖，不自动迁移历史数据。
- **以实际请求和结果验证**：隔离数据、模拟响应下检查模型仍收到完整规则及本行输入；检查新响应能通过 schema 并正确落表、废弃结构被拒绝、续跑不重复调用。不要只测试模板文件存在或字符串替换成功。发现标准 API 缺口仍按上方规范先确认，不为简化业务透传自行扩平台。

示例：固定出题规则直接写入 `tasks.yaml`，每次仅绑定当前概念。以下是最小完整示意；业务中的图片、材料和输出结构按实际需要定义。

```yaml
schema_version: demiflow_prompt_pack_v2
prompts:
  design:
    version: concept-question-1
    model:
      name: glm/glm-5.3-flash
      transport: openai_compatible
      base_url: http://127.0.0.1:4001/v1
      api_key_env: MODELHUB_API_KEY
    schema_retries: 0
    template: |
      围绕概念的核心内容设计一道图像生成题，题面应独立明确。
      当前概念：{{ concept }}
      返回 JSON：
      {
        "result": {
          "instruction": "题面"
        }
      }
    response_schema:
      type: object
      required: [result]
      additionalProperties: false
      properties:
        result:
          type: object
          required: [instruction]
          additionalProperties: false
          properties:
            instruction: {type: string, minLength: 1}
```

```python
# 模型算子直接读取 tasks.yaml：每概念一行、一次请求；固定正文不进入数据列。
questions = concepts.map_prompt_async(
    'design', config='prompts/tasks.yaml', inputs={'concept': 'concept'}, output='question',
)
```

这里需要的链路是“数据字段 → 标准模板 → 模型”，不需要“读 MD → 变量 → 新增固定字段 → inputs 改名 → YAML 占位符”。


### Pipeline 主线与平台边界（2026-09-26，用户明确要求）

修订理由：T2I V2 将运行记录、材料处理、模型请求和落表挤在同一段代码中，导致读者无法判断处理粒度。用户要求按数据主线整理、使用标准 API，并授权在 demiflow 中增加通用覆盖写入能力。本条补充下方标准读写及代码注释规范。用户随后要求将行、字段的数据操作直接展开为 SQL／Dataset API，禁止未经确认以黑盒函数绕过 API 缺口；以下规则优先于下方旧的“业务转换归算子”约定。

- **按数据流组织正式入口**：主线直接呈现“读数据 → 行／字段变换 → 必要的模型调用 → 校验/展开 → 写数据”。按业务阶段命名 Dataset 变量；步骤不适用就省略，不为套模板增加阶段。不能仅把混杂代码改成若干隐藏读写与执行的函数。
- **数据操作优先 SQL／Dataset API**：筛选、投影、字段解析与检查、展开、关联、聚合、排序、拼接和编号直接用现有标准 API 表达，如 `read_lance(filter=..., columns=...)`、`filter`、`map`、`flat_map`、`join`、`reduce_by_key`、`sort`、`union`。主线直接显示输入字段、关联键、过滤条件、聚合及排序规则；简单字段表达式可写在 API 调用处。仅在 `map`／`from_iter` 外套一层、内部仍用 Python 字典或循环完成整段关联与材料处理，不算遵循本规范。
- **让处理粒度可见**：在模型调用处说明一行代表什么、一次请求覆盖什么、是否合并/拆分输入及请求并发数。区分模型请求批次与返回结果的写表批次；不能把 `batch_map` 的落表批大小描述成模型一次接收的任务数量。预算及超限行为由业务显式配置，不隐式截断、拆题、重试或切换模型。
- **不以封装隐藏数据逻辑**：材料选择、编号、缺失输入处理等规则在正式 pipeline 的 SQL／Dataset 链中可见，不因它们属于业务规则就自动移入 `operaters/`。模型传输、图片解码等非关系操作与行、字段变换分开；已有专用函数不夹带过滤、关联或聚合规则。运行配置及源码／版本冻结单独处理，不隐藏业务表 reader、writer 或执行入口。
- **不为目录拆算子**：用户进一步明确，不能为了保留 `operaters/` 的文件划分，把普通配置、字段拼接、检查、候选展开包装成独立模块。小流程的模型配置、输出表结构直接放在正式 pipeline；行、字段操作直接写在 Dataset 链中。只有图片读取／编码等必要的非关系操作保留小函数，职责不能扩成整个材料处理或出题流程；不另加转调层。
- **读写条件显式可见**：来源路径、固定版本、目标路径、Arrow schema 和写入模式在入口或参数块可见。同步落表优先 `Dataset.write_lance(..., mode='append'|'overwrite', schema=...)`；异步模型链仍由 `run_stream()` 执行，按标准批量算子连接官方 Lance writer。只为有实际复用或诊断用途的数据落中间表，不为每个算子自动建表。
- **区分汇总与流式执行**：不要为接入 Dataset 先无条件 `list()`/`take_all()` 全量加载。确需按概念组合或计算本批输出快照时，注明汇总对象、范围及用途；这类汇总不能宣称为全链路有界流式处理，也不能改变模型请求粒度。
- **API 缺口先确认，禁止自行绕过**：现有 demiflow API 无法表达必要操作时，先说明具体缺口、建议的通用 API 扩展、行为及影响范围，取得用户明确确认后才实现。若拟采用 SQL／Dataset API 之外的数据处理方案或黑盒算子，也必须在设计落地前说明原因和方案并获确认；不得先写业务绕行代码、包装后再补报。未获确认期间，只推进不依赖该方案的工作。此前某次平台扩展授权不自动适用于新的扩展。
- **不为非必要行为扩平台**：先判断顺序、容错等是否为真实业务要求，不能为了保留旧实现习惯增加排序字段或平台参数。本次 T2I 材料处理不保证配置中的概念展示顺序；只保留必需配图优先，最终列表编号后保存并复用。不扩展 local sort 或 `map(error_output=...)`。字段缺失、审核不通过等数据条件用 Dataset 显式检查；读取、解码及程序异常默认抛出，不自动转为材料不合格。确有“失败也是业务结果”的已确认需求时，用现有 `map(output=...)` 返回业务定义的结果值。已有模型调用日志及待响应机制不在本次改动范围。
- **平台只提供通用机制**：demiflow 负责 Dataset 执行、Arrow 类型、写入模式、版本冲突和提交回执，不接收概念、题目、业务表名、运行编号或审核状态。主键合并策略、业务去重、运行冻结与是否完成留在业务侧。平台缺口仍须先说明并获得授权；本次覆盖写入已获授权。
- **验证实际语义**：修改后验证调用粒度、材料对应关系、空结果、追加/覆盖及续跑行为。writer 成功后才登记已提交版本；复用结果不重复调用模型或追加同一快照。开发检查使用隔离数据与模拟响应，保留历史表及 notebook 已存输出。

### 已有 pipeline 的统一重写与注释（2026-09-26，用户确认推广）

修订理由：用户要求把 T2I V2 中收敛的原则应用到其他已经开发的 pipeline，并完善注释。适用于现役 preparation、训练、benchmark、evaluation 入口，包括仍可执行的版本入口；历史数据、源码归档和 notebook 已保存输出不重写。

- **先列数据契约，再写执行链**：读表处标明来源及固定版本、筛选列和条件；每段说明输入一行代表什么、输出一行代表什么。关联写清键和保留哪一侧，聚合写清分组键；多对多来源先按业务粒度聚合，避免产生重复条目。
- **字段操作就地表达**：重命名、投影、JSON 字段解析、列表展开、条件检查、拼接及编号直接放在标准 SQL／Dataset 调用处。已有 Dataset 继续连接下一步，不用 `from_iter(dataset.iter_rows)` 转回去重包；不新增来源别名、旧协议往返转换或纯转调函数。
- **每个操作说明具体条件与结果**：注释应如“按 task_id 展开模型配置，每题每模型一行”“只给 status=generated 且有图片的行打分”“丢弃缺少段落引用的文字并保留原因”。避免只写“整理、对齐、处理、校验”；不逐行复述语法，不用注释替代可见的条件表达式。
- **配置、数据变换与外部操作各有位置**：模型及 writer 参数在入口可见；表结构与输出字段邻近定义；模型加载、文件／Blob 字节读写、图像编码、协议解析等必要的非关系操作可以保留小函数。函数不能顺带包住一段筛选、关联、调用和写表。已有按结果决定下一次尝试的控制循环保留在正式入口，不能包装成隐藏执行完整流程的数据源。
- **保留行为，不添加业务规则**：本次整理保留题目协议、检查条件、模型配置、调用粒度、预算、错误分类和续跑提交边界。不为统一形式增加审题、去重、排序、容错、重试或中间表；哪些状态是业务结果、哪些异常应中止，必须按已确认的流程处理。
- **用原生 writer 收口**：同步 Dataset 直接 `write_lance(..., mode=..., schema=...)`，不手工展开 Arrow 批次再交给同一个 writer。异步链按平台现有支持组合执行；标准 `map` 可以显式投影字段，不为此扩平台。涉及 Lance Blob 编码等专用类型时使用对应官方 API。
- **平台缺口需先证明必要性并确认**：先尝试现有 SQL／Dataset API 的等价表达，再说明实际缺口；不能把旧实现的展示顺序或自动继续行为当作必需能力。任何平台扩展、非标准数据处理方案，仍须按上方要求事先获得用户确认。
- **执行上下文不能丢**：删除 Dataset→迭代器→Dataset 的重包时，核对 prompt pack、并发和调用预算仍属于实际执行的 Dataset。异步链之前的同步展开／关联若不能直接执行，用现有 `materialize()` 固定这一段后继续；不以 Python 生成器绕过执行器限制。只在必要边界物化，并说明其数据范围。
- **输出字段顺序和空值显式定义**：writer 前投影至目标 schema 的列顺序，缺省列明确补空；嵌套结构按既有 Arrow 契约构造。不能依赖 Python 字典或 JSON 解码后的顺序；可选中间字段删除用可处理缺失字段的投影，不假设每种状态都生成了该字段。
- **按行为验证和清理**：验证源版本、关联数量、模型输入、评分／审核结果、空结果、append／overwrite、失败不误报成功、续跑不重复调用。删除无引用封装及对应导入，更新 README 和必要的 notebook 导入；不保留转发兼容层，不为重构调用真实模型。

### 入口文件加业务前缀（2026-09-25，用户明确要求）

修订理由：用户同时打开多个 debug、pipeline 时无法区分归属，要求文件名都加前缀。本条覆盖下方固定使用 `pipeline.py` / `debug.ipynb` 的旧命名约定；目录分层与执行职责不变。

- 现役入口统一为 `<前缀>_pipeline.py`、`<前缀>_debug.ipynb`。基础处理用 `preparation`，训练用 `t2i_train` / `edit_train`，benchmark 用 `<赛道>_<版本>_benchmark`，分赛道评测用 `<赛道>_<版本>_eval`，通用评测用 `evaluation`。
- 改名同步 Python 导入、CLI、模型子进程入口、源码快照路径、notebook、文档与目录检查；直接使用新名称，不保留同名旧入口或转发包装。
- 归档源码、历史表和冻结引用不改写；notebook 的已保存输出与执行计数保持原样。


### 代码注释规范（2026-09-25 制定，2026-09-27 补充，用户明确要求）

修订理由：用户要求将代码注释纳入项目规范，并为本次新增的 T2I Benchmark V2 实现补充注释。
2026-09-26 补充理由：用户指出“整理材料、选择配图、固定编号、补齐缺失概念”等描述过于抽象，要求注释直接说明实际操作；本次补充适用于项目内所有 pipeline。
2026-09-27 补充理由：用户指出 Edit 原图 pipeline 注释不足，要求标准 pipeline 的代码注释明确覆盖入口、配置内容、输入/输出，以及“读 → 具体处理 → 写”的数据流。适用于项目内所有新增或修改的 pipeline。

- 新增或修改代码时同步维护中文注释与 docstring。模块说明职责；对外函数、业务算子说明输入、输出和关键约束，简单函数可用一句话表达。
- **入口必须可定位**：模块和正式入口的 docstring 说明如何调用，如 `config(...) → run_pipeline(config)`；有 CLI/notebook 时说明它们调用哪个入口、执行后返回什么、何处发生读写与模型调用。
- **配置必须可理解**：在配置定义和 notebook 参数块按用途说明来源 URI/版本、筛选范围、数量上限、模型、并发、请求预算、上下文/响应限制、阶段出口、目标表及写入模式。说明实际默认行为、参数覆盖关系和超限行为；配置继续直达使用处，不为注释增加封装。
- **输入/输出必须明确**：写清来源及阶段表的关键字段、每行代表什么、关联键，以及返回摘要与落表明细的区别。业务算子说明输入行/分组和输出字段或状态变化，读图等 I/O 及失败处理需明确。
- 在关键流程和非显然的分支处说明业务目的及选择原因，特别是材料选择、编号对应、版本冻结、模型调用、失败状态、续跑及落表完成边界。不要逐行翻译代码或重复变量名。
- **阶段注释体现完整数据流**：在实际读写边界前写清“读哪些表/字段及版本 → 按什么条件筛选、展开、关联、聚合或调用模型 → 写入哪张表及其行粒度”。注明相关配置项、关联/聚合键和每次模型调用覆盖的输入单位；分支说明去向。只写实际存在的操作，不为套模板增加阶段；读者不进入算子实现，也应能沿注释还原数据流，不能只标“输入/处理/输出”三个标题。
- **不能用目的代替动作**：“整理、处理、对齐、固定、补齐、保存供复用”等词后必须有具体说明。例如，选图说明优先选哪些图、其余图片如何排序及受哪个参数限制；编号说明对象、顺序和起始值；“补齐缺失概念”须明确是补一条状态行，还是实际补充了材料，不能混为一谈。
- **结果和分支要可核对**：涉及缺失、超限、失败、跳过或续跑时，说明触发条件、记录的字段/状态及后续动作；涉及落表时，说明每行代表什么、目标表/路径变量、写入模式，以及复用哪个已登记版本。按相邻操作分段注释，不把多种行为压成一句笼统概括。
- notebook 的参数块直接注明数据源的实际表路径、固定版本及筛选范围，不增加发布名或别名。模型调用和结果预览分段标注，说明手动执行的实际行为。
- 注释必须与实现一致；修改逻辑时同步更新或删除过时说明。不保留废弃代码作为注释，不为增加注释引入额外封装。

### Pipeline 数据按表平铺（2026-09-23，用户确认执行）

用户确认把业务表迁到所属 pipeline 的 `datasets/`（用户已更正拼写），不再建立 runs、history、阶段或 case 数据子目录。这条覆盖下方旧的仓库外集中数据根和 pipeline 固定目录约定。

- 每张 Lance 表直接放所属模块 `datasets/`；名称为业务表名，加运行名、题目编号或指纹消歧。不合并历史表，不改历史行、版本、索引和图片字节。
- collect 持有原始图片、文档和采集历史；preparation 持有文章、视觉审核和旧 knowledge 运行；curation/t2i、curation/edit 持有各自结果；混合赛道历史表归 benchmark/datasets、curation/datasets；评测归 evaluation 或 evaluation/edit/v1。
- 主数据、全局登记、分类历史、跨模块维护和迁移证据直接平铺工作区 datasets/。解析根改为共同工作区，默认 PROJECT_ROOT.parent；DEMIWTG_DATASETS_ROOT 同样表示包含 demiwtg/ 和 datasets/ 的工作区根。
- 历史固定 DatasetRef、RecordRef、BlobRef 原值不改；共同工作区 `_demiflow/lance_locations.json` 精确记录旧表位置到新物理位置的映射。为本次已授权的固定引用重定位，demiflow 标准存储内部支持该映射；不增加业务读写包装，不保留旧路径目录或软链接。现役 notebook 和 pipeline 直接写新实际表路径。
- `_demiflow` 仅为锁、位置映射、迁移回执等控制文件；所有 datasets/ 忽略 Git。源码快照排除数据目录。保留两仓已有改动，不提交，不调用模型。


本文档是**定死的架构约束**。任何代码修改、脚本新增、数据整理，都必须遵守。修改本文件本身就是一次架构决策，需要显式说明理由。

### Notebook 直接读表与运行编号（2026-09-23，用户再次明确纠正）

修订理由：用户认为 `RUN` 目录和 `TASK_ID` 混淆且增加读取成本，要求涉及的查看入口一起调整。此条覆盖下方 notebook 按 RUN/TASK_ID 查看约定。

- 查看直接写实际 Lance 表路径 `TABLE_URI`、固定 `VERSION` 和标准 `read_lance()`；不通过 `read_attempt`、`open_stage_dataset` 或元数据层隐藏要读的表。可用 `CONCEPT` 等业务字段筛选；单题对照从已读结果选择一行，无需手填内部任务编号。
- `RUN_ID` 唯一表示一次 pipeline/调试运行，不表示某个人或某一道题。只有需要组织运行路径时才使用；路径变量明确叫 `RUN_DIR` 或用途名称。一个运行可包含多道题，题目行的既有 `task_id` 关联主键不改成 run_id。
- 同步现役 notebook、说明及受影响测试；历史表、内部关联主键和历史模型调用输出不改写。不为更名新增读表 API 或兼容层。

### 所有现役 pipeline 显式使用标准读写 API（2026-09-23，用户再次明确纠正）

修订理由：用户指出出题流程把写表藏在 `files.lance_checkpoint` 等封装中，无法直接看清结果落点，要求涉及的 pipeline 全部改用标准 API。此条适用于 preparation、curation、benchmark、evaluation，覆盖下方旧 checkpoint 编排约定。

- 在 `pipeline.py` 中直接写出 Lance 表路径、Arrow schema、writer 和固定版本 reader。使用现有 `Dataset.read_lance` / `write_lance` 或 Lance/PyArrow 官方 API，不用 `files.lance_checkpoint`、`checkpoint_lance_args`、`Dataset.checkpoint_lance` 隐藏或代替写表。
- 不新增、扩展或包装标准 API，不通过继承、私有属性、通用 helper 或改名后的保存函数绕过本规则。确需扩展，先说明缺口、具体改动及替代办法，得到用户明确确认后再动平台；本次不修改 demiflow。
- `map_prompt_async` 使用现有 `run_stream()` 执行。批量结果用标准分批算子接官方 Lance writer；单 case 可收集该 case 的结果后用 `write_lance`。不因异步调用重新引入 checkpoint。空结果也用显式 schema 写出有效 Lance 空表。
- 业务输入绑定、schema/行转换、审核规则和运行元信息可以保留；运行绑定类不执行 Dataset、不隐藏结果表读写。完成记录只在 writer 成功后登记，续跑读取已登记的固定版本；未完成的表不能当成完成结果。
- 历史 Lance 引用、样本、模型响应和 notebook 输出不改写。测试使用隔离湖与模拟响应，不启动正式生产、训练或模型调用，保留两仓未提交改动。

### Debug notebook 只留短模板（2026-09-23，用户再次明确纠正）

修订理由：用户认为自动列阶段、查 manifest、取前几条和 pprint 没有意义，要求只给可改参数的模板，由用户手写算子编排。此条覆盖下方要求预写完整调试链或同输入对照 cell 的旧约定。

- 各 debug.ipynb 只留一段说明和三个代码格：参数（运行/题目 ID/配置文件）、按 ID 看题、导入业务算子供自己编排。基础处理按概念看材料；未实现的 Edit 不伪造读取或执行流程。
- 配置直接读取显式 JSON 文件并调用现有配置函数；不新增配置加载框架、调试模块、阶段选择器、异常回退或自动报告。查询例子默认注释，用户按需修改和运行。
- 不预铺逐阶段流程或模型对照执行代码。历史试跑输出移入对应 reviews/ 查看册，保持原输出和执行计数；debug 模板只放链接。
- 算子、正式 pipeline 与 prompts 不因 notebook 简化而改变。保留两仓未提交改动，不提交、不调用模型或启动生产/训练。

### 所有 pipeline 固定目录（2026-09-23，用户最新明确要求）

修订理由：用户要求严格限制每个 pipeline 使用同一模式，删除 `comparison_pipeline.py` 一类额外入口，并明确将 `ops` 改名为 **`operaters`**。以下约定覆盖本文件所有旧的 ops、native、visual_pipeline、runtime、notebook 执行图和查看器约定。

```text
<pipeline>/
  __init__.py
  <前缀>_pipeline.py # 正式流程、必要配置、CLI；notebook 调用同一个函数
  <前缀>_debug.ipynb  # 参数、手动命令、直接读表和看图
  operaters/         # 业务算子、所属 schema/校验/读写
  prompts/           # 提示词及其协议、组装
  tests/             # 回归检查；历史等价性源码仅在 fixtures/ 中
  README.md
```

- 所有现役 pipeline 都遵守，不能另建 comparison_pipeline.py、visual_pipeline.py、partition.py、debug.py、runtime.py、presentation.py 等流程或调试入口，也不做旧路径兼容层。正式子流程用同一个 pipeline.py 中的具名函数和 CLI 参数；临时对照直接写 notebook 命令。
- 目录名严格为用户指定的 `operaters/`；同步更新 Python 导入、notebook、CLI、源码快照、测试与说明。算子不导入 pipeline、notebook 或测试；Python 不解析、编译或执行 notebook。
- debug notebook 保持短小，不定义调试函数/类、状态机、分发器、报告生成器或热加载器；调用模型/写表的命令默认注释。原生 demiflow 请求日志足够记录临时对照，不再为它建立独立业务 pipeline。
- preparation 的 articles、images/catalog、images/review 各自按上述目录管理全部依赖，以本文件顶部“Pipeline 必须管理自己的边界”为准。evaluation 的原 native、请求和模型算子统一在 operaters/；分区审核由同一 CLI 的 --judge-only --backend 调用。
- 只读历史成果可继续放 reviews/，已有 runs/、冻结协议及依赖清单保留；它们不是新执行入口。evaluation 下 t2i/edit 是版本分组，bagel 是模型接入和官方回归工具，第三方源码不套业务 pipeline 模板。Edit 训练契约尚未确定，不为目录齐全虚构实现。
- 布局检查必须覆盖所有现役 pipeline 的根目录、导入方向和 notebook 边界；检查文件归实际维护者，路径变更须同步引用，不能为 preparation 等目录保留豁免。新增 pipeline 也必须纳入。保留历史数据与 notebook 输出、两仓已有未提交改动；不提交、不调用模型或启动正式生产/训练。

### debug notebook 是按需命令草稿（2026-09-23，用户明确纠正）

修订理由：用户要求删除所有项目 `debug.py`，调试代码直接写在 notebook，并保持简单。此条覆盖下方关于 debug 查看器与自动生成查看册的旧约定。

- notebook 只留短小的参数、显式调用、读表和看图 cell，不设 MODE 状态机、阶段分发器、自动生成报告或热加载框架；执行命令默认注释，按需运行。
- 删除项目 debug 查看模块，不换名字藏到 presentation 等辅助层；生产 pipeline 不依赖展示代码。真正用于同输入模型对照的候选读取归所属数据算子。
- 既有 notebook 输出原样留作历史记录；pipeline、算子和 prompts 仍为独立 Python/文本源码。保留未提交改动，不提交，不启动正式生产或模型调用。

### Python pipeline 为唯一执行入口（2026-09-23，用户明确纠正）

修订理由：用户要求整个 demiwtg 不再从 notebook 标签读取、编译或执行 pipeline。此约定覆盖下方所有“notebook 是执行图来源”的旧条目。

- `pipeline.py` 直接定义可导入的流程函数、必要配置和 CLI；命令行与 notebook 调用同一个 Python 函数。多条既有子流程可由具名 Python 函数组合。
- `ops/` 保持业务算子及所属数据绑定，`prompts/` 保持提示词；Lance run 绑定与源码冻结归所属 `ops/runfiles.py` 或已有业务模块。pipeline 不混入查看器、notebook 初始化和历史试跑配置。
- debug notebook 只做导入、配置、显式调用、源码/阶段查看。禁止生产代码解析 notebook、按 metadata.tags 取执行图、通过 exec/compile 加载流程或配置；不保留旧 loader/runtime 兼容入口。
- 保留 notebook 已有输出和旧 Lance 记录；新运行指纹使用 Python 源码。保留两仓已有未提交改动，不启动正式生产、训练或模型服务，不提交。

### 单一 debug 入口与同输入模型对照（2026-09-23，用户明确要求）

修订理由：用户要求标准 pipeline 只保留 debug notebook，将 stepbystep 的有效检查内容并入；同时要求在 T2I debug 手动对照同 case、同提示词的 modelhub GLM-5.3-Flash。

- preparation、curation、benchmark、evaluation 的每条 pipeline 使用自身 debug 入口，逐算子说明、prompts、阶段查看和已有 notebook 输出收在其中；移除独立 stepbystep。CLI 继续只加载原有 `pipeline` 标签图，检查片段不另起第二条执行链。preparation 的文章与视觉两条 pipeline 各有 debug，不合并业务。
- 模型对照读取原调用冻结的实际多模态消息，校验文本、图片字节及顺序一致。构题与审核分别对照原输入；审核不悄悄改为审核新模型生成的题。对照产物写独立 Lance run，不覆盖历史模型结论，不自动交付训练数据。
- 本次 modelhub 接入已获明确授权，T2I 可显式选择 `mode=modelhub`；默认对照 cell 不调用，用户手动运行。上游密钥仍由 modelhub 管理，不写入 notebook。保留两仓未提交改动与已有数据，不重启服务、不提交。

### Preparation 标准 pipeline 结构（2026-09-23，用户最新明确纠正）

修订理由：用户明确要求只有通用平台、业务算子、业务编排三类归属；此前保留 `data/`、`runtime.py`、`inspection/`、`configs/` 没有完成收缩，本节纠正该约定。

- preparation 仅保留 `ops/`、`prompts/`、pipeline 与 debug 入口，以及测试和说明。`pipeline.py`／`visual_pipeline.py` 负责参数、输入冻结、run／stage 绑定与入口；两本 debug notebook 是实际 demiflow Dataset 编排，`debug.py` 负责只读查看／可重建查看册。不另建 data、runtime 或 inspection 层，不保留旧路径兼容包。
- `ops/` 保持 inputs、documents、identity、text、images、routing、article、results 八类业务模块。材料读取、范围与引用角色校验归 inputs；图片 schema、SHA 字节绑定、标注审核及本地审核模型选择归 images；文章 schema、引用校验和写入归 article；阶段行转换、保存固定结果归 results。辅助函数归所属算子，不将每个函数包装为算子类。
- prompt 源码、组装、图片标注配置和业务请求／响应绑定归 `prompts/`。notebook 标签加载、代码指纹、通用显示、进程工具、存储、锁和 checkpoint 归 demiflow；平台不引用项目 schema、模型名或业务路径。业务代码直接调用平台能力。
- 当前流程是基础材料准备：清洗、筛选、文章整理、视觉审核、保存结果。不建知识库，不登记独立 release。历史 `knowledge`、`publication`、`release_ids` 字段和固定 Lance 引用保持原契约；历史 release 导入归 `tools/lake_migration/`。
- 更新全部活动消费者；保留已有未提交修改、历史数据和 notebook 输出，不提交、不操作生产湖、不启动模型或重启服务。测试使用隔离数据根。

### Notebook直接编排Dataset（2026-09-15，用户明确调整）

现役交互入口仍为curation/v4/knowledge_debug.ipynb，但不再使用StreamFlow对象隐藏业务编排。原始来源读取、概念选择、资料关联、计数、分批及知识算子直接通过demiflow Dataset操作连接，业务算子在dataset_operators.py与knowledge_stages.py；notebook_io.py只提供文件读取/版本冻结。StreamFlow保留历史命令行兼容，不作为notebook主线。此条覆盖前述StreamFlow为现役交互编排对象的说明，详见pipeline_memory.md 设计第36节；业务数据契约、原始datasets只读和历史运行不可覆盖约定不变。

