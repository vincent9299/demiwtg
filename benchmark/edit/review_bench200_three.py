#!/usr/bin/env python3
"""Notebook-only review of frozen Qwen, BAGEL and Gemini edit bench200 scores."""
from __future__ import annotations
import argparse
import hashlib
import itertools
import json
import random
import sys
from pathlib import Path

EDIT_DIR = Path(__file__).resolve().parent
REPO = EDIT_DIR.parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
from benchmark.edit import review_bench200 as previous
from benchmark.edit.eval_codex_score import EDIT_DIMS, PHI

NAMES = {'a': 'Qwen-Image-Edit-2511', 'b': 'BAGEL-7B-MoT', 'g': 'Gemini 3.1 Flash Image'}
TAG = 'bench200-three-model-qib-v22-astra-medium'


def _check_hash(path, expected):
    path = Path(path)
    if not isinstance(expected, str) or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
        raise ValueError(f'Frozen file changed or hash missing: {path}')


def verify_gemini_inputs(run, candidate, qs, manifest, scores, old):
    """Check per-question materialization and actual files beyond the freeze summary."""
    from benchmark.edit.eval_codex_score import load_template, _extract_json
    template_path = EDIT_DIR / 'prompts/judge_prompt_edit_qib_v2.2.md'
    mode, template, blocks = load_template(argparse.Namespace(template=template_path))
    if mode != 'qib':
        raise ValueError('Gemini requires the frozen QIB template')
    index = previous.read_jsonl(candidate / 'prompts/index.jsonl')
    if not set(qs) <= set(index) <= set(old['qs']):
        raise ValueError('Gemini prompt index does not cover the selected qids')
    old_threads, old_sessions = set(), set()
    for c in ('a', 'b'):
        for qid in old['qs']:
            path = old['run'] / 'runtime/provenance' / f'{c}_{qid}.json'
            provenance = json.loads(path.read_text())
            old_threads.add(provenance['thread_id'])
            old_sessions.add(str(Path(provenance['session_path']).resolve()))
    threads, sessions, provenance_rows = set(), set(), []
    for qid, q in qs.items():
        m, score, entry = manifest[qid], scores[qid], index[qid]
        job = f'{candidate.name}_{qid}'
        provenance = json.loads((run / 'runtime/provenance' / f'{job}.json').read_text())
        for key, expected in (('job', job), ('model', 'gpt-6-astra'), ('reasoning_effort', 'medium'),
                              ('context_verified', True), ('tool_calls', 0), ('provider', 'openai')):
            if provenance.get(key) != expected:
                raise ValueError(f'Gemini provenance mismatch: {qid}/{key}')
        thread = provenance.get('thread_id')
        session = str(Path(provenance.get('session_path', '')).resolve())
        if (not thread or thread in threads or thread in old_threads
                or session in sessions or session in old_sessions):
            raise ValueError(f'Reused or missing judge context: {qid}')
        _check_hash(Path(session), provenance.get('session_sha256'))
        threads.add(thread); sessions.add(session)
        if provenance.get('input_hashes') != m['inputs']:
            raise ValueError(f'Gemini provenance input hashes mismatch: {qid}')
        _check_hash(Path(m['before']), m['inputs']['source_sha256'])
        _check_hash(Path(m['after']), m['inputs']['output_sha256'])
        instruction_sha = hashlib.sha256(q['edit_instruction'].encode()).hexdigest()
        if instruction_sha != m['inputs']['instruction_sha256'] or entry['instruction_sha256'] != instruction_sha:
            raise ValueError(f'Gemini instruction hash mismatch: {qid}')
        expected_prompt = (template.replace('{{EDIT_TYPE}}', q['edit_type'])
                           .replace('{{TYPE_NOTES}}', blocks[q['edit_type']].strip())
                           .replace('{{INSTRUCTION}}', q['edit_instruction']))
        prompt_path = candidate / 'prompts' / f'{qid}.txt'
        expected_prompt_sha = hashlib.sha256(expected_prompt.encode()).hexdigest()
        _check_hash(prompt_path, expected_prompt_sha)
        if entry['prompt_sha256'] != expected_prompt_sha or provenance.get('prompt_sha256') != expected_prompt_sha:
            raise ValueError(f'Gemini prompt provenance mismatch: {qid}')
        if entry.get('candidate_id') != m['candidate_id'] or score.get('candidate_id') != m['candidate_id']:
            raise ValueError(f'Gemini candidate binding mismatch: {qid}')
        raw_path = candidate / 'raw' / f'{qid}.txt'
        _check_hash(raw_path, provenance.get('raw_sha256'))
        raw = _extract_json(raw_path.read_text())
        if raw.get('validity') != score.get('validity'):
            raise ValueError(f'Raw/score validity mismatch: {qid}')
        raw_dims = raw.get('raw_dimensions', [])
        if len(raw_dims) != 3:
            raise ValueError(f'Raw dimension count mismatch: {qid}')
        for raw_dim, scored_dim in zip(raw_dims, score['raw_dimensions']):
            if (raw_dim.get('label') != scored_dim['label'] or raw_dim.get('tier') != scored_dim['tier']
                    or str(raw_dim.get('reason', '')).strip() != scored_dim['reason']):
                raise ValueError(f'Raw/score dimension mismatch: {qid}')
        provenance_rows.append(provenance)
    if len(threads) != len(qs) or len(sessions) != len(qs):
        raise ValueError('Gemini requires one distinct fresh session per scored qid')
    return provenance_rows


