# benchmark/bagel/ · BAGEL-7B-MoT 评测场景

与 `benchmark/{t2i,edit,vlm}/` 平行的**第 4 个场景**：以 **BAGEL-7B-MoT 为被测模型**的标准基准评测（生成类 + 理解类）。

> **语义界定**：`t2i/edit/vlm` 是项目自建的“基准题库构造 + LLM 裁判”流水线；`bagel/` 是“跑官方/自建基准评测被测模型并汇总”。四者平行、互不重叠。

## 整合方式（2026-09-05 已执行，物理移动）

- **代码 + 数据全部物理移动**进本目录（脚本含规范重命名），无软链迷宫
- 唯一保留的软链：`data/models → bagel/models → Bagel/models`（28G 底座权重，与模型包同体，不拷贝）
- 运行缓存（`modelscope` / `hf_home` / `LMUData`）统一指向 `data/` 下，避免再污染旧目录
- 已删除非必要项：`fa_build/`（旧机 cp310 flash_attn 构建产物 925M，py311 wheel 已装入 env-bagel）、`logs/`、`gen_eval/` 残余与下载日志、ELLA 仓除 `dpg_bench` 外的部分（公开仓可再克隆）

**旧目录现状（已极简）**：`bagel/` 下仅剩 `Bagel/`（官方模型包 modeling/ 等 + `models/BAGEL-7B-MoT` 权重，是 `eval_gen.py`/`eval_vlm.py` 的 sys.path 目标，必要）和 `models → Bagel/models` 兼容软链。

## 目录布局

```
benchmark/bagel/
├── results_review.ipynb          # 结果总览 notebook（已执行）：DPG/QIB/QIB-CN + VLM 20项 + 覆盖率/异常 + 整合记录
├── README.md
├── gen/                          # 生成类（DPG-Bench / Qwen-Image-Bench）
│   ├── eval_gen.py               # 多卡分片生成驱动（原 generate_t2i.py）
│   ├── eval_score_dpg.py         # DPG 官方 mPLUG-VQA 打分（需 modelscope；sibling 依赖 _fairseq_stub.py）
│   ├── eval_score_clip.py        # CLIPScore 规则打分（需 clip）
│   ├── eval_score_qib.py         # QIB-CN LLM 裁判榜单
│   ├── _fairseq_stub.py
│   ├── qib_official/             # QIB 官方评分库（vendored git 仓）
│   ├── run_gen_eval.sh           # 编排: base|lora × dpg|qib|all
│   ├── run_qib_cn_pipeline.sh    # QIB-CN 四阶段流水线（生成→judge输入→Q-Judger→榜单）
│   └── prep_{dl_qib,dl_mme_rw,extract_qib_prompts}.py
├── vlm/                          # 理解类（VLMEvalKit 20 项套件）
│   ├── eval_vlm.py               # 单数据集驱动（原 run_bagel_eval.py；支持 LORA_PATH 注入）
│   ├── run_eval_suite.sh         # 20 数据集编排（DATASETS_OVERRIDE 可子集）
│   ├── run_official_mmbench.sh   # MMBench 官方口径
│   └── VLMEvalKit/               # 框架（vendored git 仓）
└── data/                         # 真实数据（物理移入）
    ├── images/                   # base_dpg / base_qib / base_qib_cn 生成结果（977M）
    ├── prompts/                  # QIB 题库 prompts（178M）
    ├── dpg_bench/                # DPG 题库 + 官方计分（源自 ELLA 仓）
    ├── outputs/                  # VLM 评测结果 base/lora/official_cfg（37M）
    ├── {modelscope,hf_home,LMUData}/   # 运行缓存（按需生成）
    └── models -> ../../bagel/models    # → Bagel/models/BAGEL-7B-MoT（28G）
```

## 环境与运行

统一用 **`env-bagel`**（torch 2.5.1+cu124, flash_attn 2.7.4, py3.11）：

```bash
source /yzp/zhaozy/yangzepeng/0905/activate-bagel.sh    # 别名 myenv-bagel

# 生成类冒烟（LIMIT=32 单卡）
cd gen && LIMIT=32 GPUS="0" bash run_gen_eval.sh base dpg

# 理解类单数据集
DATASETS_OVERRIDE="MMBench_DEV_EN_V11" bash ../vlm/run_eval_suite.sh
```

## 待办 / 已知缺口

- **运行时依赖未装**：`clip`、`modelscope`、VLMEvalKit 依赖——路径/结构已验通，实跑前需装进 env-bagel
- **评测缺口**：VLM 套件 20 项实跑 18（缺 `MathVista_MINI`、`MathVision_MINI`）；`WeMath` strict≈0 疑似抽取问题；`lora`/`official_cfg` 变体大多未落盘
- **LoRA 线**：历史自建 LoRA/wkbench 产物已删（含 `train/{lora_pretrain_navit,flash_attn_adapt}.py`、`overfit_*.yaml`、`app_with_lora.py`，`Bagel/` 现与官方仓完全一致）；`eval_gen.py`/`eval_vlm.py` 的 LoRA 分支为惰性导入，底座评测不受影响；`run_gen_eval.sh lora` 的 `T2I_LORA` 待重训后更新
- 环境/权重体检：用 `LIMIT=2 GPUS="0" bash gen/run_gen_eval.sh base dpg` 冒烟即可（覆盖权重加载+题库+出图全链路）

详见 `results_review.ipynb`。
