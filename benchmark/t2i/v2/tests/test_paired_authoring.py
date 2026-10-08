from benchmark.t2i.v2.tests.agent_fixture import agent_arguments
"""固定正例门槛、配对输入、工具修复及完整交付；只使用隔离湖和模拟模型。"""
from copy import deepcopy
from pathlib import Path
import io
import json
import re

import httpx
import lance
import pyarrow as pa
import pytest
from PIL import Image
from demiflow import data
from demiflow.objects import LocalObjectStore
from preparation.concepts.operators.schema import ADOPTED
from preparation.concept_positive_images.operators.schema import RESULTS
from project import resolve_root
from benchmark.t2i.v2.t2i_v2_benchmark_pipeline import config, run_pipeline, DATASETS
from benchmark.t2i.v2.operators.comparison import VARIANTS
from test_concept_context import adopted, rows


@pytest.fixture
def sources(adopted):
    root = resolve_root()
    concepts, reviews = [], []
    store = LocalObjectStore(root / 'pixels')
    for i, category in enumerate(['A / a', 'A / b', 'B / a', 'B / b']):
        item = deepcopy(adopted)
        item.update(concept_id=f'C{i}', assessment_id=f'A{i}', original_name=f'概念{i}',
                    taxonomy=[], old_taxonomy=[category])
        item['assessment']['canonical_name'] = f'概念{i}'
        concepts.append(item)
        for j in range(3):
            encoded = io.BytesIO()
            Image.new('RGB', (8, 8), (i*50, j*80, 50)).save(encoded, format='PNG')
            ref = store.put(encoded.getvalue())
            reviews.append(dict(concept_id=f'C{i}', assessment_id=f'A{i}', sha256=ref.sha256,
                image_uri=ref.uri, status='reviewed', aligned=True,
                combined_review={'decision':'match','target_region':'全部','evidence':'测试','limitations':''}))
    uri = str(root / 'adopted.lance')
    lance.write_dataset(pa.Table.from_pylist(concepts, schema=ADOPTED), uri)
    review = str(root / 'reviews.lance')
    lance.write_dataset(pa.Table.from_pylist(reviews, schema=RESULTS), review)
    return {'uri':uri,'version':1}, {'uri':review,'version':1}


def configuration(sources, **changes):
    values = dict(run=resolve_root()/DATASETS/'paired', concept_audit_source=sources[0],
        positive_review_sources=[sources[1], sources[1]], authoring_variants=VARIANTS,
        min_positive_images=3, max_reference_images=2, sample_size=3, sample_seed=8,
        document_reads=True, through='inputs')
    values.update(changes)
    return config(**agent_arguments(resolve_root(), **values))


def test_oversized_positive_image_is_a_row_failure_before_pixel_decoding(tmp_path):
    import struct
    import zlib
    from benchmark.t2i.v2.operators.authoring import prepare_request
    encoded = io.BytesIO()
    Image.new('RGB', (1, 1)).save(encoded, format='PNG')
    raw = encoded.getvalue()
    # 有效 PNG 头宣称 1.69 亿像素；超过隔离缩图的源图预算，不能尝试解码。
    header = struct.pack('>II', 13000, 13000) + raw[24:29]
    raw = raw[:16] + header + struct.pack('>I', zlib.crc32(b'IHDR' + header)) + raw[33:]
    ref = LocalObjectStore(tmp_path).put(raw)
    row = dict(concept='巨大图片', status='ready', evidence_json='[]', reason=None,
               authoring_variant='with_positive_images', authoring_images_json=json.dumps([
                   {'number':1, 'object_ref':ref.to_dict()}]))
    result = prepare_request(row, max_context_chars=60000, prompt_chars=0)
    assert result['status'] == 'invalid_positive_image'
    assert 'source pixel limit' in result['reason']
    assert result['prompt_images'] == []


