"""审定→出题的真实文档引用、渲染、失败留行和原生离线调用验证；不评价事实质量。"""
from copy import deepcopy
import json
from pathlib import Path
from urllib.parse import unquote, urlsplit

import lance
import pyarrow as pa
import pytest
from demiflow import data
from demiflow.collect.documents import store_document
from demiflow.operator_llm.call_ref import read_call
from demiflow.agent import load_agent_config
from benchmark.t2i.v2.tests.agent_fixture import submit_design_response as submit_response
from demiflow.operator_llm.template import render_template
from project import resolve_root
from preparation.concepts.operators.schema import ADOPTED
from benchmark.t2i.v2.operators import authoring, concept_context
from benchmark.t2i.v2.t2i_v2_benchmark_pipeline import PROMPTS, DATASETS, run_pipeline
from benchmark.t2i.v2.tests.agent_fixture import business_config as config


@pytest.fixture
def adopted(tmp_path):
    ref = store_document(tmp_path / 'documents',
        '仅适用于指定实例的完整原文。\n\n另一段证据，允许不同合法外观。\n\n未引用的正文。'.encode(),
        url='https://fixture.invalid/tool', final_url='https://fixture.invalid/tool',
        content_type='text/plain', retrieved_at='2026-01-01T00:00:00Z')
    selected = [dict(evidence_id=f'{ref["sha256"]}:b{i:06d}', document_ref=ref, block_id=f'b{i:06d}',
                     claim_ids=['identity'], round=0) for i in range(3)]
    return dict(source_record_id='source1', concept_id='concept1', original_name='剪子',
        assessment_id='audit1', status='assessed', adoption_status='accepted', review_note='隔离测试采纳',
        taxonomy=['工具'], selected=selected,
        assessment=dict(identity_status='resolved', canonical_name='剪刀', definition='仅作流程测试的对象类别。',
            concept_kind='对象类别', qualifiers=['不限于某一种柄部外形'], name_relation='equivalent',
            identity_evidence_ids=[selected[0]['evidence_id']],
            core_facts=[dict(fact_id='F1', aspect='结构', statement='测试结构事实。',
                conditions='仅适用于指定实例', allowed_variations='允许不同合法外观',
                evidence_ids=[item['evidence_id'] for item in selected[:2]])],
            task_status='ready', task_sketch=dict(direction='观察测试配合关系。', conditions='接合部位可见',
                visual_check='两部件的相互关系', substantive_error='部件虽齐全但不能配合',
                allowed_variations='不固定背景或颜色', fact_ids=['F1']),
            gaps=['精确角度尚未核实']))


def input_row(adopted):
    return {**authoring.adopted_concept(adopted, source={'uri': 'fixed.lance', 'version': 1}),
            'status': 'ready', 'reason': '', 'evidence_json': '[]'}


def prepare(row, **kwargs):
    return authoring.prepare_request(row, max_context_chars=kwargs.get('budget', 60000), prompt_chars=100)


def rows(ref):
    return data.read_lance(**ref).take_all()


def test_context_keeps_definition_and_sources_without_audit_guidance(adopted, monkeypatch):
    reads = []
    reader = concept_context.read_document
    monkeypatch.setattr(concept_context, 'read_document', lambda ref: reads.append(ref) or reader(ref))
    row = input_row(adopted)
    prepared = prepare(row)
    text = prepared['concept_context']
    assert prepared['status'] == 'ready' and len(reads) == 1
    assert prepared['prompt_payload'] == {'concept': '剪刀', 'taxonomy': ['工具'], 'positive_examples': []}
    assert row['concept_record']['adopted_source'] == {'uri': 'fixed.lance', 'version': 1}
    assert len(row['concept_record']['evidence']) == 2
    for value in ['剪子', adopted['assessment']['definition'], 'https://fixture.invalid/tool',
                  '原文块：b000000', '原文块：b000001']:
        assert value in text
    for field in ('core_facts', 'gaps', 'qualifiers'):
        assert prepared['concept_record'][field] == adopted['assessment'][field]
    for value in ['已核实的相关内容', '尚未核实的事项', '不限于某一种柄部外形',
                  '测试结构事实。', '仅适用于指定实例', '允许不同合法外观', '精确角度尚未核实']:
        assert value not in text
    sketch = adopted['assessment']['task_sketch']
    assert prepared['concept_record']['task_sketch'] == sketch
    assert '可考虑的出题方向' not in text
    for field in ('direction', 'conditions', 'visual_check', 'substantive_error', 'allowed_variations'):
        assert sketch[field] not in text
    assert '未引用的正文' not in text and 'file://' not in text and 'fixed.lance' not in text
    assert '审定材料 E1' in text and '审定材料 E2' in text
    # 重排上游选块不改变正文和编号；不把动态时间或内部路径加入模型请求。
    reordered = deepcopy(adopted)
    reordered['selected'].reverse()
    assert input_row(reordered) == row
    assert prepare(input_row(reordered))['concept_context'] == text


