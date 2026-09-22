"""Public-query retrieval: explicit entity scope and title-aware lexical ranking."""
from collections import Counter
import math
import re

from curation.preparation.materials import retrieve, search_text, tokens


def mentioned(concept, query):
    concept, query = concept.casefold(), query.casefold()
    if re.search(r'[\u3400-\u9fff]', concept):
        return concept in query
    return bool(re.search(r'(?<!\w)' + re.escape(concept) + r'(?!\w)', query))


def retrieve_public(items, query, guard, *, excluded=(), text_limit=3, image_limit=2, training=False):
    # Reuse publication, split, actual-pixel and near-duplicate exclusions.
    eligible, trace = retrieve(items, query, guard, excluded=excluded,
                               text_limit=len(items), image_limit=len(items), training=training)
    concepts = sorted({i['concept'] for i in items if mentioned(i['concept'], query)})
    scoped = [i for i in eligible if not concepts or i['concept'] in concepts]
    docs = [tokens(search_text(i)) for i in eligible]
    df = Counter(t for d in docs for t in d)
    n = len(docs)
    def weight(term):
        return math.log(1 + (n - df[term] + .5) / (df[term] + .5))
    query_terms = tokens(query)
    ranked = []
    for item in scoped:
        body = tokens(search_text(item))
        title = tokens(item.get('title', ''))
        # Length normalization prevents long generic passages winning simply by
        # mentioning more prompt words. Titles identify the requested topic.
        body_score = sum(weight(t) for t in sorted(query_terms & body)) / (1 + .01 * len(body))
        title_score = 3 * sum(weight(t) for t in sorted(query_terms & title))
        ranked.append((body_score + title_score, item, body_score, title_score))
    ranked.sort(key=lambda r: (-r[0], r[1]['item_id']))
    selected, counts = [], {'text': 0, 'image': 0}
    limits = {'text': text_limit, 'image': image_limit}
    for _, item, _, _ in ranked:
        if counts[item['kind']] < limits[item['kind']]:
            selected.append(item)
            counts[item['kind']] += 1
    trace.update(method='public_entity_title_idf/2', matched_concepts=concepts,
        unscoped_candidate_ids=[i['item_id'] for i in eligible],
        ranks=[{'item_id': i['item_id'], 'score': score, 'body_score': b, 'title_score': t}
               for score, i, b, t in ranked], selected_ids=[i['item_id'] for i in selected])
    return selected, trace
