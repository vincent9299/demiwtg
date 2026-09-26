# AGENTS.md · 项目架构原则与数据约束

### Pipeline 标杆：T2I V2（2026-09-26，用户确认）

以 [T2I V2 正式入口](benchmark/t2i/v2/t2i_v2_benchmark_pipeline.py) 为现役 pipeline 编排标杆；[模块说明](benchmark/t2i/v2/README.md) 解释运行和调试方式。下方“Pipeline 编排必须直接表达具体数据流”给出对应代码示例，覆盖其后历史示例中的多题参数、文章配图关联和写死并发。

提炼后的通用原则：

1. **配置直达使用处。** 一个 `run_pipeline(config)` 入口，来源 URI/版本、运行位置、目标表/写模式、概念、预算、并发和模型参数统一配置。reader、writer 和模型节点直接取用；不先打包 manifest 再拆回，不扫描源码冻结运行。
2. **主线能看见数据关系。** 不同来源分别具名，明确每行粒度、列投影、过滤、展开、聚合和 join 键。文章一行一篇，图片一行一个 SHA，展开后按概念聚合，模型节点一行一个概念。多个来源不能藏进分派循环或阶段函数。
3. **清洗由生产者负责。** preparation 交付正文、公开可用状态和图片存储引用；下游信任这些契约，保留本业务需要的数量/上下文策略和实际读图校验。不重复清洗、不绕过引用另找默认原始表。未来的排序、去重、匹配策略须有明确需求。
4. **复杂行处理进算子，跨行关系留主线。** 简单投影内联，复杂请求构造和响应检查放 `operaters/`。算子不另读整表、不执行子 Dataset、不调用模型或写业务表；图片算子按本行已绑定的 Blob 引用读取字节。
5. **按任务范围组织可选材料。** T2I 用配置概念 left join 图文；缺材料就是空列表，由 prompt 说明，不人为加缺失状态或补齐流程。其他业务是否允许空材料依其自身契约，不能机械照搬 T2I。
6. **按真实执行边界落表。** 输入、设计、候选各有明确 schema 和 writer；同步链直接写，异步模型链 `materialize().write_lance(...)`。不 `take_all → Python 加工 → from_items`，不以物化冒充边生成边提交。并发由模型节点的 config 控制，每条记录仍独立请求。
7. **运行记录解决实际问题。** 同名运行按当前配置重新读数，原生调用日志复用相同请求；写模式和 append 提交去重在输出处可见。不增加源码/配置冻结、层层引用转换或额外调度框架。
8. **调试跟随实际调用。** Python 为唯一正式流程入口，notebook 调用它并在运行后只读预览。模型原始响应保留在原生日志；模型节点把服务实际返回的 reasoning 沿当前行传给 writer，在设计表与最终候选表保存 nullable reasoning 列，便于直接查表调试。它是调试字段，不纳入模型题目 schema、题目 ID 或后续 prompt。设计行另保存 response_ref，可按引用查看耗时、usage、finish_reason 和完整响应；call_json 不重复保存推理正文。未返回 reasoning 时存 null，失败/截断响应已有的 reasoning 仍保留在设计表。

评审时先问：只看入口，能否指出“读什么、每行是什么、如何连接、何时调用模型、写到哪里”？再检查是否存在下游重复清洗、无实际需求的抽象和隐藏 I/O。

### Preparation 清洗、T2I 独立图文消费（2026-09-26，用户确认）

本条覆盖下方 T2I 的文章去重/冲突检查、配图匹配、引用/依赖检查、缺失材料拒绝出题及必需配图推导规则。

- preparation 负责交付清洗：文章预检与依据/引用完整性检查、图片来源规范化及已知生成图排除、公开可用状态一致性。用户明确选择排除必须查看配图才能理解的段落；明确图号和指图段落在上游排除，保留审计记录，不将这类清洗下放给 T2I。
- T2I 默认信任 preparation：文章按 reviewed 取正文，图片按 published/keep 取独立材料。不解析内部审核 JSON、来源或文章配图，不检查段落引用/配图依赖，不做同概念文章去重或冲突报错。不向模型传递上游来源引用；材料编号仍用于当前输入及考点依据。
- 图、文分别按概念聚合，再组合成模型输入；不构造图文匹配或“独立图覆盖配图”的优先层。当前保留图片数上限；取消配图后不推导必需图，后续有联合字段时再扩展优先策略。
- 无材料时照常出题：空参考列表，prompt 说明没有参考材料，不产生 invalid_materials、缺失概念或补齐证据的特殊业务分支。
- 保留编号、图片编码、上下文预算，以及实际读取时的 SHA/解码检查。正文不截断，超预算跳过；图片只在预算通过后读取一次，读取失败直接抛错。
- 同步改 preparation 出口、T2I、notebook、提示词、测试和说明；历史数据及 notebook 已保存输出不回写，开发不调用正式模型。

### Preparation 交付边界与 T2I V2 配置直用（2026-09-26，用户纠正）

修订理由：用户指出 preparation 应屏蔽原始存储复杂度，出题不应绕过交付引用再指定 collect 表；源码/配置冻结和 request→manifest→拆回参数属于过度设计。本条覆盖下方 T2I V2 的运行冻结、同名运行拒绝配置变更及复用旧输入/完成状态要求。

- 下游从显式配置的 preparation 表读取材料及其图片引用。图片表已有 `source_refs`（来源 URI、Lance 版本），文章配图若已携带 `blob_ref` 则直接使用；只有 SHA 的文章配图按 SHA 关联配置的 preparation 图片表。不能在出题侧导入 collect 的 `IMAGES_URI`、读取原始表 latest 或自行重绑图片版本。缺少交付引用时明确报错，不回退到默认原图表。
- T2I V2 入口统一为 `run_pipeline(config)`；`config(...)` 汇集 run、article_source、visual_source、target_uri、write_mode、概念和模型参数；concurrency、queue_depth、temperature 等执行/采样参数同样从 config 绑定，不在节点写死。reader/writer 直接使用 config 中对应字段，不再打包成 request/manifest 后逐项拆回。不扫描源码和 prompt 建运行快照，不因源码、参数、来源或输出变化要求更换运行名。
- 每次执行按当前参数重新读取材料、写输入及设计表；不得按旧 complete 状态提前返回或读取旧输入代替本次来源。输入中明确指定的 Lance 版本和图片交付引用仍按值读取，它们不是额外的运行冻结机制。
- 保留原生模型请求日志及相同请求的响应复用。overwrite 每次写本次结果；append 保留同运行、同目标、同批内容的提交记录，避免重复追加。运行记录不再保存源码或配置 manifest。
- 来源、关系和读写条件在数据流主线可见；不为配置校验、字段包装或别名解析增加转调层。仅有名称为“元信息”的函数却打开业务表，也属于隐藏数据依赖。


### T2I / Edit Benchmark V2 单题约定（2026-09-26，用户确认）

修订理由：用户要求两条 V2 都围绕概念核心内容一次出一道题；Edit 由同一次调用从已提供图片中选一张原图，不再先设计意图、搜图、定稿和审题。本条覆盖旧 V2 多题及独立 Edit 保留旧协议的约定。

- 每概念一次请求，返回单个 `question`；无法出题时为 `question: null` 和具体 `reason`。删除 `tasks_per_concept` / `tasks_per_unit` 题数参数，不保留旧 candidates 列表兼容。允许一道题含多个紧密相关考点，不以覆盖全部知识为目标。
- 两边均使用一个 `design_question` prompt。T2I 的题目字段为 `instruction`、`test_points[{point,basis}]`；Edit 增加 `source_image`，按本次实际图片的 1 起始编号绑定原图。题面包含必要锚点、展示和保持要求，不再额外输出 criteria、知识空缺或搜索计划。
- **空题属于业务逻辑**：业务响应算子区分有题/空题，检查字段及不足原因，并检查 Edit 原图编号。复用 YAML 的字段定义和已有标准校验能力，不因对象/null 两种业务结果扩展 demiflow。
- Edit 当前从已交付图文中的图片选图，缺图/不适合留原因；删除重复选图、定稿、独立审题及构题后再检索。两边输出均为 `unreviewed` 待审题。
- **Edit 操作围绕核心内容选择（用户进一步纠正）**：先选概念核心考点，再用增、删、改及必要组合实现。风格、动作、背景等修改统一归入“改”，不再把 style 单列为选题类别；组合表示围绕同一核心结构、关系或规则联合使用增删改，不沿用旧 compose 的类型枚举与操作数配额，不拼接独立考点。仅改风格或背景时遵守相应内容保持范围。当前不采用以抠图/分割/白底提取为主要目标的题目，因为其主要考察定位、分割及边缘处理，与概念核心内容的联系通常有限。仍然每概念一题，不增加类型标签输出。
- 同步响应、表结构、CLI、notebook、测试和说明；新协议使用新运行名及目标表，历史题目与 notebook 已保存输出不改写。开发只做隔离模拟验证，不调用正式模型。


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


### Prompt 配置直接表达，禁止固定正文逐层透传（2026-09-26，用户确认推广）

修订理由：T2I V2 将 MD 正文经 Python 变量、数据行字段、`inputs` 映射传入 YAML，YAML 只剩占位符；正文与 response_schema 分散维护，导致已删除的 criteria 仍被 schema 强制要求。用户要求合入 YAML，并推广到已经开发的 pipeline。

- **固定规则放在使用它的 YAML `template: |` 中**：同一 prompt 的正文、版本、模型配置、`response_schema` 放在同一份标准 prompt pack。不要通过 `read_text()` → `instructions` → `prompt_instructions` → `{{ instructions }}` 原样转递固定正文；不在每行数据中复制运行级配置，不维护同内容的 MD 镜像。
- **模板参数只表达实际变化的输入**：如概念、材料、题面、图片、题数配置。已有字段能直接绑定就直接绑定，不为套模板改名、转 JSON 再解析或增加转调函数。字段映射、图片编码、实际载荷构造和来源绑定有明确作用时保留；不以“少变量”为由把真实的数据边界混在一起。
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


### T2I 直接使用 preparation 结果表（2026-09-25，用户明确纠正）

修订理由：用户要求去掉输入别名，不增加概念。T2I 出题和训练入口直接写 preparation 结果表路径与固定版本，不要求使用者理解发布登记。用户随后要求 review 并清理重复包装和无效转换。

- notebook 与配置使用 `uri`、`version` 指定文章/图片结果表；按审核状态与概念读取，不再要求提供发布名、别名或 release_id。
- 当前输入在运行记录及读取链路中保持 `uri`、`version`，不为内部兼容扩成另一套引用再转回路径；不为此读取表头、扫描行数或查询登记。一次请求的图文材料只组装一次，不保留没有业务作用的纯转调包装。
- 历史运行及其固定引用保持原读取语义；当前入口、注释和说明不沿用旧发布别名。

### 代码注释规范（2026-09-25 制定，2026-09-26 补充，用户明确要求）

修订理由：用户要求将代码注释纳入项目规范，并为本次新增的 T2I Benchmark V2 实现补充注释。
2026-09-26 补充理由：用户指出“整理材料、选择配图、固定编号、补齐缺失概念”等描述过于抽象，要求注释直接说明实际操作；本次补充适用于项目内所有 pipeline。

- 新增或修改代码时同步维护中文注释与 docstring。模块说明职责；对外函数、业务算子说明输入、输出和关键约束，简单函数可用一句话表达。
- 在关键流程和非显然的分支处说明业务目的及选择原因，特别是材料选择、编号对应、版本冻结、模型调用、失败状态、续跑及落表完成边界。不要逐行翻译代码或重复变量名。
- **阶段注释写具体操作**：说明处理什么数据、按什么条件筛选或组合、怎样改变行或字段、输出到哪里。只写与该段相关的关键事实，不机械罗列模板；读者不进入算子实现，也应能理解主线在做什么。
- **不能用目的代替动作**：“整理、处理、对齐、固定、补齐、保存供复用”等词后必须有具体说明。例如，选图说明优先选哪些图、其余图片如何排序及受哪个参数限制；编号说明对象、顺序和起始值；“补齐缺失概念”须明确是补一条状态行，还是实际补充了材料，不能混为一谈。
- **结果和分支要可核对**：涉及缺失、超限、失败、跳过或续跑时，说明触发条件、记录的字段/状态及后续动作；涉及落表时，说明每行代表什么、目标表/路径变量、写入模式，以及复用哪个已登记版本。按相邻操作分段注释，不把多种行为压成一句笼统概括。
- notebook 的参数块直接注明数据源的实际表路径、固定版本及筛选范围，不增加发布名或别名。模型调用和结果预览分段标注，说明手动执行的实际行为。
- 注释必须与实现一致；修改逻辑时同步更新或删除过时说明。不保留废弃代码作为注释，不为增加注释引入额外封装。

### T2I Benchmark V2 精简出题（2026-09-25，用户明确要求）

修订理由：用户要求根据新的出题共识，用标准 demiflow API 精简 V2、清理不用的代码，并参照 curation/t2i/debug.ipynb 建立调试入口。本条覆盖旧 V2 审题、构题后重新检索及四格 debug 模板约定。

- V2 当前只做发布材料绑定、候选出题、结构校验和落表。审题规则另行讨论，输出明确为待审候选；不自动打题、评测或宣称形成正式基准。
- 模型每题业务输出只保留 `instruction`、`test_points[{point, basis}]`；正确性及可观察性检查用于约束出题，不单独输出 criteria 或 requirement。删去旧知识空缺分析、重复调用封装和未使用阶段，不为旧协议保留兼容层。此输出变更只适用于 T2I V2；V1 和独立 Edit 的题目协议不变。
- debug 参照训练侧保留一个 code cell，显式参数、调用正式 run_pipeline、按实际表路径与固定版本读取、直接预览题目与图文材料；手动执行才请求模型。开发验证只使用隔离数据和模拟响应。
- 使用标准 map_prompt_async/run_stream、原生请求日志及 Lance writer；材料编号、图片 Blob 版本和运行配置固定，续跑不重检索材料。

### T2I 历史基准收敛（2026-09-24，用户明确要求）

修订理由：用户要求只保留旧 T2I V1 的固定 200 题和 800 张模型输出，将其数据归入 V1 datasets，并删除混合 20 题、后续 5 题及其他 T2I 历史实验。用户确认保留当前 V2 pipeline、公共知识/图片材料、独立 Edit 基准。此条覆盖下方相关实验继续保留的旧约定。

- `benchmark/t2i/v1/datasets/bench200_artifacts.lance` 和 `bench200_blobs.lance` 保存完整旧基准证据：题库、源图、四模型输出、评分及构题溯源；原始内容字节与证据名称不改。公共索引继续提供跨赛道历史查找，T2I 的全部字节落在 V1 datasets。
- 删除混合 `concepts10_questions20_20260918` 题表、`aligned_evaluation_20260919_v5`、两例开源模型对照和历史参考恢复数据/查看册，以及 focus1000 退役实验的专属证据；删除无引用 Blob、旧公共索引/Blob 副本及失效登记/位置映射。不删除独立 Edit、BAGEL、训练数据和公共材料。
- 当前 V1 是旧 v6.0 构题流程，不是后续小实验的源码快照。后续候选构题、原判据冻结和公开题面检索逻辑保留在现役 V2/evaluation；不为清理混入 V1 或重写版本历史。
- 清理脚本、清单、校验和回执放共同工作区 `_demiflow/t2i_cleanup_20260924/`，不保留被删除实验的数据备份，不调用模型、不提交 Git。


### 顶层共享 datasets 清理（2026-09-24，用户明确要求）

修订理由：用户要求 `/yzp/zhaozy/yangzepeng/0905/datasets` 只保留最新、实体、公共表，清除临时 run 级表。本条覆盖下方该目录继续保留所有分类历史与维护迁移表的旧约定，仅适用于本次指定的顶层目录。

- 保留 4 张现役主数据实体表、2 张全局登记表、最新公共证据索引及其 2 张 Blob 表、当前正式发布引用的 2 张审计表，共 11 张。
- 删除 19 张已完成的临时维护/进度表、被完整替代的旧证据索引及旧分类历史表；同步清除对应的现役目录登记和失效路径映射。不按名称中的日期删除仍被公共发布或评测引用的表。
- 保留表的行、历史版本和固定引用不改写，各 pipeline 自己的 datasets/ 不在本次清理范围。清理计划、文件盘点与完成回执位于工作区 `_demiflow/datasets_cleanup_20260924/`；不再往共享 datasets/ 新建一次性清理运行表。

### T2I debug 单格生成与预览（2026-09-24，用户明确要求）

修订理由：用户要求第一格改为从正式发布源筛选两个概念、调用 GLM 生成训练数据、写新 Lance 表并按条目预览，同时删除第二格。本条覆盖下方 T2I 两格历史对照约定。

- `curation/t2i/debug.ipynb` 仅一个 code cell，复用正式 `run_pipeline()` 完成材料准备、构题、校验、审核和导出；不把未审核候选或历史模型对照当作训练条目。
- 显式配置发布源、概念、模型与运行名；模型为 `glm/glm-5.3-flash`。Lance writer 留在正式 pipeline，notebook 显示新训练表的实际路径和固定版本，再用标准 `read_lance()` 读取。
- 每行展示一个样本，区分完整训练输入与监督目标，图片可点击预览；删除独立第二格，不新增查看器或调度包装。手动执行 cell 才调用模型，维护验证使用隔离数据和模拟响应。

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

### 非 curation 历史材料归档（2026-09-23，用户明确要求）

修订理由：用户要求当前 curation 以外的历史查看册和审核记录统一收进各自的 archive/，覆盖下方这些模块继续保留 reviews/ 的约定。

- benchmark、evaluation（含 BAGEL）的历史查看册、审核记录直接归所属模块的 archive/，不再套 reviews/；空 reviews/ 删除。正式 pipeline、operaters、prompts、debug 和依赖声明保持现役。
- 本次不整理 curation/，保留其当前代码、查看册和运行目录。历史文件仅移动，notebook 输出、模型结论及固定 Lance 证据 ID 原样保留；不把证据 ID 当成本地路径改写。
- 同步文档链接、Git 忽略规则和目录检查；archive/ 仅存历史材料，不成为新执行入口。保留两仓未提交改动，不提交、不调用模型、不改湖内数据。

### 直接使用标准 API 与 Edit 首版试跑（2026-09-23，用户明确要求）

修订理由：用户指出 `files.lance_checkpoint` 等封装隐藏实际读写、增加阅读成本，要求完全使用标准 API；并授权实现 Edit、调用本地模型试跑两个 case。

- pipeline 直接使用 demiflow Dataset 的标准 `read_lance`、`map`、`map_prompt_async`、`write_lance` 等 API，以及 Lance/PyArrow/Diffusers 官方 API。不得新增、扩展或包装通用读写、checkpoint、调度、模型调用 API；确需扩展时必须先向用户说明具体缺口并确认。业务算子只实现材料、构题、合成、审核等业务转换。不要用新的框架、继承层或 helper 隐藏执行链。
- 题目、失败记录、图对、审核及训练条目明确写入 Lance 表，pipeline 中能直接看到表路径和 writer。空表可直接使用 Lance 官方 writer 与显式 Arrow schema；不为此扩展 demiflow。历史表和冻结引用不改写。
- Edit 学习方向参考 T2I，额外独立记录编辑类型；已有图作原图还是监督目标由 VLM 根据监督信号决定。VLM 默认本地 `qwen3.8-27b`，合成默认本地 `Qwen-Image-2.1`，记录模型调用耗时并在 notebook 展示。
- 用户随后要求一卡一个模型：允许把本地 Qwen3.8 服务改为 GPU0 单卡，为 GPU1 的 Qwen-Image-2.1 留出显存；保持服务模型名和端口，记录上下文限制与启动参数。
- 本轮允许两个 case 的真实构题、合成、审核和试跑结果落湖；不启动训练或批量生产，不提交。Edit debug 参照 T2I：短小的看题/看图 cell 与可修改模型、算子的实际编排 cell；保存真实试跑输出。


### T2I debug 两格编排（2026-09-23，用户最新明确要求）

修订理由：用户要求 T2I notebook 第一格看指定题目，并随后明确要求同格输出图片；第二格给出可替换模型/算子的实际编排，其他部分复用。这覆盖下面“所有 notebook 三格空模板”和“T2I 调用命令必须注释”的旧限制，仅用于本次明确要求的 T2I 调试入口。

- curation/t2i/debug.ipynb 仅两个 code cell：第一格按 RUN/TASK_ID 只读看题，并直接输出构题材料图、实际作答参考图、监督目标图；第二格直接写 Dataset 算子链，MODEL 和 STAGE 可改，复用冻结输入、响应 schema 和原生 Lance 日志。
- 第二格模型使用用户在网关列表核对后选定的 `glm/glm-5.3-flash`，由用户手动运行才发请求；构题和审核分别重放原输入，审核默认仍针对原题。调试日志与原运行隔离，不改历史结论或自动导出训练数据。不加执行开关、辅助模块或新的 pipeline。
- 编写期间只读取网关模型列表、做模拟接口测试，不替用户实际调用模型。网关未列出的指定模型不得默默替换。

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
  pipeline.py        # 正式流程、必要配置、CLI；notebook 调用同一个函数
  debug.ipynb        # 参数、手动命令、直接读表和看图
  operaters/         # 业务算子、所属 schema/校验/读写
  prompts/           # 提示词及其协议、组装
  tests/             # 回归检查；历史等价性源码仅在 fixtures/ 中
  README.md
