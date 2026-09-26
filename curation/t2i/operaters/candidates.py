"""T2I candidate design and reference binding; edit pairs have their own pipeline."""
import copy

from curation.t2i.operaters.authoring import (require_texts, evidence_numbers,
                         focus_material_numbers)
from curation.t2i.operaters.operators import contains_forbidden, fail
from demiflow.execution.artifacts import digest




class ExpandCandidates:
    def __init__(self, guard, config):
        self.guard, self.config = guard, config

    def __call__(self, row):
        if row['status'] != 'candidates_designed':
            yield row
            return
        result = row['candidate_design']
        try:
            if result.get('status') == 'insufficient':
                require_texts(result, ['reason'])
                yield fail(row, 'no_valid_candidates', result['reason'])
                return
            candidates = result.get('candidates')
            if (result.get('status') != 'ok' or not isinstance(candidates, list)
                    or not 1 <= len(candidates) <= row.get('design_policy', {}).get('max_candidates', self.config['tasks_per_unit'])):
                raise ValueError('Expected bounded candidate list or explicit insufficient reason')
        except (ValueError, TypeError) as error:
            yield fail(row, 'invalid_candidates', str(error))
            return
        for index, candidate in enumerate(candidates, 1):
            child = {**row, 'task_id': row['unit_id'] + '_' + str(index), 'candidate': candidate}
            try:
                if contains_forbidden(candidate):
                    raise ValueError('Removed complexity fields are not accepted')
                require_texts(candidate, ['learning_objective', 'knowledge_gap',
                                         'knowledge_application', 'reference_selection_reason'])
                kind = candidate.get('task_type')
                if kind != 't2i' or kind not in self.config['task_types']:
                    raise ValueError('t2i accepts only T2I candidates')
                numbers = focus_material_numbers(row, candidate)
                input_numbers = focus_material_numbers(row, candidate, 'input_materials')
                materials = [copy.deepcopy(row['materials'][n-1]) for n in numbers]
                answer_materials = [copy.deepcopy(row['materials'][n-1]) for n in input_numbers]
                if not all(self.guard.allows_item(m, row['branch'] == 'training')
                           for m in materials + answer_materials):
                    raise ValueError('Selected materials violate formal-test reservation')
                remap = {old: new for new, old in enumerate(numbers, 1)}
                family = 'knowledge_' + digest(sorted(m['item_id'] for m in materials))[:20]
                plan = {'task_type': kind, 'concept': row['concept'],
                        'split': 'train' if row['branch'] == 'training' else 'development',
                        'rule_family': family, 'origin': 'pipeline.design_candidates', 'edit_source': None}
                self.guard.check_plan(plan, row['branch'])
                focus = {k: copy.deepcopy(v) for k, v in candidate.items() if k != 'draft'}
                focus['evidence'] = list(range(1, len(materials)+1))
                focus.pop('input_materials')
                focus['answer_item_ids'] = [m['item_id'] for m in answer_materials]
                child.update(plan=plan, focus=focus, materials=materials,
                    learning_objective=candidate['learning_objective'], answer_materials=answer_materials,
                    construction_selection={'method': 'model_candidate_design',
                        'item_ids': [m['item_id'] for m in materials],
                        'materials_sha256': digest(materials),
                        'input_evidence_numbers': numbers, 'actual_retrieval': False})
                if row.get('design_policy', {}).get('target_aware'):
                    require_texts(candidate, ['target_support'])
                    targets = candidate.get('target_candidates')
                    if (not isinstance(targets, list) or not targets or len(set(targets)) != len(targets)
                            or any(type(n) is not int or not 1 <= n <= len(row['design_targets']) for n in targets)):
                        raise ValueError('Target-aware candidate must select supplied target candidate numbers')
                    child['design_targets'] = [row['design_targets'][n-1] for n in targets]
                    from preparation.operaters.inputs import reference_options
                    compatible = set(reference_options(row['materials'], child['design_targets'])['evidence'])
                    if not (set(numbers) | set(input_numbers)) <= compatible:
                        raise ValueError('Selected evidence duplicates a selected target or depends on its excluded figure')
                    child.pop('target_reference_options', None)
                    child['construction_selection']['target_candidate_numbers'] = targets
                    child['target_design_binding'] = {'method':'learning_objective_target_and_inputs',
                        'target_sha256':[a['sha256'] for a in child['design_targets']],
                        'learning_objective': candidate['learning_objective'],
                        'target_support': candidate['target_support'],
                        'knowledge_application':candidate['knowledge_application']}
                if row['branch'] == 'training':
                    child['training_input_binding'] = {
                        'method': 'selected_with_task', 'actual_retrieval': False,
                        'input_material_numbers': input_numbers,
                        'item_ids': [m['item_id'] for m in answer_materials],
                        'materials_sha256': digest(answer_materials),
                        'reference_sha256': [m['asset']['sha256'] for m in answer_materials if m['kind'] == 'image'],
                        'text_item_ids': [m['item_id'] for m in answer_materials if m['kind'] == 'text']}
                draft = copy.deepcopy(candidate.get('draft'))
                if not isinstance(draft, dict):
                    raise ValueError('T2I candidate must contain its actual task draft')
                require_texts(draft, ['instruction'])
                if draft.get('status') != 'ok' or draft.get('edit_type') or draft.get('preserve') or draft.get('anchor'):
                    raise ValueError('Expected a T2I draft without edit obligations')
                if not isinstance(draft.get('criteria'), list) or not draft['criteria']:
                    raise ValueError('Observable criteria required')
                if draft.get('knowledge_application', candidate['knowledge_application']) != candidate['knowledge_application']:
                    raise ValueError('Conflicting duplicated knowledge_application')
                draft['knowledge_application'] = candidate['knowledge_application']
                for criterion in draft.get('criteria', []):
                    require_texts(criterion, ['requirement', 'observable_region', 'allowed_variation'])
                    evidence = evidence_numbers(criterion.get('evidence'), len(row['materials']))
                    if any(n not in remap for n in evidence):
                        raise ValueError('Criterion cites evidence outside the candidate evidence set')
                    criterion['evidence'] = [remap[n] for n in evidence]
                child.update(draft=draft, status='constructed')
                yield child
            except (ValueError, TypeError, KeyError, AttributeError) as error:
                yield fail(child, 'invalid_candidate', str(error))
