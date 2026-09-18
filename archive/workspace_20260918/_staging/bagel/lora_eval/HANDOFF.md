# Bagel LoRA 理解评测交接文档（新窗口开工用）

> 生成时间：2026-08-09。任务：跑完 LoRA 理解评测轮，与已完成的基座结果出对比表。
> 同时另一个窗口在做 Open-BROADER 数据工程（纯 CPU 阶段），GPU 归本任务用；
> 若对方进入 Pilot 训练（预计 1~2 周后）需要 GPU，届时协商让卡。

## 1. 背景一句话

Bagel（BAGEL-7B-MoT，14.61B 多模态）做了 **2 样本过拟合 LoRA** 验证链路。
基座理解评测已跑完（20 数据集，17 项出分，全部与官方吻合）。
现在跑 **LoRA 轮**（同 20 个数据集），目的：**验证 LoRA 链路无回归**——
预期分数与基座几乎一致（过拟合 LoRA 不应改变通用能力）。

## 1.1 暂停记录（2026-08-10 01:08）

应 Open-BROADER Pilot 训练让卡，评测已主动暂停：
- 停止时进度：第 1 个数据集 MMBench_DEV_EN_V11，3297/4876（~68%），其余 19 个未开始
- 残留：`/root/data/projects/bagel/eval_outputs/lora/BAGEL-7B-MoT/T20260809-165108/01_MMBench_DEV_EN_V11.pkl`（**不完整**）
- **恢复方法**：按 2.1 先 `rm -rf /root/data/projects/bagel/eval_outputs/lora/*`（不完整 pkl 勿续用），再按 2.2 重启，即整轮重跑
- GPU 已确认完全释放（两卡 1 MiB）

## 2. 启动步骤（按顺序执行）

### 2.1 清理上次中断的残件（必须，否则可能从脏缓存续跑）
```bash
rm -rf /root/data/projects/bagel/eval_outputs/lora/*
```

### 2.2 启动评测套件
```bash
cd /root/data/projects/bagel/VLMEvalKit && \
LORA_PATH=/root/data/projects/bagel/results/lora_overfit/checkpoints/final/lora.safetensors \
WORK_DIR=/root/data/projects/bagel/eval_outputs/lora \
nohup bash /root/data/projects/bagel/Bagel/scripts/run_eval_suite.sh > /root/data/eval_lora_suite.log 2>&1 &
```

### 2.3 启动后 5 分钟验证
```bash
grep -E "tag=|START" /root/data/eval_lora_suite.log | tail -3   # 应见 tag=lora datasets=20
nvidia-smi --query-gpu=index,memory.used --format=csv,noheader  # 两卡应逐渐占满（~20G/~10G）
```

## 3. 套件机制说明

- 脚本 `/root/data/projects/bagel/Bagel/scripts/run_eval_suite.sh`：串行跑 20 个数据集，每个数据集一个日志
  （`/root/data/projects/bagel/eval_outputs/lora/logs/<数据集>.log`），单个失败不中断（END rc=X 记录后继续）
- 环境变量已固化在脚本里：`LMUData=/root/data/projects/bagel/LMUData`、`HF_HOME=/root/data/projects/bagel/hf_home`（大缓存都在数据盘，勿改）
- `LORA_PATH` 存在时 TAG=lora，LoRA 经 `apply_lora(r=32, alpha=32)` 注入（理解链路权重 78M，勿用 t2i 那个）
- **数据缓存全部命中基座轮的下载**（LMUData 5G + hf_home 41G），本轮零下载，比基座轮快
- 预计总耗时 **12~16 小时**（MME-RealWorld EN/CN 两项占 ~5h，大图慢）

## 4. 已知问题（正常现象，不要当成故障）

1. **MathVista_MINI / MathVision_MINI / WeMath** 推理会成功但判分失败：官方口径需 OpenAI API judge，环境没有。
   日志出现 "requires a working OPENAI API" 属预期。收尾后可选做本地规则近似判分（MathVista 是选择/数值题，可从 xlsx 抽取判分）