```

- 所有现役 pipeline 都遵守，不能另建 comparison_pipeline.py、visual_pipeline.py、partition.py、debug.py、runtime.py、presentation.py 等流程或调试入口，也不做旧路径兼容层。正式子流程用同一个 pipeline.py 中的具名函数和 CLI 参数；临时对照直接写 notebook 命令。
- 目录名严格为用户指定的 `operaters/`；同步更新 Python 导入、notebook、CLI、源码快照、测试与说明。算子不导入 pipeline、notebook 或测试；Python 不解析、编译或执行 notebook。
- debug notebook 保持短小，不定义调试函数/类、状态机、分发器、报告生成器或热加载器；调用模型/写表的命令默认注释。原生 demiflow 请求日志足够记录临时对照，不再为它建立独立业务 pipeline。
- preparation 的文章、视觉子流程统一在 pipeline.py 和 debug.ipynb。evaluation 的原 native、请求和模型算子统一在 operaters/；分区审核由同一 CLI 的 --judge-only --backend 调用。
- 只读历史成果可继续放 reviews/，已有 runs/、冻结协议及依赖清单保留；它们不是新执行入口。evaluation 下 t2i/edit 是版本分组，bagel 是模型接入和官方回归工具，第三方源码不套业务 pipeline 模板。Edit 训练契约尚未确定，不为目录齐全虚构实现。
- 用 preparation/tests/test_pipeline_layout.py 检查所有 pipeline 根目录、导入方向和 notebook 边界，新增 pipeline 也必须纳入。保留历史数据与 notebook 输出、两仓已有未提交改动；不提交、不调用模型或启动正式生产/训练。

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

### 工作目录收敛执行（2026-09-23，用户确认执行清单）

修订理由：用户确认执行已核对的清理方案，保留当前主线和有效成果，退役 focus1000 与旧试跑。这次明确授权覆盖下方旧“历史目录原位保留”和“不写生产湖”的维护限制，仅限本次保全与清理。

- 采集目录及工作区、仓库内所有 `_staging` 不修改；不操作模型服务、训练、正式新数据生产、环境或权重，不提交。
- 保全两套 V1 200题、源图、模型输出、冻结评分/协议/判官证据、必要 pilot 溯源与当前设计查看证据；按固定 Lance 引用读写，原记录字节及分数不改。
- focus1000 退出独立 pipeline，不再补跑。已有生成图及提示词/生成记录/59评分/评测排除关系保留；图片保持生成来源和评测约束，不自动成为训练或已审核参考材料。
- 工作目录清掉退役实验、重复图和可重建导出。先保全必要数据、逐 Blob SHA 核验、迁移消费者和结果复核，再依据显式清单删除；不新建 backup/archive 中转目录或旧路径软链接。
- 主线为 preparation、curation/t2i 与 edit、benchmark 两赛道、evaluation。V1 留固定基准/重现能力，V2 迭代；BAGEL 作为模型接入及按需官方回归工具。

### Preparation 标准 pipeline 结构（2026-09-23，用户最新明确纠正）

修订理由：用户明确要求只有通用平台、业务算子、业务编排三类归属；此前保留 `data/`、`runtime.py`、`inspection/`、`configs/` 没有完成收缩，本节纠正该约定。

- preparation 仅保留 `ops/`、`prompts/`、pipeline 与 debug 入口，以及测试和说明。`pipeline.py`／`visual_pipeline.py` 负责参数、输入冻结、run／stage 绑定与入口；两本 debug notebook 是实际 demiflow Dataset 编排，`debug.py` 负责只读查看／可重建查看册。不另建 data、runtime 或 inspection 层，不保留旧路径兼容包。
- `ops/` 保持 inputs、documents、identity、text、images、routing、article、results 八类业务模块。材料读取、范围与引用角色校验归 inputs；图片 schema、SHA 字节绑定、标注审核及本地审核模型选择归 images；文章 schema、引用校验和写入归 article；阶段行转换、保存固定结果归 results。辅助函数归所属算子，不将每个函数包装为算子类。
- prompt 源码、组装、图片标注配置和业务请求／响应绑定归 `prompts/`。notebook 标签加载、代码指纹、通用显示、进程工具、存储、锁和 checkpoint 归 demiflow；平台不引用项目 schema、模型名或业务路径。业务代码直接调用平台能力。
- 当前流程是基础材料准备：清洗、筛选、文章整理、视觉审核、保存结果。不建知识库，不登记独立 release。历史 `knowledge`、`publication`、`release_ids` 字段和固定 Lance 引用保持原契约；历史 release 导入归 `tools/lake_migration/`。
- 更新全部活动消费者；保留已有未提交修改、历史数据和 notebook 输出，不提交、不操作生产湖、不启动模型或重启服务。测试使用隔离数据根。

### 基准构建与模型评测分工（2026-09-22，用户最新明确要求）

修订理由：用户要求 benchmark 负责出题、evaluation 负责评测，将 BAGEL 官方评测和 V1 评测代码移出 benchmark。专业名称采用 benchmark construction / evaluation，顶层目录仍叫 benchmark/、evaluation/。本节覆盖下方 BAGEL 保持原位及 V1 混合源码的旧约定。

- benchmark/{t2i,edit}/{v1,v2}/ 负责基准构建：题目、输入材料、任务判据与题库检查。evaluation/{t2i,edit}/v1/ 负责旧版模型作答、判分、冻结核验和结果展示；当前 evaluation/native pipeline 保持现有入口。
- benchmark/bagel/ 整体迁至 evaluation/bagel/；原 evaluation/bagel.py 改为 evaluation/bagel/adapter.py。根目录 bagel/ 官方模型代码不移动。benchmark/vlm/、focus1000/ 本轮保持原位。
- 历史 V1 混合批次保留于 benchmark/{t2i,edit}/v1/，不改写题目、图像、模型输出、评分或冻结 manifest。评测源码显式读取该位置；eval_codex_score.py 与冻结模板保持原字节。notebook 分拆保留已有执行输出。
- 当前 pipeline 的源码快照排除迁入的 V1 评测与 BAGEL vendor/数据目录；纳入实际使用的 BAGEL adapter。保持现有 Lance 运行身份、两仓未提交改动，不提交、不重启服务、不执行正式构题/模型作答/判分/训练。

### Benchmark 分赛道版本与顶层模块（2026-09-22，用户最新明确要求）

修订理由：用户要求合并两个 benchmark，将现役构题按 T2I/Edit 拆成 V2，旧 benchmark 同样按赛道归入 V1，并把 preparation、evaluation 移出 curation。本节覆盖下方旧目录和版本保留约定。

- 顶层模块为 preparation/、benchmark/、evaluation/；curation/ 保留 t2i/ 和 edit/。
- benchmark/t2i/v1/、benchmark/edit/v1/ 保留原顶层旧 benchmark；benchmark/t2i/v2/、benchmark/edit/v2/ 为从原 curation/benchmark 拆出的独立构题 pipeline，各自拥有 ops/、prompts/、runtime.py、pipeline.py 和 notebook。不同赛道不互相导入业务算子或 prompt。
- benchmark/bagel/、vlm/、focus1000/ 等未按 T2I/Edit 拆分的目录保持原位；只修复必要的文件引用。V1 的旧源码、历史题目、图片、评分与冻结 prompts 保留，不因旧版删除约定而移除。
- 迁移源码与入口时更新全部活动调用方，不建立旧 Python 包别名或软链接。历史 run 定位保留原 Lance 身份；preparation/evaluation 的新顶层定位仍指向原命名空间，新 benchmark V2 两赛道各有命名空间，不迁移或改写历史数据。
- 保留两仓已有未提交改动，不提交、不重启服务、不进行正式数据生产、模型作答、评分或训练。

### Curation 按 pipeline 收缩（2026-09-21，用户最新明确要求）

修订理由：用户确认当前没有知识库，文章整理和基础标注均为基础材料准备；线上 RAG 将来单独设计。用户要求 curation 只保留几条 pipeline，删除旧目录与无用调用链。本节覆盖下文旧目录、taxonomy、归档和历史文件保留要求。

- 当前目录为 `curation/preparation`、`benchmark`、`t2i`、`edit`、`evaluation`；不保留 `pipeline_v2` 包装、`downstream` 公共业务层或旧入口别名。
- 训练拆分理由（2026-09-22，用户明确要求）：现有候选设计主要面向 T2I，迁入 `t2i/ops`；T2I 与 Edit 各自维护 `ops/`、`prompts/`、测试和 `stepbystep.ipynb`，不互相导入业务实现。Edit 合成契约与 prompt 继续讨论，不把旧混合分支当作已实现的编辑 pipeline；已有编辑图对报告原样归入 `edit/reviews`。
- 文本整理、基础图片标注与视觉材料审核归 `preparation`，配置在 `preparation/configs`；产物称基础材料或语料。RAG 的索引、线上召回另做 pipeline，本轮不预建。
- 删除 curation 内旧 SQLite/JSONL 数据、taxonomy、实验、layout、归档与历史 review。保留仓库外数据湖；现有材料契约中的 knowledge 字段暂保留，随逐算子讨论再定，不改历史发布。
- 采集与材料输入读取固定发布中的概念表，不要求分类树或挂载表。运行定位符为 `curation/<pipeline>/runs/<run>`，实际业务数据写 Lance。
- 保留两仓已有改动，不重启服务、不提交；讨论期间不生产正式训练数据、不启动训练。用户明确说改之前先讨论。

### 原始材料与策展结果分离（2026-09-21，用户纠正后的最新约定）

修订理由：用户只要求合并 derived 与 releases；此前把 raw 一起合并属于错误扩展，现纠正。下方历史统一实体表约定不得再用于当前写入。

- 采集唯一写端为 `datasets/raw/images.lance`、`datasets/raw/documents.lance`；仅保存原始字节、来源、分辨率、采集关联概念等事实，不包含策展标注和审核列。
- 策展唯一结果表为 `datasets/curated/images.lance`、`datasets/curated/articles.lance`。图片描述、概念匹配、视觉审核按 SHA 聚合为结构化列表，发布状态在对应审核项；文章草稿与审核文章合表。
- 原始图片与策展图片是两张真实 Lance 表，各自独立版本。策展写入必须绑定固定 raw DatasetRef，SHA 必须存在于该版本；`source_refs[]` 保留处理所用的源版本。只读原始字节，不回写源表。采集更新不会自动改变策展结果或既有发布。
- 标注提示词与参数放 `curation/config/`，由 Git 管理；历史实际配置可保存在运行溯源，不建独立协议业务表。
- 发布是固定策展 DatasetRef 加显式 release_id 选择，不复制发布表。知识/独立视觉读取 raw；标注复用读取固定 curated 图片表；出题、训练、评测读取明确发布。
- 通用存储、索引、锁、checkpoint 和引用由 demiflow 提供；schema、来源绑定、标注及发布选择由 demiwtg 定义。master、runs、registry 的职责不变。
- 更正设计与验收见 `curation/layout/entity_tables_20260921.md`、`curation/layout/raw_curated_separation_20260921.md`。核验完成后才退役错误合并表，不改历史模型结论。

### 执行规则（2026-09-21，实体路径以上方最新约定为准）

本轮修订理由：用户要求真实合成一张 Lance 表并自查代码边界。图片、文档分别为 `raw/images.lance`、`raw/documents.lance`；不在业务层路由物理分表。平台回执与锁统一放每张表同级 `_demiflow/<表名>/`；此目录不是业务存储。旧历史条目中的 sharding、state、JSONL 写端与归档要求不再适用。

本次修改理由：用户要求架构边界清晰、取消兼容垫片、Lance 为唯一业务存储，并统一 Python 环境；只保留最新代码；用户随后要求删除归档代码，taxonomy 并入策展，并将现役源码全部纳入 Git 跟踪。

- 唯一活动策展代码为 `curation/pipeline_v2/`，保留知识、出题、训练数据、评测四框架；独立视觉是知识框架可单跑的子图。CLI 加载 notebook 中同一条 demiflow Dataset 链，不另写调度器。
- V0/V1、退役入口及源码恢复副本已按用户最新指令删除；不保留归档代码或兼容别名。历史实验数据、样本、评分与迁移记录保留，不能因源码清理一并删除。
- `curation/taxonomy/` 是策展内部的概念、分类和挂载主数据模块，不是第五条 pipeline。采集从该模块读取固定 master release；湖内 `master/` 位置不变。旧 JSON 主数据写入脚本已删除。
- 两个仓库的现役源码、测试、notebook、配置和说明纳入各自 Git 跟踪；环境、密钥、模型、湖与运行输出不入库。未经指示不提交。
- **平台在 demiflow，业务在 demiwtg**：Dataset、Lance 引用/登记/发布/checkpoint、Blob、记录表、调用预算与重放属于平台；字段 schema、材料关联、审核、隔离、出题/训练/评测规则属于业务。`data_access/` 和文件/Lance 回退适配器已删除。
- **Lance 是唯一业务存储**：输入、阶段、运行元数据、请求/响应、评测图像与发布引用进入数据湖。跨阶段传 Dataset/DatasetRef；图片按内容 SHA 或固定 BlobRef 读取，不根据旧文件是否存在切换存储。JSON 配置、源码 notebook、可重建查看导出、进程锁/日志与平台提交回执不充当另一套业务数据。
- 数据根经 `project.py`，默认工作区 `datasets/`，可用 `DEMIWTG_DATASETS_ROOT` 指定。`master/` 是概念/分类/挂载主数据；`raw/` 是原始材料，`curated/` 是策展实体结果；`runs/` 执行；`registry/` 引用/发布账本。运行定位符仍用 `curation/experiments/<用途>/<run>`，实际数据写 `datasets/runs/pipeline/...`。
- `viewer/` 已按用户要求删除。用户随后纠正：`sync/` 保留，其当前冷备中继代码已恢复并纳入 Git；它不等于旧 lake_sync/merge_meta 文件总账，后者及旧测试已删除。
- 唯一共用 Python 为工作区 `env/bin/python`。`env-cleaning`、`env-lance` 已合并退出；不创建环境软链接或历史入口。Bagel 专用环境不属于本次三环境合并范围。
- 不重启既有模型/采集服务，不改历史题目/输出/评分，不替用户提交已有改动。测试必须隔离数据根，禁止复制或写入生产湖。

下面保留旧架构决策原文用于理解历史；与本节冲突时以本节为准。

### 当前目录约定（2026-09-20，用户明确要求；覆盖后文旧布局条款）

调整理由：用户要求重要文件放在对应代码模块下，子目录名称说明用途；取消顶层 state，不用软链接；保留 V0/V1 冻结参照、V2 最新实现、最终成果、给用户看的关键节点及必要溯源，删除非关键过程文件。下面是当前有效布局，后文旧路径仅说明历史，不得据此重建 state 或兼容软链接。

- 统一导览为 `curation/README.md`。`curation/pipeline_v2/` 是唯一活动实现，四条同级流程仍为知识、出题、训练数据、评测，沿用既有 demiflow 框架。`legacy/` 仅保存历史回归所需的实现和样例，不是活动版本。
- 版本调整理由（2026-09-20，用户明确授权）：V1 已产生小批训练数据，本次独立视觉材料发布与候选目标参与构题改变了方法和协议，知识、出题、训练数据、评测统一进入 V2。`curation/pipeline_v1/` 原样冻结；273 个文件的字节清单为 `curation/layout/version_snapshots/v1_before_v2.json`。旧 run 与数据保留原版本，不改名为 V2。
- `curation/pipeline_v0/` 保存北京时间 2026-09-19 18:23 基线；原始 272 个文件字节不变，原始清单和元数据在其 `snapshot_metadata/`。V0 用于历史对照，不将其旧导入重定向到 V1。
- `curation/knowledge_base/` 保存最终知识交付：111 个机器审核通过概念，不是人工 golden。`curation/image_annotations/` 保存约 7.5 GB 的图片预标注数据库、协议和续跑信息，不能当缓存删除。`curation/training_data/` 保存最终小批样本和审核溯源。
- `curation/reviews/` 保存给用户看的关键节点，README 按讨论顺序列出题目、评分、对照和迭代；13 本关键查看册保留原始字节。`research_notes.md` 保存历史讨论全文，当前方法以 `curation/pipeline_memory.md` 为准。
- `curation/experiments/` 保存关键实验完整记录，`upstream_evidence/` 保存被其引用的上游证据，`early_benchmarks/` 保存早期题目和评分。实验 run ID 不是 pipeline 版本。新结果也写在这个目录，不能写入顶层 state。
- 原 state 中的采集材料／断点已归属 `collect/records/` 与 `collect/image_backfill/checkpoints/`；标签迁移交付／恢复资料在 `taxonomy/migration_records/`。`curation/runtime/model_service/` 只存本地模型服务锁。原始 datasets、模型和环境不属于本次清理删除范围。
- 顶层 state 和 archive 均已移除。跨模块唯一源码恢复副本放在 `tools/recovery_snapshots/`；已按 SHA-256 删除与当前文件、V0 或其他备份完全相同的副本，恢复时须同时查 duplicate_removals.json。
- 文件整理只使用真实目录。`curation/layout/relocations.json` 和当前版本 `pipeline_v2/paths.py` 用于读取冻结记录中的旧路径；模型输出、题目、判据、评分与训练样本不因移动而改写。删除清单、保留原因和完整性结果在 `curation/layout/`。
- 本次明确授权代码模块内按用途存放大体积成果，覆盖旧“所有运行成果必须进 state”的约定。原始／派生大数据仍不入 Git；源码、当前说明和必要维护元数据正常维护。可以添加说明目录用途的 README，但不再把临时过程报告散落在模块根目录。

### 平台与业务边界（2026-09-21，用户要求彻底移除 data_access）

调整理由：此前兼容层让通用能力与业务定义混在一个顶层模块，且迁移后消费者存在断点。用户明确要求删除 `data_access/`，平台在 demiflow，业务在 demiwtg；本条覆盖后文历史记录中对 data_access 的引用。

- `data_access/` 已移除，不保留兼容包或导入别名。固定版本引用、登记、发布、Lance checkpoint、Blob 读取/校验/缓存和原子导出直接使用 `demiflow.lance`；通用不可变文件、锁、调用日志及重放也由 demiflow 实现。demiflow 不导入 demiwtg 或持有项目 schema/表路径。
- 新增 `project.py` 仅存项目数据根配置；这是本次明确登记的顶层配置代码，不是新的访问层。`DEMIWTG_DATASETS_ROOT` 保留；默认根为工作区 `datasets/`。
- `collect/assets.py` 绑定单张 `raw/images.lance`，`collect/materials.py` 解释采集来源，`collect/material_schema.py` 定义材料表；`curation/taxonomy/master_data.py` 处理主数据发布选择和兼容树，`curation/taxonomy/schemas.py` 定义分类表；`curation/schemas.py` 定义知识、标注及阶段表。通用能力不得在这些模块重复实现。
- 一次性入湖/迁移程序归 `tools/lake_migration/`，只处理项目特定来源和映射。活动 pipeline 不依赖迁移工具，直接向平台提交显式业务 schema。
- 主数据必须使用完整固定发布；缺失/无效发布明确失败，不能静默回退各表 head。memberships 是挂载关系真相，树中 instances 由其投影。
- 测试必须使用临时数据根，禁止通过默认配置写入或复制生产湖。V0/V1 与历史源码证据保持字节不变；历史证据中的旧模块名不代表活动入口。

### datasets 根迁至仓库顶层（2026-09-20，用户指令）

依据：用户明确要求“datasets 转移到项目顶层目录，所有数据都放到这个目录里，分层管理”。方案 §1 的可配置数据根即此部署形态。

- 数据根迁至 `/yzp/zhaozy/yangzepeng/0905/datasets`（仓库外、工作区顶层）；`demiwtg/` 数据集内按 registry／raw／derived／runs／releases 五层分层（MECE：账本／来源事实／派生资产／执行产物／冻结交付；2026-09-20 用户指出初版七层不 MECE 后修订），既有 blobs／corpus／kb／meta／pages 作为 legacy 层平移进去，只读保全。
- 所有代码经 `project.dataset_root()`（环境变量 `DEMIWTG_DATASETS_ROOT` 可覆盖）解析数据根，不保存硬编码位置；2026-09-20 已完成 pipeline_v2 各入口、legacy 四入口、image_preannotate、common.blob_shas、downstream/runtime、taxonomy 工具、benchmark/edit、viewer、collect 本地写端常量（relay_b2／import_blobs／merge_meta）的适配，回归 398／4 与基线一致。
- 冻结记录中的旧路径经 `curation/layout/relocations.json`（新增 `datasets/demiwtg` → 新根绝对映射）＋`pipeline_v2/paths.resolve_artifact` 解析，不改写历史 JSON；V1 冻结代码不带该扩展，重放 V1 run 时数据集路径需显式传入。
- `datasets/demiwtg/meta/{taxonomy,concepts}.json` 随迁移离开 Git 追踪（工作区显示删除）；其权威转入数据根，taxonomy 的 Git 审阅形态后续按总方案 §11 由 Lance 发布生成确定性 diff 到代码模块。
- 无软链接；仓库内不残留 datasets 目录。物理搬移于 2026-09-20 16:01 完成（同文件系统 rename，13ms；前置：运行中的 visual_pipeline 退出＋blobs 静默＋无写端句柄）。搬移后验证：images.jsonl 可读、blob_shas 经新根精确计数 2,127,682、AssetReader 逐字节 SHA 校验通过、分层骨架 catalog／raw／reference／intermediate／annotations／evidence／releases 就位。

### 主数据入湖与 master/ 层（2026-09-21，用户确认，已执行）

依据：用户确认 taxonomy、concepts 是驱动采集与 pipeline 的基础主数据，按业务角色纠正目录归属。方案 [curation/layout/master_data_reorganization_plan_20260921.md](curation/layout/master_data_reorganization_plan_20260921.md)，执行记录 [curation/layout/master_data_migration_report_20260921.md](curation/layout/master_data_migration_report_20260921.md)。

- 湖内新增 `master/` 层：`concepts/v1`（385,292）、`taxonomy/v1/{nodes,edges}`（21,409/21,408）、`memberships/v1/concept_taxonomy`（446,775，挂载关系**权威表**，自旧 nodes.instances 原文提取，零重复/零悬空）。历史 taxonomy v31 CSV 包归 `raw/taxonomy_sources/v31_20260824/`（64,221 行，来源保全）。
- 发布 `master_data_v1_20260921`（release_kind=master_data）：四表固定版本组合，登记前全量对账（内容摘要逐字段等价、树/外键/挂载重建检查 16 项全过）。消费经 `taxonomy.master_data`（`resolve_master_release`/`open_master_table`/`mount_map`/`taxonomy_tree_compat`）按完整 release 读取；默认 release 是项目配置（`DEMIWTG_MASTER_RELEASE` > `DEFAULT_MASTER_RELEASE`），禁止四表各自取 latest 拼装。
- 旧 `derived/concepts/v1`、`derived/taxonomy/v1`、`releases/taxonomy/v31_20260824` 为 legacy 只读（冻结引用保留，不删除、不移动、不改登记）；`nodes.instances` 与 `concepts.taxonomy` 降级为兼容投影/只读快照，新增/移除挂载只写 memberships 新版本后发新 release。
- 迁移 run 记录与旧→新映射：`datasets/runs/master_migration/master_data_reorg_20260921/`（baseline/manifest/reconciliation/anomalies 四件）。

## 1. 项目分区

```
demiwtg/
├── viewer/                     # 【代码】查看器闭环：tag_tree_explorer.html + build_viewer.py + build/ 产物（gitignore；英文平行页已随 2026-09-06 统一版退役删除）
├── benchmark/                  # 【代码】评测基准：按三大题型拆成 vlm/、t2i/、edit/ 三子模块（抽样-出题-判分流水线 + reviews/ 下 question_dev/results_review notebooks）；评测数据不入 git：t2i=bench200/+archive/+data/，edit=无 data/ 层（批次目录、archive/、活素材全落子模块根，见架构决策 2026-09-05）；bagel/=第 4 场景（BAGEL-7B-MoT 官方基准评测：README/results_review.ipynb/gen+vlm 脚本入库，data/ 与 vendored 官方仓不入库）
├── taxonomy/                   # 【代码】标签体系维护与富化（audit_nodes / mount_map / gen_taxonomy_kb / gen_instance_kb / upgrade_v31；2026-09-05 还原盘起提升根目录）
├── curation/                   # 【代码】V4策展编排（v4/，demiflow底座）＋公共预标注工具；历史代码按archive/pre_v1、v1、v2、v3、shared、legacy_tools归档
├── bagel/                      # 【代码依赖】Bagel 官方模型包（Bagel/ 训练/推理代码 + 自研 run_wkbench runner；2026-09-05 起入主仓；2026-09-21 降级为常规保留依赖、非项目重点——BAGEL-7B-MoT 权重 28G 迁至工作区 models/、原址留软链、全部引用无感；eval/vlm/data 重物仍 gitignore；见架构决策 2026-09-05 / 2026-09-21）
├── modelhub/                   # 【子项目】LLM 网关（LiteLLM）+ 静态出口代理（mihomo）：本地 vLLM/Galaxy 直连、OpenRouter 走静态住宅 IP；独立仓库，整体不入主仓（见架构决策 2026-08-25）
├── .venv/                      # 【环境】项目公共 Python 环境（conda py3.10，torch 2.6+cu124；原 bagel/env，2026-08-24 提升为公共并由 env/ 改名；不入 git）
├── datasets/                   # 【纯数据】数据集根目录（一数据集一目录；原 data/datasets/，2026-08-24 升为顶层）
│   ├── demiwtg/                #   自建数据集 demiwtg（硬约束见第 2 节）
│   │   ├── blobs/              #     图片原始字节区（内容寻址，不可变，不入 git）
│   │   └── meta/               #     真相区：images.jsonl（统一权威主清单，2026-09-08 由 instance_images.jsonl 更名）+ taxonomy 两件套（taxonomy.json/concepts.json 入 git；2026-09-06 起中英统一，英文平行件与 alias_western 已退役；2026-09-07 instances.json 概念化为 concepts.json）
│   └── .../                    #   开源数据集落盘区（danbooru2024/coco2017 等，不入 git）
├── state/                      # 运行时状态，按模块归属分子目录（不入 git）
│   ├── collect/                #   datasets/（下载过程脚本，只读归档）+ v1 遗留运行时状态（死信/health/runs，只读归档）+ concepts_docs_draft.jsonl（docs 层摘要草稿）+ query_terms_cache.json（检索词运行时缓存）+ docs_clean/（历史清洗产物的兼容链接，已归档；新编排禁用）
│   ├── dataset_index/          #   COCO 标注缓存
│   ├── taxonomy/               #   taxonomy 模块 LLM 断点缓存与审计报告
│   ├── curation/               #   curation 历史分析残留（标签树 CSV、watermark 实验产物等）
│   └── .lancedb/               #   Lance 查询索引
├── logs/                       # 运行日志（不入 git）
├── AGENTS.md                   # 唯一权威约束/说明文档
└── README.md                   # 极简指针，只指向本文档
```

- `datasets/` 下**只是数据存储**：任何代码、页面、生成产物都不许放进去。
- 代码只允许放在顶层 `taxonomy/`、`curation/` 与 `viewer/`、`benchmark/`（2026-09-05 还原盘起模块提升根目录）。
- 仓库顶层禁止新增散落的脚本或数据目录（`datasets/`、`state/`、`logs/` 是明确登记过的例外；`bagel/` 为登记的子项目例外（2026-09-05 起入主仓；2026-09-21 降级为常规保留依赖、非项目重点，权重已迁工作区 models/、原址软链），内部布局自治，不受本仓模块/数据边界规则约束，评测数据等重物仍不入 git；`modelhub/` 为登记的独立子项目例外（LLM 网关 + 静态代理），内部布局自治，同不受约束；`.venv/` 为登记的公共环境例外，只放环境不放代码）。
- 常规文档为 `AGENTS.md`（约束）与 `README.md`（指针）。**明确例外（2026-09-10，用户要求固化研究方向）：[`curation/pipeline_memory.md`](curation/pipeline_memory.md) 为 curation 模块知识核心集的长期设计约定。** 新增此例外的理由是防止后续策展、审核与出题偏离用户已确认的研究目标；不是恢复历史过程文档。其他历史过程文档（docs/、子目录 README）仍不恢复，过程记录看 git 历史。
- **Pipeline文档合并（2026-09-18，用户明确要求）**：为统一设计、实施状态和交接，将原DESIGN.md、KNOWLEDGE_PIPELINE_HANDOFF.md、IMAGE_BACKFILL_HANDOFF.md合并为`curation/pipeline_memory.md`，保留设计章节编号及完整历史记录；原文件只留跳转，后续统一维护新文档。模型prompt源码和notebook仍独立，仓库级约束继续由本文件管理。
- **相关工作必读**：修改 curation 模块知识核心集的提取、筛选、审核及策展校准流程前，先阅读 `curation/pipeline_memory.md`。其中的研究设计约束适用于这些工作；存储和布局遵循本文件。当前代码并未全部符合设计约定，不得以现有实现反向替代设计标准。
- **图片预标注入口（2026-09-10）**：`curation/image_preannotate.py`；用户已授权本地 8000 Qwen 小批验证后全量处理 images.jsonl 清单关联图片。协议与任务边界见 `curation/pipeline_memory.md` 第 11 节，状态与结果在 `state/curation/image_preannotation_v1/`，不回写权威清单或人工标签。

### Curation归档与V4编排（2026-09-14，用户授权的架构调整）

理由：用户要求历史实验按V1／V2／V3归档，V4独立，并在curation内使用已安装的demiflow编排。以下分工替代旧“curation根目录pipeline.py为现役入口”的说明；历史记录与评分不覆盖。

- `curation/v4/`为新流程；入口`/yzp/zhaozy/yangzepeng/0905/env/bin/python -m curation.v4.flow`支持inventory／prepare／review材料准备；`-m curation.v4.pipeline`支持小批本地模型候选提取及逐阶段停靠、检查、续跑。业务算子、来源适配、材料契约均在此，不能依赖archive中的旧实验实现。用户本次明确要求先小批实现pipeline、逐过程审查，故接入现有本地Qwen，不启动正式出题或改评分，暂停notebook工作。机器候选不能自动转为已核验知识。
- `curation/archive/pre_v1/`为首轮12题之前的核心集、校准与知识probe；`v1/`为首轮及非物体／场景补充；`v2/`为expansion20；`v3/`为version3_20；`shared/`为跨版本旧运行器、展示、诊断和未采纳评分草案；`legacy_tools/`保存此前_archived内容。不能把早期core_pilot_v3误当第三版20题。
- `curation/pipeline_memory.md`继续是长期约定；`common.py`、`blob_presence.py`及图片预标注／守护／启动脚本继续服务当前任务，未当实验退役。现有服务不重启、不换模型；公共工具不依赖归档实验。
- `curation/knowledge_application_v1`与`curation/_archived`为旧路径兼容符号链接；`curation.__path__`保留旧core／pipeline等导入兼容，不能将这些兼容入口误当V4实现。归档代码仅调整路径和可核验的冻结哈希兼容，不修改冻结题目、输出、评分或请求；原始源码快照与移动映射由`python -m curation.archive.manage verify`核验。
- 运行数据仍在`state/curation/`；历史实验数据目录与case notebook原位置不变。V4试运行放`state/curation/v4/`。其中内部ID登记为新结构的试运行注册表，不修改现有concepts.json主键、权威taxonomy或新旧清单；全库身份合并／拆分与正式迁移另行实现。
- 模型配置、输入、版本和状态按pipeline_memory.md 设计第21节执行。demiflow并发与落盘成功不等于知识已核验；当前入口验证不宣称全库覆盖、COS取图接通或V4题目已完成。

### V4 notebook代码位置（2026-09-19，用户明确要求）

活动notebook及其生成／展示代码放在`curation/v4/`代码区，运行数据仍放`state/curation/v4/`。最终题目集中展示在`questions20_review.ipynb`，评测整体／逐步链为`evaluation_debug.ipynb`与`evaluation_stepbystep.ipynb`；实现放既有`evaluation/native/`分区，避免影响独立知识生产源码冻结。历史冻结的源码快照及revision notebook属于运行证据，保留原样，不作为新源码入口。理由是用户要求代码可审阅、题目可集中阅读。用户要求接入judge后，以上两册已扩展为判据核验／冻结→作答→标准`map_prompt_async`判分→配对消融的整体评测链；CLI默认准备零模型调用，已有答案可`--judge-only`续判。随后用户明确要求由“答题”改名“评测”并实跑20题、judge用子代理，因此统一上述入口，旧版册保留为`evaluation_legacy_debug.ipynb`且内容不改；公共CLI仍兼容旧manifest。实现见pipeline_memory.md第102节，后续真实运行记录按本文当前状态维护。

### 知识整理端到端入口约定（2026-09-14，用户明确更新）

理由：旧小批使用了历史临时清洗文件，无法检验从采集材料到知识库的完整链路。当前概念知识整理的入口限定为datasets中的采集原始材料及必要元数据；整体目标输出为干净、可追溯、带审核状态的知识库，详见curation/pipeline_memory.md第25节。页面保存格式不一定是HTML，采集正文与派生文本必须区别。

- 不将历史clean_docs、拼接摘要或旧候选当新编排输入；解析、规则清洗、过滤、去重等需要的能力重新编排为现役版本化算子，可复制改造旧代码，但不运行依赖archive的业务链。继续复用demiflow执行底座。
- 历史4件清洗产物已移至state/curation/archive/legacy_processing/docs_clean_20260908/，原state/collect/docs_clean路径仅保留历史兼容链接，校验清单为同级docs_clean_20260908.sha256。旧脚本已归档不再迁移。旧实验可读，不修改冻结输入输出或评分。
- 现役入口已移除clean_docs并拒绝用含其的旧材料包启动新提取；inspect/status仍可查看历史结果。新清洗和最终知识库验收尚未实现，不宣称已端到端完成。运行结果仍放state/curation，代码在curation，不回写datasets原始材料或迁移权威概念名契约。

### 逐算子调试入口（2026-09-14，用户最新要求）

用户已重新授权notebook，覆盖此前暂停决定。入口curation/v4/knowledge_debug.ipynb逐cell经demiflow运行真实业务算子；数据展示支持limit及固定种子抽样。源码与运行输出分别在curation和state/curation。新增流程内CleanMaterials（cleaning.py、knowledge_stages.py），在identity前从采集材料生成可追溯清洗版本，不读取历史clean_docs。清洗初版与审核限制见pipeline_memory.md 设计第26节；默认notebook执行至清洗，模型步骤显式配置后逐cell调试，当前不宣称知识库端到端质量验收。此条更新上一节“清洗尚未实现”的状态，原始数据和历史实验仍不修改。

### 采集记录数据流取代概念查询入口（2026-09-14，用户要求）

现役入口改为`python -m curation.v4.record_flow`及`curation/v4/knowledge_debug.ipynb`。按采集文件逐条处理，顺序记录概念页面对应、附加已有概念关联，再通过可选ID／固定种子采样过滤；后续读取、清洗、材料关联和知识算子不接收入口ID列表。小批与完整文件处理共用实现，关闭过滤／采样／读入上限即可使用同一主线。记录与关联落盘state/curation，demiflow执行流式算子，不将全量材料装入列表。细节及当前语义覆盖缺口见pipeline_memory.md 设计第27节；不宣称全库或跨窗口知识已核验。

旧flow.py／pipeline.py查询批次入口保留历史兼容、status／inspect及共享实现，不能继续称为现役端到端主线。旧notebook保留在其历史run中的knowledge_debug_query_snapshot.ipynb；原始datasets、历史结果、评分和运行服务不改动。

### 概念驱动知识整理，资料层共享执行（2026-09-14，用户再次明确）

理由：用户确认业务主线为“概念→原始资料→多模态知识”，此前将避免重复扫描误解为以材料行决定业务入口。现役入口更新为`python -m curation.v4.concept_flow`，notebook仍为`curation/v4/knowledge_debug.ipynb`。此条覆盖上一节将record_flow作为业务主线的说明；其流式读取、磁盘索引、共享清洗、断点复用保留为底层能力。

概念过滤／采样在资料关联之前执行；未入选概念不因共享资料自动进入任务，无资料概念保留缺口，未知／歧义材料单独保留待识别。资料和知识分别保存并关联，概念结果汇总不能替代跨材料语义整合。具体当前实现、标识及限制见pipeline_memory.md 设计第28节。仅本流程冻结且解析器与原始文件版本一致的原始读取结果可复用，历史clean_docs和旧清洗／知识输出不作为这轮原始输入；原始datasets、评分、历史输出不改写。

### 分类型Dataset与显式关联（2026-09-14，用户明确要求）

理由：通用kind/record封装遮蔽概念、文档、图片的字段与关联过程。现役notebook和命令行转为`curation/v4/dataset_flow.py`，业务schema在`curation/v4/datasets.py`：分别保存概念、文档、图片、关联、全文、清洗、图片检查及知识表，字段直接可见；同schema分片可以进入同类Dataset。demiflow负责惰性Dataset与算子执行，已安装版本没有通用join API，因此使用显式SQL关联并将结果流交给demiflow，不伪称调用了不存在的API。concept_flow/record_flow保留原始读取及旧知识算子的内部兼容能力，不再作为业务表契约。

原始完整字段在独立来源追溯存储中保留，不能把来源额外字段作为事实默默丢弃，也不让统一record封装贯穿业务算子。文档与图片分别关联，避免笛卡尔积；输入输出字段、关联键、未匹配行在notebook可看。知识输出仍是机器候选，身份及跨窗口整合限制见pipeline_memory.md 设计第29节。原始datasets、已有实验及评分不改写。

### 三条Dataset扩列、按需汇集（2026-09-14，用户采纳第三种方案）

理由：用户认为第29节逐处理步拆表过散，明确选择概念、文档、图片分别处理／扩列，必要时才嵌套联合处理。现役交互入口仍为knowledge_debug.ipynb，使用column_flow.ColumnFlow。文档链实际经demiflow map_async连续读取、保存读取列、清洗、保存清洗列；图片链独立扩展字节检查列；概念选择和覆盖计数扩在concepts。关联索引、来源追溯和断点为底层实现，不作为必须逐表查看的业务主线。详情见pipeline_memory.md 设计第30节。

读取／清洗／图片检查的旧分步表名在新run中仅作兼容视图，不保存独立步骤输出。新run保留原始字段和完整扩列结果，不覆盖历史run。仅在需要身份匹配或多材料推导时按概念汇集，继续保留联合核验与知识审核边界，不提前把所有资料嵌入概念。

### demiflow 原生算子主线，取消 SQLite（2026-09-14，用户明确要求）

理由：用户要求使用 demiflow Dataset 算子串联，不再用 SQLite／SQL 实现资料关联和处理。现役入口更新为 `curation/v4/stream_flow.py:StreamFlow`（命令行 `python -m curation.v4.stream_flow`）与 `knowledge_debug.ipynb`，覆盖前述 column_flow／dataset_flow 主线决定。直接从原始 datasets 读入，概念、文档、图片分别扩列，必要时分批汇集。通用 join、reduce_by_key、group_batches、map_cached、checkpoint 已扩展到兄弟 demiflow 仓库并安装到公共环境，当前 local backend 采用可落盘排序和文件缓存，不调用数据库。具体语义和限制见 pipeline_memory.md 设计第31节。

旧 SQLite 编排代码与运行保留历史兼容，不作为新主线输入。原始数据、blobs、旧实验、服务和评分不变。知识阶段沿用共享算子并保存文件，默认不重新发模型请求；本次执行方式修正不等于身份、冲突、图像支持及最终知识库质量已经验收。

## 1.5 标签体系数据契约（两类独立资产，定死；2026-09-21 起真相在湖内 master/，meta/ 两件套已随退役删除）

> **真相位置迁移（2026-09-21，已执行）**：本节数据模型与词汇纪律不变，载体从 `datasets/demiwtg/meta/{taxonomy,concepts}.json` 换为湖内主数据四表（`master/concepts`、`master/taxonomy/{nodes,edges}`、`master/memberships`，发布 `master_data_v1_20260921`）。挂载关系真相从「树节点 instances 名单」移至 **memberships 表**；树/概念旧形态经 `data_access.master_data.taxonomy_tree_compat` 等兼容投影只读生成，不恢复 meta JSON 为可写真源。下方文件形态描述保留为历史契约参考。

整个标签体系**只存在两类资产**——树（taxonomy.json）与概念（concepts.json），代码、数据字段、文档一律使用这两个词，禁止再引入其他分类术语（category、leaf、root 已废除；instance/实体 一词由 concept/概念 取代，2026-09-07）。数据模型以本节为准（原 schema/tag_taxonomy.schema.json 已删除：无校验消费者，勿恢复）。

**两类资产彻底解耦（架构决策 2026-08-19；2026-09-07 概念化迁移沿用）**：概念是资产，taxonomy 是视角。概念的生灭与富知识完全不依赖树；树只是展示/导航视图，同一套概念未来可被多套树视角引用。挂载关系的**真相**写在树一侧（节点 instances 名单）；concepts.json 行内的 `taxonomy` 字段是**快照**（消歧与源路由用，树变更后跑 `curation/migrate_concepts.py --refresh-taxonomy` 刷新）。原 build_unified.py（树推导实例表的重建器）已删除：它维护的正是被废除的耦合。

**概念不区分 class/individual（SKOS 语义：统一主键空间）**。新概念准入规则：可指称（收「词」不收「算式」，成员拼装键禁止入库）、可复现（跨题复现频次 ≥K）、有供给（通用图源可搜、VLM 可认）。

### taxonomy.json —— 树（展示视角）

```
{ "schema_version": "...", "meta": {...}, "tree": <node> }

