# GLM 交接：469 个概念的双模型图片审核与运行监控

## 最新补充：旧审核完成，改为零模型后处理（2026-09-28）

**本节覆盖下文旧v3启动/续跑/GPU检查安排。** 用户最新要求新运行仍由用户手动启动；GLM只有收到新的明确执行指令才启动，不根据下文旧交接自动重跑v3。

- 旧v3已完成Qwen1165次/Gemma935次响应，image_relevance@1共1165批；旧宽字段后处理占用很大内存。经用户授权，已停止精确识别的旧469进程（历史PID5678/5941），运行锁释放。不要按历史PID执行kill，不动其他GPU任务。
- 停止后旧run无results/visual，公共images仍@9（2026-09-27）；未产生旧469公共写入。公共按SHA一图一行，故障是嵌套sources重复复制导致宽行膨胀，不是65,463个重复图片主键。
- 当前notebook为新v4_scope3；reviewed_run指向v3，max_calls=0，全库关联概念数>=3的图排除。先在固定raw images@5过滤，只提取旧请求的身份映射，再复用旧审核结论，绕过旧26.29GiB准备表。无需GPU、不发新模型请求；若触发模型服务说明用错配置，应停止并核对。
- 新运行仍走同一config/run_pipeline，第二格手工启动。第3格查看469概念、规则排除、双模型结果和缩略图，第4格观察阶段和旧调用统计；不要将旧2100次调用计成本轮新增。
- 不改原图表/旧响应/旧阶段指纹，不删半成品后盲重跑。新旧运行锁同时持有；若旧run重新被占用，核对任务身份再处理。
- 本轮唯一候选图3956→3110，关联4258→3118；通过门槛和至少5图概念数以新run正式结果为准。只读范围/旧请求绑定核对469行耗时6.88秒、峰值RSS900MiB，不含像素校验、后处理和公共提交，不能当全流程性能数据。
- 验收：全部469概念结果提交、规则排除单列、保留图片有原审核依据；results/visual固定引用可读、公共新版本仍保留范围外图片与其他生产者列。公共只在最终export提交，输入/审核/材料等为本run阶段表。历史日志和notebook输出保留，不代表当前v4已运行。

运行观察补充（2026-09-28 15:45 UTC）：另一位执行者于15:41启动v4，当前父进程3932/内核4256；已核对冻结配置使用image_requests/image_relevance新复用路径，正在处理，无公共提交。15:34的尝试因旧manifest不一致退出，其日志保留。本会话未启动该生产运行，也未停止它；接手先查实时运行锁/阶段，不能重复提交或根据本段历史PID操作。配置和第1/3/4格已只读执行验证，四格语法通过；正式第2格仍由调用方执行。

以下是12:05 UTC的旧交接快照，仅供历史排障。


用户准备下次将本轮交给 GLM 执行、监控。本文编写时只交付文档，没有重新启动审核，也没有操作 Edit 任务进程。用户将本文交给你并要求执行后，按下文接手：已有本轮进程就监控；尚未运行则确认资源可用后启动同一本 notebook，跟到结果验收。不要只返回启动命令或以“进程已启动”结束交付。

先读项目 `/yzp/zhaozy/yangzepeng/0905/demiwtg/AGENTS.md`。本轮范围、提示词、模型和配置已确定；护航不包含平台改造、调整审核标准、重新选样或启动下游出题。

## 1. 交接时的真实状态

最新核对时间：**2026-09-28 12:05 UTC**。以下是当时快照，接手时重新检查。

| 项目 | 已核实状态 |
| --- | --- |
| 当前 run | `t2i_dual_keep469_visual_v3_tp2_20260928` |
| 本轮写入者 | 已退出，运行锁未被持有 |
| `visual_inputs` | 已提交 `@1`，469 行 |
| `image_requests` | 已提交 `@1`，1165 批 |
| Qwen / Gemma 调用日志 | SQLite 已存在，调用记录总数 **0** |
| 后续阶段 | `image_primary` 及后续审核表尚未创建，没有残留的半成品初审表 |
| 公共目标 | 没有本轮 `results/visual` 提交记录，本轮尚未更新公共表 |
| 失败原因 | 初审服务申请 `gpu_0.lock` 失败，尚未发出模型请求 |
| 最新资源状态 | 12:05 检查时 GPU 0、1 显存均为 0 MiB、无计算进程；8000/8001 端口空闲，未发现本轮或 GPU 资源持锁者 |
| 上次冲突占用方 | 11:59 时是 Edit 原图任务 `scene_pool_add_v5_l4_keep_hold_tp2_v1`，持锁 PID `64016`，占用 GPU 0、1 和端口 8000；到 12:05 已释放资源，这不代替对 Edit 业务结果的验收 |

