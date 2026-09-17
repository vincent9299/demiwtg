#!/usr/bin/env python3
"""Read frozen bench200 scores and add a review section without altering history."""
from __future__ import annotations
import argparse
import base64
import hashlib
import html
import io
import json
import random
from pathlib import Path

EDIT_DIR = Path(__file__).resolve().parent
RUN = EDIT_DIR / 'bench200/scores_qib_v22_astra_medium_20260908'
NAMES = {'a': 'Qwen-Image-Edit-2511', 'b': 'BAGEL-7B-MoT'}
TAG = 'bench200-qib-v22-astra-medium-20260908'


def read_jsonl(path):
    rows = [json.loads(x) for x in Path(path).read_text().splitlines() if x.strip()]
    by_id = {r['qid']: r for r in rows}
    if len(by_id) != len(rows):
        raise ValueError(f'Duplicate qid in {path}')
    return by_id


def load_review(run=RUN):
    """No temporary scores are opened before the orchestrator freezes the run."""
    import pandas as pd
    run = Path(run)
    if not (run / 'runtime/frozen.json').is_file():
        return None
    import sys
    repo = str(EDIT_DIR.parents[1])
    if repo not in sys.path:
        sys.path.insert(0, repo)
    from benchmark.edit.eval_codex_score import EDIT_DIMS, PHI
    frozen = json.loads((run / 'runtime/frozen.json').read_text())
    expected_config = dict(n_questions=200, n_candidates=400, n_unique_contexts=400,
                           judge_model='gpt-6-astra', reasoning_effort='medium')
    for key, expected in expected_config.items():
        if frozen.get(key) != expected:
            raise ValueError(f'Frozen configuration mismatch: {key}')
    def check_hash(path, expected, label):
        if not isinstance(expected, str) or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError(f'Frozen artifact changed or hash missing: {label}')
    required = {f'{c}/{filename}' for c in NAMES
                for filename in ('scores.jsonl', 'report.json', 'blind_manifest.jsonl')}
    required |= {'comparison/paired_scores.jsonl', 'comparison/report.json'}
    artifacts = frozen.get('artifacts', {})
    if not isinstance(artifacts, dict) or not required <= set(artifacts):
        raise ValueError('Frozen artifacts map is incomplete')
    for relative, expected in artifacts.items():
        artifact = (run / relative).resolve()
        if Path(relative).is_absolute() or not artifact.is_relative_to(run.resolve()):
            raise ValueError(f'Invalid frozen artifact path: {relative}')
        check_hash(artifact, expected, relative)
    check_hash(EDIT_DIR / 'bench200/questions.jsonl', frozen.get('questions_sha256'), 'questions.jsonl')
    check_hash(EDIT_DIR / 'eval_codex_score.py', frozen.get('pipeline_sha256'), 'eval_codex_score.py')
    check_hash(EDIT_DIR / 'prompts/judge_prompt_edit_qib_v2.2.md', frozen.get('template_sha256'), 'judge template')
    allowed = ('qid', 'edit_type', 'edit_instruction', 'level', 'suite', '_batch',
               'source_kind', 'source_type', 'construction_profile', 'difficulty')
    qs = {qid: {k: r.get(k) for k in allowed} for qid, r in
          read_jsonl(EDIT_DIR / 'bench200/questions.jsonl').items()}
    pilot = set(read_jsonl(EDIT_DIR / 'synth_v61_pilot/questions.jsonl'))
    assert len(qs) == 200 and len(pilot) == 20 and pilot <= set(qs)
    scores, manifests, totals, dims = {}, {}, [], []
    for candidate, name in NAMES.items():
        scores[candidate] = read_jsonl(run / candidate / 'scores.jsonl')
        manifests[candidate] = read_jsonl(run / candidate / 'blind_manifest.jsonl')
        assert set(scores[candidate]) == set(qs) == set(manifests[candidate])
        for qid, s in scores[candidate].items():
            m, q = manifests[candidate][qid], qs[qid]
            assert s['inputs'] == m['inputs'] and s['edit_type'] == q['edit_type']
            assert m['edit_instruction'] == q['edit_instruction']
            assert hashlib.sha256(q['edit_instruction'].encode()).hexdigest() == m['inputs']['instruction_sha256']
            status = s['validity']['status']
            assert status in ('ok', 'model_failure', 'invalid_question', 'judge_unscorable')
            base = {'qid': qid, 'model': name, 'candidate': candidate,
                    'cohort': 'pilot20' if qid in pilot else '新增180', **q,
                    'status': status, 'counted': status in ('ok', 'model_failure')}
            raw = s['raw_dimensions']
            assert [d['label'] for d in raw] == EDIT_DIMS[q['edit_type']]
            tiers = [d['tier'] for d in raw]
            assert len(tiers) == 3 and all(t in PHI for t in tiers)
            expected = [PHI[tiers[0]], PHI[min(tiers[0], tiers[1])], PHI[min(tiers[0], tiers[2])]]
            if status == 'model_failure':
                expected = [0, 0, 0]
            assert [s['official_dimensions'][f'd{i}'] for i in (1, 2, 3)] == expected
            assert abs(s['official_total'] - sum(expected) / 3) < .001
            totals.append({**base, 'official': s['official_total'],
                           'raw_mapped': sum(PHI[t] for t in tiers) / 3,
                           'clamped': any(tiers[i] > tiers[0] for i in (1, 2)) if status == 'ok' else False})
            for i, d in enumerate(raw, 1):
                dims.append({**base, 'dim': f'd{i}', 'label': d['label'], 'tier': d['tier'],
                             'official_tier': s['official_tiers'][f'd{i}'],
                             'raw_mapped': PHI[d['tier']], 'official': expected[i - 1]})
    for qid in qs:
        for key in ('source_sha256', 'instruction_sha256'):
            assert manifests['a'][qid]['inputs'][key] == manifests['b'][qid]['inputs'][key]
    total, dim = pd.DataFrame(totals), pd.DataFrame(dims)
    common = set.intersection(*(set(total[(total.candidate == c) & total.counted].qid) for c in NAMES))
    paired = read_jsonl(run / 'comparison/paired_scores.jsonl')
    assert set(paired) == common, 'Pipeline and review paired denominator differ'
    for qid, p in paired.items():
        assert abs(p['left_total'] - scores['a'][qid]['official_total']) < .001
        assert abs(p['right_total'] - scores['b'][qid]['official_total']) < .001
    rep = json.loads((run / 'comparison/report.json').read_text())
    assert rep['overall']['n'] == len(common)
    return dict(run=run, frozen=frozen, qs=qs, scores=scores, manifests=manifests,
                total=total, dim=dim, common=common, pilot=pilot, paired=paired)



