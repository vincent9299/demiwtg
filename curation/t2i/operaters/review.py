"""Validate a frozen T2I sample and interpret a separate pixel-aware review."""
from demiflow.execution.artifacts import digest
from preparation.operaters.inputs import pixels
from curation.t2i.operaters.authoring import evidence_numbers, require_texts
from curation.t2i.operaters.operators import fail, BindTrainingInputs

CHECKS = ('grounded', 'input_sufficient', 'learning_objective_visible',
          'instruction_satisfied', 'image_quality_usable', 'nonleaking')


def sample_identity(row):
    """Bind review to both material selections, the exact task and target pixels."""
    return digest({key: row[key] for key in (
        'concept', 'plan', 'draft', 'learning_objective', 'criteria', 'materials',
        'answer_materials', 'construction_selection', 'training_input_binding',
        'target_design_binding', 'design_targets')})


class ValidateSample:
    def __init__(self, guard):
        self.guard = guard

    def __call__(self, row):
        if row['status'] != 'constructed':
            return row
        try:
            if row['branch'] != 'training' or row['plan']['task_type'] != 't2i' or row.get('edit_source'):
                raise ValueError('Expected a T2I training candidate')
            self.guard.check_plan(row['plan'], 'training')
            require_texts(row, ['learning_objective'])
            require_texts(row['draft'], ['instruction'])
            self.guard.check_instruction(row['draft']['instruction'], True)
            targets = row['design_targets']
            if len(targets) != 1:
                raise ValueError('A target visit must bind exactly one supervision image')
            binding = row['target_design_binding']
            require_texts(binding, ['target_support'])
            if (binding['target_sha256'] != [targets[0]['sha256']]
                    or binding['learning_objective'] != row['learning_objective']):
                raise ValueError('Target or learning objective changed after design')
            pixels(targets[0])
            if not self.guard.allows_asset(targets[0], True):
                raise ValueError('Target violates formal-test reservation')
            if not row['draft']['criteria']:
                raise ValueError('Observable criteria required')
            criteria = []
            for item in row['draft']['criteria']:
                require_texts(item, ['requirement', 'observable_region', 'allowed_variation'])
                numbers = evidence_numbers(item['evidence'], len(row['materials']))
                criteria.append({**item, 'knowledge_ids': [row['materials'][n-1]['item_id'] for n in numbers]})
            # Exercise the same binding checks before paying for review, but do
            # not expose this provisional input as an accepted sample.
            bound = BindTrainingInputs(self.guard)({**row, 'criteria': criteria, 'status': 'accepted_task'})
            if bound['status'] != 'accepted_task':
                return bound
            out = {**row, 'criteria': criteria, 'status': 'valid_sample'}
            return {**out, 'sample_sha256': sample_identity(out)}
        except (ValueError, TypeError, KeyError, OSError) as error:
            return fail(row, 'invalid_sample', str(error))


def accept_review(row):
    if row['status'] != 'sample_reviewed':
        return row
    try:
        if row['sample_sha256'] != sample_identity(row):
            raise ValueError('Sample changed after review request')
        result = row['review_sample']
        require_texts(result, ['reason'])
        checks = result['checks']
        observations = result['criteria']
        numbers = [item['criterion'] for item in observations]
        if (any(type(n) is not int for n in numbers)
                or sorted(numbers) != list(range(1, len(row['criteria']) + 1))):
            raise ValueError('Review must cover every criterion exactly once')
        for item in observations:
            require_texts(item, ['observation'])
            if type(item['satisfied']) is not bool:
                raise ValueError('Criterion verdict must be boolean')
        if (not all(checks.get(name) is True for name in CHECKS)
                or not all(item['satisfied'] for item in observations)):
            return fail(row, 'rejected_sample', result['reason'])
        return {**row, 'status': 'accepted_task', 'certification': 'reviewed_candidate_not_golden'}
    except (ValueError, TypeError, KeyError) as error:
        return fail(row, 'invalid_review', str(error))