这次属于**服务启动前资源冲突**。准备结果仍有效；接手时确认资源仍可用，使用原 run、原配置执行第 2 格即可复用。PID 是历史观察，不是未来可直接终止的目标。

不要删锁文件来“解锁”，不要终止 Edit 或其他任务，不要临时借用 Edit 的模型服务。两个任务的上下文长度等部署参数不同；本轮之后还要释放 Qwen、加载 Gemma。等待对方正常释放双卡即可；若用户另行指示停止对方任务，按其明确指示处理。

## 2. 入口、范围与参数

- 工作区：`/yzp/zhaozy/yangzepeng/0905`。
- 项目：`/yzp/zhaozy/yangzepeng/0905/demiwtg`。
- Python：`/yzp/zhaozy/yangzepeng/0905/env/bin/python`。
- Notebook：`/yzp/zhaozy/yangzepeng/0905/demiwtg/preparation/images/review/image_review_debug.ipynb`。
- 唯一正式入口：`preparation.images.review.image_review_pipeline.config(...) → run_pipeline(config)`。后台直接执行 notebook 中的调用，不另抄模型参数、不写另一条业务执行流程。
- 已核对 Jupyter 内核 `demiwtg` 指向上述 Python。使用新内核读取磁盘上的 notebook；旧编辑器内容曾保留已经删除的 `preparation.operaters` 导入，仅重启内核不会更新 cell 文本。现役图片算子归 `preparation.images.review.operaters`。

固定来源 URI 相对于工作区：

| 用途 | URI / 版本 |
| --- | --- |
| 469 个概念、完整 taxonomy | `demiwtg/benchmark/t2i/fine_screening/datasets/results__high_l3_categories_uniform1000_v1_20260927.lance@1`，全部有效双模型 keep |
| 原始图片 | `demiwtg/collect/datasets/images.lance@5` |
| 复用准备的父 run | `t2i_dual_keep469_visual_v2_20260928`，两个准备表均 `@1` |
| 本轮 run | `t2i_dual_keep469_visual_v3_tp2_20260928`，已经保存自己的两个准备表 |
| 执行终点 / 写模式 | `through='export'` / `write_mode='merge'` |
| 公共目标 | **`/yzp/zhaozy/yangzepeng/0905/datasets/images.lance`**，不是项目目录下的同名路径 |

469 是公共 preparation 本次更新范围。无图、无可读图的概念也保留结果，但不发空图片请求。审核之后选哪些概念、是否至少 5 张通过图，由下游决定；本轮不抽 200、不缩为 5 个、不开始出题。

实际审核 prompt 为本 pipeline 的 `prompts/tasks.yaml`，版本 `v2-visual-material-review-1`。传概念名与完整 taxonomy 作为身份上下文、真实图片像素；不传上游筛选理由。Qwen 初审有有效 keep 的批次才进入 Gemma 独立复审，复审不看初审答案；最终只纳入双模型有效确认且通过发布检查的图片。Gemma 请求数通常小于初审，不能要求两模型调用数相等。

| 参数 | Qwen 初审 | Gemma 独立复审 |
| --- | --- | --- |
| 模型名 | `qwen3.8-27b` | `gemma-4-31b-it` |
| 权重（相对工作区） | `models/Qwen3.8-27B` | `models/gemma-4-31B-it` |
| 端口 | 8000 | 8001 |
| GPU / TP / DP | `[0,1]` / 2 / 1 | `[0,1]` / 2 / 1 |
| 客户端并发 / 服务 `max_num_seqs` | 128 / 64 | 64 / 32 |
| 输入队列 | 16 | 8 |
| 上下文 / `max_num_batched_tokens` | 32768 / 16384 | 32768 / 16384 |
| 显存比例 / `enforce_eager` | 0.92 / False | 0.92 / False |
| 输出上限 / 请求超时 / 加载超时 | 8192 / 600 秒 / 900 秒 | 8192 / 600 秒 / 900 秒 |
| 每节点新请求预算 / thinking | 5000 / False | 5000 / False |

