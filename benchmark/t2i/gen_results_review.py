#!/usr/bin/env python3
"""生成 reviews/results_review.ipynb（t2i bench200 四模型正式呈现，纯只读展示）。

正式呈现只保留三段（与 edit 侧 reviews/results_review.ipynb 平行）：
① 整体得分对比：三线（对齐/质量/美感）× 四模型榜单 + 综合分分段分布；
② 关键维度对比：三维 × 22 明细项 φ 均分 × 四模型（表格 + 图表；图表轴标用英文——
   运行环境无中文字体，中文对照走表格）；
③ 抽样看 case：sample_cases 对照 edit 侧 review_bench200_three.sample_cases 口径实现，
   参数按 t2i 适配（edit_type→level；配对总分=三线等权综合分）；每题展示 题源图 +
   四模型生成图（原始分辨率，独立显示、不缩放重采样、不依赖控件）+ 逐项打分矩阵，
   判官逐项完整理由用 show_reasons 按需查看。样例格可独立重跑（重跑即重新加载数据）。

数据：bench200/ 自闭环终版评测集（questions.jsonl 200 题 + samples/ 题源图 +
四模型出图 responses*.jsonl/imgs + scores/ V2 判分 jsonl）。判官 gpt-5.6-sol。
scores/*.report.json 为判分中途的过时快照，一律不读，以 jsonl 为准（见 bench200/README.md）。

用法：
    python3 benchmark/t2i/gen_results_review.py             # 覆写 reviews/results_review.ipynb（空输出）
    python3 benchmark/t2i/gen_results_review.py --execute   # 生成后以 demiwtg 内核执行并回写输出

历史十题消融（判官×V2/V5×图源）审阅册已归档：archive/notebooks_retired/results_review_v60.ipynb。
"""
import argparse
import json
from pathlib import Path

OUT_NB = Path(__file__).resolve().parent / "reviews" / "results_review.ipynb"

MD_INTRO = '''# t2i bench200 · 四模型正式判分

gemini-3.1-flash-image / Qwen-Image-2512 / Z-Image-Turbo / bagel（BAGEL-7B-MoT 底座）；判官为 **gpt-5.6-sol**，口径 **v6.0-V2**（φ 映射 0→0 / 1→60 / 2→100，N/A 剔除）。
数据为 `bench200/` 自闭环终版评测集（v6.0 出题协议 200 题 + samples/ 题源图 + 四模型出图），四模型判分各 200/200 完整；`scores/*.report.json` 为判分中途的过时快照，一律以 jsonl 为准。
本册为正式呈现，只保留：**整体得分对比 · 关键维度分模型对比 · 抽样看 case** 三节。Alignment 全线偏低是题库设计意图（知识密度 gating 拉开模型差距），非判分异常；图表轴标用英文（运行环境无中文字体），中文对照见各表格。
历史十题消融（判官×V2/V5×图源）分析已归档：`../archive/notebooks_retired/results_review_v60.ipynb`。'''

MD_OVERALL = '''## 整体得分对比
四模型判分完整性核对（各 200/200）与三线榜单：对齐 / 质量 / 美感（φ 聚合 0–100），按三线等权综合分排序；综合分仅为排序辅助，非官方口径。另附逐题综合分的分段分布。'''

