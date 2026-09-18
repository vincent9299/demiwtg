# eval_probe.py 50 实例 Pilot 交接（2026-08-28）

> **目的**：在千题批次正式跑之前，用 50 实例 pilot 量出探针真实通过率，回填超采倍数，验证原图一致性预检的已知答案（咯吱盒/醒狮）。

## 1. 状态

- `eval_probe.py` 已写好并通过语法检查（`benchmark/t2i/eval_probe.py`）。
- 样本清单：`benchmark/t2i/data/samples_20260828_v2.jsonl`（1000 实例，eval_sample.py 产物）。
- 图片目录：`benchmark/t2i/data/images_20260828_v2/`（1000 张）。
- Pilot 取前 50 实例（`--limit 50`）。

## 2. 环境依赖

- **modelhub 网关** 必须在跑：`http://127.0.0.1:4001`（modelhub 子项目 `modelhub/start.sh`）。
- Python 环境：主仓 `.venv/`（与 eval_synthesize 同）。
- 无外部 key 需求：网关 key = `EMPTY`。

验证网关活着：
```bash
curl -s http://127.0.0.1:4001/v1/models | python3 -c "import sys,json; d=json.load(sys.stdin); print(len(d.get('data',[])),'models')"
```

### 模型阵容（provider 优先级：qwen/glm 自家 > galaxy > openrouter）

| 角色 | 模型 ID | Provider |
|---|---|---|
| 生成 #1 | `glm/glm-5.2` | GLM 直连 |
| 生成 #2 | `glm/glm-5.3-flash` | GLM 直连 |
| 生成 #3 | `qianwen2/qwen3.8-flash` | 百炼（qwen 自家） |
| 生成 #4 | `galaxy/deepseek-v4-flash-0731` | Galaxy |
| 生成 #5 | `galaxy/minimax-m3` | Galaxy（唯一源） |
| 合并 | `openrouter/openai/gpt-5.6-sol` | OpenRouter |

> 模型选择原则：qwen 系列优先用自家百炼（qianwen1/2），glm 系列优先用 GLM 直连，
> 其余优先用 Galaxy，最后才走 OpenRouter。

## 3. 执行步骤

### 3.1 第一步：五路生成 + sol 合并

```bash
cd /tank/demiwtg

python3 benchmark/t2i/eval_probe.py generate \
    --samples benchmark/t2i/data/samples_20260828_v2.jsonl \
    --limit 50 \
    --workers 4 \
    --out-dir benchmark/t2i/data/synth_gen_20260828_v2
```

产物：
- `benchmark/t2i/data/synth_gen_20260828_v2/constraints.jsonl` — 合并约束清单
- `benchmark/t2i/data/synth_gen_20260828_v2/raw/` — 每实例每模型原始响应（断点续跑用）

预计耗时：50 实例 × 5 路生成 + 50 合并 ≈ 30~60 分钟（取决于 429 限流）。

**断点续跑**：中断后重跑同一命令即可，已完成的实例自动跳过。

### 3.2 第二步：原图一致性预检

```bash
python3 benchmark/t2i/eval_probe.py precheck \
    --samples benchmark/t2i/data/samples_20260828_v2.jsonl \
    --constraints benchmark/t2i/data/synth_gen_20260828_v2/constraints.jsonl \
    --workers 2
```

产物：
- `constraints.jsonl` 中被预检剔除的约束标记到 `_precheck_removed` 字段
- 约束 id 重排（剔除后连续）

## 4. Pilot 验收指标

跑完后统计（可在 IPython 里快速看）：

```python
import json
from pathlib import Path

data = [json.loads(l) for l in open('benchmark/t2i/data/synth_gen_20260828_v2/constraints.jsonl')]
n_total = len(data)
n_pass = sum(1 for r in data if r.get('_threshold_pass'))
n_fail = n_total - n_pass
print(f"总数: {n_total}, 通过阈值: {n_pass} ({n_pass/n_total*100:.1f}%), 未通过: {n_fail}")

# 每实例约束数分布
n_cons = [len(r.get('constraints',[])) for r in data]
print(f"约束数: min={min(n_cons)}, 中位数={sorted(n_cons)[len(n_cons)//2]}, max={max(n_cons)}")

# 负向占比
all_c = [c for r in data for c in r.get('constraints',[])]
n_neg = sum(1 for c in all_c if c['polarity']=='must_not_have')
print(f"负向占比: {n_neg}/{len(all_c)} ({n_neg/len(all_c)*100:.1f}%)")

# 变体率
n_var = sum(1 for c in all_c if c.get('variants'))
print(f"变体率: {n_var}/{len(all_c)} ({n_var/len(all_c)*100:.1f}%)")

# 预检剔除率
n_pre_removed = sum(len(r.get('_precheck_removed',[])) for r in data)
print(f"预检剔除: {n_pre_removed} 条")
```

关键裁定项：
1. **阈值通过率**：预期 70~90%。<50% 说明门太严或生成阵容有问题；>95% 说明阈值太松。
2. **每实例约束数中位数**：预期 5~8 条。<3 说明探针产出不足一题所需。
3. **负向占比**：预期 15~30%。负向驱动判分侧机械封顶，太少则封顶机制失灵。
4. **变体率**：预期 10~25%。变体量是探针质量的核心指标。
5. **预检剔除率**：预期 5~15%。太高说明生成阵容常产出图特定约束；0% 说明预检形同虚设。

## 5. 已知答案验证

预检跑完后，拿 `constraints_v53_trial.jsonl` 5 个已知样本验证：

| 实例 | 预期行为 |
|---|---|
| 咯吱盒（F4/F6：内里黄绿色等图特定外观） | 预检应**剔除** |
| 醒狮（风向等通用属性） | 预检应**保留** |

如果结果与预期相反 → 预检 prompt 或 gemini 路由有问题，需在千题批次前修。

## 6. 超采倍数回填

根据 pilot 通过率反推千题批次需要超采多少：

```python
target_final = 500          # 目标终题数
probe_pass_rate = n_pass / n_total
synthesize_pass = 0.95      # 出题预期通过率
audit_pass = 0.85           # 机审+预检预期通过率
needed_oversample = target_final / (probe_pass_rate * synthesize_pass * audit_pass)
print(f"需超采: {needed_oversample:.0f} 实例")
```

如果算出来 >>1000，需要：
- 调低阈值（降生成质量换数量），或
- 拉高出题/机审通过率，或
- 扩样本池（放宽质量门到 8.5，但要用户拍板）。

## 7. 已知风险

- **glm-5.3-flash 16k 截断**：已用 32768，但 reasoning 模型仍可能 burn 全预算 → `reasoning_content` 回退已处理（content=null 时取 reasoning_content）。
- **minimax-m3 解析不稳定**：单模型解析失败不阻塞合并（只记 `_gen_models[].parse_ok=false`），合并时只用成功路。
- **429 限流**：退避 30/60/120s × 5 次重试，通常能自愈。
- **modelhub 网关不在**：脚本会报 HTTP 调用失败。先 `bash modelhub/start.sh`。

## 8. 完成后

把 pilot 统计（§4 的 5 项指标 + §5 已知答案验证 + §6 超采倍数）写回主交接文档 §6.15，用户裁定后进入千题批次正式跑。