def display_usage(review):
    import pandas as pd
    from IPython.display import display, Markdown
    if review is None:
        return
    keys = ('input_tokens', 'cached_input_tokens', 'output_tokens', 'reasoning_output_tokens')
    rows = []
    for candidate, name in NAMES.items():
        for qid in review['qs']:
            job = f'{candidate}_{qid}'
            path = review['run'] / 'runtime/provenance' / f'{job}.json'
            row = {'model': name, 'job': job, **dict.fromkeys(keys), 'seconds': None}
            if path.is_file():
                provenance = json.loads(path.read_text())
                assert provenance.get('job') == job and provenance.get('context_verified') is True
                assert provenance.get('model') == 'gpt-6-astra' and provenance.get('reasoning_effort') == 'medium'
                assert provenance['input_hashes'] == review['manifests'][candidate][qid]['inputs']
                raw = review['run'] / candidate / 'raw' / f'{qid}.txt'
                assert hashlib.sha256(raw.read_bytes()).hexdigest() == provenance['raw_sha256']
                row.update({key: provenance.get('usage', {}).get(key) for key in keys})
                row['seconds'] = provenance.get('seconds')
            rows.append(row)
    frame = pd.DataFrame(rows)
    summary = []
    for name, group in [('全部', frame)] + list(frame.groupby('model', sort=False)):
        observed = group.dropna(subset=['input_tokens', 'cached_input_tokens'])
        denominator = observed.input_tokens.sum(min_count=1)
        numerator = observed.cached_input_tokens.sum(min_count=1)
        result = {'范围': name, '正式收录调用': len(group), 'cache可观测调用': len(observed),
                  'cache字段缺失调用': len(group) - len(observed),
                  '可观测cached总量': numerator, '对应input总量': denominator,
                  'cache命中比例': numerator / denominator if denominator > 0 else None,
                  '累加调用秒数(可观测)': group.seconds.sum(min_count=1)}
        for key in keys:
            result[key + '合计(可观测)'] = group[key].sum(min_count=1)
            result[key + '缺失调用'] = int(group[key].isna().sum())
        summary.append(result)
    display(Markdown('**实际调用用量与缓存**：仅统计本轮最终收录的正式调用，probe 和重试未收录调用不在本表。cache 比例 = 有完整 cache/input 字段的调用的 cached 总量 ÷ 对应 input 总量，缺失不补 0；累加调用秒数不是并发批次墙钟耗时。'))
    display(pd.DataFrame(summary).round(4))


def scopes(review):
    return [('全量200', set(review['qs'])), ('新增180', set(review['qs']) - review['pilot'])]


def overall_interpretation(review):
    common = review['common']
    if not common:
        return '没有共同可计分题目，无法计算配对均分与胜负。'
    t = review['total']
    groups = {c: t[(t.candidate == c) & t.qid.isin(common)] for c in NAMES}
    means = {c: g.official.mean() for c, g in groups.items()}
    pairs = [review['paired'][qid] for qid in common]
    wtl = [sum(p['winner'] == value for p in pairs) for value in ('left', 'tie', 'right')]
    text = (f"共同可计分 **n={len(common)}**：Qwen / Bagel 的 official 均分为 "
            f"**{means['a']:.2f} / {means['b']:.2f}**，Qwen−Bagel 为 **{means['a']-means['b']:+.2f}**；"
            f"Qwen 胜 / 平 / Bagel 胜 = **{wtl[0]} / {wtl[1]} / {wtl[2]}**。")
    rates = []
    for c, name in (('a', 'Qwen'), ('b', 'Bagel')):
        g = groups[c]
        zero, full = int((g.official == 0).sum()), int((g.official == 100).sum())
        rates.append(f"{name} 的 0 分占 **{zero}/{len(g)}（{zero/len(g):.1%}）**，100 分占 **{full}/{len(g)}（{full/len(g):.1%}）**")
    return text + '\n\n' + '；'.join(rates) + '。这些数值是 rubric 得分及其分布，不是事实准确率。'


