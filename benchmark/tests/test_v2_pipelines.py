"""隔离湖中的 Edit 单题流程：真实 prompt、响应检查、原图绑定和 Lance 续跑。"""
import copy
import json

import lance
import pyarrow as pa
import pytest

from demiflow.lance.records import LanceRecordStore
from demiflow.operator_llm.lance_journal import submit_response
from preparation.tests.publication_fixtures import inputs, article_publication, republish_fixture
from preparation.operaters.runfiles import saved_stage, run_manifest, read_record, prompt_store, run_records
from preparation.operaters.results import PIPELINE_STAGE_ROWS, to_stage_row
from project import resolve_root
from benchmark.edit.v2 import edit_v2_benchmark_pipeline as runtime


def question(source_image=1):
    return {
        'source_image': source_image,
        'instruction': '将原图中央的节点改为采用常见流程图规范的条件判断节点，保留连接和周围内容。',
        'test_points': [{'point': '条件判断的节点形状', 'basis': '材料 1：菱形表示判断。'}],
    }


def start(inputs, name='offline', *, target=None, write_mode='overwrite', **kwargs):
    run = resolve_root() / 'demiwtg/benchmark/edit/v2/datasets' / name
    sources = article_publication(inputs)
    cfg = runtime.config('offline', **kwargs)
    state = runtime.run_pipeline(run, sources, cfg, target_uri=target, write_mode=write_mode)
    return run, sources, cfg, state


def respond(run, result):
    row = saved_stage(run, 'design')[0]
    request = read_record(row['design_binding']['request_ref'])
    call = row['design_error']['call']
    submit_response(resolve_root(), request['native_offline']['request_ref'],
                    json.dumps({'result': result}, ensure_ascii=False), model=call['model'],
                    metadata={'reviewer': 'fixture', 'reviewer_kind': 'human'})


@pytest.mark.parametrize('write_mode', ['append', 'overwrite'])
def test_edit_single_question_roundtrip_and_resume(inputs, write_mode):
    target = str(resolve_root() / 'demiwtg/benchmark/edit/v2/datasets/chosen.lance')
    prior = {'task_id': 'previous', 'concept': '已有题目', 'status': 'unreviewed'}
    lance.write_dataset(pa.Table.from_pylist([to_stage_row(prior, 'candidates', 'fixture', 0)],
                                             schema=PIPELINE_STAGE_ROWS), target)
    run, sources, cfg, state = start(inputs, target=target, write_mode=write_mode)
    assert set(state['stages']) == {'knowledge', 'design', 'candidates'}
    assert run_manifest(run)['pipeline_module'] == 'benchmark.edit.v2'
    pending = saved_stage(run, 'design')[0]
    assert pending['status'] == 'pending'
    assert saved_stage(run, 'candidates') == ([prior] if write_mode == 'append' else [])
    request = read_record(pending['design_binding']['request_ref'])
    text = '\n'.join(p.get('text', '') for m in request['messages'] for p in m.get('content', [])
                     if isinstance(p, dict))
    assert '选择一张原图' in text and '流程图以菱形表示判断' in text
    assert '选图和出题在同一次调用中完成' in text
    assert 'max_candidates' not in text and '不能当待编辑原图' not in text
    assert request['image_roles'][0]['material_number'] == 2
    assert request['image_roles'][0]['image_number'] == 1
    respond(run, {'question': question()})
    completed = runtime.run_pipeline(run, sources, cfg, target_uri=target, write_mode=write_mode)
    results = saved_stage(run, 'candidates')
    assert len(results) == (2 if write_mode == 'append' else 1)
    result = next(row for row in results if row['task_id'] != 'previous')
    assert result['status'] == 'unreviewed'
    assert result['instruction'] == question()['instruction']
    assert result['source_image'] == 1 and result['source_material_number'] == 2
    assert result['edit_source']['sha256'] == inputs[4][0]['sha256']
    assert 'criteria' not in result and 'draft' not in result
    assert saved_stage(run, 'design')[0]['question'] == question()
    version = lance.dataset(target).version
    resumed = runtime.run_pipeline(run, sources, cfg, target_uri=target, write_mode=write_mode)
    assert resumed['stages'] == completed['stages']
    assert lance.dataset(target).version == version
    calls = LanceRecordStore(**prompt_store(run))
    assert len(calls.keys(prefix='request/')) == 1
    assert all('/design_question/' in key for key in run_records(run).keys(prefix='request/'))