def load_three_review(gemini_run, candidate_subdir='g', allow_partial=False):
    """Require freezing by default; explicit partial review still verifies every available judgment."""
    import pandas as pd
    run = Path(gemini_run).resolve()
    frozen_path = run / 'runtime/frozen.json'
    is_partial = not frozen_path.is_file()
    if is_partial and not allow_partial:
        return None
    old = previous.load_review()
    if old is None:
        return None
    candidate = (run / candidate_subdir).resolve()
    if not candidate.is_relative_to(run):
        raise ValueError('Candidate directory must be inside the Gemini run')
    frozen = None
    required = {str((candidate / name).relative_to(run))
                for name in ('scores.jsonl', 'report.json', 'blind_manifest.jsonl')}
    if not is_partial:
        frozen = json.loads(frozen_path.read_text())
        expected = dict(n_questions=200, n_candidates=200, n_unique_contexts=200,
                        judge_model='gpt-6-astra', reasoning_effort='medium')
        for key, value in expected.items():
            if frozen.get(key) != value:
                raise ValueError(f'Gemini frozen configuration mismatch: {key}')
        artifacts = frozen.get('artifacts', {})
        if not isinstance(artifacts, dict) or not required <= set(artifacts):
            raise ValueError('Gemini frozen artifact map is incomplete')
        for relative, expected_hash in artifacts.items():
            path = (run / relative).resolve()
            if Path(relative).is_absolute() or not path.is_relative_to(run):
                raise ValueError(f'Invalid frozen path: {relative}')
            _check_hash(path, expected_hash)
        _check_hash(EDIT_DIR / 'bench200/questions.jsonl', frozen.get('questions_sha256'))
        _check_hash(EDIT_DIR / 'prompts/judge_prompt_edit_qib_v2.2.md', frozen.get('template_sha256'))
        _check_hash(EDIT_DIR / 'eval_codex_score.py', frozen.get('pipeline_sha256'))
    # This is an in-memory read snapshot, not a frozen.json or a declaration of completion.
    read_snapshot = {relative: hashlib.sha256((run / relative).read_bytes()).hexdigest()
                     for relative in required | {str((candidate / 'prompts/index.jsonl').relative_to(run))}}
    scores = previous.read_jsonl(candidate / 'scores.jsonl')
    manifest = previous.read_jsonl(candidate / 'blind_manifest.jsonl')
    qs = old['qs']
    if not scores or not set(scores) <= set(manifest) <= set(qs):
        raise ValueError('Gemini scores/manifest do not match benchmark qids')
    if not is_partial and (set(scores) != set(qs) or set(manifest) != set(qs)):
        raise ValueError('Frozen Gemini requires all 200 qids')
    report = json.loads((candidate / 'report.json').read_text())
    if report.get('n') != len(scores):
        raise ValueError('Gemini report count differs from scored qids')
    provenance = verify_gemini_inputs(run, candidate, {qid: qs[qid] for qid in scores}, manifest, scores, old)
    totals, dimensions = [], []
    for qid, score in scores.items():
        q, m = qs[qid], manifest[qid]
        if score.get('schema') != 'edit-codex-v2-qib' or score.get('edit_type') != q['edit_type']:
            raise ValueError(f'Score schema/edit type mismatch: {qid}')
        if score.get('inputs') != m.get('inputs') or m.get('edit_instruction') != q['edit_instruction']:
            raise ValueError(f'Gemini question/image binding mismatch: {qid}')
        for key in ('source_sha256', 'instruction_sha256'):
            if m['inputs'][key] != old['manifests']['a'][qid]['inputs'][key]:
                raise ValueError(f'Cross-model input mismatch: {qid}/{key}')
        raw = score['raw_dimensions']
        if [d['label'] for d in raw] != EDIT_DIMS[q['edit_type']]:
            raise ValueError(f'Dimension contract mismatch: {qid}')
        tiers = [d['tier'] for d in raw]
        if len(tiers) != 3 or any(t not in PHI for t in tiers):
            raise ValueError(f'Invalid tiers: {qid}')
        status = score['validity']['status']
        if status not in ('ok', 'model_failure', 'invalid_question', 'judge_unscorable'):
            raise ValueError(f'Invalid validity status: {qid}')
        official_tiers = [tiers[0], min(tiers[0], tiers[1]), min(tiers[0], tiers[2])]
        if status == 'model_failure':
            official_tiers = [0, 0, 0]
        official = [PHI[t] for t in official_tiers]
        if ([score['official_dimensions'][f'd{i}'] for i in (1, 2, 3)] != official
                or [score['official_tiers'][f'd{i}'] for i in (1, 2, 3)] != official_tiers
                or abs(score['official_total'] - sum(official) / 3) >= .001):
            raise ValueError(f'Official mapping mismatch: {qid}')
        base = dict(**q, model=NAMES['g'], candidate='g',
                    cohort='pilot20' if qid in old['pilot'] else '新增180',
                    status=status, counted=status in ('ok', 'model_failure'))
        totals.append(dict(**base, official=score['official_total'], raw_mapped=sum(PHI[t] for t in tiers)/3,
                           clamped=any(t > tiers[0] for t in tiers[1:]) if status == 'ok' else False))
        for i, d in enumerate(raw, 1):
            dimensions.append(dict(**base, dim=f'd{i}', label=d['label'], tier=d['tier'],
                                   official_tier=official_tiers[i-1], raw_mapped=PHI[d['tier']], official=official[i-1]))
    total = pd.concat([old['total'], pd.DataFrame(totals)], ignore_index=True)
    dim = pd.concat([old['dim'], pd.DataFrame(dimensions)], ignore_index=True)
    common = set.intersection(*(set(total[(total.candidate == c) & total.counted].qid) for c in NAMES))
    for relative, expected_hash in read_snapshot.items():
        _check_hash(run / relative, expected_hash)
    return dict(qs=qs, pilot=old['pilot'], total=total, dim=dim, common=common,
                partial=is_partial, missing_gemini=set(qs)-set(scores), read_snapshot=read_snapshot,
                scores={**old['scores'], 'g': scores}, manifests={**old['manifests'], 'g': manifest},
                runs={'a': old['run'], 'b': old['run'], 'g': run},
                candidate_dirs={'a': old['run']/'a', 'b': old['run']/'b', 'g': candidate},
                frozen={'old': old['frozen'], 'gemini': frozen}, gemini_provenance=provenance)