# 加载器：整体格与抽样格共用（抽样格整段重复，保证可独立运行——edit 侧同款模式）
LOADER = '''# ---- bench200 数据加载（v6.0-V2 · 判官 gpt-5.6-sol · 四模型）----
import json
from pathlib import Path

import pandas as pd

_REPO_T2I = next(p for p in [Path.cwd(), *Path.cwd().parents] if (p / 'benchmark/t2i/bench200').is_dir())
BENCH = _REPO_T2I / 'benchmark/t2i/bench200'
PHI = {0: 0.0, 1: 60.0, 2: 100.0}   # 0→0 / 1→60 / 2→100，N/A 剔除
RK = {'alignment': ('alignment_scores', 'alignment_reasons'),
      'quality': ('quality_scores', 'quality_reasons'),
      'aesthetics': ('aesthetic_scores', 'aesthetic_reasons')}
DIM_ZH = {'alignment': '对齐', 'quality': '质量', 'aesthetics': '美感'}
ITEM_ZH = {'subject_presence': '主体在场', 'form_structure': '形态结构',
           'color_material': '颜色材质', 'quantity_scale': '数量尺度',
           'spatial_relation': '空间关系', 'text_symbol': '文字符号',
           'action_interaction': '动作交互', 'state_context': '状态情境',
           'scene_environment': '场景环境', 'style': '风格',
           'physical_logic': '物理逻辑', 'material_texture': '材质质感',
           'detail_richness': '细节丰富', 'artifacts': '伪影', 'resolution': '分辨率',
           'edge_clarity': '边缘清晰', 'naturalness': '自然度',
           'anatomical_fidelity': '解剖保真', 'composition': '构图',
           'color_harmony': '色彩和谐', 'lighting_atmosphere': '光影氛围',
           'emotional_expression': '情绪传达'}
AXES = ['subject_presence', 'form_structure', 'color_material', 'quantity_scale',
        'spatial_relation', 'text_symbol', 'action_interaction', 'state_context',
        'scene_environment', 'style']
QUAL_ITEMS = ['physical_logic', 'material_texture', 'detail_richness', 'artifacts',
              'resolution', 'edge_clarity', 'naturalness', 'anatomical_fidelity']
AES_ITEMS = ['composition', 'color_harmony', 'lighting_atmosphere', 'emotional_expression']
ALL_ITEMS = [(d, k) for d, keys in (('alignment', AXES), ('quality', QUAL_ITEMS),
                                    ('aesthetics', AES_ITEMS)) for k in keys]


def _load_jsonl(p):
    with open(p, encoding='utf-8') as f:
        return [json.loads(l) for l in f if l.strip()]


QS = {q['qid']: q for q in _load_jsonl(BENCH / 'questions.jsonl')}

# 判分 jsonl 为准；scores/*.report.json 是判分中途的过时快照，不读
ROWS, FAILS = [], []
for fp in sorted((BENCH / 'scores').glob('scores_v60_V2_gpt-5.6-sol_*.jsonl')):
    for r in _load_jsonl(fp):
        (FAILS if r.get('fail') else ROWS).append(r)
DF = pd.DataFrame(ROWS)
SCORES = {(r['image_model'], str(r['qid'])): r for r in ROWS}

# 模型出图索引：responses*.jsonl 自带 qid→图片相对路径（文件名含模型名；
# bagel 的 shard 命名回退目录名；imgs/ 下允许嵌套一层模型名子目录）
IMG = {}
for mdir in sorted(BENCH.iterdir()):
    if not mdir.is_dir() or mdir.name.startswith(('_', '.')):
        continue
    if mdir.name in ('scores', 'samples', 'provenance'):
        continue
    name, per_q = None, {}
    for f in sorted(mdir.glob('responses*.jsonl')):
        rest = f.stem.split('responses_', 1)[-1]
        if rest and not rest.startswith('shard'):
            name = rest
        for r in _load_jsonl(f):
            if r.get('image') and r.get('ok', True):
                per_q[str(r['qid'])] = mdir / r['image']
    if per_q:
        IMG[name or mdir.name] = per_q

BOARD = (DF.groupby('image_model')
           .agg(n=('qid', 'size'), 对齐=('alignment_score', 'mean'),
                质量=('quality_score', 'mean'), 美感=('aesthetic_score', 'mean'))
           .round(2))
BOARD['综合'] = BOARD[['对齐', '质量', '美感']].mean(axis=1).round(2)
BOARD = BOARD.sort_values('综合', ascending=False)
MODELS = list(BOARD.index)

print(f'题库 {len(QS)} 题 | 判分 {len(ROWS)} 行（fail {len(FAILS)} 行已剔除）| '
      f'模型 {len(MODELS)} 个')
print(DF.groupby(['judge_model', 'schema', 'image_model']).size().rename('n').to_string())
for m in MODELS:
    n_m = int((DF['image_model'] == m).sum())
    if n_m != len(QS):
        print(f'!! 判分不完整：{m} {n_m}/{len(QS)}')
    if m not in IMG:
        print(f'!! 未找到 {m} 的出图目录（responses*.jsonl），case 抽样将只给分数不给图')
'''

