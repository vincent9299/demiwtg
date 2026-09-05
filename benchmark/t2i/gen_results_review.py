#!/usr/bin/env python3
"""生成 t2i/results_review.ipynb（t2i 赛道打分/评估结果审阅，纯只读展示）。

布局三段：加载+过滤参数 → 整体分析（报表/分布/分组/封顶统计）
→ 逐题明细卡（生成图 vs 样本图、得分计算链、逐 check 档位+rubric 判据、
通用维度、judge 分析原文）。

用法：python3 benchmark/t2i/gen_results_review.py   # 覆写同目录 results_review.ipynb
"""
import json
from pathlib import Path

OUT_NB = Path(__file__).resolve().parent / "results_review.ipynb"

MD_INTRO = """# t2i 赛道 · 打分/评估结果审阅

- 数据三件套（都在下方参数区改路径）：题库 `QFILE`、模型产出目录 `RESP_DIR`（`responses_shard*.jsonl` + `imgs/<qid>.png`）、判分产物 `SCORES`（`eval_score.py score` 输出的 `scores.jsonl` + `scores.report.json`）
- **第 2 格 · 整体分析**：始终用全量判分结果（不受过滤器影响），给批次级结论
- **第 3 格 · 逐题明细**：受过滤参数控制（前缀/难度/分段/封顶标志/指定 qid），每题展示 生成图 vs 样本参照图、得分计算链（知识线/通用线 → 封顶 → 总分）、逐 check 的 judge 档位与 rubric 判据、通用维度档位、judge 的分析原文
- 判分口径快照：总分 = 0.7×知识线 + 0.3×通用线（通用线缺失时退化为知识线）；φ 映射 {0→0, 1→60, 2→100}；知识线 <40 熔断封顶 20；任一 critical check 得 0 封顶 20；gate（主体缺失/跑偏）封顶 20
- **判读警告**：judge 未输出的 check 档位会被判分侧按 0 档缺省计入（连锁触发熔断/封顶 → 0 分）。分析格第六节给合规性统计；这类 0 分是「judge 未判」的缺省推定，勿当作模型真实表现
- 配置了对照判分产物 `SCORES_B` 时，分析格第七节给双 judge 对照（总览/逐题 diff/gate 分歧），明细卡头并排两个 judge 的总分
- 纯只读展示，零数据加工；图片内联前会压成 JPEG 控制 notebook 体积

> 运行：菜单 Run All，或逐 Cell 运行。"""

