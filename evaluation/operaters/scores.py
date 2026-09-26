"""Judge wire contracts and deterministic, separate result dimensions.

These checks bind IDs, evidence, dimensions and nullable tiers. Semantic truth
and task validity remain prompt judgments, not hand-written knowledge rules.
"""
from collections import Counter, defaultdict
from evaluation.operaters.training_candidates import training_candidate

T2I_DIMS = {
    'alignment': ['subject_presence', 'form_structure', 'color_material', 'quantity_scale',
                  'spatial_relation', 'text_symbol', 'action_interaction', 'state_context',
                  'scene_environment', 'style'],
    'quality': ['physical_logic', 'material_texture', 'detail_richness', 'artifacts',
                'resolution', 'edge_clarity', 'naturalness', 'anatomical_fidelity'],
    'aesthetics': ['composition', 'color_harmony', 'lighting_atmosphere', 'emotional_expression'],
}
EDIT_DIMS = {
    'replace': ['Prompt Compliance', 'Visual Naturalness', 'Physical & Detail Integrity'],
    'add': ['Prompt Compliance', 'Visual Naturalness', 'Physical & Detail Coherence'],
    'adjust': ['Prompt Compliance', 'Visual Seamlessness', 'Physical & Detail Fidelity'],
    'remove': ['Prompt Compliance', 'Visual Naturalness', 'Physical & Detail Integrity'],
    'style': ['Style Fidelity', 'Content Preservation', 'Rendering Quality'],
    'action': ['Action Fidelity', 'Identity Preservation', 'Visual & Anatomical Coherence'],
    'background': ['Instruction Compliance', 'Visual Seamlessness', 'Physical Consistency'],
}
PROTOCOLS = {'t2i': 'v4-t2i-result-judge/3-draft', 'edit': 'v4-edit-result-judge/2-draft'}
PHI = {0: 0, 1: 60, 2: 100}
VALIDITY = {'ok', 'invalid_question', 'judge_unscorable', 'input_error', 'model_failure'}
RESULTS = {'met', 'partial', 'violated', 'unobservable', 'not_applicable'}


def require(condition, detail):
    if not condition:
        raise ValueError(detail)


def nonempty(value):
    return isinstance(value, str) and bool(value.strip())


def dimensions(q):
    if q['task_type'] == 't2i':
        return T2I_DIMS
    require(q.get('edit_type') in EDIT_DIMS, 'Missing/unsupported edit_type')
    return {f'd{i + 1}': name for i, name in enumerate(EDIT_DIMS[q['edit_type']])}


def mapping(row, q):
    valid = dimensions(q)
    require(row.get('dimension') in valid, 'Unknown criterion dimension')
    require(nonempty(row.get('check_category')), 'Missing criterion check_category')
    if q['task_type'] == 't2i':
        require(row['check_category'] in valid[row['dimension']], 'Category belongs to another dimension')


def unique_ids(items):
    require(isinstance(items, list), 'Expected list of ID records')
    require(all(isinstance(r, dict) for r in items), 'ID records must be objects')
    ids = [r.get('id') for r in items]
    require(all(nonempty(v) for v in ids) and len(ids) == len(set(ids)), 'Missing/duplicate record ID')
    return dict(zip(ids, items))


def id_list(value, allowed, label):
    require(isinstance(value, list) and all(isinstance(i, str) for i in value), label + ' must be IDs')
    require(len(value) == len(set(value)) and set(value) <= set(allowed), 'Invalid ' + label)


