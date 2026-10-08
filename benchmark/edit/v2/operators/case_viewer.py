"""Edit 固定结果的只读分页浏览；不调用模型、不修改业务表或补做检索。"""
import base64
from collections import Counter
from datetime import datetime
from html import escape
import hashlib
import io
import json
from pathlib import Path
import re
from zoneinfo import ZoneInfo

import lance
from PIL import Image, ImageOps
from demiflow import data
from demiflow.objects import ObjectRef
from demiflow.operator_llm.call_ref import read_call


MAX_CASES = 300
MAX_TEXT_BYTES = 64 * 1024**2
MAX_MEDIA_BYTES = 64 * 1024**2
MAX_MEDIA_ITEMS = 8192


def _bounded_json(value, limit=MAX_TEXT_BYTES):
    chunks, size = [], 0
    for chunk in json.JSONEncoder(ensure_ascii=False, separators=(',', ':')).iterencode(value):
        size += len(chunk.encode())
        if size > limit:
            raise ValueError('查看文字超过预算；未截断或发布部分内容')
        chunks.append(chunk)
    return ''.join(chunks)


class _Media:
    """按 SHA 共享缩略图；逐图读取 ≤32MiB/2400万像素，总编码 ≤64MiB。"""
    def __init__(self):
        self.items = {}
        self.digests = {}
        self.size = 0

    def add(self, ref):
        if not ref:
            return None
        key = ref['sha256']
        if key in self.items:
            return key
        if len(self.items) >= MAX_MEDIA_ITEMS:
            raise ValueError('查看图片数量超预算；未发布部分内容')
        item = {'uri': ref['uri'], 'sha256': key}
        try:
            raw = ObjectRef(**ref).read(max_bytes=32 * 1024**2)
            with Image.open(io.BytesIO(raw)) as original:
                if original.width * original.height > 24_000_000:
                    raise ValueError('图片超过2400万像素')
                mime = Image.MIME.get(original.format)
                # 历史请求保存的是 data URI 摘要；以实际原始字节核对，不能用缩略图替代。
                if mime:
                    digest = hashlib.sha256(('data:' + mime + ';base64,').encode())
                    digest.update(base64.b64encode(raw))
                    self.digests[digest.hexdigest()] = key
                image = ImageOps.exif_transpose(original)
                image.thumbnail((768, 768))
                buffer = io.BytesIO()
                image.convert('RGB').save(buffer, 'WEBP', quality=80, method=4)
                encoded = 'data:image/webp;base64,' + base64.b64encode(buffer.getvalue()).decode()
                item.update(src=encoded, width=image.width, height=image.height)
        except (OSError, ValueError) as exc:
            item['error'] = str(exc)
        if self.size + len(item.get('src', '')) > MAX_MEDIA_BYTES:
            raise ValueError('查看缩略图超过总预算；未隐藏图片或发布部分内容')
        self.size += len(item.get('src', ''))
        self.items[key] = item
        return key


def _call_record(call, kind, root):
    ref = call.get(kind + '_ref')
    if ref is None and kind == 'error' and call.get('response_ref'):
        ref = {**call['response_ref'], 'kind': 'error'}
    if not ref:
        return None, '未保存该调用记录'
    try:
        return read_call(ref, root), ''
    except (OSError, KeyError, ValueError) as exc:
        return None, str(exc)


def _request(call, root, media):
    request, error = _call_record(call, 'request', root)
    if request is None:
        return {'available': False, 'note': error, 'source': call.get('request_ref')}
    body = request.get('payload', request)
    messages = []
    for message in body.get('messages', []):
        parts = message['content']
        parts = [{'type': 'text', 'text': parts}] if isinstance(parts, str) else parts
        rendered = []
        for part in parts:
            if part.get('type') == 'text':
                rendered.append({'text': part['text']})
            elif part.get('type') == 'image_url':
                value = part['image_url']['url']
                digest = value.get('data_uri_sha256') if isinstance(value, dict) else hashlib.sha256(value.encode()).hexdigest()
                key = media.digests.get(digest)
                rendered.append({'image': key} if key else {
                    'text': '未能以原始字节匹配这张请求图片；未用其他图片替代。', 'record': value})
        messages.append({'role': message['role'], 'parts': rendered})
    return {'available': True, 'messages': messages, 'source': call.get('request_ref'),
            'parameters': {k: v for k, v in body.items() if k != 'messages'}}