node = {
  name: str                    # 节点显示名
  path: str                    # 完整路径，' / ' 分隔，从根『demiwtg』起算（前缀精简：域直挂根，无中间层）
  depth: int                   # 根为 0
  children?: [node]            # 子树；末端节点省略
  instances?: [str]            # 挂在本节点下的概念名列表（对 concepts.json 的引用，挂载关系的唯一真相落点）
  knowledge_intro?/aliases?/representative_cases?/related_tags?: [KB 字段，可选；knowledge_intro 为 150-350 字维基百科词条风格]
}
```

### concepts.json —— 概念（独立权威源，统一主键空间）

```
{ "schema_version": "...", "meta": {...}, "concepts": [concept] }

concept = {
  name: str                    # 概念主键（全局唯一；正名纪律：定了一次不再动，变化由 aliases 吸收）
  aliases: [str]               # 别名/英文名（身份字段：判重与英文源路由；无别名为 []）
  carriers: str                # 载体："image+text" | "text"（链路由：图像采集线据此跳过 text-only 概念）
  taxonomy: [str]              # 挂载路径快照（' / ' 分隔，从域起算，树遍历序；真相在树，仅供消歧与源路由，不承载知识）
}
```

- **概念独立于树**：未挂载任何树节点的概念是合法状态（taxonomy=[]，待认领池）；增删树节点不造成概念的创建或删除。
- **`name` 全局唯一是硬约束**：一个概念一条记录；多处挂载表现为多个树节点的 instances 名单同时含该名字（行内 taxonomy 快照同步多路径）。
- **退役字段（2026-09-07 概念化迁移，历史 schema 溯 git）**：`desc`（52,980 条）→ `state/collect/concepts_docs_draft.jsonl`（docs 层草稿：{name, kind: summary, body}；被消费后另批转正）；`query`（52,980 条）→ `state/collect/query_terms_cache.json`（{name: [检索词]}，采集 planner 冷启动先验——检索词从静态资产改为运行时状态）；`source` → 行内退役（迁移时分布存 concepts.json meta.source_stats：derived 330,842 / llm 54,262 / curated 158）。
- **英文平行两件套已退役（架构决策 2026-09-06）**：EN 实体对齐后成为中文概念的 aliases 或独立概念，英文知识以别名形态存活；四个平行文件物理移出 meta/ 归档 state/taxonomy/retired_meta/，search_kb --lang en 与 viewer --lang en 入口拒绝退役提示。
- 图片打标只存**概念名**（标签不含路径）——体系演化（改路径/重生成树）不需要迁移图数据。看图入口（viewer 的 build/imgs.js）由 meta/images.jsonl 的 instances 字段现场聚合（字段名 instances 沿用 demiwtg-data 采集链契约不改，语义=概念名）、相对路径指到 blobs 原图（相对 viewer/ 的 ../datasets/demiwtg/blobs/...），不再建软链树。
- 数据字段定义即契约，改字段 = 改本节 + 同步全部消费代码。

## 2. datasets/demiwtg/ 硬约束（定死，逐条执行）

### 2.1 blobs/ —— 原始字节区（不可变）

```
datasets/demiwtg/blobs/<aa>/<sha256>.<ext>   # aa = sha256 前两位；sha256 = 文件内容哈希
```

- 图片**只增不删、不重命名、不改动**。
- 新增图片必须：先算内容 sha256，再按 `blobs/<aa>/<sha256>.<ext>` 落盘；已存在同名文件则直接跳过（内容寻址天然去重）。
- 文件名中的哈希**必须是文件内容的 sha256**，禁止沿用下载器给的不可信文件名。
- 删除任何旧图片目录之前，必须逐文件验证其内容已存在于 blobs（sha256 比对），否则先并入 blobs 再删。

### 2.2 meta/ —— 真相区（只放真相，别的什么都不放）

**允许的文件（穷举，不允许出现清单之外的东西）：**

| 文件 | 角色 |
|---|---|
| `images.jsonl` | **统一权威主清单**（2026-09-06 起统一，时名 instance_images.jsonl；2026-09-08 更名复用简名 images.jsonl）：去重键 (sha256, instance) 一行一对，逐实例炸开；含 EN 并入行（实例名已归一为中文正名，new 实体保留 EN 名）与原 v1 images.jsonl 并入行（identity/focus/quality=null 待 annotate_backfill 补标）；VLM 补标、质量门、viewer imgs.js 均以它为单一来源（v1 同名退役件在 state/taxonomy/retired_meta/，同名不冲突，勿混淆） |
| `taxonomy.json` | 标签体系树（展示视角，权威源，入 git；含并入的 EN 新实体挂载） |
| `concepts.json` | 概念资产库（统一主键空间权威源，入 git；四字段契约见 1.5；2026-09-07 由 instances.json 概念化迁移而来，历史 schema 溯 git） |
| `.meta.lock` | 跨进程写锁（运行时瞬态） |

**禁止出现在 meta/ 下的东西：**

- ❌ 审计日志（只写不读的账本一律不建；先有读取代码才允许写入）
- ❌ 备份文件（*.bak-*、*.bak-sync 之类）
- ❌ 派生索引（LanceDB、实例名→图反向索引等；需要时由消费者从 images.jsonl 现场聚合）
- ❌ 运行时状态（死信队列 sqlite、健康账本、done flags、COCO 缓存）

**判据（新增任何文件前先回答）：**

1. 有消费者吗？——**必须先有读取它的代码，才允许写入它**。
2. 是真相还是派生？——派生的东西不进 meta。
3. 删掉它会丢数据吗？——丢了数据才是真相；能重建的不进 meta。

### 2.3 运行时状态在顶层 state/（不属于数据湖，按模块归属分子目录）

`state/collect/`：下载过程脚本（datasets/download_all.sh、character_resume.sh、hf_mirror_hf.py，只读归档；HF 数据集落盘区已迁至 `datasets/`）；v1 遗留状态（死信队列 `.dlq_*.sqlite3`、`source_health.json`、`runs/<run_id>/`、`source_registry.jsonl`）只读归档不再写入；`state/dataset_index/`：COCO 缓存；`state/.lancedb/`：Lance 查询索引；`state/taxonomy/`：taxonomy 模块 LLM 断点缓存与审计报告；`state/curation/`：curation 历史分析残留（标签树 CSV、watermark 实验产物等；评测数据已迁入 benchmark/，见架构决策 2026-08-24）。代码约定：仓库根由 `--meta`（默认 `datasets/demiwtg/meta`）向上三级推导（datasets/demiwtg/meta → 仓库根）。永远不进 meta/、不进 datasets/、不进 git。

### 2.4 一致性规则

- `images.jsonl` 是唯一真相（前身链：metadata.jsonl（2026-09-06 更名）→ instance_images.jsonl → images.jsonl（2026-09-08 更名复用简名，用户拍板，见 2026-09-08 决策块））；**不建任何派生索引文件**（历史上先后废除的派生件：instance_images.json（旧实例→图反向索引，2026-08-21 废）与 v1 images.jsonl（2026-09-06 退役，其名 2026-09-08 起被现清单复用）；双份存储有一致性漂移风险；需要实例名→图关系时由消费者从 images.jsonl 现场聚合，如 viewer/build_viewer.py）。
- `images.jsonl` 的 instances 字段只应是当前体系的概念名（字段名沿用 demiwtg-data 采集链契约）；体系演化后残留的死名打标从 images.jsonl 剥离（无隔离区）。
- 一张图的 instances 变更（改名/隔离）改的是 images.jsonl，**图字节不动**。
- 新元数据字段设计时必须先问"哪个消费者读它"；答案为空就不加。

## 3. 代码模块职责


> **知识核心集试点补充（2026-09-09）**：当前默认 `state/curation/core_pilot_v3`，只调用本机8000上的 `qwen3.8-27b`（仍兼容8001；4001付费网关硬拒绝）。新版图片协议独立判断 T2I 与编辑：完整T2I考点要求 full 覆盖且无未支持部分；编辑允许结构和状态改变，要求可见锚点、充分初始条件与知识依赖，不要求源图已符合目标知识。图片可视化自报值不是删除闸门。`fork --reuse-docs` 仅在相同文本协议下复用 calibration 知识候选；`compare` 对同知识/同图片比较协议结果，变化不等于准确率改善；`run --repair-invalid` 显式限额重试并保留原输出与反馈。人工可纠正图片结果，原模型结果不覆盖；更改知识陈述仍需新批次重核关联图片。`comparison.json`、`review_hints.json` 与 `report.json` 的材料缺口由审核入口消费；助手提示不算人工标签。图片来源只区分已声明生成与未核实，不把网络来源推断为真实照片。



> **架构决策（2026-09-09，小规模知识核心集）**：用户授权将旧 curation 归档后建设新流程。现役入口为 `python3 -m curation.pipeline`，模块为 `core.py`（存储/校验/人工准入）、`prompts.py`（六类知识内容与模型协议）、`pipeline.py`（选材/物化/推理/收录/统计/导出）、`review.py` + `review.ipynb`（人工审核），历史 `_archived/` 不作为新流程依赖。知识内容与29域分离；概念、树和湖文件只读，不给 concepts.json 恢复 desc 等退役字段。运行产物在 `state/curation/<run>/`，由流水线和审核 notebook 消费：manifest 冻结概念/来源片段/图片候选，tasks 绑定输入哈希，results 保存校验通过的模型候选，reviews 保存人工决定，report/core 为可重建统计与导出。先有来源支持与条件核验、再看图片证据；模型候选不自动成为核心集，fact 与 evidence 均人工接受且对应任务可用才导出。旧门通过/拒绝/未标注三组保留；六类知识多标签不作难度计数。calibration/holdout 按概念隔离，后者需人工校准后 freeze 才能推理/收录；不宣称未见 benchmark 泛化。命令顺序：prepare → render docs → run docs（显式 --limit）→ render evidence → run evidence → notebook/review → report/export。**用户明确限定仅使用本地 Qwen3.8-27B；4001 属付费网关，任何付费接口使用前必须另获明确确认。** 当前 runner 硬限制本机8000/8001、模型 qwen3.8-27b，禁代理及重定向；不启动/停止用户模型服务。事实或提示词需修改时使用新 run，旧模型结果不覆盖。


> **简单实拍补充（2026-09-07，用户拍板）**：已完成的 23 题原样计入总量，剩余题适量增加简单实拍，避免过度降低整体难度。当前选图为 55 张照片候选与 145 张生成图，其中 19 张采用独立 `synthesize_prompt_edit_v6.1_simple.md`；原 v6.1 标准协议不变。简单配置沿用原脚本的证据、图题绑定与收录校验，取消多跳及高义务数量门槛；`construction_profile=simple`、`difficulty=simple` 显式区分，`level` 只保留 T2I 逐实例参考层级，不宣称为编辑实测难度。新增清单合并为 bench200/source_review/combined_sources.jsonl 供 emit-plan 消费；最终比例按成功题实际来源统计。

> **选图补充（2026-09-07，用户拍板）**：edit bench200 改为采集照片候选与生成图共同覆盖同一 200 实例，一实例最终一图一题，已完成合格题保留。用户明确指定可复用 `_staging/benchmark/t2i/data/` 的历史采集源图副本；不改湖、不重抽实例。`eval_complexity.py prepare-mixed/select-mixed` 以历史样本质量快照和成功复杂度记录初筛，再由显式 `gpt-5.6-sol/high` 子代理逐图补核身份、实拍外观与编辑适配；缺 identity 不从 kb_match 推断。复核通过的图片按 SHA256 原字节复制到 edit/focus200/collected/，清单在 bench200/source_review/selected_sources.jsonl，由 eval_synthesize --source-manifest 消费；原生成图清单不改。照片优先；生成图沿原层级素材偏好，并复用 rank_key 在同批次内择复杂图，不能混用照片 10 分制质量与生成图百分制评分。候选失败沿原三步尝试序列补位，最终来源以 questions 的实际绑定为准，统计分来源并按 level 分层。

> **架构决策（2026-09-07）**：edit 正式出题批次落 `benchmark/edit/bench200/`（运行数据不入 git），沿用 v6.1 出题协议与 `eval_synthesize.py` 的选图、类型尝试序列、削峰及严格机审。实例顺序和难度来自 `benchmark/t2i/bench200/questions.jsonl`；pilot 20 题及其计划原样保留并计入 200 题总配额，只补其余 180 个实例。出题不用 OpenRouter/API，使用显式指定 `gpt-5.6-sol` / `high` 的全新子代理；每题只给物化 md 与绑定源图，不继承调度对话；必须完整读取 md 到文件末尾，不能截取前 240 行而漏掉末尾批次调整。后续 render 从同一校验函数显式附带原有定位方式数量及机械格式自查，修复原 v6.1 表格漏列定位门槛的问题；不修改冻结模板与任何既有验收门槛，旧渲染/题目保留。离线流程为 emit-plan → render-question → 子代理裸 JSON → ingest-question → validate；输入哈希与模型配置保存在批次 dispatch 供收录校验消费。正式判分配置由用户定为修订 QIB v2.2 + `gpt-6-astra` / `medium`，覆盖下文旧 sol 定案；此次先完成出题。

> **架构决策（2026-09-08）**：权威主清单更名 + 图片部分还原（用户拍板两则）。① `meta/instance_images.jsonl` 更名 **`images.jsonl`**（复用简名，实质推翻 2026-09-06「避开 images.jsonl 旧名防混淆」的命名决策——v1 同名退役件仍在 state/taxonomy/retired_meta/，同名不冲突但文档口径以本条为准）；主仓消费端同步：viewer/build_viewer+HTML、benchmark eval_sample×3/eval_complexity/focus1000_caption/focus1000_merge_lake、curation annotate_backfill/focus_sample/search_kb + dataset_analysis.ipynb（共 12 文件）；meta_unify.py 加退役守卫（历史一次性脚本，照 upgrade_v31_en 先例，字符串留作历史记录）。② 图片还原（目标：减少后续下载量）：Sep 5 tar 备份（目录 `/yzp/zhaozy/yangzepeng/0905/1/`，19×4GiB 分片 = 123云盘 DIR"1" 的全部内容）**流在 80GiB 处截断——原上传只到 part_as，缺 at+ 分片**（`.1.part_ad`/`.1.part_ao` 经 md5 证实为同内容重复下载件；云端无更多分片；`demiwtg_all.tar.gz` 10.8GB 为纯代码+结果备份，零 blobs）。已执行：tar 定向抽取 `datasets/demiwtg/blobs`（路径过滤绝不触碰 meta/，防 Sep-5 旧真相覆盖 concepts/taxonomy）还原 150,633 blobs/68GB（归档序前 19 个 sha 前缀目录）；`curation/harvest_blobs.py` 从本地评测样本副本（benchmark/_staging/bagel）按内容寻址收割 +1,453（preloss sha 过滤，生成图不进湖）；`curation/replay_manifest.py` 按 blobs 实存回放 preloss 清单（抽样 64/64 文件名==内容 sha）→ images.jsonl 194,449 行 / 152,081 blobs（含 1,117 无清单行孤儿 blob，blobs 不可变留置）/ 134,941 概念有图 / 质量门合格口径 15,191 概念；viewer 重建（imgs.js 26.3MB，30,565 概念有图；standalone 同步）。preloss 其余 ~196.6 万 sha 的 blob 自该备份不可恢复；云盘残余可选项：results_archive.tar.gz 9GB + taxonomy_sample_cases.zip 937MB（已删评测目录的归档，样本图约 2GB 为湖副本，可选补拉收割）。③ 同日晚用户拍板**恢复全量 preloss 清单**（推翻早间"清单只回放 blob 实存子集"的回放口径）：meta/images.jsonl 直拷归档件恢复 2,849,013 行——缺 blob 行含下载链接（99% 行带 content_url/landing_url），是集群补采的工作面而非悬空行；curation/replay_manifest.py --apply 封死（重跑会截断回 19.4 万子集），干跑保留为 blob 覆盖报表。**图片字节消费者按 blob 实存过滤行**（共用件 curation/blob_presence.py）：t2i/edit eval_sample 池过滤、eval_complexity build_pools、annotate_backfill scan_pending、focus1000_caption load_targets（viewer build_imgs_js 原有逐行 exists 守卫不变，重建验证 imgs.js 输出与子集期一致 26.3MB/30,565 概念）；仅排序/统计类消费者（search_kb 目标排序）不过滤。集群补采双产物（curation/export_cluster_coverage.py + export_refetch_min.py → state/collect/demiwtg_data_sync/）：lake_coverage_for_cluster.jsonl（blob 实存 194,449 行，集群 schema concepts 键——合并后 --skip-covered/配额/去重按湖内覆盖工作）+ **refetch_min.jsonl.gz**（补采工作清单主件：缺 blob 带 URL 行 195.8 万（同 sha 多概念合并），极简五键 {c:[概念], u:url, s:sha256, e:ext, src:源}，gzip 157MB=全字段版 2.4GB 的 7%——用户拍板"只留下载必要字段+概念加一列 url"减传输带宽；无 URL 10,319 行走常规检索线）；集群侧工具 refetch_missing.py（双格式自动识别+gzip 魔数解压；按源防盗链头下载 + sha256 复验唯一入库闸门 + 原子落 blob + norm_rec 补全 24 字段回写行 + 断点/死信轮；湖侧全量清单在册，回灌后按 (sha,concept) join 还原 license/author/打标元数据零丢失）与 merge_lake_coverage.py 已备 demiwtg-data 本地仓（ahead 待推送）。

> **架构决策（2026-09-07）**：instances.json → concepts.json 概念化迁移（用户拍板：改名 + 契约瘦身为四字段；2026-09-05/06「归一化概念体系」方法论讨论的真相层落地，采集交接件 concepts_batch_200.json 已先行新契约）。定案：① 概念行 = {name, aliases, carriers, taxonomy}，顶层 schema_version+meta+concepts；`curation/migrate_concepts.py` 一次性迁移 385,262 行（干跑→--apply，幂等防重跑；--refresh-taxonomy 供树变更后刷新快照），carriers 存量默认 image+text，taxonomy 快照从域起算、树遍历序（全量有挂载；多挂分布 1 处 348,122 / 2 处 25,574 / ≥3 处 11,566，存量按树实况全量保留，多挂≤3 为新概念策展纪律）。挂载真相仍在树——2026-08-19「挂载关系不持久化」就快照维度修订：快照只作消歧与源路由、不承载知识，禁手改、刷新走脚本。② 退役字段去向：desc → state/collect/concepts_docs_draft.jsonl（docs 层草稿 {name, kind: summary, body}，策展精修 105 条优先保留不覆盖，终态 52,980 条；docs 正式层待消费后另批转正）；query → state/collect/query_terms_cache.json（52,980 名，planner 冷启动先验——检索词从静态资产改为运行时状态）；source 行内退役（分布存 meta.source_stats：derived 330,842 / llm 54,262 / curated 158）。③ 消费端同步：viewer（build_viewer 读 concepts.json + docs 草稿 join 进概念行 docs 字段、sidecar 更名 concepts.js、缓存号 v5、HTML fetch 回退/查表键 window.__CONCEPTS__/stats 改「概念」、source 标签删除、standalone 同步；重建产物 concepts.js 115.8MB，imgs.js 空为图片全量丢失后预期态）；curation/focus_sample（mini 表改 concepts 键，四字段整条拷贝，产物名 focus*_concepts.json）；curation/search_kb（targets 改 concepts 键、--only-empty 判据改 docs 草稿名单、english_alias/all_aliases 的 query 半边改读采集缓存、source==curated 跳过删除）；taxonomy/gen_instance_kb 重写为概念富化器（aliases 回写 concepts.json + docs 追加草稿，不再生成 query/source，写盘持 .meta.lock 原子替换）；curation/annotate_backfill（kb 查表改由 concepts + docs 草稿现场构建，脱离 op_annotate.load_instance_kb）；benchmark edit eval_synthesize / dual_carrier_supplement 与 focus1000 caption/genimg/eval_complexity（desc 改读 docs 草稿，mini 表键兼容 concepts/instances 两代）；三册 curation notebook 路径键同步；taxonomy.json meta.description 自指更新；mount_map/README/.gitignore 例外链（!concepts.json，85MB < 原 98MB）。④ 同批修复 data/ 时代 ROOT 深度残留（focus_sample/search_kb/annotate_backfill/gen_instance_kb 原推导到仓库根上一层）与 import 面（顶层 taxonomy.* 直连；data/ shim 仅保留兼容存量 collect_v2.* 调用方）；§1 树形图同步顶层布局。⑤ 历史一次性脚本（en_entity_merge/meta_unify/upgrade_v31/upgrade_v31_en）不改造：读端对已删文件自然 FileNotFoundError 自守卫，历史溯 git。⑥ instance_images.jsonl 的 instances 字段名与 demiwtg-data 采集链契约不动（外部仓 --concepts 批任务模式已由集群侧上线对齐四字段契约；概念模式打标 kb 的 docs sidecar 补丁+bench283 种子 105 条已备 state/collect/demiwtg_data_sync/——本机无 GitHub 推送凭据，待 SG 授权机推送，2026-09-08）；30 个 candidate 新概念键已随人审合入（2026-09-08：+30 新键建议挂载写树生效、385,262→385,292，快照与树零失配校验通过；253 个既有概念批次与真相零差异无需同步；草稿留档 state/collect/concepts_batch_200.json）。

| 模块 | 职责 | 入口 |
|---|---|---|
| `taxonomy/` | 标签体系维护：树审计（audit_nodes 死叶子审查）、挂载聚合（mount_map，只读现算不落盘）、富化（gen_taxonomy_kb 节点 KB / gen_instance_kb 概念富化——aliases 回写 concepts.json、知识文本追加 docs 草稿，各一次 LLM 调用） | 各脚本 `--write` |
| `curation/` | 数据策展与检索接地：search_kb 概念知识检索接地管线（search_kb_sources 直供源扩充 / search_kb_supervise 全量跑监督；--lang en 赛道已随统一版退役）、annotate_backfill 补标驱动（kb_match=None 行 VLM 打标回写）、migrate_concepts（instances→concepts 迁移器与 taxonomy 快照刷新）、harvest_blobs（本地残留图副本按内容寻址回灌 blobs）、clean_docs_pages（集群 docs 页清洗出净版：原始 pages/ 只读，净版落 state/collect/docs_clean/，判定序 short_raw→low_density→nav_listing→listing_page→html_noise→gibberish→duplicate→keep）、en_entity_merge EN/ZH 实体合并（tier0/bulk/escalate/apply/orphans）、meta_unify meta 收口（images 退役并入 / en 归一并入）、focus_sample 重点补图池抽样、质量分析 notebook（download_quality / search_kb_quality / docs_analysis=docs 页质量分级与清洗呈现 / lake_sync_details=回湖同步明细抽样：两代 schema 盘点、打标完整度、缩略图墙、误绑探针）、数据集分析 notebook（dataset_analysis.ipynb，参数写在 cell 内部，直接运行：① danbooru2024 字段下钻；② demiwtg 权威清单分布与过滤；③ taxonomy 视角节点量级与抽样，只读） | 各脚本 `--help`；notebook 直接运行 |
| `viewer/` | 查看器闭环：页面 tag_tree_explorer.html + 构建脚本 build_viewer.py（读 taxonomy/concepts/docs 草稿/主清单四源，docs join 进概念行；imgs.js 只收录 VLM 打标行、每概念 top-50、caption 截断 100 字）+ 产物 build/（sidecar taxonomy.js/concepts.js/imgs.js 与 standalone 单文件，gitignore；英文平行页已随统一版退役删除）；HTML 与 build/ 同址是 file:// 双击可用的硬要求 | `viewer/build_viewer.py` |
| `benchmark/` | 评测基准：按三大题型拆成三子模块（见架构决策 2026-08-24 三子模块拆分）。**t2i/**（生成）与 **edit/**（编辑）各带完整四件套：抽样（eval_sample.py 分层配额，--filter 一条 duckdb SQL WHERE；edit 版默认叠加编辑适配门）、出题（eval_synthesize.py，Galaxy API；t2i 版含 facet 词表审计、edit 版 9 类 edit_type 轮转 + 每第 5 题知识编辑套）、判分（eval_score.py 调本地 vLLM judge，score/dump 子命令；t2i 版 FACETS 权威源 + φ 映射聚合，edit 版 EDIT_DIMS 三维钳制）、gen_results_review.py（生成审阅 notebook）；**vlm/**（理解）暂不拆代码，只放 notebook。每子模块两个 notebook（现在 reviews/ 下）：question_dev.ipynb（抽样+分布+题库审阅，for 题目构造）、results_review.ipynb（打分/评估结果分析）。评测数据布局见架构决策 2026-09-05（t2i：bench200/ 现行 + archive/ 历史 + data/ 默认落点；edit：无 data/ 层，批次目录 synth_v*/、活图池 focus200/、归档 archive/ 全落子模块根）；样本图/题库/判分产物均不入 git（.gitignore 登记）；出题/判分协议 md 在 prompts/、随代码入 git；编辑评分契约 edit/edit_score_prompts.json（ImgEdit 官方原文，随代码入 git） | 各脚本 `--help`；各子模块 `reviews/question_dev.ipynb` / `reviews/results_review.ipynb` |

> **架构决策（2026-09-21）**：BAGEL-7B-MoT 权重迁出 + bagel 子项目降级（用户拍板："bagel 可能不是项目重点，可以不作为一个子项目"）。① `bagel/Bagel/models/BAGEL-7B-MoT`（28G）同盘 rename 迁至工作区 `/yzp/zhaozy/yangzepeng/0905/models/BAGEL-7B-MoT`（与 Qwen/gemma/Z-Image 等同一模型仓），原址留相对软链 `BAGEL-7B-MoT -> ../../../../models/BAGEL-7B-MoT`；既有全部引用零改动无感——curation v0/v1/v2 的 evaluation/worker.py 与 bagel.py、run_wkbench.py 默认路径、benchmark/bagel 绝对路径，以及 `bagel/models`、`bagel/Bagel/models/BAGEL-7B-MoT`、`benchmark/bagel/data/models` 三条软链均实测解析到位；git 工作区零变化（`bagel/Bagel/models/` 本就 gitignore）。② 降级定性：bagel 由"被测/被训核心模型全链路子项目"降为常规保留依赖——代码仍在库、路径不动、pipeline 引用不变，顶层豁免（内部布局自治、重物不入 git）物理维持。③ 只迁权重、不动代码布局的依据（入库代码核验）：官方源码树仅 `inferencer.py` 晚于整体拷入且与上游 diff 逐字节一致（时间戳差异非内容改动），自研增量仅 `scripts/run_wkbench.py`（329 行评测 runner）——"纯官方 copy"不成立，故代码留在库内。
>
> **架构决策（2026-09-05）**：bagel/ 子项目入主仓 + benchmark/bagel/ 第 4 场景入 git（用户拍板，推翻 2026-08-23「独立 git 仓库 + 主仓整体排除」方案——该子仓 .git 已随旧机迁移不复存在）。定案：① 主仓 .gitignore 撤销未锚定 `bagel/` 整体排除，bagel/ 以普通目录随代码入库；重物仅定向排除：`bagel/Bagel/models/`（BAGEL-7B-MoT 权重 ~28G）与 `bagel/Bagel/eval/vlm/data/`（VLM 评测下载数据 mmbench ~50M）；上游 Bagel 自带 .gitignore（wandb/results/eval_results/notebooks/tests 等）在子树内继续生效；`bagel/models -> Bagel/models` 兼容软链随库入库；子项目内部布局自治不变。② 未锚定规则撤销的连带效应：`benchmark/bagel/`（第 4 场景：以 BAGEL-7B-MoT 为被测模型的标准基准评测，2026-09-05 物理整合，详见其 README 与 results_review.ipynb）此前被整体遮蔽未入 git，本次入库——仅代码与文档入 git（README、results_review.ipynb、gen/+vlm/ 脚本），data/（~1.2G 题库/出图/运行缓存）与 vendored 官方 git 仓（gen/qib_official、vlm/VLMEvalKit，可再克隆）不入主仓。③ t2i 线评测数据排除边界同步登记：archive/（仅 MANIFEST.md 入库）、bench200/（仅 README.md 入库）、focus1000/data/ 不入 git（题库/出图/判分/溯源留本地，口径见各目录文档）；edit/ 边界以下条「edit 子模块目录对齐 t2i 并废除 data/ 层」为准。④ /data/ 登记 .gitignore 不再入库：现存为 collect_v2 退役残件（根目录 taxonomy/、curation/ 现行版本的迁移前旧副本/重复件），留档本地不删。
>
> **架构决策（2026-09-06）**：edit 判分协议角色分离（用户拍板：判官提示词不得混入 codex 任务书内容——原 v2 合并文本会把「不得读取 caption/reasoning/level/suite/模型名/另一候选」等编排语义喂给判官，构成判定噪音与锚定风险）。定案：① 新增 `edit/prompts/judge_prompt_edit_qib_v2.md` = 判官唯一权威源（TEMPLATE 块 + 9 个 `<!--TYPE:x-->` 分型块；只含 rubric 三档定义、分型核对重点、落档硬判据、判分流程、特殊情况与裸输出 JSON；φ 映射与 d2/d3≤d1 钳制规则**刻意不向判官展示**——换算与钳制是管线确定性计算，防钳制语义反向锚定判官原始落档，此点与 ImgEdit 官方把钳制句写进判官 prompt 的做法有意分歧）。② 原 `codex_score_prompt_edit_v2.md` 重写为编排协议（任务书契约）：判定主体与盲评隔离的物质保障、prepare→render→逐字判定流程、分数行字段契约（身份/哈希字段从 manifest 逐字照抄，mapped=φ(tier) 与 official_* 机械换算）、有效性政策（model_failure 计 0 / invalid_question 成对剔除 / 基础设施重试）、aggregate/compare 命令、冻结后审计规则——其内容永不进入判官输入。③ `eval_codex_score.py` 新增 `render` 子命令（解析模板标记块，按题渲染 eNNN.txt + index.jsonl 登记 prompt_sha256/instruction_sha256，--manifest 时附盲评图片绑定），与 t2i 的 judge_prompt_gen_v6.0_V2.md 惯例对齐；新增 `ingest` 子命令（判官裸输出 raw/<qid>.txt → 机械补齐身份/哈希/换算字段并逐题校验维度契约 → part_*.jsonl；--format json=QIB 裸 JSON / imgedit=官方 Brief reasoning+分数行，MODEL_FAILURE 标记按各自口径记失败；part 文件禁止手写），管线成 prepare→render→派发→ingest→aggregate/compare 五段。判官调用方式定为**每题一个全新子代理上下文**（输入只有单题物化 prompt + BEFORE/AFTER 两图，零附加）——上下文隔离由结构保证，编排者只调度不亲判；任务书因此收缩为派发规则+命令清单。④ pilot 已冻结两轮（Gemini/Bagel QIB）判定用旧合并文本，`synth_v61_pilot/scores_qib/prompts/` 为新协议复建渲染（审计对照）；此后任何新判分轮必须 prepare→render 后逐字判，未物化产物无效。⑤ v1（1–5，冻结）与官方 rubric 实验臂（本就逐字物化）不受影响。⑥ edit 赛道判官模型钉定 `gpt-5.6-sol`（用户拍板：与 pilot 已冻结两轮一致，新轮次含实验臂一律沿用，跨协议对比不混入判官差异；曾短暂考虑换 gpt-6-astra，否决），任务书 `--judge` 固定写 `gpt-5.6-sol-built-in-imgedit-official`。⑦ 对照臂文档对称化与 prompt 世代归档：官方 rubric 臂增两份 `prompts/judge_prompt_edit_imgedit_official.md`（判官原文载体：ImgEdit 官方九类 rubric 逐字内置为 OFFICIAL_TYPE 九块，只 `<edit_prompt>` 单处替换；render 与契约 `edit_score_prompts.json` 逐字节一致性校验，块或契约任一侧被改写即拒跑（已负测试）；官方钳制句与 ≤20 词纪律原样保留——对照臂忠实性优先，与 QIB 臂"钳制不进判官输入"的防锚定策略有意分歧；判官文档不含任何编排内容）与 `prompts/codex_score_prompt_edit_imgedit_official.md`（通用编排协议，与 QIB 协议结构平行；1–5 与 QIB 百分制禁线性互换，臂产物永不进主口径结论）；`render` 支持官方臂 md/json 与 QIB md 三种模板（pilot 官方臂 20 题以 md 模板重渲染 sha256 20/20 复现）；退役 prompt 归档 archive/prompts_v1_v60/（出题协议初版+v6.0、判分协议 v1）与 archive/audit_doublecheck_prompt.md（复核轮已结、结论已落地 eval_synthesize，留作模板），archive/MANIFEST.md 登记；reviews/ 两册退役审阅 notebook 移 archive/notebooks_retired/（results_review.ipynb 的冒烟分析对象本机已不存在、audit_review.ipynb 属复核轮），question_dev.ipynb 按惯例留位且路径文案同步新布局。⑧ 两份判官 prompt 经 gpt-6-astra 全文评审（报告存档 `reviews/judge_prompts_review_gpt-6-astra_20260906.md`：文档 A 23 条 + 文档 B 机制 7 条 + 官方文本观察项 18 条）。处置：无副作用修复即时落地——QIB md 补九类三维名称附录快照（A01：拆分时维度表只留在代码 EDIT_DIMS 的权威源闭合回归）、官方 md 头部来源登记（B-M01：GitHub 出处 + 契约 sha256 基线 f9468dc8…，并如实声明双副本校验的已知边界）、render 加载期闭锁（B-M03：块唯一性/九类完整性/占位符唯一校验，重复块负测试通过）；两臂渲染 sha 修复前后 20/20 一致，判官所见零变化，pilot 冻结分数不受影响。判据类修改（A02 Excel 门槛机会偏差、A04 通用硬判据误罚 style/background/extract 合法重绘、A09 异常态与 JSON 契约闭合等）一律不在冻结期动，汇入 200 题正式批次前的 QIB v2.1 修订；官方 rubric 原文一字不动，18 条观察项（钳制非独立、钳制句锚定、style 无参考图、compose 部分成功奖励差、"两臂比较不只换分制"等）作为三臂结果解读的必读注记。⑨ QIB 判官 prompt 去内部键名（用户拍板 2026-09-06）：判官输入/输出全面改用维度名与"维度一/二/三"（d1/d2/d3 降为纯存储层位置键，仅存在于 EDIT_DIMS、分数行 schema 与 aggregate 校验中），TEMPLATE 增设显式「输入/输出」节，判官上下文不跳转；ingest 校验改为按序匹配维度名并自动补 key（兼容带 key 输出，错维度名负测试拒收）；pilot 复建渲染已按新模板刷新（sha 变化，冻结两轮分数不受影响），官方臂渲染经回归验证 20/20 不变。⑩ QIB 判准 v2.1 落定（用户拍板：v2 不再开新轮次）：新增现役判官模板 `prompts/judge_prompt_edit_qib_v2.1.md`——逐档硬判据全部下沉到九个 TYPE 块（每型 =「类型前提」+ 三维度各自的 0/1/2 判据，按该维在该类型下的真实语义写），通用节只留类型无关骨架（角色/输入/三档定义/三条通用判定原则/流程/特殊情况/输出契约）；吸收 astra 评审修正项：A02（Excel 改为"全部精确达成 + 任务内可核验的精确执行"，废除"超常规"相对参照，简单题同有可达档 2）、A03（0/1 边界 = 硬约束违反 vs 连续量偏差；记 0 必须有可指认证据，存疑不记 0）、A04（style/background/extract 的全图重绘属授权操作，不再被通用"大范围重绘 Fail"条款误伤）、A05（独立归因：任务失败不等于其他维度自动失败）、A06（编辑归因：只判新引入缺陷，源图既有缺陷不扣分、未授权修复不奖励）、A07（辨识限度条款）、A09（invalid/judge_unscorable 的 detail 必填、model_failure 的 reason 规则）、A21（extract 可见部分不得补全、天然浅色轮廓不算白边、相对布局改变属偏差）、A22（background 前景投影归属规则：投在背景区域的阴影/倒影按新光源重建、归 background 区域，协调性入 Physical Consistency 判）、A23（compose 先拆两个子操作逐个核对，细节偏差不等于少做一项）。render 默认模板切至 v2.1，编排协议角色表与步骤 2 同步；v2 原文保留（pilot 冻结两轮的判准，`scores_qib/prompts/` 即其渲染，审计对照），pilot 分数不追溯重算；**v2.1 与 v2 的分数不可直接互比**（档位语义与判据均变），跨批次对比必须显式标注判准版本；200 题正式批次首轮起用 v2.1。⑪ 判官文档纯净化（用户拍板：判官文件不写渲染机制——判官拿到的已是渲染成品，维护者注对判官是噪音、对人是错位置）：两份现役判官 md（judge_prompt_edit_qib_v2.1.md、judge_prompt_edit_imgedit_official.md）删除头部维护者引语与 v2.1 文末附录，只含标题 + 模板块；机制信息归位编排协议——九类三维名称快照表 + EDIT_DIMS 权威源声明 + "判官文件不写维护者注"规则入 codex_score_prompt_edit_v2.md 附录，官方臂来源登记（arXiv/GitHub 出处 + 契约 sha256 + 校验边界 + 钳制句与 20 词要求属官方口径逐字保留）入 codex_score_prompt_edit_imgedit_official.md；清理后三臂渲染回归：v2 冻结模板与官方臂 sha 20/20 不变、v2.1 渲染 20/20 零占位符；v2 历史模板头部原样保留（审计定位，不改写历史文件）。⑫ 判官 prompt 精简终态（用户拍板，推翻⑪中"v2 头部保留"处置：分型判据 = 全部判定文本，通用节即噪音；对齐 ImgEdit 官方的精简形态）：v2.1 TEMPLATE 收敛为「角色→输入→本题判据（分型块）→输出→题面」——判分规则元规则节、三条通用判定原则、判分流程、特殊情况节、{{DIMS}} 维度清单节全部删除；validity 四态语义与 observations 四组定义折叠进输出节（字段定义所在处）；"存疑不记 0、拿不准 1/2 给 1、同题同标准、任务失败不连坐"等跨类型纪律随通用节移出判官文本——分型判据的逐维锚点承载档位边界，未来校准如需恢复纪律条款，以分型判据形态写回；v2 冻结模板同步瘦身为"标题+模板块"（删头部维护者注与附录快照；TEMPLATE/TYPE 一字未动，渲染 sha 回归 20/20 不变）；编排协议附录快照表删除（EDIT_DIMS 代码表为维度名唯一权威源，任何文档不维护快照副本，规则并入职责边界段）；TYPE 分型块改为 ImgEdit 官方同款布局（维度名单独行 + "0/1/2 + 两空格 + 判据"纯文本行，无 markdown 修饰，"类型前提"取消、其语义并入对应档位判据），v2.1 单题判官 prompt 6313→3922 bytes，三臂渲染回归全绿。随后用户手排 v2.1 定版布局（一、输入 / 二、打分规则（九类判据逐节陈列）/ 三、输出 / 四、题面 四部分编号结构）并删除 v2 模板文件；机械修复恢复可执行：补 TEMPLATE-END、恢复四、题面与"两张图"收尾句、九个 TYPE 块移出 TEMPLATE 至模板块外（渲染只注入本题类型判据，判官不见其他八类，杜绝跨型锚点污染与提示词膨胀）、`## 类型` 标题归一到标记外；v2 删除后的审计凭 `scores_qib/prompts/` 渲染产物与 index sha 登记保存（协议角色表同步）；v2.1 终版 = 四段编号结构（一输入 / 二打分规则＝{{TYPE_NOTES}} 注入本题类型判据 / 三输出 / 四题面＝{{EDIT_TYPE}}+{{INSTRUCTION}}），九个 TYPE 块在模板块外作素材区（渲染只注入本题类型，判官不见其余八类，杜绝跨型锚点污染），中途试验过"判据内嵌模板、渲染剪裁"方案经用户定夺回退为占位注入式；单题判官 prompt ~3.8KB，QIB 渲染 20/20、官方臂 sha 回归 20/20 绿。⑬ 文档去重收口（用户质疑两份任务书冗余后定案）：官方臂独立编排协议 `codex_score_prompt_edit_imgedit_official.md` 删除，其独有内容（变体参数对照表、口径禁令、来源与完整性登记）并入主协议 `codex_score_prompt_edit_v2.md` 的「官方 rubric 对照臂」一节——编排协议全仓只此一份，两臂走同一六步流程仅参数不同；批次执行仍由各批次 TASK 承担（冷启动执行者需要实例化路径的具体工作指令，通用协议带占位符不可直接执行）。文档终态：判官文本 ×2（QIB v2.1 / 官方原文载体）+ 编排协议 ×1（含对照臂变体节）+ 批次任务书 ×1（TASK_official_rubric_codex.md）+ 契约 edit_score_prompts.json + 管线 eval_codex_score.py；官方臂渲染回归 sha 20/20 不变。
>
> **架构决策（2026-09-05）**：edit 子模块目录对齐 t2i 并废除 data/ 层（用户拍板），同日登记 ImgEdit 官方 rubric 实验臂。定案：① edit/ 新布局：prompts/（出题协议 synthesize_prompt_edit*.md + 判分协议 codex_score_prompt_edit_v{1,2}.md + audit_doublecheck_prompt.md，随代码入 git）、reviews/（四册 notebook：question_dev / results_review / results_review_v61 / audit_review）、archive/（synth_v60 世代批次 + complexity_audit 账本 + audit_doublecheck 复核产物，MANIFEST.md 入 git）、批次目录与活素材落子模块根（synth_v61_pilot/、focus200/、complexity_audit_synth.jsonl、judge_prompts/ 物化区，均 gitignore）；data/ 层撤销（t2i 维持既有布局不动）。② 排除集机制统一升级：三份 eval_sample*.py 的 DEFAULT_EXCLUDES 改为三赛道整目录递归扫描 samples*.jsonl（原 data/+archive/+bench200 定向桶列表在 edit 去 data/ 后会漏扫顶层落点），任何布局演化下历史样本永不回流；t2i/archive/MANIFEST.md 排除集条款同步改写。③ 全部脚本目录常量随迁（EVAL_DIR/OUT_DIR/DATA→子模块根，audit_doublecheck 默认读物→archive/，gen_results_review 产物→reviews/），py_compile 全过。④ 12 份盲评 manifest/identity（scores_qib×4、scores_official×4、scores/codex_blind×2 等）的绝对路径全部改写至新址并逐一验证文件存在，旧机器 /tank 前缀残件一并清理。⑤ results_review_v61.ipynb 迁 reviews/ 重建重跑（demiwtg 内核，零报错，report 对账一致）。⑥ ImgEdit 官方 rubric 实验臂（判分文本物化）：官方 prompts.json 逐字渲染 20 题 judge prompt 落 synth_v61_pilot/scores_official/prompts/{eNNN.txt,index.jsonl}（sha256 登记，判官不得回读源 rubric），判官=codex 内置模型盲评（TASK_official_rubric_codex.md），产物只落 scores_official/，与 QIB 主口径物理隔离、永不混入正式结论；动机=量化 rubric 文本本身的协议效应，并保留与 ImgEdit-Bench 官方口径对话通道。
>
> **架构决策（2026-09-06）**：meta 真相区统一收口（用户拍板：EN 并入中文湖成统一版、images.jsonl 收官退役、VLM 补标后置另跑）。EN/ZH 实体合并由 `curation/en_entity_merge.py` 五层执行：tier0 确定性配对（别名/西文串≡EN 名限同节点，38,701 对，0 调用）→ bulk 逐节点 LLM 对齐（本地 vLLM Qwen3.8-27B @8001，16,527 节点全完成、341,302 对、0 API 调用）→ escalate 红旗节点复核（四模型轮转 glm/glm-5.3-flash + qianwen/qwen3.7-plus + galaxy/qwen3.6-flash + galaxy/glm-5.3，关思考防思维链截断，368/375 有效）→ apply 写库（matched/variant→中文实体 aliases 326,272 名、new→新实体 88,249 个 source=derived 入库挂树 99,013 处、tier0 回填 1,330 对；instances 296,010→384,259）→ orphans 兜底（对齐未覆盖的 EN 独有节点实体 1,003 个全量入库挂树）。配套 `curation/meta_unify.py`：metadata_en.jsonl 73,962 行归一并入（EN 实例名按对齐映射归一中文正名，(sha,instance) 去重）+ images.jsonl 322,332 行炸开并入（v1 采集字段 tiers/credit/source_rank 等照 migrate.py 先例丢弃，identity/focus/quality=null 待补标）。收口：images.jsonl/metadata_en.jsonl/taxonomy_en.json/instances_en.json 四文件物理移出 meta/ 归档 state/taxonomy/retired_meta/（改前另有 backup_pre_en_merge、backup_pre_orphan_ingest）；2.2 白名单收敛为 metadata.jsonl+三件套+.meta.lock，metadata.jsonl 升统一权威主清单；消费端同步（viewer/build_viewer.py 改读 metadata.jsonl、imgs.js 精选口径=打标行+top50+caption 截断、缓存号 v4、--lang en 与 tag_tree_explorer_en.html 删除；search_kb --lang en 拒绝退役；upgrade_v31_en.py 加防误跑守卫；eval_sample×2/annotate_backfill 注释更新；.gitignore 英文件例外链移除）。终态校验：metadata.jsonl 2,849,013 行 / 2,116,511 sha / 304,190 实例全部在册、name 唯一性通过、并入零新增重复键（存量遗留 261 个重复键为老采集链时代产物待后续清理）。追加定案（同日）：alias_western.json 退役归档 retired_meta/——49,197 个非空西文串 100% 已含于 instances.aliases（数据零独有），246,813 个 null 的负缓存语义（op_seed 防重问）由用户后续改 demiwtg-data 仓库 op_seed 读端承接；en_entity_merge tier0 读端已容错缺文件；meta/ 白名单收敛为 metadata.jsonl + taxonomy 两件套 + .meta.lock。追加定案（同日晚）：metadata.jsonl 更名 **instance_images.jsonl**（用户拍板：语义=实例×图片观测对，避开已退役的 images.jsonl 旧名防止契约混淆；本条目前文中的 metadata.jsonl 均指此文件）；主仓 11 个引用文件同步改名（bagel 的 geneval 自带同名文件不动）；instances.json 维持实体+富知识一体（拆分否决：desc/query 是实体 1:1 正典记录而非模态样本，aliases 是结构性匹配字段；体积增长留观，每实体一份 desc 的规模问题届时再议）。
>
> **架构决策（2026-08-25）**：新开 `modelhub/` 独立子项目（用户拍板：可单独 push GitHub、其他机器 pull 直接复用；静态代理全套并入）。定位：本地 LLM 统一接入层——LiteLLM 网关（127.0.0.1:4000，OpenAI 兼容）路由三条线：本地 vLLM（qwen3.8-27b，no_proxy 直连）、Galaxy 专线（qwen3.7-plus，no_proxy 直连）、OpenRouter 通配（进程级代理注入 → mihomo 按域名分流走静态住宅 IP 出口）；静态代理模块即原 `/root/gpu-static-proxy`（mihomo v1.19.30 双层链式：10808 隧道换源 IP → 静态 IP 节点 216.132.205.99；modelhub 只维护「AI API 域名走 STATIC 双层静态出口」一层规则，其余流量 modelhub 视角直连、继承宿主策略、不感知不维护）整体迁入 `modelhub/static_proxy/`（二进制与 GeoIP 库随迁，旧目录作废可删）。定案：① 照 bagel 先例：独立 git 仓库、主仓 .gitignore 整体排除、内部自治（自带 README，不受主仓「文档只两份」约束）；② 机密零入库：.env（API keys）与 static_proxy/config.yaml（节点凭据）只进 gitignore，仓内只有 *.example 模板；mihomo 二进制不入库（fetch_mihomo.sh 下载/旧机拷贝）；③ Python 环境独立：modelhub/.venv（litellm[proxy]==1.98.0 锁定），不碰主仓 .venv（不动主仓 openai/httpx/pydantic）；④ 消费端零改动启用：网关为 OpenAI 兼容端点，既有脚本换 LLM_BASE_URL=http://127.0.0.1:4000/v1 即接入，逐步迁移；上游端点与 key 全部 .env 可配置，启动时 gateway/gen_local_models.py 自动发现各 *_API_BASE 端点的模型并注册进 /v1/models（openrouter/* 通配 litellm 原生展开；Cline 按 OpenAI Compatible 配置网关地址即自动带出模型列表）；⑤ 端口登记（均仅本机监听）：4000 网关 / 7891 mihomo mixed / 9091 mihomo API / 1053 mihomo DNS。
>
> **架构决策（2026-08-24）**：英文版标签体系入库（用户拍板三则：扩白名单同居 meta/、实例名轻量清洗、查看器 --lang en 独立页面）。「融合世界标签体系 v3.1」交付包英文底稿（taxonomy_tree_instances_en.csv：21,406 行，中文路径/英文路径/英文实例清单三列）由 `taxonomy/upgrade_v31_en.py` 建成一套完全独立的英文平行数据（干跑→--apply，照中文版惯例；不动中文三件套与 alias_western.json）。定案：① taxonomy_en.json / instances_en.json 同居 meta/，2.2 白名单扩两行 + .gitignore 例外链放行入 git；② 前缀归一为中文 norm_path 的英文同款（剥根 Fused World Label System + General Classification Tags，换根 demiwtg），底稿缺行的 4 个骨架域与中文同源隐式补齐；③ 底稿实测 119 组翻译撞车（不同中文节点译成同一英文路径，如 炊具/锅具 → Cookware，名单重合度中位数仅 0.02）用户拍板自然合并（名单取并集），另 2 个译名折叠展开隐式节点（中文段『帝王蟹/蟹』译成 King Crab / Crab 两段）→ 英文树 21,291 节点 = 中文树 21,409 - 撞车合并 120 + 折叠展开 2，域级对齐（撞车与折叠清单落 state/taxonomy/en_merge_report.json 供后续精译修复）；④ 英文实例名轻量清洗（按词：全小写/全大写词转首字母大写，全大写缩写与混排词保留），清洗后同名大小写变体合并（name 唯一主键），413,329 → 382,341 全量 source=derived 占位入库（不做富知识、不映射中文知识），清洗合并明细落 state/taxonomy/en_clean_report.json；⑤ viewer 复用：build_viewer.py 加 --lang en，读英文两件套写 viewer/build_en/ sidecar（imgs.js 注入 null——英文实例与 images.jsonl 打标零交集，英文版无图是预期），页面 tag_tree_explorer_en.html 由主页面现场替换生成（标题 demiwtg (EN)、sidecar ?v=1 独立缓存号、fetch 回退改英文两件套），页面入 git、build_en/ 产物不入。结构对齐验证以中文路径列为桥：归一中文路径 21,405 ⊆ 中文树，差集恰为 4 骨架域。纯新增零覆写，故无入库前备份。图数据（images.jsonl/metadata.jsonl）零改动。
>
> **架构决策（2026-08-24）**：`data/datasets/` 整体升为项目根目录 `datasets/`（用户拍板：自建与开源数据集同居一处；data/ 保留原名只住代码）。理由：`data` 作顶层名太泛，且目录里早已住着 collect_v2/taxonomy 两套代码名实不符；拆开后 `datasets/` 语义精准、`data/` 收敛为数据构建代码区。定案：① 同盘 rename 零拷贝（~1.1TB：自建 demiwtg 631G + 23 个开源数据集；24 目录全量在位，blobs/meta 完好）；② 路径改址全链路：data/collect_v2 与 data/taxonomy 的 REPO_ROOT/ROOT 推导补一层（此前代码被搬入 data/ 后已暗指 data/ 而非仓库根，state//logs/ 类路径实已失效，本次一并修复）+ 数据集路径常量去 `"data"` 段，import 实链路断言验证；benchmark t2i/edit 的 eval_sample 默认路径同步；viewer 页面/构建脚本改址并重建 build/ 产物（20,100 实体）；③ .gitignore 例外链改 `datasets/*` 逐级放行三件套；④ 历史决策块旧路径为当时快照不回改；⑤ curation 模块在搬移中被误删的 dataset_analysis.ipynb 已由快照恢复归位 data/curation/（20-cell 终态：基线 16 格逐字对齐 8/22 快照 + 全部补丁/内联写命令按时序重放 + 拆分后删尾四格；抢救过程产物在 state/curation/_nb_recovery/，可清理），路径与 import 已按 data/ 新位置与顶层 datasets/ 调整。本会话未动 .qoder 交接文档与 logs/ 归档脚本（历史快照）。
>
> **架构决策（2026-08-24）**：benchmark 按三大题型拆成三子模块（用户拍板）：`vlm/`（理解）、`t2i/`（生成）、`edit/`（编辑），推翻上一条「评测数据在 benchmark/ 根三目录」布局。定案：① t2i/edit 各带完整四件套（eval_sample/eval_synthesize/eval_score 由原跨赛道脚本拆分，通用工具各留一份子模块自闭环，不建 common）；vlm 赛道协议未拍板暂不拆代码，只放 notebook；② 旧评测数据三目录（eval_v1/eval_v2/taxonomy_sample_cases，合计 ~2GB）用户拍板删除重抽，重抽时指定目录分落三子模块 `data/`（不入 git，.gitignore 登记；仅保留契约文件：出题 prompt 两册与 edit_score_prompts.json 随子模块入 git）；③ notebook 两册变一册：每子模块 question_dev.ipynb（合并原 eval_analysis + eval_review：抽样委托 + 分布分析 + 题库审阅，for 题目构造）与 results_review.ipynb（原 wkbench_review.ipynb 改名+按赛道裁剪：打分/评估结果分析，由各自 gen_results_review.py 生成）；④ 抽样排除集改三子模块 data/samples.jsonl 互斥（跨赛道防重复出题）；⑤ bagel 侧 `run_wkbench.py` 同步改址：DEFAULT_QUESTIONS 改指 t2i 赛道新题库（`benchmark/t2i/data/synth_gen/questions.jsonl`），样本图根目录随 --questions 位置自动推导（题库目录与其上级两级候选，适配各赛道 data/imgs/ 布局）。判分/出题协议本身零改动（FACETS 权威源、φ 映射、三维钳制、9 类轮转配额原样随迁）。
>
> **架构决策（2026-08-24）**：公共环境 `env/` 改名 `.venv/`（用户拍板；最初提议 `.env`，因与 dotenv 密钥文件约定撞名——密钥扫描/搜索排除类工具对 `.env` 有特殊处理——改用 Python 环境惯例隐藏名）。同盘 rename 零拷贝；bin/ 内 100 处旧路径（97 shebang + config 脚本）批量重写，conda-meta/lib 零引用无需动（内部亦无绝对路径软链）；bagel 侧 4 处脚本引用与 Jupyter kernel demiwtg 同步改址；.gitignore 登记改 `.venv/`。历史决策块中的 env/ 旧路径为当时快照，不回改。