def validate_rubric(result, q, context):
    require(result.get('status') in {'ready', 'insufficient'}, 'Invalid rubric status')
    audit = result.get('audit', {})
    require(isinstance(audit, dict), 'Rubric audit must be an object')
    require(audit.get('status') in {'ok', 'invalid_question', 'judge_unscorable'} and nonempty(audit.get('detail')),
            'Missing rubric audit')
    criteria = unique_ids(result.get('criteria'))
    require(bool(criteria) or result['status'] == 'insufficient', 'Ready rubric has no criteria')
    evidence = {r['id'] for r in context['evidence']}
    author = {r['source_criterion_id'] for r in context['author_criteria']}
    for criterion in criteria.values():
        mapping(criterion, q)
        for key in ('requirement', 'condition', 'observable_region', 'allowed_variation'):
            require(nonempty(criterion.get(key)), 'Missing criterion ' + key)
        require(criterion.get('importance') in {'core', 'supporting'}, 'Invalid criterion importance')
        require(criterion.get('basis') in {'explicit', 'entailed'}, 'Invalid criterion basis')
        require(type(criterion.get('visibility_required')) is bool, 'Missing visibility requirement')
        id_list(criterion.get('evidence_ids'), evidence, 'evidence_ids')
        id_list(criterion.get('source_criterion_ids'), author, 'source_criterion_ids')
    dispositions = unique_ids(result.get('author_dispositions'))
    if result['status'] == 'ready':
        require(set(dispositions) == author, 'Every original criterion needs a disposition')
    else:
        require(audit['status'] != 'ok', 'Insufficient rubric needs a failure audit')
        require(set(dispositions) <= author, 'Unknown author disposition')
    for aid, entry in dispositions.items():
        require(entry.get('decision') in {'retained', 'split', 'excluded'} and nonempty(entry.get('reason')),
                'Invalid author disposition')
        id_list(entry.get('criterion_ids'), criteria, 'criterion_ids')
        expected = {rid for rid, c in criteria.items() if aid in c['source_criterion_ids']}
        require(set(entry['criterion_ids']) == expected, 'Author criterion trace differs')
        require(bool(expected) == (entry['decision'] != 'excluded'), 'Excluded/retained disposition differs')
    if context.get('rubric_policy') == 'author_exact_v1' and result['status'] == 'ready':
        require(len(criteria) == len(author), 'Author criteria must remain one-to-one')
        for original in context['author_criteria']:
            aid = original['source_criterion_id']
            disposition = dispositions[aid]
            require(disposition['decision'] == 'retained' and len(disposition['criterion_ids']) == 1,
                    'Author criterion cannot be split or excluded')
            criterion = criteria[disposition['criterion_ids'][0]]
            require(criterion['source_criterion_ids'] == [aid], 'Author criteria cannot be merged')
            for field in ('requirement', 'observable_region', 'allowed_variation'):
                require(criterion[field] == original[field], 'Author criterion text changed: ' + field)
            require(set(criterion['evidence_ids']) == set(original['evidence_ids']),
                    'Author criterion evidence changed')


def tier(value, na=False):
    return value is None or type(value) is int and value in PHI or na and value == 'N/A'


def line_score(scores):
    # Unknown applicable items must not disappear as if they were N/A.
    if any(v is None for v in scores):
        return None
    values = [PHI[v] for v in scores if type(v) is int and v in PHI]
    return round(sum(values) / len(values), 3) if values else None