def _process(call, root, media, selected):
    response, error = _call_record(call, 'response', root)
    if response is None:
        failure, _ = _call_record(call, 'error', root)
        response = (failure or {}).get('call', {}).get('partial_response', {})
    observations = response.get('observations', call.get('environment', {}).get('observations', []))
    operations = []
    for index, observation in enumerate(observations, 1):
        method = observation.get('call', {}).get('method')
        result = observation.get('result') or {}
        receipts = {r['image_id']: r for r in observation.get('images', [])}
        images = []
        candidates = result.get('candidates', []) if method == 'search_vectors' else []
        for rank, candidate in enumerate(candidates, 1):
            ref = {'uri': candidate['image_uri'], 'sha256': candidate['sha256']}
            identity = 'sha256:' + ref['sha256']
            receipt = receipts.pop(identity, {})
            images.append({'image': media.add(ref), 'image_id': identity, 'rank': rank,
                'distance': candidate.get('_distance'), 'status': receipt.get('status', '未保存附图回执'),
                'error': receipt.get('error'), 'selected': ref['sha256'] == selected})
        for identity, receipt in receipts.items():
            ref = receipt.get('object_ref')
            images.append({'image': media.add(ref), 'image_id': identity, 'rank': receipt.get('position'),
                'status': receipt['status'], 'error': receipt.get('error'),
                'selected': bool(ref and ref['sha256'] == selected)})
        operations.append({'index': index, 'method': method,
            'arguments': observation.get('call', {}).get('arguments'), 'result': result, 'images': images})
    # 只展示公开工具事件；不展开模型内部 reasoning 或重复 delta。
    native = {}
    for event in response.get('events', []):
        if event.get('method') not in ('item/started', 'item/completed'):
            continue
        item = event.get('params', {}).get('item', {})
        if item.get('type') in ('webSearch', 'imageGeneration', 'imageGenerationCall', 'commandExecution', 'mcpToolCall'):
            native[item.get('id', str(len(native)))] = item
    artifacts = [{'name': a['name'], 'image': media.add(a['object_ref']), 'object_ref': a['object_ref']}
                 for a in response.get('artifacts', call.get('artifacts', []))]
    return {'operations': operations, 'native': list(native.values()), 'artifacts': artifacts,
            'note': error if not response else '', 'source': call.get('response_ref')}


def _case(source, design, generation, review, root, media, rank):
    refs = json.loads(source.get('references_json') or '[]')
    call = json.loads(design.get('call_json') or '{}')
    question = design.get('question')
    seed = design.get('seed_asset')
    selected = seed['sha256'] if seed else None
    positives = [{'number': i, 'image': media.add(r['object_ref']),
                  'selected': r['object_ref']['sha256'] == selected}
                 for i, r in enumerate((r for r in refs if r['kind'] == 'image'), 1)]
    process = _process(call, root, media, selected)
    stored_source = media.add(design.get('edit_source'))
    final_image = stored_source if design.get('status') == 'candidate' else None
    answer = media.add(generation.get('object_ref'))
    result = {'rank': rank, 'concept': source['concept'], 'taxonomy': source.get('taxonomy') or [],
        'status': design.get('status', 'pending'), 'reason': design.get('reason', ''),
        'question': question, 'materials': [r for r in refs if r['kind'] != 'image'],
        'positives': positives, 'seed': media.add(seed), 'final_image': final_image,
        'source_diagnostic': stored_source if final_image is None else None,
        'source_kind': ('合成原图' if question and question.get('source_artifact') else '直接使用种子图') if final_image else '尚无可交付原图',
        'process': process, 'answer': answer, 'generation_status': generation.get('status'),
        'generation_reason': generation.get('reason', ''), 'generation_call': json.loads(generation.get('call_json') or '{}'),
        'review_status': review.get('review_status'), 'review_reason': review.get('review_reason', ''),
        'review': review.get('review'), 'case_category': review.get('case_category'),
        'author_call': call, 'author_input': _request(call, root, media)}
    result['review_input'] = _request(json.loads(review.get('review_call_json') or '{}'), root, media)
    sent = result['generation_call'].get('request', {})
    result['generation_input'] = {'available': bool(sent), 'request': sent,
        'image': final_image if sent.get('source_sha256') == final_image else None,
        'note': '' if sent.get('source_sha256') == final_image and final_image else
                '未保存可匹配的实际作答图片输入；不按当前模板补写。'}
    return result