CELL_LOAD = """import json, html as _html
from pathlib import Path

QFILE    = Path('data/synth_gen/questions_v5.jsonl')   # ← 题库
RESP_DIR = Path('data/eval_bagel_v5')                  # ← 模型产出目录
SCORES   = RESP_DIR / 'scores_ctx32k.jsonl'            # ← eval_score.py 判分产物（2026-08-26 修复版：
                                                       #   部署上下文 8192→32768 + extract_json 抗示例污染，30/30 解析成功）
REPORT   = RESP_DIR / 'scores_ctx32k.report.json'
SCORES_B = RESP_DIR / 'scores_gemini-3.1-pro.jsonl'    # ← 对照 judge 产物（不存在/改 '' 则跳过对照）
LABEL_A, LABEL_B = 'qwen3.8-27b(32k)', 'gemini-3.1-pro'   # ← 两侧 judge 名（展示用）

# ====== 过滤参数（只影响第 3 格逐题明细；空列表/None = 不过滤）======
QIDS = []                # 指定 qid 列表，如 ['gpt56-0001-t2i-1']
GENERATORS = []          # qid 前缀：'gpt56' / 'fable' / 'gemini'
DIFFICULTIES = []        # 'L1' / 'L2' / 'L3'
ONLY_FLAGGED = False     # 只看命中 gate / critical / 熔断 的题
ZERO_ONLY = False        # 只看总分 = 0 的题
SCORE_MIN, SCORE_MAX = None, None    # 总分区间（闭区间）
SORT = 'total'           # 'total'（低→高）| 'total_desc' | 'qid'
SHOW_N = 0               # 最多展示几题，0 = 全部
# ==================================================================

def _load_jsonl(p):
    with open(p, encoding='utf-8') as f:
        return [json.loads(l) for l in f if l.strip()]

def _esc(x):
    return _html.escape(str(x))

DIFF_COLOR = {'L1': '#2a7a4b', 'L2': '#b07020', 'L3': '#a33333'}
PHI = {0: 0, 1: 60, 2: 100}

qs = {q['qid']: q for q in (_load_jsonl(QFILE) if QFILE.exists() else [])}
if not qs:
    print(f'!! 题库不存在或为空：{QFILE}')

# 多分片按 qid 合并（后读到的覆盖，最新重跑优先）
recs = {}
if RESP_DIR.exists():
    for f in sorted(RESP_DIR.glob('responses_shard*.jsonl')):
        for l in f.open(encoding='utf-8'):
            if l.strip():
                r = json.loads(l)
                recs[r['qid']] = r

scored = {r['qid']: r for r in (_load_jsonl(SCORES) if SCORES.exists() else [])}
report = json.loads(REPORT.read_text(encoding='utf-8')) if REPORT.exists() else None

scored_b = {}
report_b = None
if SCORES_B and Path(SCORES_B).exists():
    scored_b = {r['qid']: r for r in _load_jsonl(SCORES_B)}
    rep_b_path = Path(SCORES_B).with_suffix('.report.json')
    if rep_b_path.exists():
        report_b = json.loads(rep_b_path.read_text(encoding='utf-8'))

# 全量合并行（分析用）：以判分结果为主轴
rows_all = [{'qid': qid, 'q': qs.get(qid), 'r': recs.get(qid), 's': s,
             's_b': scored_b.get(qid)}
            for qid, s in scored.items()]
unscored = [qid for qid in qs if qid not in scored]

# judge 输出合规性：check_scores 覆盖率（缺失档位按 0 档缺省计入，需与真判区分）
for row in rows_all:
    row['n_checks'] = len((row['q'] or {}).get('implicit_checks') or [])
    row['n_scored'] = len(row['s'].get('check_scores') or {})

def _gen_prefix(qid):
    return qid.split('-')[0]

def _flagged(s):
    return bool(s.get('gate_capped') or s.get('critical_capped') or s.get('knowledge_fused'))

# 过滤 + 排序（明细用）
sel = [row for row in rows_all
       if (not QIDS or row['qid'] in QIDS)
       and (not GENERATORS or _gen_prefix(row['qid']) in GENERATORS)
       and (not DIFFICULTIES or (row['q'] or {}).get('difficulty') in DIFFICULTIES)
       and (not ONLY_FLAGGED or _flagged(row['s']))
       and (not ZERO_ONLY or row['s'].get('total') == 0)
       and (SCORE_MIN is None or row['s'].get('total', 0) >= SCORE_MIN)
       and (SCORE_MAX is None or row['s'].get('total', 0) <= SCORE_MAX)]
sel.sort(key=lambda row: row['qid'])
if SORT in ('total', 'total_desc'):
    sel.sort(key=lambda row: row['s'].get('total') or 0, reverse=(SORT == 'total_desc'))
if SHOW_N:
    sel = sel[:SHOW_N]

print(f'题库 {len(qs)} 题 | 判分 {len(scored)} 题 | 未判分 {len(unscored)} 题'
      + (f'：{unscored[:8]}' if unscored else ''))
if report:
    print(f'报表: n={report.get("n")} overall={report.get("overall")} '
          f'judge_fail={report.get("judge_fail")}')
if scored_b:
    print(f'对照 judge（{LABEL_B}）：{len(scored_b)} 题判分'
          + (f'，overall={report_b.get("overall")}' if report_b else ''))
_n_none = sum(1 for row in rows_all if row['n_checks'] and row['n_scored'] == 0)
_n_part = sum(1 for row in rows_all if 0 < row['n_scored'] < row['n_checks'])
if _n_none or _n_part:
    print(f'⚠ judge 合规性：未输出 checks {_n_none} 题、部分输出 {_n_part} 题'
          f'（缺失档位按 0 档缺省计入，见分析格第六节）')
print(f'明细展示 {len(sel)}/{len(rows_all)} 题（SORT={SORT}）')"""

