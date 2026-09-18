# V5.2 出题 + 验证交接文档（2026-08-26）

> 目标：用 `benchmark/t2i/synthesize_prompt_gen_v5.2.md` 对当前 10 个样本重新出题（3 模型），
> 然后对产出的题目做 schema 校验 + 质量质检，验证 V5.2 新增的「判分链」
> （`visual_features → visual_probes → tier_map`）是否被正确执行。

## 0. 上下文（本窗口已完成的事，不要重复做）

- 出题协议升级到 **V5.2**：`benchmark/t2i/synthesize_prompt_gen_v5.2.md`（在 V5.1 基础上只加判分链，逐条核对过无删改语义）。
  相对 V5 的全部增量见文件头部沿革块（V5.1 三条 + V5.2 判分链一条）。
- 当前样本：`benchmark/t2i/data/samples.jsonl`（10 个样本，0001~0010；样本图在 `data/images/`）。
- 上一批（V5 协议）产物与质检基线：
  - 题库 `data/synth_gen/questions_v5.jsonl`（30 题 = 3 模型 × 10 样本）
  - 质检 `data/synth_gen/audit/questions_v5_audit.jsonl`（机审 99.0 / 语义 88.3 / 28 过 2 拒，
    拒题均 gemini D4 泄漏；三模型语义均分 fable-5 = gpt-5.6-sol 90.9 > gemini-flash 83.0）
    → V5.2 批次的质检结果要与这份基线对比。
- 判分侧已修复（本窗口完成，直接可用）：
  - `eval_score.py` 的 `extract_json` 已改为抗 thinking 示例污染（取含 `knowledge_checks` 的最后合法 JSON），
    后段计分抽成 `finalize_t2i(q, parsed, raw)`；
  - 本地 judge（qwen3.8-27b，vLLM localhost:8000）已按 `--max-model-len 32768` 重启，
    `eval_score.py` 的 `JUDGE_CTX_LEN` 同步 32768。

## 1. 环境检查

```bash
curl -s http://127.0.0.1:4001/v1/models | grep -c galaxy   # modelhub 网关（出题走这里）
curl -s http://localhost:8000/v1/models                      # 本地 judge（质检不用，判分才用）
```

网关不在位则 `bash modelhub/start.sh`。

## 2. 冒烟（先 2 个样本出 2 题，验证新字段落得下来）

```bash
cd /tank/demiwtg
MODELHUB_KEY=EMPTY PYTHONPATH=benchmark \
  python3 benchmark/t2i/eval_synthesize.py \
  --prompt benchmark/t2i/synthesize_prompt_gen_v5.2.md \
  --models openrouter/anthropic/claude-fable-5 \
  --quota L1:1,L2:1 \
  --limit 2
```

⚠️ 多模型/全量跑之前**先备份旧分文件**（eval_synthesize 多模型模式会覆盖同名
`questions_openrouter_*.jsonl`，那是旧版 V5 产物）：

```bash
cd benchmark/t2i/data/synth_gen
mkdir -p archive/2026-08-26_v5旧版分文件 && mv questions_openrouter_*.jsonl archive/2026-08-26_v5旧版分文件/
```

冒烟产出 `questions_openrouter_anthropic_claude-fable-5.jsonl`（2 题）。跑下面的 schema 校验（第 4 节脚本），
两条检查全过再全量。

## 3. 全量出题（3 模型 × 10 样本 = 30 题，约 30~40 分钟）

```bash
cd /tank/demiwtg
MODELHUB_KEY=EMPTY PYTHONPATH=benchmark \
  python3 benchmark/t2i/eval_synthesize.py \
  --prompt benchmark/t2i/synthesize_prompt_gen_v5.2.md \
  --models openrouter/anthropic/claude-fable-5,openrouter/google/gemini-3.7-flash,openrouter/openai/gpt-5.6-sol \
  --quota L1:2,L2:4,L3:4 \
  --limit 10
```

`--quota` 按样本轮替下发难度（V5 配比 L1 20% / L2 40% / L3 40%）。
产物：`questions_openrouter_*.jsonl` 三份（各 10 题）+ `raw/` 原始响应。
出完后改名归档为本批专属文件名（防后续批次覆盖）：

```bash
cd benchmark/t2i/data/synth_gen
for f in questions_openrouter_*.jsonl; do mv "$f" "${f%.jsonl}_v52.jsonl"; done
```

## 4. 验证一：V5.2 schema 机械校验（新字段专用，现写现跑）

eval_audit.py 的机审按 V5 schema 写，**不覆盖判分链新字段**，本批先跑以下校验脚本
（存 `benchmark/t2i/check_v52_schema.py` 或临时跑）：