def normalize_score(result, packet):
    q = packet['question']
    require(result.get('protocol') == PROTOCOLS[q['task_type']], 'Wrong judge protocol')
    validity = result.get('validity', {})
    require(isinstance(validity, dict), 'Judge validity must be an object')
    require(validity.get('status') in VALIDITY and nonempty(validity.get('detail')), 'Invalid judge validity')
    require(result.get('confidence') in {'high', 'medium', 'low'}, 'Invalid judge confidence')
    frozen = unique_ids(packet['rubric']['criteria'])
    observed = unique_ids(result.get('requirement_results'))
    require(set(frozen) <= set(observed), 'Judge omitted frozen criteria')
    evidence = {r['id'] for r in packet['evidence']}
    for rid, observation in observed.items():
        mapping(observation, q)
        require(observation.get('result') in RESULTS, 'Invalid requirement result')
        for key in ('region', 'reason_code'):
            require(nonempty(observation.get(key)), 'Missing observed ' + key)
        for key in (('observation',) if q['task_type'] == 't2i' else ('before', 'after')):
            require(nonempty(observation.get(key)), 'Missing image evidence ' + key)
        id_list(observation.get('evidence_ids'), evidence, 'observation evidence_ids')
        if rid in frozen:
            for key in ('dimension', 'check_category', 'importance'):
                require(observation.get(key) == frozen[rid][key], 'Frozen criterion mapping changed: ' + rid)
        else:
            require(rid.startswith('E') and rid[1:].isdigit() and observation.get('importance') is None,
                    'Unexpected post-answer criterion')
            if packet.get('rubric_policy') == 'author_exact_v1':
                require(q['task_type'] == 't2i' and observation['dimension'] in {'quality', 'aesthetics'},
                        'Judge cannot add task requirements after generation')
    metrics, labels = {}, {}

    def check_refs(refs, dim, category=None):
        id_list(refs, observed, 'score criterion references')
        own = {rid for rid, c in observed.items() if c['dimension'] == dim and
               (category is None or c['check_category'] == category)}
        expected = {rid for rid in own if observed[rid]['result'] != 'not_applicable'}
        # N/A explanations may cite their own inapplicable criteria. This does
        # not make them applicable or change any tier/mean. Missing applicable
        # evidence and references to another dimension/item still fail.
        require(expected <= set(refs) <= own,
                'Score must cite every applicable criterion in its own dimension/item')

    if q['task_type'] == 't2i':
        for dim, keys in T2I_DIMS.items():
            for obj in (result.get(dim), result.get(dim + '_reasons'), result.get('criterion_refs', {}).get(dim)):
                require(isinstance(obj, dict) and set(obj) == set(keys), 'T2I item keys differ: ' + dim)
            for key in keys:
                require(tier(result[dim][key], na=True), 'Invalid T2I tier')
                require(nonempty(result[dim + '_reasons'][key]), 'Missing T2I item evidence')
                check_refs(result['criterion_refs'][dim][key], dim, key)
            name = {'alignment': 'alignment_score', 'quality': 'quality_score', 'aesthetics': 'aesthetic_score'}[dim]
            metrics[name] = line_score(list(result[dim].values()))
            labels[name] = dim
    else:
        require(result.get('edit_type') == q['edit_type'], 'Judge changed edit_type')
        raw = result.get('raw_dimensions', [])
        require(len(raw) == 3, 'Editing needs three dimensions')
        groups = result.get('observations', {})
        require(isinstance(groups, dict) and set(groups) == {'explicit_edit', 'necessary_consequences', 'preservation', 'artifacts'},
                'Missing editing observations')
        require(all(isinstance(group, list) for group in groups.values()), 'Observation groups must be lists')
        observations = unique_ids([o for group in groups.values() for o in group])
        require(not (set(observations) & set(observed)), 'Observation/criterion IDs collide')
        for observation in observations.values():
            mapping(observation, q)
            require(all(nonempty(observation.get(k)) for k in ('region', 'before', 'after', 'reason_code')),
                    'Missing editing observation evidence')
            require(observation.get('impact') in {'none', 'minor', 'severe', 'unknown'}, 'Invalid observation impact')
        for index, label in enumerate(EDIT_DIMS[q['edit_type']]):
            dim = f'd{index + 1}'
            entry = raw[index]
            require(entry.get('dimension') == dim and entry.get('label') == label, 'Wrong editing dimension')
            require(tier(entry.get('tier')) and nonempty(entry.get('reason')), 'Invalid editing tier/evidence')
            check_refs(entry.get('criterion_ids'), dim)
            id_list(entry.get('observation_ids'), observations, 'observation_ids')
            require(all(observations[oid]['dimension'] == dim for oid in entry['observation_ids']),
                    'Observation belongs to another dimension')
            metrics[dim] = None if entry['tier'] is None else PHI[entry['tier']]
            labels[dim] = label
        require(isinstance(result.get('critical_failures'), list), 'Missing editing critical failures')
        for failure in result['critical_failures']:
            records = {**observed, **observations}
            require(failure.get('id') in records and failure.get('dimension') == records[failure['id']]['dimension']
                    and nonempty(failure.get('reason')), 'Unbound critical failure')
    # Preserve the pre-answer audit even if a later judge overlooks it.
    audit = packet['rubric']['audit']
    status = validity['status'] if audit['status'] == 'ok' else audit['status']
    return {'protocol': result['protocol'], 'validity': status, 'judge_validity': validity,
            'rubric_audit': audit, 'metrics': metrics, 'metric_labels': labels, 'raw': result,
            'core_failure_ids': [rid for rid, r in observed.items()
                                 if r.get('importance') == 'core' and r['result'] == 'violated'],
            'unobservable_ids': [rid for rid, r in observed.items() if r['result'] == 'unobservable']}


