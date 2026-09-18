# Qwen-Image-Bench

<p align="center">
  <a href="http://arxiv.org/abs/2605.28091"><img src="https://img.shields.io/badge/Paper-arXiv-b31b1b?logo=arxiv" alt="Paper"></a>
  <a href="https://huggingface.co/datasets/Qwen/Qwen-Image-Bench"><img src="https://img.shields.io/badge/Dataset-HuggingFace-ffd21e?logo=huggingface" alt="Dataset"></a>
  <a href="https://huggingface.co/Qwen/Qwen-Image-Bench"><img src="https://img.shields.io/badge/Judge_Model-HuggingFace-ffd21e?logo=huggingface" alt="Model"></a>
  <a href="https://www.modelscope.cn/datasets/Qwen/Qwen-Image-Bench"><img src="https://img.shields.io/badge/Dataset-ModelScope-624aff?logo=data:image/svg+xml;base64,PHN2ZyB3aWR0aD0iMjIyIiBoZWlnaHQ9IjIyMiIgeG1sbnM9Imh0dHA6Ly93d3cudzMub3JnLzIwMDAvc3ZnIj48cGF0aCBkPSJNNzEuNTU2IDcyLjg4OGgzOC42Njd2MzguNjY3SDcxLjU1NnpNMTExLjU1NiAxMTIuODg4aDM4LjY2N3YzOC42NjdoLTM4LjY2N3pNNzEuNTU2IDExMi44ODhoMzguNjY3djM4LjY2N0g3MS41NTZ6IiBmaWxsPSIjNjI0QUZGIi8+PC9zdmc+" alt="ModelScope"></a>
  <a href="https://www.apache.org/licenses/LICENSE-2.0"><img src="https://img.shields.io/badge/License-Apache_2.0-blue" alt="License"></a>
</p>

An evaluation toolkit for text-to-image (T2I) generation models. It uses a fine-tuned **Q-Judger** (Qwen3.6-27B) to score generated images across **5 hierarchical dimensions** (Quality, Aesthetics, Alignment, Real-world Fidelity, Creative Generation) covering **56 fine-grained facets**.

<p align="center">
  <img src="assets/show_case.png" alt="Qwen-Image-Bench dimension framework and representative model outputs">
</p>

## Key Features

- **Evaluate any T2I model** — run the judge model on your own generated images and get structured, multi-dimensional scores
- **Compute scores from pre-generated responses** — reproduce the leaderboard from the released benchmark dataset
- **Powered by ms-swift** — uses the same inference setup that produced the benchmark responses

## Quick Start

```bash
# 1. Clone the repo
git clone https://github.com/QwenLM/Qwen-Image-Bench.git
cd Qwen-Image-Bench

# 2. Install dependencies
uv venv myenv --python 3.11 && source myenv/bin/activate
# Install PyTorch first: https://pytorch.org/get-started/locally/
uv pip install -r requirements.txt

# 3. Run judge on your images
python judge.py \
  --input your_data.jsonl \
  --model Qwen/Qwen-Image-Bench
```

Your input file should be a CSV/JSON/JSONL with three columns:

| Column | Type | Description |
|--------|------|-------------|
| `ID` | int | Prompt identifier (1–1000), must match [benchmark metadata](metadata/bench_metadata.json) |
| `prompt` | str | The text prompt used to generate the image |
| `image_path` | str | Path to the generated image file |


## Installation


### Step-by-step

**1. Create and activate a virtual environment:**

```bash
uv venv myenv --python 3.11
source myenv/bin/activate
```

**2. Install PyTorch** (select the command matching your CUDA version):

