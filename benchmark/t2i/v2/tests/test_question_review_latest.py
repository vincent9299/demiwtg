"""真实固定表上的全量合并：新题身份、失败保留、跨页搜索及只读请求预算。"""
import json
from pathlib import Path
import pytest
from demiflow import data
from project import resolve_root
from benchmark.t2i.v2.operators import question_review_latest as latest, question_review_viewer as viewer
from benchmark.t2i.v2.operators.run_tables import RunTables


def review_row(concept, key, instruction, status='ready', original=None):
    points = [{'point': '知识考点', 'basis': '固定资料', 'criterion': '可观察要求'}]
    result = {'decision': status, 'question': {'instruction': instruction, 'test_points': points}
        if status == 'ready' else None, 'changes': [], 'issues': [], 'requirements': []}
    return {'concept': concept, 'source_task_id': key, 'task_id': key, 'review_status': status,
        'review_reason': '实际审核结论', 'original_instruction': original or instruction,
        'original_test_points_json': json.dumps(points, ensure_ascii=False),
        'instruction': instruction if status == 'ready' else '', 'test_points': points,
        'requirements_json': '[]', 'requires_new_answer': False, 'question_revision': 'fixture',
        'review_result_json': json.dumps(result, ensure_ascii=False),
        'review_call_json': json.dumps({'request_ref': {'id': key}, 'runtime': 'fixture'}),
        'authoring_images_json': '[]', 'authoring_variant': 'standard'}


def setup_view():
    root = resolve_root(); project = root / 'demiwtg'; owner = project / 'benchmark/t2i/v2'
    (owner / 'datasets').mkdir(parents=True, exist_ok=True)
    sources = {
        'original': [review_row('甲', 'old-alpha', '原始甲题'), review_row('乙', 'old-beta', '原始乙题', 'fail')],
        'retry': [review_row('甲', 'old-alpha', '原始甲题', 'hold'), review_row('乙', 'old-beta', '补跑乙题', original='原始乙题')],
        'fresh': [review_row('乙', 'new-beta', '重新出题后的乙题')],
    }
    for run, rows in sources.items():
        uri = str(owner / 'datasets' / f'{run}.lance')
        data.from_items(rows).write_lance(uri, mode='overwrite')
        directory = owner / 'runs' / run; directory.mkdir(parents=True)
        (directory / 'config.json').write_text(json.dumps({'sample_size': len(rows), 'review_source': {'uri': uri, 'version': 1}}))
        RunTables(root, str(owner / 'datasets' / f'records__{run}.lance')).save({
            'phase': 'question_review', 'complete': True,
            'question_review': {'outputs': {'question_reviews': {'uri': uri, 'version': 1}}}})
    cohort = str(owner / 'datasets/cohort.lance')
    data.from_items([{'concept': name, 'selection_rank': i} for i, name in enumerate(['甲', '乙', '母狮'])]).write_lance(cohort)
    designs = str(owner / 'datasets/author.lance')
    data.from_items([{'concept': '母狮', 'status': 'insufficient', 'reason': '未形成可核验题目。'}]).write_lance(designs)
    RunTables(root, str(owner / 'datasets/records__author.lance')).save({'designs': {'uri': designs, 'version': 1}})
    return project, {'uri': cohort, 'version': 1}, sources


def payload(document):
    encoded = document.split('<script id="review-data" type="application/json">', 1)[1].split('</script>', 1)[0]
    return json.loads(encoded)


def test_latest_priority_does_not_mask_hold_or_drop_insufficient():
    project, cohort, _ = setup_view()
    snapshot = latest.latest_snapshot(project, ['original', 'retry', 'fresh'], cohort, authoring_run='author')
    rows = snapshot['overview']
    assert [(row['concept'], row['review_status']) for row in rows] == [('甲', 'hold'), ('乙', 'ready'), ('母狮', 'insufficient')]
    assert rows[1]['source_task_id'] == 'new-beta' and rows[1]['modified']
    assert snapshot['baseline']['乙']['instruction'] == '原始乙题'
    document, meta = latest.build_latest_browser(project, snapshot, query='母狮', page_size=1)
    assert meta['expected'] == 3 and meta['counts'] == {'hold': 1, 'ready': 1, 'insufficient': 1}
    assert payload(document)['cases'][0]['review_reason'] == '未形成可核验题目。'


def test_global_filter_reads_only_selected_request_and_keeps_original_and_fixed_version(monkeypatch):
    project, cohort, sources = setup_view()
    snapshot = latest.latest_snapshot(project, ['original', 'retry', 'fresh'], cohort, authoring_run='author')
    reads = []

    def read_request(ref, root):
        reads.append(ref['id'])
        return {'messages': [{'content': [{'type': 'text', 'text': '真实输入 ' + ref['id']}]}],
            'model': 'fixture', 'prompt_version': 'fixture', 'settings': {}}

    monkeypatch.setattr(viewer, 'read_call', read_request)
    document, meta = latest.build_latest_browser(project, snapshot, modified_only=True, page_size=1)
    case = payload(document)['cases'][0]
    assert reads == ['new-beta'] and meta['matched'] == 1
    assert case['original_instruction'] == '原始乙题'
    assert case['instruction'] == '重新出题后的乙题' and case['requires_new_answer']
    assert case['review_input_instruction'] == '重新出题后的乙题'
    assert case['actual_request'] == '真实输入 new-beta'
    # 新表头不改变这个浏览快照，也不让翻页回退到旧题身份。
    changed = [{**sources['fresh'][0], 'instruction': '未来版本'}]
    data.from_items(changed).write_lance(snapshot['snapshots']['fresh']['ref']['uri'], mode='overwrite')
    document, _ = latest.build_latest_browser(project, snapshot, query='乙')
    assert payload(document)['cases'][0]['instruction'] == '重新出题后的乙题'
    with pytest.raises(ValueError, match='selected scope'):
        latest.build_latest_browser(project, snapshot, query='乙', page=2)