def scopes(review):
    return [('全量200', set(review['qs']))]


def pair_rows(review, left, right, ids=None):
    """Each pair uses its own eligible intersection; raw diagnostics use shared ok cases."""
    ids = set(review['qs']) if ids is None else set(ids)
    rows = []
    for qid in sorted(ids):
        if qid not in review['scores'][left] or qid not in review['scores'][right]:
            continue
        l, r = review['scores'][left][qid], review['scores'][right][qid]
        if l['validity']['status'] not in ('ok','model_failure') or r['validity']['status'] not in ('ok','model_failure'):
            continue
        a, b = float(l['official_total']), float(r['official_total'])
        rows.append(dict(qid=qid, left=a, right=b, delta=a-b,
                         winner='left' if a>b else 'right' if b>a else 'tie'))
    return rows


def overall_interpretation(review):
    t = review['total'][review['total'].qid.isin(review['common'])]
    if t.empty:
        return '三模型没有共同可计分题目，不能比较三方整体均分；两两比较另列各自分母。'
    means = t.groupby('model').official.mean().sort_values(ascending=False)
    text = (f"三模型共同可计分 **n={len(review['common'])}**，official 均分依次为：" +
            '；'.join(f'**{name} {value:.2f}**' for name,value in means.items()) + '。')
    rates=[]
    for c,name in NAMES.items():
        g=t[t.candidate==c];zero=int((g.official==0).sum());full=int((g.official==100).sum())
        rates.append(f"{name} 的0分 **{zero}/{len(g)}（{zero/len(g):.1%}）**，100分 **{full}/{len(g)}（{full/len(g):.1%}）**")
    return text+'\n\n'+'；'.join(rates)+'。这些数值是 rubric 得分及其分布，不是事实准确率。'


