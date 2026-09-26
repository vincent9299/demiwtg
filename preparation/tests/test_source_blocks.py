import copy
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from preparation.tests.fixtures import data

from preparation.operaters.text import (pack_whole, BuildSourceBlocks, BatchSourceBlocks, ApplyBlockSelection,
    block_role)


def sample():
    texts = ['在甲地，最高音笙高7.2厘米～14.5厘米（不包括笙脚，下同）。',
             'Between the beginning of spring and autumn harvest, lusheng playing is prohibited.']
    full = '\n\n'.join(texts)
    blocks, cursor = [], 0
    for i, text in enumerate(texts):
        blocks.append({'block_id': str(i), 'text': text, 'clean_start': cursor,
            'clean_end': cursor+len(text), 'decision': 'keep', 'section': ['说明'], 'kind': 'paragraph'})
        cursor += len(text)+2
    m = {'material_id': 'M1', 'record': {'title': '芦笙', 'url': 'https://example.test/a'},
         'provenance': {}, 'cleaning': {'text': full, 'blocks': blocks,
             'source_locator': {}, 'source_sha256': 'raw', 'version': 'test'}}
    return {'case_id': 'case', 'identity_materials': [m],
            'identity': {'target_label': '芦笙', 'accepted_material_ids': ['M1']}}


def candidates():
    row = BuildSourceBlocks(unit_chars=1)(sample())
    row['block_decisions'] = [{'unit_id': u['unit_id'], 'decision': 'selected', 'reason': '完整',
                              'source_check_needed': True} for u in row['source_units']]
    return row


def test_all_blocks_and_oversized_blocks_survive_with_conditions_and_offsets():
    row = candidates()
    assert len(row['source_units']) == 2
    assert len(list(pack_whole(row['source_units'], 1))) == 2
    for u in row['source_units']:
        assert sample()['identity_materials'][0]['cleaning']['text'][u['start']:u['end']] == u['text']
    assert '不包括笙脚' in row['source_units'][0]['text']
    assert 'beginning of spring' in row['source_units'][1]['text']


def test_missing_duplicate_and_unknown_decisions_do_not_silently_accept():
    row = candidates()
    request = BatchSourceBlocks()(row)[0]
    first, second = [u['unit_id'] for u in row['source_units']]
    d = {'unit_id': first, 'decision': 'selected', 'reason': 'ok', 'source_check_needed': True}
    request['prompt_result'] = {'decisions': [d, d, {**d, 'unit_id': 'invented'}]}
    result = ApplyBlockSelection()(request)
    assert all(d['decision'] == 'deferred' for d in result['block_decisions'])
    assert result['block_calls'][0]['unknown_ids'] == ['invented']






def test_source_variants_not_deleted_and_identity_rejections_not_reintroduced():
    row = sample()
    second = copy.deepcopy(row['identity_materials'][0]);second['material_id'] = 'M2'
    row['identity_materials'].append(second)
    one = BuildSourceBlocks()(row)
    assert {u['material_id'] for u in one['source_units']} == {'M1'}
    row['identity']['accepted_material_ids'].append('M2')
    two = BuildSourceBlocks()(row)
    assert {u['material_id'] for u in two['source_units']} == {'M1', 'M2'}


def test_strict_body_filter_preserves_prose_citations_and_removes_cards_before_model():
    assert block_role({'section': ['构造'], 'text': '根据[论文](https://other.test/paper)，管长不包括笙脚。'}) == 'body_candidate'
    assert block_role({'section': ['目录'], 'text': '芦笙由笙斗和笙管等构成。'}) == 'body_candidate'
    assert block_role({'section': ['相关星图'], 'text': '芦笙和很多其他乐器卡片'}) == 'related_section'
    assert block_role({'section': [], 'text': '![甲](https://a.test)![乙](https://b.test)'}) == 'multiple_link_cards'
    row = sample()
    row['identity_materials'][0]['cleaning']['blocks'][0]['section'] = ['相关星图']
    out = BuildSourceBlocks(body_only=True)(row)
    assert len(out['source_units']) == 1
    assert 'beginning of spring' in out['source_units'][0]['text']
    assert out['block_scope']['documents'][0]['structurally_filtered'][0]['role'] == 'related_section'


def test_strict_scores_gate_selected_and_bad_ids_defer_without_crashing():
    request = BatchSourceBlocks()(candidates())[0]
    request['prompt_result'] = {'decisions': [{'unit_id': u['unit_id'], 'decision': 'selected',
        'reason': '有些有关但混合', 'source_check_needed': True, 'relevance_score': 2,
        'usability_score': 3, 'standalone': True, 'mixed_content': True}
        for u in request['block_prompt']['units']] + [{'unit_id': []}]}
    result = ApplyBlockSelection(strict=True)(request)
    assert all(d['decision'] == 'deferred' and d['model_decision'] == 'selected' for d in result['block_decisions'])


def test_missing_optional_source_flag_defaults_to_review_not_quality_failure():
    request = BatchSourceBlocks()(candidates())[0]
    request['prompt_result'] = {'decisions': [{'unit_id': u['unit_id'], 'decision': 'selected',
        'reason': '完整正文', 'relevance_score': 3, 'usability_score': 3,
        'standalone': True, 'mixed_content': False} for u in request['block_prompt']['units']]}
    result = ApplyBlockSelection(strict=True)(request)
    assert all(d['decision'] == 'selected' and d['source_check_needed'] and d['protocol_notes']
               for d in result['block_decisions'])
    del request['prompt_result']['decisions'][0]['standalone']
    assert ApplyBlockSelection(strict=True)(request)['block_decisions'][0]['decision'] == 'deferred'


def test_strict_model_score_cannot_override_unparsed_interface_marker():
    request = {'case_id': 'c', 'batch_id': 'b', 'block_prompt': {'units': [
        {'unit_id': 'u', 'text': '音阶\n\n[编辑] 管序 1 2 3'}]},
        'prompt_result': {'decisions': [{'unit_id': 'u', 'decision': 'selected',
            'reason': '模型误判最高分', 'relevance_score': 3, 'usability_score': 3,
            'standalone': True, 'mixed_content': False}]}}
    d = ApplyBlockSelection(strict=True)(request)['block_decisions'][0]
    assert d['decision'] == 'deferred' and d['model_decision'] == 'selected'


def test_strict_rejects_image_markup_even_when_model_gives_full_marks():
    for text in ('节日期间随着芦笙的乐曲起舞。 ![]',
                 '正文\n\n![](https://example.test/image.jpg)苗族芦笙，在贵州',
                 '正文 <img src="image.jpg">'):
        request = {'case_id':'c','batch_id':'b','block_prompt':{'units':[{'unit_id':'u','text':text}]},
            'prompt_result':{'decisions':[{'unit_id':'u','decision':'selected','reason':'模型满分',
                'relevance_score':3,'usability_score':3,'standalone':True,'mixed_content':False}]}}
        d=ApplyBlockSelection(strict=True)(request)['block_decisions'][0]
        assert d['decision']=='deferred' and '图片' in d['reason']




