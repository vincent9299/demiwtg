"""Read-only, full-result review artifact with separate, paragraph-level annotations."""
import base64
import hashlib
import html
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import nbformat
from bs4 import BeautifulSoup
from markdown_it import MarkdownIt

ROOT = Path('/yzp/zhaozy/yangzepeng/0905/demiwtg')
sys.path.insert(0, str(ROOT))
from curation.v4.ops.article import source_text

UP = Path(__file__).resolve().parent
BASE = UP.parent
ESC = html.escape
MD = MarkdownIt('commonmark', {'html': False})


def read_rows(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def render(plan):
    out = ['<div class="review-document"><h1>最终知识 · 带问题标注的审阅版</h1>',
           '<p>正文和图片保持 pipeline 保存的内容，未替模型改写。红色或橙色批注是后续复核意见，与模型自己的审查记录分开。未标注的段落不代表事实已独立认证。</p>',
           '<p>' + ESC(plan['note']) + '</p>', '<nav>']
    all_rows = []
    inputs = {}
    pixels = {}
    hashes = {}
    for name in plan['runs']:
        run = BASE / name
        kb = run / 'knowledge_base.jsonl'
        hashes[name] = hashlib.sha256(kb.read_bytes()).hexdigest()
        all_rows.extend((name, row) for row in read_rows(kb))
        inputs[name] = {r['concept']: r for r in read_rows(run / 'datasets/final_review_requests.jsonl')}
        for row in inputs[name].values():
            pixels.update(zip(row['article_image_ids'], row['pixel_images']))
    for _, row in all_rows:
        out.append('<a href="#' + ESC(row['concept']) + '">' + ESC(row['concept']) + '</a>')
    out.append('</nav>')
    matched = set()
    published_paragraphs = []
    published_images = 0
    proposed_images = 0

    def json_details(label, value):
        return '<details><summary>' + ESC(label) + '</summary><pre>' + ESC(json.dumps(value, ensure_ascii=False, indent=2)) + '</pre></details>'

    def note_html(issue, name, concept):
        cls = 'issue' if issue.get('severity') != 'expression' else 'expression'
        s = '<aside class="' + cls + '"><b>' + ESC(issue['id'] + ' · ' + issue['title']) + '</b><p>' + ESC(issue['explanation']) + '</p>'
        if issue.get('proposal'):
            s += '<p><b>修改思路：</b>' + ESC(issue['proposal']) + '</p>'
        sources = inputs[name].get(concept, {}).get('source_catalog', [])
        for n in issue.get('sources', []):
            source = sources[n - 1]
            s += '<details><summary>核对原文 · 输入资料' + str(n) + '</summary><pre>' + ESC(source_text(source)) + '</pre></details>'
        return s + '</aside>'

    def paragraph(text, name, concept, kind='published'):
        issues = [i for i in plan['issues'] if i['concept'] == concept and i.get('kind', 'published') == kind and i.get('anchor') and i['anchor'] in text]
        escaped = ESC(text)
        for issue in issues:
            if issue['id'] in matched:
                continue
            matched.add(issue['id'])
            for phrase in issue.get('highlights', [issue['anchor']]):
                escaped = escaped.replace(ESC(phrase), '<mark>' + ESC(phrase) + '</mark>')
        out.append('<div class="paragraph"><p class="body">' + escaped + '</p>')
        for issue in issues:
            out.append(note_html(issue, name, concept))
        out.append('</div>')

    def figure(image_id, label, metadata=None):
        pixel = pixels[image_id]
        assert pixel.startswith('data:image/')
        out.append('<figure><img src="' + ESC(pixel, quote=True) + '" alt="' + ESC(label) + '"><figcaption>' + ESC(label) + '</figcaption></figure>')
        for issue in plan['issues']:
            if issue.get('image_id') == image_id and issue['concept'] == concept:
                matched.add(issue['id'])
                out.append(note_html(issue, name, concept))
        if metadata is not None:
            out.append(json_details('图片来源与字段', metadata))

    status = {'reviewed': '已保存的最终知识；仍需内容复核', 'failed': '本版本失败，未发布最终知识',
              'insufficient_materials': '入选材料不足，没有生成知识', 'no_supported_knowledge': '最终 review 后无可保留知识'}
    for name, row in all_rows:
        concept = row['concept']
        out.append('<section id="' + ESC(concept) + '"><h2>' + ESC(concept) + '</h2>')
        out.append('<p class="status">' + ESC(status.get(row['status'], row['status'])) + '</p><p class="version">版本：' + ESC(name) + '</p>')
        summary = plan['summaries'].get(concept, '')
        if summary:
            out.append('<p class="summary">' + ESC(summary) + '</p>')
        if row.get('status_reason'):
            out.append('<p>' + ESC(row['status_reason']) + '</p>')
        if not row['knowledge']:
            out.append('<p>知识库中没有本版本的正文或配图。</p>')
        for topic in row['knowledge']:
            out.append('<h3>' + ESC(topic['title']) + '</h3>')
            seen = set()
            for pi, text in enumerate(topic['content']['paragraphs']):
                paragraph(text, name, concept)
                published_paragraphs.append(text)
                for im in topic['content']['images']:
                    if im.get('paragraph_index') == pi:
                        figure(im['image_id'], '图 ' + str(im['figure_number']), im)
                        published_images += 1
                        seen.add(im['image_id'])
            for im in topic['content']['images']:
                if im['image_id'] not in seen:
                    figure(im['image_id'], '图 ' + str(im['figure_number']), im)
                    published_images += 1
            out.append(json_details('本主题的全部引用来源', topic['references']))
        if row.get('audit', {}).get('review_notes'):
            out.append('<details><summary>模型自己的审查记录（不是人工验收结论）</summary>')
            paragraph(row['audit']['review_notes'], name, concept, 'review_notes')
            out.append('</details>')
        if row['status'] == 'failed':
            out.append(json_details('失败原因', {k: row.get('audit', {}).get(k) for k in ['preflight_error', 'failed_batches', 'validation_issues']}))
        if concept in plan.get('show_failed_final_response', []):
            assert row['status'] == 'failed'
            final = next(r for r in read_rows(BASE / name / 'datasets/final_review.jsonl') if r['concept'] == concept)
            text = final['article_text']
            assert text and '## 最终知识' in text
            notes, body = text.split('## 最终知识', 1)
            out.append('<h3>最后一步的完整返回稿 · 程序未发布</h3><p class="status">下面用于查看模型实际写出了哪些问题，不是已发布的知识。文字未改写；配图是该返回稿拟选的原图。</p>')
            out.append('<details><summary>该返回稿的模型审查记录</summary><pre>' + ESC(notes) + '</pre></details>')
            for block in body.strip().split('\n\n'):
                if block.startswith('## '):
                    out.append('<h4>' + ESC(block[3:].strip()) + '</h4>')
                else:
                    paragraph(block, name, concept, 'failed_final_response')
            import re
            selections = re.findall(r'【图([1-9][0-9]*)】', body.split('## 配图', 1)[1])
            for number in selections:
                iid = final['article_image_ids'][int(number) - 1]
                figure(iid, '未发布返回稿拟选 · 输入图 ' + number)
                proposed_images += 1
        out.append('</section>')
    out.append('</div>')
    assert matched == {i['id'] for i in plan['issues']}, ({i['id'] for i in plan['issues']} - matched)
    content = ''.join(out)
    visible = BeautifulSoup(content, 'html.parser').get_text()
    assert all(p in visible for p in published_paragraphs)
    for name, expected in hashes.items():
        assert hashlib.sha256((BASE / name / 'knowledge_base.jsonl').read_bytes()).hexdigest() == expected
    css = '''.review-document{font-family:system-ui,sans-serif;color:#20282e;max-width:1080px;margin:auto;padding:24px;line-height:1.8;background:#fff}.review-document h1{font-size:28px}.review-document h2{border-top:2px solid #cbd5df;padding-top:28px;margin-top:45px}.review-document h3{margin-top:26px}.review-document .body{white-space:pre-wrap;overflow-wrap:anywhere}.review-document mark{background:#fff1a8;color:inherit}.review-document aside{border-left:4px solid #c94b3c;background:#fff3f0;padding:12px 18px;margin:12px 0 24px}.review-document aside.expression{border-color:#c68b21;background:#fff9ec}.review-document aside p{margin:6px 0}.review-document .status{font-weight:600;color:#8c3f31}.review-document .summary{background:#edf3f8;padding:12px 16px}.review-document .version{font-size:13px;color:#5b6670}.review-document nav{display:flex;flex-wrap:wrap;gap:14px;position:sticky;top:0;background:#fff;padding:12px;border-bottom:1px solid #ddd;z-index:2}.review-document img{max-width:100%;max-height:850px;height:auto;object-fit:contain}.review-document figure{text-align:center;margin:22px 0}.review-document details{border:1px solid #d9e0e4;padding:8px 12px;margin:10px 0}.review-document summary{cursor:pointer}.review-document pre{white-space:pre-wrap;overflow-wrap:anywhere;font:inherit;font-size:13px}.review-document figcaption{font-size:13px;color:#52616d}'''
    report = {'runs': plan['runs'], 'concepts': len(all_rows), 'published_paragraphs': len(published_paragraphs),
              'published_images': published_images, 'unpublished_proposed_images': proposed_images,
              'annotations': len(matched), 'all_published_paragraphs_present': True,
              'original_knowledge_hashes_unchanged': hashes, 'new_model_calls': 0,
              'created_utc': datetime.now(timezone.utc).isoformat()}
    path = UP / plan['output_name']
    document = '<!doctype html><html lang="zh"><meta charset="utf-8"><title>最终知识审阅</title><style>' + css + '</style>' + content + '</html>'
    path.write_text(document)
    path.with_suffix('.validation.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    nb = nbformat.v4.new_notebook(cells=[nbformat.v4.new_markdown_cell('完整最终知识与问题批注；原始结果未改写。'),
        nbformat.v4.new_code_cell('from pathlib import Path\nfrom IPython.display import HTML, display\ndisplay(HTML(Path(' + repr(str(path)) + ').read_text()))',
                                 execution_count=1, outputs=[nbformat.v4.new_output('display_data', data={'text/html': '<style>' + css + '</style>' + content})])])
    nb.metadata['kernelspec'] = {'display_name': 'demiwtg', 'language': 'python', 'name': 'demiwtg'}
    nbformat.write(nb, path.with_suffix('.ipynb'))
    print(json.dumps({'html': str(path), 'notebook': str(path.with_suffix('.ipynb')), **report}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    render(json.loads(Path(sys.argv[1]).read_text()))
