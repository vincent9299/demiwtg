"""Publish topic articles: title, prose/images, deduplicated references."""
import argparse,json,html
from collections import defaultdict
from pathlib import Path
from demiflow.standalone import local_data
from .contracts import immutable,digest,source_code
from .ops.topic_articles import TopicRows,BuildTopicArticle


def rows(p):return [json.loads(l) for l in p.read_text().splitlines() if l.strip()]


def render_articles(concepts,pixels,out):
    e=lambda x:html.escape(str(x));parts=[]
    for concept,articles in concepts.items():
        parts.append('<h1>'+e(concept)+'</h1>')
        for t in articles:
            parts.append('<article><h2>'+e(t['title'])+'</h2><div class="content">')
            parts.extend('<p>'+e(s)+'</p>' for s in t['content']['paragraphs'])
            if t['content']['images']:
                parts.append('<div class="gallery">')
                for im in t['content']['images']:
                    parts.append('<figure><img src="'+pixels[im['image_id']]+'" alt="'+e(im['caption'])+'"><figcaption>'+e(im['caption'])+'</figcaption><details><summary>图片说明与范围</summary>'+e(im['limitations'])+'</details></figure>')
                parts.append('</div>')
            parts.append('</div><footer><h3>参考来源</h3><ul>')
            for ref in t['references']:
                title=e(ref['title']);url=ref['url']
                parts.append('<li>'+('<a href="'+e(url)+'" target="_blank" rel="noopener noreferrer">'+title+'</a>' if url.startswith(('http://','https://')) else title)+'</li>')
            parts.append('</ul></footer></article>')
    style='body{max-width:1000px;margin:32px auto;padding:0 22px;font:17px/1.85 sans-serif;color:#242424}h1{margin-top:50px}article{border-bottom:1px solid #ddd;padding:12px 0 28px}.gallery{display:grid;grid-template-columns:repeat(auto-fit,minmax(250px,1fr));gap:20px}figure{margin:12px 0}img{width:100%;height:250px;object-fit:contain}figcaption,details,footer{font-size:14px;color:#555}footer{margin-top:24px}footer h3{font-size:15px}footer ul{padding-left:22px}'
    (out/'preview.html').write_text('<!doctype html><meta charset="utf-8"><title>概念主题文章</title><style>'+style+'</style>'+''.join(parts))


def publish_pipeline(run):
    run=Path(run);out=run/"published";out.mkdir(parents=True,exist_ok=True)
    records=rows(run/"knowledge_base.jsonl")
    concepts={r["concept"]:r["knowledge"] for r in records}
    immutable(out/"knowledge.json",{"concepts":[{"concept":c,"articles":a} for c,a in concepts.items()]})
    pixels={i:pic for r in rows(run/"requests.jsonl") for i,pic in zip(r["joint_prompt"]["image_ids"],r["pixel_images"])}
    render_articles(concepts,pixels,out)
    return out/"preview.html"


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--source-run',type=Path,required=True);p.add_argument('--out',type=Path,required=True);a=p.parse_args()
    files=[a.source_run/'published/knowledge.json',a.source_run/'requests.jsonl']
    manifest={'inputs':{str(f.resolve()):digest(f.read_bytes()) for f in files},'source_code':source_code(),
              'selection':'model_selected_images_preserved_without_cap_or_similarity_filter',
              'scope':'Formatting and source deduplication only; no model or embedding calls'}
    immutable(a.out/'manifest.json',manifest);version=digest(manifest);data=local_data()
    source=json.loads(files[0].read_text())
    topics=data.from_iter(lambda:iter(source['concepts'])).flat_map(TopicRows()).checkpoint(a.out/'topics.jsonl',version=version)
    result=(topics.map(BuildTopicArticle({}, {},source['sources'],source['image_sources']))
            .checkpoint(a.out/'articles_audit.jsonl',version=version).take_all())
    concepts=defaultdict(list)
    for r in result:concepts[r['concept']].append(r['article'])
    immutable(a.out/'knowledge.json',{'concepts':[{'concept':c,'articles':ts} for c,ts in concepts.items()]})
    pixels={i:pic for r in rows(files[1]) for i,pic in zip(r['joint_prompt']['image_ids'],r['pixel_images'])}
    render_articles(concepts,pixels,a.out)
    print(a.out/'preview.html')

if __name__=='__main__':main()
