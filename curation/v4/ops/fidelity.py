"""Source-bound semantic review; actors run through demiflow's native map interfaces."""
from curation.v4.contracts import digest
from curation.v4.ops.knowledge_stages import Stage, model_passages
from curation.v4.ops.prompt_operators import KnowledgeRow, apply_prompt_result


def review_input(row):
    knowledge = row['knowledge']
    facts = knowledge.get('facts', []) + [item['fact'] for item in knowledge.get('deferred_facts', [])]
    ids = [fact['fact_id'] for fact in facts]
    if len(set(ids)) != len(ids):
        raise ValueError('Duplicate fact IDs in fidelity input')
    # Include entire selected passages, not only quotes: omitted conditions may lie nearby.
    passages = model_passages(row['material_pack']['passages'])
    texts = {p['source_id']: p['text'] for p in passages}
    # Candidate quotes may already be fabricated/stitched. Do not let the reviewer
    # copy them as source text; provide only source IDs and the deterministic check.
    review_facts = [{**{k: f[k] for k in ('fact_id', 'statement', 'conditions', 'exceptions') if k in f},
                     'evidence': [{'source_id': e['source_id'],
                                   'quoted_text_matches': bool(e.get('quote')) and e['quote'] in texts.get(e['source_id'], '')}
                                  for e in f.get('evidence', [])]}
                    for f in sorted(facts, key=lambda f: f['fact_id'])]
    return {'review_facts': review_facts, 'passages': passages,
            'original_facts_sha256': digest(sorted(facts, key=lambda f: f['fact_id']))}


class PrepareFidelity(KnowledgeRow, Stage):
    """knowledge + full selected passages -> fidelity_prompt and content hash; no model call."""
    label = 'fidelity_prepare'

    async def process(self, row):
        payload = review_input(row)
        if not payload['review_facts']:
            return {**row, 'fidelity_review': {'status': 'not_run', 'reason': 'no_facts'}}
        return {**row, 'fidelity_prompt': payload, 'fidelity_input_hash': digest(payload)}


def apply_review(row, result, *, reviewer, call=None):
    """Validate complete reviews; defer failed/uncertain facts without rewriting any claim."""
    payload = review_input(row)
    if row.get('fidelity_input_hash') != digest(payload):
        raise ValueError('Fidelity input changed after preparation')
    facts = {f['fact_id']: f for f in payload['review_facts']}
    passages = {p['source_id']: p for p in payload['passages']}
    reviews = result.get('reviews')
    if not isinstance(reviews, list):
        raise ValueError('Missing fidelity reviews')
    ids = [r['fact_id'] for r in reviews]
    if len(ids) != len(set(ids)) or set(ids) != set(facts):
        raise ValueError('Fidelity must review each input fact exactly once')
    for review in reviews:
        if review['verdict'] not in {'faithful', 'unsupported', 'uncertain'}:
            raise ValueError('Unknown fidelity verdict')
        if not isinstance(review.get('reason'), str) or not review['reason'].strip():
            raise ValueError('Fidelity requires a reason')
        issues = review.get('issues')
        if not isinstance(issues, list) or (review['verdict'] == 'faithful' and issues):
            raise ValueError('Inconsistent fidelity issues')
        if review['verdict'] == 'unsupported' and not issues:
            raise ValueError('Unsupported verdict needs located issues')
        fact = facts[review['fact_id']]
        claim_text = '\n'.join([fact['statement'], *fact.get('conditions', []), *fact.get('exceptions', [])])
        conditions = review.get('source_conditions')
        if not isinstance(conditions, list):
            raise ValueError('Missing source condition comparison')
        for condition in conditions:
            source = passages.get(condition.get('source_id'))
            quote = condition.get('quote')
            if not source or not isinstance(quote, str) or not quote or quote not in source['text']:
                raise ValueError('Source condition quote not in supplied passage')
            if type(condition.get('preserved')) is not bool or not condition.get('reason'):
                raise ValueError('Source condition lacks comparison')
            if not condition['preserved'] and (review['verdict'] == 'faithful' or not issues):
                raise ValueError('Omitted source condition cannot pass')
        for issue in issues:
            for key in ('claim', 'source_id', 'source_quote', 'kind', 'reason'):
                if not isinstance(issue.get(key), str):
                    raise ValueError('Incomplete fidelity issue: ' + key)
            if issue['claim'] and issue['claim'] not in claim_text:
                raise ValueError('Fidelity claim not in reviewed fact')
            source = passages.get(issue['source_id'])
            if not source or not issue['source_quote'] or issue['source_quote'] not in source['text']:
                raise ValueError('Fidelity issue quote not in supplied passage')
            if not issue['kind'].strip() or not issue['reason'].strip():
                raise ValueError('Fidelity issue lacks explanation')
    by_id = {r['fact_id']: r for r in reviews}
    knowledge = row['knowledge']
    retained, deferred = [], []
    for fact in knowledge.get('facts', []):
        review = by_id[fact['fact_id']]
        if review['verdict'] == 'faithful':
            retained.append(fact)
        else:
            deferred.append({'fact': fact, 'reasons': ['source_fidelity:' + review['verdict']],
                             'next_action': '核对所列原文与上下文，另存修订候选后重新核验；不得直接恢复。'})
    # Earlier quote/conflict failures are never cleared by this separate semantic review.
    deferred = list(knowledge.get('deferred_facts', [])) + deferred
    audit = {'status': 'reviewed', 'reviewer': reviewer, 'input_hash': digest(payload),
             'reviews': reviews, 'call': call,
             'scope': 'Only supplied passage text; not source truth, whole-document or independent human approval.',
             'passage_ids': list(passages)}
    history = list(row.get('fidelity_reviews', [])) + [audit]
    out = {k: v for k, v in row.items() if k != 'fidelity_prompt'}
    return {**out, 'knowledge': {**knowledge, 'facts': retained, 'deferred_facts': deferred},
            'fidelity_review': audit, 'fidelity_reviews': history}


class ApplyFidelity(KnowledgeRow, Stage):
    """Model reviews -> validated audit + knowledge statuses; never promotes prior deferred facts."""
    label = 'fidelity'

    async def process(self, row):
        if 'fidelity_prompt' in row and not any(k in row for k in ('prompt_result', 'prompt_error')):
            raise ValueError('Missing fidelity model response')
        return apply_prompt_result(self, row)

    def apply(self, row, result, call):
        return apply_review(row, result, reviewer='local_model; source fidelity only', call=call)