def dimension_interpretation(review):
    d=review['dim'];common=review['common']
    if not common:return '三方共同分母为空，不能比较三维。'
    ok_ids=set.intersection(*(set(d[(d.candidate==c)&(d.status=='ok')].qid) for c in NAMES)) & common
    lines=[]
    for reference in ('a','b'):
        official,raw,clamped_ok=[],[],[]
        for key in ('d1','d2','d3'):
            g=d[(d.dim==key)&d.qid.isin(common)]
            delta=g[g.candidate=='g'].official.mean()-g[g.candidate==reference].official.mean()
            official.append(f'{key} {delta:+.2f}')
            if ok_ids:
                ok=g[g.qid.isin(ok_ids)]
                raw.append(f"{key} {ok[ok.candidate=='g'].raw_mapped.mean()-ok[ok.candidate==reference].raw_mapped.mean():+.2f}")
                clamped_ok.append(f"{key} {ok[ok.candidate=='g'].official.mean()-ok[ok.candidate==reference].official.mean():+.2f}")
        text=f"Gemini−{NAMES[reference]} 的 official 三维差（n={len(common)}）：**{', '.join(official)}**。"
        if raw:
            text+=f"三方均 status=ok 的相同 n={len(ok_ids)} 题中，raw 差为 **{', '.join(raw)}**，钳后差为 **{', '.join(clamped_ok)}**。"
        lines.append(text)
    return '\n\n'.join(lines)+'\n\nraw 与 official 的差异由评分映射和钳制口径区分；不能把接近的 official 分差直接解释为三维能力提升相同。'


def type_interpretation(review,ids,minimum_n=10):
    g=review['total'][review['total'].qid.isin(ids & review['common'])]
    eligible=[]
    for kind,group in g.groupby('edit_type'):
        n=group.qid.nunique()
        if n>=minimum_n:
            eligible.append((kind,n,{c:group[group.candidate==c].official.mean() for c in NAMES}))
    if not eligible:return f'该范围没有三方共同 n≥{minimum_n} 的题型，表中仅列明各组样本量与得分。'
    leaders={c:[] for c in NAMES}; tied=[]
    for kind,n,means in eligible:
        winners=[c for c in NAMES if abs(means[c]-max(means.values()))<1e-9]
        if len(winners)==1:leaders[winners[0]].append(kind)
        else:tied.append(kind)
    leader_text='在这些题型中，均分最高的模型分布：'+'；'.join(f'{NAMES[c]} {len(kinds)}类（'+', '.join(kinds)+'）' for c,kinds in leaders.items() if kinds)
    if tied:leader_text+='；并列最高：'+', '.join(tied)
    lines=[leader_text+'。']
    for reference in ('a','b'):
        rows=[(kind,n,means['g']-means[reference]) for kind,n,means in eligible]
        wins=sum(delta>0 for _,_,delta in rows);ties=sum(delta==0 for _,_,delta in rows);losses=sum(delta<0 for _,_,delta in rows)
        main=sorted(rows,key=lambda x:(-abs(x[2]),x[0]))[:3]
        lines.append(f"在三方共同 n≥{minimum_n} 的 {len(rows)} 类题型中，Gemini 相对 {NAMES[reference]} 的均值领先/相同/落后类型数为 **{wins}/{ties}/{losses}**；绝对差较大的题型为："+'；'.join(f"**{kind}（n={n}，Gemini−对方 {delta:+.2f}）**" for kind,n,delta in main)+'。')
    return '\n\n'.join(lines)+'\n\n这里只描述已完成题集的得分差，具体图像失败原因留待 case 核对。'


def show_overall(review):
    import pandas as pd
    from IPython.display import display, Markdown
    if review is None:
        display(Markdown('**Gemini 正式判分尚未冻结：不读取或汇总临时成绩。**'))
        return
    t, rows = review['total'], []
    if review.get('partial'):
        missing=review.get('missing_gemini',set())
        display(Markdown(f'**阶段性结果：Gemini 已完成 {len(review["scores"]["g"])} / {len(review["qs"])} 题，尚未全量冻结。缺少的 {len(missing)} 题因 OpenRouter HTTP402 未作答，不计为0分。以下三模型横向分析只使用共同已完成且可计分题目。**'))
        if missing:
            display(Markdown('未作答题号：'+', '.join(sorted(missing))))
    display(Markdown('**Qwen / BAGEL 原全量200成绩（已冻结，独立保留，不与Gemini部分结果直接混比分母）**'))
    full=t[t.candidate.isin(['a','b']) & t.counted]
    display(full.groupby('model').agg(n=('qid','size'),official_mean=('official','mean'),zero_count=('official',lambda x:int((x==0).sum())),full_count=('official',lambda x:int((x==100).sum()))).round(3))
    common = t[t.qid.isin(review['common'])]
    display(Markdown(overall_interpretation(review)))
    for scope, ids in scopes(review):
        shared = ids & review['common']
        for candidate, name in NAMES.items():
            all_rows = t[(t.candidate == candidate) & t.qid.isin(ids)]
            valid = all_rows[all_rows.counted]
            g = valid[valid.qid.isin(shared)]
            rows.append({'范围':scope, '模型':name, '题库应有':len(ids), '已有评分':len(all_rows), '未作答':len(ids)-len(all_rows), '单模型可计分':len(valid),
                         '三方共同n':len(g), '共同official均分':g.official.mean(), '共同中位数':g.official.median(),
                         '共同0分数':int((g.official==0).sum()), '共同0分占比':(g.official==0).mean(),
                         '共同100分数':int((g.official==100).sum()), '共同100分占比':(g.official==100).mean(),
                         **{status:int((all_rows.status==status).sum()) for status in ('model_failure','invalid_question','judge_unscorable')}})
    display(pd.DataFrame(rows).round(3))
    display(Markdown('总分分布（三方共同可计分样本；model_failure 计0）：'))
    display(common.groupby(['official','model']).size().unstack('model',fill_value=0))
    excluded=t[~t.counted][['qid','model','status']]
    if not excluded.empty:
        display(Markdown('不可计分候选（对涉及该候选的比较剔除，其他两模型仍按自身共同分母比较）：'))
        display(excluded)