CELL_OVERALL = LOADER + '''
# ---- 一、整体得分对比 ----
from IPython.display import display, Markdown

display(Markdown('**三线榜单（综合分降序；综合 = 三线等权平均，仅排序辅助，非官方口径）**'))
display(BOARD)

_nq = len(QS)
display(Markdown(f'**判分完整性**：{len(MODELS)} 模型 × 每题一行，应各 {_nq} 行；'
                 f'实际 ' + '，'.join(f'{m} {int(BOARD.loc[m, "n"])}' for m in MODELS) + '。'))

_comp = DF[['alignment_score', 'quality_score', 'aesthetic_score']].mean(axis=1)
_buckets = pd.cut(_comp, bins=[-.01, 0, 20, 40, 60, 80, 100],
                  labels=['=0', '(0,20]', '(20,40]', '(40,60]', '(60,80]', '(80,100]'])
_dist = (DF.assign(分段=_buckets)
           .groupby(['分段', 'image_model'], observed=False).size()
           .unstack('image_model').reindex(columns=MODELS).fillna(0).astype(int))
display(Markdown('**逐题综合分分段分布（200 题 × 模型）**'))
display(_dist)
'''

MD_DIMS = '''## 关键维度对比
三维 × 22 明细项（对齐 10 / 质量 8 / 美感 4）φ 均分（0–100，N/A 剔除）分模型横比；极差 = 四模型最高 − 最低。图表轴标用英文（运行环境无中文字体），中文项名以表格为准。'''

CELL_DIMS = '''# ---- 二、关键维度对比：三维 × 22 明细项 × 四模型（φ 均分）----
import matplotlib.pyplot as plt
import numpy as np
from IPython.display import display, Markdown

_it = pd.DataFrame([
    {'qid': r['qid'], 'image_model': r['image_model'], 'dim': d, 'item': k, 'phi': PHI[v]}
    for r in ROWS for d, (sk, _) in RK.items()
    for k, v in r[sk].items() if v in (0, 1, 2)])
TAB = _it.pivot_table(index=['dim', 'item'], columns='image_model',
                      values='phi').reindex(ALL_ITEMS)[MODELS].round(1)

_tab_zh = TAB.copy()
_tab_zh.index = pd.MultiIndex.from_tuples(
    [(DIM_ZH[d], ITEM_ZH.get(k, k)) for d, k in TAB.index], names=['维度', '项'])
_tab_zh['极差'] = (TAB.max(axis=1) - TAB.min(axis=1)).round(1).to_numpy()
display(Markdown('**明细项 φ 均分（中文对照 + 极差）**'))
display(_tab_zh)

_ys = np.arange(len(ALL_ITEMS))[::-1]
_h = 0.8 / len(MODELS)
fig, ax = plt.subplots(figsize=(10.5, 9.0), dpi=140)
for j, m in enumerate(MODELS):
    ax.barh(_ys + (j - (len(MODELS) - 1) / 2) * _h, TAB[m].values, height=_h * 0.94,
            label=m, color=plt.cm.tab10(j))
_bounds, _cum = [], len(ALL_ITEMS)
for d in ('alignment', 'quality', 'aesthetics'):
    _cnt = sum(1 for dd, _ in ALL_ITEMS if dd == d)
    _cum -= _cnt
    if _cum > 0:
        ax.axhline(_cum - 0.5, color='k', lw=0.8)
    _bounds.append((d, _cum, _cnt))
ax.set_yticks(_ys)
ax.set_yticklabels([k for _, k in ALL_ITEMS], fontsize=8)
for d, start, cnt in _bounds:
    ax.text(101.5, start + cnt / 2 - 0.5, d, rotation=90, va='center', ha='left',
            fontsize=9, color='#555555')
ax.set_xlim(0, 105)
ax.set_xticks(range(0, 101, 20))
ax.set_xlabel('mean phi (0-100)')
ax.set_title('Item-level mean phi by model · V2 · judge gpt-5.6-sol · N/A excluded')
ax.grid(axis='x', lw=0.4, alpha=0.4)
ax.set_axisbelow(True)
ax.legend(fontsize=8.5, loc='lower right')
plt.tight_layout()
plt.show()
'''