> **架构决策（2026-08-24）**：`bagel/env` 提升为项目公共环境 `/tank/demiwtg/env`，`.venv-notebook` 退役（用户拍板）。理由：主仓侧实验（水印检测 pilot 等）需要 torch 推理栈，为它单独建环境浪费且易漂移；环境本体与 bagel 代码仓无关，提升后 bagel 脚本与主仓实验共用一套。同盘 rename 零拷贝；bin/ 85 个 shebang + bagel 内 4 处脚本引用 + conda-meta 批量重写（二进制内嵌串与 conda history 旧路径不影响运行，沿用 2026-08-23 迁移先例）。`.venv-notebook` 唯一消费者是 Jupyter kernel demiwtg，改指 env/ 并补装分析栈（duckdb/ipykernel；pandas 用 env 既有 2.3.3），dataset_analysis.ipynb 全 12 cell 复跑零错误后删除。`models/.venv-vllm` 维持 vLLM 部署专用不动。env/ 不入 git（.gitignore 登记）。

> **架构决策（2026-08-24）**：标签体系升级 v3.1（用户拍板三则：死名保留回挂、新增实例分批入库、英文底稿仅扩名单）。外部交付的「融合世界标签体系 v3.1」终版底稿（29 域 / 21,406 路径 / 29.6 万实例，三批迁移终版：域级前缀替换 + 22 个 IP 域吸收并入通用域 + 知识与学科清理）由 `taxonomy/upgrade_v31.py` 一次性升级进仓（干跑→裁定确认→--apply，幂等：每批实例入库后可重跑刷新树名单）。定案：① taxonomy.json 按底稿重建（21,410 节点 = 底稿 21,406 + 4 个底稿缺失域骨架隐式补齐；schema 1.1.0），节点 instances 名单只留在册名（∩ instances.json，跨节点多挂 9,075 名属契约允许），「IP 分类标签」分支随吸收自然消失；② 失配节点 KB 用名单指纹法抢救（前缀替换名单随行 ⇒ 直接名单/子树名单精确匹配 + Jaccard≥0.85 唯一命中，命中 1,483/1,491，报告落 state/taxonomy/v31_kb_recovery_report.json，未命中 8 条可 gen_taxonomy_kb 重生成）；③ 95 个死名实例（粗伞名，如主战坦克/黄道十二宫，涉及 1,010 次打标）不剥离：保留实例与图，按原挂载路径回挂存活节点（零歧义，无退化）；④ 新增 237,781 实例本次不写 instances.json，按域分组候选清单落 state/taxonomy/v31_pending_instances.json，分批富化入库（用户逐批确认）；⑤ alias_western.json 仅扩名单（+522 条 null 占位，英文实例中英不对齐不做别名填充）。图数据（images.jsonl/metadata.jsonl）零改动。交付包归档 state/taxonomy/v31_交付包/（不入 git）；升级前三件套+metadata.jsonl 物理备份在 state/taxonomy/backup_pre_v31/。
>
> **架构决策（2026-08-24）**：v3.1 新增 237,781 实例占位全量入库（用户拍板，推翻上一条定案④的分批富化后才入库）。理由：树名单只留在册名导致 viewer 只见 5.8 万实例，用户要求全量可见。定案：① 237,781 个待入库名以 source=derived 占位（仅 name，与存量 derived 同构）一次性写入 instances.json（58,229 → 296,010），富知识（desc/aliases/query）留待后续按域分批 LLM 富化；② 重跑 upgrade_v31.py --apply 幂等刷新树名单（丢弃引用 0、死名回挂 95 与 KB 保留 1,493 不变；v31_pending_instances.json 清零）；③ alias_western.json 按既定「仅扩名单」策略 +237,781 null 占位；④ viewer 重建（taxonomy.js 11.1 MB / instances.js 72.9 MB，树名单唯一名 296,010 = 在册数，名单⊆在册校验过）。入库前物理备份在 state/taxonomy/backup_pre_placeholder_ingest/。
>
> **架构决策（2026-08-24）**：标签树路径前缀精简（用户拍板）：根『融合世界标签体系』→ `demiwtg`，废除一级分支『通用分类标签』，29 域直挂根（路径口径 `demiwtg / 域 / 二级 / ...`；历史双树期 IP 路径同样归一）。定案落在 `data/taxonomy/upgrade_v31.py`（norm_path 归一函数：底稿路径与旧树路径统一归一，幂等可重跑不回退旧结构）；底稿根行与中间层行同归 demiwtg 空名单按序合并（合并 1 行，节点 21,409 不变）。重跑 --apply 重建：KB 保留 1,493 / 死名回挂 95 / 实挂引用 346,729 / 唯一名 296,010 全不变；KB 指纹抢救失配 0（上轮已全部归位，本轮路径全精确匹配）。消费端同步：① benchmark t2i/edit eval_sample.py 的 branch_of 注释与层级口径（域现在是 segs[1]，抽样分层粒度随之为 域×二级）；② viewer 页标题改 demiwtg、废除已失效的 IP/通用双树过滤器下拉框、sidecar 缓存号升 ?v=3（build_viewer.py SIDECAR_MARK 同步）；③ curation dataset_analysis.ipynb 注释旧路径示例。图数据零改动；改前备份 state/taxonomy/backup_pre_prefix_simplify/。
>
> **架构决策（2026-08-24）**：评测数据由 state/curation/ 迁入 benchmark/（用户拍板）：eval_v1/（360M）、eval_v2/（893M）、taxonomy_sample_cases/（775M，无代码消费者，人工审阅用）三目录同盘 rename 零拷贝。理由：这些是评测**结果数据**而非运行时状态，与出题/判分代码同模块闭环（沿用 bagel/env 提升的同盘迁移先例）。配套：① 全部引用同步改址——eval_sample/eval_synthesize/eval_score 的默认路径常量改自脚本自推（`Path(__file__).parent`）、三个审阅/分析 notebook、gen_wkbench_review.py、bagel 侧 run_wkbench.py 的题库与图根绝对路径；② .gitignore 登记三目录不入 git（代码仍入库）；③ judge prompt 物化目录随 EVAL_DIR 落在 eval_v2/judge_prompts（仍不入 git）。历史决策块中的旧路径为当时快照，不回改。
>
> **架构决策（2026-08-23）**：覆盖口径带质量门（用户拍板）：op_coverage.load_coverage 新增 min_quality/require_identity 两参（默认 8.0/开，即 notebook 默认过滤同款口径），只数合格行；「有图但全不合格」的实例按 0 图对待继续采，缺 quality 字段的存量迁移行按不合格计。chain 新增 --min-quality/--require-identity（BooleanOptionalAction）。重点下载零合格图实例用 `--skip-covered 1`（当前实测范围 5,001 个）；两门全关退化为旧口径（回归验证与无门时计数完全一致）。分区排序（0 图排队首）自动沿用合格计数。
>
> **架构决策（2026-08-23）**：wkbench 首跑冒烟修复（runner 侧补丁，不动官方模型代码）：edit 任务将输入图过 fp32 VAE 编码，在 bf16 autocast 区内 conv_in 直接炸 dtype 不匹配，且编码产物经 NaiveCache 进 vae2llm 时 accelerate dispatch 钩子挂不上。修法两条，都在 run_wkbench.py：① vae_model.encode 包一层（内部禁 autocast + 显式对齐 VAE 设备，因实例属性影子掉 dispatch 钩子的设备搬运）；② forward_cache_update_vae 包一层 shim，在 vae2llm 自身 device/dtype 上做投影（与 generate_t2i.py 的 decode_image 设备修复对称）。vlm/t2i/edit 三任务单卡冒烟全过后才起双卡分片全量。
>
> **架构决策（2026-08-23）**：bagel 项目由 `/tank/bagel` 整体迁入本仓 `bagel/`，成为登记子项目（用户拍板，顶层目录禁令的登记例外）。同盘 rename 零拷贝；定位：Bagel（BAGEL-7B-MoT 统一模型）的训练/推理/评测全链路（权重 28G、hf_home 78G、LMUData 33G、conda env 5.1G 等重物随迁）。配套定案：① 子项目自成一个独立 git 仓库（迁入时新建，原目录系 rsync 落地无历史），主仓 .gitignore 将 `bagel/` 整体排除，子项目内部 .gitignore 只放行代码与配置、重物全部不入仓；② 全部硬编码路径 `/tank/bagel` 已批量改写为 `/tank/demiwtg/bagel`（101 文本文件 + conda env 的 75 个 shebang + pip/conda-meta 元数据，复查零残留），`Bagel/bagel` 自指符号链接改为相对链接，conda 环境迁移后验证通过（torch 2.6.0+cu124，CUDA 可用）；③ 与主仓的接口不变：评测题库读 `state/curation/eval_v1/`，评测 runner 为 `bagel/Bagel/scripts/run_wkbench.py`；④ `bagel/HANDOVER.md` 为子项目内部的下载/环境交接文档（子项目自治，不受主仓“文档只两份”约束）。
>
> **架构决策（2026-08-23）**：新开 `benchmark/` 顶层模块（用户指定，顶层目录禁令的登记例外），用于多模态世界知识+推理评测基准建设（目标：定位 Bagel 类统一模型的短板）。分工定案：出题 prompt（`state/curation/eval_v1/synthesize_prompt.md`）与合成脚本（`curation/eval_synthesize.py`，qwen3.7-plus API）归属 curation，题库与评测样本落 `state/curation/eval_v1/`（不入 git）；benchmark/ 只放审阅/评测入口。首批预合成 10 样本 × 3 任务（vlm/edit/t2i）= 30 题已产出，出题 prompt 核心机制：证据审计防 OCR 捷径、caption 防幻觉传染、probe_dims 短板探针维度 + expected_failure_modes 失败模式预测、JSON 机读输出。bagel 侧评测 runner 落在 bagel 子项目 `bagel/Bagel/scripts/run_wkbench.py`（单卡 accelerate-dispatch 加载 BAGEL-7B-MoT，三任务推理：vlm 走 think+understanding 出文本、edit 走官方 imgedit 口径 cfg 出图、t2i 沿用 generate_t2i.py 参数；按 qid 断点续跑、支持 --shard 分片与 LORA_PATH 注入，产出 responses_shard*.jsonl + imgs/）。

