"""Pure training sampling rules. Notebook Dataset operations execute the loop.

No files, model calls or independent counters are managed here. Progress is
reconstructed from the completed attempt rows in the existing Lance chain.
"""
from curation.preparation.materials import reference_options
from curation.preparation.records import digest


def training_targets(catalog, config):
    targets = {}
    for row in catalog:
        if not row.get('authoring_selected') or row['status'] != 'knowledge_available':
            continue
        for asset in row.get('target_pool', []):
            targets.setdefault(asset['sha256'], (row, asset))
    return [targets[sha] for sha in sorted(targets, key=lambda s: digest([config['seed'], s]))]


def material_batches(row, target, config, cycle):
    """Page all eligible evidence; no lexical/semantic top-k retrieval."""
    materials = row['materials']
    allowed = set(reference_options(materials, [target])['evidence'])
    indexed = [(n, m) for n, m in enumerate(materials, 1) if n in allowed]
    groups = []
    # Keep explicitly dependent figures with their text; repeated figures across
    # pages are permitted, not duplicated within a page.
    lookup = {m.get('image_id'): n for n, m in indexed if m['kind'] == 'image'}
    for n, item in indexed:
        dependencies = item.get('publication', {}).get('visual_dependencies', [])
        group = {n} | {lookup[i] for i in dependencies}
        groups.append(group)
    groups.sort(key=lambda g: digest([config['seed'], target['sha256'], cycle,
                                     sorted(materials[n-1]['item_id'] for n in g)]))
    image_limit = min(config['reference_batch_size'], config['max_reference_images'] - 1)
    char_limit = config['max_context_chars'] // 2
    pages, current, seen = [], set(), set()
    def fits(numbers):
        items = [materials[n-1] for n in numbers]
        return (sum(m['kind'] == 'image' for m in items) <= image_limit
                and sum(len(str(m)) for m in items) <= char_limit)
    for group in groups:
        if group <= seen:
            continue
        if current and not fits(current | group):
            pages.append(sorted(current)); current = set()
        # Oversized indivisible evidence is kept as its own page. The explicit
        # prompt budget check records a gap; it is never silently truncated.
        current |= group
        seen |= group
    if current:
        pages.append(sorted(current))
    return [[materials[n-1] for n in page] for page in pages] or [[]]


def loop_progress():
    return {'accepted_samples': 0, 'target_index': 0, 'cycle': 0, 'batch_index': 0, 'attempts': 0}


def training_attempt(targets, progress, config):
    row, target = targets[progress['target_index']]
    batches = material_batches(row, target, config, progress['cycle'])
    materials = batches[progress['batch_index']]
    position = {**progress, 'batch_count': len(batches), 'target_sha256': target['sha256']}
    identity = 'unit_' + digest([row['unit_id'], position])[:20]
    return {**row, 'unit_id': identity, 'task_id': identity, 'materials': materials,
            'status': 'knowledge_available' if materials else 'needs_materials',
            'issues': row.get('issues', []) + ([] if materials else ['No compatible reference materials for this target']),
            'design_targets': [target], 'target_pool': [target], 'loop_position': position,
            'target_reference_options': [{'target_candidate': 1,
                **reference_options(materials, [target])}],
            'design_policy': {**row['design_policy'], 'max_candidates': 1, 'target_aware': True,
                'text_required': False, 'reference_selection': 'per_target_evidence',
                'one_sample_per_target_visit': True}}


def advance_progress(progress, result, target_count):
    """One accepted sample advances the target; otherwise advance the page."""
    out = {**progress, 'attempts': progress['attempts'] + 1}
    accepted = bool(result.get('export_ready'))
    out['accepted_samples'] += int(accepted)
    out['batch_index'] += 1
    if accepted or out['batch_index'] >= result['loop_position']['batch_count']:
        out['batch_index'] = 0
        out['target_index'] += 1
        if out['target_index'] == target_count:
            out['target_index'] = 0
            out['cycle'] += 1
    return out


def loop_stop(progress, target_count, config):
    if progress['accepted_samples'] >= config['training_sample_goal']:
        return 'sample_goal_reached'
    if not target_count:
        return 'no_eligible_targets'
    if progress['attempts'] >= config['max_training_attempts']:
        return 'attempt_budget_exhausted'
    if progress['cycle'] >= config['max_target_cycles']:
        return 'target_cycle_budget_exhausted'
    return None


def attempt_waiting(rows):
    # Offline responses and failed calls require continuation; never treat an
    # unanswered request as a rejected sample or skip it to spend another call.
    return any(r['status'].startswith(('pending_', 'failed_')) for r in rows)
