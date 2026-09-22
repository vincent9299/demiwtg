"""Single-row adapters: questions, public retrieval, configured paired conditions, grouping."""
from pathlib import Path

from curation.preparation.records import digest
from curation.preparation.materials import SplitGuard, checked_asset, pixels
from curation.preparation.published import published_record
from curation.evaluation.contracts import MODELS, make_request
from curation.evaluation.native.retrieval import retrieve_public


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


class DeliverKnowledge:
    def __call__(self, row):
        result = published_record(row)
        return result


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


class BuildAnswerJobs:
    def __init__(self, config):
        self.config = config

    def __call__(self, row):
        q = row['question']
        qwen = 'qwen2512' if q['task_type'] == 't2i' else 'qwen2511'
        backends = self.config.get('backends', {}).get(q['task_type'], [qwen])
        conditions = [(backend, condition) for backend in backends
                      for condition in (self.config['gemini_conditions'] if backend == 'gemini'
                                        else ['without_knowledge', 'with_knowledge'])]
        for backend, condition in conditions:
            enhanced = condition == 'with_knowledge'
            material = row['materials'] if enhanced else []
            if backend == 'qwen2512':
                material = [m for m in material if m['kind'] == 'text']
            request = make_request(q, material)
            yield {'job_id': q['task_id'] + '__' + backend + '__' + condition,
                   'task_id': q['task_id'], 'task_type': q['task_type'], 'question': q,
                   'number': row['number'], 'origin': row['origin'], 'concept': row['concept'],
                   'split': row.get('split'),
                   'review_status': row['review_status'], 'accepted_by_machine': row['accepted_by_machine'],
                   'backend': backend, 'model': MODELS[backend],
                   'condition': condition, 'knowledge_mode': ('none' if not enhanced else
                       'text_only' if backend == 'qwen2512' else 'text_and_images'),
                   'actual_modalities': sorted({m['kind'] for m in material}),
                   'knowledge_ids': [m['item_id'] for m in material],
                   'omitted_image_ids': [m['item_id'] for m in row['materials']
                                         if enhanced and backend == 'qwen2512' and m['kind'] == 'image'],
                   'retrieval_gap': row['retrieval_gap'],
                   'missing_criterion_knowledge_ids': row['missing_criterion_knowledge_ids'],
                   'seed': (self.config['seed'] + int(digest(q['task_id'])[:8], 16)) % 2**32,
                   'seed_supported': backend != 'gemini',
                   'status': 'skipped_missing_knowledge' if enhanced and not material else 'pending',
                   'request': request}


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