@pytest.mark.parametrize('damage', ['missing_reference', 'mismatched_id', 'missing_block', 'missing_file', 'sha_mismatch'])
def test_bad_evidence_is_technical_failure_before_image_or_model(adopted, monkeypatch, damage):
    row = input_row(adopted)
    record = row['concept_record']
    item = record['evidence'][0]
    if damage == 'missing_reference':
        record['evidence'] = []
    elif damage == 'mismatched_id':
        item['block_id'] = 'b999999'
    elif damage == 'missing_block':
        old = item['evidence_id']
        item.update(block_id='b999999', evidence_id=f'{item["document_ref"]["sha256"]}:b999999')
        record['identity_evidence_ids'] = [item['evidence_id']]
        record['core_facts'][0]['evidence_ids'] = [item['evidence_id'] if e == old else e
                                                for e in record['core_facts'][0]['evidence_ids']]
    elif damage == 'missing_file':
        item['document_ref']['uri'] += '_missing'
    else:
        Path(unquote(urlsplit(item['document_ref']['uri']).path)).write_text('corrupted')
    row['authoring_images_json'] = json.dumps([{'kind': 'image', 'number': 1, 'object_ref': {}}])
    monkeypatch.setattr(authoring, 'image_data_url', lambda _: pytest.fail('image should not be read'))
    prepared = prepare(row)
    assert prepared['status'] == 'invalid_evidence' and prepared['reason']
    assert prepared['prompt_images'] == [] and prepared['concept_context'] == ''


def test_budget_counts_complete_audit_context_before_reading_images(adopted, monkeypatch):
    row = input_row(adopted)
    row['authoring_images_json'] = json.dumps([{'kind': 'image', 'number': 1, 'object_ref': {}}])
    monkeypatch.setattr(authoring, 'image_data_url', lambda _: 'data:image/png;base64,fixture')
    complete = prepare(row)
    total = 100 + len(json.dumps(complete['prompt_payload'], ensure_ascii=False)) + len(complete['concept_context'])
    monkeypatch.setattr(authoring, 'image_data_url', lambda _: pytest.fail('over budget'))
    over = prepare(row, budget=total - 1)
    assert over['status'] == 'needs_context_budget'
    assert over['concept_context'] == complete['concept_context']  # no hidden truncation
    assert over['prompt_images'] == []


def test_actual_prompt_has_shared_rules_before_all_variables(adopted):
    prompt = load_agent_config(PROMPTS / 'agent_codex.yaml').prompt_pack.prompt_definitions['design_question']
    template = prompt.template
    prepared = prepare(input_row(adopted))
    values = dict(payload=prepared['prompt_payload'], concept_context=prepared['concept_context'], images=[])
    first = ''.join(part.text for part in render_template(template, values))
    other = dict(payload={'concept': '完全不同的概念', 'taxonomy': [], 'positive_examples': []},
                 concept_context='', images=[])
    second = ''.join(part.text for part in render_template(template, other))
    prefix = template.source[:template.placeholders[0].start]
    assert first.startswith(prefix) and second.startswith(prefix)
    assert '**六、输出**' in prefix and '"question": null' in prefix
    assert not any(section in template.source[template.placeholders[0].start:]
                   for section in ('**一、', '**二、', '**三、', '**四、', '**五、', '**六、'))
    assert first.index('## 概念审定资料') > len(prefix)
    assert '仅适用于指定实例的完整原文。' not in first
    assert adopted['assessment']['definition'] in first
    assert 'concept_record' not in first and '审定材料 E2' in first
    assert 'task_sketch' not in first and '可考虑的出题方向' not in prepared['concept_context']
    assert adopted['assessment']['task_sketch']['direction'] not in first
    assert 'references' not in prepared['prompt_payload'] and 'authoring_condition' not in prepared['prompt_payload']
    assert all(old not in template.source for old in ('references', 'without_positive_images', '开卷', '闭卷'))


