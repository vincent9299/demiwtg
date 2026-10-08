"""只读图片证据查看；按当前 summary 的固定引用展示，不启动任何流程。"""
import json
from pathlib import Path

STAGES = ('image_inputs', 'image_results', 'patch', 'summary')


def _module_dir(root):
    return Path(root) / 'demiwtg/preparation/images/annotation'


def _open(path, version=None):
    import lance
    if not Path(path).exists():
        return None, '未生成'
    try:
        return lance.dataset(str(path), version=version), '已提交'
    except (ValueError, OSError) as error:
        return None, '未提交或不可读: ' + str(error)


def _summary(root, run):
    table, _ = _open(_module_dir(root) / 'datasets' / f'summary__{run}.lance')
    if table is None:
        return {}, {}
    rows = table.to_table().to_pylist()
    if len(rows) != 1:
        raise ValueError('Expected exactly one run summary')
    return rows[0], json.loads(rows[0].get('details_json') or '{}')


def stage_panels(root, run):
    import pandas as pd
    summary, details = _summary(root, run)
    rows = []
    for stage in STAGES:
        ref = details.get(stage)
        path = Path(root) / ref['uri'] if ref else _module_dir(root) / 'datasets' / f'{stage}__{run}.lance'
        table, state = _open(path, ref['version'] if ref else None)
        if table is not None and stage != 'summary' and not ref:
            state = '已有阶段，未被当前摘要引用'
        rows.append({'stage': stage, 'uri': str(path), 'rows': table.count_rows() if table is not None else None,
                     'version': table.version if table is not None else None, 'state': state})
    return pd.DataFrame(rows)


def latest_stage_version(root, run, stage):
    """当前 summary 引用的版本；不把旧阶段 head 当作新运行结果。"""
    _, details = _summary(root, run)
    return (details.get(stage) or {}).get('version')


def image_result_frame(root, run, *, version=None):
    import pandas as pd
    columns = ['sha256', 'image_uri', 'status', 'richness', 'annotation_id', 'error']
    _, details = _summary(root, run)
    ref = details.get('image_results')
    if version is None and not ref:
        return pd.DataFrame(columns=columns)
    path = Path(root) / ref['uri'] if ref else _module_dir(root) / 'datasets' / f'image_results__{run}.lance'
    table, _ = _open(path, version if version is not None else ref['version'])
    return table.to_table(columns=columns).to_pandas() if table is not None else pd.DataFrame(columns=columns)


def _load_image(root, item):
    from demiflow.objects import ObjectRef
    if not item.get('image_uri'):
        return None
    try:
        return ObjectRef(item['image_uri'], item['sha256']).read()
    except (ValueError, TypeError, OSError):
        return None


def thumbnail_page(root, items, *, page=1, page_size=20, size=(200, 150)):
    """当前页缩略图 HTML：每页才读 page_size 张原图，避免全范围解码。"""
    import base64
    import io
    from html import escape
    from PIL import Image
    start = max(page - 1, 0) * page_size
    window = items[start:start + page_size]
    blocks = []
    for item in window:
        raw = _load_image(root, item)
        if raw is None:
            blocks.append(f"<div style='width:{size[0]}px'>{escape(item['sha256'][:12])}… 原图不可读</div>")
            continue
        try:
            with Image.open(io.BytesIO(raw)) as image:
                image.thumbnail(size)
                buffer = io.BytesIO()
                image.convert('RGB').save(buffer, format='JPEG', quality=80)
            url = 'data:image/jpeg;base64,' + base64.b64encode(buffer.getvalue()).decode()
            caption = escape(str(item.get('caption') or item.get('concept') or ''))
            blocks.append(
                f"<figure style='margin:4px;display:inline-block;vertical-align:top'>"
                f"<img src='{url}' style='max-width:{size[0]}px;max-height:{size[1]}px'>"
                f"<figcaption style='font-size:11px;max-width:{size[0]}px'>{caption}</figcaption></figure>")
        except (OSError, ValueError):
            blocks.append(f"<div style='width:{size[0]}px'>{escape(item['sha256'][:12])}… 解码失败</div>")
    total = len(items)
    return (f"<div>共 {total} 项，第 {start + 1}–{min(start + page_size, total)} 项</div>"
            + '<br>'.join(blocks) if blocks else '<div>当前页没有可显示的项</div>')



def run_progress(root, run):
    import pandas as pd
    summary, details = _summary(root, run)
    run_dir = _module_dir(root) / 'runs' / run
    frame = pd.DataFrame([{'key': key, 'value': value} for key, value in summary.items()
                          if key != 'details_json'])
    return summary, frame, {'phase': details.get('phase'), 'config': details.get('config'),
                             'fixed_results': details.get('image_results')}, (
        sorted(p.name for p in run_dir.iterdir()) if run_dir.exists() else [])
