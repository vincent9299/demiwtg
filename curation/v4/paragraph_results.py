"""Export only retained paragraph content; keep inclusion decisions separately."""
import argparse,html,json
from pathlib import Path
from demiflow.standalone import local_data
from .contracts import digest,immutable,source_code


def read_rows(p):
    with Path(p).open() as f:return [json.loads(l) for l in f if l.strip()]


from .ops.paragraphs import SelectRetainedParagraphs


def export_results(runs,out,source_file=None,exclusions_file=None):
    out=Path(out);runs=[Path(r).resolve() for r in runs]
    exclusions=json.loads(Path(exclusions_file).read_text()) if exclusions_file else []
    files=[r/f for r in runs for f in ['requests.jsonl','paragraphs.jsonl']]
    if source_file:files.append(Path(source_file))
    if exclusions_file:files.append(Path(exclusions_file))
    manifest={'inputs':{str(p.resolve()):digest(p.read_bytes()) for p in files},'source_code':source_code(),'policy':'Only machine candidate blocks without explicit exclusion; internal decisions are not published.'}
    immutable(out/'manifest.json',manifest)
    requests={};rows=[];sources={};image_sources={}
    for run in runs:
        requests.update({(str(run),r['batch_id']):r for r in read_rows(run/'requests.jsonl')})
        rows.extend({**r,'run':str(run)} for r in read_rows(run/'paragraphs.jsonl'))
    if source_file:
        for row in read_rows(source_file):
            docs={m['material_id']:m['record'] for m in row['documents']}
            for p in row['audit']['material_selection']['passages']:
                r=docs.get(p['material_id'],{})
                sources[p['source_id']]={'title':r.get('title') or r.get('url') or p['source_id'],'url':r.get('url') or r.get('source_url') or ''}
            for m in row['images']:
                r=m['record']
                if not isinstance(m.get('image_id'),str):continue
                image_sources[m['image_id']]={'title':r.get('title') or '图片来源','url':r.get('landing_url') or r.get('content_url') or r.get('url') or ''}
    selected=local_data().from_iter(lambda:iter(rows)).map(SelectRetainedParagraphs(exclusions)).take_all()
    immutable(out/'decisions.json',{'rows':[r['decisions'] for r in selected]})
    # Public JSON carries content/provenance only, no per-block review state.
    public=[r['content'] for r in selected]
    cited={c['source_id'] for r in public for t in r['topics'] for b in t['blocks'] for c in b.get('citations',[])}
    pictured={b['image_id'] for r in public for t in r['topics'] for b in t['blocks'] if b['type']=='image'}
    sources={k:v for k,v in sources.items() if k in cited}
    image_sources={k:v for k,v in image_sources.items() if k in pictured}
    immutable(out/'knowledge.json',{'concepts':public,'sources':sources,'image_sources':image_sources})
    esc=lambda x:html.escape(str(x));sections=[];concepts={}
    for row in public:concepts.setdefault(row['concept'],[]).append(row)
    def source_link(sid,image=False):
        s=(image_sources if image else sources).get(sid,{})
        url=s.get('url','');title=s.get('title') or sid
        return '<a href="'+esc(url)+'" target="_blank" rel="noopener noreferrer">'+esc(title)+'</a>' if url.startswith(('http://','https://')) else esc(title)
    for concept,rows in concepts.items():
        sections.append('<h1>'+esc(concept)+'</h1>')
        for row in rows:
            req=requests[(row['run'],row['batch_id'])];ims=dict(zip(req['joint_prompt']['image_ids'],req['pixel_images']))
            for t in row['topics']:
                sections.append('<section><h2>'+esc(t['title'])+'</h2>')
                gallery_open=False
                for b in t['blocks']:
                    if b['type']=='text':
                        if gallery_open:sections.append('</div>');gallery_open=False
                        sections.append('<p>'+esc(b['text'])+'</p><p class="source">来源：'+'、'.join(source_link(s) for s in dict.fromkeys(c['source_id'] for c in b['citations']))+'</p>')
                    elif b.get('image_id') in ims:
                        if not gallery_open:sections.append('<div class="image-gallery">');gallery_open=True
                        sections.append('<figure><img src="'+ims[b['image_id']]+'"><figcaption>'+esc(b['caption'])+'</figcaption><p>'+esc(b['limitations'])+'</p><p class="source">来源：'+source_link(b['image_id'],True)+'</p></figure>')
                if gallery_open:sections.append('</div>')
                sections.append('</section>')
    h='<!doctype html><meta charset="utf-8"><title>概念图文知识</title><style>body{max-width:950px;margin:32px auto;padding:0 20px;font:17px/1.8 sans-serif;color:#222}section{border-bottom:1px solid #ddd;padding:8px 0 22px}img{max-width:100%;max-height:420px}figure{margin:16px 0}.image-gallery{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:20px}.image-gallery img{width:100%;height:240px;object-fit:contain}.image-gallery:has(figure:only-child){max-width:650px}figcaption,.source{font-size:14px;color:#555}</style>'+''.join(sections)
    (out/'preview.html').write_text(h)
    return public


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--run',type=Path,action='append',required=True);p.add_argument('--out',type=Path,required=True);p.add_argument('--source-file',type=Path);p.add_argument('--exclusions-file',type=Path);a=p.parse_args()
    export_results(a.run,a.out,a.source_file,a.exclusions_file)

if __name__=='__main__':main()