MD_CASES = '''## 抽样看 case
下方已直接保存 3 题的题源图 + 四模型生成图（原始分辨率，每张图独立显示，不缩放重采样、不依赖控件）与逐项打分矩阵；完整判官理由不入默认输出，用 `show_reasons` 按需查看。若编辑器自动适应栏宽，可打开图片输出或保存图片后按 100% 查看。
样例格可独立重跑（重跑即重新加载数据）；之后可直接改参再抽样：`sample_cases(n=5, seed=7)`、`sample_cases(qids=['60001'])`、`sample_cases(level='L3')`、`sample_cases(left='gemini-3.1-flash-image', right='bagel', min_abs_delta=30, winner='left')`、`sample_cases(n=3, sort_by_abs_delta=True)` 取综合分差最大的题。参数口径对照 edit 侧 `review_bench200_three.sample_cases`（edit_type→level；配对综合分 = 三线等权平均，仅抽样筛选辅助）。
默认按固定随机种子（seed=42）从全量 200 题抽取 3 题。'''

SHOW_DEFS = '''# ---- 三、抽样看 case（本格可独立运行；图片原始分辨率直接保存在输出中）----
import io
import random

from IPython.display import display, Markdown
from IPython.display import Image as NotebookImage
from PIL import Image


def show_case(qid):
    """单题卡片：题面 + 题源图 + 四模型生成图（原始分辨率）+ 逐项打分矩阵。"""
    qid = str(qid)
    if qid not in QS:
        raise ValueError(f'Unknown qid: {qid}')
    q = QS[qid]
    display(Markdown(f"### {qid} · level {q.get('level')} · 实例：{q.get('instance')}"
                     f"\\n\\n**题面**：{q.get('gen_prompt', '')}"))
    extra = []
    if q.get('reasoning'):
        extra.append(f'**出题思路**\\n\\n{q["reasoning"]}')
    if q.get('weak_points'):
        extra.append('**弱点乘积**：' + '、'.join(q['weak_points']))
    if q.get('notes'):
        extra.append(f'**notes**：{q["notes"]}')
    if extra:
        display(Markdown('<details><summary>出题思路 / 弱点乘积 / notes（点开）</summary>\\n\\n'
                         + '\\n\\n'.join(extra) + '\\n\\n</details>'))

    sample_p = BENCH / 'samples' / Path(q.get('_sample_image', '')).name
    if not sample_p.exists():
        _hits = sorted((BENCH / 'samples').glob(f'{qid}_*'))
        sample_p = _hits[0] if _hits else None
    panels = [('题源图', sample_p)] + [(m, IMG.get(m, {}).get(qid)) for m in MODELS]
    for label, path in panels:
        if path is None or not Path(path).exists():
            display(Markdown(f'**{label}：缺图**'))
            continue
        with Image.open(path) as im:
            fmt, (w, hgt) = (im.format or 'png').lower(), im.size
        if fmt in ('png', 'jpeg'):          # 原字节直出，不再压缩重采样
            data = Path(path).read_bytes()
        else:                                # webp 等浏览器/notebook 不稳的格式转 PNG
            buf = io.BytesIO()
            Image.open(path).convert('RGB').save(buf, format='PNG')
            data, fmt = buf.getvalue(), 'png'
        display(Markdown(f'**{label} · {w} × {hgt} px（原始分辨率）**'))
        display(NotebookImage(data=data, format=fmt, width=w, height=hgt, retina=False))

    lines = []
    for m in MODELS:
        r = SCORES.get((m, qid))
        if r is None:
            lines.append(f'**{m}**：无判分')
            continue
        comp = (r['alignment_score'] + r['quality_score'] + r['aesthetic_score']) / 3
        lines.append(f"**{m}** · 对齐 {r['alignment_score']:.1f} / 质量 {r['quality_score']:.1f}"
                     f" / 美感 {r['aesthetic_score']:.1f} · 综合 {comp:.1f}")
    display(Markdown('  \\n'.join(lines)))

    mat = {}
    for m in MODELS:
        r = SCORES.get((m, qid))
        if r is None:
            continue
        for d, (sk, _) in RK.items():
            for k, v in r[sk].items():
                mat.setdefault((DIM_ZH[d], ITEM_ZH.get(k, k)), {})[m] = v
    frame = pd.DataFrame.from_dict(mat, orient='index').reindex(columns=MODELS)
    frame.index = pd.MultiIndex.from_tuples(frame.index.tolist(), names=['维度', '项'])
    display(frame)


def show_reasons(qid, model=None, dim=None):
    """判官逐项完整理由：model/dim 缺省 = 全部模型、全部三维（dim 可用 alignment/quality/aesthetics 或中文）。"""
    qid = str(qid)
    for m in (MODELS if model is None else [model]):
        r = SCORES.get((m, qid))
        if r is None:
            continue
        print(f'== {qid} · {m}')
        for d, (sk, rk) in RK.items():
            if dim is not None and d != dim and DIM_ZH[d] != dim:
                continue
            for k in r[sk]:
                print(f'  [{DIM_ZH[d]}/{ITEM_ZH.get(k, k)}] {r[sk][k]} 档 | '
                      f'{r[rk].get(k, "")}')


def sample_cases(n=3, seed=42, qids=None, level='全部',
                 left='gemini-3.1-flash-image', right='bagel',
                 winner='全部', min_abs_delta=0.0, sort_by_abs_delta=False):
    """口径同 edit 侧 review_bench200_three.sample_cases，参数按 t2i 适配：
    edit_type→level（L1/L2/L3）；配对综合分 = 三线等权平均（仅抽样筛选辅助）。"""
    if left == right or left not in MODELS or right not in MODELS:
        raise ValueError('请选择两个不同的已判分模型')
    if min_abs_delta < 0:
        raise ValueError('min_abs_delta 不得为负')
    if qids is None:
        ids = set(QS)
        if level != '全部':
            ids = {q for q in ids if QS[q].get('level') == level}

        def comp(m, q):
            r = SCORES.get((m, str(q)))
            if r is None:
                return None
            return (r['alignment_score'] + r['quality_score'] + r['aesthetic_score']) / 3

        ids = {q for q in ids if comp(left, q) is not None and comp(right, q) is not None}
        delta = {q: comp(left, q) - comp(right, q) for q in ids}
        if winner == 'left':
            ids = {q for q in ids if delta[q] > 0}
        elif winner == 'right':
            ids = {q for q in ids if delta[q] < 0}
        elif winner == 'tie':
            ids = {q for q in ids if delta[q] == 0}
        elif winner != '全部':
            raise ValueError("winner 仅支持 'left' / 'tie' / 'right' / '全部'")
        if min_abs_delta > 0:
            ids = {q for q in ids if abs(delta[q]) >= min_abs_delta}
        if sort_by_abs_delta:
            qids = sorted(ids, key=lambda q: (-abs(delta[q]), q))[:n]
        else:
            qids = random.Random(seed).sample(sorted(ids), min(n, len(ids)))
    if not qids:
        display(Markdown('当前条件无匹配题目，请放宽 level、胜负或分差条件。'))
    for q in qids:
        show_case(q)
    return [str(q) for q in qids]


SAMPLE_QIDS = sample_cases(n=3, seed=42)
'''