def show_pairs(review):
    import pandas as pd
    from IPython.display import display, Markdown
    if review is None:return
    rows=[]
    for scope,ids in scopes(review):
        for left,right in itertools.combinations(NAMES,2):
            p=pair_rows(review,left,right,ids)
            mean=lambda key:sum(x[key] for x in p)/len(p) if p else None
            rows.append({'范围':scope,'左模型':NAMES[left],'右模型':NAMES[right],'配对n':len(p),
                         '左均分':mean('left'),'右均分':mean('right'),'左−右':mean('delta'),
                         '左胜':sum(x['winner']=='left' for x in p),'平':sum(x['winner']=='tie' for x in p),'右胜':sum(x['winner']=='right' for x in p)})
    display(Markdown('每一对模型使用各自共同可计分题目；同题总分相同记平局，三对比较分别列明分母。'))
    display(pd.DataFrame(rows).round(3))


def show_dimensions(review):
    import pandas as pd
    from IPython.display import display, Markdown
    if review is None:return
    d=review['dim']; rows=[]
    if not review['common']:
        display(Markdown('没有三方共同可计分题目，三维比较为空。'));return
    for scope,ids in scopes(review):
        shared=ids & review['common']
        raw_ids=set.intersection(*(set(d[(d.candidate==c)&(d.status=='ok')].qid) for c in NAMES)) & shared
        for c,name in NAMES.items():
            for key in ('d1','d2','d3'):
                g=d[(d.candidate==c)&(d.dim==key)&d.qid.isin(shared)]
                raw=g[g.qid.isin(raw_ids)]
                rows.append({'范围':scope,'模型':name,'维度槽位':key,'official共同n':len(g),'official均分':g.official.mean(),
                             '三方ok共同n':len(raw),'raw映射均分':raw.raw_mapped.mean(),'相同ok样本official':raw.official.mean(),
                             '钳制损失':(raw.raw_mapped-raw.official).mean(),'钳制率':(raw.raw_mapped>raw.official).mean(),
                             **{f'raw档位{tier}数':int((raw.tier==tier).sum()) for tier in (0,1,2)}})
    display(Markdown(dimension_interpretation(review)))
    display(Markdown('official 使用三方共同可计分题目；raw 与钳制诊断进一步限定为三方均 status=ok 的同一组题目。d1/d2/d3 是维度槽位，跨题型含义不同；下表保留真实维度名称。'))
    table=pd.DataFrame(rows);display(table.round(3))
    differences=[]
    for scope,ids in scopes(review):
        for left,right in itertools.combinations(NAMES,2):
            for key in ('d1','d2','d3'):
                a=table[(table['范围']==scope)&(table['模型']==NAMES[left])&(table['维度槽位']==key)].iloc[0]
                b=table[(table['范围']==scope)&(table['模型']==NAMES[right])&(table['维度槽位']==key)].iloc[0]
                differences.append({'范围':scope,'左模型':NAMES[left],'右模型':NAMES[right],'维度':key,
                                    'official共同n':a['official共同n'],'official差':a['official均分']-b['official均分'],
                                    '三方ok共同n':a['三方ok共同n'],'raw差':a['raw映射均分']-b['raw映射均分'],
                                    '相同ok样本official差':a['相同ok样本official']-b['相同ok样本official']})
    display(pd.DataFrame(differences).round(3))
    show_dimension_chart(review)
    g=d[d.qid.isin(review['common'])]
    display(g.groupby(['edit_type','dim','label','model']).agg(n=('qid','size'),official=('official','mean')).unstack('model').round(3))