def dimension_interpretation(review):
    d = review['dim']
    common = review['common']
    if not common:
        return '没有共同可计分题目，无法比较三维。'
    ok_ids = set.intersection(*(set(d[(d.candidate == c) & (d.status == 'ok')].qid) for c in NAMES)) & common
    fragments, clamp = [], []
    for key in ('d1', 'd2', 'd3'):
        g = d[(d.dim == key) & d.qid.isin(common)]
        off = {c: g[g.candidate == c].official.mean() for c in NAMES}
        fragments.append(f"{key} **{off['a']-off['b']:+.2f}**")
        if ok_ids:
            raw_group = g[g.qid.isin(ok_ids)]
            stats = {c: raw_group[raw_group.candidate == c] for c in NAMES}
            raw_delta = stats['a'].raw_mapped.mean() - stats['b'].raw_mapped.mean()
            off_delta = stats['a'].official.mean() - stats['b'].official.mean()
            losses = {c: (v.raw_mapped - v.official).mean() for c, v in stats.items()}
            clamp.append(f"{key} 的 raw 差 **{raw_delta:+.2f}** → official 差 **{off_delta:+.2f}**（Qwen / Bagel 平均钳制损失 **{losses['a']:.2f} / {losses['b']:.2f}**）")
    text = '共同可计分题目中，三维 official 的 Qwen−Bagel 均分差为：' + '，'.join(fragments) + '。'
    if clamp:
        text += f'\n\n仅在两方 status=ok 的相同 **n={len(ok_ids)}** 题上比较钳制影响：' + '；'.join(clamp) + '。'
    else:
        text += '\n\n没有两方均为 status=ok 的题目，raw 与钳制影响无法作配对诊断。'
    return text + '\n\nraw 是各维独立落档后映射的诊断分；official 另受 d2/d3≤d1 的管线规则约束，分差变化不等同于图像质量变化。'


def type_interpretation(review, ids, minimum_n=10):
    t = review['total']
    g = t[t.qid.isin(ids & review['common'])]
    rows = []
    for edit_type, group in g.groupby('edit_type'):
        n = group.qid.nunique()
        if n >= minimum_n:
            means = {c: group[group.candidate == c].official.mean() for c in NAMES}
            rows.append((edit_type, n, means['a'], means['b'], means['a'] - means['b']))
    if not rows:
        return f'该范围没有配对 n≥{minimum_n} 的题型，逐类型分数仅列明样本量。'
    eligible = rows
    rows = sorted(eligible, key=lambda r: (-abs(r[4]), r[0]))[:3]
    counts = [sum(r[4] > 0 for r in eligible), sum(r[4] == 0 for r in eligible), sum(r[4] < 0 for r in eligible)]
    text = (f'在配对 **n≥{minimum_n}** 的 {len(eligible)} 类题型中，Qwen / 同均分 / Bagel 均值领先的题型数为 **{counts[0]} / {counts[1]} / {counts[2]}**；绝对均分差最大的 {len(rows)} 类为：' +
            '；'.join(f"**{kind}（n={n}）**，Qwen / Bagel **{a:.2f} / {b:.2f}**，差 **{delta:+.2f}**"
                     for kind, n, a, b, delta in rows) + '。')
    omitted_opposite = [r for r in eligible if r not in rows and r[4] * rows[0][4] < 0]
    if omitted_opposite:
        text += '相反方向的题型：' + '；'.join(f"**{kind}（n={n}）**，Qwen−Bagel **{delta:+.2f}**" for kind,n,a,b,delta in omitted_opposite) + '。'
    return text + '这里只描述本题集的得分差异，失败原因需结合下方 case 审阅。'


def display_overall(review):
    import pandas as pd
    from IPython.display import display, Markdown
    if review is None:
        display(Markdown('**判分尚未冻结：本区块暂不读取或汇总临时分数。**'))
        return
    t = review['total']
    rows, paired_rows = [], []
    for scope, ids in scopes(review):
        common = ids & review['common']
        for name in NAMES.values():
            all_rows = t[(t.model == name) & t.qid.isin(ids)]
            g = all_rows[all_rows.counted]
            paired = g[g.qid.isin(common)]
            rows.append({'范围': scope, '模型': name, '题数': len(all_rows),
                         '可计分': len(g), '配对分母': len(paired), 'official均分': g.official.mean(),
                         '配对official均分': paired.official.mean(), '中位数': g.official.median(),
                         '0分数': int((g.official == 0).sum()), '100分数': int((g.official == 100).sum()),
                         'model_failure': int((all_rows.status == 'model_failure').sum()),
                         'invalid_question': int((all_rows.status == 'invalid_question').sum()),
                         'judge_unscorable': int((all_rows.status == 'judge_unscorable').sum())})
        ps = [review['paired'][qid] for qid in common]
        paired_rows.append({'范围': scope, '配对分母': len(ps),
                            'Qwen胜': sum(x['winner'] == 'left' for x in ps),
                            '平': sum(x['winner'] == 'tie' for x in ps),
                            'Bagel胜': sum(x['winner'] == 'right' for x in ps),
                            'Qwen−Bagel': sum(x['delta_left_minus_right'] for x in ps) / len(ps) if ps else None})
    display(Markdown(overall_interpretation(review)))
    display(pd.DataFrame(rows).round(2))
    display(pd.DataFrame(paired_rows).round(2))
    excluded = t[~t.counted][['qid', 'model', 'status']]
    if not excluded.empty:
        display(Markdown('不可计分候选（任一方不可计分，则该题从配对比较中剔除）：'))
        display(excluded)
    display(Markdown('总分分布（计入 model_failure=0；两个模型分别列出可计分分母）：'))
    display(t[t.counted].groupby(['official', 'model']).size().unstack('model', fill_value=0))