> **架构决策（2026-08-23）**：eval_v2 生成（T2I）+编辑双赛道评测定案，出题与判分代码归 benchmark/，运行时产物落 state/curation/eval_v2/（不入 git）。题目构成：生成赛道由本仓数据分层抽样驱动（新增 eval_sample.py——质量门 quality≥8.0 且 identity=true 读 metadata.jsonl 权威清单，口径修正：质量字段不在 images.jsonl；(L1,L2) 分支配额 ∝ sqrt 且最大余数法恰分 n；排除集剔 eval_v1+eval_v2 既有样本 sha 防背答案；每实例限张），首批实测 500 张（279 张 edit_ok），图片索引自 1099 续编兼容前序产物；编辑赛道按 ImgEdit-Bench 套系构成（9 类 edit_type 轮转配额 + 每第 5 题强制知识编辑套，改动方向须由图外知识唯一决定）。判分：生成赛道照 Qwen-Image-Bench 协议——双线判分（知识线 implicit_checks 权重加和、通用线按题面 facet_tags 激活 22 个裁剪 facet，各 0.5 合成，主体缺失/主题跑偏封顶 30），刻度 {0,1,2,NA} 经 φ 非线性映射 0/60/100 后自底向上聚合，facet 词表单一权威源在 eval_score.py FACETS；编辑赛道原文采用 ImgEdit 官方 prompts.json 九类三维 5 分制 rubric（benchmark/edit_score_prompts.json 作为评分契约代码入 git，按 edit_type 路由，二三维不得高于一维的硬约束在判分侧强制钳制）。配套：eval_synthesize.py 迁入 benchmark/ 并重构（--task all 维持 v1 语义不变，gen/edit 为 v2 专项批次）；出题 prompt v2 两册（synthesize_prompt_{gen,edit}.md）与 judge 模板物化（judge_prompts/）均不入 git；judge 用本地 Qwen3.8-27B vLLM（localhost:8000，thinking + 确定性解码，解析失败计 0 并入异常率），出题走 Galaxy API（qwen3.7-plus，需 GALAXY_API_KEY）。冒烟已过：edit 判分端到端（原图冒充产出正确判「无变化」给 1 分）、t2i 判分（gate 封顶语义正确、0 解析失败）、抽样分层配额与排除集生效。