每批最多 4 图，最大边 1536、JPEG90；准备线程并发 8、队列 8。两模型**依次使用同一组双卡**，demiflow 原生服务负责加载、就绪、释放；不另起 vLLM。缓存全部命中的模型节点可能无需加载。参数是当前已冻结配置，不代表实测性能极值；不能将 Edit 单图吞吐直接当作本轮 4 图、双模型吞吐。

## 3. 启动前检查与执行方式

Notebook 四格的职责：

1. `visual-config`：读固定输入、配置、查看 GPU/端口；不审核。
2. `visual-run`：真正执行初审 → 复审 → 本轮汇总 → 公共 merge。
3. `visual-details`：只读查看全部 469 个概念、缩略图及两模型理由。
4. `visual-runtime-status`：只读 GPU、SQLite 计数、vLLM 日志。

只跑第 1、3 格不会开始审核。`waiting_for_review` 是查看格在没有本轮最终结果时给出的占位状态，不是模型的审核结论。

先执行以下只读检查，并结合第 4 节的阶段/调用记录判断：

```bash
cd /yzp/zhaozy/yangzepeng/0905/demiwtg
lslocks -o PID,COMMAND,TYPE,MODE,PATH | rg 'local_model_gpus|t2i_dual_keep469|benchmark_edit_source_images|PID'
nvidia-smi --query-gpu=index,utilization.gpu,memory.used,memory.total --format=csv
nvidia-smi --query-compute-apps=pid,process_name,used_gpu_memory --format=csv
pgrep -af '[n]bconvert.*image_review_debug.ipynb'
PYTHONPATH=/yzp/zhaozy/yangzepeng/0905/demiflow:/yzp/zhaozy/yangzepeng/0905/demiwtg /yzp/zhaozy/yangzepeng/0905/env/bin/python - <<'PY'
from pathlib import Path
import socket
from demiflow.execution.artifacts import run_is_active
run_dir = Path('/yzp/zhaozy/yangzepeng/0905/demiwtg/preparation/datasets/_demiflow/t2i_dual_keep469_visual_v3_tp2_20260928')
print('本轮writer_active:', run_is_active(run_dir))
for port in (8000, 8001):
    with socket.socket() as probe:
        probe.settimeout(1)
        print('port', port, 'occupied:', probe.connect_ex(('127.0.0.1', port)) == 0)
PY
```

- 已有本轮 nbconvert、内核工作线程或运行锁：接管监控，不再提交。nbconvert 启动与 pipeline 加锁之间有间隔，因此不能只查运行锁。
- 已有完整本轮结果及公共提交：直接验收，不重跑模型。
- 其他任务持有 GPU 锁、端口或显存：等待并观察归属；资源释放后复查。锁文件存在本身不代表持锁，GPU 利用率瞬时为 0 也不代表模型已释放。
- 若接手状态仍与 §1 一致，且没有本轮进程、双卡及两个端口都可用，沿用当前 notebook 启动。若已有新响应或新错误，先按 §5 检查恢复条件。

交互执行时，使用新内核依次运行第 1、2 格，结束后第 3 格。用户交给 GLM 后应避免另一边再手工启动第 2 格。

后台可直接执行同一本 notebook，输出另存运行目录，保留源 notebook 及其原有输出。以下是**供接手者执行的命令，编写本文时未执行**：