def failure_score(packet):
    """Confirmed no-image failure: task zero, unobservable quality stays unknown."""
    q = packet['question']
    if q['task_type'] == 't2i':
        metrics = {'alignment_score': 0, 'quality_score': None, 'aesthetic_score': None}
        labels = {'alignment_score': 'alignment', 'quality_score': 'quality', 'aesthetic_score': 'aesthetics'}
    else:
        metrics = {'d1': 0, 'd2': None, 'd3': None}
        labels = dimensions(q)
    audit = packet['rubric']['audit']
    return {'protocol': PROTOCOLS[q['task_type']],
            'validity': 'model_failure' if audit['status'] == 'ok' else audit['status'],
            'rubric_audit': audit, 'metrics': metrics, 'metric_labels': labels,
            'source': 'confirmed_generation_failure', 'core_failure_ids': [],
            'unobservable_ids': [r['id'] for r in packet['rubric']['criteria']]}


def compare_group(group):
    """Paired metrics share questions; Gemini is a baseline-only reference."""
    answers = group['answers']
    invalid = any(a.get('score', {}).get('validity') == 'invalid_question' for a in answers)
    by_arm = {(a['backend'], a['condition']): a for a in answers}
    pairs, gaps = [], []
    def numeric(answer, key):
        score = (answer or {}).get('score', {})
        return not invalid and score.get('validity') in {'ok', 'model_failure'} and type(score.get('metrics', {}).get(key)) in {int, float}
    backends = sorted({a['backend'] for a in answers if a['backend'] != 'gemini'})
    for backend in backends:
        base = by_arm.get((backend, 'without_knowledge'))
        enhanced = by_arm.get((backend, 'with_knowledge'))
        common = {'backend': backend, 'knowledge_mode': (enhanced or {}).get('knowledge_mode'),
                  'before_status': (base or {}).get('judge_status', 'missing'),
                  'after_status': (enhanced or {}).get('judge_status', 'missing')}
        metric_names = (['alignment_score', 'quality_score', 'aesthetic_score']
                        if group['question']['task_type'] == 't2i' else ['d1', 'd2', 'd3'])
        for key in metric_names:
            valid = numeric(base, key) and numeric(enhanced, key)
            reason = ('invalid_question_in_any_arm' if invalid else
                      'missing_or_unscorable_arm' if not valid else None)
            pair = {**common, 'metric': key, 'valid': valid, 'reason': reason,
                    'delta': (enhanced['score']['metrics'][key] - base['score']['metrics'][key]) if valid else None,
                    'includes_model_failure': any((a or {}).get('score', {}).get('validity') == 'model_failure'
                                                  for a in (base, enhanced))}
            pairs.append(pair)
            closed = by_arm.get(('gemini', 'without_knowledge'))
            matched = valid and numeric(closed, key)
            gaps.append({**common, 'metric': key, 'valid': matched,
                         'reason': None if matched else reason or 'missing_or_unscorable_closed_reference',
                         'gap_without': closed['score']['metrics'][key] - base['score']['metrics'][key] if matched else None,
                         'gap_with': closed['score']['metrics'][key] - enhanced['score']['metrics'][key] if matched else None,
                         'gap_reduction': pair['delta'] if matched else None})
    return {**group, 'invalid_question': invalid, 'pairs': pairs, 'closed_reference_gaps': gaps,
            'training_candidate': training_candidate(group)}


