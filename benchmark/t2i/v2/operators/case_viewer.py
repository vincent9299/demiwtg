"""只读结果浏览：冻结已提交版本，导出可离线翻页的 HTML；不调用模型。"""
from collections import Counter
from datetime import datetime
from html import escape
from pathlib import Path
from zoneinfo import ZoneInfo
import base64
import io
import json

import lance
from PIL import Image
from demiflow import data
from demiflow.operator_llm.call_ref import read_call

from .images import positive_image_data_url
from .run_tables import RunTables

MAX_CASES = 1000
MAX_ROW_BYTES = 512 * 1024
MAX_TEXT_BYTES = 16 * 1024 * 1024
MAX_MEDIA_BYTES = 64 * 1024 * 1024
MAX_DOCUMENT_BYTES = 96 * 1024 * 1024


def _json(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'))


def _rows(ref, columns, *, predicate=None, maximum=MAX_CASES):
    """只投影查看所需列；逐行检查，再保留这次有限浏览快照。"""
    if not ref:
        return []
    result, size = [], 0
    dataset = data.read_lance(**ref, columns=columns, filter=predicate, batch_size=1)
    for row in dataset.iter_rows():
        row_bytes = len(_json(row).encode())
        size += row_bytes
        if len(result) >= maximum or row_bytes > MAX_ROW_BYTES or size > MAX_TEXT_BYTES:
            raise ValueError('浏览快照超出行数或文字预算；请显式缩小查看范围，不截断题目。')
        result.append(row)
    return result


def _stage_refs(state):
    probe = state.get('probe') or {}
    refs = dict(probe.get('outputs', {}))
    if state.get('phase') == 'probing':
        for name, uri in probe.get('stage_uris', {}).items():
            if Path(uri).exists():
                version = lance.dataset(uri).version
                if version > probe.get('start_versions', {}).get(name, version):
                    refs[name] = {'uri': uri, 'version': version}
    for name in ('designs', 'candidates'):
        if name not in refs and state.get(name):
            refs[name] = state[name]
    return refs


def build_case_browser(project, run_id):
    """读取固定表并生成有限静态浏览快照；返回 HTML 和可审计的来源摘要。"""
    project = Path(project)
    root = project.parent
    owner = project / 'benchmark/t2i/v2'
    run_dir = owner / 'runs' / run_id
    configuration = json.loads((run_dir / 'config.json').read_text())
    records = RunTables(root, str(owner / 'datasets' / f'records__{run_id}.lance'))
    state = records.load() or {}
    cohort_ref = configuration.get('cohort_source') or state.get('inputs')
    if not cohort_ref:
        raise ValueError('本轮尚未提交固定名单，不能以旧结果代替。')
    cohort = _rows(cohort_ref, ['concept', 'selection_rank', 'positive_image_count',
                              'sampling_category', 'concept_record', 'positive_images'])
    cohort.sort(key=lambda row: row['selection_rank'])
    names = {row['concept'] for row in cohort}
    if len(names) != len(cohort):
        raise ValueError('固定名单含重复概念。')
    predicate = 'concept IN (' + ','.join("'" + name.replace("'", "''") + "'" for name in sorted(names)) + ')'
    category_file = run_dir / 'case_category_sources.json'
    category_sources = json.loads(category_file.read_text()) if category_file.exists() else []
    categories = {name: [] for name in names}
    for source in category_sources:
        reason = 'classification_reason' if source['kind'] == 'classifications' else 'adjudication_reason'
        columns = ['concept', 'id', 'image_sha256', 'final_category', reason]
        if source['kind'] == 'adjudications':
            columns.append('final_selected')
        for row in _rows(source['result'], columns, predicate=predicate, maximum=MAX_CASES * 4):
            categories[row['concept']].append({
                'code': row['final_category'], 'selected': row.get('final_selected', True),
                'reason': row[reason], 'source': source['label'], 'result': source['result'],
                'case_id': row['id'], 'image_sha256': row['image_sha256']})
    paired = bool(configuration.get('authoring_variants'))
    variants = configuration.get('authoring_variants') or [configuration['authoring_variant']]
    stages, by_variant = {}, {}
    for variant in variants:
        branch_id = run_id + '__' + variant if paired else run_id
        branch = RunTables(root, str(owner / 'datasets' / f'records__{branch_id}.lance')).load() if paired else state
        refs = _stage_refs(branch or {})
        stages[variant] = refs
        views = {}
        for stage, columns in {
            'designs': ['concept', 'status', 'reason', 'question', 'call_json', 'authoring_context_json'],
            'generations': ['concept', 'task_id', 'status', 'reason', 'object_ref', 'model', 'seed', 'width', 'height'],
            'reviews': ['concept', 'review_status', 'review_reason', 'review', 'case_category', 'review_call_json', 'generation_source'],
        }.items():
            rows = _rows(refs.get(stage), columns)
            views[stage] = {row['concept']: row for row in rows}
            if len(views[stage]) != len(rows):
                raise ValueError('同概念存在重复结果：' + stage)
        by_variant[variant] = views
    media, media_bytes = {}, 0

    def preview(ref, side):
        nonlocal media_bytes
        key = str(side) + ':' + ref['sha256']
        if key in media:
            return key
        try:
            encoded = positive_image_data_url(ref)
            with Image.open(io.BytesIO(base64.b64decode(encoded.split(',', 1)[1]))) as picture:
                picture.thumbnail((side, side))
                output = io.BytesIO()
                picture.convert('RGB').save(output, format='JPEG', quality=78 if side > 200 else 70)
            value = 'data:image/jpeg;base64,' + base64.b64encode(output.getvalue()).decode()
            if len(value) > 1024 * 1024:
                raise ValueError('单张查看缩略图超过 1 MiB')
            media_bytes += len(value)
            if media_bytes > MAX_MEDIA_BYTES:
                raise ValueError('查看缩略图总量超过 64 MiB')
            media[key] = {'src': value, 'original_uri': ref['uri']}
        except (OSError, ValueError) as error:
            if 'MiB' in str(error):
                raise
            media[key] = {'error': str(error), 'original_uri': ref['uri']}
        return key

    cases = []
    for item in cohort:
        name = item['concept']
        case = {'concept': name, 'rank': item['selection_rank'],
                'taxonomy': item['sampling_category'], 'positive_count': item['positive_image_count'],
                'definition': (item.get('concept_record') or {}).get('definition', ''),
                'categories': categories[name], 'variants': [], 'references': []}
        case['category_codes'] = sorted({r['code'] for r in categories[name]
                                         if r['selected'] and r['code'] in ('1', '2', '3')})
        for variant in variants:
            views = by_variant[variant]
            design = views['designs'].get(name) or {}
            generation = views['generations'].get(name) or {}
            review = views['reviews'].get(name) or {}
            call = json.loads(design.get('call_json') or '{}')
            branch = {'variant': variant, 'status': design.get('status', 'pending'),
                      'reason': design.get('reason') or '', 'question': design.get('question'),
                      'author_call': call, 'author_context': json.loads(design.get('authoring_context_json') or '[]'),
                      'generation': generation, 'review': review}
            if generation.get('object_ref'):
                branch['image'] = preview(generation['object_ref'], 640)
            # 失败的评审原文按已有精确响应引用读取；绝不重发请求。
            if review and review.get('review_status') != 'reviewed':
                review_call = json.loads(review.get('review_call_json') or '{}')
                if review_call.get('response_ref'):
                    saved = read_call(review_call['response_ref'], root)
                    body = saved.get('body') or {}
                    branch['raw_review'] = ((body.get('choices') or [{}])[0].get('message') or {}).get('content')
            case['variants'].append(branch)
        for ref in item.get('positive_images') or []:
            case['references'].append(preview({'uri': ref['image_uri'], 'sha256': ref['sha256']}, 160))
        if len(_json(case).encode()) > MAX_ROW_BYTES:
            raise ValueError('单题查看内容超出预算，保留全文但不发布截断页面：' + name)
        cases.append(case)
    selection_file = owner / 'runs' / Path(cohort_ref['uri']).stem.removeprefix('cohort__') / 'category1_audit.json'
    coverage = json.loads(selection_file.read_text()) if selection_file.exists() else None
    meta = {'run': run_id, 'refreshed_at': datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(),
            'phase': state.get('phase'), 'complete': state.get('complete', False),
            'scope': len(cases), 'cohort': cohort_ref, 'stages': stages,
            'counts': state.get('counts', {}), 'probe_counts': (state.get('probe') or {}).get('counts', {}),
            'category_sources': category_sources,
            'category1_coverage': coverage,
            'valid_verdicts': dict(Counter(branch['review']['review']['verdict']
                for case in cases for branch in case['variants']
                if branch['review'].get('review_status') == 'reviewed'))}
    text_payload = _json({'meta': meta, 'cases': cases})
    if len(text_payload.encode()) > MAX_TEXT_BYTES:
        raise ValueError('查看文字总量超过 16 MiB；未截断或发布部分题目。')
    payload = _json({'meta': meta, 'cases': cases, 'media': media}).replace('<', '\\u003c')
    template = Path(__file__).with_suffix('.html').read_text()
    document = template.replace('__CASE_DATA__', payload)
    if len(document.encode()) > MAX_DOCUMENT_BYTES:
        raise ValueError('浏览文件超过 96 MiB；未发布不完整页面。')
    return document, meta


def notebook_browser(document, relative_path, *, height=860):
    """一个固定高度的输出容纳全部前端分页；srcdoc 无需 kernel、CDN 或网络。"""
    if type(height) is not int or not 600 <= height <= 4000:
        raise ValueError('Notebook viewer height must be in 600..4000')
    return ('<div><p>按钮翻页、搜索和筛选均在页面内完成，不需要运行代码。'
            '若 VS Code 限制内嵌交互，可打开完整离线文件：'
            '<a href="' + escape(relative_path, quote=True) + '">case_browser.html</a>。'
            '上游结果更新后才需要重新运行查看格刷新快照。</p>'
            '<iframe title="T2I 完整题目浏览器" sandbox="allow-scripts allow-popups" '
            f'style="width:100%;height:{height}px;border:1px solid #d8e0e9;border-radius:12px" '
            'srcdoc="' + escape(document, quote=True) + '"></iframe></div>')
