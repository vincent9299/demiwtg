"""Training admission uses visible failures, independently of immediate guidance gains."""
import pytest

from curation.evaluation.native.training_candidates import training_candidate
from curation.evaluation.native.scores import compare_group


def group():
    answers = []
    for condition in ('without_knowledge', 'with_knowledge'):
        answers.append({'job_id': condition, 'backend': 'qwen2512', 'condition': condition,
                        'status': 'generated', 'judge_status': 'scored', 'knowledge_mode': 'text_only',
                        'score': {'validity': 'ok', 'core_failure_ids': ['R1'],
                                  'metrics': {'alignment_score': 0, 'quality_score': 60, 'aesthetic_score': 60}}})
    return {'split': 'development', 'accepted_by_machine': True,
            'question': {'task_type': 't2i'}, 'answers': answers}


def test_zero_and_negative_immediate_gain_do_not_reject_training_candidate():
    sample = group()
    result = compare_group(sample)
    assert result['pairs'][0]['delta'] == 0
    assert result['training_candidate']['is_candidate']
    sample['answers'][0]['score']['metrics']['alignment_score'] = 60
    result = compare_group(sample)
    assert result['pairs'][0]['delta'] == -60
    assert result['training_candidate']['is_candidate']
    assert not result['training_candidate']['training_ready']


def test_baseline_failure_alone_is_enough_for_provisional_candidate():
    sample = group()
    sample['answers'] = sample['answers'][:1]
    result = compare_group(sample)
    assert all(not pair['valid'] for pair in result['pairs'])
    assert result['training_candidate']['is_candidate']
    assert 'correct_target' in result['training_candidate']['pending_checks']


@pytest.mark.parametrize('change, status', [
    ({'split': 'test'}, 'reserved_test'),
    ({'split': None}, 'needs_split_review'),
    ({'accepted_by_machine': False}, 'needs_task_review'),
])
def test_failure_does_not_bypass_task_review_or_test_reservation(change, status):
    sample = {**group(), **change}
    result = training_candidate(sample)
    assert result['status'] == status
    assert not result['is_candidate']


def test_infrastructure_errors_and_unscorable_outputs_are_not_drawing_failures():
    sample = group()
    sample['answers'][0]['status'] = 'infra_failure'
    sample['answers'][1]['score']['validity'] = 'judge_unscorable'
    result = training_candidate(sample)
    assert not result['is_candidate'] and not result['failed_trials']
    invalid = group()
    invalid['answers'][1]['score']['validity'] = 'invalid_question'
    assert training_candidate(invalid)['status'] == 'needs_task_review'
    correct = group()
    for arm in correct['answers']:
        arm['score']['core_failure_ids'] = []
    assert not training_candidate(correct)['is_candidate']
