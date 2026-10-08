from benchmark.t2i.v2.operators.run_tables import RunTables
"""Exercise real standard operators and Lance writes with offline responses."""
import json
from pathlib import Path

import lance
import pyarrow as pa
import pytest
from demiflow.operator_llm.call_ref import read_call, journal_options, PromptRecordRef
from demiflow.operator_llm.sqlite_journal import SQLitePromptJournal
from demiflow.objects import ObjectRef
from benchmark.t2i.v2.tests.agent_fixture import submit_design_response as submit_response, single_turn_arguments
from demiflow import data
from demiflow.errors import LanceWriteError
from preparation.articles.tests.publication_fixtures import inputs, article_publication
from preparation.articles.operators.article import ARTICLES, article_entity
from project import resolve_root
from benchmark.t2i.v2.t2i_v2_benchmark_pipeline import run_pipeline, DATASETS
from benchmark.t2i.v2.tests.agent_fixture import business_config as config


def rows(ref):
    return data.read_lance(ref['uri'], version=ref['version']).take_all()


def candidate():
    return {'instruction': '绘制两个条件判断依次连接的流程图，显示每个判断的两条出边。',
            'test_points': [{'point': '判断节点的形状', 'basis': '材料 1：菱形表示判断。',
                             'criterion': '两个条件判断均由菱形节点承担；装饰性菱形不能代替判断节点。'},
                            {'point': '多个判断的连接', 'basis': '题面明确要求。',
                             'criterion': '判断节点沿流程依次连接，各有两条可区分的出边；孤立节点或缺失分支不满足要求，布局可变化。'}]}







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
            evidence['bytes']['object_ref'] = fixture_blob(image['sha256'])
            image['evidence_json'] = json.dumps(evidence)
    data.from_items(entities).write_lance(uri, mode='overwrite', schema=ARTICLES)
    return {'uri': uri, 'version': lance.dataset(uri).version}


def write_visuals(records):
    """直接构造现役图片表的概念审核字段，不依赖历史发布读取器。"""
    from preparation.images.catalog.operators.schema import IMAGES
    uri = str(resolve_root() / 'demiwtg/preparation/datasets/test_images.lance')
    lance.write_dataset(pa.Table.from_pylist(records, schema=IMAGES), uri, mode='overwrite')
    return {'uri': uri, 'version': lance.dataset(uri).version}


def fixture_blob(sha256):
    """测试的 preparation 生产者交付完整引用，消费端无需默认原图路径。"""
    uri = 'demiwtg/collect/datasets/images.lance'
    from collect.assets import AssetReader
    return AssetReader(resolve_root() / uri, version=lance.dataset(str(resolve_root() / uri)).version).publish(sha256).to_dict()