```bash
cd /yzp/zhaozy/yangzepeng/0905/demiwtg
review_runtime='/yzp/zhaozy/yangzepeng/0905/demiwtg/preparation/datasets/_demiflow/t2i_dual_keep469_visual_v3_tp2_20260928'
review_attempt="$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$review_runtime"
nohup env PYTHONUNBUFFERED=1 /yzp/zhaozy/yangzepeng/0905/env/bin/jupyter nbconvert \
  --to notebook --execute \
  --ExecutePreprocessor.kernel_name=demiwtg \
  --ExecutePreprocessor.timeout=-1 \
  --ExecutePreprocessor.iopub_timeout=600 \
  --output="image_review_debug.executed.$review_attempt" \
  --output-dir="$review_runtime" \
  preparation/images/review/image_review_debug.ipynb \
  > "$review_runtime/submit.$review_attempt.log" 2>&1 < /dev/null &
review_pid=$!
printf '%s\n' "$review_pid" > "$review_runtime/submit.$review_attempt.pid"
printf 'PID=%s\nLOG=%s\n' "$review_pid" "$review_runtime/submit.$review_attempt.log"
```

记录启动时间、PID、日志、实际 run 和 notebook 源码摘要。不要把参数只改在后台内存中。nbconvert 通常在结束时保存执行副本，内核的业务输出不保证实时写到提交日志，运行中以 SQLite 和服务日志交叉判断。第 3 格第一次读取较大的历史 payload、生成缩略图会较慢；模型已完成后仍在渲染，不等于审核卡死。若第 3 格单独失败，先核对第 2 格结果，不能因此重新提交审核。

## 4. 监控：看哪些记录

固定路径：

| 用途 | 路径 |
| --- | --- |
| 本轮控制目录 / 运行锁 | `demiwtg/preparation/datasets/_demiflow/t2i_dual_keep469_visual_v3_tp2_20260928/`，锁为 `.lock` |
| GPU / 端口资源锁 | `_demiflow/local_model_gpus/`，相对于工作区，注意与上一行不是同一目录 |
| 元数据表 | `demiwtg/preparation/datasets/metadata__t2i_dual_keep469_visual_v3_tp2_20260928.lance` |
| SQLite 调用日志 | `demiwtg/preparation/datasets/model_calls__t2i_dual_keep469_visual_v3_tp2_20260928.sqlite` |
| 两模型服务日志 | 本轮控制目录的 `primary_vllm.log`、`review_vllm.log` |
| 阶段数据 | `demiwtg/preparation/datasets/<stage>__t2i_dual_keep469_visual_v3_tp2_20260928.lance` |

可在另一个内核只运行第 1、4 格观察，不能在监控内核再次运行第 2 格。以下命令不加载像素、不请求模型、不创建调用库：

```bash
cd /yzp/zhaozy/yangzepeng/0905/demiwtg
PYTHONPATH=/yzp/zhaozy/yangzepeng/0905/demiflow:/yzp/zhaozy/yangzepeng/0905/demiwtg DEMIWTG_DATASETS_ROOT=/yzp/zhaozy/yangzepeng/0905 /yzp/zhaozy/yangzepeng/0905/env/bin/python - <<'PY'
from pathlib import Path
from datetime import datetime, timezone
import sqlite3
from preparation.articles.operaters.runfiles import run_records
from demiflow.execution.artifacts import run_is_active
run = Path('/yzp/zhaozy/yangzepeng/0905/demiwtg/preparation/datasets/t2i_dual_keep469_visual_v3_tp2_20260928')
print('UTC:', datetime.now(timezone.utc).isoformat())
print('writer_active:', run_is_active(run.parent / '_demiflow' / run.name))
records = run_records(run)
for stage in ('visual_inputs', 'image_requests', 'image_primary', 'image_review_inputs',
              'image_review_responses', 'image_relevance', 'visual_reviewed',
              'visual_materials', 'visual_image_meta', 'knowledge_base'):
    entry = records.get('stage/' + stage)
    ref = (entry or {}).get('dataset_ref', {})
    print(stage, 'committed' if entry else 'not_committed',
          'rows=', ref.get('row_count'), 'version=', ref.get('lance_version'))
print('public_result:', records.get('results/visual'))
path = run.parent / ('model_calls__' + run.name + '.sqlite')
if path.is_file():
    with sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=3) as db:
        rows = db.execute("""
            SELECT coalesce(json_extract(request_json, '$.payload.model'), 'unknown'),
                   count(*), sum(response_json IS NOT NULL),
                   sum(response_json IS NULL), sum(error_json IS NOT NULL)
            FROM calls GROUP BY 1
        """).fetchall()
        print('model, reserved, saved_responses, unanswered, transport_errors:', rows)
else:
    print('No call journal yet')
PY
```