def test_large_jpeg_is_downsampled_before_decode_and_reaches_authoring(tmp_path, monkeypatch):
    import base64
    from PIL import JpegImagePlugin
    from benchmark.t2i.v2.operators.authoring import prepare_request

    # 与侗琵琶失败原图同尺寸；旋转信息仍须保留，送出的图不裁剪、不拉伸。
    encoded = io.BytesIO()
    exif = Image.Exif()
    exif[274] = 6
    with Image.new('RGB', (6720, 4480), (20, 80, 140)) as image:
        image.save(encoded, format='JPEG', exif=exif)
    ref = LocalObjectStore(tmp_path).put(encoded.getvalue())
    loaded_sizes = []
    original_load = JpegImagePlugin.JpegImageFile.load

    def bounded_load(image):
        loaded_sizes.append(image.size)
        assert image.width * image.height <= 24_000_000
        return original_load(image)

    monkeypatch.setattr(JpegImagePlugin.JpegImageFile, 'load', bounded_load)
    row = dict(concept='大尺寸正例', status='ready', evidence_json='[]', reason=None,
               authoring_variant='with_positive_images', authoring_images_json=json.dumps([
                   {'number':1, 'object_ref':ref.to_dict()}]))
    result = prepare_request(row, max_context_chars=60000, prompt_chars=0)
    assert result['status'] == 'ready' and len(result['prompt_images']) == 1
    assert loaded_sizes and loaded_sizes[0] != (6720, 4480)
    with Image.open(io.BytesIO(base64.b64decode(result['prompt_images'][0].split(',', 1)[1]))) as image:
        assert image.format == 'JPEG' and image.mode == 'RGB'
        assert image.size == (1024, 1536)


def test_large_png_uses_isolated_resize_before_authoring(tmp_path, monkeypatch):
    import base64
    from PIL import PngImagePlugin
    from benchmark.t2i.v2.operators.authoring import prepare_request
    encoded = io.BytesIO()
    with Image.new('RGB', (6000, 4200), (20, 80, 140)) as image:
        image.save(encoded, format='PNG')
    ref = LocalObjectStore(tmp_path).put(encoded.getvalue())
    original_load = PngImagePlugin.PngImageFile.load
    def bounded_parent_load(image, *args, **kwargs):
        assert image.width * image.height <= 24_000_000
        return original_load(image, *args, **kwargs)
    monkeypatch.setattr(PngImagePlugin.PngImageFile, 'load', bounded_parent_load)
    row = dict(concept='大PNG', status='ready', evidence_json='[]', reason=None,
               authoring_variant='with_positive_images', authoring_images_json=json.dumps([
                   {'number':1, 'object_ref':ref.to_dict()}]))
    result = prepare_request(row, max_context_chars=60000, prompt_chars=0)
    assert result['status'] == 'ready', result['reason']
    with Image.open(io.BytesIO(base64.b64decode(result['prompt_images'][0].split(',', 1)[1]))) as image:
        assert image.size == (1536, 1075)


def test_balanced_cohort_counts_unique_same_assessment_images_and_repeats(sources):
    cfg = configuration(sources)
    first = run_pipeline(cfg)
    selected = sorted(rows(first['inputs']), key=lambda r:r['selection_rank'])
    assert len(selected) == 3 and {r['sampling_category'].split(' / ')[0] for r in selected} == {'A','B'}
    assert all(r['positive_image_count'] == 3 and len(r['positive_images']) == 2 for r in selected)
    second = run_pipeline(cfg)
    assert sorted(rows(second['inputs']), key=lambda r:r['selection_rank']) == selected
    assert first['phase'] == 'inputs' and not first['complete']
    with pytest.raises(ValueError, match='Only 4'):
        run_pipeline(configuration(sources, sample_size=5))


