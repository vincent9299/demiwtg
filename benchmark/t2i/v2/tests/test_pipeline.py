"""Exercise real standard operators and Lance writes with offline responses."""
import json
from pathlib import Path

import lance
import pyarrow as pa
import pytest
from demiflow.lance.records import LanceRecordStore
from demiflow.lance.blobs import BlobRef
from demiflow.operator_llm.lance_journal import submit_response
from demiflow import data
from demiflow.errors import LanceWriteError
from preparation.tests.publication_fixtures import inputs, article_publication
from preparation.operaters.article import ARTICLES, article_entity
from project import resolve_root
from benchmark.t2i.v2.t2i_v2_benchmark_pipeline import config, run_pipeline, DATASETS


def rows(ref):
    return data.read_lance(ref['uri'], version=ref['version']).take_all()


def candidate():
    return {'instruction': '绘制两个条件判断依次连接的流程图，显示每个判断的两条出边。',
            'test_points': [{'point': '判断节点的形状', 'basis': '材料 1：菱形表示判断。'},
                            {'point': '多个判断的连接', 'basis': '题面明确要求。'}]}



def article_rows(inputs):
    """复用历史内容 fixture，但被测入口实际读取 preparation 的现役实体表。"""
    original = article_publication(inputs)[0]
    return [json.loads(row['payload']) for row in lance.dataset(
        str(resolve_root() / original['relative_uri']), version=original['lance_version']).to_table().to_pylist()]


def write_articles(records):
    """隔离湖中的真实 preparation 表，显式固定写入后的版本。"""
    uri = str(resolve_root() / 'demiwtg/preparation/datasets/test_articles.lance')
    entities = [article_entity(row) for row in records]
    for row in entities:
        for image in row['illustrations']:
            evidence = json.loads(image['evidence_json'])
            evidence['bytes']['blob_ref'] = fixture_blob(image['sha256'])
            image['evidence_json'] = json.dumps(evidence)
    data.from_items(entities).write_lance(uri, mode='overwrite', schema=ARTICLES)
    return {'uri': uri, 'version': lance.dataset(uri).version}


def write_visuals(records):
    """直接构造现役图片表的概念审核字段，不依赖历史发布读取器。"""
    from preparation.operaters.images import IMAGES
    uri = str(resolve_root() / 'demiwtg/preparation/datasets/test_images.lance')
    lance.write_dataset(pa.Table.from_pylist(records, schema=IMAGES), uri, mode='overwrite')
    return {'uri': uri, 'version': lance.dataset(uri).version}


def fixture_blob(sha256):
    """测试的 preparation 生产者交付完整引用，消费端无需默认原图路径。"""
    uri = 'demiwtg/collect/datasets/images.lance'
    return BlobRef(uri, lance.dataset(str(resolve_root() / uri)).version, sha256).to_dict()


def visual_row(asset, *, concept='流程图', image_id='figure1', published=True, review_status='keep', source=None):
    """一张图的一条概念审核，可控制发布状态、来源及它所支持的内容。"""
    return {'sha256': asset['sha256'], 'source_refs': [json.dumps({
                'relative_uri': fixture_blob(asset['sha256'])['relative_uri'],
                'lance_version': fixture_blob(asset['sha256'])['version']})], 'published_concepts': [concept] if published else [],
            'concept_assessments': [{'assessment_id': concept + image_id, 'concept': concept,
                'image_id': image_id, 'published': published, 'review_status': review_status,
                'visual_support': {'supports': '独立图片审核的支持范围', 'region': 'whole', 'limitations': 'fixture'},
                'review_json': json.dumps({'decision': review_status}),
                'observation_json': json.dumps({'source': source or {'url': 'test://visual'}})}]}


def start(inputs, name='offline', target_uri=None, write_mode='overwrite', **kwargs):
    run = resolve_root() / DATASETS / name
    sources = write_articles(article_rows(inputs))
    cfg = config(run=run, article_source=sources, visual_source=write_visuals([visual_row(inputs[4][0])]),
                 target_uri=target_uri, write_mode=write_mode,
                 concepts=['流程图'], **kwargs)
    state = run_pipeline(cfg)
    return run, sources, cfg, state