def build_case_browser(project, run_id):
    """最多300概念，沿已提交摘要读固定版本；运行中独立快照另行标记。"""
    if not re.fullmatch(r'[A-Za-z0-9_-]+', run_id):
        raise ValueError('Invalid run_id')
    project = Path(project)
    owner = project / 'benchmark/edit/v2/datasets'
    summary_uri = str(owner / f'summary__{run_id}.lance')
    if not Path(summary_uri).exists():
        raise ValueError('本轮尚无已提交摘要；未读取其他运行替代')
    summary_ref = {'uri': summary_uri, 'version': lance.dataset(summary_uri).version}
    summary = data.read_lance(**summary_ref).take(1)[0]
    columns = {'inputs': ['concept', 'taxonomy', 'references_json'],
        'designs': ['concept', 'status', 'reason', 'question', 'seed_asset', 'edit_source', 'call_json'],
        'generations': ['concept', 'status', 'reason', 'object_ref', 'call_json'],
        'reviews': ['concept', 'review_status', 'review_reason', 'review', 'case_category', 'review_call_json']}
    stages, snapshots, unfinalized = {}, {}, []
    for stage, projection in columns.items():
        ref = summary.get(stage)
        path = owner / f'{stage}__{run_id}.lance'
        if not ref and summary['status'] == 'running' and path.exists():
            ref = {'uri': str(path), 'version': lance.dataset(str(path)).version}
            unfinalized.append(stage)
        snapshots[stage] = ref
        if not ref:
            stages[stage] = {}
            continue
        if lance.dataset(ref['uri'], version=ref['version']).count_rows() > MAX_CASES:
            raise ValueError('完整查看超过300概念；请显式缩小运行范围，不发布截断结果')
        stages[stage] = {r['concept']: r for r in data.read_lance(**ref, columns=projection).take(MAX_CASES)}
    media, cases, text_size = _Media(), [], 0
    for rank, (concept, source) in enumerate(stages['inputs'].items(), 1):
        case = _case(source, *(stages[s].get(concept, {}) for s in ('designs', 'generations', 'reviews')),
                     project.parent, media, rank)
        text_size += len(_bounded_json(case).encode())
        if text_size > MAX_TEXT_BYTES:
            raise ValueError('查看文字超过总预算；未发布部分题目')
        cases.append(case)
    scores = [c['review']['score'] for c in cases if c['review'] and c['review'].get('score') is not None and c['review']['score'] >= 0]
    meta = {'run': run_id, 'updated_at': datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(),
        'status': summary['status'], 'complete': summary['complete'], 'error': summary.get('error'),
        'summary': summary_ref, 'snapshots': snapshots, 'independent_running_snapshots': unfinalized,
        'concepts': len(cases), 'author': dict(Counter(c['status'] for c in cases)),
        'generations': dict(Counter(c['generation_status'] for c in cases if c['generation_status'])),
        'reviews': dict(Counter(c['review']['verdict'] for c in cases if c['review'])),
        'scored': len(scores), 'mean_score': sum(scores) / len(scores) if scores else None,
        'scene_search_calls': sum(o['method'] == 'search_vectors' for c in cases for o in c['process']['operations']),
        'media_count': len(media.items), 'media_bytes': media.size,
        'media_errors': sum('error' in m for m in media.items.values())}
    payload = _bounded_json({'meta': meta, 'cases': cases, 'media': media.items}, MAX_TEXT_BYTES + MAX_MEDIA_BYTES)
    html = Path(__file__).with_suffix('.html').read_text().replace('__CASE_DATA__', payload.replace('<', '\\u003c'))
    return html, meta


def notebook_browser(document, relative_path, *, height=1500):
    if type(height) is not int or not 600 <= height <= 4000:
        raise ValueError('Notebook viewer height must be in 600..4000')
    return ('<p>翻页、搜索、筛选和图片放大无需重新运行代码。'
            '<a href="' + escape(relative_path, quote=True) + '">打开完整浏览页</a>；'
            '重新运行本格才刷新结果快照。</p>'
            '<iframe title="Edit 出题全过程与评审" sandbox="allow-scripts allow-popups" '
            f'style="width:100%;height:{height}px;border:1px solid #d8e0e9;border-radius:12px" '
            'srcdoc="' + escape(document, quote=True) + '"></iframe>')