def show_dimension_chart(review):
    import io
    import numpy as np
    import matplotlib.pyplot as plt
    from IPython.display import display, Image as NotebookImage
    d=review['dim'];shared=d[d.qid.isin(review['common'])]
    if shared.empty:return
    fig,ax=plt.subplots(figsize=(10,4.5),dpi=140)
    positions=np.arange(3);width=.24
    for i,(candidate,label,color) in enumerate([('a','Qwen','#3178c6'),('b','BAGEL','#df8843'),('g','Gemini','#279b72')]):
        values=[shared[(shared.candidate==candidate)&(shared.dim==key)].official.mean() for key in ('d1','d2','d3')]
        bars=ax.bar(positions+(i-1)*width,values,width,label=label,color=color)
        ax.bar_label(bars,fmt='%.1f',padding=3,fontsize=9)
    ax.set_xticks(positions,['d1','d2','d3']);ax.set_ylabel('Official score (0-100)');ax.set_ylim(0,105)
    ax.set_title(f'Three-model common questions: n={len(review["common"])}')
    ax.legend();ax.grid(axis='y',alpha=.2);ax.set_axisbelow(True);fig.tight_layout()
    output=io.BytesIO();fig.savefig(output,format='png',bbox_inches='tight');plt.close(fig)
    display(NotebookImage(data=output.getvalue()))


def show_strata(review):
    from IPython.display import display, Markdown
    if review is None:return
    for scope,ids in scopes(review):
        display(Markdown(f'**{scope}：三方共同可计分样本分层**'))
        g=review['total'][review['total'].qid.isin(ids & review['common'])]
        if g.empty:
            display(Markdown('该范围没有三方共同可计分题目。'));continue
        display(Markdown(type_interpretation(review, ids)))
        for field in ('edit_type','level','suite','construction_profile'):
            display(g.fillna({field:'unknown'}).groupby([field,'model']).agg(n=('qid','size'),official=('official','mean')).unstack('model').round(3))


def show_case(review,qid):
    import pandas as pd
    import matplotlib.pyplot as plt
    from PIL import Image,ImageOps
    from IPython.display import display,Markdown
    q=review['qs'][qid]
    display(Markdown(f"### {qid} · {q['edit_type']}\n\n{q['edit_instruction']}"))
    paths=[('BEFORE',review['manifests']['a'][qid]['before'])]+[(NAMES[c],review['manifests'][c].get(qid,{}).get('after')) for c in NAMES]
    import io
    from IPython.display import Image as NotebookImage
    for label,path in paths:
        if path is None:
            display(Markdown(f"**{label}：未作答**"))
            continue
        with Image.open(path) as source:
            im=ImageOps.exif_transpose(source)
            width,height=im.size
            buffer=io.BytesIO()
            im.save(buffer,format='PNG')
        display(Markdown(f"**{label} · {width} × {height} px（原始分辨率）**"))
        display(NotebookImage(data=buffer.getvalue(),format='png',
                              width=width,height=height,retina=False))
    for c,name in NAMES.items():
        if qid not in review['scores'][c]:
            display(Markdown(f'**{name}：HTTP402 未作答，因此无评分；未计为0分。**'))
            continue
        score=review['scores'][c][qid]
        display(Markdown(f"**{name} · official {score['official_total']:.2f} · {score['validity']['status']}**"))
        display(pd.DataFrame([{'维度':d['key'],'名称':d['label'],'raw档位':d['tier'],'raw映射':d['mapped'],
                               'official':score['official_dimensions'][d['key']]} for d in score['raw_dimensions']]))
        for d in score['raw_dimensions']:
            display(Markdown(f"**{d['key']} · {d['label']}**\n\n{d['reason']}"))
        if score['validity'].get('detail'):
            display(Markdown(score['validity']['detail']))


def sample_cases(review,edit_type='全部',cohort='全量200',n=3,seed=42,qids=None,
                 left='g',right='a',winner='全部',min_abs_delta=0,sort_by_abs_delta=False):
    if review is None:
        from IPython.display import display,Markdown
        display(Markdown('数据尚未加载；请先运行初始化或样例单元格。'))
        return []
    if left==right or left not in NAMES or right not in NAMES:raise ValueError('Choose two different models')
    if min_abs_delta<0:raise ValueError('min_abs_delta must be nonnegative')
    if qids is None:
        ids=set.intersection(*(set(review['scores'][c]) for c in NAMES))
        if cohort != '全量200':raise ValueError('当前仅支持全量200题范围')
        if edit_type!='全部':ids={q for q in ids if review['qs'][q]['edit_type']==edit_type}
        pairs={p['qid']:p for p in pair_rows(review,left,right,ids)}
        if winner!='全部':ids={q for q in ids if pairs.get(q,{}).get('winner','excluded')==winner}
        if min_abs_delta>0:ids={q for q in ids if abs(pairs.get(q,{}).get('delta',0))>=min_abs_delta}
        if sort_by_abs_delta:qids=sorted(ids,key=lambda q:(-abs(pairs.get(q,{}).get('delta',0)),q))[:n]
        else:qids=random.Random(seed).sample(sorted(ids),min(n,len(ids)))
    if not qids:
        from IPython.display import display,Markdown
        display(Markdown('当前条件无匹配题目，请放宽类型、胜负或分差条件。'))
    for qid in qids:
        if qid not in review['qs']:raise ValueError(f'Unknown qid: {qid}')
        show_case(review,qid)
    return qids