def display_dimensions(review):
    import pandas as pd
    from IPython.display import display, Markdown
    if review is None:
        return
    d = review['dim']
    if not review['common']:
        display(Markdown('无共同可计分题目，无法比较维度。'))
        return
    rows = []
    for scope, ids in scopes(review):
        g = d[d.qid.isin(ids & review['common'])]
        for name, key in ((name, key) for name in NAMES.values() for key in ('d1', 'd2', 'd3')):
            group = g[(g.model == name) & (g.dim == key)]
            ok = group[group.status == 'ok']
            rows.append({'范围': scope, '模型': name, '维度槽位': key, '配对分母': len(group),
                         'official均分': group.official.mean(), 'raw诊断分母': len(ok),
                         'raw映射均分': ok.raw_mapped.mean(),
                         '钳制损失(ok)': (ok.raw_mapped - ok.official).mean(),
                         '钳制率(ok)': (ok.raw_mapped > ok.official).mean(),
                         'raw0数': int((ok.tier == 0).sum()), 'raw1数': int((ok.tier == 1).sum()),
                         'raw2数': int((ok.tier == 2).sum())})
    table = pd.DataFrame(rows)
    display(Markdown(dimension_interpretation(review)))
    display(table.round(3))
    delta = table.pivot(index=['范围', '维度槽位'], columns='模型', values='official均分')
    delta['Qwen−Bagel'] = delta[NAMES['a']] - delta[NAMES['b']]
    display(delta.round(2))
    display(Markdown('d1/d2/d3 仅表示每类的第 1/2/3 维；跨类型的名称和含义并不完全相同。下表按编辑类型与真实维度名称比较，raw 仅对 status=ok 作诊断。'))
    g = d[d.qid.isin(review['common'])]
    detail = g.groupby(['edit_type', 'dim', 'label', 'model']).agg(n=('qid', 'size'), official=('official', 'mean')).unstack('model')
    display(detail.round(2))
    distribution = g.groupby(['model', 'dim', 'official_tier']).size().unstack('official_tier', fill_value=0).reindex(columns=[0, 1, 2], fill_value=0)
    display(Markdown('钳后档位分布（共同可计分样本，失败计 0）：'))
    display(distribution)
    # Simple SVG bars avoid notebook frontend dependencies and font issues.
    svg = ['<svg xmlns="http://www.w3.org/2000/svg" width="780" height="235" viewBox="0 0 780 235">']
    for i, r in enumerate(table[table['范围'] == '全量200'].to_dict('records')):
        y = 12 + i * 35
        value = r['official均分']
        svg.append(f'<text x="0" y="{y+15}" font-size="12">{html.escape(r["模型"])} / {r["维度槽位"]}</text><rect x="235" y="{y}" width="{value*4 if value == value else 0}" height="23" fill="{"#3178c6" if r["模型"] == NAMES["a"] else "#df8843"}"/><text x="{245+(value*4 if value == value else 0)}" y="{y+16}" font-size="12">{value:.2f}</text>')
    svg.append('</svg>')
    from IPython.display import HTML
    display(HTML(''.join(svg)))


def display_strata(review):
    from IPython.display import display, Markdown
    if review is None:
        return
    t = review['total']
    for scope, ids in scopes(review):
        display(Markdown(f'**{scope}：共同可计分样本分层**'))
        g = t[t.qid.isin(ids & review['common'])]
        if g.empty:
            display(Markdown('该范围没有共同可计分题目。'))
            continue
        display(Markdown(type_interpretation(review, ids)))
        for field in ('edit_type', 'level', 'suite', 'construction_profile'):
            display(g.fillna({field: 'unknown'}).groupby([field, 'model']).agg(n=('qid', 'size'), official=('official', 'mean')).unstack('model').round(2))