> **架构决策（2026-08-22）**：下载侧恢复连接复用（显式推翻 2026-08-21 keepalive=0 定案，用户拍板）。新增下载专用客户端（infra.get_download_client，双池直连/代理，Limits 128/64），只挂 dl: 档流量，检索侧维持禁复用不动。三层防线：① 病根已除（stream yield-in-retry 已修，全链零任务取消源，结构性不再产生半读连接进池）；② op_download 每请求硬超时 90s（asyncio.wait_for），超时取消任务经 stream 的 finally 关响应，未读完的连接销毁不入池，永久阻塞降级成丢一张图；③ read=30s 读超时与 supervise 12 分钟自愈兜底不动。理由：下载打 CDN，每图一次全新 TCP+TLS 握手已成实测主瓶颈（py-spy 实锤堵在 do_handshake），实测 6.8 张/s 对闸门理论上限 105 张/s。回退条件：任何停摆/风控迹象 → DOWNLOAD_LIMITS 的 max_keepalive_connections 改回 0 一行回退，重启即可。
>
> **架构决策（2026-08-22）**：覆盖过滤后实例队列稳定分区：0 图实例排队首、有存量（1~N-1 张）的难啃实例沉底（实现在 chain.py 启动期，只改顺序不改集合）。理由：重试区实测撞车 89%、实例速率不到干净区一半，先吃干净区把进度跑出来，难啃实例最后兜底；用户曾疑「降 --skip-covered 阈值能缩小重试区」，实测分布推翻：重试区主体是 36,205 个 0 图新实例（阈值降到 1 也跳不掉），1~7 张存量实例仅 2,811 个，降阈值只会放弃这批已投过资的实例。