def aggregate(groups):
    """Every denominator/status is retained; edit types and reference modalities stay separate."""
    counts = Counter()
    summaries = defaultdict(lambda: {'eligible': 0, 'excluded': 0, 'sum': 0.0, 'model_failures': 0})
    gap_summaries = defaultdict(lambda: {'eligible': 0, 'excluded': 0, 'without': 0.0, 'with': 0.0, 'reduction': 0.0})
    excluded = []
    for group in groups:
        counts['questions'] += 1
        candidate = group.get('training_candidate') or training_candidate(group)
        counts['training_candidate:' + candidate['status']] += 1
        counts['machine_accepted_questions' if group.get('accepted_by_machine') else 'machine_rejected_questions'] += 1
        counts['invalid_questions'] += bool(group['invalid_question'])
        for answer in group['answers']:
            counts['generation:' + answer['status']] += 1
            counts['judge:' + answer.get('judge_status', 'missing')] += 1
            if answer.get('score'):
                counts['validity:' + answer['score']['validity']] += 1
        for pair in group['pairs']:
            key = (group['question']['task_type'], group['question'].get('edit_type'),
                   pair['backend'], pair['knowledge_mode'], group.get('accepted_by_machine', False), pair['metric'])
            summary = summaries[key]
            summary['eligible' if pair['valid'] else 'excluded'] += 1
            if pair['valid']:
                summary['sum'] += pair['delta']
                summary['model_failures'] += pair['includes_model_failure']
            else:
                excluded.append({'task_id': group['task_id'], 'backend': pair['backend'],
                                 'metric': pair['metric'], 'reason': pair['reason']})
        for gap in group['closed_reference_gaps']:
            key = (group['question']['task_type'], group['question'].get('edit_type'),
                   gap['backend'], gap['knowledge_mode'], group.get('accepted_by_machine', False), gap['metric'])
            summary = gap_summaries[key]
            summary['eligible' if gap['valid'] else 'excluded'] += 1
            if gap['valid']:
                for target, source in [('without', 'gap_without'), ('with', 'gap_with'), ('reduction', 'gap_reduction')]:
                    summary[target] += gap[source]
    def describe(key):
        return dict(zip(['task_type', 'edit_type', 'backend', 'knowledge_mode', 'accepted_by_machine', 'metric'], key))
    return {'schema': 'v4-evaluation-summary/1', 'counts': dict(counts),
            'paired_deltas': [{**describe(k), 'n': v['eligible'], 'excluded': v['excluded'],
                               'model_failure_pairs': v['model_failures'],
                               'mean_delta': round(v['sum'] / v['eligible'], 3) if v['eligible'] else None}
                              for k, v in sorted(summaries.items(), key=lambda kv: str(kv[0]))],
            'closed_reference_gaps': [{**describe(k), 'n': v['eligible'], 'excluded': v['excluded'],
                                       **{'mean_gap_' + n: round(v[n] / v['eligible'], 3) if v['eligible'] else None
                                          for n in ('without', 'with', 'reduction')}}
                                      for k, v in sorted(gap_summaries.items(), key=lambda kv: str(kv[0]))],
            'excluded_pairs': excluded, 'cross_dimension_total': None,
            'scope': 'development_only; rejected author drafts remain flagged; modalities are not identical'}
