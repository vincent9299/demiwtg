"""Read-only notebook comparison of frozen final knowledge; never executes models."""
import base64
import html
import json
from pathlib import Path
from collections import Counter


def read_rows(path):
    path=Path(path)
    return [json.loads(line) for line in path.open() if line.strip()] if path.exists() else []


def summarize_run(run):
    run=Path(run);records=read_rows(run/'knowledge_base.jsonl');usage=Counter();stages=Counter()
    for p in (run/'knowledge/calls').glob('*.request.json'):
        stages[json.loads(p.read_text())['stage']]+=1
    for p in (run/'knowledge/calls').glob('*.response.json'):
        usage.update({k:v for k,v in json.loads(p.read_text()).get('body',{}).get('usage',{}).items() if isinstance(v,int)})
    return {'concepts':{r['concept']:{'topics':len(r['knowledge']),
        'paragraphs':sum(len(t['content']['paragraphs']) for t in r['knowledge']),
        'characters':sum(len(p) for t in r['knowledge'] for p in t['content']['paragraphs']),
        'images':len({i['image_id'] for t in r['knowledge'] for i in t['content']['images']})} for r in records},
        'calls':sum(stages.values()),'stages':dict(stages),'usage':dict(usage)}


def show_run_comparison(previous, current, *, concepts=None, images=True):
    """Full text side-by-side with optional actual input pixels; no external HTML file."""
    from IPython.display import display, HTML, Markdown
    import pandas as pd
    previous,current=Path(previous),Path(current)
    old={r['concept']:r for r in read_rows(previous/'knowledge_base.jsonl')}
    new={r['concept']:r for r in read_rows(current/'knowledge_base.jsonl')}
    if not new:raise ValueError('Current run has no final output')
    a,b=summarize_run(previous),summarize_run(current)
    selected=concepts or list(new)
    display(pd.DataFrame([{'概念':c,'旧主题':a['concepts'].get(c,{}).get('topics',0),'新主题':b['concepts'].get(c,{}).get('topics',0),
        '旧正文字符':a['concepts'].get(c,{}).get('characters',0),'新正文字符':b['concepts'].get(c,{}).get('characters',0),
        '旧配图':a['concepts'].get(c,{}).get('images',0),'新配图':b['concepts'].get(c,{}).get('images',0)} for c in selected]))
    display(Markdown(f"调用：**{a['calls']} → {b['calls']}**；token：**{a['usage'].get('total_tokens',0):,} → {b['usage'].get('total_tokens',0):,}**。数量变化不等于信息损失，逐项结论见下面人工对照记录。"))
    review_path=current/'content_review.json'
    if review_path.exists():
        review=json.loads(review_path.read_text())
        display(Markdown(review.get('scope','')))
        entries=[r for r in review.get('items',[]) if r['concept'] in selected]
        if entries:display(pd.DataFrame(entries).rename(columns={'concept':'概念','topic':'内容','change':'变化判断','evidence':'具体依据'}).style.set_properties(**{'white-space':'pre-wrap','text-align':'left'}))
    def pixels(run):
        return {iid:pixel for r in read_rows(run/'requests.jsonl') for iid,pixel in zip(r['joint_prompt']['image_ids'],r['pixel_images'])}
    oldpix,newpix=(pixels(previous),pixels(current)) if images else ({},{})
    e=html.escape
    def articles(record,pics):
        out=[]
        for t in record.get('knowledge',[]):
            out.append('<section><h3>'+e(t['title'])+'</h3>')
            out.extend('<p>'+e(p)+'</p>' for p in t['content']['paragraphs'])
            for im in t['content']['images']:
                pix=pics.get(im['image_id'],'')
                if images and pix.startswith('data:image/'):
                    out.append('<figure><img style="max-width:100%;max-height:260px" src="'+e(pix,quote=True)+'"><figcaption>'+e(im['caption'])+'</figcaption></figure>')
            refs=[]
            for ref in t['references']:
                title=e(ref['title']);url=ref.get('url','')
                refs.append('<a href="'+e(url,quote=True)+'" target="_blank" rel="noopener noreferrer">'+title+'</a>' if url.startswith(('http://','https://')) else title)
            out.append('<p><b>参考来源：</b>'+'；'.join(refs)+'</p></section>')
        return ''.join(out) or '本轮无保留内容'
    for c in selected:
        display(Markdown('## '+c+'：旧版与精简版完整输出'))
        display(HTML('<table style="table-layout:fixed;width:100%;border-collapse:collapse"><tr><th>对照运行：'+e(previous.name)+'</th><th>当前运行：'+e(current.name)+'</th></tr><tr>'+''.join('<td style="width:50%;vertical-align:top;border:1px solid #ddd;padding:12px;overflow-wrap:anywhere">'+body+'</td>' for body in [articles(old.get(c,{}),oldpix),articles(new.get(c,{}),newpix)])+'</tr></table>'))