不要调用 `run_records(...).items()` 后打印整份 manifest/所有资产，也不要全量读取 `image_requests/image_primary` 的 payload 来看进度；其中有大体积图像或来源数据。取明确的元数据键、投影列；确需看 payload 时按固定版本、小批逐行读，避免一次解码全表占用大量内存。

开始阶段观察加载日志、首次响应和服务切换；稳定后每 3—5 分钟检查一次，等待应可被用户指令中断。状态未变化时安静等待，只在完成、故障或需要用户介入时报告，不定时刷屏。

监控口径：

- Qwen 输入固定 1165 批，最多 4 图/批。Gemma 只处理初审存在有效 keep 的批次；响应数、图片数、概念数是三个不同口径。
- SQLite `saved_responses` 只代表保存了原始响应，不保证 JSON/协议有效或图片通过；`unanswered` 运行中可能包含在途请求，退出后才按不确定调用核查。
- 每 20 条完成行输出业务进度，首次也输出；没有完成行时不会定时打印。准备复用仍会重验原图 SHA，可能先有 CPU/I/O 开销。
- 两个模型顺序运行，Qwen 释放到 Gemma 就绪之间有正常加载空窗。首次 CUDA Graph/编译在 900 秒加载预算内观察日志，不因短暂 GPU 低负载重启。
- 关注 Running/Waiting、KV 占用、抢占、OOM、token 吞吐和错误。新请求速度与缓存复用速度分开记录；不能用混合速度声称模型性能。
- 无日志时结合响应增长、服务日志、进程和请求超时判断；持续超过加载/请求预算仍无进展时，保存证据再诊断，不盲目重启。

## 5. 故障与续跑边界

| 情况 | 处理 |
| --- | --- |
| 再次 GPU/端口冲突 | 查实时持锁者与进程归属，等对方释放；保留当前 run 和已提交准备表。不要删锁、抢占或改成外部服务绕过冲突。 |
| 同 run 已活跃，或中断后锁仍在 | 继续检查宿主内核/线程；`asyncio.to_thread` 的外层 await 被中断不等于工作线程退出。确认旧执行者已退出才考虑重提。 |
| 服务启动失败且日志仍为 0 调用 | 保存异常和服务日志；解决外部资源原因后，可用同配置续跑。此次 §1 的锁冲突属于此类。 |
| 已保存完整响应，执行中断 | 同 run、同代码/配置/输入可复用已提交阶段和相同请求的完整响应。先核对没有不确定调用、没有未登记的半成品阶段表，再用正式入口续跑；不能仅凭同 run 就承诺任意更改后复用。 |
| `UncertainPromptCall` / 有预约但无完整响应 | 停止自动重提，保留 request key、错误、已有完成数。按平台既有恢复能力诊断；不删除预约、不把失败变成功、不换 run 绕过。 |
| 残缺 JSON、`finish_reason=length`、协议无效 | 单列技术失败，保存原响应。同请求的缓存不会因重跑自动变正确；不能擅自改 prompt、输出上限或全量重算。 |
| 图片被排除或模型有效 pending | 正常业务结果，不为提高通过率重试。两个模型分歧可成为 pending，不伪装成通过。 |
| `Stage inputs changed` / 冻结配置不一致 | 保留当前产物，定位变动；不改 manifest 或指纹、不新建 run 重算来掩盖差异。 |
| 表存在但没有 `stage/...` 登记 | 视为待诊断的未完成提交；不手工补写成功引用，不删除表盲跑。 |
| OOM、服务异常退出、SHA 不一致 | 留存日志、进程/资源及固定输入证据，报告具体阻塞。不得通过改像素、换模型或放宽校验偷偷继续。 |
| 公共表版本冲突 / 列契约问题 | 本轮审核阶段保留，核对其他写入者及正式 merge 接口；不改 overwrite，不手工覆盖公共表。 |
| 查看格出错，但最终阶段和公共提交已齐 | 单独修复/重跑只读查看；不重提模型。 |