See the official guide: [https://pytorch.org/get-started/locally/](https://pytorch.org/get-started/locally/)

**3. Install Python dependencies:**

```bash
uv pip install -r requirements.txt
```

This installs all required dependencies including ms-swift.


## Usage

### Evaluate Your Own T2I Model (`judge.py`)

#### Run Judge Inference

```bash
python judge.py \
  --input your_data.jsonl \
  --model Qwen/Qwen-Image-Bench
```

#### CLI Options

| Argument | Default | Description |
|----------|---------|-------------|
| `--input` | *(required)* | Input CSV/JSON/JSONL with `ID`, `prompt`, `image_path` |
| `--model` | *(required)* | HuggingFace model ID or local path |
| `--hf-bench-repo` | — | HF dataset repo for bench metadata |
| `--local-metadata` | — | Local metadata file path (overrides default) |
| `--max-batch-size` | 24 | ms-swift `PtEngine` max_batch_size |
| `--max-new-tokens` | 4096 | Max generation tokens |

#### Output Files

After running `judge.py`, three files are written next to your input:

| File | Contents |
|------|----------|
| `<input>_judged.{jsonl,csv}` | Per-row results: original fields + `judge_model_output` (combined raw scores JSON) + `<dim>_judge_output` (raw judge text per L1 dimension) |
| `<input>_bench_scores.json` | Aggregated scores: `level1`, `level2`, `total` |
| `<input>_bench_scores.xlsx` | Same scores in Excel: `Level-1 Summary` sheet + one sheet per L1 dimension with L2 detail |

### Compute Scores from Pre-generated Responses (`compute_scores.py`)


```bash
# From local file
python compute_scores.py --input qwen_image_bench_hf_v0518.jsonl

# Or download from HuggingFace
python compute_scores.py --hf-repo Qwen/Qwen-Image-Bench
```

Output: `scores_result.xlsx` + `scores_detail.json`


## Top-5 Models

| Model | Quality | Aesthetics | Alignment | Real-world Fidelity | Creative Generation | **Overall** |
|-------|:-------:|:----------:|:---------:|:-------------------:|:-------------------:|:-----------:|
| GPT Image 2 | **58.65** | **67.53** | **65.85** | **57.38** | **75.23** | **64.69** |
| Nano Banana 2.0 | 54.77 | 61.08 | 62.40 | 54.28 | 67.05 | 59.82 |
| GPT Image 1.5 | 55.14 | 60.88 | 61.72 | 53.95 | 66.35 | 59.65 |
| Nano Banana Pro | 55.67 | 60.26 | 61.25 | 54.07 | 66.23 | 59.45 |
| Qwen Image 2.0 Pro | 54.39 | 58.67 | 59.28 | 51.83 | 64.94 | 57.84 |

Full results for all 18 models are available in the paper.


## Inference Parameters

The judge model uses fixed inference parameters for reproducibility:

| Parameter | Value |
|-----------|-------|
| `seed` | 42 |
| `temperature` | 0 |
| `top_k` | 1 |
| `top_p` | 1.0 |
| `repetition_penalty` | 1.05 |
| `max_new_tokens` | 4096 |
| `enable_thinking` | True |
| `max_batch_size` | 24 |


## Project Structure

```
.
├── judge.py                 # Run judge model inference on new images
├── compute_scores.py        # Compute scores from pre-generated responses
├── score_utils.py           # Score extraction, mapping, correction, aggregation
├── checklists.py            # Evaluation prompts and dimension definitions
├── backends/
│   └── ms_swift_backend.py  # ms-swift inference engine
├── metadata/
│   └── bench_metadata.json  # ID → dims_en metadata for judge inference
├── requirements.txt
└── assets/                  # Figures for documentation
```


## Evaluation Framework

The benchmark uses a **3-level hierarchical scoring system** with 5 L1 dimensions, 23 L2 sub-capabilities, and 56 L3 facets:

| L1 Dimension | L2 Sub-capabilities |
|--------------|---------------------|
| **Quality** | Realism, Detail, Resolution |
| **Aesthetics** | Composition, Color Harmony, Lighting, Anatomical Portraiture, Emotional Expression, Style Control |
| **Alignment** | Attributes, Actions, Layout, Relations, Scene |
| **Real-world Fidelity** | Fairness, Safety & Compliance, World Knowledge |
| **Creative Generation** | Imagination, Feature Matching, Logical Resolution, Text Rendering, Design Applications, Visual Storytelling |

**Scoring**: Each L3 facet is rated 0 (Fail → 0), 1 (Pass → 60), or 2 (Excel → 100), with N/A excluded. Scores aggregate bottom-up: L3 → L2 → L1 → Overall.

For the complete dimension hierarchy and detailed analysis, see the [benchmark dataset card](https://huggingface.co/datasets/Qwen/Qwen-Image-Bench).


## Citation

If you find this benchmark useful, please cite our paper:

```bibtex
@misc{li2026qwenimagebenchgenerationcreationtexttoimage,
      title={Qwen-Image-Bench: From Generation to Creation in Text-to-Image Evaluation}, 
      author={Niantong Li and Guangzheng Hu and Weixu Qiao and Ying Ba and Qichen Hong and Shijun Shen and Jinlin Wang and Fan Zhou and Jianye Kang and Xin Shang and Ziyi He and Wei Wang and Dalin Li and Jiahao Li and Jie Zhang and Kaiyuan Gao and Kun Yan and Lihan Jiang and Ningyuan Tang and Shengming Yin and Tianhe Wu and Xiao Xu and Xiaoyue Chen and Yuxiang Chen and Yan Shu and Yanran Zhang and Yilei Chen and Yixian Xu and Zekai Zhang and Zhendong Wang and Zihao Liu and Zikai Zhou and Hongzhu Shi and Yi Wang and Bing Zhao and Hu Wei and Lin Qu and Chenfei Wu},
      year={2026},
      eprint={2605.28091},
      archivePrefix={arXiv},
      primaryClass={cs.CV},
      url={https://arxiv.org/abs/2605.28091}, 
}
```

## License

This project is licensed under the [Apache License 2.0](https://www.apache.org/licenses/LICENSE-2.0).