def respond(state, result):
    call = json.loads(rows(state['designs'])[0]['call_json'])
    submit_response(resolve_root(), call['request_ref'], json.dumps({'result': result}, ensure_ascii=False),
                    model=call['model'], metadata={'reviewer': 'fixture', 'reviewer_kind': 'human'})


@pytest.mark.parametrize('finish_reason, status', [('stop', 'candidate'), ('length', 'failed')])
@pytest.mark.parametrize('reasoning_field', ['reasoning_content', 'reasoning'])
def test_reasoning_is_journaled_and_debug_preview_reads_without_model(monkeypatch, finish_reason, status, reasoning_field):
    import html
    import httpx
    import pandas as pd
    from IPython.display import HTML
    from demiflow.lance.records import RecordRef

    reasoning = 'fixture reasoning <debug>：先检查概念。\n再构造单题。'
    cfg = config(run=resolve_root() / DATASETS / 'reasoning', concepts=['流程图'], mode='modelhub')
    posted = []
    body = {'model': cfg['model'], 'choices': [{'finish_reason': finish_reason, 'message': {
        'role': 'assistant', reasoning_field: reasoning,
        'content': json.dumps({'result': {'question': candidate()}}, ensure_ascii=False),
    }}], 'usage': {'prompt_tokens': 12, 'completion_tokens': 20,
                  'completion_tokens_details': {'reasoning_tokens': 9}}}

    async def get(client, url, **kwargs):
        return httpx.Response(200, request=httpx.Request('GET', url), json={'data': [{'id': cfg['model']}]})

    async def post(client, url, **kwargs):
        posted.append(kwargs['json'])
        return httpx.Response(200, request=httpx.Request('POST', url), json=body)

    monkeypatch.setattr(httpx.AsyncClient, 'get', get)
    monkeypatch.setattr(httpx.AsyncClient, 'post', post)
    state = run_pipeline(cfg)
    design = rows(state['designs'])[0]
    assert design['status'] == status
    assert design['reasoning'] == reasoning
    if status == 'failed':
        assert rows(state['candidates']) == []
    else:
        assert rows(state['candidates'])[0]['reasoning'] == reasoning
    call = json.loads(design['call_json'])
    assert 'reasoning' not in call
    assert all('reasoning' not in attempt for attempt in call.get('attempts', []))
    saved = RecordRef.from_dict(call['response_ref']).read(resolve_root())
    assert saved['body'] == body  # Includes reasoning even when output was truncated.
    assert saved['elapsed_s'] >= 0

    notebook = json.loads((Path(__file__).parents[1] / 't2i_v2_benchmark_debug.ipynb').read_text())
    source = ''.join(notebook['cells'][0]['source'])
    preview = source[source.index('# 4. 只读本次 writer'):]
    assert 'run_pipeline' not in preview
    displays = []
    exec(compile(preview, '<debug-preview>', 'exec'), {
        'state': state, 'DATA_ROOT': resolve_root(), 'data': data, 'RecordRef': RecordRef,
        'json': json, 'html': html, 'pd': pd, 'HTML': HTML, 'display': displays.append,
    })
    rendered = '\n'.join(item.data for item in displays)
    assert html.escape(reasoning) in rendered and finish_reason in rendered
    assert 'reasoning_tokens' in rendered
    assert len(posted) == 1
    replay = run_pipeline(cfg)
    assert rows(replay['designs'])[0]['status'] == status
    assert rows(replay['designs'])[0]['reasoning'] == reasoning
    assert len(posted) == 1


