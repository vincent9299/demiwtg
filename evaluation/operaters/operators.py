"""Single-row adapters: questions, public retrieval, configured paired conditions, grouping."""
from pathlib import Path

from demiflow.execution.artifacts import digest
from preparation.operaters.inputs import SplitGuard, checked_asset, pixels
from preparation.operaters.inputs import material_record
from evaluation.operaters.retrieval import retrieve_public


def record_value(record):
    if record.get('error'):
        raise ValueError(str(record['error']))
    return record['value']


def normalize_question(row):
    draft, plan = row['draft'], row['plan']
    if draft.get('status') != 'ok' or not draft.get('instruction') or not row.get('criteria'):
        raise ValueError('Expected complete development draft')
    if plan['task_type'] not in {'t2i', 'edit'}:
        raise ValueError('Unsupported task type')
    origin = {'run': row.get('_run') or row['publication_ref'], 'task_id': row['task_id']}
    source = row.get('edit_source')
    if bool(source) != (plan['task_type'] == 'edit'):
        raise ValueError('Exactly one original is required for edit only')
    if source:
        source = checked_asset(source, 'edit_source')
    question = {'task_id': 'q_' + digest(origin)[:24], 'task_type': plan['task_type'],
                'instruction': draft['instruction'], 'edit_source': source,
                'edit_type': draft.get('edit_type') if plan['task_type'] == 'edit' else None}
    return {'question': question, 'origin': origin, 'number': row.get('number', row.get('task_id')),
            'concept': row.get('concept', plan.get('concept')), 'criteria': row['criteria'],
            'split': plan.get('split'),
            'review_status': row.get('status'), 'review': row.get('review_task'),
            'accepted_by_machine': row.get('export_ready') is True,
            'authoring_materials': row.get('materials', []),
            'authoring_context': {k: draft.get(k) for k in ('anchor', 'preserve', 'condition')},
            'scope': 'development_draft_not_human_golden'}




class RetrieveKnowledge:
    def __init__(self, items, registry, config):
        self.items, self.guard, self.config = items, SplitGuard(registry), config

    def __call__(self, row):
        q = row['question']
        excluded = [q['edit_source']] if q.get('edit_source') else []
        selected, trace = retrieve_public(self.items, q['instruction'], self.guard, excluded=excluded,
                                  text_limit=self.config['text_limit'], image_limit=self.config['image_limit'])
        # Audit only: never use author criteria, concept label, or construction
        # evidence to boost retrieval. The only query is the public instruction.
        required = {k for c in row['criteria'] for k in c.get('knowledge_ids', [])}
        trace.update(phase='answer', query_fields=['instruction'], actual_retrieval=True)
        missing = sorted(required - {m['item_id'] for m in selected})
        return {**row, 'materials': selected, 'retrieval': trace,
                'missing_criterion_knowledge_ids': missing, 'retrieval_gap': bool(missing)}




def backend_partition(state, job):
    state = state or {'backend': job['backend'], 'jobs': 0}
    state['jobs'] += 1
    return state


def group_answers(state, row):
    state = state or {k: row[k] for k in ('task_id', 'number', 'question', 'origin', 'concept',
                                        'review_status', 'accepted_by_machine')}
    state.setdefault('split', row.get('split'))
    state.setdefault('answers', []).append(row)
    return state


def verify_result(row):
    if row.get('image'):
        pixels(row['image'])
    return row