> **架构决策（2026-08-22）**：打标前撞车快查回归，推翻 2026-08-21「职责清晰优先于微优化」定稿。Sink 新增无锁只读快查 contains()，chain 的 annotate_worker 打标前先查 (sha, instance) 索引，命中即跳过打标。理由：--skip-covered 8 重试区实测撞车占下载量 89%，原「微优化」场景已变主导成本——不前置则 ~9 成 VLM 槽位烧在重复图上；咨询语义不变契约：索引漏查（跨进程新行）时照常打标，权威判定仍在 sink 锁内，最坏多打一次标永不双写。

> **架构决策（2026-08-21）**：存量迁移链开工：新增 op_backfill（补标算子，与全量打标同 prompt 同口径，只重打 kb_match 并补 identity/focus，richness/caption 沿用存量）与 migrate.py（记录驱动：images.jsonl 炸开为每实例×图一条记录 → 读 blob 复验 sha256 → 补标 → 补 queries/query_langs → 追加 metadata.jsonl）；原算子（op_annotate/op_sink/chain）零改动。配套定案：① 迁移后一图多行合法（去重键 (sha256, instance)，对 sink 的 sha 撞车跳过契约定向豁免，仅本入口）；② danbooru 处置：双源（danbooru/bulk_danbooru2023，共 16,102 行）整体从迁移链剔除、不写 metadata.jsonl，由用户另走开源数据集元数据链路单独处理（migrate.py EXCLUDE_SOURCES；含早前确定性认领写回的 7,324 行，认领成果随 images.jsonl 留存供其链路复用）；③ 迁移收官后 metadata.jsonl 升为唯一权威主清单、删 images.jsonl（届时同步改 2.2/2.4 与 viewer 读端）。

> **架构决策（2026-08-21）**：删除 curation/filter_vlm.py（VLM 图片质量过滤 run/report）。理由：质量/身份类打分字段（kb_match/richness/identity/caption/focus/quality）已由 collect_v2 的 op_annotate 随采集链路内联产出，独立质检流水线无运行进程；state/filter_vlm/ 目录不存在、无任何结果残留，删前核实全仓无引用。curation/ 脚本化流水线至此全部移除，只留数据分析 notebook。

> **架构决策（2026-08-21）**：`data/dataset/` 更名 `demiwtg` 并入 `data/datasets/`（同盘 rename 零拷贝）；`data/taxonomy/` 三件套（taxonomy.json/instances.json/alias_western.json）迁入 `data/datasets/demiwtg/meta/`，`data/taxonomy/` 目录撤销。理由：自建数据本身就是一个数据集，与开源数据集统一收编到 data/datasets/ 下，消除 data/dataset 与 data/datasets 双轨；标签体系三件套是该数据集的标注模式资产，与 images.jsonl 同居，meta/ 成为 demiwtg 的唯一真相区。配套变更：仓库根由 `--meta` 向上推导由三级改四级；gen_taxonomy_kb/gen_instance_kb 断点缓存改放 state/taxonomy/（运行时缓存不许进 meta/ 白名单，llm_common.JsonlCache 自动建目录）；三件套继续入 git（.gitignore 逐级例外），blobs 与 jsonl 大文件维持不入 git。

> **架构决策（2026-08-21）**：删除 curation/annotate_vlm.py（VLM 知识打标 run/stream/apply）、curation/emerge.py（taxonomy 涌现缺口分析）、curation/util.py（meta_lock 随唯一消费者一并失去意义），及四个一次性脚本 fix_abs_paths.py（绝对路径迁移，实测 images.jsonl 已 0 条残留）/ backfill_provenance.py（溯源回填，数据源 runs/ 批次产物已清）/ probe_identity.py 与 probe_retrieval.py（数据依赖 bulk posts_meta 与 v1 DLQ 均已删）；同期清理 state/annotate_vlm/、state/emerge/ 运行时目录。理由：打标职责已由 collect_v2 的 op_annotate 接管（打标随采集链路内联），独立打标流水线无运行进程、无消费者；emerge 依赖 annotate_vlm 打标产物且产物全部可从 images.jsonl 重算；删前逐一核实无残留 import。curation/ 此后只留 filter_vlm。
>
> **架构决策（2026-08-21）**：删除 collect v1 采集系统（整模块 40 文件）与 curation/retry_failed.py（v1 死信重试器，随 v1 失去意义）；采集职责由 collect_v2 接管。理由：v1 已被 v2 三层架构完全替代且无运行进程；残留耦合仅 curation 两处引用 v1 的 meta_lock 文件锁工具，已原样迁入 curation/util.py（annotate_vlm/backfill_provenance 改 import 该处）。同时清理 COS 迁移过程文件（remote_pull.sh、cos_pull/）：186/186 分片迁移已于 2026-08-21 收官并验证（blobs 496G/323164 文件，内容寻址抽检通过），过程脚本不再需要。
>
> **架构决策（2026-08-21）**：HF 数据集落盘区由 state/collect/datasets/ 迁至 data/datasets/（23 个数据集目录整体搬移，同盘 rename 零拷贝；下载过程脚本留 state/collect/datasets/ 只读归档）。理由：开源数据集是长期数据资产而非运行时状态，data/ 才是数据根（.gitignore 早有 data/datasets/ 预登记）；state/ 回归纯运行时语义。同期清理：coco2017 标注 zip 为 0 字节下载失败残留，已删。解压教训：多线程共享单个 ZipFile 并发解压会因共享文件指针竞态产生大量伪 CRC 错误（首轮误判 38% 文件损坏，串行 testzip 复验全部 zip 实际完好），必须每线程独立打开 ZipFile。
>
> **架构决策（2026-08-19）**：标签体系解耦——instances.json 升为独立权威源（实体资产，生灭与富知识不依赖树），taxonomy.json 降为展示视角（树 + 挂载引用）；废除 instances.taxonomy_paths 字段（schema 2.0，实例表 56,789 条一次性迁移零丢失）并删除 build_unified.py。理由：树决定实例生死的反向控制是唯一残留耦合，斩断后数据处理链路（采集/打标/涌现）全部只读实例表；树可自由重生成/多视角并存而不伤资产。需要挂载关系的消费者（collect gap 聚簇、gen_instance_kb 与 emerge 的 prompt 上下文）改由 taxonomy/mount_map.py 从树现算。
>
> **架构决策（2026-08-17）**：broader/ 模块（Open-BROADER 上下位关系模型）迁出本仓库，回归独立项目 `/root/data/projects/open_broader/`（代码、55G 训练语料、训练产物、历史日志整体搬移，脚本内绝对路径已批量改写至新家）。理由：上下位判断本质依赖世界知识，通用大模型（Qwen3.8-27B 批审计 + 现成 embedding 检索）已可覆盖 taxonomy 树审计场景，且训练语料正确性存疑、课题短期难推进，故冻结训练、语料与 checkpoint 原地归档。本决策推翻 2026-08-16 的并入决策；未来如复活，先做大模型 vs BROADER 的 head-to-head 评测再立项。

- 跨模块 import 一律 `from <包>.<文件> import ...`：`taxonomy/`、`curation/`、`viewer/`、`benchmark/` 位于仓库根（消费者先 `sys.path.insert(0, REPO_ROOT)`）。
- 路径常量一律从脚本自身向上推导到仓库根（顶层模块脚本推导两层），不依赖 cwd 之外的魔法。
- 新增脚本必须先归属到一个模块；归不进去的说明职责边界有问题。

## 4. 数据与代码的边界

- `datasets/`、`state/`、`logs/` 是本地数据/运行时产物，**不入 git**（.gitignore 强制；例外：datasets/demiwtg/meta 下 taxonomy 两件套）。
- 入库的只有：代码（taxonomy、curation、viewer、benchmark，含 viewer 页面 HTML）、约束文档（AGENTS.md、README.md）、以及 `datasets/demiwtg/meta/` 下的权威 JSON（taxonomy.json/concepts.json）。
- 大 JSON（images.jsonl、blobs）永远不进 git；需要备份走独立通道。
- 生成产物（`viewer/build/`）不入 git，数据改动后重跑 build_viewer.py。

## 5. 关键命令

```bash
# 标签体系富化（LLM 各一次调用；需 LLM_API_KEY 等环境变量；dry-run 零成本预览）
python3 taxonomy/gen_taxonomy_kb.py --only-empty --write       # 节点 KB（knowledge_intro 等 4 字段）
python3 taxonomy/gen_instance_kb.py --only-empty --write   # 概念富化（aliases→concepts.json；知识文本→docs 层草稿）

# 概念体系维护（2026-09-07 概念化迁移器；instances.json 已退役，勿恢复）
python3 curation/migrate_concepts.py --refresh-taxonomy    # 树变更后刷新 concepts.json 挂载快照

# viewer 产物重建（数据改动后）
python3 viewer/build_viewer.py

# 数据策展与检索接地（curation/，各脚本 --help；notebook 直接运行）

# 图片采集链（已独立为 demiwtg-data 仓库：flow.py + operators/ + smokes/，另含 source_health/source_plan 源策略）
# 见 https://github.com/vincent9299/demiwtg-data

# modelhub LLM 网关 + 静态代理（独立子项目；详见 modelhub/README.md）
bash modelhub/start.sh && bash modelhub/smoke.sh   # 启动+冒烟；停止: bash modelhub/stop.sh [--all]
```

## 6. 禁止事项速查

- ❌ 在 `meta/` 里建除 2.2 清单外的任何文件
- ❌ 手改 blobs/ 下的文件（包括"顺手修一下坏图"——正确做法是重新采集）
- ❌ 删除图片目录前不做 blobs 内容比对
- ❌ 新增只写不读的"审计/日志"文件
- ❌ 在 `taxonomy/`、`curation/`、`viewer/`、`benchmark/` 之外新增脚本（`bagel/`、`modelhub/` 子项目内部自治，不受此限）
- ❌ 往 datasets/ 里放代码、页面或生成产物（viewer 页面与产物在 viewer/ 内闭环）
- ❌ 恢复历史过程文档（docs/、子目录 README）
- ❌ 在数据/代码里使用 category、leaf、root 作为分类概念（instance/实体 一词亦已由 concept/概念 取代，2026-09-07）
- ❌ 在 concepts.json 里为同一 name 写多条记录（一个概念一条；多处挂载表现为 memberships 多行 + 兼容投影多路径）
- ❌ 手改 concepts 行内 taxonomy 快照或 nodes.instances（挂载真相在 master/memberships；新增/移除挂载写 memberships 新版本后发新 master release；`curation/migrate_concepts.py --refresh-taxonomy` 为 meta 时代历史工具，勿再使用）
- ❌ 恢复 meta/taxonomy.json 或 meta/concepts.json 为可写真源（2026-09-21 起真相在湖内 master/；历史工具读端对已删文件自然失败自守卫）
- ❌ 恢复 instances.json 或 desc/query/source 行级字段（已退役：desc→docs 层草稿、query→采集运行时缓存、source→meta.source_stats；历史溯 git）
- ❌ 把 `datasets/`（demiwtg/meta 权威 JSON 例外）、`state/`、`logs/` 或 `modelhub/` 提交进主仓
- ❌ 把 `bagel/` 与 `benchmark/bagel/` 的重物（模型权重、评测数据、vendored 官方 git 仓）提交进主仓

## 7. 网络与下载约定（2026-08-20 新增：环境里残留已宕机代理 100.89.199.67:7890，pip/curl 会被拖死，故将代理策略定死）

### 7.1 采集集群 SSH 互信互通（2026-09-08 建成；2026-09-17 重构：舰队重编号 + 全公网直连 + 废除 sg-master 跳板）

**架构决策（2026-09-17，用户拍板）**：湖机（本机）接管 master 指挥位；全部 25 台腾讯 SG 机改**公网 IP 直连**（经公司代理 CONNECT），不再经 sg-master 跳板；机器重新编号 r1~r20 / p1~p5，sg-master 降级为普通节点 **p5**（kb 审计嗅探收割 + raw/ 工具箱备份完成后可退役）。机器间 10.3.x.x 内网互通、22022-29 反向隧道、各机 cosfs **不受本配置影响**（原样保留）。CN 组 pipeline-e~i 已于 2026-09-10 释放。

**舰队 25 机**（别名=公网 IP + pconn.py 代理直连；同表在湖机 `~/.ssh/config` 头部）：

| 新名 | 旧名 | 公网 | 内网 | 密钥 |
|---|---|---|---|---|
| p1~p4 | pipeline-a~d | 43.160.215.28 / 43.160.238.29 / 43.160.201.131 / 43.160.240.239 | 10.3.4.14 / 10.3.4.16 / 10.3.0.17 / 10.3.8.9 | lighthouse_key |
| p5 | sg-master（VM-0-14） | 43.160.250.196 | 10.3.0.14 | cluster_key |
| r1~r20 | （09-16 起新机，编号不变） | 见 `~/.ssh/config` | 10.3.x.x | lighthouse_key |

```bash
ssh p1   # 任意别名直连，无跳板；湖机→集群唯一通路=公司代理 10.127.48.4:3128
ssh r7
```

**红线**：公司代理惩罚突发 CONNECT——避免同时对全舰队并发建连（巡检/发射错峰，sleep 间隔）；旧名（sg-master/pipeline-*）已从 ssh config 删除，历史文档中出现的旧名按上表换算。

**集群 → 湖机**（唯一反向隧道在 VM；湖机是无公网 k8s pod 只能反向打通）：

- 隧道（2026-09-14 传输线起扩为 8 条）：`/root/tunnel_keepalive.sh`（常驻保姆，30s 错峰重建 + 4h 超龄换血）——**p5(VM-0-14):22022-22029 → 湖机 sshd(127.0.0.1:2222)**，经 pconn.py 代理反向打通；传输线（882 万图 82% 暂停中）续传依赖它，**勿动**。旧单隧道脚本 `/root/.ssh/lake_tunnel.sh` 进程仍在但已被 keepalive 实质取代。
- **全部 7 机均已配 `Host lake` 别名**：VM 直连 `localhost:22022`；a/b/c/d/e/f 一律 `ProxyJump`（a–d 跳 ubuntu@43.160.250.196；e/f 跳各自 `Host sg`），密钥统一 lighthouse_key（pub 已入湖机 authorized_keys，标注 "demiwtg 采集集群"；a–d/f 的私钥已分发，e 原有）。

```bash
ssh lake             # 集群任一机上执行 → 登录湖机 root
rsync -e ssh data/ lake:/yzp/zhaozy/yangzepeng/0905/demiwtg/...   # 数据回湖
```