本轮护航不授权改 demiflow 的锁、缓存身份、预算、SQLite 日志或写入语义，也不在平台放 469/图片审核特例。若必须修平台才能继续，保留证据并报告能力缺口，按独立事项处理。当前 v3 已冻结：资源参数变化也应先评估运行契约，不能直接改 notebook 后假定可同名续跑。不要沿用其他任务历史文档中“删除预约”“迁调用库”“修改并发后直接重提”等操作。

## 6. 完成验收与交付

不要以进程退出、锁释放、某个模型完成或 SQLite 响应数够了就宣布成功。此入口没有 screening 的 `complete=True` 摘要约定，实际证据是登记的阶段固定引用和公共提交。

先做以下轻量只读验收；它不读图片或大体积 payload：

```bash
cd /yzp/zhaozy/yangzepeng/0905/demiwtg
PYTHONPATH=/yzp/zhaozy/yangzepeng/0905/demiflow:/yzp/zhaozy/yangzepeng/0905/demiwtg DEMIWTG_DATASETS_ROOT=/yzp/zhaozy/yangzepeng/0905 /yzp/zhaozy/yangzepeng/0905/env/bin/python - <<'PY'
from pathlib import Path
from collections import Counter
import lance
from preparation.articles.operaters.runfiles import run_records
from demiflow.lance.refs import DatasetRef
root = Path('/yzp/zhaozy/yangzepeng/0905')
run = root / 'demiwtg/preparation/datasets/t2i_dual_keep469_visual_v3_tp2_20260928'
records = run_records(run)
entry = records.get('stage/knowledge_base')
assert entry, '本轮概念汇总尚未提交，不能宣布完成'
ref = DatasetRef.from_dict(entry['dataset_ref'])
rows = ref.open(root).to_table(columns=['concept', 'status']).to_pylist()
source = lance.dataset(str(root / 'demiwtg/benchmark/t2i/fine_screening/datasets/results__high_l3_categories_uniform1000_v1_20260927.lance'), version=1)
expected = source.to_table(columns=['concept'], filter="status = 'screened' AND decision = 'keep'")['concept'].to_pylist()
assert len(rows) == len({r['concept'] for r in rows}) == len(expected) == len(set(expected)) == 469
assert {r['concept'] for r in rows} == set(expected)
assert {r['status'] for r in rows} <= {'no_images', 'no_available_images', 'review_pending', 'no_published_images', 'visual_only'}
print('concept_status:', Counter(r['status'] for r in rows))
print('knowledge_base_ref:', entry['dataset_ref'])
meta = records.get('stage/visual_image_meta')
assert meta, '逐图审核阶段尚未提交'
public = records.get('results/visual')
if meta['dataset_ref']['row_count']:
    assert public, '有逐图结果但没有公共提交，本轮export尚未完成'
    assert public['binding']['stage'] == meta['dataset_ref']
    assert public['binding']['write_mode'] == 'merge'
    assert public['dataset_ref']['relative_uri'] == 'datasets/images.lance'
    target = DatasetRef.from_dict(public['dataset_ref']).open(root)
    print('public_rows_at_committed_version:', target.count_rows())
print('public_result:', public)
PY
```

再从第 3 格的本轮结果汇总交付：

1. 469 个概念全部在结果中；分别统计无图、无可读图、待定/失败、无通过图、有通过图。逐概念通过图数和至少 5 张通过图的概念数完整保留。
2. 分开报告有效业务 pending 与协议无效/传输失败、未完成调用，不把它们混成“审核通过”。流程可以完成并保存 pending；这不等于所有图片判断都已确定，更不是人工金标准。
3. 双模型原始结论/理由与最终决定可在第 3 格逐图核查；通过材料应具备两个模型的有效确认。没有独立复审记录的图片不能冒称双模型通过。
4. 公共提交记录提供准确 `dataset_ref` URI/version 和 `release_id`。公共版本是累积全表，本轮只按 SHA 合并审核所属列，保留其他记录、其他生产者字段及历史版本；它不会变成只有 469 条的表。
5. `visual_image_meta` 同时保存未通过图片的审核记录，公共表写入并不表示每张图都通过。判断本轮通过数用本轮 `knowledge_base.visual_materials`，不混入公共表其他历史审核。
6. 本轮写入者已退出，demiflow 已释放本轮 Qwen/Gemma 服务及资源锁；若资源随后被其他任务取得，按实时归属说明。