def test_stale_assessment_and_conflicting_reviews_do_not_meet_threshold(sources):
    root = resolve_root()
    old = lance.dataset(**sources[1]).to_table().to_pylist()
    changed = deepcopy(old)
    for row in changed:
        if row['concept_id'] == 'C0':
            row['assessment_id'] = 'stale'
    # C1有同一审定的矛盾有效判断，此SHA不作为正例；C0另一次旧版图审不能补足新版本。
    changed += [{**deepcopy(old[3]), 'aligned':False, 'combined_review':{
        'decision':'mismatch','target_region':'','evidence':'conflict','limitations':''}}]
    uri = str(root/'changed.lance')
    lance.write_dataset(pa.Table.from_pylist(changed, schema=RESULTS), uri)
    state = run_pipeline(configuration(sources, positive_review_sources=[{'uri':uri,'version':1}], sample_size=2))
    assert {r['concept_id'] for r in rows(state['inputs'])} == {'C2','C3'}


def test_common_revision_uses_original_labels_and_replaces_professional_with_sparse_positive(sources):
    base = run_pipeline(configuration(sources))
    previous = sorted(rows(base['inputs']), key=lambda r:r['selection_rank'])
    missing = next(i for i in range(4) if f'C{i}' not in {r['concept_id'] for r in previous})
    labels = [{'concept': r['concept'], 'final_category': '3' if i < 2 else '2'}
              for i, r in enumerate(previous)] + [{'concept': f'概念{missing}', 'final_category': '1'}]
    root = resolve_root()
    label_uri = str(root / 'labels.lance')
    lance.write_dataset(pa.Table.from_pylist(labels), label_uri)
    images = rows(sources[1])
    sparse = [r for r in images if r['concept_id'] != f'C{missing}'] + [
        next(r for r in images if r['concept_id'] == f'C{missing}')]
    image_uri = str(root / 'sparse.lance')
    lance.write_dataset(pa.Table.from_pylist(sparse, schema=RESULTS), image_uri)
    excluded = next(r['sha256'] for r in images if r['concept_id'] == previous[1]['concept_id'])
    revised = run_pipeline(configuration(sources, run=root / DATASETS / 'revised',
        positive_review_sources=[{'uri': image_uri, 'version': 1}],
        cohort_revision={'base_cohort': base['inputs'], 'common_limit': 1, 'min_common_positive_images': 1,
            'image_exclusions': [{'sha256': excluded, 'reason': 'fixture decode failure'}],
            'case_category_sources': [{'uri': label_uri, 'version': 1, 'kind': 'classifications'}]}))
    chosen = sorted(rows(revised['inputs']), key=lambda r:r['selection_rank'])
    assert [r['concept'] for r in chosen] == [previous[0]['concept'], f'概念{missing}', previous[2]['concept']]
    assert chosen[1]['positive_image_count'] == 1 and len(chosen[1]['positive_images']) == 1
    assert revised['selection']['revision']['image_exclusions'][0]['sha256'] == excluded
    assert revised['selection']['revision']['removed'] == [previous[1]['concept']]
    assert revised['selection']['revision']['selected_common_count'] == 1
    assert sorted(rows(base['inputs']), key=lambda r:r['selection_rank']) == previous


def test_common_revision_keeps_conflicts_and_caps_target():
    from benchmark.t2i.v2.operators.comparison import revise_cohort
    items = [{'concept': str(i), 'concept_id': str(i), 'assessment_id': 'a',
              'sampling_category': 'x', 'base_rank': i + 1 if i < 3 else None,
              'original_categories': cats}
             for i, cats in enumerate([['2', '3'], ['3'], ['1'], ['1'], ['1']])]
    result = revise_cohort({'group': 'all', 'count': 5, 'bytes': 100, 'items': items},
                           size=3, seed=0, common_limit=2)
    assert result['selected'][0]['concept'] == '0'  # 有类别2冲突的旧项优先保留。
    assert result['selected'][2]['concept'] == '2'
    assert result['revision']['removed'] == ['1']
    assert result['revision']['selected_common_count'] == 2