def test_native_handoff_allows_no_question_and_binds_changed_context(adopted):
    root = resolve_root()
    uri = str(root / 'adopted.lance')
    hold = deepcopy(adopted)
    hold.update(original_name='暂缓对象', concept_id='hold', source_record_id='hold')
    hold['assessment']['task_status'] = 'hold'
    lance.write_dataset(pa.Table.from_pylist([adopted, hold], schema=ADOPTED), uri)
    source = {'uri': uri, 'version': lance.dataset(uri).version}
    cfg = config(run=root / DATASETS / 'handoff', concept_audit_source=source, sample_size=1)
    state = run_pipeline(cfg)
    assert state['counts'] == {'pending': 1} and state['selection']['eligible_count'] == 1
    design = rows(state['designs'])[0]
    call = json.loads(design['call_json'])
    request = json.dumps(read_call(call['request_ref'], root), ensure_ascii=False)
    assert '仅适用于指定实例的完整原文。' not in request and '精确角度尚未核实' not in request
    assert adopted['assessment']['definition'] in request and 'https://fixture.invalid/tool' in request
    submit_response(root, call['request_ref'], json.dumps({'result': {'question': None, 'reason': '方向未达到质量门槛'}}),
                    model=call['model'], metadata={'reviewer': 'fixture', 'reviewer_kind': 'human'})
    completed = run_pipeline(cfg)
    assert completed['complete'] and completed['counts'] == {'insufficient': 1}
    assert rows(completed['candidates']) == []
    assert json.loads(rows(completed['designs'])[0]['call_json'])['reused']
    # 不发送的审定缺口变化不引起新的模型请求；概念说明变化仍必须重新出题。
    adopted['assessment']['gaps'].append('新增的关键范围疑问')
    lance.write_dataset(pa.Table.from_pylist([adopted, hold], schema=ADOPTED), uri, mode='overwrite')
    fixed = run_pipeline(cfg)
    assert fixed['counts'] == {'insufficient': 1}
    unchanged = run_pipeline({**cfg, 'concept_audit_source': {'uri': uri, 'version': lance.dataset(uri).version}})
    assert unchanged['counts'] == {'insufficient': 1}
    assert json.loads(rows(unchanged['designs'])[0]['call_json'])['reused']
    adopted['assessment']['definition'] += '概念说明新增对象范围。'
    lance.write_dataset(pa.Table.from_pylist([adopted, hold], schema=ADOPTED), uri, mode='overwrite')
    changed = run_pipeline({**cfg, 'concept_audit_source': {'uri': uri, 'version': lance.dataset(uri).version}})
    assert changed['counts'] == {'pending': 1}
    changed_call = json.loads(rows(changed['designs'])[0]['call_json'])
    assert changed_call['request_ref']['request_id'] != call['request_ref']['request_id']
    # 损坏对象不能吞掉这行或记成业务不足，也不得申请模型请求。
    ref = adopted['selected'][0]['document_ref']
    Path(unquote(urlsplit(ref['uri']).path)).write_text('corrupt after snapshot')
    failed = run_pipeline(cfg)
    assert failed['counts'] == {'invalid_evidence': 1} and not failed['complete']
    failed_row = rows(failed['designs'])[0]
    assert failed_row['concept'] == '剪子' and json.loads(failed_row['call_json']) == {}
