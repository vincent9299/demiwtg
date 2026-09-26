"""Role separation is enforced on actual candidate expansion and answer rendering."""
import copy
import json
from pathlib import Path

import pytest

from demiflow.operator_llm.parser import parse_prompt_pack
from demiflow.schema import SchemaValidationError, validate_instance
from preparation.operaters.inputs import SplitGuard
from preparation.tests.publication_fixtures import inputs
from curation.t2i.operaters.candidates import ExpandCandidates
from curation.t2i.operaters.operators import BindTrainingInputs
from curation.t2i.tests.fixtures import designed_row, visual_only_training, material_catalog, design_result


def expand_and_bind(row, cfg, registry):
    guard = SplitGuard(registry)
    candidate = list(ExpandCandidates(guard, cfg)(row))[0]
    assert candidate['status'] == 'constructed', candidate.get('issues')
    # Simulate only the interface of the future reviewer, not semantic approval.
    candidate.update(status='accepted_task', criteria=[{
        'knowledge_ids': [m['item_id'] for m in candidate['materials']]}])
    return candidate, BindTrainingInputs(guard)(candidate)


@pytest.mark.parametrize('input_numbers', [[], [2], [1]])
def test_construction_evidence_does_not_implicitly_enter_answer(inputs, input_numbers):
    row, cfg = designed_row(inputs)
    design = row['candidate_design']['candidates'][0]
    design.update(input_materials=input_numbers, learning_objective='INTERNAL_LEARNING_OBJECTIVE',
                  knowledge_application='INTERNAL_APPLICATION',
                  reference_selection_reason='INTERNAL_SELECTION_REASON')
    candidate, bound = expand_and_bind(row, cfg, inputs[2])
    assert bound['status'] == 'accepted_task'
    assert [m['kind'] for m in candidate['materials']] == ['text', 'image']
    expected = [] if not input_numbers else (['image'] if input_numbers == [2] else ['text', 'image'])
    assert [m['kind'] for m in bound['answer_materials']] == expected
    serialized = json.dumps(bound['answer_input'], ensure_ascii=False)
    assert 'INTERNAL_' not in serialized
    assert design['draft']['criteria'][0]['requirement'] not in serialized
    if 1 not in input_numbers:
        assert candidate['materials'][0]['text'] not in serialized
    if not input_numbers:
        assert bound['answer_input'] == {
            'messages': [{'role': 'user', 'content': [{'type': 'text', 'text': design['draft']['instruction']}]}],
            'image_roles': []}
    else:
        assert len(bound['answer_input']['image_roles']) == 1
        assert bound['answer_input']['image_roles'][0]['role'] == 'reference'


def test_answer_selection_can_differ_from_construction_evidence(inputs):
    row, cfg = designed_row(inputs)
    item = copy.deepcopy(row['materials'][0])
    item.update(item_id='answer_context', text='PUBLIC_INPUT_CONTEXT', sources=[])
    item['publication']['visual_dependencies'] = []
    row['materials'].append(item)
    row['candidate_design']['candidates'][0]['input_materials'] = [len(row['materials'])]
    candidate, bound = expand_and_bind(row, cfg, inputs[2])
    assert bound['status'] == 'accepted_task'
    assert 'answer_context' not in {m['item_id'] for m in candidate['materials']}
    assert [m['item_id'] for m in bound['answer_materials']] == ['answer_context']
    assert 'PUBLIC_INPUT_CONTEXT' in json.dumps(bound['answer_input'])
    assert not bound['answer_input']['image_roles']


def test_changing_answer_materials_cannot_bypass_reserved_construction_family(inputs):
    row, cfg = designed_row(inputs)
    candidate, _ = expand_and_bind(row, cfg, inputs[2])
    from demiflow.execution.artifacts import read
    registry = read(inputs[2])
    registry['formal_test']['rule_families'] = [candidate['plan']['rule_family']]
    row['candidate_design']['candidates'][0]['input_materials'] = []
    rejected = list(ExpandCandidates(SplitGuard(registry), cfg)(row))[0]
    assert rejected['status'] == 'invalid_candidate'