- **隧道重启**（湖机 pod 重启后须重拉）：`setsid nohup /root/.ssh/lake_tunnel.sh >> /root/.ssh/lake_tunnel.log 2>&1 &`
- **带宽**：SG 组→湖机流量经湖机↔VM 单隧道（跨境），实测 ~10 MB/s 下行 / ~19 MB/s 上行；CN 组→湖机同样经 VM 隧道绕行（CN→SG→湖机）。传清单/代码秒级，传图片级大件按此估算（百万图回湖约数天，建议分批/先 blob 后清单）。
- 坑位记录：pipeline 首连需 `StrictHostKeyChecking accept-new`；ssh 内 heredoc 会丢（复杂配置本地写好整文件 scp，demiwtg-data HANDOVER 坑 #8）；腾讯云 hostname 按内网尾号命名（pipeline-e 与 SG 的 c 撞名 VM-0-17-ubuntu，非路由错乱）；a/b/c/d/f 原不持 lighthouse_key 私钥（已分发）；ssh -o 选项经 shell 变量展开会打碎含空格的 ProxyCommand（隧道脚本用函数直写命令，勿用 $OPTS 变量拼）。
- 采集集群全量机器清单（七机二维架构/分片）见 demiwtg-data 仓 HANDOVER.md。

### 7.2 增量回湖管线 lake_sync（2026-09-08 起，仓库根常驻 daemon）

**〔2026-09-20 退役〕** daemon 末轮 2026-09-10，此后未再运行；仓库根部署足迹（lake_sync.py 运行副本、merge_meta.py、.qwen.bak、sync_daemon.log、SYNC_HANDOFF.md 旧版、`sync/` 状态目录 1.2GB）已全部清理，真源代码保留在 `collect/lake_sync.py`（含 cn 组完整配置留档）与 git 历史。退役理由与边界见文末决策块「lake_sync 回湖 daemon 退役」。以下为运行期记录，路径已失效，不回改。

`lake_sync.py`（仓库根的常驻 daemon，每小时一轮）把集群侧新采集的图片与 docs 知识正文增量拉回湖：

- **模型**：清单增量镜像（每节点/文件记字节偏移，tail 只取完整行）→ 缺集现算（**湖侧实存 = 已同步**，无独立传输账本）→ tar 流拉取（按 sha 前缀 aa 分摊到组内多节点口）→ 逐文件校验后原子发布 → 源端回执清理（先写组桶 `meta/synced_shas.jsonl` 记账再删 COS，24h 宽限 + 每轮限量；**pages 暂不清理源端**）。
- **两类资产两套寻址**：blobs（图片，**内容寻址**，闸门 = sha256(内容)==文件名）；pages（docs 知识正文，**URL 寻址** `page_sha=sha256(url)`，闸门 = **版本化**——2026-09-09 起 docs 行带 `content_sha/page_bytes`（采集端 operators/page.py 记账），湖侧强复验内容哈希；旧行缺省走宽松门（sha256(url)==page_sha + 非空 + 文件名自洽）。同一 URL 重抓内容会变，强门只对新数据成立。）
- **状态**：`sync/`（state.json 偏移 / verified.jsonl / verified_pages.jsonl / deleted.jsonl / sync.log 轮摘要 jsonl / manifests/<node>/ 镜像清单）。
- **节点拓扑**（2026-09-17 重编号）：SG 组 p5（原 sg-master）+ p1~p4（原 pipeline-a~d，共享新加坡桶）；CN 组 pipeline-e~i 已于 2026-09-10 释放（湖侧运行副本 `demiwtg/lake_sync.py` 已摘除 cn 组；断点 state.json/镜像目录 manifests/ 已随重编号改名迁移）。**r1~r20 尚未入 NODE_GROUP**——SDC 投喂清单（各 r 机 `~/lake/meta/image-shard-extsdcfetch.jsonl`）待接入，接入时须把 r 机加进 NODE_GROUP（其 blob 在 SG 桶 datasets/demiwtg/blobs，与 kb/blobs 旧池不同树）。
- **首战成果**：pages 一轮补齐 10,049 页 / 224MB（bench283 知识页覆盖 **283/283**，平均 35.4 页/概念；authority=serp 9,132 / wiki 888）。
- **运维**：① 改脚本后须重启 daemon 才生效（Python 已载入的模块不会热更）；启动 `setsid nohup python3 lake_sync.py > sync_daemon.log 2>&1 &`；停 `pkill -f '[l]ake_sync.py'`（注意坑：pkill 模式串若原样出现在自己命令行里会自杀，用 `[l]` 括号法）。② 新节点重装导致主机密钥变更 → `ssh-keygen -R <ip>` 后 accept-new 重建（2026-09-08 pipeline-h/i 即此因被拒，已修）。③ 湖侧 `images.jsonl` 尚未合并镜像清单（新采集图在 `sync/manifests/` 可见，湖侧抽样/覆盖统计待合并后才反映）。
- **pages 清洗（2026-09-08）**：同步回的原始页是爬虫直出、约 80% 字节是站点外壳，故 `curation/clean_docs_pages.py` 出净版另存 `state/collect/docs_clean/`（原始 `datasets/demiwtg/pages/` 只读不动）。全量 10,020 页 → **净版 4,326 页（43%）**、150.9M 字压到 31.0M 字；分源可用率悬殊（wiki 96% / serp 34%）；拒页两大头 gibberish 2,280（反爬诱饵）+ short_raw 2,052（JS 壳站空壳）合占拒页 75%，均属采集端可避免的浪费。净版含 `concept_docs.jsonl`（{name, kind:"passages", body} 与湖侧 docs 层同构，bench283 覆盖 272/283，建议与现存 summary 摘要层**并存不覆盖**）。分析与呈现：`curation/docs_analysis.ipynb`（demiwtg kernel，含质量分级/原始 vs 净版样例/域名诊断/单概念钻取）。已记录的两类内容缺陷：SERP 概念误绑（如 Stargate SG-1 页被绑到「新加坡」）与近似重复页（en.m/en 双站同文，精确 sha 去重抓不到）。
- **已知缺陷与待办（2026-09-09 明细抽样发现，证据见 `curation/lake_sync_details.ipynb`）**：① **`needed_blobs` 只取 `blob_path`**，而集群两代清单路径键不同——`backfill-shard-*`（7 字段极简、无打标）用 `blob_path`，`image-shard-*`（24 字段、含 width/height/fetched_at）用 `path`，故后者被静默跳过（实测 image-shard 63,883 行中 99% 未回湖）。一行修法：`rel = r.get("blob_path") or r.get("path")`；概念键亦须兼容 `concepts`/`instances`（老 image-shard 用 instances）。② **VLM 打标字段全 null**：镜像里 quality/identity/kb_match/richness/caption 非空率 0.0（连 image-shard 行也是），故湖侧质量门对新采图完全失效（合格行 0）；需集群侧确认打标阶段是否落盘。③ `backfill-shard` 行无 `fetched_at`/`width`/`height`（溯源与尺寸门失效）。④ **概念误绑真实存在**：SERP/fandom 关键词撞车，探针「南丁格尔」14 行中 6 行是 Elder Scrolls 的 Nightingale Armor/Hall、鬼灭之刃与 Terra Battle 角色页。⑤ notebook 性能陷阱：PVC 上逐文件 `stat` 求体积会把整本拖到 ~9 分钟（实测 user CPU 仅 33s），改为「只列目录计数 + 用清单 size_bytes 估算」后全本 **63s**；同理逐行 `os.path.exists` 判回湖应改为一次列目录建 sha 集合。
- **2026-09-09 修正版部署**（repo demiwtg-data 为唯一真源，本文件副本由其覆盖）：① pages 缺集**跨组全局去重**（同 URL 两队都抓过只拉一份，修首轮 29 页双拉）；② blob/pages **两组并行拉取** + State 审计写锁 + tar 超时收紧 3600→900s + **逐批进度打印**（blob批/pages批 行，含耗时与 ok/bad/fail——此前 21h 无输出无诊断即此缺口）；③ 「镜像→真 meta」例行化 `merge_meta.py`（lake_sync 每轮末尾自动调用，已取代 merge_docs.py）：docs 全量重合并 → `meta/docs.jsonl` 10,023 行（283 概念，键 (page_sha, concepts)）；images **增量追加** → `meta/images.jsonl`（键 (sha256,instances)、偏移状态 sync/merge_state.json、不重写 285 万行大账；首轮 63,883 镜像行追加 50,882 新行，35 秒）。结构对齐：内容真源 {blobs,pages}/、清单 meta/（湖 meta/ 另有 concepts.json/taxonomy.json 既有真源）。backfill/dead 镜像不进账（复原操作/死信留档）。消费端只读 meta/，不碰 sync/manifests/。daemon pid 以 pgrep 为准，日志 sync_daemon.log（逐批）+ sync/sync.log（轮摘要）。

- 国内下载**不走代理**，优先找国内源（如 pypi 用 `pypi.tuna.tsinghua.edu.cn`；注意部分域名 DNS 只返回 IPv6 记录而本机无 IPv6，需确认 A 记录可达）。
- 确需访问外网（pypi.org、download.pytorch.org、GitHub 等）时才用代理：

```bash
export http_proxy=http://192.168.10.109:10808
export https_proxy=http://192.168.10.109:10808
# 或
export ALL_PROXY=socks5h://192.168.10.109:10808
export no_proxy="localhost,127.0.0.1,192.168.10.0/24,modelscope.cn,modelscope.org.cn,.modelscope.cn"
```

- 执行任何下载前，先 `env | grep -i proxy` 检查残留：发现已宕机的旧代理（100.89.199.67:7890）必须先 unset 或按上述配置覆盖。
- **外网链路直连优先**（2026-08-22 拍板）：外网源能直连通就直连，只有实测直连不通的才走代理，减少代理流量；代理源名单按实测增删（collect_v2 落点在 `infra._PROXY_SOURCES` 白名单制：2026-08-22 实测 mal/bing_images/yandex_images 直连可通走直连，wikimedia(_zh)/anilist/pixiv/deviantart 直连超时留代理池）。

> **图片全量守护补充（2026-09-10，用户授权选模型和失败拉起）**：本轮沿用已验证的本地 Qwen3.8-27B，候选模型未下载完整，不宣称横向实测胜出。`curation/run_image_pipeline.sh` / `curation/image_supervisor.py` 可接管并恢复当前本地 8000 服务及图片标注进程；该明确授权覆盖此前“不启动/停止用户模型服务”的限制，仅限本任务精确匹配的服务。状态、日志、断点仍在 `state/curation/image_preannotation_v1/`，详见 pipeline_memory.md 设计第 11 节。新增此条是记录本次运行管理授权，不扩展到付费接口或其他任务服务。

> **GPU 让位补充（2026-09-10，用户明确授权）**：图片预标注是利用空闲 GPU 的后台材料整理任务。当前研究实验需要资源时，助手可自行暂停该标注及其本地 Qwen 服务，保留断点和结果，实验结束后恢复原服务与标注；不再为同一让位操作重复询问。具体编排见 `curation/pipeline_memory.md` 第 12 节与 `curation/rag_diagnostic_session.py`。不授权删除标注、不混入其他模型、不影响其他无关任务、不使用付费接口。

### Notebook直接编排Dataset（2026-09-15，用户明确调整）

现役交互入口仍为curation/v4/knowledge_debug.ipynb，但不再使用StreamFlow对象隐藏业务编排。原始来源读取、概念选择、资料关联、计数、分批及知识算子直接通过demiflow Dataset操作连接，业务算子在dataset_operators.py与knowledge_stages.py；notebook_io.py只提供文件读取/版本冻结。StreamFlow保留历史命令行兼容，不作为notebook主线。此条覆盖前述StreamFlow为现役交互编排对象的说明，详见pipeline_memory.md 设计第36节；业务数据契约、原始datasets只读和历史运行不可覆盖约定不变。

### 知识pipeline统一原生算子接口（2026-09-15，用户要求）

用户要求迁移模型调用，并将包括读数据在内的通用能力尽量下沉到demiflow。现役knowledge_debug.ipynb直接使用read_datasource(ReadSource)、Dataset.union、join、reduce_by_key、group_batches、map_cached、map_prompt_async、checkpoint和read_json。ReadSource实现原生Datasource/ReadTask，通用文件解码/gzip/JSON数组流式读取在demiflow，采集schema解释/来源范围审计在curation。知识actor仅做准备、业务校验和构造候选；逐行缓存与阶段落盘均由demiflow执行，不再在新知识链调用旧Stage缓存或LocalModel。提示词、完整请求响应、持久预算、不确定调用阻塞和版本冻结继续保留。当前默认新run为knowledge_native_prompt_v5，状态和限制见pipeline_memory.md 设计第38节；兼容CLI不代表新主线，原始数据、旧run、评分不改写。

### Source直接使用原生读取（2026-09-15，用户纠正）

现役notebook不再使用ReadSource包装：具体文件直接data.read_records → checkpoint保留原始解码行 → filter/map转换业务字段。ConceptFromRecord、DocumentFromRecord、ImageFromRecord不读文件；通用扫描状态、坏行、gzip、JSON解析在demiflow。ReadSource仅供历史兼容。此条覆盖前述现役read_datasource(ReadSource)入口，文件快照冻结、原始材料只读、知识审核及版本边界不变，详见pipeline_memory.md 设计第40节。

### 知识忠实性审核四模型对照（2026-09-15，用户明确授权）

理由：用户要求试用本地四个模型，覆盖此前知识整理只准调用Qwen3.8的模型范围限制，限定为本次小批对照。候选为Qwen3.8-27B、Qwen3.6-35B-A3B、gemma-4-26B-A4B-it、gemma-4-31B-it；通过prompt_config.py中显式local_model_comparison选项，仍只允许本机8000/8001直连，禁止付费网关。该选项不改变公共预标注的原Qwen协议与默认模型。

比较仍使用demiflow PrepareFidelity → map_prompt_async → ApplyFidelity，不重跑提取或开始出题；已有错误回归与基于未参与调参原始文章的受控对照分别记录。每模型两次调用，输入、提示词、参数和全部响应先冻结后比较，不按结果调参重试。按已有GPU让位授权等待标注落盘、暂停其服务、顺序加载模型，结束后恢复原Qwen命令与标注断点。细节与实测结果写入pipeline_memory.md 设计第46节及state/curation/v4/fidelity_four_models_v1，不把模型同意或格式合格视为人工事实核验。


### 工作区保全与 collect 合并（2026-09-18，用户明确要求）

理由：用户要求尽量保留未入库代码，并将 demiwtg-data、kb_audit 统一纳入主仓 collect，旧 data/collect_v2 归档。此条覆盖上文关于采集独立仓库、禁止历史文档归档及新增顶层模块的旧限制。

- `collect/` 是原 demiwtg-data 的现役采集模块，`collect/kb_audit/` 保存原工作区审计工具，`collect/archive/collect_v2/` 保存旧兼容层。其源码、配置模板、交接文档纳入主仓；凭据、环境、数据与运行状态不入库。
- 工作区原 `demiwtg-data`、`kb_audit` 及本仓 `data/collect_v2` 保留相对符号链接，以兼容运行中的脚本；后者只是兼容入口，不放新代码。远端集群路径和 COS 对象键不因本地整合改名。
- `archive/workspace_20260918/` 与 `archive/ignored_sources_20260918/` 保存 state、_staging 和工作区根目录遗漏的源码、文档、配置及有限的小型结果，按原路径保留来源。MANIFEST.json 记录纳入／排除及内容哈希；这是历史快照，不作为新业务入口。notebook 快照去执行输出与内嵌附件；原始文件保留不变。
- `tools/` 是仓库保全工具；恢复或备份相关文档允许保留。原始 datasets/state、模型、Python 环境和依赖缓存继续不入 Git；归档源代码不等于备份完整实验数据。
- collect 不保留嵌套 .git；原采集仓库 Git 历史保存在主仓 archive/demiwtg-data-20260918 分支和本地 backup_audit Git bundle。


### 移除采集旧入口（2026-09-18，用户后续要求）

工作区 `demiwtg-data` 兼容链接已移除，唯一现役目录为 `demiwtg/collect`。现役本地脚本的绝对路径已同步，不再重建旧入口；覆盖上一节对此链接的保留约定。Git 历史归档、历史源码快照及远端 COS/集群路径保留，不因本地入口更名改写。


### 移除 data/collect_v2 兼容软链（2026-09-20，用户要求）

本仓 `data/collect_v2` 相对软链（→ collect/archive/collect_v2）已删除，`data/` 目录随之撤销，覆盖 2026-09-18「本仓 data/collect_v2 保留相对符号链接」的保留约定。存量调用方同步改直连顶层包：taxonomy/audit_nodes、taxonomy/gen_taxonomy_kb（`taxonomy.llm_common`）与 benchmark t2i/edit eval_sample、t2i eval_sample_domain_uniform（`taxonomy.mount_map`），py_compile 与导入解析均验证通过；其中两份 taxonomy 脚本的 collect_v2 导入自 2026-09-05 布局提升起即解析失败（sys.path 指向仓库根而非 data/），本次一并修复。collect_v2.* import 面只剩 `collect/archive/collect_v2/` 归档件（完整源码快照在同级 `collect_v2_staging/`），仅供历史查阅。.gitignore 的 `/data/` 规则保留防复建；历史决策块中 data/ 相关表述为当时快照，不回改。


### 清理 kb_audit 历史工作区（2026-09-20，用户要求）

`collect/kb_audit/`（2026-09-18 自湖机工作区保全的 kb 图池审计工具及 raw/ 历史脚本，共 72 文件）已删除，工作区 `kb_audit -> demiwtg/collect/kb_audit` 兼容软链一并移除。清理依据：kb 图池审计已于 2026-09-17 定案（毒行 7,904,315 / 真图 957,039，清单交付 COS `audit/2026-09-17/`），毒行重收与缩略升级由 `collect/image_backfill/` 现役工具链（kb_orchestrate/kb_backfill）承担，基础审计工具在 `collect/audit/` 另有更新副本；删除前 72 文件全部在 git 跟踪中，历史可恢复，另有 COS `audit/kb_images_20260917/` 双备份。覆盖 2026-09-18「collect/kb_audit/ 保存原工作区审计工具」的保全约定；`tools/WORKSPACE_BACKUP.md` 已同步。历史决策块中 kb_audit 表述为当时快照，不回改。


### lake_sync 回湖 daemon 退役（2026-09-20，用户确认）

lake_sync 线整体退役，仓库根 daemon 足迹已清理：`lake_sync.py`（运行副本，cn 组已摘版）、`merge_meta.py`（与 collect/ 副本逐字节一致）、`lake_sync.py.qwen.bak`、`sync_daemon.log`、`SYNC_HANDOFF.md`（9-09 pages 线前旧版，collect/ 有更新版）与 `sync/` 状态目录（1.2GB：state/merge_state 断点、manifests 镜像、verified/deleted 回执、9-13 图片丢失事故调查件 lost_blobs/lost_ledger/gz-restore-parked）。退役依据：daemon 自 2026-09-10 末轮后未再运行；数据源 p1-p5 已于 2026-09-17 退役且数据保全 COS（node-backup）；三大批次执行线走 COS 直传/直拉不经 lake_sync；r1-r20 SDC 线已定为 parts 收集→去重合并→COS 交付，本条覆盖 §7.2「r 机待接入 NODE_GROUP」的设想。根 `blobs/` 数据池不动；`curation/lake_sync_details.ipynb` 为历史分析快照，读端指向已删 sync/ 属预期；.gitignore 的 /sync/、/sync_daemon.log 规则保留防复建。真源代码 `collect/lake_sync.py` + git 历史，重启需按新节点重配 NODE_GROUP 与断点。同批清理根 `blobs/` 残迹（28KB：9-13 lost_blobs 调查日误落的 3 个 COS NoSuchKey 错误响应 XML，非图片、真湖无对应 sha；真湖 `datasets/demiwtg/blobs/` 与 `kb/` 不动）。


### Pipeline V0／V1命名（2026-09-19，用户确认分界）

理由：用户明确V0应为北京时间9月19日18:23、思路变化前的首次完整备份，V1只保留思路变化后的最新实现。`curation/archive/pipeline_v0/`为该272文件基线，`curation/pipeline_v0/`链接其中的标准pipeline代码；`curation/pipeline_v1/`链接现有`curation/v4/`最新活动实现，版本映射为`curation/pipeline_versions.json`，维护版本只列V0、V1。此前误标为V0的22:34备份已移至`curation/archive/pipeline_history/20260919_2234_checkpoint/`，仅保留历史证据。知识、出题、训练数据、评测四个同级pipeline共用这一版本分界，保持demiflow框架与标准入口。旧目录名仅作兼容链接，保留`curation.v4` import、notebook字节和冻结run标识，避免破坏活动知识生产及实验校验。更早实验V1/V2/V3以及run内源码快照属于历史归档，不是额外活动pipeline版本。

用户于2026-09-20明确更正为四个pipeline；训练数据独立入口为`training_pipeline.py`、`training_debug.ipynb`及`training_stepbystep.ipynb`，V0备份和V1均已包含。版本登记的正式键为`training_data`，`training_branch`仅为旧名称兼容映射，不再将训练数据表述为三条pipeline之外的附属分支。
