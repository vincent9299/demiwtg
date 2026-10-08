"""查看固定快照：检索附图、种子、最终原图及作答不可混淆。"""
import io
import json

import lance
import pyarrow as pa
import pytest
from PIL import Image
from demiflow.objects import LocalObjectStore
from benchmark.edit.v2.operators import case_viewer as viewer


def _image(root, color):
    buffer = io.BytesIO()
    Image.new('RGB', (120, 80), color).save(buffer, format='PNG')
    return LocalObjectStore(root / 'objects').put(buffer.getvalue()).to_dict()


def _table(owner, name, rows):
    path = owner / (name + '__sample.lance')
    lance.write_dataset(pa.Table.from_pylist(rows), str(path))
    return {'uri': str(path), 'version': 1}


@pytest.fixture
def snapshot(tmp_path, monkeypatch):
    project = tmp_path / 'demiwtg'
    owner = project / 'benchmark/edit/v2/datasets'
    owner.mkdir(parents=True)
    positive, retrieved, unused, final, answer = [_image(tmp_path, c)
        for c in ('red', 'green', 'blue', 'white', 'black')]
    instruction = '完整题面' * 300 + '</script><script>window.INJECTED=1</script>末尾'
    question = {'instruction': instruction, 'source_image_edit': '移除干扰项，构造原图',
        'source_artifact': 'source.png', 'test_points': [{'point': '结构', 'basis': '来源', 'criterion': '实际可见'}],
        'source_image_checks': ['目标位置清楚可见'], 'source_image_check': {'status': 'passed',
            'reason': '', 'observations': [{'check': 1, 'passed': True, 'evidence': '清楚可见'}]}}
    response = {'observations': [
        {'call': {'method': 'map_embeddings', 'arguments': {'text': '公园的空座椅'}}, 'result': {'dimensions': 4096}},
        {'call': {'method': 'search_vectors', 'arguments': {'query_ref': {'uri': 'query'}, 'top_k': 2}},
         'result': {'candidates': [{'image_uri': r['uri'], 'sha256': r['sha256'], '_distance': .1 + i}
                                   for i, r in enumerate((retrieved, unused))]},
         'images': [{'image_id': 'sha256:' + r['sha256'], 'object_ref': r, 'position': i + 1,
                     'status': status} for i, (r, status) in enumerate(((retrieved, 'attached'), (unused, 'not_attached_budget')))]}],
        'artifacts': [{'name': 'source.png', 'object_ref': final}],
        'events': [{'method': phase, 'params': {'item': {'type': 'webSearch', 'id': 'web-1',
            'query': '查询', 'results': [{'title': '<script>标题</script>', 'url': 'https://example.com',
                'thumbnail_url': 'https://example.com/thumbnail.jpg'}]}}}
            for phase in ('item/started', 'item/completed')]}
    records = {('author', 'response'): response, ('author', 'request'): {
        'messages': [{'role': 'user', 'content': [{'type': 'text', 'text': instruction}]}]}}
    def read(ref, root):
        return records[(ref['request_id'], ref['kind'])]
    monkeypatch.setattr(viewer, 'read_call', read)
    call = {k + '_ref': {'request_id': 'author', 'kind': k} for k in ('response', 'request')}
    inputs = [{'concept': c, 'taxonomy': [], 'references_json': json.dumps([
        {'kind': 'image', 'object_ref': positive}])} for c in ('有检索', '合成失败', '等待出题')]
    designs = [{'concept': '有检索', 'status': 'candidate', 'reason': '', 'question': question,
        'seed_asset': retrieved, 'edit_source': final, 'call_json': json.dumps(call)},
        {'concept': '合成失败', 'status': 'source_check_failed', 'reason': 'bwrap permission denied',
         'question': {**question, 'source_artifact': None}, 'seed_asset': positive,
         'edit_source': positive, 'call_json': '{}'}]
    generations = [{'concept': '有检索', 'status': 'generated', 'reason': '', 'object_ref': answer,
        'call_json': json.dumps({'request': {'prompt': instruction, 'source_sha256': final['sha256']}})}]
    reviews = [{'concept': '有检索', 'review_status': 'reviewed', 'review_reason': '',
        'review': {'verdict': 'inconclusive', 'score': -1, 'reason': '遮挡', 'basis': '', 'point_results': []},
        'case_category': None, 'review_call_json': '{}'}]
    refs = {name: _table(owner, name, rows) for name, rows in [
        ('inputs', inputs), ('designs', designs), ('generations', generations), ('reviews', reviews)]}
    _table(owner, 'summary', [{'status': 'incomplete', 'complete': False, 'error': '', **refs}])
    # 后续版本内容故意改变；浏览必须仍使用 summary 固定的 @1。
    lance.write_dataset(pa.Table.from_pylist([{**designs[0], 'reason': '不应出现的新版本'}]), refs['designs']['uri'], mode='overwrite')
    return project, (positive, retrieved, unused, final, answer), instruction


