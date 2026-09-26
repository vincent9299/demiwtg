"""Mine visible development failures; immediate signal gain is not an admission gate."""


def training_candidate(group):
    answers = group['answers']
    failures = []
    for answer in answers:
        score = answer.get('score', {})
        if (answer.get('status') == 'generated' and answer.get('judge_status') == 'scored'
                and score.get('validity') == 'ok' and score.get('core_failure_ids')):
            failures.append({'job_id': answer.get('job_id'), 'backend': answer['backend'],
                             'condition': answer['condition'],
                             'criterion_ids': score['core_failure_ids']})
    if group.get('split') == 'test':
        status = 'reserved_test'
    elif group.get('split') not in {'train', 'development'}:
        status = 'needs_split_review'
    elif (not group.get('accepted_by_machine') or
          any(a.get('score', {}).get('validity') == 'invalid_question' for a in answers)):
        status = 'needs_task_review'
    elif failures:
        status = 'candidate_needs_supervision'
    else:
        status = 'no_verified_core_failure'
    return {'policy': 'visible_failure_not_training_free_gain/1', 'status': status,
            'is_candidate': status == 'candidate_needs_supervision',
            'training_ready': False, 'training_free_gain_required': False,
            'failed_trials': failures,
            'pending_checks': ['guidance_signal_fit', 'correct_target', 'target_reference_disjointness',
                               'training_split_guard', 'held_out_training_validation']}
