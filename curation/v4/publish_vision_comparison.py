"""Read-only comparison viewer: fixed original pixels and unedited model responses."""
import argparse,html,json
from pathlib import Path

def publish(inputs,run):
    cases=[json.loads(l) for l in Path(inputs).open() if l.strip()]
    models={}
    for directory in sorted(Path(run).iterdir()):
        if not directory.is_dir():continue
        stages={}
        for stage in ['observe_image','identify_image']:
            p=directory/(stage+'.jsonl')
            if p.exists():stages[stage]={r['sample_id']:r for r in map(json.loads,p.open())}
        if stages:models[directory.name]=stages
    esc=html.escape
    chunks=['<!doctype html><meta charset="utf-8"><title>识图对照</title><style>body{font:16px sans-serif;max-width:1500px;margin:30px auto}img{max-width:500px;max-height:400px}td,th{border:1px solid #ddd;padding:12px;vertical-align:top}table{width:100%;border-collapse:collapse}pre{white-space:pre-wrap;word-break:break-word}section{margin:40px 0}</style><h1>同一图片：盲看 / 给定目标概念</h1><p>10个定向诊断样本，不是代表性模型排名。左侧条件没有概念、标题或上游判断；右侧增加目标及范围。以下为完整模型输出，非人工认证。</p>']
    for c in cases:
        chunks.append(f'<section><h2>{esc(c["sample_id"])} · {esc(c["concept"])} · {esc(c["image_id"])}</h2><img src="{esc(c["pixel_images"][0],quote=True)}"><table><tr><th>模型</th><th>盲看</th><th>给定目标</th></tr>')
        for model,stages in models.items():
            chunks.append('<tr><th>'+esc(model)+'</th>')
            for stage in ['observe_image','identify_image']:
                r=stages.get(stage,{}).get(c['sample_id'],{})
                value=r.get('result') or r.get('error') or '未完成'
                raw=''
                if r.get('error'):
                    response=Path(r['error'].get('call',{}).get('response_path',''))
                    if response.is_file():
                        body=json.loads(response.read_text())
                        raw=body.get('body',{}).get('choices',[{}])[0].get('message',{}).get('content','')
                chunks.append('<td><pre>'+esc(json.dumps(value,ensure_ascii=False,indent=2))+'</pre>'+('<details><summary>未通过协议检查的原始输出（不计为成功）</summary><pre>'+esc(raw)+'</pre></details>' if raw else '')+'</td>')
            chunks.append('</tr>')
        chunks.append('</table></section>')
    target=Path(run)/'preview.html';target.write_text(''.join(chunks));return target
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--inputs',required=True);p.add_argument('--run',required=True);a=p.parse_args();print(publish(a.inputs,a.run))