第 3 格默认选择全部 469 个概念，含无图占位；`CONCEPT_FILTER=None` 全选，字符串单选，名称列表多选；默认每页 40 行，改 `DETAIL_PAGE` 翻页，`ROWS_PER_PAGE=None` 才一次展示全部。表格内缩略图、完整理由自动换行；不要改回“前 5 个 case”。首次索引较慢，后续翻页复用内核中的固定版本缓存。

向用户报告：实际 run、两模型/TP 配置、各模型新请求/复用/错误数、耗时与实测吞吐、469 概念状态分布、至少 5 张通过图的概念数、公共表固定版本、尚未解决的问题，以及执行副本和日志路径。达到时间点仍在正常推进时继续监控，不因为到早晨就杀任务或宣称完成。

## 7. 接手与运行记录（由 GLM 追加）

### 2026-09-28 GLM 护航实录（v3 执行 + 故障 + v4 复用收尾）

- 接手时间/指令：2026-09-28 12:11 UTC 接手；用户指令"看文档护航，469 条数据处理完写到目标表"；12:18 用户补充三点：GPU 负载吃满、demiflow 平台修改需经其 review、发现的问题记录到平台 TODO 文档（DF-007 已登记）。
- 接手时状态：与 §1 快照一致（visual_inputs@1=469、image_requests@1=1165 已提交；调用日志 0 条；双卡 0 MiB、8000/8001 空闲；writer 未持有）。
- v3 启动：12:14:51Z nbconvert PID 5678（内核 5941），attempt `20260928T121451Z`，日志/副本在 `_demiflow/t2i_dual_keep469_visual_v3_tp2_20260928/`。
- v3 模型阶段：准备复用重验 SHA 约 18 分钟；Qwen 12:32 就绪、12:33 首批、12:49 完成 **1165/1165（0 未答复/0 传输错误；峰值 Running 64 + Waiting 64，双卡 100%、生成吞吐约 2135 tok/s）**；Gemma 12:51 起加载、12:56 首批、13:12 完成 **935/935（0 错误）**，13:15 双卡显存归零、服务释放。GPU 负载在两模型阶段均打满。
- v3 后处理：13:15–15:00 依次提交 image_primary(1165)、image_review_inputs(1165)、image_review_responses(935)、image_relevance(1165)、visual_reviewed(469)、visual_materials(469)、visual_image_meta(4258)、knowledge_base(469)，全部 @1。
- 关键故障一（15:16）：内核在公共 merge 中途死亡（nbconvert DeadKernelError，无 Python 异常）。GLM 诊断：死前 RSS 累积约 114GiB，cgroup `memory.max=256GiB`（memory.events `max` 计数 6,455 万次），属 cgroup 限额杀进程；宿主 2TiB 内存充足、非系统 OOM。与 §8/DF-006 的宽行物化诊断同根。全部审核阶段已安全落盘，仅缺公共 merge；已核对无不确定调用（两模型 0 未答复/0 传输错误）、无未登记半成品表。
- 关键故障二（15:24–15:34）：GLM 按 §5 续跑时恰逢用户并行会话将 review pipeline/notebook 改写为 v4 复用方案（文件 13:53–15:27:40 多次保存）。第一次读到中间态代码报 `AsyncMapOp`（编辑半成品），第二次因 v4 run 的 immutable manifest 记录的是 15:22:46 中间态代码指纹、与 15:27:40 定稿代码永不匹配。处理：等用户会话编辑与 pytest（test_image_scope_reuse 等）结束、文件稳定后，把 v4 空壳（仅 manifest 一条 + metadata 脚手架；零 stage/零调用/零公共提交）归档至 `preparation/images/review/archive/v4_scope3_reset_20260928T1542Z/`（含 RESET_NOTE.md），未动 v3 与公共表。
- v4 启动/完成：15:41:34Z nbconvert PID 3932（attempt `20260928T154134Z`），run `t2i_dual_keep469_visual_v4_scope3_20260928`（用户定稿代码：`max_calls=0` 复用 v3 全部审核结论 + 新增"全库关联概念数≥3 整图排除"规则，零模型调用、不占 GPU）。15:45:34–15:46:04 五张阶段表 30 秒内落盘，峰值 RSS 约 4GiB，15:46 公共 merge 完成，nbconvert 四格全部成功，执行副本 `image_review_debug.executed.20260928T154134Z.ipynb` 已保存。
- 最终验收（§6 脚本按 v4 适配执行，断言全部通过）：knowledge_base@1=469，概念集合与 screening keep 全集一致；状态分布 **review_pending 249 / visual_only 110 / no_images 102 / no_published_images 5 / no_eligible_images 3**；visual_image_meta@1=3118（≥3 关联排除后）；**通过参考图 ≥5 的概念 133 个**；公共提交 **`datasets/images.lance@10`**（累积全表 2,164,671 行，write_mode=merge，绑定 v4 visual_image_meta@1），release_id **`visual_65d089869af0d503e976eaa0`**。
- 资源释放：v3/v4 writer 均已退出、运行锁释放；v4 全程零模型调用未占 GPU；GPU 0/1 于 13:37 起被 Edit 任务（`scene_pool_add_v5_l4_keep_hold_tp2_v1`，另一会话）正常取得并持续持有，护航未触碰。
- 未解决/移交：review_pending 249 个概念为有效业务待定（双模型分歧等），按约定不自动重试、不伪装通过；visual_only(110，有已发布图的状态口径) 与 ≥5 通过图概念(133) 口径不同，下游选样以第 3 格逐概念明细为准；v3 执行副本因内核死亡未保存（业务结果以已落盘阶段表与 v4 副本为准）。