CELL_ANALYSIS = """# ---- 整体分析（全量，不受过滤器影响）----
import io, base64
from IPython.display import display, HTML

def _bar(frac, color='#4a7ab5', w=180, h=12):
    frac = max(0.0, min(1.0, frac))
    return (f'<span style="display:inline-block;width:{w}px;height:{h}px;'
            f'background:#eee;vertical-align:middle;margin-right:6px">'
            f'<span style="display:block;width:{int(w*frac)}px;height:{h}px;background:{color}"></span></span>')

def _score_color(v):
    if v is None: return '#999'
    return '#2a7a4b' if v >= 60 else ('#b07020' if v >= 30 else '#a33333')

def _fmt(v, nd=1):
    return '—' if v is None else f'{v:.{nd}f}'

def _mean(vals):
    vals = [v for v in vals if v is not None]
    return sum(vals) / len(vals) if vals else None

def _table(headers, rows_html):
    th = ''.join(f'<th style="padding:3px 8px;text-align:left">{h}</th>' for h in headers)
    trs = ''.join('<tr>' + ''.join(f'<td style="padding:3px 8px">{c}</td>' for c in r) + '</tr>'
                  for r in rows_html)
    return (f'<table style="border-collapse:collapse;font-size:13px;margin:6px 0 14px">'
            f'<tr style="background:#f0f0f0">{th}</tr>{trs}</table>')

html = []
n = len(rows_all)
totals = [row['s'].get('total') for row in rows_all]
html.append('<h2>一、批次总览</h2>')
if report:
    cards = [('题数', report.get('n')), ('总分均值', _fmt(report.get('overall'))),
             ('知识线均值', _fmt(report.get('knowledge'))),
             ('通用线均值', _fmt(report.get('general'))),
             ('judge 失败', report.get('judge_fail'))]
else:
    cards = [('题数', n), ('总分均值', _fmt(_mean(totals))),
             ('知识线均值', _fmt(_mean([row["s"].get("knowledge_score") for row in rows_all]))),
             ('通用线均值', _fmt(_mean([row["s"].get("general_score") for row in rows_all]))),
             ('judge 失败', 0)]
html.append('<div style="display:flex;gap:10px;flex-wrap:wrap;margin:8px 0">' + ''.join(
    f'<div style="border:1px solid #ddd;padding:8px 14px;min-width:88px">'
    f'<div style="font-size:11px;color:#888">{_esc(k)}</div>'
    f'<div style="font-size:20px;font-weight:bold">{_esc(v)}</div></div>'
    for k, v in cards) + '</div>')

# 封顶/熔断命中
n_gate = sum(1 for row in rows_all if row['s'].get('gate_capped'))
n_crit = sum(1 for row in rows_all if row['s'].get('critical_capped'))
n_fuse = sum(1 for row in rows_all if row['s'].get('knowledge_fused'))
n_zero = sum(1 for row in rows_all if row['s'].get('total') == 0)
html.append(f'<div style="font-size:13px;margin:4px 0 12px">封顶/熔断：'
            f'<b style="color:#a33333">gate {n_gate}</b> · '
            f'<b style="color:#a33333">critical {n_crit}</b> · '
            f'<b style="color:#b07020">知识熔断 {n_fuse}</b> · 总分 0 共 <b>{n_zero}</b> 题</div>')

html.append('<h2>二、总分分布</h2>')
buckets = [('=0', lambda t: t == 0), ('(0,20]', lambda t: 0 < t <= 20),
           ('(20,40]', lambda t: 20 < t <= 40), ('(40,60]', lambda t: 40 < t <= 60),
           ('(60,80]', lambda t: 60 < t <= 80), ('(80,100]', lambda t: t > 80)]
mx = 0
cnt = []
for label, fn in buckets:
    c = sum(1 for t in totals if t is not None and fn(t))
    cnt.append((label, c)); mx = max(mx, c)
html.append(_table(['分段', '题数', ''], [
    (label, str(c), _bar(c / mx if mx else 0, '#4a7ab5', 260) +
     ('' if not c else f'{c/n*100:.0f}%')) for label, c in cnt]))

# 分难度看均分
html.append('<h2>三、分组均值</h2>')
def _group_table(title, keyfn):
    groups = {}
    for row in rows_all:
        groups.setdefault(keyfn(row), []).append(row)
    rows = []
    for k in sorted(groups):
        g = groups[k]
        rows.append((
            _esc(k), str(len(g)),
            f'<b style="color:{_score_color(_mean([x["s"].get("total") for x in g]))}">'
            f'{_fmt(_mean([x["s"].get("total") for x in g]))}</b>',
            _fmt(_mean([x['s'].get('knowledge_score') for x in g])),
            _fmt(_mean([x['s'].get('general_score') for x in g])),
            str(sum(1 for x in g if x['s'].get('total') == 0))))
    display(HTML(f'<h3 style="margin:10px 0 2px;font-size:14px">{title}</h3>' + _table(
        ['组', '题数', '总分均值', '知识线', '通用线', '0 分题数'], rows)))

_group_table('按出题模型（qid 前缀）', lambda row: _gen_prefix(row['qid']))
_group_table('按难度', lambda row: (row['q'] or {}).get('difficulty', '?'))
_group_table('按知识维度', lambda row: (row['q'] or {}).get('knowledge_dim', '?'))

html.append('<h2>四、通用维度（facet）表现</h2>')
facet_stats = {}
for row in rows_all:
    for k, v in (row['s'].get('facet_scores') or {}).items():
        if isinstance(v, int) and v in (0, 1, 2):
            facet_stats.setdefault(k, []).append(v)
if facet_stats:
    rows = []
    for k in sorted(facet_stats, key=lambda k: -_mean([PHI[v] for v in facet_stats[k]])):
        vs = facet_stats[k]
        rows.append((_esc(k), str(len(vs)),
                     f'{vs.count(0)} / {vs.count(1)} / {vs.count(2)}',
                     f'<b style="color:{_score_color(_mean([PHI[v] for v in vs]))}">'
                     f'{_fmt(_mean([PHI[v] for v in vs]))}</b>'))
    html.append(_table(['facet', '计分次数', '0/1/2 档分布', 'φ 均分'], rows))
else:
    html.append('<div style="color:#999">无 facet 计分记录</div>')

html.append('<h2>五、知识 check 表现（含 critical 命中）</h2>')
ck_tot = ck_zero = ck_crit = ck_crit_zero = 0
phi_sum = 0.0
for row in rows_all:
    q = row['q']
    if not q:
        continue
    cs = row['s'].get('check_scores') or {}
    for i, c in enumerate(q.get('implicit_checks') or []):
        s = cs.get(str(i), cs.get(i))
        if not (isinstance(s, int) and s in (0, 1, 2)):
            continue
        ck_tot += 1; phi_sum += PHI[s]
        if s == 0: ck_zero += 1
        if c.get('critical'):
            ck_crit += 1
            if s == 0: ck_crit_zero += 1
html.append('<div style="font-size:13px;margin:6px 0">计分 check 共 <b>%d</b> 个，0 档占 <b>%d</b>（%.0f%%），'
            'φ 均分 <b>%.1f</b>；critical check %d 个，其中 0 档 <b style="color:#a33333">%d</b>（%.0f%%）</div>'
            % (ck_tot, ck_zero, ck_zero / ck_tot * 100 if ck_tot else 0,
               phi_sum / ck_tot if ck_tot else 0,
               ck_crit, ck_crit_zero, ck_crit_zero / ck_crit * 100 if ck_crit else 0))

html.append('<h2>六、judge 输出合规性（读懂 0 分的关键）</h2>')
full = [row for row in rows_all if row['n_checks'] and row['n_scored'] == row['n_checks']]
part = [row for row in rows_all if 0 < row['n_scored'] < row['n_checks']]
none_rows = [row for row in rows_all if row['n_checks'] and row['n_scored'] == 0]
html.append(_table(['judge 输出', '题数', '总分均值'], [
    ('✅ checks 全量输出', str(len(full)), _fmt(_mean([row['s'].get('total') for row in full]))),
    ('⚠ 部分输出（缺项按 0 档缺省）', str(len(part)), _fmt(_mean([row['s'].get('total') for row in part]))),
    ('❌ 未输出 knowledge_checks（全部按 0 档缺省）', str(len(none_rows)),
     _fmt(_mean([row['s'].get('total') for row in none_rows])))]))
if none_rows or part:
    html.append('<div style="background:#fdecea;border-left:3px solid #a33333;padding:8px 10px;'
                'font-size:13px;margin:4px 0"><b>⚠ 判读警告</b>：judge（qwen3.8-27b）对 <b>'
                f'{len(none_rows) + len(part)}/{n}</b> 题未按 schema 给出全部 check 档位'
                f'（其中 {len(none_rows)} 题完全没给）。判分侧把缺失档位按 <b>0 档（φ=0）缺省计入</b>，'
                '由此连锁触发知识熔断与 critical 封顶 → 总分 0。<b>这些 0 分是「judge 未判」的缺省推定，'
                '不能当作 Bagel 真没画对的结论</b>；批次结论应以「✅ 全量输出」子集为准，'
                '缺判题建议人工看图复核或重判。</div>')

# ---- 七、双 judge 对照 ----
if scored_b:
    html.append(f'<h2>七、双 judge 对照（{_esc(LABEL_A)} vs {_esc(LABEL_B)}）</h2>')
    def _overview(rep, rows):
        if rep:
            return (rep.get('overall'), rep.get('knowledge'), rep.get('general'),
                    rep.get('gate_capped'), rep.get('knowledge_fused'))
        ts = [r['s'].get('total') for r in rows]
        return (_mean(ts), _mean([r['s'].get('knowledge_score') for r in rows]),
                _mean([r['s'].get('general_score') for r in rows]),
                sum(1 for r in rows if r['s'].get('gate_capped')),
                sum(1 for r in rows if r['s'].get('knowledge_fused')))
    ov_a = _overview(report, rows_all)
    rows_b = [{'s': r['s_b']} for r in rows_all if r.get('s_b')]
    ov_b = _overview(report_b, rows_b)
    html.append(_table(['judge', '总分均值', '知识线', '通用线', 'gate 封顶', '知识熔断'], [
        (_esc(LABEL_A), *(_fmt(v) for v in ov_a[:3]), str(ov_a[3]), str(ov_a[4])),
        (_esc(LABEL_B), *(_fmt(v) for v in ov_b[:3]), str(ov_b[3]), str(ov_b[4]))]))

    qrows = []
    n_close = n_bgate_only = n_rescued = 0
    for row in sorted(rows_all, key=lambda x: x['qid']):
        a, b = row['s'], row.get('s_b') or {}
        ta, tb = a.get('total'), b.get('total')
        diff = None if (ta is None or tb is None) else tb - ta
        if diff is not None and abs(diff) <= 15:
            n_close += 1
        if b.get('gate_capped') and not a.get('gate_capped'):
            n_bgate_only += 1
        if not a.get('check_scores') and b.get('check_scores'):
            n_rescued += 1
        col = '#999' if diff is None else ('#a33333' if abs(diff) > 30 else '#333')
        qrows.append((_esc(row['qid']),
                      f'<span style="color:{_score_color(ta)}">{_fmt(ta)}</span>',
                      f'<span style="color:{_score_color(tb)}">{_fmt(tb)}</span>',
                      f'<span style="color:{col}">{("—" if diff is None else f"{diff:+.1f}")}</span>',
                      '🚫' if a.get('gate_capped') else '',
                      ('🚫 ' + _esc((b.get('gate_reason') or '')[:40])) if b.get('gate_capped') else ''))
    html.append('<div style="font-size:13px;margin:6px 0">'
                f'两判分差 ≤15 的题 <b>{n_close}/{n}</b>；仅 {_esc(LABEL_B)} gate 的题 <b>{n_bgate_only}</b>；'
                f'{_esc(LABEL_A)} 未判而被 {_esc(LABEL_B)} 补判出 check 的题 <b>{n_rescued}</b>'
                '（分歧题请下拉到明细卡人工看图，明细卡头部并排显示两个 judge 的总分）</div>')
    html.append(_table(['qid', _esc(LABEL_A), _esc(LABEL_B), 'diff(B−A)', 'gate A', 'gate B（理由摘要）'],
                       qrows))
display(HTML(''.join(html)))"""

