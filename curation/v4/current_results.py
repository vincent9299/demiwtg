"""Read only the selected formal run; never fall back to experiment outputs."""
import html
import json
from pathlib import Path


def show_current_results(run, *, concepts=None, limit=10, images=True):
    from IPython.display import display, HTML, Markdown
    run = Path(run)
    path = run / 'knowledge_base.jsonl'
    if not path.exists():
        display(Markdown(f'当前运行尚无最终知识文件：`{path}`。执行正式 pipeline 后再运行本格。'))
        return
    if limit < 1:
        raise ValueError('limit must be positive')
    records = []
    with path.open() as stream:
        for line in stream:
            row = json.loads(line)
            if concepts is None or row['concept'] in concepts:
                records.append(row)
                if len(records) >= limit:
                    break
    wanted = {im['image_id'] for row in records for t in row['knowledge'] for im in t['content']['images']}
    pixels = {}
    if images and wanted:
        requests = run / 'datasets/joint_requests.jsonl'
        if requests.exists():
            with requests.open() as stream:
                for line in stream:
                    row = json.loads(line)
                    for iid, pixel in zip(row['joint_prompt']['image_ids'], row['pixel_images']):
                        if iid in wanted:
                            pixels[iid] = pixel
    esc = html.escape
    output = []
    for row in records:
        output.append('<h2>' + esc(row['concept']) + '</h2>')
        if not row['knowledge']:
            output.append('<p>没有保留的知识内容。</p>')
        for topic in row['knowledge']:
            output.append('<h3>' + esc(topic['title']) + '</h3>')
            output.extend('<p style="white-space:pre-wrap">' + esc(p) + '</p>' for p in topic['content']['paragraphs'])
            if images:
                for im in topic['content']['images']:
                    pixel = pixels.get(im['image_id'], '')
                    if pixel.startswith('data:image/'):
                        output.append('<figure><img style="max-width:100%;max-height:350px" src="' + esc(pixel, quote=True) + '"><figcaption>' + esc(im['caption']) + '</figcaption></figure>')
                    else:
                        output.append('<p>图片预览不可用：' + esc(im['image_id']) + '</p>')
            refs = []
            for ref in topic['references']:
                title, url = esc(ref['title']), ref.get('url', '')
                refs.append('<a href="' + esc(url, quote=True) + '" target="_blank" rel="noopener noreferrer">' + title + '</a>' if url.startswith(('http://', 'https://')) else title)
            output.append('<p><b>参考来源：</b>' + '；'.join(dict.fromkeys(refs)) + '</p>')
    display(HTML(''.join(output) or '<p>没有匹配的概念。</p>'))