## 8. 运行中后处理性能诊断（2026-09-28 13:37—13:41 UTC）

用户观察到末段内存高，Codex只读核对如下。本次没有修改冻结代码、重启/停止任务或重新调用模型；以下为带时间的诊断快照，接手时刷新阶段状态。

- 本轮PID 5941持有运行锁。Qwen 1165条、Gemma 935条原始响应均已保存；SQLite无未完成请求和transport_error，业务有效性仍需最终审核统计。
- Gemma约13:12释放；`image_relevance@1`已提交1165批，`visual_reviewed`及之后阶段当时尚未提交，公共表没有本轮提交。现在是在把审核结论关联回概念材料，并准备序列化/落表，不是第3格的缩略图渲染。GPU重新忙碌属于另一条Edit任务。
- 本轮进程RSS从约54 GiB增长到约89 GiB，已观察峰值约93 GiB；`/tmp/demiflow-join-pwuz30a4/left`约17 GiB。多个文件读取位置持续前进，说明当时仍在处理，不能只因模型已结束而判断任务死锁。
- `visual_inputs@1`的469行payload实际UTF-8总长26.2912 GiB，Lance压缩文件约2.557 GiB；最大行243.399 MiB、中位数0.118 MiB。大头是少数关联极广的图片记录及其重复来源元数据。
- 抽查“重型猎鹰火箭”：10张图，`cleaned_materials`与`available_images`各复制约115.4 MiB压紧JSON；其中一张图片记录含65,463条来源，record约63.9 MiB。来源为什么关联这么广需另查采集语义，不能仅据元数据断言是占位图或坏图。
- 平台缓存先按256行构块再判断落盘预算，恢复时又整块pickle加载；left join即使右侧较小仍排序左侧完整载荷。业务宽行与这些通用执行路径叠加，造成大量内存和临时I/O。

问题已登记共同根 `DEMIFLOW_PLATFORM_TODO.md` 的 **DF-006**。后续需要业务改为SHA/固定来源引用、去除完整来源的多份内联复制，并改进平台按字节分块/恢复及宽行关联；不能靠增加模型并发解决。继续按当前正式阶段/公共提交验收，保留已完成响应，不为这段慢后处理重跑推理或热改平台。