def case_html(review, qid, image_size=1000, quality=88):
    from PIL import Image, ImageOps
    q = review['qs'][qid]
    delta = review['paired'].get(qid, {}).get('delta_left_minus_right')
    delta_text = f'{delta:+.2f}' if delta is not None else '不可配对'
    esc = lambda x: html.escape(str(x))
    def picture(path, label):
        with Image.open(path) as im:
            im = ImageOps.exif_transpose(im).convert('RGB')
            im.thumbnail((image_size, image_size))
            stream = io.BytesIO()
            im.save(stream, format='JPEG', quality=quality)
        data = base64.b64encode(stream.getvalue()).decode()
        return f'<div style="flex:1;min-width:220px"><b>{esc(label)}</b><br><img alt="{esc(label)}" style="width:auto;height:auto;max-width:100%;max-height:550px;object-fit:contain" src="data:image/jpeg;base64,{data}"></div>'
    out = [f'<h3>{esc(qid)} · {esc(q["edit_type"])} · Δ(Qwen−Bagel) {esc(delta_text)}</h3><p style="white-space:pre-wrap">{esc(q["edit_instruction"])}</p><div style="display:flex;gap:12px;flex-wrap:wrap">']
    out.append(picture(review['manifests']['a'][qid]['before'], 'BEFORE'))
    for c, name in NAMES.items():
        out.append(picture(review['manifests'][c][qid]['after'], name))
    out.append('</div>')
    for c, name in NAMES.items():
        s = review['scores'][c][qid]
        out.append(f'<h4>{esc(name)} · official {s["official_total"]:.2f} · {esc(s["validity"]["status"])}</h4><p>{esc(s["validity"].get("detail", ""))}</p><table style="width:100%;text-align:left"><tr><th>维度</th><th>raw档位 / 映射</th><th>official</th><th>理由</th></tr>')
        for d in s['raw_dimensions']:
            out.append(f'<tr><td>{esc(d["key"])} {esc(d["label"])}</td><td>{d["tier"]} / {d["mapped"]}</td><td>{s["official_dimensions"][d["key"]]}</td><td style="white-space:pre-wrap">{esc(d["reason"])}</td></tr>')
        out.append('</table><details><summary>判官观察</summary><pre style="white-space:pre-wrap">' + esc(json.dumps(s.get('observations', {}), ensure_ascii=False, indent=2)) + '</pre></details>')
    return ''.join(out)


def display_case_native(review, qid):
    """Persist ordinary PNG and Markdown outputs; no live widget is required."""
    import matplotlib.pyplot as plt
    from PIL import Image, ImageOps
    from IPython.display import display, Markdown, Image as NotebookImage
    q = review['qs'][qid]
    display(Markdown(f"### {qid} · {q['edit_type']}\n\n{q['edit_instruction']}"))
    paths = [('BEFORE', review['manifests']['a'][qid]['before'])] + [
        (name, review['manifests'][c][qid]['after']) for c, name in NAMES.items()]
    fig, axes = plt.subplots(1, len(paths), figsize=(15, 5), dpi=110)
    for ax, (label, path) in zip(axes, paths):
        with Image.open(path) as source:
            im = ImageOps.exif_transpose(source).convert('RGB')
            im.thumbnail((1200, 1200))
            ax.imshow(im)
        ax.set_title(label, fontsize=11)
        ax.axis('off')
        ax.set_aspect('equal')
    fig.tight_layout()
    buffer = io.BytesIO()
    fig.savefig(buffer, format='png', bbox_inches='tight')
    plt.close(fig)
    display(NotebookImage(data=buffer.getvalue()))
    for c, name in NAMES.items():
        score = review['scores'][c][qid]
        display(Markdown(f"**{name} · 总分 {score['official_total']:.2f} · {score['validity']['status']}**"))
        for d in score['raw_dimensions']:
            display(Markdown(f"**{d['label']}** · raw {d['tier']} / 映射 {d['mapped']} / official {score['official_dimensions'][d['key']]}\n\n{d['reason']}"))
        if score['validity'].get('detail'):
            display(Markdown(score['validity']['detail']))
        display(Markdown('判官观察：\n\n```json\n' + json.dumps(score.get('observations', {}), ensure_ascii=False, indent=2) + '\n```'))


def sample_cases(review, edit_type='全部', winner='全部', cohort='全量200', n=3, seed=42, qids=None, min_abs_delta=0, sort_by_abs_delta=False):
    """Deterministic sampling; explicit qids also shows excluded/unscorable cases."""
    if review is None:
        raise RuntimeError('正式分数未加载，请先运行初始化单元格并检查冻结状态。')
    if qids is None:
        ids = set(review['qs'])
        if cohort == '新增180':
            ids -= review['pilot']
        if edit_type != '全部':
            ids = {qid for qid in ids if review['qs'][qid]['edit_type'] == edit_type}
        if winner != '全部':
            ids = {qid for qid in ids if review['paired'].get(qid, {}).get('winner', 'excluded') == winner}
        if min_abs_delta < 0:
            raise ValueError('min_abs_delta must be nonnegative')
        if min_abs_delta > 0:
            ids = {qid for qid in ids if abs(review['paired'].get(qid, {}).get('delta_left_minus_right', 0)) >= min_abs_delta}
        if sort_by_abs_delta:
            qids = sorted(ids, key=lambda qid: (-abs(review['paired'].get(qid, {}).get('delta_left_minus_right', 0)), qid))[:n]
        else:
            qids = random.Random(seed).sample(sorted(ids), min(n, len(ids)))
    for qid in qids:
        if qid not in review['qs']:
            raise ValueError(f'Unknown qid: {qid}')
        display_case_native(review, qid)
    return qids