def visual_row(asset, *, concept='流程图', image_id='figure1', published=True, review_status='keep', source=None):
    """一张图的一条概念审核，可控制发布状态、来源及它所支持的内容。"""
    return {'sha256': asset['sha256'], 'image_uri': fixture_blob(asset['sha256'])['uri'], 'published_concepts': [concept] if published else [],
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
    from demiflow.operator_llm.call_ref import read_call

    reasoning = 'fixture reasoning <debug>：先检查概念。\n再构造单题。'
    cfg = config(run=resolve_root() / DATASETS / 'reasoning', concepts=['流程图'], mode='modelhub')
    posted = []
    body = {'model': cfg['model'], 'choices': [{'finish_reason': finish_reason, 'message': {
        'role': 'assistant', reasoning_field: reasoning,
        'content': json.dumps({'api_calls': [], 'response': {'result': {'question': candidate()}}}, ensure_ascii=False),
    }}], 'usage': {'prompt_tokens': 12, 'completion_tokens': 20,
                  'completion_tokens_details': {'reasoning_tokens': 9}}}

    def handler(request):
        if request.method == 'GET':
            return httpx.Response(200, json={'data': [{'id': cfg['model']}]})
        posted.append(json.loads(request.content))
        return httpx.Response(200, json=body)

    original_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kwargs:
                        original_client(transport=httpx.MockTransport(handler), **kwargs))
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
    saved = read_call(call['response_ref'], resolve_root())
    assert saved['body'] == body  # Includes reasoning even when output was truncated.
    assert saved['elapsed_s'] >= 0

    notebook = json.loads((Path(__file__).parents[1] / 'archive/notebook_before_ready200_20261003.ipynb').read_text())
    preview = ''.join(notebook['cells'][1]['source']).replace(
        "VIEW_PROJECT = Path('/yzp/zhaozy/yangzepeng/0905/demiwtg')", f'VIEW_PROJECT = Path({str(resolve_root() / "demiwtg")!r})'
    ).replace("VIEW_RUN_ID = 'dual_keep_codex5_criteria_v3_20260928'", "VIEW_RUN_ID = 'reasoning'")
    assert 'run_pipeline' not in preview
    displays = []
    monkeypatch.setattr('IPython.display.display', displays.append)
    exec(compile(preview, '<debug-preview>', 'exec'), {
        'state': state, 'DATA_ROOT': resolve_root(), 'data': data, 'read_call': read_call,
        'json': json, 'html': html, 'pd': pd, 'HTML': HTML, 'display': displays.append,
    })
    rendered = '\n'.join(item.data for item in displays if isinstance(item, HTML))
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
    refs = json.loads(original['evidence_json'])
    images = json.loads(original['authoring_images_json'])
    assert [r['number'] for r in refs] == [1] and [r['number'] for r in images] == [1]
    assert 'references_json' not in original
    blob = images[0]['object_ref']
    assert ObjectRef(**blob).read() == Path(inputs[4][0]['path']).read_bytes()
    call = json.loads(rows(pending['designs'])[0]['call_json'])
    request = SQLitePromptJournal(**journal_options(**pending['calls'])).read(call['request_ref']['request_id'], 'request')
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
    assert question['evidence_json'] == original['evidence_json']
    assert question['authoring_images_json'] == original['authoring_images_json']
    assert 'references_json' not in question
    assert question['test_points'] == answer['test_points']
    assert rows(completed['designs'])[0]['question'] == answer
    assert 'candidates' not in rows(completed['designs'])[0]
    assert 'criteria' not in question
    rerun = run_pipeline({**cfg, 'max_calls': 3})
    assert rerun['complete'] and rows(rerun['candidates']) == rows(completed['candidates'])
    assert len(SQLitePromptJournal(**journal_options(**rerun['calls'])).request_ids()) == 1
    records = RunTables(resolve_root(), str(DATASETS / 'records__offline.lance'))
    assert records.load_manifest() is None
    assert records.load()['inputs'] == rerun['inputs']



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
    ({'question': dict(candidate(), test_points=[dict(candidate()['test_points'][0], basis='  ')])}, 'invalid_response'),
    ({'question': dict(candidate(), test_points=[{'point': '判断结构', 'basis': '菱形表示判断。'}])}, 'invalid_response'),
    *[({'question': dict(candidate(), test_points=[dict(candidate()['test_points'][0], criterion=value)])},
      'invalid_response') for value in ('', '  \n ', None, [], {'correct': '菱形'})],
])
def test_empty_and_malformed_outputs_are_not_questions(inputs, result, expected):
    run, sources, cfg, pending = start(inputs)
    respond(pending, result)
    state = run_pipeline(cfg)
    if expected == 'invalid_response':
        # Schema-invalid finals exhaust the single-turn agent budget; business
        # whitespace/semantic errors are rejected by the downstream row check.
        assert set(state['counts']) <= {'invalid_response', 'failed'}
        assert sum(state['counts'].values()) == 1
    else:
        assert state['counts'] == {expected: 1}
    assert state['complete'] == (expected == 'insufficient')
    assert state['candidate_count'] == 0 and rows(state['candidates']) == []
    assert rows(state['designs'])[0]['reason']


def test_context_budget_skips_but_missing_materials_still_call_author(inputs):
    run, sources, cfg, state = start(inputs, max_context_chars=100)
    assert state['counts'] == {'needs_context_budget': 1}
    assert not SQLitePromptJournal(**journal_options(**state['calls'])).request_ids()
    missing = run_pipeline(config(run=run.with_name('missing'), concepts=['不存在']))
    assert missing['counts'] == {'pending': 1}
    assert rows(missing['inputs'])[0]['evidence_json'] == '[]'
    calls = SQLitePromptJournal(**journal_options(**missing['calls']))
    request = calls.read(next(iter(calls.request_ids())), 'request')
    assert '本概念没有提供作答参考材料' in json.dumps(request, ensure_ascii=False)


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
    saved = RunTables(resolve_root(), str(DATASETS / 'records__offline.lance')).load()
    assert not saved['complete']
    complete = run_pipeline(cfg)
    assert complete['complete'] and complete['candidate_count'] == 1
    assert len(SQLitePromptJournal(**journal_options(**complete['calls'])).request_ids()) == 1