def test_fixed_sources_retrieval_receipts_final_source_and_failures(snapshot):
    project, refs, instruction = snapshot
    document, meta = viewer.build_case_browser(project, 'sample')
    payload = json.loads(document.split('<script id="case-data" type="application/json">')[1].split('</script>')[0])
    case, failed, pending = payload['cases']
    assert meta['concepts'] == 3 and meta['scene_search_calls'] == 1
    assert meta['scored'] == 0 and meta['mean_score'] is None
    assert meta['snapshots']['designs']['version'] == 1
    assert case['question']['instruction'] == instruction
    assert case['author_input']['messages'][0]['parts'][0]['text'] == instruction
    assert case['positives'][0]['image'] == refs[0]['sha256']
    assert case['seed'] == refs[1]['sha256'] and case['final_image'] == refs[3]['sha256']
    assert case['answer'] == refs[4]['sha256'] and case['generation_input']['image'] == case['final_image']
    candidates = case['process']['operations'][1]['images']
    assert [i['status'] for i in candidates] == ['attached', 'not_attached_budget']
    assert [i['selected'] for i in candidates] == [True, False]
    assert candidates[1]['image'] == refs[2]['sha256']
    assert len(case['process']['native']) == 1
    assert case['process']['artifacts'][0]['image'] == case['final_image']
    assert failed['seed'] and failed['final_image'] is None and failed['answer'] is None
    assert failed['source_diagnostic'] == refs[0]['sha256']
    assert pending['status'] == 'pending'
    assert '</script><script>window.INJECTED' not in document
    assert meta['media_count'] == 5 and meta['media_errors'] == 0


def test_viewer_resource_limits_fail_explicitly(snapshot, monkeypatch):
    project, _, _ = snapshot
    monkeypatch.setattr(viewer, 'MAX_MEDIA_BYTES', 1)
    with pytest.raises(ValueError, match='未隐藏图片'):
        viewer.build_case_browser(project, 'sample')
    monkeypatch.setattr(viewer, 'MAX_MEDIA_BYTES', 64 * 1024**2)
    monkeypatch.setattr(viewer, 'MAX_CASES', 1)
    with pytest.raises(ValueError, match='不发布截断结果'):
        viewer.build_case_browser(project, 'sample')


def test_browser_navigation_images_filters_and_escaping(snapshot):
    from playwright.sync_api import sync_playwright
    project, _, instruction = snapshot
    document, _ = viewer.build_case_browser(project, 'sample')
    with sync_playwright() as play:
        browser = play.chromium.launch(executable_path=play.chromium.executable_path, args=['--no-sandbox'])
        page = browser.new_page()
        errors, requests = [], []
        page.on('pageerror', lambda e: errors.append(str(e)))
        page.on('request', lambda r: requests.append(r.url))
        page.set_content(document)
        assert page.locator('[data-field=instruction]').inner_text() == instruction
        assert page.locator('[data-field=final-source] img').count() == 1
        assert page.locator('[data-field=answer] img').count() == 1
        assert page.evaluate('window.INJECTED') is None
        page.locator('[data-field=final-source] img').click()
        assert page.locator('#image-dialog').is_visible()
        page.click('#close-image')
        page.select_option('#status', 'technical')
        assert '2 个概念' in page.locator('#count').inner_text()
        assert page.locator('[data-field=final-source] img').count() == 0
        page.click('#next')
        assert '等待出题' in page.locator('.card h2').inner_text()
        page.select_option('#status', 'retrieved')
        assert '1 个概念' in page.locator('#count').inner_text()
        assert page.locator('.gallery .selected').count() >= 1
        assert not errors
        assert not any(u.startswith('https://example.com') for u in requests)
        browser.close()