def test_two_conditions_use_same_concept_and_repair_tools_without_answer_image_leak(sources, monkeypatch):
    cfg = configuration(sources, through='author', mode='modelhub', model='fixture',
        model_deployment_id='fixture-deployment', model_revision='fixture-deployment-v1',
        sample_size=1, max_calls=3)
    posted = []
    def handler(request):
        if request.method == 'GET':
            return httpx.Response(200,json={'data':[{'id':'fixture'}]})
        body = json.loads(request.content)
        assert body['model'] == 'fixture-deployment'
        posted.append(body)
        turn = (len(posted)-1) % 3
        text = json.dumps(body, ensure_ascii=False)
        parts = [m['content'] if isinstance(m['content'],str) else ''.join(
            p.get('text','') for p in m['content']) for m in body['messages']]
        environment = json.loads(''.join(parts).split('当前执行环境与已返回的结果：')[-1].strip())
        assert environment['current_turn'] == turn + 1
        assert environment['remaining_turns'] == 4 - turn
        assert environment['used_operator_calls'] == turn
        assert environment['limits']['max_calls_per_turn'] == 2
        if turn == 0:
            response = {'api_calls':[{'method':'read_documents','arguments':{'document_ids':['D1']}}], 'response':None}
        elif turn == 1:
            assert 'invalid_arguments' in text
            response = {'api_calls':[{'method':'read_documents','arguments':{
                'request': {'documents':[environment['resources']['D1']],
                'questions':[{'id':'scope','text':'前后文的适用条件'}],
                'new_chars':6000,'total_chars':6000}}}], 'response':None}
        else:
            assert '未引用的正文' in text
            response = {'api_calls':[], 'response':{'result':{'question':{
                'instruction':'生成该概念的测试图', 'test_points':[{
                    'point':'测试结构','basis':'审定材料以及D1原文','criterion':'结构可见且符合测试关系'}]}}}}
        return httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{
            'content':json.dumps(response,ensure_ascii=False)}}]})
    original = httpx.AsyncClient
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kw:original(transport=httpx.MockTransport(handler),**kw))
    state = run_pipeline(cfg)
    actual = rows(state['designs'])
    assert state['complete'] and len(actual) == 2 and len(posted) == 6
    assert len({r['concept'] for r in actual}) == 1
    assert {r['authoring_variant'] for r in actual} == set(VARIANTS)
    for i, row in enumerate(actual):
        assert row['evidence_json'] == '[]' and row['status'] == 'candidate'
        history = json.loads(row['authoring_context_json'])
        assert history[0]['result']['code'] == 'invalid_arguments'
        assert len(json.loads(row['call_json'])['environment']['turns']) == 3
    image_counts = [sum(p.get('type')=='image_url' for m in b['messages'] if isinstance(m['content'],list)
                        for p in m['content']) for b in posted]
    assert image_counts == [2,2,2,0,0,0]
    replay = run_pipeline(cfg)
    assert replay['complete'] and len(posted) == 6
    # 配对查看格在全新命名空间独立执行，读取固定结果；不触发新的模型调用。
    book = json.loads((Path(__file__).parents[1] / 't2i_v2_benchmark_debug.ipynb').read_text())
    preview = ''.join(book['cells'][1]['source']).replace(
        "PROJECT = Path('/yzp/zhaozy/yangzepeng/0905/demiwtg')",
        f'PROJECT = Path({str(resolve_root() / "demiwtg")!r})')
    preview = re.sub(r'^RUN_ID = .*$', "RUN_ID = 'paired'", preview, flags=re.MULTILINE)
    fixture_config = resolve_root() / 'demiwtg/benchmark/t2i/v2/runs/paired/config.json'
    fixture_config.parent.mkdir(parents=True, exist_ok=True)
    fixture_config.write_text(json.dumps(cfg))
    displays = []
    monkeypatch.setattr('IPython.display.display', displays.append)
    exec(compile(preview, '<paired-preview>', 'exec'), {})
    rendered = '\n'.join(str(getattr(item, 'data', item)) for item in displays)
    assert '提供正例图' in rendered and '不提供正例图' in rendered
    assert rendered.count('生成该概念的测试图') == 2
    assert 'invalid_arguments' in rendered
    assert len(posted) == 6
