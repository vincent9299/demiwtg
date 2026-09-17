"""Inspect frozen embeddings; no encoder/model calls or knowledge edits."""
import argparse,json,html
from pathlib import Path
from collections import defaultdict
from .contracts import immutable,digest
from .ops.paragraph_similarity import pair_candidates,bounded_groups


def key(a,b):return tuple(sorted((a,b)))


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--run',type=Path,required=True);p.add_argument('--labels',type=Path,required=True);p.add_argument('--out',type=Path,required=True);a=p.parse_args()
    rows=[json.loads(l) for l in (a.run/'embeddings.jsonl').read_text().splitlines()];labels=json.loads(a.labels.read_text());by_concept=defaultdict(list)
    for r in rows:by_concept[r['concept']].append(r)
    assert json.loads((a.run/'manifest.json').read_text())['source_sha256']==labels['source_sha256'], 'Labels belong to a different source snapshot'
    known={key(x['left'],x['right']):x for x in labels['labels']};ids={r['paragraph_id']:r for r in rows};indices={r['paragraph_id']:i for i,r in enumerate(labels['rows'])}
    assert set(known).issubset({key(x['paragraph_id'],y['paragraph_id']) for rs in by_concept.values() for i,x in enumerate(rs) for y in rs[i+1:]})
    trials=[];views={}
    for field in ['embedding_title_body','embedding_body']:
        for threshold in [.7,.75,.8,.85,.9,.95]:
            allpairs=[];groups=[];residual=[]
            for concept,rs in by_concept.items():
                pairs,scores=pair_candidates(rs,field,threshold,3)
                gs,left=bounded_groups(rs,pairs,scores,threshold)
                allpairs.extend({'concept':concept,**x} for x in pairs);groups.extend({'concept':concept,**x} for x in gs);residual.extend(left)
            matched={key(p['left'],p['right']):p for p in allpairs};tp=fp=fn=tn=0
            for k,label in known.items():
                selected=matched[k]['candidate'];positive=label['joint_review']
                tp+=int(selected and positive);fp+=int(selected and not positive);fn+=int(not selected and positive);tn+=int(not selected and not positive)
            score={'representation':field,'threshold':threshold,'top_k':3,'candidate_pairs':sum(p['candidate'] for p in allpairs),'tp':tp,'fp':fp,'fn':fn,'tn':tn,
                   'precision_on_labeled':tp/(tp+fp) if tp+fp else None,'recall_on_labeled':tp/(tp+fn),'multi_groups':sum(len(g['paragraph_ids'])>1 for g in groups),'residual_pairs':len(residual)}
            trials.append(score);views[(field,threshold)]={'pairs':allpairs,'groups':groups,'residual':residual}
    report={'scope':'Directed small calibration, not held-out quality estimate. Similarity means candidate relationship, never duplicate or truth certification. No LLM merge calls.',
            'paragraphs':len(rows),'concepts':{c:len(rs) for c,rs in by_concept.items()},'labeled_pairs':len(known),'source_embedding_sha256':digest((a.run/'embeddings.jsonl').read_bytes()),'labels_sha256':digest(a.labels.read_bytes()),'trials':trials}
    immutable(a.out/'report.json',report)
    # Show the prespecified .8/title+body default, without selecting by the evaluation score.
    view=views[('embedding_title_body',.8)];immutable(a.out/'default_candidates.json',view)
    immutable(a.out/'threshold_075_candidates.json',views[('embedding_title_body',.75)])
    e=lambda x:html.escape(str(x));body=['<h1>段落相似度实验</h1><p>Qwen3-Embedding-0.6B；30段真实提炼文本，3个概念。这里只提出一起核对的候选，不自动合并或删除。标注为实验前冻结的23对定向判断，不是独立测试集。</p>']
    body.append('<h2>两种输入表示与阈值对照</h2><table><tr><th>输入</th><th>阈值</th><th>候选对</th><th>标注正例命中/10</th><th>标注负例误入/13</th></tr>')
    for t in trials:body.append('<tr>'+''.join('<td>'+e(x)+'</td>' for x in [t['representation'],t['threshold'],t['candidate_pairs'],t['tp'],t['fp']])+'</tr>')
    body.append('</table><h2>预先设置的分组：标题＋正文，阈值0.8，每段最多取3个近邻</h2><p>每个组最多4段，组内任意两段均需达到阈值；不按连通关系无限扩张。孤立段落保留。组外仍相关的候选单独保存，不能当作已完成全局整合。</p>')
    for g in view['groups']:
        body.append('<section><h3>'+e(g['concept'])+' · '+('待联合处理' if len(g['paragraph_ids'])>1 else '独立保留')+'</h3>')
        for pid in g['paragraph_ids']:
            r=ids[pid];body.append('<h4>P'+str(indices[pid])+' '+e(r['title'])+'</h4><p>'+e(r['text'])+'</p>')
        body.append('</section>')
    body.append('<h2>候选关系明细</h2><table><tr><th>段落</th><th>相似度</th><th>实验前判断</th></tr>')
    for x in view['pairs']:
        if not x['candidate']:continue
        label=known.get(key(x['left'],x['right']));judgment='未标注' if label is None else '应一起核对' if label['joint_review'] else '不同主题'
        body.append('<tr><td>'+e('P'+str(indices[x['left']])+' ↔ P'+str(indices[x['right']]))+'</td><td>'+f"{x['score']:.4f}"+'</td><td>'+judgment+'</td></tr>')
    body.append('</table><h2>阈值0.75仍漏掉的已标注相关内容</h2>')
    recall_view=views[('embedding_title_body',.75)]
    for x in recall_view['pairs']:
        label=known.get(key(x['left'],x['right']))
        if label and label['joint_review'] and not x['candidate']:
            body.append('<section><p>相似度 '+f"{x['score']:.4f}"+'；未召回不代表无关，不会删除。</p>')
            for pid in [x['left'],x['right']]:body.append('<h4>P'+str(indices[pid])+' '+e(ids[pid]['title'])+'</h4><p>'+e(ids[pid]['text'])+'</p>')
            body.append('</section>')
    body.append('<h2>默认分组之外仍有的相关候选</h2><p>复合段落可能同时与两个主题有关，不能只归入一组就丢掉另一条关系。这些边保留给后续局部整合。</p><ul>')
    for x in view['residual']:body.append('<li>P'+str(indices[x['left']])+' ↔ P'+str(indices[x['right']])+': '+f"{x['score']:.4f}"+'</li>')
    body.append('</ul><details><summary>全部23对预标注的分数（含漏召回）</summary><table>')
    pairs={key(x['left'],x['right']):x for x in view['pairs']}
    for k,label in known.items():
        x=pairs[k];body.append('<tr><td>'+e(str(label['left_index'])+' ↔ '+str(label['right_index']))+'</td><td>'+e(label['joint_review'])+'</td><td>'+f"{x['score']:.4f}"+'</td><td>'+e(x['candidate'])+'</td></tr>')
    body.append('</table></details>')
    (a.out/'preview.html').write_text('<!doctype html><meta charset="utf-8"><style>body{max-width:1050px;margin:30px auto;padding:15px;font:16px/1.7 sans-serif}table{border-collapse:collapse}td,th{border:1px solid #ccc;padding:8px}section{border:1px solid #ddd;margin:20px 0;padding:15px}</style>'+''.join(body))
    print(json.dumps(report,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