def cell(src, ctype="code", cid=""):
    d = {
        "cell_type": ctype,
        "id": cid,
        "metadata": {},
        "source": src.splitlines(keepends=True),
    }
    if ctype == "code":
        d["outputs"] = []
        d["execution_count"] = None
    return d


def build():
    return {
        "cells": [cell(MD_INTRO, "markdown", "intro"),
                  cell(MD_OVERALL, "markdown", "overall-text"),
                  cell(CELL_OVERALL, cid="overall"),
                  cell(MD_DIMS, "markdown", "dims-text"),
                  cell(CELL_DIMS, cid="dims"),
                  cell(MD_CASES, "markdown", "cases-text"),
                  cell(LOADER + SHOW_DEFS, cid="cases")],
        "metadata": {
            "kernelspec": {"display_name": "Python 3 (demiwtg env)",
                           "language": "python", "name": "demiwtg"},
            "language_info": {"name": "python", "version": "3.10"},
        },
        "nbformat": 4,
        "nbformat_minor": 5,
    }


def execute(path):
    import nbformat
    from nbclient import NotebookClient
    nb = nbformat.read(path, as_version=4)
    NotebookClient(nb, timeout=1800, kernel_name=nb.metadata.kernelspec.name,
                   resources={"metadata": {"path": str(Path(path).parent)}}).execute()
    nbformat.write(nb, path)
    print(f"executed: {path}")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--execute", action="store_true",
                   help="生成后以 demiwtg 内核执行并回写输出")
    a = p.parse_args()
    OUT_NB.write_text(json.dumps(build(), ensure_ascii=False, indent=1) + "\n",
                      encoding="utf-8")
    print(f"written: {OUT_NB}")
    if a.execute:
        execute(OUT_NB)
