"""Display-only image previews for Dataset notebooks; never changes pipeline rows."""
import ast
import base64
import html
import io
import json
import re
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageOps


@lru_cache(maxsize=8)
def image_catalog(run, dataset):
    run, dataset = Path(run), Path(dataset)
    catalog = {}
    def register(value):
        if isinstance(value, list):
            for item in value: register(item)
        elif isinstance(value, dict):
            record = value.get('record', value)
            if record.get('sha256') and (record.get('path') or value.get('bytes', {}).get('path')):
                path = Path(value.get('bytes', {}).get('path') or record['path'])
                if not path.is_absolute(): path = dataset / path
                entry = {'path':str(path), 'url':record.get('content_url'), 'landing':record.get('landing_url')}
                aliases = [value.get('image_id'), value.get('material_id'), record.get('image_id'), record.get('sha256')]
                if value.get('kind') in {'legacy_images', 'qid_images'} and 'provenance' in value:
                    from curation.v4.ops.knowledge_stages import material_id
                    mid = material_id(value)
                    aliases.extend([mid, 'I' + mid[1:]])
                for key in aliases:
                    if key: catalog[key] = entry
            for child in value.values():
                if isinstance(child, (dict, list)): register(child)
    for name in ['selected_images', 'blocks']:
        path = run / 'datasets' / (name + '.jsonl')
        if path.exists():
            for line in path.open(): register(json.loads(line))
    return catalog


@lru_cache(maxsize=256)
def thumbnail(path):
    try:
        with Image.open(path) as source:
            pic = ImageOps.exif_transpose(source).convert('RGB')
            pic.thumbnail((360, 260))
            out = io.BytesIO(); pic.save(out, 'JPEG', quality=85)
        return 'data:image/jpeg;base64,' + base64.b64encode(out.getvalue()).decode()
    except (OSError, ValueError): return None


def render_value(value, catalog):
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2)
    ids = list(dict.fromkeys(x for x in re.findall(r'(?<![\w])[IM][0-9a-f]{12}(?![\w])|(?<![\w])[0-9a-f]{64}(?![\w])', text) if x in catalog))
    pre = '<pre style="white-space:pre-wrap;min-width:160px;max-width:800px;max-height:360px;overflow:auto">' + html.escape(text) + '</pre>'
    if not ids: return pre
    cards = []
    # Different aliases for the same image share a card; original fields remain expandable.
    grouped = {}
    for key in ids: grouped.setdefault(catalog[key]['path'], []).append(key)
    for path, keys in grouped.items():
        entry = catalog[keys[0]]; src = thumbnail(path)
        picture = '<img src="'+src+'" style="max-width:280px;max-height:220px">' if src else '<div style="padding:20px;color:#777">本地缺图，暂无预览</div>'
        url = entry.get('landing') or entry.get('url')
        link = '<a href="'+html.escape(url, quote=True)+'" target="_blank" rel="noopener noreferrer">原始来源</a>' if url and url.startswith(('https://','http://')) else ''
        cards.append('<figure style="margin:6px;padding:6px;border:1px solid #ddd;max-width:300px">'+picture+'<figcaption style="overflow-wrap:anywhere">'+html.escape(' / '.join(keys))+' '+link+'</figcaption></figure>')
    return '<div style="display:flex;flex-wrap:wrap;max-height:560px;overflow:auto">'+''.join(cards)+'</div><details><summary>原始字段</summary>'+pre+'</details>'


def show(ds, columns=None, n=100, *, run, dataset):
    from IPython.display import display, HTML
    rows = ds.take(n)
    if not rows: display(HTML('<p>0 rows</p>')); return
    available = list(dict.fromkeys(k for r in rows for k in r))
    keys = [k for k in columns if k in available] if columns else available
    catalog = image_catalog(str(run), str(dataset))
    def cell(value):
        if isinstance(value, list) and value and all(isinstance(x,str) and x.startswith('data:image/') for x in value):
            return ''.join('<img style="max-width:280px;max-height:220px" src="'+html.escape(x,quote=True)+'">' for x in value)
        return render_value(value, catalog)
    display(HTML('<div style="overflow:auto"><table border="1" style="border-collapse:collapse"><thead><tr>'+''.join('<th>'+html.escape(k)+'</th>' for k in keys)+'</tr></thead><tbody>'+''.join('<tr>'+''.join('<td style="vertical-align:top">'+cell(r.get(k))+'</td>' for k in keys)+'</tr>' for r in rows)+'</tbody></table></div>'))


# The existing run freezes all notebook code, including show(). Only this display
# function may change while retaining its checkpoints. Business cells stay frozen.
MANIFEST_ADAPTER = "from curation.notebook_image_preview import preserve_display_version\nmanifest = preserve_display_version(RUN, manifest)\n"

def preserve_display_version(run, current):
    path = Path(run) / 'manifest.json'
    if not path.exists(): return current
    old = json.loads(path.read_text())
    def normalize(source):
        source = source.replace(MANIFEST_ADAPTER, '')
        tree = ast.parse(source)
        tree.body = [node for node in tree.body if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) or node.name != 'show']
        return ast.dump(tree, include_attributes=False)
    if len(old['cells']) != len(current['cells']): return current
    if any(normalize(a) != normalize(b) for a,b in zip(old['cells'], current['cells'])): return current
    # All other fields must still pass the original immutable manifest check.
    return {**current, 'cells':old['cells']}
