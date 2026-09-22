"""One model designs candidates from each published knowledge package.

Reading, optional sampling and validation are data operations, not extra authors.
T2I leaves this operator with a draft; editing waits for actual scene pixels.
"""
import copy

from curation.benchmark.authoring import (DeliverPublishedKnowledge, require_texts, evidence_numbers,
                         focus_material_numbers)
from curation.benchmark.operators import contains_forbidden, fail
from curation.preparation.records import digest


class ReadKnowledge:
    def __init__(self, version, branch, config, guard):
        self.deliver = DeliverPublishedKnowledge(version)
        self.branch, self.config, self.guard = branch, config, guard

    def __call__(self, source):
        row = self.deliver(source)
        allowed, issues = [], list(row['delivery_issues'])
        for item in row['materials']:
            if self.guard.allows_item(item, self.branch == 'training'):
                allowed.append(item)
            else:
                issues.append({'item_id': item['item_id'], 'reason': 'Formal-test reservation'})
        figures = {m['image_id'] for m in allowed if m['kind'] == 'image'}
        usable = []
        for item in allowed:
            missing = set(item['publication'].get('visual_dependencies', [])) - figures
            if missing:
                issues.append({'item_id': item['item_id'], 'reason': 'Required published figure excluded'})
            else:
                usable.append(item)
        key = 'unit_' + digest([self.branch, row['concept'], row['knowledge_version']])[:20]
        return {**row, 'materials': usable, 'issues': issues,
                'unit_id': key, 'task_id': key, 'branch': self.branch,
                'status': 'knowledge_available' if usable else 'needs_materials',
                'sampling_key': digest([self.config['seed'], row['concept']]),
                'design_policy': {'task_types': self.config['task_types'],
                                  'max_candidates': self.config['tasks_per_unit']},
                'edit_source': None}


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
                require_texts(candidate, ['knowledge_gap', 'knowledge_application'])
                kind = candidate.get('task_type')
                if kind not in self.config['task_types']:
                    raise ValueError('Unsupported candidate task type')
                numbers = focus_material_numbers(row, candidate)
                materials = [row['materials'][n-1] for n in numbers]
                remap = {old: new for new, old in enumerate(numbers, 1)}
                family = 'knowledge_' + digest(sorted(m['item_id'] for m in materials))[:20]
                plan = {'task_type': kind, 'concept': row['concept'],
                        'split': 'train' if row['branch'] == 'training' else 'development',
                        'rule_family': family, 'origin': 'pipeline.design_candidates', 'edit_source': None}
                self.guard.check_plan(plan, row['branch'])
                focus = {k: copy.deepcopy(v) for k, v in candidate.items() if k != 'draft'}
                focus['evidence'] = list(range(1, len(materials)+1))
                child.update(plan=plan, focus=focus, materials=materials,
                    construction_selection={'method': 'model_candidate_design',
                        'item_ids': [m['item_id'] for m in materials],
                        'input_evidence_numbers': numbers, 'actual_retrieval': False})
                if kind == 't2i':
                    draft = copy.deepcopy(candidate.get('draft'))
                    if not isinstance(draft, dict):
                        raise ValueError('T2I candidate must contain its actual task draft')
                    if draft.get('knowledge_application', candidate['knowledge_application']) != candidate['knowledge_application']:
                        raise ValueError('Conflicting duplicated knowledge_application')
                    draft['knowledge_application'] = candidate['knowledge_application']
                    for criterion in draft.get('criteria', []):
                        evidence = evidence_numbers(criterion.get('evidence'), len(row['materials']))
                        if any(n not in remap for n in evidence):
                            raise ValueError('Criterion cites evidence outside the candidate evidence set')
                        criterion['evidence'] = [remap[n] for n in evidence]
                    child.update(draft=draft, status='constructed')
                else:
                    require_texts(candidate, ['edit_intent', 'scene_requirements'])
                    queries = candidate.get('search_queries')
                    if (not isinstance(queries, list) or not 1 <= len(queries) <= 3
                            or any(not isinstance(q, str) or not q.strip() or len(q) > 200 for q in queries)):
                        raise ValueError('Editing requires 1–3 concise scene search queries')
                    if candidate.get('draft') is not None:
                        raise ValueError('Do not finalize an editing draft before seeing a real scene')
                    child.update(status='needs_edit_source_search')
                yield child
            except (ValueError, TypeError, KeyError, AttributeError) as error:
                yield fail(child, 'invalid_candidate', str(error))
