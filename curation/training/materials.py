"""Unified training input; publication eligibility and task-local use are separate."""
from curation.training.candidates import ReadKnowledge
from curation.preparation.materials import reference_options
from curation.training.operators import fail
from curation.preparation.records import digest


def design_concepts(sources, config):
    """Freeze exploration scope without shrinking the retrieval catalog."""
    concepts = {r['concept'] for r in sources
                if not config.get('concepts') or r['concept'] in config['concepts']}
    ranked = sorted(concepts, key=lambda c: (digest([config['seed'], c]), c))
    return set(ranked[:config['max_units']] if config['max_units'] is not None else ranked)


class PrepareTrainingMaterials:
    def __init__(self, version, pool, config, guard, selected_concepts):
        self.read = ReadKnowledge(version, 'training', config, guard)
        self.pool, self.config = pool, config
        self.selected_concepts = set(selected_concepts)

    def __call__(self, source):
        row = self.read(source)
        row['authoring_selected'] = row['concept'] in self.selected_concepts
        row['material_policy'] = {'roles': 'task_local', 'text_required': False,
                                  'target_source': 'declared_publications',
                                  'target_eligibility': 'candidate_only'}
        if (not row['authoring_selected'] or row['status'] != 'knowledge_available'
                or self.config.get('training_design', 'target_aware') == 'knowledge_first'):
            return row
        candidates, trace = self.pool.load(row, 'target')
        out = {**row, 'design_targets': [], 'target_pool': candidates,
               'target_reference_options': [],
               'design_target_search': {**trace, 'method': 'shared_pool_for_target_visits',
                   'candidate_count': len(candidates)},
               'design_policy': {**row['design_policy'], 'target_aware': True,
                                 'text_required': False, 'reference_selection': 'per_target_evidence'}}
        if not candidates:
            return fail(out, 'needs_target', 'No source-backed target candidates before task design')
        return out