CELL_DETAIL = """# ---- 逐题明细：得分 + 原因 ----
import re, io, base64
from PIL import Image
from IPython.display import display, HTML

try:
    import eval_score as _es
    _FACET_CRIT = {k: crit for k, _pillar, _sub, crit in _es.FACETS}
except Exception:
    _FACET_CRIT = {}

REPO_ROOT = Path('..').resolve().parent          # benchmark/t2i → 仓库根
BLOBS_DIR = REPO_ROOT / 'datasets' / 'demiwtg' / 'blobs'
IMG_W = 300
SCORE2_COLOR = {0: '#a33333', 1: '#b07020', 2: '#2a7a4b'}

def _resolve_sample(img_rel):
    '''题库 _sample_image → 路径；RESP_DIR/../.. 数据区优先，缺失回退 blobs 内容寻址'''
    base = Path('data')
    p = base / img_rel
    if p.exists():
        return p
    m = re.search(r'_([a-f0-9]{8,})\\.[a-z]+$', img_rel or '')
    if m:
        d = BLOBS_DIR / m.group(1)[:2]
        if d.exists():
            hits = sorted(d.glob(f'{m.group(1)}*'))
            if hits:
                return hits[0]
    return None

def _img(path, title='', w=IMG_W):
    p = Path(path) if path else None
    if not p or not p.exists():
        return (f'<figure style="margin:4px"><figcaption style="color:#999">'
                f'（{title} 图缺失）</figcaption></figure>')
    buf = io.BytesIO()
    Image.open(p).convert('RGB').save(buf, format='JPEG', quality=82)
    uri = base64.b64encode(buf.getvalue()).decode()
    return (f'<figure style="margin:4px;text-align:center">'
            f'<img src="data:image/jpeg;base64,{uri}" style="max-width:{w}px;max-height:{w}px">'
            f'<figcaption style="font-size:12px;color:#666">{title}</figcaption></figure>')

def _chip(text, bg, fg='#fff'):
    return (f'<span style="background:{bg};color:{fg};border-radius:3px;padding:1px 6px;'
            f'font-size:11px;margin-right:4px">{_esc(text)}</span>')

def _grade_chip(s):
    if not (isinstance(s, int) and s in (0, 1, 2)):
        return _chip('未判', '#999')
    return _chip(f'{s} 档 → φ{PHI[s]}', SCORE2_COLOR[s])

def _judge_json(raw):
    i = (raw or '').find('{')
    if i < 0:
        return None
    try:
        return json.JSONDecoder().raw_decode(raw[i:])[0]
    except Exception:
        return None

def _score_chain(s):
    k, g = s.get('knowledge_score'), s.get('general_score')
    ks, gs = _fmt(k), _fmt(g)
    raw_total = (0.7 * k + 0.3 * g) if (k is not None and g is not None) else k
    caps = []
    if s.get('gate_capped'): caps.append('gate 主体缺失/跑偏 → 封顶 20')
    if s.get('critical_capped'): caps.append('critical check 0 档 → 封顶 20')
    if s.get('knowledge_fused'): caps.append(f'知识线 {ks} < 40 → 熔断封顶 20')
    chain = (f'知识 <b>{ks}</b> ×0.7 + 通用 <b>{gs}</b> ×0.3 = {_fmt(raw_total)}'
             if g is not None else f'通用线缺失，总分退化 = 知识线 <b>{ks}</b>')
    if caps:
        chain += '　→　' + '　→　'.join(_esc(c) for c in caps)
    t = s.get('total')
    chain += (f'　→　<b style="font-size:15px;color:{_score_color(t)}">总分 {_fmt(t)}</b>')
    return f'<div style="background:#f6f8fa;border-left:3px solid #4a7ab5;padding:6px 10px;margin:6px 0;font-size:13px">{chain}</div>'

def _check_table(q, s):
    checks = q.get('implicit_checks') or []
    if not checks:
        return ''
    cs = s.get('check_scores') or {}
    out = []
    for i, c in enumerate(checks):
        sc = cs.get(str(i), cs.get(i))
        rub = c.get('rubric') or {}
        rub_html = []
        for lvl in ('0', '1', '2'):
            txt = rub.get(lvl)
            if txt is None:
                continue
            hit = isinstance(sc, int) and str(sc) == lvl
            rub_html.append(
                f'<div style="margin:1px 0 1px 12px;{"background:#fff3cd;font-weight:bold" if hit else "color:#777"}">'
                f'{"▶ " if hit else ""}{lvl} 档：{_esc(txt)}</div>')
        head = (f'<tr><td style="vertical-align:top;padding:4px 8px;border-top:1px solid #eee">'
                f'{_grade_chip(sc)}{" " + _chip("critical", "#a33333") if c.get("critical") else ""}'
                f'{" " + _chip("w=%g" % c["weight"], "#607a93") if c.get("weight") is not None else ""}'
                f'</td><td style="padding:4px 8px;border-top:1px solid #eee">'
                f'<b>{i}. {_esc(c.get("check", ""))}</b>'
                f'<div style="color:#555;font-size:12px">考察知识：{_esc(c.get("knowledge", ""))}'
                f'{"" if c.get("verified", True) else " ⚠未验证"}</div>'
                f'{"".join(rub_html)}')
        extras = []
        if c.get('acceptable_variants'):
            extras.append('允许变体：' + '；'.join(str(x) for x in c['acceptable_variants']))
        if c.get('visibility_requirement'):
            extras.append('最低可见条件：' + str(c['visibility_requirement']))
        if extras:
            head += f'<div style="color:#888;font-size:12px;margin-left:12px">{_esc(" | ".join(extras))}</div>'
        out.append(head + '</td></tr>')
    return ('<details open><summary style="cursor:pointer;margin-top:8px"><b>知识线逐 check（judge 档位 + rubric 判据）</b></summary>'
            '<table style="border-collapse:collapse;font-size:13px;width:100%">' + ''.join(out) + '</table></details>')

def _facet_table(q, s):
    fs = s.get('facet_scores') or {}
    if not fs:
        return ''
    chips = []
    for k in sorted(fs):
        v = fs[k]
        chip = _grade_chip(v) if isinstance(v, int) and v in (0, 1, 2) else _chip(f'{k}: {v}', '#999')
        crit = _FACET_CRIT.get(k)
        chips.append(f'<div style="margin:2px 0">{chip} <b>{_esc(k)}</b>'
                     + (f' <span style="color:#777;font-size:12px">{_esc(crit)}</span>' if crit else '')
                     + '</div>')
    return (f'<details><summary style="cursor:pointer;margin-top:8px"><b>通用线维度（{len(fs)} 项）</b></summary>'
            '<div style="font-size:13px;margin:4px 0 4px 8px">' + ''.join(chips) + '</div></details>')

def _judge_analysis(s):
    raw = s.get('raw') or ''
    obj = _judge_json(raw)
    prose = (raw[:raw.find("{")].strip() if "{" in raw else raw.strip())
    parts = []
    if prose:
        parts.append(f'<details open><summary style="cursor:pointer;margin-top:8px"><b>judge 分析原文（得分理由）</b></summary>'
                     f'<pre style="white-space:pre-wrap;font-size:12px;background:#fafafa;padding:8px">{_esc(prose)}</pre></details>')
    gate = (obj or {}).get('gate') or {}
    if s.get('gate_reason') or gate:
        parts.append(f'<div style="font-size:13px;margin-top:6px">gate：'
                     f'{"❌ 主体缺失/跑偏" if gate.get("subject_missing") else "✅ 通过"}'
                     + (f'　{_esc(s.get("gate_reason") or gate.get("reason") or "")}</div>' if (s.get('gate_reason') or gate.get('reason')) else ''))
    parts.append(f'<details><summary style="cursor:pointer;margin-top:6px"><b>judge 完整原始输出</b></summary>'
                 f'<pre style="white-space:pre-wrap;font-size:12px;background:#fafafa;padding:8px">{_esc(raw)}</pre></details>')
    return ''.join(parts)

for row in sel:
    q, r, s, qid = row['q'], row['r'], row['s'], row['qid']
    if q is None:
        display(HTML(f'<h3>{_esc(qid)}</h3><div style="color:#c00">不在题库中，无法展开明细</div><hr>'))
        continue
    gen_p = (RESP_DIR / r['image']) if (r and r.get('ok') and r.get('image')) else RESP_DIR / 'imgs' / f'{qid}.png'
    sample_p = _resolve_sample(q.get('_sample_image') or '')
    col = DIFF_COLOR.get(q.get('difficulty'), '#666')
    badges = ''
    if s.get('gate_capped'): badges += _chip('gate→20', '#a33333')
    if s.get('critical_capped'): badges += _chip('critical→20', '#c0392b')
    if s.get('knowledge_fused'): badges += _chip('知识熔断→20', '#b07020')
    if row['n_checks'] and row['n_scored'] == 0:
        badges += _chip('judge 未输出 checks', '#a33333')
    elif 0 < row['n_scored'] < row['n_checks']:
        badges += _chip('checks 仅判 %d/%d' % (row['n_scored'], row['n_checks']), '#b07020')
    t = s.get('total')
    head = (f'<h3 style="margin:18px 0 4px">{_esc(qid)} '
            f'<span style="color:{col}">{_esc(q.get("difficulty", "?"))}</span> · '
            f'{_esc(_gen_prefix(qid))} · {_esc(q.get("knowledge_dim", "?"))} · '
            f'<span style="color:{_score_color(t)}">总分 {_fmt(t)}</span> {badges}</h3>'
            f'<div style="color:#666;font-size:12px">样本：{_esc(q.get("_query_label") or "")}'
            f'（{_esc(q.get("sample_id") or "")}）· 出题模型 {_esc(q.get("_generator_model") or "?")}'
            + (f' · 推理 {r.get("seconds")}s' if r and r.get('seconds') is not None else '') + '</div>')
    if row.get('s_b'):
        tb = row['s_b'].get('total')
        head += (f'<div style="font-size:12px;color:#607a93">对照 judge（{_esc(LABEL_B)}）总分：'
                 f'<b style="color:{_score_color(tb)}">{_fmt(tb)}</b>'
                 + ('　🚫 gate：' + _esc((row['s_b'].get('gate_reason') or '')[:60])
                    if row['s_b'].get('gate_capped') else '') + '</div>')
    pics = ('<div style="display:flex;gap:12px;align-items:flex-start">'
            + _img(gen_p, '模型生成') + _img(sample_p, '样本参照') + '</div>')
    prompt = f'<div style="background:#f6f6f6;padding:6px 8px;margin:6px 0;font-size:13px"><b>生成提示</b>：{_esc(q.get("gen_prompt", ""))}</div>'
    if row['n_checks'] and row['n_scored'] == 0:
        warn = ('<div style="background:#fdecea;border-left:3px solid #a33333;padding:6px 10px;'
                'font-size:13px;margin:6px 0"><b>⚠ judge 未输出 knowledge_checks</b>：以下 check '
                '全部按 <b>0 档（φ=0）缺省计入</b>，知识线/熔断/封顶均为缺省推定——'
                '不能当作「Bagel 没画对」的结论，请结合图片人工复核。</div>')
    elif 0 < row['n_scored'] < row['n_checks']:
        warn = ('<div style="background:#fff8e1;border-left:3px solid #b07020;padding:6px 10px;'
                f'font-size:13px;margin:6px 0"><b>⚠ judge 仅输出 {row["n_scored"]}/{row["n_checks"]} '
                '项 check 档位</b>，未输出项按 0 档缺省计入。</div>')
    else:
        warn = ''
    meta = []
    if q.get('gate_spec'):
        gs = q['gate_spec']
        meta.append(f'gate_spec: required_subjects={gs.get("required_subjects")} · 主题={gs.get("theme_definition")}')
    if q.get('expected_failure_modes'):
        meta.append('expected_failure_modes: ' + '；'.join(str(x) for x in q['expected_failure_modes']))
    if q.get('notes'):
        meta.append('notes: ' + str(q['notes']))
    if s.get('facet_diagnostic'):
        meta.append('facet_diagnostic: ' + str(s['facet_diagnostic']))
    meta_html = (f'<details><summary style="cursor:pointer;margin-top:6px"><b>题目元信息</b></summary>'
                 f'<div style="font-size:12px;color:#555;margin-left:8px">'
                 + ''.join(f'<div>{_esc(m)}</div>' for m in meta) + '</div></details>') if meta else ''
    display(HTML(head + pics + prompt + warn + _score_chain(s) + _check_table(q, s)
                 + _facet_table(q, s) + _judge_analysis(s) + meta_html))
    display(HTML('<hr>'))"""


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


nb = {
    "cells": [cell(MD_INTRO, "markdown", "intro"), cell(CELL_LOAD, cid="load"),
              cell(CELL_ANALYSIS, cid="analysis"), cell(CELL_DETAIL, cid="detail")],
    "metadata": {
        "kernelspec": {"display_name": ".venv", "language": "python", "name": "python3"},
        "language_info": {"name": "python", "version": "3.10"},
    },
    "nbformat": 4,
    "nbformat_minor": 5,
}

OUT_NB.write_text(json.dumps(nb, ensure_ascii=False, indent=1), encoding="utf-8")
print(f"written: {OUT_NB}")