def test_notebook_calls_formal_entry():
    import ast
    book = json.loads((Path(__file__).parents[1] / 't2i_v2_benchmark_debug.ipynb').read_text())
    assert len(book['cells']) == 2
    cell = book['cells'][0]
    source = ''.join(cell['source'])
    compile(source, 't2i_v2_benchmark_debug.ipynb', 'exec', flags=ast.PyCF_ALLOW_TOP_LEVEL_AWAIT)
    assert 'asyncio.to_thread(pipeline.run_pipeline' in source
    preview = ''.join(book['cells'][1]['source'])
    assert "read_lance(**branch['designs']" in preview
    assert "read_lance(**state['inputs']" in preview
    assert 'run_pipeline' not in preview


def test_notebook_refreshes_cached_readers_config_and_runs_offline(monkeypatch):
    from benchmark.t2i.v2 import t2i_v2_benchmark_pipeline as pipeline
    from benchmark.t2i.v2.operators import authoring, images
    from demiflow.operator_llm import client
    from demiflow.data import api as data_api

    def old_config(*, concepts):
        return {'concepts': concepts}

    def stale(*args, **kwargs):
        pytest.fail('A cached implementation survived notebook reload')

    monkeypatch.setattr(pipeline, 'config', old_config)
    monkeypatch.setattr(pipeline, 'run_pipeline', stale)
    monkeypatch.setattr(authoring, 'check_response', stale)
    monkeypatch.setattr(images, 'image_object_ref', stale)
    monkeypatch.setattr(client.AsyncOperatorLLMClient, 'decode', stale)
    # 模拟统一 data 入口发布前已启动的内核，连 reader 依赖也仍是旧版本。
    monkeypatch.delattr(data, 'read_lance')
    monkeypatch.delattr(data, 'from_items')
    monkeypatch.delattr(data, 'from_arrow')
    monkeypatch.delattr(data_api, '_current_executor')
    book = json.loads((Path(__file__).parents[1] / 't2i_v2_benchmark_debug.ipynb').read_text())
    source = ''.join(book['cells'][0]['source'])
    # 实验配置在 run 目录；隔离测试使用临时配置，不依赖或执行真实来源。
    import re
    run_id = re.search(r"^RUN_ID = '([^']+)'", source, re.MULTILINE).group(1)
    fixture_config = resolve_root() / 'demiwtg/benchmark/t2i/v2/runs' / run_id / 'config.json'
    fixture_agent = resolve_root() / 'demiwtg/benchmark/t2i/v2/prompts/agent_codex.yaml'
    fixture_agent.parent.mkdir(parents=True, exist_ok=True)
    fixture_agent.write_text((Path(__file__).parents[1] / 'prompts/agent_codex.yaml').read_text())
    fixture_config.parent.mkdir(parents=True, exist_ok=True)
    fixture_config.write_text(json.dumps({
        'run': str(resolve_root() / DATASETS / 'notebook_reload'),
        'cohort_source': {'uri': 'cohort.lance', 'version': 1},
    }))
    # 执行真实 notebook 的刷新和配置段，截止模型调用前；不接触正式数据。
    setup = source[:source.index('if RUN_PIPELINE:')].replace(
        "PROJECT = Path('/yzp/zhaozy/yangzepeng/0905/demiwtg')", f'PROJECT = Path({str(resolve_root() / "demiwtg")!r})')
    namespace = {}
    exec(compile(setup, '<notebook-setup>', 'exec'), namespace)
    assert namespace['CONFIG']['run'] == str(namespace['RUN_DIR'])
    assert namespace['pipeline'].config is pipeline.config and pipeline.config is not old_config
    assert namespace['pipeline'].run_pipeline is pipeline.run_pipeline and pipeline.run_pipeline is not stale
    assert pipeline.check_response is authoring.check_response and authoring.check_response is not stale
    assert pipeline.image_object_ref is images.image_object_ref and images.image_object_ref is not stale
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
    cfg = namespace['pipeline'].config(run=resolve_root() / DATASETS / 'notebook_reload',
        concepts=['示例', '无参考'], article_source={'uri': uri, 'version': 1},
        **single_turn_arguments(resolve_root(), mode='offline'))
    state = namespace['pipeline'].run_pipeline(cfg)
    assert state['counts'] == {'pending': 2}
    inputs = {row['concept']: json.loads(row['evidence_json']) for row in rows(state['inputs'])}
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
    calls = SQLitePromptJournal(**journal_options(**pending['calls']))
    assert len(calls.request_ids()) == 3

    for row in rows(pending['designs']):
        if row['status'] != 'pending':
            continue
        call = json.loads(row['call_json'])
        request = json.dumps(calls.read(call['request_ref']['request_id'], 'request'), ensure_ascii=False)
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
    assert len(calls.request_ids()) == 3

