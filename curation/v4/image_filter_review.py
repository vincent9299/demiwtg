"""Notebook-only display of the saved local image-filter comparison."""
import base64
import html
import io
import json
from pathlib import Path

from IPython.display import HTML, display
from PIL import Image, ImageOps
from .analyze_image_filter import combine


LABELS = {'keep': '保留', 'exclude': '排除', 'pending': '待定'}


def show_images(run, concept=None, disagreements_only=False, limit=20, offset=0, model_names=None):
    rows = [json.loads(line) for line in (Path(run) / 'comparison_rows.jsonl').read_text().splitlines()]
    if concept:
        rows = [r for r in rows if r['concept'] == concept]
    if disagreements_only:
        rows = [r for r in rows if len({(m.get('raw') or {}).get('decision', 'pending') for m in r['models'].values()} | {r['reference']}) > 1]
    models = (model_names or list(rows[0]['models'])) if rows else []
    total = len(rows)
    rows = rows[offset:] if limit is None else rows[offset:offset + limit]
    table = ['<table><thead><tr><th>概念／原图</th><th>独立初审（非专家真值）</th>']
    table.extend('<th>' + html.escape(m) + '</th>' for m in models)
    table.append('<th>Qwen3.8→Gemma31<br>复核入选图</th></tr></thead><tbody>')
    for row in rows:
        with Image.open(row['path']) as image:
            image = ImageOps.exif_transpose(image).convert('RGB')
            image.thumbnail((260, 220))
            buffer = io.BytesIO(); image.save(buffer, format='JPEG', quality=90)
        uri = 'data:image/jpeg;base64,' + base64.b64encode(buffer.getvalue()).decode()
        table.append(f'<tr><td>{html.escape(row["concept"])} · {row["sample_id"]}<br><img src="{uri}"><br>{row["image_id"]}</td>')
        table.append('<td>' + LABELS[row['reference']] + '<br>' + html.escape(row['reason']) + '</td>')
        for model in models:
            decision = row['models'][model]
            raw = decision.get('raw') or {}
            label = LABELS.get(raw.get('decision'), '无有效回答')
            table.append('<td>' + label + '<br>' + html.escape(str(raw.get('reason') or '')) +
                         '<details><summary>实际观察／解析结果</summary>' +
                         html.escape(str(raw.get('visible_information') or '')) + '<br>限制：' + html.escape(str(raw.get('limitations') or '')) +
                         '<br>原算子输出：' + LABELS[decision['pipeline_decision']] +
                         ('<br>修正字段校验后：' + LABELS[decision['revalidated_decision']] if 'revalidated_decision' in decision else '') + '</details></td>')
        pair = [row['models'].get(m, {}) for m in ['qwen3.8-27b', 'gemma-4-31b-it']]
        decisions = [m.get('revalidated_decision', (m.get('raw') or {}).get('decision', 'pending')) for m in pair]
        table.append('<td>' + LABELS[combine(*decisions, 'confirm_keep')] + '</td>')
        table.append('</tr>')
    table.append('</tbody></table>')
    display(HTML(f'<p>符合筛选 {total} 张；展示 {offset + 1 if rows else 0}–{offset + len(rows)}。图片为原文件的展示缩略图；模型输入另按冻结配置处理。</p>' + ''.join(table)))