def show_browser(review):
    from IPython.display import display,Markdown
    if review is None:return
    display(Markdown("默认不加载 case 图片。可按编辑类型、两模型胜负/分差筛选；每个选中的 case 始终展示原图与三个候选，以及三模型完整三维理由。"))
    try:import ipywidgets as w
    except ImportError:
        display(Markdown("运行 `sample_cases(B200_3, left='g', right='a', min_abs_delta=60, n=3)`，或传 `qids=['e001']` 查看指定题目。"));return
    typ=w.Dropdown(options=['全部']+sorted({q['edit_type'] for q in review['qs'].values()}),description='编辑类型')
    left=w.Dropdown(options=[(v,k) for k,v in NAMES.items()],value='g',description='左模型')
    right=w.Dropdown(options=[(v,k) for k,v in NAMES.items()],value='a',description='右模型')
    winner=w.Dropdown(options=[('全部','全部'),('左胜','left'),('平','tie'),('右胜','right'),('不可配对','excluded')],description='胜负')
    minimum=w.BoundedFloatText(value=0,min=0,max=100,description='最小|Δ|')
    descending=w.Checkbox(value=False,description='按|Δ|降序选取')
    n=w.BoundedIntText(value=3,min=1,max=20,description='数量');seed=w.IntText(value=42,description='种子')
    qids=w.Text(value='',description='指定题号',placeholder='多个题号用逗号分隔')
    button=w.Button(description='抽样查看');out=w.Output()
    def click(_):
        with out:
            out.clear_output(wait=True)
            if left.value==right.value:
                display(Markdown('请为胜负/分差筛选选择两个不同模型。'));return
            sample_cases(review,typ.value,'全量200',n.value,seed.value,
                         [q.strip() for q in qids.value.split(',') if q.strip()] or None,
                         left.value,right.value,winner.value,minimum.value,descending.value)
    button.on_click(click)
    display(w.VBox([w.HBox([typ]),w.HBox([left,right,winner]),w.HBox([minimum,descending]),w.HBox([n,seed]),qids,button,out]))