def test_text_and_images_are_independent_and_image_count_is_bounded(inputs):
    articles = write_articles(article_rows(inputs))
    visuals = write_visuals([visual_row(inputs[4][1], image_id='unrelated'),
                             visual_row(inputs[4][0], image_id='figure1')])
    state = run_pipeline(config(run=resolve_root() / DATASETS / 'independent', article_source=articles,
        visual_source=visuals, concepts=['流程图'], max_reference_images=1))
    refs = json.loads(rows(state['inputs'])[0]['evidence_json'])
    images = json.loads(rows(state['inputs'])[0]['authoring_images_json'])
    assert len(refs) == len(images) == 1 and refs[0]['kind'] == 'text'
    assert images[0]['object_ref']['sha256'] == inputs[4][1]['sha256']
    assert 'sources' not in refs[0] and 'support' not in images[0]


def test_visual_consumer_filters_only_public_status_and_never_parses_audit(inputs):
    keep = visual_row(inputs[4][0])
    keep['concept_assessments'][0].update(review_json='not JSON', observation_json='not JSON')
    visuals = write_visuals([keep, visual_row(inputs[4][1], published=False),
        visual_row(inputs[4][1], review_status='reject'), visual_row(inputs[4][2], concept='其他概念')])
    state = run_pipeline(config(run=resolve_root() / DATASETS / 'visual_only', concepts=['流程图'], visual_source=visuals))
    refs = json.loads(rows(state['inputs'])[0]['authoring_images_json'])
    assert len(refs) == 1 and refs[0]['object_ref']['sha256'] == inputs[4][0]['sha256']


def test_article_and_visual_sources_read_their_configured_versions(inputs):
    """两张来源表均有更新版本时，仍读取各自配置的旧版本，不混入最新内容。"""
    article = article_rows(inputs)[0]
    article_source = write_articles([article])
    visual_source = write_visuals([visual_row(inputs[4][0])])
    article['knowledge'][0]['content']['paragraphs'][0] = 'NEW_ARTICLE_MUST_NOT_APPEAR'
    write_articles([article])
    write_visuals([visual_row(inputs[4][1], image_id='new_image')])

    state = run_pipeline(config(run=resolve_root() / DATASETS / 'fixed_sources', article_source=article_source, concepts=['流程图'], visual_source=visual_source))
    refs = json.loads(rows(state['inputs'])[0]['evidence_json'])
    images = json.loads(rows(state['inputs'])[0]['authoring_images_json'])
    assert len(refs) == len(images) == 1
    assert '流程图以菱形表示判断' in refs[0]['text']
    assert 'NEW_ARTICLE_MUST_NOT_APPEAR' not in refs[0]['text']
    assert images[0]['object_ref']['sha256'] == inputs[4][0]['sha256']
    assert set(images[0]) == {'number', 'kind', 'object_ref'}


def test_article_consumer_needs_only_public_content_and_status(inputs):
    uri = str(resolve_root() / 'minimal_articles.lance')
    data.from_items([{'concept': '流程图', 'review_status': 'reviewed',
        'content': [{'title': '正文', 'content': {'paragraphs': ['可独立使用的正文']}}]}]).write_lance(uri)
    state = run_pipeline(config(run=resolve_root() / DATASETS / 'minimal', concepts=['流程图'],
        article_source={'uri': uri, 'version': lance.dataset(uri).version}))
    refs = json.loads(rows(state['inputs'])[0]['evidence_json'])
    assert refs == [{'number': 1, 'kind': 'text', 'title': '正文', 'text': '可独立使用的正文'}]