def test_offline_roundtrip_reuses_calls_without_freezing_run(inputs):
    run, sources, cfg, pending = start(inputs)
    assert pending['counts'] == {'pending': 1} and not pending['complete']
    assert rows(pending['candidates']) == []
    original = rows(pending['inputs'])[0]
    refs = json.loads(original['references_json'])
    assert [r['number'] for r in refs] == [1, 2]
    blob = refs[1]['blob_ref']
    assert BlobRef(**blob).read(resolve_root()) == Path(inputs[4][0]['path']).read_bytes()
    call = json.loads(rows(pending['designs'])[0]['call_json'])
    request = LanceRecordStore(**pending['calls']).get(call['request_ref']['key'])
    assert '流程图以菱形表示判断' in json.dumps(request, ensure_ascii=False)
    assert 'image_number' in json.dumps(request)
    assert '围绕概念的核心内容选择考点' in json.dumps(request, ensure_ascii=False)
    answer = candidate()
    answer['test_points'][1]['basis'] = '作者知识：判断分支可按布尔条件的真假分别连接。'
    respond(pending, {'question': answer})
    completed = run_pipeline(cfg)
    assert completed['complete'] and completed['candidate_count'] == 1
    question = rows(completed['candidates'])[0]
    assert question['status'] == 'unreviewed' and question['task_id'].startswith('t2i_')
    assert question['reasoning'] is None
    assert question['references_json'] == original['references_json']
    assert question['test_points'] == answer['test_points']
    assert rows(completed['designs'])[0]['question'] == answer
    assert 'candidates' not in rows(completed['designs'])[0]
    assert 'criteria' not in question
    rerun = run_pipeline({**cfg, 'max_calls': 3})
    assert rerun['complete'] and rows(rerun['candidates']) == rows(completed['candidates'])
    assert len(LanceRecordStore(**rerun['calls']).keys(prefix='request/')) == 1
    records = LanceRecordStore(resolve_root(), str(DATASETS / 'records__offline.lance'))
    assert records.get('manifest') is None and records.get('inputs') is None



@pytest.mark.parametrize('result, expected', [
    ({'question': None, 'reason': '未找到依据充分且可检查的题目。'}, 'insufficient'),
    ({'question': None}, 'invalid_response'),
    ({'question': None, 'reason': '  '}, 'invalid_response'),
    ({'question': dict(candidate(), knowledge_gap='obsolete')}, 'invalid_response'),
    ({'question': dict(candidate(), criteria=[{'requirement': '旧判据', 'basis': '旧依据'}])}, 'invalid_response'),
    ({'question': dict(candidate(), test_points=['旧字符串考点'])}, 'invalid_response'),
    ({'question': dict(candidate(), instruction='  ')}, 'invalid_response'),
    ({'question': dict(candidate(), test_points=[])}, 'invalid_response'),
    ({'question': [candidate(), candidate()]}, 'invalid_response'),
    ({'question': {}}, 'invalid_response'),
    ({'question': '题目'}, 'invalid_response'),
    ({'question': candidate(), 'reason': '又声称不能出题'}, 'invalid_response'),
    ({'candidates': [candidate()]}, 'failed'),
    ({'candidates': [candidate(), candidate()]}, 'failed'),
    ({'question': dict(candidate(), test_points=[{'point': '主体可辨认', 'basis': '  '}])}, 'invalid_response'),
])
def test_empty_and_malformed_outputs_are_not_questions(inputs, result, expected):
    run, sources, cfg, pending = start(inputs)
    respond(pending, result)
    state = run_pipeline(cfg)
    assert state['counts'] == {expected: 1}
    assert state['complete'] == (expected == 'insufficient')
    assert state['candidate_count'] == 0 and rows(state['candidates']) == []
    assert rows(state['designs'])[0]['reason']


def test_context_budget_skips_but_missing_materials_still_call_author(inputs):
    run, sources, cfg, state = start(inputs, max_context_chars=100)
    assert state['counts'] == {'needs_context_budget': 1}
    assert not LanceRecordStore(**state['calls']).keys()
    missing = run_pipeline(config(run=run.with_name('missing'), concepts=['不存在']))
    assert missing['counts'] == {'pending': 1}
    assert rows(missing['inputs'])[0]['references_json'] == '[]'
    calls = LanceRecordStore(**missing['calls'])
    request = calls.get(next(iter(calls.keys(prefix='request/'))))
    assert '本概念没有提供参考材料' in json.dumps(request, ensure_ascii=False)