```python
import json, sys
from pathlib import Path
rows = []
for fp in sorted(Path('benchmark/t2i/data/synth_gen').glob('questions_openrouter_*_v52.jsonl')):
    rows += [json.loads(l) for l in fp.read_text().splitlines() if l.strip()]
KNOWLEDGE_WORDS = ('年代', '世纪', '派别', '流派', '归属', '风格属于', '属于')
bad = []
for q in rows:
    qid = q.get('qid', '?')
    for i, c in enumerate(q.get('implicit_checks') or []):
        vf, vp, tm = c.get('visual_features'), c.get('visual_probes'), c.get('tier_map')
        if not (isinstance(vf, list) and 3 <= len(vf) <= 5): bad.append(f'{qid} c{i}: visual_features 缺失或非 3~5 条')
        if not (isinstance(vp, list) and isinstance(vf, list) and len(vp) == len(vf)): bad.append(f'{qid} c{i}: visual_probes 与 features 非一一对应')
        if not (isinstance(tm, list) and len(tm) >= 3): bad.append(f'{qid} c{i}: tier_map 缺失或少于 3 条规则')
        if isinstance(tm, list) and not any('不可见' in str(r) for r in tm): bad.append(f'{qid} c{i}: tier_map 未覆盖不可见组合')
        for p in (vp or []):
            if any(w in str(p) for w in KNOWLEDGE_WORDS): bad.append(f'{qid} c{i}: 探针疑似含知识词判据: {str(p)[:40]}')
print(f'{len(rows)} 题，问题 {len(bad)} 条:')
print('\n'.join(bad) if bad else '全部通过')
```

判读：`全部通过` 即判分链执行合格；`探针疑似含知识词` 是启发式误报高发条目，逐条人工过一眼。

## 5. 验证二：跑既有质检（基线对比）

质检脚本与语义审 prompt（`audit_prompt_question_quality.md`，七维）按 V5 schema 写，
对 V5.2 题直接可用（新字段不干扰），只是**语义审不评判分链本身**——链质量以第 4 节
机械校验 + 每题人工抽看 1~2 条为准。

```bash
# 三文件合并成单个审源（质检默认读 questions_v5.jsonl，--questions 指定合并文件）
cd benchmark/t2i/data/synth_gen
cat questions_openrouter_*_v52.jsonl > questions_v52.jsonl
cd /tank/demiwtg
python3 benchmark/t2i/eval_audit.py \
  --questions benchmark/t2i/data/synth_gen/questions_v52.jsonl \
  --out benchmark/t2i/data/synth_gen/audit/questions_v52_audit.jsonl
```

语义审走 `galaxy/qwen3.8-max`（默认），30 题约 25~30 分钟，断点续审。
完成后对比基线（V5：机审 99.0 / 语义 88.3 / 28 过 2 拒）：
- 拒题数应 ≤2，且拒因不再是「粗暴泄漏」类（V5.1 的 leak_check 已治）；
- 语义均分不低于 88.3 为持平，重点看 `check_rubric_soundness` 与 `counterexample_resistance`
  两弱项维度是否提升（判分链穷尽映射若被执行，这两维应显著改善）。

## 6. 验收标准（本交接的完成定义）

1. 30/30 出题成功，`status=accepted` ≥27（拒题是合法输出，但比例不应高于 V5 批）；
2. 第 4 节 schema 校验：新字段零结构性缺失，知识词探针误报经人工确认后为零；
3. 质检拒题 ≤2 且无 D4 泄漏红线；
4. 抽 3 题人工看判分链：探针全是纯感知问句、`tier_map` 能接住该题 `acceptable_variants`
   与 `counterexample_test` 里的每个合法画面。

达标后回报：两批质检对比表 + 判分链执行质量结论，供决定是否用 V5.2 题替换题库重跑
Bagel 判分（判分侧 `eval_score.py` 的 judge 模板暂不带探针字段，消费探针是后续迭代）。

## 附：关键文件索引

| 文件 | 角色 |
|---|---|
| `benchmark/t2i/synthesize_prompt_gen_v5.2.md` | 出题协议（本批） |
| `benchmark/t2i/eval_synthesize.py` | 出题脚本（`--prompt`/`--models`/`--quota`/`--limit`） |
| `benchmark/t2i/eval_audit.py` | 题目质检（机审 + 语义审，断点续审） |
| `benchmark/t2i/audit_prompt_question_quality.md` | 语义审七维 prompt |
| `benchmark/t2i/question_dev.ipynb` | 题库审阅（第三格看质检结果） |
| `benchmark/t2i/data/handover_bagel_v5_eval.md` | 上一窗口交接（生成+判分侧） |