@pytest.mark.parametrize('field,bad', [('learning_objective', ''), ('input_materials', None),
                                      ('input_materials', [999]), ('input_materials', [True])])
def test_incomplete_learning_or_input_contract_is_rejected(inputs, field, bad):
    row, cfg = designed_row(inputs)
    row['candidate_design']['candidates'][0][field] = bad
    out = list(ExpandCandidates(SplitGuard(inputs[2]), cfg)(row))[0]
    assert out['status'] == 'invalid_candidate'
    assert 'answer_input' not in out


def test_bound_answer_materials_cannot_be_changed_independently(inputs):
    row, cfg = designed_row(inputs)
    candidate, bound = expand_and_bind(row, cfg, inputs[2])
    bound['answer_materials'][0]['text'] += 'CHANGED_INPUT'
    assert bound['materials'][0]['text'] == candidate['materials'][0]['text']
    rejected = BindTrainingInputs(SplitGuard(inputs[2]))(bound)
    assert rejected['status'] == 'invalid_training_inputs'
    assert 'answer_input' not in rejected


def test_target_cannot_enter_answer_even_when_construction_evidence_is_safe(inputs):
    ref, cfg = visual_only_training(inputs)
    row = material_catalog(inputs[0] / 'target_roles', [], cfg, visual_runs=[ref])[0]
    target = row['target_pool'][0]
    row['design_targets'] = [target]
    target_number = next(n for n, m in enumerate(row['materials'], 1)
                         if m['asset']['sha256'] == target['sha256'])
    evidence = next(n for n in range(1, len(row['materials']) + 1) if n != target_number)
    design = design_result(inputs[-1]); candidate = design['candidates'][0]
    candidate.update(evidence=[evidence], input_materials=[target_number], target_candidates=[1],
                     target_support='目标能看见节点和分支。')
    candidate['draft']['criteria'][0]['evidence'] = [evidence]
    row.update(status='candidates_designed', candidate_design=design)
    expand = ExpandCandidates(SplitGuard(inputs[2]), cfg)
    rejected = list(expand(row))[0]
    assert rejected['status'] == 'invalid_candidate'
    assert 'duplicates a selected target' in str(rejected['issues'])
    candidate['input_materials'] = []
    valid = list(expand(row))[0]
    assert valid['status'] == 'constructed'
    assert valid['target_design_binding']['learning_objective'] == candidate['learning_objective']
    candidate.pop('target_support')
    assert list(expand(row))[0]['status'] == 'invalid_candidate'


def test_missing_required_input_figure_is_not_silently_dropped(inputs):
    row, cfg = designed_row(inputs)
    row['materials'][0]['publication']['visual_dependencies'] = ['missing_figure']
    row['candidate_design']['candidates'][0]['evidence'] = [2]
    row['candidate_design']['candidates'][0]['draft']['criteria'][0]['evidence'] = [2]
    result = list(ExpandCandidates(SplitGuard(inputs[2]), cfg)(row))[0]
    assert result['status'] == 'invalid_candidate'
    assert 'missing figure' in str(result['issues'])


def test_response_schema_requires_explicit_roles_but_allows_instruction_only(inputs):
    text = (Path(__file__).resolve().parents[1] / 'prompts/tasks.yaml').read_text()
    pack = parse_prompt_pack(text)
    schema = pack.prompt_definitions['design_candidates'].response_schema
    response = {'result': design_result(inputs[-1])}
    response['result']['candidates'][0]['input_materials'] = []
    validate_instance(response, schema)
    validate_instance({'result': {'status': 'insufficient', 'reason': '目标看不到所需结构'}}, schema)
    for field in ['learning_objective', 'input_materials']:
        missing = copy.deepcopy(response)
        missing['result']['candidates'][0].pop(field)
        with pytest.raises(SchemaValidationError):
            validate_instance(missing, schema)