def display_case_browser(review):
    from IPython.display import display, HTML
    if review is None:
        return
    rows = []
    for qid in sorted(review['qs']):
        p = review['paired'].get(qid, {})
        rows.append(f'<tr><td>{html.escape(qid)}</td><td>{html.escape(review["qs"][qid]["edit_type"])}</td><td>{p.get("left_total", "excluded")}</td><td>{p.get("right_total", "excluded")}</td><td>{p.get("winner", "excluded")}</td></tr>')
    display(HTML('<details><summary>展开全量 200 题索引（图片按需抽样加载）</summary><table><tr><th>qid</th><th>edit_type</th><th>Qwen</th><th>Bagel</th><th>winner</th></tr>' + ''.join(rows) + '</table></details>'))
    try:
        import ipywidgets as w
    except ImportError:
        print("未安装 ipywidgets；运行 sample_cases(B200, edit_type='add', n=3, seed=42) 或传 qids=['…']。")
        return
    typ = w.Dropdown(options=['全部'] + sorted({q['edit_type'] for q in review['qs'].values()}), description='编辑类型')
    win = w.Dropdown(options=[('全部','全部'),('Qwen胜','left'),('平','tie'),('Bagel胜','right'),('不可配对','excluded')], description='胜负')
    cohort = w.Dropdown(options=['全量200', '新增180'], description='范围')
    n = w.BoundedIntText(value=3, min=1, max=20, description='数量')
    seed = w.IntText(value=42, description='随机种子')
    qids = w.Text(value='', placeholder='可直接输入 qid，多个用逗号分隔', description='指定题号')
    minimum = w.BoundedFloatText(value=0, min=0, max=100, description='最小|Δ|')
    descending = w.Checkbox(value=False, description='按|Δ|降序选取')
    button, out = w.Button(description='抽样查看'), w.Output()
    def click(_):
        with out:
            out.clear_output(wait=True)
            requested = [x.strip() for x in qids.value.split(',') if x.strip()]
            sample_cases(review, typ.value, win.value, cohort.value, n.value, seed.value, requested or None, minimum.value, descending.value)
    button.on_click(click)
    display(w.VBox([w.HBox([typ, win, cohort]), w.HBox([n, seed]), w.HBox([minimum, descending]), qids, button, out]))


def install_notebook(path):
    path = Path(path)
    original = path.read_bytes()
    nb = json.loads(original)
    def cell(kind, source, key):
        obj = dict(cell_type=kind, metadata={'tags': [TAG]}, id=f'b200-{key}', source=source.splitlines(True))
        if kind == 'code':
            obj.update(execution_count=None, outputs=[])
        return obj
    cells = [cell('markdown', '''# edit bench200 · Qwen / BAGEL 正式判分

固定判官 **gpt-6-astra / medium**，修订版 **QIB v2.2**；下方先整体、再维度与分层，最后抽样看 case。
仅在本轮 `runtime/frozen.json` 存在且 400 份分数、题图绑定、配对分母全部验证通过后展示。
主分使用三档映射 0/60/100 与 d2、d3 ≤ d1 钳制；raw 是钳制前诊断，不能替代主分。
`model_failure` 计 0；任一方 `invalid_question` / `judge_unscorable` 均从配对比较剔除并单列。
历史章节原样保留在本轮区块之后；本轮使用独立变量，不混合旧结果。
''', 'intro'), cell('code', '''from pathlib import Path
import sys
_edit_candidates = [Path.cwd(), *Path.cwd().parents]
_REPO_B200 = next(p for p in _edit_candidates if (p / 'benchmark/edit/review_bench200.py').is_file())
if str(_REPO_B200) not in sys.path:
    sys.path.insert(0, str(_REPO_B200))
from benchmark.edit.review_bench200 import load_review, display_overall, display_dimensions, display_strata, display_case_browser, display_usage, sample_cases
B200 = load_review()
display_overall(B200)
''', 'overall'), cell('markdown', '## 三维对比与钳制诊断\n三维槽位的整体平均只作总览，真实维度名称见分型表；两模型对比使用共同可计分题目。', 'dims-text'), cell('code', 'display_dimensions(B200)\n', 'dims'), cell('markdown', '## 分层得分\n全量 200 与新增 180 分列；level 是 T2I 参考层级，不能当作编辑实测难度。', 'strata-text'), cell('code', 'display_strata(B200)\n', 'strata'), cell('markdown', '''## 抽样看 case
默认不展开图片；可按编辑类型、胜负、最小绝对总分差、全量/新增筛选并固定随机种子，也可按绝对分差降序选取或直接输入题号。
控件需要正在运行的 notebook 内核；也可运行 `sample_cases(B200, edit_type='add', n=3, seed=42, min_abs_delta=60, sort_by_abs_delta=True)`。
''', 'cases-text'), cell('code', 'sample_cases(B200, n=3, seed=42)\n', 'cases'), cell('code', 'display_usage(B200)\n', 'usage'), cell('markdown', '---\n# 历史 pilot 分析与协议复核\n以下为此前保留章节，模型和判官配置以各节说明为准。', 'history')]
    history = [c for c in nb['cells'] if TAG not in c.get('metadata', {}).get('tags', [])]
    nb['cells'] = cells + history
    if path.read_bytes() != original:
        raise RuntimeError('Notebook changed concurrently; retry against latest file')
    tmp = path.with_suffix('.ipynb.tmp')
    tmp.write_text(json.dumps(nb, ensure_ascii=False, indent=1) + '\n')
    tmp.replace(path)
    print(f'Installed {len(cells)} current-run cells; retained {len(history)} historical cells: {path}')