def install_notebook(path,gemini_run,candidate_subdir='g',allow_partial=False):
    path=Path(path);original=path.read_bytes();nb=json.loads(original)
    def cell(kind,source,key):
        c=dict(cell_type=kind,metadata={'tags':[TAG]},id=f'b200-three-{key}',source=source.splitlines(True))
        if kind=='code':c.update(outputs=[],execution_count=None)
        return c
    initialize=f'''from pathlib import Path
import sys, importlib
_REPO_B200_3 = next(p for p in [Path.cwd(), *Path.cwd().parents] if (p / 'benchmark/edit/review_bench200_three.py').is_file())
if str(_REPO_B200_3) not in sys.path: sys.path.insert(0, str(_REPO_B200_3))
import benchmark.edit.review_bench200_three as _review3
_review3 = importlib.reload(_review3)
load_three_review = _review3.load_three_review
sample_cases = _review3.sample_cases
B200_3 = load_three_review({str(Path(gemini_run).resolve())!r}, candidate_subdir={candidate_subdir!r}, allow_partial={allow_partial!r})
'''
    load=initialize+'_review3.show_overall(B200_3)\n'
    cases=initialize+'# This cell is independently runnable; images and complete reasons are saved in notebook outputs.\nSAMPLE_QIDS = sample_cases(B200_3, n=3, seed=42)\n'
    partial_mode = allow_partial and not (Path(gemini_run) / 'runtime/frozen.json').is_file()
    heading='三模型阶段性结果' if partial_mode else '三模型正式判分'
    data_description = (
        'Qwen/Bagel 原400份冻结分数原样复用；Gemini 每份现有评分逐题核验实际图片、prompt、raw、provenance和独立session。'
        if partial_mode else
        '三模型各200份评分均已完成并冻结；Qwen/Bagel 原400份分数原样复用，Gemini 200份评分逐题核验实际图片、prompt、raw、provenance和独立session。')
    comparison_description = (
        '正式呈现册：只保留整体得分对比、关键维度（d1/d2/d3）分模型对比与抽样看 case 三节。'
        '三模型横向分析使用共同已完成题目；Qwen/Bagel全量200单列。API402未作答不计为0分。'
        if partial_mode else
        '正式呈现册：只保留整体得分对比、关键维度（d1/d2/d3）分模型对比与抽样看 case 三节。'
        '三模型横向分析覆盖完整200题，并按有效性规则列明共同分母；Qwen/Bagel全量200单列。')
    case_description = (
        '默认仅抽三模型均已作答的题，指定缺题则明示Gemini未作答。'
        if partial_mode else '默认按固定随机种子从完整题库抽取3题，支持按题号、题型或两模型分差继续查看。')
    cells=[cell('markdown',f'''# edit bench200 · {heading}

Qwen-Image-Edit-2511 / BAGEL-7B-MoT / Gemini 3.1 Flash Image；判官为 **gpt-6-astra / medium**，修订 **QIB v2.2**。
{data_description}
{comparison_description}
''','intro'),
        cell('markdown','## 整体得分对比\nQwen/Bagel 全量200单列；三方共同可计分分母下横比 official 总分（0–100，model_failure 计 0），附总分分布与不可计分候选清单。','overall-text'),
        cell('code',load,'overall'),
        cell('markdown','## 关键维度对比（d1/d2/d3 三维 × 模型）\nraw 为独立落档诊断；official 另执行 d2/d3≤d1，不能将两种口径混为模型能力变化。','dims-text'),
        cell('code','_review3.show_dimensions(B200_3)\n','dims'),
        cell('markdown',"## 抽样看 case\n下方已直接保存3题原图+三候选的原始分辨率PNG与完整理由，每张图独立显示，不缩放重采样、不依赖控件。若编辑器自动适应栏宽，可打开图片输出或保存图片后按100%查看。样例单元格可独立运行；之后可直接调用 `sample_cases(B200_3, left='g', right='a', min_abs_delta=60, n=3)`，或 `sample_cases(B200_3, qids=['e001'])`。"+case_description,'cases-text'),
        cell('code',cases,'cases')]
    history=[c for c in nb['cells'] if not ({TAG,previous.TAG} & set(c.get('metadata',{}).get('tags',[])))]
    if history:
        cells.append(cell('markdown','---\n# 历史 pilot 分析与协议复核\n以下历史章节原样保留。','history'))
    nb['cells']=cells+history
    if path.read_bytes()!=original:raise RuntimeError('Concurrent notebook change; retry')
    temp=path.with_suffix('.ipynb.tmp');temp.write_text(json.dumps(nb,ensure_ascii=False,indent=1)+'\n');temp.replace(path)
    print(f'Installed {len(cells)} three-model cells; retained {len(history)} historical cells')


def execute_notebook(path):
    from IPython.core.interactiveshell import InteractiveShell
    from IPython.utils.capture import capture_output
    path=Path(path);original=path.read_bytes();nb=json.loads(original);shell=InteractiveShell.instance()
    for c in nb['cells']:
        if c['cell_type']!='code' or TAG not in c.get('metadata',{}).get('tags',[]):continue
        with capture_output() as capture:result=shell.run_cell(''.join(c['source']),store_history=True)
        if not result.success:raise RuntimeError(result.error_in_exec or result.error_before_exec)
        outputs=[]
        for name in ('stdout','stderr'):
            text=getattr(capture,name)
            if text:outputs.append(dict(output_type='stream',name=name,text=text.splitlines(True)))
        outputs.extend(dict(output_type='display_data',data=o.data,metadata=o.metadata) for o in capture.outputs)
        c.update(outputs=outputs,execution_count=result.execution_count)
    if path.read_bytes()!=original:raise RuntimeError('Concurrent notebook change; retry')
    temp=path.with_suffix('.ipynb.tmp');temp.write_text(json.dumps(nb,ensure_ascii=False,indent=1)+'\n');temp.replace(path)
    print('Executed three-model notebook cells only')


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--notebook',type=Path,default=EDIT_DIR/'reviews/results_review.ipynb')
    p.add_argument('--gemini-run',type=Path)
    p.add_argument('--candidate-subdir',default='g')
    p.add_argument('--execute',action='store_true')
    p.add_argument('--allow-partial',action='store_true',help='Explicitly review existing verified Gemini scores before full freeze')
    a=p.parse_args()
    if a.execute:execute_notebook(a.notebook)
    elif a.gemini_run:install_notebook(a.notebook,a.gemini_run,a.candidate_subdir,a.allow_partial)
    else:p.error('--gemini-run is required when installing the three-model section')