def test_multiple_articles_are_kept_without_deduplication_or_conflict_checks(inputs):
    from copy import deepcopy
    first = article_rows(inputs)[0]
    second = deepcopy(first)
    second['knowledge'][0]['content']['paragraphs'][0] = '另一篇文章正文'
    source = write_articles([first, first, second])
    state = run_pipeline(config(run=resolve_root() / DATASETS / 'many_articles', article_source=source, concepts=['流程图']))
    refs = json.loads(rows(state['inputs'])[0]['evidence_json'])
    assert len(refs) == 3 and refs[0]['text'] == refs[1]['text']
    assert refs[2]['text'] == '另一篇文章正文'


def test_article_illustrations_never_enter_image_budget_or_requests(inputs):
    source = write_articles(article_rows(inputs))
    state = run_pipeline(config(run=resolve_root() / DATASETS / 'text_only', article_source=source,
        concepts=['流程图'], max_reference_images=1))
    assert state['counts'] == {'pending': 1}
    assert all(r['kind'] == 'text' for r in json.loads(rows(state['inputs'])[0]['evidence_json']))
    assert rows(state['inputs'])[0]['authoring_images_json'] == '[]'


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
    calls = SQLitePromptJournal(**journal_options(resolve_root(), str(DATASETS / 'calls__decode_error.lance')))
    assert not calls.request_ids()


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
    assert 'UPDATED_MATERIAL' in rows(changed['inputs'])[0]['evidence_json']
    assert len(SQLitePromptJournal(**journal_options(**changed['calls'])).request_ids()) == 2
    missing = run_pipeline(config(run=run, article_source=changed_source, concepts=['另一个概念']))
    assert missing['counts'] == {'pending': 1}
    assert rows(missing['inputs'])[0]['concept'] == '另一个概念'


def test_preparation_reference_resolves_nondefault_blob_table_and_version(inputs):
    from demiflow.objects import LocalObjectStore
    store = LocalObjectStore(resolve_root() / 'objects')
    blob = store.put(Path(inputs[4][0]['path']).read_bytes())
    store.put(Path(inputs[4][1]['path']).read_bytes())
    visual = visual_row(inputs[4][0])
    visual['image_uri'] = blob.uri
    source = write_visuals([visual])
    import shutil
    shutil.rmtree(resolve_root() / 'demiwtg/collect/datasets/images.lance')
    state = run_pipeline(config(run=resolve_root() / DATASETS / 'custom_source', concepts=['流程图'], visual_source=source))
    ref = json.loads(rows(state['inputs'])[0]['authoring_images_json'])[0]['object_ref']
    assert ref == blob.to_dict()
    assert ObjectRef(**ref).read() == Path(inputs[4][0]['path']).read_bytes()


def test_missing_preparation_reference_does_not_fall_back_to_raw(inputs):
    """缺少交付引用必须暴露问题，不偷偷读取默认 collect 表。"""
    visual = visual_row(inputs[4][0])
    visual['image_uri'] = None
    with pytest.raises((ValueError, LanceWriteError), match='lacks image_uri'):
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

    async def synchronized_call(self, prompt, values, **kwargs):
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
            return await original(self, prompt, values, **kwargs)
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
    assert len(SQLitePromptJournal(**journal_options(**state['calls'])).request_ids()) == 3


def test_selected_pixels_are_read_once_and_budget_failure_reads_none(inputs, monkeypatch):
    reads = []
    original = ObjectRef.read
    def counted(self):
        reads.append(self.sha256)
        return original(self)
    monkeypatch.setattr(ObjectRef, 'read', counted)
    source = write_visuals([visual_row(inputs[4][0])])
    cfg = config(run=resolve_root() / DATASETS / 'once', visual_source=source, concepts=['流程图'])
    assert run_pipeline(cfg)['counts'] == {'pending': 1}
    assert reads == [inputs[4][0]['sha256']]
    reads.clear()
    state = run_pipeline({**cfg, 'run': str(resolve_root() / DATASETS / 'budget'), 'max_context_chars': 10})
    assert state['counts'] == {'needs_context_budget': 1} and reads == []