def export_html(path, review=None):
    """Portable static review: summary first, case images decoded on demand."""
    from IPython.core.interactiveshell import InteractiveShell
    from IPython.utils.capture import capture_output
    review = review or load_review()
    if review is None:
        raise RuntimeError('Cannot export unfinished scoring run')
    shell = InteractiveShell.instance()
    shell.user_ns.update(_b200_html_review=review, _b200_overall=display_overall,
                         _b200_dimensions=display_dimensions, _b200_strata=display_strata,
                         _b200_usage=display_usage)
    chunks = []
    for title, command in [('整体得分', '_b200_overall'), ('维度对比', '_b200_dimensions'),
                           ('分层统计', '_b200_strata'), ('实际用量与缓存', '_b200_usage')]:
        with capture_output() as capture:
            result = shell.run_cell(f'{command}(_b200_html_review)')
        if not result.success:
            raise RuntimeError(result.error_in_exec or result.error_before_exec)
        chunks.append('<section><h2>' + title + '</h2>')
        for output in capture.outputs:
            data = output.data
            if 'text/html' in data:
                chunks.append(data['text/html'])
            elif 'text/markdown' in data:
                # Render only trusted analysis text; user strings are separately escaped.
                text = html.escape(data['text/markdown'])
                import re
                text = re.sub(r'\*\*(.*?)\*\*', r'<strong>\1</strong>', text)
                chunks.append('<p>' + text + '</p>')
            elif 'text/plain' in data:
                chunks.append('<pre>' + html.escape(data['text/plain']) + '</pre>')
        if capture.stdout:
            chunks.append('<pre>' + html.escape(capture.stdout) + '</pre>')
        chunks.append('</section>')
    cases = []
    for qid in sorted(review['qs']):
        p = review['paired'].get(qid, {})
        cases.append(dict(qid=qid, edit_type=review['qs'][qid]['edit_type'],
                          cohort='pilot20' if qid in review['pilot'] else '新增180',
                          winner=p.get('winner', 'excluded'), left=p.get('left_total'), right=p.get('right_total'), delta=p.get('delta_left_minus_right'),
                          body=case_html(review, qid, image_size=800, quality=78)))
    payload = json.dumps(cases, ensure_ascii=False).replace('<', '\\u003c')
    page = '''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>edit bench200 · 正式判分审阅</title>
<style>
body{margin:0;background:#f5f7fa;color:#182333;font:15px/1.65 system-ui,-apple-system,sans-serif}main{max-width:1440px;margin:auto;padding:36px}h1{margin-bottom:8px}h2{font-size:23px}h3{font-size:19px}section{background:white;border:1px solid #dce2e9;border-radius:12px;padding:24px;margin:24px 0;overflow:auto}table{border-collapse:collapse;font-size:13px;max-width:100%;margin:14px 0}th,td{border:1px solid #dce2e9;padding:7px 10px;vertical-align:top}th{background:#edf2f8}tr:nth-child(even){background:#fafbfd}pre{white-space:pre-wrap}button,select,input{padding:9px 11px;margin:5px;border:1px solid #acb9c9;border-radius:6px;font:inherit}button{background:#2467ad;color:white;cursor:pointer}.controls{display:flex;flex-wrap:wrap;align-items:center}.muted{color:#5d6c80}details{border:1px solid #dce2e9;border-radius:8px;padding:12px;margin:10px 0}summary{cursor:pointer;font-weight:600}img{background:#f3f3f3}svg{max-width:100%;height:auto}#cases details[open]{background:#fff}
</style><main><h1>edit bench200 · 正式判分审阅</h1><p>Qwen-Image-Edit-2511 × BAGEL-7B-MoT · gpt-6-astra / medium · 修订 QIB v2.2</p>
<p class="muted">主分：三档 0/60/100，d2/d3 ≤ d1 后三维平均；raw 单独作诊断。失败计 0；无效题与不可判项成对剔除。此文件自包含，可离线打开；图片为适当缩小的审阅副本，原始图在远端 notebook 对应输入路径。</p>'''
    page += ''.join(chunks)
    page += '''<section><h2>抽样看 case</h2><p>先选择范围、编辑类型与胜负，再固定随机种子抽样；也可搜索题号。图片只在展开对应 case 时装入页面。</p><div class="controls">
<select id="cohort"><option value="all">全量 200</option><option value="新增180">新增 180</option><option value="pilot20">pilot 20</option></select>
<select id="type"><option value="all">全部编辑类型</option></select>
<select id="winner"><option value="all">全部胜负</option><option value="left">Qwen 胜</option><option value="tie">平</option><option value="right">Bagel 胜</option><option value="excluded">不可配对</option></select>
<input id="qid" placeholder="搜索题号" aria-label="搜索题号"><label>数量<input id="count" type="number" min="1" max="20" value="3" style="width:60px"></label><label>种子<input id="seed" type="number" value="42" style="width:75px"></label>
<label>最小|Δ|<input id="minimum" type="number" min="0" max="100" value="0" style="width:65px"></label><button id="sample">抽样</button><button id="list">列出筛选结果</button><button id="largest">按|Δ|降序列出</button></div><p id="status" class="muted"></p><div id="cases"></div></section></main><script id="case-data" type="application/json">'''
    page += payload + '''</script><script>
const data=JSON.parse(document.getElementById('case-data').textContent), el=id=>document.getElementById(id);
for(const t of [...new Set(data.map(x=>x.edit_type))].sort()){const o=document.createElement('option');o.value=t;o.textContent=t;el('type').appendChild(o)}
function pool(){return data.filter(x=>(el('cohort').value==='all'||x.cohort===el('cohort').value)&&(el('type').value==='all'||x.edit_type===el('type').value)&&(el('winner').value==='all'||x.winner===el('winner').value)&&x.qid.toLowerCase().includes(el('qid').value.trim().toLowerCase())&&((Number(el('minimum').value)||0)<=0||(x.delta!=null&&Math.abs(x.delta)>=Number(el('minimum').value))))}
function seeded(seed){return ()=>{let t=seed+=0x6D2B79F5;t=Math.imul(t^t>>>15,t|1);t^=t+Math.imul(t^t>>>7,t|61);return ((t^t>>>14)>>>0)/4294967296}}
function show(mode){let rows=pool(),n=rows.length;if(mode==='sample'){const rng=seeded(Number(el('seed').value)||0);rows=[...rows];for(let i=rows.length-1;i>0;i--){const j=Math.floor(rng()*(i+1));[rows[i],rows[j]]=[rows[j],rows[i]]}rows=rows.slice(0,Math.min(20,Math.max(1,Number(el('count').value)||3)))}else if(mode==='largest'){rows=[...rows].sort((a,b)=>Math.abs(b.delta??0)-Math.abs(a.delta??0)||a.qid.localeCompare(b.qid))}el('cases').replaceChildren();el('status').textContent=`匹配 ${n} 题，当前列出 ${rows.length} 题；点击题号展开。`;for(const x of rows){const d=document.createElement('details'),s=document.createElement('summary'),body=document.createElement('div');s.textContent=`${x.qid} · ${x.edit_type} · Qwen ${x.left??'不可配对'} / Bagel ${x.right??'不可配对'} · Δ ${x.delta==null?'—':(x.delta>=0?'+':'')+x.delta.toFixed(2)}`;d.append(s,body);d.addEventListener('toggle',()=>{if(d.open&&!body.dataset.loaded){body.innerHTML=x.body;body.dataset.loaded='1'}});el('cases').appendChild(d)}}
el('sample').onclick=()=>show('sample');el('list').onclick=()=>show('list');el('largest').onclick=()=>show('largest');for(const id of ['cohort','type','winner','minimum'])el(id).onchange=()=>{el('status').textContent=`匹配 ${pool().length} 题，点击抽样或列出。`};el('qid').oninput=()=>{el('status').textContent=`匹配 ${pool().length} 题，点击抽样或列出。`};show('sample');
</script></html>'''
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(page)
    print(f'Exported portable HTML ({path.stat().st_size / 1024 / 1024:.1f} MiB): {path}')