2. 基座轮 MME-RealWorld 曾两次因 HF CDN 中断失败，**本次数据已在缓存**，不应再现；若再现，参考
   `/root/data/projects/bagel/gen_eval/scripts/dl_mme_rw.py`（带 100 次重试的 snapshot_download）
3. 磁盘注意：`/root/data` 剩 ~33G（另一窗口要扩盘下 Wikidata），本轮评测新增 <5G，不影响

## 5. 监控姿势

```bash
grep -E "START|END|ALL DONE" /root/data/eval_lora_suite.log | tail -10   # 主进度
LOG=$(ls -t /root/data/projects/bagel/eval_outputs/lora/logs/*.log | head -1); tail -c 400 "$LOG" | tr '\r' '\n' | grep -aoE "[0-9]+/[0-9]+ \[[^]]*\]" | tail -1   # 当前数据集进度
```

## 6. 收尾：出对比表

全部 ALL DONE 后，分数文件在 `/root/data/projects/bagel/eval_outputs/lora/BAGEL-7B-MoT/`：
- 选择题类：`BAGEL-7B-MoT_<数据集>_acc.csv`（第 2 行第 2 列是主分数）
- MME：`*_MME_score.csv`；OCRBench：`*_OCRBench_score.json`（Final Score）
- MME-RealWorld：`*_MME-RealWorld*_rating.json`（Overall 字段 ×100）

**基座参考分（对比基准）**：

| 数据集 | 基座分 | 数据集 | 基座分 |
|---|---|---|---|
| MMBench_EN | 76.0% | AI2D_TEST | 89.3% |
| MMBench_CN | 73.8% | AI2D_TEST_NO_MASK | 94.8% |
| MME | 2349.9 | DocVQA_VAL | 94.0 |
| SEEDBench_IMG | 71.1% | ChartQA_TEST | 84.0（avg） |
| SEEDBench2_Plus | 69.5% | InfoVQA_VAL | 59.7 |
| CV-Bench-2D | 78.5% | CountBenchQA | 93.6 |
| CV-Bench-3D | 43.3% | OCRBench | 809 |
| RealWorldQA | 71.5% | MME-RW_EN | 55.1 |
| — | — | MME-RW_CN | 47.0 |

**判定标准**：各项差值应在 ±1pt 内（2 样本过拟合 LoRA 无通用能力变化）；
若某项大幅下跌（>3pt），检查该数据集日志有无推理异常（截断/报错率）。

## 7. 可选后续：生成评测（LoRA 轮结束后，用户未明确要，先请示）

生成评测流水线已就绪（DPG-Bench 1065 + Qwen-Image-Bench 1000，CLIPScore + 官方 mPLUG 评分）：
```bash
LIMIT=8 bash /root/data/projects/bagel/gen_eval/scripts/run_gen_eval.sh base dpg   # 冒烟
bash /root/data/projects/bagel/gen_eval/scripts/run_gen_eval.sh base dpg           # 全量（lora 版传 lora，自动注入 t2i LoRA）
```
注意生成评测用的是 **t2i LoRA**（`/root/data/projects/bagel/results/lora_overfit_t2i/checkpoints/final/lora.safetensors`），
与理解轮的 LoRA 不是同一个。base×DPG 生图约 4~6h，mPLUG 打分约 30min。

## 8. 环境备忘

- conda base 环境直接可用；VLMEvalKit 装在 `/root/data/projects/bagel/VLMEvalKit`（源码安装，config.py 已注册 Bagel）
- 模型权重：`/root/data/models`（魔搭下载的 BAGEL-7B-MoT ema.safetensors）
- 基座轮完整结果：`/root/data/projects/bagel/eval_outputs/base/BAGEL-7B-MoT/`
- 经验：Write 工具偶发不落盘需 grep 验证；大文件删除用终端命令