def test_screening_source_fixed_selection_and_prompt_boundary():
    """只接固定通过版本；去重后精确选 N 个，原始理由不进入出题请求。"""
    source_uri = str(resolve_root() / 'fixtures/screening.lance')
    approved = [{'concept': f'保留{i}', 'taxonomy': ['完整路径 / 一级 / 叶子', '第二路径 / 另一个挂载'],
                 'status': 'screened', 'decision': 'keep', 'reason': '不得进入出题的粗筛理由'}
                for i in range(8)]
    excluded = [{'concept': '暂缓', 'taxonomy': [], 'status': 'screened', 'decision': 'hold', 'reason': ''},
                {'concept': '失败', 'taxonomy': [], 'status': 'failed', 'decision': None, 'reason': ''}]
    lance.write_dataset(pa.Table.from_pylist([*approved, approved[0], *excluded]), source_uri)
    cfg = config(run=resolve_root() / DATASETS / 'from_screening',
                 screening_source={'uri': source_uri, 'version': 1}, sample_size=5, sample_seed=20260928)
    state = run_pipeline(cfg)
    chosen = [row['concept'] for row in rows(state['inputs'])]
    assert len(chosen) == len(set(chosen)) == 5
    assert set(chosen) <= {row['concept'] for row in approved}
    assert state['selection']['eligible_count'] == 8
    assert set(state['selection']['concepts']) == set(chosen)
    assert state['counts'] == {'pending': 5} and cfg['max_calls'] == 5
    journal = SQLitePromptJournal(**journal_options(**state['calls'])).records('request')
    assert len(journal) == 5
    assert '不得进入出题的粗筛理由' not in json.dumps(journal, ensure_ascii=False)
    assert '完整路径 / 一级 / 叶子' in json.dumps(journal, ensure_ascii=False)
    assert '第二路径 / 另一个挂载' in json.dumps(journal, ensure_ascii=False)
    assert all(row['taxonomy'] == approved[0]['taxonomy'] for row in rows(state['inputs']))
    assert all(row['taxonomy'] == approved[0]['taxonomy'] for row in rows(state['designs']))
    assert all(row['evidence_json'] == '[]' for row in rows(state['inputs']))
    # 上游产生新版本不能改变已固定的本轮输入；不覆盖或改写上游版本。
    lance.write_dataset(pa.Table.from_pylist([dict(approved[0], concept='新版概念')]), source_uri, mode='overwrite')
    resumed = run_pipeline(cfg)
    assert {row['concept'] for row in rows(resumed['inputs'])} == set(chosen)
    assert resumed['selection']['concepts'] == state['selection']['concepts']
    assert lance.dataset(source_uri).version == 2
    # 换物理行顺序仍选中同一组，不依赖 Lance 扫描顺序。
    reversed_uri = str(resolve_root() / 'fixtures/screening_reversed.lance')
    lance.write_dataset(pa.Table.from_pylist(list(reversed([*approved, *excluded]))), reversed_uri)
    reordered = run_pipeline(config(run=resolve_root() / DATASETS / 'reordered_screening',
        screening_source={'uri': reversed_uri, 'version': 1}, sample_size=5, sample_seed=20260928))
    assert {row['concept'] for row in rows(reordered['inputs'])} == set(chosen)
    assert reordered['selection']['concepts'] == state['selection']['concepts']
    too_large = config(run=resolve_root() / DATASETS / 'insufficient_pool',
        screening_source={'uri': source_uri, 'version': 1}, sample_size=9)
    with pytest.raises(ValueError, match='Only 8'):
        run_pipeline(too_large)
    assert not (resolve_root() / DATASETS / 'calls__insufficient_pool.lance').exists()
    assert not (resolve_root() / DATASETS / 'inputs__insufficient_pool.lance').exists()


@pytest.mark.parametrize('kwargs', [
    {'screening_source': {'uri': 'source.lance', 'version': 1}},
    {'screening_source': {'uri': 'source.lance', 'version': None}, 'sample_size': 5},
    {'screening_source': {'uri': 'source.lance', 'version': True}, 'sample_size': 5},
    {'screening_source': {'uri': 'source.lance', 'version': 1}, 'sample_size': 5, 'concepts': ['不应混用']},
    {'concepts': ['概念'], 'sample_size': 5},
])
def test_screening_selection_requires_explicit_fixed_source_and_size(kwargs):
    with pytest.raises(ValueError):
        config(run='/unused', **kwargs)