def execute_current_notebook(path):
    """Execute only this run's cells and retain historical sources/outputs byte-for-byte in JSON values."""
    from IPython.core.interactiveshell import InteractiveShell
    from IPython.utils.capture import capture_output
    path = Path(path)
    original = path.read_bytes()
    nb = json.loads(original)
    shell = InteractiveShell.instance()
    for c in nb['cells']:
        if c['cell_type'] != 'code' or TAG not in c.get('metadata', {}).get('tags', []):
            continue
        with capture_output() as captured:
            result = shell.run_cell(''.join(c['source']), store_history=True)
        if not result.success:
            raise RuntimeError(f"Notebook cell {c.get('id')} failed: {result.error_before_exec or result.error_in_exec}")
        outputs = []
        for name in ('stdout', 'stderr'):
            text = getattr(captured, name)
            if text:
                outputs.append(dict(output_type='stream', name=name, text=text.splitlines(True)))
        for output in captured.outputs:
            outputs.append(dict(output_type='display_data', data=output.data, metadata=output.metadata))
        c.update(outputs=outputs, execution_count=result.execution_count)
    if path.read_bytes() != original:
        raise RuntimeError('Notebook changed concurrently; retry execution')
    temp = path.with_suffix('.ipynb.tmp')
    temp.write_text(json.dumps(nb, ensure_ascii=False, indent=1) + '\n')
    temp.replace(path)
    print(f'Executed current-run cells only: {path}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--notebook', type=Path, default=EDIT_DIR / 'reviews/results_review_v61.ipynb')
    parser.add_argument('--execute', action='store_true', help='Execute current cells only; keep historical outputs')
    parser.add_argument('--export-html', type=Path, help='Export frozen run to a portable HTML review')
    args = parser.parse_args()
    if args.export_html:
        export_html(args.export_html)
    elif args.execute:
        execute_current_notebook(args.notebook)
    else:
        install_notebook(args.notebook)
