"""Read-only material case notebook; no model calls or summary HTML artifact."""
import base64
import html
import io
import json
from pathlib import Path
from .contracts import read


def render(run):
    run=Path(run).resolve();report=read(run/'report.json')
    cells=[{'cell_type':'markdown','id':'intro','metadata':{},'source':
        '# V4 材料入口验证\n\n这是显式选定的工程验证案例，不是V4正式采样、知识审核或试题。展示已有材料、关联依据、字节核验和扫描边界。每个来源的扫描上限与缺口均保留；未找到不等于全库没有。新旧概念身份尚未自动合并，COS下载尚未接入。\n'}]
    for index,entry in enumerate(report['bundles'],1):
        bundle=read(entry['path']);req=bundle['request'];parts=[f'<h2>{index}. {html.escape(req["kind"]+": "+req["value"])}</h2>',
          '<p>内部ID：'+html.escape(bundle['concept_id'])+'；状态：材料准备，知识未提取／未核验。</p>',
          '<p>关联范围：同一来源身份；跨源对应尚未裁定。下方材料并不自动构成事实证据。</p>']
        parts.append('<details><summary>实际来源覆盖及扫描边界</summary><pre>'+html.escape(json.dumps(bundle['coverage'],ensure_ascii=False,indent=2))+'</pre></details>')
        for i,m in enumerate(bundle['materials'],1):
            parts.append('<h3>'+str(i)+' · '+html.escape(m['kind'])+'</h3>')
            parts.append('<p>关联依据：'+html.escape(m['association_method'])+'</p>')
            parts.append('<pre>'+html.escape(json.dumps(m['provenance'],ensure_ascii=False,indent=2))+'</pre>')
            check=m.get('bytes',{})
            if check.get('status')=='verified_bytes':
                from PIL import Image,ImageOps
                with Image.open(check['path']) as im:
                    preview=ImageOps.exif_transpose(im).convert('RGB');preview.thumbnail((900,900));buf=io.BytesIO();preview.save(buf,format='JPEG',quality=85)
                parts.append('<img style="max-width:900px;width:100%" src="data:image/jpeg;base64,'+base64.b64encode(buf.getvalue()).decode()+'">')
                parts.append('<p><a href="'+html.escape(check['path'],quote=True)+'">原始图片</a>；已核对字节，不代表真实性或知识支持已核验。</p>')
            parts.append('<details open><summary>完整材料记录与核验结果</summary><pre style="white-space:pre-wrap">'+html.escape(json.dumps(m,ensure_ascii=False,indent=2))+'</pre></details>')
        if not bundle['materials']:parts.append('<p>本次指定扫描范围未找到关联材料，不能据此判断该概念无材料。</p>')
        parts.append('<p>未解决：'+html.escape('；'.join(bundle['gaps']))+'</p>')
        cells.append({'cell_type':'code','id':f'material-{index:03d}','metadata':{'jupyter':{'source_hidden':True}},'execution_count':index,
          'source':'# 已保存材料入口展示；重新生成：python -m curation.v4.flow review --run '+str(run),
          'outputs':[{'output_type':'display_data','metadata':{},'data':{'text/html':''.join(parts),'text/plain':req['value']}}]})
    nb={'cells':cells,'metadata':{'kernelspec':{'display_name':'demiwtg','language':'python','name':'demiwtg'},'language_info':{'name':'python'}},'nbformat':4,'nbformat_minor':5}
    out=run/'materials_review_zh.ipynb';out.write_text(json.dumps(nb,ensure_ascii=False,indent=1));return out