def test_writer_failure_does_not_commit_completion(inputs, monkeypatch):
    run, sources, cfg, pending = start(inputs)
    respond(pending, {'question': candidate()})
    writer = lance.write_dataset
    def fail_candidates(data, uri, **kwargs):
        if 'candidates__' in str(uri):
            raise OSError('injected writer failure')
        return writer(data, uri, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(lance, 'write_dataset', fail_candidates)
        with pytest.raises(LanceWriteError, match='injected'):
            run_pipeline(cfg)
    saved = LanceRecordStore(resolve_root(), str(DATASETS / 'records__offline.lance')).get('state')
    assert not saved['complete']
    complete = run_pipeline(cfg)
    assert complete['complete'] and complete['candidate_count'] == 1
    assert len(LanceRecordStore(**complete['calls']).keys(prefix='request/')) == 1


def test_notebook_calls_formal_entry():
    import ast
    book = json.loads((Path(__file__).parents[1] / 't2i_v2_benchmark_debug.ipynb').read_text())
    assert len(book['cells']) == 1
    cell = book['cells'][0]
    source = ''.join(cell['source'])
    compile(source, 't2i_v2_benchmark_debug.ipynb', 'exec', flags=ast.PyCF_ALLOW_TOP_LEVEL_AWAIT)
    assert 'asyncio.to_thread(run_pipeline' in source
    assert "read_lance(TABLE_URI, version=VERSION)" in source


def test_notebook_refreshes_cached_readers_config_and_runs_offline(monkeypatch):
    from benchmark.t2i.v2 import t2i_v2_benchmark_pipeline as pipeline
    from benchmark.t2i.v2.operaters import authoring, images
    from demiflow.operator_llm import client
    from demiflow.data import api as data_api

    def old_config(*, concepts):
        return {'concepts': concepts}

    def stale(*args, **kwargs):
        pytest.fail('A cached implementation survived notebook reload')

    monkeypatch.setattr(pipeline, 'config', old_config)
    monkeypatch.setattr(pipeline, 'run_pipeline', stale)
    monkeypatch.setattr(authoring, 'check_response', stale)
    monkeypatch.setattr(images, 'image_blob_ref', stale)
    monkeypatch.setattr(client.AsyncOperatorLLMClient, 'decode', stale)
    # 模拟统一 data 入口发布前已启动的内核，连 reader 依赖也仍是旧版本。
    monkeypatch.delattr(data, 'read_lance')
    monkeypatch.delattr(data, 'from_items')
    monkeypatch.delattr(data, 'from_arrow')
    monkeypatch.delattr(data_api, '_current_executor')
    book = json.loads((Path(__file__).parents[1] / 't2i_v2_benchmark_debug.ipynb').read_text())
    source = ''.join(book['cells'][0]['source'])
    # 执行真实 notebook 的刷新和配置段，截止模型调用前；不接触正式数据。
    setup = source[source.index('from importlib import reload'):source.index('# 3. 运行正式 pipeline')]
    namespace = {'DATA_ROOT': resolve_root()}
    exec(compile(setup, '<notebook-setup>', 'exec'), namespace)
    assert namespace['CONFIG']['run'] == str(namespace['RUN_DIR'])
    assert namespace['config'] is pipeline.config and pipeline.config is not old_config
    assert namespace['run_pipeline'] is pipeline.run_pipeline and pipeline.run_pipeline is not stale
    assert pipeline.check_response is authoring.check_response and authoring.check_response is not stale
    assert pipeline.image_blob_ref is images.image_blob_ref and images.image_blob_ref is not stale
    assert client.AsyncOperatorLLMClient.decode is not stale
    assert 'reasoning' in pipeline.DESIGNS.names and 'reasoning' in pipeline.QUESTIONS.names
    assert callable(namespace['data'].read_lance)
    assert callable(namespace['data'].from_items)
    assert callable(namespace['data'].from_arrow)

    uri = str(resolve_root() / 'articles.lance')
    lance.write_dataset(pa.Table.from_pylist([{
        'article_id': 'fixture', 'concept': '示例', 'review_status': 'reviewed',
        'content': [{'title': '正文', 'content': {'paragraphs': ['独立正文。']}}],
    }], schema=ARTICLES), uri)
    cfg = namespace['config'](run=resolve_root() / DATASETS / 'notebook_reload',
        concepts=['示例', '无参考'], article_source={'uri': uri, 'version': 1}, mode='offline')
    state = namespace['run_pipeline'](cfg)
    assert state['counts'] == {'pending': 2}
    inputs = {row['concept']: json.loads(row['references_json']) for row in rows(state['inputs'])}
    assert inputs['示例'][0]['text'] == '独立正文。'
    assert inputs['无参考'] == []
    assert len(rows(state['designs'])) == 2 and rows(state['candidates']) == []


def test_changed_output_is_used_without_new_run(inputs):
    """指定目标真实落表，同名运行显式改目标后写入新目标。"""
    target = str(resolve_root() / DATASETS / 'chosen.lance')
    run, sources, cfg, pending = start(inputs, target_uri=target)
    assert pending['candidates']['uri'] == target
    respond(pending, {'question': candidate()})
    complete = run_pipeline({**cfg, 'target_uri': target})
    assert len(rows(complete['candidates'])) == 1
    assert not (run.parent / ('candidates__' + run.name + '.lance')).exists()
    changed = run_pipeline({**cfg, 'target_uri': target + '.other'})
    assert changed['candidates']['uri'] == target + '.other'
    assert rows(changed['candidates']) == rows(complete['candidates'])


def test_append_and_overwrite_target_with_completed_resume(inputs):
    """追加重复执行不重复写入；覆盖重新执行且可在同名运行切换模式。"""
    target = str(resolve_root() / DATASETS / 'shared_candidates.lance')
    for name, mode, expected in [('first', 'append', 1), ('next', 'append', 2), ('replace', 'overwrite', 1)]:
        run, sources, cfg, pending = start(inputs, name=name, target_uri=target, write_mode=mode)
        proposal = {**candidate(), 'instruction': candidate()['instruction'] + name}
        respond(pending, {'question': proposal})
        state = run_pipeline({**cfg, 'target_uri': target, 'write_mode': mode})
        assert state['candidate_count'] == 1
        assert len(rows(state['candidates'])) == expected
        repeated = run_pipeline({**cfg, 'target_uri': target, 'write_mode': mode})
        assert len(rows(repeated['candidates'])) == expected
        if mode == 'append':
            assert repeated['candidates'] == state['candidates']
        else:
            assert repeated['candidates']['version'] > state['candidates']['version']
    switched = run_pipeline({**cfg, 'target_uri': target, 'write_mode': 'append'})
    assert len(rows(switched['candidates'])) == 2



def test_each_concept_has_its_own_request_and_missing_concepts_remain(inputs):
    """一个请求只含一个概念的材料；每条响应只出一题，缺失概念不消失也不请求。"""
    from copy import deepcopy
    first = article_rows(inputs)[0]
    first['knowledge'][0]['content']['paragraphs'][0] += ' FIRST_CONCEPT_ONLY'
    second = deepcopy(first)
    second['concept'] = '第二个测试概念'
    second['knowledge'][0]['content']['paragraphs'][0] = 'SECOND_CONCEPT_ONLY'
    sources = write_articles([first, second])
    run = resolve_root() / DATASETS / 'per_concept'
    cfg = config(run=run, article_source=sources,
                 concepts=['第二个测试概念', '缺失的测试概念', '流程图'])
    pending = run_pipeline(cfg)
    assert {row['concept'] for row in rows(pending['inputs'])} == set(cfg['concepts'])
    assert pending['counts'] == {'pending': 3}
    calls = LanceRecordStore(**pending['calls'])
    assert len(calls.keys(prefix='request/')) == 3

    for row in rows(pending['designs']):
        if row['status'] != 'pending':
            continue
        call = json.loads(row['call_json'])
        request = json.dumps(calls.get(call['request_ref']['key']), ensure_ascii=False)
        included, excluded = ('SECOND_CONCEPT_ONLY', 'FIRST_CONCEPT_ONLY') if row['concept'] == second['concept'] else ('FIRST_CONCEPT_ONLY', 'SECOND_CONCEPT_ONLY')
        if row['concept'] == '缺失的测试概念':
            assert included not in request and excluded not in request
        else:
            assert included in request and excluded not in request
        question = {**candidate(), 'instruction': row['concept'] + '的完整单题'}
        assert 'max_candidates' not in request
        submit_response(resolve_root(), call['request_ref'], json.dumps({'result': {'question': question}}),
                        model=call['model'], metadata={'reviewer': 'fixture', 'reviewer_kind': 'human'})
    completed = run_pipeline(cfg)
    assert completed['candidate_count'] == 3
    assert completed['counts'] == {'candidate': 3}
    assert len(calls.keys(prefix='request/')) == 3

def test_text_and_images_are_independent_and_image_count_is_bounded(inputs):
    articles = write_articles(article_rows(inputs))
    visuals = write_visuals([visual_row(inputs[4][1], image_id='unrelated'),
                             visual_row(inputs[4][0], image_id='figure1')])
    state = run_pipeline(config(run=resolve_root() / DATASETS / 'independent', article_source=articles,
        visual_source=visuals, concepts=['流程图'], max_reference_images=1))
    refs = json.loads(rows(state['inputs'])[0]['references_json'])
    assert len(refs) == 2 and [r['kind'] for r in refs] == ['text', 'image']
    assert refs[1]['blob_ref']['sha256'] == inputs[4][1]['sha256']
    assert 'sources' not in refs[0] and 'support' not in refs[1]


def test_visual_consumer_filters_only_public_status_and_never_parses_audit(inputs):
    keep = visual_row(inputs[4][0])
    keep['concept_assessments'][0].update(review_json='not JSON', observation_json='not JSON')
    visuals = write_visuals([keep, visual_row(inputs[4][1], published=False),
        visual_row(inputs[4][1], review_status='reject'), visual_row(inputs[4][2], concept='其他概念')])
    state = run_pipeline(config(run=resolve_root() / DATASETS / 'visual_only', concepts=['流程图'], visual_source=visuals))
    refs = json.loads(rows(state['inputs'])[0]['references_json'])
    assert len(refs) == 1 and refs[0]['blob_ref']['sha256'] == inputs[4][0]['sha256']


def test_article_and_visual_sources_read_their_configured_versions(inputs):
    """两张来源表均有更新版本时，仍读取各自配置的旧版本，不混入最新内容。"""
    article = article_rows(inputs)[0]
    article_source = write_articles([article])
    visual_source = write_visuals([visual_row(inputs[4][0])])
    article['knowledge'][0]['content']['paragraphs'][0] = 'NEW_ARTICLE_MUST_NOT_APPEAR'
    write_articles([article])
    write_visuals([visual_row(inputs[4][1], image_id='new_image')])

    state = run_pipeline(config(run=resolve_root() / DATASETS / 'fixed_sources', article_source=article_source, concepts=['流程图'], visual_source=visual_source))
    refs = json.loads(rows(state['inputs'])[0]['references_json'])
    assert len(refs) == 2
    assert '流程图以菱形表示判断' in refs[0]['text']
    assert 'NEW_ARTICLE_MUST_NOT_APPEAR' not in refs[0]['text']
    assert refs[1]['blob_ref']['sha256'] == inputs[4][0]['sha256']
    assert set(refs[1]) == {'number', 'kind', 'blob_ref'}


def test_article_consumer_needs_only_public_content_and_status(inputs):
    uri = str(resolve_root() / 'minimal_articles.lance')
    data.from_items([{'concept': '流程图', 'review_status': 'reviewed',
        'content': [{'title': '正文', 'content': {'paragraphs': ['可独立使用的正文']}}]}]).write_lance(uri)
    state = run_pipeline(config(run=resolve_root() / DATASETS / 'minimal', concepts=['流程图'],
        article_source={'uri': uri, 'version': lance.dataset(uri).version}))
    refs = json.loads(rows(state['inputs'])[0]['references_json'])
    assert refs == [{'number': 1, 'kind': 'text', 'title': '正文', 'text': '可独立使用的正文'}]


def test_multiple_articles_are_kept_without_deduplication_or_conflict_checks(inputs):
    from copy import deepcopy
    first = article_rows(inputs)[0]
    second = deepcopy(first)
    second['knowledge'][0]['content']['paragraphs'][0] = '另一篇文章正文'
    source = write_articles([first, first, second])
    state = run_pipeline(config(run=resolve_root() / DATASETS / 'many_articles', article_source=source, concepts=['流程图']))
    refs = json.loads(rows(state['inputs'])[0]['references_json'])
    assert len(refs) == 3 and refs[0]['text'] == refs[1]['text']
    assert refs[2]['text'] == '另一篇文章正文'


def test_article_illustrations_never_enter_image_budget_or_requests(inputs):
    source = write_articles(article_rows(inputs))
    state = run_pipeline(config(run=resolve_root() / DATASETS / 'text_only', article_source=source,
        concepts=['流程图'], max_reference_images=1))
    assert state['counts'] == {'pending': 1}
    assert all(r['kind'] == 'text' for r in json.loads(rows(state['inputs'])[0]['references_json']))


def test_image_decode_failure_raises_before_request(inputs):
    import hashlib
    from collect.material_writer import write_images
    raw = b'not an image'
    sha = hashlib.sha256(raw).hexdigest()
    write_images(resolve_root(), [{'sha256': sha, 'ext': 'png', 'byte_size': len(raw), 'storage_mode': 'lance_blob',
        'data': raw, 'concepts': [], 'sources': [], 'availability': 'available', 'resolution': None}])
    source = write_visuals([visual_row({'sha256': sha})])
    with pytest.raises((LanceWriteError, OSError), match='cannot identify image'):
        run_pipeline(config(run=resolve_root() / DATASETS / 'decode_error', concepts=['流程图'], visual_source=source))
    calls = LanceRecordStore(resolve_root(), str(DATASETS / 'calls__decode_error.lance'))
    assert not calls.keys()


def test_question_count_is_not_configurable():
    """单题不是默认上限为 1；入口已经移除多题配置。"""
    assert 'tasks_per_concept' not in config(run='test', concepts=['流程图'])
    with pytest.raises(TypeError, match='tasks_per_concept'):
        config(run='test', concepts=['流程图'], tasks_per_concept=1)


def test_current_source_and_config_replace_previous_run_inputs(inputs):
    """同名调用改来源版本或概念后重新读材料，不能复用旧完成状态/输入表。"""
    run, source, cfg, pending = start(inputs)
    respond(pending, {'question': candidate()})
    completed = run_pipeline(cfg)
    article = article_rows(inputs)[0]
    article['knowledge'][0]['content']['paragraphs'][0] = 'UPDATED_MATERIAL'
    changed_source = write_articles([article])
    changed = run_pipeline({**cfg, 'article_source': changed_source})
    assert changed['counts'] == {'pending': 1}
    assert 'UPDATED_MATERIAL' in rows(changed['inputs'])[0]['references_json']
    assert len(LanceRecordStore(**changed['calls']).keys(prefix='request/')) == 2
    missing = run_pipeline(config(run=run, article_source=changed_source, concepts=['另一个概念']))
    assert missing['counts'] == {'pending': 1}
    assert rows(missing['inputs'])[0]['concept'] == '另一个概念'


def test_preparation_reference_resolves_nondefault_blob_table_and_version(inputs):
    from demiflow.lance.blobs import LanceBlobStore
    store = LanceBlobStore(resolve_root(), 'custom/assets.lance')
    blob = store.put(Path(inputs[4][0]['path']).read_bytes())
    store.put(Path(inputs[4][1]['path']).read_bytes())
    visual = visual_row(inputs[4][0])
    visual['source_refs'] = [json.dumps({'relative_uri': blob.relative_uri, 'lance_version': blob.version})]
    source = write_visuals([visual])
    import shutil
    shutil.rmtree(resolve_root() / 'demiwtg/collect/datasets/images.lance')
    state = run_pipeline(config(run=resolve_root() / DATASETS / 'custom_source', concepts=['流程图'], visual_source=source))
    ref = json.loads(rows(state['inputs'])[0]['references_json'])[0]['blob_ref']
    assert ref == blob.to_dict()
    assert BlobRef(**ref).read(resolve_root()) == Path(inputs[4][0]['path']).read_bytes()


def test_missing_preparation_reference_does_not_fall_back_to_raw(inputs):
    """缺少交付引用必须暴露问题，不偷偷读取默认 collect 表。"""
    visual = visual_row(inputs[4][0])
    visual['source_refs'] = []
    with pytest.raises((ValueError, LanceWriteError), match='lacks blob_ref/source_refs'):
        run_pipeline(config(run=resolve_root() / DATASETS / 'missing_ref', article_source=None, concepts=['流程图'], visual_source=write_visuals([visual])))


@pytest.mark.parametrize('concurrency', [1, 2])
def test_config_controls_actual_overlapping_concept_requests(inputs, monkeypatch, concurrency):
    """通过异步请求屏障验证真实重叠度；小队列不把多 worker 降成串行。"""
    import asyncio
    from copy import deepcopy
    from demiflow.operator_llm.runtime import AsyncOperatorLLMRuntime

    articles = []
    for name in ('概念甲', '概念乙', '概念丙'):
        article = deepcopy(article_rows(inputs)[0])
        article['concept'] = name
        articles.append(article)
    source = write_articles(articles)
    active = peak = 0
    entered = []
    barrier = None
    original = AsyncOperatorLLMRuntime.call_with_trace

    async def synchronized_call(self, prompt, values):
        nonlocal active, peak, barrier
        if barrier is None:
            barrier = asyncio.Event()
        active += 1
        peak = max(peak, active)
        entered.append(values['payload']['concept'])
        if active == concurrency:
            barrier.set()
        try:
            await asyncio.wait_for(barrier.wait(), timeout=2)
            return await original(self, prompt, values)
        finally:
            active -= 1

    monkeypatch.setattr(AsyncOperatorLLMRuntime, 'call_with_trace', synchronized_call)
    state = run_pipeline(config(
        run=resolve_root() / DATASETS / 'concurrent', article_source=source,
        concepts=[row['concept'] for row in articles], concurrency=concurrency, queue_depth=1,
    ))
    assert peak == concurrency
    assert sorted(entered) == ['概念丙', '概念乙', '概念甲']
    assert state['counts'] == {'pending': 3}
    assert len(LanceRecordStore(**state['calls']).keys(prefix='request/')) == 3


def test_selected_pixels_are_read_once_and_budget_failure_reads_none(inputs, monkeypatch):
    reads = []
    original = BlobRef.read
    def counted(self, root):
        reads.append(self.sha256)
        return original(self, root)
    monkeypatch.setattr(BlobRef, 'read', counted)
    source = write_visuals([visual_row(inputs[4][0])])
    cfg = config(run=resolve_root() / DATASETS / 'once', visual_source=source, concepts=['流程图'])
    assert run_pipeline(cfg)['counts'] == {'pending': 1}
    assert reads == [inputs[4][0]['sha256']]
    reads.clear()
    state = run_pipeline({**cfg, 'run': str(resolve_root() / DATASETS / 'budget'), 'max_context_chars': 10})
    assert state['counts'] == {'needs_context_budget': 1} and reads == []