@pytest.mark.parametrize('result, expected', [
    ({'question': None, 'reason': '原图已经满足拟考察的状态。'}, 'insufficient'),
    ({'question': None}, 'invalid_response'),
    ({'question': None, 'reason': '  '}, 'invalid_response'),
    ({'question': question(2)}, 'invalid_response'),
    ({'question': question(0)}, 'invalid_response'),
    ({'question': question(True)}, 'invalid_response'),
    ({'question': question('1')}, 'invalid_response'),
    ({'question': [question(), question()]}, 'invalid_response'),
    ({'question': {}}, 'invalid_response'),
    ({'question': dict(question(), criteria=[])}, 'invalid_response'),
    ({'question': dict(question(), instruction='  ')}, 'invalid_response'),
    ({'question': dict(question(), test_points=[])}, 'invalid_response'),
    ({'question': dict(question(), test_points=[{'point': '形状', 'basis': ' '}])}, 'invalid_response'),
    ({'question': question(), 'reason': '不能出题'}, 'invalid_response'),
    ({'candidates': [question()]}, 'failed'),
])
def test_invalid_or_empty_question_is_not_exported(inputs, result, expected):
    run, sources, cfg, _ = start(inputs)
    respond(run, result)
    runtime.run_pipeline(run, sources, cfg)
    design = saved_stage(run, 'design')[0]
    assert design['status'] == expected and design['reason']
    assert design['question'] is None
    assert saved_stage(run, 'candidates') == []


def test_second_image_is_selected_by_pixel_order(inputs):
    """材料号与图片号不同；模型选第 2 张图，不能绑定成第 2 条材料里的第 1 张图。"""
    sources = article_publication(inputs)
    original = sources[0]
    article = json.loads(lance.dataset(str(resolve_root() / original['relative_uri']),
                                      version=original['lance_version']).to_table().to_pylist()[0]['payload'])
    scene = copy.deepcopy(article['images'][1])
    article['published_images'].append(scene)
    article['audit']['selected_image_ids'].append(scene['image_id'])
    placement = copy.deepcopy(article['knowledge'][0]['content']['images'][0])
    placement['image_id'] = scene['image_id']
    placement['figure_number'] = 2
    article['knowledge'][0]['content']['images'].append(placement)
    sources = [republish_fixture(original, [article])]
    run = resolve_root() / 'demiwtg/benchmark/edit/v2/datasets/second_image'
    cfg = runtime.config('offline', max_units=None)
    runtime.run_pipeline(run, sources, cfg)
    pending = saved_stage(run, 'design')[0]
    assert len(pending['source_candidates']) == 2
    selected = pending['source_candidates'][1]
    respond(run, {'question': question(2)})
    runtime.run_pipeline(run, sources, cfg)
    result = saved_stage(run, 'candidates')[0]
    assert result['edit_source']['sha256'] == selected['asset']['sha256'] == inputs[4][1]['sha256']
    assert result['source_material_number'] == selected['material_number']
    assert len(LanceRecordStore(**prompt_store(run)).keys(prefix='request/')) == 1


def test_no_images_and_context_budget_do_not_call_author(inputs):
    original = article_publication(inputs)[0]
    article = json.loads(lance.dataset(str(resolve_root() / original['relative_uri']),
                                      version=original['lance_version']).to_table().to_pylist()[0]['payload'])
    article['published_images'] = []
    article['knowledge'][0]['content']['images'] = []
    sources = [republish_fixture(original, [article])]
    run = resolve_root() / 'demiwtg/benchmark/edit/v2/datasets/no_image'
    runtime.run_pipeline(run, sources, runtime.config('offline'))
    assert saved_stage(run, 'design')[0]['status'] == 'needs_source_images'
    assert not LanceRecordStore(**prompt_store(run)).keys(prefix='request/')
    limited, _, _, _ = start(inputs, name='context', max_context_chars=100)
    assert saved_stage(limited, 'design')[0]['status'] == 'needs_context_budget'
    assert not LanceRecordStore(**prompt_store(limited)).keys(prefix='request/')


def test_question_count_and_removed_stages_are_not_options():
    assert 'tasks_per_unit' not in runtime.config('offline')
    with pytest.raises(TypeError, match='tasks_per_unit'):
        runtime.config('offline', tasks_per_unit=1)
    with pytest.raises(ValueError):
        runtime.config('offline', task_types=['t2i'])
